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

ROOT = Path(__file__).resolve().parent.parent
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

    # 注入点 = `remote.http`：`_get_with_retry` 已搬进 http（S4 第五步），它在本模块命名空间
    # 解析 `_request` ⇒ patch `worker` 会静默失效（patch 后调**宿主**函数的仍是 `worker`）。
    monkeypatch.setattr(http_mod, "_request", fake)
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

    monkeypatch.setattr(http_mod, "_request", fake)
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

    # 同上：`_read_body` 在 `remote.http` 里解析 `_reroll_decision`。
    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
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
    """速率判据拿到的 `elapsed` = **墙钟 − Σ让路**（让路不是链路的错）。"""
    seen: list[float] = []

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append(elapsed)
        return False, 0.0, 0.0  # 本用例只关心它拿到了什么时间

    # patch 面：`_read_body` 的 global 解析在 `remote.http`（S4 第五步拆分）——
    # patch `remote.worker` 的名字只改转发名，改动不了 http 里的解析（静默空操作）。
    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
    calls = {"n": 0}

    def pace() -> float:
        calls["n"] += 1
        if calls["n"] > 1:  # 只有首块前那一次真让路（后两次别把用例拖慢）
            return 0.0
        # sleep-ok: 夹具模拟的工作量：一次停满一秒的让路（不是同步手段）
        time.sleep(1.0)
        return 1.0

    resp = _FakeResp([b"x" * (256 * 1024)], total=8 * MB)
    t0 = time.time()
    out = worker_mod._read_body(
        resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True, pace=pace
    )
    wall = time.time() - t0
    assert len(out) == 256 * 1024
    assert wall >= 1.0, "夹具没真让路（探针没被测到）"
    assert len(seen) == 1, f"首块只该判一次：{seen}"
    assert seen[0] < 0.5, f"让路时间没被扣掉：elapsed={seen[0]:.3f}（应 ≈0，墙钟 ≈{wall:.2f}）"


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
    """净值 ≠ 万灵药：**真**慢（没有让路可扣）仍必须重抽（别把支路修死）。"""
    seen: list[float] = []

    def spy(got: int, total: int, elapsed: float, **kw):
        seen.append(elapsed)
        return True, 6.4 * 1024, 1180.0  # 判「该重抽」

    monkeypatch.setattr(http_mod, "_reroll_decision", spy)
    resp = _FakeResp([b"x" * (256 * 1024)], total=8 * MB)
    with pytest.raises(worker_mod.WireSlowError):
        worker_mod._read_body(
            resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True, pace=lambda: 0.0
        )
    assert seen and seen[0] >= 0.0


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


def _hold_slot_for(seconds: float) -> threading.Thread:
    """占住唯一 bulk 通道 `seconds` 秒（夹具：让被测调用**真的排上队**）。"""
    release = threading.Event()
    holding = threading.Event()

    def holder() -> None:
        with worker_mod._BULK.slot(BULK_P1_CRITICAL, label="holder"):
            holding.set()
            release.wait(10)

    def _free_soon() -> None:
        # sleep-ok: 夹具模拟的工作量：持有者占住通道一段时间（不是同步手段）
        time.sleep(seconds)
        release.set()

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert holding.wait(5), "占位线程没进通道"
    threading.Thread(target=_free_soon, daemon=True).start()
    return th


def _segment_seconds(line: str, seg: str) -> float:
    m = re.search(rf"{seg}=[\d.]+MB/([\d.]+)s", line)
    assert m, line
    return float(m.group(1))


def test_segment_seconds_exclude_the_queue_wait(monkeypatch) -> None:
    """下载段账 = **纯传输**（排队归调度账 `wait=` / `排队 … 才拿到单通道`）。"""
    th = _hold_slot_for(0.3)
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
    assert out and wall >= 0.3, f"夹具没让这次调用真的排上队：{wall:.2f}s"

    lines: list[str] = []
    worker_mod._wire_flush("jq", lines.append)
    assert _segment_seconds(lines[-1], "payload") < 0.2, f"段秒数把排队算进去了：{lines[-1]}"
    assert worker_mod._BULK.stats()["queue_waits"] >= 1, "调度器没记到这次排队"


def test_result_segment_seconds_exclude_the_queue_wait(monkeypatch) -> None:
    """回传段同理（现场 `result=0.63MB/18-19s` 的主角）。"""
    th = _hold_slot_for(0.3)
    monkeypatch.setattr(JL, "_request", lambda *a, **kw: (200, b'{"ok":1}'))
    status = worker_mod.post_result("http://hub", "t", "jr", {"a": 1}, log=lambda _m: None)
    th.join(5)
    assert status == 200

    lines: list[str] = []
    worker_mod._wire_flush("jr", lines.append)
    assert _segment_seconds(lines[-1], "result") < 0.2, f"回传段秒数把排队算进去了：{lines[-1]}"
