"""R3-4：控制台「调度器」卡片里的 **BC 行**（`rl/loop_plan.py` + `run_rl_cluster.build_rows`）。

用户口径：BC 课与 RL 课在**同一张卡片**里并列展示。要并列而不互相冒充，三件事必须成立：

1. **行上带课程种类**（`kind`）——控制台据此上标签（BC 没有门禁/verdict，也没有 13 步表，
   把它当 RL 读会得到一整套「像真的一样」的零）；
2. **指针取自 BC 自己的账本**（`bc_round_completed`，不是 RL 的 `iteration`）——否则 BC 课永远
   显示 it1；
3. **「在等什么」认得 BC 的在飞事实**（账本 `job_pending ∖ 终局` + job 目录的 dispatch）——
   BC 不写 `commit_journal`，只按 journal 找会把「正等着 GPU 回传」读成「没有外部等待」，
   那正是这张卡片唯一要回答的问题。

不跑 rollout / PPO / 真发布：`build_rows` 是纯读盘（dry-run 执行体永不被调用）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.bc_config as bc_config
from rl.bc_ledger import ROUND_DONE_EVENT
from rl.loop_plan import WAIT_IDLE, WAIT_INFLIGHT, WAIT_READY, bc_inflight
from rl.loop_scheduler import CourseQueue, Supervisor
from rl.loop_tasks import Task, TaskResult
from run_rl_cluster import _fmt_table, build_rows


def _never(task: Task, queue: CourseQueue) -> TaskResult:
    raise AssertionError("dry-run 不得执行任务体")


def _supervisor() -> Supervisor:
    return Supervisor(executor=_never, planner=lambda c, it, q: [], capacities={})


@pytest.fixture
def curricula(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 curricula 目录：`is_bc_course` 只认 `curricula/<课>.bc.jsonc` 存在。

    测试不得依赖仓根真实课程（它们会被别处重跑/改名），也不得让 `course_kind` 去读真课程表。
    """
    d = tmp_path / "curricula"
    d.mkdir(parents=True, exist_ok=True)
    (d / "bc-x.bc.jsonc").write_text('{"name": "bc-x", "kind": "bc"}', encoding="utf-8")
    monkeypatch.setattr(bc_config, "CURRICULA_DIR", d)
    return d


def _ledger(root: Path, course: str, events: list[dict]) -> Path:
    traj = root / course
    traj.mkdir(parents=True, exist_ok=True)
    (traj / "training_log.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )
    return traj


def _job(traj: Path, jid: str, *, it: int, dispatch: str = "") -> None:
    """在盘上造一份已发布 job（manifest 是 `dispatch` 的唯一来源）。"""
    d = traj / "remote-jobs" / jid
    d.mkdir(parents=True, exist_ok=True)
    m = {"job_id": jid, "it": it, "kind": "bc", "mode": "bc"}
    if dispatch:
        m["dispatch"] = dispatch
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


# ────────────────────────────── BC 行的形状 ──────────────────────────────


def test_bc_row_is_marked_and_carries_a_single_round_task(tmp_path: Path, curricula: Path) -> None:
    """`kind='bc'` + 粒度 = **单个轮任务**（13 步表是 RL 的一轮，绝不发给 BC）。"""
    _ledger(tmp_path, "bc-x", [{"event": "run_start", "runId": "r1", "iters": 3}])
    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())

    assert r["kind"] == "bc"
    assert r["it"] == 1
    assert r["current"] == "round" and r["pending"] == ["round"]
    assert r["state"] == "ready"


def test_bc_pointer_follows_bc_round_completed(tmp_path: Path, curricula: Path) -> None:
    """指针 = 第一个未完成的 BC 轮（`bc_round_completed`）；RL 的 `iteration` 不作数。"""
    _ledger(
        tmp_path,
        "bc-x",
        [
            {"event": "iteration", "iter": 9},  # RL 事件：BC 不认
            {"event": ROUND_DONE_EVENT, "it": 1},
            {"event": ROUND_DONE_EVENT, "it": 2},
        ],
    )
    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())
    assert r["it"] == 3


def test_bc_row_reports_the_inflight_gpu_job(tmp_path: Path, curricula: Path) -> None:
    """★ BC 的「在等什么」= 等云端 GPU 回传（账本 `job_pending` + manifest 的 dispatch）。

    没有这条：BC 课会显示「本轮无待办」/「无外部等待」——而它明明在等回传。
    """
    traj = _ledger(
        tmp_path,
        "bc-x",
        [
            {"event": "run_start", "runId": "r1", "iters": 3},
            {"event": "job_pending", "job_id": "job-bc-1", "it": 1},
        ],
    )
    _job(traj, "job-bc-1", it=1, dispatch="hubpush")

    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())
    assert r["waiting"]["kind"] == WAIT_INFLIGHT
    assert "bc@1" in r["waiting"]["text"] and "job-bc-1" in r["waiting"]["text"]
    assert "hubpush" in r["waiting"]["text"]
    assert [(x["jid"], x["phase"], x["round"], x["dispatch"]) for x in r["inflight"]] == [
        ("job-bc-1", "bc", "1", "hubpush")
    ]


