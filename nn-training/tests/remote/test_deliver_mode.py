"""tests/remote/test_deliver_mode.py —— 补传**档位机**与锚点兜底（`plan/offline-deliver-full-and-gaps`）。

2026-10-24 评审后钉住四件事：

  1. **两档是观测结论不是旋钮**：`every`（每轮都发；形态在写盘时定）与 `anchors`（只发有 A 层
     读数的轮 + 每轮统计）；断连是独立一维（带宽再高，断 3 次也掉 `anchors`）；
  2. **断连窗口会过期**：3 次失败之后网络恢复，`disconnects` 必须在 60 s 后归零，档位能回升
     （评审 P0：旧实现只在「下一次失败」时裁剪，恢复后永久卡 `anchors`）；
  3. **`anchors` 档统计不被饿死**：没有锚点待发时，统计批仍要尝试（评审 P0）；
  4. **积压能越过 `sync_cap`**：`every` 档下一次 `_push_backlog` 把整段推空（评审 P1），
     以及「权重体没带 row」时不能把统计销账（评审 P1）。
"""

from __future__ import annotations

import hashlib
import math
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.artifacts import ArtifactStore
from remote.deliver_mode import (
    ANCHORS_MIN_BPS,
    BYTES_PER_ROUND,
    DEFAULT_ROUND_PERIOD_SEC,
    DISCONNECT_MAX,
    DISCONNECT_WINDOW_SEC,
    EVERY_MIN_BPS,
    SAFETY,
    ModeTracker,
    choose_mode,
    eta_sec,
    eta_text,
    need_bps,
)
from remote.deliver_wire import pick_anchors
from remote.offline_deliver import OfflineDeliverer

#: 一轮 15 s 的两档需求（27 / 13.5 KB/s）乘 SAFETY 后应与两个常量对得上。
_WANT_EVERY = BYTES_PER_ROUND["every"] / DEFAULT_ROUND_PERIOD_SEC * SAFETY
_WANT_ANCHORS = BYTES_PER_ROUND["anchors"] / DEFAULT_ROUND_PERIOD_SEC * SAFETY


def _ceil_kb(x: float) -> int:
    """上取整到整 KB/s（常量的形状：人读得懂的 36 000 / 19 000）。"""
    return math.ceil(x / 1000.0) * 1000


def test_constant_thresholds_match_the_dynamic_formula() -> None:
    """常量 = 缺省轮周期下动态需求**上取整到整 KB/s**（不许各写一份）。"""
    assert _ceil_kb(_WANT_EVERY) == EVERY_MIN_BPS == 36_000
    assert _ceil_kb(_WANT_ANCHORS) == ANCHORS_MIN_BPS == 19_000
    # 动态判据比常量**略宽松**一点 ⇒ 用常量做验收是安全的
    assert _WANT_EVERY <= EVERY_MIN_BPS
    assert _WANT_ANCHORS <= ANCHORS_MIN_BPS


@pytest.mark.parametrize(
    "avail_kb, want",
    [
        (500_000, "every"),  # 正常档（用户口径 >500 KB/s）
        (36_000, "every"),  # 恰好卡在下限
        (35_000, "anchors"),  # 差一点
        (20_000, "anchors"),  # 晚高峰
        (5_000, "anchors"),  # 抖动档
        (0, "anchors"),
        (-1, "anchors"),
    ],
)
def test_choose_mode_two_tiers(avail_kb: int, want: str) -> None:
    assert choose_mode(float(avail_kb)) == want


def test_choose_mode_disconnect_overrides_bandwidth() -> None:
    """断连是**独立一维**：带宽 1 MB/s、断 3 次 ⇒ 仍掉 anchors。"""
    assert choose_mode(1_000_000.0) == "every"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX - 1) == "every"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX) == "anchors"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX + 5) == "anchors"


def test_choose_mode_cold_start_is_every() -> None:
    """还没量出吞吐 ⇒ `every`（用户口径：顺畅就每轮发；慢链路会在头几拍自然降档）。"""
    assert choose_mode(None) == "every"
    assert choose_mode(None, disconnects=DISCONNECT_MAX) == "anchors"


def test_choose_mode_scales_with_round_period() -> None:
    """慢腿（60 s/轮）不该被「15 s/轮」的绝对阈值钉死。"""
    assert choose_mode(20_000.0) == "anchors"  # 15 s/轮时要 36 KB/s
    assert choose_mode(20_000.0, round_period_sec=60.0) == "every"
    assert need_bps("every", round_period_sec=60.0) == pytest.approx(
        BYTES_PER_ROUND["every"] / 60.0
    )


