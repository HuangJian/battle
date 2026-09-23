"""e2e/test_push_mode_integration.py —— 纯 Push 集成测试（无真 rollout/PPO/eval）。

覆盖：
  1. `push_client.submit_job` / `wait_result` 线协议（428 补传 code、4xx 拒绝、轮询）；
  2. `_push_job_round` 多节点 failover；
  3. 真 `worker_server` × 真 `push_client`（fake starter，不跑 PPO）；
  4. `require_remote_transport`：有 gpu_push 时 hub_url 可空（无本地 hub-server）；
  5. push-first bootstrap HTTP：/ping、无 code 428、code_b64 升级解包。

纪律：不 spawn bun/node、不加载 torch；HTTP 用本机临时端口。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    WIRE_JOB_CONTENT_TYPE,
    WIRE_JOB_MAGIC,
    ProtocolError,
    RetryableError,
    pack_job_v2,
    unpack_job_v2,
)
from remote.push_client import submit_job, wait_result
from remote.worker_server import WorkerServerState, make_worker_server
from rl.loop_steps import _push_job_round, require_remote_transport


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_job_body(data: bytes) -> dict:
    """测试侧解码器：M2 B5 后 /job 体默认是 v2（BRJ2 魔数），旧 JSON 体也要能解。"""
    if data.startswith(WIRE_JOB_MAGIC):
        return unpack_job_v2(data)
    loaded = json.loads(data.decode())
    assert isinstance(loaded, dict)
    return loaded


def _mini_manifest(jid: str, payload: bytes, code_sha: str) -> dict:
    return {
        "proto": 1,
        "runId": "push-it",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": code_sha,
        "course": "{}",
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
        "payload_sha256": _sha(payload),
    }


# ────────────────────────── push_client 线协议（mock _request） ──────────────────────────


def test_submit_code_cache_hit_skips_code_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点已有 code 缓存 → 请求体不带 code_b64（M2 B5 后默认走 v2 体）。"""
    seen: list[dict] = []
    ctypes: list[str] = []

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        seen.append(_decode_job_body(data))
        ctypes.append((headers or {}).get("Content-Type", ""))
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    payload, code = b"pay", b"code"
    man = _mini_manifest("j1", payload, _sha(code))
    out = submit_job("http://n", "tok", man, payload, code, log=lambda m: None)
    assert len(seen) == 1
    assert "code_b64" not in seen[0]
    assert seen[0]["payload_b64"] == base64.b64encode(payload).decode()
    # M2 B5：默认 v2 Content-Type；计量的 body_bytes 是 v2 体长度
    assert ctypes == [WIRE_JOB_CONTENT_TYPE]
    assert out["body_bytes"] < len(json.dumps(seen[0], ensure_ascii=False).encode())


def test_submit_428_retries_with_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """428 code-missing → 下一轮重试必须带上 code_b64。"""
    bodies: list[dict] = []
    calls = {"n": 0}

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            # 真实 428 路径：探测误报已缓存 → 首次 POST 不带 code → 节点 428
            return 200, json.dumps({"cached": True}).encode()
        bodies.append(_decode_job_body(data))
        calls["n"] += 1
        if calls["n"] == 1:
            return 428, b'{"error":"code-missing"}'
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    payload, code = b"pay", b"code"
    man = _mini_manifest("j1", payload, _sha(code))
    submit_job("http://n", "tok", man, payload, code, attempts=3, log=lambda m: None)
    assert calls["n"] == 2
    assert "code_b64" not in bodies[0]  # 首次按缓存探测结果不带
    assert bodies[1]["code_b64"] == base64.b64encode(code).decode()


# ────────────────────────── M3：TS 运行时（kind=iter 的节点要用 bun 跑 rollout） ──


