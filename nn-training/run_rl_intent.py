"""run_rl_intent.py — M8 意图 RL on-policy 主循环（semi-MDP，plan/Intent-Policy-NN-Plan.md §7-M8）。

**训练机制与 run_rl.py 完全一致**（除网络与超参外，逐项复用 `rl/` 共享基础设施，
禁止复制第二份——机制约定见 nn-training/README.md「RL 训练机制」）：
  - 断点续跑：training_log.jsonl 锚点（--start-it / rotateSeed 继承）+ completed_pairs
    （wver 匹配 shard 秒回）+ PPO epoch checkpoint（traj_dir/ppo_ckpt）。
  - 流式迭代（--stream 1，默认）：rollout 采集与 PPO 波次重叠（rl/stream.py
    run_rollout_stream，backend=ppo_intent）；首个 PPO 波次 → local 槽位让位
    （local_suspend）；派发队列清空 → 派发干净评估（on_collect_done），PPO 收尾后
    eval join（eval_gate 语义）。
  - F4 熔断（rl/breaker.py）+ keep-iters 轮转 + 每轮 HTML 巡检（rl-hourly-inspect.ts
    --traj-dir）+ 失败重试（iter_error 事件 + 连续 5 次退出）。
  - 权重：B′ 意图 BC warm-start（value 头随机）→ 每轮 ppo_intent 更新 → 原子写回 +
    归档（前缀 intent-rl-weights）。
  - 评估：m1-eval --policy intent-exec 固定语料贪心局（35 关 × --eval-seeds/关），
    主指标 = Δ vs --baseline（M7② 72.3%，预注册 #27 iter15 350 局）；iter15 Δ≤0 →
    止损转 M9（P2-5）。

**启动参数单一事实来源（用户 2026-08-27 指令）**：
所有 argparse 启动参数的默认值存放在 `nn-training/rl-config.json` 的 `intent_rl`
块；本文件 argparse 的 `default` 直接读该块（缺失才回退下方硬编码常量）。使用纪律：
  1. 平时启动 RL 直接用 json 默认参数，命令行不必重复指定（启动器仅传
     `-Script run_rl_intent.py`）。
  2. 仅在排查/调试问题时，才在启动命令里显式追加参数（覆盖 json 默认）用于定位。
  3. 调试结束、参数验证有效后，把该值写回 `rl-config.json` 的 `intent_rl` 块作为新默认。
禁止在 json 之外把可调参数硬编码成"第二个默认"——保持单一事实来源。
注意：`--start-it` 不在此块内（续跑轮次由 training_log 自动派生，仅手动重跑时显式传 CLI）。

经统一启动器进入（venv/torch 由它保证）：
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script run_rl_intent.py
  # 启动参数取自 rl-config.json 的 intent_rl 块；调试时追加 --iters 20 等覆盖即可。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import torch

import ppo_intent

REPO_ROOT = Path(__file__).resolve().parents[1]

# 共享基础设施（与 run_rl.py 同源，勿复制第二份实现——README「RL 训练机制」）。
from rl.log import log  # noqa: E402
from rl.course import build_pairs  # noqa: E402
from rl.queue import (REPO_ROOT as _RQ, RUN_ID,  # noqa: E402,F401
                      run_rollout, run_rollout_queue)
from rl.resume import (completed_pairs, last_completed_iter,  # noqa: E402
                       last_rotate_seed)
from rl.stream import run_rollout_stream  # noqa: E402
from rl.breaker import (CIRCUIT_EXIT_CODE, KL_WARN, ENT_COLLAPSE_DROP,  # noqa: E402
                        breaker_update)
from run_rl import backup_weights, ensure_current_branch_pushed  # noqa: E402

# M7② 基线（m1-eval 35×10 hard，intent-exec B′，nn.progress.intent §25/§26）。
DEFAULT_BASELINE = 0.723
# 止损线：iter15 主指标 Δ（相对基线）≤ 0 → 停止并转 M9。
STOP_AT_ITER = 15
# 意图 RL 干净评估默认迭代（评估旁路不拖慢采集：只在这几个迭代跑）。
DEFAULT_EVAL_AT = "5,10,15"

# Windows：spawn 子进程（bun 巡检/评估、python 权重初始化）时用 CREATE_NO_WINDOW，
# 避免每个子进程开黑色控制台窗口反复弹出抢占焦点。stdout/stderr 已 capture 或重定向。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW  # noqa: E402


def _run_inspect(bun: str, traj_root: Path, it: int) -> None:
    """每轮 PPO 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。非致命。"""
    try:
        subprocess.run(
            [bun, "tools/diag/rl-hourly-inspect.ts", "--up-to", str(it),
             "--traj-dir", str(traj_root)],
            cwd=str(REPO_ROOT), timeout=180, capture_output=True, text=True,
            **_POPEN_NO_WINDOW)
        log(f"inspection HTML regenerated (up to it{it})")
    except Exception as e:  # noqa: BLE001 — 巡检失败不中断训练
        log(f"WARN inspection failed (non-fatal): {e}")


def _log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）——与 run_rl.py 同语义。"""
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "iter_error", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(err)[:500],
            }) + "\n")
    except OSError:
        pass


