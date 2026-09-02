
"""run_rl.py — RL on-policy 主循环入口（唯一 RL 入口，P1.5 蒸馏 → RL 阶段）。

**三模式入口**（2026-09-01 RL 入口整合，plan/RL-Entry-Consolidation.md /
DECISIONS §307）：`--mode {per-tick,intent,goal}` 参数化三套后端；
`run_rl_intent.py` 已退役并入本文件。`--goal` 保留为 `--mode goal` 的别名。
  - per-tick（默认）：逐 tick move/fire PPO（ppo.py，ConvMixer rl_model）。
  - intent：意图步 semi-MDP PPO（ppo_intent.py，8 类意图，变步长 GAE γ^Δt）。
  - goal：goal 承诺步 PPO（ppo_goal.py，676/169 路目标格，心跳承诺期）。

工程结构（2026-08-25 工程化重组）：编排逻辑抽取至 rl/ 包 ——
  rl/course.py        课程（build_pairs / parse_range）
  rl/queue.py         中央队列调度 + 纯本地回退（run_rollout_queue / run_rollout）
  rl/stream.py        流式迭代（run_rollout_stream / wave_params）
  rl/eval_dispatch.py 干净评估分发（per-tick：逐局派发 + eval_log 对账）
  rl/eval_m1.py       干净评估管线（intent/goal：m1-eval 整批 + Δ 止损）
  rl/resume.py        断点对账（completed_pairs / resumed_manifests / jsonl 锚点）
  rl/reports.py       报告聚合（combine_reports / win_of）
  rl/breaker.py       F4 熔断纯逻辑（阈值 + 连击判定）
本文件只保留：CLI、迭代主循环、权重初始化/归档、巡检与熔断停车。

流程：
  ① 权重初始化（幂等）：RL 权重不存在时从 BC 检查点 warm-start——per-tick 走
     DAgger 检查点 + A4 trunk 校准归一；intent/goal 走 ppo_intent/ppo_goal CLI
     init-from（B′ 三头迁移，value 头随机）；已存在则直接续跑。
  ② 迭代 N 次：bun TS rollout（subprocess，无需 torch）→ 进程内 clipped PPO 更新
     （模型常驻内存）→ 原子写回权重文件，下一轮 rollout 即用新权重（标准 on-policy）。

经统一启动器进入（venv/torch 由它保证）：
  bash nn-training/start-training.sh --script run_rl.py --iters 15 --stream 1
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl.py --iters 15          # --xxx 参数原样透传
  # 意图 RL（原 run_rl_intent.py）：-Script run_rl.py --mode intent
  # goal 承诺步 RL（原 run_rl_intent.py --goal）：-Script run_rl.py --mode goal

单步调试仍可用 ppo*.py 的 --init-from / --resume CLI；回归测试见 test_run_rl.py。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

# 注意（B7，2026-09-02）：本文件**顶层不 import torch / ppo.* / models.**——它们是
# 训练路径的依赖，而 `--collect-only` 子进程只采样不训练。若顶层加载 torch，
# 每个双缓冲预采子进程白白支付 3~8s 的 torch import（run_rl.py:912 的注释正是
# 如此声称，但此前实现并未兑现）。torch 相关 import 一律延迟到 build_model() /
# main() 内（rl/modes.get_backend 与 rl/stream 的默认后端同样延迟）。
import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.archive import backup_weights, ensure_current_branch_pushed
from rl.breaker import (
    CIRCUIT_EXIT_CODE,
    ENT_COLLAPSE_DROP,
    KL_BREAK,
    KL_BREAK_CONSEC,
    KL_WARN,
    breaker_update,
)
from rl.cli import build_argparser
from rl.course import build_pairs
from rl.eval_dispatch import (
    dispatch_eval_bg,
)
from rl.eval_m1 import (
    dispatch_eval_bg_m1,
    read_eval_summary,
)

# rl/ 包：编排逻辑单点实现（本文件以下仅存 CLI + 主循环 + 权重归档/巡检）
from rl.log import Tee as _Tee  # P2-6d：run_rl._Tee 迁入 rl/log.py 通用实现
from rl.log import log
from rl.modes import (
    _MODE_BACKUP_PREFIX,
    apply_mode_flags,
    get_backend,
    merged_mode_args,
    resolve_mode,
)
from rl.queue import (
    REPO_ROOT,
    RUN_ID,
    run_rollout,
    run_rollout_queue,
)
from rl.resume import (
    completed_pairs,
    last_completed_iter,
    last_rotate_seed,
    resumed_manifests,  # noqa: F401 — re-exported for tests
)


# Per-iteration weights archive (user request 2026-08-24): every completed PPO
# write-back is copied into nn-training/weights/ with an identifiable name.
def update_kwargs(args, it: int, start_it: int, ref_model) -> dict:
    """intent/goal 模式的 PPO 更新参数：value 预热（前 warmup-iters 冻结主干只训
    value 头）+ kickstarting KL 惩罚（系数按策略迭代衰减）。自 run_rl_intent 原样
    迁入（数学不变）。纯函数，可单测。"""
    warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
    policy_iter = (it - start_it) - args.warmup_iters + 1
    kl_coef = (
        args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1))
        if args.kickstart_kl > 0 and policy_iter >= 1
        else 0.0
    )
    return {
        "value_warmup_epochs": warmup_epochs,
        "ref_model": ref_model,
        "kl_coef": kl_coef,
        "seed": args.seed,
    }


def _eval_sigma(eval_rec: dict | None) -> float | None:
    """单次干净评估的 winRate 标准误 σ = √(p(1−p)/n)（二项分布）。

    返回 None = eval_rec 无 games/winRate 字段（旧调用方），调用侧回退旧语义。
    注意这是**乐观下界**：同 stage 的 n 局共享关卡、存在 stage 级相关性，真实 σ
    更大——因此"显著"判定是保守的（宁可少停，不可乱停）。
    """
    if not eval_rec:
        return None
    games = eval_rec.get("games")
    wr = eval_rec.get("winRate")
    if not isinstance(games, (int, float)) or games <= 0 or not isinstance(wr, (int, float)):
        return None
    p = max(0.0, min(1.0, float(wr)))
    return float((p * (1.0 - p) / games) ** 0.5)


def stop_loss_hit(
    mode: str,
    stop_loss_at: int,
    stop_loss_delta: float,
    it: int,
    eval_rec: dict | None,
    *,
    z_score: float = 2.0,
) -> bool:
    """预注册止损判门（D4 泛化：原 run_rl_intent 的 iter15 Δ≤0 硬编码）。纯函数。

    仅 intent/goal 模式生效；stop_loss_at=0 = 关闭（per-tick 恒 False）。

    **统计化修正（plan/python-refactor.md P1-9，2026-09-02）**：旧逻辑 `Δ ≤ delta`
    （默认 0.0）在评估 σ≈0.024（350 局 p≈0.72；stage 级相关下实为 0.03~0.04）时，
    真实中立策略单次评估约 50% 概率误停——抛硬币。新逻辑要求 Δ **显著**为负：
      trip = (Δ ≤ stop_loss_delta) ∧ (σ 未知 ∨ Δ ≤ −z·σ)
    eval_rec 带 games/winRate 时按二项分布推 σ（z=2 → 约 97.5% 置信单侧）；
    不带时（旧调用方）回退原语义。连击护栏（连续 ≥2 轮显著）在调用侧实现。
    """
    if mode == "per-tick" or stop_loss_at <= 0:
        return False
    if not (it >= stop_loss_at and eval_rec and eval_rec.get("delta") is not None):
        return False
    delta = float(eval_rec["delta"])
    if delta > stop_loss_delta:
        return False
    sigma = _eval_sigma(eval_rec)
    if sigma is None:
        return True  # 无 games 数据：保持原语义（Δ≤bar 即停）
    return delta <= -z_score * sigma


def _setup_log_redirect(args) -> None:
    """stdout/stderr 重定向到 out_log/err_log（Tee 控制台+文件）。空字符串=仅控制台
    （per-tick 默认 → 行为不变）。落盘追加模式 + 启动横幅，多次启动日志累积。"""
    if getattr(args, "out_log", ""):
        try:
            p = Path(args.out_log)
            p.parent.mkdir(parents=True, exist_ok=True)
            sys.stdout = _Tee(sys.stdout, open(p, "a", encoding="utf-8"))
            log(f"[launch] stdout -> {p} (tee console+file, append)")
        except Exception as e:
            log(f"WARN cannot redirect stdout to {args.out_log}: {e}")
    if getattr(args, "err_log", ""):
        try:
            pe = Path(args.err_log)
            pe.parent.mkdir(parents=True, exist_ok=True)
            sys.stderr = _Tee(sys.stderr, open(pe, "a", encoding="utf-8"))
            log(f"[launch] stderr -> {pe} (tee console+file, append)")
        except Exception as e:
            log(f"WARN cannot redirect stderr to {args.err_log}: {e}")


def _log_rl_args(src: dict, merged: dict) -> None:
    """生效启动配置落地日志：标注每个键来源（rl.<mode> / intent_rl(legacy) / rl /
    fallback），便于核对单一事实来源（trust-but-verify）。"""
    log(
        "[launch] rl args source: "
        + " ".join(f"{k}={src.get(k, 'fallback')}" for k in sorted(merged))
    )


def _run_collect_only(args, traj_root, rotate_seed, bun) -> None:
    """吞吐 T4：仅采集一轮落盘后退出（双缓冲子进程模式）。不 PPO/不 eval/不写权重。
    落盘 shard 的 manifest.wver = 快照权重指纹 → 主进程下一轮 completed_pairs 命中走聚合。
    --precollect-games>0 时只采前 N 局（下一轮首波 wave 语料，其余由下轮以 θ_N 现场采）。
    --precollect-samples>0 时按样本量提前 halt（样本数达标即停，无需等满 N 局）。
    """
    import threading as _threading

    it = args.start_it or 1
    traj_dir = traj_root / f"it{it}"
    traj_dir.mkdir(parents=True, exist_ok=True)
    pairs = build_pairs(args, it, rotate_seed)
    pre_games = int(getattr(args, "precollect_games", 0) or 0)
    pre_samples = int(getattr(args, "precollect_samples", 0) or 0)
    halt_event: _threading.Event | None = None
    if 0 < pre_games < len(pairs):
        pairs = pairs[:pre_games]
        log(
            f"[collect-only] it{it}: limited to first {pre_games} games "
            f"(rest collected by next round with θ_N)"
        )
    elif pre_samples > 0 and len(pairs) > 0:
        # 按样本量 halt：不截断 pairs，用 halt_event 在累计样本达标时提前停采。
        # 主进程的轮询逻辑在检测到足够 shard 后也会放行，两者互补。
        halt_event = _threading.Event()
        log(
            f"[collect-only] it{it}: {len(pairs)} pairs, target_samples={pre_samples}, "
            f"halt when reached (subprocess exits early, excess collected by next round)"
        )
    dist_cfg = dist_common.load_dist_config()
    # iter_id 必须遵循 "{RUN_ID}.{it}" —— run_rollout_queue 用 rsplit('.',1)[-1]
    # 做 int() 解析迭代号（2026-08-31 实测：'collect-1-<pid>' 格式直接 ValueError
    # 崩崩，分布式路径的子进程从未成功采集过一轮，等于双缓冲静默失效）。
    iter_id = f"{RUN_ID}.{it}"
    log(f"[collect-only] it{it}: {len(pairs)} pairs -> {traj_dir} (weights={args.bc})")
    if halt_event:
        # 样本量提前 halt 模式：on_result 累计样本，达标即 set halt_event。
        # 在途局自然收尾，子进程退出，主进程 wait() 返回。
        _pre_samples_acc = [0]

        def _on_result(summary):
            s = summary.get("totalSamples", 0) or summary.get("samples", 0) or 0
            _pre_samples_acc[0] += s
            if _pre_samples_acc[0] >= pre_samples:
                halt_event.set()

        if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
            report = run_rollout_queue(
                bun,
                args.bc,
                traj_dir,
                pairs,
                args,
                dist_cfg,
                iter_id,
                on_result=_on_result,
                halt_event=halt_event,
            )
        else:
            report = run_rollout(bun, args.bc, traj_dir, pairs, args)
    else:
        if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
            report = run_rollout_queue(bun, args.bc, traj_dir, pairs, args, dist_cfg, iter_id)
        else:
            report = run_rollout(bun, args.bc, traj_dir, pairs, args)
    log(
        f"[collect-only] it{it}: games={report['games']} samples={report['totalSamples']} "
        f"outcomes={json.dumps(report['outcomes'])}"
    )


def _spawn_collect_next(args, it, snap_src: str | None = None) -> subprocess.Popen | None:
    """吞吐 T4：后台子进程预采 it+1（行为快照）。返回 Popen 或 None。

    正确性：子进程用 args.out（或 snap_src，提前预采时=epoch3 快照）的**快照**——下轮
    PPO 若写回 args.out 不回影响子进程（它读 snap 文件）；shards 自带快照权重的 lp →
    IS 分母天然正确（快照≈θ_N 时仍 on-policy 带内，差最后一段梯度）。子进程同样继承
    --precollect-games：>0 则只采下一轮首波，其余局由下轮以 θ_N 现场采（严格 on-policy）。
    --precollect-samples 自动从 training_log 上一轮实测数据计算：wave_games × avg_sppg × 1.5。
    """
    import shutil

    next_it = it + 1
    if args.iters > 0 and next_it > args.iters:
        return None
    src = snap_src or args.out
    snap = str(Path(args.out).with_name(f"weights-collect-{next_it}.json"))
    if os.path.abspath(src) != os.path.abspath(snap):
        try:
            shutil.copyfile(src, snap)
        except OSError as e:
            log(f"[double-buffer] snapshot fail: {e} — skip precollect")
            return None
    # 提前预采（snap_src 已由调用方 save_weights_json 写好目标文件）：src==snap，
    # 跳过 copy 直接复用——否则 copyfile(自己→自己) 抛 same-file 错误。
    elif snap_src is not None:
        log(f"[double-buffer] early snapshot already at {snap} — reuse")

    # 动态计算 precollect_samples：从 training_log 上一轮实测 avg_sppg × wave_games × 1.5
    pre_samples = 0
    jsonl_path = Path(args.traj) / "training_log.jsonl"
    if jsonl_path.exists():
        try:
            with open(jsonl_path) as _f:
                _lines = [line for line in _f if line.strip()]
            if _lines:
                _last = json.loads(_lines[-1])
                _last_samples = _last.get("samples", 0)
                _last_games = _last.get("expectedGames", 1)
                if _last_samples > 0 and _last_games > 0:
                    _avg_sppg = _last_samples / _last_games
                    _cfg = dist_common.load_dist_config()
                    if _cfg is None:
                        _cfg = {}
                    _wave = max(4, int(_cfg.get("policy", {}).get("streamWaveGames", 12)))
                    pre_samples = int(_wave * _avg_sppg * 1.5)
                    log(
                        f"[double-buffer] precollect it{next_it}: auto-calc precollect_samples="
                        f"{pre_samples} (wave={_wave} × avg_sppg={_avg_sppg:.1f} × 1.5)"
                    )
        except Exception as _e:
            log(
                f"[double-buffer] precollect it{next_it}: calc failed ({_e}), "
                f"fallback to --precollect-games"
            )

    argv = [
        sys.executable,
        "-u",
        os.path.abspath(__file__),
        *sys.argv[1:],
        "--collect-only",
        "1",
        "--bc",
        snap,
        "--start-it",
        str(next_it),
        "--iters",
        "1",
    ]
    if pre_samples > 0:
        argv += ["--precollect-samples", str(pre_samples)]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception as e:
        log(f"[double-buffer] spawn fail: {e} — skip precollect")
        return None
    log(
        f"[double-buffer] precollect it{next_it} spawned pid={p.pid} snapshot={snap}"
        + (f" (early, src={snap_src})" if snap_src else "")
    )
    return p


def _precollect_snapshot_wver(out_path: str, it: int) -> str | None:
    """回读上一轮提前预采（epoch3 快照）写入的 weights-collect-{it}.json 的 wver。

    提前预采时快照=θ_{N,e3}（尚未最终写回 args.out），其 shard 的 wver=快照指纹；
    本轮对账需把该 wver 也纳入 done 判定（双白名单），否则预采首波被当作未完成清场。
    文件存在才返回（无提前预采/常规尾部预采都返回 None——尾部预采时 args.out 已是 θ_N，
    wver 与当前一致，无需双白名单）。"""
    try:
        snap = Path(out_path).with_name(f"weights-collect-{it}.json")
        if not snap.exists():
            return None
        return dist_common.weights_fingerprint(str(snap))
    except OSError:
        return None
def _log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）。

    此前失败详情只进易失 stdout——detach 启动下不可见，it2/it3 连续跳轮时
    无任何可复盘痕迹。观测必须自带牙齿：last_completed_iter 只认 iteration
    事件，iter_error 不影响断点续跑定位。
    """
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "iter_error",
                        "iter": it,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "error": str(err)[:500],
                    }
                )
                + "\n"
            )
    except OSError:
        pass


