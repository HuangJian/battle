"""常驻训练准入闸（S1，plan/single-resident-trainer.plan.md §1）。

契约三条：
  ① **没有一次性动作标志** ⇒ 常驻训练 ⇒ 拒启（与 `--course` 无关：无课程的老全局路径
     同样是常驻写者），且拒启消息**必须给出替代**（否则操作员只会去找 `--force`，
     而 `--force` 是锁抢先、过不了闸）；
  ② 一次性腿（`--smoke` / `--export-bundle` / `--collect-only` / `--echo-config`）一字不拦；
  ③ 逃生舱 `--allow-detached-trainer`：放行 + 醒目告警 + 往自报文件记一笔
     （控制台横幅 `detachedTrainers` 的数据源；环境口 `NN_TRAINER_DETACHED` 让用例
     不写操作员真盘）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.trainer_gate import (
    DETACHED_KEEP,
    REFUSE_MESSAGE,
    REFUSE_MESSAGE_BC,
    announce_escape_hatch,
    detached_report_path,
    escape_hatch_used,
    note_detached_trainer,
    oneshot_action_present,
    refuse_detached_trainer,
    resident_trainer_wanted,
)


def _args(**over: object) -> types.SimpleNamespace:
    """裸 `run_rl.py --course x` 的命名空间（再按需覆写某几个动作键）。"""
    ns = types.SimpleNamespace(
        course="x20-clutch",
        smoke=False,
        collect_only=0,
        export_bundle="",
        echo_config=False,
        allow_detached_trainer=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# ────────────────────────── ① 判据（纯函数） ──────────────────────────


def test_bare_course_run_is_refused() -> None:
    """裸 `--course` = 常驻训练循环 ⇒ 拒启，且消息给出替代命令。"""
    msg = refuse_detached_trainer(_args())
    assert msg == REFUSE_MESSAGE
    assert "run_rl_cluster.py --serve" in msg
    assert "bun run dashboard" in msg
    assert "--allow-detached-trainer" in msg  # 排障逃生舱在文案里（否则操作员只会找 --force）


def test_legacy_global_run_without_course_is_refused_too() -> None:
    """无 `--course` 的老全局路径同样是常驻写者（`.run_rl.lock` 时代形态）。"""
    assert refuse_detached_trainer(_args(course="")) == REFUSE_MESSAGE


@pytest.mark.parametrize(
    "flag",
    [
        {"smoke": True},
        {"collect_only": 1},
        {"export_bundle": "tmp/x.bundle.zip"},
        {"echo_config": True},
    ],
)
def test_oneshot_legs_are_exempt(flag: dict) -> None:
    """四类一次性腿一字不拦（豁免判的是**标志**，不是课程）。"""
    args = _args(**flag)
    assert refuse_detached_trainer(args) is None
    assert oneshot_action_present(args) is True
    assert resident_trainer_wanted(args) is False
    assert escape_hatch_used(args) is False  # 一次性腿不算「靠逃生舱放行」


def test_escape_hatch_passes_and_is_flagged() -> None:
    args = _args(allow_detached_trainer=True)
    assert refuse_detached_trainer(args) is None
    assert escape_hatch_used(args) is True  # ⇒ 调用点打印告警 + 记一笔


def test_collect_only_zero_is_not_an_action() -> None:
    """`--collect-only 0` = 没带（argparse 默认值），仍是常驻；>0 才是动作。"""
    assert refuse_detached_trainer(_args(collect_only=0)) == REFUSE_MESSAGE
    assert refuse_detached_trainer(_args(collect_only=1)) is None


def test_bc_message_points_at_bc_alternative() -> None:
    msg = refuse_detached_trainer(_args(), message=REFUSE_MESSAGE_BC)
    assert msg == REFUSE_MESSAGE_BC
    assert "serve" in msg and "开课" in msg


# ────────────────────────── ③ 逃生舱自报（横幅数据源） ──────────────────────────


def test_hatch_writes_report_with_env_redirect(tmp_path: Path, monkeypatch) -> None:
    """放行分支写一笔（pid/course/entry/at/argv）；env 口把文件重定向出真 `tmp/`。"""
    target = tmp_path / "trainer-detached.json"
    monkeypatch.setenv("NN_TRAINER_DETACHED", str(target))
    assert detached_report_path() == str(target)
    announce_escape_hatch("x20-clutch", entry="run_rl", log_fn=lambda _m: None)
    got = json.loads(target.read_text(encoding="utf-8"))
    assert len(got) == 1
    rec = got[0]
    assert rec["pid"] == __import__("os").getpid()
    assert rec["course"] == "x20-clutch"
    assert rec["entry"] == "run_rl"
    assert isinstance(rec["at"], float | int)
    assert "trainer" in rec["argv"]


def test_hatch_report_keeps_last_n_newest_first(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "trainer-detached.json"
    monkeypatch.setenv("NN_TRAINER_DETACHED", str(target))
    for i in range(DETACHED_KEEP + 2):
        note_detached_trainer(f"c{i}", entry="run_bc", log_fn=lambda _m: None)
    got = json.loads(target.read_text(encoding="utf-8"))
    assert len(got) == DETACHED_KEEP
    assert got[0]["course"] == f"c{DETACHED_KEEP + 1}"  # 新的在前
    assert got[-1]["course"] == "c2"  # 最老的两条被挤出


def test_hatch_report_recovers_from_corrupt_file(tmp_path: Path, monkeypatch) -> None:
    """坏 JSON 不许让逃生舱失效——从空表重开（写侧不阻断排障）。"""
    target = tmp_path / "trainer-detached.json"
    target.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("NN_TRAINER_DETACHED", str(target))
    note_detached_trainer("c-new", log_fn=lambda _m: None)
    got = json.loads(target.read_text(encoding="utf-8"))
    assert [r["course"] for r in got] == ["c-new"]


def test_hatch_report_write_failure_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    """目录不可写 = 写失败只告警，逃生舱照常放行（判据是「能跑」，记录是其次）。"""
    blocker = tmp_path / "blocker"  # 父路径是普通文件 ⇒ mkdir/open 必抛 OSError
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("NN_TRAINER_DETACHED", str(blocker / "x.json"))
    lines: list[str] = []
    note_detached_trainer("c", log_fn=lines.append)  # 不抛
    assert lines and "WARN" in lines[0]


# ────────────────────────── 入口接线（真入口跑一遍） ──────────────────────────


def test_run_rl_entry_refuses_bare_start(tmp_path: Path, monkeypatch) -> None:
    """`trainer/run_rl.py`（无参数、无课程）在**真入口**上被闸拦下（不只是谓词对）。"""
    from trainer import run_rl

    cfg = tmp_path / "rl-config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(cfg))
    monkeypatch.setenv("NN_TRAINER_DETACHED", str(tmp_path / "detached.json"))
    monkeypatch.setattr(sys, "argv", ["trainer/run_rl.py"])
    # 闸在代码快照之前 ⇒ 快照不该被触发；但为稳妥仍打桩（真跑会碰 hub）
    monkeypatch.setattr(
        "remote.code_snapshot.ensure_cluster_snapshot", lambda **_kw: None, raising=False
    )
    monkeypatch.chdir(ROOT)  # main() 自己会 chdir 到仓根；monkeypatch 兜底还原
    with pytest.raises(SystemExit) as e:
        run_rl.main()
    msg = str(e.value)
    assert "run_rl_cluster.py --serve" in msg
    assert not (tmp_path / "detached.json").exists()  # 拒启 = 不记自报


def test_run_bc_entry_refuses_bare_start(monkeypatch) -> None:
    """`trainer/run_bc.py --course X`（无 `--smoke`）= 常驻 BC 编排 ⇒ 拒启（真入口）。"""
    from trainer import run_bc

    monkeypatch.chdir(ROOT)
    with pytest.raises(SystemExit) as e:
        run_bc.main(["--course", "x20-clutch"])
    assert "serve" in str(e.value)
