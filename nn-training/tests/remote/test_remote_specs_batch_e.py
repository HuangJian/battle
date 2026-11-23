"""批 E 三份 spec 的形状护栏（`python.refactor.md` §P0-3 批 E）。

三处入口原本各收一串相邻的「身份 / 哈希 / 浮点」参数，互换一格都不抛、只是把事做错：

  · `job_round._prefetch_fill`(10) —— `depth` 与 `unpack_n` 是两个相邻 int（取几份 vs 解包前几份）；
  · `node_upgrade.request_upgrade_guarded`(9) —— `ping_hash` / `expected_hash` 是去重键的两栏，
    互换则去重恒不命中（连环杀）或恒命中（永不再发）；`timeout` 与 `cooldown_sec` 是两个 float
    （HTTP 预算 vs 去重窗）；
  · `rollout_node._try_register_once`(9) —— `url` / `auth` / `hub_url` / `hub_token` 是四个相邻地址与
    凭据串（互换 `auth` 与 `hub_token` = 把本节点密钥发去 hub）。

这里不重复已有的行为用例，只钉「字段表 / frozen + kw_only / 入口只吃一份 spec / 关键格各就各位」。
"""

from __future__ import annotations

import dataclasses
import inspect
import threading
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest

import remote.job_round as JR
import remote.node_upgrade as nu
import remote.rollout_node as rn

SPECS = {
    "PrefetchFillSpec": JR.PrefetchFillSpec,
    "UpgradeAsk": nu.UpgradeAsk,
    "RegisterAsk": rn.RegisterAsk,
}

FIELDS = {
    "PrefetchFillSpec": [
        "base_url",
        "token",
        "store",
        "stop",
        "worker_id",
        "role",
        "depth",
        "unpack_n",
        "skip",
        "log",
    ],
    "UpgradeAsk": [
        "nid",
        "url",
        "auth_key",
        "branch",
        "ping_hash",
        "timeout",
        "dirty",
        "expected_hash",
        "cooldown_sec",
    ],
    "RegisterAsk": [
        "url",
        "auth",
        "node_id",
        "workers",
        "label",
        "hub_url",
        "hub_token",
        "log",
        "urlopen",
    ],
}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_field_table(name: str) -> None:
    assert [f.name for f in dataclasses.fields(SPECS[name])] == FIELDS[name]


def _sample(name: str) -> Any:
    """一份最小可构造的 spec（三个入口都只读字段，不做校验）。"""
    if name == "PrefetchFillSpec":
        return JR.PrefetchFillSpec(
            base_url="", token="", store=cast("JR.PrefetchStore", _Store()), stop=threading.Event()
        )
    if name == "UpgradeAsk":
        return nu.UpgradeAsk(nid="n", url="", auth_key="", branch="", ping_hash="")
    return rn.RegisterAsk(
        url="", auth="", node_id="n", workers=1, label="", hub_url="", hub_token="",
        log=lambda _m: None,
    )


@pytest.mark.parametrize("name", sorted(SPECS))
def test_spec_is_frozen_and_keyword_only(name: str) -> None:
    assert all(f.kw_only for f in dataclasses.fields(SPECS[name]))
    spec = _sample(name)
    field = FIELDS[name][0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, "changed")


@pytest.mark.parametrize(
    ("func", "cls"),
    [
        (JR._prefetch_fill, "PrefetchFillSpec"),
        (nu.request_upgrade_guarded, "UpgradeAsk"),
        (rn._try_register_once, "RegisterAsk"),
    ],
)
def test_entry_takes_one_spec_only(func: Callable[..., object], cls: str) -> None:
    params = list(inspect.signature(func).parameters.values())
    assert [p.name for p in params] == ["spec"], [p.name for p in params]
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    with pytest.raises(TypeError):
        func(**{"no_such_field": 1})


# ── 关键格各就各位 ────────────────────────────────────────────────────────────


class _Store:
    """只够 `_prefetch_fill` 走到 peek 的暂存器桩（自停判据要 `stats()`）。"""

    def stats(self) -> dict[str, int]:
        return {"prefetched": 3, "taken": 2, "white_bytes": 0}

    def held(self) -> set[str]:
        return set()


