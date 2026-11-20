"""坏 job 的 `SystemExit` 只报失败，不许把整段会话带走（2026-11-20 kaggle 真机）。

现场（`logs/kaggle-...-1ppoli.log`）：一致命 bug 发布出去的 replay 轮 payload 缺 `corpus_fmt`
⇒ worker 的语料装载直接 `SystemExit("[ppo] no RL shards found under /tmp/remote-worker/<jid>")`。
`SystemExit` 是 **BaseException**，job 边界的所有 `except Exception` 都接不住它 ⇒

  子进程 rc=1 → 监督器按「崩溃」重拉 → 又领到同一份坏 job → 30/60/90/120/150s 退避五次
  → `FATAL: worker 连续失败 6 次（rc=1）——放弃自动重启；会话结束 rc=1`

一份**内容有问题**的 job 因此付掉了整台云机的会话（用户口径：「坏 job 只该报失败」）。

契约（本文件钉死）：

  * **job 作用域里的 `SystemExit` = 这份活跑不成**，按确定性失败处置（`report_job_failure`
    → hub 终局 → 训练侧带原因停腿），然后**继续轮询**（`stop=False`，进程不退）；
  * **`HOT_RELOAD_EXIT`（86）例外**：它唯一的语义是「请求监督器换代码重拉进程」，
    在 job 边界被吞掉就等于把热替换变成一份假失败；
  * push 腿同一条不变量（`worker_server._execute_job`）：坏 job 落 failed，服务进程不死
    （线程里逃出去的 `SystemExit` 只会静默杀死那条线程，job 永远停在 running）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import remote.job_round as job_round
import remote.worker_server as ws
from common.protocol import ROLE_ONLINE
from remote.worker_proc import HOT_RELOAD_EXIT

JID = "0123456789abcdef"
#: 现场原话（`worker/ppo/lazy_corpus.py`）：只在断言里用，不按它扫日志措辞。
BAD_CORPUS = "[ppo] no RL shards found under /tmp/remote-worker/0123456789abcdef"


class _Uploader:
    def submit(self, _task: Any) -> None:
        pass


def _quiet_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """把一轮里与「失败分类」无关的网络面剪掉（本文件只判分类）。"""
    monkeypatch.setattr(job_round, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(job_round, "start_cancel_watcher", lambda *a, **k: None)


def _pull_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boom: BaseException,
    msgs: list[str],
    reported: list[dict[str, Any]],
) -> job_round.RoundOutcome:
    """跑一轮 pull 腿：`run_job_fn` 抛 `boom`，记录 `report_job_failure` 的实参。"""
    _quiet_seams(monkeypatch)

    def _report(*a: Any, **k: Any) -> bool:
        reported.append({"args": a, "kwargs": k})
        return True

    monkeypatch.setattr(job_round, "report_job_failure", _report)

    def _run_job(*_a: Any, **_k: Any) -> dict:
        raise boom

    return job_round.run_one_round(
        "http://hub.invalid",
        "tok",
        {"job_id": JID, "manifest": {"it": 104, "kind": "ppo"}, "status": "ok", "lease_token": ""},
        part_dir=tmp_path,
        code_cache_dir=None,
        pf_store=None,
        polls_since_accept=0,
        worker_id="w-1",
        device="cpu",
        torch_threads=0,
        echo=False,
        restart_argv=None,
        role=ROLE_ONLINE,
        prefetch_depth=1,
        uploader=cast(Any, _Uploader()),
        run_job_fn=_run_job,
        log=msgs.append,
    )


def test_bad_job_systemexit_is_reported_as_job_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """语料坏掉的 job ⇒ 报一次确定性失败，本轮照常收尾（不抛、不 stop）。"""
    msgs: list[str] = []
    reported: list[dict[str, Any]] = []
    outcome = _pull_round(tmp_path, monkeypatch, SystemExit(BAD_CORPUS), msgs, reported)

    assert len(reported) == 1, reported
    call = reported[0]
    assert call["args"][2] == JID
    assert call["args"][3] == BAD_CORPUS  # 原因原样上浮（hub 侧 / 训练侧都读它）
    assert call["kwargs"]["kind"] == "SystemExit"
    # 会话必须活着：ok=False（本轮没产出）、没交回传、也没要求整条循环退出
    assert (outcome.ok, outcome.uploaded, outcome.stop) == (False, False, False)


def test_hot_reload_exit_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """86 是「请监督器换代码重拉」的唯一信号——被当成 job 失败就等于热替换静默失效。"""
    msgs: list[str] = []
    reported: list[dict[str, Any]] = []
    with pytest.raises(SystemExit) as ei:
        _pull_round(tmp_path, monkeypatch, SystemExit(HOT_RELOAD_EXIT), msgs, reported)
    assert ei.value.code == HOT_RELOAD_EXIT
    assert reported == []


def test_push_leg_records_systemexit_and_keeps_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push 腿：`SystemExit` 逃出训练体会让线程静默死掉、job 永远停在 running ⇒ 落 failed。"""

    def _run_job(*_a: Any, **_k: Any) -> dict:
        raise SystemExit(BAD_CORPUS)

    monkeypatch.setattr(ws, "run_job", _run_job)
    state = ws.WorkerServerState(tmp_path / "work")
    msgs: list[str] = []
    # 2026-11-20 P0-3 第六刀：`_execute_job` 收单个只读 JobExecSpec（kw_only），
    # 不再是 11 位置 + log 关键字——旧写法会让 mypy 报「参数过多 / 类型不匹配」。
    ws._execute_job(
        ws.JobExecSpec(
            state=state,
            manifest={"job_id": JID, "it": 104, "kind": "ppo"},
            payload_zip=b"PK\x03\x04fake-payload",
            code_zip=None,
            ts_code_zip=None,
            blobs=None,
            work_dir=tmp_path / "work",
            device="cpu",
            torch_threads=0,
            echo=False,
            log=msgs.append,
        )
    )
    job = state.jobs[JID]
    assert job["state"] == "failed"
    assert job["kind"] == "SystemExit"
    assert BAD_CORPUS in job["error"]
