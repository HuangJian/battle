"""预采子进程（T4 双缓冲）的入口与动作覆盖通道 —— P0-3 回归。

背景（plan/single-resident-trainer §7-P0-3，评审二读实测）三层病：

1. 子进程入口写的是 `collect_only.py` 自己，而它**没有 `__main__`** ⇒ 零输出 exit 0：
   预采静默零产出，`precollect_ready` 因「子进程已退出」立刻为真 ⇒ 不卡、也不采；
2. 就算入口改对，子进程 argv 里的 `--bc`/`--iters` 也会被 `course_cli_conflicts` 拒启
   （那些键由**课程**定义：`apply_course` 覆盖 CLI 值、显式 CLI 响亮拒启）；
3. 于是正确的形状只能是：入口 = `trainer/run_rl.py`（唯一拥有 `--collect-only` 语义的入口）
   + 只带 `--course`（单一事实来源）+ 动作键走 `--collect-only-config` 这条**课程之后**的通道。

本文件钉住这四件：入口真的是可执行的入口（不是静默空转）、动作覆盖能生效（且能经
`--echo-config` 看见）、CLI 那条老路仍然会被拒（通道存在的理由）、无课程时**不预采**
（拿错快照比不预采更糟）。
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.subproc_util import run_utf8
from trainer.collect_only import RUN_RL_ENTRY, collect_child_spec

#: 夹具课程：`curricula/s-dodge.jsonc` 在仓里（与 `test_serve_wiring` 的 oracle 同款用法）。
COURSE = "s-dodge"


def _args(**over: object) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        course=COURSE,
        course_path=str(ROOT / "curricula" / f"{COURSE}.jsonc"),
        iters=20,
        precollect_games=0,
        traj=str(ROOT.parent / "tmp" / "x20-fixture"),
        out=str(ROOT.parent / "tmp" / "x20-fixture" / "weights.json"),
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# ────────────────────────── ① argv 形状（纯函数） ──────────────────────────


def test_spec_points_at_the_real_entry(tmp_path: Path) -> None:
    """入口必须是 `trainer/run_rl.py`，且它**真的有入口守卫**（P0-3 的根因是「零输出 exit 0」）。"""
    spec = collect_child_spec(_args(), "/tmp/snap.json", 2, 0, str(tmp_path / "cc.json"))
    assert spec is not None
    argv, payload = spec
    assert argv[0] == sys.executable
    assert argv[2] == str(RUN_RL_ENTRY)
    assert RUN_RL_ENTRY.name == "run_rl.py"
    assert RUN_RL_ENTRY.exists(), "入口文件不在 ⇒ 子进程必然起不来"
    assert "run_main(main)" in RUN_RL_ENTRY.read_text(encoding="utf-8"), (
        "入口没有 `__main__` 守卫 = 子进程零输出 exit 0（正是 P0-3 那个静默失效）"
    )
    # 只带课程 + 动作覆盖通道；**不再继承父进程 argv**（serve 的 argv 里没有课程参数）
    assert argv[3:5] == ["--course", str(ROOT / "curricula" / f"{COURSE}.jsonc")]
    assert argv[5] == "--collect-only-config"
    assert payload == {
        "collect_only": 1,
        "bc": "/tmp/snap.json",
        "start_it": 2,
        "precollect_games": 0,
        "precollect_samples": 0,
    }


def test_spec_without_course_is_none(tmp_path: Path) -> None:
    """无课程的老调用：重建不出子进程的命名空间 ⇒ 不预采（拿错快照比不预采更糟）。"""
    args = _args(course="", course_path="")
    assert collect_child_spec(args, "/tmp/snap.json", 2, 0, str(tmp_path / "cc.json")) is None


# ────────────────────────── ② 动作覆盖真的生效 ──────────────────────────


def _run_rl_main_with_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    """在**当前进程**里跑 `run_rl.main()`（与 `test_serve_wiring` 的 oracle 同一套夹具思路）：

    · `BCITY_RL_CONFIG` = 自带夹具（不读操作员那份未入库的 rl-config）；
    · `echo_config` 换成 dump —— 真 `echo_config` 对 `course_obj`（dataclass，含 bytes）会
      `TypeError`（既有行为，与本用例无关；oracle 也是这么绕的），dump 用 `repr` 不挑类型。
    """
    import worker.config as cfg
    from trainer import run_rl

    fixture = tmp_path / "rl-config.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    monkeypatch.chdir(ROOT)  # main() 自己会 chdir 到仓根；monkeypatch 兜底还原
    dumped: dict[str, str] = {}

    def fake_echo(args, course, it=1):
        dumped.update({k: repr(v) for k, v in vars(args).items() if not k.startswith("_")})
        raise SystemExit(0)  # 走到 dump 即达目的（echo 在真源码里就是短路返回）

    monkeypatch.setattr(cfg, "echo_config", fake_echo)
    monkeypatch.setattr(sys, "argv", ["trainer/run_rl.py", *argv])
    with pytest.raises(SystemExit) as e:
        run_rl.main()
    assert e.value.code == 0
    return dumped


def test_override_channel_wins_over_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--collect-only-config` 的键在 `apply_course` **之后**施加 ⇒ 真生效（不是被课程盖掉）。"""
    cfg_path = tmp_path / "cc.json"
    cfg_path.write_text(
        json.dumps(
            {
                "collect_only": 1,
                "bc": "/tmp/snap.json",
                "start_it": 2,
                "precollect_games": 0,
                "precollect_samples": 0,
            }
        ),
        encoding="utf-8",
    )
    got = _run_rl_main_with_fixture(
        tmp_path,
        monkeypatch,
        ["--course", COURSE, "--collect-only-config", str(cfg_path), "--echo-config"],
    )
    assert got["collect_only"] == "1"
    assert got["bc"] == "'/tmp/snap.json'"
    assert got["start_it"] == "2"
    # 课程定义的键没被碰坏（夹具课程 iters=20 ⇒ 覆盖表里没有它就不动）
    assert got["iters"] == "20"


