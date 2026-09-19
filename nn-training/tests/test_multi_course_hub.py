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
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    HUB_SCOPE_HEADER,
    RACE_MODE_AUTO,
    RACE_MODE_OFF,
    WORKER_ID_HEADER,
    ProtocolError,
    may_avoid_stale_holder,
    normalize_manifest,
    parse_course_arg,
    race_decision,
    rotation_order,
)
from tests.subproc_util import spawn_bound_port

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


def test_race_decision_active_courses() -> None:
    """新口径：**不同 worker 数 > 在派发课程数** 才广播；缺省 1 课 = 旧口径逐字节等价。"""
    now = 1000.0
    three = [("a", now, 1), ("b", now, 1), ("c", now, 1)]
    two = [("a", now, 1), ("b", now, 1)]
    one = [("a", now, 1)]
    # 缺省（单课程）：与旧行为一致
    assert race_decision(RACE_MODE_AUTO, one, now) is False
    assert race_decision(RACE_MODE_AUTO, two, now) is True
    # 2 课 2 worker：各拿一条卡，谁也不多 ⇒ 独占
    assert race_decision(RACE_MODE_AUTO, two, now, active_courses=2) is False
    # 2 课 3 worker：多出来的那条卡去抢 ⇒ 竞速
    assert race_decision(RACE_MODE_AUTO, three, now, active_courses=2) is True
    # 3 课 3 worker：刚好够分 ⇒ 独占
    assert race_decision(RACE_MODE_AUTO, three, now, active_courses=3) is False
    # 1 课 3 worker：退化成单课程多卡竞速
    assert race_decision(RACE_MODE_AUTO, three, now, active_courses=1) is True
    # 多 hub worker 混入仍然一票否决（scope 是安全条件，不随课程数放宽）
    assert (
        race_decision(
            RACE_MODE_AUTO, [("a", now, 1), ("b", now, 1), ("c", now, 2)], now, active_courses=1
        )
        is False
    )


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


def _claim(hub: _HubQueue, worker: str, *, race: bool = False) -> tuple[str, str, str]:
    """取下一份该派的 job；**没有必须是失败**（否则断言会变成静默跳过）。"""
    picked = hub.claim_next(worker_id=worker, race=race)
    assert picked is not None, f"应当有可派给 {worker} 的 job"
    return picked


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


# ------------------------------------------------------------------ 路由


def test_course_of_uses_none_for_missing_not_empty_string(tmp_path: Path) -> None:
    """回归：单课程队列的课程名**就是空串**，所以「找不到」必须是 None。

    2026-09-18 实测故障：`course_of` 用空串兼作缺失值 ⇒ 单课程下 `/jobs/next` 刚派出的
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
    hub.note_worker("w1", 1)
    hub.note_worker("w2", 1)
    first = _claim(hub, "w1")
    assert first[:2] == ("a", "a" * 16)
    assert hub.claim_next(worker_id="w2", race=False) is None, "租约未过期 ⇒ 没人能抢"
    clock.tick(CLAIM_TTL_SEC + 1)
    # 活着的 worker 会持续轮询（协议就是它们每几秒打一次 /jobs/next），所以「活跃」要按
    # 真实节奏刷一遍。不刷的话两台都被当成已离场 ⇒ 活跃数 0 ⇒ 避让闸不开（这是对的：
    # 连一台活着的都没有时，避让只会让这活没人干）。租约 TTL(300s) 比
    # RACE_WORKER_WINDOW_SEC(180s) 长，就是为此——一个 worker 停 poll 超窗口即可判离场。
    hub.note_worker("w1", 1)
    hub.note_worker("w2", 1)
    assert hub.claim_next(worker_id="w1", race=False) is None, "刚跑死它的那台得让位"
    second = _claim(hub, "w2")
    assert second[1] == "a" * 16, "换一台 worker 就该给它"


def test_expired_lease_self_claim_allowed_when_sole_worker(tmp_path: Path) -> None:
    """独苗必须能自领自己超时过的活——否则那台 worker 永远空转（集群停摆）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)
    _publish(hub, "a", "a" * 16)
    hub.note_worker("only", 1)
    _claim(hub, "only")
    clock.tick(CLAIM_TTL_SEC + 1)
    assert _claim(hub, "only")[1] == "a" * 16