def test_submit_ts_code_cache_hit_skips_ts_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点已有 ts_code 缓存 → 体里不带 ts_code_b64、计量 ts_code_bytes=0。

    这条线每轮都花字节（TS 运行时与 opt 同量级）：探针失效 = 每轮白传一份 TS 树。
    """
    seen: list[dict] = []
    probed: list[str] = []

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        probed.append(path)
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        if path.startswith("/ts-code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        seen.append(_decode_job_body(data))
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    ts_zip = b"ts-tree-zip"
    payload, code = b"pay", b"code"
    man = _mini_manifest("m3-j1", payload, _sha(code))
    man["ts_code_sha256"] = _sha(ts_zip)
    out = submit_job(
        "http://n", "tok", man, payload, code, ts_code_zip=ts_zip, log=lambda m: None
    )
    assert len(seen) == 1
    assert "ts_code_b64" not in seen[0]
    assert out["ts_code_bytes"] == 0
    # 探针必须真的问过节点（不问就上传 = 每轮多传一份 TS 树）
    assert any(p.startswith("/ts-code-sha") for p in probed)


def test_submit_ts_code_uploaded_on_cache_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点无 ts_code 缓存 → 体里带 ts_code_b64，计量出实际上传字节。"""
    seen: list[dict] = []

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        if path.startswith("/ts-code-sha"):
            return 200, json.dumps({"cached": False}).encode()
        seen.append(_decode_job_body(data))
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    ts_zip = b"ts-tree-zip"
    payload, code = b"pay", b"code"
    man = _mini_manifest("m3-j2", payload, _sha(code))
    man["ts_code_sha256"] = _sha(ts_zip)
    out = submit_job(
        "http://n", "tok", man, payload, code, ts_code_zip=ts_zip, log=lambda m: None
    )
    assert seen[0]["ts_code_b64"] == base64.b64encode(ts_zip).decode()
    assert out["ts_code_bytes"] == len(ts_zip)


def test_submit_428_ts_code_missing_retries_with_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """428 ts-code-missing → 下一轮重试必须带上 ts_code_b64。

    回归点（M3 评审发现）：该分支原先只写 last、**不置 need_ts**。走到这里的典型
    场景是「探针说缓存命中、真 POST 时缓存已不在」（并发清理/两课共享节点）——此时
    need_ts 本是 False，不置真就会一直重发不带 ts 的体，白烧满重试预算后整轮失败。
    与 code-missing 的 `need_code = True` 同规。
    """
    bodies: list[dict] = []
    calls = {"n": 0}

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        if path.startswith("/ts-code-sha"):
            # 探针误报已缓存 → 首次 POST 不带 ts → 节点 428
            return 200, json.dumps({"cached": True}).encode()
        bodies.append(_decode_job_body(data))
        calls["n"] += 1
        if calls["n"] == 1:
            return 428, b'{"error":"ts-code-missing"}'
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    ts_zip = b"ts-tree-zip"
    payload, code = b"pay", b"code"
    man = _mini_manifest("m3-j3", payload, _sha(code))
    man["ts_code_sha256"] = _sha(ts_zip)
    out = submit_job(
        "http://n", "tok", man, payload, code, ts_code_zip=ts_zip, attempts=3, log=lambda m: None
    )
    assert calls["n"] == 2
    assert "ts_code_b64" not in bodies[0]  # 首次按探针结果不带
    assert bodies[1]["ts_code_b64"] == base64.b64encode(ts_zip).decode()
    assert out["ts_code_bytes"] == len(ts_zip)


def test_submit_missing_ts_code_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点无缓存且调用方没带 ts_code.zip → POST 前就 RetryableError（不占队列）。"""

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": True}).encode()
        return 200, json.dumps({"cached": False}).encode()

    monkeypatch.setattr("remote.push_client._request", fake_request)
    payload, code = b"pay", b"code"
    man = _mini_manifest("m3-j4", payload, _sha(code))
    man["ts_code_sha256"] = "t" * 64
    with pytest.raises(RetryableError, match="ts_code"):
        submit_job("http://n", "tok", man, payload, code, log=lambda m: None)


def test_submit_4xx_is_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 确定性拒绝：不做瞬时重试、最终立刻 ProtocolError。

    M2 B5 后语义微调：v2 体被 4xx 拒时会再发一次 JSON 退路（旧节点混跑），仍是**一次**
    确定性路径、不消耗瞬时重试预算；JSON 也被拒即 ProtocolError。
    """
    posts: list[tuple[str, bool]] = []

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": False}).encode()
        posts.append(((headers or {}).get("Content-Type", ""), data.startswith(WIRE_JOB_MAGIC)))
        return 400, b'{"error":"bad manifest"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    man = _mini_manifest("j1", b"p", "0" * 64)
    with pytest.raises(ProtocolError, match="400"):
        submit_job("http://n", "tok", man, b"p", b"c", log=lambda m: None)
    # 第 1 次 v2、第 2 次 JSON 退路；没有第 3 次
    assert [is_v2 for _ct, is_v2 in posts] == [True, False]
    assert posts[1][0] == "application/json"


