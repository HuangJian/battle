"""common/errors —— nn-training 的**失败类型族**（S5 第六刀，2026-09-27）。

从 `common/protocol.py` 整块搬出（**逐字节不动**）。这里是「一次作业失败该怎么**分类**」的
唯一实现——每条继承线都对应一个**处置分支**（`worker_loop` / 各腿的 `except`），所以它们是契约，
不是随手起的异常名：

* `ProtocolError(ValueError)` —— 协议违规（缺失必填 / 类型错 / 哈希不匹配）⇒ 拒收；
* `RetryableError(Exception)` —— 瞬时失败（网络抖动 / 5xx / 传输损坏）⇒ 还租约、立即重领；
* `UnreapableChildError(RetryableError)` —— 子进程 SIGKILL 后仍收不回来（**机器**的病）；
* `JobCancelledError(RuntimeError)` —— 本 job 已被别人赢下 ⇒ 丢弃、不回传、不报失败；
* `JobFailedError(RuntimeError)` —— **确定性**节点失败（原因已回传）⇒ 训练侧停腿；
* `CodeChangedError(RuntimeError)` —— 已 import 的代码与 job 的 `code_sha256` 不一致 ⇒ 重启进程。

**为什么单独成家**：它是**唯一的**「没有上层依赖、又人人要 import」的那一小块——`protocol`
的每一个校验函数都抛 `ProtocolError`，但 `common.wire_codec`（线格式）也要抛它；若后者反向
import `common.protocol`，而 `protocol` 又为门面 import `wire_codec` ⇒ 模块级成环。把失败类型
抽成叶子，两边都能向下依赖。

依赖面 = **pure stdlib**（本模块**零** import）。`common/protocol.py` 保留 `X as X` 门面 ⇒
历史 `from common.protocol import ProtocolError` 一行不改。
"""

from __future__ import annotations


class ProtocolError(ValueError):
    """协议违规（缺失必填 / 类型错 / 哈希不匹配）。调用方（hub/worker）决定拒收方式。"""


class RetryableError(Exception):
    """瞬时失败（网络抖动 / 5xx / 传输损坏）——租约窗口内重试即可修复，非确定性拒绝。

    与 ProtocolError 的分界（2026-09-05，DECISIONS §340 补充 3）：4xx/字段级校验
    失败 = 确定性拒绝（重试无意义）；网络层异常与 5xx = 可重试。worker_loop 捕获
    RetryableError 后主动 release 租约回池，立即可重领（不再干等 30min 过期）。"""


class UnreapableChildError(RetryableError):
    """子进程 SIGKILL 之后仍然回收不了（D 状态 / 挂住的挂载点）——**机器**的病，不是内容的错。

    事实基础：`kill()` 只是把信号递进去；子进程若卡在**不可中断**的 IO 里，要等那个系统调用
    返回才真的死。`platform_utils` 的处置是**有界**回收（`reap_bounded`，预算 = `KILL_REAP_SEC`），
    收不回来就把这个子进程记进账（`keep_unreaped`）并抛本异常 —— 绝不能在那里等下去
    （2026-09-25 云机「卡死机器半天」的现场就是一条线程永远停在 `waitpid` 上：92 条线程里一条
    不返回，整轮就再也收不齐，而日志里什么都看不出来）。

    为什么它**必须**与普通超时分开（两腿都按这个分类分岔，2026-09-25 用户口径）：

      * 普通超时（`TimeoutExpired`）= 这一局慢（内容/负载）：它有自己的出路 —— 原地重跑同一
        argv，几次之后仍失败就是**这一轮的确定性失败**（响亮记一笔，读数少一局）；
      * 收不了尸 = 机器卡住：旧写者**可能还活着** ⇒ 在同一个输出目录上重跑就是两个写者写同一
        份产出（半截/交错）⇒ 静默错数据。所以腿侧的处置只能是**轮内重投**：先把它半截的产出
        删干净，再与其它没产出的局一起投。判成本轮失败则是把机器的问题记在内容头上
        （worker 侧 `report_job_failure` ⇒ hub 落终局 ⇒ 停腿 ⇒ 反过来把云机停掉）。

    继承 `RetryableError` 是因为它在语义上就是「可重试、非确定性拒绝」；**重试的粒度由各腿自己
    定**（rollout 腿 `remote/iter_rollout.run_iter_rollout`、eval 腿
    `remote/offline_eval.run_cloud_eval` 都是在轮内重投，只补没产出的局；缺省不限，各自留一个
    操作员上限 env）。worker_loop 里还有一条同名的兜底分支（还租约 + 立即重领，不报失败、
    不冷却）。
    """


