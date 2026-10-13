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
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 第八刀起，本簇住在 `remote/job_lifecycle.py`：**簇内互调**与直调 `_request` / `_wire_add`
# 的调用点解析在该模块 ⇒ 那类 patch 目标必须是 `JL`（宿主调用的仍 patch `W`）。
import remote.job_lifecycle as JL
from common.protocol import RetryableError
from remote import http as http_mod
from remote import wire as wire_mod
from remote import worker as worker_mod
from remote.bulk_sched import BULK_P1_CRITICAL, BULK_P2_PREFETCH, BulkPreemptError

# 跨测试模块复用可观测等待原语（先例：`test_role_routing` → `test_run_segment._publish`）。
# 同款夹具在 `test_bulk_sched` 已用于「单通道并发排队」。
from tests.remote.test_bulk_sched import _CountingEvent

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_wire_state():
    """本文件的用例都动模块级状态（传输账 + 会话最好速率）——每例前后清干净。

    `_WIRE` 是原地可变（`.clear()`），从 `worker` 或 `wire` 进都是同一份账；
    而 `_BEST_RATE` 是**重绑式**会话标量，实现与注入点都在 `remote.wire`——
    从 `worker` 重绑只会改名而不改值（S4 第四步拆分的 seam，见 `remote/wire.py`）。
    """
    worker_mod._WIRE.clear()
    wire_mod._BEST_RATE = 0.0
    yield
    worker_mod._WIRE.clear()
    wire_mod._BEST_RATE = 0.0


class _FakeResp:
    """假响应：逐块吐数据（供 `_read_body` 用；headers 给 Content-Length）。

    只实现 `read` —— 它同时也是「`_recv_some` 的存在性回退」这条路径的替身（M1）。
    """

    def __init__(self, chunks: list[bytes], total: int):
        self._chunks = list(chunks)
        self.headers = {"Content-Length": str(total)}
        self.status = 200

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _WindowedResp:
    """`read1` 形状的假响应（M1）：每次只吐 `size` 字节；`read` 故意抛（别再退回定长读）。"""

    def __init__(self, chunks: int, size: int, total: int | None = None):
        self.chunks = chunks
        self.size = size
        self.headers = {"Content-Length": str(total if total is not None else chunks * size)}
        self.status = 200

    def read1(self, _n: int) -> bytes:
        if self.chunks <= 0:
            return b""
        self.chunks -= 1
        return b"x" * self.size

    def read(self, _n: int) -> bytes:
        raise AssertionError("body 读回到了定长 read（M1：必须走 read1）")


