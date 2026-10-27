"""tests/worker/test_stage_rotation.py —— 逐轮采样子集（窗口）的纯函数契约。

plan/it-stage-ratation.plan.md §6（判据 1 / 4 + 模式 2 的两条）。本文件只测**判据本身**：
窗口是 `(rotate_seed, it)` 的纯函数（resume-safe）、`ceil(n/k)` 轮铺满一遍且 epoch 内不重叠、
pinned 恒在、返回值排序且是 `--stages` 的子集、配置错一律响亮。

接线（`volume_block` / `_per_stage_quota` / 导出侧）在
`tests/trainer/test_stage_rotation_wiring.py` —— 那条钉的是「三条腿同一个窗口」，本文件不重复。
"""

from __future__ import annotations

import pytest

from worker.stage_rotation import describe, window_for, window_from_args


def _cover(mode: str, *, n: int, k: int, stages: list[int], seed: int = 4242) -> list[list[int]]:
    """连跑 `ceil(n/k)` 轮（一个 epoch）的窗口。"""
    per_epoch = -(-n // k)
    return [
        window_for(mode=mode, it=it, all_stages=stages, rotate_seed=seed, per_it=k)
        for it in range(1, per_epoch + 1)
    ]


def test_even_window_covers_all_in_ceil_rounds() -> None:
    """判据 1：`ceil(n/k)` 轮的**并集 == 全集**，且 epoch 内**无重复**（覆盖 ≠ 重复采）。

    关号用非连续的 `2000+i`（自定义关的真实形态）：旧实现 `permutation(total_stages)` 把关号
    当位置，非连续关号会算出窗口外的关 —— 这条用例就是那个回归的探针。
    """
    stages = [2000 + i for i in range(35)]
    k = 12
    rounds = _cover("even", n=len(stages), k=k, stages=stages)

    seen = [s for w in rounds for s in w]
    assert sorted(seen) == stages, "并集必须等于全集"
    assert len(seen) == len(set(seen)), f"同一 epoch 内重复采了：{seen}"
    assert [len(w) for w in rounds] == [12, 12, 11], rounds  # 最后一窗不满
    assert all(w == sorted(w) for w in rounds), "窗口必须排序（顺序进 data_fp）"


def test_window_is_resume_stable() -> None:
    """判据 4：同 `(rotate_seed, it)` **两次独立构造**逐元素相同；换 seed 才变。"""
    stages = list(range(10))
    a = [window_for(mode="even", it=it, all_stages=stages, rotate_seed=7, per_it=4) for it in (1, 2, 5)]
    b = [window_for(mode="even", it=it, all_stages=stages, rotate_seed=7, per_it=4) for it in (1, 2, 5)]
    assert a == b
    other = window_for(mode="even", it=1, all_stages=stages, rotate_seed=8, per_it=4)
    assert other != a[0], "换 rotate_seed 必须换窗口（否则两条腿共用一份抽签）"


def test_pinned_window_always_contains_pinned() -> None:
    """模式 2：任意 `it` 的窗口都含 pinned（难关每轮都采），且余额关来自其余关。"""
    stages = list(range(8))
    pinned = [2, 5]
    for it in range(1, 12):
        w = window_for(
            mode="pinned", it=it, all_stages=stages, rotate_seed=99, per_it=3, pinned=pinned
        )
        assert set(pinned) <= set(w), (it, w)
        assert len(w) == len(pinned) + 3, w
        assert w == sorted(w)


def test_pinned_rest_rotates_without_overlap() -> None:
    """pinned 之外的余额关也轮换覆盖（不能每轮都抽同样 3 关）。"""
    stages = list(range(11))
    pinned = [0]
    rest = [s for s in stages if s not in pinned]
    k = 3
    per_epoch = -(-len(rest) // k)
    windows = [
        window_for(mode="pinned", it=it, all_stages=stages, rotate_seed=5, per_it=k, pinned=pinned)
        for it in range(1, per_epoch + 1)
    ]
    seen = [s for w in windows for s in w if s not in pinned]
    assert sorted(seen) == rest
    assert len(seen) == len(set(seen)), f"余额关重复：{seen}"


def test_window_is_sorted_and_subset() -> None:
    """窗口恒为 `all_stages` 的子集且排序（乱序会改声明集指纹）。"""
    stages = [7, 3, 11, 5]
    for mode, kw in (("even", {"per_it": 2}), ("pinned", {"per_it": 1, "pinned": [11, 3]})):
        w = window_for(mode=mode, it=3, all_stages=stages, rotate_seed=1, **kw)
        assert w == sorted(w)
        assert set(w) <= set(stages)


def test_unknown_mode_and_bad_pinned_are_loud() -> None:
    """配置错一律 `ValueError`（静默取一个窗口 = 采集量对不上目标，最难发现那种）。"""
    stages = [0, 1, 2]
    with pytest.raises(ValueError, match="rotation_mode"):
        window_for(mode="weird", it=1, all_stages=stages, rotate_seed=1, per_it=1)
    with pytest.raises(ValueError, match="pinned"):
        window_for(mode="pinned", it=1, all_stages=stages, rotate_seed=1, per_it=1, pinned=[9])
    with pytest.raises(ValueError, match="it"):
        window_for(mode="even", it=0, all_stages=stages, rotate_seed=1, per_it=1)
    with pytest.raises(ValueError, match="至少一个"):
        window_for(mode="even", it=1, all_stages=[], rotate_seed=1, per_it=1)


def test_per_it_zero_or_oversize_is_clamped() -> None:
    """`even` 的 per_it 必须 ≥1；`pinned` 的 0 = 只采 pinned；过大的 k 退化成整集。"""
    stages = list(range(6))
    with pytest.raises(ValueError, match="rotation_per_it"):
        window_for(mode="even", it=1, all_stages=stages, rotate_seed=1, per_it=0)
    # pinned + 余额 0 ⇒ 只采 pinned（合法配置）
    assert window_for(
        mode="pinned", it=4, all_stages=stages, rotate_seed=1, per_it=0, pinned=[3]
    ) == [3]
    # k ≥ n ⇒ 每轮整集（等于关闭窗口，但仍走确定性排列）
    assert window_for(mode="even", it=2, all_stages=stages, rotate_seed=1, per_it=99) == stages


def test_disabled_window_is_the_full_set_and_describe_reads_it() -> None:
    """`rotation_mode` 缺席 ⇒ 原样全集（老路径逐字节不变），`describe` 是一行读数。"""
    stages = [0, 1, 2]
    args = type("A", (), {"rotation_mode": "", "rotation_per_it": 0, "rotation_pinned": ""})()
    assert window_from_args(args, 9, stages) == stages
    assert describe([1, 2], stages).startswith("rotation 2/3 关")
