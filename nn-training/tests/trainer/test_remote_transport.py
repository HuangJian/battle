"""test_remote_transport.py — `--remote-transport` 传输裁决（2026-09-15）。

背景：控制台 `local` preset 把本机 PPO 拆成了独立 worker（`remote_worker --poll`
本机 hub，2026-09-15）。控台必须**钉死 pull**，因为 `_remote_ppo` 的历史优先级是
「rl-config 里本课 gpu_push 节点 > hub」——某课用 push 跑过一次后
`courses.<课>.push_node_url` 就留在配置里，不钉死则 job 全被推去云机、本机 worker
永远领不到活，而日志看起来「训练正常」（最贵的那类错误）。

本文件覆盖两侧纯函数（run_rl 的 `resolve_transport` / BC 的 `resolve_transport`，后者 R3-4 后
住在 `trainer/bc_loop.py`）
与 argparse 接线（默认值 + choices），不碰 torch。
"""

from __future__ import annotations

import pytest

from trainer.loop_steps import REMOTE_TRANSPORTS, resolve_hub_push, resolve_transport
from trainer.loop_transport import resolve_route
from worker.cli import build_argparser

NODE = {"url": "https://gpu.example", "authKey": "k"}


# ────────────────────────── run_rl（trainer/loop_steps.resolve_transport） ──────────────────────────


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


def test_wire_diet_defaults_on_and_stays_rollbackable() -> None:
    """★ 2026-10-14 用户令「不需要 A/B，全部启用」：缺省就是 **1**（DECISIONS
    §2026-10-14-goalnn-wire-diet-enable-all），而回退旋钮 `rl.wire_diet: 0` 必须还在。

    为什么值得一条用例：缺省值是「全部启用」这道的**唯一实现点**，有人把它拨回 0 时
    现象是「字节默默变多」（而不是报错）—— 这条红线只能靠这里钉住。
    """
    ap = build_argparser("per-tick", {})
    assert ap.parse_args([]).wire_diet == 1
    # 一次性回退：rl-config 写 0 ⇒ 逐字节旧行为；命令行显式传参覆盖配置。
    assert build_argparser("per-tick", {"wire_diet": 0}).parse_args([]).wire_diet == 0
    assert ap.parse_args(["--wire-diet", "0"]).wire_diet == 0
    assert build_argparser("per-tick", {}).parse_args(["--wire-diet", "1"]).wire_diet == 1
    with pytest.raises(SystemExit):
        ap.parse_args(["--wire-diet", "2"])


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


def test_auto_with_hub_ready_has_no_direct_nodes() -> None:
    """auto + hub 就绪 ⇒ **只走 hub**，直推清单必须清空。

    2026-10-26 it72 事故（`1ce99b04f5aa4da8`）：`gpu_push` 节点同时是 hub 派发器的登记项
    （同一份 `rl-config.nodes[]`）⇒ 各自裁决会两条腿同时成立——发布带 `dispatch="push"`
    交给 hub，训练侧又直推同一份 job，且 `sess.transport` 只看节点（`push`）。直推 POST 的
    应答在隧道里丢了，训练侧卡在 `submit_job(timeout=600)` 的读超时里空等 600s；而 hub 早在
    worker 完成后 6s（21:15:02）就把结果落盘了——`job_result_accepted` 在账本里。
    """
    assert resolve_route("auto", "https://hub", "tok", [NODE], True) == ([], True)
    # hub 未就绪（auto 不成立）⇒ 历史直推一字不变；显式 push/hubpush 的语义也不动。
    assert resolve_route("auto", "", "tok", [NODE], True) == ([NODE], False)
    assert resolve_route("auto", "https://hub", "", [NODE], True) == ([NODE], False)
    assert resolve_route("push", "https://hub", "tok", [NODE], True) == ([NODE], False)
    assert resolve_route("hubpush", "https://hub", "tok", [NODE], True) == ([], True)


# ────────────────── BC（trainer/bc_loop.resolve_transport；R3-4 引擎化后移出 run_bc） ──────────────────


def test_bc_auto_keeps_historical_priority() -> None:
    from trainer import bc_loop

    assert (
        bc_loop.resolve_transport(
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
        bc_loop.resolve_transport(
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
    from trainer import bc_loop

    assert (
        bc_loop.resolve_transport(
            local=False,
            mode="pull",
            remote=True,
            push_url="https://gpu",
            hub_url="https://h",
            token="t",
        )
        == "hub"
    )


def test_bc_pull_needs_full_hub_triple() -> None:
    from trainer import bc_loop

    with pytest.raises(SystemExit, match="pull"):
        bc_loop.resolve_transport(
            local=False, mode="pull", remote=False, push_url="https://gpu", hub_url="", token=""
        )


def test_bc_push_needs_node_and_local_wins() -> None:
    from trainer import bc_loop

    with pytest.raises(SystemExit, match="push"):
        bc_loop.resolve_transport(
            local=False, mode="push", remote=True, push_url="", hub_url="https://h", token="t"
        )
    # --local 是显式意图，优先于任何 --remote-transport
    assert (
        bc_loop.resolve_transport(
            local=True,
            mode="pull",
            remote=True,
            push_url="https://gpu",
            hub_url="https://h",
            token="t",
        )
        == "local"
    )


def test_bc_no_transport_is_loud() -> None:
    from trainer import bc_loop

    with pytest.raises(SystemExit, match="无法确定传输"):
        bc_loop.resolve_transport(
            local=False, mode="auto", remote=False, push_url="", hub_url="", token=""
        )