def _run_inspect(bun: str, it: int, traj_dir: Path) -> None:
    """每轮 PPO 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    显式传 --traj-dir（intent/goal 的非默认 traj 也要能出巡检 HTML）。"""
    try:
        subprocess.run(
            [
                bun,
                "tools/diag/rl-hourly-inspect.ts",
                "--up-to",
                str(it),
                "--traj-dir",
                str(traj_dir),
            ],
            cwd=str(REPO_ROOT),
            timeout=180,
            capture_output=True,
            text=True,
            **_POPEN_NO_WINDOW,
        )
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")


def main() -> None:
    # Anchor cwd to the repo root (parent of nn-training/): all default paths
    # (tmp/student-weights-dagger, tmp/rl-weights, tmp/rl-traj) are repo-root
    # relative. Required for start-training.ps1 --detach, whose WorkingDirectory
    # is nn-training/ — same pattern as train_loop.py's REPO_ROOT.
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # ===== RL 入口整合（DECISIONS §307）：两阶段 argparse，先预解析 --mode/--goal =====
    mode = resolve_mode(sys.argv[1:])
    # 启动参数默认取自 rl-config.json（单一事实来源；CLI 显式传参覆盖 json 默认）。
    # 查找优先级 rl.<mode> → intent_rl 遗留块（intent/goal 迁移期）→ rl（D2）。
    try:
        _cfg = json.loads(
            (REPO_ROOT / "nn-training" / "rl-config.json").read_text(encoding="utf-8")
        )
    except Exception:
        _cfg = {}
    _rl_args, _rl_src = merged_mode_args(_cfg, mode)


    ap = build_argparser(mode, _rl_args)
    args = ap.parse_args()
    apply_mode_flags(args)
    # P1-3（2026-09-02）：启动期配置校验（互斥/范围 fail fast——此前这些错误
    # 要等训练中途才暴露）。
    from rl.config import validate_args

    validate_args(args)

    # stdout/stderr 落盘（out_log/err_log；CLI 可覆盖调试；per-tick 默认空=仅控制台）。
    _setup_log_redirect(args)
    # 生效启动配置落地日志（trust-but-verify：核对 json 默认是否被正确读取）。
    _log_rl_args(_rl_src, _rl_args)

    # 启动前推送当前分支到 origin（远端 agent 靠 git pull 同步——§30 教训）。
    # 2026-08-30 事故修复（用户指令）：节点的远控升级分支**永远用训练机当前分支**，
    # 不再读 rl-config 的 upgradeBranch（残留旧战役分支名曾把全部节点 reset 回
    # 31 个提交前的 intent-ai）。config 键仅作 push 失败时的最后回退。
    ensure_current_branch_pushed(REPO_ROOT)  # side-effect: push current branch
    _current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
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
    _rseed = _prs if _prs is not None else (args.seed * 1009 + 1 + int(time.time())) % (2**32)
    if getattr(args, "collect_only", 0):
        _run_collect_only(args, _traj_root, _rseed, bun)
        log("[run_rl] collect-only done — exit")
        return
    # ===== 双缓冲：collect-only 分支结束 =====

    # ===== 训练路径延迟导入（B7，2026-09-02）：collect-only 子进程已提前 return，
    # 此刻起才允许拉起 torch / ppo.* / models.*（CPU 上 ~3-8s 的 torch 加载不再
    # 出现在每轮的双缓冲预采子进程里）。=====
    import numpy as np
    import torch

    import ppo.engine as ppo_mod
    import ppo.goal as ppo_goal
    import ppo.intent as ppo_intent
    from data.weights_io import save_weights_json
    from models.goal_net import GoalNet
    from models.intent_net import IntentNet
    from rl.model_build import build_model
    from rl.stream import run_rollout_stream

    np.random.seed(args.seed)
    PPO = get_backend(args.mode)

    device = torch.device("cpu")
    model = build_model(args.bc, args.out, mode=args.mode, workers=args.workers)
    model.to(device)
    ref_model = None
    if args.mode in ("intent", "goal") and args.kickstart_kl > 0:
        # kickstarting 参考策略：B′ 冻结快照（须在 build_model 完成 init-from 落盘
        # args.out 之后构建）。warmup 冻结主干+三头 → 策略与 B′ 一致。
        ref_model = PPO.build_rl_net(args.out)
        if args.mode == "goal":
            ppo_goal.load_goal_weights(ref_model, args.out)
        else:
            ppo_intent.load_intent_weights(ref_model, args.out)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
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
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2**32)
    # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
    # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(
            json.dumps(
                {
                    "event": "run_start",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "args": {k: v for k, v in vars(args).items()},
                    "rotateSeed": rotate_seed,
                }
            )
            + "\n"
        )

    log(
        f"[run_rl] mode={args.mode} "
        f"iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (
            f"curriculum={args.curriculum_stages} start={args.curriculum_start} "
            f"every={args.curriculum_every} grow={args.curriculum_grow}"
            if args.curriculum_stages
            else f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
            f"of {args.total_stages} (full coverage every "
            f"{-(-args.total_stages // args.rotate_stages)} iters)"
            if args.rotate_stages > 0
            else f"stages={args.stages} seeds={args.seeds}"
        )
        + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
        f"workers={args.workers} keepIters={args.keep_iters}"
    )
    log(f"training_log: {jsonl_path}")
    log(f"[run_rl] runId={RUN_ID}")

    # 自动巡检：per-tick 仅对默认 traj 生效（巡检脚本默认读 tmp/rl-traj）；
    # intent/goal 总是生成巡检 HTML（_run_inspect 显式传 --traj-dir）。
    auto_inspect = (
        True
        if args.mode in ("intent", "goal")
        else traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
    )
    if auto_inspect:
        log("[run_rl] per-iteration auto-inspection ENABLED (HTML report after each PPO)")

    deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
    total = "∞" if args.iters <= 0 else str(args.iters)
    prev_entropy = None
    consec_fail = 0
    kl_streak = 0  # F4: consecutive iters with kl >= KL_BREAK
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < MAX_WINRATE
    stop_loss_streak = 0  # P1-9: 统计显著止损（Δ≤−2σ）的连续轮数，≥2 才停车
    tripped = None
    # it 断点续跑：--start-it 显式，否则自动 = 日志最后一个完成迭代 + 1
    start_it = args.start_it if args.start_it is not None else (last_completed_iter(jsonl_path) + 1)
    if start_it > 1:
        log(
            f"[run_rl] resume: continuing from iteration {start_it} "
            f"(weights resume from {args.out})"
        )
    it = start_it - 1
    # 吞吐 T3：eval 稀疏化周期（默认 1 = 每轮，字节一致；>1 = 每 N 轮一次）。
    eval_every = int(getattr(args, "eval_every", 1) or 1)
    # 吞吐 T3：eval 绝对迭代点集（复用 run_rl_intent 的 eval_at 语义；空 = 不启用该维）。
    eval_at_set = {int(x) for x in str(getattr(args, "eval_at", "") or "").split(",") if x.strip()}
    _collect_child: subprocess.Popen | None = None  # 吞吐 T4：预采子进程句柄（下一轮开头 join）
    _spawned_early = False  # 吞吐 T4：本轮是否已在 PPO 中途（epoch 提前量处）spawn 过预采
    while args.iters <= 0 or it < args.iters:
        it += 1
        # 吞吐 T4：本轮开头检查预采子进程产出——轮询 completed_pairs 直到足够开首波
        # （而非 wait 子进程退出），避免预采尾段拖慢、主进程空等。
        # 子进程在后台继续产出剩余 shard，run_rollout_queue 的 completed_pairs 会跳过已落盘局。
        if _collect_child is not None:
            _pre_traj_dir = traj_root / f"it{it}"
            # wver/extra_wver 需在子进程退出前算出才能匹配预采 shard
            _pre_wver = dist_common.weights_fingerprint(args.out)
            _pre_extra_wver = _precollect_snapshot_wver(args.out, it)
            _pre_cfg = dist_common.load_dist_config() or {}
            _pre_policy = _pre_cfg.get("policy", {})
            _pre_wave = max(4, int(_pre_policy.get("streamWaveGames", 12)))
            _pre_min_wave = max(4, _pre_wave // 2)  # 半波即可开训
            _pre_deadline = time.time() + 3600
            _pre_ready = False
            while time.time() < _pre_deadline:
                _pre_done = completed_pairs(_pre_traj_dir, _pre_wver, extra_wver=_pre_extra_wver)
                if len(_pre_done) >= _pre_min_wave:
                    log(
                        f"[double-buffer] precollect it{it}: {len(_pre_done)} shards ready "
                        f"(≥{_pre_min_wave}), proceeding before subprocess exit"
                    )
                    _pre_ready = True
                    break
                if _collect_child.poll() is not None:
                    log(f"[double-buffer] precollect it{it} done rc={_collect_child.returncode}")
                    _pre_ready = True
                    break
                time.sleep(2)
            if not _pre_ready:
                log(f"[double-buffer] precollect it{it} timeout — fall back to own collect")
                _collect_child.terminate()
            _collect_child = None
        if deadline is not None and time.time() >= deadline:
            log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
            break
        traj_dir = traj_root / f"it{it}"
        try:
            # rollout/PPO 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
            # 保留续跑（跳过已完成局 + 续 PPO checkpoint）；否则清空重建。
            wver = dist_common.weights_fingerprint(args.out)
            # 吞吐 T4 提前预采：上一轮若在 epoch3 已 spawn，本轮对账还需接受快照 wver
            # （θ_{N,e3} ≈ θ_N 于最后 1 个 epoch 前）——否则预采首波被当"未完成"清场。
            extra_wver = _precollect_snapshot_wver(args.out, it)
            have_resume = bool(completed_pairs(traj_dir, wver, extra_wver=extra_wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(
                    f"[run_rl] resume iteration {it}: keeping existing shards + PPO checkpoint"
                    + (f" (precollect snapshot wver {extra_wver[:12]}…)" if extra_wver else "")
                )
            else:
                if traj_dir.exists():
                    try:
                        shutil.rmtree(traj_dir)  # 沙箱删除保护拦截时跳过（保留旧目录，训练照常）
                    except BaseException:
                        pass
                traj_dir.mkdir(parents=True)

            log(f"[run_rl] === iteration {it}/{total} ===")
            pairs = build_pairs(args, it, rotate_seed)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            # 吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
            # 三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。
            if args.mode == "per-tick":
                eval_on_round = (
                    int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                    and (eval_every <= 1 or it % eval_every == 0)
                    and (not eval_at_set or it in eval_at_set)
                )
            else:
                eval_on_round = it in eval_at_set
            t_rollout = time.time()
            stream_meta = None
            eval_thread: threading.Thread | None = None
            eval_gate: threading.Event | None = None  # R6：PPO 收尾后放行本地 eval 参与
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
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
                        if args.mode == "per-tick":
                            return dispatch_eval_bg(
                                bun,
                                args.out,
                                traj_dir,
                                args,
                                dist_cfg,
                                iter_id,
                                it,
                                local_gate=eval_gate,
                            )
                        # intent/goal：m1-eval 固定语料整批路线（rl/eval_m1.py）。
                        return dispatch_eval_bg_m1(
                            bun, args.out, args, it, jsonl_path, args.baseline
                        )

                    # 吞吐 T4 提前预采（--precollect-early>0）：PPO 第 (epochs-early) 个
                    # epoch 完成后，把当前 model（θ_{N,e3}）冻结为快照并 spawn 预采下一轮
                    # 首波——预采墙钟藏进剩下的 epochs 里，而非 PPO 全部结束后的串行等待。
                    # 快照≈最终权重（差最后一段梯度），on-policy 带内；配合
                    # --precollect-games 限制只采首波，剩余局由下轮以 θ_N 现场采。
                    _early = int(getattr(args, "precollect_early", 0) or 0)
                    _spawned_early = False

                    def _on_epoch_done(ep_done: int, _mdl):
                        nonlocal _collect_child  # 提前 spawn 的句柄存入主循环变量（下轮开头 join）
                        nonlocal _spawned_early
                        if _early <= 0 or _spawned_early:
                            return
                        if ep_done < args.epochs - _early:
                            return
                        if not (
                            getattr(args, "double_buffer", 0)
                            and not getattr(args, "collect_only", 0)
                            and (args.iters <= 0 or it < args.iters)
                        ):
                            return
                        # 冻结当前权重快照（θ_{N,e3}）→ spawn；PPO 尾段自然完成临时文件。
                        _snap = str(Path(args.out).with_name(f"weights-collect-{it + 1}.json"))
                        try:
                            save_weights_json(_mdl, _snap)
                            log(f"[double-buffer] early snapshot θ_{{N,e{ep_done}}} -> {_snap}")
                        except Exception as _e:
                            log(f"[double-buffer] early snapshot fail: {_e} — skip")
                            return
                        _spawned_early = True
                        _collect_child = _spawn_collect_next(args, it, snap_src=_snap)
                        if _collect_child is not None:
                            log(
                                f"[double-buffer] precollect EARLY at epoch {ep_done}/{args.epochs} "
                                f"(rest of PPO hides collection)"
                            )

                    _stream_kwargs = {}
                    if args.mode in ("intent", "goal"):
                        # backend/update_kwargs 注入（intent 的 value warmup + kickstarting）。
                        _stream_kwargs = {
                            "backend": PPO,
                            "update_kwargs": update_kwargs(args, it, start_it, ref_model),
                        }
                    report = run_rollout_stream(
                        bun,
                        args.out,
                        traj_dir,
                        pairs,
                        args,
                        dist_cfg,
                        iter_id,
                        model,
                        opt,
                        device,
                        on_collect_done=_fire_eval,
                        on_epoch_done=_on_epoch_done,
                        extra_wver=extra_wver,
                        **_stream_kwargs,
                    )
                    stream_meta = report
                else:
                    report = run_rollout_queue(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id
                    )
                    # 串行：rollout 返回即 collector 收官；后台评估藏进随后的长 PPO 空窗
                    # 吞吐 T3：非 eval 轮不派发（eval_on_round 循环级统一门控）。
                    if eval_on_round:
                        if args.mode == "per-tick":
                            eval_thread = dispatch_eval_bg(
                                bun,
                                args.out,
                                traj_dir,
                                args,
                                dist_cfg,
                                iter_id,
                                it,
                                report["winRate"],
                                local_gate=eval_gate,
                            )
                        else:
                            eval_thread = dispatch_eval_bg_m1(
                                bun, args.out, args, it, jsonl_path, args.baseline
                            )
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
            log(
                f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}"
            )
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(
                    f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}"
                )
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            if stream_meta is None:
                t_ppo = time.time()
                # P1-7：--adv-norm none 时串行路径跳过 global 归一（对照实验）
                episodes = PPO.load_episodes(
                    str(traj_dir), normalize_adv=getattr(args, "adv_norm", "auto") != "none"
                )
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = PPO.chunk_episodes(episodes, args.mb)
                # PPO epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
                if args.mode in ("intent", "goal"):
                    agg = PPO.update(
                        model,
                        opt,
                        chunks,
                        args.epochs,
                        device,
                        ckpt_path=str(traj_dir / "ppo_ckpt"),
                        **update_kwargs(args, it, start_it, ref_model),
                    )
                else:
                    agg = ppo_mod.ppo_update(
                        model,
                        opt,
                        chunks,
                        args.epochs,
                        device,
                        ckpt_path=str(traj_dir / "ppo_ckpt"),
                    )
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径
            if args.mode == "goal":
                ppo_goal.export_goal_weights(cast(GoalNet, model), args.out)
            elif args.mode == "intent":
                ppo_intent.export_intent_weights(cast(IntentNet, model), args.out)
            else:
                save_weights_json(model, args.out)
            bak = backup_weights(args.out, it, prefix=_MODE_BACKUP_PREFIX[args.mode])
            log(
                f"[run_rl] ppo it{it}: steps={total_steps} chunks={chunks_n} "
                + (
                    f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                    f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}"
                    if agg is not None
                    else "metrics n/a — PPO checkpoint completed by previous process"
                )
            )
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
                budget = float(args.eval_window_sec) + 60.0
                if args.mode in ("intent", "goal"):
                    # intent/goal：eval_summary 须在 jsonl 写回前结算（止损判门依赖）。
                    log(
                        f"waiting up to {budget:.0f}s for clean-eval round before next "
                        f"weight distribution"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=budget)
                    eval_join_sec = round(time.time() - _t_join, 1)
                else:
                    soft = min(budget, 180.0)  # per-tick 软等待上限：吃尾巴 + 缓存缓冲
                    log(
                        f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                        f"(remaining eval finishes in background, wver-keyed)"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=soft)
                    eval_join_sec = round(time.time() - _t_join, 1)
            # intent/goal：回读该迭代 eval_summary（评估线程写入；止损判门的数据源）。
            eval_rec = (
                read_eval_summary(jsonl_path, it) if args.mode in ("intent", "goal") else None
            )
            # pace checkpoint（intent/goal 护栏）：iter5 首现通关。
            if args.mode in ("intent", "goal") and it == 5 and report["winRate"] <= 0:
                log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(
                    json.dumps(
                        {
                            "event": "iteration",
                            "iter": it,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "winRate": report["winRate"],
                            "outcomes": report["outcomes"],
                            "score_mean": report.get("scoreStats", {}).get("mean"),
                            "score_std": report.get("scoreStats", {}).get("std"),
                            "dim_means": report.get("dimMeans", {}),
                            # 意图 RL 字段（per-tick 报告无此行 → None，不破兼容）。
                            "intentCounts": report.get(
                                "intentCounts" if args.mode != "goal" else "actionCounts"
                            ),
                            "baseIntegrity": report.get("dimMeans", {}).get("baseIntegrity"),
                            "samples": report["totalSamples"],
                            "ticks": report["totalTicks"],
                            "rollout_sec": rollout_sec,
                            "ppo_sec": ppo_sec,
                            "steps": total_steps,
                            "chunks": chunks_n,
                            "policy": agg["policy"] if agg else None,
                            "value": agg["value"] if agg else None,
                            "entropy": agg["entropy"] if agg else None,
                            "kl": agg["kl"] if agg else None,
                            "mean_ret": agg["mean_ret"] if agg else None,
                            "lr": args.lr,
                            # P1-12：reward/dodge 臂版本落盘（历史实验可归因——
                            # 此前奖励规格无记录，复盘无法区分 v7/toy 臂）
                            "reward": getattr(args, "reward", ""),
                            "dodge": getattr(args, "dodge", ""),
                            "mb": args.mb,
                            "epochs": args.epochs,
                            # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
                            **(
                                {
                                    "missing": report["missing"],
                                    "expectedGames": report["expectedGames"],
                                    "dist": report["dist"],
                                }
                                if "missing" in report
                                else {}
                            ),
                            # 纯采集（用户定义）：末局结算 − 权重分发完毕；队列模式实测透传，
                            # 纯本地路径回退为 rollout 全长（无重叠即等价纯采集）。
                            "pure_collect_sec": report.get(
                                "pure_collect_sec", round(rollout_sec, 1)
                            ),
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
                        }
                    )
                    + "\n"
                )

            # 每轮 PPO 写回后自动生成巡检 HTML（intent/goal 总是生成；per-tick 仅默认 traj）
            if auto_inspect:
                _run_inspect(bun, it, traj_dir=traj_root)

            # F4 circuit breaker（纯逻辑在 rl/breaker.py）。agg 为 None 的轮
            # （流式 checkpoint-complete，无任何梯度步）不计连击也不告警——
            # 本来就没有发生新的策略更新。break (not raise)：下方 except 会吞掉重试。
            if agg is not None:
                # intent/goal 用放宽的 KL 熔断阈值（原 intent_rl 专属 --kl-break
                # 0.6 / --kl-break-consec 3，避免误熔断 Bug D；per-tick 用默认 0.15/3）。
                _kl_break = args.kl_break if args.mode in ("intent", "goal") else KL_BREAK
                _kl_consec = (
                    args.kl_break_consec if args.mode in ("intent", "goal") else KL_BREAK_CONSEC
                )
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak,
                    ent_streak,
                    kl=agg["kl"],
                    entropy=agg["entropy"],
                    win_rate=report["winRate"],
                    kl_break=_kl_break,
                    kl_consec=_kl_consec,
                )
                if tripped_now is not None:
                    tripped = tripped_now
                if tripped is not None:
                    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                        jsonl_f.write(
                            json.dumps(
                                {
                                    "event": "circuit_break",
                                    "iter": it,
                                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "reason": tripped,
                                    "kl": agg["kl"],
                                    "kl_streak": kl_streak,
                                    "entropy": agg["entropy"],
                                    "ent_streak": ent_streak,
                                    "winRate": report["winRate"],
                                    "weights": args.out,
                                }
                            )
                            + "\n"
                        )
                    log(f"[run_rl] CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                    log(
                        f"[run_rl] training PAUSED; weights kept at {args.out}; "
                        f"inspect policy behavior before relaunching"
                    )
                    break

                if agg["kl"] > KL_WARN:
                    log(
                        f"[run_rl] WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast; "
                        f"consider lower lr/epochs"
                    )
                if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                    log(
                        f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f}) — possible premature convergence"
                    )
                prev_entropy = agg["entropy"]

            # 止损判定（D4 泛化，仅 intent/goal 生效）：eval_summary 的 Δ（相对
            # baseline）在 stop-loss-at 迭代 ≤ stop-loss-delta → 停车（原
            # run_rl_intent iter15 Δ≤0 转 M9 语义由 --stop-loss-at 15 --stop-loss-delta 0 复现）。
            # P1-9（2026-09-02）：单次评估噪声 σ≈0.024~0.04，旧"Δ≤0 即停"是抛硬币——
            # 现在要求 Δ 统计显著（≤ −2σ，_eval_sigma 推 σ）且**连续 2 轮**才停车。
            if stop_loss_hit(args.mode, args.stop_loss_at, args.stop_loss_delta, it, eval_rec):
                assert eval_rec is not None  # stop_loss_hit 已保证非 None（delta 可读）
                stop_loss_streak += 1
                sigma = _eval_sigma(eval_rec)
                log(
                    f"STOP-LOSS: iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                    f"(σ={'--' if sigma is None else f'{sigma:.4f}'}, "
                    f"z·σ={'--' if sigma is None else f'{2.0 * sigma:.4f}'}) "
                    f"streak={stop_loss_streak}/2 — waiting for confirmation"
                )
            else:
                stop_loss_streak = 0
            if stop_loss_streak >= 2:
                assert eval_rec is not None
                stop_reason = (
                    f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                    f"<= {args.stop_loss_delta:+.4f} (显著: Δ≤−2σ) × 2 轮 — stop-loss"
                )
                log(f"STOP-LOSS CONFIRMED: {stop_reason}")
                break

            if args.keep_iters > 0:
                for old in traj_root.glob("it*"):
                    try:
                        n_old = int(old.name[2:])
                    except ValueError:
                        continue
                    if n_old <= it - args.keep_iters:
                        try:
                            shutil.rmtree(old, ignore_errors=True)  # 沙箱删除保护拦截时跳过（磁盘轮转降级）
                        except BaseException:
                            pass

            # 吞吐 T4：双缓冲 spawn 下一轮预采（仅 stream + 双缓冲开启 + 非 collect-only）。
            # 下一轮开头 join（上方）：采集藏进本轮 PPO 尾段 + 非 eval 轮集群空档，墙钟直降。
            # 提前预采（--precollect-early）已在 epoch3 spawn 过 → 尾部跳过，避免双 spawn。
            if (
                getattr(args, "double_buffer", 0)
                and stream_meta is not None
                and not getattr(args, "collect_only", 0)
                and (args.iters <= 0 or it < args.iters)
                and not _spawned_early
            ):
                _collect_child = _spawn_collect_next(args, it)
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(
                f"[run_rl] it{it} FAILED (SystemExit: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration"
            )
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 原地重试同一迭代（resume 保留已完成 shard + PPO ckpt，不重跑已完局）
        except Exception as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(
                f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration"
            )
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 同上：失败迭代不前跳，杜绝静默跳轮丢语料

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
