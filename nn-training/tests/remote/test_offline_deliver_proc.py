"""tests/remote/test_offline_deliver_proc.py —— 补传腿的**独立子进程**（2026-10-07 P1）。

慢层（本文件）：真起子进程 + 真 HTTP（`ThreadingHTTPServer` + 端口 0 —— 子进程里没法注入
opener，所以这一层用真 socket 说真话）。快层（假对象、秒级）在
`tests/remote/test_offline_deliver_async.py`；两层拆开是为了「只跑慢的那个」。

本文件钉住的是 `plan/offline-deliver-isolation.plan.md` §3/§10 里那些**错了就静默丢数据**的点：

  1. `submit_*` **非阻塞**（入队与网络无关）且积压能被推完（`delivered.json` 记满）；
  2. `close(timeout)` **有界**：hub 卡住时按预算收线，卡在 HTTP 里的子进程会被 terminate；
  3. `stop` 之后子进程退出码 0（非零退出要响亮——降级用例里也是这条）；
  4. **token 不进 argv**（`/proc/<pid>/cmdline` 全机可读），只从 `$BATTLE_HUB_TOKEN` 读；
  5. 控制文件**增量消费**：半行 `final` 留在缓冲区，拼好后仍然送达（C4）；
  6. **重启不重放**：子进程被杀后按需重启，从已写字节处继续（C15）；
  7. **起不来 = 上线后的常态**（在飞的 `code.zip` 里没有本模块）⇒ **sticky 降级线程模式 +
     响亮一行 + 补传仍然送达**（C8/C9；这是本功能在真机上的主路径）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER, OFFLINE_ARTIFACT_PATH, OFFLINE_RESULT_PATH
from remote import deliver_proc as dp
from remote.deliver_proc import DelivererProcess
from tests.remote.test_offline_deliver import RUN, _make_artifacts  # type: ignore

TOKEN = "sekret-proc"


def _wait_until(pred, *, timeout: float = 30.0, step: float = 0.02) -> bool:
    """等一个**状态**成立（`timeout` 只是挂起兜底，不是同步手段）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


class _HubStub:
    """最小 hub：`GET /ping`（验 token）+ `POST /offline/{artifact,result}`（记账体）。

    只要这三条就够：补传腿的探活在 `/ping`，投递在 `/offline/artifact`，段末摘要在
    `/offline/result`；`fail` 开关 = 隧道断线（530，**不是** 401/403 ⇒ 不停用整条腿）。
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        #: 收到的**全部**请求路径（含被卡住/被 530 的那些——`requests` 只记走到业务处理面的）。
        self.hits: list[str] = []
        self.bodies: dict[str, list[dict]] = {}
        self.fail = False
        self.hang_path = ""  # 命中它的请求会「卡住」（模拟隧道挂死）
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None:  # 静音（门禁不稀罕 access log）
                pass

            def _serve(self, body: bytes | None) -> None:
                outer.hits.append(self.path)
                # 卡死优先于 530：这两个开关一起用时，要的是「隧道挂死」而不是「快速失败」。
                if outer.hang_path and self.path.endswith(outer.hang_path):
                    # sleep-ok: 夹具模拟的工作量：一个卡死的隧道（客户端只能等自己的超时）
                    time.sleep(30.0)
                    return
                if outer.fail:
                    self._reply(530, b"{}")
                    return
                if self.headers.get(AUTH_HEADER) != f"Bearer {TOKEN}":
                    self._reply(403, b'{"error":"bad token"}')
                    return
                outer.requests.append((self.command, self.path))
                if self.command == "GET":
                    self._reply(200, b"{}")
                    return
                raw = body or b""
                got: dict = {}
                if raw:
                    try:
                        loaded = json.loads(raw.decode("utf-8"))
                        got = loaded if isinstance(loaded, dict) else {}
                    except ValueError:
                        got = {}
                outer.bodies.setdefault(self.path, []).append(got)
                self._reply(200, b"{}")

            def _reply(self, code: int, payload: bytes) -> None:
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

            def do_GET(self) -> None:  # BaseHTTPRequestHandler 的约定命名
                self._serve(None)

            def do_POST(self) -> None:  # BaseHTTPRequestHandler 的约定命名
                n = int(self.headers.get("Content-Length") or 0)
                self._serve(self.rfile.read(n) if n else b"")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def artifact_bodies(self) -> list[dict]:
        return self.bodies.get(OFFLINE_ARTIFACT_PATH, [])

    def result_bodies(self) -> list[dict]:
        return self.bodies.get(OFFLINE_RESULT_PATH, [])

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)


@pytest.fixture
def hub() -> Iterator[_HubStub]:
    stub = _HubStub()
    try:
        yield stub
    finally:
        stub.close()


@pytest.fixture(autouse=True)
def _no_real_hub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发机的真 `$BATTLE_HUB_TOKEN` 不进这个文件：子进程**继承环境**，而本层打的是本地 stub。

    token 由构造参数显式给（走 ctl 文件），环境里留着真的只会多一条「打到真面」的路
    （`plan/offline-deliver-isolation.plan.md` §10.6-4）。
    """
    monkeypatch.delenv("BATTLE_HUB_TOKEN", raising=False)