def parse_m1_eval_report(text: str) -> dict:
    """从 m1-eval 输出（stdout+stderr 合并文本）提取结果。**纯函数，可单测**。

    返回：
      winRate — 横幅 `[m1-eval] WIN RATE xx.x%` 打印在 stderr（m1-eval.ts L350
        process.stderr.write），JSON 报告在 stdout——必须合并查（it20/25 教训：
        只解析 stdout → 干净评估恒 null → 止损失效）；
      total / cleared / error — 顶层 JSON report 的 `"total": N, "outcomes": {...}`。
        逐关 perStage 数组里的 "total" 后不跟 "outcomes"，不会被误匹配。
    """
    win = None
    for line in text.splitlines():
        # 大小写不敏感（横幅曾只匹配 'winRate' 漏掉 'WIN RATE'——§27 教训）。
        m = re.search(r"win[ -]?rate[=:\s]+([\d.]+)\s*%", line, re.IGNORECASE)
        if m:
            win = float(m.group(1)) / 100
            break
    total = 0
    cleared = 0
    error = 0
    m = re.search(r'"total": (\d+),\s*"outcomes": \{([^}]*)\}', text)
    if m:
        total = int(m.group(1))
        oc = m.group(2)
        m2 = re.search(r'"stage_clear": (\d+)', oc)
        cleared = int(m2.group(1)) if m2 else 0
        m3 = re.search(r'"error": (\d+)', oc)
        error = int(m3.group(1)) if m3 else 0
    return {"winRate": win, "total": total, "cleared": cleared, "error": error}


# 干净评估最大重跑次数：error 局 > 0 就整批重跑（节点瞬态失败常见——it33 实测
# 87% error、同权重重跑即干净）。超限后接受带 error 标记的结果，不再无限重试。
CLEAN_EVAL_MAX_RETRY = 3


