"""`intent_probe` 的配额采样两臂都必须返回 (行, 选中下标)。

`quota_sample`（A 臂：比例配额）与 `quota_sample_priority`（B 臂：priority-root 优先）
在 P2-6c（2026-09-02）之后都返回 `(五行, sel)` —— 调用方要按同一个 `sel` 同步筛
teacher-forced 注入特征。B 臂的调用点跟着改了，A 臂没改，于是 `--quota>0` 且默认
`--priority-root=-1`（= 探针的默认 A 臂）在解包处直接 ValueError：这条路径自那时起
就没跑通过。本用例钉住三件事：A 臂能跑完、每类 ≤ quota、注入特征与 intent 同下标。

固定 seed，无墙钟；纯 numpy，不训练。
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from worker.train.intent_probe import _inject_features, _sample_train, _Split


def _split(n: int = 200) -> _Split:
    rng = np.random.RandomState(5)
    return _Split(
        obs=rng.rand(n, 3).astype(np.float32),
        scal=rng.rand(n, 4).astype(np.float32),
        intent=rng.randint(0, 8, n).astype(np.int64),
        bucket=rng.randint(0, 3, n).astype(np.int64),
        root=np.zeros(n, np.int64),
        self_inj=np.full((n, 9), -1.0, np.float32),
    )


def _args(**over: object) -> SimpleNamespace:
    base = dict(inject=True, max_train=0, quota=5, priority_root=-1, ss_eps=0.0)
    return SimpleNamespace(**{**base, **over})


def test_a_arm_quota_returns_rows_and_sel() -> None:
    """A 臂（默认 priority_root=-1）必须跑完，每类 ≤ quota，三块同下标。"""
    split = _split()
    inj = _inject_features(split.intent)
    train = _sample_train(split, inj, _args(), np.random.RandomState(7))

    assert train.inj is not None
    assert train.intent.shape == train.inj.shape[:1]
    assert (np.bincount(train.intent, minlength=8) <= 5).all()
    assert train.obs.shape[0] == train.scal.shape[0] == train.intent.shape[0]


def test_b_arm_matches_a_arm_contract() -> None:
    """B 臂（priority-root ≥ 0）返回同样的行数契约。"""
    split = _split()
    inj = _inject_features(split.intent)
    train = _sample_train(split, inj, _args(priority_root=0), np.random.RandomState(7))

    assert train.inj is not None
    assert train.intent.shape == train.inj.shape[:1]
    assert (np.bincount(train.intent, minlength=8) <= 5).all()


def test_sampling_is_seed_deterministic() -> None:
    """同 seed 两遍 → 逐元素相同（探针结论必须可复现）。"""
    split = _split()
    inj = _inject_features(split.intent)
    a = _sample_train(split, inj, _args(), np.random.RandomState(11))
    b = _sample_train(split, inj, _args(), np.random.RandomState(11))
    assert np.array_equal(a.intent, b.intent)
    assert a.inj is not None and b.inj is not None
    assert np.array_equal(a.inj, b.inj)
