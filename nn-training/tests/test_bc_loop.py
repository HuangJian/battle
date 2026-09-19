"""rl/bc_loop.py —— BC 课程引擎（R3-4）：一轮切段可重入，让 supervisor 能带 BC 课。

只钉**性质**，不钉实现（假件：语料采集 / 发布 / 落位 / 归档 / hub 全换成假体，零真运算）：

1. **重入不重发布**（最贵的一条）——BC 的续训按 jid 存（hub `/jobs/{jid}/resume`、
   worker 本地 `bc-resume/<jid>`）⇒ 重发 = 新 jid = 从头训 = 白烧一轮 GPU 时间。
   「让位后再来问」与「进程重启后再来问」都必须先认领已有的那份 job。
2. **让位不改 task 语义**——等远端时返回 `ROUND_WAIT`（本轮**未完**，不是失败也不是完成）。
3. **账本零污染**——冒烟轮不写 `run_start`/`bc_round_completed`；真轮恰好写一次。
4. **一条指针**——`bc_round_completed`（`rl/bc_ledger.py`），不是 RL 的 `iteration`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.bc_loop as bc_loop
from remote import hub_client
from rl.bc_config import load_bc_course
from rl.bc_ledger import ROUND_DONE_EVENT, read_events
from rl.bc_loop import BcLoop, BcRuntime, find_round_job
from rl.loop_round import ROUND_NEXT, ROUND_SMOKE_STOP, ROUND_WAIT


class FakeHub:
    """假 hub：`/result` 前 `ready_after-1` 次 404（排队中），之后给结果。"""

    def __init__(self, *, ready_after: int = 2, result: dict | None = None) -> None:
        self.polls = 0
        self.ready_after = ready_after
        self.result = result if result is not None else {"metrics": {"epochs": 1}}
        self.metrics: list[dict] = []
        self.metric_calls = 0
        self.boom_metrics = False
        self.paths: list[str] = []

    def request(self, base: str, token: str, path: str, timeout: float = 0.0) -> Any:
        self.paths.append(path)
        if path.endswith("/result"):
            self.polls += 1
            if self.polls < self.ready_after:
                return 404, b"{}"
            return 200, json.dumps(self.result).encode("utf-8")
        if path.endswith("/bc-metrics"):
            self.metric_calls += 1
            if self.boom_metrics:
                raise RuntimeError("指标端点炸了")
            return 200, json.dumps({"rows": self.metrics}).encode("utf-8")
        return 404, b"{}"


class Recorder:
    """假件的调用记录（发布次数是「重入不重发布」的直接证据）。"""

    def __init__(self) -> None:
        self.published: list[int] = []
        self.collected: list[int] = []
        self.landed: list[tuple[int, str]] = []
        self.archived: list[int] = []
        self.submitted: list[str] = []
        self.local_train: list[int] = []
        self.marked: list[str] = []
        self.halts: list[tuple[str, str, bool]] = []
        self._n = 0

    def new_jid(self) -> str:
        self._n += 1
        return f"jid{self._n}"


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    h = FakeHub()
    monkeypatch.setattr(hub_client, "_request", h.request)
    return h


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()

    def collect_corpus(course: Any, course_fp: str, corpus_fp: str, it: int, *a: Any, **kw: Any):
        r.collected.append(it)

    def publish_bc_job(**kw: Any) -> dict:
        it = int(kw["it"])
        jid = r.new_jid()
        r.published.append(it)
        job_root = Path(kw["job_root"])
        (job_root / jid).mkdir(parents=True, exist_ok=True)
        manifest = {"job_id": jid, "it": it, "kind": "bc", "course_name": "x", "mode": "bc"}
        (job_root / jid / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # push 直推要提交 payload/code（真 publish 会写这两个文件；假件补上）
        (job_root / jid / "payload.tar.xz").write_bytes(b"payload")
        (job_root / "code.zip").write_bytes(b"code")
        return manifest

    def verify_and_land_bc(result: dict, manifest: dict, **kw: Any) -> None:
        it = int(kw["it"])
        r.landed.append((it, str(manifest["job_id"])))
        Path(str(kw["out_weights"])).parent.mkdir(parents=True, exist_ok=True)
        Path(str(kw["out_weights"])).write_text("{}", encoding="utf-8")

    def archive_round(course: Any, key: str, it: int, out: str, metrics: dict, *, smoke: bool):
        r.archived.append(int(it))

    def train_local_bc(course: Any, data: Path, out: str, **kw: Any) -> dict:
        it = int(kw.get("it") or 0)
        r.local_train.append(it)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text("{}", encoding="utf-8")
        return {"epochs": 1, "move_acc": 0.5}

    class FakeSubmit:
        @staticmethod
        def submit_job(
            url: str, token: str, manifest: dict, payload: bytes, code: bytes, **kw: Any
        ):
            r.submitted.append(str(manifest["job_id"]))

    import remote.push_client as push_client

    monkeypatch.setattr(bc_loop, "collect_corpus", collect_corpus)
    monkeypatch.setattr(bc_loop, "publish_bc_job", publish_bc_job)
    monkeypatch.setattr(bc_loop, "verify_and_land_bc", verify_and_land_bc)
    monkeypatch.setattr(bc_loop, "archive_round", archive_round)
    monkeypatch.setattr(bc_loop, "train_local_bc", train_local_bc)
    monkeypatch.setattr(bc_loop, "mark_job_completed", lambda p, jid: r.marked.append(str(jid)))
    monkeypatch.setattr(push_client, "submit_job", FakeSubmit.submit_job)

    # 收官路径的 hub 停机令：真实调用会打网络
    def fake_halt(base: str, token: str, halt: bool, **kw: Any) -> bool:
        r.halts.append((base, token, bool(halt)))
        return True

    monkeypatch.setattr(hub_client, "set_cloud_halt", fake_halt)
    return r


def _runtime(
    tmp_path: Path,
    *,
    iters: int = 2,
    smoke: bool = False,
    transport: str = "hub",
    sub: str = "traj",
) -> BcRuntime:
    """直建运行时（解析链 `resolve_bc_runtime` 由 e2e 覆盖，这里只测引擎）。

    `sub`：同一用例里两门课必须**各用一份 traj**（共用一份账本会让断言互相污染）。
    """
    course = load_bc_course("bc-c4-v3")
    traj = tmp_path / sub
    traj.mkdir(parents=True, exist_ok=True)
    return BcRuntime(
        course=course,
        course_key="bc-c4-v3",
        course_path=Path("curricula/bc-c4-v3.bc.jsonc"),
        course_fp="f" * 64,
        course_text="{}",
        corpus_fp="c" * 64,
        traj=traj,
        out_weights=str(traj / "weights.json"),
        jsonl_path=traj / "training_log.jsonl",
        job_root=traj / "remote-jobs",
        data_root=traj / "bc-data",
        transport=transport,
        hub_url="http://hub",
        push_url="http://gpu" if transport == "push" else "",
        token="tok",
        iters=iters,
        smoke=smoke,
        wait_sec=0.0,
        run_id="bc-test",
    )


def _loop(tmp_path: Path, **kw: Any) -> BcLoop:
    return BcLoop(_runtime(tmp_path, **kw), log=lambda _m: None, sleep=lambda _s: None)


# ------------------------------------------------------------ 指针 / 生命周期


def test_entry_is_a_thin_shell_over_the_engine() -> None:
    """`run_bc.py` 不得再养第二份编排——它必须与引擎共用**同一个对象**。

    这条是「一轮只有一份实现」的结构性护栏：谁把编排体搬回入口（或复制一份），谁就给了
    「重发布 ⇒ bc-resume 失效 ⇒ 从头训」第二次机会。
    """
    import run_bc

    assert run_bc.BcLoop is bc_loop.BcLoop
    assert run_bc.resolve_bc_runtime is bc_loop.resolve_bc_runtime
    assert run_bc.bc_argparser is bc_loop.bc_argparser
    # 搬走的编排体不得在入口里复活（旧私有名一个都不该再有定义）
    for gone in ("collect_corpus", "publish_bc_job", "wait_bc_round", "_finish_all_rounds"):
        assert not hasattr(run_bc, gone), f"{gone} 不该再住在 run_bc（它归 rl/bc_loop.py）"


def test_ledger_next_it_reads_bc_round_completed(tmp_path: Path) -> None:
    """指针来自 `bc_round_completed`——不是 RL 的 `iteration`（BC 课被按 RL 读会永远停在 it1）。"""
    loop = _loop(tmp_path, iters=4)
    loop.runtime.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    bc_loop.append_ledger(loop.runtime.jsonl_path, {"event": "iteration", "iter": 3})
    assert loop.ledger_next_it() == 1  # RL 事件不作数
    bc_loop.append_ledger(loop.runtime.jsonl_path, {"event": ROUND_DONE_EVENT, "it": 1})
    bc_loop.append_ledger(loop.runtime.jsonl_path, {"event": ROUND_DONE_EVENT, "it": 2})
    assert loop.ledger_next_it() == 3
    assert bc_loop.bc_progress(loop.runtime.jsonl_path, 4).finished is False


def test_setup_writes_run_start_exactly_once(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._setup()
    loop._setup()
    starts = [e for e in read_events(loop.runtime.jsonl_path) if e.get("event") == "run_start"]
    assert len(starts) == 1
    assert starts[0]["runId"] == "bc-test"  # console 的分段锚字段
    assert starts[0]["course"] == "bc-c4-v3"


def test_smoke_round_writes_no_run_start(tmp_path: Path) -> None:
    """冒烟轮不得污染真轮的账本语义（不写 run_start / 不写完成事件）。"""
    loop = _loop(tmp_path, smoke=True)
    loop._setup()
    assert read_events(loop.runtime.jsonl_path) == []


def test_release_and_local_stack_hooks_are_explicit_noops(tmp_path: Path) -> None:
    """空操作要**显式实现**（引擎池的驱逐路径靠 `getattr` 调用它们）。"""
    loop = _loop(tmp_path)
    assert callable(loop.release_torch) and callable(loop._ensure_local_ppo_stack)
    loop.release_torch()  # 不抛即通过（缺了这两个方法引擎池的驱逐路径会被 getattr 静默跳过）
    loop._ensure_local_ppo_stack()


# ------------------------------------------------------------------ 一轮


def test_reentrant_wait_does_not_republish(tmp_path: Path, hub: FakeHub, rec: Recorder) -> None:
    """★ 让位后再来问：**一次发布**、wait→wait→next，账本恰好写一次完成事件。"""
    loop = _loop(tmp_path, iters=1)
    hub.ready_after = 3  # 头两次问：还在排队

    first = loop.run_one_round(1)
    second = loop.run_one_round(1)
    assert first.status == ROUND_WAIT and second.status == ROUND_WAIT
    assert first.detail.startswith("等远端 BC job 回传") and "bc@1" in first.detail
    assert rec.published == [1]  # ★ 只发布一次
    assert rec.landed == []

    third = loop.run_one_round(1)  # 结果到了
    assert third.status == ROUND_NEXT
    assert rec.published == [1] and rec.marked == ["jid1"]
    assert rec.landed == [(1, "jid1")] and rec.archived == [1]
    done = [e for e in read_events(loop.runtime.jsonl_path) if e.get("event") == ROUND_DONE_EVENT]
    assert [e["it"] for e in done] == [1]


def test_completed_round_is_skipped_without_any_publish(
    tmp_path: Path, hub: FakeHub, rec: Recorder
) -> None:
    """账本说这轮已结算（重放/重入/别的进程刚跑完）⇒ 不重做，更不重发 job。"""
    loop = _loop(tmp_path, iters=2)
    bc_loop.append_ledger(loop.runtime.jsonl_path, {"event": ROUND_DONE_EVENT, "it": 1})
    assert loop.run_one_round(1).status == ROUND_NEXT
    assert rec.published == [] and rec.collected == []


def test_round_two_runs_after_round_one_lands(tmp_path: Path, hub: FakeHub, rec: Recorder) -> None:
    """两轮的发布/落位各自一次，完成事件按序入账。"""
    loop = _loop(tmp_path, iters=2)
    hub.ready_after = 1
    assert loop.run_one_round(1).status == ROUND_NEXT
    assert loop.run_one_round(2).status == ROUND_NEXT
    assert rec.published == [1, 2] and rec.archived == [1, 2]
    done = [
        e["it"] for e in read_events(loop.runtime.jsonl_path) if e.get("event") == ROUND_DONE_EVENT
    ]
    assert done == [1, 2]


def test_metrics_ingest_metrics_only_once_and_is_ledger_visible(
    tmp_path: Path, hub: FakeHub, rec: Recorder
) -> None:
    """每 epoch 指标增量入账（控制台数据源），且**不重复**；轮询失败不打断等待。"""
    loop = _loop(tmp_path, iters=2)
    hub.ready_after = 4
    hub.metrics = [{"epoch": 1, "train_loss": 1.0}, {"epoch": 2, "train_loss": 0.5}]
    loop.run_one_round(1)
    hub.ready_after = 5
    loop.run_one_round(1)  # 第二问：rows 没变 ⇒ 不再入账
    epochs = [e for e in read_events(loop.runtime.jsonl_path) if e.get("event") == "bc_epoch"]
    assert [e["epoch"] for e in epochs] == [1, 2]

    hub.boom_metrics = True
    assert loop.run_one_round(1).status == ROUND_WAIT  # 观测面坏了不致命


def test_discovered_disk_job_is_adopted_not_republished(
    tmp_path: Path, hub: FakeHub, rec: Recorder
) -> None:
    """进程重启后再来问：盘上已有未收口 job ⇒ 认领它，**不重发布**（重发 = 从头训）。"""
    rt = _runtime(tmp_path, iters=1)
    job_root = rt.job_root
    (job_root / "oldjid").mkdir(parents=True, exist_ok=True)
    (job_root / "oldjid" / "manifest.json").write_text(
        json.dumps({"job_id": "oldjid", "it": 1, "kind": "bc"}), encoding="utf-8"
    )
    found = find_round_job(job_root, 1)
    assert found is not None and found[0] == "oldjid"

    hub.ready_after = 1
    loop = BcLoop(rt, log=lambda _m: None, sleep=lambda _s: None)
    assert loop.run_one_round(1).status == ROUND_NEXT
    assert rec.published == []  # ★ 认领，不重发布
    assert rec.landed == [(1, "oldjid")]
    assert loop.inflight_job_id(1) == "oldjid"


def test_disk_job_of_a_completed_round_is_ignored(
    tmp_path: Path, hub: FakeHub, rec: Recorder
) -> None:
    """已收口的 job（`job_completed`）不再认领——那是历史残留，不是「在等的那一份」。"""
    rt = _runtime(tmp_path, iters=1)
    (rt.job_root / "oldjid").mkdir(parents=True, exist_ok=True)
    (rt.job_root / "oldjid" / "manifest.json").write_text(
        json.dumps({"job_id": "oldjid", "it": 1, "kind": "bc"}), encoding="utf-8"
    )
    bc_loop.append_ledger(rt.jsonl_path, {"event": "job_completed", "job_id": "oldjid"})
    assert find_round_job(rt.job_root, 1, completed_jids={"oldjid"}) is None


def test_smoke_round_does_not_adopt_disk_job(tmp_path: Path, hub: FakeHub, rec: Recorder) -> None:
    """冒烟轮**不认领**盘上 job：它的语料口径/尺寸都被压缩过（复用等于拿冒烟语料冒充真语料）。"""
    loop = _loop(tmp_path, iters=1, smoke=True)
    (loop.runtime.job_root / "real").mkdir(parents=True, exist_ok=True)
    (loop.runtime.job_root / "real" / "manifest.json").write_text(
        json.dumps({"job_id": "real", "it": 1, "kind": "bc"}), encoding="utf-8"
    )
    hub.ready_after = 1
    assert loop.run_one_round(1).status == ROUND_SMOKE_STOP
    assert rec.published == [1]  # 自己发了一份（没有认领盘的）
    assert rec.archived == [1]
    assert [
        e for e in read_events(loop.runtime.jsonl_path) if e.get("event") == ROUND_DONE_EVENT
    ] == []


def test_echo_result_voids_the_round(tmp_path: Path, hub: FakeHub, rec: Recorder) -> None:
    """对端冒烟回显（占位 worker）：本轮作废，不落盘不归档不写完成事件。"""
    hub.result = {"smoke": True}
    hub.ready_after = 1
    loop = _loop(tmp_path, iters=1, smoke=True)
    assert loop.run_one_round(1).status == ROUND_SMOKE_STOP
    assert rec.landed == [] and rec.archived == []
    assert read_events(loop.runtime.jsonl_path) == []


@pytest.mark.parametrize("transport", ["local"])
def test_local_transport_closes_the_round_in_one_shot(
    tmp_path: Path, hub: FakeHub, rec: Recorder, transport: str
) -> None:
    """本机训练（`--local`）：整轮在本机做完 ⇒ 一次调用即 `ROUND_NEXT`（无让位对象）。"""
    loop = _loop(tmp_path, iters=1, transport=transport)
    assert loop.run_one_round(1).status == ROUND_NEXT
    assert rec.local_train == [1] and rec.archived == [1]
    assert rec.published == []  # 本机路径不发 job


def test_push_transport_submits_once_per_round(tmp_path: Path, hub: FakeHub, rec: Recorder) -> None:
    """push 直推：提交是内存态 ⇒ 进程内每轮**只提交一次**（让位重入不重复提交）。"""
    loop = _loop(tmp_path, iters=1, transport="push")
    hub.ready_after = 2
    assert loop.run_one_round(1).status == ROUND_WAIT
    assert loop.run_one_round(1).status == ROUND_NEXT
    assert rec.submitted == ["jid1"]  # 一次，不是两次


def test_run_blocking_skips_settled_rounds_and_finishes(
    tmp_path: Path, hub: FakeHub, rec: Recorder
) -> None:
    """单课程阻塞驱动：已结算轮跳过、两轮跑完、收官走 `finish_all_rounds`（含 `ALL DONE` 尾行）。"""
    hub.ready_after = 1
    msgs: list[str] = []
    loop = BcLoop(_runtime(tmp_path, iters=2), log=msgs.append, sleep=lambda _s: None)
    bc_loop.append_ledger(loop.runtime.jsonl_path, {"event": ROUND_DONE_EVENT, "it": 1})
    loop.run_blocking()
    assert rec.published == [2]  # it1 已结算 ⇒ 只跑 it2
    assert any("ALL DONE" in m for m in msgs)
    assert rec.halts == [("http://hub", "tok", True)]  # hub 传输 ⇒ 下发停机达令
    events = [e.get("event") for e in read_events(loop.runtime.jsonl_path)]
    assert events.count("run_complete") == 1


def test_finish_course_does_not_exit_and_skips_smoke(tmp_path: Path, rec: Recorder) -> None:
    """收官不做进程退出（单进程 supervisor 还要服务别的课）；冒烟轮不写收官事件。"""
    real = _loop(tmp_path, iters=1, sub="real")
    real.finish_course(1)
    assert rec.halts == [("http://hub", "tok", True)]
    smoke = _loop(tmp_path, iters=1, smoke=True, sub="smoke")
    smoke.finish_course(1)
    assert len(rec.halts) == 1  # 冒烟轮不重复下发
    assert read_events(smoke.runtime.jsonl_path) == []
