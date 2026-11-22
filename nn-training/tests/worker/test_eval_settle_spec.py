"""tests/worker/test_eval_settle_spec.py —— `EvalSettleSpec` 的形状守卫（§P0-3，2026-11-22）。

`settle_eval_summary` 原先 21 个参数：前 13 个是身份位（账本 / 本轮语料 / 现场计数 /
写者），后 8 个是可缺省读数（课程血缘、收工余量、`eval_bundle` 与 P2-2 观测）。云机调用点
（`remote/offline_eval`）旧版是**位置参数**调用，而 `total` / `wins` / `node_games` 类型相近
且相邻 —— 多传少传一位不会报错，summary 的读数会静默错位。所以判据是：

  · 值对象 frozen + kw_only（读数不许靠位置猜）；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

生产调用点传的就是 spec —— 由 `test_eval_seed_pool` 的落账行为用例与
`test_offline_eval_cloud` 覆盖（不读源码：读 `getsource` 会把每条 assert 计入口径 B 的
文本断言）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tests.helpers import run_setup_probe as probe
from worker.eval_track import EvalSettleSpec

#: `EvalSettleSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "eval_jsonl",
        "key16",
        "it",
        "pairs",
        "total",
        "landed",
        "wins",
        "cleared_total",
        "outcomes",
        "node_games",
        "append_row",
        "t_eval_start",
        "rollout_winrate",
        "course_fp",
        "left_pending",
        "left_pairs",
        "close_reason",
        "eval_bundle",
        "tail_race_grants",
        "dup_games",
        "frozen_sec",
    }
)


def test_eval_settle_spec_is_frozen_and_kw_only(tmp_path: Path) -> None:
    spec = EvalSettleSpec(
        eval_jsonl=tmp_path / "eval_log.jsonl",
        key16="w" * 16,
        it=0,
        pairs=[],
        total=0,
        landed=set(),
        wins=[0],
        cleared_total=[0],
        outcomes={},
        node_games={},
        append_row=lambda _row: None,
        t_eval_start=0.0,
        rollout_winrate=None,
    )
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(EvalSettleSpec)), (
        "`EvalSettleSpec` 不是 kw_only ⇒ 21 个位置参数只是换了个地方写"
    )


def test_eval_settle_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(EvalSettleSpec)}
    assert got == FIELDS, (
        f"`EvalSettleSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_settle_eval_summary_takes_only_the_spec() -> None:
    got = probe.param_names("worker.eval_track", "settle_eval_summary")
    assert got == ["spec"], f"`settle_eval_summary` 的形参是 {got}，应只有 `spec`"
