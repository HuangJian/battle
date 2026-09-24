"""test_opt_blob_shape.py — opt-blob-diet（plan/opt-blob-diet.plan.md）形状与权重解析。

覆盖 §6.1 的纯逻辑项（**不 import torch**——本文件钉的都是 worker 里免 torch 的那几层）：

  1. `pack_opt_tar` 成员恰好 `{"opt.pt"}`（`model.pt` 从此不进 tar）；
  2. `_resolve_weights` 的源优先级（W0 payload / W1 cache / W2 preloaded / W3 download）
     与「W0 优先但照校 sha」；
  3. 四源皆尽 ⇒ 返回 none（**由调用方决定** W4 还是响亮拒绝；用源码锚点钉住 `run_job`
     那两处 `ProtocolError` 带源清单的口径）；
  3b. 瞬时失败（5xx/网络/sha 不符）⇒ `RetryableError`，不许报成确定性失败；
  3c. 旧 hub（无 `init` blob ⇒ 400）不报错，交 W4 兜底；
  4. 哨兵 sha（`""`/`"bc"`，BC job 与全新 run 首轮）⇒ **零 blob 请求**；
  5. `_cache_produced_weights` 闭环：产物字节进 `blob_cache` ⇒ 下一轮 W1 命中、零下行
     （本 plan 唯一一处「少写就净亏 110 KB/轮」的地方，评审 F1）；
  6. `worker_server.missing_blobs` 的两道闸（`slim` + `is_content_sha`）——BC / 非 slim
     臂不许索取 `init`（否则永久 428）。
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import worker as worker_mod
from remote.protocol import (
    BLOB_INIT,
    BLOB_NAMES,
    ProtocolError,
    RetryableError,
    blob_path,
    is_content_sha,
)
from remote.worker_server import missing_blobs


def _log(_msg: str) -> None:
    return None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _resolve(job_dir: Path, fp: str, *, blob_root: Path | None = None, preloaded=None):
    """`_resolve_weights` 的测试包装（缺省 blob_root = job_dir 的兄弟目录）。"""
    return worker_mod._resolve_weights(
        job_dir=job_dir,
        init_weights_fp=fp,
        blob_root=blob_root if blob_root is not None else job_dir.parent / "blob_cache",
        jid="j1",
        base_url="http://hub.local",
        token="t",
        preloaded=preloaded,
        log=_log,
    )


def _job(tmp_path: Path, name: str) -> Path:
    jd = tmp_path / name
    jd.mkdir(parents=True, exist_ok=True)
    return jd


# ------------------------------------------------------------------ 1) tar 形状


def test_pack_opt_tar_members(tmp_path: Path) -> None:
    """成员集合恰为 `{"opt.pt"}` —— 假目录里同时放 model.pt / opt.pt / state.json。"""
    src = tmp_path / "ppo_final"
    src.mkdir()
    (src / "model.pt").write_bytes(b"model-bytes")
    (src / "opt.pt").write_bytes(b"opt-bytes")
    (src / "state.json").write_bytes(b'{"rng": 1}')
    raw = worker_mod.pack_opt_tar(src)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        members = set(tf.getnames())
        f = tf.extractfile("opt.pt")
        assert f is not None
        payload = f.read()
    assert members == {"opt.pt"}, f"opt tar 应只装 opt.pt（实得 {sorted(members)}）"
    assert payload == b"opt-bytes"
    # 形状指纹：model.pt / state.json 的字节一个都不该进 tar（§2.1-1）
    assert b"model-bytes" not in raw
    assert b"state.json" not in raw


def test_pack_opt_tar_empty_dir_is_empty_tar(tmp_path: Path) -> None:
    """无 opt.pt（首轮/异常）⇒ 空 tar 不抛（旧行为逐字保留）。"""
    src = tmp_path / "empty"
    src.mkdir()
    with tarfile.open(fileobj=io.BytesIO(worker_mod.pack_opt_tar(src)), mode="r:") as tf:
        assert set(tf.getnames()) == set()


def test_unpack_opt_only_tar_roundtrip(tmp_path: Path) -> None:
    """`unpack_opt_tar` 对 opt-only tar 解出 `opt.pt`、**没有** `model.pt`（restore 分流判据）。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "opt.pt").write_bytes(b"adam-state")
    dest = tmp_path / "opt_init"
    worker_mod.unpack_opt_tar(worker_mod.pack_opt_tar(src), dest)
    assert (dest / "opt.pt").read_bytes() == b"adam-state"
    assert not (dest / "model.pt").exists(), "新形状下 opt_init 里不该有 model.pt"


