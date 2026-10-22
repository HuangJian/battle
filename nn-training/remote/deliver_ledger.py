"""remote/deliver_ledger.py —— 补传的**唯一记账**：`delivered.json` 的读 / 写 / 重置水位。

> 从 `remote/offline_deliver.py` 搬出（2026-10-21，plan/offline-deliver-stall §4-W5）：
> LOC 预算（`tests/test_python_loc_budget.py`）把那个文件顶到 1000 行，而这一块是**独立所有者**
> （`delivered.json` —— 不变量 §3-4「唯一记账」）+ **独立触发条件**（每轮送达 / 拒收 / 人重置），
> 与「探活/投递/降级」那条链没有共享状态。

**为什么是 mixin 而不是函数库**：这六个方法的全部输入就是交付器自己的字段（`_delivered` /
`_rejected*` / `_repost` / `_cv` / `root` / `run_id`）。搬成「函数 + 把八个字段当参数传」会把
调用点变成一堵墙；这里沿用 `hub/queue_*.py` 的混入模式 —— 字段仍由 `OfflineDeliverer.__init__`
持有（单写者纪律不变），本模块只提供读写面。

**记账语义（改之前先读）**：

  * `artifacts`：**权重已到岸**的轮次（`ok`/`skip` 才销账）——「送达」永远只指这一件事；
  * `owed_reposts`：欠一次**评估读数**重投的轮次（跨会话活着；送达即销账）；
  * `rejected`：被 hub **内容拒收**的轮次 + 当时的权重指纹（D6：到 TTL / 记满次数自动复活）；
  * `rejected_reset_at`：人显式重置的水位 —— 活着的补传子进程下次落盘时不许把重置过的行写回来。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from common.protocol import OFFLINE_DELIVERED_NAME
from remote.artifacts import ArtifactStore, atomic_write_json, sha256_bytes

#: 内容拒收记录的**寿命**（秒）与**最多记几次**（D6 / plan §15）：`rejected` 跨会话活着（按权重
#: 指纹认领），而一次**误判**（例如 hub 侧 409 被读成内容问题）会让那一轮**永久不再回传** ——
#: 权重还在本地盘上，代价却是永久静默丢一轮。到点就自动复活（宁多试一次，不静默丢）。
REJECTED_TTL_SEC = 24 * 3600.0
REJECTED_MAX_TRIES = 3
#: `delivered.json` 里「人显式重置过拒收」的水位（unix 秒）：`reset_rejected()` 写它，而写方
#: （`_save_ledger`）比它更早的拒收记录一律不再落盘 —— 否则运维刚清掉的行会被**活着的**补传
#: 子进程在下一次落盘时原样写回来（它的 `_rejected` 还在内存里）。
REJECTED_RESET_KEY = "rejected_reset_at"


def _num_or(v: object, default: float) -> float:
    """宽容地读一个数（JSON 里可能是字符串/None/压根没有）。

    为什么不用 `float(v)`：`delivered.json` 是**盘上的**、也可能被手改过 ⇒ 一个坏字段不该让
    整个加载返回（那会把「已投递」全忘掉 = 全量重投）。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _prune_reset_markers(markers: dict[str, float], *, now: float) -> dict[str, float]:
    """只留 TTL 窗内的重置水位。

    为什么不能「行没了就把水位也删掉」：水位的作用恰恰是拦**别人内存里那一份**还在的记录
    ——行刚被放掉时它还在飞；删了水位就等于把刚做的重置撤回。代价是水位会慢慢积累，所以
    按 TTL 清：过期的水位已经没有用了（能靠它救回来的那份内存记录自己也过了 TTL）。
    """
    return {k: v for k, v in markers.items() if now - v <= REJECTED_TTL_SEC}


