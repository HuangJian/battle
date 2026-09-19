"""test_course_push.py — 课程与 worker 节点**正交**：push 节点清单不按课程过滤。

2026-09-19 用户口径：「课程任务与 worker 节点互相正交！！！所有 worker 都可能接到在训的
课程任务！不管它是哪个课程的！」

⇒ 训练侧删掉了 `courses.<课>.push_node_url` 这层按课程过滤（P3-W1b 的遗留）：
`_gpu_push_nodes` 恒取**全部** enabled 的 `nodes[].gpu_push`，谁接到活由部署
（`rl.hub_push` + hub 队列）决定，而不是由科目名决定。

本文件钉三件事：① 清单与课程无关；② env `REMOTE_PUSH_NODE` 是**独占**的显式覆盖
（冒烟预演不能被真 GPU 节点抢走活）；③ 旧读取点 `_course_push_url` 不得回流。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

import rl.loop_steps as loop_steps
from rl.loop_steps import _gpu_push_nodes, _hub_push_opt_in


def _args(course_path: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(course_path=course_path)


def _cfg(nodes: list, courses: dict | None = None) -> dict:
    return {"nodes": nodes, "courses": courses or {}}


# ────────────────────────── _gpu_push_nodes ──────────────────────────


def test_all_enabled_gpu_push_nodes_are_candidates() -> None:
    nodes = [
        {"url": "https://a.example", "authKey": "k", "gpu_push": True, "enabled": True},
        {"url": "https://b.example", "authKey": "k", "gpu_push": True, "enabled": True},
    ]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        got = _gpu_push_nodes("tok")
    assert [n["url"] for n in got] == ["https://a.example", "https://b.example"]


def test_course_block_does_not_filter_the_node_list() -> None:
    """正交性尺子：课程块里**留着**旧的 `push_node_url` 也不影响清单（那个键已无读者）。"""
    nodes = [
        {"url": "https://a.example", "authKey": "k", "gpu_push": True},
        {"url": "https://b.example", "authKey": "k", "gpu_push": True},
    ]
    courses = {"s-dodge": {"push_node_url": "https://a.example"}}
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes, courses)
        got = _gpu_push_nodes("tok")
    assert [n["url"] for n in got] == ["https://a.example", "https://b.example"]


def test_no_push_node_reads_the_course_block_at_all() -> None:
    """任何课程上下文都不参与解析 ⇒ 调用签名里没有课程参数（形参围栏）。"""
    import inspect

    params = list(inspect.signature(_gpu_push_nodes).parameters)
    assert params == ["remote_token"]


def test_env_override_is_exclusive(monkeypatch) -> None:
    """env `REMOTE_PUSH_NODE`（冒烟预演）= 只推它：真 GPU 节点不得抢走预演的 job。"""
    monkeypatch.setenv("REMOTE_PUSH_NODE", "http://127.0.0.1:9999")
    nodes = [{"url": "https://a.example", "authKey": "k", "gpu_push": True, "enabled": True}]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        got = _gpu_push_nodes("tok")
    assert [n["url"] for n in got] == ["http://127.0.0.1:9999"]
    assert got[0]["authKey"] == "tok"  # 本机伪节点与 rl.remote_token 同源


def test_disabled_or_non_push_nodes_excluded() -> None:
    nodes = [
        {"url": "https://off.example", "authKey": "k", "gpu_push": True, "enabled": False},
        {"url": "https://pull.example", "authKey": "k"},
    ]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        assert _gpu_push_nodes("tok") == []


# ────────────────────────── 防回流 ──────────────────────────


def test_course_push_url_reader_is_gone() -> None:
    """按课程的 push 指针读取点必须不存在——它的存在就是「把某门课钉到某台机器」。"""
    assert not hasattr(loop_steps, "_course_push_url")


# ────────────────────────── hub 中介派发的开关 ──────────────────────────


def test_hub_push_defaults_on_when_unset() -> None:
    """用户口径「配了节点就默认走 hub 中介派发」：`rl.hub_push` 缺省 = 允许。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = {"rl": {}}
        assert _hub_push_opt_in() is True


def test_hub_push_can_be_turned_off_explicitly() -> None:
    """显式 `rl.hub_push: false` → 回直推节点（部署事实住全局一个键，不再按课程读）。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = {"rl": {"hub_push": False}}
        assert _hub_push_opt_in() is False


def test_hub_push_ignores_the_course_block() -> None:
    """课程块里的 `hub_push` 不再被读（课程与派发路径正交）。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = {"rl": {}, "courses": {"s-dodge": {"hub_push": False}}}
        assert _hub_push_opt_in() is True


def test_hub_push_survives_a_broken_config() -> None:
    """配置读不到 ⇒ 缺省（允许）——不炸训练；真生效还需 hub_url+token（resolve_hub_push 管）。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.side_effect = RuntimeError("boom")
        assert _hub_push_opt_in() is True
