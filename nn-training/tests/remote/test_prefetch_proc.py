"""tests/remote/test_prefetch_proc.py —— 下行腿（预取下载/解包）的**进程模式**（2026-10-20）。

为什么有这组用例：预取的 `peek` + `download_payload`（P2）+ `unpack_payload`（单线程 lzma，
单份 300MB 级）此前全在训练进程的后台线程里跑 —— 而它**刻意**与上一轮 PPO 重叠
（`remote/prefetch.py` 头部 §0）⇒ 重叠的代价是抢同一个 GIL、同一颗核。回传腿
（`plan/ppo-worker-transfer-isolation` 上半场）已经搬走，本文件钉住下半场：

  ① **真隔离**：`fill` 只在父侧下一道命令，下载/解包在子进程里；父侧不再起填充线程；
  ② **staging 单写者**：`take` / `drop` / `release` 都是请求-应答（父侧不许自己动那些目录）；
  ③ **降级不恶化**：子进程起不来 / 半路死 ⇒ sticky 降级到进程内 `PrefetchStore`，训练照跑；
  ④ **跨进程 P1 协同**：父侧 P1 进出等待要广播给子进程（`bulk_sched.foreign_p1`）；
  ⑤ **字节形状无竞态**：命中项的 zip 走 base64（父侧读文件会被子进程的 prune 夹住）。

快层用「假子进程」（Popen 兼容面，可注入 take 结果 / 崩溃点 / 不应答的命令）；
真子进程只留一条（1 次 python 启动，规模刻意压小 —— 慢层 N 次启动会顶红邻居用例）。
"""

from __future__ import annotations

import base64
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import payload_proc as PP
from remote.payload_proc import PayloadProcess, make_prefetcher
from remote.prefetch import (
    PREFETCH_LINE_PREFIX,
    PREFETCH_MODE_ENV,
    PrefetchStore,
)

JID1 = "a1" * 8
JID2 = "b2" * 8


# ────────────────────────── 假子进程（Popen 兼容面） ──────────────────────────


class _Writer:
    def __init__(self, child: _FakePayloadChild) -> None:
        self._child = child

    def write(self, s: str) -> int:
        self._child._inbox.put(s)
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._child._inbox.put(None)


class _Reader:
    """行队列的只读面（`for line in stream`）。空串 = EOF。"""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()

    def put(self, s: str) -> None:
        self._q.put(s)

    def __iter__(self):
        while True:
            line = self._q.get()
            if line == "":
                return
            yield line

    def close(self) -> None:
        pass


