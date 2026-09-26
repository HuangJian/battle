"""test_ppo_quota —— 严格样本量配额（target_transitions 路线，方案 A）。

三组契约：
  1. `trim_shard_arrays` 边界：只切「第 0 维 == N」的数组；0 维字段不动；
     `keep >= N` 原样返回（零拷贝）；`keep <= 0` 空。
  2. 逐关配额：每关严格 = per_stage_quota；配额满后**整 shard 丢弃**；
     截断发生在 **GAE 之前**（gaE 收到的是截断后的 N）。
  3. `per_stage_quota=0`（默认）逐字节走原路：全收、字段集与 episode 数不变。
"""

from __future__ import annotations

import numpy as np
import pytest

# 本文件测的全是 numpy 纯逻辑（trim_shard_arrays / load_episodes_common / 逐关配额），
# 这两个函数的家是免 torch 的 `ppo.np_core`（2026-09-26 从 ppo.common 搬出）——
# 从这里 import 才不把 torch 拖进测试路径；补丁点也随之在本模块命名空间里。
import ppo.np_core as C


def _fake_shard(n: int, stage: int) -> dict:
    return {
        "obs": np.zeros((n, 2), dtype=np.uint8),
        "mask": np.ones((n, 2), dtype=np.uint8),
        "value": np.zeros(n, dtype=np.float32),
        "reward": np.zeros(n, dtype=np.float32),
        "done": np.zeros(n, dtype=np.float32),
        "stage": np.asarray(stage),  # 0 维：trim 不该碰它
    }


def test_trim_shard_arrays_edges() -> None:
    d = _fake_shard(10, 3)
    t = C.trim_shard_arrays(d, 4)
    assert t["obs"].shape[0] == 4
    assert t["reward"].shape[0] == 4
    assert t["stage"].shape == ()  # 0 维字段原样
    assert t["stage"] == 3
    # keep >= N：同一对象（零拷贝，不是逐键复制）
    assert C.trim_shard_arrays(d, 10) is d
    assert C.trim_shard_arrays(d, 99) is d
    # keep <= 0：空
    assert C.trim_shard_arrays(d, 0)["obs"].shape[0] == 0


def _collect(monkeypatch, shards: list[tuple[int, int]], quota: int):
    """shards = [(n_steps, stage), ...] → (episodes, gae 看到的 N, [(stage, N), ...])。

    `meta` 是**旁路记录**：`load_episodes_common` 按设计把 `stage` 从 episode 里剥掉，
    所以「每个 episode 属于哪一关」只能在这里顺手记下来——**不能用
    `zip(episodes, shards)` 位置对齐**（一旦配额满丢了 shard，位置就全错；
    本文件恰好有这种用例）。
    """
    monkeypatch.setattr(C, "discover_shards", lambda root, need: list(range(len(shards))))
    seen: list[int] = []
    meta: list[tuple[int, int]] = []  # (stage, kept_N)，与 episodes 一一对应

    def loader(sd):
        n, stage = shards[sd]
        return _fake_shard(n, stage)

    def gae(d):
        n = int(d["obs"].shape[0])
        seen.append(n)
        meta.append((int(d["stage"]), n))
        return np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)

    eps = C.load_episodes_common(
        "root",
        label="t",
        shard_kind="RL",
        need_files=(),
        shard_loader=loader,
        gae=gae,
        gae_name="GAE",
        normalize_ret=False,
        normalize_adv=False,
        per_stage_quota=quota,
    )
    return eps, seen, meta


def test_per_stage_quota_is_exact_and_per_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个 stage 各 40 步、quota=25 ⇒ 每关恰好 25（截断发生在 GAE 之前）。"""
    shards = [(20, 1000), (20, 1000), (20, 1001), (20, 1001)]
    eps, seen, meta = _collect(monkeypatch, shards, quota=25)
    total = sum(int(e["obs"].shape[0]) for e in eps)
    assert total == 50, f"两关各 25 步，实际 {total}"
    # 逐关精确（走旁路 meta，不靠位置对齐）
    per_stage: dict[int, int] = {}
    for st, n in meta:
        per_stage[st] = per_stage.get(st, 0) + n
    assert per_stage == {1000: 25, 1001: 25}
    # 截断在 GAE 之前：gaE 见过被截短的 5（25 - 20）
    assert 5 in seen, f"GAE 未收到截断后的长度：{seen}"
    assert 20 in seen


def test_quota_full_drops_whole_shard(monkeypatch: pytest.MonkeyPatch) -> None:
    """配额满后整 shard 丢弃（不切半局、也不跨关借额度）。"""
    shards = [(10, 1000), (10, 1000), (10, 1000), (10, 1001)]
    eps, _, meta = _collect(monkeypatch, shards, quota=15)
    # ★ 按 **stage** 断言（不是按 episode 长度）：第 3 个 10 步 shard 应被整块丢弃；
    #   stage1001 供给不足 15，也不许从 stage1000 借额度。
    per_stage: dict[int, int] = {}
    for st, n in meta:
        per_stage[st] = per_stage.get(st, 0) + n
    assert per_stage == {1000: 15, 1001: 10}, per_stage
    assert sum(per_stage.values()) == 25
    assert len(eps) == len(meta) == 3, "丢的是整 shard：4 个 shard → 3 个 episode"


def test_quota_zero_is_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """per_stage_quota=0（默认）⇒ 全收，且 episode 字段集不含 "stage"。"""
    shards = [(7, 1000), (9, 1001)]
    eps, seen, _ = _collect(monkeypatch, shards, quota=0)
    assert [int(e["obs"].shape[0]) for e in eps] == [7, 9]
    assert seen == [7, 9]  # 没截过
    assert "stage" not in eps[0], "stage 是加载期的分组键，不能进 episode"
    assert "reward" not in eps[0] and "done" not in eps[0]  # 原有排除项不受影响
    assert "adv" in eps[0] and "ret" in eps[0]
