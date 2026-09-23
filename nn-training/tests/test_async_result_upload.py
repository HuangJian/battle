"""tests/test_async_result_upload.py —— P2.5 异步结果回传（plan/transfer-scheduling §4 P2.5）。

为什么有这组用例：改造前 `out`（结果回传）**全程压在关键路径上**——`worker.py` 里
`post_result` 同步阻塞，25s 就是 25s。用户口径（2026-09-22）：双课程单 worker 下
rollout 与 PPO 互相填空、算力已经满了，所以要快只能**把传输从关键路径上摘掉**；
而 `out` 25s 比 `in` 15s 还大（plan §1.1），正是最大的一块。

本文件钉死四件事，每一件都能独立改坏：

  ① **不占关键路径**：回传慢**不**阻塞下一份 job 开算（A/B：async 早于上传结束就开算，
     sync 必须等 —— 后者是基线，没有它这条断言会「恒真」）；
  ② **绝不丢结果**：退出前 drain（`--once` / 空闲退出 / 热替换 / 异常）、队列满或上传器
     已收尾 ⇒ 退回**同步**（宁可慢，不许丢）；
  ③ **失败响亮**：重试耗尽 / 确定性拒绝要留一行**带 jid** 的痕 + 计数（静默 = 训练侧
     干等租约过期那类事故）；
  ④ **记账不撒谎**：`out` 的秒数照报，阶段行 `overlap=` 说清楚它被重叠掉了。

线程类用例一律小超时 + 事件放行（不 sleep 等固定秒数），单例 < 1s，符合 conftest 的耗时预算。
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

import remote.worker as W
from common.protocol import RetryableError
from remote.result_upload import ResultUploader, UploadTask

JID1 = "a1" * 8
JID2 = "b2" * 8
JID3 = "c3" * 8


# ────────────────────────── 单元层：ResultUploader ──────────────────────────


def _task(jid: str, *, on_settled=None) -> UploadTask:
    return UploadTask(
        jid=jid,
        base_url="http://hub",
        token="tok",
        result={"job_id": jid},
        on_settled=on_settled,
    )


def test_submit_returns_before_the_upload_finishes() -> None:
    """**核心契约**：`submit()` 立刻返回 —— 上传还在飞的时候调用方就能继续干活。"""
    started = threading.Event()
    release = threading.Event()
    done: list[str] = []

    def _upload(base, token, jid, result, **k):
        started.set()
        assert release.wait(5.0), "测试自锁：上传没被放行"
        done.append(jid)
        return 200

    up = ResultUploader(upload=_upload)
    up.submit(_task(JID1))
    assert started.wait(2.0), "上传线程没起来"
    assert done == [], "此刻上传**不该**已经结束（否则本用例没测到异步）"
    release.set()
    assert up.drain(timeout=5.0)
    assert done == [JID1]
    out = up.outcome(JID1)
    assert out is not None and out.ok


def test_close_drains_everything_exactly_once() -> None:
    """退出前必收尾：`close()` **等**所有在飞结果落定，且每份**恰好**送一次。

    上传被事件闸住 ⇒「close 到底有没有等」是**确定性**可判的（不等就必然看到空 `seen`），
    而不是靠「睡多睡少」去赌线程调度——那种写法在慢机器上会变成假绿。
    """
    seen: list[str] = []
    release = threading.Event()

    def _upload(base, token, jid, result, **k):
        assert release.wait(5.0), "测试自锁"
        seen.append(jid)
        return 200

    up = ResultUploader(upload=_upload, depth=4)
    for j in (JID1, JID2):
        up.submit(_task(j))
    threading.Timer(0.2, release.set).start()  # 结果要 0.2s 后才允许落定
    assert up.close(timeout=5.0) is True, "close 必须等干净"
    assert seen == [JID1, JID2], f"close 没等结果落定就返回了（退出即丢结果）：{seen}"
    st = up.stats()
    assert st["submitted"] == 2 and st["ok"] == 2 and st["pending"] == 0 and st["failed"] == 0


def test_upload_failure_is_loud_and_counted() -> None:
    """重试耗尽 = 失败，必须留一行**带 jid** 的痕（主循环已经走远，日志是唯一信号）。"""
    logs: list[str] = []

    def _upload(base, token, jid, result, **k):
        raise RetryableError("result POST 重试 5 次仍失败: HTTP None")

    up = ResultUploader(upload=_upload, log=logs.append)
    up.submit(_task(JID1))
    assert up.close(timeout=5.0) is True  # 失败也算「落定」，不是「没落定」
    out = up.outcome(JID1)
    assert out is not None and not out.ok and "RetryableError" in out.error
    assert up.stats()["failed"] == 1
    assert any(JID1 in m and "回传失败" in m for m in logs), f"失败必须响亮：{logs}"


def test_queue_full_falls_back_to_sync_instead_of_dropping() -> None:
    """队列满（上传线程被卡住）不许丢结果：超时后退回**同步**回传。

    造法：深度 1 + 第一份把上传线程永远占住（它再也不会 `get()`）⇒ 后面两份必然有一份
    撞上满队列。批次里的另一份到底是谁撞上取决于线程调度，所以只断言「至少一次逃生 +
    三份全部送达且各一次」，不断言具体是哪一份。
    """
    logs: list[str] = []
    blocking = threading.Event()
    seen: list[str] = []

    def _upload(base, token, jid, result, **k):
        if jid == JID1:
            assert blocking.wait(5.0), "测试自锁"
        seen.append(jid)
        return 200

    up = ResultUploader(
        upload=_upload, depth=1, enqueue_timeout=0.05, drain_timeout=5.0, log=logs.append
    )
    up.submit(_task(JID1))  # 占住上传线程（它卡在 blocking 里，不会再从队列取）
    up.submit(_task(JID2))
    up.submit(_task(JID3))  # 槽位被占着 ⇒ 超时 ⇒ 同步逃生口（**不许**丢）
    assert up.stats()["sync_fallback"] >= 1, "队列满必须走同步逃生口，而不是丢"
    assert any("同步" in m for m in logs), f"逃生口要留痕：{logs}"
    blocking.set()
    assert up.close(timeout=5.0) is True
    assert sorted(seen) == sorted([JID1, JID2, JID3]) and len(seen) == 3, (
        f"三份结果必须全部送达且各一次：{seen}"
    )


def test_sync_mode_is_the_old_behaviour_inline() -> None:
    """`--result-upload sync` = 逐字回退：submit 返回时结果**已经**送完，且不起线程。"""
    seen: list[str] = []

    def _upload(base, token, jid, result, **k):
        seen.append(jid)
        return 200

    up = ResultUploader(upload=_upload, mode="sync")
    up.submit(_task(JID1))
    assert seen == [JID1], "sync 模式必须当场发完"
    assert up.stats()["pending"] == 0
    assert up._thread is None, "sync 模式不该起上传线程"
    assert up.mode == "sync"


def test_unknown_mode_falls_back_to_async() -> None:
    """域校验：写错的模式**不许**静默变成 sync（那会把回传重新压回关键路径）。"""
    up = ResultUploader(upload=lambda *a, **k: 200, mode="asink")
    assert up.mode == "async"


def test_settled_callback_runs_after_the_upload() -> None:
    """落定回调必须在上传**之后**跑（生产侧靠它把 `out` 的账收在这一刻）。"""
    order: list[str] = []

    def _upload(base, token, jid, result, **k):
        order.append("upload")
        return 200

    def _settled(jid: str, out: object) -> None:
        order.append(f"settled:{jid}:{getattr(out, 'status', None)}")

    up = ResultUploader(upload=_upload)
    up.submit(_task(JID1, on_settled=_settled))
    assert up.close(timeout=5.0) is True
    assert order == ["upload", f"settled:{JID1}:200"]


def test_submit_after_close_still_sends() -> None:
    """收尾之后再 submit（理论上的竞态）也不许丢：退回同步发。"""
    seen: list[str] = []

    def _upload(base, token, jid, result, **k):
        seen.append(jid)
        return 200

    up = ResultUploader(upload=_upload)
    assert up.close(timeout=5.0) is True
    up.submit(_task(JID1))
    assert seen == [JID1]


# ──────────────────── 集成层：worker_loop（真实主循环） ────────────────────


def _patch_hub(monkeypatch, jobs: list[dict]) -> None:
    """把 worker_loop 的 hub 面全换成假的（只剩我们要测的那条路径是真的）。"""
    seq = list(jobs)
    monkeypatch.setattr(W, "acquire_job", lambda *a, **k: seq.pop(0) if seq else None, raising=True)
    monkeypatch.setattr(W, "job_ready", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(W, "start_cancel_watcher", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None, raising=True)


def _job(jid: str) -> dict:
    return {"job_id": jid, "manifest": {"job_id": jid}, "status": "ok", "lease_token": ""}


def test_worker_loop_starts_the_next_job_before_the_upload_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 本文件的核心断言：回传**不**阻塞下一份 job 开算（async 新口径 vs sync 基线）。

    只比两件事的**次序**（不比秒数，避免慢机器上抖）：
      async：第二份 job 开算 **早于** 第一份回传结束；
      sync ：第二份 job 开算 **晚于** 第一份回传结束 —— 这就是改造前的样子，
             有它才证明本用例不是「恒真」（sync 永远是 async 的对照）。
    """
    events: list[str] = []
    lock = threading.Lock()

    def _stamp(tag: str) -> None:
        with lock:
            events.append(tag)

    def _measure(mode: str) -> list[str]:
        events.clear()
        up_release = threading.Event()
        second_started = threading.Event()

        def _upload(base, token, jid, result, **k):
            _stamp(f"up_block:{jid}")
            up_release.wait(5.0)
            _stamp(f"up_end:{jid}")
            return 200

        def _run_job(base, token, job, **k):
            jid = job["job_id"]
            if jid == JID2:
                _stamp("run2")
                second_started.set()
            else:
                _stamp("run1")
            time.sleep(0.15)  # 冒充 PPO 计算
            return {"job_id": jid}

        monkeypatch.setattr(W, "post_result", _upload, raising=True)
        monkeypatch.setattr(W, "run_job", _run_job, raising=True)
        _patch_hub(monkeypatch, [_job(JID1), _job(JID2)])

        def _releaser() -> None:
            second_started.wait(0.8)  # async 会很快等到；sync 等到超时也没关系
            up_release.set()

        t = threading.Thread(target=_releaser, daemon=True)
        t.start()
        W.worker_loop(
            "http://hub",
            "tok",
            work_dir=tmp_path,
            poll_sec=0.0,
            max_idle_sec=0.2,
            prefetch_depth=0,  # 预取线程与本用例无关（省一条线程）
            result_upload=mode,
            log=lambda _m: None,
        )
        t.join(timeout=5.0)
        with lock:
            return list(events)

    a = _measure("async")
    assert a.index("run2") < a.index(f"up_end:{JID1}"), (
        f"async 下第二份 job 竟然等在回传后面 —— 回传还在关键路径上：{a}"
    )
    s = _measure("sync")
    assert s.index(f"up_end:{JID1}") < s.index("run2"), (
        f"sync 基线应当等回传（否则本用例是空转）：{s}"
    )


