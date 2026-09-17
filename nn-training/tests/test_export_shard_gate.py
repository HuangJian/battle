"""tests/test_export_shard_gate.py —— 远程发布前的「本轮该训 shard 集」判定（`_gate_round_shards`）。

为什么单独钉这个纯函数：三条分支各自对应一类真事故，而**导出的那条**是 2026-09-17
实测踩出来的——拿一个刚跑过一轮的真课导任务包，`rollout_spec` 非空 + traj 目录有历史
残留 shard 命中「M3 上云轮必须空 shard」的门，`--export-bundle` 直接 SystemExit，包产
不出来。即「任何跑过一轮的课都导不出包」，而导出既不发 job 也不训练，那些门在这里没有
对象。

（本模块只测纯函数：真正的 `_remote_ppo` 发布链另有 tests/test_remote_ppo.py 覆盖。）
"""

from __future__ import annotations

import pytest

from rl.loop_steps import _gate_round_shards


def _gate(**kw: object) -> list:
    base: dict = {
        "local_shards": [],
        "rollout_spec": False,
        "exporting": False,
        "it": 7,
        "it_dir": "/traj/it7",
    }
    base.update(kw)
    return _gate_round_shards(**base)


def test_local_round_returns_the_shards() -> None:
    """本机轮：有完整 shard → 原样返回（发布链拿它打包 payload）。"""
    assert _gate(local_shards=["a", "b"]) == ["a", "b"]


def test_local_round_without_shards_is_refused() -> None:
    """本机轮没 shard：拦在发布前（否则云上以「payload 缺件」这种假因失败）。"""
    with pytest.raises(SystemExit) as e:
        _gate(local_shards=[])
    assert "无完整 shard" in str(e.value)
    assert "/traj/it7" in str(e.value)  # 诊断带上具体目录（不然得自己去猜）


def test_node_round_must_have_no_local_shards() -> None:
    """M3 上云轮：shard 集必须为空（非空 = 本地采样没关干净，会双份采集）。"""
    assert _gate(rollout_spec=True, local_shards=[]) == []
    with pytest.raises(SystemExit) as e:
        _gate(rollout_spec=True, local_shards=["leftover"])
    assert "拒绝发布 iter job" in str(e.value)
    assert "双份采集" in str(e.value)


# ────────────────────────── 回归：全离线导出 ──────────────────────────


def test_export_ignores_leftover_shards() -> None:
    """回归（2026-09-17）：导出时残留 shard 不该拦住打包——它既不发 job 也不训练。

    不修这条的后果不是「少一个便利功能」，而是**导入/导出这条腿整体不可用**：课程只要
    跑过一轮（traj 下就有 it-NNN 目录），控制台点「导出任务包」必失败。
    """
    assert _gate(exporting=True, rollout_spec=True, local_shards=["leftover"]) == []
    # 本机轮的「必须真有 shard」门同样不适用于导出（导出只要起点权重 + 计划）
    assert _gate(exporting=True, local_shards=[]) == []


def test_export_still_pins_the_publish_guards_for_real_rounds() -> None:
    """导出的例外**只**覆盖导出自己：同一批参数把 exporting 关掉，两条门照旧生效。

    （防的是「顺手把门删了」——例外必须窄到只剩导出这一条路。）
    """
    with pytest.raises(SystemExit):
        _gate(exporting=False, rollout_spec=True, local_shards=["leftover"])
    with pytest.raises(SystemExit):
        _gate(exporting=False, local_shards=[])
