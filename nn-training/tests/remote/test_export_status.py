"""`export-status.json` —— 导出**成败**的权威落点（`remote/bundle.py`，2026-10-15）。

背景（`plan/offline-open-ended-iters-and-loud-export` §3.3，J3；用户 2026-10-14 报障第二条
「导出离线包失败应响亮报错」）：控制台起导出是 **detached 子进程**（拿不到退出码），而且
控制台重启后子进程仍在跑 ⇒ **控制台的内存状态不可靠**。落盘是唯一跨重启的锚。

本文件钉四件事（每条都对应一个具体的静默故障）：

  ① **非导出腿一字不写**——「没写」与「还没跑完」必须可分（否则普通 run_rl 会造出一份
     看起来像「导出成功」的旁挂件）；
  ② **成功也写**（`ok=true` + `exit_code=0`）——只写失败等于「文件不在 = 不可知」；
  ③ **退出码与原因来自进程自己**（第一手），且异常**继续上抛**（旁挂件不配改主腿的退出语义）；
  ④ **写不下只降级**（`write_export_status` 返回 `None`、不抛）——状态文件是旁挂件，
     不配把导出拖红。

跨语言那一半（控制台 `bundles/export.ts::taskBundleStatusPath` 按同一条规则拼、同目录 =
`tmp/<课>/`）由 `dashboard/tests/auto-offline-handoff.test.ts` 读本模块源码对账
（与 `BUNDLE_META_SUFFIX` 同一手法）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from remote.bundle import (
    EXPORT_STATUS_MAGIC,
    EXPORT_STATUS_NAME,
    EXPORT_STATUS_PROTO,
    export_status_guard,
    export_status_path,
    write_export_status,
)


def _args(tmp_path: Path, **over: object) -> SimpleNamespace:
    base: dict = {
        "export_bundle": str(tmp_path / "task-x1.zip"),
        "course_name": "x1",
        "iters": 0,
        "run_iters": -1,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _status(tmp_path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(export_status_path(tmp_path).read_text(encoding="utf-8")))


def test_export_status_path_is_next_to_the_pack() -> None:
    """落点 = **与包同目录**（`tmp/<课>/`）：日志/包/状态三者一处，人一眼能找到。"""
    assert EXPORT_STATUS_NAME == "export-status.json"
    assert export_status_path("/repo/tmp/x1") == Path("/repo/tmp/x1/export-status.json")


def test_non_export_leg_writes_nothing(tmp_path: Path) -> None:
    """非导出腿（没有 `--export-bundle`）**一字不写**——这条腿不该产出任何旁挂件。"""
    with export_status_guard(SimpleNamespace(course_name="x1", iters=0)):
        pass
    assert list(tmp_path.iterdir()) == []


def test_success_is_written_too(tmp_path: Path) -> None:
    """**成功也写**：只写失败的话，「文件不在」就同时意味着「失败了」与「还没跑完」。"""
    with export_status_guard(_args(tmp_path)):
        pass
    st = _status(tmp_path)
    assert (st["magic"], st["proto"]) == (EXPORT_STATUS_MAGIC, EXPORT_STATUS_PROTO)
    assert (st["ok"], st["exit_code"], st["reason"]) == (True, 0, "")
    assert st["running"] is False  # 终态：进行中必须被翻掉
    assert (st["course"], st["iters"], st["run_iters"]) == ("x1", 0, -1)
    assert st["pack"].endswith("task-x1.zip")
    assert st["pid"] > 0 and st["at"] > 0


def test_running_is_written_on_entry(tmp_path: Path) -> None:
    """启动就写 `running:true`：进程不退出时盘上仍有「进行中」，而不是上一次的 ok:true。"""
    with export_status_guard(_args(tmp_path)):
        st = _status(tmp_path)
        assert st["running"] is True
        assert st["ok"] is False
        assert st["reason"] == "导出进行中"
        assert st["pid"] > 0


def test_failure_records_the_first_hand_reason_and_still_raises(tmp_path: Path) -> None:
    """退出码与原因来自**进程自己**（第一手）；异常继续上抛（不改主腿的退出语义）。"""
    with_path = _args(tmp_path, iters=2000)
    msg = "[run_rl] --export-bundle 需要 --run-iters 说明整段长度"
    try:
        with export_status_guard(with_path):
            raise SystemExit(msg)
    except SystemExit as e:
        assert str(e) == msg  # 原样上抛（旁挂件不吞、不改）
    else:  # pragma: no cover - 失败分支
        raise AssertionError("异常被旁挂件吞了")
    st = _status(tmp_path)
    assert (st["ok"], st["exit_code"]) == (False, 1)
    assert st["running"] is False
    assert st["reason"].startswith("SystemExit: [run_rl] --export-bundle")
    assert st["iters"] == 2000


def test_exit_code_follows_systemexit_code(tmp_path: Path) -> None:
    """`SystemExit(0)` 是「自己决定干净退出」；`SystemExit("msg")` 按 1 记（与解释器同口径）。"""
    for code, want in ((0, 0), (3, 3), (None, 0), ("msg", 1)):
        try:
            with export_status_guard(_args(tmp_path)):
                raise SystemExit(code)
        except SystemExit:
            pass
        assert _status(tmp_path)["exit_code"] == want, code


def test_round_failure_path_is_covered_by_the_same_write_point(tmp_path: Path) -> None:
    """轮内 `ProtocolError`（撞硬上界 / 空计划）**也**落盘——它要同轮重试 5 次才退，
    只包住那两处 `raise SystemExit` 会漏掉这条真实失败路径（v1 设计的原缺口）。"""
    try:
        with export_status_guard(_args(tmp_path)):
            raise ValueError("build_plan: 计划覆盖 2000 轮 > 硬上界 500——拒发")
    except ValueError:
        pass
    st = _status(tmp_path)
    assert (st["ok"], st["exit_code"]) == (False, 1)
    assert st["reason"].startswith("ValueError: build_plan:")


def test_last_attempt_overwrites_atomically(tmp_path: Path) -> None:
    """每次尝试**覆盖写**（末次即真值）+ 原子替换（不留 `.tmp` 残留：残留会被当成一次真状态读走）。"""
    try:
        with export_status_guard(_args(tmp_path)):
            raise SystemExit("first")
    except SystemExit:
        pass
    assert _status(tmp_path)["reason"].startswith("SystemExit: first")
    with export_status_guard(_args(tmp_path)):
        pass
    assert _status(tmp_path)["ok"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export-status.json"]


def test_write_never_raises_and_degrades_to_none(tmp_path: Path) -> None:
    """旁挂件**不配把主腿拖红**：写不下（父路径是个文件）⇒ 返回 `None`、不抛。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    assert write_export_status(blocker, ok=True, exit_code=0) is None
    # 导出腿本身照旧走完（守卫只降级，不改控制流）
    with export_status_guard(_args(blocker)):
        pass
