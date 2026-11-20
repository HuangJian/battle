"""test_conftest_hooks.py — 根 `conftest.py` 的共享 hook 契约。

历史（2026-09-26）：这里曾自证 `_no_silent_check_failures`（模块级 `check()`/`FAILS`
的静默绿守卫）。2026-11-19 把 9 个脚本壳文件的 `check()` 全部换成真 `assert`
（`python.refactor.md` §P0-8 第三项）后，那个 autouse 夹具与对应的 5 条自证用例一起退役；
本文件只剩两条仍然有效的 hook 契约。
"""

from __future__ import annotations

from types import SimpleNamespace

import conftest


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
