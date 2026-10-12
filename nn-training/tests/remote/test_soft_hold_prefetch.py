"""test_soft_hold_prefetch.py — 软持有预取（plan/transfer-scheduling §2.6 / P2，2026-09-22）。

预取的价值是「把一个 job 的下载叠到上一个 job 的 PPO 上」（§0：串行把 GPU 饿死在传输上）；
它能不能做对，全看四条纪律有没有被钉住：

  ① **无租约**：候选来自 `peek`（无副作用），下载走 P2 通道（可被打断），worker 死亡 =
     本地暂存蒸发、hub 零残留。本文件里用「`store` 全程不碰 hub」来钉。
  ② **暂存 + 让路**：payload 的**唯一**副本就在 `prefetch/<jid>/`（`payload.zip`，或解好的
     `shards/`）；两本账**量纲不同**：zip 按压缩字节（写死的 64MB，超限按 `updated_at` 丢最旧、
     仅剩一份也保留），staged 只按**条数**（`staged_max` = 每轮解包份数；2026-10-12 撤销
     256MiB 字节闸，理由见 `remote/prefetch.py::PREFETCH_UNPACK_DEFAULT` 上的长注）。
     ⚠ 曾经还往 `blob_cache/<payload_sha>` 写一份“内容寻址副本”——那份**全仓无读点**，
     2026-10-12 已删（plan/prefetch-stage-unpack §3）。
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
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.download as DL
import remote.http as http_mod
import remote.job_lifecycle as JL
import remote.job_round as JR
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


def _packed_payload(root: Path, *, shards: int = 2, tag: str = "a") -> bytes:
    """造一份**真** payload（tar.xz）：`shards` 个带 `manifest.json` 的 shard 目录 + 根级 extra。

    解包路径（`store(unpack=True)`）要真归档——随机字节只能走到“解包失败回退”那一条。
    `tag` 保证两次调用的字节不同（tar 的 mtime 只有秒级精度）。
    """
    from common.payload import pack_payload

    src = root / "src_payload"
    dirs: list[Path] = []
    for i in range(shards):
        d = src / f"w{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"course_fp": "f" * 64, "tag": tag}), encoding="utf-8")
        (d / "ep.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
        dirs.append(d)
    init = src / "init_weights.json"
    init.write_text(json.dumps({"tag": tag}), encoding="utf-8")
    out = src / "packed.tar.xz"
    pack_payload([str(d) for d in dirs], {"it": 1}, out, extra_files=[init])
    return out.read_bytes()


def _ppo_manifest(jid: str = JID) -> dict:
    """最小合法 manifest（与 `tests/remote/test_worker_bun_precheck.py::_manifest` 同形状）。"""
    from common.protocol import normalize_manifest

    return normalize_manifest(
        {
            "proto": 1,
            "kind": "ppo",
            "runId": "run-reuse",
            "it": 1,
            "job_id": jid,
            "commit": "c" * 40,
            "code_sha256": "z" * 64,
            "course": "// course jsonc\n{}",
            "course_fp": "f" * 64,
            "reward_formula": "score",
            "formula_hash": "h" * 40,
            "metrics_version": 1,
            "gamma": 0.995,
            "lam": 0.95,
            "mode": "per-tick",
            "seed": "s" * 64,
            "epochs": 1,
            "mb": 8,
            "lr": 3e-4,
            "init_weights_fp": "w" * 64,
            "data_fp": "d" * 64,
            "payload_sha256": "p" * 64,
        }
    )


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


def test_payload_is_staged_once_and_never_copied_into_blob_cache(tmp_path: Path) -> None:
    """暂存区那一份就是唯一那份：**不写** `blob_cache/<payload_sha>`（plan/prefetch-stage-unpack §3）。

    内容寻址那一层只服务 `opt/init/ref/demo`（`_resolve_blob` 只查那四个名字的 sha），payload 的
    sha **从不被查** ⇒ 旧行为每 job 往最慢的盘上白写 2.5–2.7MB（首包 6.8MB）且永远没人读。
    """
    store = PrefetchStore(tmp_path)
    p = _payload(512)
    assert store.store(JID, p, _summary(p)) is True
    sha = hashlib.sha256(p).hexdigest()
    assert (tmp_path / PREFETCH_DIR_NAME / JID / "payload.zip").read_bytes() == p, (
        "暂存副本必须逐字节完整（命中路径零网络就靠它）"
    )
    assert not (tmp_path / "blob_cache" / sha).exists(), "payload 又被写进 blob_cache（纯写税，没人读）"


# ───────── ①b staged：预取线程解包（plan/prefetch-stage-unpack §2.3/§2.6） ─────────


def test_staged_store_keeps_only_the_unpacked_copy(tmp_path: Path) -> None:
    """`store(unpack=True)` 后：`shards/` 在、`payload.zip` **不在**、meta 记 staged 三件读数。"""
    store = PrefetchStore(tmp_path)
    p = _packed_payload(tmp_path / "p1")
    assert store.store(JID, p, _summary(p), unpack=True) is True
    d = tmp_path / PREFETCH_DIR_NAME / JID
    assert (d / "shards").is_dir(), "解包产物没落盘"
    assert not (d / "payload.zip").exists(), "解包成功后不该再留一份压缩副本"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["staged"] is True and meta["staged_bytes"] > 0
    assert meta["unpack_sec"] >= 0.0
    st = store.stats()
    assert st["staged"] == 1 and st["staged_bytes"] == meta["staged_bytes"]
    assert store.has(JID) is True


def test_take_staged_hands_over_the_path_without_touching_the_job_dir(tmp_path: Path) -> None:
    """`take()` 只交路径 + 记账：**不碰 job 目录**（评审 P0-3），`release_handoff` 才收尾。"""
    store = PrefetchStore(tmp_path)
    p = _packed_payload(tmp_path / "p1")
    store.store(JID, p, _summary(p), unpack=True)
    job_dir = tmp_path / JID  # 同 work_dir 下的 job 目录（生产里就是它）
    job_dir.mkdir(parents=True)
    (job_dir / "_result.json").write_text("{}", encoding="utf-8")

    got = store.take(JID)
    assert got is not None and set(got) >= {"staged_dir", "payload_nbytes", "unpack_sec", "blob_sha"}
    assert got.get("payload_zip") is None, f"staged 形状不该带字节：{sorted(got)}"
    assert got["payload_nbytes"] == len(p)
    assert got["blob_sha"] == hashlib.sha256(p).hexdigest()
    assert Path(got["staged_dir"]).is_dir(), "staged 目录得留着（搬入由 _ensure_payload 做）"
    assert (job_dir / "_result.json").exists(), "take() 碰了 job 目录（复用缓存会被吃掉）"
    assert store.has(JID) is False and store.stats()["hits"] == 1
    assert store.stats()["handoff"] == 1, "交棒的暂存没记账（孤儿就没人收了）"

    store.release_handoff()
    assert not (tmp_path / PREFETCH_DIR_NAME / JID).exists(), "未搬走的暂存没被收尾"
    assert store.stats()["handoff"] == 0


def test_a_successful_adoption_leaves_no_residual_shell(tmp_path: Path) -> None:
    """搬入**成功**（`shards/` 被 rename 走）⇒ `prefetch/<jid>/` 的余壳也要收掉（评审 P2-3）。

    余壳只剩 `meta.json`：`take()` 已把它从 `_items` 摘了、`release_handoff` 又摘了 `_handoff`
    ⇒ 不清就按命中次数无界堆积，而预算账（只看 `_items`）看不见它。
    """
    store = PrefetchStore(tmp_path)
    p = _packed_payload(tmp_path / "p1")
    store.store(JID, p, _summary(p), unpack=True)
    got = store.take(JID)
    assert got is not None
    dst = tmp_path / JID
    Path(got["staged_dir"]).rename(dst)  # = `_ensure_payload` 的搬入（同 fs rename）

    store.release_handoff()
    assert not (tmp_path / PREFETCH_DIR_NAME / JID).exists(), "搬入成功后余壳没被清（会按命中次数堆积）"
    assert any(dst.iterdir()), "收尾把搬进 job 目录的那份连坐了"
    assert store.stats()["handoff"] == 0


def test_a_stage_that_fails_the_checksum_is_dropped_with_its_note(tmp_path: Path) -> None:
    """sha 不符 ⇒ 丢弃 + **连交棒的暂存一起清**（否则它以孤儿形式留在盘上）。"""
    store = PrefetchStore(tmp_path)
    p = _packed_payload(tmp_path / "p1")
    store.store(JID, p, _summary(p), unpack=True)
    store._items[JID]["sha"] = "0" * 64  # 模拟 meta 里的 sha 与摘要不符
    assert store.take(JID) is None
    assert store.stats()["hits"] == 0 and store.stats()["handoff"] == 0
    assert not (tmp_path / PREFETCH_DIR_NAME / JID).exists()


def test_prune_keeps_a_handed_off_stage_but_sweeps_the_orphans(tmp_path: Path) -> None:
    """`prune()`：交棒/在册的不动；`.tmp` 崩溃残留与不在册的残壳要清（跨会话遗留只有这里收）。"""
    store = PrefetchStore(tmp_path)
    p = _packed_payload(tmp_path / "p1")
    store.store(JID, p, _summary(p), unpack=True)
    assert store.take(JID) is not None  # 交棒（搬入还没发生）
    root = tmp_path / PREFETCH_DIR_NAME
    (root / JID / "shards.tmp").mkdir(parents=True, exist_ok=True)  # 崩溃残留
    (root / "deadbeefdeadbeef" / "shards").mkdir(parents=True, exist_ok=True)  # 上一会话的残壳
    assert store.prune() >= 2
    assert not (root / JID / "shards.tmp").exists(), "崩溃残留的 .tmp 没清"
    assert not (root / "deadbeefdeadbeef").exists(), "不在册的残壳没清"
    assert Path(store._handoff[JID]).is_dir(), "交棒的那份被 prune 扫掉了（搬入会失败）"


def test_unpack_failure_falls_back_to_bytes_without_raising(tmp_path: Path) -> None:
    """解包失败（坏字节）⇒ 保留 zip、照旧入库、不抛（预取失败不是失败）。"""
    store = PrefetchStore(tmp_path)
    logs: list[str] = []
    store._log = logs.append
    p = _payload(4096, b"z")
    assert store.store(JID, p, _summary(p), unpack=True) is True
    d = tmp_path / PREFETCH_DIR_NAME / JID
    assert (d / "payload.zip").read_bytes() == p and not (d / "shards").exists()
    assert any("解包失败" in m for m in logs), logs
    got = store.take(JID)
    assert got is not None and got["payload_zip"] == p, "退回字节形状后照旧要能命中"


def test_a_stage_is_never_vetoed_by_size_and_only_accounted(tmp_path: Path) -> None:
    """★ 2026-10-12：解包产物体积**不设闸**（撤销 256MiB 单份上界）——只记账。

    旧实现在这里是一条「解包后超 staged 预算 ⇒ 退回 zip」的分支（`staged_budget_bytes=1`
    就能触发）。现场读数把那条分支证伪了：现行课程 payload 3MB → 解包 **300–330MB**
    （实测膨胀 ~110×）而闸是 256MiB ⇒ **每份都超**、staged 路径 100% 回退 = 该档位等于没开
    （真机日志 `解包 325.7MB / 299.6MB 超 staged 预算 256MB`）。

    撤销后的判据只有两条：形状照旧是 staged（且**与 zip 那本账无关**——所以这里故意把 zip
    预算给到 1 字节），体积落 `meta.json` / `stats()` 当读数。
    """
    store = PrefetchStore(tmp_path, budget_bytes=1)
    p = _packed_payload(tmp_path / "p1")
    assert store.store(JID, p, _summary(p), unpack=True) is True
    d = tmp_path / PREFETCH_DIR_NAME / JID
    assert (d / "shards").is_dir() and not (d / "payload.zip").exists()
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["staged"] is True and meta["staged_bytes"] > 0, "体积没记账"
    assert store.held() == {JID}, "staged 条目被 zip 账误杀（两本账必须分开）"
    st = store.stats()
    assert st["staged"] == 1 and st["staged_bytes"] == meta["staged_bytes"]
    got = store.take(JID)
    assert got is not None and "staged_dir" in got, "撤销体积闸后 staged 形状没交付"


def test_staged_overage_drops_only_the_oldest_stage(tmp_path: Path) -> None:
    """staged 超**条数**（`staged_max`）⇒ 只丢最旧的 staged，**不拿 zip 候选偿债**。

    两者量纲差 ~100×（3MB 压缩 vs 300MB 解包）：混在一处按「全池最旧」丢，一条 staged 超量
    会把健康的 zip 候选逐个吃掉仍不达标（旧实现靠那条已撤销的单份上界才「永不可达」）。
    """
    jid_z, jid_a, jid_b = "z" * 16, "a" * 16, "b" * 16
    pz = _packed_payload(tmp_path / "pz", tag="z")
    pa = _packed_payload(tmp_path / "pa", tag="a")
    pb = _packed_payload(tmp_path / "pb", tag="b")
    clock = [0.0]
    s = PrefetchStore(tmp_path / "w", staged_max=1, clock=lambda: clock[0])
    assert s.store(jid_z, pz, _summary(pz, jid_z)) is True  # zip 形状，且是池子里最旧的一份
    clock[0] = 1.0
    assert s.store(jid_a, pa, _summary(pa, jid_a), unpack=True) is True
    clock[0] = 2.0
    assert s.store(jid_b, pb, _summary(pb, jid_b), unpack=True) is True
    assert s.held() == {jid_z, jid_b}, "超条数丢错了对象（zip 候选被偿债 / staged 没丢最旧）"
    # zip 账口径逐字不变：单份超预算 ⇒ 既有「仅剩一份也保留」照旧
    s2 = PrefetchStore(tmp_path / "w2", budget_bytes=1)
    assert s2.store(jid_a, pa, _summary(pa, jid_a)) is True
    assert s2.held() == {jid_a}


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


def test_the_filler_unpacks_only_the_first_n_candidates(tmp_path: Path, monkeypatch) -> None:
    """`unpack_n`（`--prefetch-unpack`）= peek 顺序里的**前 N 份**（plan §2.3 的风险闸）。

    N=1：head 解包就绪，其余仍是字节形状；N=0：全字节（= 改造前的旧行为）。
    真下真解（真 store + 真 tar.xz），不走假 store。
    """
    ja, jb, jc = "a" * 16, "b" * 16, "c" * 16
    payloads = {ja: _packed_payload(tmp_path / "pa", tag="a"),
                jb: _packed_payload(tmp_path / "pb", tag="b"),
                jc: _packed_payload(tmp_path / "pc", tag="c")}
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.01)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([_summary(payloads[j], j) for j in (ja, jb, jc)], False))
    monkeypatch.setattr(JR, "download_payload", lambda _b, _t, jid, **k: payloads[jid])
    store = PrefetchStore(tmp_path / "w")
    stop = threading.Event()
    t = threading.Thread(
        target=JR._prefetch_fill,
        args=("http://hub", "tok", store, stop),
        kwargs={"depth": 3, "unpack_n": 1, "log": lambda _m: None},
        daemon=True,
    )
    t.start()
    deadline = time.time() + 10
    while store.held() != {ja, jb, jc} and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「三份都下好了」这个状态，10s 只当挂起兜底）
        time.sleep(0.01)
    stop.set()
    t.join(10)
    assert store.held() == {ja, jb, jc}, "填充器没把三份都下完"
    root = store.root
    assert (root / ja / "shards").is_dir(), "head 没解包（N=1 的第一份该解）"
    assert not (root / ja / "payload.zip").exists()
    for jid in (jb, jc):
        assert (root / jid / "payload.zip").exists() and not (root / jid / "shards").exists(), (
            f"第 2/3 份不该进 stage（N=1 的上界）：{jid}"
        )
    assert store.stats()["staged"] == 1


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
        #: 填充器**要求**解包的那几份（只记意图：真解包由真 store 的用例钉）
        self.unpack_seen: dict[str, bool] = {}

    def store(self, jid: str, payload: bytes, summary: dict, *, unpack: bool = False) -> bool:
        # 填充器用例不关心磁盘，只关心「谁被下载、以什么优先级、要不要解包、结果有没有被吞」
        self.unpack_seen[jid] = unpack
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

    monkeypatch.setattr(JR, "peek_jobs", _peek)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.05)
    # 第三條 HTTP 缝（预取状态上报）：不打桩就首轮白等 ~1.4s（见 tests/helpers/hub_seams.py）。
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)

    def _dl(base_url, token, jid, *, bulk_prio=BULK_P1_CRITICAL, wire_jid="", log=None, **kw):
        calls.append((jid, bulk_prio, wire_jid))
        p = _payload(64)
        return p

    monkeypatch.setattr(JR, "download_payload", _dl)
    stop = threading.Event()

    def _run() -> None:
        JR._prefetch_fill("http://hub", "tok", store, stop, depth=2, log=lambda _m: None)

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

    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: _peek_of("a" * 16))

    def _dl(*a, **k):
        boom["n"] += 1
        if boom["n"] == 1:
            raise BulkPreemptError("被高优传输挤走")
        if boom["n"] == 2:
            raise W.RetryableError("瞬时失败")
        if boom["n"] == 3:
            raise W.ProtocolError("404 之类的确定性拒绝")
        return _payload(64)

    monkeypatch.setattr(JR, "download_payload", _dl)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.05)  # 别真等 5s 一轮
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)  # 第三条 HTTP 缝
    logs: list[str] = []
    stop = threading.Event()

    def _run() -> None:
        JR._prefetch_fill("http://hub", "tok", store, stop, depth=1, log=logs.append)

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
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([], False))
    monkeypatch.setattr(JR, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)
    # 阶段通知 `job_ready`（`POST /jobs/{id}/ready`）**也要打桩**：不打就会对着
    # `base_url="http://hub"` 发真 HTTP，靠 `except Exception: pass` 吞掉连接失败。
    # 那条路径本机实测每次 1.4s（不可达地址 + 重试），两个 job = 2.9s 纯空等
    # （本用例总耗时几乎全是它）——§14 的「测试空等生产超时」同型。
    # 2026-10-10：第三条同型的缝 = 预取状态上报（`report_prefetch`，每个填充线程**首轮
    # 无条件发一笔**）——同上打桩（见 tests/helpers/hub_seams.py 的模块头）。
    monkeypatch.setattr(JR, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)

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


# ───────────────── ⑤ 现场回归：取消环在跑时预取仍能传完（A1，2026-09-25） ─────────────────


class _FakeResp:
    """假响应：逐块吐数据（`_read_body` 的入参形状）。"""

    def __init__(self, chunks: list[bytes], total: int) -> None:
        self._chunks = list(chunks)
        self.headers = {"Content-Length": str(total)}

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def test_prefetch_survives_the_cancel_watcher_control_ring(tmp_path: Path, monkeypatch) -> None:
    """**现场回归（A1）**：取消环每 ~1.5s 打一个控制面小包时，预取仍能**传完并入库**。

    2026-09-24 现场（x20-dodge-l1/l3 双课程单云 worker）：预取**零命中**，每个 job 都刷一屏
    `定期预取被高优 bulk 挤走 → 重试次数用完，放弃这份提前量`。根因 = `control()` 无条件写
    `_preempt_at`，而取消环 `start_cancel_watcher` 每 1.5s 一个 `/jobs/{id}/status`
    （`control_path()` 判为控制面）⇒ 每 1.5s 打断一次 P2 ⇒ 多 MB 的 payload 数学上永远传不完。

    本用例走**真路**（`_prefetch_fill → download_payload → _get_with_retry → slot(P2) →
    _read_body(pace)`）；只把 HTTP 换成假响应，控制面窗口等价于取消环那一个小包。
    修复前：`pace` 第一下就被挤走 ⇒ `preempted > 0`、这份预取永远入库不了（用例红）。
    """
    payload = _payload(4096)
    store = PrefetchStore(tmp_path)
    W._BULK.reset()
    # patch 面（S4 第十刀拆分）：`_prefetch_fill` 与它的依赖（`peek_jobs` /
    # `PREFETCH_ROUND_SEC`）的 global 解析都在 `remote.job_round`；HTTP 核心在 `remote.http`。
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([_summary(payload, JID)], False))
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.05)
    # 第三条 HTTP 缝：本用例只把 `remote.http._request` 换成假响应，而 `job_lifecycle`
    # 是 `from remote.http import _request`（自己持一份引用）⇒ 上报腿仍会真发 HTTP（1.4s）。
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)
    # 控制面窗口在本用例里一直开着 ⇒ 把让路预算压小，否则每次 `pace` 要停满 5s。
    monkeypatch.setattr(W._BULK, "_yield_budget", 0.01)
    monkeypatch.setattr(W._BULK, "_yield_step", 0.005)

    def _fake_request(base_url: str, token: str, path: str, *, pace=None, **kw):
        """真 `_request` 的替身：只换 HTTP；控制面标记 + `_read_body(pace=…)` 全是真的。"""
        with W._BULK.control(label="/jobs/x/status"):  # ★ 取消环那个小包的等价物
            chunks = [payload[i : i + 512] for i in range(0, len(payload), 512)]
            return 200, W._read_body(
                _FakeResp(chunks, len(payload)),
                idle_timeout=45.0,
                total_timeout=300.0,
                allow_reroll=bool(kw.get("allow_reroll", False)),
                pace=pace,
            )

    monkeypatch.setattr(http_mod, "_request", _fake_request)
    logs: list[str] = []
    stop = threading.Event()
    t = threading.Thread(
        target=lambda: JR._prefetch_fill("http://hub", "tok", store, stop, depth=1, log=logs.append),
        daemon=True,
    )
    t.start()
    deadline = time.time() + 10
    while not store.has(JID) and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「预取入库」这个状态，10s 只当挂起兜底）
        time.sleep(0.01)
    stop.set()
    t.join(10)

    assert store.has(JID), f"预取没传完（控制面把 P2 挤走了？）：{logs}"
    assert store.take(JID) is not None, "入库的那份取不回来（sha 对不上？）"
    assert store.stats()["hits"] == 1
    st = W._BULK.stats()
    assert st["preempted"] == 0, f"控制面仍在抢占 P2：{st}"
    assert st["inflight_bulk"] == 0
    assert not any("preempt=" in ln for ln in logs), f"wire 行里出现了抢占作废：{logs}"


# ────────────── ⑥ 传输/观测补面（2026-10-02，plan/transfer-residual W1/W2） ──────────────


def test_worker_loop_restores_the_library_yield_defaults(tmp_path: Path, monkeypatch) -> None:
    """W1/§3.5 ★：`worker_loop` 退出必须还原让路策略（`_BULK` 是进程单例）。

    不还原的后果是**跨用例泄漏**：同进程后续用例（`test_control_plane_bypass.py` 等）
    继承 `auto`，它依赖的「无条件让路」断言会确定性变红。
    """
    monkeypatch.setattr(W, "acquire_job", lambda *a, **k: None)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)
    before = W._BULK.configure_yield(after_sec=0.5, total_budget_sec=1.0)
    try:
        n = W.worker_loop(
            "http://hub",
            "tok",
            work_dir=tmp_path,
            poll_sec=0.0,
            once=True,
            bulk_yield="never",
            log=lambda _m: None,
        )
        assert n == 0
        # `configure_yield` 返回**旧值** ⇒ 拿它当读数：loop 后应当仍是进 loop 前的策略。
        current = W._BULK.configure_yield(after_sec=0.5, total_budget_sec=1.0)
        assert current == (0.5, 1.0), f"worker_loop 没有还原进 loop 前的策略：{current}"
    finally:
        W._BULK.configure_yield(after_sec=before[0], total_budget_sec=before[1])


# 已退役（2026-10-06，用户指令「删除云机 worker 刷屏 log」）：原
# `test_prefetch_round_summary_is_logged`（S3f/G6 每轮摘要行）与
# `test_prefetch_stats_have_a_production_reader`（那条行的「不许变死代码」守卫）一并删除——
# 摘要行按轮打，而预取常态是 0 命中 / 0 下载（现场 `held=0 hits=0 misses=61 本轮下载=0.00MB`）
# ⇒ 每轮一行零信息增量。读数没丢：命中账由 **S3e** 的 `payload=prefetch-hit` 落在该 job 的
# wire 行（`test_prefetch_hit_is_visible_in_the_wire_line` 在），字节账由预取的 wire 传输行覆盖。


def test_prefetch_hit_is_visible_in_the_wire_line(tmp_path: Path, monkeypatch) -> None:
    """S3e/G6：命中的 payload 要在 wire 行里可见（`payload=prefetch-hit`），下载轮不得误标。

    打点在 `run_one_round` 的 take 命中处（**不是** `_ensure_payload` 的 preloaded 分支——
    那条分支同样服务 push 腿，会把 push 标成预取命中）。
    """
    j1, j2 = "1" * 16, "2" * 16
    payloads = {j1: _payload(128, b"1"), j2: _payload(128, b"2")}
    manifests = {
        jid: {
            "job_id": jid,
            "payload_sha256": hashlib.sha256(p).hexdigest(),
            "course_fp": "f" * 64,
        }
        for jid, p in payloads.items()
    }
    calls = {"n": 0}

    def _acquire(base_url, token, **kw):
        calls["n"] += 1
        if calls["n"] > 2:  # 两个 job 都跑完：让循环靠 max_idle_sec 自己收尾
            return None
        jid = j1 if calls["n"] == 1 else j2
        return {"job_id": jid, "manifest": manifests[jid], "status": "ok", "lease_token": ""}

    def _run_job(base_url, token, job, **kw):
        if job["job_id"] == j1:  # 第一个 job 跑的时候，第二个 job 已被预取进来
            store.store(j2, payloads[j2], _summary(payloads[j2], j2))
        return {"job_id": job["job_id"]}

    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "run_job", _run_job)
    monkeypatch.setattr(W, "post_result", lambda *a, **k: 200)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([], False))
    monkeypatch.setattr(JR, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(JR, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)  # 第三条 HTTP 缝
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)
    store = PrefetchStore(tmp_path)
    real_init = PrefetchStore.__init__

    def _init(self, work_dir, **kw):
        real_init(self, work_dir, **kw)
        self._items = store._items  # 让 worker_loop 造的那个 store 用我们的账本
        self.root = store.root

    monkeypatch.setattr(PrefetchStore, "__init__", _init)
    logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.05,
        max_idle_sec=0.3,
        log=logs.append,
    )
    assert n == 2, f"应当跑完两个 job：n={n} {logs}"
    hit_lines = [ln for ln in logs if j2 in ln and "payload=prefetch-hit" in ln]
    assert hit_lines, f"命中轮没有 payload=prefetch-hit：{logs}"
    assert not any(j1 in ln and "payload=prefetch-hit" in ln for ln in logs), (
        "j1 是下载轮，不该有命中标记"
    )


# ──────────── ⑤ 预取状态上报（plan/dashboard-ppo-live-rows）────────────


def test_snapshot_tracks_the_download_window_and_clears_on_store(tmp_path: Path) -> None:
    """`begin`/`end`/`snapshot`：观测面要知道「这一轮正在下哪一份」，且形状是**规范化**的。"""
    store = PrefetchStore(tmp_path)
    assert store.snapshot() == {"held": [], "dl": []}

    b, a = "b" * 16, "a" * 16
    store.begin(b, _summary(_payload(16), b))
    store.begin(a, _summary(_payload(16), a))
    assert store.downloading() == {a, b}
    assert store.snapshot()["dl"] == [a, b], "快照没排序（形状会随顺序抖动）"

    p = _payload(64)
    assert store.store(a, p, _summary(p, a)) is True
    assert store.snapshot() == {"held": [a], "dl": [b]}, "入库即从「正在下载」摘掉"

    store.end(b)
    store.end(b)  # 幂等：失败路径与成功路径都会调
    store.clear()
    assert store.snapshot() == {"held": [], "dl": []}


def test_filler_reports_the_shape_once_and_never_leaves_a_failed_download_hanging(
    tmp_path: Path, monkeypatch
) -> None:
    """填充器每轮末上报一次，**形状没变就不发**；失败的那份**不许**留在「下载中」。"""
    store = _FakeStore(tmp_path)
    sent: list[dict] = []

    def _report(base_url, token, snap, **kw):
        sent.append(dict(snap))
        return True

    monkeypatch.setattr(JR, "report_prefetch", _report)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.02)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: _peek_of("a" * 16, "b" * 16))

    def _dl(*a, **k):
        raise BulkPreemptError("被高优传输挤走")  # 每个候选都失败

    monkeypatch.setattr(JR, "download_payload", _dl)
    stop = threading.Event()
    t = threading.Thread(
        target=JR._prefetch_fill,
        args=("http://hub", "tok", store, stop),
        kwargs={"depth": 2, "log": lambda _m: None},
        daemon=True,
    )
    t.start()
    # sleep-ok: 轮询步长（等的是「填充器跑过若干轮」这个状态，0.2s 只当挂起兜底）
    time.sleep(0.2)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    # 首轮无条件报一次（hub 重启后表是空的，靠 worker 的下一轮重建）
    assert sent and sent[0] == {"held": [], "dl": []}, sent
    assert all(s == {"held": [], "dl": []} for s in sent), sent
    assert len(sent) == 1, f"形状没变却重复上报了 {len(sent)} 次（常态空转本该零 HTTP）"


def test_filler_reports_the_held_set_and_stops_on_unchanged_shape(
    tmp_path: Path, monkeypatch
) -> None:
    """下好一份 ⇒ 上报里出现它（`held`），且下一轮形状相同时不再发。"""
    store = _FakeStore(tmp_path)
    sent: list[dict] = []

    def _report(base_url, token, snap, **kw):
        sent.append(dict(snap))
        return True

    monkeypatch.setattr(JR, "report_prefetch", _report)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.02)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: _peek_of("a" * 16))
    monkeypatch.setattr(JR, "download_payload", lambda *a, **k: _payload(64))
    stop = threading.Event()
    t = threading.Thread(
        target=JR._prefetch_fill,
        args=("http://hub", "tok", store, stop),
        kwargs={"depth": 1, "log": lambda _m: None},
        daemon=True,
    )
    t.start()
    deadline = time.time() + 5
    while store.held() != {"a" * 16} and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「那份下好了」这个状态，5s 只当挂起兜底）
        time.sleep(0.01)
    # sleep-ok: 轮询步长（等的是「接下来几轮都不再上报」这个状态，0.1s 只当挂起兜底）
    time.sleep(0.1)  # 再多跑几轮：形状不变就不该再发
    stop.set()
    t.join(5)
    assert store.held() == {"a" * 16}
    assert sent == [{"held": ["a" * 16], "dl": []}], sent


def test_filler_backs_off_after_a_failed_report(tmp_path: Path, monkeypatch) -> None:
    """上报失败 ⇒ 静默一段再试（**不许**每轮各付一次连接超时把预取节拍拖慢）。"""
    store = _FakeStore(tmp_path)
    peeks = {"n": 0}
    calls = {"n": 0}

    def _peek(*a, **k):
        peeks["n"] += 1
        return _peek_of("a" * 16)

    monkeypatch.setattr(JR, "peek_jobs", _peek)
    monkeypatch.setattr(JR, "PREFETCH_ROUND_SEC", 0.01)
    monkeypatch.setattr(JR, "download_payload", lambda *a, **k: _payload(64))

    def _report(base_url, token, snap, **kw):
        calls["n"] += 1
        return False  # 连不通

    monkeypatch.setattr(JR, "report_prefetch", _report)
    stop = threading.Event()
    t = threading.Thread(
        target=JR._prefetch_fill,
        args=("http://hub", "tok", store, stop),
        kwargs={"depth": 1, "log": lambda _m: None},
        daemon=True,
    )
    t.start()
    deadline = time.time() + 5
    while peeks["n"] < 8 and time.time() < deadline:
        # sleep-ok: 轮询步长（等的是「跑够 8 轮」这个计数，5s 只当挂起兜底）
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert peeks["n"] >= 8, f"填充器没在跑（peeks={peeks['n']}）"
    assert calls["n"] == 1, f"失败后仍在每轮重试（发了 {calls['n']} 次，应当在退避窗口内静默）"


def test_report_prefetch_is_best_effort_and_never_raises(monkeypatch) -> None:
    """上报腿的契约：200 ⇒ True；非 200 / 任何异常 ⇒ False 且**不抛**（训练一字不受影响）。"""
    seen: list[tuple[str, dict]] = []

    def _ok(base_url, token, path, **kw):
        seen.append((path, json.loads(kw["data"].decode("utf-8"))))
        return 200, b'{"ok": true}'

    monkeypatch.setattr(JL, "_request", _ok)
    snap = {"held": ["a" * 16], "dl": ["b" * 16]}
    assert JL.report_prefetch("http://hub", "tok", snap, worker_id="kaggle-c") is True
    assert seen == [
        (
            "/admin/worker-prefetch",
            {"worker": "kaggle-c", "held": ["a" * 16], "dl": ["b" * 16]},
        )
    ], seen

    monkeypatch.setattr(JL, "_request", lambda *a, **k: (403, b'{"error":"nope"}'))
    logs: list[str] = []
    assert JL.report_prefetch("http://hub", "tok", snap, log=logs.append) is False
    assert any("被拒" in m for m in logs), logs

    def _boom(*a, **k):
        raise W.RetryableError("hub 不可达")

    monkeypatch.setattr(JL, "_request", _boom)
    logs.clear()
    assert JL.report_prefetch("http://hub", "tok", snap, log=logs.append) is False
    assert any("上报失败" in m for m in logs), logs


# ─────── ④b worker_loop 交棒 staged＋复用优先（plan §2.2/§2.4；评审 P0-3）───────


def test_worker_loop_hands_over_a_stage_and_cleans_it_at_job_end(
    tmp_path: Path, monkeypatch
) -> None:
    """缺省档（`--prefetch-unpack 1`）：命中交给 `run_job` 的是 `staged_dir` 形状、盘上已解好；
    **没被搬走**的（假 run_job 不消费）由 job 收尾的 `release_handoff()` 收干净。"""
    j1, j2 = "1" * 16, "2" * 16
    packed = _packed_payload(tmp_path / "p2", tag="2")
    manifests = {
        j1: {"job_id": j1, "payload_sha256": "x" * 64, "course_fp": "f" * 64},
        j2: {
            "job_id": j2,
            "payload_sha256": hashlib.sha256(packed).hexdigest(),
            "course_fp": "f" * 64,
        },
    }
    seen: list[dict] = []
    calls = {"n": 0}

    def _acquire(base_url, token, **kw):
        calls["n"] += 1
        if calls["n"] > 2:  # 两个 job 都跑完：让循环靠 max_idle_sec 自己收尾
            return None
        jid = j1 if calls["n"] == 1 else j2
        return {"job_id": jid, "manifest": manifests[jid], "status": "ok", "lease_token": ""}

    def _run_job(base_url, token, job, **kw):
        if job["job_id"] == j2:
            pl = dict(kw.get("preloaded") or {})
            seen.append(pl)
            assert set(pl) == {"staged_dir", "payload_nbytes", "unpack_sec"}, f"形状不对：{pl}"
            assert Path(pl["staged_dir"]).is_dir(), f"交棒的暂存不在盘上：{pl}"
        if job["job_id"] == j1:  # 第一个 job 跑的时候，把第二个 job 解包预取进来
            store.store(j2, packed, _summary(packed, j2), unpack=True)
        return {"job_id": job["job_id"]}

    monkeypatch.setattr(W, "acquire_job", _acquire)
    monkeypatch.setattr(W, "run_job", _run_job)
    monkeypatch.setattr(W, "post_result", lambda *a, **k: 200)
    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([], False))
    monkeypatch.setattr(JR, "start_cancel_watcher", lambda *a, **k: None)
    monkeypatch.setattr(JR, "job_ready", lambda *a, **k: None)
    monkeypatch.setattr(JR, "report_prefetch", lambda *a, **k: False)
    monkeypatch.setattr(W, "_release_cloud_machine", lambda *a, **k: None)
    store = PrefetchStore(tmp_path)
    real_init = PrefetchStore.__init__

    def _init(self, work_dir, **kw):
        real_init(self, work_dir, **kw)
        self._items = store._items  # 让 worker_loop 造的那个 store 用我们的账本
        self.root = store.root

    monkeypatch.setattr(PrefetchStore, "__init__", _init)
    logs: list[str] = []
    n = W.worker_loop(
        "http://hub",
        "tok",
        work_dir=tmp_path,
        poll_sec=0.05,
        max_idle_sec=0.3,
        prefetch_unpack=1,
        log=logs.append,
    )
    assert n == 2, f"应当跑完两个 job：n={n} {logs}"
    assert seen and seen[0].get("staged_dir"), f"staged 形状没交给 run_job：{seen}"
    assert not (tmp_path / PREFETCH_DIR_NAME / j2).exists(), (
        "job 收尾没清掉没被搬走的暂存（孤儿会跨会话堆积）"
    )


def test_a_handed_off_stage_never_eats_the_cached_result(
    tmp_path: Path, monkeypatch
) -> None:
    """★ 评审 P0-3：搬入点必须在 `_result.json` 复用检查**之后**（住在 `_ensure_payload`）。

    搬入若发生在 `take()`（job 轮开头）而不是 `_ensure_payload`，这个用例会红：job 目录被
    rmtree ⇒ 复用缓存没了 ⇒ 走全流程重算（静默白烧一整轮 PPO）——那正是 `JOB_DIR_KEEP=2`
    与「回传失败后重领同 job」幂等路径存在的理由。
    """
    cached = {"job_id": JID, "kind": "ppo", "echo": False}
    job_dir = tmp_path / JID
    job_dir.mkdir(parents=True)
    (job_dir / "_result.json").write_text(json.dumps(cached), encoding="utf-8")
    staged = tmp_path / PREFETCH_DIR_NAME / JID / "shards"
    (staged / "w0").mkdir(parents=True)
    (staged / "w0" / "manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(W, "validate_result", lambda *_a, **_k: None)
    monkeypatch.setattr(DL, "download_payload", lambda *_a, **_k: pytest.fail("复用路径不该下载"))
    got = W.run_job(
        "http://hub",
        "tok",
        {"job_id": JID, "manifest": _ppo_manifest()},
        work_dir=tmp_path,
        preloaded={"staged_dir": str(staged), "payload_nbytes": 123, "unpack_sec": 0.4},
    )
    assert got == cached
    assert (job_dir / "_result.json").exists(), "搬入把 `_result.json` 吃掉了（复用路径失效）"
    assert staged.is_dir(), "复用路径下暂存也不该被搬走（收尾由 job_round 的 release_handoff 做）"
