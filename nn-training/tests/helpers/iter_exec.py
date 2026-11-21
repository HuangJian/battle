"""tests/helpers/iter_exec.py —— 把 `worker.iter_rollout.EXEC` 换成只覆盖关心字段的端口。

为什么要有它（`python.refactor.md` §P0-8 的主例）：执行面的六处接缝（bun 解析 / bun 版本 /
单局子进程 / 建池 / 半截产出清理）现在住一个 `NodeExec` 端口里。测试**整体替换端口**
（`iter_rollout.EXEC`，一个公共名字），而不是逐名 `monkeypatch.setattr(iter_rollout,
"resolve_bun", …)` / `… "_make_pool"` / `… "_run_one_game"` —— 后者把实现细节变成了事实上的
公开 API，而且**改实现就静默失效**（桩还挂在旧名字上，用例照绿）。

用法：

    stub_exec(monkeypatch, bun=sys.executable, ver="9.9.9-stub")
    stub_exec(monkeypatch, make_pool=lambda *a, **k: None)

多次调用**叠乘**：每次都基于**当前** `EXEC` 覆盖（`dataclasses.replace`），所以一个用例里可以
分几处改不同接缝，互不抹掉。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from worker import iter_rollout


def stub_exec(
    monkeypatch: Any,
    *,
    bun: str | None = None,
    ver: str | None = None,
    **over: Callable[..., Any],
) -> None:
    """把执行面端口换成替身：`bun` / `ver` 是「这轮用哪个 bun、版本读成什么」的快捷方式。

    `over` 直接按 `NodeExec` 的字段名覆盖（`run_game` / `make_pool` / `clean_attempt` /
    `clean_partial` / `resolve_bun` / `bun_version`），没给的字段沿用真实现。
    """
    fields: dict[str, Callable[..., Any]] = dict(over)
    if bun is not None:
        fields["resolve_bun"] = lambda _name="": bun
    if ver is not None:
        fields["bun_version"] = lambda _bun: ver
    monkeypatch.setattr(iter_rollout, "EXEC", replace(iter_rollout.EXEC, **fields))
