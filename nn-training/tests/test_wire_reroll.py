"""test_wire_reroll.py — 大 body 的**低速重抽**与**每 job 传输账**（2026-09-20；plan §4.0 / M1）。

现场（同一台云机、同一个 hub，两次会话）：

  * 12:49 —— `code` GET 1.37 MB 花了 106 s（**13.5 KB/s**），而 **3 秒之后**的 opt_init blob
    GET ≥120 KB/s（无进度行 ⇒ <5 s）：慢的不是端点，是**每条连接抽签**。
  * 12:59 —— `payload` 3.32 MB 以 **6–9 KB/s** 烧完了整个 300 s 预算（到 75% 才被
    `BODY_TOTAL_TIMEOUT_SEC` 判死）；**重试换连接后 354 KB/s**，剩下 0.6 MB 只用 5 s。
    那次「重试」其实就是一次**意外重抽**，只是由总预算在 300 s 之后触发，而不是首块的 ~39 s。

判据（M1）：首块就慢得离谱 ⇒ 主动断开、换连接重发。本文件钉五件事：

  ① 判据本身（纯函数 `_reroll_decision`：阈值 / 样本 / 预计剩余 / 长度未知）；
  ② 相对阈值 `max(80KB/s, 本会话最好速率/4)`（小 body 不参与采样）；
  ③ 重抽**有界**且**最后一次尝试永不重抽**（慢链路不会变成「永远下不完」）；
  ④ 浪费**有界**（判据只在首块跑一次 ⇒ 每次浪费 ≤ 一块，绝不可能整份重传）；
  ⑤ 每 job 一行传输账（`(endpoint, bytes, sec)` + 零字节命中 + 重抽），
     且 **POST result 永不重抽**（它是产物上行，走既有的 5 次退避）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import worker as worker_mod
from remote.protocol import RetryableError

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_wire_state():
    """本文件的用例都动模块级状态（传输账 + 会话最好速率）——每例前后清干净。"""
    worker_mod._WIRE.clear()
    worker_mod._BEST_RATE = 0.0
    yield
    worker_mod._WIRE.clear()
    worker_mod._BEST_RATE = 0.0


class _FakeResp:
    """假响应：逐块吐数据（供 `_read_body` 用；headers 给 Content-Length）。"""

    def __init__(self, chunks: list[bytes], total: int):
        self._chunks = list(chunks)
        self.headers = {"Content-Length": str(total)}
        self.status = 200

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


# ────────────────────────────── ① 判据（纯函数） ──────────────────────────────


def test_reroll_decision_matches_the_live_slow_draw() -> None:
    """12:59 现场：首块 256 KB 用了 39 s ⇒ 6.4 KB/s、预计剩余 ~1180 s ⇒ **该重抽**。"""
    should, rate, remain = worker_mod._reroll_decision(256 * 1024, 3486200, 39.0, min_rate=80 * 1024)
    assert should is True
    assert rate < 80 * 1024
    assert remain > worker_mod.WIRE_REROLL_BUDGET_SEC


def test_reroll_decision_matches_the_live_fast_draw() -> None:
    """同一份 body 的**好签**（同步 354 KB/s 那种）不该被重抽。"""
    should, rate, _ = worker_mod._reroll_decision(256 * 1024, 3486200, 0.72, min_rate=80 * 1024)
    assert should is False
    assert rate > 80 * 1024


def test_reroll_decision_waits_for_a_usable_sample() -> None:
    """样本太小（既没够字节、也没够时间）不下结论——免得拿 2 KB 判一条链路。"""
    should, _, _ = worker_mod._reroll_decision(1024, 10 * MB, 0.01, min_rate=80 * 1024)
    assert should is False


def test_reroll_decision_handles_edge_inputs() -> None:
    """长度未知（无 Content-Length）/ 已收完 / 零字节：都不重抽（三个都是「不该赌」的形状）。"""
    for got, total in ((0, 0), (100, 0), (3486200, 3486200), (10 * MB, 10 * MB)):
        should, _, _ = worker_mod._reroll_decision(got, total, 10.0, min_rate=80 * 1024)
        assert should is False, (got, total)


def test_reroll_decision_honours_the_remaining_time_budget() -> None:
    """慢，但**马上就完了**（预计剩余 < 20 s）⇒ 不折腾：重抽也有建连成本。"""
    # 8 KB/s、还剩 80 KB ⇒ 预计 10 s < 20 s
    should, rate, remain = worker_mod._reroll_decision(32 * 1024, 112 * 1024, 4.0, min_rate=80 * 1024)
    assert rate < 80 * 1024
    assert remain < worker_mod.WIRE_REROLL_BUDGET_SEC
    assert should is False


# ────────────────────── ② 相对阈值（会话最好速率的 1/4） ──────────────────────


def test_min_rate_is_relative_to_the_session_best() -> None:
    """好签观测过 900 KB/s ⇒ 阈值抬到 225 KB/s（明显偏离才算坏）；没观测过就用绝对下限。"""
    assert worker_mod._min_rate() == worker_mod.WIRE_MIN_RATE

    worker_mod._note_rate(900 * 1024, 4 * MB)
    assert worker_mod._min_rate() == pytest.approx(225 * 1024)

    # 小 body 的瞬时速率不代表链路 ⇒ 不参与采样（否则 1 KB 的 JSON 会把阈值抬到天上）
    worker_mod._note_rate(10 * 1024 * 1024, 2 * 1024)
    assert worker_mod._min_rate() == pytest.approx(225 * 1024)


def test_min_rate_never_drops_below_the_absolute_floor() -> None:
    """本会话只见过慢签（100 KB/s）⇒ 阈值仍是绝对下限 80 KB/s，不会退化成「见谁都重抽」。"""
    worker_mod._note_rate(100 * 1024, 4 * MB)
    assert worker_mod._min_rate() == worker_mod.WIRE_MIN_RATE


# ──────────────── ③④ 重抽循环：有界、最后一次不重抽、浪费有界 ────────────────


def _rerolling_request(monkeypatch, waste_bytes: int = 256 * 1024):
    """假 `_request`：**只在允许重抽时**抛 `WireSlowError`，否则返回 body。

    返回 `(calls, body)`；`calls` 记录每次的 `allow_reroll`，用来钉「重抽的开放面」。
    """
    calls: list[bool] = []
    body = b"z" * 300_000

    def fake(base_url: str, token: str, path: str, **kw):
        allow = bool(kw.get("allow_reroll", False))
        calls.append(allow)
        if allow:
            raise worker_mod.WireSlowError(waste_bytes, 6.4 * 1024, 1180.0)
        return 200, body

    monkeypatch.setattr(worker_mod, "_request", fake)
    return calls, body


def test_rerolls_then_succeeds_and_records_the_waste(monkeypatch) -> None:
    """慢签 ⇒ 重抽 ⇒ 拿到 body；且**作废字节与次数都进账**（坏签比例靠它统计）。"""
    calls, body = _rerolling_request(monkeypatch)
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j1/payload",
        timeout=300.0,
        attempts=3,
        log=lines.append,
        wire_jid="j1",
        wire_seg="payload",
        reroll=True,
    )
    assert out == body
    assert calls == [True, True, False]  # 最后一次尝试不重抽 ⇒ 必然传完
    assert sum("wire: re-roll" in ln for ln in lines) == 2
    assert any("6 KB/s < 阈值" in ln for ln in lines)  # 日志里有速率与阈值（可诊断）

    worker_mod._wire_flush("j1", lines.append)
    summary = lines[-1]
    assert "payload=0.29MB/" in summary
    assert "reroll=2(wasted 0.50MB)" in summary  # 2 × 256 KB


def test_reroll_is_capped_and_the_last_attempt_always_transfers(monkeypatch) -> None:
    """`attempts` 给再多，重抽也不超过 `WIRE_REROLL_MAX`；之后必有一次「硬传」。"""
    calls, body = _rerolling_request(monkeypatch)
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j2/payload",
        timeout=300.0,
        attempts=8,
        log=lines.append,
        wire_jid="j2",
        wire_seg="payload",
        reroll=True,
    )
    assert out == body
    assert calls.count(True) == worker_mod.WIRE_REROLL_MAX == 3
    assert calls[-1] is False

    worker_mod._wire_flush("j2", lines.append)
    assert "reroll=3(wasted 0.75MB)" in lines[-1]


def test_reroll_disabled_path_is_untouched(monkeypatch) -> None:
    """`reroll=False`（缺省）⇒ **一次都不抛**、行为与改造前一致（旧调用面零变化）。"""
    calls, body = _rerolling_request(monkeypatch)
    out = worker_mod._get_with_retry(
        "http://hub", "t", "/jobs/j3/code", timeout=60.0, attempts=3, log=lambda _m: None
    )
    assert out == body
    assert calls == [False]


def test_exhausted_rerolls_still_fail_loudly(monkeypatch) -> None:
    """连最后一次都拿不到 body ⇒ 响亮 `RetryableError`（不静默、不拿半截数据当成功）。"""

    def fake(base_url: str, token: str, path: str, **kw):
        return 500, b"boom"

    monkeypatch.setattr(worker_mod, "_request", fake)
    with pytest.raises(RetryableError):
        worker_mod._get_with_retry(
            "http://hub",
            "t",
            "/jobs/j4/payload",
            timeout=60.0,
            attempts=2,
            log=lambda _m: None,
            reroll=True,
        )


def test_read_body_probes_only_its_first_chunk(monkeypatch) -> None:
    """判据**只跑一次**（首块）⇒ 重抽的浪费 ≤ 一块，绝不会退化成「再整份传一遍」。"""
    seen: list[tuple[int, int, float]] = []

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append((got, total, elapsed))
        return True, 6.4 * 1024, 1180.0  # 第一次就判「该重抽」

    monkeypatch.setattr(worker_mod, "_reroll_decision", spy)
    resp = _FakeResp([b"x" * 256, b"y" * 256, b"z" * 256], total=768)
    with pytest.raises(worker_mod.WireSlowError):
        worker_mod._read_body(
            resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True
        )
    assert len(seen) == 1
    assert seen[0][0] == 256  # 首块就裁定


def test_read_body_fast_draw_returns_the_whole_body() -> None:
    """好签（首块够大又够快）⇒ 原样读完，不抛（默认阈值下的集成面）。"""
    resp = _FakeResp([b"x" * (128 * 1024), b"y" * 1024], total=129 * 1024)
    out = worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True)
    assert len(out) == 129 * 1024


# ────────────────────── ⑤ 每 job 一行传输账（+ 产物不重抽） ──────────────────────


def test_wire_flush_line_shape() -> None:
    """账行必须一眼读全：各段 `(bytes, sec, rate)`、零字节命中、重抽浪费、合计。"""
    worker_mod._wire_add("j5", "payload", 3486200, 321.8)
    worker_mod._wire_add("j5", "result", 928857, 3.7)
    worker_mod._wire_hit("j5", "code")
    worker_mod._wire_note_reroll("j5", 262144)
    lines: list[str] = []
    worker_mod._wire_flush("j5", lines.append)
    line = lines[0]
    assert line.startswith("job j5: wire ")
    # 速率只钉形状（取整到 KB/s 的整数——精确值不是契约）
    assert re.search(r"payload=3\.32MB/321\.8s\(\d+KB/s\)", line)
    assert re.search(r"result=0\.89MB/3\.7s\(\d+KB/s\)", line)
    assert "code=cache-hit" in line
    assert "reroll=1(wasted 0.25MB)" in line
    assert re.search(r"合计=4\.21MB/325\.5s\(\d+KB/s\)", line)
    # 已清：同一 jid 再 flush 一次不会多出一行（账是「一次性」的）
    worker_mod._wire_flush("j5", lines.append)
    assert len(lines) == 1


def test_wire_buckets_are_capped() -> None:
    """push 模式没有 pull 循环的 flush 点 ⇒ 未 flush 的 job 必须封顶（不无界增长）。"""
    for i in range(worker_mod.WIRE_MAX_JOBS + 6):
        worker_mod._wire_add(f"jx{i}", "payload", 1024, 0.1)
    assert len(worker_mod._WIRE) <= worker_mod.WIRE_MAX_JOBS


def test_download_payload_records_its_segment(monkeypatch) -> None:
    """真实下载路径（`download_payload`）自带记账：成功即落 `payload=` 段。"""
    monkeypatch.setattr(
        worker_mod, "_request", lambda *a, **kw: (200, b"p" * (512 * 1024))
    )
    worker_mod.download_payload("http://hub", "t", "j6", log=lambda _m: None)
    lines: list[str] = []
    worker_mod._wire_flush("j6", lines.append)
    assert "payload=0.50MB/" in lines[0]


def test_post_result_never_rerolls_but_is_accounted(monkeypatch) -> None:
    """产物上行（POST result）：**永不重抽**（走既有 5 次退避），但字节要进账。"""
    seen: dict = {}

    def fake(base_url: str, token: str, path: str, **kw):
        seen.update(kw)
        return 200, b'{"ok":1}'

    monkeypatch.setattr(worker_mod, "_request", fake)
    status = worker_mod.post_result("http://hub", "t", "j7", {"a": 1}, log=lambda _m: None)
    assert status == 200
    assert not seen.get("allow_reroll", False)
    assert seen.get("method") == "POST"

    lines: list[str] = []
    worker_mod._wire_flush("j7", lines.append)
    assert "result=" in lines[0]
    assert "reroll=" not in lines[0]
