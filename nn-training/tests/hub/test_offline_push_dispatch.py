"""tests/hub/test_offline_push_dispatch.py —— hub 侧离线 push 派发器（W5）的 loopback 用例。

这里跑的是**真的两半**：真的 `ThreadingHTTPServer`（`remote.offline_push_face` 那一份）
+ 真的 `OfflinePushDispatcher`（真的 `urllib` 出站、真的 zip、真的 `store_offline_*`）。
注入的只有两件：

  * **假训练**：worker 的 starter 直接在 outbox 里写「一轮 + 段末」，不跑 `run_loop`
    （那是 W4 的 e2e 要覆盖的面，不是这里要判的「hub 会不会拉」）；
  * **假墙钟**：运行期节拍用 `pump_sec`/`heartbeat_sec` 的小数值，等待用有界轮询。

判据按**盘上的事实**取（hub 的 `offline/<run>/it-NNN/`、worker 的 outbox 文件、租约），
不按日志措辞：措辞会改，事实不会。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from common.http_respond import respond_json
from common.protocol import (
    COURSE_ENABLE_MARKER,
    OFFLINE_PUSH_PACK_DIR,
    OFFLINE_PUSH_PROTO,
    OFFLINE_PUSH_RESUME_DIR,
    OFFLINE_PUSH_TASK_NAME,
)
from hub import offline_push as disp_mod
from hub.offline_ingest import ingest_offline_artifact
from hub.offline_push import OfflinePushDispatcher, OfflinePushWorkers
from hub.server import _HubQueue, _JobStore
from remote.offline_push_face import PushFaceState, make_face_server

TOKEN = "t0k3n"
COURSE = "c5-gae"


# ------------------------------------------------------------------ 夹具


def _mk_hub(tmp_path: Path, *, pack: bool = True) -> tuple[_HubQueue, Path]:
    """一个「在训 + 有包」的离线课程（发现模式：课程根 = tmp_path）。"""
    course_dir = tmp_path / COURSE
    course_dir.mkdir(parents=True, exist_ok=True)
    (course_dir / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    store = _JobStore(course_dir / "remote-jobs", course_dir / "training_log.jsonl")
    hub = _HubQueue({COURSE: store}, order=[COURSE], discover_root=tmp_path)
    pack_path = course_dir / f"task-{COURSE}.zip"
    if pack:
        # 真包的最小形状：索引（run_id 是 assignment 权威面的起点）+ 计划（end_it）。
        with zipfile.ZipFile(pack_path, "w") as zf:
            zf.writestr(
                "tools.task.json",
                json.dumps({"run_id": "run-1", "it": 0, "commit": "c0ffee"}),
            )
            zf.writestr("plan.json", json.dumps({"start_it": 0, "end_it": 4}))
    return hub, pack_path


def _offline_root(hub: _HubQueue) -> Path:
    """`<job_root>/offline`（`job_root_of` 的注解是 `Path | None`；这里一次性断言掉）。"""
    root = hub.job_root_of(COURSE)
    assert root is not None
    return root / "offline"


def _artifact_body(
    *, run_id: str, it: int, weights: bytes = b"W" * 64, course: str = COURSE
) -> dict:
    """与 `deliver_parts.build_round_body` 同形的**最小** wire body（够 `store_offline_artifact`）。"""
    fp = hashlib.sha256(weights).hexdigest()
    return {
        "course": course,
        "run_id": run_id,
        "it": int(it),
        "weights_json": base64.b64encode(weights).decode("ascii"),
        "weights_fp": fp,
        "row": {"it": int(it), "run_id": run_id, "weights_fp": fp, "opt_bytes": 0},
        "eval_rows": [],
    }


def _result_body(*, run_id: str, it_end: int, course: str = COURSE) -> dict:
    return {
        "course": course,
        "run_id": run_id,
        "it_end": int(it_end),
        "state": "done",
        "end_it_reached": False,
        "seg_end_kind": "",
        "delivered": int(it_end),
        "summary": {},
    }


class _Worker:
    """一台「离线 push worker」：真 face 服务 + 一个把 outbox 写好的假训练。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        run_id: str = "run-1",
        round_it: int = 1,
        body_run_id: str | None = None,
        write_final: bool = True,
        finish_now: bool = True,
        metrics: bool = False,
        oversize: bool = False,
        late_round: bool = False,
    ) -> None:
        self.run_id = run_id
        self.round_it = round_it
        self.body_run_id = body_run_id or run_id
        self.write_final = write_final
        self.finish_now = finish_now
        self.metrics = metrics
        self.oversize = oversize
        self.late_round = late_round
        self._late_body: tuple[int, bytes] | None = None
        self.task: dict | None = None
        self.state = PushFaceState(work_dir=tmp_path / "worker", log=lambda _m: None)
        self.state.set_starter(self._starter)
        self.server = make_face_server(self.state, 0, TOKEN, host="127.0.0.1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _starter(self, task: dict, task_dir: Path, outbox: Path) -> None:
        self.task = dict(task)
        outbox.mkdir(parents=True, exist_ok=True)
        rounds = outbox / "rounds"
        rounds.mkdir(exist_ok=True)
        rounds_doc: dict[str, dict] = {}
        if self.oversize:
            # 超 `OFFLINE_ARTIFACT_BODY_MAX` 的轮：没有权重体，只有统计（F5 的唯一读数面）。
            rounds_doc[str(self.round_it)] = {
                "rev": 0,
                "file": "",
                "bytes": 0,
                "sha256": "",
                "oversize": True,
            }
        else:
            body = _artifact_body(run_id=self.body_run_id, it=self.round_it)
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            (rounds / f"it{self.round_it:04d}.json").write_bytes(raw)
            rounds_doc[str(self.round_it)] = {
                "rev": 0,
                "file": f"rounds/it{self.round_it:04d}.json",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "oversize": False,
            }
        if self.metrics:
            self._write_metrics(outbox, self.round_it)
        if self.late_round:
            # 账本先声明的第二轮，body 稍后才落（`write_late_round`）——F1 的积压形状。
            it2 = self.round_it + 1
            raw2 = json.dumps(
                _artifact_body(run_id=self.body_run_id, it=it2), ensure_ascii=False
            ).encode("utf-8")
            self._late_body = (it2, raw2)
            rounds_doc[str(it2)] = {
                "rev": 0,
                "file": f"rounds/it{it2:04d}.json",
                "bytes": len(raw2),
                "sha256": hashlib.sha256(raw2).hexdigest(),
                "oversize": False,
            }
        (outbox / "state.json").write_text(
            json.dumps(
                {
                    "rounds": rounds_doc,
                    "final_ready": True,
                    "final_acked": False,
                    "updated_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        (outbox / "progress.json").write_text(
            json.dumps(
                {
                    "seq": 1,
                    "at": time.time(),
                    "kind": "round",
                    "it": self.round_it,
                    "done": 10,
                    "total": 10,
                }
            ),
            encoding="utf-8",
        )
        if self.write_final:
            (outbox / "final.json").write_text(
                json.dumps(_result_body(run_id=self.run_id, it_end=self.round_it)),
                encoding="utf-8",
            )
        if self.finish_now:
            self.state.note_child_exit(0)

    def _write_metrics(self, outbox: Path, it: int) -> None:
        """写这一轮的统计体（`/offline/push/metrics` 的读面；F5）。"""
        fp = hashlib.sha256(b"W" * 64).hexdigest()
        doc = {
            "run_id": self.run_id,
            "course": COURSE,
            "rows": [
                {
                    "it": it,
                    "weights_fp": fp,
                    "row": {"it": it, "run_id": self.run_id, "weights_fp": fp},
                }
            ],
        }
        (outbox / "metrics").mkdir(exist_ok=True)
        (outbox / "metrics" / f"it{it:04d}.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )

    def write_late_round(self) -> None:
        """补上 `late_round` 那一轮的 body（模拟「训练刚写到第二轮」；F1 的积压解除点）。"""
        assert self._late_body is not None and self.state.outbox is not None
        it, raw = self._late_body
        (self.state.outbox / "rounds" / f"it{it:04d}.json").write_bytes(raw)

    def finish(self) -> None:
        """补上段末（用于「hub 重启后还在拉」那条用例：先让 worker 停在运行态）。"""
        outbox = self.state.outbox
        assert outbox is not None
        (outbox / "final.json").write_text(
            json.dumps(_result_body(run_id=self.run_id, it_end=self.round_it)),
            encoding="utf-8",
        )
        doc = json.loads((outbox / "state.json").read_text(encoding="utf-8"))
        doc["final_ready"] = True
        (outbox / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        self.state.note_child_exit(0)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _wait(pred, *, timeout: float = 30.0, step: float = 0.05) -> bool:
    """轮询一个谓词（「主路径」用例等的是**派发器自己的进度**，不是等对方先跑）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)  # sleep-ok: 轮询步长 —— 等 pump 线程把轮次拉完，谓词为真即退
    return False


def _dispatcher(hub: _HubQueue, tmp_path: Path) -> tuple[OfflinePushDispatcher, OfflinePushWorkers]:
    workers = OfflinePushWorkers(tmp_path / "rl-config.json", log=lambda _m: None)
    disp = OfflinePushDispatcher(
        hub,
        workers,
        TOKEN,
        root=tmp_path / "push-state",
        port=0,
        pump_sec=0.02,
        heartbeat_sec=0.02,
        cooldown_sec=60.0,
        log=lambda _m: None,
    )
    return disp, workers


# ------------------------------------------------------------------ 主路径


def test_dispatcher_dispatches_pulls_rounds_acks_and_finishes(tmp_path: Path) -> None:
    """派单 → 拉轮 → 入账 → ack（删 body）→ 段末 → release（P2 的 hub 侧 happy path）。"""
    hub, _pack = _mk_hub(tmp_path)
    worker = _Worker(tmp_path)
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)  # 探活 + 版本门
        assert workers.snapshot()[0]["proto_ok"] is True
        disp.tick(ping=False)  # 派发
        assert _wait(lambda: disp.assignments.get(COURSE) is None), "pump 没在 30s 内收摊"

        # worker 收到的单：proto/course/包名/run_id 都来自 hub 的 assignment
        assert worker.task is not None
        assert worker.task["proto"] == OFFLINE_PUSH_PROTO
        assert worker.task["course"] == COURSE
        assert worker.task["pack_name"] == f"task-{COURSE}.zip"
        assert len(worker.task["task_id"]) == 16

        # hub 盘上：`offline/<run>/it-001/` 三件里至少权重 + 行落了
        it_dir = _offline_root(hub) / "run-1" / "it-001"
        assert it_dir.is_dir(), f"没落到 {it_dir}"
        assert (it_dir / "weights.json").is_file()
        # 段末摘要覆盖写
        assert (_offline_root(hub) / "run-1" / "result.json").is_file()

        # ack 生效：worker 的 outbox 里那一轮的 body 已被删（result=true 也清了 final）
        outbox = worker.state.outbox
        assert outbox is not None
        assert not (outbox / "rounds" / "it0001.json").exists()
        assert not (outbox / "final.json").exists()

        # 收尾：租约交还、worker 回到 idle
        assert hub.offline_lease(COURSE) is None
        assert worker.state.phase == "idle"
        assert disp.dispatched == 1 and disp.conflicts == 0
    finally:
        disp.stop()
        worker.close()


def test_hub_restart_resumes_an_assignment_without_reuploading(tmp_path: Path) -> None:
    """hub 重启：盘上的 assignment 被重新 claim（`mine`）后接着拉，**不**重派新单。"""
    hub, _pack = _mk_hub(tmp_path)
    # worker 先停在「跑着、还没收尾」——否则整单会在这一拍内跑完，没有可续的东西。
    worker = _Worker(tmp_path, write_final=False, finish_now=False)
    try:
        # 第一次派发（走到 running 后立刻停掉派发器，模拟「hub 被重启」）
        disp1, workers1 = _dispatcher(hub, tmp_path)
        workers1.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp1.tick(ping=True)
        disp1.tick(ping=False)
        assert _wait(lambda: (disp1.assignments.get(COURSE) or {}).get("phase") == "running")
        task_id = str((disp1.assignments.get(COURSE) or {}).get("task_id") or "")
        disp1.stop()
        for st in list(disp1._pumps.values()):
            st.stop.set()
        _wait(lambda: not disp1._pumps)
        rec = disp1.assignments.get(COURSE)
        assert rec is not None and rec.get("phase") == "running"
        assert (_offline_root(hub) / "run-1" / "it-001").is_dir()

        # 「新进程」：同一份 assignment 文件 + 同一台 worker
        disp2, workers2 = _dispatcher(hub, tmp_path)
        workers2.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        resumed = disp2.tick(ping=True)["resumed"]
        assert resumed == [COURSE]
        worker.finish()  # 收尾：段末出现 ⇒ pump 拉 result → release → ack
        assert _wait(lambda: disp2.assignments.get(COURSE) is None)
        assert (_offline_root(hub) / "run-1" / "result.json").is_file()
        # 同一单（task_id 没变 ⇒ 没有重新上传一份新任务）
        assert rec.get("task_id") == task_id
        assert worker.task is not None and worker.task["task_id"] == task_id
        disp2.stop()
    finally:
        worker.close()


# ------------------------------------------------------------------ 积压 / 统计 / 续跑锚点


def test_a_missing_round_body_blocks_the_final_until_caught_up(tmp_path: Path) -> None:
    """F1：账本里已经有两轮、第二轮 body 还没落 ⇒ **不许**收 final。

    final 一 ack 就会让 worker 删掉整个 outbox —— 还没拉的轮次会永久丢。
    """
    hub, _pack = _mk_hub(tmp_path)
    worker = _Worker(tmp_path, late_round=True)
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)
        disp.tick(ping=False)
        it1 = _offline_root(hub) / "run-1" / "it-001"
        assert _wait(lambda: it1.is_dir()), "it1 没入账"

        # 有界观察窗：settle 只需一拍（pump_sec=20ms）——它若没等积压，这里必然已经收摊。
        time.sleep(0.5)  # sleep-ok: 夹具模拟 —— 让 pump 至少转十几拍，判据是「一直不收摊」
        assert disp.assignments.get(COURSE) is not None, "第二轮还没拉到就收摊了（outbox 会被删）"
        assert worker.state.outbox is not None
        assert (worker.state.outbox / "final.json").exists(), "final 被提前 ack 删掉了"
        assert not (_offline_root(hub) / "run-1" / "result.json").exists()
        assert not (_offline_root(hub) / "run-1" / "it-002").exists()

        worker.write_late_round()
        assert _wait(lambda: disp.assignments.get(COURSE) is None), "补上 it2 后仍没收摊"
        assert (_offline_root(hub) / "run-1" / "it-002").is_dir()
        assert (_offline_root(hub) / "run-1" / "result.json").is_file()
        assert not (worker.state.outbox / "final.json").exists()
    finally:
        disp.stop()
        worker.close()


def _metrics_lines(hub: _HubQueue) -> list[dict]:
    mpath = _offline_root(hub) / "run-1" / "metrics.jsonl"
    assert mpath.is_file(), f"没有 {mpath}"
    return [json.loads(line) for line in mpath.read_text(encoding="utf-8").splitlines() if line]


def test_round_metrics_are_pulled_alongside_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5：有权重体的轮也要拉统计腿（同 `(it, weights_fp)` 时 hub 侧幂等跳过，不重复记账）。"""
    hub, _pack = _mk_hub(tmp_path)
    calls: list[str] = []
    orig = hub.store_offline_metrics

    def spy(course: str, body: dict) -> dict:
        calls.append(course)
        return orig(course, body)

    monkeypatch.setattr(hub, "store_offline_metrics", spy)
    worker = _Worker(tmp_path, metrics=True)
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)
        disp.tick(ping=False)
        assert _wait(lambda: disp.assignments.get(COURSE) is None)

        assert calls == [COURSE], "统计腿没被调用（旧行为：只拉权重体）"
        rows = _metrics_lines(hub)
        assert any(int(r["it"]) == 1 and r["weights_fp"] for r in rows)
        # ack 按 rev 收下后统计体也被删（否则 outbox 永远清不掉）
        outbox = worker.state.outbox
        assert outbox is not None
        assert not (outbox / "metrics" / "it0001.json").exists()
    finally:
        disp.stop()
        worker.close()


def test_an_oversize_round_still_delivers_its_metrics(tmp_path: Path) -> None:
    """F5 的正身：`oversize` 的轮**没有** body，统计体是它唯一的读数面（y31-null 的教训）。"""
    hub, _pack = _mk_hub(tmp_path)
    worker = _Worker(tmp_path, oversize=True, metrics=True)
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)
        disp.tick(ping=False)
        assert _wait(lambda: disp.assignments.get(COURSE) is None), "只有统计的轮把整单卡死了"

        rows = _metrics_lines(hub)
        assert [int(r["it"]) for r in rows] == [1]
        assert rows[0]["event"] == "offline_metrics", "没有权重体的轮只能靠统计腿留读数"
        # 没有权重 ⇒ 不建 `it-001/`（那是 `resume_anchor` 会误认的脏轮）
        assert not (_offline_root(hub) / "run-1" / "it-001").exists()
        assert (_offline_root(hub) / "run-1" / "result.json").is_file()
    finally:
        disp.stop()
        worker.close()


def test_task_zip_carries_resume_json_and_a_deterministic_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2 + F4：锚点要带 `resume.json`（`apply_resume_overlay` 的入口判据）；task_id 可复算。"""
    import shutil

    hub, pack = _mk_hub(tmp_path)
    adir = tmp_path / "anchor" / "it-002"
    adir.mkdir(parents=True)
    weights = b'{"w":2}'
    (adir / "weights.json").write_bytes(weights)
    (adir / "opt.tar").write_bytes(b"OPT")
    (adir / "row.json").write_text(json.dumps({"it": 2}), encoding="utf-8")
    anchor = {
        "course": COURSE,
        "it": 2,
        "run_id": "run-1",
        "source": "artifact",
        "weights_fp": hashlib.sha256(weights).hexdigest(),
        "files": {"weights": "weights.json", "opt": "opt.tar", "row": "row.json"},
        "opt_bytes": 3,
        "metrics": {"it": 2},
        "_dir": str(adir),
    }
    monkeypatch.setattr(hub, "resume_anchor", lambda _course: dict(anchor))
    disp, _workers = _dispatcher(hub, tmp_path)
    zpath = Path()
    zpath2 = Path()
    zpath0 = Path()
    try:
        zpath, meta = disp.build_task_zip(COURSE, pack, "run-1", resume_it=2)
        assert meta["resume_it"] == 2
        want = disp._task_id(COURSE, hashlib.sha256(pack.read_bytes()).hexdigest(), 2)
        assert meta["task_id"] == want and len(want) == 16
        with zipfile.ZipFile(zpath) as zf:
            task = json.loads(zf.read(OFFLINE_PUSH_TASK_NAME).decode("utf-8"))
            assert task["resume_it"] == 2 and task["task_id"] == want
            assert task["run_id"] == "run-1"
            assert zf.read(f"{OFFLINE_PUSH_PACK_DIR}/{pack.name}") == pack.read_bytes()
            resume = json.loads(zf.read(f"{OFFLINE_PUSH_RESUME_DIR}/resume.json").decode("utf-8"))
            assert resume["it"] == 2 and resume["weights_fp"] == anchor["weights_fp"]
            assert resume["files"]["weights"] == "weights.json"
            assert not any(str(k).startswith("_") for k in resume), "内部键（_dir）泄进了包"
            base = f"{OFFLINE_PUSH_RESUME_DIR}/it-002"
            assert zf.read(f"{base}/weights.json") == weights
            assert zf.read(f"{base}/opt.tar") == b"OPT"
            assert zf.read(f"{base}/row.json") == b'{"it": 2}'

        # 同一份包 + 同一个地板 ⇒ 同一个 task_id（重派/重复投递不会被当成新单）
        zpath2, meta2 = disp.build_task_zip(COURSE, pack, "run-1", resume_it=2)
        assert meta2["task_id"] == meta["task_id"]
        # 没有地板 ⇒ 不带锚点目录（也不该误读 hub 上的旧锚点）
        zpath0, meta0 = disp.build_task_zip(COURSE, pack, "run-1")
        assert meta0["task_id"] != meta["task_id"]
        with zipfile.ZipFile(zpath0) as zf:
            assert not any(n.startswith(f"{OFFLINE_PUSH_RESUME_DIR}/") for n in zf.namelist())
    finally:
        for z in (zpath, zpath2, zpath0):
            if z.name:
                shutil.rmtree(z.parent, ignore_errors=True)


def test_adoption_accepts_only_the_same_unfinished_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9/F10：上传没拿到 202（超时/409）时，确定性 task_id 让「同一单」可判。"""
    hub, _pack = _mk_hub(tmp_path)
    disp, _workers = _dispatcher(hub, tmp_path)
    try:
        disp.assignments.put(
            {
                "course": COURSE,
                "worker_id": "push:w1",
                "run_id": "run-1",
                "lease_token": "L",
                "phase": "dispatching",
            }
        )
        tid, sha = "0123456789abcdef", "s" * 64
        state: dict = {
            "phase": "running",
            "ack": False,
            "task": {"task_id": tid, "course": COURSE, "pack_sha256": sha},
        }
        monkeypatch.setattr(disp._client, "get_state", lambda _url, _key: dict(state))
        meta = {"task_id": tid, "pack_sha256": sha, "resume_it": 2}
        assert disp._adopt_existing(COURSE, "http://w", "k", meta) is True
        rec = disp.assignments.get(COURSE)
        assert rec is not None
        assert rec["task_id"] == tid and rec["phase"] == "running" and rec["resume_it"] == 2

        # 别人的单（id 不同）⇒ 不接管，账本原样
        disp.assignments.set_fields(COURSE, task_id="", phase="dispatching")
        state["task"] = {**state["task"], "task_id": "fedcba9876543210"}
        assert disp._adopt_existing(COURSE, "http://w", "k", meta) is False
        assert (disp.assignments.get(COURSE) or {}).get("task_id") == ""

        # 同 id 但已收尾（ack=true 且非 running）⇒ 重投只会 duplicate，不接管
        state["task"] = {**state["task"], "task_id": tid}
        state.update(phase="done", ack=True)
        assert disp._adopt_existing(COURSE, "http://w", "k", meta) is False
    finally:
        disp.stop()


def test_settle_without_an_answer_is_parked_then_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3：收摊时 worker 没答（None/5xx）⇒ 记 `settling` 原样重放；有答（404/409）才算完。"""
    hub, _pack = _mk_hub(tmp_path)
    disp, _workers = _dispatcher(hub, tmp_path)
    try:
        disp.assignments.put(
            {
                "course": COURSE,
                "worker_id": "push:w1",
                "task_id": "t" * 16,
                "run_id": "run-1",
                "lease_token": "L",
                "phase": "running",
                "cursor_it": 1,
                "revs": {"1": 0},
            }
        )
        rec = disp.assignments.get(COURSE)
        assert rec is not None
        monkeypatch.setattr(disp._client, "cancel_task", lambda *_a, **_k: None)
        monkeypatch.setattr(disp._client, "post_ack", lambda *_a, **_k: None)
        assert (
            disp._settle_assignment(
                COURSE,
                "http://w",
                "k",
                rec,
                cancel=True,
                free_worker=True,
                reason="conflict",
                failed=True,
            )
            is False
        )
        parked = disp.assignments.get(COURSE)
        assert parked is not None and parked["phase"] == "settling"
        assert parked["settle_reason"] == "conflict"
        assert parked["settle_cancel"] is True and parked["settle_free"] is True
        assert parked["settle_failed"] is True and parked["settle_at"] > 0

        calls: list[str] = []

        def fake_cancel(_url: str, _key: str, **_kw):
            calls.append("cancel")
            return 200, {}

        def fake_ack(_url: str, _key: str, _payload: dict, **_kw):
            calls.append("ack")
            return 200, {}

        monkeypatch.setattr(disp._client, "cancel_task", fake_cancel)
        monkeypatch.setattr(disp._client, "post_ack", fake_ack)
        assert disp._settle_retry(
            COURSE, "http://w", "k", disp.assignments.get(COURSE) or parked
        ) is True
        assert calls == ["cancel", "ack"]
        assert disp.assignments.get(COURSE) is None
    finally:
        disp.stop()


# ------------------------------------------------------------------ 拒绝面


class _OldWorker:
    """旧 worker：只有 GPU 那套 `/ping`（没有 `/offline/push/state`）。"""

    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:
                if self.path.split("?")[0] == "/ping":
                    respond_json(self, {"ok": True, "busy": False, "queued": 0, "done": 0})
                else:
                    respond_json(self, {"error": "not found"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_old_worker_without_push_endpoints_is_never_dispatched(tmp_path: Path) -> None:
    """版本门（评审 P1-7）：`/state` 404 ⇒ `proto_ok=False`，一拍都不派。"""
    hub, _pack = _mk_hub(tmp_path)
    old = _OldWorker()
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "old", "url": old.url, "authKey": TOKEN})
        assert disp.tick(ping=True)["started"] == []
        assert workers.snapshot()[0]["proto_ok"] is False
        assert disp.tick(ping=False)["started"] == []
        assert disp.assignments.snapshot() == {}
        assert hub.offline_lease(COURSE) is None  # 没有白占租约
    finally:
        disp.stop()
        old.close()


def test_missing_pack_asks_the_console_to_export_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺包 ⇒ pending_export + 触发导包（P0-4：push-only 部署的唯一导包入口），且只触发一次。"""
    hub, _pack = _mk_hub(tmp_path, pack=False)
    worker = _Worker(tmp_path)
    disp, workers = _dispatcher(hub, tmp_path)
    calls: list[str] = []

    def fake_trigger(_hub, course):
        calls.append(course)
        return {"decision": "trigger", "state": "triggered", "ok": True, "note": "ok"}

    monkeypatch.setattr(disp_mod, "trigger_pack_export", fake_trigger)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)
        disp.tick(ping=False)
        assert calls == [COURSE]
        assert hub.pending_export_of(COURSE).get("by") == "push:w1"
        # 冷却窗里不再打扰控制台（同一拍又过了一次）
        disp.tick(ping=False)
        assert calls == [COURSE]
        assert worker.task is None  # 没派单
    finally:
        disp.stop()
        worker.close()


def test_round_authority_mismatch_becomes_a_conflict_and_releases(tmp_path: Path) -> None:
    """worker 自报的 `run_id` 与 assignment 不符 ⇒ 不落盘、不推进 cursor、release（P0-5）。"""
    hub, _pack = _mk_hub(tmp_path)
    worker = _Worker(tmp_path, body_run_id="somebody-else", write_final=False)
    disp, workers = _dispatcher(hub, tmp_path)
    try:
        workers.add({"id": "w1", "url": worker.url, "authKey": TOKEN})
        disp.tick(ping=True)
        disp.tick(ping=False)
        assert _wait(lambda: disp.assignments.get(COURSE) is None)
        assert disp.conflicts == 1
        # 没落盘：hub 上没有 run-1 的产物目录
        assert not (_offline_root(hub) / "run-1").exists()
        assert hub.offline_lease(COURSE) is None
    finally:
        disp.stop()
        worker.close()


# ------------------------------------------------------------------ 共享 ingest 的权威面


def test_ingest_injects_the_assignment_run_id_when_the_body_omits_it(tmp_path: Path) -> None:
    """体里缺 `run_id` ⇒ 用 assignment 的（旧 outbox 可能没这个键，权威在 hub）。"""
    hub, _pack = _mk_hub(tmp_path)
    body = _artifact_body(run_id="", it=2)
    body.pop("run_id")
    status, res = ingest_offline_artifact(
        hub, COURSE, body, run_id="assignment-run", log=lambda _m: None
    )
    assert status == 200 and res["status"] == "accepted"
    assert (_offline_root(hub) / "assignment-run" / "it-002").is_dir()


def test_ingest_rejects_a_body_for_another_course_without_writing(tmp_path: Path) -> None:
    hub, _pack = _mk_hub(tmp_path)
    body = _artifact_body(run_id="run-1", it=1, course="other-course")
    status, res = ingest_offline_artifact(
        hub, COURSE, body, run_id="run-1", log=lambda _m: None
    )
    assert status == 409 and "course" in str(res.get("error") or "")
    assert not (_offline_root(hub) / "run-1").exists()
