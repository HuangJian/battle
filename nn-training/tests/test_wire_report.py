"""tests/test_wire_report.py —— 传输账聚合（`tools/wire_report.py`；plan §4.0 / §7.2 / P0.5）。

为什么有这组用例：plan §7.2 的验收口径是「**报 p50/p90，不报均值**；每组 ≥10 job」，
而 §8 开放问题 4（阈值取值）要靠实机 `wire:` 数据校准——两者都需要一个**可复算**的聚合，
不能靠人眼扫日志。本文件用**现场原文**（12:49 / 12:59 那两条坏签）钉住：

  ① 三类行都认（worker 每 job 摘要 / 引导期摘要 / hub 发送完成行）；
  ② 分位数用最近秩（样本少时不插值不外推）；
  ③ `合计` 不是一段、零字节命中不是速率样本（混进去 p50 会被拉成 0）；
  ④ 重抽只数逐次行（摘要里的 `rerolls=N` 与它同源，两边都算就是重复计数）；
  ⑤ 坏签比例（速率 < 80 KB/s）——`WIRE_MIN_RATE` 取值的直接依据。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_wire_report() -> Any:
    """按**文件路径**加载 `tools/wire_report.py`（不依赖包布局）。

    刻意不写 `from tools import wire_report`：`tools/` 无 `__init__.py`，那样 import 会让
    mypy 把同一份文件当成两个模块（`wire_report` 与 `tools.wire_report`）而直接报错——
    门禁跑的是 `mypy .`。同款做法见 `tests/test_pid_probe_windows_safe.py`。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_wire_report_under_test", ROOT / "tools" / "wire_report.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 必须先登记进 sys.modules：`@dataclass` 建类时要按 `cls.__module__` 反查
    # （`sys.modules.get(...).__dict__`），缺席就 AttributeError。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


wr = _load_wire_report()

MB = 1024 * 1024

#: 现场原文（12:59 那次 payload 以 6–9 KB/s 烧完 300 s 预算，重试换连接后 354 KB/s）。
#: 速率字段是产出方从**同一对 (bytes, sec)** 算的（`3486200/321.8/1024 = 10.6 → 11`），
#: 所以这里与聚合侧重算的值一致——测试夹具必须是自家工具真会写出来的形状。
LIVE_SLOW = (
    "[13:04:51] [worker] job 3c8cf83ce824aef8: wire payload=3.32MB/321.8s(11KB/s) "
    "合计=3.32MB/321.8s(11KB/s)"
)
#: 现场原文（12:49 code 坏签之后，同一会话的下一个 job 走了 code 缓存）。
LIVE_HIT = (
    "[13:04:56] [worker] job 3c8cf83ce824aef8: wire payload=0.50MB/1.0s(512KB/s) "
    "code=cache-hit ts_code=cache-hit reroll=1(wasted 0.25MB) 合计=0.50MB/1.0s(512KB/s)"
)
REROLL_LINE = (
    "[13:04:38] [worker] /jobs/3c8cf83ce824aef8/payload: 瞬时失败(\"body 超时\")\n"
    "[13:04:38] [worker] wire: re-roll #1/3 payload: 实测 6 KB/s < 阈值 80 KB/s"
    "（按此速率剩余 1180s）——断开重发（已收 2883584 bytes 作废）"
)
BOOT_LINE = "[12:51:30] [battle] wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0"
#: 现场原文（12:49 那次 code GET 13.5 KB/s，同日 12:59 靠重抽换连接后 354 KB/s）——
#: 这两行放在一起正是「bad% 高 + reroll 生效」的形状。
BOOT_LINE_2 = "[13:14:00] [battle] wire: code.zip 1.37MB/3.0s(467KB/s) attempts=2 rerolls=1"
HUB_LINE = "[13:04:51] [hub-server] 响应发送完成 /jobs/3c8cf83ce824aef8/payload 3486200 bytes in 12.3s"


def _rows(lines: list[str]) -> dict[str, dict]:
    stats = wr.parse_lines(lines)
    wr._merge_rerolls(stats)
    return {r["seg"]: r for r in wr.summarize(stats)}