def run_clean_eval(bun: str, rl_path: str, args, _runner=None) -> dict:
    """m1-eval intent-exec 固定语料贪心评估（35 关 × eval_seeds/关，**派发远端 agents**）。

    seeds 固定为 1..N 与 M7② 基线同语料 → 配对可比（P1-1k3 / §245 协议）。
    --dist-nodes 把 350 局派到全部 agent（40+ 槽，~4–6min），**本机不再独占**。

    2026-08-27 §30 修订（两次教训）：① 取消『eval 本地跑』——远端算力必须被利用
    （用户：纯 eval 任务也应分派 agents）；② 触发时机从『队列清空』改为 stream 的
    on_ppo_started（PPO 启动 = 全量结算到账 + 节点空闲）——『队列清空』会撞尾局
    tail_drain → eval 350 局与 rollout 残余并行抢槽 → 大批 503 → winRate 掉到
    4.9%/6.9% → 假阳性止损（it25 实测）。PPO 本地跑、eval 远端跑、互不抢。
    ③ **结果校验 + 自动重跑**（用户指令 2026-08-28）：跑完解析 outcomes.error，
    非零即整批重跑（最多 CLEAN_EVAL_MAX_RETRY 次），杜绝假阳性胜率污染止损判定
    （it33 首跑 87% error → 同权重重跑即干净 75% 量级的实证）。
    `_runner` 供测试注入 fake runner（替换 subprocess 执行）。
    """
    seeds = args.eval_seeds
    cmd = [bun, "tools/sim/m1-eval.ts",
           "--stages", "all", "--seeds", f"1-{seeds}",
           "--difficulty", args.difficulty,
           "--policy", "intent-exec", "--intent-weights", rl_path,
           "--dist-nodes", "nn-training/rl-config.json",
           "--workers", str(max(2, min(8, args.workers)))]
    attempts = 0
    while True:
        attempts += 1
        log(f"clean eval (distributed) attempt {attempts}/{CLEAN_EVAL_MAX_RETRY}: {' '.join(cmd)}")
        if _runner is not None:
            res = _runner(cmd)
        else:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                                  timeout=3600, **_POPEN_NO_WINDOW)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"m1-eval rc={proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
            res = parse_m1_eval_report(proc.stdout + "\n" + proc.stderr)
        err = res.get("error") or 0
        if err > 0 and attempts < CLEAN_EVAL_MAX_RETRY:
            log(f"WARN clean eval attempt {attempts} had {err} error games "
                f"(total={res.get('total')}) — rerunning whole batch")
            continue
        res["retries"] = attempts - 1
        res["games"] = 35 * seeds  # 兼容 dispatch_eval_bg_intent 的 eval_summary 字段
        if err > 0:
            log(f"WARN clean eval accepted with {err} error games after {attempts} "
                f"attempts (max {CLEAN_EVAL_MAX_RETRY}) — result carries error mark")
        return res


def dispatch_eval_bg_intent(bun: str, rl_path: str, args, it: int,
                            jsonl_path: Path, baseline: float) -> threading.Thread:
    """干净评估后台线程（流式 on_collect_done 触发）：跑 m1-eval → 结果写回
    training_log.jsonl 的 eval 事件 + 记录 iter15 止损判定。返回线程句柄，
    主循环在 jsonl 写回前 join（与 run_rl dispatch_eval_bg 同语义）。"""
    def _body() -> None:
        try:
            er = run_clean_eval(bun, rl_path, args)
            ev = er.get("winRate")
            delta = (ev - baseline) if ev is not None else None
            rec = {
                "event": "eval_summary", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "winRate": round(ev, 4) if ev is not None else None,
                "games": er["games"], "baseline": baseline,
                "delta": round(delta, 4) if delta is not None else None,
            }
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            if ev is not None:
                log(f"eval it{it}: clean winRate={ev:.1%} ({er['games']} games) "
                    f"Δ vs baseline={delta:+.1%}")
            else:
                # 评估盲（横幅未命中）必须显式告警，而不是靠 f-string 对 None 抛
                # ValueError 伪装成「评估失败」（it10 实测教训：kill agent 打断评估
                # → m1-eval rc=0 无横幅 → win=None → None.__format__ 崩溃）。
                log(f"eval it{it}: WARN clean winRate=null (banner missed) — "
                    f"games={er['games']} baseline={baseline}")
        except Exception as e:  # noqa: BLE001 — 评估旁路失败不中断训练
            log(f"WARN clean eval it{it} failed (ignored): {e}")
    t = threading.Thread(target=_body, daemon=True, name=f"eval-intent-it{it}")
    t.start()
    return t


def _read_eval_summary(jsonl_path: Path, it: int) -> dict | None:
    """回读该迭代最新 eval_summary（评估线程写入）——断点/线程竞态下仍可对上。"""
    out = None
    try:
        if jsonl_path.exists():
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("event") == "eval_summary" and r.get("iter") == it:
                    out = r
    except OSError:
        pass
    return out


