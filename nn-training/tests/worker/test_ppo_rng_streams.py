"""tests/worker/test_ppo_rng_streams.py — PPO 的三条显式随机流（§P1-7 / §10-B6，2026-11-17）。

## 本文件守的是什么

改造前：`np_core.chunk_episodes` 的重排、每 epoch 的 minibatch 顺序、每 minibatch 的
demo 抽样共吃**全局 `np.random`**。于是「同一 seed ⇒ 同一条轨迹」的前提是「进程里没有
别人抽随机数」——一条谁也执行不了的前提；而且加一处抽样就推歪别处（demo 系数从 0 改到
>0 ⇒ 之后每个 epoch 的重排全变）。

现在三条流各自一个 `default_rng`（`worker/ppo/rng.py::PPORng`），ckpt 存每条流的
`bit_generator.state`。本文件的判据：

1. 同 seed 两条独立实例 ⇒ 逐位相同的序列；不同 seed ⇒ 不同（不是「都随种子漂」的空话）；
2. `from_runtime` ≡ `from_seed`（派生规则只有 `Runtime.substream` 一份，不许两处漂移）；
3. 三条流互不干扰（消耗 chunk 不移动 epoch/demo —— 这正是「改一处不动别处」的可测形态）；
4. `chunk_episodes` 不碰全局 `np.random`（seed 后快照 get_state 前后相等）；
5. ckpt 存下的流状态灌回去 ⇒ 下一位抽样逐位接上；旧 ckpt（无 `rng_streams`）⇒
   `restore()` 返回 False 且**不抛**，模型/优化器照常续跑（调用方负责记一行 WARN）；
6. 静态面：`worker/ppo/**` 不许再出现任何**消费/播种全局 numpy 随机**的调用。

第 6 条是「零容忍」而非棘轮：三个训练线（engine / engine_paddle / goal / intent）外加
np_core 全在扫描面上，新增一处 `np.random.permutation` 就是回归 —— 它不会让今天的用例
变红，只会让「同 seed 同轨迹」在半年后悄悄失效，这正是要静态钉住它的理由。
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import runtime
from worker.ppo import np_core
from worker.ppo.rng import STREAMS, PPORng

ROOT = Path(__file__).resolve().parents[2]
PPO_DIR = ROOT / "worker" / "ppo"

#: 消费/播种全局随机状态的属性名（`np.random.Generator` 这类**类型标注**不在此列）。
GLOBAL_DRAWS = frozenset(
    {
        "choice",
        "get_state",
        "permutation",
        "rand",
        "randint",
        "random",
        "randn",
        "seed",
        "set_state",
        "shuffle",
    }
)


def test_same_seed_same_streams() -> None:
    a = PPORng.from_seed(5)
    b = PPORng.from_seed(5)
    for name in STREAMS:
        np.testing.assert_array_equal(
            getattr(a, name).permutation(64), getattr(b, name).permutation(64)
        )
    # 对照：不同种子必须给出不同的顺序（否则「同 seed」这条约束一分钱都不值）
    c = PPORng.from_seed(6)
    assert not np.array_equal(a.chunk.permutation(64), c.chunk.permutation(64))


def test_from_runtime_matches_from_seed() -> None:
    """派生规则只有一处：`Runtime.substream`。两条入口不得漂移。"""
    a = PPORng.from_seed(7)
    b = PPORng.from_runtime(runtime.Runtime(scope="ppo", seed=7))
    for name in STREAMS:
        np.testing.assert_array_equal(
            getattr(a, name).permutation(32), getattr(b, name).permutation(32)
        )
    # scope 是 seed 的一部分：换个 scope 就是另一组流（stream 训练按 iter 派生也靠它）
    d = PPORng.from_seed(7, scope="stream-it3")
    assert not np.array_equal(a.epoch.permutation(32), d.epoch.permutation(32))


def test_streams_are_independent() -> None:
    """消耗 chunk 流不移动 epoch/demo —— 「加/删一处抽样不动别处」的可测形态。"""
    a = PPORng.from_seed(11)
    b = PPORng.from_seed(11)
    for _ in range(5):
        a.chunk.permutation(16)
        a.chunk.integers(0, 8, size=4)
    np.testing.assert_array_equal(a.epoch.permutation(16), b.epoch.permutation(16))
    np.testing.assert_array_equal(a.demo.integers(0, 8, size=4), b.demo.integers(0, 8, size=4))


def test_chunk_episodes_does_not_touch_global_rng() -> None:
    eps: list[dict] = [
        {"obs": np.zeros((6, 2), dtype=np.uint8), "adv": np.zeros(6, dtype=np.float32)},
        {"obs": np.zeros((6, 2), dtype=np.uint8), "adv": np.zeros(6, dtype=np.float32)},
    ]
    # 判据用「重播」而不是比对 get_state 的元组：同一种子下，切块**之后**全局流吐出的
    # 数必须与没切过时逐位相同 —— 等价于「一次抽样也没从全局拿」。
    np.random.seed(1234)
    want = np.random.random(3)
    np.random.seed(1234)
    chunks = np_core.chunk_episodes(eps, 4, rng=PPORng.from_seed(3).chunk)
    got = np.random.random(3)
    assert len(chunks) == 3, f"12 步 / mb=4 ⇒ 3 块（got {len(chunks)}）"
    np.testing.assert_array_equal(got, want, err_msg="全局 numpy 流的位置被 chunk_episodes 动了")


def test_ckpt_restores_streams() -> None:
    """`_ppo_save`/`_ppo_load` 存的是每条流的 `bit_generator.state`（§P1-7）。"""
    import torch
    import torch.nn as nn

    from worker.ppo.common import _ppo_load, _ppo_save

    model = nn.Linear(3, 2)
    opt = torch.optim.Adam(model.parameters(), lr=0.1)
    rng = PPORng.from_seed(21)
    for _ in range(3):
        rng.epoch.permutation(8)
    td = tempfile.mkdtemp(dir=str(Path(__file__).resolve().parents[2] / "tmp"))
    try:
        _ppo_save(td, model, opt, 2, rng)
        st = json.loads(Path(os.path.join(td, "state.json")).read_text(encoding="utf-8"))
        assert set(st) == {"epochs_done", "rng_streams"}, f"state.json 字段（got {sorted(st)}）"
        assert set(st["rng_streams"]) == set(STREAMS), "三条流都要落盘"
        want = rng.epoch.permutation(8).tolist()

        m2 = nn.Linear(3, 2)
        o2 = torch.optim.Adam(m2.parameters(), lr=0.1)
        rng2 = PPORng.from_seed(21)
        assert _ppo_load(td, m2, o2, rng2) == 2, "epochs_done 照旧返回"
        assert rng2.epoch.permutation(8).tolist() == want, "续跑从 ckpt 那一点接着抽"

        # 旧 ckpt（全局 MT19937 时代）：没有 rng_streams ⇒ 不抛、模型照常加载、
        # 流停在调用方刚建好的位置（=「从种子重建」，由调用方记 WARN）。
        st.pop("rng_streams")
        st["rng"] = {"legacy": True}
        Path(os.path.join(td, "state.json")).write_text(json.dumps(st), encoding="utf-8")
        rng3 = PPORng.from_seed(21)
        expected = PPORng.from_seed(21).epoch.permutation(4).tolist()
        assert _ppo_load(td, m2, o2, rng3) == 2
        assert rng3.epoch.permutation(4).tolist() == expected, "旧 ckpt 不改动流（不半份续跑）"
        assert PPORng.from_seed(0).restore(None) is False
        assert PPORng.from_seed(0).restore({"chunk": {}, "epoch": {}, "demo": {}}) is False
    finally:
        import shutil

        shutil.rmtree(td, ignore_errors=True)


def test_no_global_numpy_random_in_ppo() -> None:
    """静态面：`worker/ppo/**` 里不许再消费全局 numpy 随机（零容忍，见模块 docstring）。"""
    hits: list[str] = []
    for path in sorted(PPO_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # `np.random.<name>`（属性访问）：只认「别名是 np 或 numpy」的链，别误伤
            # 一个叫 `random` 的变量自己的属性。
            if isinstance(node, ast.Attribute) and node.attr in GLOBAL_DRAWS:
                v = node.value
                if (
                    isinstance(v, ast.Attribute)
                    and v.attr == "random"
                    and isinstance(v.value, ast.Name)
                    and v.value.id in {"np", "numpy"}
                ):
                    hits.append(f"{path.name}:{node.lineno} np.random.{node.attr}")
    assert not hits, "全局 numpy 随机不许进 PPO（用 PPORng 的三条流）：\n" + "\n".join(hits)
