from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rl.archive import ensure_current_branch_pushed
from rl.cli import build_argparser
from rl.collect_only import run_collect_only
from rl.log import Tee as _Tee
from rl.log import log
from rl.modes import apply_mode_flags, get_backend, merged_mode_args, resolve_mode
from rl.queue import (  # noqa: F401 — run_rollout_queue re-exported for tests
    REPO_ROOT,
    run_rollout_queue,
)
from rl.resume import (
    completed_pairs,  # noqa: F401 — re-exported for tests
    last_completed_iter,  # noqa: F401 — re-exported for tests
    last_rotate_seed,
    resumed_manifests,  # noqa: F401 — re-exported for tests
)


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
    # ===== 课程配置化（plan/rl-training-config.md §3）：唯一启动入口 =====
    # 优先级 课程 > rl-config.json > argparse 默认；无 CLI 逐参覆盖。课程自带
    # 关卡布局/奖励公式/超参 schedule，apply 后由各阶段消费。
    from rl.config import (
        apply_course,
        course_cli_conflicts,
        course_from_args,
        echo_config,
    )

    course = course_from_args(args)
    if course is not None:
        # CLI 快照（apply 前）+ 纯默认基线（parse_args([])）→ 冲突检测
        _cli_before = {k: v for k, v in vars(args).items()}
        _defaults_ns = ap.parse_args([])
        apply_course(args, course)
        conflicts = course_cli_conflicts(_cli_before, vars(_defaults_ns), course)
        if conflicts:
            raise SystemExit(
                "[run_rl] --course 与显式 CLI 参数冲突（课程是单一事实来源，无 CLI 逐参"
                "覆盖——plan §3）：\n  "
                + "\n  ".join(conflicts)
                + "\n删掉冲突 flag 或改课程配置后重试"
            )
    if getattr(args, "echo_config", False):
        echo_config(args, course)
        log("[run_rl] --echo-config done — exit")
        return
    # P1-3（2026-09-02）：启动期配置校验（互斥/范围 fail fast——此前这些错误
    # 要等训练中途才暴露）。课程覆盖后校验（课程值是单一事实来源）。
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
        run_collect_only(args, _traj_root, _rseed, bun)
        log("[run_rl] collect-only done — exit")
        return
    # ===== 双缓冲：collect-only 分支结束 =====

    # ===== 主循环（rl/loop.py::run_training）=====
    from rl.loop import run_training

    run_training(args, get_backend(args.mode), bun, update_kwargs)


if __name__ == "__main__":
    main()
