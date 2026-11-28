"""biz/cmd.py — 本地 rollout 命令模板的 flag 口径锁死。

回归背景（2026-09-12 c6-gae 核验）：`args_rollout_overrides()` 的键是下划线
（`lives_override`/`player_level`），`build_rollout_cmd` 曾用 `f\"--{k}\"` 原样
拼出 `--lives_override`，而 `export-rl-rollout.ts` 只认连字符
`--lives-override`/`--player-level`（未知 flag 静默忽略）——本地直跑全程以
hard 缺省（3命1星）跑，远端以课程覆盖（1命0星）跑，rollout 胜率虚高 ~17pp。
本文件把导出器接受的连字符口径钉死：下划线变体一律视为 bug。
"""
from __future__ import annotations

import types

from worker.cmd import RolloutCmdSpec, build_rollout_cmd


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "goal_rollout": False,
        "intent_rollout": False,
        "max_ticks": 2400,
        "difficulty": "hard",
        "dodge": "",
        "lives_override": None,
        "player_level": None,
        "course_obj": None,
        "course_path": "",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cmd(**kw) -> list[str]:
    return build_rollout_cmd(
        RolloutCmdSpec(
            bun="bun",
            args=_args(**kw),
            weights="tmp/w.json",
            out_dir="tmp/out",
            stage=2000,
            seed=7,
            wver="abc",
            node_label="local",
        )
    )


def test_overrides_use_hyphen_flags() -> None:
    cmd = _cmd(lives_override=1, player_level=0)
    assert "--lives-override" in cmd
    assert "--player-level" in cmd
    assert cmd[cmd.index("--lives-override") + 1] == "1"
    assert cmd[cmd.index("--player-level") + 1] == "0"


def test_no_underscore_flag_variants() -> None:
    """下划线 flag 会被导出器静默丢弃——出现即 bug（c6-gae 根因）。"""
    cmd = _cmd(lives_override=1, player_level=0)
    assert "--lives_override" not in cmd
    assert "--player_level" not in cmd


def test_no_overrides_no_flags() -> None:
    cmd = _cmd()
    assert "--lives-override" not in cmd
    assert "--player-level" not in cmd
    assert "--lives_override" not in cmd
    assert "--player_level" not in cmd


def test_intent_cmd_supports_custom_stage_json() -> None:
    """P′-4a（2026-11-28）：意图线必须能跑自定义关（classic35 id 2000+）。

    旧命令不传 `--stage-json` ⇒ 导出器 `STAGES[2000]` 未定义 ⇒ 整批静默 SKIP。
    """

    class _Course:
        name = "fake-intent-course"

        def stage_json(self, stage: int) -> str:
            return '{"grid": [[0]]}'

    cmd = _cmd(intent_rollout=True, replan=30, course_obj=_Course(), course_path="")
    assert cmd[1] == "tools/sim/export-intent-rollout.ts"
    assert cmd[cmd.index("--stage-json") + 1] == '{"grid": [[0]]}'
    assert cmd[cmd.index("--replan") + 1] == "30"


def test_replay_corpus_passes_local_code_sha() -> None:
    """2026-11-17（docs/nn/remote-transport.md §154）：replay 语料透传本机 codeHash。

    导出器拿到就不必自己遍历 180 个文件（实测 13.5 ms/进程，成本全在 syscall）。
    只有**本机腿**透传：远端跑的树是 job 里解出来的 ts_code.zip，拿 hub 的 hash
    当它的身份就是撒谎；而 node_side 一律 full（没有 replay_meta），两条纪律同一条分支。
    """
    from worker.cmd import local_code_sha

    cmd = _cmd(corpus_fmt="replay")
    assert cmd.count("--code-sha") == 1
    got = cmd[cmd.index("--code-sha") + 1]
    assert got == local_code_sha()
    assert len(got) == 64 and set(got) <= set("0123456789abcdef")

    node = build_rollout_cmd(
        RolloutCmdSpec(
            bun="bun",
            args=_args(corpus_fmt="replay"),
            weights="w.json",
            out_dir="w0",
            stage=2000,
            seed=7,
            wver="abc",
            node_label="iter",
            node_side=True,
        )
    )
    # 集合判据（非文本断言，口径 C）：本机腿才带 `--code-sha`；`node_side` 恒 full ⇒ 两个 flag 都不该出现。
    assert {"--code-sha", "--corpus-fmt"}.isdisjoint(node)


def test_teacher_argmax_only_when_requested() -> None:
    """greedy 第二遍才追加 `--teacher-argmax`；普通采集逐字节不变。"""
    assert _cmd().count("--teacher-argmax") == 0
    assert _cmd(rollout_argmax=False).count("--teacher-argmax") == 0
    assert _cmd(rollout_argmax=True).count("--teacher-argmax") == 1


def test_teacher_argmax_reaches_node_side_argv() -> None:
    """2026-11-10：kind=iter / 半离线的 argv 由节点执行，也必须带 greedy flag。"""
    cmd = build_rollout_cmd(
        RolloutCmdSpec(
            bun="bun",
            args=_args(rollout_argmax=True),
            weights="w.json",
            out_dir="w0",
            stage=2000,
            seed=7,
            wver="abc",
            node_label="iter",
            node_side=True,
        )
    )
    assert cmd.count("--teacher-argmax") == 1
    plain = build_rollout_cmd(
        RolloutCmdSpec(
            bun="bun",
            args=_args(),
            weights="w.json",
            out_dir="w0",
            stage=2000,
            seed=7,
            wver="abc",
            node_label="iter",
            node_side=True,
        )
    )
    assert plain.count("--teacher-argmax") == 0


def test_local_code_sha_is_computed_once_per_process(monkeypatch) -> None:
    """代码身份**进程内只算一次**（`functools.cache`）：35 局/轮的每局 0.03s 不该重复付。

    清场口径是 `local_code_sha.cache_clear()`——旧形状是「0/1 元素 list 当 memo」，
    除了清不掉，也答不出「谁清的」（P0-5，2026-11-21）。
    """
    import common.codehash as codehash
    from worker.cmd import local_code_sha

    calls: list[int] = []

    def fake() -> str:
        calls.append(1)
        return f"sha-{len(calls)}"

    monkeypatch.setattr(codehash, "compute_code_hash", fake)
    local_code_sha.cache_clear()
    try:
        first = local_code_sha()
        assert local_code_sha() == first
        assert len(calls) == 1, "第二次调用必须命中记忆（否则每局都重算 0.03s）"
        local_code_sha.cache_clear()
        assert local_code_sha() != first, "cache_clear 之后必须重算"
        assert len(calls) == 2
    finally:
        local_code_sha.cache_clear()
