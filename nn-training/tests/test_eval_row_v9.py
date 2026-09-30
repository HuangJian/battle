"""metrics v9 九列必须出现在逐局 eval 账本行里（eval_row + batch_runner.record 共用 helper）。

回归来源与 v8 同一条：`biz/eval_local.eval_row` 与 `trainer/batch_runner.BatchEvalRunner.record`
是**两个**行构造点，只改一处就会让日常 eval_log 失明（2026-09-24 v8 提交的实际事故）。
本文件按 v8 的 `test_eval_row_v8.py` 逐条镜像，覆盖命中方位 5 列 + 穿越税 4 列。
"""
from biz.eval_local import eval_row, eval_v9_fields

EVAL_V9_KEYS = (
    "backHits",
    "sideHits",
    "frontHitsExempt",
    "farHits",
    "geoFallback",
    "onLaneTicks",
    "onLaneExemptTicks",
    "onLaneMoveTicks",
    "onLaneHoldFireTicks",
)


def _manifest():
    return {
        "win": 1,
        "cleared": 1,
        "outcome": "stage_clear",
        "ticks": 5000,
        "score": 0.9,
        "kills": 20,
        "enemyHits": 50,
        "playerDamageTaken": 100,
        "playerHits": 1,
        "policy": "nn",
        "enemyTotal": 20,
        "playerDeaths": 0,
        "playerShots": 60,
        "playerLevel": 1,
        "cellsVisited": 134,
        "firstKillTick": 422,
        "stuckTicks": 100,
        "playerHpRatio": 0.75,
        "dangerTicks": 120,
        "threatTicks": 36,
        "dmgFirst600": 0,
        "backHits": 7,
        "sideHits": 9,
        "frontHitsExempt": 2,
        "farHits": 3,
        "geoFallback": 0,
        "onLaneTicks": 36,
        "onLaneExemptTicks": 4,
        "onLaneMoveTicks": 30,
        "onLaneHoldFireTicks": 2,
    }


def test_v9_helper_passthrough_exact():
    out = eval_v9_fields(_manifest())
    assert out == {
        "backHits": 7,
        "sideHits": 9,
        "frontHitsExempt": 2,
        "farHits": 3,
        "geoFallback": 0,
        "onLaneTicks": 36,
        "onLaneExemptTicks": 4,
        "onLaneMoveTicks": 30,
        "onLaneHoldFireTicks": 2,
    }


def test_v9_helper_old_manifest_omits_keys():
    # 缺省语义与 v8 同：整键省略（不是 None）——下游以“键缺席”判未知；
    # 且 `0` 是合法读数（例如 geoFallback=0 的干净局），不得被当成缺失丢掉。
    assert eval_v9_fields({"win": 1}) == {}
    assert eval_v9_fields(None) == {}
    assert eval_v9_fields({"geoFallback": 0}) == {"geoFallback": 0}


def test_eval_row_carries_v9_fields():
    row = eval_row(_manifest(), it=5, key16="0" * 16, task=(2000, 860001), node="self")
    for k in EVAL_V9_KEYS:
        assert row[k] == _manifest()[k]


def test_batch_runner_record_uses_shared_helper():
    # `BatchEvalRunner.record` 是方法体，改与不改只能看源码：它必须经 eval_v9_fields 取
    # v9 列（与 eval_row 同源），否则两处行构造点再次分叉（v8 的实际事故）。
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[2] / "nn-training/trainer/batch_runner.py").read_text(
        encoding="utf-8"
    )
    assert "eval_v9_fields" in src