class _FakeClock:
    """确定性墙钟（判据按时间窗采样 ⇒ 测试必须能控时）。"""

    def __init__(self, start: float = 1000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


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


def _rerolling_request(monkeypatch, waste_bytes: int = 256 * 1024, *, improving: bool = True):
    """假 `_request`：允许重抽时抛 `WireSlowError`（**带 `partial`**，模拟支持 Range 的服务端），
    否则把**剩下的那一段**当 206 回给调用方。

    返回 `(calls, total_bytes)`；`calls` 记录每次的 `allow_reroll`，用来钉「重抽的开放面」。

    `improving=True`（缺省）让每次重抽的实测速率**更高**（3× 递增）——这样「重抽上限」那条
    用例不会被 2026-10-12 的「**无收益即停**」提前截断：两条是不同的判据，必须能分别钉住。

    ★ 2026-10-12（Range 断点续传）：夹具改成「断连时交出 `partial`、重来时回 206 尾巴」，
    于是重抽**不再作废字节**（`wasted` 恒 0）；「服务端忽略 Range ⇒ 如实记账」由单独用例钉住。
    """
    calls: list[bool] = []
    total = 1_200_000
    n = {"k": 0}

    def fake(base_url: str, token: str, path: str, **kw):
        allow = bool(kw.get("allow_reroll", False))
        calls.append(allow)
        start = int(kw.get("range_start") or 0)
        if allow:
            n["k"] += 1
            rate = 6.4 * 1024 * (3 ** (n["k"] - 1)) if improving else 6.4 * 1024
            raise worker_mod.WireSlowError(
                start + waste_bytes, rate, 1180.0, partial=b"z" * waste_bytes
            )
        return (206 if start else 200), b"z" * (total - start)

    # 注入点 = `remote.http`：`_get_with_retry` 已搬进 http（S4 第五步），它在本模块命名空间
    # 解析 `_request` ⇒ patch `worker` 会静默失效（patch 后调**宿主**函数的仍是 `worker`）。
    monkeypatch.setattr(http_mod, "_request", fake)
    return calls, total


def test_rerolls_then_succeeds_and_records_the_waste(monkeypatch) -> None:
    """慢签 ⇒ 重抽**续传** ⇒ 拿到整份；次数进账，而 `wasted` 为 0（断连那截没扔）。"""
    calls, total = _rerolling_request(monkeypatch)
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
    assert len(out) == total, f"续传没拼成整份：{len(out)}"
    assert calls == [True, True, False]  # 最后一次尝试不重抽 ⇒ 必然传完
    assert sum("wire: re-roll" in ln for ln in lines) == 2
    assert any("6 KB/s < 阈值" in ln for ln in lines)  # 日志里有速率与阈值（可诊断）

    worker_mod._wire_flush("j1", lines.append)
    summary = lines[-1]
    # 段账记的是**最后一段**（206 那截 = 1.2MB − 2×256KB）
    assert "payload=0.64MB/" in summary, summary  # 最后一段 = 1.2MB − 2×256KB
    # ★ 2026-10-12：续传 ⇒ 断连读到的字节**不作废**
    assert "reroll=2(wasted 0.00MB)" in summary, summary


def test_a_server_that_ignores_range_is_accounted_as_waste(monkeypatch) -> None:
    """★ 服务端**忽略 Range**（回 200）⇒ 那截真白传了：如实记 `wasted`。

    别把「续传成功」当默认：旧 hub / 中间代理剥掉 `Range` 时，我们拿到的是一份从头传的整份，
    前缀确实传了两遍 —— 记账必须能从日志里看出来，否则「抖动期到底白扔了多少」永远算不出来。
    """
    total = 400_000
    calls: list[int] = []

    def fake(base_url: str, token: str, path: str, **kw):
        calls.append(int(kw.get("range_start") or 0))
        if len(calls) == 1:
            raise worker_mod.WireSlowError(100_000, 6.4 * 1024, 1180.0, partial=b"z" * 100_000)
        return 200, b"q" * total  # 旧 hub：Range 被忽略

    monkeypatch.setattr(http_mod, "_request", fake)
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j6/payload",
        timeout=300.0,
        attempts=3,
        log=lines.append,
        wire_jid="j6",
        wire_seg="payload",
        reroll=True,
    )
    assert calls == [0, 100_000], f"重抽没带续传起点：{calls}"
    assert out == b"q" * total, "服务端给了整份就按整份返回"
    # 判据读**账本本身**（不看渲染出来的日志措辞）：次数 1、浪费 100KB（那截真传了两遍）。
    bucket = wire_mod._WIRE["j6"]
    assert bucket["rerolls"] == 1, bucket
    assert bucket["wasted"] == 100_000, bucket


def test_reroll_is_capped_and_the_last_attempt_always_transfers(monkeypatch) -> None:
    """`attempts` 给再多，重抽也不超过 `WIRE_REROLL_MAX`；之后必有一次「硬传」。"""
    calls, total = _rerolling_request(monkeypatch)
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
    assert len(out) == total, f"续传没拼成整份：{len(out)}"
    assert calls.count(True) == worker_mod.WIRE_REROLL_MAX == 3
    assert calls[-1] is False

    worker_mod._wire_flush("j2", lines.append)
    assert "reroll=3(wasted 0.00MB)" in lines[-1]  # 续传：一次都没白扔


def test_a_reroll_that_does_not_improve_stops_rerolling(monkeypatch) -> None:
    """★ 2026-10-12（抖动是常态）：重抽**没变好**就停手，按当前速率传完。

    真机证据：重抽前后 79 → 51 KB/s（越抽越差），每次白扔 256KB + 一次建连。第一条重抽没有
    对比对象（允许抽），第二条起要求速率至少提高 `WIRE_REROLL_GAIN` 倍，否则不再抽。

    判据只看**行为**（抽几次、有没有传完）：那句「重抽无收益」的日志是给操作员看的说明，
    本用例不拿它当判据（文本断言预算只许降）。
    """
    calls, total = _rerolling_request(monkeypatch, improving=False)
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j5/payload",
        timeout=300.0,
        attempts=8,
        log=lambda _m: None,
        wire_jid="j5",
        wire_seg="payload",
        reroll=True,
    )
    assert len(out) == total, f"停抽之后必须老实传完：{len(out)}"
    assert calls == [True, True, False], f"无收益没停手：{calls}"


