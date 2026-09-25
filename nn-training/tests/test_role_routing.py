"""归属角色（role）路由的门禁 —— 「该由哪块盘执行」是 **job 自己的属性**（2026-09-25）。

背景（事故 + 设计）：`plan/online-offline-role-routing.plan.md` +
`reports/online-offline-hot-switch-audit-2026-09-25.md`。旧口径把归属读成「课程当前 mode」
（易变：hub 内存表 + 控制台可热切）⇒ 切一次模式，历史 job 的归属就跳一次：两小时前因缺
bun 被拒的 `kind=run` job，在课程切成在线之后被另一块盘领走了。

本文件钉四层：

  ① **映射**（纯函数）：`KIND_ROLES` 覆盖 `MANIFEST_KINDS` 全集、`role_of` 的三段兜底、
     角色头解析（缺头 = online，旧 worker 行为逐字不变）。
  ② **发布**：`publish_job` 把归属**落成字段**（与 `kind` 同一份快照），显式覆盖是逃生口。
  ③ **认领**：闸住在 `_JobStore._claim_locked`（租约写入的唯一临界区）⇒ 三条认领面天然
     同源；另有一条**枚举式**源码断言把「第 5 条绕过闸的路径」钉死。
  ④ **推腿**：push 登记表里没有角色字段 ⇒ push worker 一律当在线盘，`role=offline` 的活
     一律不推（`push_dispatch._dispatch`）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    KIND_ROLES,
    MANIFEST_KINDS,
    ROLE_FIELD,
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
    ROLE_OFFLINE,
    ROLE_ONLINE,
    ROLES,
    ProtocolError,
    normalize_manifest,
    role_from_header,
    role_of,
)
from remote.hub_server import _HubQueue, _JobStore
from remote.push_dispatch import PushDispatcher
from tests.helpers.hub_poll import ROLE_HEADER as HELPER_ROLE_HEADER

JID = "j" * 16
OFF_JID = "k" * 16


def _manifest(jid: str = JID, **over: object) -> dict:
    """最小合法 job manifest（必填齐全，与 hub 的账本/租约无关——纯形状）。"""
    m: dict = {
        "proto": 1,
        "runId": "test-run",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }
    m.update(over)
    return m


def _store(tmp_path: Path) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")


# ═══════════════════════════ ① 映射（纯函数） ═══════════════════════════


def test_kind_role_map_covers_every_manifest_kind() -> None:
    """`KIND_ROLES` 必须**恰好**覆盖 `MANIFEST_KINDS`（加 kind 而没给归属 = 当场红）。

    这条断言是「未来加第 5 个 kind」的唯一防线：`role_of` 对未知 kind 会静默回落 online，
    而一个本该属于离线盘的活被静默归到在线盘，表现只是「它一直在队列里等人」。
    """
    assert set(KIND_ROLES) == set(MANIFEST_KINDS)
    assert len(MANIFEST_KINDS) == len(set(MANIFEST_KINDS))
    # 逐条冻结（顺序即语义：只有整段 run 属于离线盘）
    assert KIND_ROLES["run"] == ROLE_OFFLINE, "整段自主 = 离线盘的活"
    assert KIND_ROLES["iter"] == ROLE_ONLINE
    assert KIND_ROLES["ppo"] == ROLE_ONLINE
    assert KIND_ROLES["bc"] == ROLE_ONLINE, "BC job 由云机跑 train/bc.py ⇒ 在线盘的活"


@pytest.mark.parametrize(
    ("manifest", "want"),
    [
        ({"role": "offline", "kind": "ppo"}, ROLE_OFFLINE),  # 字段优先于 kind
        ({"role": "online", "kind": "run"}, ROLE_ONLINE),
        ({"role": " OFFLINE ", "kind": "ppo"}, ROLE_OFFLINE),  # 大小写/空白容忍
        ({"role": "garbage", "kind": "run"}, ROLE_OFFLINE),  # 非法值 ⇒ 回落 kind
        ({"kind": "run"}, ROLE_OFFLINE),  # 旧 job（无字段）⇒ 按 kind
        ({"kind": "iter"}, ROLE_ONLINE),
        ({"kind": "bc"}, ROLE_ONLINE),
        ({"kind": "nope"}, ROLE_ONLINE),  # 未知 kind ⇒ 保守 online（入口已挡，兜底面）
        ({}, ROLE_ONLINE),  # 连 kind 都没有
    ],
)
def test_role_of_prefers_field_then_kind(manifest: dict, want: str) -> None:
    """`role_of` 三段兜底：字段 → kind → online；**永不抛**（旧 job 不能因缺字段变孤儿）。"""
    assert role_of(manifest) == want


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("", ROLE_ONLINE),
        (None, ROLE_ONLINE),
        ("0", ROLE_ONLINE),
        ("false", ROLE_ONLINE),
        ("no", ROLE_ONLINE),
        ("online", ROLE_ONLINE),
        ("1", ROLE_OFFLINE),
        ("true", ROLE_OFFLINE),
        ("on", ROLE_OFFLINE),
        ("yes", ROLE_OFFLINE),
        ("offline", ROLE_OFFLINE),
        ("OFFLINE", ROLE_OFFLINE),
        (" offline ", ROLE_OFFLINE),
        ("maybe", ROLE_ONLINE),
    ],
)
def test_role_from_header_whitelist(raw: object, want: str) -> None:
    """只认白名单真值：判错的两个方向不对称（低估 = 少一个人领活，看得见；高估 = 隐形）。"""
    assert role_from_header(raw) == want


def test_role_header_literals_are_the_historic_offline_header() -> None:
    """头名/值**故意**不改名（混合部署里旧 hub/旧 worker 共用这份字面量）。

    语义变了（能力 → 归属）而名字不变，所以这条断言守的是「不许顺手改名」；测试助手
    `tests/helpers/hub_poll` 里那份独立字面量必须与生产同值。
    """
    assert ROLE_HEADER == "X-Battle-Offline"
    assert ROLE_HEADER_VALUE == "1"
    assert HELPER_ROLE_HEADER == ROLE_HEADER
    assert set(ROLES) == {ROLE_ONLINE, ROLE_OFFLINE}
    assert ROLE_FIELD == "role"


def test_normalize_manifest_keeps_role_and_rejects_garbage() -> None:
    """字段可选（旧 job 没有它）但**一旦存在必须合法**：拼错静默变 online 正是隐形失败。"""
    assert normalize_manifest(_manifest(role="offline"))["role"] == "offline"
    assert "role" not in normalize_manifest(_manifest()), "缺席不注入默认（否则第二个事实源）"
    with pytest.raises(ProtocolError) as e:
        normalize_manifest(_manifest(role="offilne"))  # 拼错的
    assert "role" in str(e.value)


# ═══════════════════════════ ② 发布：归属落成字段 ═══════════════════════════


def test_publish_writes_role_from_the_kind_snapshot(tmp_path: Path) -> None:
    """`kind=run` 的 job ⇒ manifest（含**盘上那份**）带 `role=offline`。

    为什么断言盘上那份：hub 的闸读的是 `job_root/<id>/manifest.json`，不是内存里的返回值。
    """
    from tests.test_run_segment import _publish as _publish_run

    m, _plan = _publish_run(tmp_path, n=2)
    assert m["kind"] == "run"
    assert m[ROLE_FIELD] == ROLE_OFFLINE
    on_disk = json.loads(
        (tmp_path / "jobs" / m["job_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert on_disk[ROLE_FIELD] == ROLE_OFFLINE
    assert role_of(on_disk) == ROLE_OFFLINE


def test_publish_writes_online_role_for_iter_jobs(tmp_path: Path) -> None:
    """`kind=iter`（整轮上云，节点只做一轮）⇒ 在线盘的活。"""
    from tests.test_remote_iter import _publish_iter

    m, _ = _publish_iter(tmp_path)
    assert m["kind"] == "iter"
    assert m[ROLE_FIELD] == ROLE_ONLINE


def test_publish_role_override_is_honored(tmp_path: Path) -> None:
    """显式 `role=` 覆盖 kind 推导（离线盘手工排活 / 将来加 kind 时的逃生口）。"""
    from tests.test_run_segment import _publish as _publish_run

    m, _ = _publish_run(tmp_path, n=2, role="online")
    assert m["kind"] == "run" and m[ROLE_FIELD] == ROLE_ONLINE
    # 非法的覆盖值**不**静默生效（回落 kind 快照，而不是把垃圾写进字段）
    m2, _ = _publish_run(tmp_path, n=2, role="nonsense")
    assert m2[ROLE_FIELD] == ROLE_OFFLINE


# ═══════════════════════════ ③ 认领：单一咽喉点 ═══════════════════════════


def test_role_gate_lives_in_the_lease_critical_section() -> None:
    """闸必须住在 `_JobStore._claim_locked`（租约写入的唯一临界区）。

    push 腿（`push_dispatch` → `Hub.claim`）**不经过** `claim_job`——闸若写在各个调用点，
    必然漏掉 push 这一条（这就是本设计把闸下沉的理由）。
    """
    # ★ 位置（2026-09-25 并入 `origin/goal-nn`）：`_JobStore` 随 S4 第十四/十五刀拆进
    # `remote/hub/store*.py` —— `_claim_locked` 与 `role_blocked` 现在住 `store_leases.py`。
    # 断言本身钉的是**形状**（闸在临界区里、两道闸共用一份判据），不是文件名。
    src = (ROOT / "remote" / "hub" / "store_leases.py").read_text(encoding="utf-8")
    i = src.index("def _claim_locked(")
    body = src[i : i + 8000]
    assert "blocked = self.role_blocked(job_id, role)" in body
    assert 'return False, "", blocked' in body
    # 两道闸共用一份判据（归属 = job 的字段；停摆 = 课程模式）――查一处就够了
    j = src.index("def role_blocked(")
    gate = src[j : j + 1600]
    assert "if self.parked and role != ROLE_OFFLINE" in gate
    assert "if self.job_role(job_id) != role" in gate


def test_parking_flag_is_synced_from_course_mode(tmp_path: Path) -> None:
    """停摆位随 `set_mode` 热切（课程级闸的**唯一**输入）——否则重启/热切后离线课变成可领。"""
    store = _JobStore(tmp_path / "c5-gae" / "remote-jobs", tmp_path / "c5-gae" / "log.jsonl")
    store.publish(JID, _manifest(JID), b"PK\x03\x04fake")
    hub = _HubQueue({"c5-gae": store}, order=["c5-gae"])
    assert store.parked is False
    assert hub.set_mode("c5-gae", "offline") is True
    assert store.parked is True, "热切没同步到 store ⇒ 停摆闸静默失效"
    # 停摆：离线盘也领不到**在线归属**的活（归属闸仍在）——两道闸正交
    assert hub.claim_next(worker_id="off", role=ROLE_OFFLINE) is None
    # 同一门课切成在线 ⇒ 立刻可领（且无人丢失过 job）
    assert hub.set_mode("c5-gae", "online") is True
    assert store.parked is False
    got = hub.claim_next(worker_id="on", role=ROLE_ONLINE)
    assert got is not None and got[1] == JID


def test_store_gate_rejects_role_mismatch_without_touching_the_lease(tmp_path: Path) -> None:
    """拒绝必须是**确定性**的（不是 409 busy）且**零副作用**：不写租约、不占 owner。"""
    store = _store(tmp_path)
    store.publish(OFF_JID, _manifest(OFF_JID, kind="run"), b"PK\x03\x04fake")
    out = store.claim_outcome(OFF_JID, worker_id="online-disk", role=ROLE_ONLINE)
    assert out.ok is False and out.status == "role"
    assert store._leases == {} and store._lease_owners == {}, "拒了就不许留痕"
    assert store.claimable_job_ids() == [OFF_JID], "还回池里等对的人"
    ok = store.claim_outcome(OFF_JID, worker_id="offline-disk", role=ROLE_OFFLINE)
    assert ok.ok is True and ok.status == "ok"


def test_job_role_cache_is_invalidated_by_republish(tmp_path: Path) -> None:
    """重发覆盖 manifest ⇒ 归属缓存必须跟着失效（否则闸按**旧**归属放行/拦截）。"""
    store = _store(tmp_path)
    store.publish(JID, _manifest(JID, kind="run"), b"PK\x03\x04fake")
    assert store.job_role(JID) == ROLE_OFFLINE
    store.publish(JID, _manifest(JID, role=ROLE_ONLINE, kind="run"), b"PK\x03\x04fake")
    assert store.job_role(JID) == ROLE_ONLINE, "缓存吐了谎"


#: 生产代码里所有「进 `_JobStore` 领活」的调用点（枚举式：出现第 5 条路径就红）。
_PAID_CALL = re.compile(
    r"\b[a-zA-Z_][\w.]*\.(?P<callee>claim|claim_outcome|claim_job|claim_next)\("
)
#: ⚠ 文件名列的是**当前布局**（S4 第十五刀把 `_HubQueue` 拆进 `remote/hub/queue_*.py`）：
#: 名字是契约，位置不是 —— 搬家时改这张表，别改判据。
_EXPECTED_CALL_SITES: dict[tuple[str, str], int] = {
    # 两条腿都住 `hub/queue_claims.py`：`claim_next`（多课程轮转挑选）+ `Hub.claim`（push 派发腿）
    ("remote/hub/queue_claims.py", "claim"): 2,
    # `Hub.claim_job`（HTTP `POST /jobs/{id}/claim` 的唯一实现入口）
    ("remote/hub/queue_claims.py", "claim_outcome"): 1,
    # HTTP 面本体（路由混入 `schedule.py` 的 claim handler）
    ("remote/hub/schedule.py", "claim_job"): 1,
    # push 派发
    ("remote/push_dispatch.py", "claim"): 1,
}

#: 认领调用点的扫描面：**整个** `remote/hub/` 目录 + push 派发腿。
#:
#: 为什么是目录而不是三个文件名：枚举式判据的强度全在扫描面上——只列已知文件，第 5 条路径
#: 只要落在一个新拆出来的 `hub/*.py` 里就不见了（而它的表现是静默的：活被错的盘领走）。
_CALL_SITE_MODULES = (
    *sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in (ROOT / "remote" / "hub").glob("*.py")
    ),
    "remote/push_dispatch.py",
)


def _call_text(src: str, start: int) -> str:
    """取这次调用的完整实参文本（括号配平）——用来断言「这一跳带了 role」。"""
    i = src.index("(", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    raise AssertionError("调用括号不配平（源码被改动过？）")


def test_claim_paths_are_enumerated_and_all_carry_role() -> None:
    """枚举式断言：认领路径**只有**这几条，且每条都把归属透进临界区。

    加第 5 条路径（新的调用点、新的传输腿）时必须回来改这张表——否则它会绕过归属闸，
    而「绕过」的表现是静默的：活被错的盘领走，日志上一切正常。
    """
    found: dict[tuple[str, str], list[str]] = {}
    for rel in _CALL_SITE_MODULES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        for m in _PAID_CALL.finditer(src):
            found.setdefault((rel, m.group("callee")), []).append(_call_text(src, m.start()))
    got = {k: len(v) for k, v in found.items()}
    assert got == _EXPECTED_CALL_SITES, (
        f"认领调用点集合变了：得到 {sorted(got.items())}，期望 {sorted(_EXPECTED_CALL_SITES.items())}"
        "——新路径必须自己把 role 送进 `_claim_locked`（见本文件头的 ③）"
    )
    for key, texts in found.items():
        for t in texts:
            assert "role=" in t, f"{key[0]}:{key[1]} 这一跳没带 role ⇒ 绕过归属闸：{t}"


def test_claim_next_partitions_courses_by_job_role(tmp_path: Path) -> None:
    """`claim_next` 按 job 的归属分区；`queue_state` 把归属报出来（事后能对账）。"""
    store = _JobStore(tmp_path / "c5-gae" / "remote-jobs", tmp_path / "c5-gae" / "log.jsonl")
    store.publish(JID, _manifest(JID), b"PK\x03\x04fake")  # 无 kind ⇒ online
    store.publish(OFF_JID, _manifest(OFF_JID, kind="run"), b"PK\x03\x04fake")  # 离线盘
    hub = _HubQueue({"c5-gae": store}, order=["c5-gae"])

    got_on = hub.claim_next(worker_id="online-disk", role=ROLE_ONLINE)
    assert got_on is not None and got_on[1] == JID, "在线盘只能拿到在线盘的活"
    assert hub.claim_next(worker_id="online-disk", role=ROLE_ONLINE) is None
    got_off = hub.claim_next(worker_id="offline-disk", role=ROLE_OFFLINE)
    assert got_off is not None and got_off[1] == OFF_JID, "离线盘拿到整段"

    st = hub.queue_state()
    assert st["courses"]["c5-gae"]["roles"] == {}, "两份都已被领走（pending 里没了）"


def test_queue_state_reports_pending_roles(tmp_path: Path) -> None:
    """待领池里两类归属并存时（模式刚热切过的常态），观测面必须说清「谁在等谁」。"""
    store = _JobStore(tmp_path / "c5-gae" / "remote-jobs", tmp_path / "c5-gae" / "log.jsonl")
    store.publish(JID, _manifest(JID), b"PK\x03\x04fake")
    store.publish(OFF_JID, _manifest(OFF_JID, kind="run"), b"PK\x03\x04fake")
    hub = _HubQueue({"c5-gae": store}, order=["c5-gae"])
    assert hub.queue_state()["courses"]["c5-gae"]["roles"] == {
        JID: ROLE_ONLINE,
        OFF_JID: ROLE_OFFLINE,
    }


# ═══════════════════════════ ④ 推腿 ═══════════════════════════


def _disp(hub: _HubQueue, ws) -> PushDispatcher:
    return PushDispatcher(hub, ws, "sekret", poll_sec=0.02, timeout_sec=30.0, log=lambda _m: None)


def test_push_leg_never_pushes_an_offline_role_job(tmp_path: Path, worker_factory) -> None:
    """push 登记表里没有角色字段 ⇒ push worker 一律当在线盘：整段 job **不推**。

    对照组就在同一条用例里（把同一份活的归属改回在线 ⇒ 立刻推得出去）：否则「没推」
    也可能只是装置没打拍，而这个用例的意义正是「推得出去的东西**故意**不推」。
    """
    from tests.helpers.push_worker import PAYLOAD
    from tests.test_hub_push_dispatch import _hub, _publish, _pump, _workers_with

    w1 = worker_factory(complete=False)
    hub = _hub(tmp_path, ["x2"])
    m = _publish(hub, "x2", JID, dispatch="push")
    store = hub._stores["x2"]
    # 同一份活改成「整段」（离线盘的活）：kind=run ⇒ role=offline（重发覆盖 manifest）
    store.publish(JID, {**m, "kind": "run"}, PAYLOAD)
    assert store.job_role(JID) == ROLE_OFFLINE

    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _disp(hub, ws)
    try:
        assert not _pump(disp, lambda: bool(w1.received), timeout=0.6), (
            "离线盘的活被推给了在线 push worker"
        )
        # 对照组：换回在线归属（同一 job_id / 同一 worker / 同一 kind=ppo 形状）⇒ 推得出去
        store.publish(JID, {**m, ROLE_FIELD: ROLE_ONLINE}, PAYLOAD)
        assert store.job_role(JID) == ROLE_ONLINE
        assert _pump(disp, lambda: bool(w1.received)), "对照组没推出去 ⇒ 上面的「没推」不算证据"
    finally:
        disp.stop()
