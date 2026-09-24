"""test_multi_course_hub —— 多课程单 hub（2026-09-18 用户指令）。

目标形状：**一个 hub 进程服务所有并行课程**，而磁盘布局逐字节不变（每课程仍是
`tmp/<course>/remote-jobs` + `training_log.jsonl`）。本文件钉住四件跨课程的事：

  ① **路由**：`/jobs/{id}/...` 按 job_id 找归属课程（单课程时课程名是空串——
     空串与「找不到」必须靠 None 区分，本文件有专门一条回归）；
  ② **队形**：每课程一条 FIFO + **跨课程轮转**（否则一门积压的课饿死其它课程）；
  ③ **超时换 worker**：租约过期 → 回队首并**避开**那位死掉的持有人（只剩一个
     活跃 worker 时必须允许自领，否则集群停摆）；
  ④ **竞速口径**：`在派发课程数 < 活跃 worker 数` 才对每门课的最新 job 广播；
     离线课程不实时派发（只收回传），也不计入分母。

单课程等价性（`as_hub`）不在这里重复钉——它由既有 100 个 hub 用例守着（那些文件
一行未改，正是本改造的验收条件之一）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _HubQueue, _JobStore, as_hub, make_server
from remote.protocol import (
    AUTH_HEADER,
    CLAIM_TTL_SEC,
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    WORKER_ID_HEADER,
    ProtocolError,
    has_offline_capability,
    may_avoid_stale_holder,
    normalize_manifest,
    parse_course_arg,
    rotation_order,
)
from tests.helpers.hub_poll import hub_poll
from tests.subproc_util import spawn_bound_port


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把**权重归档根**指到 tmp（2026-09-23）。

    回传轮会往归档根写 `<课>.it<N>.<时间戳>.json`（用户口径的 ③：让控制台的 evalA 选择器
    看得见回传段的轮次）。工装用例若往真 `nn-training/weights/` 撒这些文件，它们会被
    `eval-board/ckpts.ts` 当成**真训练轮次**列出来 —— 所以每个碰补传的测试文件都要隔离。
    用 env 而不是 patch 模块常量：e2e 那条是**真子进程**，patch 传不进去。
    """
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


# ------------------------------------------------------------------ 纯函数


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("tiny-a", ("tiny-a", COURSE_MODE_ONLINE)),
        ("tiny-a=offline", ("tiny-a", COURSE_MODE_OFFLINE)),
        (" tiny-a = OFFLINE ", ("tiny-a", COURSE_MODE_OFFLINE)),
        ("x1-rebirth-a2", ("x1-rebirth-a2", COURSE_MODE_ONLINE)),
        ("tiny-a=", ("tiny-a", COURSE_MODE_ONLINE)),  # 空模式 = online（不是错误）
    ],
)
def test_parse_course_arg(raw: str, want: tuple[str, str]) -> None:
    assert parse_course_arg(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", "=offline", "../x", "a/b", "a\\b", "a b", "..", ".", "a=bogus"])
def test_parse_course_arg_rejects(raw: str) -> None:
    """课程名进磁盘路径 ⇒ 非法名必须在这里响亮拒启，不能等落盘才发现写歪了。"""
    with pytest.raises(ProtocolError):
        parse_course_arg(raw)


def test_rotation_order_starts_after_last_dispatch() -> None:
    """轮转从上次派发的下一门开始；起点不在表里（首次/课程被摘）→ 原序。"""
    assert rotation_order(["a", "b", "c"], None) == ["a", "b", "c"]
    assert rotation_order(["a", "b", "c"], "a") == ["b", "c", "a"]
    assert rotation_order(["a", "b", "c"], "c") == ["a", "b", "c"]
    assert rotation_order(["a", "b"], "zz") == ["a", "b"]


def test_may_avoid_stale_holder_needs_a_spare_worker() -> None:
    """避让的闸：请求者有身份 + 还有别的活跃 worker 可接手（否则停摆）。

    身份**是否真是前持有人**由 store 判（只有它知道租约回收后的 stale 记录），
    对应回归见 `test_expired_lease_goes_to_another_worker`。
    """
    assert may_avoid_stale_holder("w1", 2) is True
    assert may_avoid_stale_holder("w1", 1) is False, "独苗必须能自领，否则谁都不干"
    assert may_avoid_stale_holder("", 3) is False, "身份未知不避让（旧 worker/手写 curl）"
    assert may_avoid_stale_holder("w1", 0) is False, "一个活跃 worker 都没有时不避让"


# ------------------------------------------------------------------ 夹具


class _Clock:
    """可拨动的假时钟（租约过期必须能测，不许 sleep 300s）。"""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


def _manifest(jid: str, *, run: str = "run", it: int = 1) -> dict:
    return normalize_manifest(
        {
            "proto": 1,
            "runId": run,
            "it": it,
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
            "epochs": 2,
            "mb": 512,
            "lr": 3e-4,
            "init_weights_fp": "w" * 64,
            "data_fp": "d" * 64,
            "payload_sha256": "p" * 64,
        }
    )


def _hub(
    tmp_path: Path,
    courses: tuple[str, ...] = ("a", "b"),
    offline: tuple[str, ...] = (),
    clock: _Clock | None = None,
) -> _HubQueue:
    stores = {
        c: _JobStore(
            tmp_path / c / "remote-jobs",
            tmp_path / c / "training_log.jsonl",
            now_fn=clock,
        )
        for c in courses
    }
    modes = {c: (COURSE_MODE_OFFLINE if c in offline else COURSE_MODE_ONLINE) for c in courses}
    return _HubQueue(stores, order=list(courses), modes=modes, now_fn=clock)


def _publish(hub: _HubQueue, course: str, jid: str, *, run: str = "run", it: int = 1) -> None:
    st = hub._stores[course]
    st.publish(jid, _manifest(jid, run=run, it=it), b"PK\x03\x04fake")


def _claim(
    hub: _HubQueue, worker: str, *, offline_ok: bool = False
) -> tuple[str, str, str]:
    """取下一份该派的 job；**没有必须是失败**（否则断言会变成静默跳过）。"""
    picked = hub.claim_next(worker_id=worker, offline_ok=offline_ok)
    assert picked is not None, f"应当有可派给 {worker} 的 job"
    return picked


# ------------------------------------------------------------------ 离线课的能力闸（2026-09-19）
#
# 离线课（`kind=run` 整段）不实时派发，但**不是**谁都领不到的坟墓：用户口径「也支持带
# 特别标识的云端 worker 在线领取」——标识语义 = 能力（「我能自己跑完整段」），不是课程绑定。
# 判错的方向是刻意选的：低估只是少一个 worker 领离线课（队列可见地不降），高估会让只会
# 逐轮的 worker 搬走整段 job 并在那儿卡到租约超时（不可见）。


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        (" 1 ", True),
        ("", False),
        (None, False),
        ("0", False),
        ("false", False),
        ("2", False),
        ("offline", False),  # 只认白名单真值：能力名写进来不算声明
    ],
)
def test_offline_capability_header_truth_table(raw: object, want: bool) -> None:
    assert has_offline_capability(raw) is want