def test_worker_loop_marks_out_as_overlapped_in_the_phases_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """记账（④）：`out` 的秒数照报，但 `overlap=` 说清楚它没占关键路径。

    同时钉住「每 job**恰好一行**阶段账」——async 把 flush推迟到落定，最容易出的 bug
    是「finally 收一次 + 落定再收一次」（两行）或「谁都没收」（零行）。
    """
    logs: list[str] = []

    def _upload(base, token, jid, result, **k):
        # 真实 `post_result` 会在内部记这一段；这里照抄它的记账（25s = 用户口径的 out）
        W._wire_add(jid, "result", 1024 * 1024, 25.0)
        return 200

    monkeypatch.setattr(W, "post_result", _upload, raising=True)
    monkeypatch.setattr(W, "run_job", lambda b, t, job, **k: {"job_id": job["job_id"]}, raising=True)
    _patch_hub(monkeypatch, [_job(JID1)])

    W._WIRE.clear()
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        prefetch_depth=0,
        result_upload="async",
        once=True,
        log=logs.append,
    )
    assert n == 1
    phases = [m for m in logs if " phases " in m]
    assert len(phases) == 1, f"每 job 必须恰好一行阶段账（0 或 2 行都是 bug）：{logs}"
    line = phases[0]
    assert "out=25.0s" in line, "回传的实际耗时不许被抹掉"
    assert "overlap=25.0s" in line, f"重叠的那一段必须如实报：{line}"
    # 关键路径 `wall` 不含那 25s（本用例里 in=ppo=0，所以墙钟只剩实测的几毫秒）
    assert "wall=" in line
    assert any("回传收尾" in m for m in logs), f"退出必须有一行收尾账：{logs}"


