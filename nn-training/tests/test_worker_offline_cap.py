"""worker 的**归属角色**自报（2026-09-19 落地离线模式，2026-09-25 语义升级为归属）。

头名的字面量仍是 `X-Battle-Offline`（混合部署兼容，见 `protocol.ROLE_HEADER`），但语义
从「**能力**声明」（我能自己跑完整段）换成了「**归属**声明」（本会话属于哪块盘）：
`kind=run` 整段**确实**由 tailscale 盘跑得动——事故正是「有能力的盘接走了不属于它的整段
job」（2026-09-25，plan/online-offline-role-routing.plan.md §1）。⇒ 一个盘一种任务：
带标 worker 领整段、**不**领在线盘的活（旧口径「带标仍可领在线课」已作废）。

本文件钉**跨层契约的两半**：
  ① worker 侧：归属声明 → peek **与 claim** 都带 `X-Battle-Offline: 1`（缺省不带，逐字不变）；
  ② 端到端：真 hub（进程内 HTTP）+ 真取活助手 —— 归属 offline 的 job 对在线盘不可见/不可领，
     对离线盘可见/可领；**按 id 直领**（`/jobs/{id}/claim`）同样受闸。

漏了哪一半的代价都是**静默**的：漏发头 = 整段 job 永远没人领（看着像「节点都不在线」）；
只给 peek 带而 claim 不带 = 带标 worker 自锁（peek 看得到、claim 那一步被归属闸当在线盘
当场拒）——那是本文件里最容易被漏的一跳。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import remote.worker as W
from remote.hub_server import _HubQueue, make_server
from remote.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
    ROLE_OFFLINE,
    ROLE_ONLINE,
    WORKER_ID_HEADER,
)
from tests.helpers.hub_poll import hub_poll

TOKEN = "sekret"


# ---------------------------------------------------------------- ① 头


def test_peek_sends_role_header_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`role=offline` ⇒ 带上归属头（值取协议常量，不在 worker 里再写一份字面量）。"""
    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append({"path": path, "headers": dict(headers or {})})
        return 200, b'{"jobs": [], "halt": false}'

    monkeypatch.setattr(W, "_request", _fake_request)
    assert W.peek_jobs("http://hub", "t", worker_id="host:1", role=ROLE_OFFLINE) == ([], False)
    assert seen[0]["headers"][ROLE_HEADER] == ROLE_HEADER_VALUE
    assert seen[0]["headers"][WORKER_ID_HEADER] == "host:1"


def test_peek_sends_no_role_header_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省 role=online ⇒ 不发头 —— hub 侧按在线盘处理，行为逐字节不变。"""
    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append(dict(headers or {}))
        return 200, b'{"jobs": [], "halt": false}'

    monkeypatch.setattr(W, "_request", _fake_request)
    assert W.peek_jobs("http://hub", "t") == ([], False)
    assert ROLE_HEADER not in seen[0]


def test_claim_sends_role_header_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ **claim 也必须带头**（F6 的老毛病 + 自锁陷阱）。

    归属闸下沉到 `_JobStore._claim_locked` 之后，claim 不带头 = 带标 worker 被当在线盘，
    自己 peek 到的活当场被拒（peek 绿、claim 红——最难看的一种）。
    """
    seen: list[dict] = []

    def _fake_request(base, token, path, timeout=30.0, data=None, method=None, headers=None):
        seen.append({"path": path, "headers": dict(headers or {})})
        return 200, b'{"job_id": "j", "status": "ok", "lease_token": "t"}'

    monkeypatch.setattr(W, "_request", _fake_request)
    assert W.claim_job("http://hub", "t", "j" * 16, worker_id="host:1", role=ROLE_OFFLINE)
    assert seen[0]["path"].endswith("/claim")
    assert seen[0]["headers"][ROLE_HEADER] == ROLE_HEADER_VALUE
    assert seen[0]["headers"][WORKER_ID_HEADER] == "host:1"
    # 在线盘请求不带这条头（旧 worker 行为逐字不变）
    seen.clear()
    assert W.claim_job("http://hub", "t", "j" * 16, worker_id="host:1")
    assert ROLE_HEADER not in seen[0]["headers"]