# ------------------------------------------------------------------ 2) 源优先级


def test_resolve_weights_payload_and_sha_check(tmp_path: Path) -> None:
    """W0：payload 里那份（随包能离线跑）—— 命中即用，且**照样校 sha**（评审 F4）。"""
    raw = b'{"format":"nn-weights-json","params":{"w":1}}'
    jd = _job(tmp_path, "job")
    (jd / "init_weights.json").write_bytes(raw)
    path, src, wire = _resolve(jd, _sha(raw))
    assert src == "payload" and wire == 0 and path is not None
    assert path.read_bytes() == raw


def test_resolve_weights_bad_payload_is_invalidated_then_blob(tmp_path: Path) -> None:
    """payload 里那份 sha 不符 ⇒ 作废 + 回源 blob（**绝不**用坏字节，也**绝不**静默）。"""
    good = b'{"format":"nn-weights-json","params":{"good":1}}'
    jd = _job(tmp_path, "job")
    (jd / "init_weights.json").write_bytes(b'{"format":"nn-weights-json","params":{"bad":1}}')
    cache = tmp_path / "blob_cache"
    cache.mkdir()
    (cache / _sha(good)).write_bytes(good)
    _, src, wire = _resolve(jd, _sha(good), blob_root=cache)
    assert src == "cache" and wire == 0
    assert (jd / "init_weights.json").read_bytes() == good


def test_resolve_weights_source_priority(tmp_path: Path, monkeypatch) -> None:
    """W1/W2/W3 各命中一次；W0 与 W1 同时可得时取 W0（§3.2）。"""
    raw = b'{"format":"nn-weights-json","params":{"w":2}}'
    fp = _sha(raw)

    # W1：blob_cache 命中（零字节）
    cache = tmp_path / "w1" / "blob_cache"
    cache.mkdir(parents=True)
    (cache / fp).write_bytes(raw)
    path, src, wire = _resolve(_job(tmp_path, "w1/job"), fp, blob_root=cache)
    assert (src, wire) == ("cache", 0)
    assert path is not None and path.read_bytes() == raw

    # W2：preloaded（push 腿，节点盘上还没有）
    jd2 = _job(tmp_path, "w2/job")
    path2, src2, wire2 = _resolve(jd2, fp, preloaded={"blobs": {BLOB_INIT: raw}})
    assert (src2, wire2) == ("preloaded", 0)
    assert path2 is not None and path2.read_bytes() == raw
    # 顺手落进 blob_cache（下一次同 sha 就是 W1 命中）
    assert (tmp_path / "w2" / "blob_cache" / fp).read_bytes() == raw

    # W3：换机首次——只能下载（wire_bytes = raw 长度）
    seen: list[str] = []

    def _dl(_base, _tok, _jid, name, **_kw):
        seen.append(name)
        return raw

    monkeypatch.setattr(worker_mod, "download_blob", _dl)
    path3, src3, wire3 = _resolve(_job(tmp_path, "w3/job"), fp)
    assert (src3, wire3) == ("download", len(raw))
    assert seen == [BLOB_INIT], f"下载路径只该走 name=init（实得 {seen}）"
    assert path3 is not None and path3.read_bytes() == raw

    # W0 胜过 W1：两份都在时取 payload
    jd4 = _job(tmp_path, "w0/job")
    (jd4 / "init_weights.json").write_bytes(raw)
    cache4 = tmp_path / "w0" / "blob_cache"
    cache4.mkdir(parents=True)
    (cache4 / fp).write_bytes(raw)
    _, src4, wire4 = _resolve(jd4, fp, blob_root=cache4)
    assert (src4, wire4) == ("payload", 0)


# ------------------------------------------------------------------ 3) 失败分类


def test_resolve_weights_transient_is_retryable(tmp_path: Path, monkeypatch) -> None:
    """瞬时失败（下回来 sha 不符 / 网络）⇒ `RetryableError`。

    报成确定性失败 = `report_job_failure` 停掉一条腿（§3.2 的失败分类红线）。
    """
    monkeypatch.setattr(worker_mod, "download_blob", lambda *_a, **_k: b"wrong-bytes")
    with pytest.raises(RetryableError):
        _resolve(_job(tmp_path, "job"), "a" * 64)


