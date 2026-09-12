"""test_course_push.py — P3-W1b：gpu_push 节点清单按课过滤。

plan: `plan/multi-course-parallel-training.md`（P3-W1b、F-B5）。

- `push_node_url` 为空 → 沿用旧逻辑（全取；默认行为零变化）。
- 非空 → 只取 URL 匹配项（N:1 共享天然成立）；env REMOTE_PUSH_NODE 永远保留。
- 非空但匹配 0 个 → 调用方响亮失败（本文件只锁 helper 语义；WARN + manifest
  打标在 loop_steps 调用点，不抛异常）。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from rl.loop_steps import _course_push_url, _gpu_push_nodes


def _args(course_path: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(course_path=course_path)


def _cfg(nodes: list, courses: dict | None = None) -> dict:
    return {"nodes": nodes, "courses": courses or {}}


# ────────────────────────── _gpu_push_nodes ──────────────────────────


def test_empty_push_url_keeps_legacy_all() -> None:
    """空 push_node_url = 全取（默认行为零变化）。"""
    nodes = [
        {"url": "https://a.example", "authKey": "k", "gpu_push": True, "enabled": True},
        {"url": "https://b.example", "authKey": "k", "gpu_push": True, "enabled": True},
    ]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        got = _gpu_push_nodes("tok", "")
    assert [n["url"] for n in got] == ["https://a.example", "https://b.example"]


def test_push_url_filters_to_match_only() -> None:
    """非空只取匹配项；同 URL 多课同取（N:1 共享）。"""
    nodes = [
        {"url": "https://a.example/", "authKey": "k", "gpu_push": True},
        {"url": "https://b.example", "authKey": "k", "gpu_push": True},
    ]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        got = _gpu_push_nodes("tok", "https://a.example")
    assert [n["url"] for n in got] == ["https://a.example"]


def test_push_url_no_match_returns_empty() -> None:
    """非空零匹配 → 空清单（调用方 WARN + manifest 打标，不抛异常）。"""
    nodes = [{"url": "https://a.example", "authKey": "k", "gpu_push": True}]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        assert _gpu_push_nodes("tok", "https://zzz.example") == []


def test_env_injection_survives_filter(monkeypatch) -> None:
    """env REMOTE_PUSH_NODE 是显式冒烟覆盖，不受课程过滤影响。"""
    monkeypatch.setenv("REMOTE_PUSH_NODE", "http://127.0.0.1:9999")
    nodes = [{"url": "https://a.example", "authKey": "k", "gpu_push": True}]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        got = _gpu_push_nodes("tok", "https://zzz.example")
    assert [n["url"] for n in got] == ["http://127.0.0.1:9999"]


def test_disabled_or_non_push_nodes_excluded() -> None:
    nodes = [
        {"url": "https://off.example", "authKey": "k", "gpu_push": True, "enabled": False},
        {"url": "https://pull.example", "authKey": "k"},
    ]
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(nodes)
        assert _gpu_push_nodes("tok", "") == []


# ────────────────────────── _course_push_url ──────────────────────────


def test_no_course_means_empty() -> None:
    assert _course_push_url(_args("")) == ""


def test_reads_courses_block_by_stem() -> None:
    courses = {"s-dodge": {"push_node_url": "https://w.example/"}}
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg([], courses)
        assert _course_push_url(_args("nn-training/curricula/s-dodge.jsonc")) == "https://w.example"
        assert _course_push_url(_args("nn-training/curricula/s1.jsonc")) == ""