def test_submit_v2_falls_back_to_json_on_legacy_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧节点（只认 application/json）会把 v2 体 400 掉 → 客户端退回 JSON 重发成功。

    这是 M2 B5 的安全阀：协议升级绝不能因为一次不匹配而丢掉整个 job。
    """
    posts: list[bytes] = []

    def fake_request(url, token, path, *, data=None, method=None, headers=None, timeout=30.0):
        if path.startswith("/code-sha"):
            return 200, json.dumps({"cached": False}).encode()
        posts.append(data)
        if data.startswith(WIRE_JOB_MAGIC):
            return 400, b'{"error":"Expecting value: line 1 column 1 (char 0)"}'
        return 202, b'{"status":"accepted"}'

    monkeypatch.setattr("remote.push_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    payload, code = b"pay", b"code"
    man = _mini_manifest("j1", payload, _sha(code))
    out = submit_job("http://n", "tok", man, payload, code, log=lambda m: None)
    assert len(posts) == 2 and posts[0].startswith(WIRE_JOB_MAGIC)
    # 退路体是 JSON，且逐字段与 v2 体等价（同一 payload/code）
    fallback = json.loads(posts[1].decode())
    assert fallback["payload_b64"] == base64.b64encode(payload).decode()
    assert fallback["code_b64"] == base64.b64encode(code).decode()
    # 计量如实反映**实际发出**的那一份
    assert out["body_bytes"] == len(posts[1])


def test_wait_result_polls_until_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """202/404 轮询后拿到 200；5xx 也重试。"""
    seq = [
        (202, b'{"status":"running"}'),
        (500, b"boom"),
        (200, json.dumps({"job_id": "j1", "agg": {"kl": 0.1}}).encode()),
    ]
    i = {"k": 0}

    def fake_request(url, token, path, *, timeout=30.0, **kw):
        r = seq[min(i["k"], len(seq) - 1)]
        i["k"] += 1
        return r

    # ★ 补丁打在**共用的** HTTP 出口上（R2c-3 起 `wait_result` 的单次探测走
    # `hub_client.probe_job_result`——两条链路共用一份状态码分类，所以 HTTP 出口也只有一处）。
    monkeypatch.setattr("remote.hub_client._request", fake_request)
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    out = wait_result("http://n", "tok", "j1", timeout_sec=30, poll_sec=0.01, log=lambda m: None)
    assert out["job_id"] == "j1" and out["agg"]["kl"] == 0.1
    assert i["k"] == 3


def test_wait_result_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(url, token, path, *, timeout=30.0, **kw):
        return 202, b'{"status":"running"}'

    monkeypatch.setattr("remote.hub_client._request", fake_request)
    # 让 deadline 立刻过期：time.time 先返回 t0 再返回 t0+10
    ticks = iter([1000.0, 1000.0, 1010.0, 1010.0, 1010.0])

    monkeypatch.setattr("remote.push_client.time.time", lambda: next(ticks, 1010.0))
    monkeypatch.setattr("remote.push_client.time.sleep", lambda _s: None)
    with pytest.raises(RetryableError, match="超时"):
        wait_result("http://n", "tok", "j1", timeout_sec=5, poll_sec=0.01, log=lambda m: None)


# ────────────────────────── _push_job_round failover ──────────────────────────


def test_push_job_round_failover_to_second_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点 1 确定性失败 → 自动换节点 2 成功。"""
    submitted: list[str] = []
    waited: list[str] = []

    def fake_submit(url, key, manifest, payload, code, *, echo=False, log=None, **kw):
        submitted.append(url)
        if "bad" in url:
            raise ProtocolError("HTTP 400: rejected")

    def fake_wait(url, key, jid, *, timeout_sec=30.0, log=None, **kw):
        waited.append(url)
        return {"job_id": jid, "from": url}

    # `_push_job_round` 住在 rl/loop_transport.py（S4 拆出）——patch 目标随实现走。
    monkeypatch.setattr("rl.loop_transport._push_submit", fake_submit)
    monkeypatch.setattr("rl.loop_transport._push_wait_result", fake_wait)
    nodes = [
        {"url": "http://bad.example", "authKey": "k"},
        {"url": "http://good.example", "authKey": "k"},
    ]
    result = _push_job_round(
        nodes, {"job_id": "j9"}, "j9", b"p", b"c", SimpleNamespace(smoke=False), 30.0, lambda m: None
    )
    assert submitted == ["http://bad.example", "http://good.example"]
    assert waited == ["http://good.example"]
    assert result["from"] == "http://good.example"