class DeliverLedgerMixin:
    """`delivered.json` 的读 / 写 / 重置水位（字段由宿主类持有，见模块 docstring）。"""

    # 宿主（`OfflineDeliverer`）在 `__init__` 里给的字段 —— 这里只声明类型：
    root: Path
    run_id: str
    base_url: str
    log: Callable[[str], None]
    _cv: threading.Condition
    _delivered: set[int]
    #: ★ 2026-10-23（`plan/offline-deliver-full-and-gaps` §3.3）：**统计件**已送达的轮次。
    #: 与 `_delivered`（权重件）**必须分开**：共用一本时「metrics 成功」会把「权重没发」
    #: 掩盖掉（`pending()` 就再也列不出那一轮了）。
    _metrics_sent: set[int]
    _repost: set[int]
    _rejected: dict[int, str]
    _rejected_at: dict[int, float]
    _rejected_tries: dict[int, int]
    _rejected_fp: dict[int, str]
    _ledger_readable: bool
    _result_done: bool
    _now: Callable[[], float]

    @property
    def ledger_path(self) -> Path:
        return self.root / OFFLINE_DELIVERED_NAME

    def _load_ledger(self) -> None:
        """读已投递项（缺失/损坏 → 空：那只会导致重复投递，hub 侧幂等兜住）。

        读成功（存在 ∧ 解析开 ∧ `run_id` 是本次的）时**顺带**把 `_ledger_readable` 置真 ——
        抽屉删除拿它当硬门槛（DoD-3c），所以「读不出来」与「没送达」必须在这里就分开。
        """
        p = self.ledger_path
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("run_id") != self.run_id:
            return  # 别的 run 的记账（同一个目录被复用）——不认
        self._ledger_readable = True
        for it in data.get("artifacts") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._delivered.add(it)
        # 统计件（2026-10-23）：老账本没有这个键 ⇒ 空集（等价于「都还没发」，会重发一次，
        # hub 侧按 (it, weights_fp) 幂等兜住）。
        for it in data.get("metrics") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._metrics_sent.add(it)
        self._result_done = bool(data.get("result_done"))
        # 跨会话的两笔欠账（必须落盘；§10.3）：
        #   * `owed_reposts` —— 上一段没送出去的重投，**本会话直接接手**（那些轮次早已记账，
        #     `pending()` 不会再现它们 ⇒ 不读回就是丢一次补读数）；
        #   * `rejected` —— 被 hub 内容拒收的轮次（按**权重指纹**记账）：核对得上才认，
        #     指纹不符（同一 it 换了内容）⇒ 不认，新会话照常再试一次。
        for it in data.get("owed_reposts") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._repost.add(it)
                self._rejected.pop(it, None)
        if self._repost:
            self.log(f"上个会话欠 {len(self._repost)} 轮重投（补评估读数）——本会话继续补")
        for row in data.get("rejected") or []:
            if not isinstance(row, dict):
                continue
            it = row.get("it")
            fp = str(row.get("weights_fp", "") or "")
            if not isinstance(it, int) or isinstance(it, bool):
                continue
            if not fp or not self._weights_fp_matches(it, fp):
                continue  # 没证据 / 内容换了 ⇒ 当它没被拒过（宁多试一次，不静默丢一轮）
            # D6：拒收**不是**永久判决 —— 到 TTL（或记满次数）就当我没拒过。
            # 旧账本没有 `at`（当时不记年龄）⇒ 按「早于 TTL」算，也就是**一律复活**：一条没有
            # 年龄的证据不足以支撑「永不再试」，而重试一轮的代价只是再推一个小体。
            at = _num_or(row.get("at"), 0.0)
            tries = int(_num_or(row.get("tries"), 0.0))
            age = self._now() - at
            if tries >= REJECTED_MAX_TRIES or age > REJECTED_TTL_SEC:
                self.log(
                    f"补传 it{it} 不再沿用上次的内容拒收（记了 {tries} 次｜{age / 3600:.1f}h 前）"
                    "——本会话再试一次（D6）"
                )
                continue
            self._rejected[it] = str(row.get("why", "") or "")
            self._rejected_fp[it] = fp
            self._rejected_at[it] = at
            self._rejected_tries[it] = tries

    def _reject_round(self, it: int, reason: str, weights_fp: str = "") -> str:
        """把这一轮记进拒收集（`pending()` 从下拍起不再带它），返回 "skip"。

        与 401/403 的「停用整条腿」严格区分：内容问题（体形状/账本行）只毁一轮，而停用
        会把这一整段剩下的几十轮全部拦下（2026-09-22 事故：it1 一次 400 之后整段再没回过
        一轮）；而鉴权问题每轮重试会把本 IP 封掉（D9）——两者代价完全不同，不能共用一个反应。

        跨会话（2026-10-07）：这条记录随 `delivered.json` 带上**权重指纹**——子进程重启/idle-timeout
        会把「会话」缩到分钟级，只住内存的话同一轮会反复重投 ~1.9MB；而新会话读回时先核对指纹，
        同一 `it` 换了内容就不认（照常再试一次）。

        D6（2026-10-16）：跨会话意味着一**次误判会永久生效**（hub 409 被读成内容问题时，那一轮
        就再也不会回传）⇒ 每条记录带上 `at`/`tries`，到 `REJECTED_TTL_SEC` 或记满
        `REJECTED_MAX_TRIES` 次自动复活；`reset_rejected()` 是「现在就要它再试一次」的人工出口。
        """
        self._rejected[int(it)] = reason
        self._rejected_at[int(it)] = self._now()
        self._rejected_tries[int(it)] = self._rejected_tries.get(int(it), 0) + 1
        if weights_fp:
            self._rejected_fp[int(it)] = str(weights_fp)
        self.log(
            f"补传 it{it} **本轮跳过**（{reason}）——产物照常落本地；"
            f"其余轮次照推（本会话不再重试这一轮，{REJECTED_TTL_SEC / 3600:.0f}h 内或新会话会再试一次）"
        )
        return "skip"

    def refresh_from_ledger(self) -> None:
        """重读 `delivered.json`（**只读镜像用**：进程模式下账本的写者是子进程，父侧那份缓存
        不刷新就会骗人：明明三轮回传都到了，`pending()` 还列着它们）。

        与 `_load_ledger` 的差别只有一个：先把上一份读面清掉（否则删除的记账永远删不掉）。
        """
        self._delivered.clear()
        self._metrics_sent.clear()
        self._rejected.clear()
        self._rejected_fp.clear()
        self._rejected_at.clear()
        self._rejected_tries.clear()
        self._result_done = False
        self._load_ledger()

    def _weights_fp_matches(self, it: int, fp: str) -> bool:
        """这一轮的权重文件是不是**就是**上次被拒收的那份（只在加载时，对「被拒过的」那几轮算）。"""
        try:
            wj = ArtifactStore(self.root, run_id=self.run_id).weights_path(it).read_bytes()
        except OSError:
            return False
        return sha256_bytes(wj) == fp

    def _save_ledger(self) -> None:
        with self._cv:
            owed = sorted(self._repost)
        # 人的重置水位（D6 的「现在就要它再试一次」）：比它更早的拒收**不落盘**。不读这一眼的话，
        # 运维刚清空的行会被活着的子进程下一次落盘原样写回来（它的 `_rejected` 还在内存里）。
        markers = self._ledger_reset_markers()
        for it, at in list(self._rejected_at.items()):
            if at <= markers.get(str(it), 0.0):
                self._rejected.pop(it, None)
                self._rejected_fp.pop(it, None)
                self._rejected_at.pop(it, None)
                self._rejected_tries.pop(it, None)
        rows = [
            {
                "it": it,
                "weights_fp": self._rejected_fp.get(it, ""),
                "why": why,
                # 年龄面（D6）：`at` 与 `tries` 决定这条记录何时过期（`REJECTED_TTL_SEC`/
                # `REJECTED_MAX_TRIES`）——没有它们就只剩「永久生效」这一种语义。
                "at": self._rejected_at.get(it, self._now()),
                "tries": self._rejected_tries.get(it, 1),
            }
            for it, why in sorted(self._rejected.items())
        ]
        atomic_write_json(
            self.ledger_path,
            {
                "run_id": self.run_id,
                "hub_url": self.base_url,
                "artifacts": sorted(self._delivered),
                # ★ 2026-10-23：统计件单独一本（权重件 = `artifacts`，两本互不相掩）。
                "metrics": sorted(self._metrics_sent),
                "result_done": self._result_done,
                # 欠账（§10.3）：`owed_reposts` = 还没送达的重投（送达即销账）；`rejected` = 内容
                # 拒收过的轮次 + 当时的权重指纹。两者都是**磁盘状态**：_repost 只住内存时，
                # 段末或子进程重启丢掉的那一次就永远补不回来了。
                "owed_reposts": owed,
                "rejected": rows,
                "updated_at": self._now(),
                # 水位原样带过去（连「已被放掉」那些也要带）：抹掉它 = 活着的会话下一次落盘
                # 又把那一轮写回来了。只按 TTL 窗清（见 `_prune_reset_markers`）。
                REJECTED_RESET_KEY: _prune_reset_markers(markers, now=self._now()),
            },
        )
        # 写成功 ⇒ 账本这份证据是**可信**的（`_ledger_readable` 从此不再只是「进门时读得到」）：
        # 抽屉删除把它当硬门槛（DoD-3c），而本会话第一次送达时磁盘上还没有账本（首开目录），
        # 不在这里置真就永远删不掉 —— 刚写完的账本就是「hub 收过」的书面证据，且它是我们写的。
        self._ledger_readable = True

    def _ledger_reset_markers(self) -> dict[str, float]:
        """`delivered.json` 里「人重置过这一轮拒收」的水位：`it（字符串）→ 重置时刻`。

        为什么按 it 而不是一个全局水位：只重置一环时，全局水位会把**其余**环次的拒收
        一并复活（活着的子进程下一次落盘就会把它们也放了）。见 `reset_rejected()`。
        """
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get(REJECTED_RESET_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(k): _num_or(v, 0.0) for k, v in raw.items()}
