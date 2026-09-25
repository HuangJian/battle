"""§2.5 配对 rotateSeed 核对（plan/accident.plan.md §2，2026-09-21）。

事故形态：配对要求两臂同 rotateSeed，但值靠人手在命令行传 —— 第一次就传漏/传错
（1789926833 vs 1789926915，相差 82 秒抖动）⇒ 两臂跑在不同种子流上，配对失败、返工重开。
修法（§2.3）：V 写进**课程文件**（两门配对课各写同一把）。本文件钉残留口子的守门：

  ① **本课声明 ≠ 实际生效 ⇒ 拒启**（`SystemExit`）——映射被静默丢弃就是「一条腿按错误
     种子流跑 80 轮」（`ent_break` 前科同族）；
  ② 未声明 ⇒ 单腿口径（一行说明，不打扰既有课程）；
  ③ 声明了却没有对端 ⇒ 响亮 WARNING（不许按配对口径结算）；
  ④ 有对端 ⇒ 打印同 V 课程表 + 各臂账本末条 `run_start.rotateSeed`（≠ V ⇒ WARNING；
     无账本 ⇒ 还没跑过）；
  ⑤ 核对是观测设施：读不到/坏文件**绝不抛**（不许拖垮训练主线）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.config import CourseConfig
from rl.loop_lifecycle import _paired_seed_startup_check
from rl.paired import (
    PAIRED_KEY,
    compose_lines,
    declared_paired_seed,
    latest_run_start_seed,
    pair_check,
    scan_paired_courses,
)

V = 20260921


def _course(**kw) -> CourseConfig:
    return CourseConfig(name="t2-pair", mode="per-tick", **kw)


def _write_course(dir_: Path, name: str, seed: int | None) -> None:
    body: dict[str, object] = {"name": name, "mode": "per-tick", "env": {}}
    if seed is not None:
        body[PAIRED_KEY] = seed
    (dir_ / f"{name}.jsonc").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _write_ledger(traj_root: Path, course: str, seeds: list[int | None]) -> None:
    d = traj_root / course
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, s in enumerate(seeds):
        if s is None:
            lines.append(json.dumps({"event": "iteration", "iter": i + 1}))
        else:
            lines.append(
                json.dumps({"event": "run_start", "iter": 0, "rotateSeed": s, "time": "x"})
            )
    (d / "training_log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ────────────────────────── ① 声明读法 ──────────────────────────


def test_declared_seed_only_when_explicitly_set() -> None:
    assert declared_paired_seed(_course(**{PAIRED_KEY: V})) == V
    assert declared_paired_seed(_course()) is None  # 未写
    assert declared_paired_seed(_course(**{PAIRED_KEY: None})) is None  # 显式 null ≠ 声明
    assert declared_paired_seed(None) is None  # 非课程运行


# ────────────────────────── ② 扫同 V 课程 ──────────────────────────


def test_scan_lists_only_same_v_courses(tmp_path: Path) -> None:
    _write_course(tmp_path, "pair-a", V)
    _write_course(tmp_path, "pair-b", V)
    _write_course(tmp_path, "other", V + 1)
    _write_course(tmp_path, "single", None)
    (tmp_path / "broken.jsonc").write_text('{ "name": "broken", // 半截', encoding="utf-8")
    got = scan_paired_courses(V, self_name="pair-a", curricula_dir=tmp_path)
    assert got == (("pair-b", V),)  # 自己不算、别 V 不算、坏文件跳过


def test_scan_without_same_v_is_empty(tmp_path: Path) -> None:
    _write_course(tmp_path, "lonely", V)
    assert scan_paired_courses(V, self_name="lonely", curricula_dir=tmp_path) == ()


# ────────────────────────── ③ 账本末条 run_start ──────────────────────────


def test_latest_run_start_seed_takes_last_row(tmp_path: Path) -> None:
    _write_ledger(tmp_path, "c1", [111, 222])  # 续跑会再写一条：末条才是事实
    assert latest_run_start_seed(tmp_path / "c1") == 222
    (tmp_path / "c2").mkdir()
    (tmp_path / "c2" / "training_log.jsonl").write_text(
        '{"event": "run_start", "rotateSeed": \n{"event": "iteration", "iter": 3}\n',
        encoding="utf-8",
    )
    assert latest_run_start_seed(tmp_path / "c2") is None  # 半截行跳过
    assert latest_run_start_seed(tmp_path / "nope") is None  # 无账本


# ────────────────────────── ④ 行组装（纯函数） ──────────────────────────


def test_compose_undeclared_is_single_leg() -> None:
    lines = compose_lines(
        declared=None, effective=1789926833, source="jitter", siblings=(), sibling_seeds=()
    )
    assert len(lines) == 1
    assert "未声明" in lines[0] and "单腿" in lines[0]
    assert "1789926833" in lines[0]


def test_compose_declared_without_peer_warns() -> None:
    lines = compose_lines(declared=V, effective=V, source="explicit", siblings=(), sibling_seeds=())
    assert "WARNING" in lines[-1]
    assert "配对无对端" in lines[-1]
    assert "不许按配对口径结算" in lines[-1]


def test_compose_peer_rows_ok_and_mismatch() -> None:
    lines = compose_lines(
        declared=V,
        effective=V,
        source="explicit",
        siblings=(("pair-b", V),),
        sibling_seeds=(("pair-b", V),),
    )
    assert any("✓ 同 V" in ln for ln in lines)
    assert not any("WARNING" in ln for ln in lines)

    bad = compose_lines(
        declared=V,
        effective=V,
        source="explicit",
        siblings=(("pair-b", V),),
        sibling_seeds=(("pair-b", 1789926915),),
    )
    assert any("WARNING" in ln and "1789926915" in ln for ln in bad)

    fresh = compose_lines(
        declared=V,
        effective=V,
        source="explicit",
        siblings=(("pair-b", V),),
        sibling_seeds=(("pair-b", None),),
    )
    assert any("账本无 run_start" in ln for ln in fresh)
    assert not any("WARNING" in ln for ln in fresh)


# ────────────────────────── ⑤ 端到端（pair_check） ──────────────────────────


def test_pair_check_end_to_end(tmp_path: Path) -> None:
    cur, traj = tmp_path / "curricula", tmp_path / "tmp"
    cur.mkdir()
    _write_course(cur, "pair-a", V)
    _write_course(cur, "pair-b", V)
    _write_ledger(traj, "pair-b", [V])
    lines = pair_check(
        declared=V, effective=V, source="explicit", traj_root=traj, self_name="pair-a",
        curricula_dir=cur,
    )
    text = "\n".join(lines)
    assert "V=20260921" in text and "pair-b" in text and "✓ 同 V" in text


def test_pair_check_never_raises_on_bad_inputs(tmp_path: Path) -> None:
    lines = pair_check(
        declared=V,
        effective=V,
        source="explicit",
        traj_root=tmp_path / "does-not-exist",
        curricula_dir=tmp_path / "also-missing",
    )
    assert lines and "WARNING" in "\n".join(lines)  # 无对端告警，而不是抛异常


# ────────────────────────── ⑥ 启动闸（拒启 / 打印） ──────────────────────────


def _args(course: CourseConfig | None, traj: Path):
    return SimpleNamespace(course_obj=course, course="pair-a", traj=str(traj / "pair-a"))


def test_startup_check_refuses_when_effective_differs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        _paired_seed_startup_check(_args(_course(**{PAIRED_KEY: V}), tmp_path), V + 1, "explicit")
    msg = str(e.value)
    assert "配对前提已被破坏" in msg
    assert str(V) in msg and str(V + 1) in msg


def test_startup_check_prints_peer_table(tmp_path: Path, capsys) -> None:
    cur = tmp_path / "curricula"
    traj = tmp_path / "tmp"
    cur.mkdir()
    _write_course(cur, "pair-a", V)
    _write_course(cur, "pair-b", V)
    _write_ledger(traj, "pair-b", [V])
    args = _args(_course(**{PAIRED_KEY: V}), traj)
    # 课程目录走默认（nn-training/curricula）；这条腿在真目录里没有对端 ⇒ 告警行
    _paired_seed_startup_check(args, V, "explicit")
    out = capsys.readouterr().out
    assert "paired seed" in out and f"V={V}" in out


def test_startup_check_single_leg_is_quiet(tmp_path: Path, capsys) -> None:
    _paired_seed_startup_check(_args(_course(), tmp_path), 4242, "jitter")
    out = capsys.readouterr().out
    assert "单腿" in out
    assert "WARNING" not in out
