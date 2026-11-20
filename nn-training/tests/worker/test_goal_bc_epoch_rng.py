"""`goal_bc.epoch_rng`：epoch 打乱顺序必须是**局部**随机流（P0/B6，`python.refactor.md` §11）。

此前是 `np.random.seed(args.seed)` + `np.random.permutation(...)`：种子落在全局 numpy 状态上，
于是「同 seed 两跑一致」只在「进程里没别人动过 `np.random`」时成立。对 BC 这是真事故形状 ——
同一条命令重跑，episode 顺序变了，loss 曲线对不上，复盘只能猜。

钉住两件事：

1. 同 seed ⇒ 同顺序（且顺序确实被打乱，不是恒等排列）；
2. 建流**不吃**全局 numpy 流 —— 全局状态原样，任何插入的 `np.random.*` 调用都不会改我们的顺序。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.train.goal_bc import epoch_rng


def test_same_seed_same_order() -> None:
    n = 32
    a = list(epoch_rng(7).permutation(n))
    b = list(epoch_rng(7).permutation(n))
    assert a == b, "同 seed 两跑顺序不同 ⇒ 训练不可复现"
    assert a != list(range(n)), "顺序没被打乱（这是洗牌，不是恒等）"
    assert sorted(a) == list(range(n))


def test_deriving_the_stream_leaves_global_numpy_alone() -> None:
    np.random.seed(1234)
    fresh = np.random.random()
    np.random.seed(1234)
    order_first = list(epoch_rng(7).permutation(16))
    after = np.random.random()
    assert after == fresh, "建流动了全局 numpy 状态 ⇒ 别人插一次抽样就会改我们的顺序"
    assert order_first == list(epoch_rng(7).permutation(16))


def test_other_random_calls_cannot_reshuffle_the_order() -> None:
    before = list(epoch_rng(9).permutation(24))
    np.random.random(1000)  # 模拟别的库/导入期随手抽样
    after = list(epoch_rng(9).permutation(24))
    assert after == before, "外部 `np.random` 调用改变了 epoch 顺序 ⇒ 又回到全局流"
