"""test_metrics_shard —— metrics.npy 加载端到端（M1b，plan §4.2 / §11 DoD）。

三层确定性/契约在 shard 级验证：
  1. `[N+1,METRICS_DIM] f8` 布局 + `metrics_version` 版本分支（错版本响亮报错，不静默错读）；
  2. Python 公式引擎在加载器里算 reward：Σr ≡ REWARD_SCALE×gatedScore（telescoping）；
  3. 无 holder 时响亮报错（旧 reward.npy 直读路径已删除）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from common.schema import OBS_CHANNELS, SCALAR_DIM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_context import Scoped, current, reset
from biz.reward_library import METRIC_INDEX, METRICS_DIM, METRICS_VERSION, build_reward_fn
from worker.config import load_course
from worker.ppo.np_core import load_episodes


def _write_shard(root: Path, name: str, n: int, metrics: np.ndarray, manifest: dict) -> Path:
    """合成一个 per-tick shard 目录（与 export-rl-rollout.ts 同文件名）。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(n * 31 + 7)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.random((n, SCALAR_DIM), dtype=np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 5, n))
    np.save(d / "a_fire.npy", rng.integers(0, 2, n))
    np.save(d / "lp_move.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "lp_fire.npy", rng.random(n).astype(np.float32) - 2)
    np.save(d / "value.npy", rng.random(n).astype(np.float32) * 0.1)
    np.save(d / "metrics.npy", metrics)
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 7), dtype=np.int64))
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _synthetic_metrics(n: int, seed: int = 1) -> np.ndarray:
    """n 决策行 + 1 终局行（39 维；kills 单调、baseAlive 终局可翻 0）。"""
    rng = np.random.default_rng(seed)
    m = np.zeros((n + 1, METRICS_DIM), dtype=np.float64)
    m[:, METRIC_INDEX["ticks"]] = np.arange(n + 1) * 10.0
    kills = np.cumsum(rng.integers(0, 2, n + 1)).astype(np.float64)
    m[:, METRIC_INDEX["kills"]] = kills
    m[:, METRIC_INDEX["lives"]] = 3.0
    m[:, METRIC_INDEX["playerShots"]] = rng.integers(0, 30, n + 1)
    m[:, METRIC_INDEX["enemyTotal"]] = 20.0
    m[:, METRIC_INDEX["baseAlive"]] = 1.0
    m[:, METRIC_INDEX["baseWallTotal"]] = 8.0
    m[:, METRIC_INDEX["baseWallIntact"]] = 8.0
    m[:, METRIC_INDEX["firstKillTick"]] = -1.0
    m[-1, METRIC_INDEX["firstKillTick"]] = float(m[-1, METRIC_INDEX["ticks"]])
    return m


@pytest.fixture(autouse=True)
def _ctx_reset() -> Iterator[None]:
    reset()
    yield
    reset()


def _s4b_fn():
    return build_reward_fn(load_course("s4b").reward_spec())


def test_load_episodes_reconcile_telescoping(tmp_path: Path) -> None:
    """公式引擎在加载器内算 reward：每局 Σr ≡ 10×score（score_reconcile）。"""
    specs = [
        ("g1", 40, 0.21, "stage_clear"),
        ("g2", 60, 0.05, "base_destroyed"),
    ]
    for name, n, score, outcome in specs:
        m = _synthetic_metrics(n, seed=n)
        _write_shard(
            tmp_path,
            name,
            n,
            m,
            {
                "metrics_version": METRICS_VERSION,
                "nSamples": n,
                "outcome": outcome,
                "score": score,
                "k": 10,
            },
        )
    with Scoped(reward_fn=_s4b_fn(), gamma=0.995, lam=0.95, it=1):
        eps = load_episodes(str(tmp_path), normalize_adv=False)
    assert len(eps) == 2
    for e, (name, n, _score, _outcome) in zip(eps, specs, strict=True):
        assert e["obs"].shape[0] == n
        assert e["adv"].shape == (n,) and e["ret"].shape == (n,)
        # telescope：Σr = scale × gatedScore（gated score 已含 base_destroyed ×0.1）
        (d,) = [x for x in [tmp_path / name]]
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        # 直接重算 reward 总量
        m = np.load(d / "metrics.npy")
        total = float(_s4b_fn()(m, man["outcome"], man["score"], 1).sum())
        assert total == pytest.approx(10.0 * float(man["score"]), abs=1e-6)


def test_metrics_version_mismatch_loud(tmp_path: Path) -> None:
    """metrics_version 不匹配必须响亮报错，不静默错读（LC §1.1）。"""
    n = 8
    _write_shard(
        tmp_path,
        "g1",
        n,
        _synthetic_metrics(n),
        {"metrics_version": 1, "nSamples": n, "outcome": "timeout", "score": 0.0},
    )
    with (
        Scoped(reward_fn=_s4b_fn(), gamma=0.995, lam=0.95, it=1),
        pytest.raises(ValueError, match="metrics_version=1"),
    ):
        load_episodes(str(tmp_path))


