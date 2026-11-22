"""`BannerSpec` 的形状守卫（`python.refactor.md` §P0-3，批 F1，2026-11-22）。

批 F1：`remote/rollout_node._banner_lines`(10 个关键字 → 1 个 `BannerSpec`)。
调用面 4 处（`run` 里 1 处 + 用例 3 处）。

为什么值得动：横幅是**操作员判断「这台机器跑的是哪份代码、注册上没有」的唯一现场读数**。
`bun_version` 与 `code_hash` 是两个相邻身份串 —— 互换后横幅照样打印，只是**大声宣布错的版本**；
`url` / `auth` / `hub_url` 三个相邻串互换会把 authKey 粘进错的地址；`hub_configured` 与
`reg_state` 互换则把「已自动注册」与「还得手工写 nodes[]」说反（2026-11-18 DSW 现场把人送向
cloudflared 日志的那次就是这个方向）。

除形状外钉住三条语义：空 URL 时**不**打印会 400 的 `nodes[]` 片段、relay 模式怪 relay 不怪
cloudflared、`hub_configured + reg_state["url"]` 时打「已自动注册」。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any, cast

import pytest

from remote import rollout_node as rn

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: 旧 `_banner_lines` 的 10 个关键字参数。
FIELDS: list[tuple[str, object]] = [
    ("node_id", MISSING),
    ("url", MISSING),
    ("auth", MISSING),
    ("workers", MISSING),
    ("bun_version", MISSING),
    ("code_hash", MISSING),
    ("hub_configured", MISSING),
    ("reg_state", MISSING),
    ("channel", ""),
    ("hub_url", ""),
]


def _spec(**over: Any) -> rn.BannerSpec:
    base: dict[str, Any] = {
        "node_id": "n1",
        "url": "https://x.trycloudflare.com",
        "auth": "sekret",
        "workers": 4,
        "bun_version": "1.4.4",
        "code_hash": "a" * 40,
        "hub_configured": True,
        "reg_state": {"url": "https://x.trycloudflare.com"},
    }
    base.update(over)
    return rn.BannerSpec(**base)


def test_field_table_matches_the_old_signature() -> None:
    assert _table(rn.BannerSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(rn.BannerSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[-1].name, "x")


def test_entry_takes_exactly_one_spec() -> None:
    # 私有名先绑局部再断言（G5：「断言里不读私有量」）。
    entry = rn._banner_lines
    assert list(inspect.signature(entry).parameters) == ["spec"]


def test_old_keyword_form_is_rejected() -> None:
    call = cast(Callable[..., object], rn._banner_lines)
    with pytest.raises(TypeError):
        call(node_id="n1", url="u", auth="a")


def test_registered_banner_names_the_node() -> None:
    """`hub_configured` 且 `reg_state["url"]` 在场 ⇒ 必须打「已自动注册 : id=<node>」。"""
    lines = rn._banner_lines(_spec())
    assert any(ln.startswith("★ 已自动注册 : id=n1") for ln in lines), lines


def test_no_url_does_not_print_a_nodes_fragment() -> None:
    """空 URL ⇒ 不打印那句必 400 的 `nodes[]`（复印粘贴只会换来一个 400）。"""
    lines = rn._banner_lines(_spec(url="", hub_configured=False, reg_state={}))
    assert not any(ln.lstrip().startswith('{"nodes"') for ln in lines), lines


def test_relay_mode_blames_relay_not_cloudflared() -> None:
    """relay 模式根本没有 cloudflared 进程：指「看 cloudflared 日志」是把人往错方向送。"""
    lines = rn._banner_lines(
        _spec(url="", hub_configured=False, reg_state={}, channel=rn.LINK_RELAY)
    )
    assert "\n".join(lines).lower().count("cloudflared") == 0, lines
