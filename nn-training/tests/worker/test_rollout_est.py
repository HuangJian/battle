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
from worker.volume_alloc import (
    pick_stage_est,
    pool_games_by_stage,
    pull_est_low,
    quota_met,
    widen_by_width,
)


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
    # 窗口排序**按轮号**（2026-10-25 起；mtime 只做同号兜底）——这里显式给 mtime 是为了
    # 让「反序」那条用例有得可翻，正常用例不依赖它。
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


def test_latest_orders_by_round_number_not_mtime(tmp_path: Path) -> None:
    """★ 2026-10-25（修 flake）：轮号大 = 更近；mtime 只做同号兜底。

    旧实现按目录 mtime 排序 —— 而 `itN` 之间只差几次写盘，同一时间刻度内顺序会翻
    （Windows 粗时间戳 / 机器有负载）：`it1(900)` 反压 `it2(530)` ⇒ 「最近一轮」取错，
    分关 est 被单关值盖掉。现场就是随机拿到 `{0: 900}` 或 `{0: 530}`。
    """
    root = tmp_path / "traj"
    _round(root, 1, [900], mtime=1_700_000_900)  # 故意让 it1 的 mtime 更「新」
    _round(root, 2, [530], mtime=1_700_000_100)
    latest = trailing_stage_samples_per_game(root, [0], window_iters=3, fallback=1, agg="latest")
    assert latest[0] == 530, f"按轮号该取 it2（530），实际 {latest}"
    # pooled 的窗口也按轮号切：近 3 轮 = it2、it1（顺序无关求和，但窗口成员要确定）
    pooled = trailing_stage_samples_per_game(root, [0], window_iters=1, fallback=1)
    assert pooled[0] == 530, f"窗口只留最近一轮 ⇒ it2，实际 {pooled}"


def test_latest_skips_the_in_progress_round(tmp_path: Path) -> None:
    """★ 2026-10-21 评审 P0：**正在跑的那一轮**不是历史，必须跳过。

    现场形状：`step_prepare_iter` 在每轮开跑前就 `mkdir(traj_root/it{N})`
    （`trainer/loop_iter_dir._prepare_iter_dir`），所以「mtime 最新的目录」在**每轮首批**
    恒等于这个空目录。旧实现 (`it_dirs[:1]`) 因此每次都读到 0 个 shard ⇒ 整份回退
    `fallback`（全关同一个值）⇒ `rollout-stage-balance` 的分关估计被静默抹平。

    两条口径各钉一次：
      * **不传 `exclude_it`**（老调用方/无 `it` 的调用点）⇒ 跳过空目录、用上一轮的分关值，
        **不是**回退 fallback；
      * **传 `exclude_it`**（trainer / 导出侧知道 `it`）⇒ 同一结果，且窗口不被空目录占位。
    """
    root = tmp_path / "traj"
    _round(root, 1, [900, 300], mtime=1_700_000_001)  # 上一轮：两关不同局长
    (root / "it2").mkdir()  # 本轮：已建、空
    os.utime(root / "it2", (1_700_000_002, 1_700_000_002))

    # `_round` 只写 stage 0（两个 shard）；这里补一个 stage 1 的，才能验「分关」不被抹平
    _shard(root / "it1", 1, 99, 120)
    latest = trailing_stage_samples_per_game(
        root, [0, 1], window_iters=3, fallback=42, agg="latest", exclude_it=2
    )
    assert latest == {0: 600, 1: 120}, latest  # (900+300)/2=600；不是 42
    # 不传 exclude_it 也必须跳过空目录（否则老调用点又回到「全关一个值」）
    assert trailing_stage_samples_per_game(
        root, [0, 1], window_iters=3, fallback=42, agg="latest"
    ) == {0: 600, 1: 120}


def test_latest_falls_back_only_when_no_round_has_shards(tmp_path: Path) -> None:
    """**没有任何一轮有 shard** 才回退 fallback（首轮冷启动）。

    ⚠️ 旧用例钉的是「最新目录为空 ⇒ 回退 fallback」，那正是 P0 的成因：
    空目录 = 正在跑的那一轮，不是「最近一轮」。
    """
    root = tmp_path / "traj"
    (root / "it1").mkdir(parents=True)
    os.utime(root / "it1", (1_700_000_001, 1_700_000_001))

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