def test_prefetch_depth_lands_in_the_peek_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _peek(base_url: str, token: str, *, worker_id: str, role: str, n: int, log: Any) -> list:
        seen.update(base_url=base_url, token=token, worker_id=worker_id, role=role, n=n)
        raise KeyboardInterrupt  # 第一拍就退：填充器只捕 Exception

    monkeypatch.setattr(JR, "peek_jobs", _peek)
    spec = JR.PrefetchFillSpec(
        base_url="http://hub",
        token="tok",
        store=cast("JR.PrefetchStore", _Store()),
        stop=threading.Event(),
        worker_id="wkr",
        role="rl",
        depth=4,
        unpack_n=9,
    )
    with pytest.raises(KeyboardInterrupt):
        JR._prefetch_fill(spec)
    assert seen == {
        "base_url": "http://hub",
        "token": "tok",
        "worker_id": "wkr",
        "role": "rl",
        "n": 4,  # 是 depth 而不是 unpack_n
    }


def test_upgrade_ask_keeps_the_dedup_key_and_both_floats_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def _request(url: str, auth_key: str, branch: str, *, timeout: float) -> bool:
        calls.append({"url": url, "auth_key": auth_key, "branch": branch, "timeout": timeout})
        return True

    monkeypatch.setattr(nu, "request_upgrade", _request)
    monkeypatch.setattr(nu, "is_self_node", lambda *_a: False)
    nu.reset_restart_state()

    def _ask(**kw: Any) -> nu.UpgradeAsk:
        base: dict[str, Any] = {
            "nid": "n1",
            "url": "http://node:1",
            "auth_key": "AUTH",
            "branch": "goal-nn",
            "ping_hash": "agent-1",
            "dirty": [],
            "timeout": 1.5,
            "expected_hash": "expect-1",
            "cooldown_sec": 900.0,
        }
        base.update(kw)
        return nu.UpgradeAsk(**base)

    assert nu.request_upgrade_guarded(_ask()) == (True, "restart-requested")
    assert calls == [
        {"url": "http://node:1", "auth_key": "AUTH", "branch": "goal-nn", "timeout": 1.5}
    ], "三栏身份/分支与 HTTP 预算各自落位"
    # 冷却窗（900s）内、同一 (agent hash, 期望 hash) ⇒ dedup；期望 hash 变 ⇒ 放行。
    assert nu.request_upgrade_guarded(_ask())[1] == "dedup"
    assert nu.request_upgrade_guarded(_ask(expected_hash="expect-2"))[1] == "restart-requested"
    # agent hash 变 ⇒ 也放行（键的两栏都真正参与）。
    assert nu.request_upgrade_guarded(_ask(ping_hash="agent-2"))[1] == "restart-requested"
    assert len(calls) == 3


def test_register_ask_keeps_four_address_slots_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[Any, ...]] = []
    posted: list[tuple[Any, ...]] = []

    def _payload(node_id: str, url: str, auth: str, workers: int, label: str) -> Mapping[str, Any]:
        built.append((node_id, url, auth, workers, label))
        return {"ok": True}

    def opener(*_a: Any, **_k: Any) -> Any:  # 只验「同一个对象被原样交给 register」
        return None

    def _register(urlopen: Any, hub_url: str, hub_token: str, payload: Any, log: Any) -> dict:
        posted.append((urlopen, hub_url, hub_token, payload))
        return {"ok": True}

    monkeypatch.setattr(rn, "build_register_payload", _payload)
    monkeypatch.setattr(rn, "register", _register)
    spec = rn.RegisterAsk(
        url="https://tunnel.example",
        auth="NODE_KEY",
        node_id="mac",
        workers=3,
        label="mac-label",
        hub_url="http://hub:8787",
        hub_token="HUB_TOKEN",
        log=lambda _m: None,
        urlopen=opener,
    )
    out = rn._try_register_once(spec)
    assert out == {"ok": True}
    assert built == [("mac", "https://tunnel.example", "NODE_KEY", 3, "mac-label")]
    assert posted == [(opener, "http://hub:8787", "HUB_TOKEN", {"ok": True})]


def test_empty_url_short_circuits_before_any_hub_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rn, "register", lambda *_a, **_k: pytest.fail("空 URL 不得发包"))
    out = rn._try_register_once(
        rn.RegisterAsk(
            url="",
            auth="NODE_KEY",
            node_id="mac",
            workers=1,
            label="l",
            hub_url="http://hub:8787",
            hub_token="HUB_TOKEN",
            log=lambda _m: None,
        )
    )
    assert out["kind"] == "nourl" and out["ok"] is False