def test_bc_completed_or_cancelled_job_is_not_inflight(tmp_path: Path, curricula: Path) -> None:
    """收口 / 作废都算终局：只排除收口会让作废的 job 永远显示在飞（读面撒谎）。"""
    traj = _ledger(
        tmp_path,
        "bc-x",
        [
            {"event": "job_pending", "job_id": "done-1", "it": 1},
            {"event": "job_completed", "job_id": "done-1"},
            {"event": "job_pending", "job_id": "gone-1", "it": 2},
            {"event": "job_cancelled", "job_id": "gone-1"},
        ],
    )
    _job(traj, "done-1", it=1)
    _job(traj, "gone-1", it=2)

    assert bc_inflight(traj) == []
    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())
    assert r["waiting"]["kind"] != WAIT_INFLIGHT
    assert r["inflight"] == []


def test_inflight_job_survives_a_missing_manifest(tmp_path: Path, curricula: Path) -> None:
    """manifest 读不到 ⇒ dispatch 未知（None），但 job **仍算在飞**（宁可少一个字段）。"""
    traj = _ledger(tmp_path, "bc-x", [{"event": "job_pending", "job_id": "no-mf", "it": 1}])
    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())
    assert r["waiting"]["kind"] == WAIT_INFLIGHT
    assert [(x["jid"], x["dispatch"]) for x in r["inflight"]] == [("no-mf", None)]
    assert not (traj / "remote-jobs" / "no-mf").exists()


def test_bc_inflight_round_filter(tmp_path: Path, curricula: Path) -> None:
    """`it=` 过滤：只报指那一轮的在飞（`waiting_state` 之外的调用方也可能按轮问）。"""
    traj = _ledger(
        tmp_path,
        "bc-x",
        [
            {"event": "job_pending", "job_id": "a", "it": 1},
            {"event": "job_pending", "job_id": "b", "it": 2},
        ],
    )
    assert [x["jid"] for x in bc_inflight(traj, it=2)] == ["b"]
    assert [x["jid"] for x in bc_inflight(traj)] == ["a", "b"]  # 默认：全课（按 (it, jid) 升序）


# ────────────────────────────── 与 RL 行并列 ──────────────────────────────


def test_rl_row_keeps_its_own_pointer_and_granularity(tmp_path: Path, curricula: Path) -> None:
    """RL 课不受影响：`kind='rl'`、指针读 `iteration`、粒度 = 13 步。"""
    _ledger(
        tmp_path,
        "c4-dodge",
        [{"event": "run_start", "iter": 0}, {"event": "iteration", "iter": 4}],
    )
    (r,) = build_rows(["c4-dodge"], str(tmp_path), _supervisor())
    assert r["kind"] == "rl"
    assert r["it"] == 5
    assert len(r["pending"]) == 13 and r["current"] == "precollect_join"


def test_two_rows_coexist_in_one_table(tmp_path: Path, curricula: Path) -> None:
    """同一张卡片里并列（用户口径）：BC 与 RL 各自的指针/粒度/在飞都取自自己的账本。"""
    bc = _ledger(
        tmp_path,
        "bc-x",
        [
            {"event": "run_start", "runId": "r1", "iters": 3},
            {"event": ROUND_DONE_EVENT, "it": 1},
            {"event": "job_pending", "job_id": "job-bc-2", "it": 2},
        ],
    )
    _job(bc, "job-bc-2", it=2)
    _ledger(tmp_path, "c4-dodge", [{"event": "iteration", "iter": 7}])

    rows = build_rows(["bc-x", "c4-dodge"], str(tmp_path), _supervisor())
    by = {r["course"]: r for r in rows}
    assert by["bc-x"]["kind"] == "bc" and by["bc-x"]["it"] == 2
    assert by["bc-x"]["waiting"]["kind"] == WAIT_INFLIGHT
    assert by["c4-dodge"]["kind"] == "rl" and by["c4-dodge"]["it"] == 8

    table = _fmt_table(rows)
    assert "kind" in table.splitlines()[0]  # 人读表也区分种类
    assert "bc 课程（轮指针 it2）" in table
    # BC 行不得摆一排 RL 的零（那读起来像真的，其实没有这一回事）
    assert "last_verdict" not in table.split("bc-x")[1].split("c4-dodge")[0]


def test_unknown_course_kind_defaults_to_rl_row(tmp_path: Path) -> None:
    """课程表里没有对应 `curricula/<课>.jsonc` 的目录（手工 tmp 目录）⇒ 按 RL 处理。

    这是刻意的保守方向：RL 是既有语义，误判成 BC 会让一个 RL 课拿到轮粒度 + 错指针；
    而误判成 RL 的 BC 课只是显示难看（它真在跑的进程仍按自己的引擎走）。
    """
    _ledger(tmp_path, "mystery", [{"event": "iteration", "iter": 2}])
    (r,) = build_rows(["mystery"], str(tmp_path), _supervisor())
    assert r["kind"] == "rl" and r["it"] == 3


def test_empty_bc_course_is_idle_not_inflight(tmp_path: Path, curricula: Path) -> None:
    """没开训的 BC 课（空账本）不得报「在等回传」——空目录进读面也得是空态。"""
    (tmp_path / "bc-x").mkdir()
    (r,) = build_rows(["bc-x"], str(tmp_path), _supervisor())
    assert r["kind"] == "bc" and r["inflight"] == []
    assert r["waiting"]["kind"] in (WAIT_IDLE, WAIT_READY)
