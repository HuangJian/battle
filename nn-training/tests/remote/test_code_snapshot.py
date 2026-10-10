"""集群代码快照（plan/cluster-code-snapshot）—— 会话冻结、内容寻址、消费侧只读。

覆盖 plan §7.1 的判据：幂等不重打 · 锚死重打 · 损坏自愈 · `read` 不打包不删 · 抢锚 ·
内容寻址不失配 · pid 复用（指纹）防误判 · 无快照回落 per-course 且**不建锚**。

全部用例都传 `repo_root=tmp_path`（或 `BCITY_CODE_SNAPSHOT_DIR` 重定向）——缺省会写**真实**
`<repo>/tmp`，那是生产路径，不是测试目标。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from remote import code_snapshot as cs


def _fake_repo(tmp_path: Path) -> Path:
    """最小可打包仓：`code.zip` 用 `<root>/nn-training/**`，`ts_code.zip` 用 `<root>/src|tools|…`。

    ★ 2026-10-10（P1）：两件物料都得打得出。`pack_ts_code_zip` 对 `src/` · `tools/` ·
    `src/nn/conv/prebuilt/` 三处是**硬要求**（缺一抛 `HubClientError`）——夹具不补齐，
    每个用例都会落到「ts 打包失败」那条降级分支上，ts 的快照路径就永远测不到。
    """
    nn = tmp_path / "nn-training"
    (nn / "curricula").mkdir(parents=True)
    (nn / "trainer.py").write_text("x = 1\n", encoding="utf-8")
    (nn / "curricula" / "c.jsonc").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tools" / "sim").mkdir(parents=True)
    (tmp_path / "tools" / "sim" / "export-rl-rollout.ts").write_text("export const x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "nn" / "conv" / "prebuilt").mkdir(parents=True)
    (tmp_path / "src" / "main.ts").write_text("export const y = 2\n", encoding="utf-8")
    return tmp_path


def _meta(root: Path) -> dict:
    parsed: object = json.loads((cs.snapshot_dir(root) / cs.META_NAME).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "快照元数据必须是 JSON 对象"
    return parsed


def _rewrite_meta(root: Path, meta: dict) -> None:
    (cs.snapshot_dir(root) / cs.META_NAME).write_text(json.dumps(meta), encoding="utf-8")


def _kill_anchor(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把锚标成「已死」：pid 是必然不存在的哨兵 + 指纹不匹配（不依赖 OS 的 pid 状态）。"""
    meta = _meta(root)
    meta["anchor"]["pid"] = 4_000_000
    meta["anchor"]["cmdline_sha12"] = "deadbeefcafe"
    _rewrite_meta(root, meta)
    monkeypatch.setattr(cs, "pid_alive", lambda _pid: False)


