"""metrics v8 四列必须出现在逐局 eval 账本行里（eval_row + batch_eval.record 共用 helper）。

回归来源：v8 提交只改了 TS 导出器与 reward_library，Python 两处行构造点
（rl/eval_local.eval_row、rl/batch_eval.record）没跟进 ⇒ 日常 eval_log 的
dmgFirst600/dangerTicks/threatTicks/playerHpRatio 全缺，机制先行读数失明。
"""
from rl.eval_local import eval_row, eval_v8_fields

EVAL_V8_KEYS = ("playerHpRatio", "dangerTicks", "threatTicks", "dmgFirst600")


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
    }


def test_v8_helper_passthrough_exact():
    out = eval_v8_fields(_manifest())
    assert out == {
        "playerHpRatio": 0.75,
        "dangerTicks": 120,
        "threatTicks": 36,
        "dmgFirst600": 0,
    }


def test_v8_helper_old_manifest_omits_keys():
    # 缺省语义：整键省略（不是 None）——下游以“键缺席”判未知；
    # 显式 None 落盘为 null 会被误计入 clean600 分母。
    assert eval_v8_fields({"win": 1}) == {}
    assert eval_v8_fields(None) == {}


def test_eval_row_carries_v8_fields():
    row = eval_row(_manifest(), it=5, key16="0" * 16, task=(2000, 860001), node="self")
    for k in EVAL_V8_KEYS:
        assert row[k] == _manifest()[k]


def test_batch_eval_record_uses_shared_helper():
    # batch_eval.record 是闭包，改与不改只能看源码：它必须经 eval_v8_fields 取 v8 列
    # （与 eval_row 同源），否则两处行构造点再次分叉。
    import pathlib

    # 从本文件反推仓根：门禁（`nn-python-gate.sh`）的 cwd 是 `nn-training/`，
    # 写死仓根相对路径会 FileNotFoundError（2026-09-24 修）。
    src = (pathlib.Path(__file__).resolve().parents[2] / "nn-training/rl/batch_eval.py").read_text(
        encoding="utf-8"
    )
    assert "eval_v8_fields" in src
