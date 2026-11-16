"""replay 语料转换器的运行时选择：**Bun 优先**，Bun 缺失才回落 Node，两者皆无 = 响亮失败。

项目纪律（用户 2026-11-16 明确）：一律优先 bun —— 所有环境开工前都会装 bun；
只有 bun 装不上时才用环境预装的 node（<1%）。plan/replay-corpus.plan.md D5 同向。

回归背景：2026-11-16 前实现是 `for name in ("node", "bun")`（node 优先），docstring 却写
「Node 优先（bundle 目标），Bun 兜底」。Kaggle/Colab 两者都装 ⇒ 生产稳定挑到 Node，
云机实测换算 22.1(bun) → 25.4(node) µs/tick，白丢 1.15×。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import replay_corpus as rc


def _fake_which(available: dict[str, str]):
    """把 shutil.which 换成查表：命中返回路径，未命中返回 None。"""

    def which(name: str) -> str | None:
        return available.get(name)

    return which


def test_prefers_bun_when_both_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rc.shutil, 'which', _fake_which({'bun': '/root/.bun/bin/bun', 'node': '/tools/node/bin/node'})
    )
    assert rc.resolve_js_runtime() == '/root/.bun/bin/bun'


def test_falls_back_to_node_when_bun_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc.shutil, 'which', _fake_which({'node': '/tools/node/bin/node'}))
    assert rc.resolve_js_runtime() == '/tools/node/bin/node'


def test_raises_protocol_error_when_neither_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc.shutil, 'which', _fake_which({}))
    with pytest.raises(ProtocolError):
        rc.resolve_js_runtime()


def test_probe_order_is_bun_then_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接锁探测顺序，防止再被写成 node 优先。"""
    seen: list[str] = []

    def which(name: str) -> str | None:
        seen.append(name)
        return f'/usr/bin/{name}'

    monkeypatch.setattr(rc.shutil, 'which', which)
    rc.resolve_js_runtime()
    # 两个都装时 bun 命中即返回，node 连探测都不该发生。
    assert seen == ['bun']


def test_convert_fallback_flag_counts_by_primary_runtime() -> None:
    """`convert_fallback` 以 bun 为主：bun=0，其余（node/未知）=1。

    同一次改动里的配套读数——只改探测顺序不改这里，正常 bun 转换会被记成 fallback=1。
    """
    assert rc.convert_fallback_flag('bun') == 0
    assert rc.convert_fallback_flag('bun.exe') == 0
    assert rc.convert_fallback_flag('node') == 1
    assert rc.convert_fallback_flag('python3.13') == 1