def test_cli_action_keys_still_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """对照：同一条腿若走**普通 CLI**（老形状）仍被冲突闸拒 —— 这就是通道存在的理由。"""
    import worker.config as cfg
    from trainer import run_rl

    fixture = tmp_path / "rl-config.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(
        cfg,
        "echo_config",
        lambda *a, **k: pytest.fail("不该走到 echo —— 冲突闸应该先拒"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["trainer/run_rl.py", "--course", COURSE, "--collect-only", "1", "--bc", "/tmp/x.json",
         "--start-it", "2", "--iters", "1", "--echo-config"],
    )
    with pytest.raises(SystemExit) as e:
        run_rl.main()
    assert "冲突" in str(e.value)


# ────────────────────────── ③ 坏覆盖 = 响亮死，不是静默活 ──────────────────────────


def test_bad_override_config_fails_loudly(tmp_path: Path) -> None:
    """真起子进程：覆盖文件读不到 ⇒ **非零退出 + 有话说**（而不是零输出 exit 0）。"""
    fixture = tmp_path / "rl-config.json"
    fixture.write_text("{}", encoding="utf-8")
    proc = run_utf8(
        [
            sys.executable,
            "-u",
            str(RUN_RL_ENTRY),
            "--course",
            COURSE,
            "--collect-only-config",
            str(tmp_path / "does-not-exist.json"),
        ],
        cwd=str(ROOT),
        timeout=180,
        env={**os.environ, "BCITY_RL_CONFIG": str(fixture), "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode != 0, "零输出 exit 0 = P0-3 的静默失效形态，必须响亮死"
    assert "动作覆盖" in (proc.stdout + proc.stderr), "失败必须留痕（子进程日志会落到 precollect-*.log）"
