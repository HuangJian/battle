"""test_race_broadcast —— 单课程多卡竞速广播（2026-09-17 用户指令）。

背景：§343（2026-09-06）PPO job 竞速广播 → §2026-09-12 P3b 以「多课程并行时同 job
被重复算、慢者白烧」为由 supersede 回独占加超时。本次**定向重开**：只有当这个 hub 的
worker 全都只服务这一个 hub 时，**最新的** job 才不下租约、对所有 worker 可见——
先回传者胜（`store_result` 首写锁定），后到者 409 丢弃（worker 侧 409 已按成功处理）。

判据的输入全部来自 worker 自报（`X-Worker-Id` / `X-Hub-Scope`）：隧道回源把全流量归成
127.0.0.1，源 IP 分不出 worker；而不报就按多 hub 保守（退回 P3b 独占）。
"""

from __future__ import annotations

import json
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

from remote.hub_server import _JobStore, make_server
from remote.protocol import (
    AUTH_HEADER,
    HUB_SCOPE_HEADER,
    RACE_MODE_AUTO,
    RACE_MODE_OFF,
    RACE_MODE_ON,
    WORKER_ID_HEADER,
    normalize_manifest,
    parse_hub_scope,
    race_decision,
)

# ------------------------------------------------------------------ 纯函数


@pytest.mark.parametrize(
    ("raw", "want"),
    [("1", 1), ("2", 2), (" 1 ", 1), ("", None), ("abc", None), ("0", None), ("-1", None), (None, None)],
)
def test_parse_hub_scope(raw: object, want: int | None) -> None:
    """头解析：≥1 才算数；缺失/垃圾/0 一律 None（未知 = 保守）。"""
    assert parse_hub_scope(raw) == want


def test_race_decision_forced_modes() -> None:
    """off 恒关、on 恒开（应急强制，不看 worker 表）。"""
    now = 1000.0
    assert race_decision(RACE_MODE_OFF, [], now) is False
    assert race_decision(RACE_MODE_OFF, [("a", now, 1), ("b", now, 1)], now) is False
    assert race_decision(RACE_MODE_ON, [], now) is True
    assert race_decision(RACE_MODE_ON, [("a", now, 7)], now) is True


def test_race_decision_auto_needs_two_single_hub_workers() -> None:
    """auto：窗口内 ≥2 个**不同** worker 且全部 scope==1 才开（单卡/多 hub/未知都不开）。"""
    now = 1000.0
    assert race_decision(RACE_MODE_AUTO, [], now) is False
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1)], now) is False  # 单 worker：无竞速收益
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("b", now, 1)], now) is True
    # 同一个 worker 报两次不算两个 worker（身份去重）
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("a", now, 1)], now) is False
    # 有人服务两个 hub（多课程）→ 退回独占
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("b", now, 2)], now) is False
    # 范围未知（旧 worker / 手写 curl）→ 同样保守
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("b", now, None)], now) is False


def test_race_decision_auto_window() -> None:
    """离场判定：窗口外不再计数（一个还在 + 一个已走 → 退回独占）。"""
    now = 1000.0
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("b", now - 181, 1)], now) is False
    assert race_decision(RACE_MODE_AUTO, [("a", now, 1), ("b", now - 179, 1)], now) is True


# ------------------------------------------------------------------ 存储层