def test_need_bps_never_divides_by_zero() -> None:
    assert need_bps("every", round_period_sec=0) == pytest.approx(
        BYTES_PER_ROUND["every"] / DEFAULT_ROUND_PERIOD_SEC
    )
    assert need_bps("nonsense", round_period_sec=15.0) == pytest.approx(
        BYTES_PER_ROUND["anchors"] / 15.0
    )


def test_eta_zero_backlog_and_unknown_bandwidth() -> None:
    assert eta_sec(0, mode="every", avail_bps=500_000.0) == 0.0
    assert eta_sec(10, mode="every", avail_bps=None) is None
    assert eta_sec(10, mode="every", avail_bps=0) is None
    assert eta_text(10, mode="every", avail_bps=None) == "-"
    assert eta_text(0, mode="every", avail_bps=None) == "0s"


def test_eta_text_avoids_false_precision() -> None:
    assert eta_text(1, mode="every", avail_bps=500_000.0) == "1s"
    # 1000 轮 × 405 KB / 100 KB/s = 4050 s ⇒ 只写「>1h」
    assert eta_text(1000, mode="every", avail_bps=100_000.0) == ">1h"
    assert eta_sec(1000, mode="every", avail_bps=100_000.0) == pytest.approx(4050.0)


# ────────────────────────── 断连窗口（评审 P0）──────────────────────────


def test_disconnect_window_expires_and_mode_recovers() -> None:
    """3 次断连后网络恢复 ⇒ 60 s 后断连计数归零，连续 3 拍后升回 `every`。"""
    clock = [1000.0]
    m = ModeTracker(adaptive=True, now_fn=lambda: clock[0])
    m.note_delivery(500_000, 1.0)
    for _ in range(DISCONNECT_MAX):
        m.note_disconnect()
    assert m.tick_mode() == "every"  # 第 1 拍：只是记下想要的档
    assert m.tick_mode() == "every"  # 第 2 拍：仍未过滞回
    assert m.tick_mode() == "anchors", "第 3 拍必须切（断连压过带宽）"
    assert m.disconnects == DISCONNECT_MAX
    clock[0] += DISCONNECT_WINDOW_SEC + 1.0
    assert m.disconnects == 0, "窗口必须按当前时间过期（不能等下一次失败才裁剪）"
    assert m.tick_mode() == "anchors"  # 回升也要 3 拍
    assert m.tick_mode() == "anchors"
    assert m.tick_mode() == "every"


def test_round_period_comes_from_the_last_five_rows() -> None:
    clock = [1000.0]
    m = ModeTracker(adaptive=True, now_fn=lambda: clock[0])
    for v in (10, 20, 30, 40, 50, 90):
        m.note_round_period(v)
    assert m.round_period_sec == pytest.approx(40.0)  # 最近 5 个：20/30/40/50/90 的中位数
    m.note_round_period("bad")
    m.note_round_period(-1)
    assert m.round_period_sec == pytest.approx(40.0)


# ────────────────────────── 锚点兜底（plan §3.5 / S6）──────────────────────────


def test_pick_anchors_uses_eval_iters_when_present() -> None:
    assert pick_anchors([1, 2, 3, 4, 5], {5}) == [5]
    assert pick_anchors([1, 2, 3, 4, 5], {5}, with_newest=True) == [5]
    assert pick_anchors([1, 2, 3], {5}, with_newest=True) == [3]  # 末轮强制（D-1）


def test_pick_anchors_falls_back_to_every_when_no_eval_readings() -> None:
    assert pick_anchors([3, 4, 5, 6, 7], set(), every=5) == [5]
    assert pick_anchors([3, 4, 5, 6, 7], set(), every=5, with_newest=True) == [5, 7]
    assert pick_anchors(list(range(1, 16)), set(), every=5) == [5, 10, 15]


def test_pick_anchors_keeps_legacy_full_behavior_without_every() -> None:
    """`every<=0` = 旧行为（全量）——只留给不传这个参数的历史调用点。"""
    assert pick_anchors([1, 2, 3], set()) == [1, 2, 3]
    assert pick_anchors([1, 2, 3], set(), every=0) == [1, 2, 3]


def test_pick_anchors_empty_pending() -> None:
    assert pick_anchors([], {5}, every=5) == []
    assert pick_anchors([], set(), every=5) == []


# ────────────────────────── S1 死代码契约（不接线）──────────────────────────

#: 假的权重 delta 容器（魔数 + 头长度 + 一个空头）：`any_container_file` 认它，展开必失败。
_FAKE_DELTA = b"BWD1" + struct.pack(">I", 2) + b"{}" + b"\x00" * 8
#: 假的「整份」（首字节 `{` ⇒ 不是容器）。
_PLAIN_FULL = b'{"format":"nn-weights-json","version":1}\n'