def main() -> None:
    os.chdir(REPO_ROOT)
    # 启动参数默认取自 rl-config.json 的 intent_rl 块（单一事实来源，见文件顶部规则）。
    # CLI 显式传参会覆盖 json 默认，用于调试；调试结束写回 json。
    rl_args = _load_intent_rl_args()

    def _d(name, fallback):
        """json 默认优先，缺失回退硬编码常量（CLI 在 argparse 层再覆盖）。"""
        return rl_args.get(name, fallback)

    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default=_d("bc", "tmp/intent-weights-Bp.json"),
                    help="B′ 意图 BC 权重（首轮 init）")
    ap.add_argument("--out", default=_d("out", "tmp/intent-rl/weights.json"),
                    help="RL 意图权重路径（每轮写回；续跑源）")
    ap.add_argument("--traj", default=_d("traj", "tmp/intent-rl"),
                    help="意图 rollout shard 根（默认 tmp/intent-rl）")
    ap.add_argument("--iters", type=int, default=_d("iters", 15))
    ap.add_argument("--start-it", type=int, default=None,
                    help="手动指定起始迭代（续跑由 training_log 自动派生；仅手动重跑时传 CLI，不进 json）")
    ap.add_argument("--rotate-stages", type=int, default=_d("rotate_stages", 35),
                    help="每轮轮转覆盖的关数（35=全量）")
    ap.add_argument("--seeds-per-stage", type=int, default=_d("seeds_per_stage", 4),
                    help="每关新鲜种子数（4 → 140 局/轮）")
    ap.add_argument("--total-stages", type=int, default=_d("total_stages", 35))
    ap.add_argument("--difficulty", default=_d("difficulty", "hard"))
    ap.add_argument("--max-ticks", type=int, default=_d("max_ticks", 12000))
    ap.add_argument("--workers", type=int, default=_d("workers", min(os.cpu_count() or 4, 12)))
    ap.add_argument("--local-slots", type=int, default=_d("local_slots", 10),
                    help="本机直接 rollout 槽位（流式下首个 PPO 波次后让位训练；0=自动 max(2,workers//4)）")
    ap.add_argument("--stream", type=int, default=_d("stream", 1),
                    help="1=流式（rollout 与 PPO 波次重叠，推荐）；0=串行")
    ap.add_argument("--epochs", type=int, default=_d("epochs", 4))
    ap.add_argument("--mb", type=int, default=_d("mb", 512))
    ap.add_argument("--lr", type=float, default=_d("lr", ppo_intent.LR))
    ap.add_argument("--seed", type=int, default=_d("seed", 7))
    ap.add_argument("--replan", type=int, default=_d("replan", 30), help="意图 replan cadence（M7① 定稿 30）")
    ap.add_argument("--warmup-iters", type=int, default=_d("warmup_iters", 1),
                    help="前 N 迭代只训 value 头（B′ 冷启动 value 随机 → 先学回报基线再动策略）")
    ap.add_argument("--kickstart-kl", type=float, default=_d("kickstart_kl", 1.0),
                    help="kickstarting KL 惩罚基础系数（plan #5；0=关闭）")
    ap.add_argument("--kickstart-decay", type=float, default=_d("kickstart_decay", 0.5),
                    help="kickstarting 系数每策略迭代衰减因子（预注册 #5：0.5/iter）")
    ap.add_argument("--keep-iters", type=int, default=_d("keep_iters", 3))
    ap.add_argument("--eval-at", default=_d("eval_at", DEFAULT_EVAL_AT),
                    help="干净评估的迭代集合（逗号分隔）")
    ap.add_argument("--eval-seeds", type=int, default=_d("eval_seeds", 10),
                    help="干净评估每关种子数（350 局/轮 @10）")
    ap.add_argument("--eval-window-sec", type=int, default=_d("eval_window_sec", 1800),
                    help="干净评估线程 join 预算；超时未结算放弃（不阻塞下一轮）")
    ap.add_argument("--baseline", type=float, default=_d("baseline", DEFAULT_BASELINE),
                    help="M7② 基线胜率（干净评估 Δ 的参照）")
    ap.add_argument("--kl-break", type=float, default=_d("kl_break", 0.6),
                    help="F4 KL 熔断阈值（意图 RL 专属；per-tick 用 breaker.py 常量 0.15）。"
                         "正常意图 KL≈0.32–0.49 不触发，仅极端漂移才拦。覆盖 json intent_rl.kl_break。")
    ap.add_argument("--kl-break-consec", type=int, default=_d("kl_break_consec", 3),
                    help="F4 KL 连续代阈值（意图 RL 专属）。覆盖 json intent_rl.kl_break_consec。")
    ap.add_argument("--out-log", default=_d("out_log", "tmp/intent-rl/train.out.log"),
                    help="stdout 落盘路径（json intent_rl.out_log；CLI 覆盖；空=仅控制台）。Tee 控制台+文件。")
    ap.add_argument("--err-log", default=_d("err_log", "tmp/intent-rl/train.err.log"),
                    help="stderr 落盘路径（json intent_rl.err_log；CLI 覆盖；空=仅控制台）。Tee 控制台+文件。")
    args = ap.parse_args()
    # 意图 rollout 语义透传给 rl.queue / rl.stream（intent_rollout 分支 + kind/replan）。
    args.intent_rollout = True
    # stdout/stderr 落盘（路径来自 json intent_rl.out_log/err_log，CLI 可覆盖调试）。
    _setup_log_redirect(args)
    # 生效启动配置落地日志（trust-but-verify：核对 json 默认是否被正确读取）。
    _log_rl_args(args, rl_args)
    # 启动前推送当前分支到 origin——远端 agent 靠 git pull 同步（§30：不 push
    # 则 agents 永远拉旧代码 → codeHash 排除 → 远端 30+ 槽闲置）。
    ensure_current_branch_pushed(REPO_ROOT)

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl_intent] bun not found on PATH — rollout needs it")

    # 权重初始化（幂等）：RL 权重不存在时从 B′ warm-start（value 头随机）。
    if not os.path.exists(args.out):
        log(f"init RL weights from B′ ({args.bc}) -> {args.out}")
        subprocess.run([sys.executable, "nn-training/ppo_intent.py",
                        "--init-from", args.bc, "--out", args.out,
                        "--threads", str(max(1, min(8, args.workers)))],
                       cwd=str(REPO_ROOT), check=True, **_POPEN_NO_WINDOW)

    device = torch.device("cpu")
    model = ppo_intent.build_rl_net(args.out)
    ppo_intent.load_intent_weights(model, args.out)
    model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    log(f"model params={sum(int(p.numel()) for p in model.parameters())}")

    # kickstarting 参考策略：B′ 策略冻结快照（warmup 冻结主干+三头 → 策略与 B′ 一致）。
    ref_model = None
    if args.kickstart_kl > 0:
        ref_model = ppo_intent.build_rl_net(args.out)
        ppo_intent.load_intent_weights(ref_model, args.out)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = traj_root / "training_log.jsonl"

    # 续跑继承 rotateSeed（与 run_rl.py 同语义）：build_pairs 是 (rotateSeed, it) 纯函数。
    prev_rs = last_rotate_seed(jsonl_path)
    if prev_rs is not None:
        rotate_seed = prev_rs
        log(f"resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
    else:
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)

    start_it = args.start_it if args.start_it is not None else last_completed_iter(jsonl_path) + 1
    eval_at = {int(x) for x in str(args.eval_at).split(",") if x.strip()}
    log(f"iters={args.iters} start={start_it} stream={args.stream} "
        f"rotate={args.rotate_stages}×{args.seeds_per_stage}/轮 difficulty={args.difficulty} "
        f"replan={args.replan} local_slots={args.local_slots} "
        f"eval_at={sorted(eval_at)} baseline={args.baseline:.1%}")
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()}, "rotateSeed": rotate_seed,
        }) + "\n")

    # 自动巡检仅对默认 traj 生效（巡检脚本读 --traj-dir 指定目录）。
    auto_inspect = True

    prev_entropy = None
    consec_fail = 0
    kl_streak = 0
    ent_streak = 0
    tripped = None
    it = start_it - 1
    stop_reason = None
    while args.iters <= 0 or it < args.iters:
        it += 1
        traj_dir = traj_root / f"it{it}"
        try:
            # 断点感知：该迭代已有 wver 匹配的完整 shard → 保留续跑；否则清空重建。
            wver = _weights_fingerprint(args.out)
            have_resume = bool(completed_pairs(traj_dir, wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(f"resume iteration {it}: keeping existing shards + PPO checkpoint")
            else:
                if traj_dir.exists():
                    shutil.rmtree(traj_dir)
                traj_dir.mkdir(parents=True)

            log(f"=== iteration {it}/{args.iters} ===")
            pairs = build_pairs(args, it, rotate_seed)
            dist_cfg = _load_dist_config()
            t_rollout = time.time()
            stream_meta = None
            eval_thread: threading.Thread | None = None
            eval_gate = threading.Event()
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                if args.stream:
                    def _fire_eval():
                        # 只在 eval_at 迭代派发干净评估（350 局约 5min，全轮跑太贵）。
                        if it not in eval_at:
                            return None
                        return dispatch_eval_bg_intent(bun, args.out, args, it,
                                                       jsonl_path, args.baseline)
                    report = run_rollout_stream(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id,
                        model, opt, device, on_collect_done=None,
                        on_ppo_started=_fire_eval,
                        backend=ppo_intent, update_kwargs=_update_kwargs(args, it, start_it,
                                                                         ref_model))
                    stream_meta = report
                else:
                    report = run_rollout_queue(bun, args.out, traj_dir, pairs, args,
                                               dist_cfg, iter_id,
                                               local_slots_max=args.local_slots)
                    if it in eval_at:
                        eval_thread = dispatch_eval_bg_intent(bun, args.out, args, it,
                                                              jsonl_path, args.baseline)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)

            kl_cum = None
            halted_flag = False
            dropped_games = None
            load_sec = None
            tail_drain_sec = None
            waves_n = None
            if stream_meta is not None:
                eval_thread = report.pop("_eval_thread", None)
                _sm = report.pop("_stream")
                rollout_sec = _sm["rollout_sec"]
                ppo_sec = _sm["ppo_sec"]
                total_steps = _sm["steps"]
                chunks_n = _sm["chunks"]
                agg = _sm["agg"]
                tail_drain_sec = _sm.get("tail_drain_sec")
                kl_cum = _sm.get("kl_cum")
                halted_flag = bool(_sm.get("halted", False))
                dropped_games = _sm.get("dropped_games")
                load_sec = _sm.get("load_sec")
                waves_n = _sm.get("waves")
            else:
                rollout_sec = round(time.time() - t_rollout, 1)
            log(f"rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} samples={report['totalSamples']} "
                f"ticks={report['totalTicks']} kills={report['totalKills']}")

            if stream_meta is None:
                t_ppo = time.time()
                episodes = ppo_intent.load_episodes_intent(str(traj_dir))
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = ppo_intent.chunk_episodes(episodes, args.mb)
                agg = ppo_intent.ppo_update_intent(
                    model, opt, chunks, args.epochs, device, seed=args.seed,
                    ckpt_path=str(traj_dir / "ppo_ckpt"),
                    **_update_kwargs(args, it, start_it, ref_model))
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None

            ppo_intent.export_intent_weights(model, args.out)
            bak = backup_weights(args.out, it, prefix="intent-rl-weights")
            log(f"ppo it{it}: steps={total_steps} chunks={chunks_n} "
                f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f}"
                + (f" early_stopped={agg['early_stopped']}" if agg and agg.get('early_stopped')
                   else ""))
            if bak:
                log(f"weights archived -> {bak}")

            # 每轮 PPO 写回后自动生成巡检 HTML。
            if auto_inspect:
                _run_inspect(bun, traj_root, it)

            # 评估线程收尾（R6 eval_gate 语义）：PPO 已收尾 → 放行本机评估参与 → join。
            eval_gate.set()
            eval_join_sec = 0.0
            if eval_thread is not None and eval_thread.is_alive():
                budget = float(args.eval_window_sec) + 60.0
                log(f"waiting up to {budget:.0f}s for clean-eval round before next "
                    f"weight distribution")
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)
            eval_rec = _read_eval_summary(jsonl_path, it)

            # pace checkpoint：iter5 首现通关。
            if it == 5 and report["winRate"] <= 0:
                log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")

            with open(jsonl_path, "a", encoding="utf-8") as f:
                dm = report.get("dimMeans") or {}
                ss = report.get("scoreStats") or {}
                rec = {
                    "event": "iteration", "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "winRate": report["winRate"], "outcomes": report["outcomes"],
                    "samples": report["totalSamples"], "ticks": report["totalTicks"],
                    "kills": report["totalKills"], "intentCounts": report["intentCounts"],
                    # v7 诊断（HTML 报告 score_mean/baseIntegrity 列）。
                    "score_mean": ss.get("mean") if ss else None,
                    "baseIntegrity": dm.get("baseIntegrity"),
                    "dim_means": dm,
                    "rollout_sec": rollout_sec, "ppo_sec": ppo_sec,
                    "steps": total_steps, "chunks": chunks_n,
                    "policy": agg["policy"] if agg else None,
                    "value": agg["value"] if agg else None,
                    "entropy": agg["entropy"] if agg else None,
                    "kl": agg["kl"] if agg else None,
                    "early_stopped": agg.get("early_stopped", False) if agg else False,
                    "kl_cum": kl_cum, "halted": halted_flag,
                    "dropped_games": dropped_games, "waves": waves_n,
                    "load_sec": load_sec, "tail_drain_sec": tail_drain_sec,
                    "eval_join_sec": eval_join_sec,
                    "eval": eval_rec,
                    "lr": args.lr, "mb": args.mb, "epochs": args.epochs,
                }
                f.write(json.dumps(rec) + "\n")

            # F4 熔断（纯逻辑 rl/breaker.py；意图 RL 用更高的 KL 阈值，避免误熔断 Bug D）。
            if agg is not None:
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak, ent_streak, kl=agg["kl"], entropy=agg["entropy"],
                    win_rate=report["winRate"],
                    kl_break=args.kl_break, kl_consec=args.kl_break_consec)
                if tripped_now is not None:
                    tripped = tripped_now
                if tripped is not None:
                    with open(jsonl_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "event": "circuit_break", "iter": it,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "reason": tripped,
                            "kl": agg["kl"], "kl_streak": kl_streak,
                            "entropy": agg["entropy"], "ent_streak": ent_streak,
                            "winRate": report["winRate"], "weights": args.out,
                        }) + "\n")
                    log(f"CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                    break
                if agg["kl"] > KL_WARN:
                    log(f"WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast")
                if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                    log(f"WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f})")
                prev_entropy = agg["entropy"]

            # iter15 止损：eval_summary 的 Δ ≤ 0 → 转 M9（P2-5，不续命）。
            if it >= STOP_AT_ITER and eval_rec and eval_rec.get("delta") is not None \
                    and eval_rec["delta"] <= 0:
                stop_reason = (f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                               f"<= 0 — stop-loss to M9")
                log(f"STOP-LOSS: {stop_reason}")
                break

            if args.keep_iters > 0:
                for old in traj_root.glob("it*"):
                    try:
                        n_old = int(old.name[2:])
                    except ValueError:
                        continue
                    if n_old <= it - args.keep_iters:
                        shutil.rmtree(old, ignore_errors=True)
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(f"it{it} FAILED (SystemExit: {e}); consecutive={consec_fail}/5 — retry")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1
        except Exception as e:  # noqa: BLE001 — 失败原地重试（不静默跳轮丢语料）
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(f"it{it} FAILED ({type(e).__name__}: {e}); consecutive={consec_fail}/5 — retry")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl_intent] ALL DONE -> {args.out}")