def _manifest(jid: str = "j" * 16, it: int = 3) -> dict:
    """最小合法 manifest（必填齐全）。"""
    return normalize_manifest(
        {
            "proto": 1,
            "runId": "test-run",
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


def _store(tmp_path: Path, mode: str = RACE_MODE_AUTO) -> _JobStore:
    s = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    s.race_mode = mode
    return s


def test_auto_mode_tracks_worker_scopes(tmp_path: Path) -> None:
    """auto 的判据 = 本 hub 登记到的 worker 全为单 hub：多课程 worker 一出现即自动关。"""
    s = _store(tmp_path)
    s.note_worker("w1", 1)
    assert s.race_active() is False  # 只有一个 worker
    s.note_worker("w2", 1)
    assert s.race_active() is True
    s.note_worker("w3", 2)  # 多课程机群混入
    assert s.race_active() is False
    st = s.race_state()
    assert st["race_mode"] == RACE_MODE_AUTO and st["race_active"] is False
    assert st["workers"] == 3 and st["worker_scopes"]["w3"] == 2
    # 无名轮询（缺头）不登记：无处可去重计数
    s2 = _store(tmp_path / "b")
    s2.note_worker("", 1)
    assert s2.race_state()["workers"] == 0


def test_set_race_mode_rejects_junk(tmp_path: Path) -> None:
    """非法模式不改现状（配置/接口写错不得静默变成 on）。"""
    s = _store(tmp_path)
    assert s.set_race_mode("AUTO") == "auto"
    assert s.set_race_mode("bogus") is None and s.race_mode == "auto"
    assert s.set_race_mode("off") == "off"


def test_claimable_race_broadcasts_newest_only(tmp_path: Path) -> None:
    """竞速轮：**最新** job 忽略租约（谁都能领），更老的维持独占（不在陈旧 job 上堆卡）。"""
    s = _store(tmp_path, RACE_MODE_ON)
    s.publish("a" * 16, _manifest("a" * 16, it=1), b"PK\x03\x04fake")
    time.sleep(0.01)  # ts 按发布序（同毫秒时靠 ts 排序会平局）
    s.publish("b" * 16, _manifest("b" * 16, it=2), b"PK\x03\x04fake")
    assert s.claim("a" * 16) and s.claim("b" * 16)  # 两份都被独占领走
    assert s.claimable_job_ids(race=False) == [], "独占：租约期内两份都领不到"
    # 竞速轮：最新的一定可领（忽略租约——它就是被广播的那份）；更老的不广播
    assert s.claimable_job_ids(race=True) == ["b" * 16]
    # 广播领取：不下租约、并清掉先前那份独占租约
    assert s.claim("b" * 16, race=True) == ""
    assert s.claimable_job_ids(race=True) == ["b" * 16], "广播副本不落租约 ⇒ 一直可领"
    # 无活租约 ⇒ 外来回传放行（胜负交给首写锁定）；老的仍只认持有人
    assert s.result_token_ok("b" * 16, "") is True
    assert s.result_token_ok("a" * 16, "") is False


# ------------------------------------------------------------------ HTTP 全链路


def _boot(tmp_path: Path, mode: str = RACE_MODE_AUTO) -> tuple:
    store = _store(tmp_path, mode)
    srv = make_server(store, 0, "sekret", host="127.0.0.1")
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", store, srv, th


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


def _poll(base: str, worker: str, scope: int) -> dict:
    _st, body = _http(
        base, "/jobs/next", headers={WORKER_ID_HEADER: worker, HUB_SCOPE_HEADER: str(scope)}
    )
    return body


def _result(manifest: dict) -> bytes:
    return json.dumps(
        {
            "job_id": manifest["job_id"],
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": json.dumps({"format": "raw-b64", "b64": "e30="}),
            "agg": {
                "policy": 0.1,
                "value": 0.2,
                "entropy": 0.3,
                "kl": 0.4,
                "mean_ret": 0.5,
            },
            "commit_echo": manifest["commit"],
        }
    ).encode("utf-8")


def test_two_workers_race_same_job_first_result_wins(tmp_path: Path) -> None:
    """两张单 hub 卡领到**同一份**最新 job（无租约）；先落账者胜，后到者 409 丢弃。"""
    base, store, srv, th = _boot(tmp_path)
    try:
        # 真实时序：worker 先连续轮询（hub 因此知道这是单课程双卡机群），job 才发布。
        assert _poll(base, "worker-a", 1) == {"job_id": None, "halt": False}
        assert _poll(base, "worker-b", 1) == {"job_id": None, "halt": False}
        m = _manifest("j" * 16)
        store.publish("j" * 16, m, b"PK\x03\x04fake")
        a = _poll(base, "worker-a", 1)
        b = _poll(base, "worker-b", 1)
        assert a["job_id"] == b["job_id"] == "j" * 16, "单课程多卡：两个 worker 领同一份"
        assert a["race"] is True and b["race"] is True
        assert a.get("lease_token") == "" and b.get("lease_token") == "", "竞速副本不下租约"
        # 先回传者胜（无租约 token 也能写）
        st1, _ = _http(base, f"/jobs/{'j' * 16}/result", "POST", _result(m))
        assert st1 in (200, 201)
        # 后到者 409（worker 侧按成功丢弃，不重试、不报失败）
        st2, body2 = _http(base, f"/jobs/{'j' * 16}/result", "POST", _result(m))
        assert st2 == 409 and "already stored" in body2["error"]
        # 结果落盘后不再派发（赢家已定）
        assert _poll(base, "worker-c", 1)["job_id"] is None
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_race_releases_earlier_exclusive_lease(tmp_path: Path) -> None:
    """先独领过的 worker 不会被后来的竞速副本打成 403（那会被读成确定性失败）。

    时序：A 独领（当时只它一个）→ B 入场转竞速 → 先前那份租约必须当场失效。
    """
    base, store, srv, th = _boot(tmp_path)
    try:
        store.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
        first = _poll(base, "worker-a", 1)  # 机群只有它 → 独占
        assert first["race"] is False and first["lease_token"]
        second = _poll(base, "worker-b", 1)  # B 一到 ⇒ 竞速开
        assert second["race"] is True and second["job_id"] == first["job_id"]
        # 旧 token 已作废但**仍能写**（无活租约）——先落账者胜
        st1, _ = _http(
            base,
            f"/jobs/{'j' * 16}/result",
            "POST",
            _result(_manifest("j" * 16)),
            headers={"X-Lease-Token": first["lease_token"]},
        )
        assert st1 in (200, 201), f"先到者必须能落账，收到 {st1}"
        st2, _ = _http(
            base,
            f"/jobs/{'j' * 16}/result",
            "POST",
            _result(_manifest("j" * 16)),
            headers={"X-Lease-Token": first["lease_token"]},
        )
        assert st2 == 409, "重复回传一律 409（绝不能 403——worker 会把 4xx 当确定性失败上报）"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_multi_hub_worker_falls_back_to_exclusive(tmp_path: Path) -> None:
    """多 hub worker（服务两个课程）在场 ⇒ 退回 P3b 独占：第二个 worker 领不到。"""
    base, store, srv, th = _boot(tmp_path)
    try:
        store.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
        a = _poll(base, "worker-a", 2)  # 服务 2 个 hub
        b = _poll(base, "worker-b", 2)
        assert a["job_id"] == "j" * 16 and a["race"] is False
        assert a["lease_token"], "独占发放必须下发 lease_token"
        assert b["job_id"] is None, "多课程机群：租约期内不重发（P3b 语义不变）"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_no_scope_header_is_conservative(tmp_path: Path) -> None:
    """旧 worker / 手写 curl（不报身份与范围）⇒ 绝不竞速：默认仍是 P3b 独占。"""
    base, store, srv, th = _boot(tmp_path)
    try:
        store.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
        st1, first = _http(base, "/jobs/next")
        st2, second = _http(base, "/jobs/next")
        assert st1 == st2 == 200
        assert first["job_id"] == "j" * 16 and first["race"] is False
        assert second["job_id"] is None
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_forced_on_overrides_scopes_and_off_restores(tmp_path: Path) -> None:
    """应急闸：on 无视 worker 声明直接广播；off 立刻退回独占（热切走 /admin/race）。"""
    base, store, srv, th = _boot(tmp_path, mode=RACE_MODE_AUTO)
    try:
        store.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
        assert _poll(base, "w1", 2)["race"] is False  # auto + 多 hub → 独占
        st, body = _http(base, "/admin/race?mode=on", "POST")
        assert st == 200 and body["race_mode"] == "on" and body["race_active"] is True
        st_g, body_g = _http(base, "/admin/race")
        assert st_g == 200 and body_g["race_active"] is True  # GET 与 POST 同源读数
        got = _poll(base, "w1", 2)
        assert got["race"] is True and got["job_id"] == "j" * 16  # 即使它服务 2 个 hub
        st2, body2 = _http(base, "/admin/race?mode=off", "POST")
        assert st2 == 200 and body2["race_active"] is False
        # off 后仍在待领池（竞速副本没落过租约、也还没结果）⇒ 退回独占领取
        back = _poll(base, "w1", 2)
        assert back["race"] is False and back["job_id"] == "j" * 16
        assert back["lease_token"], "退回独占后必须重新下发租约"
    finally:
        srv.shutdown()
        th.join(timeout=5)


# ------------------------------------------------------------------ worker 侧上报


def test_worker_reports_identity_and_hub_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """worker 在 /jobs/next 上自报身份与服务范围（hub 的竞速判据的唯一输入）。"""
    import remote.worker as w

    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append({"path": path, "headers": dict(headers or {})})
        return 200, b'{"job_id": null, "halt": false}'

    monkeypatch.setattr(w, "_request", _fake_request)
    assert w.poll_job("http://hub", "t", hub_scope=2, worker_id="host:1") is None
    assert seen[0]["path"] == "/jobs/next"
    assert seen[0]["headers"][w.WORKER_ID_HEADER] == "host:1"
    assert seen[0]["headers"][w.HUB_SCOPE_HEADER] == "2"
    # 不传（旧调用方）⇒ 不发头：hub 侧按未知处理 ⇒ 退回独占（宁可不竞速）
    seen.clear()
    assert w.poll_job("http://hub", "t") is None
    assert seen[0]["headers"] == {}


def test_worker_loop_passes_hub_count() -> None:
    """接线断言：主循环把 `len(hubs)` 当范围上报（漏了它 = 功能静默失效）。

    2026-09-22 取活换面（`poll_job` → `acquire_job` = peek+priority+claim）：身份/范围
    照旧上报（否则竞速判定会在换面那一刻静默退化），只是上报点从 poll_job 挪到新面；
    身份改为每次循环只算一次（`worker_id = worker_tag()` 提在循环外）——登记表靠这个值
    去重，值必须稳定。
    """
    from pathlib import Path

    import remote.worker as w

    src = Path(w.__file__).read_text(encoding="utf-8")
    assert "hub_scope=len(hubs)" in src, "worker_loop 必须上报自己的 hub 数"
    assert "worker_id=worker_id," in src, "取活面必须带上 worker_loop 的身份"
    assert "worker_id = worker_tag()" in src, "身份必须算一次并保持稳定（hub 靠它去重）"
    assert "def acquire_job(" in src, "取活入口（新面）不得回退成 /jobs/next"


def test_admin_race_rejects_junk_mode(tmp_path: Path) -> None:
    """非法 mode 400 且不改现状。"""
    base, store, srv, th = _boot(tmp_path)
    try:
        st, body = _http(base, "/admin/race?mode=bogus", "POST")
        assert st == 400 and "mode" in body["error"]
        assert store.race_mode == RACE_MODE_AUTO
        # 只读观测走 GET /admin/race（不往 /admin/workers/status 里叠字段——那是 console 的伴偶）
        st2, body2 = _http(base, "/admin/race")
        assert st2 == 200 and body2["race_mode"] == RACE_MODE_AUTO
        st3, body3 = _http(base, "/admin/workers/status")
        assert st3 == 200 and list(body3) == ["halt"], "workers/status 体形状不得变（console 读它）"
    finally:
        srv.shutdown()
        th.join(timeout=5)