def test_reference_rate_decays_after_repeated_slow_samples() -> None:
    """★ 2026-10-12：参照速率不再钉死在会话最高水位（真机：一次 3,089KB/s ⇒ 门槛 772KB/s）。

    规则：更好即抬（**恢复是瞬时的**）；**连续** `WIRE_RATE_STALE_SAMPLES` 个非更好样本起，
    参照每多一个慢样本乘一次 `WIRE_RATE_DECAY`，直到落回 `WIRE_MIN_RATE` 地板。
    单个慢样本仍不覆盖更好值（既有契约，见 `test_note_rate_is_session_stateful_and_reproducible`）。
    """
    mb = 1024 * 1024
    wire_mod._BEST_RATE = 0.0
    worker_mod._note_rate(3 * mb, 4 * mb)
    assert abs(wire_mod._BEST_RATE - 3 * mb) < 1.0

    worker_mod._note_rate(150 * 1024, 4 * mb)  # 第 1 个慢样本：迟滞内，参照不动
    assert abs(wire_mod._BEST_RATE - 3 * mb) < 1.0
    assert worker_mod._min_rate() == pytest.approx(3 * mb / 4)  # 旧语义下这一条就是 772KB/s

    worker_mod._note_rate(150 * 1024, 4 * mb)  # 第 2 个：开始衰减
    assert abs(wire_mod._BEST_RATE - 3 * mb * wire_mod.WIRE_RATE_DECAY) < 1.0

    for _ in range(6):
        worker_mod._note_rate(80 * 1024, 4 * mb)
    assert worker_mod._min_rate() == worker_mod.WIRE_MIN_RATE, "持续慢之后门槛没落回地板"

    worker_mod._note_rate(3 * mb, 4 * mb)  # 链路恢复 ⇒ 参照瞬时回到新高（不惩罚恢复）
    assert abs(wire_mod._BEST_RATE - 3 * mb) < 1.0


def test_reroll_disabled_path_is_untouched(monkeypatch) -> None:
    """`reroll=False`（缺省）⇒ **一次都不抛**、行为与改造前一致（旧调用面零变化）。"""
    calls, total = _rerolling_request(monkeypatch)
    out = worker_mod._get_with_retry(
        "http://hub", "t", "/jobs/j3/code", timeout=60.0, attempts=3, log=lambda _m: None
    )
    assert len(out) == total
    assert calls == [False], "关掉重抽就不该带 range（range_start 也不必带）"


def test_exhausted_rerolls_still_fail_loudly(monkeypatch) -> None:
    """连最后一次都拿不到 body ⇒ 响亮 `RetryableError`（不静默、不拿半截数据当成功）。"""

    def fake(base_url: str, token: str, path: str, **kw):
        return 500, b"boom"

    monkeypatch.setattr(http_mod, "_request", fake)
    # 不替生产退避白等（`_get_with_retry` 第 1 次重试睡 2s；本用例只看抛不抛）：
    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)
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


def test_read_body_samples_by_time_window_and_needs_two_slow_windows(monkeypatch) -> None:
    """★ 2026-10-13 取代 `test_read_body_probes_only_its_first_chunk`。

    旧不变式是「判据只在首块跑一次 ⇒ 重抽浪费 ≤ 一块」。它在两条链路上都失效：

      · 慢链路：样本要等**一块**（块不返回就没有样本）⇒ 现场那块花了 300 s；
      · 快链路：首块太小 ⇒ 判一次 `should=False` 就把标记永久置上（现场 343.5 s 的行）。

    新不变式：**按时间窗采样 + 连续两窗慢才断** ⇒ 浪费的上界变成「≤ 连续 2 窗」，
    而判据不再被第一个样本关掉（下面第 1、2 次判「快」就是这条的回归）。
    """
    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 1.0)
    monkeypatch.setattr(http_mod, "WIRE_REROLL_PROBES", 2)
    monkeypatch.setattr(http_mod.time, "time", _FakeClock(step=1.0))
    seen: list[tuple[int, int, float, dict]] = []
    verdicts = iter([False, False, True, True])

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append((got, total, elapsed, kw))
        return next(verdicts, False), 6.4 * 1024, 1180.0

    # 同上：`_read_body` 在 `remote.http` 里解析 `_reroll_decision`。
    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
    resp = _WindowedResp(chunks=8, size=256)
    with pytest.raises(worker_mod.WireSlowError):
        worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True)
    assert len(seen) == 4, f"快窗把判据关掉了（只判了 {len(seen)} 次）"
    assert seen[0][0] == 256  # 样品是**窗内**字节：每窗一块
    assert seen[0][3]["remaining"] == 2048 - 256  # 真实剩余显式传入（不是 total - 窗内字节）


