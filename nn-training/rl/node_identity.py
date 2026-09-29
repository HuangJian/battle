"""node_identity —— 「同 node 的 bootId 与本轮账本不一致 ⇒ 本轮排除」的训练侧判据。

**为什么有这一层**（plan/sampler-single-instance.plan.md §8-Q2）：agent 侧已用应用层互斥保证
「一个 node id = 一个监听实例」（`tools/agent/single-instance.ts`），但那是**节点自证**。
这里留一道**客户端**的观测判据：`/v1/ping` 现在报 `pid` / `bootId`（进程启动随机 8 hex），
同一轮里同一节点的 bootId 变了 ⇒ 这个端口上**换过进程**——a98 事故（2026-09-29）的指纹：
两个 agent 同时听 8443，内核按 4 元组哈希把一轮 192 局的请求分给两个代码版本，旧版本
（metrics 45 列）的 shard 混进 payload，云 worker 一算就炸、整门课 aborted。

**判据**（纯函数 + 一个进程内小账本，无 IO）：

1. **本轮**（round key = `腿:采集单元 id`，见 `round_key`）第一次 ping 到某节点的 bootId
   就是该节点在本轮的**钉子**；
2. 之后的任何一次 ping 若 bootId 不同 ⇒ 返回一行人读原因（调用方据此**本轮排除**该节点：
   不再派新任务 / 不再回场）；
3. **旧 agent 无 `bootId` 字段 ⇒ 恒 `None`**（fail-open）——这道门只在新 agent 上生效，
   否则升级波期间会把还没升级的节点整批排除（那是比它要防的事更大的事故）；
4. 钉子**只钉一次**（不一致后不改写成新值）：不一致是**本轮**的事实，重复 ping 得到同一行
   原因，调用方的日志/账本因此幂等。

**为什么按轮记账、不按全局**：一次正常升级/重启就会换 bootId，跨轮比会把「重启前后的一轮」
误伤（plan §8-Q2 的原话）。按轮钉 ⇒ 影响面恰好 = 触发变化的那一轮，下一轮自动重新钉。
代价（已知并接受）：一个节点**在轮中途**重启后本轮不再被用（它对这一轮的贡献止于重启前）。
判据比「客户端的 codeHash 门」更早、更便宜：codeHash 门要等一个代码版本差异，而 bootId
变化在**同一份代码**的进程换代上也看得见。

**它不是重启/升级的触发器**：mismatch 只上报 + 排除，**绝不**下发 `/v1/restart`——两个进程
同时服务不是重启一个能修好的事（要人去停掉多出来的那个；见 `docs/nn/runtime-opt.md` §29
的 `/proc/net/tcp` 配方）。
"""

from __future__ import annotations

__all__ = ["LEDGER_MAX_ROUNDS", "clear", "note_ping", "round_key"]

#: 账本最多记住几轮（只留最近的；采集单元一轮一个 key，防长跑内存无界）。
LEDGER_MAX_ROUNDS = 8

#: round key → node id → {"bootId": str, "pid": int | None}
_LEDGER: dict[str, dict[str, dict[str, object]]] = {}


def round_key(leg: str, ident: str) -> str:
    """采集单元的账本键 = `<采集单元 id>:<腿>`。

    **腿后缀不能省**：同一个 iterId 的 rollout 与 eval 是两轮不同的采集，共用账本会把
    「eval 之前重启过」误判成 rollout 中途换进程。钉与查必须调**同一个** helper——
    rollout 的钉在 `rl/dispatch.py`、查在 `rl/queue_local.py`，两处各写一份字面量必漂。
    """
    return f"{ident}:{leg}"


def clear() -> None:
    """清空账本（单测用；生产不调用——清空等于「重新钉」，会放过一次不一致）。"""
    _LEDGER.clear()


def _rounds(key: str) -> dict[str, dict[str, object]]:
    """取该轮的账本段（按需建，且只留最近 `LEDGER_MAX_ROUNDS` 轮）。"""
    rounds = _LEDGER.get(key)
    if rounds is None:
        rounds = {}
        _LEDGER[key] = rounds
        while len(_LEDGER) > LEDGER_MAX_ROUNDS:
            _LEDGER.pop(next(iter(_LEDGER)))
    return rounds


def note_ping(rkey: str, node_id: str, ping: dict | None) -> str | None:
    """把一次 ping 结果记进本轮账本，返回 `None` = 一致/无意见，否则 = 排除原因。

    `ping` = `/v1/ping` 的 JSON dict（`ping is None` = 没探到：**不记账**，探测失败与
    「换过进程」是两回事——探不到的节点由调用方原有的重探/回场机制管）。
    """
    if not isinstance(ping, dict):
        return None
    boot = ping.get("bootId")
    if not isinstance(boot, str) or not boot:
        return None  # 旧 agent（升级波未覆盖）/ 字段缺失：fail-open，见模块 docstring 第 3 条
    pid = ping.get("pid")
    rounds = _rounds(rkey)
    pinned = rounds.get(node_id)
    if pinned is None:
        rounds[node_id] = {"bootId": boot, "pid": pid}
        return None
    if pinned["bootId"] == boot:
        return None
    return (
        f"同一轮里 bootId 变了（bootId {str(pinned['bootId'])[:8]}… → {boot[:8]}…，"
        f"pid {pinned.get('pid')} → {pid}）—— 这个端口上换过进程"
        "（两个 agent 同时在听？先查 /proc/net/tcp 的 st=0A 行）"
    )
