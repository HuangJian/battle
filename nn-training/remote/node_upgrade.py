"""`remote/node_upgrade.py` — 训练机对采样节点的**远控升级 / 重启护栏**（2026-11-19，B8）。

从 `common/distribution.py` 整簇搬来（plan `python.refactor.md` §0.4-10 / 附录 B；技术档案
`docs/nn/engineering.md` §219）。为什么家在 `remote/` 而不是 `common/`：

  * **这是跨机器的控制动作**，不是线协议：`POST /v1/restart` 让远端 agent `git pull` + 重启；
  * `common/` 是 L0（stdlib-only 的纯层），B8 的方向是**把出站副作用搬出去**；`_request`
    这个唯一 HTTP 漏斗留在 `common.distribution`（wire client），本模块**只调它、不自备出口**
    —— G8 门禁的零额度断言（`tests/test_distribution_io_budget.py`）盯着这一条；
  * 消费者全在它上面：`trainer/`（入口锁存分支 / ping 门 / rescan）与 `tools/dist_upgrade_cli.py`
    —— 没有 `common/` 或 `worker/` 的读者，所以搬上来不会制造反向边。

两块状态都是有理由的进程级状态，且**都住显式对象**（模块只留一个默认实例，没有模块级可变量、
没有 `global`）：
  * `_BRANCH`（`BranchLatch`）：训练机当前分支的**锁存值**（`run_rl` / `run_bc` / `loop_serve`
    启动时 `set_upgrade_branch` 一次）。远端升级永远用它，rl-config 的 `upgradeBranch` 已废弃——
    2026-08-30 事故：残留旧分支名把全部节点 reset 回旧代码。
  * `_RESTARTS`（`RestartMemo`）：跨代重启去重 + 冷却窗。要隔离的测试自己 new 一份
    （`reset_restart_state` / `seed_restart_state` 是给 CLI 与用例的入口）。

为什么要对象化而不是一个模块字符串（2026-11-20，P0-5 收尾）：字符串 + `global` 在 AST 层面
就是「进程级可变态」，读代码的人无法从类型看出它只有 set/get 两个合法操作；`BranchLatch` 把
写入通道收成一个方法（顺带把 `None` 收成 `""`，调用方不必再写 `or ""`），`GLOBAL_BASELINE` 里
本文件的那条也因此归零。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable

from common.codehash import dirty_hash_files
from common.codehash_ref import is_self_node as _is_self_node
from common.distribution import NET_ERRORS, node_ping, node_request


class BranchLatch:
    """训练机当前分支的锁存（`run_rl` / `run_bc` / `loop_serve` 启动时写一次）。

    节点远控升级**永远**用这个分支，且它是**唯一**来源——rl-config 的 upgradeBranch 已废弃
    （残留旧战役分支名曾把全部节点 reset 回旧代码，2026-08-30 事故）。取值入口 =
    `current_upgrade_branch()`；值随派发向下传给 `trainer/queue_local`，**没有**
    「调用方传 branch 进来」那条路了。

    为什么是个对象：把「写」收成一个方法之后，模块里不再需要 `global`（那条基线归零），
    而 `None` 也在入口处收成 `""` ——调用方不必每处再写 `or ""`。
    """

    def __init__(self, branch: str = "") -> None:
        self._branch = str(branch or "")

    def set(self, branch: str | None) -> None:
        self._branch = str(branch or "")

    def get(self) -> str:
        return self._branch


_BRANCH = BranchLatch()


def set_upgrade_branch(branch: str) -> None:
    _BRANCH.set(branch)


def is_self_node(url: str, node_id: str = "") -> bool:
    """self/回环节点：agent 由训练机同一工作区启动——代码与训练机**天然同源**。

    远控语义（2026-08-30 用户修订）：stale 时允许远控，但只做**纯重启**
    （request_upgrade 不带 pullBranch ⇒ agent 不做任何 git 操作，重启进程即
    拾取工作区新代码）；**禁止 pull**——共享工作区上的 git pull 是破坏性的
    （曾把本机 reset 回旧分支，2026-08-30 事故）。codeHash 正确时零动作。

    判据本体住 `common/codehash_ref.py`（`common/` 不能反向 import `remote/`，
    而参考 codeHash 的挑选也要用它）——这里只转调，不写第二份。
    """
    return _is_self_node(url, node_id)


def current_upgrade_branch() -> str:
    """节点远控升级要用的分支 = **训练机锁存的那一个**（空串 = 不升级，调用方只告警）。

    ★ 2026-09-26（评审更正）：本函数此前是 `upgrade_branch_or(explicit)`，语义为
    「**显式值优先于锁存**」——那正是 2026-08-30 事故的载体：rl-config 里残留的
    `'intent-ai'` 只要非空就盖掉锁存，把全部节点 reset 回旧代码。读点已随
    `policy.upgradeBranch` 一起删除（plan/rl-config-cleanup.plan.md §3.1），本次评审又把
    那个 `explicit` 参数拿掉 ⇒「配置能盖锁存」在**签名层面**不再成立——要复发必须改签名，
    而不是顺手写一行 `or` 兜底回去。
    """
    return _BRANCH.get()


def request_upgrade(url: str, auth_key: str, branch: str, timeout: float = 20.0) -> bool:
    """主动升级机制（M8）：POST /v1/restart {pullBranch} → agent 端 git pull + 重启。

    编排层 ping 发现节点 codeHash 不符（stale）时调用——把它从「静默排除」升级为
    「主动指示更新重启」。返回 True = 接受（agent 异步执行，重启窗口内不可达，
    rescan 会在它恢复后按新 codeHash 纳入）。失败返回 False（不抛、不阻塞训练）。
    """
    try:
        status, _body = node_request(
            url.rstrip("/") + "/v1/restart",
            auth_key,
            timeout,
            data=json.dumps({"pullBranch": branch}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return status in (200, 202)
    except NET_ERRORS:
        return False


# ---------------- 远控重启护栏（2026-09-01 重启循环修复） ----------------
# 症状：① 远控重启过的进程被再次远控重启（无限循环，节点无法贡献）；
#       ② 用户手动更新代码重启的进程被远控杀掉再重启。
# 根因：expected codeHash 由训练机**工作区**文件内容算出——含未提交改动；远端
#   `git pull` 只能拿到已推送提交，永远无法收敛到该 hash ⇒ 每轮 ping 门 / rescan
#   都判 stale ⇒ 再杀再拉，无限循环（2026-09-01 实测四连杀）。叠加根因：旧实现的
#   去重集合 upgrade_requested 是每轮局部变量，新一轮迭代重新建集合，stale 节点
#   每轮、每次 rescan 都再收一次 restart。
# 处置（双层）：
#   - 脏工作区护栏：hash 集内有未提交改动时，拒绝对**远端**节点下发 pull+restart
#     （无效且具破坏性）；self/回环节点豁免（代码同源，纯重启即拾取工作区新代码）。
#   - 跨代去重：同一节点 + 同一 (agent codeHash, 期望 hash) 只下发一次 restart；
#     节点 hash 变化（pull 生效 / 手动更新）或训练机期望值变化（本机 commit/切分支/
#     改集内文件）后自动恢复资格（F1，plan/dist-codehash-stale-fix.md：dedup 键纳入
#     期望 hash——否则 mac 类字节差异稳定后训练机永远不再下发升级，运维 pull+重启
#     因 hash 不变反而永远无法重新纳管）。
#: 升级去重**冷却窗**（秒，2026-09-19 审计 F3）。同节点、同 (agent codeHash, 期望 hash)
#: 在窗内只下发一次 restart；**超窗后允许再发一次**（不收敛的节点由此自愈）。
#: 为什么需要（旧行为 = 永久 dedup）：节点 pull 失败 / 环境不支持远端升级时，它会带着
#: **同一个** codeHash 回来 ⇒ dedup 键永久命中 ⇒ 该节点再也收不到升级指令（训练循环里
#: 直到训练机有新提交；TS 工具那条腿还把这个 memo 落盘到 tmp/node-upgrade-memo.json，
#: 跨调用继续压制）。冷却窗既保住「防连环杀」（2026-09-01 重启循环事故），又给不收敛节点
#: 一条自愈路径。`NN_RESTART_DEDUP_COOLDOWN_S` 可覆盖（0 = 关闭去重，每轮都发）。
RESTART_DEDUP_COOLDOWN_SEC = 600.0


class RestartMemo:
    """跨代重启去重 memo（`nid -> (agent_ping_hash, expected_hash, 该次下发时刻)`）。

    P0-5 同款：状态住显式对象，模块只留 `_RESTARTS` 默认实例；第三项（时刻）只为冷却窗存在。
    """

    def __init__(self) -> None:
        self.seen: dict[str, tuple[str, str, float]] = {}

    def reset(self) -> None:
        self.seen.clear()


_RESTARTS = RestartMemo()


def restart_dedup_cooldown_sec(cooldown_sec: float | None = None) -> float:
    """去重冷却窗秒数：显式传参 > env `NN_RESTART_DEDUP_COOLDOWN_S` > 缺省常量。"""
    if cooldown_sec is not None:
        return max(0.0, float(cooldown_sec))
    raw = os.environ.get("NN_RESTART_DEDUP_COOLDOWN_S", "")
    if raw.strip():
        try:
            return max(0.0, float(raw))
        except ValueError:
            return RESTART_DEDUP_COOLDOWN_SEC
    return RESTART_DEDUP_COOLDOWN_SEC


def reset_restart_state() -> None:
    """测试专用：清空默认实例里的跨代去重状态（进程级状态，测试间必须隔离）。"""
    _RESTARTS.reset()


def seed_restart_state(entries: Iterable[object]) -> int:
    """把调用方持久化的跨代去重 memo 灌回 `_RESTARTS.seen`。返回灌入条数。

    一次性进程（CLI）专用：本模块的去重状态只活在进程内，调用方（TS 工具）把上次
    成功下发的 (nid, agent codeHash, 期望 hash) 存盘，下次调用前预置回来 ⇒ 守卫
    的 `dedup` 分支语义与常驻训练循环**逐字一致**，不必在调用方重写判据。
    每项须含 id / pingHash / expectedHash（全量 hex，不接受截断值）；可选 `atSec`
    （= 该次下发的 epoch 秒，调用方 memo 里就有这个时间）——冷却窗靠它判定「是否已过期」，
    **缺省 = 现在**（保持旧调用方的永久 dedup 语义不变，F3 向后兼容）。
    """
    n = 0
    now = time.time()
    for e in entries:
        if not isinstance(e, dict):
            continue
        nid = str(e.get("id") or "").strip()
        ping_hash = str(e.get("pingHash") or "").strip()
        exp_hash = str(e.get("expectedHash") or "").strip()
        if not nid or not ping_hash or not exp_hash:
            continue
        at = e.get("atSec")
        ts = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else now
        _RESTARTS.seen[nid] = (ping_hash, exp_hash, ts)
        n += 1
    return n


def request_upgrade_guarded(
    nid: str,
    url: str,
    auth_key: str,
    branch: str,
    ping_hash: str,
    timeout: float = 20.0,
    dirty: list[str] | None = None,
    expected_hash: str = "",
    cooldown_sec: float | None = None,
) -> tuple[bool, str]:
    """带护栏的重启请求（ping 门 / rescan / upgrade_stale_nodes 共用入口）。

    返回 (ok, reason)：
      restart-requested  — 已下发，节点将 pull + 重启（本轮生效；含**冷却窗过期后的重发**）
      dedup              — 同节点同 (agent codeHash, 期望 hash) 且**仍在冷却窗内**，跳过
                           （防连环杀；期望 hash 变化 ⇒ 立即允许再发一次，F1；
                            冷却窗过期 ⇒ 也允许再发一次，F3——否则 pull 失败/不支持远端
                            升级的节点会被永久压制，见 RESTART_DEDUP_COOLDOWN_SEC）
      dirty-tree:<n>     — 期望 hash 含 n 个未提交文件，远端 pull 永不收敛，拒发
      restart-failed     — agent 拒绝（409 grace / 5xx）或不可达；不写去重状态
                           （协调器下轮 rescan 自动重试）
    self/回环节点：永远**纯重启**（branch 强制空——共享工作区禁 pull），不受
    脏工作区护栏限制，去重同样生效。
    dirty 显式传参供测试与调用方复用每轮已检测结果；None = 自动检测。
    expected_hash 默认 "" 使未传参调用方行为与旧版逐字等价（向后兼容）；调用方
    必须显式传训练机当前 codeHash（F1 核心：dedup 键含期望值）。
    """
    if dirty is None:
        dirty = [] if is_self_node(url, nid) else dirty_hash_files()
    if not is_self_node(url, nid) and dirty:
        return False, f"dirty-tree:{len(dirty)}"
    key = (ping_hash, expected_hash)
    prev = _RESTARTS.seen.get(nid)
    # 冷却窗内 ⇒ dedup（防连环杀）；窗**已过**而节点仍 stale（pull 失败 / 环境不支持远端
    # 升级）⇒ 允许再发一次，并把时钟重置（下一窗内继续 dedup）。F3：旧行为是永久 dedup，
    # 这类节点会静默卡死到训练机出新提交为止（TS 工具的落盘 memo 还会跨调用压制）。
    if (
        prev is not None
        and prev[:2] == key
        and time.time() - prev[2] < restart_dedup_cooldown_sec(cooldown_sec)
    ):
        return False, "dedup"
    actual_branch = "" if is_self_node(url, nid) else branch
    ok = request_upgrade(url, auth_key, actual_branch, timeout=timeout)
    if ok:
        _RESTARTS.seen[nid] = (ping_hash, expected_hash, time.time())
    return ok, ("restart-requested" if ok else "restart-failed")


def upgrade_stale_nodes(
    cfg: dict,
    expected_hash: str,
    branch: str,
    status_timeout: float = 3.0,
    restart_timeout: float = 20.0,
    max_nodes: int = 16,
    dirty: list[str] | None = None,
    cooldown_sec: float | None = None,
) -> list[dict]:
    """主动升级扫描：ping 每个 enabled 节点，codeHash ≠ expected（stale）→
    request_upgrade_guarded（跨代去重 + 脏工作区拒发，2026-09-01 重启循环修复）。

    返回 [{id, upgraded, reason}]（诊断用）。不可达/失败跳过——升级机制是 best-effort，
    绝不阻塞本轮 dispatch（stale 节点本轮仍不参与，待重启后由 rescan 纳入）。
    """
    out: list[dict] = []
    for n in cfg.get("nodes", [])[:max_nodes]:
        if not n.get("enabled", True):
            continue
        nid = str(n.get("id") or n.get("url") or "?")
        ping = node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
        if ping is None:
            out.append({"id": nid, "upgraded": False, "reason": "unreachable"})
            continue
        ping_hash = str(ping.get("codeHash"))
        if ping_hash == expected_hash:
            out.append({"id": nid, "upgraded": False, "reason": "current"})
            continue
        ok, reason = request_upgrade_guarded(
            nid,
            n["url"],
            n.get("authKey", ""),
            branch,
            ping_hash,
            timeout=restart_timeout,
            dirty=dirty,
            expected_hash=expected_hash,
            cooldown_sec=cooldown_sec,
        )
        # pingHash 回传给调用方：一次性 CLI 要靠它把本次下发写进跨调用 memo
        # （键 = (nid, agent ping hash, 期望 hash)，与 `_RESTARTS.seen` 同构）。
        out.append(
            {
                "id": nid,
                "upgraded": ok,
                "reason": reason,
                "pingHash": ping_hash,
                "agentVersion": ping.get("agentVersion"),
            }
        )
    return out
