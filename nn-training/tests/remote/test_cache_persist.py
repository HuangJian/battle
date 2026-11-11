"""test_cache_persist.py — **持久缓存根 + 容量上限**（plan/aistudio-transfer-hardening §3.3，A3）。

现场：aistudio 的 `/tmp` 每会话被清 ⇒ 每次冷启都要重下 code（2.5MB）/ init / ref，一轮 3.23MB
冷启动税。把内容寻址缓存（code/ts_code/blob）挂到**持久目录**就跨会话复用；job 工作目录
仍留 `/tmp`（每轮清场的语义不变）。

换根之后暴露出第二个洞：这三棵树按 sha 存且**原本完全没有预算**（预取暂存区有 64MB，它们
没有）⇒ 每会话一次热替换就是一份新 sha ⇒ 无界增长。所以「换根」和「封顶」是**同一刀的两半**，
拆开做就是拿磁盘换冷启动。

本文件钉四件事：

1. **只挪缓存，不挪 job 目录**（`--out` 的形状逐字不变）；
2. **三棵树共用一个预算**（每棵各给一份 = 把上限悄悄放成三倍）；
3. **按 mtime 丢最旧**，且**命中要 touch** —— 不 touch 的话删掉的不是「最久没用」而是
   「最早写进来」（那不是 LRU，是「谁先来谁先死」，会把天天命中的 code 删掉）；
4. **写入中的 `*.tmp` 永不 prune**（`replace` 自己会收拾；删它 = 把正在落地的件打断）。
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.download as dl
import remote.job_round as round_mod
import remote.plan_handoff as ph
import remote.worker as W

MB = 1024 * 1024


def _mk(root: Path, rel: str, nbytes: int, *, age_sec: float = 0.0) -> Path:
    """在 `root/rel` 造一个**目录型**缓存条目（含一个 nbytes 的文件），mtime 按 age 回拨。"""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "payload.bin").write_bytes(b"x" * nbytes)
    if age_sec:
        os.utime(d, (time.time() - age_sec, time.time() - age_sec))
        os.utime(d / "payload.bin", (time.time() - age_sec, time.time() - age_sec))
    return d


# ───────────────── ① prune：最旧先丢 ─────────────────


def test_prune_drops_the_oldest_until_the_total_fits(tmp_path: Path) -> None:
    """超预算 ⇒ 按 mtime 最旧先丢，丢到**合计**进预算为止（新的那份必须活着）。"""
    root = tmp_path / "cache"
    old = _mk(root, "code_cache/aaaa", 1 * MB, age_sec=300)
    mid = _mk(root, "code_cache/bbbb", 1 * MB, age_sec=200)
    new = _mk(root, "code_cache/cccc", 1 * MB, age_sec=10)
    logs: list[str] = []
    dropped = dl.prune_caches(root, budget_bytes=1 * MB, log=logs.append)
    assert dropped == 2, f"3MB / 上限 1MB ⇒ 应当丢 2 件（实丢 {dropped}）"
    assert not old.exists() and not mid.exists(), "最旧的两件该走"
    assert new.exists(), "最新的一件必须留着（丢新留旧 = 每轮都白下）"
    assert len(logs) == 1, f"prune 是可观测事件（静默删盘会被读成 bug）：{logs}"


def test_the_budget_is_one_pot_for_all_three_trees(tmp_path: Path) -> None:
    """★ 三棵树**合计**封顶：每棵都没超、加起来超 ⇒ 照样要丢（每棵各给一份 = 上限 ×3）。"""
    root = tmp_path / "cache"
    old = _mk(root, "code_cache/aaaa", 1 * MB, age_sec=300)
    new = _mk(root, "blob_cache/bbbb", 1 * MB, age_sec=10)
    dropped = dl.prune_caches(root, budget_bytes=1 * MB, log=lambda _m: None)
    assert dropped == 1, f"两棵各 1MB、合计上限 1MB ⇒ 应当丢 1 件（实丢 {dropped}）"
    assert not old.exists(), "跨树记账：丢的是**全局最旧**那一件，不是「本树没超就不动」"
    assert new.exists()


def test_tmp_files_are_never_pruned(tmp_path: Path) -> None:
    """`*.tmp` 是写入中的半成品：删它 = 把正在落地的件打断（`replace` 自己会收拾）。"""
    root = tmp_path / "cache"
    half = root / "code_cache" / "writing.tmp"
    half.mkdir(parents=True)
    (half / "part.bin").write_bytes(b"x" * (4 * MB))
    # 只这件（2MB）就已超 1MB 预算 ⇒ prune 一定会动手；动手时**不许**碰 .tmp
    keep = _mk(root, "code_cache/cccc", 2 * MB, age_sec=1)
    dropped = dl.prune_caches(root, budget_bytes=1 * MB, log=lambda _m: None)
    assert dropped == 1, f"该丢的那件没丢（prune 没真跑）：{dropped}"
    assert half.exists(), "半成品被 prune 了（断点续传的半截会被打断）"
    assert not keep.exists()


def test_a_non_positive_budget_disables_pruning(tmp_path: Path) -> None:
    """预算 ≤0 = 关闭（旧行为）：有人显式关掉时不许偷偷删盘。"""
    root = tmp_path / "cache"
    keep = _mk(root, "code_cache/aaaa", 1 * MB, age_sec=999)
    assert dl.prune_caches(root, budget_bytes=0, log=lambda _m: None) == 0
    assert keep.exists()


# ───────────────── ② 命中要 touch（否则 prune 不是 LRU）─────────────────


def test_a_blob_cache_hit_touches_mtime(tmp_path: Path) -> None:
    """★ A3 的那一半：`_cache_blob` 命中（字节一致）必须 touch —— 不然「天天命中但本会话
    没重写」的件会被当最旧删掉。"""
    root = tmp_path / "blob_cache"
    root.mkdir()
    raw = b"opt" * 64
    sha = "a" * 64
    p = root / sha
    p.write_bytes(raw)
    os.utime(p, (time.time() - 10_000, time.time() - 10_000))
    before = p.stat().st_mtime
    dl._cache_blob(root, sha, raw, lambda _m: None)
    assert p.stat().st_mtime > before, "命中没 touch mtime ⇒ prune 会把高频命中的件当最旧删掉"
    assert p.read_bytes() == raw, "touch 不该动字节"


def test_a_blob_cache_read_hit_touches_mtime(tmp_path: Path) -> None:
    """★ 读路径的命中也要 touch（`_resolve_blob` / `resolve_blobs_batch`）。

    现场（2026-11-11 07:33，o2-notax it163）：段启动时 `_seed_ref_blob_cache` 把 kickstart ref
    写进 `blob_cache/<sha>`，此后**每轮都命中、但一次也没 touch** —— 于是它在合计预算的
    prune 里永远是「最旧」的那一件。跑了约 100 分钟后 prune 终于把它挑中删掉，下一轮
    `_resolve_blob` 只能向空 base_url 要字节 ⇒ `ProtocolError` 打死整段（自主腿没有下载通道，
    确定性、重试不会变好）。

    `_cache_blob` 早就有这条规则（见上一个用例），写路径修了、读路径漏了。
    """
    root = tmp_path / "blob_cache"
    root.mkdir()
    raw = b"ref" * 64
    sha = hashlib.sha256(raw).hexdigest()
    p = root / sha
    p.write_bytes(raw)
    os.utime(p, (time.time() - 10_000, time.time() - 10_000))
    before = p.stat().st_mtime
    got, hit, src = dl._resolve_blob(
        blob_root=root,
        name=dl.BLOB_REF,
        sha=sha,
        inline_b64="",
        jid="j1",
        base_url="",
        token="",
        preloaded=None,
        log=lambda _m: None,
    )
    assert got == raw and hit and src == "cache"
    assert p.stat().st_mtime > before, "读命中没 touch ⇒ prune 会把锚点当最旧删掉"
    assert p.read_bytes() == raw, "touch 不该动字节"


def test_a_batch_blob_cache_read_hit_touches_mtime(tmp_path: Path) -> None:
    """批量那条读路径同规（判据同源：改一条必须改两条）。"""
    root = tmp_path / "blob_cache"
    root.mkdir()
    raw = b"opt" * 64
    sha = hashlib.sha256(raw).hexdigest()
    p = root / sha
    p.write_bytes(raw)
    os.utime(p, (time.time() - 10_000, time.time() - 10_000))
    before = p.stat().st_mtime
    out = dl.resolve_blobs_batch(
        wanted={dl.BLOB_OPT: sha},
        blob_root=root,
        jid="j1",
        base_url="",
        token="",
        preloaded=None,
        log=lambda _m: None,
    )
    assert out[dl.BLOB_OPT][0] == raw and out[dl.BLOB_OPT][2] == "cache"
    assert p.stat().st_mtime > before, "批量读命中没 touch ⇒ 同一件会先于真正冷件被删"


def test_prune_never_drops_a_pinned_anchor(tmp_path: Path) -> None:
    """★ B：manifest 点名的锚点（ref/demo/opt）**不许被 prune 挑中**——哪怕它是最旧的那一件。

    为什么不能只靠「命中 touch」（A）：touch 依赖 mtime 排序这个概率，而配额是共享池。
    某一拍如果一次性落进来一批大件，锚点仍可能被挑中；它一旦被删，自主腿就没有第二份字节。
    """
    root = tmp_path / "cache"
    sha = "e" * 64
    anchor = _mk(root, f"blob_cache/{sha}", 1 * MB, age_sec=300)  # 最旧的那一件
    code = _mk(root, "code_cache/cccc", 1 * MB, age_sec=200)
    fresh = _mk(root, "blob_cache/dddd", 1 * MB, age_sec=1)
    dropped = dl.prune_caches(root, budget_bytes=1 * MB, keep={sha}, log=lambda _m: None)
    assert anchor.exists(), "锚点被 prune 删了（keep 没生效）"
    assert not code.exists() and not fresh.exists(), "非锚点的旧件应该被丢光"
    assert dropped == 2


def test_prune_says_so_when_the_pinned_floor_exceeds_the_budget(tmp_path: Path) -> None:
    """全剩下无法回收的锚点件 ⇒ **不删**，但要响亮说明配额没兑现在哪。"""
    root = tmp_path / "cache"
    sha = "f" * 64
    anchor = _mk(root, f"blob_cache/{sha}", 2 * MB, age_sec=300)
    logs: list[str] = []
    dropped = dl.prune_caches(root, budget_bytes=1 * MB, keep={sha}, log=logs.append)
    assert dropped == 0 and anchor.exists()
    # 只判「有没有响」而不判措辞（文本断言预算：改标题字不该炸）——「为什么」是给操作员的，
    # 而同族两条已钉住行为面（dropped=0 / 锚点在）。
    assert logs, "配额没兑现（锚点超预算）却没有打任何一行——静默超限 = 没人知道为什么盘一直涨"


def test_anchor_shas_takes_only_content_addressed_values() -> None:
    """`anchor_shas` 只认 64 位十六进制（`"bc"` / 空串是哨兵，不是地址）。"""
    real = "a" * 64
    got = dl.anchor_shas(
        {"ref_sha": real, "opt_sha": "", "init_weights_fp": "bc", "demo_sha": "G" * 64}
    )
    assert got == frozenset({real}), got


def test_reseed_anchors_refills_a_missing_ref_without_any_network(tmp_path: Path) -> None:
    """★ C：每轮开跑前的自愈 —— 缓存里没了就从产物目录/包根补回，**不发任何网络请求**。

    这是 07:33 现场的直译：贮子（blob_cache）被 prune 清掉后，旧行为是 `_resolve_blob` 向
    空 base_url 发 GET ⇒ ProtocolError 打死整段；新行为是这一轮先自己把字节种回去。
    """
    raw = b"ref" * 1024
    sha = hashlib.sha256(raw).hexdigest()
    work = tmp_path / "work"
    (work / "blob_cache").mkdir(parents=True)
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "ref_weights.json").write_bytes(raw)
    manifest = {"ref_sha": sha}
    filled = ph._reseed_anchors(
        manifest=manifest, job_dir=arts, work_dir=work, artifacts_root=arts, log=lambda _m: None
    )
    assert filled == 1
    assert (work / "blob_cache" / sha).read_bytes() == raw
    # 链下游真的能拿到：空 base_url 也命中缓存（= 这一轮不会去打网）
    got, hit, src = dl._resolve_blob(
        blob_root=work / "blob_cache",
        name=dl.BLOB_REF,
        sha=sha,
        inline_b64="",
        jid="j1",
        base_url="",
        token="",
        preloaded=None,
        log=lambda _m: None,
    )
    assert got == raw and hit and src == "cache"


def test_reseed_anchors_does_nothing_when_the_anchor_is_there(tmp_path: Path) -> None:
    """在就不动（每轮真去哈希 0.4MB 是无谓开销；touch 由读路径负责）。"""
    raw = b"ref" * 1024
    sha = hashlib.sha256(raw).hexdigest()
    work = tmp_path / "work"
    cache = work / "blob_cache"
    cache.mkdir(parents=True)
    p = cache / sha
    p.write_bytes(raw)
    before = p.stat().st_mtime
    filled = ph._reseed_anchors(
        manifest={"ref_sha": sha}, job_dir=work, work_dir=work, artifacts_root=None, log=lambda _m: None
    )
    assert filled == 0
    assert p.stat().st_mtime == before, "在缓存里就不该重写（mtime 被改会让 prune 读数失真）"


def test_a_corrupt_cache_entry_is_repaired_not_kept(tmp_path: Path) -> None:
    """键是 sha、内容却不是它 ⇒ 用刚校验过的字节**覆盖**（否则这件坏缓存让人白付一辈子下载）。"""
    root = tmp_path / "blob_cache"
    root.mkdir()
    raw = b"opt" * 64
    sha = "b" * 64
    (root / sha).write_bytes(b"corrupted")
    dl._cache_blob(root, sha, raw, lambda _m: None)
    assert (root / sha).read_bytes() == raw, "坏件要被覆盖（否则每轮都「命中→校验不过→重下」）"


def test_a_code_cache_hit_touches_mtime(tmp_path: Path) -> None:
    """code 命中同理（它是最贵的一份：2.5MB 且每会话都要）。"""
    root = tmp_path / "cache"
    sha = "c" * 64
    cache_dir = root / "code_cache" / sha
    cache_dir.mkdir(parents=True)
    (cache_dir / "mod.py").write_text("x = 1\n")
    os.utime(cache_dir, (time.time() - 10_000, time.time() - 10_000))
    before = cache_dir.stat().st_mtime
    sys_path0 = list(sys.path)
    try:
        dl._ensure_code(
            "http://hub",
            "tok",
            "j1",
            {"code_sha256": sha},
            tmp_path / "job",
            tmp_path / "work",
            code_cache_root=root / "code_cache",
            log=lambda _m: None,
        )
    finally:
        sys.path[:] = sys_path0
    assert cache_dir.stat().st_mtime > before, "code 命中没 touch mtime"


# ───────────────── ③ 换根：只挪缓存 ─────────────────


def _run_one_round_capture(monkeypatch, tmp_path: Path, **kw) -> dict:
    """跑一轮 `worker_loop`（第二轮 `stop=True`），返回 `run_one_round` 收到的关键字。"""
    seen: list[int] = []
    captured: dict = {}

    def _fake_round(base_url, token, job, **kwargs):
        seen.append(1)
        if not captured:
            captured.update(kwargs)
        if len(seen) == 1:
            return round_mod.RoundOutcome(jid=job["job_id"], ok=True, uploaded=True, stop=False)
        return round_mod.RoundOutcome(jid=job["job_id"], ok=False, uploaded=False, stop=True)

    n = {"i": 0}

    def _acquire(*_a, **_k):
        n["i"] += 1
        return {"job_id": f"j{n['i']}", "manifest": {}, "status": "ok", "lease_token": ""}

    monkeypatch.setattr(W, "run_one_round", _fake_round)
    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *_a, **_k: None)
    W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.01,
        log=lambda _m: None,
        **kw,
    )
    return captured


def test_without_a_cache_root_the_call_shape_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    """缺省 ⇒ **逐字旧形状**（单 hub 时 `code_cache_dir=None`，缓存仍住 work_dir）。"""
    got = _run_one_round_capture(monkeypatch, tmp_path)
    assert got["code_cache_dir"] is None, f"缺省不该凭空多出缓存根：{got['code_cache_dir']}"
    assert str(got["part_dir"]).startswith(str(tmp_path)), "job 目录仍在 work_dir 下"


def test_a_cache_root_moves_only_the_caches(monkeypatch, tmp_path: Path) -> None:
    """★ 给了 `cache_dir` ⇒ 只有**内容寻址缓存**换根，`part_dir`（job 目录）仍留在 work_dir。"""
    cache_root = tmp_path / "persist"
    got = _run_one_round_capture(monkeypatch, tmp_path, cache_dir=cache_root)
    assert got["code_cache_dir"] == cache_root / "code_cache", (
        f"缓存根没挪过去：{got['code_cache_dir']}"
    )
    assert str(got["part_dir"]).startswith(str(tmp_path)), (
        f"job 目录也被挪走了（`/tmp` 每轮清场的语义就没了）：{got['part_dir']}"
    )
    assert cache_root.is_dir(), "缓存根要自己建出来（云盘上它通常是空的）"