def test_reroll_decision_supports_a_window_sample() -> None:
    """窗口口径（M2）：`got/elapsed` 是窗内的量，`remaining` 是**真实剩余**。

    同 `(got, total, elapsed)` 两个结论不同 ⇒ 读到的一定是 `remaining`（而不是 `total - got`）。
    缺省（不给 `remaining`）逐字保留旧行为（上面的纯函数用例就是它的回归）。
    """
    slow_then_leftover = worker_mod._reroll_decision(
        15 * 1024, 100 * 1024, 3.0, min_rate=80 * 1024, remaining=400 * 1024
    )
    assert slow_then_leftover[0] is True and slow_then_leftover[2] == pytest.approx(80.0)
    nearly_done = worker_mod._reroll_decision(
        15 * 1024, 100 * 1024, 3.0, min_rate=80 * 1024, remaining=10 * 1024
    )
    assert nearly_done[0] is False and nearly_done[2] == pytest.approx(2.0)
    leftover_done = worker_mod._reroll_decision(
        15 * 1024, 100 * 1024, 3.0, min_rate=80 * 1024, remaining=0
    )
    assert leftover_done[0] is False and leftover_done[2] == 0.0  # 已收完：不赌


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


def test_failed_attempts_are_accounted_in_the_wire_line(monkeypatch) -> None:
    """★ R6/M4：**失败的尝试**也要进账 —— 否则现场 `other=343.7s` 是黑箱（那 300s 归不了因）。

    口径三件事：`failed.sec` 只算**传输段**（排队归 `wait=`）；失败字节从异常自带的
    `got` 读（M3）；`wall` 的 max() 把 failed 算进去（否则守恒式恒真而无信息）。
    """
    body = b"z" * 4096
    calls: list[int] = []
    # `http_mod.time` 就是 time 模块 ⇒ 给退避打桩会把夹具自己的耗时一起打没，故先存一份真身。
    real_sleep = time.sleep

    def fake(base_url: str, token: str, path: str, **kw):
        calls.append(1)
        if len(calls) == 1:
            err = http_mod.BodyTimeoutError(
                "body 超时：总耗时 > 300s（已收 262144 bytes / 共 2608740）——链路太慢，重试",
                b"x" * 262144,  # M3：异常自带已收字节（半截仍作废：无 Range，见 R3）
                2608740,
            )
            # sleep-ok: 夹具模拟的「失败尝试」本身：它必须占掉一段可测墙钟，账里才看得见
            real_sleep(0.05)
            raise err
        return 200, body

    monkeypatch.setattr(http_mod, "_request", fake)
    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)  # 退避不真等
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j9/payload",
        timeout=300.0,
        attempts=2,
        log=lines.append,
        wire_jid="j9",
        wire_seg="payload",
    )
    assert out == body
    worker_mod._wire_flush("j9", lines.append)
    line = lines[-1]
    m = re.search(r"failed=([\d.]+)s/([\d.]+)MB\(n=(\d+)\)", line)
    assert m, line
    assert m.group(2) == "0.25" and m.group(3) == "1"  # 262144 B = 0.25MB，就一次
    ph = re.search(
        r"phases in=([\d.]+)s out=([\d.]+)s ppo=([\d.]+)s failed=([\d.]+)s "
        r"other=([\d.]+)s wall=([\d.]+)s",
        line,
    )
    assert ph, line
    in_s, out_s, ppo_s, failed_s, other_s, wall_s = (float(x) for x in ph.groups())
    assert failed_s >= 0.04, line  # 那 0.05s 真的进了账（不是只写了个 0）
    assert abs((in_s + out_s + ppo_s + failed_s + other_s) - wall_s) <= 0.06, line


def test_preempted_prefetch_attempts_are_accounted_too(monkeypatch) -> None:
    """被 P1 挤走也算一次**未成功**的尝试：`preempt=` 报字节、`failed=` 报时间（口径不混）。"""
    body = b"z" * 4096
    calls: list[int] = []

    def fake(base_url: str, token: str, path: str, **kw):
        calls.append(1)
        if len(calls) == 1:
            err = BulkPreemptError("定期预取被高优 bulk 挤走：丢半截，稍后重下（payload 幂等）")
            err.bytes_read = 128 * 1024
            raise err
        return 200, body

    monkeypatch.setattr(http_mod, "_request", fake)
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/pre/payload",
        timeout=300.0,
        attempts=3,
        log=lines.append,
        wire_jid="pre",
        wire_seg="payload",
        bulk_prio=BULK_P2_PREFETCH,
    )
    assert out == body
    worker_mod._wire_flush("pre", lines.append)
    line = lines[-1]
    assert re.search(r"preempt=1\(wasted 0\.12MB\)", line), line
    assert re.search(r"failed=[\d.]+s/0\.12MB\(n=1\)", line), line


def test_wire_buckets_are_capped() -> None:
    """push 模式没有 pull 循环的 flush 点 ⇒ 未 flush 的 job 必须封顶（不无界增长）。"""
    for i in range(worker_mod.WIRE_MAX_JOBS + 6):
        worker_mod._wire_add(f"jx{i}", "payload", 1024, 0.1)
    assert len(worker_mod._WIRE) <= worker_mod.WIRE_MAX_JOBS