def test_release_clears_avoidance(tmp_path: Path) -> None:
    """主动还租约 = 不是「跑死了」⇒ 不该被避让（否则 worker 瞬时失败还了活就再也领不回来）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock=clock)
    _publish(hub, "a", "a" * 16)
    hub.note_worker("w1", 1)
    hub.note_worker("w2", 1)
    picked = _claim(hub, "w1")
    assert hub.release(picked[1], picked[2]) is True
    assert _claim(hub, "w1")[1] == "a" * 16, "还租约后自己该能立刻重新领到"


# ------------------------------------------------------------------ 竞速（多课程口径）


def test_race_true_when_more_workers_than_courses(tmp_path: Path) -> None:
    """2 课 3 worker ⇒ 广播：每门课的最新 job 都可被多人抢（先回传者胜）。"""
    hub = _hub(tmp_path)
    _publish(hub, "a", "a" * 16)
    _publish(hub, "b", "b" * 16)
    for w in ("w1", "w2", "w3"):
        hub.note_worker(w, 1)
    assert hub.race_active() is True
    # 广播轮：不下租约 ⇒ 同一份能被第二个 worker 再领（先回传者胜）
    assert _claim(hub, "w1", race=True)[1] == "a" * 16
    assert _claim(hub, "w2", race=True)[1] == "b" * 16
    assert _claim(hub, "w3", race=True)[1] == "a" * 16, "广播副本不落租约，可重复领"


def test_race_false_when_workers_match_courses(tmp_path: Path) -> None:
    """3 课 3 worker ⇒ 独占：每门课各拿一条卡，谁都不多余（不重复烧）。"""
    hub = _hub(tmp_path, courses=("a", "b", "c"))
    for c in ("a", "b", "c"):
        _publish(hub, c, c * 16)
    for w in ("w1", "w2", "w3"):
        hub.note_worker(w, 1)
    assert hub.race_active() is False
    assert _claim(hub, "w1")[1] == "a" * 16
    assert _claim(hub, "w2")[1] == "b" * 16
    assert _claim(hub, "w3")[1] == "c" * 16


def test_race_ignores_offline_course_in_denominator(tmp_path: Path) -> None:
    """离线课不算「在派发」⇒ 2 活跃 worker 对 1 门在派发课就已该竞速。"""
    hub = _hub(tmp_path, courses=("a", "b"), offline=("b",))
    _publish(hub, "a", "a" * 16)
    _publish(hub, "b", "b" * 16)
    hub.note_worker("w1", 1)
    hub.note_worker("w2", 1)
    assert hub.race_active() is True


# ------------------------------------------------------------------ HTTP 全链路


def test_http_serves_all_courses_and_reports_course(tmp_path: Path) -> None:
    """一个进程、一个端口服务两门课：/jobs/next 轮流给，并在响应里自报 course。

    用 2 个 worker：worker 数 == 课程数 ⇒ 不竞速（独占），每门课各拿一条卡。
    （3 个 worker 时会自动转竞速——那是 `test_race_*` 那一组的命题，见上面那条日志。）
    """
    hub = _hub(tmp_path)
    _publish(hub, "a", "a" * 16)
    _publish(hub, "b", "b" * 16)
    base, _hub_ref, srv, th = _boot(tmp_path, hub)
    try:
        seen = []
        for w in ("w1", "w2", "w1"):
            st, body = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: w, HUB_SCOPE_HEADER: "1"})
            assert st == 200, body
            if body["job_id"]:
                seen.append(body["course"])
        assert seen == ["a", "b"], f"独占轮转跨课失效：{seen}"
        assert hub.race_active() is False, "2 课 2 worker 不该竞速（除非多出来的卡）"
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
        _st, ra = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w1"})
        assert (ra["course"], ra["halt"]) == ("a", False), ra
        _st, rb = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w2"})
        assert (rb["course"], rb["halt"]) == ("b", True), rb

        # 空闲轮询没有课程上下文 ⇒ 全部课都停才告诉它停（否则 idle worker 会被凭空停掉）
        _st, idle = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w3"})
        assert idle["job_id"] is None and idle["halt"] is False
        _http(base, "/admin/workers/halt?course=a")
        _st, idle2 = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w3"})
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
    store.race_mode = RACE_MODE_OFF
    assert hub.race_mode == RACE_MODE_OFF
    # 鉴权面同源（既有用例正是靠这点在 store 上预热封禁态再发 HTTP）
    for _ in range(5):
        hub.auth_failure("203.0.113.7")
    assert store.is_blocked("203.0.113.7") is True
    assert store._auth_fail == {}


def test_single_course_race_matches_legacy_two_worker_rule(tmp_path: Path) -> None:
    """单课程队列的竞速口径 = 旧口径（≥2 个不同 worker），不是 `1 < workers` 的另一种写法。"""
    hub = _hub(tmp_path, courses=("",))
    assert hub.race_active() is False
    hub.note_worker("w1", 1)
    assert hub.race_active() is False
    hub.note_worker("w2", 1)
    assert hub.race_active() is True
    assert hub.race_state()["workers"] == 2


# ------------------------------------------------------------------ 自动发现（--discover）
#
# 共享 hub 的课程表来源：训练侧发布 job 写的是**盘**（hub 与 trainer 共享同一份盘），
# 所以「这门课在跑」本身就写在盘上——不需要第二事实源（HTTP 注册那条旁路会失败、
# 会乱序、会忘了调，而漏注册的后果是那门课永久饿死且看起来训练正常）。


def _discover_hub(tmp_path: Path, clock: _Clock | None = None) -> _HubQueue:
    """共享 hub 的最简形状：起始**零课程**，课程表只靠盘上发现。"""
    return _HubQueue({}, now_fn=clock, discover_root=tmp_path)


def _mk_course_dir(tmp_path: Path, name: str, *, age: float = 0.0) -> Path:
    """造一个课程目录（`remote-jobs/` + 本课 jsonl）；`age` 秒把两者 mtime 拨到过去。"""
    d = tmp_path / name / "remote-jobs"
    d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / name / "training_log.jsonl"
    log.write_text("", encoding="utf-8")
    if age:
        past = time.time() - age
        os.utime(d, (past, past))
        os.utime(log, (past, past))
    return d


def _publish_standalone(root: Path, course: str, jid: str, *, it: int = 1) -> None:
    """在**hub 还不知道**这门课时就往盘上发一份 job（真实训练侧的写法）。"""
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


def test_discover_skips_stale_and_non_course_dirs(tmp_path: Path) -> None:
    """陈旧实验目录（同样的磁盘形状、同样残留 pending job）与无关目录都不登记。

    误登记的代价是**真金白银**：已死课程的 pending job 会被继续派给真 GPU worker。
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

    不搬就是「新开一门课，把停机达令 / 竞速模式 / 鉴权闭锁一起清了」——三类事故
    （云端不停机、竞速口径漂、封禁失效）都只在多课程同时跑时才出现。
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
    hub.race_mode = RACE_MODE_OFF
    for _ in range(5):
        hub.auth_failure("203.0.113.9")
    assert hub.is_blocked("203.0.113.9") is True, "预热：封禁态住在 store 里"

    _mk_course_dir(tmp_path, "b")
    assert hub.discover() == ["b"]
    assert hub.halt_workers is True, "多一门课不该清停机达令"
    assert hub.race_mode == RACE_MODE_OFF
    assert hub.is_blocked("203.0.113.9") is True, "鉴权闭锁跨课程延续（同进程一份）"
    # 搬完就不再借 store：此后写的是队列自己的状态
    hub.halt_workers = False
    assert hub._stores["a"].halt_workers is True, "store 上那份已成历史（不再生效）"


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
            time.sleep(0.25)
        assert seen is not None, f"--discover 没把新课程登记进来：{body}"
        assert seen["courses"]["late"]["pending_n"] == 1

        # 真 worker 轮询能领到它（端到端：不是只出现在观测面）
        st, task = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w1"})
        assert st == 200 and task["course"] == "late", task
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
        st, body = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w1"})
        assert st == 200 and not body["job_id"], "还没有课 ⇒ 空轮询（不是错误）"
        # 另一门课此刻才开跑（job 落盘）——真实世界里 hub 早就起着了
        _publish_standalone(tmp_path, "late", "L" * 16)
        clock.tick(hub.DISCOVER_SCAN_MIN_SEC + 1.0)  # 最小间隔闸：最坏等待就是这个量级
        st, body = _http(base, "/jobs/next", headers={WORKER_ID_HEADER: "w1"})
        assert st == 200, body
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
