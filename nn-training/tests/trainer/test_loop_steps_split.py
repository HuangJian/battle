"""P0-1 第六刀前置步的契约守卫：结算账本（iteration 事件读数）的**一个读面 + 一个写面**。

## 这一刀切了什么

`_log_report` 原先直接给 13 个槽位赋值，`TrainingRemoteJob._land_settle_fields` 又直接写其中 6 个
（`_agg` / `_chunks_n` / `_total_steps` / `_kl_cum` / `_ppo_sec` / `_ppo_cloud_sec`）——
同一份账本有两个写面、两个文件各写一遍。现在：

* `TrainingSteps.land_iter_ledger(fields)` 是**唯一**写入口（只接受 `LEDGER_WRITE_SLOTS`，
  拼错名字当场 `TypeError`），`_log_report` 与 `_land_settle_fields` 都经它；
* `TrainingSteps.iter_ledger()` + `IterLedgerView` 是**唯一**读入口（活引用，不拷副本），
  给测试与后续的协作对象一个具名读数；
* `_eval_thread` **不在**写面里：它是评估簇的句柄（`TrainingEval` 三处会写它），只在读面出现。

## 本文件钉住什么

1. 两个写者文件里对账本槽位的 `self._x = …` **零**直接赋值（写面收口是结构事实，不是注释）；
2. `land_iter_ledger` 的槽位表 == `LEDGER_WRITE_SLOTS`，未知槽位响亮 `TypeError`；
3. `iter_ledger()` 覆盖全表 + `eval_thread` / `node_rollout_sec`，且读的是**活**槽位。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from worker.loop_round import RemotePpoJob

NN_ROOT = Path(__file__).resolve().parents[2]
STEPS_PY = NN_ROOT / "trainer" / "loop_steps.py"
JOB_PY = NN_ROOT / "trainer" / "loop_remote_job.py"


def _methods(path: Path, cls: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}


def _self_stores(fn: ast.FunctionDef) -> set[str]:
    return {
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.ctx, ast.Store)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }


def test_ledger_slots_are_written_only_through_the_entry() -> None:
    """两个写者零直接赋值：写面收口是结构事实（拼错的名字也长不出来）。"""
    from trainer.loop_steps import LEDGER_WRITE_SLOTS

    slots = set(LEDGER_WRITE_SLOTS)
    assert not any(slot == "_eval_thread" for slot in slots), "评估句柄归 TrainingEval，不属于账本写面"
    for path, cls in ((STEPS_PY, "TrainingSteps"), (JOB_PY, "TrainingRemoteJob")):
        offenders = {
            f"{name}:{attr}"
            for name, fn in _methods(path, cls).items()
            for attr in _self_stores(fn) & slots
        }
        assert offenders == set(), f"{path.name} 还有直接写账本的路径：{sorted(offenders)}"


def test_entry_rejects_unknown_slots_loudly() -> None:
    """拼错槽位 → TypeError（不是静默在实例上长一个没人读的字段）。"""
    import pytest

    from trainer.loop_steps import TrainingSteps

    ts = TrainingSteps()
    with pytest.raises(TypeError, match="未知的结算账本槽位"):
        ts.land_iter_ledger({"agg": {}})


def test_log_report_lands_through_the_entry() -> None:
    """★ `_log_report` 的 12 个账本槽只经写面落位，值逐字相同（stream 与本地两条分支）。"""
    from trainer.loop_steps import TrainingSteps

    class _Recorder(TrainingSteps):
        def __init__(self) -> None:
            self.landed: dict[str, object] = {}

        def land_iter_ledger(self, fields: dict[str, object]) -> None:
            self.landed.update(fields)
            super().land_iter_ledger(fields)

    ts = _Recorder()
    ts._report = {
        "_eval_thread": "thread-handle",
        "_stream": {
            "rollout_sec": 1.5,
            "ppo_sec": 2.5,
            "steps": 10,
            "chunks": 2,
            "agg": {"kl": 0.25},
            "tail_drain_sec": 0.5,
            "kl_cum": 0.25,
            "halted": True,
            "dropped_games": 3,
            "load_sec": 0.7,
            "waves": 4,
        },
        "games": 4,
    }
    ts._stream_meta = {"stream": True}
    ts._node_rollout_sec = None
    ts._log_report(1, time.time())
    assert ts.landed.get("_rollout_sec") == 1.5
    assert ts.landed.get("_ppo_sec") == 2.5
    assert ts.landed.get("_ppo_cloud_sec") == 2.5
    assert ts.landed.get("_total_steps") == 10
    assert ts.landed.get("_chunks_n") == 2
    assert ts.landed.get("_agg") == {"kl": 0.25}
    assert ts.landed.get("_kl_cum") == 0.25
    assert ts.landed.get("_halted_flag") is True
    assert ts.landed.get("_dropped_games") == 3
    assert ts.landed.get("_load_sec") == 0.7
    assert ts.landed.get("_tail_drain_sec") == 0.5
    assert ts.landed.get("_waves_n") == 4
    assert not any(key == "_eval_thread" for key in ts.landed), "评估句柄不走账本写面"
    assert ts.iter_ledger().eval_thread == "thread-handle"


def test_iter_ledger_reads_the_live_slots() -> None:
    """读面是活读数：槽位改了，下一次 `iter_ledger()` 立刻反映（不拷副本）。"""
    from trainer.loop_steps import TrainingSteps

    ts = TrainingSteps()
    ts._agg = {"kl": 0.1}
    ts._chunks_n = 2
    ts._total_steps = 100
    ts._kl_cum = 0.1
    ts._ppo_sec = 3.0
    ts._ppo_cloud_sec = 2.5
    ts._rollout_sec = 4.0
    ts._halted_flag = False
    ts._dropped_games = None
    ts._load_sec = None
    ts._tail_drain_sec = None
    ts._waves_n = None
    ts._eval_thread = None
    ts._node_rollout_sec = None

    view = ts.iter_ledger()
    assert view.chunks_n == 2
    assert view.total_steps == 100
    assert view.agg is ts.iter_ledger().agg, "agg 是活引用（事件侧读的就是同一份）"
    assert view.agg == {"kl": 0.1}
    ts.land_iter_ledger({"_chunks_n": 5})
    assert ts.iter_ledger().chunks_n == 5


def test_settle_fields_go_through_the_same_entry() -> None:
    """`TrainingRemoteJob._land_settle_fields` 的 6 个字段也进同一个写面（同一份账本）。"""
    from trainer.loop_remote_job import TrainingRemoteJob

    class _Recorder(TrainingRemoteJob):
        def __init__(self) -> None:
            self.landed: dict[str, object] = {}

        def land_iter_ledger(self, fields: dict[str, object]) -> None:
            self.landed.update(fields)

    job = _Recorder()
    job.args = SimpleNamespace(traj="traj", out="out", hub_push=None)
    TrainingRemoteJob._land_settle_fields(
        job,
        cast("RemotePpoJob", _Sess()),
        {"agg": {"chunks": 3, "steps": 9, "kl": 0.5}},
    )
    agg = job.landed.get("_agg")
    assert isinstance(agg, dict)
    assert agg.get("kl") == 0.5
    assert job.landed.get("_chunks_n") == 3
    assert job.landed.get("_total_steps") == 9
    assert job.landed.get("_kl_cum") == 0.5
    ppo_sec = job.landed.get("_ppo_sec")
    ppo_cloud_sec = job.landed.get("_ppo_cloud_sec")
    assert isinstance(ppo_sec, float) and ppo_sec >= 0.0
    # result 没有 worker 自报的 `ppo_sec` ⇒ 云端训练秒回落往返墙钟，值逐字相同。
    assert ppo_cloud_sec == ppo_sec


class _Sess:
    """最小 `RemotePpoJob` 替身：`_land_settle_fields` 在账本之外只读这几格。"""

    t_ppo = time.time() - 2.0
    nodes = None
    hub_push = False
    pack_sec = 0.0
    rollout_spec = None
