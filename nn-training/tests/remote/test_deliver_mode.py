"""tests/remote/test_deliver_mode.py —— 补传**档位机**与锚点兜底（`plan/offline-deliver-full-and-gaps`）。

钉住两件事（plan §5-D9 / §3.5）：

  1. **三档是观测结论不是旋钮**：500 KB/s ⇒ `full`、50 KB/s ⇒ `bulk`、5 KB/s ⇒ `anchors`（D9 的
     验收数字），且**断连是独立一维**（带宽再高，断 3 次也掉 `anchors`）；
  2. **锚点兜底**：`eval_log` 里没有读数时按 `every` 取（不再退回全量），`every<=0` 才是旧行为。
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
    BULK_MIN_BPS,
    BYTES_PER_ROUND,
    DEFAULT_ROUND_PERIOD_SEC,
    DISCONNECT_MAX,
    FULL_MIN_BPS,
    SAFETY,
    choose_mode,
    eta_sec,
    eta_text,
    need_bps,
)
from remote.deliver_wire import pick_anchors
from remote.offline_deliver import OfflineDeliverer

#: 一轮 15 s 的两档需求（59.7 / 18.5 KB/s）乘 SAFETY 后应与两个常量对得上——
#: 这是「D9 直接拿绝对数字验收」与「阈值随轮周期变化」两件事不打架的锚。
_WANT_FULL = BYTES_PER_ROUND["full"] / DEFAULT_ROUND_PERIOD_SEC * SAFETY
_WANT_BULK = BYTES_PER_ROUND["bulk"] / DEFAULT_ROUND_PERIOD_SEC * SAFETY


def _ceil_kb(x: float) -> int:
    """上取整到整 KB/s（常量的形状：人读得懂的 80 000 / 25 000）。"""
    return math.ceil(x / 1000.0) * 1000


def test_constant_thresholds_match_the_dynamic_formula() -> None:
    """`FULL_MIN_BPS` / `BULK_MIN_BPS` = 缺省轮周期下动态需求**上取整到整 KB/s**（不许各写一份）。

    实测 79 650.8 / 24 653.1 B/s ⇒ 80 000 / 25 000。判档用的是 `need_bps × SAFETY`（动态），
    这两个常量只做读数与 D9 的绝对数字验收——**差那几百 B/s 是刻意的四舍五入**，
    不是两份不一致的公式。
    """
    assert _ceil_kb(_WANT_FULL) == FULL_MIN_BPS
    assert _ceil_kb(_WANT_BULK) == BULK_MIN_BPS
    assert FULL_MIN_BPS == 80_000
    assert BULK_MIN_BPS == 25_000
    # 动态判据比常量**略宽松**一点 ⇒ 用常量做验收是安全的（不会「常量说行、动态说不行」）
    assert _WANT_FULL <= FULL_MIN_BPS
    assert _WANT_BULK <= BULK_MIN_BPS


@pytest.mark.parametrize(
    "avail_kb, want",
    [
        (500_000, "full"),  # 正常档（用户口径 >500 KB/s）
        (80_000, "full"),  # 恰好卡在下限
        (79_000, "bulk"),  # 差一点
        (50_000, "bulk"),  # 晚高峰（用户口径 <50 KB/s）
        (25_000, "bulk"),
        (24_000, "anchors"),
        (5_000, "anchors"),  # 抖动档
        (0, "anchors"),
        (-1, "anchors"),
    ],
)
def test_choose_mode_three_tiers(avail_kb: int, want: str) -> None:
    assert choose_mode(float(avail_kb)) == want


def test_choose_mode_disconnect_overrides_bandwidth() -> None:
    """断连是**独立一维**：带宽 1 MB/s、断 3 次 ⇒ 仍掉 anchors。"""
    assert choose_mode(1_000_000.0) == "full"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX - 1) == "full"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX) == "anchors"
    assert choose_mode(1_000_000.0, disconnects=DISCONNECT_MAX + 5) == "anchors"


def test_choose_mode_cold_start_probes_with_bulk() -> None:
    """还没量出吞吐 ⇒ 用 `bulk`（277 KB）探一次路，别上来就赌 896 KB。"""
    assert choose_mode(None) == "bulk"
    assert choose_mode(None, disconnects=DISCONNECT_MAX) == "anchors"


def test_choose_mode_scales_with_round_period() -> None:
    """慢腿（60 s/轮）不该被「15 s/轮」的绝对阈值钉死：need(full) 降到 15 KB/s。"""
    assert choose_mode(20_000.0) == "anchors"  # 15 s/轮时要 79.5 KB/s
    assert choose_mode(20_000.0, round_period_sec=60.0) == "full"
    assert need_bps("full", round_period_sec=60.0) == pytest.approx(
        BYTES_PER_ROUND["full"] / 60.0
    )


def test_need_bps_never_divides_by_zero() -> None:
    assert need_bps("full", round_period_sec=0) == pytest.approx(
        BYTES_PER_ROUND["full"] / DEFAULT_ROUND_PERIOD_SEC
    )
    assert need_bps("nonsense", round_period_sec=15.0) == pytest.approx(
        BYTES_PER_ROUND["anchors"] / 15.0
    )


def test_eta_zero_backlog_and_unknown_bandwidth() -> None:
    assert eta_sec(0, mode="full", avail_bps=500_000.0) == 0.0
    assert eta_sec(10, mode="full", avail_bps=None) is None
    assert eta_sec(10, mode="full", avail_bps=0) is None
    assert eta_text(10, mode="full", avail_bps=None) == "-"
    assert eta_text(0, mode="full", avail_bps=None) == "0s"


def test_eta_text_avoids_false_precision() -> None:
    assert eta_text(1, mode="full", avail_bps=500_000.0) == "2s"  # 1.79 s
    # 500 轮 × 896 KB / 100 KB/s = 4480 s ⇒ 只写「>1h」，不写 4480s（假精确）
    assert eta_text(500, mode="full", avail_bps=100_000.0) == ">1h"
    assert eta_sec(500, mode="full", avail_bps=100_000.0) == pytest.approx(4480.36)


# ────────────────────────── 锚点兜底（plan §3.5 / S6）──────────────────────────


def test_pick_anchors_uses_eval_iters_when_present() -> None:
    assert pick_anchors([1, 2, 3, 4, 5], {5}) == [5]
    assert pick_anchors([1, 2, 3, 4, 5], {5}, with_newest=True) == [5]
    assert pick_anchors([1, 2, 3], {5}, with_newest=True) == [3]  # 末轮强制（D-1）


def test_pick_anchors_falls_back_to_every_when_no_eval_readings() -> None:
    """★ 2026-10-23：无 A 层读数 ⇒ 按 `every` 兜底，**不是全量**。"""
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


# ────────────────────────── S1：档位决定发整份还是原件 ──────────────────────────

#: 假的权重 delta 容器（魔数 + 头长度 + 一个空头）：`any_container_file` 认它，展开必失败。
_FAKE_DELTA = b"BWD1" + struct.pack(">I", 2) + b"{}" + b"\x00" * 8
#: 假的「整份」（首字节 `{` ⇒ 不是容器）。
_PLAIN_FULL = b'{"format":"nn-weights-json","version":1}\n'

_RUN = "r" * 8


def _round(root: Path, it: int, wj: bytes, opt: bytes = b"") -> None:
    ArtifactStore(root, run_id=_RUN).checkpoint(it, weights_json=wj, opt_tar=opt)

def test_full_parts_bulk_sends_the_original_container(tmp_path: Path) -> None:
    """`bulk` 档不展开：直接发原件（这一档的意义就是把体积压到最小）。"""
    logs: list[str] = []
    _round(tmp_path, 1, _FAKE_DELTA)
    w, o, form = _deliverer(tmp_path, logs)._full_parts_for(1, mode="bulk")
    assert w == _FAKE_DELTA, "bulk 档不许展开"
    assert o == b""
    assert form == "delta"
    assert logs == [], f"bulk 档一行日志都不该有（展开动作一次都没发生）：{logs}"


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
    """`full` 档展开失败（基准不在本机）⇒ 回落发原件 + `wire_form=delta` + 一行可见日志。

    这是「不阻塞、不静默」的落地：投递照旧进行，只是这一轮不是整份，控制台看得到。
    """
    logs: list[str] = []
    _round(tmp_path, 3, _FAKE_DELTA)
    w, _o, form = _deliverer(tmp_path, logs)._full_parts_for(3, mode="full")
    assert w == _FAKE_DELTA, "展开失败必须回落原件（绝不丢字节、也不发半个包）"
    assert form == "delta"


# ────────────────────────── S5：统计批（`?parts=metrics`）─────────────────────────


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
    """把每次 POST 折成 `"metrics"` / `"weights"` 两件再记账（读的是 query，不是字符串匹配）。

    `parts=metrics` 返回指定状态、其余一律 200 —— 把「统计那一件」与「权重那一件」分开断言。
    """
    from urllib.parse import parse_qs, urlparse

    def op(url, data, headers, timeout):
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
    assert seen.count("metrics") == 0, seen
    # `row` 随权重体到达 ⇒ 统计件也记上（不必再单独发一次）；缺省关的是「**单独**的统计批」
    assert d._metrics_sent == {1}, "权重体已带 row ⇒ 销账"
    assert d.status()["metrics_backlog"] == 0


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
    # 统计**批**那条路被停用；但权重体本身仍带着 row 过去了 ⇒ 那一轮的统计并不欠
    assert d._metrics_sent == {1}
    # 权重那一趟仍然打出去了（状态 200、且不是 metrics 路径）
    assert seen.count("weights") >= 1, seen


# ────────────────────────── S0 接线：档位决定「发哪些轮」──────────────────────────


def test_adaptive_switches_from_anchors_to_bulk_and_then_sends_every_round(
    tmp_path: Path,
) -> None:
    """★ 用户口径：网络顺畅 ⇒ **每一轮都发**。

    时序：`choose_mode(None)` = 冷启动 `bulk`，但**滞回 3 拍**才切 ⇒ 前两拍仍是 `anchors`
    （无 A 层读数 + `eval_every=5` ⇒ 一个都不发），第三拍起发全量。
    """
    seen: list[str] = []
    logs: list[str] = []
    for it in (1, 2, 3):
        _round_with_row(tmp_path, it, _PLAIN_FULL)
    clock = [1000.0]
    d = OfflineDeliverer(
        base_url="http://hub.invalid",
        token="tok",
        run_id=_RUN,
        artifacts_dir=tmp_path,
        adaptive=True,
        eval_every=5,
        metrics_parts=True,
        opener=_metrics_opener(seen),
        now_fn=lambda: clock[0],
        log=logs.append,
    )
    d.sync()
    assert d._modes.mode == "anchors", "第一拍还没过滞回"
    assert seen.count("weights") == 0, "anchors 档 + 无锚点 ⇒ 一件不发"
    clock[0] += 1.0
    d.sync()
    assert d._modes.mode == "anchors", "第二拍仍未过滞回（防抖）"
    clock[0] += 1.0
    d.sync()
    assert d._modes.mode == "bulk", f"第三拍该切了：{d._modes.mode}"
    assert seen.count("weights") >= 1, seen
    # 切档后 `pending()` 全量进候选 ⇒ 3 轮都该发出去
    assert set(d._delivered) == {1, 2, 3}, d._delivered
    st = d.status()
    assert st["mode"] == "bulk" and st["adaptive"] is True
    assert st["backlog"] == 0 and st["eta"] == "0s"


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
