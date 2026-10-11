"""tests/remote/test_result_backup.py —— 回传失败时备份**失败的那一份**（`plan/rollout-topup-fastpath` §3.6）。

用户第 1 条的余项：结果是最贵的产物，而回传是它唯一的出口。这一层保险只在**回传失败**时才动：
把发出去的那份**线上体**（`pack_result_v2` 的字节）落到 `<job_dir>/backup/`，文件名带 `it`/`jid`/`sha256`
⇒ 恢复 = 原样重发一个 POST，不需要重跑 PPO。

钉住四件事：

  1. **成功 ⇒ 一个字节都不多写**（常态零变化：`_persist_result` 的本地盘路径逐字不变）；
  2. **失败 ⇒ 落的就是线上体**（`read_bytes() == pack_result_v2(result)`）+ 名字带 it/jid/sha256；
  3. **尽力而为**：连盘都写不成也只 WARN、不抛、不改 job 状态；
  4. **回收**：备份住在 `<job_dir>/` 里 ⇒ `prune_job_dirs` 按 job 目录回收，不需要新豁免
     （这也是它**不**住 job 目录隔壁的原因）。

端到端那一层走**真** `run_one_round` + **真** `ResultUploader`（`mode="sync"`，注入一个必抛的上传函数）
——接线（谁在什么时候调它）不是靠文本断言钉的。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_fs as job_fs
import remote.job_round as job_round
from common.errors import RetryableError
from common.platform_utils import sandbox_delete_blocked
from common.wire_codec import pack_result_v2
from remote.job_fs import RESULT_BACKUP_DIR, backup_failed_result
from remote.result_upload import ResultUploader

#: 沙箱 safe-delete 配额耗尽时，把「删除没落地」判成**环境**而非回归（仓内既有约定）。
_SKIP_BLOCKED = "沙箱 safe-delete 配额耗尽拦截删除（环境，非回归）——换 turn 重跑即绿"

RESULT = {
    "job_id": "j-abc",
    "data_fp": "f" * 64,
    "init_weights_fp": "0" * 64,
    "weights_json": "e30=",
    "opt_tar_b64": "",
    "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "steps": 7, "chunks": 1},
    "report": {"games": 2, "winRate": 1.0, "shards": 2},
    "ppo_sec": 1.5,
}


def _sha16(result: dict) -> str:
    return hashlib.sha256(pack_result_v2(result)).hexdigest()[:16]


def test_ok_upload_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """① 成功回传 = 零备份（常态逐字不变）。"""
    monkeypatch.setattr(job_round, "job_ready", lambda *a, **kw: None)
    monkeypatch.setattr(job_round, "start_cancel_watcher", lambda *a, **kw: None)
    uploader = ResultUploader(upload=lambda *a, **kw: 200, mode="sync", log=lambda _m: None)
    out = _round(tmp_path, uploader)
    assert out.uploaded is True
    assert not (tmp_path / "j-abc" / RESULT_BACKUP_DIR).exists()
    settled = uploader.outcome("j-abc")
    assert settled is not None and settled.ok


def test_failed_upload_backs_the_wire_body_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """② 失败 ⇒ `<job_dir>/backup/it<N>-<jid>-<sha256:16>.result.v2`，内容**就是**线上体。"""
    monkeypatch.setattr(job_round, "job_ready", lambda *a, **kw: None)
    monkeypatch.setattr(job_round, "start_cancel_watcher", lambda *a, **kw: None)
    msgs: list[str] = []

    def boom(*a: object, **kw: object) -> int:
        raise RetryableError("result POST 重试 5 次仍失败: HTTP 503")

    uploader = ResultUploader(upload=boom, mode="sync", log=msgs.append)
    out = _round(tmp_path, uploader, log=msgs.append)
    assert out.uploaded is True

    files = sorted((tmp_path / "j-abc" / RESULT_BACKUP_DIR).glob("*.result.v2"))
    assert len(files) == 1, files
    assert files[0].name == f"it7-j-abc-{_sha16(RESULT)}.result.v2"
    assert files[0].read_bytes() == pack_result_v2(RESULT), "备份必须是发出去的那份字节（可直接重发）"
    # 不留临时名（原子落盘的半成品）
    assert not list((tmp_path / "j-abc" / RESULT_BACKUP_DIR).glob("*.tmp-*"))
    # 响亮一行（操作员唯一信号：失败与备份各一行）
    assert any("结果回传失败" in m for m in msgs), msgs
    assert any("已备份" in m for m in msgs), msgs


def test_backup_is_best_effort_and_never_raises(tmp_path: Path) -> None:
    """③ 连盘都写不成 ⇒ 只 WARN、不抛（它是保险，不许反过来把收尾搞挂）。"""
    msgs: list[str] = []
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")  # `blocker/j-abc` 造不出来 ⇒ mkdir 抛
    assert backup_failed_result(blocker, "j-abc", RESULT, it=3, log=msgs.append) is None
    assert any("WARN 结果备份没写成" in m for m in msgs), msgs


def test_backup_is_idempotent_per_payload(tmp_path: Path) -> None:
    """同一份结果备份两次 ⇒ 同一个文件（sha 在名字里）；换一份结果 ⇒ 多一个文件（都不覆盖）。"""
    first = backup_failed_result(tmp_path, "j-abc", RESULT, it=7, log=lambda _m: None)
    again = backup_failed_result(tmp_path, "j-abc", RESULT, it=7, log=lambda _m: None)
    other = backup_failed_result(tmp_path, "j-abc", {**RESULT, "ppo_sec": 9.0}, it=7, log=lambda _m: None)
    assert first == again is not None
    assert other is not None and other != first
    assert len(list((tmp_path / "j-abc" / RESULT_BACKUP_DIR).glob("*.result.v2"))) == 2


def test_prune_reclaims_backups_with_the_job_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """④ 回收：备份住在 job 目录里 ⇒ 随 job 目录被 `prune_job_dirs` 回收（不需新豁免）。"""
    work = tmp_path / "work"
    for i, name in enumerate(("oldest", "middle", "newest")):
        job = work / name
        (job / RESULT_BACKUP_DIR).mkdir(parents=True)
        (job / RESULT_BACKUP_DIR / "it1-x.result.v2").write_bytes(b"body")
        (job / "_result.json").write_text(json.dumps({"job_id": name}), encoding="utf-8")
        os.utime(job, (1000 + i, 1000 + i))  # mtime 定序（prune 按它排序）
    removed = job_fs.prune_job_dirs(work, keep=2, log=lambda _m: None)
    if (work / "oldest").exists() and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert removed == 1 and not (work / "oldest").exists(), "最旧的 job（连同 backup/）该被回收"
    assert (work / "newest" / RESULT_BACKUP_DIR).exists(), "保留窗口内的备份不许动"


# ------------------------------------------------------------------ 夹具：真 run_one_round


def _round(tmp_path: Path, uploader: ResultUploader, *, log=None) -> job_round.RoundOutcome:
    """跑一轮真 `run_one_round`（回传路径全真）：假 run_job + 注入必埋的上传函数。"""
    job = {"job_id": "j-abc", "manifest": {"it": 7}, "status": "ok"}
    return job_round.run_one_round(
        "http://hub.invalid",
        "tok",
        job,
        part_dir=tmp_path,
        code_cache_dir=None,
        pf_store=None,
        polls_since_accept=0,
        worker_id="w-test",
        device="cpu",
        torch_threads=1,
        echo=False,
        restart_argv=None,
        role="online",
        prefetch_depth=1,
        uploader=uploader,
        run_job_fn=lambda *a, **kw: dict(RESULT),
        log=log or (lambda _m: None),
    )
