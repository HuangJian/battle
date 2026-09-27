"""dist_weights_ledger.py — 进程内权重下发账本（同 it 补波复用；S5 第十一刀，2026-09-27）。

从 `dist_common.py` 搬出（**逐字节不动**）：模块全局 `_WEIGHTS_PUSHED`（键 `(kind, wver)`
→ 成功 POST 过的 node id）+ 记 / 忘 / 清 / 查 + `partition_weights_nodes`（reuse/need 拆分）。
`dist_common.py` 留 `X as X` 门面 ⇒ 全仓 `dist_common.partition_weights_nodes(...)` 等
调用点一行不改；**账本是同一个 dict 对象**（两个模块看到同一份状态，不是副本）。

粘性事实（随行注释）：跨 it 换 wver 天然失效；ping/codeHash/bun 门 exclude 的节点须先
`forget_weights_node`，否则脏缓存让该节点整轮 409（2026-09-19 B6）。
"""

from __future__ import annotations

# 进程内「已成功下发过该 wver」缓存（volume 同 it 补波复用；跨 it 换 wver 天然失效）。
# 值 = 成功 POST 过的 node id（键 = (kind, wver)，见下）。失效：ping/codeHash/bun 门 exclude
# 时调用 forget_weights_node。局限：同 codeHash 手动重启可能残留脏缓存（同 it 窗口内罕见）。
# 键 = (kind, wver)。**必须带 kind**：节点侧按 kind 分桶缓存权重，而同一个权重文件
# （同一 sha）会被多条腿使用——训练 rollout 用 'rollout'，其干净评估用 'eval'
# （2026-09-19 B6）。不带 kind 时先跑的那条腿的 note 会让另一条腿误判「已下发」
# 而跳过 POST ⇒ 该节点对另一条腿整轮 409（脏缓存，与 A1 同类陷阱、方向相反）。
_WEIGHTS_PUSHED: dict[tuple[str, str], set[str]] = {}


def weights_push_cache_reset() -> None:
    """测试/运维：清空进程内权重下发缓存。"""
    _WEIGHTS_PUSHED.clear()


def note_weights_pushed(wver: str, node_id: str, kind: str = "rollout") -> None:
    if wver and node_id:
        _WEIGHTS_PUSHED.setdefault((kind, wver), set()).add(node_id)


def forget_weights_node(node_id: str, kind: str | None = None) -> int:
    """把某节点从「已下发」账本摘掉 → 返回摘掉的条数（0 = 本来就没有）。

    kind=None（缺省）= 该节点**所有** kind 都摘（节点重启 ⇒ 它的桶全空了）；
    给 kind 时只摘那一条腿（避免同进程其它腿被无谓重握手；它们各自有 409 自愈兜底）。
    """
    if not node_id:
        return 0
    n = 0
    for key, s in _WEIGHTS_PUSHED.items():
        if kind is not None and key[0] != kind:
            continue
        if node_id in s:
            s.discard(node_id)
            n += 1
    return n


def weights_already_pushed(wver: str, node_id: str, kind: str = "rollout") -> bool:
    return bool(node_id) and node_id in _WEIGHTS_PUSHED.get((kind, wver), ())


def partition_weights_nodes(
    nodes: list, wver: str, kind: str = "rollout"
) -> tuple[list, list]:
    """按进程内缓存把节点拆成 (reuse, need)：reuse 跳过 POST，need 要下发。

    volume 同 it 补波：权重不变，首波已成功的节点进 reuse。kind 决定取哪条腿的账（缺省 'rollout'）；ping/codeHash 门
    exclude 的节点须先 forget_weights_node，否则可能带着脏缓存进 reuse。
    """
    reuse = [
        nd
        for nd in nodes
        if weights_already_pushed(wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind)
    ]
    need = [
        nd
        for nd in nodes
        if not weights_already_pushed(wver, str(nd.get("id") or nd.get("url") or "?"), kind=kind)
    ]
    return reuse, need
