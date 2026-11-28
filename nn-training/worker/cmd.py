"""build_rollout_cmd —— rollout 子进程命令模板（2026-09-02）。

queue_local 的 run_rollout / run_local_rollout 曾各自手工拼装
`bun tools/sim/export-{goal,intent,rl}-rollout.ts` 命令（三分支 × 两处 ≈ 200 行
重复）。本模块按 mode 统一拼装——新增 exporter 或参数时只改这一处。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class RolloutCmdSpec:
    """一局 rollout 子进程命令的全部输入（P0-3 批 D2b：2 位置 + 8 关键字 → 一个对象）。

    失效面：`stage` / `seed` 是两个相邻 int —— 互换后命令的 `--stages` 拿到种子、`--seeds`
    拿到关卡号：导出器只报「这组对局没有可用关卡」或静默产出一批不对局的轨迹；
    `weights` / `out_dir` 是两个相邻路径 str（权重 vs 落盘目录），互换后 `--out` 指进权重文件；
    `wver` / `node_label` / `code_sha` 是三个身份串 —— 互换后节点互斥/血缘归属静默错位。
    """

    bun: str
    args: Any
    weights: str
    out_dir: str
    stage: int
    seed: int
    wver: str = ""
    node_label: str = ""
    #: `True` = 这份命令由**别的机器**执行（逐轮上云 / 半离线整段）。
    node_side: bool = False
    code_sha: str = ""


def build_rollout_cmd(spec: RolloutCmdSpec) -> list[str]:
    """按 args.goal_rollout / intent_rollout 选 exporter 并拼装 bun 命令。

    入参 = 一个 `RolloutCmdSpec`（P0-3 批 D2b）；下面一次展开，函数体逐字不动。

    三模式差异（plan/distributed-rollout.md）：
      goal   → export-goal-rollout.ts（心跳承诺期，--heartbeat [--coarse]）
      intent → export-intent-rollout.ts（意图步半 MDP，--replan）
      per-tick → export-rl-rollout.ts（--wver/--node-label [--reward/--dodge]）

    `node_side=True` = 这份命令将由**别的机器**执行（逐轮上云 / 半离线整段）：路径必须是 job
    目录内的相对路径、且节点上必须有那份数据。起始分布（state_init）的快照**还没**走这条通道
    （plan §P2.5）⇒ 在 `argv_init_for` 里**响亮拒**（宁可拒发，不可静默跑成标准开局）。
    """
    bun, args = spec.bun, spec.args
    weights, out_dir = spec.weights, spec.out_dir
    stage, seed = spec.stage, spec.seed
    wver, node_label = spec.wver, spec.node_label
    node_side, code_sha = spec.node_side, spec.code_sha
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
        # M1d / D14（2026-11-28，P′-4a）：意图线与 per-tick 同规——自定义关
        # （classic35 id 2000+）与语料血缘必须随命令走，否则 2000+i 在导出器侧
        # 走到 STAGES[2000] 未定义，整批静默 SKIP；远端 PPO 还会因缺 course_fp 拒收。
        _append_stage_lineage(cmd, args, stage)
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
        # greedy 采集（plan/greedy-outcome-quota rev4）：只有 greedy 阶段临时置位；
        # 普通 rollout 不置位 ⇒ 命令逐字节不变。TS 侧据此写 manifest labelMode。
        if bool(getattr(args, "rollout_argmax", False)):
            cmd.append("--teacher-argmax")
        # P2（plan/replay-corpus.plan.md）：replay 语料只产 replay+标签（不产 obs 三张）。
        # 只在显式 replay 时追加 ⇒ full 命令逐字节不变。
        # ★ A2（2026-11-17）：`node_side=True`（kind=iter 整轮上云 / --export-bundle 离线包）
        # **一律 full** —— 那种情况下 rollout 与 PPO 在同一台机器上，语料不上路：replay 零收益，
        # 而盘上从此没有 obs（节点侧任何同机读 shard 的腿都会断）。判据的单一出处是
        # `trainer/loop_round_steps._resolve_corpus_fmt`（它改的是 args），这里只是**同一规则的
        # 第二道闸**：本函数的 args 可能来自节点自己的 CLI 缺省（那缺省 2026-11-17 起已是 replay）。
        want_replay = (
            not node_side and str(getattr(args, "corpus_fmt", "full") or "full") == "replay"
        )
        if want_replay:
            cmd += ["--corpus-fmt", "replay"]
            # 2026-11-17（docs/nn/remote-transport.md §154）：把本机现算的 codeHash 透传给导出器，
            # 它就不必自己遍历 180 个文件（实测 13.5 ms/进程，成本全在 syscall 上）。**只在本机腿**
            # 透传：远端跑的树是 job 里解出来的 ts_code.zip，拿 hub 的 hash 当它的身份就是撒谎。
            # 与 `--corpus-fmt replay` 同一条分支是巧合也是纪律——replay_meta.codeSha 只有 replay
            # 语料才有，而 replay 恒为本机腿（`node_side` 一律 full，见上）。
            cmd += ["--code-sha", code_sha or local_code_sha()]
        # goal-nn 卡 A2/A3：玩具奖励臂 / dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。
        # A2 已废（plan/rl-training-config.md §4：奖励由课程配置公式驱动，TS 不再算
        # reward）——不再追加 --reward；--dodge 保留透传。
        if getattr(args, "dodge", ""):
            cmd += ["--dodge", args.dodge]
        # R2 事件 rung（plan/new-era-stop.plan.md §6）：课程 decision_events=True ⇒
        # rollout 走事件门（均匀 K ∪ threat-ONSET + Δt≥3）；缺席/False = 老行为（导出器缺省）。
        # 与 --dodge 同形：显式开关才透传，无静默。
        if bool(getattr(args, "decision_events", False)):
            cmd += ["--decision-events"]
        # 决策周期 K（plan/k5-rhythm.plan.md）：课程 decision_k ≠10 才透传
        # （缺席/10 = 导出器缺省 K=10，命令逐字节不变）。
        _dk = int(getattr(args, "decision_k", 10))
        if _dk != 10:
            cmd += ["--decision-k", str(_dk)]
        # M1d：课程自定义关 stageJson + D14 语料血缘（per-tick / intent 共用一份）。
        _append_stage_lineage(cmd, args, stage)
        # 起始分布（plan/x20-state-init.plan.md P1/P3）：人类中段快照注入，一局一个快照。
        # 派生（哪一局哪个切点）住 `biz/state_init`：纯函数、key 含 (rotate_seed, it, stage, seed)
        # ⇒ 断点续跑不会换起始状态，每轮换（§15.1）。课程没开 state_init ⇒ None（零回归）。
        from worker.state_init import argv_init_for

        init = argv_init_for(args, stage=int(stage), seed=int(seed), node_side=node_side)
        if init is not None:
            cmd += ["--init-snapshot", init.path]
        from worker.config import args_rollout_overrides

        for k, v in args_rollout_overrides(args).items():
            # 键是下划线（lives_override），导出器只认连字符（--lives-override，
            # 未知 flag 静默忽略）——c6-gae 本地 3命1星事故根因，见 tests/worker/test_rl_cmd.py。
            cmd += [f"--{k.replace('_', '-')}", v]
    return cmd


def _append_stage_lineage(cmd: list[str], args: object, stage: int) -> None:
    """M1d / D14：自定义关 stageJson + 语料血缘（per-tick 与 intent 共用）。

    - `stageJson` 只有 stage ∈ [2000..] 的自定义关才有（arena/真实关恒 None）。缺它 ⇒
      导出器按 `STAGES[stage]` 走：2000+i 未定义 ⇒ 整批静默 SKIP（意图线旧行为）。
    - `course_fp` / `corpus_fp` = 远端 PPO 的 D14 装载校验（job 与 shard 必须同源）。
    """
    from worker.config import stage_json_for_args

    sj = stage_json_for_args(args, stage)
    if sj:
        cmd += ["--stage-json", sj]
    cfp = course_fp_for_args(args)
    if cfp:
        cmd += ["--course-fp", cfp]
    cfp2 = corpus_fp_for_args(args)
    if cfp2:
        cmd += ["--corpus-fp", cfp2]


@functools.cache
def local_code_sha() -> str:
    """本机 TS 代码身份（`--code-sha` 的取值）：**进程内只算一次**。

    成本口径：`common.codehash.compute_code_hash()` ≈ 0.03 s（同 180 个文件，Python 侧比 TS
    略慢）；逐局重算（35 局/轮）比导出器自己算还贵，故按进程记忆。

    记忆用 `functools.cache` 而不是模块级 `list`（P0-5，2026-11-21）：旧写法是「0/1 元素的 list
    当 memo」，读的人要自己回答「谁清的、为什么是 list」；`cache` 把「这份值只算一次」变成函数
    自己的属性，清场 `local_code_sha.cache_clear()`（用例需要重算时）也是最大路货的口径。
    延迟 import 留在函数体里：`worker.cmd` 的 import 不该把 `common.codehash` 一起拖进来。

    与 `sampler-agent.ts::memoizedCodeHash` 同一假设："运行中的代码" = 进程启动时那份，要换
    hash 只有重启。运行中 git pull 的窗口里 `replay_meta.codeSha` 可能标旧码——这是审计字段，
    与节点门无关（门比的是 ping 自报值），且该窗口本来就无人在跑新代码。
    """
    from common.codehash import compute_code_hash

    return compute_code_hash()


def course_fp_for_args(args) -> str:
    """课程文件 sha256（D14 **文件**血缘）。无课程返回 ""（非课程路径不写 course_fp）。

    ⚠ 它不是「语料血缘」——语料身份是 `corpus_fp_for_args`（语义哈希）。两者分工见
    `biz/resume._scan_shards`；把文件字节当成语料身份正是 §2/A 误诊的源头。

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
        from worker.config import resolve_course

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
    from worker.config import corpus_identity_fp

    try:
        return corpus_identity_fp(course)
    except Exception:
        return ""
