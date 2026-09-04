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
            "--weights",
            weights,
            "--out",
            out_dir,
            "--stages",
            str(stage),
            "--seeds",
            str(seed),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--heartbeat",
            str(getattr(args, "heartbeat", 240)),
        ]
        if getattr(args, "goal_coarse", False):
            cmd.append("--coarse")
    elif getattr(args, "intent_rollout", False):
        cmd = [
            bun,
            "tools/sim/export-intent-rollout.ts",
            "--weights",
            weights,
            "--out",
            out_dir,
            "--stages",
            str(stage),
            "--seeds",
            str(seed),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--replan",
            str(getattr(args, "replan", 30)),
        ]
    else:
        cmd = [
            bun,
            "tools/sim/export-rl-rollout.ts",
            "--weights",
            weights,
            "--out",
            out_dir,
            "--stages",
            str(stage),
            "--seeds",
            str(seed),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--wver",
            wver,
            "--node-label",
            node_label,
        ]
        # goal-nn 卡 A2/A3：玩具奖励臂 / dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。
        # A2 已废（plan/rl-training-config.md §4：奖励由课程配置公式驱动，TS 不再算
        # reward）——不再追加 --reward；--dodge 保留透传。
        if getattr(args, "dodge", ""):
            cmd += ["--dodge", args.dodge]
        # M1d：课程自定义关 stageJson + 命数/星级覆盖（plan §5.2；四守卫在导出器端）。
        # stageJson 只有 stage ∈ [2000..] 的自定义关才有；arena/真实关恒 None。
        from rl.config import args_rollout_overrides, stage_json_for_args

        sj = stage_json_for_args(args, stage)
        if sj:
            cmd += ["--stage-json", sj]
        # D14：语料血缘 course_fp 进 shard manifest（远程 PPO 装载校验
        # job.course_fp == shard.course_fp，跨课程语料绝不混训）。
        cfp = course_fp_for_args(args)
        if cfp:
            cmd += ["--course-fp", cfp]
        for k, v in args_rollout_overrides(args).items():
            cmd += [f"--{k}", v]
    return cmd


def course_fp_for_args(args) -> str:
    """课程文件 sha256（D14 语料血缘）。无课程返回 ""（非课程路径不写 course_fp）。

    与远程发布（remote/hub_client.publish_job）同一算法：sha256(课程 jsonc 文件字节)。
    """
    import hashlib

    course = getattr(args, "course_obj", None)
    if course is None:
        return ""
    path = getattr(args, "course_path", "") or ""
    if not path:
        from rl.config import resolve_course

        try:
            path = str(resolve_course(course.name))
        except Exception:
            return ""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""
