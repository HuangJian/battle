"""课程 `inference_rules` 块接线测试（评估/部署专用，训练链恒关）。

覆盖：课程字段解析 → corpus 指纹（必须不变）→ apply_course 扁平参 → rollout 命令
（必须无该开关）→ 本机评估命令 → 分发参数 / 任务键 → 节点能力门。
任何一处漏接都是“课程说开了、实际没开”或“训练链偷偷开了”的静默事故。
"""
from __future__ import annotations

import json
import subprocess
import types
import urllib.parse
from pathlib import Path

import pytest

from biz.course_spec import InferenceRulesBlock
from trainer.batch_plan import (
    eval_rule_kwargs,
    eval_rules_reject_reason,
    node_supports_break_bricks_on_path,
    node_supports_skip_steel_only_shots,
    node_supports_suppress_lead_miss_fire,
    node_supports_suppress_useless_fire,
)
from worker.cmd import build_rollout_cmd
from worker.config import CourseConfig, apply_course, corpus_identity_fp
from worker.eval_local import run_local_eval_game

#: 部署面名字的唯一来源（测试内常量：断言用变量，不把措辞散在每条判据里）。
SKIP_FLAG = "--skip-steel-only-shots"
SKIP_PARAM = "skipSteelOnlyShots"
SKIP_SUFFIX = ":skipSteelShots"
BRICK_FLAG = "--break-bricks-on-path"
BRICK_PARAM = "breakBricksOnPath"
BRICK_SUFFIX = ":breakBricks"
USELESS_FLAG = "--suppress-useless-fire"
USELESS_PARAM = "suppressUselessFire"
USELESS_SUFFIX = ":suppressUselessFire"
LEAD_FLAG = "--suppress-lead-miss-fire"
LEAD_PARAM = "suppressLeadMissFire"
LEAD_SUFFIX = ":suppressLeadMissFire"


def _has_flag(cmd: list[str]) -> bool:
    return cmd.count(SKIP_FLAG) > 0


def _has_brick_flag(cmd: list[str]) -> bool:
    return cmd.count(BRICK_FLAG) > 0


def _has_useless_flag(cmd: list[str]) -> bool:
    return cmd.count(USELESS_FLAG) > 0


def _has_lead_flag(cmd: list[str]) -> bool:
    return cmd.count(LEAD_FLAG) > 0


def _course(**kw) -> CourseConfig:
    return CourseConfig(name="h5e", mode="per-tick", **kw)


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "goal_rollout": False,
        "intent_rollout": False,
        "max_ticks": 2400,
        "difficulty": "hard",
        "dodge": "",
        "max_hours": 0,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_course_block_defaults_false() -> None:
    assert _course().inference_rules.skip_steel_only_shots is False
    assert _course().inference_rules.break_bricks_on_path is False
    assert _course().inference_rules.suppress_useless_fire is False
    assert _course().inference_rules.suppress_lead_miss_fire is False
    on = _course(
        inference_rules={"skip_steel_only_shots": True, "break_bricks_on_path": True}
    )
    assert on.inference_rules.skip_steel_only_shots is True
    assert on.inference_rules.break_bricks_on_path is True
    assert _course(inference_rules={"suppress_useless_fire": True}).inference_rules.suppress_useless_fire is True
    assert _course(inference_rules={"suppress_lead_miss_fire": True}).inference_rules.suppress_lead_miss_fire is True


def test_course_block_rejects_both_shot_gates() -> None:
    """B8/B10 同机制，同时开启不构成组合规则 ⇒ 课程文件响亮拒。"""
    with pytest.raises(ValueError):
        InferenceRulesBlock(
            **{"suppress_useless_fire": True, "suppress_lead_miss_fire": True}
        )


def test_course_block_rejects_typos() -> None:
    """extra="forbid"：键名拼错必须响亮拒，不静默忽略。"""
    with pytest.raises(ValueError):
        InferenceRulesBlock(**{"skip_steel_shot": True})


def test_corpus_identity_unchanged() -> None:
    """评估专用开关不得改训练语料身份（否则在跑的腿会被 D14 判成异身份）。"""
    plain = _course()
    assert corpus_identity_fp(plain) == corpus_identity_fp(_course())
    on = _course(
        inference_rules={"skip_steel_only_shots": True, "break_bricks_on_path": True}
    )
    assert corpus_identity_fp(on) == corpus_identity_fp(plain)