def _log_fields(line: str) -> dict[str, str]:
    """把启动期那行日志拆成 `k=v` 字典（**值与措辞解耦**：改一句话不该炸用例）。

    形态：`[code-snapshot] sha12=… packed_at=… anchor=hub/pid=123 reused=0 bytes=… file=…`
    """
    body = line.split("] ", 1)[1]
    out: dict[str, str] = {}
    for tok in body.split(" "):
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def _counting_pack(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """数 `pack_code_zip` 被调了几次（真正打包是唯一"贵"的副作用）。"""
    calls: list[int] = []
    real = cs.pack_code_zip

    def _spy(root, path, *, log=lambda _m: None):
        calls.append(1)
        return real(root, path, log=log)

    monkeypatch.setattr(cs, "pack_code_zip", _spy)
    return calls


def test_ensure_packs_once_then_reuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """启动路径：第一次打，锚活着时后续每次都是复用（且不改锚主人）。"""
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    lines: list[str] = []
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", log=lines.append, repo_root=root)
    assert a.reused is False and len(calls) == 1
    assert a.zip_path.is_file() and a.sha256
    f0 = _log_fields(lines[-1])
    assert f0["reused"] == "0" and f0["anchor"] == f"hub/pid={a.anchor_pid}"
    assert f0["sha12"] == a.sha256[:12]
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", log=lines.append, repo_root=root)
    assert b.reused is True and len(calls) == 1
    assert (b.sha256, b.zip_path, b.anchor_kind, b.anchor_pid) == (
        a.sha256,
        a.zip_path,
        "hub",  # 复用不改锚：仍是 hub 那一份
        a.anchor_pid,
    )
    assert _log_fields(lines[-1])["reused"] == "1"


def test_read_is_pure_and_none_without_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`read_*` 绝不打包、绝不建目录：没有快照就是 None（消费侧据此回落）。"""
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    assert cs.read_cluster_snapshot(root) is None
    assert calls == []
    assert not cs.snapshot_dir(root).exists()


def test_read_reports_dead_anchor_without_touching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锚已死 ⇒ 仍然返回对象（`anchor_alive=False`）且**盘上文件一个没少**。"""
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    _kill_anchor(root, monkeypatch)
    before = sorted(p.name for p in cs.snapshot_dir(root).iterdir())
    got = cs.read_cluster_snapshot(root)
    assert got is not None and got.anchor_alive is False and got.sha256 == a.sha256
    assert calls == [1]
    assert sorted(p.name for p in cs.snapshot_dir(root).iterdir()) == before


def test_corrupt_zip_is_healed_on_ensure_not_on_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """zip 被截断（sha 与元数据不符）：read ⇒ None（不删不修），ensure ⇒ 重打。"""
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    a.zip_path.write_bytes(b"PK\x03\x04 truncated")
    assert cs.read_cluster_snapshot(root) is None
    assert calls == [1]  # read 本身不打包
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", repo_root=root)
    assert b.reused is False and len(calls) == 2
    assert cs.read_cluster_snapshot(root) is not None


def test_source_change_keeps_the_old_content_addressed_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内容寻址的全部价值：锚更替后新代码是**新文件**，旧文件仍是旧字节。

    固定名 `code.zip` 下，活着的消费者（缓存了 path+sha）会读到新字节配旧 sha ⇒
    云端 `ensure_code` 逐候选按 manifest 的 sha 选件必对不上（plan §3.1'）。
    """
    root = _fake_repo(tmp_path)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    (root / "nn-training" / "trainer.py").write_text("x = 2\n", encoding="utf-8")
    _kill_anchor(root, monkeypatch)
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", repo_root=root)
    assert b.sha256 != a.sha256
    assert b.zip_path != a.zip_path
    assert a.zip_path.is_file()  # 旧副本保留 ⇒ 缓存永不失配
    now = cs.read_cluster_snapshot(root)
    assert now is not None and now.zip_path == b.zip_path


def test_repack_with_unchanged_source_is_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """误重打几乎免费（打包器固定时间戳 ⇒ 同源码同 sha ⇒ 不新增文件、不换路径）。"""
    root = _fake_repo(tmp_path)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    _kill_anchor(root, monkeypatch)
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", repo_root=root)
    assert b.reused is False and b.sha256 == a.sha256 and b.zip_path == a.zip_path
    zips = sorted(p.name for p in cs.snapshot_dir(root).glob("code.*.zip"))
    assert zips == [a.zip_path.name]


def test_pid_reuse_fingerprint_mismatch_repacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anchor pid 存活（＝本进程）但命令行指纹不符 ⇒ 判 PID 复用 ⇒ 重打。"""
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    assert a.anchor_pid == os.getpid()
    monkeypatch.setattr(cs, "proc_cmdline", lambda _pid: "some other program --x")
    # 读面是廉价口径（只看 pid）——它不分支于指纹，所以这里仍是 True；
    # 严格口径（= ensure 的分岔口）必须说「不是同一个进程」。
    loose = cs.read_cluster_snapshot(root)
    assert loose is not None and loose.anchor_alive is True
    strict = cs.read_cluster_snapshot(root, strict_anchor=True)
    assert strict is not None and strict.anchor_alive is False
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", repo_root=root)
    assert b.reused is False and len(calls) == 2


def test_unreadable_cmdline_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命令行读不到（无 /proc、wmic 被移除…）⇒ 不可核验 ⇒ 重打（fail-closed）。"""
    root = _fake_repo(tmp_path)
    a = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    monkeypatch.setattr(cs, "proc_cmdline", lambda _pid: None)
    strict = cs.read_cluster_snapshot(root, strict_anchor=True)
    assert strict is not None and strict.anchor_alive is False
    b = cs.ensure_cluster_snapshot(anchor_kind="trainer", repo_root=root)
    assert b.reused is False and b.sha256 == a.sha256  # 同源码 ⇒ 同 sha（代价≈0）


def test_stale_lock_is_taken_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """锁文件残留（持有者已死）⇒ 接管并照常打一份，不会被一把陈锁永久挡住。"""
    root = _fake_repo(tmp_path)
    d = cs.snapshot_dir(root)
    d.mkdir(parents=True)
    (d / cs.LOCK_NAME).write_text("4000000|python|1", encoding="utf-8")
    monkeypatch.setattr(cs, "pid_alive", lambda _pid: False)
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    assert snap.reused is False and snap.zip_path.is_file()
    assert not (d / cs.LOCK_NAME).exists()  # 用完就删


def test_live_packer_does_not_hang_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """别人正在打（锁被活进程持有）⇒ 有界等待后**自己打**：启动绝不挂住。

    等待窗口在用例里压到 50ms（生产 10s）——判据是「超时后仍然拿到快照」，不是等多久。
    """
    root = _fake_repo(tmp_path)
    calls = _counting_pack(monkeypatch)
    d = cs.snapshot_dir(root)
    d.mkdir(parents=True)
    (d / cs.LOCK_NAME).write_text(f"{os.getpid()}|python|1", encoding="utf-8")
    monkeypatch.setattr(cs, "LOCK_WAIT_SEC", 0.05)
    monkeypatch.setattr(cs, "LOCK_POLL_SEC", 0.01)
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    assert snap.reused is False and len(calls) == 1
    assert cs.read_cluster_snapshot(root) is not None


def test_prune_keeps_only_recent_zips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧锚的副本不能无限堆：留最近 `KEEP_ZIPS` 份（删不动只 WARN，不影响本次快照）。"""
    root = _fake_repo(tmp_path)
    d = cs.snapshot_dir(root)
    d.mkdir(parents=True)
    old = time.time() - 10_000
    for i in range(cs.KEEP_ZIPS + 2):
        p = d / f"code.old{i:06d}.zip"
        p.write_bytes(b"stale")
        os.utime(p, (old + i, old + i))
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    zips = sorted(p.name for p in d.glob("code.*.zip"))
    assert snap.zip_path.name in zips
    assert len(zips) == cs.KEEP_ZIPS
    # 最新的那些留着、最旧的被清掉
    assert not (d / "code.old000000.zip").exists()


# ---------------------------------------------------------------- 消费路径（publish）


def test_published_code_zip_prefers_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有快照 ⇒ publish 用快照（会话冻结），**不**动 per-course 的 code.zip。"""
    snap_source = _fake_repo(tmp_path / "src")
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=snap_source)
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(cs.snapshot_dir(snap_source)))
    job_root = tmp_path / "traj" / "course" / "remote-jobs"
    path, sha, used = cs.published_code_zip(
        job_root, pack_root=snap_source / "nn-training", log=lambda _m: None
    )
    assert used is True and path == snap.zip_path and sha == snap.sha256
    assert not (job_root / "code.zip").exists()


def test_published_code_zip_falls_back_without_creating_an_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无快照 ⇒ 回落 per-course 打包 + WARN，且**绝不**悄悄建快照（plan §3.2 / 评审 F4）。"""
    snap_dir = tmp_path / "snap"  # 存在但里面没有快照
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(snap_dir))
    repo = _fake_repo(tmp_path / "repo")
    job_root = repo / "tmp" / "course" / "remote-jobs"
    lines: list[str] = []
    path, sha, used = cs.published_code_zip(
        job_root, pack_root=repo / "nn-training", log=lines.append
    )
    assert used is False and path == job_root / "code.zip" and path.is_file() and sha
    # 回落必须**响亮**（否则「会话中途换了代码」这件事再次静默）
    assert lines and lines[0].startswith("WARN: 集群代码快照缺失")
    assert not (snap_dir / cs.META_NAME).exists()  # 消费侧不建锚