# ────────────────────────── ① 三类行都认 ──────────────────────────


def test_worker_summary_is_split_into_segments() -> None:
    """worker 每 job 摘要 → 逐段 `(bytes, sec, rate)`；`合计` 是各段之和、不是一段。"""
    rows = _rows([LIVE_SLOW])
    assert set(rows) == {"job:payload"}  # `合计` 不是一段
    assert rows["job:payload"]["mb"] == pytest.approx(3.32, abs=0.01)
    assert rows["job:payload"]["p50_sec"] == pytest.approx(321.8)
    assert rows["job:payload"]["p50_kbps"] == 11
    assert rows["job:payload"]["bad_pct"] == 100.0  # 11 KB/s 就是那次坏签
    assert rows["job:payload"]["worst_kbps"] == 11


def test_boot_and_hub_lines_are_recognised() -> None:
    """引导期摘要（code.zip / task-pack）与 hub 发送完成行都进表——两者对账即可定位慢腿。"""
    rows = _rows([BOOT_LINE, HUB_LINE])
    assert rows["boot:code.zip"]["mb"] == pytest.approx(1.37, abs=0.01)
    assert rows["boot:code.zip"]["p90_sec"] == pytest.approx(106.0)
    assert rows["boot:code.zip"]["bad_pct"] == 100.0  # 13 KB/s
    assert rows["hub:/jobs/3c8cf83ce824aef8/payload"]["mb"] == pytest.approx(3.32, abs=0.01)
    assert rows["hub:/jobs/3c8cf83ce824aef8/payload"]["p50_sec"] == pytest.approx(12.3)


def test_job_lines_ignore_unrelated_noise() -> None:
    """无关日志行（心跳、下载进度行）不产出任何读数——否则表里全是假段。"""
    rows = _rows(
        [
            "[12:50:30] [battle-rl] [keepalive] alive (2 min)",
            "[13:00:13] [worker] job X: payload 下载中 0.25 MB / 3.32 MB (7%) 用时 39s（6 KB/s）",
        ]
    )
    assert rows == {}


# ────────────────────────── ② 分位数：最近秩 ──────────────────────────


def test_percentile_is_nearest_rank() -> None:
    """p50/p90 一律最近秩：样本少时插值会造出一个从没发生过的数。"""
    assert wr.percentile([], 0.5) != wr.percentile([], 0.5)  # 空样本 = nan
    assert wr.percentile([7.0], 0.9) == 7.0
    assert wr.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert wr.percentile([1.0, 2.0, 3.0, 4.0], 0.9) == 4.0


def test_bimodal_draws_show_up_in_the_seconds_tail_not_the_rate_tail() -> None:
    """44× 双峰：坏签在**秒数**上是高尾（p90 抓得住），在**速率**上是低尾（得看 worst/bad%）。

    所以表里速率只报 p50 + worst —— 报「速率 p90」量到的是好签那一端，会把一次
    321.8 s 的坏签读成「一切正常」（这正是本工具存在的理由）。
    """
    lines = [
        f"[13:0{i}:00] [worker] job j{i}: wire payload=1.00MB/1.0s(1024KB/s)" for i in range(8)
    ] + [
        f"[13:1{i}:00] [worker] job k{i}: wire payload=1.00MB/80.0s(13KB/s)" for i in range(2)
    ]
    row = _rows(lines)["job:payload"]
    assert row["jobs"] == 10
    assert row["p50_kbps"] == 1024 and row["worst_kbps"] == 13
    assert row["p50_sec"] == pytest.approx(1.0)
    assert row["p90_sec"] == pytest.approx(80.0)  # 坏签在秒数上是高尾
    assert row["bad_pct"] == 20.0  # 坏签比例 = 阈值取值的直接依据


# ──────────────── ③④ 命中不参与速率、重抽不重复计数 ────────────────


