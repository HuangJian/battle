"""run_rl.py — RL on-policy 主循环入口（P1.5 蒸馏 → RL 阶段）。

工程结构（2026-08-25 工程化重组）：编排逻辑抽取至 rl/ 包 ——
  rl/course.py        课程（build_pairs / parse_range）
  rl/queue.py         中央队列调度 + 纯本地回退（run_rollout_queue / run_rollout）
  rl/stream.py        流式迭代（run_rollout_stream / wave_params）
  rl/eval_dispatch.py 干净评估分发（dispatch_eval_bg 等）
  rl/resume.py        断点对账（completed_pairs / resumed_manifests / jsonl 锚点）
  rl/reports.py       报告聚合（combine_reports / win_of）
  rl/breaker.py       F4 熔断纯逻辑（阈值 + 连击判定）
本文件只保留：CLI、迭代主循环、权重初始化/归档、巡检与熔断停车。

流程：
  ① 权重初始化（幂等）：RL 权重不存在时，从 DAgger BC 检查点 warm-start 策略头
     （价值头随机初始化）；已存在则直接续跑。
  ② 迭代 N 次：bun TS rollout（subprocess，无需 torch）→ 进程内 clipped PPO 更新
     （复用 ppo.py 的 GAE/minibatch/更新函数，模型常驻内存）→ 原子写回权重文件，
     下一轮 rollout 即用新权重（标准 on-policy）。

经统一启动器进入（venv/torch 由它保证）：
  bash nn-training/start-training.sh --script run_rl.py --iters 15 --stream 1
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl.py --iters 15          # --xxx 参数原样透传

单步调试仍可用 ppo.py 的 --init-from / --resume CLI；回归测试见 test_run_rl.py。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口反复弹出抢焦点。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW  # noqa: E402

import torch

import dist_common
import ppo as ppo_mod
from weights_io import load_state_into, save_weights_json

# rl/ 包：编排逻辑单点实现（本文件以下仅存 CLI + 主循环 + 权重归档/巡检）
from rl.log import log
from rl.course import build_pairs, parse_range, curriculum_active_count  # noqa: F401
from rl.reports import combine_reports, win_of as _win_of  # noqa: F401
from rl.resume import (completed_pairs, last_completed_iter,  # noqa: F401
                       last_rotate_seed, resumed_manifests)
from rl.queue import (MAX_TASK_ATTEMPTS, REPO_ROOT, RUN_ID,  # noqa: F401
                      ROLLOUT_LOG_EVERY, bun_version as _bun_version,
                      mm as _mm, run_rollout, run_rollout_queue)
from rl.stream import run_rollout_stream, wave_params  # noqa: F401
from rl.eval_dispatch import (EVAL_ITER_SUFFIX, EVAL_SEEDS,  # noqa: F401
                              EVAL_TASK_ATTEMPTS, dispatch_eval_bg,
                              dispatch_eval_round, eval_done_keys as _eval_done_keys,
                              report_winrate_safe)
from rl.breaker import (CIRCUIT_EXIT_CODE, ENT_BREAK, ENT_BREAK_CONSEC,
                        ENT_BREAK_MAX_WINRATE, ENT_COLLAPSE_DROP, KL_BREAK,
                        KL_BREAK_CONSEC, KL_WARN, breaker_update)

# Per-iteration weights archive (user request 2026-08-24): every completed PPO
# write-back is copied into nn-training/weights/ with an identifiable name.
WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # bounded archive: prune oldest it-backups beyond this


def _run_collect_only(args, traj_root, rotate_seed, bun) -> None:
    """吞吐 T4：仅采集一轮落盘后退出（双缓冲子进程模式）。不 PPO/不 eval/不写权重。
    落盘 shard 的 manifest.wver = 快照权重指纹 → 主进程下一轮 completed_pairs 命中走聚合。"""
    it = args.start_it or 1
    traj_dir = traj_root / f"it{it}"
    traj_dir.mkdir(parents=True, exist_ok=True)
    pairs = build_pairs(args, it, rotate_seed)
    dist_cfg = dist_common.load_dist_config()
    # iter_id 必须遵循 "{RUN_ID}.{it}" —— run_rollout_queue 用 rsplit('.',1)[-1]
    # 做 int() 解析迭代号（2026-08-31 实测：'collect-1-<pid>' 格式直接 ValueError
    # 崩崩，分布式路径的子进程从未成功采集过一轮，等于双缓冲静默失效）。
    iter_id = f"{RUN_ID}.{it}"
    log(f"[collect-only] it{it}: {len(pairs)} pairs -> {traj_dir} (weights={args.bc})")
    if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
        report = run_rollout_queue(bun, args.bc, traj_dir, pairs, args, dist_cfg, iter_id)
    else:
        report = run_rollout(bun, args.bc, traj_dir, pairs, args)
    log(f"[collect-only] it{it}: games={report['games']} samples={report['totalSamples']} "
        f"outcomes={json.dumps(report['outcomes'])}")


def _spawn_collect_next(args, it) -> subprocess.Popen | None:
    """吞吐 T4：后台子进程预采 it+1（θ_N 行为快照）。返回 Popen 或 None。

    正确性：子进程用 args.out 的**快照**（θ_N）——下轮 PPO 若写回 args.out 不回影响
    子进程（它读 snap 文件）；shards 自带 θ_N 采样的 lp → IS 分母天然正确（on-policy）。"""
    import shutil

    next_it = it + 1
    if args.iters > 0 and next_it > args.iters:
        return None
    snap = str(Path(args.out).with_name(f"weights-collect-{next_it}.json"))
    try:
        shutil.copyfile(args.out, snap)
    except OSError as e:  # noqa: BLE001
        log(f"[double-buffer] snapshot fail: {e} — skip precollect")
        return None
    argv = [sys.executable, "-u", os.path.abspath(__file__),
            *sys.argv[1:], "--collect-only", "1", "--bc", snap,
            "--start-it", str(next_it), "--iters", "1"]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(argv, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    except Exception as e:  # noqa: BLE001
        log(f"[double-buffer] spawn fail: {e} — skip precollect")
        return None
    log(f"[double-buffer] precollect it{next_it} spawned pid={p.pid} snapshot={snap}")
    return p


def build_model(bc_path: str, rl_path: str) -> torch.nn.Module:
    """Init once: warm-start policy heads from BC when no RL weights exist yet;
    otherwise resume from the existing RL weights (policy + trained value).
    The init path SAVES the merged weights to rl_path before returning — the
    TS rollout reads that file, so it must exist before iteration 1."""
    resume = os.path.exists(rl_path)
    src = rl_path if resume else bc_path
    model = ppo_mod.build_ppo(src)
    load_state_into(model, src)
    if not resume:
        # goal-nn 卡 A4（2026-08-30 最终版）：BC 权重有两个 PPO 不可消费的量级问题——
        # ① BC 训练动态把 ConvMixer trunk 激活放大到真实局面上 ~千级（合成探针会
        #    低估百倍，必须用真实 shard obs 校准）；
        # ② 策略头 logits ±7600 ⇒ 采样近 one-hot、熵≈0.01，PPO 无法探索也无法
        #    消费（kl 一次更新爆 3 万）。
        # 处置（warm_start_normalize，见下）：真实 obs 校准 trunk→h≈15；
        # move/fire 头缩到 logit 范围 ~3（保 argmax、软先验、熵≈1）；value 头清零。
        import numpy as np
        import torch

        def _sample_real_obs(n: int = 16) -> torch.Tensor:
            """真实 obs 校准样本：多个最近 shard 各取一层 + 合成极端（全零/全亮/条纹），
            取并集——单一 shard 可能是退化样本（全暗 obs 曾让 feat_max=1，α 放大 14x
            把已归一的 trunk 再抬爆，2026-08-30 s1-cap 首启实测）。"""
            import glob
            import os

            paths = sorted(
                glob.glob(str(REPO_ROOT / "tmp" / "*" / "it*" / "**" / "obs.npy"),
                          recursive=True),
                key=os.path.getmtime,
                reverse=True,
            )[:8]
            chunks: list[torch.Tensor] = []
            for p_ in paths:
                try:
                    arr = np.load(p_, mmap_mode="r")
                    if arr.ndim == 4 and arr.shape[1] == 14 and arr.shape[0] >= 1:
                        chunks.append(torch.from_numpy(np.ascontiguousarray(arr[:n])))
                except Exception:
                    continue
            synth = torch.zeros(3, 14, 26, 26, dtype=torch.uint8)
            synth[1] = 255
            synth[2, :, ::2] = 255
            chunks.append(synth)
            return torch.cat(chunks, dim=0)

        def warm_start_normalize(model: torch.nn.Module) -> None:
            TRUNK = ("stem.", "blocks.", "fc.")
            sample = _sample_real_obs(32)
            sc = torch.zeros(sample.shape[0], 19)

            def _feat_max() -> float:
                with torch.no_grad():
                    return float(model.features(sample, sc).abs().max()) + 1e-6

            def _logit_max() -> float:
                with torch.no_grad():
                    mv, fr, _v = model(sample, sc)
                return max(float(mv.abs().max()), float(fr.abs().max())) + 1e-6

            alpha = 15.0 / _feat_max()
            with torch.no_grad():
                for n, p_ in model.named_parameters():
                    if n.startswith(TRUNK):
                        p_.mul_(alpha)
            beta = 3.0 / _logit_max()  # 保 argmax 的软先验：logit 范围 ~3（熵≈1.2）
            with torch.no_grad():
                for n, p_ in model.named_parameters():
                    if n.startswith(("move_head.", "fire_head.")):
                        p_.mul_(beta)
                    elif n.startswith("value_head."):
                        p_.zero_()
            print(f"[run_rl] BC warm-start normalize: trunk x{alpha:.4g}, "
                  f"policy heads x{beta:.4g} (logit range -> 3.0 soft prior), value zeroed; "
                  f"feat_max={15.0 / alpha:.0f}, logit_max_pre={3.0 / beta:.1f}")

        warm_start_normalize(model)
        save_weights_json(model, rl_path)
    print(
        f"[{time.strftime('%H:%M:%S')}] [run_rl] "
        + ("resume" if resume else "init")
        + f" weights <- {src} "
        f"(params={sum(int(p.numel()) for p in model.parameters())})"
        + ("" if resume else f" -> {rl_path}")
    )
    return model


def backup_weights(weights_path: str, it: int, prefix: str = "rl-weights") -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    Name: <prefix>.it<N>.<YYYYMMDD-HHMMSS>.json — iteration-first so the
    archive sorts by training progress at a glance; the timestamp disambiguates
    re-runs of the same iter. Deliberately NOT matching weights_io's strict
    `weights.<ts>_ep<N>_val<V>.json` BC auto-discovery regex (same reason the
    manual `rl-weights.*_post-it*ppo.json` backup avoided it): eval_bridge's
    latest_weights_path must never pick up RL archives. Less-recent pruned
    beyond WEIGHTS_BACKUP_KEEP; non-fatal on any IO error.
    prefix（工程化共享）：run_rl_intent 用 'intent-rl-weights' 独立前缀，与 per-tick
    RL 归档分桶（各自按前缀 prune，互不干扰）。

    2026-08-27 §30 修复 prune 排序：原来是 `sorted(glob)`（按**文件名**字典序）取最小
    一批删——而名字 `it2 < it20 < it3`（'2'<'3'），字典序最小 ≠ 最旧，导致重启后轮
    it20–23 的最新多样权重被当"最旧"误删、12:00 时代的老 it3–9 反而幸存（it21–24
    多样 checkpoint 永久丢失的根因）。改为按 **st_mtime**（真实新旧）排序删最旧，
    名字时间戳只作同 mtime 兜底。"""
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = WEIGHTS_BACKUP_DIR / f"{prefix}.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(weights_path, dst)
        baks = sorted(
            WEIGHTS_BACKUP_DIR.glob(f"{prefix}.it*.json"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        if len(baks) > WEIGHTS_BACKUP_KEEP:
            for old in baks[:len(baks) - WEIGHTS_BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        return str(dst)
    except OSError as e:
        log(f"[run_rl] WARN weights backup failed (non-fatal): {e}")
        return None


def ensure_current_branch_pushed(repo_root: Path) -> str | None:
    """启动前把当前 git 分支 push 到 origin——远端 agent 的 upgrade 是 `git pull`，
    拉的是 **origin**，本地 ahead 的 commit 不 push，agents 永久拉旧代码 →
    codeHash 对不上被排除 → 训练全程本机独扛、远端 30+ 槽闲置（§30 实测教训）。
    push 失败（离线/无远端/无 upstream）仅告警不中断——本地训练不依赖远端。
    返回 push 的分支名（失败返回 None）。"""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_root),
            capture_output=True, text=True, timeout=30
        ).stdout.strip()
        if not branch or branch == "HEAD":
            return None
        r = subprocess.run(["git", "push", "origin", branch], cwd=str(repo_root),
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log(f"[run_rl] pushed {branch} -> origin (agents can git-pull to sync)")
            return branch
        log(f"[run_rl] WARN git push {branch} failed (rc={r.returncode}): "
            f"{(r.stderr or r.stdout)[-200:]} — remote agents may stay stale")
    except Exception as e:  # noqa: BLE001 — 非致命：不阻断本地训练
        log(f"[run_rl] WARN git push skipped: {e}")
    return None


def _log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）。

    此前失败详情只进易失 stdout——detach 启动下不可见，it2/it3 连续跳轮时
    无任何可复盘痕迹。观测必须自带牙齿：last_completed_iter 只认 iteration
    事件，iter_error 不影响断点续跑定位。
    """
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "iter_error", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(err)[:500],
            }) + "\n")
    except OSError:
        pass


def _run_inspect(bun: str, it: int) -> None:
    """每轮 PPO 写回后自动生成巡检 HTML（rl-hourly-inspect.ts）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    仅当 traj 为默认 tmp/rl-traj 时生效（巡检脚本读固定 TRAJ_DIR）。
    """
    try:
        subprocess.run(
            [bun, "tools/diag/rl-hourly-inspect.ts", "--up-to", str(it)],
            cwd=str(REPO_ROOT), timeout=180, capture_output=True, text=True,
            **_POPEN_NO_WINDOW)
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:  # noqa: BLE001 — 巡检失败不中断训练
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")


def main() -> None:
    # Anchor cwd to the repo root (parent of nn-training/): all default paths
    # (tmp/student-weights-dagger, tmp/rl-weights, tmp/rl-traj) are repo-root
    # relative. Required for start-training.ps1 --detach, whose WorkingDirectory
    # is nn-training/ — same pattern as train_loop.py's REPO_ROOT.
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # 启动参数默认取自 rl-config.json 的 rl 块（与 run_rl_intent 的 intent_rl 块
    # 同一模式——单一事实来源；CLI 显式传参覆盖 json 默认）。
    try:
        _rl_args = json.loads((REPO_ROOT / "nn-training" / "rl-config.json").read_text(encoding="utf-8")).get("rl", {}) or {}
    except Exception:
        _rl_args = {}

    def _d(name, fallback):
        return _rl_args.get(name, fallback)

    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default="tmp/student-weights-dagger/weights.json",
                    help="BC checkpoint to warm-start from (first init only)")
    ap.add_argument("--out", default="tmp/rl-weights/weights.json",
                    help="RL weights path (written every iteration; also the resume source)")
    ap.add_argument("--traj", default="tmp/rl-traj", help="trajectory root dir")
    ap.add_argument("--iters", type=int, default=15,
                    help="iterations to run; 0 = infinite (stop via --max-hours or Ctrl-C)")
    ap.add_argument("--start-it", type=int, default=None,
                    help="resume iteration index (default: auto — last completed iteration in "
                         "training_log.jsonl + 1, so restarts continue where they stopped)")
    ap.add_argument("--stages", default="0-3", help="explicit stage range (ignored in rotate mode)")
    ap.add_argument("--seeds", default="0-3", help="explicit seed range (ignored in rotate mode)")
    ap.add_argument("--seed-rotate", type=int, default=0,
                    help="explicit 模式 seed 轮转：>0 时每迭代对 --stages 每关抽 N 个全新 "
                         "seed（(rotateSeed,it) 键控、断点复现）；0 = 固定 --seeds（旧行为）")
    ap.add_argument("--rotate-stages", type=int, default=_d("rotate_stages", 0),
                    help=">0: rotate through ALL stages this many per iteration "
                         "(iteration i uses stages [(i-1)*N %% 35 ...]); seeds are drawn "
                         "fresh every iteration from a (seed, iter)-derived RNG")
    ap.add_argument("--seeds-per-stage", type=int, default=10,
                    help="random seeds per stage in rotate mode")
    ap.add_argument("--total-stages", type=int, default=_d("total_stages", 35),
                    help="stage count for rotate mode (repo has 35)")
    ap.add_argument("--curriculum-stages", default="",
                    help="curriculum mode: easy→hard ordered stage list (e.g. "
                         "'13,1,16,8,21,4,15,31,0,29,33,...'). Non-empty enables it: each "
                         "iteration samples only the active window (first N stages), N grows "
                         "deterministically with it (see --curriculum-every). Recommended "
                         "ordering = per-stage eval win rate desc (2026-08-25 audit).")
    ap.add_argument("--curriculum-start", type=int, default=4,
                    help="curriculum initial active-stage count")
    ap.add_argument("--curriculum-every", type=int, default=8,
                    help="curriculum: expand every N iterations (0 = never expand)")
    ap.add_argument("--curriculum-grow", type=int, default=4,
                    help="curriculum: +G stages per expansion step")
    ap.add_argument("--difficulty", default=_d("difficulty", "hard"))
    ap.add_argument("--max-ticks", type=int, default=_d("max_ticks", 12000))
    # goal-nn 卡 A2：玩具奖励臂覆盖（''=按 stage 解析：arena→级默认臂，真实关→v7；
    # 'toy:<arm>' 强制玩具臂用于扫参，'v7' 强制 v7）。经 queue/agent 透传到导出器。
    ap.add_argument("--reward", default="",
                    help="rollout reward override: '' (stage-derived), 'v7', or 'toy:<arm>'")
    # goal-nn 卡 A3：dodge 模式覆盖（''=按 stage 解析：arena→l0，真实关→off；
    # 'off'|'l0'|'god' 强制，'god' 仅 A/B 报告用）。经 queue/agent 透传到导出器。
    ap.add_argument("--dodge", default="",
                    help="dodge override: '' (stage-derived), 'off', 'l0', or 'god'")
    ap.add_argument("--workers", type=int, default=_d("workers", min(os.cpu_count() or 4, 12)),
                    help="concurrent bun rollout workers (games partitioned by seed)")
    ap.add_argument("--local-slots", type=int, default=_d("local_slots", 0),
                    help="trainer direct-thread slots (stream mode). R6 schedule: "
                         "first-dispatched during collection; suspend once PPO waves "
                         "begin (auto-resume if the whole cluster stalls); join eval "
                         "remainder after PPO. 0 = auto (max(2, workers//4))；默认取 "
                         "rl-config 的 rl.local_slots")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=_d("mb", 512),
                    help="minibatch size — 512 halves gradient steps vs 256 "
                         "(faster PPO, smaller per-iteration KL drift)")
    ap.add_argument("--lr", type=float, default=ppo_mod.LR)
    ap.add_argument("--seed", type=int, default=_d("seed", 7))
    ap.add_argument("--max-hours", type=float, default=0.0,
                    help="wall-clock budget in hours; checked between iterations; 0 = unlimited")
    ap.add_argument("--keep-iters", type=int, default=_d("keep_iters", 3),
                    help="keep only the last N trajectory dirs (disk bound); 0 = keep all")
    ap.add_argument("--stream", type=int, default=_d("stream", 1),
                    help="1（默认，AGENTS §15.6）= 流式迭代：采集与 PPO 波次重叠，集群不在 PPO 窗口闲置；"
                         "0 = 串行（采集全部完成后再统一 PPO）——仅调试/归因用")
    ap.add_argument("--eval-stages", default="",
                    help="干净评估语料（goal-nn）：'' = 真实关 0..total_stages-1（旧行为）；"
                         "传关卡规格如 '1000-1002' = arena 训练场自评（OOD 信号）")
    ap.add_argument("--eval-games-per-stage", type=int, default=2,
                    help="干净评估：每关固定种子贪心局数（0=关闭）。rollout 收官后的 PPO 空窗期 "
                         "分发到全部 ping.evalSupport 节点；结果追加 tmp/rl-traj/eval_log.jsonl")
    ap.add_argument("--eval-window-sec", type=int, default=_d("eval_window_sec", 1500),
                    help="干净评估线程的墙钟预算；超时未结算的局放弃（不阻塞 PPO 与下一轮）")
    ap.add_argument("--eval-every", type=int, default=_d("eval_every", 1),
                    help="干净评估稀疏化（吞吐 T3）：每 N 轮跑一次 eval。1 = 每轮（默认，字节一致）；N>1 = 非 eval 轮不派发不 join（集群尾段留给下一轮采集/双缓冲）。判门频率随之降为每 N 轮，判据不变（plan/goal-nn-throughput.md）。")
    ap.add_argument("--eval-at", default=_d("eval_at", ""),
                    help=("干净评估绝对迭代点集（复用 run_rl_intent 的 eval_at 语义，如 "
                          "'5,10,15,20'）：只在列出的迭代派发 eval；空 = 关闭该维（配合 "
                          "--eval-every 或默认每轮）。与 --eval-every 可叠加（两者都满足才跑）。"))
    ap.add_argument("--double-buffer", type=int, default=_d("double_buffer", 0),
                    help="吞吐 T4：双缓冲——本轮 PPO 收尾后 spawn 后台 collect-only 子进程预采"
                         "下一轮（行为快照 θ_N，子进程读快照不读 args.out，防权重写回污染）；"
                         "下轮开头 join 子进程后直接走盘上 shard 聚合重放（藏掉采集墙钟）。"
                         "依赖 T3（--eval-at/--eval-every 释放集群尾段）。默认 0 = 原行为字节一致。")
    ap.add_argument("--collect-only", type=int, default=0,
                    help="内部：仅采集一轮落盘后退出（T4 双缓冲子进程模式；不 PPO/不 eval/不写权重）。")
    args = ap.parse_args()

    import numpy as np

    np.random.seed(args.seed)

    # 启动前推送当前分支到 origin（远端 agent 靠 git pull 同步——§30 教训）。
    # 2026-08-30 事故修复（用户指令）：节点的远控升级分支**永远用训练机当前分支**，
    # 不再读 rl-config 的 upgradeBranch（残留旧战役分支名曾把全部节点 reset 回
    # 31 个提交前的 intent-ai）。config 键仅作 push 失败时的最后回退。
    pushed_branch = ensure_current_branch_pushed(REPO_ROOT)
    _current_branch = (
        subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=30).stdout.strip()
    )
    if _current_branch and _current_branch != "HEAD":
        import dist_common as _dc
        _dc.set_upgrade_branch(_current_branch)
        log(f"[run_rl] node upgrade branch locked to training-machine branch: {_current_branch}")

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl] bun not found on PATH — rollout needs it")

    # ===== 吞吐 T4：collect-only 分支必须在 build_model 之前（子进程无需 torch 模型/权重）=====
    _traj_root = Path(args.traj)
    _traj_root.mkdir(parents=True, exist_ok=True)
    _jpath = _traj_root / "training_log.jsonl"
    _prs = last_rotate_seed(_jpath)
    _rseed = _prs if _prs is not None else (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)
    if getattr(args, "collect_only", 0):
        _run_collect_only(args, _traj_root, _rseed, bun)
        log("[run_rl] collect-only done — exit")
        return 0
    # ===== 双缓冲：collect-only 分支结束 =====

    device = torch.device("cpu")
    model = build_model(args.bc, args.out)
    model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = traj_root / "training_log.jsonl"

    # 续跑继承 rotateSeed：已有 run_start 历史 → 沿用其 rotateSeed（课程连续 → it 续跑时
    # 下轮 (stage,seed) 与已落盘局一致 → 断点续跑剔除生效，不重跑已完成局）。
    # 全新开始（无 jsonl 历史，例如用户清空重建）才用当前时刻抖动种子。
    prev_rs = last_rotate_seed(jsonl_path)
    if prev_rs is not None:
        rotate_seed = prev_rs
        log(f"[run_rl] resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
    else:
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)
    # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
    # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()},
            "rotateSeed": rotate_seed,
        }) + "\n")

    log(f"[run_rl] iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (f"curriculum={args.curriculum_stages} start={args.curriculum_start} "
           f"every={args.curriculum_every} grow={args.curriculum_grow}"
           if args.curriculum_stages else
           f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
           f"of {args.total_stages} (full coverage every "
           f"{-(-args.total_stages // args.rotate_stages)} iters)" if args.rotate_stages > 0
           else f"stages={args.stages} seeds={args.seeds}")
        + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
        f"workers={args.workers} keepIters={args.keep_iters}")
    log(f"training_log: {jsonl_path}")
    log(f"[run_rl] runId={RUN_ID}")

    # 自动巡检仅对默认 traj 生效（巡检脚本读固定 tmp/rl-traj 的 TRAJ_DIR）
    auto_inspect = traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
    if auto_inspect:
        log("[run_rl] per-iteration auto-inspection ENABLED (HTML report after each PPO)")

    deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
    total = "∞" if args.iters <= 0 else str(args.iters)
    prev_entropy = None
    consec_fail = 0
    kl_streak = 0   # F4: consecutive iters with kl >= KL_BREAK
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < MAX_WINRATE
    tripped = None
    # it 断点续跑：--start-it 显式，否则自动 = 日志最后一个完成迭代 + 1
    start_it = args.start_it if args.start_it is not None else \
        (last_completed_iter(jsonl_path) + 1)
    if start_it > 1:
        log(f"[run_rl] resume: continuing from iteration {start_it} "
            f"(weights resume from {args.out})")
    it = start_it - 1
    # 吞吐 T3：eval 稀疏化周期（默认 1 = 每轮，字节一致；>1 = 每 N 轮一次）。
    eval_every = int(getattr(args, "eval_every", 1) or 1)
    # 吞吐 T3：eval 绝对迭代点集（复用 run_rl_intent 的 eval_at 语义；空 = 不启用该维）。
    eval_at_set = {int(x) for x in str(getattr(args, "eval_at", "") or "").split(",") if x.strip()}
    _collect_child: subprocess.Popen | None = None  # 吞吐 T4：预采子进程句柄（下一轮开头 join）
    while args.iters <= 0 or it < args.iters:
        it += 1
        # 吞吐 T4：本轮开头 join 预采子进程（bounded）——shards 全量落盘后走盘上聚合路径，
        # 采集墙钟藏进上一轮 PPO 尾段；子进程失败/超时则回退本轮自采（completed_pairs 空）。
        if _collect_child is not None:
            try:
                _collect_child.wait(timeout=3600)
                log(f"[double-buffer] precollect it{it} done rc={_collect_child.returncode}")
            except subprocess.TimeoutExpired:
                log(f"[double-buffer] precollect it{it} timeout — fall back to own collect")
                _collect_child.terminate()
            finally:
                _collect_child = None
        if deadline is not None and time.time() >= deadline:
            log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
            break
        traj_dir = traj_root / f"it{it}"
        try:
            # rollout/PPO 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
            # 保留续跑（跳过已完成局 + 续 PPO checkpoint）；否则清空重建。
            wver = dist_common.weights_fingerprint(args.out)
            have_resume = bool(completed_pairs(traj_dir, wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(f"[run_rl] resume iteration {it}: keeping existing shards + PPO checkpoint")
            else:
                if traj_dir.exists():
                    shutil.rmtree(traj_dir)
                traj_dir.mkdir(parents=True)

            log(f"[run_rl] === iteration {it}/{total} ===")
            pairs = build_pairs(args, it, rotate_seed)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            # 吞吐 T3：本轮是否派发干净评估（eval_games>0 ∧ 周期命中 ∧ 绝对点命中）。
            # stream 的 _fire_eval 闭包与本分支共用；非 eval 轮 = 不派发不 join。
            eval_on_round = (
                int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                and (eval_every <= 1 or it % eval_every == 0)
                and (not eval_at_set or it in eval_at_set))
            t_rollout = time.time()
            stream_meta = None
            dist_iter_id: str | None = None
            eval_thread: threading.Thread | None = None
            eval_gate: threading.Event | None = None  # R6：PPO 收尾后放行本地 eval 参与
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                dist_iter_id = iter_id
                enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
                log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
                # 本地 eval 参与的门控事件：派发即创建，PPO/采集收尾时 set 放行
                eval_gate = threading.Event()
                if int(getattr(args, "stream", 0) or 0):
                    def _fire_eval():
                        # 触发点在中央派发队列清空瞬间（on_queue_drained →
                        # _fire_eval_once）：全部采集任务已派到节点、结果仍在途，
                        # 评估局顺势填补收尾空槽（2026-08-25 用户修订）。
                        # 线程句柄经报告回传主循环，jsonl 写回前 join——下轮新权重
                        # 分发前评估必已收官或到预算。positional args 创建即快照，
                        # 无闭包竞态。eval_gate 随闭包捕获：PPO 收尾时 set 放行本地。
                        # 吞吐 T3：非 eval 轮不派发（返回 None → 无 eval_thread →
                        # 下方 join 跳过，集群尾段留给下一轮采集/双缓冲）。
                        if not eval_on_round:
                            return None
                        return dispatch_eval_bg(bun, args.out, traj_dir, args, dist_cfg,
                                                iter_id, it, local_gate=eval_gate)
                    report = run_rollout_stream(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id,
                        model, opt, device, on_collect_done=_fire_eval)
                    stream_meta = report
                else:
                    report = run_rollout_queue(bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id)
                    # 串行：rollout 返回即 collector 收官；后台评估藏进随后的长 PPO 空窗
                    # 吞吐 T3：非 eval 轮不派发（eval_on_round 循环级统一门控）。
                    if eval_on_round:
                        eval_thread = dispatch_eval_bg(bun, args.out, traj_dir, args, dist_cfg,
                                                       iter_id, it, report["winRate"],
                                                       local_gate=eval_gate)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)

            kl_cum = None
            halted_flag = False
            dropped_games = None
            load_sec = None
            tail_drain_sec = None
            waves_n = None
            if stream_meta is not None:
                # 流式评估线程句柄随报告回传（R4）：jsonl 写回前 join。
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
            log(f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}")
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}")
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            if stream_meta is None:
                t_ppo = time.time()
                episodes = ppo_mod.load_episodes(str(traj_dir))
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = ppo_mod.chunk_episodes(episodes, args.mb)
                # PPO epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
                agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, device,
                                         ckpt_path=str(traj_dir / "ppo_ckpt"))
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径
            save_weights_json(model, args.out)
            bak = backup_weights(args.out, it)
            log(f"[run_rl] ppo it{it}: steps={total_steps} chunks={chunks_n} "
                + (f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                   f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}"
                   if agg is not None else
                   "metrics n/a — PPO checkpoint completed by previous process"))
            if bak:
                log(f"[run_rl] weights archived -> {bak}")

            # v3.12 eval 延迟化（用户 2026-08-31）：eval **不阻塞训练主链**。eval 冻结本轮
            # 权重、由后台线程独立写 eval_log（dispatch_eval_bg 返回 daemon 线程），账按
            # wver 晚入（eval_done_keys 按 wver16 去重，晚到不重跑）。此处只做短软等待
            # （吃已收官尾巴 + 给在途 eval 局一段缓存缓冲，防止下轮新权重 POST purge 掐
            # 掉），长尾 eval 项留在 it+1..N 的采集/PPO 空档消化——节点任务队列天然仲裁
            # （采集忙则 eval 排队，采集 done 则 eval 补做）。
            # 门判定读 eval_log 的 eval_summary（iter 字段保留原轮号 + wver），晚入账只
            # 让判定窗口顺延，判据不变。溢出预算未收官的在途局由下轮异 sha 清场 + 阈值
            # 熔断兜底（与 v3.10 前语义一致）。
            if eval_gate is not None:
                eval_gate.set()
            eval_join_sec = 0.0
            if eval_thread is not None and eval_thread.is_alive():
                budget = float(getattr(args, "eval_window_sec", 900)) + 60.0
                soft = min(budget, 180.0)  # 软等待上限：吃尾巴 + 缓存缓冲，不再全额等账
                log(f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                    f"(remaining eval finishes in background, wver-keyed)")
                _t_join = time.time()
                eval_thread.join(timeout=soft)
                eval_join_sec = round(time.time() - _t_join, 1)

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(json.dumps({
                    "event": "iteration", "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "winRate": report["winRate"], "outcomes": report["outcomes"],
                    "score_mean": report.get("scoreStats", {}).get("mean"),
                    "score_std": report.get("scoreStats", {}).get("std"),
                    "dim_means": report.get("dimMeans", {}),
                    "samples": report["totalSamples"], "ticks": report["totalTicks"],
                    "rollout_sec": rollout_sec, "ppo_sec": ppo_sec,
                    "steps": total_steps, "chunks": chunks_n,
                    "policy": agg["policy"] if agg else None,
                    "value": agg["value"] if agg else None,
                    "entropy": agg["entropy"] if agg else None,
                    "kl": agg["kl"] if agg else None,
                    "mean_ret": agg["mean_ret"] if agg else None, "lr": args.lr,
                    "mb": args.mb, "epochs": args.epochs,
                    # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
                    **({"missing": report["missing"], "expectedGames": report["expectedGames"],
                        "dist": report["dist"]} if "missing" in report else {}),
                    # 纯采集（用户定义）：末局结算 − 权重分发完毕；队列模式实测透传，
                    # 纯本地路径回退为 rollout 全长（无重叠即等价纯采集）。
                    "pure_collect_sec": report.get(
                        "pure_collect_sec", round(rollout_sec, 1)),
                    # R5 遥测补牙（2026-08-25）：流式的 kl 只是末 wave 单值，对轮内
                    # 累积漂移全盲——补 kl_cum/halted/dropped 与各阶段耗时拆分。
                    # F4 熔断仍读 kl（每梯度步均值，跨模式可比）；轮内漂移由
                    # streamKlCap 治理，kl_cum 供观测与事后分析。
                    "kl_cum": kl_cum,
                    "halted": halted_flag,
                    "dropped_games": dropped_games,
                    "waves": waves_n,
                    "load_sec": load_sec,
                    "tail_drain_sec": tail_drain_sec,
                    "dist_phase_sec": report.get("dist_phase_sec"),
                    "eval_join_sec": eval_join_sec,
                }) + "\n")

            # 每轮 PPO 写回后自动生成巡检 HTML（仅默认 traj；非致命，失败不断训练）
            if auto_inspect:
                _run_inspect(bun, it)

            # F4 circuit breaker（纯逻辑在 rl/breaker.py）。agg 为 None 的轮
            # （流式 checkpoint-complete，无任何梯度步）不计连击也不告警——
            # 本来就没有发生新的策略更新。break (not raise)：下方 except 会吞掉重试。
            if agg is not None:
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak, ent_streak, kl=agg["kl"], entropy=agg["entropy"],
                    win_rate=report["winRate"])
                if tripped_now is not None:
                    tripped = tripped_now
                if tripped is not None:
                    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                        jsonl_f.write(json.dumps({
                            "event": "circuit_break", "iter": it,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "reason": tripped,
                            "kl": agg["kl"], "kl_streak": kl_streak,
                            "entropy": agg["entropy"], "ent_streak": ent_streak,
                            "winRate": report["winRate"], "weights": args.out,
                        }) + "\n")
                    log(f"[run_rl] CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                    log(f"[run_rl] training PAUSED; weights kept at {args.out}; "
                        f"inspect policy behavior before relaunching")
                    break

                if agg["kl"] > KL_WARN:
                    log(f"[run_rl] WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast; "
                        f"consider lower lr/epochs")
                if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                    log(f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f}) — possible premature convergence")
                prev_entropy = agg["entropy"]

            if args.keep_iters > 0:
                for old in traj_root.glob("it*"):
                    try:
                        n_old = int(old.name[2:])
                    except ValueError:
                        continue
                    if n_old <= it - args.keep_iters:
                        shutil.rmtree(old, ignore_errors=True)

            # 吞吐 T4：双缓冲 spawn 下一轮预采（仅 stream + 双缓冲开启 + 非 collect-only）。
            # 下一轮开头 join（上方）：采集藏进本轮 PPO 尾段 + 非 eval 轮集群空档，墙钟直降。
            if (getattr(args, "double_buffer", 0) and stream_meta is not None
                    and not getattr(args, "collect_only", 0)
                    and (args.iters <= 0 or it < args.iters)):
                _collect_child = _spawn_collect_next(args, it)
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(f"[run_rl] it{it} FAILED (SystemExit: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 原地重试同一迭代（resume 保留已完成 shard + PPO ckpt，不重跑已完局）
        except Exception as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 同上：失败迭代不前跳，杜绝静默跳轮丢语料

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