_RUN = "r" * 8


def _round(root: Path, it: int, wj: bytes, opt: bytes = b"") -> None:
    ArtifactStore(root, run_id=_RUN).checkpoint(it, weights_json=wj, opt_tar=opt)


def test_full_parts_bulk_sends_the_original_container(tmp_path: Path) -> None:
    """非 `full` 模式不展开：直接发原件（`full_parts_for` 是死代码，保留其契约）。"""
    logs: list[str] = []
    _round(tmp_path, 1, _FAKE_DELTA)
    w, o, form = _deliverer(tmp_path, logs)._full_parts_for(1, mode="anchors")
    assert w == _FAKE_DELTA, "非 full 档不许展开"
    assert o == b""
    assert form == "delta"
    assert logs == [], f"不该有展开日志：{logs}"


def test_full_parts_full_keeps_an_already_standard_weights_file(tmp_path: Path) -> None:
    """`full` 档遇到**本来就是整份**的文件 ⇒ 原样返回（`wire_form=full`）。"""
    logs: list[str] = []
    _round(tmp_path, 2, _PLAIN_FULL)
    w, _o, form = _deliverer(tmp_path, logs)._full_parts_for(2, mode="full")
    assert w == _PLAIN_FULL
    assert form == "full"


def test_full_parts_full_falls_back_to_the_original_when_expansion_fails(
    tmp_path: Path,
) -> None:
    """`full` 档展开失败（基准不在本机）⇒ 回落发原件 + `wire_form=delta`。"""
    logs: list[str] = []
    _round(tmp_path, 3, _FAKE_DELTA)
    w, _o, form = _deliverer(tmp_path, logs)._full_parts_for(3, mode="full")
    assert w == _FAKE_DELTA, "展开失败必须回落原件（绝不丢字节、也不发半个包）"
    assert form == "delta"


# ────────────────────────── S5：统计批（`?parts=metrics`）──────────────────────────


def _deliverer(
    root: Path,
    logs: list[str],
    *,
    metrics_parts: bool = False,
    opener=None,
    **kw,
) -> OfflineDeliverer:
    return OfflineDeliverer(
        base_url="http://hub.invalid",
        token="tok",
        run_id=_RUN,
        artifacts_dir=root,
        metrics_parts=metrics_parts,
        opener=opener,
        log=logs.append,
        **kw,
    )


def _metrics_opener(seen: list[str], *, metrics_status: int = 200):
    """把每次 `/offline/artifact` POST 折成 `"metrics"` / `"weights"` 两件再记账。"""
    from urllib.parse import parse_qs, urlparse

    def op(url, data, headers, timeout):
        if "/offline/artifact" not in url:
            return (200, b"{}")  # /ping
        parts = parse_qs(urlparse(url).query).get("parts") or [""]
        seen.append("metrics" if parts[0] == "metrics" else "weights")
        if parts[0] == "metrics":
            return (metrics_status, b"{}")
        return (200, b"{}")

    return op


def _round_with_row(root: Path, it: int, wj: bytes) -> None:
    """落一轮**带账本行**的产物（`_row_for` 要它才拿得到统计）。"""
    ArtifactStore(root, run_id=_RUN).checkpoint(
        it,
        weights_json=wj,
        opt_tar=b"",
        row={
            "weights_fp": hashlib.sha256(wj).hexdigest(),
            "opt_bytes": 0,
            "agg": {"kl": 0.01, "entropy": 1.5, "policy": 0.001, "value": 0.4},
            "report": {"games": 94, "winRate": 0.5},
            "wall_sec": 15.0,
        },
    )


def test_metrics_batch_goes_to_parts_metrics(tmp_path: Path) -> None:
    """开了开关：`sync()` 先把统计批打去 `?parts=metrics`（小件优先）。"""
    seen: list[str] = []
    logs: list[str] = []
    _round_with_row(tmp_path, 1, _PLAIN_FULL)
    d = _deliverer(tmp_path, logs, metrics_parts=True, opener=_metrics_opener(seen))
    d.sync()
    assert seen.count("metrics") == 1, seen
    assert d._metrics_sent == {1}, "送达后统计件销账"
    assert d._metrics_stat[0] == 1, "读数：1 轮 / 若干字节"


def test_metrics_batch_is_off_by_default(tmp_path: Path) -> None:
    """缺省不启用 ⇒ 老 argv / 老调用点行为逐字不变（一个统计体都不发）。"""
    seen: list[str] = []
    logs: list[str] = []
    _round_with_row(tmp_path, 1, _PLAIN_FULL)
    d = _deliverer(tmp_path, logs, opener=_metrics_opener(seen))
    d.sync()
    assert seen.count("metrics") == 0
    assert d._metrics_sent == {1}, "权重体已带 row ⇒ 销账"
    assert d.status()["metrics_backlog"] == 0


