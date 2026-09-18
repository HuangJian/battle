"""test_remote_transport.py — `--remote-transport` 传输裁决（2026-09-15）。

背景：控制台 `local` preset 把本机 PPO 拆成了独立 worker（`remote_worker --poll`
本机 hub，2026-09-15）。控台必须**钉死 pull**，因为 `_remote_ppo` 的历史优先级是
「rl-config 里本课 gpu_push 节点 > hub」——某课用 push 跑过一次后
`courses.<课>.push_node_url` 就留在配置里，不钉死则 job 全被推去云机、本机 worker
永远领不到活，而日志看起来「训练正常」（最贵的那类错误）。

本文件覆盖两侧纯函数（run_rl 的 `resolve_transport` / run_bc 的 `resolve_transport`）
与 argparse 接线（默认值 + choices），不碰 torch。
"""

from __future__ import annotations

import pytest

from rl.cli import build_argparser
from rl.loop_steps import REMOTE_TRANSPORTS, resolve_hub_push, resolve_transport

NODE = {"url": "https://gpu.example", "authKey": "k"}


# ────────────────────────── run_rl（rl/loop_steps.resolve_transport） ──────────────────────────


def test_rl_auto_keeps_historical_priority() -> None:
    """auto = 历史行为零变化：config/env 的 gpu_push 节点原样生效。"""
    assert resolve_transport("auto", "https://hub", "tok", [NODE]) == [NODE]
    assert resolve_transport("auto", "https://hub", "tok", []) == []


def test_rl_pull_forces_hub_even_with_gpu_push_nodes() -> None:
    """钉死 pull：即使配置里有本课 gpu_push 节点也不推云机（本机 worker 场景）。"""
    assert resolve_transport("pull", "http://127.0.0.1:8787", "tok", [NODE]) == []


def test_rl_pull_without_hub_or_token_is_loud() -> None:
    with pytest.raises(SystemExit, match="pull"):
        resolve_transport("pull", "", "tok", [NODE])
    with pytest.raises(SystemExit, match="pull"):
        resolve_transport("pull", "http://127.0.0.1:8787", "", [NODE])


def test_rl_push_requires_nodes() -> None:
    assert resolve_transport("push", "", "tok", [NODE]) == [NODE]
    with pytest.raises(SystemExit, match="push"):
        resolve_transport("push", "https://hub", "tok", [])


def test_rl_unknown_mode_is_loud() -> None:
    with pytest.raises(SystemExit, match="remote-transport"):
        resolve_transport("pullx", "https://hub", "tok", [])


def test_rl_argparser_wires_the_single_decision_knob() -> None:
    """argparse：默认 auto（可被 rl-config `remote_transport` 覆盖）+ 四值封闭。"""
    ap = build_argparser("per-tick", {})
    args = ap.parse_args([])
    assert args.remote_transport == "auto"
    assert ap.parse_args(["--remote-transport", "pull"]).remote_transport == "pull"
    assert ap.parse_args(["--remote-transport", "hubpush"]).remote_transport == "hubpush"
    assert set(REMOTE_TRANSPORTS) == {"auto", "pull", "push", "hubpush"}
    with pytest.raises(SystemExit):
        ap.parse_args(["--remote-transport", "nope"])
    # rl-config 提供默认值时以配置为准（历史键风格 _d(name, fallback)）
    ap2 = build_argparser("per-tick", {"remote_transport": "pull"})
    assert ap2.parse_args([]).remote_transport == "pull"


# ────────────────────────── hub 中介推送（resolve_hub_push） ──────────────────────────


def test_hubpush_transport_forces_it_and_is_loud_without_hub() -> None:
    """`hubpush` 无条件走 hub；缺 hub_url/token 响亮拒绝（绝不静默回落 pull
    ——「配置写错了但训练看着正常」是本仓最贵的一类错误）。"""
    assert resolve_hub_push("hubpush", "https://hub", "tok", opt_in=False) is True
    for hub_url, token in (("", "tok"), ("https://hub", "")):
        with pytest.raises(SystemExit, match="hubpush"):
            resolve_hub_push("hubpush", hub_url, token, opt_in=False)


def test_push_and_pull_never_go_through_hub() -> None:
    """显式 `push`/`pull` 压过配置：直推云机是本机伪 GPU/无 hub 现场的唯一活路。"""
    assert resolve_hub_push("push", "https://hub", "tok", opt_in=True) is False
    assert resolve_hub_push("pull", "https://hub", "tok", opt_in=True) is False


def test_auto_uses_hub_push_only_when_opted_in_and_hub_ready() -> None:
    """auto：`courses.<课>.hub_push` / `rl.hub_push` 开 **且** hub_url+token 齐备才切；
    否则保持历史行为（gpu_push 节点直推）——旧部署一行不改。"""
    assert resolve_hub_push("auto", "https://hub", "tok", opt_in=True) is True
    assert resolve_hub_push("auto", "https://hub", "tok", opt_in=False) is False
    assert resolve_hub_push("auto", "", "tok", opt_in=True) is False
    assert resolve_hub_push("auto", "https://hub", "", opt_in=True) is False


def test_hubpush_has_no_direct_nodes() -> None:
    """hubpush 下训练侧不直连节点：即使 config 里有本课 gpu_push 也交回空清单
    （空清单 = 走 `wait_job` 等 hub 回传，不是「推给全部节点」）。"""
    assert resolve_transport("hubpush", "https://hub", "tok", [NODE]) == []


# ────────────────────────── run_bc（run_bc.resolve_transport） ──────────────────────────


def test_bc_auto_keeps_historical_priority() -> None:
    import run_bc

    assert (
        run_bc.resolve_transport(
            local=False,
            mode="auto",
            remote=True,
            push_url="https://gpu",
            hub_url="https://hub",
            token="tok",
        )
        == "push"
    )
    assert (
        run_bc.resolve_transport(
            local=False,
            mode="auto",
            remote=True,
            push_url="",
            hub_url="https://hub",
            token="tok",
        )
        == "hub"
    )


def test_bc_pull_overrides_configured_push_url() -> None:
    """--remote-transport pull 压过 courses.<课>.push_node_url（本地 worker 的唯一活路）。"""
    import run_bc

    assert (
        run_bc.resolve_transport(
            local=False, mode="pull", remote=True, push_url="https://gpu", hub_url="https://h", token="t"
        )
        == "hub"
    )


def test_bc_pull_needs_full_hub_triple() -> None:
    import run_bc

    with pytest.raises(SystemExit, match="pull"):
        run_bc.resolve_transport(
            local=False, mode="pull", remote=False, push_url="https://gpu", hub_url="", token=""
        )


def test_bc_push_needs_node_and_local_wins() -> None:
    import run_bc

    with pytest.raises(SystemExit, match="push"):
        run_bc.resolve_transport(
            local=False, mode="push", remote=True, push_url="", hub_url="https://h", token="t"
        )
    # --local 是显式意图，优先于任何 --remote-transport
    assert (
        run_bc.resolve_transport(
            local=True, mode="pull", remote=True, push_url="https://gpu", hub_url="https://h", token="t"
        )
        == "local"
    )


def test_bc_no_transport_is_loud() -> None:
    import run_bc

    with pytest.raises(SystemExit, match="无法确定传输"):
        run_bc.resolve_transport(
            local=False, mode="auto", remote=False, push_url="", hub_url="", token=""
        )
