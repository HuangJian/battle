"""test_conftest_check_guard.py — 自证 conftest 的 `_no_silent_check_failures` 真会变红。

背景（2026-09-26）：一批从 standalone 脚本迁来的用例用模块级 `FAILS: list[str]` +
`check()` 累积失败，却只有脚本入口 `main()` 才 `sys.exit(1 if FAILS else 0)` —— 在 pytest 下
`check()` 只追加、无人断言（全仓 `assert not FAILS` 曾零命中、9 个文件无 autouse 守卫）
⇒ 整批用例**永远绿**。补上 conftest 的 autouse fixture 后，本文件证明它真在工作：
守卫一旦被改坏（不再抛错 / 不再按模块取 FAILS / 不再 autouse），这里立刻红 ——
否则「静默绿」这个事故会以另一种形式回来。

（顺带记录首次启用时它揭出的三处静默失败：`test_terminal_stats` 的 by_stage 去重期望、
`e2e/test_run_rl_m1` 两处 kickstart 的 `0.5**30` —— 都是归零机制落地后未同步的旧断言。）
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conftest

#: 本模块声明了 FAILS（但不写 check()）—— 用于证明夹具对「声明了 FAILS 的模块」自动生效。
FAILS: list[str] = []


def _enter(module: object):
    """手动驱动夹具生成器（绕过 pytest 夹具协议，取最内层的生成器函数）。"""
    raw = inspect.unwrap(conftest._no_silent_check_failures)
    gen = raw(SimpleNamespace(module=module))
    next(gen)  # 走到 yield：记录的基线
    return gen


def test_guard_is_autouse(request) -> None:
    """夹具必须**自动**挂到用例上：本文件没显式请求它，却应在 fixturenames 里。"""
    assert "_no_silent_check_failures" in request.fixturenames, "夹具不是 autouse（或名字被改）"


def test_guard_raises_when_fails_grows() -> None:
    """FAILS 增长的用例必须变红，且报错文本带上失败原因。"""
    mod = SimpleNamespace(FAILS=[])
    gen = _enter(mod)
    mod.FAILS.append("boom: 用例真实失败")
    with pytest.raises(AssertionError, match="boom: 用例真实失败"):
        next(gen)


def test_guard_silent_when_fails_unchanged() -> None:
    """FAILS 没涨 ⇒ 正常结束（不误伤）。"""
    mod = SimpleNamespace(FAILS=[])
    gen = _enter(mod)
    with pytest.raises(StopIteration):
        next(gen)


def test_guard_ignores_modules_without_fails() -> None:
    """没有模块级 FAILS 的用例（绝大多数）不得被误伤。"""
    gen = _enter(SimpleNamespace())
    with pytest.raises(StopIteration):
        next(gen)


def test_guard_only_counts_this_tests_entries() -> None:
    """只比对**增量**：基线之前就有的条目不算本次用例的失败。"""
    mod = SimpleNamespace(FAILS=["先前用例留下的失败"])
    gen = _enter(mod)
    with pytest.raises(StopIteration):
        next(gen)


def test_guard_ignores_non_list_fails() -> None:
    """`FAILS` 同名但不是 list（外部库模块）⇒ 不介入。"""
    gen = _enter(SimpleNamespace(FAILS="not a list"))
    with pytest.raises(StopIteration):
        next(gen)


def test_tmp_hooks_are_registered_once(request) -> None:
    """tmp 清理/失败标记 hook 每进程只能有一份实现（2026-11-11）。

    e2e/conftest.py 曾把 `pytest_runtest_makereport` / `pytest_sessionfinish`
    import 进自己的命名空间 —— pytest 把 conftest 模块的每个 `pytest_*` 属性都注册成
    hook 实现，于是全量门禁（tests/ + e2e/）里每个 hook 跑两遍：两个 detached 清理
    进程抢删同一份清单、各自 skip 掉对方已删的目录，删除预算一用完就欠账（实测残留
    6116 个 tmp 目录）。按 plugin 名钉死「只许根 conftest 一份」。
    """
    for hook_name in ("pytest_sessionfinish", "pytest_runtest_makereport"):
        impls = getattr(request.config.pluginmanager.hook, hook_name).get_hookimpls()
        names = [i.plugin_name for i in impls if "conftest" in i.plugin_name]
        assert len(names) == 1, f"{hook_name} 注册了多份：{names}"


def test_tmp_dir_for_does_not_create_the_directory() -> None:
    """路径计算不许建目录：autouse 隔离每用例都调它，建了就等于恢复空目录垃圾。"""
    node = SimpleNamespace(nodeid="tests/fake::test_lazy")
    d = conftest.tmp_dir_for(SimpleNamespace(node=node))
    assert not d.exists(), f"tmp_dir_for 建了目录：{d}"