def test_zero_byte_hits_are_reported_but_not_sampled() -> None:
    """`code=cache-hit` 进表（是本会话省下字节的证据），但不进速率样本（否则 p50 → 0）。"""
    rows = _rows([LIVE_HIT])
    assert rows["job:code"]["hits"] == 1
    assert rows["job:code"]["jobs"] == 0
    assert rows["job:code"]["p50_sec"] is None  # 渲染成「—」，不是 0
    assert rows["job:ts_code"]["hits"] == 1
    assert rows["job:payload"]["p50_kbps"] == 512


def test_rerolls_come_from_the_per_attempt_lines_only() -> None:
    """摘要里的 `reroll=N(wasted …)` 与逐次 `wire: re-roll` 行同源——只算一次。"""
    rows = _rows([LIVE_HIT, REROLL_LINE, LIVE_SLOW])
    assert rows["job:payload"]["rerolls"] == 1  # 逐次行 1 次；摘要里的那个不算第二遍
    assert rows["job:payload"]["wasted_mb"] == pytest.approx(2883584 / MB, abs=0.01)
    assert rows["job:payload"]["jobs"] == 2  # 两次传输都进账


def test_reroll_lines_without_a_summary_are_not_reported_as_segments() -> None:
    """只有重抽行、没有摘要行（进程被杀）时也不该多出一行无速率的「段」。"""
    rows = _rows([REROLL_LINE])
    assert rows == {}


def test_boot_rerolls_are_attributed_to_the_boot_segment() -> None:
    """引导侧的 code.zip 重抽要落到 `boot:code.zip`（标的是引导那一份，不是 worker 那份）。"""
    line = (
        "[12:49:58] [battle] wire: re-roll #1/3 code.zip: 实测 13 KB/s < 阈值 80 KB/s"
        "（按此速率剩余 100s）——断开重发（已收 131072 bytes 作废）"
    )
    rows = _rows([BOOT_LINE, line])
    assert rows["boot:code.zip"]["rerolls"] == 1
    assert rows["boot:code.zip"]["wasted_mb"] == pytest.approx(131072 / MB, abs=0.01)


# ────────────────────────── ⑤ 端到端：文件 → 表 ──────────────────────────


# ────────────── ⑥ 阶段占比（P0.5 基线：T_in / T_out / T_ppo / other）──────────────

#: 现场形状（新 worker 会写的那一行）：段账 + 调度账 + 阶段账，全在同一条 `wire` 行里。
PHASE_LINE = (
    "[13:04:51] [worker] job 3c8cf83ce824aef8: wire payload=3.32MB/321.8s(11KB/s) "
    "result=0.89MB/3.7s(245KB/s) wait=12.0s/yield=2/p0_p95=41ms 合计=4.21MB/325.5s(13KB/s) "
    "ppo=613.2s phases in=321.8s out=3.7s ppo=613.2s other=61.3s wall=1000.0s"
)


def test_phase_line_is_parsed_into_shares() -> None:
    """`phases …` 行 → 总量占比（决策口径：T_in 高 = 链路是瓶颈，other 高 = 等活/装载）。"""
    row = wr.summarize_phases(wr.parse_phases([PHASE_LINE]))
    assert row["jobs"] == 1
    assert row["in_sec"] == pytest.approx(321.8)
    assert row["out_sec"] == pytest.approx(3.7)
    assert row["ppo_sec"] == pytest.approx(613.2)
    assert row["other_sec"] == pytest.approx(61.3)
    assert row["wall_sec"] == pytest.approx(1000.0)
    assert row["in_pct"] == pytest.approx(32.2, abs=0.1)
    assert row["ppo_pct"] == pytest.approx(61.3, abs=0.1)
    assert row["other_pct"] == pytest.approx(6.1, abs=0.1)