def test_worker_loop_forwards_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """接线断言：主循环把 `role` 透传给取活面（漏了它 = 功能静默失效）。

    2026-09-22 换面后取活 = `acquire_job`（peek → priority → claim）；它把 `role` 继续
    透给 `peek_jobs` / `claim_job` 的同名头（本文件上面三个用例守着这两跳）。
    """
    seen: list[dict] = []

    def _fake_poll(*a, **k):
        seen.append(k)
        return None  # once=True ⇒ 立刻干净退出

    monkeypatch.setattr(W, "acquire_job", _fake_poll, raising=True)
    n = W.worker_loop(
        "http://hub", "tok", work_dir=Path("/tmp/x"), poll_sec=0.0, once=True, role=ROLE_OFFLINE
    )
    assert n == 0
    assert seen[0]["role"] == ROLE_OFFLINE
    # 预取线程（同一份归属）也要透到：漏了它 = 预取拿别的盘的活，白烧带宽
    src = (Path(__file__).resolve().parent.parent / "remote" / "worker.py").read_text(
        encoding="utf-8"
    )
    assert '"role": role,' in src, "预取线程的 kwargs 没带 role"

    seen.clear()
    W.worker_loop("http://hub", "tok", work_dir=Path("/tmp/x"), poll_sec=0.0, once=True)
    assert seen[0]["role"] == ROLE_ONLINE, "缺省必须是在线盘（保守方向）"


