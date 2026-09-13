"""I1（roadmap Phase 0）commit_journal / forensics 的单测。

事故背景：c5-tick / c6-bonus 两起「rollout 150/150 settled → PPO 提交返回前进程消失、
无堆栈」。WAL（started/done）让重启后一条日志就知道上一轮提交到哪；forensics 快照
让 OOM/写盘失败这类「无堆栈死法」留下临终状态。

注入测试：子进程 journal.start 后 `os._exit(1)` 硬死（绕过 atexit/finally——
kill -9 / OOM killer 的同构模拟），父进程验证 pending() 能看见未完成轮次并可收口。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.commit_journal import CommitJournal
from rl.forensics import log_snapshot, rss_mb, snapshot

# ---- CommitJournal：WAL 语义 ----


def test_start_finish_roundtrip(tmp_path: Path) -> None:
    j = CommitJournal(tmp_path / "commit_journal.jsonl")
    assert j.pending() == []  # 从未提交过 = 无 pending
    j.start("ppo_local", "7")
    pend = j.pending()
    assert pend == [{"phase": "ppo_local", "round": "7"}]
    j.finish("ppo_local", "7")
    assert j.pending() == []


def test_double_start_same_round_is_one_pending(tmp_path: Path) -> None:
    """重试轮重复 start 同一 round 合法——最后一条 wins，pending 仍是一项。"""
    j = CommitJournal(tmp_path / "j.jsonl")
    j.start("ppo_local", "7")
    j.start("ppo_local", "7")
    assert j.pending() == [{"phase": "ppo_local", "round": "7"}]


def test_phases_and_rounds_independent(tmp_path: Path) -> None:
    j = CommitJournal(tmp_path / "j.jsonl")
    j.start("ppo_local", "7")
    j.start("ppo_remote", "7")
    j.finish("ppo_local", "7")
    pend = j.pending()
    assert pend == [{"phase": "ppo_remote", "round": "7"}]


def test_corrupt_line_skipped(tmp_path: Path) -> None:
    """坏行（进程死于写入中间的半行 json）跳过不抛——WAL 按最后一条 wins 读。"""
    p = tmp_path / "j.jsonl"
    j = CommitJournal(p)
    j.start("ppo_local", "7")
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"event": "commit_journal", "op": "star')  # 半行截断
    assert j.pending() == [{"phase": "ppo_local", "round": "7"}]
    j.finish("ppo_local", "7")
    assert j.pending() == []


def test_missing_file_no_pending(tmp_path: Path) -> None:
    assert CommitJournal(tmp_path / "nonexistent.jsonl").pending() == []


def test_hard_exit_replay(tmp_path: Path) -> None:
    """★ 注入：子进程 start 后 os._exit(1) 硬死（kill-9/OOM 同构），父进程按
    pending 收口（finish）——「断点重放」的最小闭环。"""
    jpath = tmp_path / "j.jsonl"
    child = (
        "import os, sys\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "from rl.commit_journal import CommitJournal\n"
        f"j = CommitJournal(r'{jpath}')\n"
        "j.start('ppo_remote', '41')\n"
        "os._exit(1)\n"  # 硬死：不跑 finally/atexit，与 kill -9 / OOM 同构
    )
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(ROOT),
    )
    assert r.returncode != 0  # 确实是硬死
    parent = CommitJournal(jpath)
    assert parent.pending() == [{"phase": "ppo_remote", "round": "41"}]
    # 重放收口：同 round 重新 start（重试）→ finish → pending 清空
    parent.start("ppo_remote", "41")
    parent.finish("ppo_remote", "41")
    assert parent.pending() == []


def test_journal_lines_are_jsonl_audit_trail(tmp_path: Path) -> None:
    """WAL 是追加账本：start/finish 都留痕（带 ts/pid），复盘可回放全过程。"""
    j = CommitJournal(tmp_path / "j.jsonl")
    j.start("ppo_local", "3", note="x")
    j.finish("ppo_local", "3", jid="job-1")
    lines = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert [rec["op"] for rec in lines] == ["start", "finish"]
    assert lines[0]["event"] == "commit_journal" and "ts" in lines[0] and "pid" in lines[0]
    assert lines[1]["jid"] == "job-1"


# ---- forensics：取证快照 ----


def test_rss_mb_sane() -> None:
    cur, peak = rss_mb()
    assert cur >= 0 and peak >= 0  # 任何平台失败都降级为 0，绝不抛
    if peak > 0:
        assert peak >= cur  # 峰值 ≥ 当前


def test_snapshot_fields(tmp_path: Path) -> None:
    snap = snapshot("test", paths=[tmp_path])
    assert snap["event"] == "forensics"
    assert snap["tag"] == "test"
    assert snap["rss_mb"] >= 0 and snap["rss_peak_mb"] >= snap["rss_mb"] - 1e-6
    assert snap["disk_free_mb"][str(tmp_path)] > 0


def test_log_snapshot_writes_jsonl(tmp_path: Path) -> None:
    jpath = tmp_path / "tl.jsonl"
    snap = log_snapshot("ppo_local_pre it1", jpath, paths=[tmp_path])
    lines = [json.loads(x) for x in jpath.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["tag"] == "ppo_local_pre it1"
    assert lines[0]["event"] == "forensics"
    assert snap["rss_mb"] >= 0