def test_phase_shares_use_percentiles_not_means() -> None:
    """多个 job 时给每 job 占比的 p50/p90（口径同段表：不报均值）。"""
    lines = [
        PHASE_LINE,
        PHASE_LINE.replace("other=61.3s wall=1000.0s", "other=800.0s wall=1000.0s"),
    ]
    row = wr.summarize_phases(wr.parse_phases(lines))
    assert row["jobs"] == 2
    # 最近秩：两个样本时 p50 取较小的那个（不插值、不外推）。
    assert row["other_pct_p50"] == pytest.approx(6.1, abs=0.1)
    # 而「总量占比」是 ratio of sums（101.3/2000）：两个口径故意都报——
    # 前者回答「典型一个 job 长什么样」，后者回答「这台机器的时间花在哪」。
    assert row["other_pct"] == pytest.approx(43.1, abs=0.1)


def test_lines_without_phases_are_reported_as_empty() -> None:
    """旧 worker 的日志（没有 `phases`）不得伪造成 0 占比——空就是空。"""
    assert wr.parse_phases([LIVE_SLOW]).jobs == 0
    empty = wr.render_phases(wr.summarize_phases(wr.PhaseSums()))
    assert "旧 worker" in empty and "T_in" not in empty  # 说清楚是「日志里没有」，不伪造成 0%


def test_writer_and_reader_agree_on_the_phase_format() -> None:
    """**写方与读方同一格式**：worker 的 `_wire_flush` 写出来的行，本工具必须能解析出来。

    这是本组用例里最要紧的一条：格式漂了不会报错，只会让基线表**静默变空**——
    而 P0.5 的意义就是「没有基线不许开 P2」，空表会被读成「没数据」而不是「坏了」。
    """
    import remote.worker as W

    W._WIRE.clear()
    W._BULK.reset()
    jid = "a" * 16
    W._wire_start(jid)
    W._wire_add(jid, "payload", 3 * MB, 300.0)  # T_in
    W._wire_add(jid, "result", MB, 4.0)  # T_out
    W._wire_time(jid, "ppo", 610.0)  # T_ppo
    lines: list[str] = []
    W._wire_flush(jid, lines.append)
    W._WIRE.clear()
    assert len(lines) == 1
    row = wr.summarize_phases(wr.parse_phases(lines))
    assert row["jobs"] == 1, f"读不出阶段行（格式漂了）：{lines[0]}"
    assert row["in_sec"] == pytest.approx(300.0)
    assert row["out_sec"] == pytest.approx(4.0)
    assert row["ppo_sec"] == pytest.approx(610.0)
    # `wall` ≥ 各阶段之和（写方保证）：本用例里实测墙钟几乎为 0，所以它就取 914.0，
    # `other` 被夹到 0——否则分母小于各部分之和会把占比算成荒谬值。
    assert row["wall_sec"] == pytest.approx(914.0, abs=0.5)
    assert row["other_sec"] == 0.0
    assert row["in_pct"] == pytest.approx(32.8, abs=0.2)


def test_main_reads_files_and_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI：给目录取全部 `*.log`；给文件取该文件；表里不出现均值（口径写死在验收里）。"""
    d = tmp_path / "logs"
    d.mkdir()
    (d / "a.log").write_text("\n".join([LIVE_SLOW, REROLL_LINE]) + "\n", encoding="utf-8")
    (d / "b.log").write_text(BOOT_LINE + "\n", encoding="utf-8")

    assert wr.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "job:payload" in out and "boot:code.zip" in out
    assert "2 个文件" in out

    assert wr.main([str(d / "b.log"), "--json"]) == 0
    js = capsys.readouterr().out
    assert '"seg": "boot:code.zip"' in js

    # 阶段占比也上表（`--json` 里同一层）：`phases` 块必须在
    (d / "c.log").write_text(PHASE_LINE + "\n", encoding="utf-8")
    assert wr.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "阶段占比" in out and "T_in" in out
    assert "均值" not in out.split("（bad%")[0]  # 表体里不出现均值口径
    assert wr.main([str(d / "c.log"), "--json"]) == 0
    assert '"phases"' in capsys.readouterr().out


def test_main_without_readable_files_is_loud(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert wr.main([str(tmp_path / "nope.log")]) == 2
    assert "没有可读的日志文件" in capsys.readouterr().err