def test_current_code_zip_path_prefers_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """push 腿只解析路径（不打包）：快照在就用快照，否则 job_root 里那份。"""
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(tmp_path / "no-snap"))
    job_root = tmp_path / "traj" / "course" / "remote-jobs"
    assert cs.current_code_zip_path(job_root) == job_root / "code.zip"
    snap_source = _fake_repo(tmp_path / "src")
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=snap_source)
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(cs.snapshot_dir(snap_source)))
    assert cs.current_code_zip_path(job_root) == snap.zip_path


# ------------------------------------------------- TS 运行时（P1：与 code.zip 同一份会话快照）


def test_snapshot_packs_both_artifacts(tmp_path: Path) -> None:
    """一次 `ensure` 产出两件：`code.<sha12>.zip` + `ts_code.<sha12>.zip`，且元数据/读面同源。"""
    root = _fake_repo(tmp_path)
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    assert snap.ts_zip_path is not None and snap.ts_zip_path.is_file()
    assert snap.ts_zip_path.name.startswith(cs._TS_ZIP_PREFIX)
    assert snap.ts_zip_path.name != snap.zip_path.name  # 两族文件名不可能互撞
    assert snap.ts_sha256 and snap.ts_bytes > 0
    assert snap.ts_sha256[:12] in snap.ts_zip_path.name  # 内容寻址：文件名 = 内容
    meta = _meta(root)
    assert meta["ts_zip"] == snap.ts_zip_path.name
    assert meta["ts_sha256"] == snap.ts_sha256
    got = cs.read_cluster_snapshot(root)
    assert got is not None and got.ts_zip_path == snap.ts_zip_path
    assert got.ts_sha256 == snap.ts_sha256 and got.ts_bytes == snap.ts_bytes


