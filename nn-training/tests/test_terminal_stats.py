"""terminal_stats 单元测试。"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from rl.terminal_stats import terminal_stats

REPO = Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    if not cond:
        FAILS.append(label)
        print(f"  FAIL: {label}")
    else:
        print(f"  OK: {label}")


def _write_manifest(
    base: Path,
    subdir: str,
    stage: int,
    seed: int,
    kills: int,
    powerups: int,
    ticks: int,
    nsamples: int,
    outcome: str = "lives_exhausted",
) -> Path:
    d = base / subdir / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    m = d / "manifest.json"
    m.write_text(
        json.dumps(
            {
                "stage": stage,
                "seed": seed,
                "kills": kills,
                "powerUpsCollected": powerups,
                "ticks": ticks,
                "nSamples": nsamples,
                "outcome": outcome,
                "metrics_version": 2,
            }
        ),
        encoding="utf-8",
    )
    return m


def test_terminal_stats_basic() -> None:
    """基础聚合：多嵌套层级、(stage,seed) 去重、local-eval 跳过。"""
    tmp = REPO / "tmp" / "pytest-tmp" / f"test_terminal_{secrets.token_hex(4)}"
    it_dir = tmp / "it1"
    # w0/ 层级
    _write_manifest(it_dir, "w0", 2000, 1, kills=3, powerups=1, ticks=4500, nsamples=450)
    _write_manifest(it_dir, "w0", 2000, 2, kills=0, powerups=0, ticks=500, nsamples=50)
    # dist/mac/ 层级
    _write_manifest(it_dir, "dist/mac", 2001, 1, kills=5, powerups=2, ticks=8000, nsamples=800)
    # local-eval/ 应被跳过
    _write_manifest(it_dir, "local-eval", 2002, 1, kills=9, powerups=3, ticks=9000, nsamples=900)
    # 同一 (stage,seed) 副本，取 nSamples 大者
    _write_manifest(it_dir, "dist/self", 2000, 1, kills=1, powerups=0, ticks=200, nsamples=20)
    # 顶层 queue 布局
    _write_manifest(it_dir, ".", 2002, 1, kills=2, powerups=1, ticks=3000, nsamples=300)

    rec = terminal_stats(str(it_dir), it=1)
    check(rec["games"] == 4, "4 games after dedup (skipped local-eval, dedup 2000/1)")
    check(rec["kills"]["total"] == 10.0, "kills total = 3+0+5+2 = 10")
    check(rec["kills"]["mean"] == 2.5, "kills mean = 10/4 = 2.5")
    check(rec["powerUps"]["total"] == 4.0, "powerups total = 1+0+2+1 = 4")
    check(rec["ticks"]["mean"] == 4000.0, "ticks mean = (4500+500+8000+3000)/4 = 4000")
    check(rec["outcomes"]["lives_exhausted"] == 4, "all 4 lives_exhausted")
    check(rec["win_rate"] == 0.0, "win_rate = 0")
    check(rec["by_stage"]["2000"]["games"] == 2, "stage 2000 has 2 games")
    check(rec["by_stage"]["2000"]["kills_total"] == 4.0, "stage 2000 kills = 3+1 (took max) = 4")
    check(rec["by_stage"]["2001"]["games"] == 1, "stage 2001 has 1 game")
    check(rec["by_stage"]["2001"]["kills_total"] == 5.0, "stage 2001 kills = 5")


def test_terminal_stats_no_manifests() -> None:
    """空目录返回 games=0 不崩溃。"""
    tmp = REPO / "tmp" / "pytest-tmp" / f"test_terminal_empty_{secrets.token_hex(4)}"
    it_dir = tmp / "it_empty"
    it_dir.mkdir(parents=True, exist_ok=True)
    rec = terminal_stats(str(it_dir), it=5)
    check(rec["games"] == 0, "empty dir: games=0")
    check("error" in rec, "empty dir: has error key")


def test_terminal_stats_missing_dir() -> None:
    """不存在的目录返回 games=0 不崩溃。"""
    rec = terminal_stats("/nonexistent/path", it=3)
    check(rec["games"] == 0, "missing dir: games=0")


def test_terminal_stats_win_rate() -> None:
    """混合 outcome 验证 win_rate。"""
    tmp = REPO / "tmp" / "pytest-tmp" / f"test_terminal_win_{secrets.token_hex(4)}"
    it_dir = tmp / "it2"
    _write_manifest(it_dir, "w0", 2000, 1, kills=5, powerups=2, ticks=8000, nsamples=800, outcome="stage_clear")
    _write_manifest(it_dir, "w0", 2000, 2, kills=0, powerups=0, ticks=400, nsamples=40)
    _write_manifest(it_dir, "w0", 2000, 3, kills=3, powerups=1, ticks=6000, nsamples=600, outcome="stage_clear")
    _write_manifest(it_dir, "w0", 2000, 4, kills=1, powerups=0, ticks=1500, nsamples=150)

    rec = terminal_stats(str(it_dir), it=2)
    check(rec["games"] == 4, "4 games")
    check(rec["win_rate"] == 0.5, "win_rate = 2/4 = 0.5")
    check(rec["outcomes"]["stage_clear"] == 2, "2 stage_clear")
    check(rec["outcomes"]["lives_exhausted"] == 2, "2 lives_exhausted")
    check(rec["kills"]["total"] == 9.0, "kills total = 5+0+3+1 = 9")
    check(rec["kills"]["max"] == 5.0, "kills max = 5")


def test_terminal_stats_dedup_nsamples() -> None:
    """同一 (stage,seed) 取 nSamples 最大者。"""
    tmp = REPO / "tmp" / "pytest-tmp" / f"test_terminal_dedup_{secrets.token_hex(4)}"
    it_dir = tmp / "it3"
    # 先写小 nSamples
    _write_manifest(it_dir, "dist/a", 2000, 1, kills=1, powerups=0, ticks=1000, nsamples=100)
    # 后写大 nSamples
    _write_manifest(it_dir, "dist/b", 2000, 1, kills=5, powerups=2, ticks=8000, nsamples=800)

    rec = terminal_stats(str(it_dir), it=3)
    check(rec["games"] == 1, "1 game after dedup")
    check(rec["kills"]["total"] == 5.0, "took max nSamples version: kills=5")
    check(rec["ticks"]["mean"] == 8000.0, "took max nSamples version: ticks=8000")


def test_terminal_stats_ignores_metrics_npy() -> None:
    """函数不读 metrics.npy，放一个伪造的不报错。"""
    tmp = REPO / "tmp" / "pytest-tmp" / f"test_terminal_npy_{secrets.token_hex(4)}"
    it_dir = tmp / "it4"
    _write_manifest(it_dir, "w0", 2000, 1, kills=2, powerups=0, ticks=3000, nsamples=300)
    # 伪造 metrics.npy
    (it_dir / "w0" / "rl_s2000_seed1").joinpath("metrics.npy").write_text("garbage", encoding="utf-8")
    rec = terminal_stats(str(it_dir), it=4)
    check(rec["games"] == 1, "metrics.npy ignored: 1 game")
    check(rec["kills"]["total"] == 2.0, "metrics.npy ignored: kills=2")


def main() -> None:
    import sys

    test_terminal_stats_basic()
    test_terminal_stats_no_manifests()
    test_terminal_stats_missing_dir()
    test_terminal_stats_win_rate()
    test_terminal_stats_dedup_nsamples()
    test_terminal_stats_ignores_metrics_npy()

    print()
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    else:
        print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()