def test_download_payload_records_its_segment(monkeypatch) -> None:
    """真实下载路径（`download_payload`）自带记账：成功即落 `payload=` 段。"""
    # `download_payload` 走 `_get_with_retry`（已搬 `remote.http`）⇒ 注入点在 http。
    monkeypatch.setattr(
        http_mod, "_request", lambda *a, **kw: (200, b"p" * (512 * 1024))
    )
    worker_mod.download_payload("http://hub", "t", "j6", log=lambda _m: None)
    lines: list[str] = []
    worker_mod._wire_flush("j6", lines.append)
    assert "payload=0.50MB/" in lines[0]


def test_ts_code_cache_hit_is_accounted(tmp_path: Path) -> None:
    """ts_code 命中也要进账（与 code 同规）：零字节命中不写进摘要 ⇒ 「本段没走网络」看不见。

    实测教训：`code` 与 `blob` 都记了，`_ensure_ts_code` 那一处漏了——kind=iter 的会话里
    ts_code 段就永远只是「缺席」（与 `cache-hit` 是两回事）。
    """
    sha = "c" * 64
    (tmp_path / sha).mkdir()  # 内容寻址缓存已在盘上 ⇒ 命中路径
    cache, nbytes, hit = worker_mod._ensure_ts_code(
        "http://hub",
        "t",
        "j8",
        {"ts_code_sha256": sha},
        ts_root=tmp_path,
        preloaded=None,
        log=lambda _m: None,
    )
    assert hit is True and nbytes == 0 and cache == tmp_path / sha
    lines: list[str] = []
    worker_mod._wire_flush("j8", lines.append)
    assert "ts_code=cache-hit" in lines[0]


def test_post_result_never_rerolls_but_is_accounted(monkeypatch) -> None:
    """产物上行（POST result）：**永不重抽**（走既有 5 次退避），但字节要进账。"""
    seen: dict = {}

    def fake(base_url: str, token: str, path: str, **kw):
        seen.update(kw)
        return 200, b'{"ok":1}'

    monkeypatch.setattr(JL, "_request", fake)
    status = worker_mod.post_result("http://hub", "t", "j7", {"a": 1}, log=lambda _m: None)
    assert status == 200
    assert not seen.get("allow_reroll", False)
    assert seen.get("method") == "POST"

    lines: list[str] = []
    worker_mod._wire_flush("j7", lines.append)
    assert "result=" in lines[0]
    assert "reroll=" not in lines[0]


# ──────────── ⑥ 净值判据 + 抢占独立预算（2026-09-25，plan/bulk-p2-preempt-fix）────────────
#
# 现场（2026-09-24，x20-dodge-l1/l3 双课程单云 worker）：预取零命中，每个 payload 下载都带
# `reroll=1(wasted 0.25MB)`。两条互不相干的根因：
#   ① 让路时间污染首块速率探针（首块 256KB 之前先 `pace()`，可能停满 5s 预算 ⇒
#      256KB/5s ≈ 51KB/s 恒低于 `WIRE_MIN_RATE=80KB/s`）⇒ 每次下载都假性重抽；
#   ② 被高优挤走与真实失败共用 `attempts` 预算 ⇒ 三次被打断就把整份预取判死。


def test_reroll_probe_uses_the_net_elapsed(monkeypatch) -> None:
    """速率判据拿到的 `elapsed` = **墙钟 − Σ让路**（让路不是链路的错）。

    2026-10-13 起判据按**时间窗**采样（窗宽与判据看同一把尺 = `WIRE_PROBE_SEC`，这里压到 0.3s），
    时钟换成确定性假钟：**墙钟走 1.0s，其中 0.3s 是让路 ⇒ 判据只该看到 0.7s**。
    相对判据（2026-09-26）：绝对上界在满载下是假红，只看「扣没扣」。
    """
    seen: list[float] = []

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append(elapsed)
        return False, 0.0, 0.0  # 本用例只关心它拿到了什么时间

    # patch 面：`_read_body` 的 global 解析在 `remote.http`（S4 第五步拆分）——
    # patch `remote.worker` 的名字只改转发名，改动不了 http 里的解析（静默空操作）。
    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 0.3)
    monkeypatch.setattr(http_mod.time, "time", _FakeClock(step=1.0))
    calls = {"n": 0}

    def pace() -> float:
        calls["n"] += 1
        return 0.3 if calls["n"] == 1 else 0.0  # 只有首块前那一次让路

    resp = _FakeResp([b"x" * (256 * 1024)], total=256 * 1024)  # 声明=实收（本用例只测净值判据）
    out = worker_mod._read_body(
        resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True, pace=pace
    )
    assert len(out) == 256 * 1024
    assert len(seen) == 1, f"这一窗只该判一次：{seen}"
    assert seen[0] == pytest.approx(0.7), "让路时间没被扣掉（净值 ≠ 墙钟 − Σ让路）"


