"""loop_round —— 轮内上下文（`RoundContext`）与 13 步表（R2c-3，plan/r2-loop-task-queue §3/§4）。

R2c-2 让「**一轮**」成为可调度的单元（`TrainingLoop.run_one_round`）；本模块把一轮再切成
**13 步**，让位点因此从「每轮一次」变成「每步一次」：一门课在等远端 PPO 回传时可以先把
执行权交给别的课，而它自己那一步**不丢**（账本 + 幂等判据在 `rl.loop_tasks` 里）。

**为什么先提 `RoundContext`**：轮体里锁着几个轮内局部量（`pairs` / `dist_cfg` / `t_rollout`
/ `seg`）与一个**会被改写的指针**（`it`：半离线整段曾一次吃掉 it..end_it —— **那条腿
2026-09-25 已退役**，见 `plan/online-offline-role-routing.plan.md` §7；指针仍是
`RoundOutcome.it` 契约的一部分：重试/让位靠它原地不跳轮）。
步骤化之后它们必须跨步可见 ⇒ 提成一个显式对象；**不放进引擎的实例属性**（§2.2 无隐藏状态）：
轮内状态是「这一轮的」，长在引擎上会被同进程的其它课程互相覆盖。

**本模块只放纯数据 + 表**（无 torch / 无网络 / 无文件 IO），可被单测直接构造：

```
ROUND_TASKS（rl.loop_tasks，唯一顺序来源）
        │
        ├── STEP_ORDER   = ROUND_TASKS（同序）
        └── STEP_METHOD  : kind → 引擎方法名（本模块是唯一翻译处）
                          └── 组合路径（run_one_round）与细粒度驱动器都从表取步骤
                              ⇒ 两条驱动不可能漂移（加一步必须同时进表）
```

`RoundOutcome` / `ROUND_*` 也在这里定义（R2c-2 时在 `loop_core`）：步骤 mixin 需要它们，
而 mixin 被 `loop_core` import —— 常量若留在 `loop_core` 就成环。`loop_core` 原样再导出，
既有 `from rl.loop_core import ROUND_NEXT` 的调用点不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rl.loop_tasks import ROUND_TASKS

# ---- 一轮的终态（R2c-2 引入；R2c-3 迁来本模块以断开 loop_core ↔ 步骤 mixin 的环） ----

#: 本轮正常收官，继续下一轮。
ROUND_NEXT = "next"
#: 硬边界停车（门 / 熔断 / 止损 / 预算 / 停腿）。
ROUND_STOP = "stop"
#: 本轮作废，it 原地重试。
ROUND_RETRY = "retry"
#: **本轮未完，让位等外部**（R3-4：BC 轮粒度路径）。语义与 `ROUND_RETRY` 的区别很重要：
#: retry = 「我试过了，失败了，重做」；wait = 「我已经发布/采集完了，**正在等外部事实**
#: （GPU job 回传 / 语料落盘）」——进程没出错，只是这一步的结果还没到。
#: 调度器据此把执行权交给别的课程（不占票、不计失败连击），过一会儿再来问同一轮。
ROUND_WAIT = "wait"
#: `--smoke` 冒烟回显作废，干净退出。
ROUND_SMOKE_STOP = "smoke_stop"
#: 全离线任务包已写出，整条腿结束。
ROUND_BUNDLE_EXIT = "bundle_exit"
#: **离线课不由本机跑**（`rollout_src=run`）：本机侧干净收官，执行者是云机（取任务包接手）。
#: 2026-09-25 起“半离线整段”（发一份 `kind=run` 队列项、本机等 8h）那条腿已退役
#: —— 见 `plan/online-offline-role-routing.plan.md` §7。它不是失败：不落 `iter_error`、不计连击。
ROUND_OFFLINE_EXIT = "offline_exit"


@dataclass(frozen=True)
class RoundOutcome:
    """`run_one_round` 的返回：终态 + 本轮结束时的迭代号。

    `it` 必须带回驱动循环：`ROUND_RETRY` / `ROUND_WAIT` 靠它原地重试（丢了返回值 = 跳轮）。
    半离线整段曾一次吃掉 it..end_it（该腿已退役，`ROUND_OFFLINE_EXIT` 不推进指针）。

    `detail`：`ROUND_WAIT` 的**人读原因**（在等什么、等谁）——它会直接上屏到控制台调度器
    卡片的「在等什么」列，所以必须是事实句（带 jid/轮号），而不是「等待中」。其它终态
    留空（它们各自有既有的读面字段）。
    """

    status: str
    it: int
    detail: str = ""


# ---- 采集模式（轮内 fact：决定 rollout / 补波走哪条支路） -------------------

#: 本机采样（默认）：配额课程走 `_volume_collect_continuous`（VOLUME_RULE_V2，采完即含补波），
#: 其余走 `_rollout_phase`。
COLLECT_LOCAL = "local"
#: 整轮上云（kind=iter）：节点自己跑 rollout + PPO，本机**完全不采样、不补波**。
COLLECT_NODE = "node"
#: **离线课**（kind=run / `rollout_src=run`）：本机**不跑这门课**——不采样、不派发、不等待；
#: 执行者是云机（取任务包接手）。2026-09-25 替换掉 `COLLECT_SEGMENT`（半离线整段）。
COLLECT_OFFLINE = "offline"


def resolve_collect_mode(source: str, seg: int) -> str:
    """采集模式的**唯一**裁决点：离线课 > 整轮上云 > 本机采样。

    为什么单拎出来：这三条支路决定「本机到底采不采样」，而派发点（`step_rollout`）与
    裁决点（`step_course_iter`）在两个文件里。2026-09-17 的半离线整段写着 `ctx.seg` 却
    没人翻 `collect_mode`，于是 kind=run 分支**永远不可达**（且表面看一切正常：本机照常
    采样、账本照常记账，只是云机永远领不到整段）。把裁决收在一个纯函数里，它就能被单测
    钉住，而不是靠人把两处对齐。

    ★ 2026-09-25（退役「半离线整段」腿）：`run` / 段长**不再**对应「本机替它派发并等」的任何
    模式，而是 `COLLECT_OFFLINE` = 这门课不归本机（云机取任务包接手）。**绝不**回落到
    `COLLECT_LOCAL` —— 那正是「本机偷偷自己采样、与云机双跑」的那个坑（§7.2）。

    `source` 取 `rl/loop_steps.py::_rollout_source` 的返回值（auto 已解析过）；`seg` 取
    `_run_segment_iters`（`>0` = N 轮，`<0` = 到课程末尾）——退役后它只剩两个用途：
    ① 与 `run` 一起声明「这门课由云机接手」（历史配置里可能只写了 `run_iters`）；
    ② `--export-bundle` 的「跑到哪停」。
    """
    if source == "run" or seg:
        return COLLECT_OFFLINE
    if source == "node":
        return COLLECT_NODE
    return COLLECT_LOCAL


@dataclass
class RemotePpoJob:
    """一次远端 PPO 提交的全部上下文——「发布 / 等结果 / 落位」三相之间**唯一**的载体。

    为什么需要它：拆分前三相是一个 400 行顺序函数，中间那些量（`jid` / `manifest` / 超时预算 /
    打包墙钟 / kickstart 系数 / 运输方式）全是局部变量。拆成三相后它们必须跨步可见，而
    **不能**长在引擎实例上（§2.2 无隐藏状态；单进程多课程会互相覆盖）⇒ 由 `RoundContext.remote`
    持有，生命周期 = 一轮（落位后置空）。

    刻意**不**持有 payload/权重字节：那是几十 MB 级，而发布之后真正有用的只是
    「哪个节点、哪个 job」；需要重发时从 job 目录重读盘（`find_payload`）。于是本对象保持小、
    纯数据、可打印，调度器/控制台读它也不会触发任何 IO。
    """

    it: int
    jid: str
    manifest: dict[str, Any]
    #: 运输方式：`"hub"`（pull 或 hub 中介推送 ⇒ 探 hub）· `"push"`（直推节点 ⇒ 探节点）。
    transport: str = "hub"
    #: 直推时的候选节点表与「当前把活交给谁」（失败换下一个，见 `_remote_ppo_fetch`）。
    nodes: list[dict[str, Any]] = field(default_factory=list)
    node_i: int = 0
    hub_url: str = ""
    hub_token: str = ""
    job_root: str = ""
    #: 等结果预算（发布时算定：同一份 job 的超时不该因为「谁先问了一句」而变）。
    timeout_sec: float = 0.0
    #: 发布起点的墙钟（`_ppo_sec` 往返口径的分母）与打包墙钟（wire 读数）。
    t_ppo: float = 0.0
    pack_sec: float = 0.0
    kick_on: bool = False
    kick_kl: float = 0.0
    #: 整轮上云规格（非空 = 节点侧采集）。
    #: （半离线整段那条腿 2026-09-25 退役 ⇒ 本对象不再有 `segment` 字段。）
    rollout_spec: dict[str, Any] | None = None
    #: hub 中介推送（读数口径用：它也算「推」）。
    hub_push: bool = False
    #: 直推时最后一次 submit 的传输读数（随结果上浮给 `_wire_from_result`）。
    submit_wire: dict[str, Any] | None = None

    @property
    def node(self) -> dict[str, Any] | None:
        if self.transport == "push" and self.nodes:
            return self.nodes[self.node_i]
        return None

    @property
    def probe_base_url(self) -> str:
        """探针打谁：直推 = 已提交的那个节点；hub（pull / hubpush）= hub。"""
        node = self.node
        return str(node["url"]) if node is not None else self.hub_url

    @property
    def probe_token(self) -> str:
        node = self.node
        return str(node.get("authKey", "")) if node is not None else self.hub_token

    def probe_path(self) -> str:
        """结果端点路径（节点侧少一个 s）——两条链路各自的真实形状。"""
        return "/job/{jid}/result" if self.transport == "push" else "/jobs/{jid}/result"


@dataclass
class RoundContext:
    """一轮的轮内状态：**跨步可见**的那几个量 + 已走过的步骤痕迹。

    生命周期 = 一轮。`LoopRunner`（细粒度驱动）每课持一份，`it` 变了就换新的；
    组合路径（`run_one_round`）每次现造一份。**它不是第二份真相**：轮内指针的权威
    仍是账本（`rl.train_ledger`），本对象只承载「这一步算出来的东西给下一步用」。

    `done` 是**本进程内**的进度痕迹（诊断/加速），不是幂等判据——判据一律来自盘上事实
    （`rl.loop_tasks.already_done` / `RoundFacts`），重启后由盘重建。
    """

    it: int
    #: 本轮 (stage, seed) 批次（`_iteration_pairs` 的产物；(rotateSeed, it) 的纯函数）。
    pairs: list[Any] = field(default_factory=list)
    #: 本轮的 dist 配置（每轮热读一次；`nodes=[]` ⇒ 纯本地路径）。
    dist_cfg: dict[str, Any] | None = None
    #: rollout 起点墙钟（`_log_report` 的耗时分母）。
    t_rollout: float = 0.0
    #: 段长（`--run-iters`；0 = 未声明）。退役后本机只用它做两件事：
    #: 判「这门课归云机」（与 `run` 同义）与 `--export-bundle` 的终点。
    seg: int = 0
    #: 采集模式（见 `COLLECT_*`）。
    collect_mode: str = COLLECT_LOCAL
    #: 本轮是否派发干净评估（`_eval_on_round(it)` 的**同一次**求值结果）。
    eval_on_round: bool = False
    #: 本轮干净评估的回执（`_join_eval` 的产物；止损判定要用）。
    eval_rec: dict[str, Any] | None = None
    #: 本轮是否在节点采集（与 `self._node_rollout` 同值的快照——后者是引擎属性，
    #: 供其它方法读；这里是**本轮事实**的记录，便于读面/测试断言）。
    node_rollout: bool = False
    #: 已提交但尚未落位的远端 PPO 会话（发布 → 等结果 → 落位 的载体；落位后置空）。
    remote: RemotePpoJob | None = None
    #: **本轮是否有让位点**：细粒度驱动器（`Supervisor`）置 True；组合路径
    #: （`run_one_round`）恒 False——那里没人接手，"等"只能阻塞着等。步骤据此决定
    #: 「未就绪时让位」还是「就地阻塞取结果」（见 `step_ppo`）。
    resumable: bool = False
    #: 本轮已走过的步骤（kind 列表，按顺序；仅诊断/加速，不是幂等判据）。
    done: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- 小工具

    def mark(self, kind: str) -> None:
        """记下「这一步走完了」（重复标记只保留一次，顺序即执行顺序）。"""
        if kind not in self.done:
            self.done.append(kind)

    def is_done(self, kind: str) -> bool:
        return kind in self.done

    def next_kind(self) -> str:
        """按表序返回本进程还没走过的第一步（全走完 ⇒ 空串）。诊断/读面用。"""
        for kind in STEP_ORDER:
            if kind not in self.done:
                return kind
        return ""


# ---- 一步的结局 -------------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    """一步的结局：`outcome` 非空 = 本轮终态；否则继续（`reason` 非空 = 让位等外部）。

    谁产生「让位」：**目前只有细粒度驱动器**（`LoopRunner`）——它认识世界（远端回传到没到、
    eval 尾巴结没结），引擎只管干活。`reason` 是给这条通路预留的字段：等 `wait_job` /
    eval 尾巴 / 预采子进程真的轮询化（R2c-3 余下部分）时，步骤自己就能说「我在等什么」。
    """

    outcome: str | None = None
    reason: str = ""

    @property
    def is_final(self) -> bool:
        """本轮的终态（该停就停）。"""
        return self.outcome is not None

    @property
    def is_wait(self) -> bool:
        """要让位等外部（组合路径不支持——见 `loop_core.run_one_round` 的响亮报错）。"""
        return self.outcome is None and bool(self.reason)


def advance() -> StepResult:
    """继续下一步。"""
    return StepResult()


def finish(outcome: str, reason: str = "") -> StepResult:
    """本轮到此为止（`outcome` 取 `ROUND_*`）。"""
    return StepResult(outcome=outcome, reason=reason)


def wait_for(reason: str) -> StepResult:
    """让位：等外部事实就绪（细粒度驱动器专用）。"""
    return StepResult(reason=reason)


class RoundYieldError(RuntimeError):
    """组合路径（`run_one_round`）里出现了「让位」——该路径**没有让位点**。

    是**设计走岔**（有人给步骤加了引擎侧等待，却只更新了细粒度驱动器），不是「一次失败」：
    必须响亮、不落 `iter_error`、不计失败连击——否则它会伪装成普通的引擎异常被吞进重试
    阶梯，症状变成「每轮都重试同一轮」（2026-09-18 写测试时实测到：不加专门类型时这个
    守卫被 `except Exception` 吃掉了）。`run_one_round` 里必须先于通用失败分支捕获再上抛。
    """


# ---- 13 步表 ----------------------------------------------------------------

#: 一轮的步骤顺序 = `rl.loop_tasks.ROUND_TASKS`（**单一来源**，不在两处各写一遍）。
STEP_ORDER: tuple[str, ...] = ROUND_TASKS

#: kind → 引擎方法名。组合路径与细粒度驱动器**都**从这张表取步骤 ⇒ 不可能漂移；
#: `tests/test_loop_round.py` 断言它与 `STEP_ORDER` 一一对应（加一步必须同时加实现）。
STEP_METHOD: dict[str, str] = {
    "precollect_join": "step_precollect_join",
    "prepare_iter": "step_prepare_iter",
    "hot_reload": "step_hot_reload",
    "course_iter": "step_course_iter",
    "rollout": "step_rollout",
    "volume_topup": "step_volume_topup",
    "eval_dispatch": "step_eval_dispatch",
    "ppo": "step_ppo",
    "export_weights": "step_export_weights",
    "eval_join": "step_eval_join",
    "record_iteration": "step_record_iteration",
    "gate": "step_gate",
    "cleanup": "step_cleanup",
}