def test_ts_part_is_best_effort_and_does_not_block_code_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ts 打包失败 ⇒ code.zip 快照照常建立（只 WARN + ts 字段留空）——hub 起不来代价更高。"""
    root = _fake_repo(tmp_path)
    lines: list[str] = []

    def boom(repo_root: object, zip_path: object, *, log: object = None) -> str:
        raise RuntimeError("no bun / no src")

    monkeypatch.setattr(cs, "pack_ts_code_zip", boom)
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", log=lines.append, repo_root=root)
    assert snap.zip_path.is_file() and snap.sha256  # 关键件在
    assert snap.ts_zip_path is None and snap.ts_sha256 == "" and snap.ts_bytes == 0
    # 降级必须**响亮**（形态判据：只看 WARN 前缀，不钉整句话——文本断言预算只许降，见
    # `tests/test_source_text_assert_budget.py`）
    assert any(m.startswith("[code-snapshot] WARN: TS") for m in lines), lines
    meta = _meta(root)
    assert meta["ts_zip"] == "" and meta["ts_sha256"] == ""
    got = cs.read_cluster_snapshot(root)
    assert got is not None and got.ts_zip_path is None  # 读面把「没有 ts 件」如实报出来
    assert _log_fields(lines[-1])["ts"] == "none"


def test_published_ts_code_zip_prefers_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有快照 ⇒ TS 走快照（会话冻结），**不**在 job_root 里留 per-course 的那份。"""
    snap_source = _fake_repo(tmp_path / "src")
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=snap_source)
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(cs.snapshot_dir(snap_source)))
    job_root = tmp_path / "traj" / "course" / "remote-jobs"
    path, sha, used = cs.published_ts_code_zip(
        job_root, repo_root=snap_source, log=lambda _m: None
    )
    assert used is True and path == snap.ts_zip_path and sha == snap.ts_sha256
    assert not (job_root / "ts_code.zip").exists()


