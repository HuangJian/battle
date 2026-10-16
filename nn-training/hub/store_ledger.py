"""hub/store_ledger.py — 磁盘事实来源：jsonl 账本 + 作业目录 + 发布 / 可领取池。

`_JobStore` 的六个域混入之一（S4 第十四刀）。本簇回答的是「**盘上有什么**」：

* `_job_dir` —— 唯一的目录解析（别处不再 `job_root / job_id` 拼第二遍）；
* `_read_ledger` / `_append_ledger` —— jsonl 双态账本（H6 增量读：记住上次 size，只解析
  新增行 ⇒ 长跑轮询不随账本线性变慢）；
* `claimable_job_ids` / `inflight_job_ids` —— 「未完成」的两侧（`_unfinished_pending` 是唯一
  筛子）：可领取池 = ∧ **无**活租约；在飞集 = ∧ **有**活租约（2026-10-06：后者是空闲 worker
  领备份副本的候选源，见 `inflight_job_ids`）；
* `publish` —— 发布（写磁盘 + 记账）；
* `job_failure` / `get_result` —— 两个**读回**面（失败标记 / 已落盘结果）。

## 依赖方向

`store_ledger → {common.protocol}`（向下）。**不 import 任何兄弟混入**——跨域调用
（本簇的 `_job_dir` 被租约/调度/结果/离线四簇调用）一律经 `self`，这是「一个对象、
一把锁」能成立的前提（见 `hub_server._JobStore` 头部那段）。

## 状态（`_init_ledger`）

`_ledger_cache`（H6 增量读缓存）。`job_root` / `jsonl_path` 是**构造身份**，由组合类的
`__init__` 直接声明（`_HubQueue` 与测试都直接读它们）。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import FAIL_NAME, PAYLOAD_NAME, find_payload

#: 账本增量读的分块（字节）。只影响 IO 粒度，不影响解析结果（见 `_read_ledger_from`）。
LEDGER_READ_CHUNK = 64 * 1024

#: 账本里**参与折叠**的三种事件（其余行读了就丢——`_unfinished_pending` 等的唯一筛子）。
LEDGER_EVENTS = ("job_pending", "job_completed", "job_cancelled")


def _ledger_event(raw: bytes) -> dict | None:
    """一行账本 → 事件 dict（不是这三种事件 / 空行 / 坏 JSON ⇒ None）。"""
    line = raw.strip()
    if not line:
        return None
    try:
        e = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(e, dict) and e.get("event") in LEDGER_EVENTS:
        return e
    return None


class LedgerMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 构造身份：组合类 `__init__` 直接声明（`_HubQueue` 与测试都直接读它们）
    job_root: Path
    jsonl_path: Path
    #: 兄弟簇 `store_leases` 拥有的状态（本簇的 `claimable_job_ids` 要用它判「别处在做」）
    _leases: dict[str, float]
    #: 租约**持有人身份**（同住 `store_leases`）：`inflight_job_ids(not_held_by=…)` 要按它
    #: 排掉「请求者自己正在跑的那份」（只读，**不**走 `lease_worker()`——那个会顺手回收死租约，
    #: 而 peek 面按 R1-4 是无副作用的）。
    _lease_workers: dict[str, str]
    _frozen: dict[str, dict]
    #: 兄弟簇 `store_scheduling` 的「有人承诺在跑」痕迹：撤单（`cancel_unsettled_jobs`）
    #: 要跳过它们（在飞的不撤），并顺手撕掉以让拿旧 epoch 来的认领被 `demoted` 拒。
    _claimed: dict[str, dict]
    _computing: dict[str, dict]
    _drop_commitment_locked: Any
    #: 兄弟簇 `store_leases` 的**唯一**死活判据的布尔视图（过期 ∨ 孤儿，§52）——池过滤与
    #: 认领闸必须共用同一把尺子：只在过滤里加判据会做出「池里看得见、claim 说 held」
    #: 那种更难查的形状。取布尔而不是状态字符串，是因为本簇拿不到那边的常量
    #: （混入之间不许 import）。
    _lease_held: Any
    #: 归属缓存与它自己的锁（同住 `store_leases`）：`publish` 要随 manifest 一起失效它。
    #: 用独立锁的理由见那里（`job_role` 会被持 `_lock` 的临界区调到，共锁会自锁）。
    _roles: dict[str, str]
    #: job → kind 缓存（★M1b：`publish` 要和 `_roles` 一起失效它 —— Q5 的 bc 豁免读它）。
    _kinds: dict[str, str]
    _role_lock: Lock
    #: ★M5：BC 进度锚（同住 `store_leases`）——重发 = 新一轮，`publish` 要把它一并作废
    #: （旧一轮的 epoch 时间戳会把新租约当场判成「在推进」）。
    _last_progress: dict[str, float]

    def _init_ledger(self) -> None:
        #: 账本增量读缓存（H6）：文件 size -> 已解析事件列表
        self._ledger_cache: tuple[int, list[dict]] = (0, [])
        #: ★ H6 的**另一半**（2026-10-17，plan/hub-server-memory-slim L3）：已解析到的**字节位**。
        #: 只有 size 才知道"变没变"，而只有 offset 才知道"从哪读"——旧实现缺这一半，于是
        #: size 一变就**全文重解析**（一轮一行 ⇒ 长跑时每拍 O(账本)，hub 35h 累计 74713s CPU
        #: 有一半是这个）。末尾的半截行不算进 offset（写者可能正在追加）。
        self._ledger_off: int = 0
        #: 本进程内已作废的 job（`cancel_unsettled_jobs` 填）。**只是认领闸的即时缓存，
        #: 不是事实源**——事实源是账本里的 `job_cancelled`（`claimable_job_ids` 从它重算）。
        #: 有它才挡得住「worker 拿着作废前 peek 到的 jid 来 claim」这个秒级窗口。
        #: ★ 2026-10-05（六轮 F1）：它**不是「一旦进入就永久否决」**——生产 republish 走磁盘
        #: IPC（hub 收不到复活信号），所以 `_claim_locked` 命中时按账本**净态**对账
        #: （`_ledger_net_state` 为 pending ⇒ discard 放行）；`publish` 的复活分支同样 discard。
        self._cancelled: set[str] = set()

    def _job_dir(self, job_id: str) -> Path:
        return self.job_root / job_id

    # ---- jsonl 账本（job_pending / job_completed 双态，§3.1/D8） ----
    def _read_ledger(self) -> list[dict]:
        if not self.jsonl_path.exists():
            self._ledger_cache = (0, [])
            self._ledger_off = 0
            return []
        size = self.jsonl_path.stat().st_size
        cached_size, cached = self._ledger_cache
        if cached_size == size and self._ledger_off == size:
            return list(cached)  # 未变化：零 IO 复用
        # ★ 真增量：能续着上次的字节位读就只**读新增**（判据 = 文件只变长且上次解析到了头）；
        # 否则（文件被截短/重写/首次）退回全量。
        out: list[dict] = []
        off = 0
        if cached_size == size or (size > cached_size and 0 < self._ledger_off <= size):
            out = list(cached)
            off = self._ledger_off
        try:
            off = self._read_ledger_from(out, off)
        except OSError:
            return list(cached)
        # 半截行（写者正在追加）不计入 offset ⇒ 下次从它前面接着读，不重解也不漏解。
        self._ledger_off = off
        self._ledger_cache = (size, out)
        return list(out)

    def _read_ledger_from(self, out: list[dict], off: int) -> int:
        """从 `off` 起解析到 EOF，追加进 `out`；返回**新的**已解析字节位。

        二进制 + 手动分行的三个理由：① 只有二进制才能 `seek(off)` 做增量（文本模式的
        `tell()` 在 UTF-8 多字节边界上不可靠，也不该拿来当"读了多少字节"用）；
        ② 按块读 ⇒ 峰值 = 一块（旧实现逐行迭代，文本层缓冲把整个文件拉进来的形状类似）；
        ③ 半截行必须留到下回，不能"读进来就解析"——写者正在追加时那行只有一半。
        """
        tail = b""
        pos = off
        with open(self.jsonl_path, "rb") as f:
            if off:
                f.seek(off)
            while True:
                chunk = f.read(LEDGER_READ_CHUNK)
                if not chunk:
                    break
                pos += len(chunk)
                parts = (tail + chunk).split(b"\n")
                tail = parts.pop()  # 最后一段未以 \n 结尾 ⇒ 半截行，留给下次
                for raw in parts:
                    e = _ledger_event(raw)
                    if e is not None:
                        out.append(e)
        return pos - len(tail)

    def _append_ledger(self, event: dict) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _ledger_net_state(self, job_id: str) -> str:
        """该 jid 的**净态**：文件序里最后一条 `job_pending|job_completed|job_cancelled` 的事件名。

        折叠口径与 `claimable_job_ids` 同源（它才是判据的唯一实现）；`""` = 账本里没有它。
        ★ 2026-10-05（六轮 F1）：生产 republish 走磁盘 IPC（`remote.hub_client.publish_job`
        **无条件追加** `job_pending`）——「撤单后同 jid 复活」在账本里就是「`job_pending` 晚于
        `job_cancelled`」，而 hub 内存的 `_cancelled` 即时闸收不到那个信号 ⇒ 认领闸必须拿本
        函数对账（`_claim_locked`），`publish` 的去重也按它（不能按「历史任一条 pending」）。
        """
        net = ""
        for e in self._read_ledger():
            if e.get("job_id") != job_id:
                continue
            ev = e.get("event")
            if ev in ("job_pending", "job_completed", "job_cancelled"):
                net = str(ev)
        return net

    # ---- 未完成的 pending job（一个判据，两个视图：可领取池 / 在飞集） ----
    def _unfinished_pending(self) -> list[tuple[str, float]]:
        """「**未完成**」的 pending job → `[(job_id, 发布时刻)]`，按发布序。

        判据（`claimable_job_ids` 与本函数是两个视图，筛子必须**只写一遍**——两处各写一遍
        必然漂成「池里看得见、认领说 held」那种更难查的形状）：

          * 账本净态 = `job_pending`（`job_completed` / `job_cancelled` 折叠掉）；
          * 未熔断（`_frozen`，§4.1：认领后零回传满阈值 ⇒ 不再回池，重发同 job_id 不清它）；
          * 目录在盘 ∧ payload 在盘；
          * **无 `result/`**（首写已分胜负）、**无 `fail.json`**（节点已报确定性失败）。

        「有没有人在跑」不在这份判据里：池看的是 `_lease_held` **假**，在飞集看的是**真**。
        """
        pending: dict[str, dict] = {}
        for e in self._read_ledger():
            jid = e.get("job_id")
            if not isinstance(jid, str):
                continue
            if e["event"] == "job_pending":
                pending[jid] = e
            elif e["event"] in ("job_completed", "job_cancelled"):
                pending.pop(jid, None)
        eligible: list[tuple[str, float]] = []
        for jid, e in pending.items():
            if jid in self._frozen:
                # ★ 毒包熔断（§4.1）：认领后零回传满阈值 ⇒ 冻结，不再回池。
                # 这是**独立于失败标记**的第二状态：重发同 job_id（publish_job）不清它，
                # 解冻只走人工入口（`unfreeze`）——否则「重发即重试」会把冻结当场抹掉。
                continue
            jd = self._job_dir(jid)
            if not jd.exists() or find_payload(jd) is None:
                continue  # 目录不存在或 payload 未落盘——不可领取
            if (jd / "result").exists():
                continue  # 结果已落盘待验收——首写已分胜负，不再领取
            if (jd / FAIL_NAME).exists():
                # 节点已报**确定性失败**（POST /jobs/{id}/fail）：再派给别的节点只是把
                # 同一个失败重演一遍（能力缺失类失败与节点无关地稳定复现），而训练侧
                # 此刻已经拿着原因停腿了。重发同 job（同幂等键 → 同 job_id）由
                # publish_job 清标记——重试路径不受影响。
                continue
            eligible.append((jid, float(e.get("ts", 0.0) or 0.0)))
        eligible.sort(key=lambda kv: kv[1])  # 发布序（同 P3b 的池排序）
        return eligible

    def claimable_job_ids(self) -> list[str]:
        """可领取池 = 「未完成」（`_unfinished_pending`）∧ **没有活租约**，按发布序。

        P3b 独占加超时（supersede §343）：持有**活租约**的 job 不在池中——
        worker 领到 PPO 任务后超时前不被别 worker 重领（那条活由 `inflight_job_ids`
        以**备份副本**的形式发出去，见那里）。判据是 `_lease_held`
        （活租约 = 未过期 ∧ 非孤儿，§52）而不是「未过期」：孤儿（claim 后零心跳、
        超过宽限）也回池（死 worker 回收只管这一条，不管调大 TTL——it24 倒车禁令）。
        已有结果未验收的 job 从池中剔除——首写锁定兜底（hub 重启丢租约时用）。
        """
        now = self._now()
        return [jid for jid, _ts in self._unfinished_pending() if not self._lease_held(jid, now)]

    def inflight_job_ids(self, *, not_held_by: str = "") -> list[str]:
        """**在飞集** = 「未完成」（同一个 `_unfinished_pending`）∧ **有活租约**，按发布序。

        为什么需要它（2026-10-06 用户口径：「按优先级表派任务，只要没有回传结果都是没完成，
        都能发给 worker 竞速」）：空闲 worker 的候选以前只有可领取池 =「没有活租约」那一半，
        于是「**有人正在跑、但还没回传结果**」的活对别的 worker **完全不可见**。代价在现场看得见：
        一份卡住的活（主线程卡死而心跳线程照旧每 60s 续租，`_lease_state` 永远是 ALIVE）能把
        自己永久锁在池外，唯一解法是训练侧重启换一个 job 身份（`RUN_ID` 每进程随机 ⇒ 换 job_id）。
        `peek` 用本集给空闲 worker 发**备份副本**（`mode="backup"`：无租约、先回传者胜、输家 409
        丢弃），「哪份更值得复制」由优先级表 `job_priority` 排序。

        `not_held_by`：把「**持有人就是请求者自己**」的那份排掉——空闲 worker 上一条 job 的结果
        可能还在异步回传（`ResultUploader`），把同一份活当备份再领一遍 = 同一张卡跑两遍。
        """
        now = self._now()
        out: list[str] = []
        for jid, _ts in self._unfinished_pending():
            if not self._lease_held(jid, now):
                continue
            if not_held_by and self._lease_workers.get(jid, "") == not_held_by:
                continue
            out.append(jid)
        return out

    # ---- 撤单：切模式 = 上一段整体作废（plan/switch-mode-drops-jobs §0） ----
    def _cancel_job_locked(self, job_id: str, *, reason: str) -> None:
        """单 job 作废的**唯一核心**（须持 `_lock`）：账本事件 + 撕承诺 + 即时闸。

        全池版（`cancel_unsettled_jobs`）与单 job 版（`cancel_job`）共用本函数——
        判据（谁可撤）留在各自的入口，落账动作只有这一份（2026-10-11，W7）。
        """
        self._append_ledger(
            {"event": "job_cancelled", "job_id": job_id, "reason": reason, "ts": self._now()}
        )
        # 撕掉残留承诺并 bump epoch：拿着作废前 epoch 来的认领会被 `_claim_locked`
        # 判 `demoted` 拒掉，不必等它自己发现池子空了。
        self._drop_commitment_locked(job_id)
        self._cancelled.add(job_id)

    def cancel_unsettled_jobs(self, *, reason: str = "mode-switch") -> list[str]:
        """把**未被认领**的可领取 job 作废（写 `job_cancelled` 账本事件）。返回被撤的 jid。

        治什么：切「在线/离线」后，上一个模式发布的 pending job 既领不到（停摆/归属两道闸，
        见 `role_blocked`）又一直躺在池里，直到人工清理——`claim_next` 的 `role_blocked`
        分支自己就点名「跳过后一直无人领」的收尾归撤单腿（`hub/queue_claims.py:100`）。
        训练侧的 `cancel_stale_jobs`（`remote/hub_client.py`）只按 `it <= 当前` 作废**本机
        自己**发布的滞后项，管不到「模式翻转」这一类。

        **在飞的不撤**（2026-10-03 用户裁决：「不管在算的，只管没领的」）——撤销在飞 job 属于
        轮边界强杀，本 plan 不做（plan §4 T0 / §7）。

        幂等：判据是 `claimable_job_ids()`（它已排除 job_completed / job_cancelled / 结果
        已落盘 / 已报失败 / 已冻结），重复调用返回空表。**落盘**（账本 jsonl）⇒ 重启后仍然
        作废：读面从账本重算，比「内存 volatile 标记」硬（plan 裁决 4 的原口径是内存，这里
        升级为账本——`job_cancelled` 的读面本来就这么设计的，见 `_read_ledger`）。
        """
        with self._lock:
            claimed = set(self._claimed) | set(self._computing)
            drop = [jid for jid in self.claimable_job_ids() if jid not in claimed]
            for jid in drop:
                self._cancel_job_locked(jid, reason=reason)
            return drop

    def cancel_job(self, job_id: str, *, reason: str = "manual") -> dict:
        """**单 job 定向作废**（人的动作；2026-10-11 事故，plan W7）。

        治什么：毒 job（三重校验必拒收的那类）以前没有任何人工把手——它既不在
        `cancel_unsettled_jobs` 的批量语义里（那是「切模式撤上一段」），单为它撤也说不出
        「撤哪一个」。返回 `{"job_id", "cancelled", "inflight", "why"}`（**形状恒定**，
        admin 层据 `inflight` 分 409/200）：撤成功 `cancelled=True`；在飞 ⇒ `inflight=True`；
        不可撤（已终局/结果已落盘/无 payload/已冻结）⇒ `cancelled=False` + why
        （**幂等可重放**，不报错）。

        判据与全池版**同源**（同一份 `claimable_job_ids()` ∧ 同一份 `_claimed|_computing`
        在飞排除，2026-10-03 用户裁决「不管在算的，只管没领的」）——单版本只多一层
        「哪一个」的寻址，不是第二份口径（评审 F4）。
        """
        with self._lock:
            claimed = set(self._claimed) | set(self._computing)
            if job_id in claimed:
                return {
                    "job_id": job_id,
                    "cancelled": False,
                    "inflight": True,
                    "why": "在飞（已认领/在算）——不撤",
                }
            if job_id not in self.claimable_job_ids():
                return {
                    "job_id": job_id,
                    "cancelled": False,
                    "inflight": False,
                    "why": "不在可领取池（已终局/结果已落盘/无 payload/已冻结）",
                }
            self._cancel_job_locked(job_id, reason=reason)
            return {"job_id": job_id, "cancelled": True, "inflight": False, "why": reason}

    # ---- 发布（训练主循环调用：写磁盘 + 账本） ----
    def publish(self, job_id: str, manifest: dict, payload_zip: bytes) -> None:
        """hub 发布 job：落盘 payload.zip + manifest.json + 账本 job_pending。

        幂等：同 job_id 已发布 → 覆盖 payload 但**不重复**追加 job_pending
        （账本按 job_id 去重——重启后重发布不产生双 pending）。
        """
        # 归属缓存随 manifest 一起失效（2026-09-25）：重发覆盖了 manifest ⇒ 缓存里的旧
        # 归属就是**谎报**（`job_role` 用它判「这份活归谁」，而它是租约闸的输入）。发布路径
        # 只此一处，放在这里就不需要「同 job_id 的 role 永不变」这条假设。
        with self._role_lock:
            self._roles.pop(job_id, None)
            self._kinds.pop(job_id, None)  # 同理由：新的 manifest 可能换了 kind（Q5 的闸读它）
        # ★M5：重发 = 新一轮跑（进度锚同属旧一轮）⇒ 一并作废，否则重发后一个旧 epoch
        # 时间戳会把新租约当场判成「在推进」。
        self._last_progress.pop(job_id, None)
        with self._lock:
            jd = self._job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True)
            (jd / PAYLOAD_NAME).write_bytes(payload_zip)
            (jd / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # ★六轮 F1/R3-b：按**净态**去重（旧实现按「历史任一条 job_pending」判：撤单后
            # 旧 pending 行还在 ⇒ `job_id in pending_ids` 永远为真 ⇒ 复活行永远不追加）。
            # 净态为 cancelled/缺失 ⇒ 追加一条新的 `job_pending`（复活事件）并清即时闸。
            net = self._ledger_net_state(job_id)
            if net not in ("job_pending", "job_completed"):
                self._append_ledger(
                    {
                        "event": "job_pending",
                        "job_id": job_id,
                        "runId": manifest.get("runId"),
                        "it": manifest.get("it"),
                        "ts": self._now(),
                    }
                )
                self._cancelled.discard(job_id)

    def job_failure(self, job_id: str) -> dict | None:
        """该 job 的失败记录（无 = None）。损坏/半截文件按「无」处理（不毒死端点）。"""
        p = self._job_dir(job_id) / FAIL_NAME
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
                return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            return None

    def get_result(self, job_id: str) -> dict | None:
        p = self._job_dir(job_id) / "result" / "result.json"
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
                return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            return None