def test_net_elapsed_does_not_extend_the_wall_clock_timeout() -> None:
    """净值**只**喂速率判据：`total_timeout` 仍按墙钟（暂停的 0.05s 也算总耗时）。"""

    def pace() -> float:
        # sleep-ok: 夹具模拟的工作量：一次让路（不是同步手段）
        time.sleep(0.05)
        return 0.05  # 声称让路了 0.05s

    resp = _FakeResp([b"x" * 1024], total=2048)
    with pytest.raises(TimeoutError, match="body 超时"):
        worker_mod._read_body(
            resp, idle_timeout=45.0, total_timeout=0.01, allow_reroll=False, pace=pace
        )


def test_preempt_error_carries_the_bytes_already_read() -> None:
    """A4：被挤走时已收字节挂在异常上（否则预取真实成本在账上恒等于 0）。"""
    calls = {"n": 0}

    def pace() -> float:
        calls["n"] += 1
        if calls["n"] >= 3:
            raise BulkPreemptError("模拟被 P1 挤走")
        return 0.0

    resp = _FakeResp([b"x" * 2048, b"y" * 2048, b"z" * 2048], total=6144)
    with pytest.raises(BulkPreemptError) as ei:
        worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=300.0, pace=pace)
    assert ei.value.bytes_read == 4096, f"作废字节没挂上：{ei.value.bytes_read}"


def test_reroll_probe_still_fires_when_the_link_is_really_slow(monkeypatch) -> None:
    """净值 ≠ 万灵药：**真**慢（没有让路可扣）仍必须重抽（别把支路修死）。

    2026-10-13：迟滞要**连续两窗**慢 ⇒ 两块各开一窗，第二窗动手（作废 = 两块）。
    """
    seen: list[float] = []

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append(elapsed)
        return True, 6.4 * 1024, 1180.0  # 判「该重抽」

    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 1.0)
    monkeypatch.setattr(http_mod.time, "time", _FakeClock(step=1.0))
    resp = _FakeResp([b"x" * (256 * 1024), b"y" * (256 * 1024)], total=8 * MB)
    with pytest.raises(worker_mod.WireSlowError) as ei:
        worker_mod._read_body(
            resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True, pace=lambda: 0.0
        )
    assert len(seen) == 2 and seen[0] >= 0.0
    assert ei.value.bytes_read == 512 * 1024  # 两窗判慢那一刻收到的两块


def test_preempt_retries_do_not_consume_attempts(monkeypatch) -> None:
    """被 P1 挤走**不消耗** `attempts`：连挤 5 次（`attempts=3`）后仍能成功。"""
    calls = {"n": 0}
    body = b"p" * 128

    def fake(base_url: str, token: str, path: str, **kw):
        calls["n"] += 1
        if calls["n"] <= 5:
            raise BulkPreemptError("被 P1 挤走", bytes_read=MB)
        return 200, body

    monkeypatch.setattr(http_mod, "_request", fake)
    lines: list[str] = []
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/j9/payload",
        timeout=300.0,
        attempts=3,
        log=lines.append,
        wire_jid="j9",
        wire_seg="payload",
        reroll=True,
        bulk_prio=BULK_P2_PREFETCH,
    )
    assert out == body
    assert calls["n"] == 6, f"抢占被当成失败了（挤 5 次后应第 6 次才真传）：{calls['n']}"
    # 每次挤走都留一行日志（G4 的对账口径靠它）
    assert sum("挤走" in ln for ln in lines) == 5
    worker_mod._wire_flush("j9", lines.append)
    assert "preempt=5(wasted 5.00MB)" in lines[-1], lines[-1]


def test_preempt_budget_is_capped_and_still_raises_preempt_error(monkeypatch) -> None:
    """一直挤 ⇒ 最多重排 `WIRE_PREEMPT_MAX` 次，然后抛 **`BulkPreemptError`**（不是 `RetryableError`）。"""
    calls = {"n": 0}

    def fake(base_url: str, token: str, path: str, **kw):
        calls["n"] += 1
        raise BulkPreemptError("被 P1 挤走", bytes_read=1024)

    monkeypatch.setattr(http_mod, "_request", fake)
    lines: list[str] = []
    with pytest.raises(BulkPreemptError):
        worker_mod._get_with_retry(
            "http://hub",
            "t",
            "/jobs/j10/payload",
            timeout=300.0,
            attempts=3,
            log=lines.append,
            wire_jid="j10",
            wire_seg="payload",
            bulk_prio=BULK_P2_PREFETCH,
        )
    assert calls["n"] == worker_mod.WIRE_PREEMPT_MAX + 1, calls
    # G4 的对账口径：wire 行的 `preempt=N` 与日志里的「挤走」行数**恒等**
    squashed = sum("挤走" in ln for ln in lines)
    assert squashed == calls["n"], f"日志行数与抢占次数对不上：{squashed} != {calls['n']}"
    worker_mod._wire_flush("j10", lines.append)
    assert f"preempt={squashed}(wasted" in lines[-1], lines[-1]


