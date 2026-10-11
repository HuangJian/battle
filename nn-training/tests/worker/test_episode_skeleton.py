"""tests/worker/test_episode_skeleton.py —— 公共装载骨架的口径守卫（plan/nn-training-reuse-and-deps §S3）。

三个 backend（`np_core` / `goal` / `intent`）现在共用 `worker/ppo/_episode.episode_from_shard`。
本文件钉住它**唯一可能被改坏的四处**：

1. **键序**：`fields` 的声明顺序 = episode dict 的插入顺序（各模块的字段契约按原序摆，`adv`/`ret`
   是占位符）——顺序变了不影响语义，但会让「同输入同输出」的对账与 diff 变难；
2. `adv`/`ret` **恒为 `float32`**（下游按该 dtype 打包，改了会静默抬内存/降精度）；
3. 空 shard（`obs.shape[0] == 0`）→ `None`（不是空 dict：调用方靠 `is None` 收尾）；
4. `optional` 字段**存在才带**（旧 shard 缺列 = 老行为）。

三个 backend 的端到端行为由既有 stream/e2e 用例覆盖，这里只钉骨架本身。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from worker.ppo._episode import episode_from_shard


def _shard(n: int = 3) -> dict[str, Any]:
    return {
        "obs": np.zeros((n, 4, 13, 13), np.float32),
        "scalars": np.zeros((n, 8), np.float32),
        "a_move": np.zeros(n, np.int64),
        "value": np.zeros(n, np.float32),
        "mask": np.ones(n, np.float32),
        "reward": np.zeros(n, np.float64),
        "done": np.zeros(n, np.float64),
        "dt": np.ones(n, np.int64),
    }


def _gae_zero(d: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    n = d["obs"].shape[0]
    return np.zeros(n, np.float64), np.zeros(n, np.float64)


def test_key_order_follows_the_declared_fields() -> None:
    ep = episode_from_shard(
        _shard(),
        _gae_zero,
        fields=("obs", "scalars", "a_move", "value", "adv", "ret", "mask"),
    )
    assert ep is not None
    assert list(ep) == ["obs", "scalars", "a_move", "value", "adv", "ret", "mask"]


def test_adv_and_ret_are_float32() -> None:
    ep = episode_from_shard(_shard(), _gae_zero, fields=("obs", "adv", "ret"))
    assert ep is not None
    assert ep["adv"].dtype == np.float32
    assert ep["ret"].dtype == np.float32


def test_empty_shard_returns_none_not_empty_dict() -> None:
    """空 shard → `None`（调用方按 `is None` 收尾；空 dict 会被当成「有 episode 但没数据」）。"""
    assert episode_from_shard(_shard(0), _gae_zero, fields=("obs", "adv", "ret")) is None


def test_optional_fields_only_when_present_in_the_shard() -> None:
    """旧 shard 缺列 ⇒ 老行为（不带该键）；有列 ⇒ 原样带上。"""
    fields = ("obs", "adv", "ret")
    absent = episode_from_shard(_shard(), _gae_zero, fields=fields, optional=("engage_label",))
    # 钉**键集与顺序**（不用 `"x" in dict` 这种措辞判据：`tests/test_source_text_assert_budget.py`
    # 的口径 A 会把它计成形态文本断言）。
    assert list(absent or {}) == ["obs", "adv", "ret"]
    present = episode_from_shard(
        {**_shard(), "engage_label": np.ones(3, np.float32)},
        _gae_zero,
        fields=fields,
        optional=("engage_label",),
    )
    assert list(present or {}) == ["obs", "adv", "ret", "engage_label"]


def test_gae_is_computed_from_the_whole_shard() -> None:
    """`gae` 吃的是**整个 shard dict**（goal/intent 的变步长 GAE 需要 `dt`/`done`）。"""
    seen: list[dict[str, Any]] = []

    def spy(d: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        seen.append(d)
        return np.ones(d["obs"].shape[0], np.float64), np.zeros(d["obs"].shape[0], np.float64)

    ep = episode_from_shard(_shard(), spy, fields=("obs", "adv", "ret"))
    assert seen, "gae 没被调用"
    assert {"dt", "done", "reward"} <= set(seen[0])  # 变步长 GAE 需要的列都在
    assert ep is not None and float(ep["adv"][0]) == 1.0
