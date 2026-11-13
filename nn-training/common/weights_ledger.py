"""common/weights_ledger.py — 进程内权重下发账本（同 it 补波复用；S5 第十一刀，2026-09-27）。

从 `common/distribution.py` 搬出：**账本对象** `WeightsLedger`（键 `(kind, wver)` →
成功 POST 过的 node id）+ 记 / 忘 / 清 / 查 + `partition_weights_nodes`（reuse/need 拆分）。

`common/distribution.py` 留 `X as X` 门面 ⇒ 全仓 `common.distribution.partition_weights_nodes(...)`
等调用点一行不改；**账本是同一个对象**（两个模块看到同一份状态，不是副本）。

**状态归属（2026-11-13，python.refactor.md §P0-5）**：状态住 `WeightsLedger` 实例，
进程默认实例由 `default_ledger()` 提供；需要隔离的场景（测试、多 run 宿主）自建实例并传
`ledger=`。模块级 `_WEIGHTS_PUSHED` 只保留为**兼容别名**（指向默认实例的同一个 dict），
新代码不要再用它。

粘性事实（随行注释）：跨 it 换 wver 天然失效；ping/codeHash/bun 门 exclude 的节点须先
`forget_weights_node`，否则脏缓存让该节点整轮 409（2026-09-19 B6）。
"""

from __future__ import annotations


class WeightsLedger:
    """「已成功下发过该 wver」的进程内账本（同 it 补波复用；跨 it 换 wver 天然失效）。

    键 = `(kind, wver)`。**必须带 kind**：节点侧按 kind 分桶缓存权重，而同一个权重文件
    （同一 sha）会被多条腿使用——训练 rollout 用 'rollout'，其干净评估用 'eval'
    （2026-09-19 B6）。不带 kind 时先跑的那条腿的 note 会让另一条腿误判「已下发」
    而跳过 POST ⇒ 该节点对另一条腿整轮 409（脏缓存，与 A1 同类陷阱、方向相反）。
    """

    def __init__(self) -> None:
        self._pushed: dict[tuple[str, str], set[str]] = {}

    @property
    def pushed(self) -> dict[tuple[str, str], set[str]]:
        """底层映射（只读消费；兼容别名 `_WEIGHTS_PUSHED` 指向它）。"""
        return self._pushed

    def reset(self) -> None:
        """测试/运维：清空账本。"""
        self._pushed.clear()

    def mark_pushed(self, wver: str, node_id: str, kind: str = "rollout") -> None:
        if wver and node_id:
            self._pushed.setdefault((kind, wver), set()).add(node_id)

    def forget(self, node_id: str, kind: str | None = None) -> int:
        """把某节点从「已下发」账本摘掉 → 返回摘掉的条数（0 = 本来就没有）。

        kind=None（缺省）= 该节点**所有** kind 都摘（节点重启 ⇒ 它的桶全空了）；
        给 kind 时只摘那一条腿（避免同进程其它腿被无谓重握手；它们各自有 409 自愈兜底）。
        """
        if not node_id:
            return 0
        n = 0
        for key, s in self._pushed.items():
            if kind is not None and key[0] != kind:
                continue
            if node_id in s:
                s.discard(node_id)
                n += 1
        return n

    def already_pushed(self, wver: str, node_id: str, kind: str = "rollout") -> bool:
        return bool(node_id) and node_id in self._pushed.get((kind, wver), ())

    def partition(self, nodes: list, wver: str, kind: str = "rollout") -> tuple[list, list]:
        """按账本把节点拆成 (reuse, need)：reuse 跳过 POST，need 要下发。

        volume 同 it 补波：权重不变，首波已成功的节点进 reuse。kind 决定取哪条腿的账
        （缺省 'rollout'）；ping/codeHash 门 exclude 的节点须先 forget，否则可能带着脏缓存进 reuse。
        """
        reuse = [
            nd
            for nd in nodes
            if self.already_pushed(wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind)
        ]
        need = [
            nd
            for nd in nodes
            if not self.already_pushed(
                wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind
            )
        ]
        return reuse, need


_DEFAULT_LEDGER = WeightsLedger()
#: 兼容别名（指向默认实例的**同一个 dict**；`common/distribution.py` 的 `X as X` 门面靠它）。
_WEIGHTS_PUSHED = _DEFAULT_LEDGER.pushed


def default_ledger() -> WeightsLedger:
    """进程默认账本（需要隔离时自建 `WeightsLedger()` 并传 `ledger=`）。"""
    return _DEFAULT_LEDGER


def weights_push_cache_reset(*, ledger: WeightsLedger | None = None) -> None:
    (ledger or _DEFAULT_LEDGER).reset()


def note_weights_pushed(
    wver: str, node_id: str, kind: str = "rollout", *, ledger: WeightsLedger | None = None
) -> None:
    (ledger or _DEFAULT_LEDGER).mark_pushed(wver, node_id, kind)


def forget_weights_node(
    node_id: str, kind: str | None = None, *, ledger: WeightsLedger | None = None
) -> int:
    return (ledger or _DEFAULT_LEDGER).forget(node_id, kind)


def weights_already_pushed(
    wver: str, node_id: str, kind: str = "rollout", *, ledger: WeightsLedger | None = None
) -> bool:
    return (ledger or _DEFAULT_LEDGER).already_pushed(wver, node_id, kind)


def partition_weights_nodes(
    nodes: list, wver: str, kind: str = "rollout", *, ledger: WeightsLedger | None = None
) -> tuple[list, list]:
    return (ledger or _DEFAULT_LEDGER).partition(nodes, wver, kind)