def test_metrics_batch_survives_anchor_starvation(tmp_path: Path) -> None:
    """★ 评审 P0：`anchors` 档没有锚点待发时，统计批仍要尝试（不能走 todo 空早退）。"""
    seen: list[str] = []
    logs: list[str] = []
    for it in (1, 2, 3, 4):
        _round_with_row(tmp_path, it, _PLAIN_FULL)
    d = _deliverer(
        tmp_path,
        logs,
        adaptive=False,
        metrics_parts=True,
        eval_every=5,
        opener=_metrics_opener(seen),
    )
    assert d.pending_anchors() == [], "前提：1…4 里没有 5 的倍数"
    assert d.sync() == 0
    assert seen.count("metrics") == 1, seen
    assert d._metrics_sent == {1, 2, 3, 4}


def test_weights_without_row_do_not_mark_metrics_sent(tmp_path: Path) -> None:
    """★ 评审 P1：权重体因指纹不符没带 row ⇒ 不能销统计账。"""
    w = _PLAIN_FULL
    ArtifactStore(tmp_path, run_id=_RUN).checkpoint(
        1,
        weights_json=w,
        opt_tar=b"",
        row={"it": 1, "weights_fp": "deadbeef", "wall_sec": 15.0},
    )
    seen: list[str] = []
    logs: list[str] = []
    d = _deliverer(tmp_path, logs, metrics_parts=True, opener=_metrics_opener(seen))
    d.sync()
    assert seen.count("weights") == 1, seen
    assert d._metrics_sent == set(), "没有 row 的权重体不许销统计账"
    assert d.status()["metrics_backlog"] == 1


def test_metrics_batch_disables_itself_on_404_but_weights_still_flow(
    tmp_path: Path,
) -> None:
    """旧 hub 不认这个入口（404）⇒ 本会话停发统计，**权重那条路照旧**。"""
    seen: list[str] = []
    logs: list[str] = []
    _round_with_row(tmp_path, 1, _PLAIN_FULL)
    d = _deliverer(
        tmp_path, logs, metrics_parts=True, opener=_metrics_opener(seen, metrics_status=404)
    )
    d.sync()
    assert d._metrics_unsupported is True
    assert d._metrics_sent == {1}
    assert seen.count("weights") >= 1, seen


# ────────────────────────── S0 接线：档位决定「发哪些轮」──────────────────────────


def test_adaptive_every_mode_sends_every_round(tmp_path: Path) -> None:
    """★ 用户口径：网络顺畅 ⇒ **每一轮都发**（初始档就是 every，不再等 3 拍）。"""
    seen: list[str] = []
    logs: list[str] = []
    for it in (1, 2, 3):
        _round_with_row(tmp_path, it, _PLAIN_FULL)
    d = _deliverer(
        tmp_path,
        logs,
        adaptive=True,
        metrics_parts=True,
        eval_every=5,
        opener=_metrics_opener(seen),
    )
    assert d._modes.mode == "every"
    d.sync()
    assert set(d._delivered) == {1, 2, 3}, d._delivered
    st = d.status()
    assert st["mode"] == "every" and st["adaptive"] is True
    assert st["backlog"] == 0 and st["eta"] == "0s"


def test_backlog_drains_past_sync_cap_in_every_mode(tmp_path: Path) -> None:
    """★ 评审 P1：`_push_backlog` 的继续判据必须是 `pending()`，不是锚点。"""
    logs: list[str] = []
    for it in range(1, 7):
        _round_with_row(tmp_path, it, _PLAIN_FULL)
    d = _deliverer(
        tmp_path,
        logs,
        adaptive=True,
        eval_every=5,
        sync_cap=2,
        opener=_metrics_opener([]),
    )
    d._push_backlog(d._now() + 30.0)
    assert set(d._delivered) == set(range(1, 7)), d._delivered


def test_adaptive_off_means_anchors_only(tmp_path: Path) -> None:
    """关掉自适应 ⇒ 永远 `anchors`（= 旧行为：只发有 A 层读数的轮）。"""
    seen: list[str] = []
    logs: list[str] = []
    for it in (1, 2, 3):
        _round_with_row(tmp_path, it, _PLAIN_FULL)
    d = _deliverer(
        tmp_path,
        logs,
        opener=_metrics_opener(seen),
        eval_every=5,
    )
    assert d.adaptive is False
    d.sync()
    assert d._modes.mode == "anchors"
    assert seen.count("weights") == 0, seen
    assert d.status()["mode"] == "anchors"
    assert d.status()["adaptive"] is False
