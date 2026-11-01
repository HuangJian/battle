"""机制闸两列（wallPressTicks / aliveTicks）必须出现在逐局 eval 账本行里。

背景（plan/r5-next §5.3）：`wallPress/千 = wallPressTicks / 存活拍 × 1000`。E1/E2 的机制闸
读这一列；只加在 rollout shard、不加进 eval 行，判决点就永远读不出（2026-11-01 p2002 实测：
eval 行只有 onLaneTicks，只能拿它当近似）。

两个行构造点（`eval_rows.eval_row` 与 `trainer/batch_runner.record`）都经
`eval_metric_fields`，所以本文件同时钉这两条：单点改了、另一条漏了 = 行不一致。
"""
from worker.eval_local import eval_mechanism_fields, eval_metric_fields, eval_row


def _manifest(**extra):
    m = {
        "win": 0,
        "cleared": 0,
        "outcome": "gameover",
        "lossDetail": "base_destroyed",
        "ticks": 3000,
        "kills": 6,
        "playerDeaths": 0,
        "stuckTicks": 100,
        "wallPressTicks": 12,
        "aliveTicks": 2800,
    }
    m.update(extra)
    return m


def test_eval_row_carries_mechanism_fields():
    row = eval_row(_manifest(), it=150, key16="a" * 16, task=(2002, 2000000001), node="self")
    assert row["wallPressTicks"] == 12
    assert row["aliveTicks"] == 2800
    # wallPress/千 可直接从行推出（机制闸的分母定义见 plan §5.3）
    assert row["wallPressTicks"] / row["aliveTicks"] * 1000 == 12 / 2800 * 1000


def test_eval_metric_fields_carries_mechanism_fields():
    # batch_runner.record 走这条聚合入口；漏登记 ⇒ 分布式 eval 行失明
    fields = eval_metric_fields(_manifest())
    assert fields["wallPressTicks"] == 12
    assert fields["aliveTicks"] == 2800


def test_mechanism_fields_hidden_when_absent():
    m = _manifest()
    del m["wallPressTicks"]
    del m["aliveTicks"]
    # 与 v8–v13 同约：缺键 = 整键省略（不写 None、不伪造 0）
    assert "wallPressTicks" not in eval_mechanism_fields(m)
    assert "aliveTicks" not in eval_mechanism_fields(m)
    row = eval_row(m, it=150, key16="a" * 16, task=(2002, 2000000001), node="self")
    assert "wallPressTicks" not in row
    assert "aliveTicks" not in row
