"""test_wire_cf_tunnel.py — M1 隧道选项必须**真正写进** iteration 事件的 wire。

背景（2026-09-17 查出的真缺口）：`_wire_from_result` 原本读
`getattr(args, "remote_cf_protocol", None)`，但 CLI 从未声明这两个参数
（`cf_protocol` 在整个 python 侧只出现在那两行读取处）⇒ **`wire.protocol` /
`wire.edge_ip` 恒为 None**。后果是 `plan/remote-wire-remediation.plan.md` §1.4 的硬要求
「开关取值必须写进 iteration 事件（否则事后无法按选项分组统计）」没被满足：控制台能显示
「此刻生效值」，却回答不了「改用 http2 之后那几轮 vs 之前那几轮」。

本文件锁三件事：
  ① CLI 真的声明了这两个参数，且缺省取 rl-config（控制台启动时回写的正是这里）；
  ② 优先级 CLI > `courses.<stem>.cf_*` > `rl.cf_*` > None（与 `_course_push_url` 同口径：
     选项住 rl-config，**永不进 curricula**，D14 血缘）；
  ③ 读不到一律 None **不炸训练**（旧配置/旧 args/坏 config 都安全）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from rl.cli import build_argparser
from rl.loop_steps import _course_cf_tunnel

COURSE = "nn-training/curricula/s-dodge.jsonc"


def _args(course_path: str = "", **kw) -> types.SimpleNamespace:
    return types.SimpleNamespace(course_path=course_path, **kw)


def _cfg(rl: dict | None = None, courses: dict | None = None) -> dict:
    return {"rl": rl or {}, "courses": courses or {}}


# ────────────────────────── ① CLI 声明（缺口本体） ──────────────────────────


def test_argparser_declares_cf_tunnel_and_defaults_from_rl_config() -> None:
    """rl-config 的 rl.cf_*（控制台回写的键）必须成为 CLI 缺省——这正是缺口所在。"""
    ap = build_argparser("per-tick", {"cf_protocol": "http2", "cf_edge_ip": "4"})
    ns = ap.parse_args([])
    assert ns.remote_cf_protocol == "http2"
    assert ns.remote_cf_edge_ip == "4"


def test_argparser_defaults_are_empty_without_config() -> None:
    """未配 = 空串（不记），**不是** 'auto'——别把「没记录」伪装成一个合法取值。"""
    ns = build_argparser("per-tick", {}).parse_args([])
    assert ns.remote_cf_protocol == ""
    assert ns.remote_cf_edge_ip == ""


def test_explicit_cli_overrides_config_default() -> None:
    ap = build_argparser("per-tick", {"cf_protocol": "http2"})
    ns = ap.parse_args(["--remote-cf-protocol", "quic", "--remote-cf-edge-ip", "6"])
    assert ns.remote_cf_protocol == "quic"
    assert ns.remote_cf_edge_ip == "6"


def test_illegal_value_rejected_loudly() -> None:
    """choices 兜住非法值（写错协议名 = 启动即报错，而不是账本里记一个假值）。"""
    ap = build_argparser("per-tick", {})
    for bad in (["--remote-cf-protocol", "h3"], ["--remote-cf-edge-ip", "5"]):
        try:
            ap.parse_args(bad)
        except SystemExit:
            continue
        raise AssertionError(f"{bad} 未被 argparse 拒绝")


# ────────────────────────── ② 优先级 ──────────────────────────


def test_cli_value_wins_without_touching_config() -> None:
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg({"cf_protocol": "auto"}, {"s-dodge": {"cf_protocol": "quic"}})
        got = _course_cf_tunnel(_args(COURSE, remote_cf_protocol="http2", remote_cf_edge_ip="6"))
    assert got == ("http2", "6")
    assert not dc.load_dist_config.called, "CLI 已给值就不该再读盘（省 IO，避免误报）"


def test_course_override_beats_rl_block() -> None:
    """per-course 覆盖最贴近「这一课真正用的值」——rl.* 只是全局缺省。"""
    courses = {"s-dodge": {"cf_protocol": "quic", "cf_edge_ip": "6"}}
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg({"cf_protocol": "http2", "cf_edge_ip": "4"}, courses)
        assert _course_cf_tunnel(_args(COURSE)) == ("quic", "6")


def test_rl_block_used_when_course_has_no_override() -> None:
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg(
            {"cf_protocol": "http2", "cf_edge_ip": "4"}, {"s-dodge": {}}
        )
        assert _course_cf_tunnel(_args(COURSE)) == ("http2", "4")


def test_other_course_is_not_leaked() -> None:
    """别的课的覆盖不得串台（按 stem 查表，不是「取第一个」）。"""
    courses = {"other": {"cf_protocol": "quic"}}
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg({"cf_protocol": "http2"}, courses)
        assert _course_cf_tunnel(_args(COURSE)) == ("http2", None)


# ────────────────────────── ③ 降级：绝不炸训练 ──────────────────────────


def test_no_args_attributes_at_all_is_safe() -> None:
    """旧 args（没这两个属性）→ (None, None)，不 AttributeError。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg({"cf_protocol": "http2"})
        assert _course_cf_tunnel(_args("")) == (None, None)


def test_config_read_failure_is_silent() -> None:
    """读 rl-config 抛异常 = 记录缺失，不是训练故障（wire 字段允许 None）。"""
    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.side_effect = RuntimeError("boom")
        assert _course_cf_tunnel(_args(COURSE, remote_cf_protocol="http2")) == ("http2", None)


def test_no_course_path_skips_config_entirely() -> None:
    with patch("rl.loop_steps.dist_common") as dc:
        assert _course_cf_tunnel(_args("")) == (None, None)
        assert not dc.load_dist_config.called


def test_wire_from_result_records_resolved_values() -> None:
    """端到端锁：解析出的值进了 wire 子字典（不再恒 None）。"""
    from rl.loop_steps import _wire_from_result

    with patch("rl.loop_steps.dist_common") as dc:
        dc.load_dist_config.return_value = _cfg({"cf_protocol": "http2", "cf_edge_ip": "4"})
        proto, edge = _course_cf_tunnel(_args(COURSE))
    w = _wire_from_result({}, is_push=True, cfg={"protocol": proto, "edge_ip": edge, "slim": True})
    assert w["protocol"] == "http2"
    assert w["edge_ip"] == "4"
    assert w["slim"] is True