def _weights_fingerprint(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_dist_config():
    import dist_common
    return dist_common.load_dist_config()


def _load_intent_rl_args() -> dict:
    """启动参数默认来源：rl-config.json 的 intent_rl 块（单一事实来源）。

    见文件顶部「启动参数单一事实来源」规则。加载失败（缺文件/坏 JSON/路径异常）时
    返回空 dict，argparse 回退到硬编码常量，训练仍可离线启动。
    """
    try:
        cfg = _load_dist_config()
        return dict(cfg.get("intent_rl", {}) or {})
    except Exception as e:  # noqa: BLE001
        log(f"WARN cannot load intent_rl args from rl-config.json ({e}); "
            f"falling back to hardcoded defaults")
        return {}


# intent_rl 块中受管的启动参数名（与 argparse dest 一一对应）。
_RL_ARG_KEYS = ("bc", "out", "traj", "iters", "rotate_stages", "seeds_per_stage",
                "total_stages", "difficulty", "max_ticks", "workers", "local_slots",
                "stream", "epochs", "mb", "lr", "seed", "replan", "warmup_iters",
                "kickstart_kl", "kickstart_decay", "keep_iters", "eval_at",
                "eval_seeds", "eval_window_sec", "baseline", "kl_break",
                "kl_break_consec", "out_log", "err_log")


def _log_rl_args(args, rl_args: dict) -> None:
    """生效启动配置落地日志：标注每个参数来源（json / fallback），便于核对单一事实来源。"""
    src = {k: ("json" if k in rl_args else "fallback") for k in _RL_ARG_KEYS}
    log(f"[launch] intent_rl args source: " + " ".join(f"{k}={src[k]}" for k in _RL_ARG_KEYS))
    log(f"[launch] intent_rl effective: " + json.dumps(
        {k: getattr(args, k) for k in _RL_ARG_KEYS}, default=str))


class _Tee:
    """同时写多个流（控制台 + 文件），供长训日志持久化且终端仍可见。"""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
            except Exception:  # noqa: BLE001
                pass

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self) -> bool:
        return False


