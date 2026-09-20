"""R2c-3：「在等什么」判据（`rl/loop_plan.py::waiting_state`）+ CLI 行组装（`build_rows`）。

这一列是控制台调度器卡片的唯一新增语义，也是 `--json` 契约的一部分（dashboard
`server/api/loop-queue.ts` 消费），所以要钉死三件事：

1. **取值域与优先级**：inflight > collect > idle > ready —— 运维唯一能干预的是
   「进程外的等待」（远端回传），它必须排第一；
2. **未知事实不编分母**：`games_planned=0`（CLI 今天传送的值）时不得报「x/0 局」，
   只报已落局数——与 `already_done` 同一条规矩（算不出来的事实不得当成完成）；
3. **盘上事实驱动**：`build_rows` 真的去读账本 / commit journal / shard 目录，
   CLI 表与控制台拿到的行是同一份（含 `waiting.text`）。

不跑 rollout / PPO —— `build_rows` 是纯读盘（dry-run 的执行体永不被调用）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.commit_journal import CommitJournal
from rl.loop_plan import (
    WAIT_COLLECT,
    WAIT_IDLE,
    WAIT_INFLIGHT,
    WAIT_READY,
    enabled_courses,
    waiting_state,
)
from rl.loop_scheduler import CourseQueue, Supervisor
from rl.loop_tasks import Task, TaskResult
from run_rl_cluster import build_rows, main


def _state(**kw: object) -> tuple[str, str]:
    """默认值填好，只有被测的那一项需要显式给。"""
    base: dict[str, object] = {
        "inflight": [],
        "games_settled": 0,
        "games_planned": 0,
        "pending": 13,
        "current": "rollout",
    }
    base.update(kw)
    return waiting_state(**base)  # type: ignore[arg-type]


# ────────────────────────────── 在飞（进程外等待） ──────────────────────────────


def test_inflight_wins_and_carries_job_identity() -> None:
    kind, text = _state(
        inflight=[
            {"phase": "ppo", "round": 37, "jid": "abcdef0123456789", "dispatch": "push"},
        ],
        games_settled=150,
        games_planned=150,
    )
    assert kind == WAIT_INFLIGHT
    # 运维要从这一行直接定位「在等谁」：相位 + 轮次 + job_id 前 12 位 + 运输方式
    assert "ppo@37" in text and "jid=abcdef012345" in text and "via push" in text


def test_inflight_without_jid_does_not_invent_one() -> None:
    _, text = _state(inflight=[{"phase": "ppo", "round": 3}])
    assert "ppo@3" in text and "jid=" not in text and "via" not in text


def test_multiple_inflight_is_a_count_not_a_wall_of_text() -> None:
    _, text = _state(
        inflight=[{"phase": "ppo", "round": i} for i in (37, 38, 39, 40)],
    )
    assert "4 个远端任务回传" in text
    assert "ppo@37、ppo@38、ppo@39" in text
    assert "ppo@40" not in text  # 只列前三条（要细节走 CLI 的 inflight 行）


def test_inflight_outranks_collection_progress() -> None:
    """一条腿上既有未落齐的采集、又有在飞 job 时，报**外部**那个（可干预的那个）。"""
    kind, _ = _state(inflight=[{"phase": "ppo", "round": 1}], games_settled=3)
    assert kind == WAIT_INFLIGHT


# ────────────────────────────── 采集 / 空闲 / 可推进 ──────────────────────────────


def test_collect_reports_denominator_only_when_it_is_known() -> None:
    kind, text = _state(games_settled=120, games_planned=150)
    assert kind == WAIT_COLLECT and "120/150" in text


def test_collect_without_plan_never_invents_a_denominator() -> None:
    """★ CLI 今天传 `games_planned=0`（盘上没记计划局数）——此时不得出现 `x/0`。"""
    kind, text = _state(games_settled=78)
    assert kind == WAIT_COLLECT and "78" in text
    assert "/" not in text and "78/0" not in text


def test_quota_met_is_not_a_wait() -> None:
    kind, text = _state(games_settled=150, games_planned=150)
    assert kind == WAIT_READY and "rollout" in text


def test_zero_settled_is_not_collecting() -> None:
    """本轮目录还没产出（0 局）⇒ 这不是「采集中」，是「还没开始」。"""
    kind, _ = _state(games_settled=0)
    assert kind == WAIT_READY


def test_empty_queue_is_idle() -> None:
    kind, text = _state(pending=0, current="")
    assert kind == WAIT_IDLE and "无待办" in text


def test_ready_names_the_next_step() -> None:
    kind, text = _state(current="prepare_iter")
    assert kind == WAIT_READY and "prepare_iter" in text


# ────────────────────────────── CLI 行组装（真读盘） ──────────────────────────────


def _make_course(root: Path, course: str, *, it: int, verdict: str = "OK") -> Path:
    """造一个「看起来跑过 it-1 轮」的课程目录（账本 + 本轮 shard）。"""
    traj = root / course
    traj.mkdir(parents=True, exist_ok=True)
    rows = [
        {"event": "run_start", "iter": 0},
        {"event": "iteration", "iter": it - 1, "samples": 100},
        {"event": "gate_verdict", "verdict": verdict},
    ]
    (traj / "training_log.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return traj


def _never(task: Task, queue: CourseQueue) -> TaskResult:
    raise AssertionError("dry-run 不得执行任务体")


def _supervisor() -> Supervisor:
    return Supervisor(executor=_never, planner=lambda c, it, q: [], capacities={})


def test_build_rows_reads_ledger_pointer_and_shards(tmp_path: Path) -> None:
    traj = _make_course(tmp_path, "c4-dodge", it=5)
    # 本轮已落 3 局（有 manifest 即完整落盘——与断点续跑同判据）
    for i in range(3):
        d = traj / "it5" / f"rl_s1_seed{i}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
    rows = build_rows(["c4-dodge"], str(tmp_path), _supervisor())
    (r,) = rows
    assert r["course"] == "c4-dodge"
    assert r["it"] == 5  # 指针 = 账本尾行 + 1（下一轮）
    assert r["facts"]["games_settled"] == 3
    assert r["facts"]["last_verdict"] == "OK"
    assert r["waiting"]["kind"] == WAIT_COLLECT and "3" in r["waiting"]["text"]


def test_build_rows_reports_inflight_job_from_commit_journal(tmp_path: Path) -> None:
    """在飞集来自 `it<N>/commit_journal.jsonl`——「上一轮在等哪个 job」的唯一盘上痕迹。"""
    traj = _make_course(tmp_path, "c5-tick", it=8)
    (traj / "it8").mkdir(parents=True, exist_ok=True)
    # 用生产写入器造 fixture（不手拼行格式——行格式变了这里要跟着红，而不是静默绿）
    j = CommitJournal(traj / "it8" / "commit_journal.jsonl")
    j.start("ppo_remote", "8")
    j.attach("ppo_remote", "8", jid="job-xyz-42", dispatch="push")
    (r,) = build_rows(["c5-tick"], str(tmp_path), _supervisor())
    assert r["waiting"]["kind"] == WAIT_INFLIGHT
    assert "job-xyz-42" in r["waiting"]["text"]
    assert [x["jid"] for x in r["inflight"]] == ["job-xyz-42"]


def test_build_rows_state_comes_from_the_scheduler(tmp_path: Path) -> None:
    """队列状态由 `Supervisor.add_course` 判定（不在 build_rows 里重写「有任务=ready」）。"""
    _make_course(tmp_path, "tiny-a", it=2)
    sup = _supervisor()
    rows = build_rows(["tiny-a"], str(tmp_path), sup)
    assert rows[0]["state"] == sup.courses["tiny-a"].state == "ready"
    assert rows[0]["current"] == rows[0]["pending"][0]


def test_build_rows_handles_course_without_any_disk_state(tmp_path: Path) -> None:
    """未开训 / 目录被清场的课程不得让整个读面炸（空账本 + 无目录）。"""
    (tmp_path / "empty").mkdir()
    (r,) = build_rows(["empty"], str(tmp_path), _supervisor())
    # 空账本 ⇒ 指针从 1 起（LedgerView.next_it 的默认值就是「第一轮」）
    assert r["it"] == 1 and r["inflight"] == []
    assert r["waiting"]["kind"] in (WAIT_IDLE, WAIT_READY)


# ────────────────────── 课程表为空：`--json` 仍回契约形状（2026-09-20） ──────────────────────


def test_json_keeps_the_contract_shape_when_no_course_is_opened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**空课程表是默认稳态**：默认表 = 「已开课」的课，而开课是显式动作（用户 2026-09-20）。

    控制台把 `--json` 的 stdout 直接 `JSON.parse`（`dashboard/server/api/loop-queue.ts`）——
    空表回一行人话（“[cluster] 下没有已开课的课程”）就会让它报「输出不可解析」的红错，
    而那正是启动后、开第一门课之前**长期存在**的状态。形状在所有分支里必须一致（`courses`
    与 `pools` 两把键都在），否则洞会在最不该出错的那一瞬间露出来。
    """
    assert main(["--traj-root", str(tmp_path), "--json"]) == 0
    body = json.loads(capsys.readouterr().out.strip())  # 不可解析即失败（这里就是回归点）
    assert body["courses"] == []
    assert "local_ppo" in body["pools"]  # 池容量是进程事实，与有没有课无关


def test_discovery_defaults_to_opened_courses_only(tmp_path: Path) -> None:
    """只读课程表的默认来源 = **已开课**（账本 ∧ `training-enabled.txt`）。

    有账本 ≠ 在训：tmp/ 下每门历史课都有账本（2026-09-20 报障「一堆课程正在训练」的成因）。
    """
    for name, opened in (("opened", True), ("history", False)):
        d = tmp_path / name
        d.mkdir()
        (d / "training_log.jsonl").write_text("", encoding="utf-8")
        if opened:
            (d / "training-enabled.txt").write_text("", encoding="utf-8")
    assert enabled_courses(tmp_path) == ["opened"]