def test_real_failures_still_consume_attempts_after_preempts(monkeypatch) -> None:
    """抢占不吃 `attempts`，但**真实失败照吃**：挤 2 次 + 5xx 2 次（`attempts=2`）⇒ RetryableError。"""
    calls = {"n": 0}

    def fake(base_url: str, token: str, path: str, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise BulkPreemptError("被 P1 挤走", bytes_read=0)
        return 500, b"boom"

    monkeypatch.setattr(http_mod, "_request", fake)
    # 同上：2 次挤走 + 2 次真失败中间的退避（2s/4s）不用真等。
    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)
    with pytest.raises(RetryableError):
        worker_mod._get_with_retry(
            "http://hub",
            "t",
            "/jobs/j11/payload",
            timeout=60.0,
            attempts=2,
            log=lambda _m: None,
            bulk_prio=BULK_P2_PREFETCH,
        )
    assert calls["n"] == 4, f"2 次挤走 + 2 次真实尝试：{calls['n']}"


# ──────────── ⑦ 段账口径：排队不算传输（2026-09-25，plan/bulk-p2-preempt-fix §8.1）────────────
#
# 现场：同一段日志里既有 `result=0.63MB/18-19s`，又有 `bulk payload: 排队 17.6s 才拿到单通道` ——
# 而段秒数当时是**从进槽前**起算的（`t_req` / `t_a`）⇒ 它含排队。hub 侧的
# `响应发送完成 … in X s` 只量发送窗（`hub_server._bytes` 的 `t0` 在写完响应头之后）⇒ 两侧同段
# 根本不可比（`tools/wire_report.py` 文档里「与 hub 侧同段对账即可本地化慢腿」的前提被破坏）。


def _hold_slot_for(seconds: float, monkeypatch) -> threading.Thread:
    """占住唯一 bulk 通道：**等被测调用真的排上队**，再模拟它还传 `seconds` 秒（事件驱动）。

    为什么不能让持有者按墙钟放手（2026-10-04 门禁 flake 修）：旧版从本函数返回就开始
    `sleep(seconds)`——满载时主线程还没被调度进 `slot()`，持有者已经放手 ⇒ 调用方
    `waited=False`、排队账恒 0（`wait=0.0` 假红；`queue_waits>=1`、「排队」日志同族判据
    被一起吃）。而 `slot()` 的等待者自己不发出任何信号，只能从等待原语上观测 ⇒ 换成
    可观测 Event（`_CountingEvent`），等到有线程真卡在 `_released` 上才开始计时：
    「排上队」是构造性的，与调度无关。10s 只是挂起兜底：等不到就照旧放手，由用例断言
    响亮失败（不再静默偶发）。
    """
    holding = threading.Event()
    waiting = _CountingEvent()
    # 只换等待原语（仍是 Event，`slot()` 的等/唤醒语义不变）；monkeypatch 用例结束还原。
    monkeypatch.setattr(worker_mod._BULK, "_released", waiting)

    def holder() -> None:
        with worker_mod._BULK.slot(BULK_P1_CRITICAL, label="holder"):
            holding.set()
            waiting.wait_until(1, timeout=10.0)
            # sleep-ok: 夹具模拟的工作量：从「排上队」起算的占位时长（不是同步手段）
            time.sleep(seconds)

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert holding.wait(5), "占位线程没进通道"
    return th


def _segment_seconds(line: str, seg: str) -> float:
    m = re.search(rf"{seg}=[\d.]+MB/([\d.]+)s", line)
    assert m, line
    return float(m.group(1))


def test_segment_seconds_exclude_the_queue_wait(monkeypatch) -> None:
    """下载段账 = **纯传输**（排队归调度账 `wait=` / `排队 … 才拿到单通道`）。"""
    th = _hold_slot_for(0.3, monkeypatch)
    monkeypatch.setattr(http_mod, "_request", lambda *a, **kw: (200, b"p" * 128))
    t0 = time.time()
    out = worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/jq/payload",
        timeout=60.0,
        attempts=1,
        log=lambda _m: None,
        wire_jid="jq",
        wire_seg="payload",
    )
    wall = time.time() - t0
    th.join(5)
    # 占位从「排上队」起算（事件驱动，见 `_hold_slot_for`）⇒ 墙钟下界是结构性的；
    # 0.2 只是本就不该失败的旧余量。
    assert out and wall >= 0.2, f"夹具没让这次调用真的排上队：{wall:.2f}s"

    lines: list[str] = []
    worker_mod._wire_flush("jq", lines.append)
    # timing-ok: 夹具模拟（段秒数是极小块的真传输，≈0；上界只挡「把排队算进去」）
    assert _segment_seconds(lines[-1], "payload") < 0.2, f"段秒数把排队算进去了：{lines[-1]}"
    assert worker_mod._BULK.stats()["queue_waits"] >= 1, "调度器没记到这次排队"


