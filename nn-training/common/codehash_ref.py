"""common/codehash_ref.py — 节点门的**参考 codeHash**（2026-11-15 用户口径）。

为什么单独成模块：`common/distribution.py` 已顶到 1000 LOC 预算（AGENTS §9 的
`test_python_loc_budget`），而这段判据要被 rollout / eval / batch / BC 四条腿共用。

口径：**以 self 节点自报的为准**。hub 工作区有未提交改动时 `compute_code_hash()`
算的是「还没人在跑的那份」——拿它当基准，所有远端节点都被判 mismatch（本地改代码 =
全集群瘫痪）。self 的 agent 报的是实际在跑的代码，与远端同一把尺子；self 缺失/未报
⇒ 回落本机现算（老行为，保持可跑）。
"""

from __future__ import annotations

import urllib.parse

from common import codehash


def is_self_node(url: str, node_id: str = "") -> bool:
    """self/回环节点：agent 由训练机同一工作区启动——代码与训练机**天然同源**。

    `remote/node_upgrade.is_self_node` 转调这里（同一个判据，两份文档都别改口径）：
    远控语义是「stale 只纯重启、禁止 pull」，而本模块只用它挑参考 codeHash。
    """
    if node_id.strip().lower() == "self":
        return True
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        # `.hostname` 只在端口非法时抛（如 `http://host:abc`）：这是配置错，判「不是本机」。
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def reference_code_hash(
    cfg_nodes: list[dict],
    probes: list[tuple[str, dict | None]] | None = None,
    *,
    timeout: float = 3.0,
) -> str:
    """返回集群参考 codeHash；`probes` = 调用方已并行 ping 好的 `(节点 id, ping)`。

    给了 `probes`（rollout / eval 腿本来就要 ping 全集群）⇒ **零新增请求**；
    没给（batch / BC 腿在规划期还没有 ping 结果）⇒ 只 ping self 一台。
    """
    if probes is None:
        # 延迟 import：`common.distribution` 是 wire client（本模块被四条腿共用，别在
        # import 期把它整包拉起来）。
        from common.distribution import node_ping

        probes = []
        for n in cfg_nodes:
            nid = str(n.get("id") or n.get("url") or "?")
            if is_self_node(str(n.get("url") or ""), nid):
                ping = node_ping(n["url"], n.get("authKey", ""), timeout=timeout)
                probes = [(nid, ping)]
                break
    for n, (nid, ping) in zip(cfg_nodes, probes, strict=False):
        if ping and ping.get("codeHash") and is_self_node(str(n.get("url") or ""), nid):
            return str(ping["codeHash"])
    return codehash.compute_code_hash()
