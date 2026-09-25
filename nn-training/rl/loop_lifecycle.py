"""loop_lifecycle —— **主循环骨架** mixin（2026-09-25 从 rl/loop_core.py 拆出，S4 第十九刀）。

这一簇 7 个方法 = 一条真链（实测连通分量，非人工归类）：**启动准备 → run 编排 → 轮派发 →
收官/停车**；另有 7 个模块级定义（`WAIT_RETRY_SEC` / `_course_file_fp` / kickstart 自检两件 /
`KICKSTART_DEFAULT_WARN` / 对照行 / `should_park_on_done`）**只能随它走**（闭包实测 150 行），
它们原先混在 `TrainingLoop` 本体里。实测：7 方法 351 行 + 7 模块级定义 150 行。
`rl/loop_core.py` 因此 **931 → 446 行**（本模块 **673 行** = 7 方法 351 + 7 模块级定义 150 + 借用声明块
42 名与头注/import/类壳）。

## 宿主判据：为什么是 `TrainingLoop`（组合根），而不是某个 sibling mixin

本仓的组装修辞是「**调用者依赖被调用者**」（`rl/loop_remote.py` 头注）：把被调用的一簇做成
调用者那一侧的基类。本簇**破例**，因为入边把这条路堵死了——`_evalboard_idle` 被两个 mixin
以 `self.` 调用：

    rl/loop_remote_job.py  （TrainingRemoteJob；S4 第二十二刀前在 loop_remote.py）
    rl/loop_round_steps.py （RoundSteps）

新混入要同时是两者的祖先才接得住这条既有入边；而
`ancestors(RoundSteps) = {RoundSteps, TrainingVolume}` 与
`ancestors(TrainingRemote) = {TrainingSteps, TrainingRemote, TrainingEval}` 的**交集为空**
⇒ 任何 sibling 都当不了宿主，唯一公共祖先是 `TrainingLoop` 本身。
出边同指：`.run` / `.run_one_round` / `._setup` / `.finish_course` 的调用者全是
`TrainingLoop` 实例（`rl/loop_serve.py` 的 `engine` 是多态的 `BcLoop | TrainingLoop`，
而 `BcLoop` 自带 `_setup`／`run_one_round`，与本簇无关）。

代价与边界：`TrainingLoop.__bases__` 由三件套**追加**为
`(RoundSteps, TrainingSteps, TrainingGuards, TrainingLifecycle)`——追加末位是因为
**7 个成员名在既有混入里零同名定义**（实测），MRO 不会遮罩；`TrainingSteps.__bases__` /
`RoundSteps.__bases__` / 各 `__mro__[1]` 逐字不变（S17/S18 的「追加不插队」纪律仍成立）。

## 跨模块手（本模块是**被调用**的一方，且只此一条入边）

`loop_remote_job.py` / `loop_round_steps.py` → `self._evalboard_idle(...)`：搬家前它们解析到
`loop_core` 里的定义，搬家后解析到本模块——**手数不变，只是换了落点**（两处源码一字不改）。
反方向（本模块 → 其余混入）是混入常态的动态解析：`self.round_steps()` / `self.round_failure()`
（RoundSteps）、`self._drain_pending_eval()`（TrainingEval）、`self._sync_cloud_halt()`
（TrainingGuards）——同一条 `self`，同一把锁。

## 状态归属不变

槽位声明**全在** `TrainingLoop.__init__`（跨轮字段的持有者不动），本模块只赋值/读取；
`_course_fp` / `_corpus_fp` 由 `_setup_common` 赋值、被 `_prepare_iter_dir` / `_rollout_phase`
读取——这条写-读手闭集写在 `tests/test_loop_lifecycle_split.py` 里。

## patch 点

7 个模块级名字**零 monkeypatch 点**（实测），只有 4 处普通 import，已同步到本模块：
`rl/collect_only.py`（延迟 import，保环断）、`tests/test_loop_park.py`、
`tests/test_paired_seed_check.py`、`e2e/test_run_rl_m1.py`。
`rl/loop_core.py::run_inspect`（`_run_inspect` 的委托点）**刻意留在旧家**——那是文档化的
可替换点（`rl/loop.py` 再导出它、测试替换它）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import dist_common
from rl.breaker import CIRCUIT_EXIT_CODE
from rl.course import resolve_rotate_seed
from rl.events import write_run_complete, write_run_start
from rl.log import log
from rl.loop_guards import TrainingGuards
from rl.loop_round import (
    ROUND_BUNDLE_EXIT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
    ROUND_WAIT,
    RoundContext,
    RoundOutcome,
    RoundYieldError,
)
from rl.loop_steps import kickstart_coef
from rl.queue import REPO_ROOT, RUN_ID
from rl.rollout_phase import join_precollect_child
from rl.train_ledger import LedgerSpec, load_ledger

#: `run()` 撞上 `ROUND_WAIT` 时的再问间隔（秒）。单课程驱动器是阻塞语义（退避后再问同一轮），
#: 不是让位——真正的让位在 supervisor（`rl/loop_runner` 的 `waiting()`，间隔 `poll_interval`）。
WAIT_RETRY_SEC = 5.0


def _course_file_fp(args) -> str | None:
    """D14 **文件**血缘：课程文件 sha256；无课程返回 None（旧行为不过滤）。

    ⚠ 措辞纪律（§2/A 误诊源头）：这是**文件字节**，不是「语料血缘」。语料身份是
    `corpus_fp`（`corpus_fp_for_args` / `config.corpus_identity_fp`：env+reward 解析值）。
    两者分工与优先级见 `rl/resume._scan_shards`（由 `d14_corpus_match` 统一裁决）。

    torch-free（hub 远程分支也调用——只读文件字节）。与
    remote/hub_client.publish_job 的 course_fp 同算法，两端必须一致。
    字节源 = 启动期冻结（args.course_frozen_bytes）——mid-run 热加载编辑不改血缘。
    """
    import hashlib

    course = getattr(args, "course_obj", None)
    if course is None:
        return None
    frozen = getattr(args, "course_frozen_bytes", None)
    if frozen:
        return hashlib.sha256(frozen).hexdigest()
    path = getattr(args, "course_path", "") or ""
    if not path:
        from rl.config import resolve_course

        try:
            path = str(resolve_course(course.name))
        except Exception:
            return None
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _kickstart_startup_check(args: Any, start_it: int) -> float:
    """BC 缰绳重启自检（纯逻辑，可单测；IO 仅读存在性 + 写一行日志）。

    ① bc 文件启动期即查（换机器漏同步权重 ≠ 静默裸奔；失败指到 in-use 备份）；
    ② 返回并落日志 kk(start_it)（衰减按 run 原点续算，重启不再回满额）；
    ③ §5.1：响亮报出**初值来源**（课程 `kickstart_init` 显式 / 缺省）——拿缺省大值
       复活正是 C 事故的形状（kk=1 连烧 30 轮），这时该被拦下来问一句；
    ④ §5.3：开腿前的**起点-基线对照行**（本腿起点的评估读数 vs 基线读数，读账本）。
    kickstart 未启用时返回 0.0 且零副作用。
    """
    if not bool(getattr(args, "kickstart_ref", False)):
        return 0.0
    bc_path = str(getattr(args, "bc", "") or "")
    if not bc_path or not Path(bc_path).exists():
        raise SystemExit(
            f"[run_rl] kickstart_ref 要求课程 bc 权重存在（换机器漏同步？）：{bc_path!r}——"
            "从 nn-training/weights/in-use/ 常备备份恢复（§379），禁裸奔启动"
        )
    kk0 = kickstart_coef(args, start_it)
    course = getattr(args, "course_obj", None)
    explicit = course is not None and "kickstart_init" in getattr(course, "model_fields_set", set())
    src = (
        f"课程 kickstart_init={getattr(course, 'kickstart_init', None)}"
        if explicit
        else f"缺省（rl-config/argparse kickstart_kl={getattr(args, 'kickstart_kl', 0.0)}）"
    )
    log(
        f"[run_rl] kickstart: ref={bc_path} "
        f"kk(start_it={start_it})={kk0:.6g}（run 原点衰减，resume 不复位；初值来源：{src}）"
    )
    if not explicit and kk0 >= KICKSTART_DEFAULT_WARN:
        # 不断言、不断向后兼容：只是把「这条腿拿的是缺省大值」说出来。
        log(
            f"[run_rl] WARNING kickstart 初值未在课程里声明，用的是缺省 kk={kk0:.6g}"
            f"（≥{KICKSTART_DEFAULT_WARN}）——从已收敛的权重复活时，满额锚会把起点洗回去"
            "（C 事故：it1 kl=0.90 连烧 30 轮）。请在课程里显式写 kickstart_init（或调小它）"
        )
    _kickstart_baseline_row(args)
    return kk0


def _paired_seed_startup_check(args: Any, rotate_seed: int, rs_source: str) -> None:
    """§2.5 配对 rotateSeed 启动自检（plan/accident.plan.md §2）。

    两件事，分工明确：
      · **拒启**（唯一无歧义的「不等」）：课程声明了 `paired_rotate_seed=V`，而实际生效的
        rotateSeed ≠ V —— 那一定是「声明被静默丢弃」或「有人在跑一对只有一条腿带对 V 的腿」，
        代价是一条腿按错误种子流跑满 80 轮（`ent_break` 漏映射前科同族）。
      · **响亮报告**：同 V 课程（= 机器口径的「配对对端」）与各自账本末条 run_start.rotateSeed；
        无对端 / 对端不在同 V 上 ⇒ WARNING（不阻断，理由见 `rl/paired.py` 头注：账本是历史累积，
        用陈旧读数杀在跑的腿比漏报更贵——真正的 fail-fast 闸门在控制台开课回执那一屏）。

    未声明 `paired_rotate_seed` 的课程照旧（单腿口径），只打一行说明——不打扰既有课程。
    """
    from rl.paired import declared_paired_seed, pair_check

    course = getattr(args, "course_obj", None)
    declared = declared_paired_seed(course)
    if declared is not None and int(rotate_seed) != int(declared):
        raise SystemExit(
            f"[run_rl] paired_rotate_seed={declared} 但实际生效的 rotateSeed={rotate_seed}"
            f"（来源 {rs_source}）—— 两条腿会跑在不同的种子流上，**配对前提已被破坏**。"
            "查 flat_overrides 映射（课程键 → args.rotate_seed）与是否有人用 --rotate-seed 后门覆盖。"
        )
    traj_root = Path(getattr(args, "traj", "") or ".").parent
    course_name = str(getattr(args, "course", "") or "")
    for line in pair_check(
        declared=declared,
        effective=int(rotate_seed),
        source=rs_source,
        traj_root=traj_root,
        self_name=course_name,
    ):
        log(line)


#: 缺省初值大到该被警告的阀值（§5.1）。0.5 = 一半的锚权就已经能把更新压向 ref。
KICKSTART_DEFAULT_WARN = 0.5


def _kickstart_baseline_row(args: Any) -> None:
    """§5.3 开腿前对照行：本腿起点（账本里最近的评估读数）vs 基线（it0 行）——只读、只打印。

    为什么放在启动期而不是控制台：执行面才有 args/_jsonl_path，而一行「起点 35.5% vs
    基线 35.0%（差 0.5pp）配 kk=1」正是 C 事故里**该被拦下来问一句**的那个事实
    （差在噪声带里还配满额锚 = 无论如何都会先变差）。控制台要展示它得等下一轮开发，
    而训练侧现在就能说——日志是它现成的展示面。读不到就静默（不阻断启动）。
    """
    traj = str(getattr(args, "traj", "") or "")
    if not traj:
        return
    try:
        from rl.gate_check import read_trend_rows
        from rl.kickstart_burn import baseline_reading

        # 与 `_gate` / 干烧熔断同一个读者与同一个文件（per-tick 评估行在 eval_log.jsonl）。
        rows = read_trend_rows(Path(traj) / "eval_log.jsonl", include_baseline=True)
        base = baseline_reading(rows)
        last: float | None = None
        for r in rows:
            if isinstance(r, dict):
                it = r.get("iter")
                v = r.get("winRate")
                if isinstance(it, int) and it > 0 and isinstance(v, (int, float)):
                    last = float(v)
        if base is None:
            log("[run_rl] kickstart burn 基线：账本里无 it0 行（本腿还没跑过 bc 基线评估）")
            return
        if last is None:
            log(f"[run_rl] kickstart burn 基线：{base * 100:.1f}%（起点对照行：账本无历史评估点）")
            return
        gap = (last - base) * 100
        log(
            f"[run_rl] kickstart burn 起点-基线对照：起点 {last * 100:.1f}% vs 基线 "
            f"{base * 100:.1f}%（差 {gap:+.1f}pp）——差在噪声带里又配大 kk 就该先问一句"
        )
    except Exception as e:  # 对照行是观测，永不得阻断启动
        log(f"[run_rl] WARN kickstart burn 对照行生成失败（{type(e).__name__}: {e}）")


def should_park_on_done(args, smoke_void: bool) -> bool:
    """ALL DONE 后停车还是退出：--smoke 作废干净退出 / --exit-on-done → 退出
    （旧行为：前台脚本/预演等待进程结束）；其余一律停车不断进程。"""
    if smoke_void:
        return False
    return not bool(getattr(args, "exit_on_done", False))


class TrainingLifecycle:
    """主循环骨架（7 方法 = 一条链；见本模块头注的宿主判据）。

    本类**不是** `TrainingLoop` 的基类链上的一个 sibling——它由组合根 `TrainingLoop` 直接
    继承（追加末位）。`_evalboard_idle` 的入边（`TrainingRemote` / `RoundSteps` 的 `self.`
    调用）之所以仍解析，就是因为组合根在 MRO 里同时高于两者。
    """

    # 依赖的 `TrainingLoop` 实例属性（声明类型供 mypy/阅读；实际赋值在
    # `TrainingLoop.__init__` / 本类自己的 `_setup_common`）。与各兄弟混入里的同类声明
    # **有意并存**（同 `rl/loop_volume.py`）：混入的状态契约必须在**每个**文件里对 mypy
    # 可见，否则本文件里的 `self._traj_dir` 会被判成未声明属性；运行期的唯一真相仍是同一
    # 实例上的那一份。用 `Any` 而不是精确类型：同一名字在兄弟文件里已各有声明。
    args: Any
    bun: Any
    ppo_backend: Any
    _auto_inspect: Any
    _bc_ref: Any
    _collect_child: Any
    _consec_fail: Any
    _corpus_fp: Any
    _course_fp: Any
    _deadline: Any
    _device: Any
    _eb_thread: Any
    _eb_window: Any
    _ent_peak: Any
    _ent_streak: Any
    _eval_at_set: Any
    _eval_every: Any
    _jsonl_path: Any
    _kl_streak: Any
    _ledger: Any
    _model: Any
    _opt: Any
    _ppo_goal: Any
    _ppo_intent: Any
    _ppo_mod: Any
    _prev_entropy: Any
    _ref_model: Any
    _rotate_seed: Any
    _save_weights_json: Any
    _soft_remediate_count: Any
    _start_it: Any
    _stop_loss_streak: Any
    _total: Any
    _train_samples_total: Any
    _train_sec_total: Any
    _traj_dir: Any
    _traj_root: Any
    _tripped: Any

    #: 兄弟混入的方法（混入常态：在组合实例上动态解析）——逐条声明类型，理由同
    #: `rl/loop_volume.py`：混入间互调的方法必须在每个文件里类型可见。
    _drain_pending_eval: Any  # TrainingEval（rl/loop_eval.py）
    _sync_cloud_halt: Any  # TrainingGuards（rl/loop_guards.py）
    round_steps: Any  # RoundSteps（rl/loop_round_steps.py）
    #: `round_failure` 声明为**精确签名**而不是 `Any`：`run_one_round` 直接 `return` 它，
    #: `Any` 会让 mypy 报 no-any-return（方法体要逐字节保持搬前原样，所以把精度放在声明里）。
    round_failure: Callable[..., RoundOutcome]  # RoundSteps（rl/loop_round_steps.py）

    # ------------------------------------------------------------------ 编排

    def run(self) -> None:
        args = self.args
        self._setup()
        it = self._start_it - 1
        smoke_void = False  # --smoke 作废干净退出：收官后仍退出进程（预演等待结束）
        while args.iters <= 0 or it < args.iters:
            it += 1
            # 吞吐 T4：本轮开头检查预采子进程产出（句柄消费后归零）
            self._collect_child = join_precollect_child(
                self._collect_child,
                self._traj_root,
                it,
                args,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
            if self._deadline is not None and time.time() >= self._deadline:
                log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
                break
            outcome = self.run_one_round(it)
            it = outcome.it  # 段跑会推进 it；必须以返回值为准
            if outcome.status == ROUND_BUNDLE_EXIT:
                return
            if outcome.status == ROUND_SMOKE_STOP:
                smoke_void = True
                break
            if outcome.status == ROUND_STOP:
                break
            if outcome.status == ROUND_RETRY:
                it -= 1
            if outcome.status == ROUND_WAIT:
                # 单课程驱动器的让位（RL 的 `run_one_round` 今天从不返回它——本分支是**语义
                # 完整性**：等外部事实时不能把这一轮当成跑完（那会跳轮），也不能原地空转烧 CPU）。
                # 退避一小段再来问同一轮；这仍是阻塞式单课程语义（真正的让位在 supervisor 那边）。
                it -= 1
                time.sleep(WAIT_RETRY_SEC)

        # P0 收官 drain（用户指令：最终轮立即 eval）：循环结束（跑满/break/预算）
        # 后，为最新已完成且无完整 summary 的评估轮权重派发并等收官。smoke 轮跳过。
        if not smoke_void:
            self._drain_pending_eval()
        if self._tripped is not None:
            sys.exit(CIRCUIT_EXIT_CODE)
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")
        if not should_park_on_done(args, smoke_void):
            return
        self._park_after_completion(it)

    def run_one_round(self, it: int) -> RoundOutcome:
        """跑一轮（R2c-2 从 `run()` 抽出；R2c-3 起由**步骤表**驱动，仍是同一套控制流）。

        组合路径 = 依次施加 `rl.loop_round.STEP_ORDER` 里的每一步：某一步给出终态
        （门 / 熔断 / 止损 / 预算 / 停腿 / 冒烟 / 全离线导出）即返回，否则一路到 `cleanup`
        （它返回 `ROUND_NEXT`）。细粒度路径（`LoopRunner.run_step`）走**同一张表**，只是每步
        之间可以把执行权交给别的课程——两条驱动因此不可能漂移（加一步必须同时进表）。

        `ctx` = 轮内状态（`pairs` / `dist_cfg` / `t_rollout` / `seg` / `eval_rec`，以及会被
        半离线整段推进的 `it`）。语义与抽取前逐条一致：5 连击重试、冒烟作废、段跑推进 it、
        异常分类，全部在 `round_failure` 里**与细粒度执行器共用一份判决**。

        返回：`RoundOutcome(status, ctx.it)`；`it` 可能大于入参（半离线整段一次推进多轮）
        ——调用方**必须**用返回的 it 继续，否则会重跑已跑过的段。
        """
        ctx = RoundContext(it=it)
        try:
            for step in self.round_steps():
                res = step(ctx)
                if res is None:
                    continue
                if res.is_final:
                    return RoundOutcome(res.outcome or ROUND_NEXT, ctx.it)
                if res.is_wait:
                    # 组合路径没有让位点（单课程前台跑，没人接手）。步骤自己要求让位 = 设计
                    # 走岔了 ⇒ 响亮报错，而不是静默停住或空转。R2c-3 余下部分把 `wait_job` /
                    # eval 尾巴 / 预采子进程真轮询化时，必须同时给组合路径加 `ROUND_WAIT`。
                    raise RoundYieldError(
                        f"步骤在组合路径里要求让位（{res.reason}）——组合路径没有让位点，"
                        "请走 Supervisor 细粒度驱动"
                    )
            return RoundOutcome(ROUND_NEXT, ctx.it)
        except RoundYieldError:
            # 设计走岔 ≠ 一次失败：不落 iter_error、不计连击、不重试（否则它会伪装成
            # 普通引擎异常被吞进重试阶梯，症状是「每轮都重试同一轮」）。
            raise
        except (SystemExit, Exception) as e:
            # `SystemExit` 不是 `Exception`（基类是 BaseException），故并列捕获——与抽取前的
            # 四个 except 分支（BundleExportedError / SmokeVoidRoundError / SystemExit /
            # Exception）覆盖同一集合，分类判决搬进 `round_failure`。退避只在组合路径生效
            # （调度器的退避是 `TaskResult.retry` 的 resume_at，不在任务体里睡 30s）。
            return self.round_failure(e, ctx.it, backoff=True)

    def finish_course(self, it: int) -> None:
        """**本课程**收官（一轮跑满）的三件事，不含停车循环（R2d 拆分）。

        ① 本地停止采集（收敛在飞预采子进程，不留孤儿空烧）；② 向云机下发停机指示
        （PAUSE，能自停的释配额）；③ 账本落 `run_complete` 事件（console「已完成」横幅
        派生源）。

        **为什么从 `_park_after_completion` 里拆出来**：停车（死循环等重启）的语义前提是
        「这个进程就是这门课」。单进程多课程（`rl/loop_serve.py`）下停车会**冻住所有课**，
        所以那条路径只做本方法、调度器把该课队列置 `done`；单课程前台入口 = 本方法 + 停车
        （进程即该课）。两条路径因此共用同一份收官副作用（不会一边落 `run_complete`、
        另一边忘了）。
        """
        args = self.args
        total = args.iters if args.iters > 0 else "∞"
        # 在飞预采子进程（门判决等中途 break 时可能残留）：收敛掉，不留孤儿空烧。
        # 正常跑满时 spawn_next_collect 已因 it==iters 短路，child 恒为 None。
        child = getattr(self, "_collect_child", None)
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    log(f"[run_rl] 停车：收敛在飞预采子进程（it{it + 1} 未开跑）")
            except Exception as e:  # 收敛失败不阻断停车
                log(f"[run_rl] 停车：预采子进程收敛失败（{type(e).__name__}: {e}）")
            self._collect_child = None
        self._sync_cloud_halt(it, "PAUSE")
        reason = f"正常收官（it{it}/{total}），本地停采、云机已停机"
        try:
            write_run_complete(self._jsonl_path, it, int(args.iters or 0), reason)
        except OSError as e:
            log(f"[run_rl] 停车：run_complete 落账失败（{e}）— 仅横幅派生缺失，继续停车")
        log(
            f"[run_rl] 正常完成（it{it}/{total}）——本地停止采集，进程停车不断开；"
            "EvalBoard B 批照常认领；改大 iters 后经控制台 停止→启动 以继续训练"
        )

    def _park_after_completion(self, it: int) -> None:
        """正常收官（ALL DONE）→ 停车不断进程（2026-09-12 用户定案）。

        三件事（收敛采集 / 云机 PAUSE / `run_complete` 落账）在 `finish_course` 里；本方法
        = 它 + **永久停车**：期间 EvalBoard B 批照常认领（idle 窗常开、机器本就空闲；直连节点
        派发，不受 hub 云停机影响）——本地训练采集已停，只服务评估。中断（Ctrl-C/SIGTERM
        语义）干净返回。冒烟/--exit-on-done 不进这里。
        """
        self.finish_course(it)
        parked_min = 0
        while True:
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                log("[run_rl] 停车中收到中断——干净退出")
                return
            parked_min += 1
            # 停车期认领（60s 粒度）：复用 idle 窗逻辑——窗常开（无 rollout 抢占），
            # 有在途单元则只保窗不重复领，无则认领最早 pending 批；dist_cfg 每轮热读
            # （节点变更下一分钟即生效）。异常自吞（认领失败不影响停车）。
            try:
                try:
                    dist_cfg = dist_common.load_dist_config()
                except Exception:
                    dist_cfg = None
                self._evalboard_idle(it, dist_cfg)
            except Exception as e:
                log(f"[run_rl] 停车期认领失败（忽略）：{type(e).__name__}: {e}")
            if parked_min % 60 == 0:
                log(f"[run_rl] parked（已停车 {parked_min // 60}h）：无采集，等待重启")

    # ---------------------------------------------------------------- 启动

    def _setup(self) -> None:
        args = self.args

        # ===== 训练路径延迟导入（B7，2026-09-02）：collect-only 子进程已提前 return，
        # 此刻起才允许拉起 torch / ppo.* / models.*（CPU 上 ~3-8s 的 torch 加载不再
        # 出现在每轮的双缓冲预采子进程里）。=====
        #
        # ===== 单一 PPO 路径（2026-09-21，plan/accident.plan.md §3）：hub 免 torch（D2）=====
        # PPO 恒在 hub 队列上由 worker 认领执行 ⇒ 训练进程**永不**建 model/opt/ref 栈：
        # 不 import torch / ppo.*，模型与优化器零加载。C 腿式灾难（误配 → 本机 CPU PPO 慢 13
        # 小时）在结构上不可能：loop 自己没有计算能力，误配无处发生。
        import numpy as np

        np.random.seed(args.seed)
        self.ppo_backend = None
        self._model = None
        self._opt = None
        self._device = None
        self._ref_model = None
        self._bc_ref = None
        self._ppo_mod = None
        self._ppo_goal = None
        self._ppo_intent = None
        self._save_weights_json = None
        log(
            "[run_rl] single PPO path: hub torch-free (D2) — PPO runs on a claimed worker; "
            "rollout/eval stay local"
        )
        self._setup_common()

    def _setup_common(self) -> None:
        """两模式（local/remote）共享的启动尾部：traj 目录 / rotateSeed / run_start 账本 /
        日志 / eval 稀疏化 / 断点续跑定位。remote 分支在跳过 torch 构建后也走这里。"""
        args = self.args
        traj_root = Path(args.traj)
        traj_root.mkdir(parents=True, exist_ok=True)
        self._traj_root = traj_root
        self._jsonl_path = traj_root / "training_log.jsonl"
        # D14 **文件**血缘：课程文件 sha256（None = 非课程运行，不过滤——旧行为字节不变）
        self._course_fp = _course_file_fp(args)
        # D14 **语义**身份（§2/A，2026-09-21）：与远端发布/hub 打包/worker 装载同源
        # （`rl.cmd.corpus_fp_for_args` → `config.corpus_identity_fp`）。本地对账也要它：
        # 只比文件字节时，改一下课程里的预算/路径/注释就把自己历史的 shard 全判成异血缘
        # ⇒ 全量重采（而云端照收）。两者都传给 `completed_pairs`/`settled_stage_totals`，
        # 由 `d14_corpus_match` 按同一条规则决定“优先比语义、缺则回退字节”。
        from rl.cmd import corpus_fp_for_args

        self._corpus_fp = corpus_fp_for_args(args)

        # R2a（plan/r2-loop-task-queue §5）：**一次扫描**得到账本视图——续跑指针、累计量、
        # 熔断连击、提示类判决次数全由它重建（旧实现是 5 个扫描器各读一遍全文件）。
        # 视图同时是本进程的增量账本：写事件的调用点随后 apply_event ⇒ 永不重扫。
        # 用户裁决（2026-09-18）：门禁语义 = 扫账本，指标按课缓存、只在开课/续跑读一遍。
        self._ledger = load_ledger(self._jsonl_path, LedgerSpec.from_args(args))
        # 续跑继承 rotateSeed：已有 run_start 历史 → 沿用其 rotateSeed（课程连续 → it 续跑时
        # 下轮 (stage,seed) 与已落盘局一致 → 断点续跑剔除生效，不重跑已完成局）。
        # 全新开始（无 jsonl 历史，例如用户清空重建）才用当前时刻抖动种子。
        prev_rs = self._ledger.rotate_seed
        rotate_seed, rs_source = resolve_rotate_seed(
            args.seed, getattr(args, "rotate_seed", None), prev_rs, int(time.time())
        )
        if rs_source == "explicit":
            log(f"[run_rl] rotateSeed explicit override={rotate_seed} (paired-course mode; prev={prev_rs})")
        elif rs_source == "inherited":
            log(f"[run_rl] resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
        self._rotate_seed = rotate_seed
        # G13 duty 的分子：从账本重算累计有效训练（Σ 真训练秒）。
        # 进程内存累计重启会归零 → 占空比被低估 → 误报"在烧事故"；账本是 SSOT。
        self._train_sec_total = self._ledger.train_sec_total
        self._train_samples_total = self._ledger.train_samples_total
        # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
        # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。
        write_run_start(self._jsonl_path, args, rotate_seed)
        # §2.5 配对核对：本课声明 vs 实际生效（不等 ⇒ 拒启）+ 同 V 课程表与各臂账本读数（响亮）。
        # 位置：写 run_start **之后**（核对要读到本臂刚落的这一条）。
        _paired_seed_startup_check(args, rotate_seed, rs_source)

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
        log(f"training_log: {self._jsonl_path}")
        log(f"[run_rl] runId={RUN_ID}")

        # 自动巡检：per-tick 仅对默认 traj 生效（巡检脚本默认读 tmp/rl-traj）；
        # intent/goal 总是生成巡检 HTML（run_inspect 显式传 --traj-dir）。
        auto_inspect = (
            True
            if args.mode in ("intent", "goal")
            else traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
        )
        if auto_inspect:
            log(
                "[run_rl] per-iteration auto-inspection ENABLED (HTML report after each ppo_backend)"
            )
        self._auto_inspect = auto_inspect

        self._deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
        self._total = "∞" if args.iters <= 0 else str(args.iters)
        self._prev_entropy = None
        # consec_fail（重试连击）**不跨重启继承**：它是单腿内的进程级护栏（5 连击即抛），
        # 继承会让「重启即秒死」（一次失败就撞上历史 5 连击）。只在视图里观测。
        self._consec_fail = 0
        # F4 连击从账本继承（与 ent_peak §339 同理）：连击是**连续**计数，只有下一轮的
        # kl/entropy 再越线才继续，故继承既真又无害；不继承则重启即清零。
        self._kl_streak = self._ledger.kl_streak  # F4: consecutive iters with kl >= KL_BREAK
        self._ent_streak = self._ledger.ent_streak
        # F4 ENT 相对崩塌基线（2026-09-06）：本轮之前见过的最大熵。None = 无历史
        # （冷启动首轮）→ breaker 退回绝对电平判定。续跑时从 training_log.jsonl 回读，
        # 否则每次重启 peak 归零，it9 会被当成"首轮"白送一次连击。
        self._ent_peak: float | None = self._ledger.ent_peak
        self._stop_loss_streak = (
            self._ledger.stop_loss_streak
        )  # P1-9: 止损连击（新 stop_loss 事件）
        # I2（提示类门 REMEDIATE ×N 停腿）：计**整条账本**，不随进程重启洗白
        # （2026-09-18 修）。旧实现在内存里，重启后 4 次确认可以从头再来。
        self._soft_remediate_count = self._ledger.soft_remediate_count(
            TrainingGuards.NO_CLOUD_HALT_KINDS
        )
        if self._soft_remediate_count:
            log(
                f"[run_rl] resume: inherited soft-REMEDIATE count={self._soft_remediate_count} "
                "（I2 停腿判据读账本，重启不洗白）"
            )
        self._tripped = None
        # it 断点续跑：--start-it 显式，否则自动 = 账本最后完成迭代 + 1
        start_it = args.start_it if args.start_it is not None else self._ledger.next_it
        if start_it > 1:
            log(
                f"[run_rl] resume: continuing from iteration {start_it} "
                f"(weights resume from {args.out})"
            )
            # ENT 相对崩塌基线续跑继承（§339）：已在上面从视图赋值，这里只回显。
            if self._ent_peak is not None:
                log(
                    f"[run_rl] resume: inherited entropy peak={self._ent_peak:.3f} (F4 ENT baseline)"
                )
        self._start_it = start_it
        _kickstart_startup_check(args, start_it)
        # 吞吐 T3：eval 稀疏化周期（默认 1 = 每轮，字节一致；>1 = 每 N 轮一次）。
        # 2026-09-03 修正：`or 1` 曾把显式 eval_every=0 吞成 1（想关闭 eval 却变成
        # 每轮都跑——课程 _s5t 测试期实测）。现在 0 表示关闭；默认（cli/rl-config
        # 未给时 = 1）仍每轮，字节一致。
        self._eval_every = int(getattr(args, "eval_every", 1) or 0)
        # 吞吐 T3：eval 绝对迭代点集（复用 run_rl_intent 的 eval_at 语义；空 = 不启用该维）。
        self._eval_at_set = {
            int(x) for x in str(getattr(args, "eval_at", "") or "").split(",") if x.strip()
        }

    def _evalboard_idle(self, it: int, dist_cfg: dict | None) -> None:
        """训练空闲窗（rollout 收官后 / A-eval 收官后）：开窗并认领最早 pending 批。

        与 A-eval 轮解耦（2026-09-11 用户）：采集结束后即可领（含 remote PPO
        等待期）；rollout 开始时 _evalboard_yield 关窗暂停。已有在途单元则只开窗不重复领。
        """
        self._eb_window.set()
        # R4-G1 心跳：开窗（后续单元起止由 batch_eval 续写 batch/unit/rung）。
        try:
            from rl.eval_heartbeat import write_state

            write_state(window_open=True)
        except Exception:
            pass
        t_prev = self._eb_thread
        if t_prev is not None and t_prev.is_alive():
            return
        try:
            from rl.batch_eval import maybe_dispatch_batch

            t = maybe_dispatch_batch(
                self.bun,
                self.args.out,
                self._traj_dir,
                self.args,
                dist_cfg or {},
                RUN_ID,
                it,
                window_event=self._eb_window,
            )
            self._eb_thread = t
            if t is not None:
                log(f"[batcheval] idle window it{it}: claimed unit (thread={t.name})")
        except Exception as e:
            log(f"[batcheval] idle claim failed (ignored): {type(e).__name__}: {e}")