def test_queue_log_label_carries_the_priority(monkeypatch) -> None:
    """S3a/W0：排队行的 label 带优先级（`bulk payload/P2: 排队 …`）——现场才分得清谁被卡。

    修前是 `bulk payload: 排队 …`，关键下载与预取撞同一个名字（§1.6-①）。
    """
    worker_mod._WIRE.clear()
    worker_mod._BULK.reset()
    th = _hold_slot_for(1.05, monkeypatch)  # >1.0s 才触发 `slot()` 的「排队」日志（既有阈值）
    monkeypatch.setattr(http_mod, "_request", lambda *a, **kw: (200, b"p" * 16))
    logs: list[str] = []
    prev_log = worker_mod._BULK._log
    worker_mod.set_bulk_log(logs.append)
    try:
        worker_mod._get_with_retry(
            "http://hub",
            "t",
            "/jobs/jp/payload",
            timeout=60.0,
            attempts=1,
            log=lambda _m: None,
            wire_jid="prefetch",
            wire_seg="payload",
            bulk_prio=BULK_P2_PREFETCH,
        )
    finally:
        worker_mod.set_bulk_log(prev_log)
    th.join(5)
    assert any("bulk payload/P2: 排队" in m for m in logs), f"label 没带优先级：{logs}"


def test_queue_wait_is_attributed_by_jid(monkeypatch) -> None:
    """S3d/G5：排队按 jid 归属——预取（合成 id）的排队只进预取的账，job 的 `wait=` 不带它。"""
    worker_mod._WIRE.clear()
    worker_mod._BULK.reset()
    th = _hold_slot_for(0.2, monkeypatch)
    # 确定性复现 2026-10-04 门禁 flake：模拟「主线程在到达 slot() 前被调度延迟」——
    # 修复前持有者按墙钟早就放手，调用方 waited=False、wait=0.0（必红）；事件驱动后
    # 持有者等排队观测再计时，延迟多久都归账（必绿）。这不是睡眠同步，是回归探针。
    real_slot = worker_mod._BULK.slot

    def _slow_slot(*a, **kw):
        # sleep-ok: 夹具模拟的工作量：调用方被调度延迟后才进 slot()（回归探针）
        time.sleep(0.4)
        return real_slot(*a, **kw)

    monkeypatch.setattr(worker_mod._BULK, "slot", _slow_slot)
    monkeypatch.setattr(http_mod, "_request", lambda *a, **kw: (200, b"p" * 16))
    worker_mod._get_with_retry(
        "http://hub",
        "t",
        "/jobs/jp/payload",
        timeout=60.0,
        attempts=1,
        log=lambda _m: None,
        wire_jid="prefetch",
        wire_seg="payload",
    )
    th.join(5)
    lines: list[str] = []
    worker_mod._wire_flush("prefetch", lines.append)
    assert len(lines) == 1 and "wait=" in lines[0], lines
    wait = float(lines[0].split("wait=", 1)[1].split("s", 1)[0])
    assert wait > 0.0, f"预取的排队没有归到自己账上：{lines[0]}"
    job_lines: list[str] = []
    worker_mod._wire_flush("j" * 16, job_lines.append)
    assert job_lines == [], "job 没有这笔排队 ⇒ 不该有它的账"


def test_result_segment_seconds_exclude_the_queue_wait(monkeypatch) -> None:
    """回传段同理（现场 `result=0.63MB/18-19s` 的主角）。"""
    th = _hold_slot_for(0.3, monkeypatch)
    monkeypatch.setattr(JL, "_request", lambda *a, **kw: (200, b'{"ok":1}'))
    status = worker_mod.post_result("http://hub", "t", "jr", {"a": 1}, log=lambda _m: None)
    th.join(5)
    assert status == 200

    lines: list[str] = []
    worker_mod._wire_flush("jr", lines.append)
    # timing-ok: 夹具模拟（同上下载段：真实传输 ≈0，上界只挡「把排队算进去」）
    assert _segment_seconds(lines[-1], "result") < 0.2, f"回传段秒数把排队算进去了：{lines[-1]}"
