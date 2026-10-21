"""tests/worker/test_rollout_est.py —— 语料供给侧 A0 的口径钉子（plan/rollout-corpus-supply §3.1）。

钉住四件事（每条都对应一次误判或一次事故形状）：

  1. **只取最近一轮**：`agg="latest"` 不得把更早的轮池进来。路线（2026-10-21 it62/it63）：
     `est {476,523,693,657}` → `{652,674,664,548}` 是**逐轮跳变**的，3 轮 pooled 均值会把
     变短的局判高（652 vs 实际 530，+23%），进而少派局、多补一批；
  2. **优先级是 learned → trailing**：本轮已结算的 `collected/games` 必须先说话——它是唯一
     不依赖历史窗口的量；`or` 写反（trailing 先）就是 v2 现场那条「自校准被短路」；
  3. **派局 est 压低且定点化**：`× 0.92` 走整数运算（`20 × 1.15` 的浮点教训见 volume_alloc
     文件头），且永不超过原值；
  4. **0 = 无估计**，不许被抬成某个假 est（冷启动走「不猜」档）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.resume import trailing_stage_samples_per_game
from worker.volume_alloc import pick_stage_est, pull_est_low


def _shard(it_dir: Path, stage: int, seed: int, samples: int) -> None:
    """造一个最小可扫描的 shard（`rl_s*_seed*/manifest.json`；门槛是目录名 + manifest）。"""
    d = it_dir / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"stage": int(stage), "nSamples": int(samples)}), encoding="utf-8"
    )


def _round(root: Path, it: int, samples: list[int], *, mtime: float) -> Path:
    it_dir = root / f"it{it}"
    it_dir.mkdir(parents=True, exist_ok=True)
    for seed, n in enumerate(samples, start=1):
        _shard(it_dir, 0, seed, n)
    # `trailing_stage_samples_per_game` 按目录 mtime 选窗口 ⇒ 必须显式定序
    # （同一测试进程里连续 mkdir 的 mtime 可能同毫秒）。
    os.utime(it_dir, (mtime, mtime))
    return it_dir


def test_latest_takes_only_the_newest_round(tmp_path: Path) -> None:
    """3 轮 900/600/500 ⇒ pooled=667，latest=500。"""
    root = tmp_path / "traj"
    for i, n in enumerate((900, 600, 500), start=1):
        _round(root, i, [n], mtime=1_700_000_000 + i)

    pooled = trailing_stage_samples_per_game(root, [0], window_iters=3, fallback=1)
    latest = trailing_stage_samples_per_game(root, [0], window_iters=3, fallback=1, agg="latest")

    assert pooled[0] == 667  # (900+600+500)/3
    assert latest[0] == 500, "latest 必须只读最近一轮；池进来就是把变短的局判高"


def test_latest_falls_back_when_newest_round_has_no_shards(tmp_path: Path) -> None:
    """最近一轮还没落 shard（刚开轮）⇒ 回退 fallback，**不**回读更早的轮。

    回读更早的轮会把上一次的局长当成这一次的——正是本轮要消灭的高估源。
    """
    root = tmp_path / "traj"
    _round(root, 1, [900], mtime=1_700_000_001)
    (root / "it2").mkdir()
    os.utime(root / "it2", (1_700_000_002, 1_700_000_002))

    out = trailing_stage_samples_per_game(root, [0], window_iters=3, fallback=42, agg="latest")
    assert out[0] == 42


def test_latest_rejects_unknown_agg(tmp_path: Path) -> None:
    """口径是枚举，写错必须响亮失败（静默退回 pooled = 下一次误读）。"""
    root = tmp_path / "traj"
    _round(root, 1, [500], mtime=1_700_000_001)
    try:
        trailing_stage_samples_per_game(root, [0], agg="median")
    except ValueError:
        return
    raise AssertionError("未知 agg 必须 ValueError")


def test_pick_stage_est_prefers_learned() -> None:
    """本轮已结算优先于历史窗口；两者都没有 ⇒ 0（无估计）。"""
    assert pick_stage_est(530, 652) == (530, "learned")
    assert pick_stage_est(0, 652) == (652, "trailing")
    assert pick_stage_est(0, 0) == (0, "none")


def test_pull_est_low_is_bounded_and_deterministic() -> None:
    """0.92 的定点化：不超过原值、不靠二进制浮点、0 仍是 0。"""
    assert pull_est_low(530) == 487  # int(530 × 0.92) = 487.6 → 487
    assert pull_est_low(20) == 18
    assert pull_est_low(1) == 1  # 不许掉到 0（0 = 无估计，会改走 fallback）
    assert pull_est_low(0) == 0
    assert pull_est_low(530) < 530
