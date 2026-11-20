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

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.commit_journal import CommitJournal
from worker.forensics import log_snapshot, rss_mb, snapshot

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
        "from worker.commit_journal import CommitJournal\n"
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


# ---- R2b：attach / inflight（在飞集：job_id + 等谁回传）----


def test_attach_does_not_change_pending_state(tmp_path: Path) -> None:
    """attach 只补事实，**不改状态机**：start→attach→attach 仍算「未完成」。"""
    j = CommitJournal(tmp_path / "j.jsonl")
    j.start("ppo_remote", "37")
    j.attach("ppo_remote", "37", jid="job-a", dispatch="push")
    j.attach("ppo_remote", "37", jid="job-a", dispatch="push", attempt=2)
    assert j.pending() == [{"phase": "ppo_remote", "round": "37"}]
    got = j.inflight()
    assert len(got) == 1
    # 附带字段（ts/pid）是刻意保留的：说清楚「何时、由哪个 pid 发布」
    assert {k: got[0][k] for k in ("phase", "round", "jid", "dispatch", "attempt")} == {
        "phase": "ppo_remote",
        "round": "37",
        "jid": "job-a",
        "dispatch": "push",
        "attempt": 2,
    }
    assert "ts" in got[0] and "pid" in got[0]


def test_attach_before_start_is_not_inflight(tmp_path: Path) -> None:
    """只有**已开始**的提交才算在飞：孤立 attach 不凭空造出一个在飞任务。"""
    j = CommitJournal(tmp_path / "j.jsonl")
    j.attach("ppo_remote", "37", jid="job-a")
    assert j.pending() == [] and j.inflight() == []


def test_finish_clears_inflight_and_late_attach_cannot_resurrect_it(tmp_path: Path) -> None:
    """完成即在飞集里消失；完成后再来的 attach 不得把它拉回来（最后一条 start/finish wins）。"""
    j = CommitJournal(tmp_path / "j.jsonl")
    j.start("ppo_remote", "37", jid="job-a")
    j.finish("ppo_remote", "37", jid="job-a")
    j.attach("ppo_remote", "37", late=True)
    assert j.inflight() == []


def test_inflight_survives_hard_exit_with_job_id(tmp_path: Path) -> None:
    """★ R2b 的核心价值：硬死后能看到「在等哪个 job、推给了谁」。"""
    jpath = tmp_path / "j.jsonl"
    child = (
        "import os, sys\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "from worker.commit_journal import CommitJournal\n"
        f"j = CommitJournal(r'{jpath}')\n"
        "j.start('ppo_remote', '41')\n"
        "j.attach('ppo_remote', '41', jid='job-zzz', dispatch='push')\n"
        "os._exit(1)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ),
        cwd=str(ROOT),
    )
    assert r.returncode != 0
    parent = CommitJournal(jpath)
    got = parent.inflight()
    assert len(got) == 1
    assert {k: got[0][k] for k in ("phase", "round", "jid", "dispatch")} == {
        "phase": "ppo_remote",
        "round": "41",
        "jid": "job-zzz",
        "dispatch": "push",
    }
    # 重放收口后，在飞集清空
    parent.finish("ppo_remote", "41", jid="job-zzz")
    assert parent.inflight() == []


def test_inflight_skips_corrupt_lines(tmp_path: Path) -> None:
    p = tmp_path / "j.jsonl"
    j = CommitJournal(p)
    j.start("ppo_remote", "41")
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"event": "commit_journal", "op": "att')  # 半行截断
    assert j.inflight() == [{"phase": "ppo_remote", "round": "41"}]


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