def test_push_job_round_all_nodes_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_submit(url, key, manifest, payload, code, *, echo=False, log=None, **kw):
        raise RetryableError(f"node {url} down")

    monkeypatch.setattr("rl.loop_transport._push_submit", fake_submit)
    with pytest.raises(RetryableError, match="全部节点失败"):
        _push_job_round(
            [{"url": "http://a", "authKey": "k"}, {"url": "http://b", "authKey": "k"}],
            {"job_id": "j1"},
            "j1",
            b"p",
            b"c",
            SimpleNamespace(smoke=False),
            5.0,
            lambda m: None,
        )


def test_push_over_nodes_prefers_the_deterministic_cause() -> None:
    """所有节点都倒时把**确定性原因**原样抛出（410 的原因不能被包成 RetryableError）。

    2026-09-17：不这样就会让「bun 装不上」被上层当瞬时失败重试 3 次（每次重新 push +
    等满超时）。这条是组合路径与拆相路径**共用**的那份 failover 判决。
    """
    from common.protocol import JobFailedError
    from rl.loop_steps import _push_over_nodes

    def step(i: int, _node: dict):
        if i == 0:
            raise JobFailedError("job j: bun 未安装", kind="ProtocolError")
        raise RetryableError("node down")

    with pytest.raises(JobFailedError, match="bun 未安装"):
        _push_over_nodes([{"url": "http://a"}, {"url": "http://b"}], 0, step, lambda m: None)


# ──────────────── 三相拆分后的 push 相位（发布即提交 / 换节点重提交） ────────────────


def _push_session(tmp_path: Path, *, bad_first: bool = True):
    """造一份直推会话 + 真 job 目录（换节点时要重读盘上 payload）。"""
    from common.protocol import PAYLOAD_NAME
    from rl.loop_round import RemotePpoJob
    from rl.loop_steps import TrainingSteps

    job_root = tmp_path / "remote-jobs"
    (job_root / "j7").mkdir(parents=True)
    (job_root / "j7" / PAYLOAD_NAME).write_bytes(b"payload-bytes")
    st = TrainingSteps()
    st.args = SimpleNamespace(smoke=False)
    st._code_zip_path = tmp_path / "code.zip"
    st._code_zip_path.write_bytes(b"code-bytes")
    first = "http://bad.example" if bad_first else "http://good.example"
    other = "http://good.example" if bad_first else "http://second.example"
    sess = RemotePpoJob(
        it=1,
        jid="j7",
        manifest={"job_id": "j7"},
        transport="push",
        nodes=[{"url": first, "authKey": "k"}, {"url": other, "authKey": "k"}],
        job_root=str(job_root),
        timeout_sec=5.0,
    )
    return st, sess