def test_offline_course_needs_capability(tmp_path: Path) -> None:
    """普通 worker 领不到离线课；带标（自报能跑整段）的领得到。"""
    hub = _discover_hub(tmp_path)
    _publish_standalone(tmp_path, "c1", "j" * 16)
    hub.discover()
    assert hub.set_mode("c1", COURSE_MODE_OFFLINE) is True

    assert hub.claim_next(worker_id="w1") is None, "空头 = 无能力 ⇒ 不得领离线课"
    assert hub.claim_next(worker_id="w1", offline_ok=False) is None
    course, jid, _tok = _claim(hub, "w1", offline_ok=True)
    assert (course, jid) == ("c1", "j" * 16)
    # 租约在持：同一份不会被第二个带标 worker 再领一次（那是同一段跑两遍）
    assert hub.claim_next(worker_id="w2", offline_ok=True) is None


def test_marked_worker_still_claims_online_courses(tmp_path: Path) -> None:
    """课程与 worker **正交**（用户 2026-09-19 再强调）：带标 worker 照样领在线课。

    标识是「我能跑完整段」的能力声明，不是「我只服务离线课」的归属——把它做成归属，就会
    重新制造「某门课钉到某台机器」的耦合（R4 刚拆掉的那种）。
    """
    hub = _discover_hub(tmp_path)
    _publish_standalone(tmp_path, "c-online", "a" * 16)
    _publish_standalone(tmp_path, "c-offline", "b" * 16)
    hub.discover()
    assert hub.set_mode("c-offline", COURSE_MODE_OFFLINE) is True

    seen = set()
    for _ in range(2):
        course, _jid, _tok = _claim(hub, "w1", offline_ok=True)
        seen.add(course)
    assert seen == {"c-online", "c-offline"}, f"带标 worker 应两门课都能领，实得 {seen}"


def _boot(tmp_path: Path, hub: _HubQueue) -> tuple:
    srv = make_server(hub, 0, "sekret", host="127.0.0.1")
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", hub, srv, th


