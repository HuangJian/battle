"""`_trim_shards_to_quota` —— 打包前按 per_stage_quota 裁掉超采（2026-10-26）。

为什么需要这条：超采是采集侧的必要保险（局长方差极大：it15 实测 mean 331 / median 247 /
p90 625 / max 2832），但**不能把 1.86× 的字节打包传到 PPO 再丢**（现场 it15：采 689 局 /
227,962 samples，配额 122,885 ⇒ 46% 的 dist 白打包白传输）。截断点原本只在
`worker/ppo/np_core.py:602`（云机装载时）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_remote_job import _trim_shards_to_quota


def _shard(root: Path, stage: int, seed: int, n: int) -> Path:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"stage": stage, "seed": seed, "nSamples": n}), encoding="utf-8"
    )
    return d


def test_trims_by_samples_not_by_game_count(tmp_path: Path) -> None:
    """按 **samples** 累加（关间局均差 2–3× ⇒ 按局数裁会让长局关欠采、短局关超采）。"""
    root = tmp_path / "dist"
    # s2000：5 局 × 200 = 1000；s2001：5 局 × 500 = 2500 ⇒ quota=1000 时
    #   按局数会各留 2 局（400 / 1000）；按 samples 应该是 s2000 留 5 局、s2001 留 2 局。
    for i in range(5):
        _shard(root, 2000, i, 200)
        _shard(root, 2001, i, 500)
    dirs = sorted(root.iterdir())
    kept, trimmed, samples = _trim_shards_to_quota(dirs, 1000)
    by_stage = {2000: 0, 2001: 0}
    for d in kept:
        by_stage[int(d.name.split("_")[1][1:])] += 1
    assert by_stage[2000] == 5, f"s2000 每局 200，quota 1000 ⇒ 全留；实得 {by_stage[2000]}"
    assert by_stage[2001] == 2, f"s2001 每局 500，quota 1000 ⇒ 留 2 局；实得 {by_stage[2001]}"
    assert trimmed == 3
    assert samples == 1000 + 1000


def test_keeps_whole_games_never_slices_rows(tmp_path: Path) -> None:
    """整局取舍：`(stage, seed)` 是 data_fp 的身份成分，切行会让「声明集 == 实产集」失效。"""
    root = tmp_path / "dist"
    a = _shard(root, 2000, 0, 900)  # 单局 900 > quota 500
    kept, trimmed, samples = _trim_shards_to_quota([a, _shard(root, 2000, 1, 900)], 500)
    assert kept == [a], "第一局整局保留（900 > 500 也照留，不切行）"
    assert samples == 900 and trimmed == 1


def test_quota_zero_is_caller_gated_but_never_crashes(tmp_path: Path) -> None:
    """`quota=0` 由调用方门住（`if quota > 0`）；这里只保证不崩、不做怪事。"""
    root = tmp_path / "dist"
    dirs = [_shard(root, 2000, i, 100) for i in range(3)]
    kept, trimmed, samples = _trim_shards_to_quota(dirs, 0)
    assert kept == [] and trimmed == 3 and samples == 0


def test_bad_manifest_is_skipped_not_fatal(tmp_path: Path) -> None:
    """坏 manifest / 缺 nSamples 的 shard 跳过（少一局不该让整轮发布失败）。"""
    root = tmp_path / "dist"
    good = _shard(root, 2000, 0, 100)
    bad = root / "rl_s2000_seed1"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    nodata = root / "rl_s2000_seed2"
    nodata.mkdir(parents=True)
    (nodata / "manifest.json").write_text(json.dumps({"stage": 2000}), encoding="utf-8")
    kept, trimmed, samples = _trim_shards_to_quota([good, bad, nodata], 10_000)
    assert kept == [good] and trimmed == 0 and samples == 100