def test_push_publish_phase_submits_and_wait_phase_switches_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发布相位**只提交**；等待相位等；当前节点等待中失败 ⇒ 换下一个并**重新提交**。

    这条钉的是拆相新增的那条路：提交必须落在发布相位（否则探针问「那份 job 怎么样了」
    时节点上根本没有它），而换节点时新节点从没见过这份 job ⇒ 必须从盘上重读 payload 重发。
    """
    submitted: list[str] = []
    waited: list[str] = []

    def fake_submit(url, key, manifest, payload, code, *, echo=False, log=None, **kw):
        submitted.append(url)
        assert payload == b"payload-bytes"  # 真从 job 目录重读的（不是内存缓存）
        return {"body_bytes": 1}

    def fake_wait(url, key, jid, *, timeout_sec=30.0, log=None, **kw):
        waited.append(url)
        if "bad" in url:
            raise RetryableError("node down mid-wait")
        return {"job_id": jid, "from": url}

    # `_push_submit_first` / `_push_fetch` 住在 rl/loop_remote.py（S4 第二步）——patch 目标
    # 随实现走（同名 seam 在 loop_steps 里已不再被任何方法读取）。
    monkeypatch.setattr("rl.loop_remote._push_submit", fake_submit)
    monkeypatch.setattr("rl.loop_remote._push_wait_result", fake_wait)
    st, sess = _push_session(tmp_path)

    st._push_submit_first(sess)
    assert submitted == ["http://bad.example"] and sess.node_i == 0
    out = st._push_fetch(sess)
    assert submitted == ["http://bad.example", "http://good.example"]
    assert waited == ["http://bad.example", "http://good.example"]
    assert out["from"] == "http://good.example"
    assert sess.node_i == 1  # 探针之后要问这个节点
    assert sess.submit_wire == {"body_bytes": 1}  # 随结果上浮的传输读数仍挂着
    assert out["wire_hub"] == {"body_bytes": 1}


def test_push_probe_targets_the_node_holding_the_job(tmp_path: Path) -> None:
    """探针打的是**当前持有 job 的那个节点**（换节点后自动跟着换）。"""
    st, sess = _push_session(tmp_path, bad_first=False)
    assert sess.transport == "push"
    assert sess.probe_base_url == "http://good.example"
    sess.node_i = 1
    assert sess.probe_base_url == "http://second.example"
    assert sess.node is not None and sess.node["url"] == "http://second.example"


# ────────────────────────── 真 worker_server × 真 push_client ──────────────────────────


class _LiveWorker:
    """本机临时端口 worker_server；starter 覆盖为写结果的假执行器（无 PPO）。"""

    def __init__(self, tmp_path: Path, token: str = "tok") -> None:
        self.state = WorkerServerState(tmp_path / "work")
        self.srv = make_worker_server(self.state, 0, token)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.token = token
        self.started: list[str] = []
        #: 每 job 解包后的 item（M3：验 ts_code_zip 真字节到达执行侧）。
        self.items: dict[str, dict] = {}
        self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._thread.start()
        # 覆盖真 starter（会 spawn run_job 线程）——只记 jid 并立刻写结果。
        self.state.set_starter(self._fake_starter)

    def _fake_starter(self, jid: str, item: dict) -> None:
        self.started.append(jid)
        self.items[jid] = item
        self.state.set_result(
            jid,
            {
                "job_id": jid,
                "agg": {"kl": 0.01, "steps": 1, "chunks": 1},
                "weights_json": "{}",
                "echo": bool(item.get("echo")),
            },
        )

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _wait_ready(url: str, token: str) -> None:
    from remote.hub_client import _request

    for _ in range(50):
        try:
            st, _ = _request(url, token, "/ping", timeout=1.0)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("worker_server did not become ready")


def test_e2e_push_client_against_worker_server(tmp_path: Path) -> None:
    """submit_job → server 受理 → wait_result 拿到假 starter 写的结果。无 PPO。"""
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        payload, code = b"payload-bytes", b"code-bytes"
        man = _mini_manifest("e2e-j1", payload, _sha(code))
        submit_job(w.url, w.token, man, payload, code, echo=False, log=lambda m: None)
        assert w.started == ["e2e-j1"]
        out = wait_result(w.url, w.token, "e2e-j1", timeout_sec=10, poll_sec=0.05, log=lambda m: None)
        assert out["job_id"] == "e2e-j1"
        assert out["agg"]["steps"] == 1
    finally:
        w.close()


def test_e2e_push_body_v2_byte_accounting_and_25pct_saving(tmp_path: Path) -> None:
    """M2 B5 字节验收（本机闭环）：真 worker_server 前实测 body_bytes = v2 体长度，
    且相对于同内容的 JSON 体（base64）≥20% 更小 —— 这正是 push 模式每轮的上行。"""
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        # 确定性伪随机填充：形状像真 payload（高熵 → 压缩后仍大），字节数是确定性量。
        payload = bytes((i * 37 + 11) & 0xFF for i in range(400_000))
        code = bytes((i * 91 + 5) & 0xFF for i in range(300_000))
        blobs = {"opt": bytes((i * 13 + 7) & 0xFF for i in range(500_000))}
        man = _mini_manifest("e2e-bytes", payload, _sha(code))
        man["opt_sha"] = _sha(blobs["opt"])
        out = submit_job(w.url, w.token, man, payload, code, blobs=blobs, log=lambda m: None)
        assert w.started == ["e2e-bytes"]
        json_body = json.dumps(
            {
                "manifest": man,
                "payload_b64": base64.b64encode(payload).decode(),
                "code_b64": base64.b64encode(code).decode(),
                "blobs": {"opt": base64.b64encode(blobs["opt"]).decode()},
            },
            ensure_ascii=False,
        ).encode()
        assert out["body_bytes"] == len(pack_job_v2(man, payload, code, blobs))
        assert out["blob_bytes"] == len(blobs["opt"])
        assert out["body_bytes"] < len(json_body) * 0.8, (
            f"v2 {out['body_bytes']} 未比 JSON 体 {len(json_body)} 小 20%"
        )
    finally:
        w.close()


def test_e2e_push_smoke_echo_flag_reaches_server(tmp_path: Path) -> None:
    """X-Smoke-Echo=1 → item.echo 真，结果里可见（消费方据此作废本轮）。"""
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        payload, code = b"p", b"c"
        man = _mini_manifest("e2e-smoke", payload, _sha(code))
        submit_job(w.url, w.token, man, payload, code, echo=True, log=lambda m: None)
        out = wait_result(w.url, w.token, "e2e-smoke", timeout_sec=10, poll_sec=0.05, log=lambda m: None)
        assert out.get("echo") is True
    finally:
        w.close()


def test_e2e_upload_code_when_cache_miss(tmp_path: Path) -> None:
    """节点无 code 缓存且客户端带 code：随 job 上传后受理（无 PPO）。"""
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        payload, code = b"pay", b"code-zip"
        man = _mini_manifest("e2e-code", payload, _sha(code))
        assert w.state.code_cached(_sha(code)) is False
        submit_job(w.url, w.token, man, payload, code, log=lambda m: None)
        assert w.started == ["e2e-code"]
        out = wait_result(w.url, w.token, "e2e-code", timeout_sec=10, poll_sec=0.05, log=lambda m: None)
        assert out["job_id"] == "e2e-code"
    finally:
        w.close()


def test_e2e_missing_code_rejected_before_queue(tmp_path: Path) -> None:
    """无缓存且不带 code_zip：客户端在 POST 前即 RetryableError，不占队列。"""
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        payload = b"pay"
        man = _mini_manifest("e2e-nocode", payload, "b" * 64)
        with pytest.raises(RetryableError, match="code"):
            submit_job(w.url, w.token, man, payload, None, attempts=1, log=lambda m: None)
        assert w.started == []
    finally:
        w.close()


def test_e2e_ts_code_uploaded_and_gated_by_node_cache(tmp_path: Path) -> None:
    """M3 真 worker_server 闭环：无缓存 → 上传 TS 运行时并受理；有缓存 → 不再上传。

    真 `_LiveWorker` 的 `state.ts_code_cached` 看的是 `work/<ts_code_cache>/<sha>`——
    测试就位一个同名目录，复现「同会话第二轮」（缓存已在）这一形态。
    """
    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        ts_zip = b"ts-tree-zip-bytes"
        ts_sha = _sha(ts_zip)
        payload, code = b"pay", b"code-ts"
        man = _mini_manifest("e2e-ts1", payload, _sha(code))
        man["ts_code_sha256"] = ts_sha
        assert w.state.ts_code_cached(ts_sha) is False
        out = submit_job(w.url, w.token, man, payload, code, ts_code_zip=ts_zip, log=lambda m: None)
        assert w.started == ["e2e-ts1"]
        assert out["ts_code_bytes"] == len(ts_zip)
        # 真 server 解出的 ts 段字节 == 原字节（不是被静默丢掉的一段）
        assert w.items["e2e-ts1"].get("ts_code_zip") == ts_zip
        # 缓存已就位 → 第二轮探针命中，不需要再传
        (tmp_path / "work" / "ts_code_cache" / ts_sha).mkdir(parents=True, exist_ok=True)
        payload2 = b"pay2"
        man2 = _mini_manifest("e2e-ts2", payload2, _sha(code))
        man2["ts_code_sha256"] = ts_sha
        out2 = submit_job(w.url, w.token, man2, payload2, code, log=lambda m: None)
        assert out2["ts_code_bytes"] == 0
    finally:
        w.close()


def test_e2e_node_reports_ts_code_missing_428(tmp_path: Path) -> None:
    """节点无 TS 缓存且体里没带 → server 回 428 ts-code-missing（不静默跑空）。

    直接打线协议（不走 push_client 的前置守卫），验的是**服务端**这一侧的门。
    """
    from remote.hub_client import _request

    w = _LiveWorker(tmp_path)
    try:
        _wait_ready(w.url, w.token)
        payload, code = b"pay", b"code-ts-428"
        man = _mini_manifest("e2e-ts428", payload, _sha(code))
        man["ts_code_sha256"] = "t" * 64
        body = json.dumps(
            {
                "manifest": man,
                "payload_b64": base64.b64encode(payload).decode(),
                "code_b64": base64.b64encode(code).decode(),
            }
        ).encode()
        status, resp = _request(
            w.url,
            w.token,
            "/job",
            timeout=10.0,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        assert status == 428
        assert b"ts-code-missing" in resp
        assert w.started == []  # 未受理（不占队列）
    finally:
        w.close()


# ────────────────────────── 纯 push 传输门 + bootstrap 升级 ──────────────────────────


def test_require_remote_transport_push_without_hub() -> None:
    """纯 push：有 gpu_push 节点时 hub_url 可空（不再依赖本地 hub-server）。"""
    require_remote_transport(
        hub_url="",
        token="tok",
        gpu_nodes=[{"url": "https://gpu", "authKey": "k"}],
        env_push=None,
    )
    with pytest.raises(SystemExit, match=r"hub_url|gpu_push"):
        require_remote_transport("", "tok", [], env_push=None)


def test_bootstrap_http_upgrade_and_428(tmp_path: Path) -> None:
    """push-first bootstrap：/ping、无 code 428、带 code_b64 升级并解包。"""
    import base64 as b64
    import io as _io
    import zipfile
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from remote.push_bootstrap import start_bootstrap_server

    upgraded: list[bytes] = []
    code_dir = tmp_path / "code"
    srv = start_bootstrap_server(
        0, "tok", code_dir, on_upgrade=lambda raw: upgraded.append(raw)
    )
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:

        def _req(path, data=None):
            r = Request(
                f"http://127.0.0.1:{port}{path}",
                data=data,
                headers={"Authorization": "Bearer tok", "Content-Type": "application/json"},
                method="POST" if data else "GET",
            )
            with urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())

        st, ping = _req("/ping")
        assert st == 200 and ping.get("bootstrap") is True
        st, sha = _req("/code-sha?sha=abc")
        assert st == 200 and sha.get("cached") is False

        # 无 code → 428
        body = json.dumps({"manifest": {"job_id": "j"}, "payload_b64": "e30="}).encode()
        try:
            _req("/job", body)
            raise AssertionError("expected 428")
        except HTTPError as e:
            assert e.code == 428

        # 带 code_b64 → 202 + 解包 + on_upgrade
        zbuf = _io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            z.writestr("hello.txt", "hi")
        payload = json.dumps(
            {
                "manifest": {"job_id": "j1"},
                "payload_b64": "e30=",
                "code_b64": b64.b64encode(zbuf.getvalue()).decode(),
            }
        ).encode()
        st, out = _req("/job", payload)
        assert st == 202 and out.get("upgrading") is True
        assert (code_dir / "hello.txt").read_text(encoding="utf-8") == "hi"
        assert upgraded and upgraded[0] == payload
    finally:
        srv.shutdown()
        srv.server_close()