def test_once_waits_for_the_result_and_nonzero_exit_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--once` 的冒烟语义（H8）：回传失败必须非零退出。

    async 下「成败」在提交那一刻还不知道 —— 必须先 drain 再判，否则失败会被
    静默当成成功（退出码 0），而 smoke 只判 returncode。
    """
    monkeypatch.setattr(W, "run_job", lambda b, t, job, **k: {"job_id": job["job_id"]}, raising=True)

    posted: list[str] = []

    def _ok(b, t, jid, r, **k):
        posted.append(jid)
        return 200

    monkeypatch.setattr(W, "post_result", _ok, raising=True)
    _patch_hub(monkeypatch, [_job(JID1)])
    ok_logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        once=True,
        prefetch_depth=0,
        result_upload="async",
        log=ok_logs.append,
    )
    assert n == 1 and posted == [JID1], f"回传必须已经送出去（drain）：n={n} {ok_logs}"

    def _boom(b, t, jid, r, **k):
        raise RetryableError("result POST 重试 5 次仍失败: HTTP None")

    monkeypatch.setattr(W, "post_result", _boom, raising=True)
    _patch_hub(monkeypatch, [_job(JID1)])  # 第二跑要自己的那一份（上面 seq 已耗尽）
    bad_logs: list[str] = []
    n2 = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        once=True,
        prefetch_depth=0,
        result_upload="async",
        log=bad_logs.append,
    )
    assert n2 == -1, f"回传失败必须非零退出（H8）：{bad_logs}"
    assert any(JID1 in m and "回传失败" in m for m in bad_logs)


def test_cancelled_job_never_uploads_and_still_gets_its_wire_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """取消/失败路径**没走到回传** ⇒ 回传永远不会落定 ⇒ 账必须在 finally 里照旧收。

    这是「推迟 flush」最容易漏的一条：只有 `uploaded=True` 那条分支才该等落定。
    """
    from common.protocol import JobCancelledError

    _patch_hub(monkeypatch, [_job(JID1)])

    def _cancel(*a, **k):
        raise JobCancelledError("landed")

    monkeypatch.setattr(W, "run_job", _cancel, raising=True)
    monkeypatch.setattr(W, "abandon_job", lambda *a, **k: None, raising=True)
    posted: list[str] = []

    def _post(b, t, jid, r, **k):
        posted.append(jid)
        return 200

    monkeypatch.setattr(W, "post_result", _post, raising=True)
    logs: list[str] = []
    W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.0,
        once=True,
        prefetch_depth=0,
        result_upload="async",
        log=logs.append,
    )
    assert posted == [], "取消的 job 零回传"
    assert len([m for m in logs if " phases " in m]) == 1, f"取消路径也必须有一行阶段账：{logs}"