def test_main_offline_flag_reaches_worker_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI：`--offline` → `worker_loop(role='offline')`（子进程分支就是 notebook 走的那条）。

    FLAG 名不变（notebook 的 `CFG["offline_worker"]` 链路照旧），变的是它解析出的**归属**。
    """
    seen: dict[str, object] = {}

    def _fake_loop(*a, **k):
        seen.update(k)
        raise SystemExit(0)

    monkeypatch.setattr(W, "worker_loop", _fake_loop, raising=True)
    monkeypatch.setenv("REMOTE_WORKER_CHILD", "1")
    tok = tmp_path / "t"
    tok.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setattr(
        W.sys,
        "argv",
        ["remote_worker", "--poll", "http://x", "--token-file", str(tok), "--offline"],
    )
    with pytest.raises(SystemExit):
        W.main()
    assert seen["role"] == ROLE_OFFLINE


def test_notebook_pull_worker_adds_offline_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """notebook 的 pull 分支：`CFG["offline_worker"]` ⇒ 监督器 argv 带 `--offline`。"""
    import remote.notebook_runtime as nbr

    captured: list[list[str]] = []

    @contextmanager
    def _fake_open(req, timeout=15.0):
        class _Resp:
            def read(self) -> bytes:
                return b"ok"

        yield _Resp()

    monkeypatch.setattr(nbr, "_hub_open", _fake_open, raising=True)
    # `supervise_worker` 是**函数内**导入的（`from remote.worker import …`）⇒ 补它的源模块
    def _fake_supervise(argv):
        captured.append(list(argv))
        return 0

    monkeypatch.setattr(W, "supervise_worker", _fake_supervise, raising=True)
    cfg = {
        "hub_url": "http://hub",
        "hub_token": TOKEN,
        "work_dir": "/tmp/x",
        "device_resolved": "cpu",
        "idle_floor_sec": 60,
        "max_session_hours": 2,
        "poll_interval_sec": 1,
    }
    assert nbr.run_pull_worker({**cfg, "offline_worker": True}, lambda _m: None) == 0
    assert "--offline" in captured[-1]
    assert nbr.run_pull_worker(cfg, lambda _m: None) == 0
    assert "--offline" not in captured[-1], "缺省不带标"


# ---------------------------------------------------------------- ② 端到端


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def test_run_job_forwards_the_hub_course_into_the_backfeed() -> None:
    """补传的**归位键**（课程键）链路仍然完整 —— 但入口只剩取包腿（队列腿 2026-09-25 退役）。

    为什么用源码断言：链路中段是「跑完一整段的真 PPO」——单测里跑不起来，而这一跳断掉的
    表现极其隐：多课程 hub 下每条补传都被 400「无法归属课程」拒掉，节点侧补传整体停用，
    而训练本身完全正常（只有控制台看不到段内进度）。端到端那条在 `e2e/test_offline_training_e2e.py`。

    ★ 2026-09-25（plan/online-offline-role-routing §7）：「worker 领到 kind=run job 后自己把
    剩下轮次跑完」那条腿退役 ⇒ worker 侧**不再有**这一跳透传（它连 kind=run 都拒收）；
    归位键今天由取包腿给（`run_standalone --hub-course`）。所以这里同时钉住「worker 侧
    不再透传」——防止有人把队列腿连人带键一起复活。
    """
    root = Path(__file__).resolve().parent.parent
    src = (root / "remote" / "worker.py").read_text(encoding="utf-8")
    assert 'hub_course=str(job.get("course") or "")' not in src  # 队列腿已退役（连带这一跳）
    # 下游每一跳都真的接这个形参（漏一跳 = 云机上 TypeError，或值静默丢掉）
    run_loop_src = (root / "remote" / "run_loop.py").read_text(encoding="utf-8")
    assert run_loop_src.count('hub_course: str = ""') == 3  # run_plan_job/open_run_context/run_standalone
    assert "hub_course=hub_course" in run_loop_src
    assert "course=hub_course" in run_loop_src  # make_deliverer 那一跳
    assert "hub_course=args.hub_course" in run_loop_src  # CLI（全离线包那条腿）


def _publish_offline_course(root: Path, course: str, jid: str) -> None:
    """在盘上造一门**已开课**的课，里面躺着一份 `kind=run`（整段 ⇒ 离线盘的活）的 job。

    ⚠ 这里**不**调 `set_mode(OFFLINE)`：归属是 job 自己的属性（发布时定死），与课程当前
    mode 无关——这正是本文件要钉住的那条（旧口径靠 mode，切一次就漂）。
    """
    from remote.hub_server import _JobStore

    job_root = root / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    jsonl = root / course / "training_log.jsonl"
    jsonl.touch()
    # 开课标记 = hub/训练侧的「在训」闸（2026-09-20）；不写它，hub 不登记这门课。
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    store = _JobStore(job_root, jsonl)
    store.publish(
        jid,
        {
            "job_id": jid,
            "kind": "run",
            "run_id": "r1",
            "it": 1,
            "course": course,
            "data_fp": "d",
            "init_weights_fp": "w",
            "commit": "c" * 40,
            "code_sha256": "e" * 64,
            "ts_code_sha256": "f" * 64,
        },
        b"PK\x03\x04fake",
    )


def test_offline_role_job_is_invisible_to_online_disk_and_claimable_by_offline(
    tmp_path: Path,
) -> None:
    """真 HTTP 端到端：在线盘领不到整段 job；离线盘领得到（同一份 job）。

    课程 mode 与位置：这里把它显式切成 OFFLINE **也不行/不需要** —— 归属只看 job 的 `role`。
    """
    base, hub, srv = _boot(tmp_path)
    try:
        _publish_offline_course(tmp_path, "c5-gae", "j" * 16)
        assert hub.discover() == ["c5-gae"]
        # 课程 mode 是**在线**：看看它能不能改变归属（不能——旧口径就是在这里读 mode 的）
        assert hub.mode_of("c5-gae") != COURSE_MODE_OFFLINE

        # 在线盘：hub 说「没有可领的 job」（整段 job 只给离线盘）
        assert hub_poll(base, TOKEN, worker_id="plain") is None
        # 离线盘：同一份 job 立刻到手，并且响应自报归属课程（对账用）
        got = hub_poll(base, TOKEN, worker_id="marked", role=ROLE_OFFLINE)
        assert got is not None and got["job_id"] == "j" * 16
        assert got["course"] == "c5-gae"
        assert got["manifest"]["kind"] == "run"
        assert got["manifest"].get("role") in (None, ROLE_OFFLINE), (
            "旧 job 无 role 字段 ⇒ 由 kind 兜底；新 job 应显式为 offline"
        )
    finally:
        srv.shutdown()
        srv.server_close()