class JobCancelledError(RuntimeError):
    """本 job 已被**别人赢下**（结果已落盘）⇒ 停算丢弃，**不**回传、**不**报失败。

    为什么它必须是**独立**异常（2026-09-22，plan/transfer-scheduling §2.4）：取消是一个
    **正常**结局（备份副本被首写锁定判负），而 `worker_loop` 的两个既有分支都会把它读错——
    `except ProtocolError` ⇒ `report_job_failure`（把合法放弃报成确定性失败 ⇒ 训练停腿）、
    `except RetryableError` ⇒ `release` 租约（把别人已经赢下的活重新放回池子）。

    唯一正确的处置：丢本地副本 + `POST /jobs/{id}/abandon`（幂等，含 release 租约）+ 走
    priority 选下家。抛点 = `ppo_update` 的 **epoch 边界**（`on_epoch_done`），
    实测延迟记 `cancel_latency_s`。
    """


class JobFailedError(RuntimeError):
    """**确定性**节点失败，且失败原因已随 `POST /jobs/{id}/fail` 回传到控制面。

    与 RetryableError/ProtocolError 的分界（2026-09-17，DECISIONS
    §2026-09-17-job-fail-report）：节点**已经判定这个 job 在这台机器上跑不成**（bun
    装不上 / TS 运行时取不到 / argv 非法），并把原因报给了 hub/节点服务。

    在此之前这条信息只落在**云机日志**里：pull 侧 worker 走 `except ProtocolError`
    静默 skip（不回传、不还租约），训练侧只能等 `wait_job` 25 分钟超时（看到的是
    "超时"，不是"bun 缺失"）；push 侧节点服务用 500 报失败，而 500 在
    `push_client.wait_result` 里被当**瞬时错误**重试到预算耗尽。两者都把
    「确定性能力缺失」伪装成了「网络/排队问题」。

    reason/kind/detail 由回报方填写（`kind` = 异常类名，`detail` = 截断后的原文）。
    """

    def __init__(self, message: str, *, kind: str = "", detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(message)


class CodeChangedError(RuntimeError):
    """本进程已 import 的代码与 job 携带的 code_sha256 不一致（热替换事件）。

    成因（2026-09-11 review）：worker 常驻进程在首 job 才 import 代码进 sys.modules；
    本地改代码后 hub 重打 code.zip（sha 变），后续 job 解压新代码、sys.path.insert(0,
    新目录)，但 import 只查 sys.modules → 跑的还是旧代码**且零报错**。

    ⚠ 刻意不继承 ProtocolError：worker_loop 对 ProtocolError 是 "skip (not retried)"
    ——会把该 job 永久跳过，hub 侧干等到 1800s 超时、触发 R9 连败降级/停腿。本异常
    必须走"重启进程"这条独立分支。
    """

    def __init__(self, loaded_sha: str, job_sha: str) -> None:
        self.loaded_sha = loaded_sha
        self.job_sha = job_sha
        super().__init__(
            f"代码已变更：本进程加载 {loaded_sha[:12]}… != job 要求 {job_sha[:12]}…"
            "（继续跑会用旧代码产出看似正常的结果）"
        )
