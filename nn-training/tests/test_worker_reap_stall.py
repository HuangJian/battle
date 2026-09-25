"""test_worker_reap_stall.py — worker_loop 对**机器级停滞**的处置（2026-09-25 二次取证）。

现场（`battle.offline.ipynb` × x20-dodge-l3）：一轮 rollout 在 5s 内 ≥23 局踩硬顶 ⇒ 熔断 ⇒
`进度=271/336 games settled (910s)` 一行不动 —— 890s 里花在**子进程回收**上（`p.kill(); p.wait()`
没有上限，子进程卡在不可中断的 IO 里时 SIGKILL 要等系统调用返回）。收尸有界那一半在
`tests/test_platform_utils_proc.py` / `tests/test_remote_iter.py` 钉。

本文件钉的是**接下来那一步**（用户 2026-09-25 口径：**失败就重试，不许关云机让任务失败，
也不许空转烧配额**）。`UnreapableChildError` 上抛到 worker_loop 之后必须同时满足三条：

  ① **重试**：还租约（不占着活）⇒ 立即重领重投同一份活；
  ② **不判死**：不报 `report_job_failure`（那会把机器的问题记在内容头上 ⇒ hub 落终局 failed
     ⇒ 训练停腿 ⇒ 反过来下发停机指示把云机停掉）；hub 的毒包熔断只对**租约过期**计数，
     主动 release 不算 ⇒ 反复重投不会把自己冻死；
  ③ **不空转**：不睡（云机按分钟计费）。所以这里连「冷却」都没有 —— 30s 冷却被明文否决
     （云机卡死的那 15 分钟里，睡的那部分就是白烧的配额；宁可重复干活，不要停着等）。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import iter_rollout as _iter_rollout
from remote import job_round as JR
from remote import worker as W

#: 一轮之内与 hub 打交道的那批名字，**实现与调用点都在 `remote/job_round.py`**（S4 第十刀把
#: `run_one_round` 从 worker 搬走）。所以要拦它们必须 patch **该模块**：patch `remote.worker.X`
#: 是**静默空操作**（worker 只是转发面，没人再读它）——那是本文件并入时踩到的第一个坑
#: （真 HTTP 打到 `http://hub` ⇒ 退避重试把用例拖成 9.9s）。
_HUB_FACES = ("job_ready", "start_cancel_watcher", "heartbeat")


def _job(jid: str) -> dict:
    return {"job_id": jid, "manifest": {"job_id": jid}, "status": "ok", "lease_token": f"t-{jid}"}


def _patch_hub(monkeypatch: pytest.MonkeyPatch, jobs: list[dict]) -> None:
    """把 worker_loop 的 hub 面全换成假的（只剩我们要测的那条路径是真的）。

    分档：`acquire_job` / `_release_cloud_machine` / `post_result` 的调用点仍在 `worker` 命名
    空间（宿主），而那三个一轮之内才用的面（`_HUB_FACES`）住在 `remote.job_round`。
    """
    seq = list(jobs)
    monkeypatch.setattr(W, "acquire_job", lambda *a, **k: seq.pop(0) if seq else None, raising=True)
    for name in _HUB_FACES:
        monkeypatch.setattr(JR, name, lambda *a, **k: None, raising=True)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None, raising=True)
    # 回传也必须假（缺省 async：真 POST 会撞 502 然后退避重试 2+4+8+16s ⇒ 用例等成钟表）
    monkeypatch.setattr(W, "post_result", lambda *a, **k: 200, raising=True)


def _run_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    records: list[tuple[str, float]],
    fail_with: dict[str, BaseException],
    lock: threading.Lock,
    logs: list[str] | None = None,
) -> float:
    """跑一遍 worker_loop；`records` 记 `(jid, 开算时刻)`，`fail_with` 里的 jid 抛给定异常。"""

    def _run_job(base, tok, job, **k):
        jid = job["job_id"]
        with lock:
            records.append((jid, time.time()))
        boom = fail_with.get(jid)
        if boom is not None:
            raise boom
        return {"job_id": jid}

    monkeypatch.setattr(W, "run_job", _run_job, raising=True)
    t0 = time.time()
    W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        max_idle_sec=0.2,
        prefetch_depth=0,  # 预取线程与本用例无关（省一条线程）
        log=(logs if logs is not None else []).append,
    )
    return time.time() - t0


def test_reap_stall_is_retried_immediately_and_never_reported_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """机器级停滞 ⇒ 还租约 + **立即**重领（不睡）；**不**报确定性失败（那会停腿、停云机）。"""
    _patch_hub(monkeypatch, [_job("j1"), _job("j2")])
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        JR,
        "release_job",
        lambda base, tok, jid, lease, log=None: released.append((jid, lease)),
        raising=True,
    )
    reported: list[str] = []
    monkeypatch.setattr(
        JR, "report_job_failure", lambda base, tok, jid, *a, **k: reported.append(jid), raising=True
    )
    records: list[tuple[str, float]] = []
    logs: list[str] = []
    wall = _run_loop(
        tmp_path,
        monkeypatch,
        records=records,
        fail_with={
            "j1": _iter_rollout.UnreapableChildError(
                "rollout 单局超时（5.0s > 硬顶 5s）：s0/d0；且 SIGKILL 之后 5s 内回收不了"
            )
        },
        lock=threading.Lock(),
        logs=logs,
    )

    assert [j for j, _ in records] == ["j1", "j2"], f"两个 job 都必须被领到：{records}"
    assert released == [("j1", "t-j1")], f"机器级停滞必须把租约还回去（才能被重领）：{released}"
    assert not reported, f"机器的问题不许报成确定性失败（那会停腿 + 停云机）：{reported}"
    gap = records[1][1] - records[0][1]
    assert gap < 1.0, f"必须立即重领（不许睡/空转烧配额）：下一份活在 {gap:.3f}s 后开算"
    assert wall < 10.0, f"处置必须有界：{wall:.1f}s"
    stall = [m for m in logs if "机器级停滞" in m]
    assert stall and "立即重领" in stall[0], f"停滞必须响亮留痕（带处置）：{logs}"


def test_repeated_stalls_keep_being_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一份活**连着**被卡住也要接着重投：不累计「失败」、不冻结、不把这一轮判死。

    停下来的那条路只有一条（hub 对**租约过期**计 reclaims ≥3 冻成毒包）——而主动 release 刻意
    不计数（`test_poison_freeze` 管着那一侧）。这里钉 worker 侧：反复停滞只产生「重领」，不产生
    「失败」这笔账，也不产生任何等待。
    """
    # 同一份 job 被连着领 3 次（每次都停滞），第 4 次领到时它好了。
    _patch_hub(monkeypatch, [_job("j1"), _job("j1"), _job("j1"), _job("j1"), _job("j2")])
    monkeypatch.setattr(JR, "release_job", lambda *a, **k: None, raising=True)
    reported: list[str] = []
    monkeypatch.setattr(
        JR, "report_job_failure", lambda base, tok, jid, *a, **k: reported.append(jid), raising=True
    )
    stall = _iter_rollout.UnreapableChildError("rollout 单局超时（5.0s > 硬顶 5s）：s0/d0；收不了尸")
    state = {"n": 0}
    records: list[tuple[str, float]] = []
    lock = threading.Lock()

    def _run_job(base, tok, job, **k):
        jid = job["job_id"]
        with lock:
            records.append((jid, time.time()))
            state["n"] += 1
            n = state["n"]
        if n <= 3:
            raise stall
        return {"job_id": jid}

    monkeypatch.setattr(W, "run_job", _run_job, raising=True)
    t0 = time.time()
    W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        max_idle_sec=0.2,
        prefetch_depth=0,
        log=lambda _m: None,
    )
    wall = time.time() - t0
    assert [j for j, _ in records] == ["j1", "j1", "j1", "j1", "j2"], (
        f"连卡 3 次之后必须还能重领（第 4 次才跑成）：{records}"
    )
    assert not reported, f"反复停滞不许变成失败（那会停腿 + 停云机）：{reported}"
    assert wall < 5.0, f"重投不许带等待（睡前摇/冷却 = 空转烧配额）：整段 {wall:.1f}s"


def test_plain_transient_failure_still_reclaims_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照腿：普通瞬时失败照旧立即重领（2026-09-05 起的既有语义，本次没动）。"""
    _patch_hub(monkeypatch, [_job("j1"), _job("j2")])
    monkeypatch.setattr(JR, "release_job", lambda *a, **k: None, raising=True)
    records: list[tuple[str, float]] = []
    wall = _run_loop(
        tmp_path,
        monkeypatch,
        records=records,
        fail_with={"j1": W.RetryableError("payload_sha256 不匹配——传输损坏（重下可修复）")},
        lock=threading.Lock(),
    )
    assert [j for j, _ in records] == ["j1", "j2"], records
    gap = records[1][1] - records[0][1]
    assert gap < 1.0, f"普通瞬时失败也是立即重领（{gap:.3f}s）"
    assert wall < 10.0, wall
