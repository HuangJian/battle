"""metrics v10 二十列（本表 19 键）必须出现在逐局 eval 账本行里（eval_row + batch_runner.record 共用 helper）。

回归来源与 v8/v9 同一条：`worker/eval_local.eval_row` 与 `trainer/batch_runner.BatchEvalRunner.record`
是**两个**行构造点，只改一处就会让日常 eval_log 失明（2026-09-24 v8 提交的实际事故）。
本文件按 v9 的 `test_eval_row_v9.py` 逐条镜像，覆盖差距四族 15 列 − 3（enclExempt*）
+ aim-dodge 8 列 = 20 列（plan/metrics-v10-gap-columns.plan.md §1、plan/aim-dodge-levers §4）。

注意 `idleTicks` 不走本族：它已有自己的通道（`EVAL_NEWERA_KEYS`，与
moveHist/decisions/stopRuns 同行）——两表同含一键会在行构造点重复赋值。
"""
from worker.eval_local import eval_row, eval_v10_fields

EVAL_V10_KEYS = (
    "stopTicks",
    "fireHeldTicks",
    "enemyDist",
    "nearEnemy4Ticks",
    "damageBursts",
    "maxDamage120",
    "damageWhileLow",
    "encl1Ticks",
    "encl2Ticks",
    "encl3pTicks",
    "enclMax",
    "aimHits",
    "aimHitDistSum",
    "aimBricks",
    "aimIgnited",
    "aimMisses",
    "hurtWeight",
    "enclWeightTicks",
    "cornerWeightTicks",
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
        "stopTicks": 1200,
        "fireHeldTicks": 1600,
        "enemyDist": 0,
        "nearEnemy4Ticks": 240,
        "damageBursts": 0,
        "maxDamage120": 86,
        "damageWhileLow": 0,
        "encl1Ticks": 320,
        "encl2Ticks": 90,
        "encl3pTicks": 12,
        "enclMax": 3,
        "aimHits": 25,
        "aimHitDistSum": 62.0,
        "aimBricks": 7,
        "aimIgnited": 3,
        "aimMisses": 25,
        "hurtWeight": 14.5,
        "enclWeightTicks": 88.0,
        "cornerWeightTicks": 120.0,
    }


def test_v10_helper_passthrough_exact():
    out = eval_v10_fields(_manifest())
    assert out == {
        "stopTicks": 1200,
        "fireHeldTicks": 1600,
        "enemyDist": 0,
        "nearEnemy4Ticks": 240,
        "damageBursts": 0,
        "maxDamage120": 86,
        "damageWhileLow": 0,
        "encl1Ticks": 320,
        "encl2Ticks": 90,
        "encl3pTicks": 12,
        "enclMax": 3,
        "aimHits": 25,
        "aimHitDistSum": 62.0,
        "aimBricks": 7,
        "aimIgnited": 3,
        "aimMisses": 25,
        "hurtWeight": 14.5,
        "enclWeightTicks": 88.0,
        "cornerWeightTicks": 120.0,
    }


def test_v10_helper_omits_idle_ticks():
    # `idleTicks` 属 EVAL_NEWERA_KEYS（与 moveHist/decisions/stopRuns 同行），
    # 本 helper 不得重复透传 —— 否则行构造点出现同名键两次。
    assert "idleTicks" not in eval_v10_fields(_manifest())


def test_v10_helper_old_manifest_omits_keys():
    # 缺省语义与 v8/v9 同：整键省略（不是 None）——下游以“键缺席”判未知；
    # 且 `0` 是合法读数（例如干净局的 damageWhileLow=0），不得被当成缺失丢掉。
    assert eval_v10_fields({"win": 1}) == {}
    assert eval_v10_fields(None) == {}
    assert eval_v10_fields({"enemyDist": 0}) == {"enemyDist": 0}
    assert eval_v10_fields({"enclMax": 0}) == {"enclMax": 0}
    assert eval_v10_fields({"cornerWeightTicks": 0}) == {"cornerWeightTicks": 0}


def test_eval_row_carries_v10_fields():
    row = eval_row(_manifest(), it=5, key16="0" * 16, task=(2000, 860001), node="self")
    for k in EVAL_V10_KEYS:
        assert row[k] == _manifest()[k]


def test_batch_runner_record_uses_shared_helper():
    # `BatchEvalRunner.record` 是方法体，改与不改只能看源码：它必须经 eval_v10_fields 取
    # v10 列（与 eval_row 同源），否则两处行构造点再次分叉（v8 的实际事故）。
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[3] / "nn-training/trainer/batch_runner.py").read_text(
        encoding="utf-8"
    )
    assert "eval_v10_fields" in src