def _setup_log_redirect(args) -> None:
    """stdout/stderr 重定向到 json 配置的 out_log/err_log（Tee 控制台+文件）。

    路径来自 intent_rl 块（CLI --out-log/--err-log 可覆盖调试）。空字符串=仅控制台。
    落盘采用追加模式 + 启动横幅，多次启动日志累积且可按时间轴复盘。
    """
    if args.out_log:
        try:
            p = Path(args.out_log)
            p.parent.mkdir(parents=True, exist_ok=True)
            sys.stdout = _Tee(sys.stdout, open(p, "a", encoding="utf-8"))
            log(f"[launch] stdout -> {p} (tee console+file, append)")
        except Exception as e:  # noqa: BLE001
            log(f"WARN cannot redirect stdout to {args.out_log}: {e}")
    if args.err_log:
        try:
            pe = Path(args.err_log)
            pe.parent.mkdir(parents=True, exist_ok=True)
            sys.stderr = _Tee(sys.stderr, open(pe, "a", encoding="utf-8"))
            log(f"[launch] stderr -> {pe} (tee console+file, append)")
        except Exception as e:  # noqa: BLE001
            log(f"WARN cannot redirect stderr to {args.err_log}: {e}")


def _update_kwargs(args, it: int, start_it: int, ref_model):
    """ppo_intent 更新参数：value 预热（前 warmup-iters）+ kickstarting KL（系数衰减）。"""
    warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
    policy_iter = (it - start_it) - args.warmup_iters + 1
    kl_coef = args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1)) \
        if args.kickstart_kl > 0 and policy_iter >= 1 else 0.0
    return {"value_warmup_epochs": warmup_epochs, "ref_model": ref_model, "kl_coef": kl_coef,
            "seed": args.seed}


if __name__ == "__main__":
    main()