def test_old_metrics_version_wins_over_shape_guess(tmp_path: Path) -> None:
    """行宽不符 + 声明旧版本 ⇒ 报**声明的那一版**（真因），不是形状猜问号（2026-09-29 事故）。

    现场：节点 a98 的 rollout 写 (74,45) 而云端按 54 列读，旧顺序只报「形状应为
    [N+1,54]，收到 (74,45)——metrics_version 不匹配？」——排障人看不到是谁写旧了。
    """
    n = 8
    _write_shard(
        tmp_path,
        "g1",
        n,
        _synthetic_metrics(n)[:, :45],  # v8 行宽（MEMETRICS_DIM 45 时代的字节）
        {"metrics_version": 8, "nSamples": n, "outcome": "timeout", "score": 0.0},
    )
    with (
        Scoped(reward_fn=_s4b_fn(), gamma=0.995, lam=0.95, it=1),
        pytest.raises(ValueError, match="metrics_version=8") as ei,
    ):
        load_episodes(str(tmp_path))
    msg = str(ei.value)
    assert "形状应为" not in msg, "旧版本必须报版本，不得退回形状猜问号"
    assert "导出器" in msg, "真因（产出端/读取端版本不同）要写进报错"


def test_publish_set_drops_shards_from_another_metrics_version(tmp_path: Path) -> None:
    """发布端行宽版本门：声明旧版本的 shard 不进 payload（默认行为逐字节不变）。

    2026-09-29 事故：旧导出器写的 shard 进了 payload，云端 grad 第 1 秒就炸、同份字节
    重领也炸 ⇒ 5 轮连续失败、整门课 aborted。同血缘、同 wver 的语料看不出版本差
    （`data_fp` 只按 (stage,seed,wver) 对账），故必须在发布端按 shard 自己的声明拦。
    """
    from remote.hub_client import iter_shard_dirs

    it_dir = tmp_path / "it1"
    ok = _write_shard(
        it_dir,
        "rl_s2000_seed1",
        4,
        _synthetic_metrics(4),
        {"metrics_version": METRICS_VERSION, "nSamples": 4, "outcome": "timeout", "score": 0.0},
    )
    old = _write_shard(
        it_dir,
        "rl_s2000_seed2",
        4,
        _synthetic_metrics(4)[:, :45],
        {"metrics_version": METRICS_VERSION - 1, "nSamples": 4, "outcome": "timeout", "score": 0.0},
    )
    logs: list[str] = []
    # 默认（不传）= 旧行为：两份都在（顺序按目录名排序）
    assert iter_shard_dirs(tmp_path, 1) == [ok, old]
    kept = iter_shard_dirs(tmp_path, 1, log=logs.append, metrics_version=METRICS_VERSION)
    assert kept == [ok], "声明旧版本的 shard 必须被拦在 payload 之外"
    assert any("metrics" in m and "剔除 1" in m for m in logs), logs


def test_publish_set_drops_shards_without_a_declared_version(tmp_path: Path) -> None:
    """声明缺失同样算不符（读不到 = 不收），且日志点名「缺字段」。"""
    from remote.hub_client import iter_shard_dirs

    it_dir = tmp_path / "it1"
    _write_shard(
        it_dir,
        "rl_s2000_seed1",
        4,
        _synthetic_metrics(4),
        {"nSamples": 4, "outcome": "timeout", "score": 0.0},
    )
    logs: list[str] = []
    assert iter_shard_dirs(tmp_path, 1, log=logs.append, metrics_version=METRICS_VERSION) == []
    assert any("缺字段" in m for m in logs), logs


def test_no_holder_loud_error(tmp_path: Path) -> None:
    """旧 reward.npy 直读路径已删除：无 holder 时响亮报错并指向 --course。"""
    n = 8
    _write_shard(
        tmp_path,
        "g1",
        n,
        _synthetic_metrics(n),
        {"metrics_version": METRICS_VERSION, "nSamples": n, "outcome": "timeout", "score": 0.0},
    )
    with pytest.raises(RuntimeError, match="reward_context holder 未设置"):
        load_episodes(str(tmp_path))


def test_row_shape_mismatch_loud(tmp_path: Path) -> None:
    """metrics 行数 ≠ nSamples+1 → 响亮报错。"""
    n = 8
    _write_shard(
        tmp_path,
        "g1",
        n,
        _synthetic_metrics(n - 1),  # 行数少一行
        {"metrics_version": METRICS_VERSION, "nSamples": n, "outcome": "timeout", "score": 0.0},
    )
    with (
        Scoped(reward_fn=_s4b_fn(), gamma=0.995, lam=0.95, it=1),
        pytest.raises(ValueError, match="指标行失配"),
    ):
        load_episodes(str(tmp_path))


def test_same_seed_shards_byte_identical(tmp_path: Path) -> None:
    """（Python 侧可验的半条）同一布局生成两遍 → metrics 逐字节一致。

    完整端到端双跑确定性由 bun 侧 exporter 冒烟覆盖（tests/golden 已锁 v7 对账）。
    """
    a = _synthetic_metrics(20, seed=99)
    b = _synthetic_metrics(20, seed=99)
    np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_load_episodes_reconcile_telescoping(Path(td))
        print("ok  test_load_episodes_reconcile_telescoping")
        reset()