def test_resolve_weights_legacy_tar_without_init_blob(tmp_path: Path, monkeypatch) -> None:
    """旧形状 tar + 无 `init` blob（旧 hub）⇒ W1–W3 全 miss 但**不报错**，交 W4 兜底。

    旧 hub 对 `?name=init` 回 400（未知名）⇒ `_resolve_blob` 抛 ProtocolError；
    本函数该把它消化成 none，让 restore 段继续走 tar 里的 `model.pt`（§3.5 第三行）。
    """

    def _400(*_a, **_k):
        raise ProtocolError("blob init: HTTP 400")

    monkeypatch.setattr(worker_mod, "download_blob", _400)
    path, src, wire = _resolve(_job(tmp_path, "job"), "b" * 64)
    assert (path, src, wire) == (None, "none", 0)


def test_run_job_refusal_and_order_anchors() -> None:
    """源码锚点（§6.1-3）：四源皆尽的两处拒绝都带**源清单**；权重解析早于 rollout。

    为什么用锚点而不是跑一遍：那两条路径要真 torch + 真 job（本文件刻意免 torch）。
    锚点钉住的是「口径」——错误消息必须能读出试过哪些源（事故现场靠它定位）。
    """
    src = Path(worker_mod.__file__).read_text(encoding="utf-8")
    assert "四源皆尽" in src
    # 三处拒绝都带源清单：① kind=iter/run 的提前拒绝；② 有 tar 但无 model.pt；③ 无 tar 首轮
    assert src.count("{'/'.join(WEIGHT_SOURCES[:4])}") == 3, "三处拒绝都要带源清单"
    assert "WEIGHT_SOURCES: tuple[str, ...] = (" in src
    # W4 分流：restore 段按 legacy tar 里有没有 model.pt 决定，并把它记成 legacy_tar
    assert 'weights_src = "legacy_tar"' in src
    assert 'legacy_model = opt_dir / "model.pt"' in src
    # 顺序约束（§3.3）：解析必须在 rollout 调用点之前
    assert src.index("init_w, weights_src, weights_wire_bytes = _resolve_weights(") < src.index(
        "iter_info = run_iter_rollout("
    ), "权重解析必须早于 run_iter_rollout"
    # 且必须落在 BC 的 early-return 之后（哨兵 "bc" 不许进五源解析）
    assert src.index("return _run_bc_job(") < src.index(
        "init_w, weights_src, weights_wire_bytes = _resolve_weights("
    ), "权重解析必须晚于 BC 分叉"


# ------------------------------------------------------------------ 4) 哨兵 sha


def test_resolve_weights_sentinel_sha_skips_blobs(tmp_path: Path, monkeypatch) -> None:
    """`init_weights_fp ∈ {"", "bc"}`（BC job / 全新 run 首轮）⇒ **零网络请求**、返回 none。

    不这么做的话 BC 会打一个必然 404 的 `?name=init`，被判成「确定性缺失」⇒ 停腿（评审 F2）。
    """
    calls: list[str] = []

    def _boom(*_a, **_k):
        calls.append("called")
        raise AssertionError("哨兵 sha 下不许碰 blob 路径")

    monkeypatch.setattr(worker_mod, "download_blob", _boom)
    for fp in ("", "bc", "deadbeef", "A" * 64):
        assert _resolve(_job(tmp_path, f"job_{fp or 'empty'}"), fp) == (None, "none", 0), fp
    assert calls == []
    # payload 里若有那份，照用（BC 无权重；离线/非 slim 臂会有）
    jd = _job(tmp_path, "bcjob")
    (jd / "init_weights.json").write_bytes(b'{"bc":0}')
    path, src, _ = _resolve(jd, "bc")
    assert src == "payload" and path is not None


def test_is_content_sha_shape() -> None:
    """形状判据（单一实现住 protocol）：64 位小写 hex 才算内容寻址 sha。"""
    assert is_content_sha("a" * 64)
    assert not is_content_sha("bc")
    assert not is_content_sha("")
    assert not is_content_sha("a" * 63)
    assert not is_content_sha("A" * 64)  # 大写不是本仓的 sha 形状
    assert not is_content_sha("g" * 64)


