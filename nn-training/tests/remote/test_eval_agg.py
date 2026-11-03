"""tests/remote/test_eval_agg.py —— 分析列全量聚合行 `eval_agg`（2026-11-03）。

用户口径：逐局仍走白名单（体积不变），但**每个 it** 额外把整轮的分析列聚合成一行传回 hub。

钉住五件事：

1. **口径**：数值 sum/mean/min/max/n、数字列表逐位求和、字符串列表元素计数、字符串值计数、
   `None` 不计、白名单键不进 `analysis`（逐局已有）；
2. **确定性**：同一批行打乱顺序 → 逐字节相同的聚合行；
3. **分组合并**：只按 `(iter, wver)` 分组，一组一行；
4. **随体发送**：`eval_rows_for` 返回「逐局（白名单）+ summary + 聚合」，聚合不占逐局上界；
5. **落账单调**：`merge_eval_rows` 把聚合行并进账本，重投幂等、少局不覆盖多局。
"""

from __future__ import annotations

import json
from pathlib import Path

from remote.deliver_parts import eval_rows_for
from remote.deliver_wire import EVAL_AGG_EVENT, EVAL_ROW_WIRE_KEYS, aggregate_eval_rows
from worker.eval_rows import merge_eval_rows, read_eval_agg_rows


def _row(stage: int = 2000, seed: int = 1, **kw: object) -> dict:
    base: dict = {
        "event": "eval",
        "iter": 5,
        "wver": "a" * 16,
        "stage": stage,
        "seed": seed,
        "node": "cloud",
        "time": "2026-11-03 01:00:00",
        "win": 1,
        "cellsVisited": 100,
        "idleTicks": 10,
        "moveHist": [1, 2, 3],
        "killOrder": ["basic", "fast"],
        "firstHitKind": "basic",
        "lossDetail": None,
        "dims": {"loot": {"coins": 1}},
    }
    base.update(kw)
    return base


def _agg(iter_: int, games: int, wver: str = "a" * 16) -> dict:
    return {
        "event": EVAL_AGG_EVENT,
        "iter": iter_,
        "wver": wver,
        "games": games,
        "analysis": {"cellsVisited": {"sum": games * 100}},
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_aggregate_analysis_columns_and_exclude_wire_keys() -> None:
    rows = [
        _row(seed=1, cellsVisited=100, moveHist=[1, 2, 3]),
        _row(seed=2, cellsVisited=300, moveHist=[2, 2, 2], idleTicks=30),
    ]
    got = aggregate_eval_rows(rows)
    assert len(got) == 1
    agg = got[0]
    assert agg["event"] == EVAL_AGG_EVENT
    assert (agg["games"], agg["stages"], agg["nodes"]) == (2, 1, {"cloud": 2})
    assert agg["analysis"]["cellsVisited"] == {
        "sum": 400,
        "mean": 200.0,
        "min": 100,
        "max": 300,
        "n": 2,
    }
    assert agg["analysis"]["idleTicks"]["sum"] == 40
    assert agg["analysis"]["moveHist"] == {"sum": [3, 4, 5], "n": 2}
    assert agg["analysis"]["killOrder"]["counts"] == {"basic": 2, "fast": 2}
    assert agg["analysis"]["firstHitKind"]["counts"] == {"basic": 2}
    # 全 None 的列省略；白名单键（含 dims）逐局已有，不再进聚合
    assert set(agg["analysis"]).isdisjoint(set(EVAL_ROW_WIRE_KEYS) | {"lossDetail"})


def test_aggregate_is_order_independent() -> None:
    rows = [_row(seed=i, cellsVisited=i * 10) for i in range(20)]
    shuffled = list(rows)
    shuffled.reverse()
    a = json.dumps(aggregate_eval_rows(rows), ensure_ascii=False, sort_keys=True)
    b = json.dumps(aggregate_eval_rows(shuffled), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_aggregate_groups_by_iter_and_wver() -> None:
    rows = [_row(seed=1), _row(seed=2, wver="b" * 16), _row(seed=3, iter=6)]
    got = aggregate_eval_rows(rows)
    assert [(r["iter"], r["wver"]) for r in got] == [(5, "a" * 16), (5, "b" * 16), (6, "a" * 16)]
    assert [r["games"] for r in got] == [1, 1, 1]


def test_eval_rows_for_carries_one_agg_without_touching_per_game_rows(tmp_path: Path) -> None:
    rows = [
        _row(stage=2000 + s, seed=i, cellsVisited=100)
        for s in range(35)
        for i in range(20)
    ]
    _write(tmp_path / "eval_log.jsonl", rows)

    got, _raw_b, _wire_b = eval_rows_for(tmp_path, 5)
    games = [r for r in got if r["event"] == "eval"]
    aggs = [r for r in got if r["event"] == EVAL_AGG_EVENT]
    assert len(games) == 700, "逐局仍是 700 条"
    assert all(set(r) <= set(EVAL_ROW_WIRE_KEYS) for r in games), "逐局仍然只有白名单"
    assert len(aggs) == 1 and aggs[0]["games"] == 700
    assert aggs[0]["analysis"]["cellsVisited"]["sum"] == 700 * 100


def test_agg_merge_is_idempotent_and_monotone(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "eval_log.jsonl"

    _write(src, [_agg(5, 700)])
    assert merge_eval_rows(src, dst) == (0, 0)
    assert [r["games"] for r in read_eval_agg_rows(dst)] == [700]

    # 重投/重导：同局数不写第二行
    assert merge_eval_rows(src, dst) == (0, 0)
    assert [r["games"] for r in read_eval_agg_rows(dst)] == [700]

    # 后到的旧聚合（更少局）不许覆盖新的
    _write(src, [_agg(5, 200)])
    merge_eval_rows(src, dst)
    assert [r["games"] for r in read_eval_agg_rows(dst)] == [700]

    # 补齐的更大聚合可以升级
    _write(src, [_agg(5, 800)])
    merge_eval_rows(src, dst)
    assert [r["games"] for r in read_eval_agg_rows(dst)] == [700, 800]
