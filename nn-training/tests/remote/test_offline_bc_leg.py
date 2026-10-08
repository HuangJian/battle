"""tests/remote/test_offline_bc_leg.py — ★M5：自主腿的 BC 通道（`plan/worker-type-dispatch-model` §3-M5 / Q5 / 需求 7）。

守卫的是**闸与接线形状**，不是训练：真实执行链在包内（`remote.worker.worker_loop`），
所以用例把 `_run_bc_once` 当注入点（它的返回值就是「跑了几份」）：

  * `holding=True`（本盘还带着课程 hold）⇒ **一枪不发**（Q5 的 hold × BC 互斥）；
  * hub 地址 / token / 包内代码任一缺 ⇒ 不领（best-effort 腿，绝不打断课程主循环）；
  * 领到 ⇒ 返回一句摘要（调用方据此重置空转锚）；失败 ⇒ 只记一行、不上抛；
  * `_run_auto` 的空档分支**真的会调它**（清单全被别人持满时就是这种空档）。

为什么闸也要在客户端留一份（真闸在 hub）：省一趟必然 409 的往返；两处判据同源
（`?worker=` 清单回填的 `holds["mine"]`），而 hub 侧那两条腿有自己的用法与用例
（`tests/hub/test_hold.py::test_hold_and_bc_are_mutually_exclusive_in_both_directions`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from remote import offline_boot as ob


def _ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, hub: str = "http://hub") -> None:
    """把「有 hub、有 token、包内代码就绪」三条前置一次备好（每个用例各取所需）。"""
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: [hub])
    code = tmp_path / "worker-code"
    code.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ob, "CODE_DIR", str(code))


def test_holding_worker_never_takes_bc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Q5：带 hold 的盘不去领 BC（连注入点都不该被摸到）。"""
    _ready(monkeypatch, tmp_path)
    touched: list[int] = []

    def boom(*a: Any, **kw: Any) -> int:
        touched.append(1)
        raise AssertionError("带 hold 的盘不许走到执行面")

    monkeypatch.setattr(ob, "_run_bc_once", boom)
    assert ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lambda _m: None, holding=True) == ""
    assert touched == []


def test_missing_hub_or_code_is_a_quiet_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """best-effort 腿：没有 hub / 没有 token / 包内代码还没就绪 ⇒ 不领，且只留一句可读日志。"""
    lines: list[str] = []
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: [])
    assert ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lines.append) == ""
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: ["http://hub"])
    assert ob.try_take_bc_job({}, {}, lines.append) == ""  # 无 token
    code = tmp_path / "nope"
    monkeypatch.setattr(ob, "CODE_DIR", str(code))
    assert ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lines.append) == ""
    assert any("包内代码" in ln for ln in lines), lines
    assert not code.exists()


def test_takes_bc_through_the_injection_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """领到 ⇒ 摘要一行（含份数与 hub）；没领到（0 份）⇒ 空串。"""
    _ready(monkeypatch, tmp_path)
    monkeypatch.setattr(ob, "_run_bc_once", lambda cfg, hub, token, log: 2)
    got = ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lambda _m: None)
    assert "2" in got and "http://hub" in got, got
    monkeypatch.setattr(ob, "_run_bc_once", lambda cfg, hub, token, log: 0)
    assert ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lambda _m: None) == ""


def test_failure_is_swallowed_and_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """一段白跑的 BC 不该把整个会话带走：异常 ⇒ 空串 + 一行（带类型名，可查）。"""
    _ready(monkeypatch, tmp_path)
    lines: list[str] = []

    def boom(*a: Any, **kw: Any) -> int:
        raise RuntimeError("hub 掉线")

    monkeypatch.setattr(ob, "_run_bc_once", boom)
    assert ob.try_take_bc_job({}, {"HUB_TOKEN": "t"}, lines.append) == ""
    assert any("RuntimeError" in ln and "hub 掉线" in ln for ln in lines), lines


def test_run_auto_takes_bc_in_the_idle_branch_and_passes_my_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接线：清单里全是别人的 hold（空档）⇒ 真去领 BC，并把「本盘还持着哪些课」带下去。"""
    seen: dict[str, Any] = {}

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list, list, list]:
        # 自己那门课还挂着 hold（Q5 的前置闸就该亮）：清单行既有别人的、也有自己的
        kw["holds"]["mine"] = ["mine-held"]
        return [], [{"course": "a", "holder": "w2", "expires_in": 12.0}], []

    def fake_take(cfg: dict, creds: dict, log: Any, *, holding: bool = False) -> str:
        seen["holding"] = holding
        raise SystemExit("bc-leg-called")

    monkeypatch.setattr(ob, "resolve_courses", fake_resolve)
    monkeypatch.setattr(ob, "worker_id_of", lambda work, log: "w1")
    monkeypatch.setattr(ob, "try_take_bc_job", fake_take)
    with pytest.raises(SystemExit, match="bc-leg-called"):
        ob._run_auto({}, {}, lambda _m: None, None)
    assert seen["holding"] is True, "前置闸必须把「本盘还持着的课」带下去（Q5）"


def test_run_auto_does_not_take_bc_while_courses_are_runnable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有课可跑就不碰 BC（空档才领——否则一段长 BC 会把可跑的课饿死）。"""

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list, list, list]:
        return [{"course": "a", "pack_sha256": "aa" * 32}], [], []

    def nope(*a: Any, **kw: Any) -> str:
        raise AssertionError("有活可跑时不该领 BC")

    monkeypatch.setattr(ob, "resolve_courses", fake_resolve)
    monkeypatch.setattr(ob, "worker_id_of", lambda work, log: "w1")
    monkeypatch.setattr(ob, "try_take_bc_job", nope)
    monkeypatch.setattr(
        ob,
        "_run_batch",
        lambda *a, **kw: (_ for _ in ()).throw(SystemExit("batch-ran")),
    )
    monkeypatch.setattr(ob, "hub_candidates", lambda cfg, creds: ["http://hub"])
    with pytest.raises(SystemExit, match="batch-ran"):
        ob._run_auto({}, {}, lambda _m: None, None)