def test_rss_probe_survives_a_foreign_module_rewriting_kernel32(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 2026-11-20 全量门禁偶发红（同进程内**保质**）：第三方改写了进程级共享的 ctypes 签名。

    现场：`paddle/base/core.py` 里 `ctypes.windll.kernel32.GetCurrentProcess.restype = c_void_p`
    —— `ctypes.windll` 是**每进程一份**的 `LibraryLoader`，这一改在全进程生效。此后
    `GetCurrentProcess()` 返回的是 `0xFFFFFFFFFFFFFFFF`（而不是默认 `c_int` 截断出的 -1），
    而旧实现把 Python int **直接**塞进未声明 `argtypes` 的 `GetProcessMemoryInfo`（按 c_int 转换）
    ⇒ `ctypes.ArgumentError: argument 1: OverflowError: int too long to convert`
    ⇒ `rss_mb=0.0` 且 `probe_errors={'rss': ...}`。

    症状为何看着像「偶发」：只有**先 import 过 paddle 的那些 worker** 中招，而一旦中招就一直是它
    （现场 `test_commit_journal` 三条断言在同一个 pid 上连红）。判据：伪句柄变成 64 位语义之后，
    `snapshot()` 仍必须拿到**真读数**，且不带 `probe_errors['rss']`。
    """
    if os.name != "nt":
        pytest.skip("Windows 才有 psapi + 伪句柄这条路")

    import ctypes
    from ctypes import wintypes

    import worker.forensics as forensics

    # mypy 按 Linux 平台检查（typeshed 里 `ctypes.windll` 只在 Windows 存在）⇒ 经 `__dict__` 取，
    # 不为这个测试加 `# type: ignore`（抑制符预算只许降不许升）。
    k32 = ctypes.__dict__["windll"].kernel32
    # 与 paddle 同款：把返回值声明成 64 位句柄（`HANDLE` == `c_void_p`），不再按 c_int 截断。
    monkeypatch.setattr(k32.GetCurrentProcess, "restype", wintypes.HANDLE)

    snap = forensics.snapshot("rss-poison", paths=[tmp_path])
    # 取值即断言（不用字符串成员判据：那会被文本形态预算计入，而这完全不是「读源码文本」）。
    rss_failure = (snap.get("probe_errors") or {}).get("rss")
    assert rss_failure is None, f"共享签名被第三方改写后 RSS 探针崩了：{rss_failure}"
    assert snap["rss_mb"] > 0, "探针必须给出真读数（0 会被当成 OOM 证据）"


def test_log_snapshot_writes_jsonl(tmp_path: Path) -> None:
    jpath = tmp_path / "tl.jsonl"
    snap = log_snapshot("ppo_local_pre it1", jpath, paths=[tmp_path])
    lines = [json.loads(x) for x in jpath.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["tag"] == "ppo_local_pre it1"
    assert lines[0]["event"] == "forensics"
    assert snap["rss_mb"] >= 0


# ---- forensics：降级要说得清为什么（G9 第二种处置，2026-11-15） ----


def test_snapshot_records_why_the_rss_probe_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RSS 探针失败 ⇒ 值仍是 0（绝不反杀训练），但快照里留有成因。

    这份 jsonl 的读者是事故后的人：「rss_mb: 0」与「探针自己失败了」在文件里长得一模一样，
    而 OOM 判定恰恰只认这个数。降级不等于静默 —— 缺省值旁边要有一行「为什么」。
    """
    import worker.forensics as forensics

    if os.name == "nt":
        pytest.skip("POSIX 探针分派（Windows 走 _rss_mb_windows）")

    def _boom(errors=None):
        raise OSError("/proc/self/status 不可读")

    monkeypatch.setattr(forensics, "_rss_mb_posix", _boom)
    snap = forensics.snapshot("oom-check", paths=[tmp_path])
    assert snap["rss_mb"] == 0.0 and snap["rss_peak_mb"] == 0.0
    # 取键即断言（缺 `rss` 会 KeyError）；前缀判据钉住「成因类型」而不钉文案全文。
    assert snap["probe_errors"]["rss"].startswith("OSError")


def test_rss_mb_never_raises_even_when_the_probe_explodes(monkeypatch) -> None:
    """`rss_mb()` 的契约是「绝不出错」——探针自己炸了也必须降级成 (0.0, 0.0)。"""
    import worker.forensics as forensics

    if os.name == "nt":
        pytest.skip("POSIX 探针分派（Windows 走 _rss_mb_windows）")

    def _boom(errors=None):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(forensics, "_rss_mb_posix", _boom)
    assert forensics.rss_mb() == (0.0, 0.0)


def test_snapshot_records_a_disk_path_it_could_not_measure(tmp_path: Path) -> None:
    """量不到的盘要留名（`disk:<path>`）：静默丢键看起来就像「这里没有盘压力」。"""
    missing = tmp_path / "no-such-dir"
    snap = snapshot("disk-check", paths=[missing])
    assert set(snap) == {k for k in snap if k != "disk_free_mb"}  # 没有 disk_free_mb 这个键
    assert set(snap["probe_errors"]) == {f"disk:{missing}"}


def test_snapshot_stays_schema_clean_when_everything_works(tmp_path: Path) -> None:
    """全好时不出现 `probe_errors`（干净路径的 schema 逐字不变）。"""
    snap = snapshot("ok", paths=[tmp_path])
    assert {"event", "tag", "ts", "pid", "rss_mb", "rss_peak_mb", "disk_free_mb"} <= set(snap)
    assert set(snap).isdisjoint({"probe_errors"})


def test_log_line_names_the_fields_that_could_not_be_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """控制台上那行也要说得清：`rss=0MB` 旁边得有 `probe_errors=rss`（全文在 jsonl 里）。"""
    import worker.forensics as forensics

    if os.name == "nt":
        pytest.skip("POSIX 探针分派（Windows 走 _rss_mb_windows）")

    def _boom(errors=None):
        raise OSError("/proc/self/status 不可读")

    monkeypatch.setattr(forensics, "_rss_mb_posix", _boom)
    forensics.log_snapshot("t", tmp_path / "tl.jsonl")
    assert capsys.readouterr().out.count("probe_errors=rss") == 1


def test_log_line_stays_short_when_everything_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """干净路径不出现该后缀（那行本来就短；只有一个字段坏了才加它）。"""
    log_snapshot("ok", tmp_path / "tl.jsonl", paths=[tmp_path])
    assert capsys.readouterr().out.count("probe_errors") == 0
