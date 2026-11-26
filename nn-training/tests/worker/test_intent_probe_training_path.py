"""`intent_probe` 训练路径的非配额分支（数据装载 → 采样 → 训练 → 评估）。

配额两臂已由 `test_intent_probe_sampling.py` 钉住；这里审计其余**从未跑过**的分支，
重点是与 `gen_self_inj.py` 的契约：self-feed 注入文件是 **N×9**（prev one-hot(8)+dur），
缺失 shard 的哨兵必须同宽（全 -1），否则
① 混合数据根（部分 shard 有 self_inj、部分没有）在 `np.concatenate` 处 ValueError；
② 全部缺失时 `_load_split` 产出 1-D 数组，`--ss-eps>0` 的 `xinj[:, 0]` 直接 IndexError。

另外钉住 `_load_split` 的两个容量分支：`--max-train` 比第一个 shard 还小时不得返回空块；
只有一个 shard（train 划分必空）时应响亮退出而不是让 numpy 抛「need at least one array」。

固定 seed，无墙钟。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import worker.train.intent_probe as probe
from common.schema import BOARD, OBS_CHANNELS, SCALAR_DIM
from worker.models.arch_meta import NetSpec
from worker.train.intent_probe import (
    IntentProbeNet,
    _fit,
    _inject_features,
    _load_split,
    _predict_val,
    _sample_train,
    _Split,
    _TrainSet,
    build_injection,
)


def _write_shard(root: Path, name: str, n: int, *, self_inj: bool) -> Path:
    d = root / name
    d.mkdir(parents=True)
    rng = np.random.RandomState(abs(hash(name)) % 1000)
    np.save(d / "obs.npy", rng.rand(n, 1, 1, 1).astype(np.float32))
    np.save(d / "scalars.npy", rng.rand(n, 2).astype(np.float32))
    np.save(d / "intent.npy", (np.arange(n) % 8).astype(np.int64))
    np.save(d / "bucket.npy", (np.arange(n) % 3).astype(np.int64))
    if self_inj:
        np.save(d / "self_inj.npy", np.full((n, 9), 0.5, dtype=np.float32))
    return d


def test_build_injection_prev_and_duration() -> None:
    """首帧 prev=0、duration 在同类连续段内累加、换类归 1。"""
    onehot, dur = build_injection(np.array([3, 3, 5, 5, 5, 0], dtype=np.int64))
    assert onehot.shape == (6, 8)
    assert onehot.argmax(1).tolist() == [0, 3, 3, 5, 5, 5]
    assert onehot[0].sum() == 1.0 and onehot[0, 0] == 1.0
    assert dur.tolist() == [1.0, 2.0, 1.0, 2.0, 3.0, 1.0]


def test_load_split_pads_missing_self_inj_nine_wide(tmp_path: Path) -> None:
    """部分 shard 有 self_inj、部分没有 → 拼接得动，缺失行是 9 宽 -1 哨兵。"""
    d1 = _write_shard(tmp_path, "a", 4, self_inj=True)
    d2 = _write_shard(tmp_path, "b", 3, self_inj=False)
    split = _load_split([str(d1), str(d2)], [0, 1], [0, 0], max_train=0)

    assert split.self_inj.shape == (7, 9)
    assert (split.self_inj[:4] == 0.5).all()
    assert (split.self_inj[4:] == -1.0).all()


def test_scheduled_sampling_only_replaces_available_rows() -> None:
    """`--ss-eps=1` 时只有带 self_inj 的帧换用 self 注入，哨兵帧保留 teacher 注入。"""
    intent = np.array([1, 2, 3, 4, 5, 6], dtype=np.int64)
    self_inj = np.full((6, 9), -1.0, dtype=np.float32)
    self_inj[2:4] = 0.0
    self_inj[2, 0] = 1.0  # 第 2/3 帧是真实 self 注入（prev=0），其余是缺失哨兵
    self_inj[3, 8] = 2.0
    split = _Split(
        obs=np.zeros((6, 1, 1, 1), np.float32),
        scal=np.zeros((6, 2), np.float32),
        intent=intent,
        bucket=np.zeros(6, np.int64),
        root=np.zeros(6, np.int64),
        self_inj=self_inj,
    )
    teacher = _inject_features(intent)
    train = _sample_train(
        split,
        teacher,
        SimpleNamespace(inject=True, max_train=0, quota=0, priority_root=-1, ss_eps=1.0),
        np.random.RandomState(3),
    )

    assert train.inj is not None
    assert np.array_equal(train.inj[2:4], self_inj[2:4])
    assert np.array_equal(train.inj[:2], teacher[:2])
    assert np.array_equal(train.inj[4:], teacher[4:])


def test_scheduled_sampling_without_self_inj_files_is_a_noop(tmp_path: Path) -> None:
    """全部 shard 都没有 self_inj.npy：`--ss-eps=1` 也不该炸，teacher 注入原样保留。"""
    d = _write_shard(tmp_path, "solo", 6, self_inj=False)
    split = _load_split([str(d)], [0], [0], max_train=0)
    teacher = _inject_features(split.intent)
    train = _sample_train(
        split,
        teacher,
        SimpleNamespace(inject=True, max_train=0, quota=0, priority_root=-1, ss_eps=1.0),
        np.random.RandomState(3),
    )
    assert train.inj is not None
    assert np.array_equal(train.inj, teacher)


def test_max_train_subsample_keeps_injection_aligned() -> None:
    """上限子采样与注入特征用同一下标：采样行的注入仍等于**全量有序**构建的那一行。"""
    n = 40
    rng = np.random.RandomState(4)
    obs = np.zeros((n, 1, 1, 1), np.float32)
    obs[:, 0, 0, 0] = np.arange(n)  # 行号带在 obs 里，便于反查全局下标
    split = _Split(
        obs=obs,
        scal=rng.rand(n, 2).astype(np.float32),
        intent=rng.randint(0, 8, n).astype(np.int64),
        bucket=rng.randint(0, 3, n).astype(np.int64),
        root=np.zeros(n, np.int64),
        self_inj=np.full((n, 9), -1.0, np.float32),
    )
    teacher = _inject_features(split.intent)
    train = _sample_train(
        split,
        teacher,
        SimpleNamespace(inject=True, max_train=10, quota=0, priority_root=-1, ss_eps=0.0),
        np.random.RandomState(9),
    )

    assert train.intent.shape[0] == 10
    picked = train.obs[:, 0, 0, 0].astype(np.int64)
    assert train.inj is not None
    assert np.array_equal(train.inj, teacher[picked])


def test_load_split_keeps_first_shard_when_cap_is_tiny(tmp_path: Path) -> None:
    """`--max-train` 远小于第一个 shard：上限是软的，块不得为空（否则 concat 抛错）。"""
    d = _write_shard(tmp_path, "big", 10, self_inj=False)
    split = _load_split([str(d)], [0], [0], max_train=2)  # cap = 3 < 10
    assert split.intent.shape == (10,)


def test_single_shard_is_a_clear_error(tmp_path: Path) -> None:
    """只有一个 shard ⇒ val 拿走唯一一个、train 为空：应响亮报错，而不是 numpy ValueError。"""
    _write_shard(tmp_path, "only", 5, self_inj=False)
    with pytest.raises(SystemExit):
        probe._index_shards([str(tmp_path)], np.random.RandomState(1), 0.2)


def test_fit_and_predict_round_trip() -> None:
    """`_fit` 两 epoch 后 `_predict_val` 产出逐帧合法类别（训练路径端到端冒烟）。"""
    n = 12
    rng = np.random.RandomState(3)
    split = _Split(
        obs=rng.rand(n, OBS_CHANNELS, BOARD, BOARD).astype(np.float32),
        scal=rng.rand(n, SCALAR_DIM).astype(np.float32),
        intent=rng.randint(0, 8, n).astype(np.int64),
        bucket=rng.randint(0, 3, n).astype(np.int64),
        root=np.zeros(n, np.int64),
        self_inj=np.full((n, 9), -1.0, np.float32),
    )
    train = _TrainSet(
        obs=split.obs,
        scal=split.scal,
        intent=split.intent,
        inj=_inject_features(split.intent),
    )
    model = IntentProbeNet(NetSpec(h=8, d=4), inject=True)
    _fit(model, train, SimpleNamespace(epochs=2, batch=5, lr=1e-3))

    pred = _predict_val(model, split, _inject_features(split.intent))
    assert pred.shape == (n,)
    assert ((pred >= 0) & (pred < 8)).all()
