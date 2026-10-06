"""P1 回归：`course_openable` 只读判据（2026-10-05 事故，plan/course-startup-recover §3.1/§4.1）。

事故：`x20-adv3-open-r2` 的课程文件自相矛盾（kickstart_init>0 而 kickstart_ref=false）⇒
`open_course` 启动期 SystemExit、整课被 trainer 跳过；而只读 `--json` 却报 `state=ready` /
`waiting=ready`（「无外部等待，下一步 …」）——人只能对着不动的界面干等。

这里钉住判据本身：复用开课同一条校验链（不另写判断）、缺文件/配置错 ⇒ ok=false + 原文
reason、合法课 ⇒ ok=true、BC 课不误报（覆盖边界，由 `loop_plan.course_openable` 分流）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import biz.course_resolve as course_resolve
import worker.bc_config as bc_config
from trainer.loop_plan import course_openable
from worker.loop_scheduler import CourseQueue
from worker.loop_tasks import Task, TaskResult

FIXTURE = "c4-dodge"  # 真课程文件（合法链）；内容复制进 tmp，改一个字就有对照


def _tmp_curricula(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """课程目录 + rl-config 夹具都重定向进 tmp（不读别人机器上的 rl-config.json）。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    monkeypatch.setattr(course_resolve, "CURRICULA_DIR", curricula)
    fixture = tmp_path / "rl-config.fixture.json"
    fixture.write_text('{"rl": {}}', encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    return curricula


def _real_source() -> str:
    return (ROOT / "curricula" / f"{FIXTURE}.jsonc").read_text(encoding="utf-8")


def test_valid_course_is_openable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    curricula = _tmp_curricula(tmp_path, monkeypatch)
    (curricula / f"{FIXTURE}.jsonc").write_text(_real_source(), encoding="utf-8")
    ok, reason = course_openable(FIXTURE)
    assert ok is True and reason == ""


def test_missing_course_file_is_not_openable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """诚实性：读不到课程文件 ⇒ `ok=false` + 原文（不假设「读不到就没事」）。"""
    _tmp_curricula(tmp_path, monkeypatch)
    ok, reason = course_openable("no-such-course-xyz")
    assert ok is False and "不存在" in reason


def test_contradictory_kickstart_is_not_openable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本事故的逐字形状：kickstart_init>0 ∧ kickstart_ref=false ⇒ 启动期响亮拒启。"""
    curricula = _tmp_curricula(tmp_path, monkeypatch)
    src = _real_source()
    assert '"kickstart_ref": true,' in src
    patched = src.replace(
        '"kickstart_ref": true,',
        '"kickstart_ref": false,\n  "kickstart_init": 0.1,',
        1,
    )
    (curricula / f"{FIXTURE}.jsonc").write_text(patched, encoding="utf-8")
    ok, reason = course_openable(FIXTURE)
    assert ok is False and "kickstart_ref" in reason


def test_openable_is_read_only_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零副作用：不建锁/不写账本/不动课程目录；两次调用同结果。"""
    curricula = _tmp_curricula(tmp_path, monkeypatch)
    (curricula / f"{FIXTURE}.jsonc").write_text(_real_source(), encoding="utf-8")
    before = sorted(p.name for p in curricula.iterdir())
    assert course_openable(FIXTURE) == course_openable(FIXTURE) == (True, "")
    assert sorted(p.name for p in curricula.iterdir()) == before


def test_bc_course_is_out_of_scope_ok_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖边界：BC 走 `_open_bc_course` 另一条链——不在这里把「没判过」写成「坏」。"""
    bc_dir = tmp_path / "curricula"
    bc_dir.mkdir()
    (bc_dir / "c-bc.bc.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bc_config, "CURRICULA_DIR", bc_dir)
    assert course_openable("c-bc") == (True, "")


def test_json_stdout_is_pure_json_when_course_check_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` 契约：只读路径里的诊断日志不得混进 stdout（2026-10-06 回归）。

    事故形状：`course_openable` 走真课程校验链（`course_args` → `apply_course`），它的
    `[course] …` 诊断行落到 stdout ⇒ 控制台 `JSON.parse` 第一个字符就是 `[` ⇒ 整块读面
    不可用（顶部 pill 变 `it- 视图不可用`）。控制台消费的就是这一份 stdout
    （`dashboard/server/api/loop-queue.ts`），故契约用例必须在**真链**上跑——把
    `course_openable` monkeypatch 掉就再也照不到这种回归。
    """
    curricula = _tmp_curricula(tmp_path, monkeypatch)
    (curricula / f"{FIXTURE}.jsonc").write_text(_real_source(), encoding="utf-8")
    traj = tmp_path / "traj"
    d = traj / FIXTURE
    d.mkdir(parents=True)
    (d / "training_log.jsonl").write_text('{"event": "run_start", "iter": 0}\n', encoding="utf-8")
    (d / "training-enabled.txt").write_text("", encoding="utf-8")

    import trainer.run_rl_cluster as cluster

    assert cluster.main(["--traj-root", str(traj), "--json"]) == 0
    out = capsys.readouterr()
    body = json.loads(out.out.strip())  # 不可解析即失败（这里就是回归点）
    assert [r["course"] for r in body["courses"]] == [FIXTURE]
    assert body["courses"][0]["openable"] == {"ok": True, "reason": ""}


def _never(task: Task, queue: CourseQueue) -> TaskResult:
    """只读 build_rows 不得执行任务体（类型形状与 `Supervisor.executor` 一致，mypy 才过）。"""
    raise AssertionError("只读 build_rows 不得执行任务体")


def test_build_rows_exports_openable_and_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json` 契约：每行带 `openable`，且不可开课 ⇒ `waiting.kind=blocked`（不再误报 ready）。"""
    import trainer.run_rl_cluster as cluster
    from worker.loop_scheduler import Supervisor

    d = tmp_path / "c-x"
    d.mkdir()
    (d / "training_log.jsonl").write_text(
        '{"event": "run_start", "iter": 0}\n{"event": "iteration", "iter": 0}\n',
        encoding="utf-8",
    )
    sup = Supervisor(executor=_never, planner=lambda *a: [], capacities={})

    monkeypatch.setattr(cluster, "course_openable", lambda _c: (False, "SystemExit: boom"))
    (blocked_row,) = cluster.build_rows(["c-x"], str(tmp_path), sup)
    assert blocked_row["openable"] == {"ok": False, "reason": "SystemExit: boom"}
    assert blocked_row["waiting"]["kind"] == "blocked"
    assert "boom" in blocked_row["waiting"]["text"]

    monkeypatch.setattr(cluster, "course_openable", lambda _c: (True, ""))
    (ok_row,) = cluster.build_rows(["c-x"], str(tmp_path), sup)
    assert ok_row["openable"] == {"ok": True, "reason": ""}
    assert ok_row["waiting"]["kind"] != "blocked"
