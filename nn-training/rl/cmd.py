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
    node_side: bool = False,
) -> list[str]:
    """按 args.goal_rollout / intent_rollout 选 exporter 并拼装 bun 命令。

    三模式差异（plan/distributed-rollout.md）：
      goal   → export-goal-rollout.ts（心跳承诺期，--heartbeat [--coarse]）
      intent → export-intent-rollout.ts（意图步半 MDP，--replan）
      per-tick → export-rl-rollout.ts（--wver/--node-label [--reward/--dodge]）

    `node_side=True` = 这份命令将由**别的机器**执行（逐轮上云 / 半离线整段）：路径必须是 job
    目录内的相对路径、且节点上必须有那份数据。起始分布（state_init）的快照**还没**走这条通道
    （plan §P2.5）⇒ 在 `argv_init_for` 里**响亮拒**（宁可拒发，不可静默跑成标准开局）。
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
        # D14 语义版：corpus_fp = 语料身份（env+reward 解析值哈希）与文件血缘并存。
        # 预算/路径/注释类编辑只动 course_fp，不动 corpus_fp ⇒ 不触发混训拒收
        # （DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25；worker 侧优先比 corpus_fp）。
        cfp2 = corpus_fp_for_args(args)
        if cfp2:
            cmd += ["--corpus-fp", cfp2]
        # 起始分布（plan/x20-state-init.plan.md P1/P3）：人类中段快照注入，一局一个快照。
        # 派生（哪一局哪个切点）住 `rl/state_init`：纯函数、key 含 (rotate_seed, it, stage, seed)
        # ⇒ 断点续跑不会换起始状态，每轮换（§15.1）。课程没开 state_init ⇒ None（零回归）。
        from rl.state_init import argv_init_for

        init = argv_init_for(args, stage=int(stage), seed=int(seed), node_side=node_side)
        if init is not None:
            cmd += ["--init-snapshot", init.path]
        for k, v in args_rollout_overrides(args).items():
            # 键是下划线（lives_override），导出器只认连字符（--lives-override，
            # 未知 flag 静默忽略）——c6-gae 本地 3命1星事故根因，见 tests/test_rl_cmd.py。
            cmd += [f"--{k.replace('_', '-')}", v]
    return cmd


def course_fp_for_args(args) -> str:
    """课程文件 sha256（D14 **文件**血缘）。无课程返回 ""（非课程路径不写 course_fp）。

    ⚠ 它不是「语料血缘」——语料身份是 `corpus_fp_for_args`（语义哈希）。两者分工见
    `rl/resume._scan_shards`；把文件字节当成语料身份正是 §2/A 误诊的源头。

    与远程发布（remote/hub_client.publish_job）同一算法：sha256(课程 jsonc 文件字节)。
    字节源 = **启动期冻结**（`args.course_frozen_bytes`，course_from_args 落）——
    mid-run 热加载编辑（含被拒的语料身份改动）不改变血缘，不进云端 payload。
    """
    import hashlib

    course = getattr(args, "course_obj", None)
    if course is None:
        return ""
    frozen = getattr(args, "course_frozen_bytes", None)
    if frozen:
        return hashlib.sha256(frozen).hexdigest()
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


def corpus_fp_for_args(args) -> str:
    """语料身份指纹（D14 语义版，config.corpus_identity_fp）。无课程返回 ""。

    与远程发布（loop_steps → hub_client.publish_job 的 corpus_fp）同源：都基于
    **解析后**的 CourseConfig 语义哈希，与文件字节无关（课程内注释/预算字段编辑
    不改变本值）。异常回退 ""（= manifest 缺该字段，worker 走 legacy course_fp）。
    """
    course = getattr(args, "course_obj", None)
    if course is None:
        return ""
    from rl.config import corpus_identity_fp

    try:
        return corpus_identity_fp(course)
    except Exception:
        return ""