# ------------------------------------------------------------------ 5) 产物缓存闭环


def test_weights_produced_lands_in_blob_cache(tmp_path: Path) -> None:
    """评审 F1 的回归闸：产物字节进 `blob_cache` ⇒ 下一轮 W1 命中、零下行。

    hub 下一轮的 `init_weights_fp` = `sha256(args.out)`，而 `args.out` 就是 worker 这一轮
    回传的 `weights.json` 原始字节（`verify_and_land` 落盘不改字节）⇒ 两侧的键必然相等。
    少了这一步：每轮多下 379,114 B 而只省 268,996 B（净亏 110 KB/轮）。
    """
    raw = b'{"format":"nn-weights-json","params":{"it":1}}'
    blob_root = tmp_path / "blob_cache"
    sha = worker_mod._cache_produced_weights(blob_root, raw, _log)
    assert sha == _sha(raw)
    assert (blob_root / sha).read_bytes() == raw
    # 二次调用幂等（同 sha 已存在直接返回，不重写）
    assert worker_mod._cache_produced_weights(blob_root, raw, _log) == sha

    # 下一轮：hub 用同一份字节的 sha 发 init blob ⇒ 命中、零字节
    jd = _job(tmp_path, "next_job")
    path, src, wire = _resolve(jd, sha, blob_root=blob_root)
    assert (src, wire) == ("cache", 0)
    assert path is not None and path.read_bytes() == raw


def test_run_job_product_section_caches_weights() -> None:
    """源码锚点：`run_job` 产物段必须调用 `_cache_produced_weights`（删掉它 = 每轮净亏）。"""
    src = Path(worker_mod.__file__).read_text(encoding="utf-8")
    assert "_cache_produced_weights(blob_root, wj_raw, log)" in src
    # 且 opt tar 那一行仍在（两者同规、同一理由）
    assert "hashlib.sha256(opt_tar_raw).hexdigest()" in src
    # 回传的 weights_json 用的就是被缓存的那份字节（同字节 ⇒ 同 sha，别中途再读一次盘）
    assert '"weights_json": encode_weights_json(wj_raw),' in src


# ------------------------------------------------------------------ 6) 节点缺 blob 判据


def test_missing_blobs_init_requires_slim_and_real_sha() -> None:
    """两道闸：非 slim 臂（权重在 payload 里）与哨兵 sha 都**不许**索取 `init`。"""
    sha = "a" * 64
    base = {"init_weights_fp": sha, "slim": True}

    assert missing_blobs(base, {}, lambda _s: False) == [BLOB_INIT]
    # ① 已随 body 带了 ⇒ 不缺
    assert missing_blobs(base, {BLOB_INIT: b"x"}, lambda _s: False) == []
    # ② 节点盘上已有 ⇒ 不缺
    assert missing_blobs(base, {}, lambda s: s == sha) == []
    # ③ 非 slim（A/B 回退臂）：权重在 payload 里（worker 的 W0），索取 blob = 428 死循环
    assert missing_blobs({**base, "slim": False}, {}, lambda _s: False) == []
    # ④ BC job / 全新 run 首轮：哨兵不是内容寻址 sha
    assert missing_blobs({**base, "init_weights_fp": "bc"}, {}, lambda _s: False) == []
    assert missing_blobs({**base, "init_weights_fp": ""}, {}, lambda _s: False) == []
    # opt/ref 的旧口径不变（有 sha 就查缓存）
    assert missing_blobs({"opt_sha": sha}, {}, lambda _s: False) == ["opt"]
    assert missing_blobs({"opt_sha": "", "ref_sha": ""}, {}, lambda _s: False) == []


# ------------------------------------------------------------------ 7) 协议白名单


def test_blob_init_is_registered_in_blob_names() -> None:
    """`init` 进 `BLOB_NAMES` 白名单（`blob_path` 只认它；hub 的 `?name=init` 靠这条放行）。"""
    assert BLOB_INIT == "init"
    assert BLOB_INIT in BLOB_NAMES
    assert blob_path("/tmp/j", BLOB_INIT).name == "blob.init"
    with pytest.raises(ProtocolError):
        blob_path("/tmp/j", "nope")
