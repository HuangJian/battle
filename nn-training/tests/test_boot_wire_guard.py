"""tests/test_boot_wire_guard.py —— 引导期大 body 传输护栏（plan/minimize-payload.plan.md §4.0）。

现场（同一台云机 / 同一个 hub）：传输速率是 **44× 双峰**——payload 22.7 KB/s ↔ 990 KB/s；
引导期 code GET 13.5 KB/s，而**3 秒之后**的 blob GET ≥120 KB/s。慢的不是端点，是**每条连接抽签**。

引导期两个大 body（hub `/code` 的 code.zip、离线盘 `task-pack`）都发生在 **code.zip 之前**，
`remote.worker` 的停滞/低速重抽那时还不存在 ⇒ 护栏住 `tailscale_boot`（三个 notebook 都拉它）。
本文件钉四件事：

  ① 判据是纯函数（阈值 / 样本 / 预计剩余 / 长度未知），且与 worker 侧同口径；
  ② 停滞 / 超预算 ⇒ **带正文**报错（裸 `TimeoutError()` 等于什么都没说）；
  ③ 低速 ⇒ 有界重抽（≤3、最后一次不重抽、浪费 ≤ 一块），好签一次都不重抽；
  ④ 成功时落一行可 grep、可对账的 `wire:` 传输账（含 attempts / rerolls）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.tailscale_boot as ts
from remote.notebook_boot import CODE_TOTAL_TIMEOUT_SEC

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_session_rate():
    """判据挂在本会话最好速率上（模块级）——每例前后清干净，免用例互相影响。"""
    ts._BEST_RATE = 0.0
    yield
    ts._BEST_RATE = 0.0


class _FakeResp:
    """假响应：逐块吐数据；`chunks` 用尽即 EOF（供 `_read_body` 用）。"""

    def __init__(self, chunks: list[bytes], total: int):
        self._chunks = list(chunks)
        self.headers = {"Content-Length": str(total)}
        self.status = 200

    def __enter__(self):  # `fetch_guarded` 走 `with opener.open(...)`
        return self

    def __exit__(self, *_a):
        return False

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _FakeOpener:
    """假 ``opener.open``：每次调用按顺序取一个「本次尝试」的结果（响应 / 异常）。"""

    def __init__(self, results: list[object]):
        self._results = list(results)
        self.calls = 0

    def open(self, _req, timeout=None):  # 测试替身：签名对齐 urllib 的 opener.open
        self.calls += 1
        item = self._results.pop(0) if self._results else b""
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, _FakeResp)
        return item


# ────────────────────────────── ① 判据（纯函数） ──────────────────────────────


def test_decision_flags_the_live_slow_draw() -> None:
    """现场坏签：首块 256 KB 用了 39 s ⇒ 6.4 KB/s、预计剩余 ~1180 s ⇒ 该重抽。"""
    should, rate, remain = ts._reroll_decision(256 * 1024, 3486200, 39.0, floor_rate=80 * 1024)
    assert should is True
    assert rate < 80 * 1024
    assert remain > ts.BOOT_REROLL_BUDGET_SEC


def test_decision_ignores_a_fast_draw() -> None:
    """好签（≈354 KB/s 那种）不该被重抽——重抽也有建连成本。"""
    should, rate, _ = ts._reroll_decision(256 * 1024, 3486200, 1.0, floor_rate=80 * 1024)
    assert should is False
    assert rate > 80 * 1024


def test_decision_waits_for_a_usable_sample() -> None:
    """样本太小（既没够字节也没够时间）不下结论——免得拿 2 KB 判一条链路。"""
    should, _, _ = ts._reroll_decision(1024, 10 * MB, 0.01, floor_rate=80 * 1024)
    assert should is False


def test_decision_handles_edge_inputs() -> None:
    """长度未知（无 Content-Length）/ 已收完 / 零字节：三个都是「不该赌」的形状。"""
    for got, total in ((0, 0), (100, 0), (3486200, 3486200), (10 * MB, 10 * MB)):
        should, _, _ = ts._reroll_decision(got, total, 10.0, floor_rate=80 * 1024)
        assert should is False, (got, total)


def test_decision_honours_the_remaining_time_budget() -> None:
    """慢，但**马上就完了**（预计剩余 < 20 s）⇒ 不折腾。"""
    should, rate, remain = ts._reroll_decision(32 * 1024, 112 * 1024, 4.0, floor_rate=80 * 1024)
    assert rate < 80 * 1024
    assert remain < ts.BOOT_REROLL_BUDGET_SEC
    assert should is False


def test_threshold_is_relative_to_the_session_best() -> None:
    """好签观测过 900 KB/s ⇒ 阈值抬到 225 KB/s（明显偏离才算坏）；小 body 不参与采样。"""
    assert ts._min_rate() == ts.BOOT_MIN_RATE
    ts._note_rate(900 * 1024, 4 * MB)
    assert ts._min_rate() == pytest.approx(225 * 1024)
    ts._note_rate(10 * 1024 * 1024, 2 * 1024)  # 1 KB 的 JSON 不代表链路
    assert ts._min_rate() == pytest.approx(225 * 1024)


# ────────────────────── ② 停滞 / 超预算：带正文 ──────────────────────


def test_stall_reports_bytes_and_reason() -> None:
    """停滞异常必须有正文（已收字节 + 多久没有新字节）——裸 `TimeoutError()` 无法排障。"""

    class _Stalling(_FakeResp):
        def read(self, _n: int) -> bytes:
            raise TimeoutError("socket timed out")

    with pytest.raises(ts.BootBodyError) as ei:
        ts._read_body(_Stalling([], total=4096), idle_timeout=45.0, total_timeout=None)
    msg = str(ei.value)
    assert "停滞" in msg and "45s" in msg and "已收 0 bytes" in msg


def test_total_budget_reports_progress() -> None:
    """「一直在滴水」由墙钟预算兜住：报错里能读出收了多久、收了多少。"""

    class _Dripping(_FakeResp):
        def read(self, _n: int) -> bytes:
            # 预算判据是**墙钟**比较，Windows 上 time.time() 粒度 ~15.6ms ⇒ 得真等一下
            # 才能构造出「已经超预算」的状态（总预算是 0，任何非零耗时都算超）。
            time.sleep(0.05)
            return super().read(_n)

    resp = _Dripping([b"x" * 128] * 3, total=10 * MB)
    with pytest.raises(ts.BootBodyError) as ei:
        ts._read_body(resp, idle_timeout=45.0, total_timeout=0.0)
    assert "超预算" in str(ei.value)


def test_read_body_returns_the_whole_body_and_reports_progress() -> None:
    """好签：原样读完（不抛），并回调进度（引导期原来一行进度都没有）。"""
    seen: list[int] = []
    resp = _FakeResp([b"x" * 4096, b"y" * 1024], total=5120)
    body = ts._read_body(
        resp, idle_timeout=45.0, total_timeout=300.0, progress=lambda got, *_a: seen.append(got)
    )
    assert len(body) == 5120


# ────────────────────── ③ 有界重抽：换连接、不重复整传 ──────────────────────
#
# 判据挂**墙钟**（实测速率 = 已收字节 / 耗时），所以测试不能靠「投喂很多块」模拟慢签
# （块是瞬间到的 ⇒ 速率无限大）。改为把判据换成受控替身：这既确定，又恰好能钉
# 「每次尝试只判首块」这件事（替身被调用几次 = 判了几次）。


def _stub_decision(monkeypatch, verdicts: list[bool]) -> dict:
    """把 `_reroll_decision` 换成按顺序出牌的替身，返回 `{"calls": n}`。"""
    state = {"calls": 0}

    def fake(got: int, total: int, elapsed: float, **_kw: object):
        should = verdicts[min(state["calls"], len(verdicts) - 1)]
        state["calls"] += 1
        return should, 6.4 * 1024, 1180.0

    monkeypatch.setattr(ts, "_reroll_decision", fake)
    return state


def test_slow_draw_is_rerolled_then_succeeds(monkeypatch) -> None:
    """坏签 ⇒ 断开重发 ⇒ 换到好签后成功；次数与作废字节都进账（坏签比例靠它统计）。"""
    state = _stub_decision(monkeypatch, [True, False])
    slow = _FakeResp([b"x" * (128 * 1024), b"y" * (128 * 1024)], total=4 * MB)
    good = _FakeResp([b"z" * (256 * 1024), b"w" * (256 * 1024)], total=512 * 1024)
    opener = _FakeOpener([slow, good])
    lines: list[str] = []

    body = ts.fetch_guarded(
        "http://hub/code", log=lines.append, label="code.zip", opener=opener, attempts=3
    )

    assert body == b"z" * (256 * 1024) + b"w" * (256 * 1024)
    assert opener.calls == 2  # 第一次坏签被断开，第二次拿全
    assert state["calls"] == 2  # 每次尝试各判一次
    assert sum(1 for ln in lines if "wire: re-roll #1/3 code.zip" in ln) == 1
    # 作废的只有**首块**（128 KB），不是整份：判据只在首块跑一次
    assert any("已收 131072 bytes 作废" in ln for ln in lines)
    done = [ln for ln in lines if ln.startswith("wire: code.zip ")][-1]
    assert "attempts=2" in done and "rerolls=1" in done


def test_reroll_is_capped_and_the_last_attempt_always_transfers(monkeypatch) -> None:
    """`attempts` 给再多，重抽也不超过上限；之后必有一次「硬传」把 body 拿回来。

    这条是「慢链路不会变成永远下不完」的硬保证：6 KB/s 的坏签真实存在。
    """
    state = _stub_decision(monkeypatch, [True])  # 一直判「坏签」
    good = b"z" * (256 * 1024)
    opener = _FakeOpener(
        [
            _FakeResp([b"x" * (128 * 1024)], total=4 * MB),
            _FakeResp([b"x" * (128 * 1024)], total=4 * MB),
            _FakeResp([b"x" * (128 * 1024)], total=4 * MB),
            _FakeResp([good], total=len(good)),
        ]
    )
    lines: list[str] = []

    body = ts.fetch_guarded(
        "http://hub/code", log=lines.append, label="code.zip", opener=opener, attempts=4
    )

    assert body == good
    assert opener.calls == ts.BOOT_REROLL_MAX + 1  # 3 次重抽 + 1 次硬传
    assert state["calls"] == ts.BOOT_REROLL_MAX  # 最后一次连判都不判
    assert sum(1 for ln in lines if "wire: re-roll" in ln) == ts.BOOT_REROLL_MAX
    done = [ln for ln in lines if ln.startswith("wire: code.zip ")][-1]
    assert "attempts=4" in done and f"rerolls={ts.BOOT_REROLL_MAX}" in done


def test_stall_is_retried_within_the_same_fetch() -> None:
    """停滞也在同一次 fetch 内重试（引导期的外层循环是 30 s 退避，太慢）。"""

    class _Stalling(_FakeResp):
        def read(self, _n: int) -> bytes:
            raise TimeoutError("socket timed out")

    good = _FakeResp([b"z" * 4096], total=4096)
    opener = _FakeOpener([_Stalling([], total=1 * MB), good])
    lines: list[str] = []
    body = ts.fetch_guarded(
        "http://hub/code", log=lines.append, label="code.zip", opener=opener, attempts=2
    )
    assert body == b"z" * 4096
    assert any("传输失败（" in ln and "停滞" in ln for ln in lines)


def test_exhausted_attempts_raise_the_informative_error() -> None:
    """几次都没成 ⇒ 抛**带正文**的错误（调用方要能把它写进日志，而不是只写类型名）。"""

    class _Stalling(_FakeResp):
        def read(self, _n: int) -> bytes:
            raise TimeoutError("socket timed out")

    opener = _FakeOpener([_Stalling([], total=1 * MB) for _ in range(2)])
    with pytest.raises(ts.BootBodyError) as ei:
        ts.fetch_guarded(
            "http://hub/code",
            log=lambda _m: None,
            label="code.zip",
            opener=opener,
            attempts=2,
        )
    assert "停滞" in str(ei.value) and "已收" in str(ei.value)


def test_reroll_can_be_disabled() -> None:
    """`reroll=False` ⇒ 一次都不重抽（旧调用面/非幂等场景的退路）。"""
    slow = _FakeResp([b"x" * (128 * 1024), b"y" * 128], total=4 * MB)
    opener = _FakeOpener([slow])
    body = ts.fetch_guarded(
        "http://hub/code", log=lambda _m: None, label="x", opener=opener, attempts=1, reroll=False
    )
    assert len(body) == 128 * 1024 + 128
    assert opener.calls == 1


# ────────────────────── ④ 接线：引导期两处真的走护栏 ──────────────────────


def test_boot_pull_uses_the_guarded_fetch(monkeypatch) -> None:
    """`notebook_boot._pull` 的 code 段必须走护栏（大 body + 幂等 GET），且带着墙钟预算。"""
    seen: dict = {}

    def fake_fetch(url: str, **kw):
        seen.update(url=url, **kw)
        raise SystemExit("stop-here")

    monkeypatch.setattr(ts, "fetch_guarded", fake_fetch)
    import remote.notebook_boot as nb

    with pytest.raises(SystemExit, match="stop-here"):
        nb._pull({}, lambda _m: None, None, "http://100.64.0.5:8787", "tok", "ptok")

    assert seen["url"] == "http://100.64.0.5:8787/code"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["total_timeout"] == CODE_TOTAL_TIMEOUT_SEC
    assert seen["attempts"] == 3


def test_offline_task_pack_uses_the_guarded_fetch(monkeypatch, tmp_path: Path) -> None:
    """离线任务包（几 MB～几十 MB，全仓最大的 body）同样走护栏。"""
    import remote.offline_boot as ob

    seen: dict = {}

    def fake_fetch(url: str, **kw):
        seen.update(url=url, **kw)
        return b"PK\x03\x04 fake"

    monkeypatch.setattr(ts, "fetch_guarded", fake_fetch)
    got = ob.fetch_task_pack("http://hub", "tok", "c5-gae", tmp_path, lambda _m: None)

    assert got is not None and got.read_bytes() == b"PK\x03\x04 fake"
    assert seen["url"].endswith("/offline/task-pack?course=c5-gae")
    assert seen["label"] == "task-pack"
    assert seen["total_timeout"] == ob.PACK_TIMEOUT


def test_offline_task_pack_survives_without_tailscale_boot(monkeypatch, tmp_path: Path) -> None:
    """纯离线兜底路径（`tailscale_boot` 没加载）不得因此取不到包——退回整读并**响亮记一笔**。"""
    import remote.offline_boot as ob

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self) -> bytes:
            return b"PK\x03\x04 fallback"

    class _Opener:
        def open(self, _req, timeout=None):  # 测试替身：签名对齐 urllib 的 opener.open
            return _Resp()

    lines: list[str] = []
    monkeypatch.setattr(ob, "_build_opener", lambda: _Opener())
    monkeypatch.setattr(
        ob, "_load_tailscale_boot", lambda: (_ for _ in ()).throw(ImportError("没有 tailscale_boot"))
    )
    got = ob.fetch_task_pack("http://hub", "tok", "c5-gae", tmp_path, lines.append)
    assert got is not None and got.read_bytes() == b"PK\x03\x04 fallback"
    assert any("引导传输护栏不可用" in ln for ln in lines)