def test_flat_overrides_has_no_training_key() -> None:
    """刻意不映射到训练 args（只有 apply_course 单独写扁平规则键）。"""
    on = _course(
        inference_rules={"skip_steel_only_shots": True, "break_bricks_on_path": True}
    )
    ov = on.flat_overrides()
    assert ov.get("skip_steel_only_shots") is None
    assert ov.get("break_bricks_on_path") is None
    assert ov.get("suppress_useless_fire") is None
    assert ov.get("suppress_lead_miss_fire") is None
    assert ov.get("inference_rules") is None


def test_apply_course_sets_eval_flag() -> None:
    args = types.SimpleNamespace(mode="per-tick")
    apply_course(args, _course())
    assert args.skip_steel_only_shots is False
    assert args.break_bricks_on_path is False
    assert args.suppress_useless_fire is False
    assert args.suppress_lead_miss_fire is False
    args_on = types.SimpleNamespace(mode="per-tick")
    apply_course(
        args_on,
        _course(
            inference_rules={"skip_steel_only_shots": True, "break_bricks_on_path": True}
        ),
    )
    assert args_on.skip_steel_only_shots is True
    assert args_on.break_bricks_on_path is True
    assert eval_rule_kwargs(args_on) == {
        "skip_steel_only_shots": True,
        "break_bricks_on_path": True,
        "suppress_useless_fire": False,
        "suppress_lead_miss_fire": False,
    }
    args_useless = types.SimpleNamespace(mode="per-tick")
    apply_course(args_useless, _course(inference_rules={"suppress_useless_fire": True}))
    assert args_useless.suppress_useless_fire is True
    assert eval_rule_kwargs(args_useless)["suppress_useless_fire"] is True
    args_lead = types.SimpleNamespace(mode="per-tick")
    apply_course(args_lead, _course(inference_rules={"suppress_lead_miss_fire": True}))
    assert args_lead.suppress_lead_miss_fire is True
    assert eval_rule_kwargs(args_lead)["suppress_lead_miss_fire"] is True


def _rollout_cmd(**kw) -> list[str]:
    return build_rollout_cmd(
        "bun",
        _args(**kw),
        weights="tmp/w.json",
        out_dir="tmp/out",
        stage=2000,
        seed=7,
        wver="abc",
        node_label="local",
    )


def test_rollout_cmd_never_carries_eval_flag() -> None:
    """训练链恒关：即使 args 上有开值，rollout 命令也不得拼评估开关。"""
    cmd_on = _rollout_cmd(
        skip_steel_only_shots=True,
        break_bricks_on_path=True,
        suppress_useless_fire=True,
        suppress_lead_miss_fire=True,
    )
    assert not _has_flag(cmd_on)
    assert not _has_brick_flag(cmd_on)
    assert not _has_useless_flag(cmd_on)
    assert not _has_lead_flag(cmd_on)
    assert not _has_flag(_rollout_cmd())
    assert not _has_brick_flag(_rollout_cmd())