class _State:
    """一个段的工作面：产物目录 + work/ + 交付器（进程模式）。"""

    def __init__(self, tmp_path: Path, hub: _HubStub, **kw: object) -> None:
        self.root = _make_artifacts(tmp_path / "art", iters=(1, 2, 3))
        self.work = tmp_path / "work"
        self.logs: list[str] = []
        self.d = DelivererProcess(
            base_url=hub.url,
            token=TOKEN,
            run_id=RUN,
            artifacts_dir=self.root,
            work_dir=self.work,
            log=self.logs.append,
            **kw,  # type: ignore[arg-type]
        )

    def ledger(self) -> dict:
        data = json.loads((self.root / "delivered.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}


def test_process_mode_pushes_the_backlog_and_exits_cleanly(tmp_path: Path, hub: _HubStub) -> None:
    """端到端：入队非阻塞 → 积压推完 → `stop` 后子进程干净退出（code 0）。"""
    st = _State(tmp_path, hub)
    d = st.d
    d.start()
    try:
        assert d.status()["mode"] == "process"
        assert d.status()["drain_alive"] is True, f"子进程没起来：{st.logs}"
        t0 = time.time()
        for it in (1, 2, 3):
            d.submit_round(it)
        queued = time.time() - t0
        assert queued < 0.1, f"入队被网络拖住了 {queued:.3f}s（应只 append 一行即回）"
        assert _wait_until(lambda: len(hub.artifact_bodies()) == 3), (
            f"三轮回传没推完（实到 {len(hub.artifact_bodies())}）——日志：{st.logs}"
        )
        assert sorted(int(b.get("it") or 0) for b in hub.artifact_bodies()) == [1, 2, 3]
        assert all(str(b.get("weights_fp", "")) for b in hub.artifact_bodies()), "权重指纹缺失"
        # 状态面：子进程把「它自己看到的」写进文件（父侧只读它，不猜）。
        # 等它**落到盘上**再断言：写状态在一个 tick 的末尾（推完积压之后），而上面那几条
        # 断言可能正好抢在那一刻之间（delivered 是从账本读的，所以它先到）—— 不要赌。
        status_file = Path(str(d.status()["status_file"]))
        assert _wait_until(lambda: status_file.exists(), timeout=15.0), (
            f"子进程从未写出状态文件：{st.logs}"
        )
        assert _wait_until(lambda: int(d.status()["delivered"]) == 3, timeout=15.0), st.logs
        status = d.status()
        assert status["pending"] == 0 and status["reposts_unsent"] == 0
        assert status["restarts"] == 0 and status["mode"] == "process"
    finally:
        d.close(timeout=20.0)
    assert d.pending() == []
    assert sorted(st.ledger()["artifacts"]) == [1, 2, 3]
    assert not any("非零退出" in m for m in st.logs), f"子进程应干净退出：{st.logs}"
    assert any("补传" in m for m in st.logs), f"子进程的日志必须被逐行转发上来：{st.logs}"


def test_close_is_bounded_when_the_child_hangs_in_http(
    tmp_path: Path, hub: _HubStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hub 卡住时按预算收线：子进程卡在 HTTP 里 ⇒ 余量过后 `terminate()`（有界，不无限等）。

    余量在测试里缩到 1s（生产 10s）——判据是「升级路径真的走到了」，不是墙钟数字。
    """
    monkeypatch.setattr(dp, "PROC_CLOSE_GRACE_SEC", 1.0)
    st = _State(tmp_path, hub)
    d = st.d
    hub.hang_path = "/ping"  # 从头就把探活卡死：子进程第一次探活就出不来（只能被 terminate）
    d.start()
    assert _wait_until(lambda: any(p.endswith("/ping") for p in hub.hits), timeout=15.0), (
        f"子进程没探到 /ping（卡死夹具没生效）：{st.logs}"
    )
    spent = None
    try:
        t0 = time.time()
        d.close(timeout=0.5)
        spent = time.time() - t0
    finally:
        hub.hang_path = ""
    # timing-ok: 上界兜底（只兜「有界收线失效」；预算 0.5 + 余量 1 + 升级时间，余量 4×）
    assert spent is not None and spent < 6.0, (
        f"close 拖了 {spent:.2f}s（有界收线失效；budget 0.5 + 余量 1 + 重启余量）"
    )
    assert d.status()["drain_alive"] is False, "收线后子进程必须已经不在"
    assert any("terminate" in m or "未按预算收线" in m for m in st.logs), f"升级路径没走：{st.logs}"


def test_control_file_is_consumed_incrementally_and_half_lines_survive(
    tmp_path: Path, hub: _HubStub
) -> None:
    """控制通道的 C4 纪律：半行 `final` 留在缓冲区，拼好后**仍然送达**（不许丢）。"""
    st = _State(tmp_path, hub)
    d = st.d
    d.start()
    try:
        assert _wait_until(lambda: bool(d.status()["ctl"])), st.logs
        ctl = Path(str(d.status()["ctl"]))
        # 父侧写到一半（`{"final": …}` 完全可能 > 4KB：POSIX 单次 write 的原子边界只有 ~4KB）
        partial = b'{"final": {"it_end": 7, "state": "comp'
        with ctl.open("ab") as f:
            f.write(partial)
            f.flush()
        # 等子进程**真的读到了那半行**（状态面里的 `ctl_offset` 是它自己的读偏移）：它必须把它
        # 留在缓冲区里等下半截，而不是当「坏行」跳过去 —— 跳过去就永远拼不回来了。
        assert _wait_until(
            lambda: int(d._child_status().get("ctl_offset") or 0) >= len(partial), timeout=10.0
        ), f"子进程没读到半行：{st.logs}"
        with ctl.open("ab") as f:
            f.write(b'lete", "summary": {"last_it": 7}, "end_it_reached": true}}\n')
            f.flush()
        assert _wait_until(lambda: len(hub.result_bodies()) == 1, timeout=20.0), (
            f"半行 final 没被拼起来送达：{st.logs}"
        )
        got = hub.result_bodies()[0]
        assert got["it_end"] == 7 and got["end_it_reached"] is True
        assert got["summary"] == {"last_it": 7}
    finally:
        d.close(timeout=10.0)


def test_restart_does_not_replay_consumed_commands(tmp_path: Path, hub: _HubStub) -> None:
    """★C15：子进程被杀后按需重启 ⇒ 从已写字节处继续，**不重放**已消费的 `final`。

    判据：段末摘要只被投一次（若从头重放，就会看到第二次 `/offline/result` POST）。
    """
    st = _State(tmp_path, hub)
    d = st.d
    d.start()
    try:
        d.submit_final(it_end=3, state="complete", summary={"last_it": 3}, end_it_reached=True)
        assert _wait_until(lambda: len(hub.result_bodies()) == 1, timeout=20.0), st.logs
        proc = d._proc
        assert proc is not None
        proc.kill()  # 夹具模拟的工作量：子进程「崩了」（OOM / 被平台杀掉）
        proc.wait(timeout=10.0)
        # 下一轮唤醒 ⇒ 发现子进程已死 ⇒ 按需重启（offset = 已写字节）+ 响亮一行
        d.submit_round(1)
        assert _wait_until(lambda: d.status()["restarts"] == 1, timeout=20.0), st.logs
        assert any("按需重启" in m for m in st.logs), f"重启必须响亮：{st.logs}"
        assert _wait_until(lambda: len(hub.artifact_bodies()) == 3, timeout=25.0), (
            f"重启后积压没推完：{st.logs}"
        )
    finally:
        d.close(timeout=10.0)
    assert len(hub.result_bodies()) == 1, (
        f"重启重放了已消费的 final（result POST 出现 {len(hub.result_bodies())} 次）"
    )


def test_boot_failure_degrades_to_thread_mode_and_still_delivers(
    tmp_path: Path, hub: _HubStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★C8/C9 主路径：包太旧（没有 `remote.deliver_worker`）⇒ **sticky 降级 + 照样送达**。

    在飞的 `code.zip` 是导出那一刻的快照，里面**没有**本模块 ⇒ 上线后第一次遇到的形态**必然**
    是「起不来」。所以这不是异常路径，是常态路径：降级一次、记一行、之后不再重试 `Popen`，
    而补传本身照旧工作（训练永远不受影响）。
    """
    calls: list = []
    real_popen = subprocess.Popen

    def counting_popen(argv, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return real_popen(argv, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "Popen", counting_popen)
    st = _State(tmp_path, hub, worker_module="remote.deliver_worker_missing_on_purpose")
    d = st.d
    try:
        d.start()
        assert d.status()["mode"] == "thread", f"没降级：{st.logs}"
        assert any("降级为进程内线程模式" in m for m in st.logs), f"降级必须响亮：{st.logs}"
        first = len(calls)
        d.submit_round(1)
        assert _wait_until(lambda: bool(hub.artifact_bodies()), timeout=25.0), (
            f"降级之后补传必须照常工作：{st.logs}"
        )
        assert len(calls) == first, "降级必须 sticky（不许每轮重试 Popen）"
    finally:
        d.close(timeout=15.0)
    assert d.status()["mode"] == "thread"
    assert d.pending() == [], "线程兜底也要把积压推完"


