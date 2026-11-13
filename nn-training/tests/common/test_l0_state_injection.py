"""L0 状态对象化的**注入契约**（`python.refactor.md` §P0-5）。

三个模块级可变态已各自收进显式对象：

  · `common/log.LogRouter` —— 课程前缀（ContextVar）与课日志句柄；
  · `common/fs.JsonlLedger` —— 每路径单写者；
  · `common/weights_ledger.WeightsLedger` —— 权重下发账本。

判据只有一条：**两个实例互不串味**（能隔离才叫注入；共用一个默认实例只是把全局藏进对象）。
"""

from __future__ import annotations

import json
from pathlib import Path

from common.fs import JsonlLedger, append_jsonl
from common.log import LogRouter
from common.weights_ledger import WeightsLedger


def test_log_routers_are_isolated() -> None:
    a, b = LogRouter(), LogRouter()
    a.set_prefix("course-a")
    assert a.get_prefix() == "course-a"
    assert b.get_prefix() == "", "前缀必须是实例级 ContextVar，不能串到另一个 router"


def test_jsonl_ledgers_are_isolated(tmp_path: Path) -> None:
    a, b = JsonlLedger(), JsonlLedger()
    path = tmp_path / "ledger.jsonl"
    append_jsonl(path, {"i": 1}, ledger=a)
    try:
        assert a.writers, "注入的账本应持有写者"
        assert not b.writers, "另一个账本不得看到别人的写者"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        assert rows == [{"i": 1}]
    finally:
        a.close()


def test_weights_ledgers_are_isolated() -> None:
    a, b = WeightsLedger(), WeightsLedger()
    a.mark_pushed("w1", "node-1")
    assert a.already_pushed("w1", "node-1")
    assert not b.already_pushed("w1", "node-1"), "账本必须实例级隔离"
    assert a.forget("node-1") == 1
    assert not a.already_pushed("w1", "node-1")