def test_pool_games_by_stage_overprovisions_and_respects_caps() -> None:
    """★ 候选池（2026-10-21 用户口径）：池 = 缺口 × mult，且**夹在硬顶内**。

    它回答的不是「要采多少局」，而是「队列里先放多少候选」——多出来的那部分只在配额
    没达成时才可能被派出去，满额即熔断 ⇒ 从不运行。
    """
    # quota=50、est=20、mult=2 ⇒ ceil(50×2/20)=5 局/关
    pool = pool_games_by_stage(
        stages=[0, 1],
        collected={0: 0, 1: 10},
        games_done={0: 0, 1: 1},
        target_transitions=100,
        ests={0: 20, 1: 20},
        game_caps={},
        mult=2.0,
    )
    assert pool == {0: 5, 1: 4}, pool  # 关 1 已收 10 ⇒ ceil(40×2/20)=4

    # 硬顶是硬顶：cap 余量只有 2 ⇒ 只给 2（不是 5）
    capped = pool_games_by_stage(
        stages=[0],
        collected={},
        games_done={0: 3},
        target_transitions=100,
        ests={0: 20},
        game_caps={0: 5},
        mult=2.0,
    )
    assert capped == {0: 2}, capped

    # 已达标的关不进池；无估计 ⇒ 1 局（不猜）
    assert pool_games_by_stage(
        stages=[0, 1],
        collected={0: 50, 1: 0},
        games_done={},
        target_transitions=100,
        ests={0: 20, 1: 0},
        game_caps={},
    ) == {1: 1}


def test_pool_games_by_stage_fills_up_to_width() -> None:
    """`width` 已知时再兜一次：池比机器窄 ⇒ 按轮转补足（显式铺满所有槽）。"""
    pool = pool_games_by_stage(
        stages=[0, 1],
        collected={},
        games_done={},
        target_transitions=100,
        ests={0: 50, 1: 50},  # 池只有 2 局
        game_caps={},
        width=8,
        mult=1.0,
    )
    assert sum(pool.values()) == 8, pool
    assert pool == {0: 4, 1: 4}, pool


def test_quota_met_is_per_stage() -> None:
    """逐关判据：全局达标不算——短局关不能靠长局关顶。"""
    assert quota_met(stages=[0, 1], collected={0: 50, 1: 50}, target_transitions=100)
    assert not quota_met(stages=[0, 1], collected={0: 90, 1: 10}, target_transitions=100)


def test_widen_by_width_refills_to_the_machine_width() -> None:
    """★ 2026-10-22 用户口径「有几个 worker 铺多少局」：把逐关局数重铺到 ≥ 本机宽度。

    自主机自己 rollout ⇒ 宽度 = `cpu_worker_slots()`（不 ping、不看 hub 的 plan 声明）。
    plan 里那份是**打包时**的缺口形状，本机核数比它多就加宽；派发侧逐关满额即停，
    多加的候选不会白跑。
    """
    per = {0: 6, 1: 6, 2: 6, 3: 6}  # 打包时 24 局
    assert widen_by_width(per, 32) == {0: 8, 1: 8, 2: 8, 3: 8}  # 24 → 32（轮转补 8）
    assert widen_by_width(per, 24) == per, "已经够宽 ⇒ 原样返回（不折腾）"
    assert widen_by_width(per, 8) == per, "比它窄 ⇒ 也不动（宽度只做下界）"
    assert widen_by_width(per, 0) == per, "宽度未知（0）⇒ 原样"
    assert widen_by_width({}, 32) == {}
    # 不均匀的也按序轮转补，不重新分配已有份额（5+2=7 → 10：补 3 ⇒ stage0 得 2、stage1 得 1）
    assert widen_by_width({0: 5, 1: 2}, 10) == {0: 7, 1: 3}