class _FakePayloadChild:
    """假子进程：stdin 收命令 → 按 `take` 剧本回 `@@pf@@` 应答行。

    `silent` 里的 op **不应答**（模拟命令线程卡死）；`crash_after=N` = 处理完 N 条命令后
    直接退出（模拟 OOM / 被杀）；`boot=False` = 一启动就退（模拟「在飞的代码树里没有那个模块」）。
    """

    def __init__(
        self,
        *,
        take=None,
        boot: bool = True,
        crash_after: int | None = None,
        silent: tuple[str, ...] = (),
        pid: int = 424243,
    ) -> None:
        self.pid = pid
        self.stdin = _Writer(self)
        self.stdout = _Reader()
        self.commands: list[dict[str, Any]] = []
        self._inbox: queue.Queue[str | None] = queue.Queue()
        self._take = take or (lambda _jid: None)
        self._crash_after = crash_after
        self._silent = set(silent)
        self._boot = boot
        self._rc: int | None = None
        self._thread = threading.Thread(target=self._run, name=f"fake-payload-{pid}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        if not self._boot:
            self._rc = 1
            self.stdout.put("")
            return
        self.stdout.put("[payload] boot ok 424243\n")
        while True:
            item = self._inbox.get()
            if item is None:
                break
            cmd = json.loads(item)
            self.commands.append(cmd)
            op = str(cmd.get("op") or "")
            if op == "shutdown":
                self._rc = 0
                self.stdout.put(
                    PREFETCH_LINE_PREFIX + json.dumps({"id": cmd.get("id"), "ok": True}) + "\n"
                )
                break
            if op in self._silent:
                continue
            self.stdout.put(PREFETCH_LINE_PREFIX + json.dumps(self._answer(cmd)) + "\n")
            # 「处理完第 N 条就死」：在**应答之后**退（模拟 OOM / 被杀时手上那条刚完）
            if self._crash_after is not None and len(self.commands) >= self._crash_after:
                self._rc = 1
                break
        if self._rc is None:
            self._rc = 0
        self.stdout.put("")

    def _answer(self, cmd: dict[str, Any]) -> dict[str, Any]:
        rid = cmd.get("id")
        op = str(cmd.get("op") or "")
        if op == "init":
            return {"id": rid, "ok": True, "pruned": 0}
        if op == "stopfill":
            return {"id": rid, "ok": True, "joined": True}
        if op == "take":
            return {"id": rid, "ok": True, "rec": self._take(str(cmd.get("jid") or ""))}
        return {"id": rid, "ok": True}

    # ---- Popen 兼容面 ----
    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int | None:
        self._thread.join(timeout=timeout)
        return self._rc

    def terminate(self) -> None:
        self._rc = -15
        self._inbox.put(None)

    def kill(self) -> None:
        self._rc = -9


def _until(pred, timeout: float = 5.0) -> bool:
    """等一个状态（假子进程的处理是**异步**的：断言前必须等它真吃到命令）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是子进程线程处理完一条命令这个状态，5s 只当挂起兜底）
        time.sleep(0.005)
    return bool(pred())


def _proc(
    tmp_path: Path, *, logs: list[str] | None = None, **kw
) -> tuple[PayloadProcess, _FakePayloadChild]:
    """造一个进程模式代理 + 假子进程（**不**走真 `Popen`）。"""
    box: dict[str, _FakePayloadChild] = {}

    def _spawn(_argv: list[str]) -> Any:  # 假子进程只是 Popen 的**兼容面**（mypy 走 ``Any``）
        box["child"] = _FakePayloadChild(**kw)
        return box["child"]

    out = logs if logs is not None else []
    pf = PayloadProcess(tmp_path / "work", staged_max=2, log=out.append, spawn=_spawn)
    pf.start()
    return pf, box["child"]


# ────────────────────────── ① 往返：fill / take / drop / release ──────────────────────────


def test_fill_only_sends_a_command_and_the_child_stages(tmp_path: Path) -> None:
    """**核心契约**：父侧只下一道 `fill` 命令（不起线程）；参数逐字到子进程。"""
    pf, child = _proc(tmp_path)
    try:
        assert pf.fills_out_of_process, "进程模式没起效（还在父进程里）"
        assert pf.start_fill(
            base_url="http://hub",
            token="sekret",
            worker_id="w1",
            role="online",
            depth=2,
            unpack_n=1,
            skip={JID1},
        )
        assert _until(lambda: any(c.get("op") == "fill" for c in child.commands)), (
            f"没下到子进程：{child.commands}"
        )
        fill = [c for c in child.commands if c.get("op") == "fill"]
        assert fill[0]["token"] == "sekret", "token 没走命令通道"
        assert fill[0]["skip"] == [JID1]
        assert (fill[0]["depth"], fill[0]["unpack_n"]) == (2, 1)
        pf.stop_fill()
        assert _until(lambda: any(c.get("op") == "stopfill" for c in child.commands))
    finally:
        pf.close()


def test_take_returns_staged_path_and_decodes_zip_bytes(tmp_path: Path) -> None:
    """两种形状：staged ⇒ 只交路径；字节 ⇒ base64 解回 `payload_zip`（无文件竞态）。"""
    payload = b"PK\x03\x04" + b"\x00" * 32

    def _take(jid: str) -> dict | None:
        if jid == JID1:
            return {
                "staged_dir": str(tmp_path / "work" / "prefetch" / JID1 / "shards"),
                "payload_nbytes": 128,
                "unpack_sec": 1.25,
                "blob_sha": "sha1",
            }
        if jid == JID2:
            return {
                "payload_b64": base64.b64encode(payload).decode("ascii"),
                "blob_sha": "sha2",
            }
        return None

    pf, _child = _proc(tmp_path, take=_take)
    try:
        got = pf.take(JID1)
        assert got is not None and got["staged_dir"].endswith("shards")
        assert got["unpack_sec"] == 1.25
        blob = pf.take(JID2)
        assert blob is not None and blob["payload_zip"] == payload
        assert sorted(blob) == ["blob_sha", "payload_zip"], "内部编码字段不许漏给调用方"
        assert pf.take("nope") is None
    finally:
        pf.close()


def test_drop_release_and_prune_are_requests(tmp_path: Path) -> None:
    """暂存区的**唯一写者**是子进程：父侧三个动作都必须变成命令（不许自己动目录）。"""
    pf, child = _proc(tmp_path)
    try:
        pf.drop(JID1)
        pf.release_handoff()
        pf.release_handoff(JID2)
        pf.prune()
        assert _until(lambda: len(child.commands) >= 5), child.commands
        ops = [c.get("op") for c in child.commands]
        assert ops.count("drop") == 1 and ops.count("release") == 2 and ops.count("prune") == 1
        rel = [c for c in child.commands if c.get("op") == "release"]
        assert rel[0]["jid"] == "", "release_handoff() 无参 = 全放（空串约定）"
        assert rel[1]["jid"] == JID2
    finally:
        pf.close()


# ────────────────────────── ② 降级（起不来 / 半路死） ──────────────────────────


def test_boot_failure_degrades_to_in_process_store(tmp_path: Path) -> None:
    """子进程起不来 ⇒ sticky 降级到进程内 `PrefetchStore`（预取照常，训练不受影响）。"""
    logs: list[str] = []
    pf, _child = _proc(tmp_path, logs=logs, boot=False)
    try:
        assert not pf.fills_out_of_process
        st = pf.status()
        assert st["degraded"] and st["mode"] == "thread"
        # 降级的**原因**是状态面字段（不是日志文本）：日志只是给人读的那一份
        assert st["degrade_reason"], "降级必须留下原因（否则后人只能看到「变慢了」）"
        # 降级后**逐字**像 PrefetchStore：填充线程那几个方法都得在（`__getattr__` 转发）
        assert isinstance(pf._local, PrefetchStore)
        assert pf.held() == set() and pf.stats()["held"] == 0
        # `start_fill` 明确返回 False（`job_round` 据此起旧线程）
        assert pf.start_fill(base_url="", token="") is False
        assert pf.take(JID1) is None
    finally:
        pf.close()


def test_child_death_midflight_degrades_instead_of_hanging(tmp_path: Path) -> None:
    """子进程半路死 ⇒ 读者线程当场降级；后续 take/drop 走进程内，绝不挂住调用方。"""
    logs: list[str] = []
    pf, child = _proc(tmp_path, logs=logs, crash_after=1)  # 只处理 init 就退
    try:
        assert child.wait(timeout=5.0) == 1
        assert _until(lambda: not pf.fills_out_of_process), f"子进程死了却没降级：{logs}"
        assert pf.status()["degrade_reason"], logs
        assert pf.start_fill(base_url="", token="") is False
    finally:
        pf.close()


def test_take_timeout_is_a_miss_not_a_hang(tmp_path: Path, monkeypatch) -> None:
    """子进程不应答 `take` ⇒ 按**未命中**处理（走关键下载），绝不把训练卡在管道上。"""
    monkeypatch.setattr(PP, "PROC_TAKE_TIMEOUT_SEC", 0.2)
    logs: list[str] = []
    pf, _child = _proc(tmp_path, logs=logs, silent=("take",))
    try:
        t0 = time.time()
        assert pf.take(JID1) is None
        # timing-ok: 上界兜底（超时本身已被钉在 0.2s；5s 只挡「根本没超时就挂住了」）
        assert time.time() - t0 < 5.0, "take 没有按超时返回"
        assert pf.fills_out_of_process, "一次 take 超时不该把整条腿降级（暂存还在子进程里）"
    finally:
        pf.close()


# ────────────────────────── ③ 跨进程 P1 协同 ──────────────────────────


def test_p1_waiting_is_broadcast_to_the_child(tmp_path: Path) -> None:
    """父进程 P1 进出等待必须传到子进程（否则 P2 会重新和关键下载抢带宽）。"""
    pf, child = _proc(tmp_path)
    try:
        pf.note_p1(True)
        pf.note_p1(False)
        assert _until(lambda: len([c for c in child.commands if c.get("op") == "p1"]) == 2), (
            child.commands
        )
        p1 = [c for c in child.commands if c.get("op") == "p1"]
        assert [c["waiting"] for c in p1] == [True, False], child.commands
    finally:
        pf.close()


def test_bulk_p1_observer_feeds_the_prefetcher(tmp_path: Path) -> None:
    """`BulkScheduler.set_p1_observer` 的接线（`worker_loop` 用它广播给所有子进程）。"""
    from remote.bulk_sched import BULK_P1_CRITICAL
    from remote.wire import _BULK

    pf, child = _proc(tmp_path)
    prev = _BULK.set_p1_observer(pf.note_p1)
    try:
        with _BULK.slot(BULK_P1_CRITICAL, label="test"):
            pass
        assert _until(lambda: len([c for c in child.commands if c.get("op") == "p1"]) == 2), (
            f"P1 进出没广播：{child.commands}"
        )
        p1 = [c for c in child.commands if c.get("op") == "p1"]
        assert [c["waiting"] for c in p1] == [True, False], f"P1 进出没广播：{child.commands}"
    finally:
        _BULK.set_p1_observer(prev)
        pf.close()


def test_degraded_p1_notification_is_a_noop(tmp_path: Path) -> None:
    """降级后 `note_p1` 必须是**空操作**（P2 已住回本进程，`slot()` 自己就是那套口径）。

    为什么要钉：再走一遍广播会把同一个 P1 记两次（`_p1_waiting` 会漏配），而且降级路径
    本来就不该碰「另一个进程的调度器」这个概念。
    """
    from remote.bulk_sched import BULK_P2_PREFETCH
    from remote.wire import _BULK

    pf, _child = _proc(tmp_path, boot=False)  # 起不来 ⇒ 已降级
    assert not pf.fills_out_of_process
    try:
        pf.note_p1(True)
        # 没被挡：真正的 P1（本进程 `slot()`）才挡 P2，桥不参与
        with _BULK.slot(BULK_P2_PREFETCH, label="degraded") as tok:
            _BULK.check_preempted(tok)
        pf.note_p1(False)
    finally:
        pf.close()


# ────────────────────────── ④ 模式域 / 工厂 ──────────────────────────


def test_make_prefetcher_thread_mode_returns_the_store(tmp_path: Path) -> None:
    """`thread` 档 = 逐字旧行为（进程内 `PrefetchStore`，没有子进程）。"""
    pf = make_prefetcher(tmp_path, mode="thread", staged_max=2)
    assert isinstance(pf, PrefetchStore)
    assert not hasattr(pf, "start_fill")


def test_mode_env_is_between_the_argument_and_the_default(tmp_path: Path, monkeypatch) -> None:
    """解析顺序：显式参数 > `$NN_PREFETCH_MODE` > 缺省（process）。"""
    monkeypatch.setenv(PREFETCH_MODE_ENV, "thread")
    assert isinstance(make_prefetcher(tmp_path, mode="thread"), PrefetchStore)
    monkeypatch.setenv(PREFETCH_MODE_ENV, "nonsense")
    logs: list[str] = []
    pf = make_prefetcher(tmp_path, log=logs.append)
    assert isinstance(pf, PrefetchStore), "未知模式按 thread（不许静默起子进程）"
    assert logs, "未知模式必须壤亮一声（静默默认 = 后人看不出来）"


def test_close_reaps_the_child_and_is_idempotent(tmp_path: Path) -> None:
    pf, child = _proc(tmp_path)
    pf.start_fill(base_url="", token="")
    pf.close()
    pf.close()
    assert not pf.status()["alive"]
    assert child.wait(timeout=5.0) == 0
    ops = [c.get("op") for c in child.commands]
    assert ops[-1] == "shutdown", ops
    assert ops.index("stopfill") < ops.index("shutdown"), "先停填充再关子进程"


# ────────────────────────── ④b 宿主接线（`run_one_round` 的分派） ──────────────────────────


def test_worker_loop_fills_through_the_child_not_a_local_thread(
    tmp_path: Path, monkeypatch
) -> None:
    """**接线契约**：预取器是 `PayloadProcess` ⇒ 本轮只下一道 `fill` 命令，**不起**进程内线程。

    这是「下行腿真搬出去了」的唯一可见面：如果 `run_one_round` 还无条件起 `_prefetch_fill`，
    网络与解包就仍在训练进程里（搬了个空）。
    """
    import remote.job_round as JR
    import remote.worker as W

    jid = "7" * 16
    calls = {"acquire": 0}

    def _acquire(_base, _token, **_kw):
        calls["acquire"] += 1
        if calls["acquire"] > 1:
            return None
        return {"job_id": jid, "manifest": {"job_id": jid}, "status": "ok", "lease_token": ""}

    def _run_job(_base, _token, job, **_kw):
        return {"job_id": job["job_id"]}

    local_fills: list[str] = []

    def _bomb(*a, **k):  # 进程内填充线程**不该**被走到
        local_fills.append("called")

    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "run_job", _run_job)
    monkeypatch.setattr(W, "post_result", lambda *a, **k: 200)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([], False))
    monkeypatch.setattr(JR, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(JR, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)
    monkeypatch.setattr(JR, "_prefetch_fill", _bomb)

    pf, child = _proc(tmp_path)
    monkeypatch.setattr(W, "make_prefetcher", lambda *_a, **_kw: pf)

    logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path / "w",
        poll_sec=0.05,
        max_idle_sec=0.3,
        prefetch_depth=2,
        log=logs.append,
    )
    assert n == 1, logs
    assert local_fills == [], "进程模式下仍在进程内起了填充线程（下行腿没真搬出去）"
    assert _until(lambda: any(c.get("op") == "fill" for c in child.commands)), child.commands
    fill = next(c for c in child.commands if c.get("op") == "fill")
    assert fill["skip"] == [jid], "在跑的这份没被排除（会自己预取自己）"
    assert fill["depth"] == 2 and fill["worker_id"], fill
    assert any(c.get("op") == "stopfill" for c in child.commands), "job 收尾没停填充"


# ────────────────────────── ⑤ 真子进程（慢层，只一条） ──────────────────────────


def test_real_child_boots_init_fills_and_shuts_down(tmp_path: Path) -> None:
    """真 `python -m remote.payload_worker`：`init` 动手建 store、`fill/stopfill` 能收发、
    收线后进程退干净（**只证明协议与环境链**，不证明下载逻辑——那由快层与 `_prefetch_fill`
    的既有用例盖住）。"""
    logs: list[str] = []
    pf = PayloadProcess(tmp_path / "work", staged_max=1, log=logs.append)
    try:
        pf.start()
        assert pf.fills_out_of_process, f"真子进程没起来：{logs}"
        assert pf.status()["alive"]
        assert pf.status()["pid"] and pf.status()["pid"] != os.getpid()
        # 不可达的 hub 地址 ⇒ peek 立刻失败（`urlopen` 的 URL 解析错误），填充器进等待循环
        assert pf.start_fill(base_url="", token="", depth=1, unpack_n=0)
        assert pf.take(JID1) is None
        pf.stop_fill()
    finally:
        pf.close()
    assert not pf.status()["alive"]
