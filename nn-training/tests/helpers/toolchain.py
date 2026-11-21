"""tests/helpers/toolchain.py —— 把某个模块的 `TOOLCHAIN` 端口换成「版本读成 X」的替身。

为什么要有它（`python.refactor.md` §P0-8）：训练机侧三个模块（`trainer/batch_runner` /
`trainer/eval_dispatch` / `trainer/dispatch`）的版本门探测原先各自绑一个模块级 `bun_version`，
测试只能 `monkeypatch.setattr(mod, "bun_version", …)` —— 模块名成了事实 API。现在调用点读
模块全局的 `TOOLCHAIN`（`common.proc.Toolchain` 的实例），测试只替换**端口里的一项**：

    stub_toolchain(monkeypatch, br, "9.9.9")

`monkeypatch.setattr` 打的是**读它的那个模块**：三家绑的是同一个默认实例，但替换只重绑一家。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any


def stub_toolchain(monkeypatch: Any, module: Any, version: str) -> None:
    """把 `module.TOOLCHAIN` 的 `bun_version` 读成 `version`（其余字段沿用真实现）。"""
    monkeypatch.setattr(
        module, "TOOLCHAIN", replace(module.TOOLCHAIN, bun_version=lambda _bun: version)
    )
