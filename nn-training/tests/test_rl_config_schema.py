"""`rl_config_schema.py` 契约（plan/rl-config-cleanup.plan.md §3.4、E8）。

四条要钉住的性质：
  · **只告警不拒**：未知 / 已退役键只出现在返回列表里，绝不抛（未知键 ≠ 训练起不来）；
  · **已退役比未知更具体**：`rl.stream` 这类给的是「已退役 + 原因」，不是泛泛的「未知键」；
  · **自由形状段不下钻**：`nodes` / `courses` / `rl.remote_hubs` / `rl.intent` / `rl.goal`
    是每课/每节点/每模式自定的，键名不在白名单里也**不算**未知；
  · **接线**：`run_rl` 启动时真的把告警打进日志（E8 的「塞假键 ⇒ 启动日志出现告警」）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import rl_config_schema as S

NN_ROOT = Path(__file__).resolve().parent.parent


def _clean_cfg() -> dict:
    """今天（清洗后）真机 rl-config 的形状——应零告警。"""
    return {
        "version": 1,
        "policy": {"taskTimeoutSec": 900, "queueWindowSec": 1800, "streamKlCap": 0.18},
        "nodes": [{"id": "self", "url": "http://127.0.0.1:8443", "authKey": "k"}],
        "rl": {
            "hub_port": 8787,
            "agent_port": 8443,
            "mb": 512,
            "workers": 8,
            "remote_token": "t",
            "remote_hubs": {"x1-rebirth": "http://u"},
            "rollout_src": "local",
        },
        "courses": {"x20-dodge-l2a": {"rollout_src": "run", "run_iters": -1}},
    }


def test_clean_config_has_no_warnings() -> None:
    assert S.check_rl_config(_clean_cfg()) == []


def test_retired_keys_are_flagged_with_a_reason() -> None:
    """已退役键给「已退役 + 原因」，而不是泛泛的未知——这是 §3.1 那些删除的可回归钉子。"""
    cfg = {
        "intent_rl": {"iters": 0},
        "policy": {
            "upgradeBranch": "",
            "minDiskFreeMB": 2048,
            "streamKlCapIntent": 0.5,
            "streamWaveGamesIntent": 200,
        },
        "rl": {"stream": 0, "double_buffer": 0, "precollect_early": 0},
    }
    out = " | ".join(S.check_rl_config(cfg))
    for dotted in (
        "intent_rl",
        "policy.upgradeBranch",
        "policy.minDiskFreeMB",
        "policy.streamKlCapIntent",
        "policy.streamWaveGamesIntent",
        "rl.stream",
        "rl.double_buffer",
        "rl.precollect_early",
    ):
        assert dotted in out, f"{dotted} 未被点出"
    assert out.count("已退役") == len(S.retired_keys())


def test_unknown_keys_warn_but_never_raise() -> None:
    cfg = {"policy": {"taskTmeoutSec": 1}, "rl": {"mb_": 1}, "mystery": {"x": 1}}
    out = S.check_rl_config(cfg)
    assert any("policy.taskTmeoutSec" in line for line in out)
    assert any("rl.mb_" in line for line in out)
    assert any("mystery" in line for line in out)


def test_freeform_sections_are_not_drilled() -> None:
    cfg = {
        "nodes": [{"id": "a", "authKey": "k", "whatever_field": 1}],
        "courses": {"c": {"any_course_knob": 1}},
        "rl": {"remote_hubs": {"whatever": "u"}, "intent": {"nested": 1}, "goal": {"nested": 2}},
    }
    assert S.check_rl_config(cfg) == []


@pytest.mark.parametrize("cfg", [{}, {"version": 1}])
def test_empty_or_shape_broken_input_is_silent(cfg: dict) -> None:
    assert S.check_rl_config(cfg) == []


def test_schema_data_is_self_consistent() -> None:
    """退役键不得同时出现在该段白名单里（否则一会告警一会放行）。"""
    retired = S.retired_keys()
    assert retired, "白名单数据没加载到（rl_config.schema.json 缺失？）"
    for dotted in retired:
        parts = dotted.split(".", 1)
        if len(parts) == 2:
            assert parts[1] not in S.allowed_keys(parts[0]), f"{dotted} 同时在退役表与白名单里"


def test_retired_reason_is_non_empty() -> None:
    assert all(reason.strip() for reason in S.retired_keys().values())


# ------------------------------------------------------------------ 接线（E8）

#: 子进程 oracle（手法同 `tests/test_serve_wiring.py::_ORACLE`）：跑真 `run_rl.main()`，
#: 用 `--echo-config` 让它在 dump 后早退——否则会一路跑进训练链（导 torch + 读权重）。
_ORACLE = """
import sys
sys.argv = ["run_rl.py", "--course", sys.argv[1], "--echo-config"]
import rl.config as cfg
cfg.echo_config = lambda *a, **k: print("ECHO-REACHED")
import run_rl
run_rl.main()
"""


def test_run_rl_logs_the_advisory_and_still_starts(tmp_path: Path) -> None:
    """**接线钉子**：假键 ⇒ 启动日志里真的有告警行，且**照常起训**（只告警不拒）。

    为什么必须有这条（2026-09-26 评审）：单测只钉住 `check_rl_config` 纯函数 ⇒ 谁把
    `run_rl.py` 里那三行「读配置 → 打告警」挪掉都无人报警——而「静默沉睡」正是本案要防的形态。
    """
    fixture = tmp_path / "rl-config.fixture.json"
    fixture.write_text(
        json.dumps(
            {"version": 1, "bogus_top": {}, "rl": {"typo_key": 1, "stream": 0}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # zh-CN Windows 默认 GBK：oracle stdout 含非 ASCII ⇒ 显式 UTF-8（同 serve_wiring 的教训）。
    env = {**os.environ, "PYTHONUTF8": "1", "BCITY_RL_CONFIG": str(fixture)}
    proc = subprocess.run(
        [sys.executable, "-c", _ORACLE, "c4-dodge"],
        cwd=str(NN_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    out = proc.stdout or ""
    assert "ECHO-REACHED" in out, (
        f"oracle 没走到 echo_config（与告警无关的链坏了）：{(proc.stderr or '').strip()[-300:]}"
    )
    assert "rl-config 告警：bogus_top" in out, f"未知顶层段没被点出：{out[-500:]}"
    assert "rl.typo_key" in out, f"未知键没被点出：{out[-500:]}"
    assert "rl.stream（已退役）" in out, f"已退役键没被点出：{out[-500:]}"