def _http_raw(base: str, path: str) -> tuple[int, bytes]:
    """取**二进制**端点（payload/blob）：`_http` 会 json.loads，对 zip 体直接爆。"""
    req = urllib.request.Request(
        base + path, headers={AUTH_HEADER: "Bearer sekret"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _http(
    base: str,
    path: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={AUTH_HEADER: "Bearer sekret", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def _next(base: str, *, worker: str = "") -> dict:
    """旧轮询面的同形替代（peek + claim；实现见 `tests/helpers/hub_poll`）。"""
    got = hub_poll(base, "sekret", worker_id=worker)
    if got is None or not got.get("job_id"):
        return {"job_id": None, "halt": bool(got and got.get("halt"))}
    return got


# ------------------------------------------------------------------ 路由


def test_course_of_uses_none_for_missing_not_empty_string(tmp_path: Path) -> None:
    """回归：单课程队列的课程名**就是空串**，所以「找不到」必须是 None。

    2026-09-18 实测故障：`course_of` 用空串兼作缺失值 ⇒ 单课程下刚派出的
    job 立刻解析不到归属，handler 打到哨兵路径上 500（每一次拉活都失败）。
    """
    hub = _hub(tmp_path, courses=("",))
    _publish(hub, "", "j" * 16)
    assert hub.course_of("j" * 16) == "", "找到、但名字是空串"
    assert hub.course_of("x" * 16) is None, "找不到 = None（不是空串）"
    assert (hub._job_dir("x" * 16) / "manifest.json").exists() is False


def test_unknown_job_is_not_dispatched_or_accepted(tmp_path: Path) -> None:
    """不归本 hub 的 job_id：状态 404、结果拒收（否则会写出一个无归属的 result）。"""
    hub = _hub(tmp_path)
    assert hub.claimable_job_ids("a") == []
    assert hub.result_token_ok("nope", "") is False
    assert hub.store_result("nope", {"x": 1}) is False


def test_jobs_route_to_their_own_course_store(tmp_path: Path) -> None:
    """两门课的 job 各进各家账本/目录，互不可见（这是多课程不串账的底线）。"""
    hub = _hub(tmp_path)
    _publish(hub, "a", "a" * 16, run="run-a")
    _publish(hub, "b", "b" * 16, run="run-b")
    assert hub.course_of("a" * 16) == "a" and hub.course_of("b" * 16) == "b"
    assert hub.claimable_job_ids("a") == ["a" * 16]
    assert hub.claimable_job_ids("b") == ["b" * 16]
    # 账本事件各写各的
    la = (tmp_path / "a" / "training_log.jsonl").read_text(encoding="utf-8")
    lb = (tmp_path / "b" / "training_log.jsonl").read_text(encoding="utf-8")
    assert "run-a" in la and "run-b" not in la
    assert "run-b" in lb and "run-a" not in lb


# ------------------------------------------------------------------ 队形


def test_dispatch_round_robins_across_courses(tmp_path: Path) -> None:
    """跨课程轮转：A 的 backlog 不许把 B 饿死（顺序 A,B,A 而不是 A,A,B）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)  # 假时钟：发布 ts 必须有序（同毫秒会平局）
    _publish(hub, "a", "a1" + "0" * 14, it=1)
    clock.tick(1)
    _publish(hub, "a", "a2" + "0" * 14, it=2)
    clock.tick(1)
    _publish(hub, "b", "b1" + "0" * 14, it=1)
    got: list[str] = []
    for _ in range(3):
        got.append(_claim(hub, f"w{len(got)}")[0])
    assert got == ["a", "b", "a"], f"轮转失效：{got}"


def test_offline_course_is_not_dispatched(tmp_path: Path) -> None:
    """离线课不实时派发 PPO（用户口径），但它的 job 仍在盘上、账本照记。"""
    hub = _hub(tmp_path, offline=("b",))
    _publish(hub, "b", "b" * 16)
    assert hub.claim_next(worker_id="w1") is None, "离线课不得被派出去"
    assert hub.claimable_job_ids("b") == ["b" * 16], "job 仍在（只是不派）"
    # 在线课照常
    _publish(hub, "a", "a" * 16)
    assert _claim(hub, "w1")[:2] == ("a", "a" * 16)


def test_active_courses_excludes_offline_and_idle(tmp_path: Path) -> None:
    """竞速判据的分母：非离线 + 有活（待领或在飞）。空转的课不算。"""
    hub = _hub(tmp_path, courses=("a", "b", "c"), offline=("c",))
    assert hub.active_courses() == 0, "谁都没活"
    _publish(hub, "a", "a" * 16)
    assert hub.active_courses() == 1
    _publish(hub, "c", "c" * 16)  # 离线课即便有活也不计
    assert hub.active_courses() == 1
    _publish(hub, "b", "b" * 16)
    assert hub.active_courses() == 2
    # 领走 A 之后它仍在飞 ⇒ 仍算「在派发」
    _claim(hub, "w1")
    assert hub.active_courses() == 2


# ------------------------------------------------------------------ 超时换 worker


def test_expired_lease_goes_to_another_worker(tmp_path: Path) -> None:
    """超时回落队首并**改为派给别的 worker**（用户口径），且不还给死掉的那位。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)
    _publish(hub, "a", "a" * 16)
    # 两个 worker 都报过到（否则「独苗可自领」规则会放行 w1）
    hub.note_worker("w1")
    hub.note_worker("w2")
    first = _claim(hub, "w1")
    assert first[:2] == ("a", "a" * 16)
    assert hub.claim_next(worker_id="w2") is None, "租约未过期 ⇒ 没人能抢"
    clock.tick(CLAIM_TTL_SEC + 1)
    # 活着的 worker 会持续轮询（协议就是它们每几秒打一次轮询面），所以「活跃」要按
    # 真实节奏刷一遍。不刷的话两台都被当成已离场 ⇒ 活跃数 0 ⇒ 避让闸不开（这是对的：
    # 连一台活着的都没有时，避让只会让这活没人干）。租约 TTL(300s) 比
    # WORKER_SEEN_WINDOW_SEC(180s) 长，就是为此——一个 worker 停 poll 超窗口即可判离场。
    hub.note_worker("w1")
    hub.note_worker("w2")
    assert hub.claim_next(worker_id="w1") is None, "刚跑死它的那台得让位"
    second = _claim(hub, "w2")
    assert second[1] == "a" * 16, "换一台 worker 就该给它"


def test_expired_lease_self_claim_allowed_when_sole_worker(tmp_path: Path) -> None:
    """独苗必须能自领自己超时过的活——否则那台 worker 永远空转（集群停摆）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)
    _publish(hub, "a", "a" * 16)
    hub.note_worker("only")
    _claim(hub, "only")
    clock.tick(CLAIM_TTL_SEC + 1)
    assert _claim(hub, "only")[1] == "a" * 16


def test_release_clears_avoidance(tmp_path: Path) -> None:
    """主动还租约 = 不是「跑死了」⇒ 不该被避让（否则 worker 瞬时失败还了活就再也领不回来）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)
    _publish(hub, "a", "a" * 16)
    hub.note_worker("w1")
    hub.note_worker("w2")
    picked = _claim(hub, "w1")
    assert hub.release(picked[1], picked[2]) is True
    assert _claim(hub, "w1")[1] == "a" * 16, "还租约后自己该能立刻重新领到"


# ------------------------------------------------------------------ 竞速（多课程口径）


# ------------------------------------------------------------------ HTTP 全链路


def test_http_serves_all_courses_and_reports_course(tmp_path: Path) -> None:
    """一个进程、一个端口服务两门课：轮询面轮流给，并在响应里自报 course。

    独占口径：每门课各拿一条卡（租约在持不重发）。
    """
    hub = _hub(tmp_path)
    _publish(hub, "a", "a" * 16)
    _publish(hub, "b", "b" * 16)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        seen = []
        for w in ("w1", "w2", "w1"):
            body = _next(base, worker=w)
            if body["job_id"]:
                seen.append(body["course"])
        assert seen == ["a", "b"], f"独占轮转跨课失效：{seen}"
        # 第二门课的 payload 能按 job_id 反查归属取到（worker 不需要知道课程）
        st, body_b = _http_raw(base, f"/jobs/{'b' * 16}/payload")
        assert st == 200 and body_b == b"PK\x03\x04fake", (st, body_b[:16])
        # 未知 job → 404（不是 500、也不是别的课的 job）
        st, _ = _http_raw(base, f"/jobs/{'x' * 16}/payload")
        assert st == 404
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_admin_queue_and_courses_surfaces(tmp_path: Path) -> None:
    """/admin/queue（每课深度/在飞/轮转游标）与 /admin/courses（含热切离线）。"""
    hub = _hub(tmp_path)
    _publish(hub, "a", "a" * 16)
    _publish(hub, "b", "b1" + "0" * 14)
    _publish(hub, "b", "b2" + "0" * 14)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        st, q = _http(base, "/admin/queue")
        assert st == 200
        assert q["courses"]["a"]["pending_n"] == 1
        assert q["courses"]["b"]["pending_n"] == 2
        assert q["courses"]["b"]["next_job"] == "b1" + "0" * 14, "FIFO：队首是先生成的"
        assert q["offline"] == [] and q["order"] == ["a", "b"]

        st, c = _http(base, "/admin/courses")
        assert st == 200 and [x["course"] for x in c["courses"]] == ["a", "b"]
        # 热切 b 为离线 ⇒ 立刻不派，但它仍在队列里
        st, r = _http(base, "/admin/courses?course=b&mode=offline", method="POST")
        assert st == 200 and r["mode"] == "offline"
        st, q2 = _http(base, "/admin/queue")
        assert q2["offline"] == ["b"] and q2["courses"]["b"]["mode"] == "offline"
        assert q2["courses"]["b"]["pending_n"] == 2, "改模式不动队列内容"
        # 非法课程/模式 → 400 且不改现状
        for bad in ("?course=zz&mode=offline", "?course=b&mode=bogus"):
            st, _r = _http(base, "/admin/courses" + bad, method="POST")
            assert st == 400, bad
        st, q3 = _http(base, "/admin/queue")
        assert q3["offline"] == ["b"]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_mode_post_discovers_the_course_on_demand(tmp_path: Path) -> None:
    """`POST /admin/courses` 指名的课**刚建好目录、扫描还没轮到**时，也必须靠按需真扫接住。

    2026-09-23 用户报障（真机日志）：共享 hub 刚重启（`courses=[]`）——控制台那份「离线意图
    回灌」跑在第一次顺带扫描**之前**，九条 POST 全 400；随后三个离线课各自靠「开课时有界
    重试（3×2s）」去赌发现时机，**恰有一门输掉**（最后一次重试 20:29:46、发现也 20:29:46）
    ⇒ 该课静默留在 online，面板一直显示「在训 / 切离线」，而操作员以为自己开的是离线课。
    修法：POST 只在「课不在表里」时跳间隔闸真扫一次再试（模式非法不白扫盘）。
    """
    clock = _Clock()
    hub = _discover_hub(tmp_path, clock)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        # 第一次顺带扫描之后才开课（模拟「刚建好 remote-jobs/」）——此刻闸还没过期
        _mk_course_dir(tmp_path, "late")
        clock.tick(hub.DISCOVER_SCAN_MIN_SEC / 2)
        assert hub.courses() == []
        st, r = _http(base, "/admin/courses?course=late&mode=offline", method="POST")
        assert st == 200, r
        assert r["mode"] == "offline"
        assert hub.courses() == ["late"] and hub.mode_of("late") == COURSE_MODE_OFFLINE
        st, q = _http(base, "/admin/queue")
        assert q["offline"] == ["late"]
        # 真不存在的课仍然 400（按需发现不是「什么都接受」），且不改变课程表
        st, _r = _http(base, "/admin/courses?course=ghost&mode=offline", method="POST")
        assert st == 400 and hub.courses() == ["late"]
        # 模式非法不用扫盘：直接 400
        st, _r = _http(base, "/admin/courses?course=late&mode=bogus", method="POST")
        assert st == 400 and hub.mode_of("late") == COURSE_MODE_OFFLINE
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_halt_is_per_course(tmp_path: Path) -> None:
    """停机达令**按课程**（单 hub 化的关键副作用）。

    一个 hub 服务所有并行课程之后，达令若是进程级一个布尔，「A 课门禁 ABORT」会把 B 课的
    云机一起停掉（worker 下一轮轮询就拿到 halt 并自停）。故：达令跟着**这份活所属的课**走；
    无课程参数的 halt/resume 保持旧语义（全课程）。
    """
    hub = _hub(tmp_path)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        # 全课程（旧语义：不带 course 参数，体形状与既有用例/console 一致）
        st, r = _http(base, "/admin/workers/halt")
        assert st == 200 and r == {"halt": True}
        assert hub.halt_of("a") is True and hub.halt_of("b") is True
        st, r = _http(base, "/admin/workers/resume")
        assert st == 200 and r == {"halt": False}
        assert hub.halt_of("a") is False and hub.halt_of("b") is False

        # 只停 b（共享 hub 上这才是门禁 ABORT 的真实语义）
        st, r = _http(base, "/admin/workers/halt?course=b")
        assert st == 200 and r == {"halt": True, "course": "b"}
        assert hub.halt_of("b") is True and hub.halt_of("a") is False

        # 达令跟**活所属的课**走：a 的 job 不带 halt，b 的带
        _publish(hub, "a", "a" * 16)
        _publish(hub, "b", "b" * 16)
        ra = _next(base, worker="w1")
        assert (ra["course"], ra["halt"]) == ("a", False), ra
        rb = _next(base, worker="w2")
        assert (rb["course"], rb["halt"]) == ("b", True), rb

        # 空闲轮询没有课程上下文 ⇒ 全部课都停才告诉它停（否则 idle worker 会被凭空停掉）
        idle = _next(base, worker="w3")
        assert idle["job_id"] is None and idle["halt"] is False
        _http(base, "/admin/workers/halt?course=a")
        idle2 = _next(base, worker="w3")
        assert idle2["halt"] is True

        # 单课程读取：`?course=` → 那一门课；体形状恒为 {"halt": ...}（console 读它）
        _st, sb = _http(base, "/admin/workers/status?course=b")
        assert sb == {"halt": True}
        _st, sa = _http(base, "/admin/workers/status?course=a")
        assert sa == {"halt": True}
        _http(base, "/admin/workers/resume?course=b")
        _st, sb2 = _http(base, "/admin/workers/status?course=b")
        _st, sa2 = _http(base, "/admin/workers/status?course=a")
        assert sb2 == {"halt": False} and sa2 == {"halt": True}, "解停只动点名的那一课"

        # 未知课程 → 400 且点名（不猜、不静默改写全局）
        st, bad = _http(base, "/admin/workers/halt?course=zz")
        assert st == 400 and bad["course"] == "zz"
        _st, sa3 = _http(base, "/admin/workers/status?course=a")
        assert sa3 == {"halt": True}, "400 不得顺手改掉现状"

        # 观测面：每课一行带自己的停机态（仪表盘不问就不知道哪门课被停了）
        st, q = _http(base, "/admin/queue")
        assert st == 200
        assert q["courses"]["a"]["halt"] is True and q["courses"]["b"]["halt"] is False
        assert q["halt"] is False, "顶层 halt = 全课程（不是任一门）"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_offline_artifact_routes_by_body_course(tmp_path: Path) -> None:
    """补传按体里的 course 归属：同一进程服务多门课也能各落各家。"""
    import base64
    import gzip
    import hashlib

    weights = json.dumps({"w": [1.0, 2.0]}).encode("utf-8")
    b64 = base64.b64encode(gzip.compress(weights)).decode("ascii")
    hub = _hub(tmp_path)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        body = {
            "run_id": "run-b",
            "it": 3,
            "weights_json": b64,
            "weights_sha256": hashlib.sha256(weights).hexdigest(),
            "course": "b",
        }
        st, r = _http(base, "/offline/artifact", method="POST", data=json.dumps(body).encode())
        assert st == 200 and r["status"] == "accepted", r
        assert (tmp_path / "b" / "remote-jobs" / "offline" / "run-b" / "it-003").exists()
        assert not (tmp_path / "a" / "remote-jobs" / "offline").exists(), "不许落错课"
        # 归属不到 → 400 且点名该填什么（不静默塞进第一门课）
        st, r = _http(
            base,
            "/offline/artifact",
            method="POST",
            data=json.dumps({**body, "course": "", "run_id": "run-zz"}).encode(),
        )
        assert st == 400 and "course" in r["error"]


        # 续投（不带 course）靠已有 offline/<run_id>/ 自动归位
        st, r = _http(
            base,
            "/offline/artifact",
            method="POST",
            data=json.dumps({**body, "course": "", "it": 4}).encode(),
        )
        assert st == 200 and r["status"] == "accepted", r
        assert (tmp_path / "b" / "remote-jobs" / "offline" / "run-b" / "it-004").exists()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_single_course_wrapper_keeps_legacy_semantics(tmp_path: Path) -> None:
    """`as_hub(store)` 的形状：课程名空串、鉴权/竞速面借 store（旧调用零改动）。"""
    clock = _Clock()
    store = _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl", now_fn=clock)
    hub = as_hub(store)
    assert hub.courses() == [""]
    assert as_hub(hub) is hub, "幂等：已经是队列就原样返回"
    store.halt_workers = True
    assert hub.halt_workers is True, "单课程：进程级状态读写都落在那一份 store 上"
    hub.halt_workers = False
    assert store.halt_workers is False
    # 鉴权面同源（既有用例正是靠这点在 store 上预热封禁态再发 HTTP）
    for _ in range(5):
        hub.auth_failure("203.0.113.7")
    assert store.is_blocked("203.0.113.7") is True
    assert store._auth_fail == {}


# ------------------------------------------------------------------ 自动发现（--discover）
#
# 共享 hub 的课程表来源：训练侧发布 job 写的是**盘**（hub 与 trainer 共享同一份盘），
# 所以「这门课在跑」本身就写在盘上——不需要第二事实源（HTTP 注册那条旁路会失败、
# 会乱序、会忘了调，而漏注册的后果是那门课永久饿死且看起来训练正常）。


def _discover_hub(tmp_path: Path, clock: _Clock | None = None) -> _HubQueue:
    """共享 hub 的最简形状：起始**零课程**，课程表只靠盘上发现。"""
    return _HubQueue({}, now_fn=clock, discover_root=tmp_path)


def _enable(root: Path, course: str) -> None:
    """写**开课标记**（`<traj>/<课>/training-enabled.txt`）= 代操作员按一下控制台的「开课」。

    生产里这个文件由控制台写（`actions/course-lifecycle.ts`）、由「停课」删；hub 与训练侧的
    课程表判据都是 **账本 ∧ 开课标记**（`_course_dir_live` / `loop_plan.enabled_courses`）。
    测试造课目录时不写它 ⇒ 那门课**不算在训**（2026-09-20：这正是「一启动就把 tmp/ 下
    几十门历史课拉起来跑」的那条闸）。
    """
    (root / course).mkdir(parents=True, exist_ok=True)
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")


def _mk_course_dir(tmp_path: Path, name: str, *, age: float = 0.0) -> Path:
    """造一个课程目录（`remote-jobs/` + 本课 jsonl + 开课标记）；`age` 秒把两者 mtime 拨到过去。"""
    d = tmp_path / name / "remote-jobs"
    d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / name / "training_log.jsonl"
    log.write_text("", encoding="utf-8")
    _enable(tmp_path, name)
    if age:
        past = time.time() - age
        os.utime(d, (past, past))
        os.utime(log, (past, past))
    return d


def _publish_standalone(root: Path, course: str, jid: str, *, it: int = 1) -> None:
    """在**hub 还不知道**这门课时就往盘上发一份 job（真实训练侧的写法，课已开）。"""
    _enable(root, course)
    st = _JobStore(root / course / "remote-jobs", root / course / "training_log.jsonl")
    st.publish(jid, _manifest(jid, it=it), b"PK\x03\x04fake")


def test_discover_registers_published_course(tmp_path: Path) -> None:
    """盘上出现 job ⇒ 课程被发现、可领、可派——零注册调用。"""
    hub = _discover_hub(tmp_path)
    assert hub.courses() == []
    _publish_standalone(tmp_path, "c1", "j" * 16)
    assert hub.discover() == ["c1"]
    assert hub.courses() == ["c1"]
    assert hub.mode_of("c1") == COURSE_MODE_ONLINE
    course, jid, _tok = _claim(hub, "w1")
    assert (course, jid) == ("c1", "j" * 16)
    # 幂等：已登记的课不重复登记（返回空），而模式不被重置
    hub.set_mode("c1", COURSE_MODE_OFFLINE)
    assert hub.discover() == []
    assert hub.mode_of("c1") == COURSE_MODE_OFFLINE


def test_discover_skips_a_course_that_was_never_opened(tmp_path: Path) -> None:
    """**没开过课的目录不进课程表**（2026-09-20 用户报障的回归）。

    tmp/ 下堆着几十门历史课，每门都有 `remote-jobs/` 残影（「停课」是非破坏的：队列与账本
    一个字不动）；只看「目录新鲜」的话，hub 会把它们全部登记进课程表，并把残留的 pending
    job 继续派给真 GPU worker（白烧租约）——实测症状是「起了 trainer，控制台列出一堆课程
    正在训练」。开课标记就是那道显式闸：删它 = 停课，写它 = 开课。
    """
    clock = _Clock()  # 扫描有最小间隔闸（claim_next 是派发热路径）——推进时钟才真重扫
    hub = _discover_hub(tmp_path, clock)
    _mk_course_dir(tmp_path, "never-opened")
    # 停课 = 删开课标记（队列/账本原样保留，正是这里被误登记的盘上形状）
    (tmp_path / "never-opened" / COURSE_ENABLE_MARKER).unlink()
    assert hub.discover() == []
    assert hub.courses() == []
    # 再开课（写回标记）⇒ 下一次扫描就认它，不需要重启 hub
    _enable(tmp_path, "never-opened")
    clock.tick(hub.DISCOVER_SCAN_MIN_SEC + 0.5)
    assert hub.discover() == ["never-opened"]


def test_discover_skips_stale_and_non_course_dirs(tmp_path: Path) -> None:
    """陈旧实验目录（同样的磁盘形状、同样残留 pending job）与无关目录都不登记。

    误登记的代价是**真金白银**：已死课程的 pending job 会被继续派给真 GPU worker。
    （`dead-old` 有开课标记但目录陈旧——两道闸各自独立，这条钉的是新鲜度那道。）
    """
    hub = _discover_hub(tmp_path)
    _mk_course_dir(tmp_path, "dead-old", age=hub.DISCOVER_FRESH_SEC * 3)
    (tmp_path / "training-start").mkdir()  # 合法名字但没有 remote-jobs/offline
    (tmp_path / "bad name").mkdir()  # 名字非法（含空白）
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")  # 不是目录
    assert hub.discover() == []
    assert hub.courses() == []


def test_discover_keeps_registered_course_after_it_goes_stale(tmp_path: Path) -> None:
    """登记后不再撤销：课程暂停/结束（目录变旧）时，在飞 job 的结果回传不能 404。"""
    hub = _discover_hub(tmp_path)
    _mk_course_dir(tmp_path, "c1")
    assert hub.discover() == ["c1"]
    past = time.time() - hub.DISCOVER_FRESH_SEC * 5
    os.utime(tmp_path / "c1" / "remote-jobs", (past, past))
    os.utime(tmp_path / "c1" / "training_log.jsonl", (past, past))
    assert hub.discover() == []
    assert hub.courses() == ["c1"], "已登记的课程不因目录变旧被摘掉"


def test_stopped_course_stops_being_dispatched(tmp_path: Path) -> None:
    """停课（删开课标记）⇒ **立刻**停止派发它的 pending job；重新开课即恢复。

    2026-09-20 事故：课程表在**发现那一刻**建好就不再变，而 `remote-jobs/` 里的 pending
    job 躺在盘上不会消失——在旧表/旧代码里登记过的课程会把陈旧 job 继续派给真 GPU
    worker（云端逐份失败：D14 血缘不匹配 / 旧 code.zip 触发自重启），白烧租约，而
    训练侧什么都看不到（那门课早就不跑了）。用户口径「课程开训需要用户手动开启」
    ⇒ 删标记必须**当拍**断派发，不能等到下一次发现扫描，也不能靠控制台记得置离线。
    """
    hub = _discover_hub(tmp_path)
    _publish_standalone(tmp_path, "c1", "j" * 16)
    assert hub.discover() == ["c1"]
    (tmp_path / "c1" / COURSE_ENABLE_MARKER).unlink()  # 停课（非破坏：队列/账本一个字不动）
    assert hub.claim_next(worker_id="w1") is None, "未开课的课程不得派发"
    assert hub.claimable_job_ids("c1") == ["j" * 16], "停课不动队列（非破坏暂停）"
    _enable(tmp_path, "c1")  # 重新开课 ⇒ 立刻恢复派发（无需重启 hub）
    assert _claim(hub, "w1")[0] == "c1"


def test_single_course_hub_is_not_gated_by_enable_marker(tmp_path: Path) -> None:
    """单课程模式（`--job-root` 直给、无 `--discover`）不受开课闸影响——
    那条路径的「开课」就是有人显式起了这个 hub（既有数十个用例的夹具都是这个形状）。"""
    st = _JobStore(tmp_path / "remote-jobs", tmp_path / "training_log.jsonl")
    st.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
    hub = as_hub(st)
    assert hub.claim_next(worker_id="w1") is not None


def test_discover_scan_is_throttled(tmp_path: Path) -> None:
    """最小间隔闸：`claim_next` 是派发热路径（worker 每几秒一轮询），不能每次都 readdir。"""
    clock = _Clock()
    hub = _discover_hub(tmp_path, clock)
    _mk_course_dir(tmp_path, "c1")
    assert hub.discover() == ["c1"]
    _mk_course_dir(tmp_path, "c2")
    assert hub.discover() == [], "间隔内不重扫"
    clock.tick(hub.DISCOVER_SCAN_MIN_SEC + 0.5)
    assert hub.discover() == ["c2"], "过了间隔就该看到新课程"


def test_discover_adopts_process_state_from_solo_store(tmp_path: Path) -> None:
    """单课程时进程级状态借 store；发现第二门课时必须**搬**过来，不能悄悄清掉。

    不搬就是「新开一门课，把停机达令 / worker 登记 / 鉴权闭锁一起清了」——三类事故
    （云端不停机、避让失灵、封禁失效）都只在多课程同时跑时才出现。
    """
    clock = _Clock()
    hub = _HubQueue(
        {
            "a": _JobStore(
                tmp_path / "a" / "remote-jobs",
                tmp_path / "a" / "training_log.jsonl",
                now_fn=clock,
            )
        },
        order=["a"],
        now_fn=clock,
        discover_root=tmp_path,
    )
    hub.halt_workers = True
    for _ in range(5):
        hub.auth_failure("203.0.113.9")
    assert hub.is_blocked("203.0.113.9") is True, "预热：封禁态住在 store 里"

    _mk_course_dir(tmp_path, "b")
    assert hub.discover() == ["b"]
    assert hub.halt_workers is True, "多一门课不该清停机达令"
    assert hub.is_blocked("203.0.113.9") is True, "鉴权闭锁跨课程延续（同进程一份）"
    # 搬完就不再借 store：此后写的是队列自己的状态
    hub.halt_workers = False
    assert hub._stores["a"].halt_workers is True, "store 上那份已成历史（不再生效）"


# ------------------------------------------------------------------ 补传归位（离线训练模式）
#
# 离线段（kind=run）在云机上自己跑完，逐轮把产物**补传**回 hub（`/offline/artifact`）。
# 补传体里没有 job、也没有租约 ⇒ hub 只能靠体里自报的课程把每一轮落进正确的课程目录。
# 缺这个键时的后果（2026-09-19 发现）：多课程 hub 下每一条补传都被 400「无法归属课程」
# 拒掉，节点侧补传**整体停用**（体是自己造的，重试不会变对）——训练照常，但控制台上
# 段内进度永远是空的，只剩「跑完自己下载导入」。


def _artifact_body(run_id: str, it: int, *, course: str = "") -> bytes:
    """一份最小合法补传体（权重自动算指纹；course 可选）。"""
    import hashlib

    from remote.protocol import encode_weights_json

    wj = json.dumps({"it": it, "w": it * 1.5}).encode("utf-8")
    body: dict = {
        "run_id": run_id,
        "it": it,
        "weights_fp": hashlib.sha256(wj).hexdigest(),
        "weights_json": encode_weights_json(wj),
        "row": {"it": it},
    }
    if course:
        body["course"] = course
    return json.dumps(body).encode("utf-8")


def test_offline_backfeed_routes_by_the_declared_course(tmp_path: Path) -> None:
    """体里带 `course` ⇒ 落进**那门课**的 `offline/<run>/it-NNN/`，另一门课一个字都不写。"""
    hub = _discover_hub(tmp_path)
    for c in ("c4", "c5"):
        _mk_course_dir(tmp_path, c)
    assert hub.discover() == ["c4", "c5"], hub.courses()
    base, _ref, srv, th = _boot(tmp_path, hub)
    try:
        st, body = _http(
            base,
            "/offline/artifact",
            method="POST",
            data=_artifact_body("seg-1", 7, course="c5"),
        )
        assert st == 200 and body["status"] == "accepted", body
        assert (tmp_path / "c5" / "remote-jobs" / "offline" / "seg-1" / "it-007").is_dir()
        assert not (tmp_path / "c4" / "remote-jobs" / "offline").exists(), "串课 = 曲线画错课程"
        # 段末摘要同规（同一门课）
        st, body = _http(
            base,
            "/offline/result",
            method="POST",
            data=json.dumps(
                {"run_id": "seg-1", "it_end": 7, "state": "complete", "course": "c5"}
            ).encode("utf-8"),
        )
        assert st == 200, body
        assert (tmp_path / "c5" / "remote-jobs" / "offline" / "seg-1" / "result.json").exists()
        # 读面（控制台读的就是它）：只报 c5 这一门
        st, body = _http(base, "/admin/offline")
        assert st == 200 and list(body["progress"]) == ["c5"], body
        assert body["progress"]["c5"]["seg-1"]["its"] == [7]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_offline_backfeed_moves_the_console_table(tmp_path: Path) -> None:
    """回传一轮 ⇒ 课程账本出现 `iteration` 行 + 逐局画像落 `it<N>/per-game.json`。

    用户之问（2026-09-22）：「云机通过网络请求回传，会算这些数据回显吗？」——此前**不会**：
    回传只落 `offline/<run>/it-NNN/{weights,opt,row}.json` + 一条 `offline_artifact` 事件，
    控制台那张表（含耗时/击杀/残血/道具）一行不显，而这件事**人工导入能修、实时回传不能**
    ⇒ 同一个 hub 上两条腿的观测面不一致。现在两路走同一张翻译表
    （`remote.artifacts.ledger_row_from_metrics`）与同一个画像落点（控制台按文件优先读它）。
    """
    hub = _discover_hub(tmp_path)
    _mk_course_dir(tmp_path, "c4")
    assert hub.discover() == ["c4"], hub.courses()
    base, _ref, srv, th = _boot(tmp_path, hub)
    try:
        row: dict = {
            "it": 3,
            "wall_sec": 91.0,
            "rollout_sec": 4.5,
            "ppo_sec": 33.9,
            "steps": 48000,
            "agg": {"kl": 0.011, "policy": 0.002, "value": 0.5, "entropy": 0.7},
            "report": {
                "games": 328,
                "winRate": 0.25,
                "totalSamples": 48000,
                "totalTicks": 1234,
                "dimMeans": {"kills": 0.3},
                "scoreStats": {"mean": 1.5, "std": 0.2},
            },
            "perGame": [
                {"stage": 1, "seed": 7, "nSamples": 5, "kills": 4, "ticks": 900},
                {"stage": 2, "seed": 8, "nSamples": 5, "kills": 1, "ticks": 700},
            ],
        }
        body = json.loads(_artifact_body("seg-1", 3, course="c4").decode("utf-8"))
        body["row"] = row
        payload = json.dumps(body).encode("utf-8")
        st, res = _http(base, "/offline/artifact", method="POST", data=payload)
        assert st == 200 and res["status"] == "accepted", res
        traj = tmp_path / "c4"
        # ① 逐局画像落到控制台读得到的地方（`iters.ts::readRoundActuals` 先看这个文件）
        pg = traj / "it3" / "per-game.json"
        assert pg.is_file(), "逐局画像没落盘 ⇒ 表上四列恒空"
        assert json.loads(pg.read_text(encoding="utf-8")) == row["perGame"]
        # ② 课程账本多一行 `iteration`（控制台按它画表）
        ledger = [
            json.loads(x)
            for x in (traj / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        its = [x for x in ledger if x.get("event") == "iteration"]
        assert len(its) == 1 and its[0]["iter"] == 3, ledger
        one = its[0]
        assert one["winRate"] == 0.25 and one["samples"] == 48000
        assert one["expectedGames"] == 328 and one["ticks"] == 1234
        assert one["rollout_sec"] == 4.5 and one["ppo_sec"] == 33.9
        assert one["kl"] == 0.011 and one["dim_means"] == {"kills": 0.3}
        assert one["score_mean"] == 1.5 and one["source"] == "offline_backfeed"
        # ③ 重复投递：绝不写第二行（读方按 it 画曲线，两行 = 曲线打结）
        st, res = _http(base, "/offline/artifact", method="POST", data=payload)
        assert st == 200 and res["status"] == "duplicate", res
        ledger = [
            json.loads(x)
            for x in (traj / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        assert len([x for x in ledger if x.get("event") == "iteration"]) == 1
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_offline_backfeed_mirrors_advances_active_weights_and_archives(tmp_path: Path) -> None:
    """回传轮的**三处课程侧落位**（用户 2026-09-23 口径 ①②③）。

    实测缺口：seg-1（人工导入）在 `deliver/<run>/it-000…048`，而 seg-2（回传）只在
    `remote-jobs/offline/<run>/it-048…124` ⇒ 两段分居两棵树；`<traj>/weights.json` 整段
    停在段起点（= 该 run 的 it0 指纹）；`nn-training/weights/<课>/` 一个回传轮都没有
    —— 而控制台 evalA 的 iter 选择器只扫那个目录 ⇒ **回传段的权重"看不见"**（真正意义上
    的"没落盘"）。三条一起钉，外加"不倒退"。
    """
    hub = _discover_hub(tmp_path)
    _mk_course_dir(tmp_path, "c4")
    assert hub.discover() == ["c4"], hub.courses()
    base, _ref, srv, th = _boot(tmp_path, hub)
    try:
        traj = tmp_path / "c4"
        wj3 = json.dumps({"it": 3, "w": 4.5}).encode("utf-8")
        st, res = _http(
            base, "/offline/artifact", method="POST", data=_artifact_body("seg-2", 3, course="c4")
        )
        assert st == 200 and res["status"] == "accepted", res
        # ① 交付镜像：与导入腿同路径同文件名（两腿同构 ⇒ 找东西只翻一棵树）
        mir = traj / "deliver" / "seg-2" / "it-003"
        assert (mir / "weights.json").read_bytes() == wj3, "镜像要落同一份字节"
        assert (mir / "row.json").is_file()
        # ② 活动权重推进到该轮
        assert (traj / "weights.json").read_bytes() == wj3, "活动权重要跟着段尾走"
        # ③ 归档（evalA 的 iter 选择器只看这里）——隔离根由 autouse fixture 指到 tmp
        root = Path(os.environ["BCITY_WEIGHTS_ARCHIVE_ROOT"]) / "c4"
        arch = sorted(root.glob("c4.it3.*.json"))
        assert len(arch) == 1 and arch[0].read_bytes() == wj3, list(root.glob("*"))
        # ② 不倒退：账本已有更新的轮（本机循环落的 it5）⇒ 晚到的 it4 不得覆盖活动权重
        with open(traj / "training_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "iteration", "iter": 5, "kl": 0.0}) + "\n")
        st, res = _http(
            base, "/offline/artifact", method="POST", data=_artifact_body("seg-2", 4, course="c4")
        )
        assert st == 200 and res["status"] == "accepted", res
        assert (traj / "weights.json").read_bytes() == wj3, "旧轮不得覆盖活动权重"
        assert (traj / "deliver" / "seg-2" / "it-004" / "weights.json").is_file(), "镜像照落"
        assert list(root.glob("c4.it4.*.json")), "归档照落（可见性不该被活动权重门挡住）"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_backup_target_follows_the_course_config(tmp_path: Path) -> None:
    """归档 `(prefix, dir)` 要跟**课程配置**走（而不是硬编码课名）——真课程声明了就用它。"""
    st = _JobStore(tmp_path / "c4" / "remote-jobs", tmp_path / "c4" / "training_log.jsonl")
    # 仓里有真课程配置的课（x20-noexplore 声明 backup_dir/backup_prefix）
    assert st._course_backup_target("x20-noexplore") == (
        "x20-noexplore",
        str(ROOT.parent / "nn-training" / "weights" / "x20-noexplore"),
    )
    # 没配置的课（工装/新课）：同构缺省落到**当前归档根**（env 隔离的那一个）
    assert st._course_backup_target("no-such-course") == (
        "no-such-course",
        str(Path(os.environ["BCITY_WEIGHTS_ARCHIVE_ROOT"]) / "no-such-course"),
    )


def test_offline_backfeed_without_a_course_is_refused_loudly_on_a_multi_course_hub(
    tmp_path: Path,
) -> None:
    """多课程 hub 上不带课程 ⇒ **响亮 400**（带课程清单），不猜、不错落另一门课。

    为什么不允许猜：补传落到错课程上，那条曲线看起来完全正常（数值合理、时间戳合理），
    只有事后对账才能发现——比拒收危险得多。
    """
    hub = _discover_hub(tmp_path)
    for c in ("c4", "c5"):
        _mk_course_dir(tmp_path, c)
    assert hub.discover() == ["c4", "c5"], hub.courses()
    base, _ref, srv, th = _boot(tmp_path, hub)
    try:
        st, body = _http(
            base, "/offline/artifact", method="POST", data=_artifact_body("seg-2", 1)
        )
        assert st == 400 and "无法归属课程" in body["error"], body
        assert "c4" in body["error"] and "c5" in body["error"], "拒因要带上课程清单"
        assert not (tmp_path / "c4" / "remote-jobs" / "offline").exists()
        assert not (tmp_path / "c5" / "remote-jobs" / "offline").exists()
        # `?course=` 是运维手工补传那条路（体里没有课程时用它）
        st, body = _http(
            base,
            "/offline/artifact?course=c4",
            method="POST",
            data=_artifact_body("seg-2", 1),
        )
        assert st == 200 and body["status"] == "accepted", body
        assert (tmp_path / "c4" / "remote-jobs" / "offline" / "seg-2" / "it-001").is_dir()
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_spawn_hub_argv_is_the_console_shape(tmp_path: Path) -> None:
    """控制台实际启动的 argv 形状（`--traj-root <traj> --discover`）——防漂移锚点。"""
    argv = _spawn_hub(1234, tmp_path)
    assert "remote.hub_server" in argv
    assert argv[argv.index("--traj-root") + 1] == str(tmp_path)
    assert "--discover" in argv


def _spawn_hub(port: int, tmp_path: Path) -> list[str]:
    """控制台实际启动的那条 argv（`--traj-root tmp --discover`）。"""
    return [
        sys.executable,
        "-u",
        "-m",
        "remote.hub_server",
        "--port",
        str(port),
        "--host",
        "127.0.0.1",
        "--token",
        "sekret",
        "--traj-root",
        str(tmp_path),
        "--discover",
        "--discover-sec",
        "0.2",
        # 锁文件落临时目录：默认住 nn-training/（会向仓库目录撒 `.hub_server.<port>.lock`）
        "--lock-file",
        str(tmp_path / "hub.lock"),
    ]


def test_main_discover_picks_up_course_from_disk(tmp_path: Path) -> None:
    """**真进程**路径：控制台实际启动的 argv（`--traj-root tmp --discover`）能服务新课程。

    上面那组钉的是 `_HubQueue.discover()` 的判定；这条钉的是 main() 的接线（argparse →
    ΔHubQueue(discover_root=...) → 后台扫描线程）。接线断了的表现极隐蔽：单测全绿、
    进程也“在跑”，但新开的课**永远**领不到活（轮转表里没有它）。
    """
    # 端口竞态由 helper 消化：裸的「探一个端口 → 交给子进程 bind」在 xdist 并行下会撞
    # 「禁止双监听」当场退出（2026-09-19 pre-commit 实测）⇒ 看着像本用例的假红
    srv = spawn_bound_port(
        lambda port: _spawn_hub(port, tmp_path),
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    port, proc = srv.port, srv.proc
    base = f"http://127.0.0.1:{port}"
    try:
        st = 0
        body: dict = {}
        for _ in range(120):  # 就绪 = /admin/queue 能答（Python 冷启动 import 链 ~1s）
            if proc.poll() is not None:
                break
            try:
                st, body = _http(base, "/admin/queue")
                if st == 200:
                    break
            except Exception:  # 启动窗口内的连接失败是常态
                pass
            # sleep-ok: 轮询步长（等的是「hub 已就绪」这个状态，30s 只当挂起兜底）
            time.sleep(0.25)
        else:
            raise AssertionError(f"hub-server 未在 30s 内就绪（rc={proc.poll()}）")
        assert st == 200, f"启动失败：{body}；输出：{srv.tail()}"
        assert body["courses"] == {}, "还没有课 ⇒ 空课程表（不是错误）"

        # 训练侧发布 job（写盘）= 这门课在跑
        _publish_standalone(tmp_path, "late", "L" * 16)
        seen: dict | None = None
        for _ in range(60):
            _st, body = _http(base, "/admin/queue")
            if "late" in body["courses"]:
                seen = body
                break
            # sleep-ok: 轮询步长（等的是「发现线程已登记新课程」这个状态，deadline 只当兜底）
            time.sleep(0.25)
        assert seen is not None, f"--discover 没把新课程登记进来：{body}"
        assert seen["courses"]["late"]["pending_n"] == 1

        # 真 worker 轮询能领到它（端到端：不是只出现在观测面）
        task = _next(base, worker="w1")
        assert task["course"] == "late", task
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_http_worker_picks_up_new_course_without_restart(tmp_path: Path) -> None:
    """端到端口径：hub 起来之后新开的课，**下一次轮询**就能被领到（不必重启、不必注册）。"""
    clock = _Clock()
    hub = _discover_hub(tmp_path, clock)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        body = _next(base, worker="w1")
        assert not body["job_id"], "还没有课 ⇒ 空轮询（不是错误）"
        # 另一门课此刻才开跑（job 落盘）——真实世界里 hub 早就起着了
        _publish_standalone(tmp_path, "late", "L" * 16)
        clock.tick(hub.DISCOVER_SCAN_MIN_SEC + 1.0)  # 最小间隔闸：最坏等待就是这个量级
        body = _next(base, worker="w1")
        assert (body["course"], body["job_id"]) == ("late", "L" * 16), body
        st, q = _http(base, "/admin/queue")
        assert st == 200 and "late" in q["courses"]
        # payload 也能按 job_id 反查归属（worker 不需要知道课程名）
        st, raw = _http_raw(base, f"/jobs/{'L' * 16}/payload")
        assert st == 200 and raw == b"PK\x03\x04fake"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)
