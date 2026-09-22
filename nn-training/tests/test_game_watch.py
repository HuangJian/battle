"""test_game_watch.py — 单局看门狗的口径常量与四行日志（`remote/game_watch.py`，纯函数）。

为什么单独测这一层：rollout 与 eval 两条腿**必须逐字同口径**（软告警线、首次硬顶、重试倍数），
两份实现各自漂一格就会造出「同一台机器上 rollout 的 5s 就是 eval 的 30s」这种静默错口径。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import game_watch


def test_defaults_are_the_user_line() -> None:
    """默认口径 = 用户 2026-09-22 的判据：单局 >5s 肯定不正常。"""
    assert game_watch.SLOW_GAME_WARN_SEC == 5.0
    assert game_watch.DEFAULT_GAME_TIMEOUT_SEC == 5.0  # 首次尝试就按这条线杀
    assert game_watch.RETRY_TIMEOUT_FACTOR == 4.0  # 重试放宽（兜底尝试宁可多等）
    assert game_watch.GAME_MAX_ATTEMPTS == 3
    assert 0 < game_watch.GAME_POLL_SEC <= 1.0  # 轮询必须比告警线细，否则杀不到点上


def test_attempt_timeout_first_attempt_is_the_line_retry_is_relaxed() -> None:
    """首次 = 配置值；重试 = ×RETRY_TIMEOUT_FACTOR（未显式配置时）。"""
    base = game_watch.DEFAULT_GAME_TIMEOUT_SEC
    assert game_watch.attempt_timeout_sec(base, 1) == base
    assert game_watch.attempt_timeout_sec(base, 2) == base * game_watch.RETRY_TIMEOUT_FACTOR
    assert game_watch.attempt_timeout_sec(base, 3) == base * game_watch.RETRY_TIMEOUT_FACTOR


def test_attempt_timeout_explicit_config_wins_verbatim() -> None:
    """调用方显式给了上限 ⇒ 每次尝试都用它（配置说了算，不做解释、不放宽）。"""
    assert game_watch.attempt_timeout_sec(30.0, 1, explicit=True) == 30.0
    assert game_watch.attempt_timeout_sec(30.0, 3, explicit=True) == 30.0


def test_warn_is_redundant_only_when_cap_is_at_or_below_the_line() -> None:
    """软告警与硬顶同值（默认 5s=5s）时不该多打一行 WARN（超时行自己带局身份）。"""
    assert game_watch.warn_is_redundant(game_watch.DEFAULT_GAME_TIMEOUT_SEC) is True
    assert game_watch.warn_is_redundant(game_watch.SLOW_GAME_WARN_SEC) is True
    assert game_watch.warn_is_redundant(0.5) is True
    assert game_watch.warn_is_redundant(30.0) is False


def test_lines_name_the_game_and_the_cap() -> None:
    """四行日志都必须带局身份（`s3/d7`）——2026-09-22 那 651s 里最缺的就是这个。"""
    warn = game_watch.slow_warn_line("eval", "s3/d7", 6.2, 30.0, 1, "w0/rollout.log")
    assert "s3/d7" in warn and "6.2s" in warn and "第 1/3 次" in warn

    cap = game_watch.hard_cap_line("rollout", "s3/d7", 5.3, 5.0, "w0/rollout.log")
    assert "s3/d7" in cap and "硬顶 5s" in cap and "w0/rollout.log" in cap

    retry = game_watch.retry_line("rollout", "s3/d7", 2, "rollout 单局超时…", 20.0)
    assert "s3/d7" in retry and "2/3" in retry and "本次上限 20s" in retry


def test_game_time_summary_reports_distribution_slowest_and_retries() -> None:
    """轮末分布行：分位数 + ≥5s 计数 + 重试次数 + **最慢 3 局点名**（读数要能追溯）。"""
    items = [(0.4, "s0/d1"), (0.5, "s0/d2"), (0.6, "s0/d3"), (1.0, "s0/d4"), (9.0, "s3/d7")]
    line = game_watch.game_time_summary("rollout", items, retried=2)
    assert "5 局" in line and "p50=" in line and "max=9.00s" in line
    assert "≥5s 有 1 局" in line and "重试过的局 2 个" in line
    assert "s3/d7=9.00s" in line  # 最慢那局被点名（光有 p99 没法查是哪几局）


def test_game_time_summary_empty_is_not_a_crash() -> None:
    """零局也要有话说（别让诊断行自己成为失败点）。"""
    assert "没有跑成的局" in game_watch.game_time_summary("eval", [])