def test_local_eval_cmd_flag_only_when_enabled(monkeypatch, tmp_path: Path) -> None:
    """本机评估腿：开 ⇒ 命令带 --skip-steel-only-shots；关 ⇒ 命令逐字节不变。"""
    import worker.eval_local as eval_local_mod

    seen: list[list[str]] = []

    def fake_capture(cmd, timeout_sec, **kw):
        seen.append(list(cmd))
        out_dir = Path(cmd[cmd.index("--out") + 1])
        (out_dir / "_eval_report.json").write_text(
            json.dumps({"outcome": "stage_clear", "ticks": 1}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(eval_local_mod, "run_eval_runner_capture", fake_capture)
    run_local_eval_game(
        "bun",
        "tmp/w.json",
        2000,
        7,
        tmp_path / "off",
        100,
        "hard",
        5.0,
        "abc",
        skip_steel_only_shots=False,
        break_bricks_on_path=False,
        suppress_useless_fire=False,
        suppress_lead_miss_fire=False,
    )
    run_local_eval_game(
        "bun",
        "tmp/w.json",
        2000,
        7,
        tmp_path / "on",
        100,
        "hard",
        5.0,
        "abc",
        skip_steel_only_shots=True,
        break_bricks_on_path=True,
        suppress_useless_fire=True,
        suppress_lead_miss_fire=False,
    )
    run_local_eval_game(
        "bun",
        "tmp/w.json",
        2000,
        7,
        tmp_path / "lead",
        100,
        "hard",
        5.0,
        "abc",
        suppress_lead_miss_fire=True,
    )
    assert not _has_flag(seen[0])
    assert not _has_brick_flag(seen[0])
    assert not _has_useless_flag(seen[0])
    assert not _has_lead_flag(seen[0])
    assert _has_flag(seen[1])
    assert _has_brick_flag(seen[1])
    assert _has_useless_flag(seen[1])
    assert not _has_lead_flag(seen[1])
    assert _has_lead_flag(seen[2])
    assert not _has_useless_flag(seen[2])


def test_fetch_task_encodes_param_and_key(monkeypatch) -> None:
    """透传不断：开 ⇒ 查询串带两个规则参数且任务键带后缀；关 ⇒ 都不带。"""
    import common.distribution

    seen: dict = {}
    keys: list[str] = []

    class _BoomError(Exception):
        pass

    def fake_request(url, auth_key, timeout, headers=None):
        seen["url"] = url
        raise _BoomError()

    monkeypatch.setattr(common.distribution, "_request", fake_request)
    with pytest.raises(common.distribution.DistError):
        common.distribution.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0,
            skip_steel_only_shots=True, break_bricks_on_path=True,
            suppress_useless_fire=True, suppress_lead_miss_fire=False, on_key=keys.append,
        )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert qs.get(SKIP_PARAM) == ["1"]
    assert qs.get(BRICK_PARAM) == ["1"]
    assert qs.get(USELESS_PARAM) == ["1"]
    assert qs.get(LEAD_PARAM) is None
    assert keys and keys[0].endswith(SKIP_SUFFIX + BRICK_SUFFIX + USELESS_SUFFIX)

    seen.clear()
    keys.clear()
    with pytest.raises(common.distribution.DistError):
        common.distribution.fetch_task(
            "http://x/", "k", iter_id="i", wver="w", stage=2000, seed=7,
            max_ticks=100, difficulty="hard", timeout=5.0,
            suppress_lead_miss_fire=True, on_key=keys.append,
        )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert qs.get(SKIP_PARAM) is None
    assert qs.get(BRICK_PARAM) is None
    assert qs.get(USELESS_PARAM) is None
    assert qs.get(LEAD_PARAM) == ["1"]
    assert keys and keys[0].endswith(LEAD_SUFFIX)
    assert not keys[0].endswith(SKIP_SUFFIX)
    assert not keys[0].endswith(BRICK_SUFFIX)


def test_node_capability_gate() -> None:
    assert node_supports_skip_steel_only_shots({}) is False
    assert node_supports_skip_steel_only_shots({"skipSteelOnlyShotsSupport": False}) is False
    assert node_supports_skip_steel_only_shots({"skipSteelOnlyShotsSupport": True}) is True
    assert node_supports_break_bricks_on_path({}) is False
    assert node_supports_break_bricks_on_path({"breakBricksOnPathSupport": True}) is True
    assert node_supports_suppress_useless_fire({}) is False
    assert node_supports_suppress_useless_fire({"suppressUselessFireSupport": True}) is True
    assert node_supports_suppress_lead_miss_fire({}) is False
    assert node_supports_suppress_lead_miss_fire({"suppressLeadMissFireSupport": True}) is True

    # 评估侧节点门（rollout 的 check_node_capabilities 不查这些 eval-only 开关）。
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(skip_steel_only_shots=True), {})
        is not None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(skip_steel_only_shots=True), {
            "skipSteelOnlyShotsSupport": True
        })
        is None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(break_bricks_on_path=True), {})
        is not None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(break_bricks_on_path=True), {
            "breakBricksOnPathSupport": True
        })
        is None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(suppress_useless_fire=True), {})
        is not None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(suppress_useless_fire=True), {
            "suppressUselessFireSupport": True
        })
        is None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(suppress_lead_miss_fire=True), {})
        is not None
    )
    assert (
        eval_rules_reject_reason(types.SimpleNamespace(suppress_lead_miss_fire=True), {
            "suppressLeadMissFireSupport": True
        })
        is None
    )
