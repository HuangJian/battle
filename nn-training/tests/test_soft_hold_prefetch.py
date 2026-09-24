"""test_soft_hold_prefetch.py — 软持有预取（plan/transfer-scheduling §2.6 / P2，2026-09-22）。

预取的价值是「把一个 job 的下载叠到上一个 job 的 PPO 上」（§0：串行把 GPU 饿死在传输上）；
它能不能做对，全看四条纪律有没有被钉住：

  ① **无租约**：候选来自 `peek`（无副作用），下载走 P2 通道（可被打断），worker 死亡 =
     本地暂存蒸发、hub 零残留。本文件里用「`store` 全程不碰 hub」来钉。
  ② **只存引用 + 让路**：payload 落内容寻址 `blob_cache`，暂存只留一份引用与摘要；
     总量有**写死的预算**，超限按 `updated_at` 丢最旧。
  ③ **`prefetch/` 必须在 `prune_job_dirs` 豁免名单里**：`blob_cache` 漏过一次名单，
     现象是「缓存永远未命中」而看起来像协议没生效（2026-09-17 事故）。
  ④ **命中即零下载开算**：`worker_loop` 把暂存副本接进 `run_job(preloaded=…)`——这条接缝
     push 腿已在用，不需要拆 `run_job`。sha 不符（hub 换过 job / 副本陈旧）一律丢弃走关键下载。

另外两条「不许」：预取失败（`BulkPreemptError`/404/瞬时）**绝不**变成
`ProtocolError`/`report_job_failure`；预取路径**只走** `download_payload`（omit 协商落在
那个函数里，另写一条整包 GET 会把 minimize-payload 的瘦身吹回去）。
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.bulk_sched import BULK_P1_CRITICAL, BULK_P2_PREFETCH, BulkPreemptError
from remote.prefetch import (
    PREFETCH_DIR_NAME,
    PrefetchStore,
    pick_candidates,
)

JID = "j" * 16


def _payload(n: int = 1024, seed: bytes = b"a") -> bytes:
    return (seed * n)[:n]


def _summary(payload: bytes, jid: str = JID) -> dict:
    return {
        "job_id": jid,
        "course": "c5-gae",
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


# ───────────────────────────── ① 暂存语义 ─────────────────────────────


def test_store_and_take_are_byte_exact_and_single_use(tmp_path: Path) -> None:
    """存下的字节原样取回；**取走即删**（软持有只用一次，少一份「陈旧副本被误用」的面）。"""
    store = PrefetchStore(tmp_path)
    p = _payload(4096)
    assert store.store(JID, p, _summary(p)) is True
    got = store.take(JID)
    assert got is not None and got["payload_zip"] == p
    assert store.has(JID) is False  # 取过就没了
    assert store.take(JID) is None
    assert store.stats()["hits"] == 1 and store.stats()["misses"] == 1


def test_sha_mismatch_is_dropped_not_stored(tmp_path: Path) -> None:
    """摘要 sha 与真下到的字节不符（hub 换过 job / 副本陈旧）⇒ **不收**，也不报失败。"""
    store = PrefetchStore(tmp_path)
    other = _payload(1024, b"b")
    assert store.store(JID, other, _summary(_payload(1024, b"c"))) is False
    assert store.held() == set()
    assert not (tmp_path / PREFETCH_DIR_NAME / JID).exists()


def test_take_rejects_a_tampered_copy(tmp_path: Path) -> None:
    """暂存副本在盘上被改过：取的时候再校一次 sha，**绝不**把坏字节喂给 run_job。"""
    store = PrefetchStore(tmp_path)
    p = _payload(2048)
    store.store(JID, p, _summary(p))
    (tmp_path / PREFETCH_DIR_NAME / JID / "payload.zip").write_bytes(_payload(2048, b"z"))
    assert store.take(JID) is None
    assert store.stats()["hits"] == 0


def test_payload_lands_in_content_addressed_cache(tmp_path: Path) -> None:
    """字节同时落 `blob_cache/<sha>`（内容寻址）：重领/多候选用同一份空间，不复制第二遍。"""
    store = PrefetchStore(tmp_path)
    p = _payload(512)
    store.store(JID, p, _summary(p))
    sha = hashlib.sha256(p).hexdigest()
    assert (tmp_path / "blob_cache" / sha).read_bytes() == p


def test_drop_removes_held_copy(tmp_path: Path) -> None:
    """`none`/claim 失败/被降级 ⇒ 丢副本（软持有丢弃不是失败，也不回 hub）。"""
    store = PrefetchStore(tmp_path)
    p = _payload(256)
    store.store(JID, p, _summary(p))
    store.drop(JID)
    assert store.has(JID) is False and store.held() == set()


# ───────────────────────────── ② 预算与候选 ─────────────────────────────


def test_budget_drops_oldest_first(tmp_path: Path) -> None:
    """超预算按 `updated_at` 丢**最旧**的候选（§2.6：预算硬约束，不许默默超）。"""
    clock = [0.0]
    store = PrefetchStore(tmp_path, budget_bytes=3000, clock=lambda: clock[0])
    ids = [f"{i:04d}" + "x" * 12 for i in range(3)]
    for i, jid in enumerate(ids):
        clock[0] = float(i)
        p = _payload(2000, bytes([65 + i]))
        assert store.store(jid, p, _summary(p, jid))
    assert store.held() == {ids[2]}  # 只留得下最新的那一份（每份 2000 > 预算的一半）
    assert store.stats()["bytes"] <= 3000


def test_budget_never_drops_the_last_copy(tmp_path: Path) -> None:
    """单份就超预算时也要留下它（否则「预算比一份还小」会让预取永远空转）。"""
    store = PrefetchStore(tmp_path, budget_bytes=10)
    p = _payload(1024)
    assert store.store(JID, p, _summary(p))
    assert store.held() == {JID}


def test_pick_candidates_skips_held_and_skip_sets_and_caps_depth() -> None:
    """候选挑选（纯函数）：跳过已持有/在跑/刚失败；深度封顶；摘要不全的不预取。"""
    cands = [
        {"job_id": "a" * 16, "payload_bytes": 10, "payload_sha256": "s" * 64},
        {"job_id": "b" * 16, "payload_bytes": 10, "payload_sha256": "s" * 64},
        {"job_id": "c" * 16, "payload_bytes": 0, "payload_sha256": "s" * 64},  # 摘要不全
        {"job_id": "d" * 16, "payload_bytes": 10},  # 缺 sha
        {"job_id": "e" * 16, "payload_bytes": 10, "payload_sha256": "s" * 64},
    ]
    got = pick_candidates(cands, held={"a" * 16}, skip={"e" * 16}, depth=3)
    assert [c["job_id"] for c in got] == ["b" * 16]


def test_prune_job_dirs_keeps_the_prefetch_dir(tmp_path: Path) -> None:
    """`prefetch/` 必须在 prune 豁免名单里——否则每轮被当旧 job 删掉，预取永远不命中。"""
    store = PrefetchStore(tmp_path)
    p = _payload(128)
    store.store(JID, p, _summary(p))
    for i in range(5):  # 造 5 个「旧 job 目录」，逼 prune 动手
        d = tmp_path / (f"{i:016d}")
        d.mkdir()
        d.joinpath("shard").write_bytes(b"x")
    removed = W.prune_job_dirs(tmp_path, keep=2, log=lambda _m: None)
    assert removed >= 1, "prune 没动手，用例没测到东西"
    assert store.has(JID), "prefetch 目录被 prune 删掉了（豁免名单漏了）"


# ──────────────── ③ 填充器：P2 通道 + 失败不算失败 + 只走一条路 ────────────────


class _FakeStore(PrefetchStore):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path, budget_bytes=1 << 20)

    def store(self, jid: str, payload: bytes, summary: dict) -> bool:
        # 填充器用例不关心磁盘，只关心「谁被下载、以什么优先级、结果有没有被吞」
        self._items[jid] = {"sha": "x", "bytes": len(payload), "at": 0.0, "summary": summary}
        return True


def _peek_of(*jids: str) -> tuple[list[dict], bool]:
    out = []
    for jid in jids:
        p = _payload(64)
        out.append(_summary(p, jid))
    return out, False


def test_filler_uses_p2_and_only_download_payload(tmp_path: Path, monkeypatch) -> None:
    """填充器：候选来自 peek；下载走 **P2**；只经 `download_payload` 这一条路（继承 omit）。"""
    store = _FakeStore(tmp_path)
    calls: list[tuple[str, str, str]] = []
    seen: list[str] = []

    def _peek(*a, **k):
        seen.append("peek")
        return _peek_of("a" * 16, "b" * 16)

    monkeypatch.setattr(W, "peek_jobs", _peek)
    monkeypatch.setattr(W, "PREFETCH_ROUND_SEC", 0.05)

    def _dl(base_url, token, jid, *, bulk_prio=BULK_P1_CRITICAL, wire_jid="", log=None, **kw):
        calls.append((jid, bulk_prio, wire_jid))
        p = _payload(64)
        return p

    monkeypatch.setattr(W, "download_payload", _dl)
    stop = threading.Event()

    def _run() -> None:
        W._prefetch_fill("http://hub", "tok", store, stop, depth=2, log=lambda _m: None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while len(calls) < 2 and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「预取真发了 2 次」这个状态，5s 只当挂起兜底）
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert [c[0] for c in calls] == ["a" * 16, "b" * 16]
    assert {c[1] for c in calls} == {BULK_P2_PREFETCH}, "预取没有走 P2 通道（会拖慢在跑的 job）"
    assert {c[2] for c in calls} == {"prefetch"}, "预取账记进了在跑 job（阶段占比会失真）"
    assert seen, "填充器没有先 peek（软持有必须来自无副作用的候选查询）"
    assert store.held() == {"a" * 16, "b" * 16}


def test_filler_swallows_preemption_and_errors(tmp_path: Path, monkeypatch) -> None:
    """被挤走/瞬时错误：记一行、丢掉、**不经** ProtocolError（预取失败不是失败）。"""
    store = _FakeStore(tmp_path)
    boom = {"n": 0}

    monkeypatch.setattr(W, "peek_jobs", lambda *a, **k: _peek_of("a" * 16))

    def _dl(*a, **k):
        boom["n"] += 1
        if boom["n"] == 1:
            raise BulkPreemptError("被高优传输挤走")
        if boom["n"] == 2:
            raise W.RetryableError("瞬时失败")
        if boom["n"] == 3:
            raise W.ProtocolError("404 之类的确定性拒绝")
        return _payload(64)

    monkeypatch.setattr(W, "download_payload", _dl)
    monkeypatch.setattr(W, "PREFETCH_ROUND_SEC", 0.05)  # 别真等 5s 一轮
    logs: list[str] = []
    stop = threading.Event()

    def _run() -> None:
        W._prefetch_fill("http://hub", "tok", store, stop, depth=1, log=logs.append)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while boom["n"] < 4 and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「填充器重试到第 4 次」这个状态，5s 只当挂起兜底）
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert boom["n"] >= 4, "填充器在前三次失败后就死了（预取应该下一轮再来）"
    assert any("挤走" in m for m in logs)
    assert any("不算失败" in m for m in logs)
    assert store.held() == {"a" * 16}  # 第四次成功入库


# ──────────────── ④ 命中即零下载开算（worker_loop 的接缝）───────────────


def test_worker_loop_uses_prefetched_payload_without_downloading(tmp_path: Path, monkeypatch) -> None:
    """命中路径：预取的 payload 直接接进 `run_job(preloaded=…)`——**零下载**开算。

    这是 P2 的验收形状（`PPO_B ‖ upload_A` 的 B 那一半）：第二个 job 的 payload 在
    第一个 job 跑的时候就已经躺在暂存区了，开算时不应再发一次 payload GET。
    """
    j1, j2 = "1" * 16, "2" * 16
    payloads = {j1: _payload(200, b"1"), j2: _payload(200, b"2")}
    manifests = {
        jid: {
            "job_id": jid,
            "payload_sha256": hashlib.sha256(p).hexdigest(),
            "course_fp": "f" * 64,
        }
        for jid, p in payloads.items()
    }
    downloads: list[str] = []
    preloaded_seen: list[str] = []
    calls = {"n": 0}

    def _acquire(base_url, token, **kw):
        calls["n"] += 1
        if calls["n"] > 2:  # 两个 job 都跑完：让循环靠 max_idle_sec 自己收尾
            return None
        jid = j1 if calls["n"] == 1 else j2
        return {"job_id": jid, "manifest": manifests[jid], "status": "ok", "lease_token": ""}

    def _run_job(base_url, token, job, **kw):
        jid = job["job_id"]
        pl = (kw.get("preloaded") or {}).get("payload_zip")
        if pl is not None:
            preloaded_seen.append(jid)
            assert pl == payloads[jid], "preloaded 不是这份 job 的 payload"
        else:
            downloads.append(jid)
        if jid == j1:  # 第一个 job 跑的时候，把第二个 job 预取进来（模拟后台填充器）
            store.store(j2, payloads[j2], _summary(payloads[j2], j2))
        return {"job_id": jid}

    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "run_job", _run_job)
    monkeypatch.setattr(W, "post_result", lambda *a, **k: 200)
    monkeypatch.setattr(W, "peek_jobs", lambda *a, **k: ([], False))
    monkeypatch.setattr(W, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)

    store = PrefetchStore(tmp_path)
    real_init = PrefetchStore.__init__

    def _init(self, work_dir, **kw):
        real_init(self, work_dir, **kw)
        self._items = store._items  # 让 worker_loop 造的那个 store 用我们的账本
        self.root = store.root

    monkeypatch.setattr(PrefetchStore, "__init__", _init)

    logs: list[str] = []
    # `once=True` 只跑一个 job 就退出（它是「单发」语义），所以用 max_idle_sec 收尾：
    # 两个 job 跑完后 acquire 返回 None，空闲 0.3s 即退出。
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.05,
        max_idle_sec=0.3,
        log=logs.append,
    )
    assert n == 2, f"应当跑完两个 job：n={n} {logs}"
    assert preloaded_seen == [j2], f"第二个 job 没有走零下载开算（预取命中面没接上）：{logs}"
    assert downloads == [j1], f"第一个 job 应当正常下载；实测下载了 {downloads}：{logs}"
