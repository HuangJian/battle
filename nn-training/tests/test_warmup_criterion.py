"""预热判据的语义护栏（`tests/helpers/warmup`，2026-11-02）。

钉的是**半不可见**的那件事：预热只在「本次会话真会用到它」时才做。判据曾经只看
`sys.modules`，于是「测试顶层 import 了」与「预热该跑」被绑在一起 —— 一旦把重导入改懒，
预热会在没有任何报错的情况下静默停掉，后果是每个 worker 各付一次 import + 首算子。

不依赖真 torch / 真 remote_dag：判据只看字串与文件内容，预热本身用假 thunk 替。

**注意判据模块要拿不存在的名字（`_NO_MODULE`）**：拿 "torch" 去断言「不该预热」在门禁里
必红 —— 那里 `torch` 本就在 `sys.modules`（测试模块顶层 import 过），OR 的兼容支路会成立。
要验的是**源码面那一路能单独成立**，所以把兼容支路按住。
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from tests.helpers import warmup

#: 判据里「不可能已在 `sys.modules`」的模块名：按住兼容支路，只验源码面。
_NO_MODULE = "no_such_module_xyz"


class _Item:
    """最小 item 替身：判据只读 `item.path`。"""

    def __init__(self, path: Path | None) -> None:
        self.path = path


def _item_for(dirpath: Path, name: str, body: str) -> _Item:
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return _Item(p)


def test_unused_things_stay_cold(tmp_path: Path) -> None:
    """源码里没提到 ⇒ 不预热（`test_forkdist` 那种 scratch 目录就是这种）。"""
    item = _item_for(tmp_path, "alice", "def test_a():\n    assert True\n")
    assert warmup._needed(_NO_MODULE, ("import torch", "from torch"), [item]) is False


def test_a_lazy_import_in_the_source_is_enough(tmp_path: Path) -> None:
    """**核心**：源码里 import 了（哪怕在函数里、此刻还没真的 import）⇒ 必须预热。"""
    item = _item_for(tmp_path, "bob", "def test_b():\n    import torch\n    assert torch\n")
    assert warmup._needed(_NO_MODULE, ("import torch", "from torch"), [item]) is True, (
        "改懒之后预热会静默失效 —— 判据必须能只看源码就成立"
    )


def test_the_from_form_also_counts(tmp_path: Path) -> None:
    item = _item_for(tmp_path, "carol", "from torch import nn\n")
    assert warmup._needed(_NO_MODULE, ("import torch", "from torch"), [item]) is True


def test_an_unreadable_item_is_not_a_failure(tmp_path: Path) -> None:
    """读不到文件 ⇒ 当「没提」，绝不让预热把门禁弄红。"""
    assert warmup._needed(_NO_MODULE, ("import torch",), [_Item(tmp_path / "missing")]) is False
    assert warmup._needed(_NO_MODULE, ("import torch",), [_Item(None)]) is False


def test_items_without_path_attribute_are_skipped() -> None:
    """测试替身/非文件 item 不能把判据炸掉。"""
    assert warmup._needed(_NO_MODULE, ("import torch",), [types.SimpleNamespace()]) is False


def test_the_loaded_module_is_still_a_fallback() -> None:
    """兼容支路：源码没提但人已经 import 进来了，也要预热（间接路径）。

    拿 `pytest` 验：跑测试的进程里它必定在 `sys.modules`，与运行机器无关。
    """
    assert warmup._needed("pytest", ("never-appears-in-any-source",), []) is True


def test_every_entry_declares_both_signals() -> None:
    """清单每项都要有判据模块**和**源码标记 —— 少一个就退回单信号。"""
    for name, fn, needed_by, markers in warmup.WARMUPS:
        assert callable(fn), name
        assert needed_by, f"{name} 缺判据模块"
        assert markers and all(isinstance(m, str) and m for m in markers), f"{name} 缺源码标记"


def test_warm_all_skips_the_unused_and_runs_the_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端（假 thunk）：跳过项不计入合计、命中项才跑。"""
    calls: list[str] = []
    monkeypatch.setattr(
        warmup,
        "WARMUPS",
        (
            ("假-没用到", lambda: calls.append("cold"), "no_such_module_xyz", ("import nothing",)),
            ("假-用到", lambda: calls.append("warm"), "no_such_module_xyz", ("import torch",)),
        ),
    )
    items = [_item_for(tmp_path, "dave", "def test_d():\n    import torch\n    assert torch\n")]
    warmup.warm_all(items)
    assert calls == ["warm"], calls