def test_published_ts_code_zip_falls_back_without_creating_an_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无快照 ⇒ 回落 per-course 打包 + WARN，且**绝不**建快照（与 code 件同规）。"""
    snap_dir = tmp_path / "snap"
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(snap_dir))
    repo = _fake_repo(tmp_path / "repo")
    job_root = repo / "tmp" / "course" / "remote-jobs"
    lines: list[str] = []
    path, sha, used = cs.published_ts_code_zip(job_root, repo_root=repo, log=lines.append)
    assert used is False and path == job_root / "ts_code.zip" and path.is_file() and sha
    assert lines and lines[0].startswith("WARN: 集群代码快照缺失")
    assert not (snap_dir / cs.META_NAME).exists()


def test_published_ts_code_zip_rejects_a_corrupt_ts_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ts 件被截断（元数据 sha 没变）⇒ 消费点核验拦下 ⇒ 回落 per-course。

    设计上是**故意**把 ts 的核验放在这里而不是 `read_*`：读面带 code.zip 已经哈希 1.4MB，
    再叠 2.5MB 的 ts 就是给不关心 ts 的调用方白付（见 `CodeSnapshot.ts_sha256` 注释）。
    """
    snap_source = _fake_repo(tmp_path / "src")
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=snap_source)
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(cs.snapshot_dir(snap_source)))
    assert snap.ts_zip_path is not None
    snap.ts_zip_path.write_bytes(b"PK\x03\x04 truncated")
    # 读面只说「文件在」（形状检查），不假装它能用：
    got = cs.read_cluster_snapshot(snap_source)
    assert got is not None and got.ts_zip_path is not None
    job_root = tmp_path / "traj" / "course" / "remote-jobs"
    lines: list[str] = []
    path, sha, used = cs.published_ts_code_zip(job_root, repo_root=snap_source, log=lines.append)
    assert used is False and path == job_root / "ts_code.zip" and path.is_file()
    # 回落打的是**同一个源码根** ⇒ 固定时间戳打包器给同一个 sha（离线/内容寻址的既有性质）：
    # 这里要比的是「用的是哪份文件」，不是 sha 值——sha 相同恰恰说明回落没换掉内容。
    assert sha == snap.ts_sha256
    assert any(m.startswith("WARN: 集群快照里的 ts_code") for m in lines), lines


def test_prune_keeps_both_families(tmp_path: Path) -> None:
    """两族各自的保留窗口独立：ts 件变新不许把刚打的 `code.zip` 挤掉。"""
    root = _fake_repo(tmp_path)
    d = cs.snapshot_dir(root)
    d.mkdir(parents=True)
    old = time.time() - 10_000
    for i in range(cs.KEEP_ZIPS + 2):
        for name in (f"code.old{i:06d}.zip", f"ts_code.old{i:06d}.zip"):
            p = d / name
            p.write_bytes(b"stale")
            os.utime(p, (old + i, old + i))
    snap = cs.ensure_cluster_snapshot(anchor_kind="hub", repo_root=root)
    assert len(sorted(d.glob("code.*.zip"))) == cs.KEEP_ZIPS
    assert len(sorted(d.glob("ts_code.*.zip"))) == cs.KEEP_ZIPS
    assert snap.zip_path.is_file() and snap.ts_zip_path is not None and snap.ts_zip_path.is_file()
    assert not (d / "code.old000000.zip").exists()
    assert not (d / "ts_code.old000000.zip").exists()
