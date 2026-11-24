"""`OfflineDeliverer` 的**一拍编排**：`_sync` 与它的八段判据。

为什么单独成文件（不是「为拆而拆」）：`remote/offline_deliver.py` 的 LOC 预算是
`< 1000`（同一份理由见 `remote/deliver_wire.py` / `remote/deliver_spec.py` 的模块头），
而 `_sync` 从前是一段 162 行、直排的「起跑闸 → 选谁发 → 探活 → 统计批 / 权重批 →
收尾记账」。按 §5.7 的纪律（先拆再收）：把这一面**整家搬出来**，再在原地按段拆 ——
改一段判据不必再读整条补传链路，腿本体也不再被这一面顶破预算。

分段（2026-11-23，逐句搬自 `offline_deliver._sync`，只把早退改成返回值）：

  · `_pass_gate` —— 起跑闸（停用档记一次「下次何时再试」）；
  · `_plan_pass` —— 这一拍发谁（锚点轮 / 重投 + 统计批缺口）；
  · `_record_probe_fail` —— 探活没过的可观测面；
  · `_send_metrics` —— 统计批先走（小件优先）；
  · `_order_todo` —— 段末最新一轮排最前（D-1）；
  · `_deliver_todo` —— 权重批逐轮投递与销账（G2：失败不 `break`）；
  · `_settle_pass` / `_settle_metrics_only` —— 收尾落盘与失败面。

依赖面 = stdlib + `deliver_mode`(L0，档位机 + 节奏常量) ⇒ **L1**：腿本体
`offline_deliver`(L2) 继承本混入，反向 import 不存在（本模块不认识 `OfflineDeliverer`）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from remote.deliver_mode import (
    DISABLE_COOLDOWN_SEC,
    LOG_THROTTLE_SEC,
    MAX_FAILS_PER_PASS,
    ModeTracker,
)


@dataclass(frozen=True)
class _PassPlan:
    """一拍要发的两批：权重/重投轮次（`todo`）与统计批缺口（`gap`）。

    `_sync` → `_deliver_todo` / `_send_metrics` 的唯一入口：两批在原文里是两个跨了几十行的
    局部列表，回传时按名字给，不靠位置。
    """

    todo: Sequence[int]
    gap: list[int]


class DeliverPassMixin:
    """一拍的分段实现。宿主（`OfflineDeliverer`）提供状态与发送面。"""

    #: 宿主面（`OfflineDeliverer` 拥有；这里只声明，mypy 才能检查本混入）。
    enabled: bool
    disabled_reason: str
    metrics_parts: bool
    sync_cap: int
    probe_fail_ttl: float
    _retry_n: int
    _last_error: str
    _next_try: float
    _repost: set[int]
    _metrics_sent: set[int]
    _metrics_owed: set[int]
    _post_failures: dict[int, int]
    _metrics_unsupported: bool
    _attempt_degraded: bool
    _last_round_had_row: bool
    _cv: threading.Condition
    _modes: ModeTracker
    _now: Callable[[], float]
    #: 两个宿主方法用 `Callable` 声明而不是 `TYPE_CHECKING` 存根：存根会被
    #: `tools/reuse_audit.py` 的同名账本计成「第三份定义」（`probe` / `pending_anchors`
    #: 已各在 2 个模块里定义）。注解不参与那份账本，语义与类型检查效果相同。
    probe: Callable[..., bool]
    pending_anchors: Callable[..., list[int]]

    if TYPE_CHECKING:  # 宿主（本混入不认识的实现面）；运行时不存在，只为类型检查可见。

        def _maybe_revive(self) -> None: ...
        def pending(self) -> list[int]: ...
        def _ledger_its(self) -> set[int]: ...
        def _metrics_gap(self) -> list[int]: ...
        def _save_ledger(self) -> None: ...
        def _post_metrics_batch(
            self, its: list[int], *, budget_sec: float | None = None
        ) -> int: ...
        def _post_artifact_guarded(self, it: int, *, budget_sec: float | None = None) -> str: ...

    def _sync(self, *, force_probe: bool = False, budget_sec: float | None = None) -> int:
        """`force_probe` = 绕过探活的负结果 TTL；`budget_sec` = 本拍硬预算（见 `sync`）。

        为什么段末必须绕过：`probe()` 把「不可达」缓存 `PROBE_TTL_SEC` 秒，而段末收线是
        **最后一次机会**——若最后一次探活在 60s 内失败过，不强制就会让整个段末 flush
        静默什么都不做（最该送出去的那份摘要就此丢掉）。

        排队顺序：真积压本轮的在前、重投（语评估行）的在后——重投那一轮的权重早在 hub 上，
        它只是「把新的读数补上」，不该挡在真正的待投递轮次前面（代价：backlog ≥ `sync_cap` 时
        重投会饿着，而 backlog 总会清空 ⇒ 接受的饥饿，与本次改动前同语义）。

        `_repost` 是**持久集**（2026-10-07）：不再「先取走、失败再放回」——那样会留下一个
        「只唤醒、不消费」的入口，正是 2026-10-06 忙等事故的成因。只有**真正被 `_post_artifact`
        处理过**且返回 `ok`/`skip` 的轮次才销账；被 `sync_cap` 截断在外的、返回 `stop` 的一律留着。
        ⚠ 例外（2026-10-21 降级档）：**降级体**的 `ok` 不销账 —— 那一趟只把权重送到岸，
        评估读数还欠着（销了它就真的丢了）。

        失败**不 `break`**（G2）：返回 `stop`/`deferred` 就续推下一轮，直到 `MAX_FAILS_PER_PASS`——
        每次失败已经付了一个超时窗口，所以「续推」与「每拍有界」必须同时成立。

        只做**编排**（2026-11-23）：起跑闸 → 选谁发 → 探活 → 统计批 / 权重批 → 收尾记账。
        每段判据住自己的助手（`_pass_gate` / `_plan_pass` / `_record_probe_fail` /
        `_send_metrics` / `_deliver_todo` / `_settle_pass`），改一段不必再读整条补传链路。
        """
        if not self._pass_gate():
            return 0
        plan = self._plan_pass(force_probe=force_probe)
        if plan is None:
            return 0
        if not self.probe(force=force_probe):
            self._record_probe_fail()
            return 0
        #: 预算从**这一拍开头**算，统计批花掉的时间不能从下一件里再借一遍（评审 P1-9）。
        deadline = (self._now() + float(budget_sec)) if budget_sec is not None else None
        changed = self._send_metrics(plan.gap, deadline, budget_sec)
        if not plan.todo:
            self._settle_metrics_only(changed)
            return 0
        todo = self._order_todo(plan.todo, force_probe)
        done, changed = self._deliver_todo(todo, deadline, budget_sec, changed)
        return self._settle_pass(done, changed)

    def _pass_gate(self) -> bool:
        """起跑闸：停用档记一次「下次何时再试」后返回 False（未启用则什么都不记）。"""
        if not self.enabled:
            return False
        self._maybe_revive()
        if self.disabled_reason:
            self._retry_n += 1
            self._last_error = self.disabled_reason
            self._next_try = self._now() + DISABLE_COOLDOWN_SEC
            return False
        return True

    def _plan_pass(self, *, force_probe: bool) -> _PassPlan | None:
        """这一拍要发谁：锚点轮 / 重投轮 + 统计批缺口；空 ⇒ None（顺手清掉失败面）。"""
        with self._cv:
            repost = sorted(self._repost)
        # 候选集 = **锚点轮**（有 A 层评估读数的轮次）+ 重投；段末带上最新那一轮（D-1）。
        # 为什么不是 `pending()`（plan §4-W1-C/D）：实时链路只负责「把有读数的轮次送回去」，
        # 其余轮次的权重与账本行走抽屉 + 离线包（两条腿都是整份）。`pending()` 本身没收窄
        # （D-3）——抽屉 / `status()` / 重投判据照吃全量，安全网一格没少。
        # ★ 2026-10-24（评审 P0-1）：档位只剩两档——`every`（每一轮都发；形态在写盘时定，
        #   锚点整份/其余 delta）与 `anchors`（只发有 A 层读数的轮）。`full`/`bulk` 已退场：
        #   它们发的是同一批字节，留着只会让日志谎报「每轮整份」。
        mode = self._modes.tick_mode()
        todo = (
            self.pending_anchors(with_newest=force_probe) if mode == "anchors" else self.pending()
        )
        # 重投只服务**真轮次**（纵深防御：旧账本/旧会话里的起点或半截轮次不该被重投带出去）。
        rounds = self._ledger_its()
        todo += [it for it in repost if it not in todo and it in rounds]
        # ★ 2026-10-24（评审 P0-3）：统计批**不能**被「没有锚点待发」的早退吃掉。
        #   抖动期最常见的形状就是「锚点都发完了、非锚点轮还没发」——那几轮的 row 只能走统计批。
        #   所以先算 gap，只有「权重也没活 + 统计也没活」才早退。
        gap = self._metrics_gap() if (self.metrics_parts and not self._metrics_unsupported) else []
        if mode != "anchors":
            # `every` 档下权重体自带 row：这一拍就要发权重的轮次**不再单独发统计**，
            # 否则同一轮既走统计批（~24 KB）又走权重体（row 内嵌），还会在课程账本里
            # 落两条 `iteration`（一条 provisional、一条真权重）。抖动档相反：锚点轮的
            # 权重可能发不出去，那几轮的统计必须先走（宁可重复一次，也不能等权重）。
            todo_set = set(todo)
            gap = [it for it in gap if it not in todo_set]
        if not todo and not gap:
            self._retry_n = 0
            self._last_error = ""
            self._next_try = 0.0
            return None
        return _PassPlan(todo=todo, gap=gap)

    def _record_probe_fail(self) -> None:
        """探活没过：记下「下拍最早何时再试」，重投留在 `_repost` 里等下次会话。"""
        # 读数只**给人看**（plan §4.2-1：不做退避判决）—— 下一拍的可试时刻就是探活负缓存的长度
        # （连不上时 `PROBE_FAIL_TTL_SEC` 那个短窗，抖动恢复不用等一整轮）。
        self._retry_n += 1
        self._last_error = self.disabled_reason or "hub 不可达"
        self._next_try = self._now() + (
            LOG_THROTTLE_SEC if self.disabled_reason else float(self.probe_fail_ttl)
        )

    def _send_metrics(
        self, gap: list[int], deadline: float | None, budget_sec: float | None
    ) -> bool:
        """统计批先走（小件优先）；返回「账本要不要落盘」。失败不销账 ⇒ 下一拍照旧再试。"""
        # ★ S5（2026-10-23）：权重发不动时读数也要到（D11）。
        if not gap or (deadline is not None and self._now() >= deadline):
            return False
        remaining = max(0.0, deadline - self._now()) if deadline is not None else budget_sec
        return bool(self._post_metrics_batch(gap, budget_sec=remaining))

    def _settle_metrics_only(self, changed: bool) -> None:
        """这一拍只有统计可发：送达/失败都要把账落盘；没送达就留一句可观测的失败原因。"""
        if changed:
            self._save_ledger()
            self._retry_n = 0
            self._last_error = ""
            self._next_try = 0.0
        else:
            self._retry_n += 1
            self._last_error = "统计批没有送达（下拍再试）"
            self._next_try = self._now() + LOG_THROTTLE_SEC

    def _order_todo(self, todo: Sequence[int], force_probe: bool) -> Sequence[int]:
        """段末（`force_probe`）：最新那一轮排最前 —— D-1「末轮强制补发」的落地形态。

        为什么不是「全部降序」：升序才是续跑进度的自然顺序，只有最后那一轮值是**整段**的终点。
        """
        if force_probe and len(todo) > 1:
            newest = max(todo)
            return [newest] + [it for it in todo if it != newest]
        return todo

    def _deliver_todo(
        self,
        todo: Sequence[int],
        deadline: float | None,
        budget_sec: float | None,
        changed: bool,
    ) -> tuple[int, bool]:
        """权重批：逐轮 `_post_artifact_guarded` 并销账；返回 `(done, 账本要不要落盘)`。"""
        done = 0
        fails = 0
        for it in todo[: self.sync_cap]:
            if deadline is not None and self._now() >= deadline:
                break  # 预算用尽：**不**再起一件（起跑就可能越界，段末会被 terminate）
            outcome = self._post_artifact_guarded(it, budget_sec=budget_sec)
            if outcome in ("stop", "deferred"):
                # ★ 2026-10-21（评审 P0-4 / G2）：**不再 break**。
                # `stop` 只说明「这一件这次没过去」，不该让后面的轮次一次都不被尝试——
                # 积压 14 轮时 `break` 等于永远只赌队列第 1 轮（现场 it69…it81 从未被 POST 过）。
                # 但也不能无限接着打：每次失败已经付了一个超时窗口，所以有上限。
                if outcome == "deferred":
                    changed = True  # 降级档：`owed_reposts` 多了这一轮，必须落盘
                fails += 1
                if fails >= MAX_FAILS_PER_PASS:
                    break
                continue
            if outcome in ("ok", "skip"):
                with self._cv:
                    # 降级体送达**不销**重投：那一趟只把权重送到岸，评估读数还欠着。
                    # （非降级趟 / 内容拒收照旧销账——否则 `_repost` 会变成一个永不熄灭的热循环。）
                    if it in self._repost and not (outcome == "ok" and self._attempt_degraded):
                        self._repost.discard(it)
                        changed = True
                self._post_failures.pop(it, None)
            if outcome == "ok":
                done += 1
                # `row` **真的**随这一趟到达才销统计账（评审 P1-7）：权重体可能因为账本行与
                # 权重指纹不符而没有 `row`（`build_round_body` 宁可不带），那一轮必须留给
                # 统计批再试——否则「权重到了、统计永久丢」且账本显示已送。
                if self._last_round_had_row:
                    if it not in self._metrics_sent:
                        self._metrics_sent.add(it)
                        changed = True
                    if it in self._metrics_owed:
                        self._metrics_owed.discard(it)
                        changed = True
                elif it not in self._metrics_owed:
                    # 这一轮已进 `_delivered`，`pending()` 不会再列它 ⇒ 统计欠账必须单独记，
                    # 否则下一拍 gap 看不见它，权重成功反而把统计静默丢定。
                    self._metrics_owed.add(it)
                    changed = True
            # "skip" = 这一轮被内容拒收（已记进 `_rejected`）：跳过它，后面的照推。
            # 拒收**本身也要落盘**（D6）：TTL/上限的年龄面就住在那条记录里，而「本拍只有拒收、
            # 没有成功投递」的组合只会让 `done=0` ⇒ 不置脏就会连旧记录一起停在上一次的水位。
            elif outcome == "skip":
                changed = True
        return done, changed

    def _settle_pass(self, done: int, changed: bool) -> int:
        """收尾：落盘 + 失败面（成功投递**不打日志** —— 每轮一句纯刷屏；失败那条才响）。"""
        if done or changed:
            self._save_ledger()
        if done:
            self._retry_n = 0
            self._last_error = ""
            self._next_try = 0.0
        elif not changed:
            # 这一拍一轮都没送出去（且没有别的脏东西要落盘）⇒ 记进可观测面：连续失败 + 原因。
            self._retry_n += 1
            self._last_error = "这一拍没有轮次送达（传输失败/被拦；下拍再试）"
            self._next_try = self._now() + LOG_THROTTLE_SEC
        return done
