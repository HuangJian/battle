"""build_rollout_cmd —— rollout 子进程命令模板（2026-09-02）。

queue_local 的 run_rollout / run_local_rollout 曾各自手工拼装
`bun tools/sim/export-{goal,intent,rl}-rollout.ts` 命令（三分支 × 两处 ≈ 200 行
重复）。本模块按 mode 统一拼装——新增 exporter 或参数时只改这一处。
"""

from __future__ import annotations


def build_rollout_cmd(
    bun: str,
    args,
    *,
    weights: str,
    out_dir: str,
    stage: int,
    seed: int,
    wver: str = "",
    node_label: str = "",
) -> list[str]:
    """按 args.goal_rollout / intent_rollout 选 exporter 并拼装 bun 命令。

    三模式差异（plan/distributed-rollout.md）：
      goal   → export-goal-rollout.ts（心跳承诺期，--heartbeat [--coarse]）
      intent → export-intent-rollout.ts（意图步半 MDP，--replan）
      per-tick → export-rl-rollout.ts（--wver/--node-label [--reward/--dodge]）
    """
    if getattr(args, "goal_rollout", False):
        cmd = [
            bun,
            "tools/sim/export-goal-rollout.ts",
            "--weights", weights,
            "--out", out_dir,
            "--stages", str(stage),
            "--seeds", str(seed),
            "--max-ticks", str(args.max_ticks),
            "--difficulty", args.difficulty,
            "--heartbeat", str(getattr(args, "heartbeat", 240)),
        ]
        if getattr(args, "goal_coarse", False):
            cmd.append("--coarse")
    elif getattr(args, "intent_rollout", False):
        cmd = [
            bun,
            "tools/sim/export-intent-rollout.ts",
            "--weights", weights,
            "--out", out_dir,
            "--stages", str(stage),
            "--seeds", str(seed),
            "--max-ticks", str(args.max_ticks),
            "--difficulty", args.difficulty,
            "--replan", str(getattr(args, "replan", 30)),
        ]
    else:
        cmd = [
            bun,
            "tools/sim/export-rl-rollout.ts",
            "--weights", weights,
            "--out", out_dir,
            "--stages", str(stage),
            "--seeds", str(seed),
            "--max-ticks", str(args.max_ticks),
            "--difficulty", args.difficulty,
            "--wver", wver,
            "--node-label", node_label,
        ]
        # goal-nn 卡 A2/A3：玩具奖励臂 / dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。
        if getattr(args, "reward", ""):
            cmd += ["--reward", args.reward]
        if getattr(args, "dodge", ""):
            cmd += ["--dodge", args.dodge]
    return cmd
