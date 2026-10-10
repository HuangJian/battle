"""test_blob_batch.py — **一次 GET 取多件 blob**（plan/aistudio-transfer-hardening §3.4，B1）。

现场：一轮里 `opt`（~0.5MB）与 `ref`（kickstart 打开时）是**两次**独立的 blob GET。省的不是字节
（字节数逐字不变），是**建连 + TCP 慢启动**那笔税——aistudio 走 Cloudflare quick tunnel，
每建一次连都要重新抽一次签（§22）。

三条边界（都有用例钉住）：

1. **旧 hub 兼容**：旧 hub 收到逗号名会走 `blob_path` 的**未知名 400** ⇒ worker 必须整批
   **逐个回退单取**（回退链就是兼容本身，不是「出错兜底」）；
2. **绝不返回半份**：解析任一环节不对就 `ValueError`；hub 侧缺一件就**整笔 404**。
   worker 的安全阀要求 sha 非空时只认内容寻址，半份会被读成「全拿到」⇒ 那是静默 warm-start
   那一类事故的形状；
3. **缺件就少一个键**（`resolve_blobs_batch`），由调用方按安全阀响亮失败——**不**降级、
   **不**编造、`preloaded` 字节不符直接 `RetryableError`。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.download as dl
from common.protocol import BLOB_OPT, BLOB_REF, RetryableError, blob_path
from hub.blob import BlobRoutes

OPT_RAW = b"opt" * 512
REF_RAW = b"ref" * 256


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _frame(name: str, raw: bytes) -> bytes:
    """**分帧格式**（hub 侧 `_serve_blob_batch` 与 worker 侧 `parse_blob_batch` 的唯一契约）：
    `name\\n<len>\\n` + raw，重复。"""
    return name.encode("utf-8") + b"\n" + str(len(raw)).encode("ascii") + b"\n" + raw


# ───────────────── ① 帧解析（纯函数）────────────────


def test_frames_round_trip() -> None:
    """两件顺序拼回原样（含二进制内容与空件）。"""
    body = _frame(BLOB_OPT, OPT_RAW) + _frame(BLOB_REF, b"")
    got = dl.parse_blob_batch(body)
    assert got == {BLOB_OPT: OPT_RAW, BLOB_REF: b""}


def test_a_half_frame_is_an_error_not_a_partial_answer() -> None:
    """★ 绝不返回半份：声明 100 字节却只给 10 ⇒ ValueError（调用方据此整批回退）。"""
    body = _frame(BLOB_OPT, OPT_RAW) + _frame(BLOB_REF, REF_RAW)[:-10]
    with pytest.raises(ValueError):
        dl.parse_blob_batch(body)


@pytest.mark.parametrize(
    "bad",
    [
        b"opt",  # 缺第一个换行
        b"opt\n",  # 缺长度换行
        b"opt\nabc\nxx",  # 长度不是整数
        b"opt\n-1\nxx",  # 负长度
    ],
)
def test_every_malformed_frame_is_rejected(bad: bytes) -> None:
    """帧头四种坏法都要抛（而不是静默少给一件）。"""
    with pytest.raises(ValueError):
        dl.parse_blob_batch(bad)


# ───────────────── ② `download_blobs`：单件委派 / 整批 / 回退 ─────────────────


def test_one_name_delegates_to_the_old_single_path(monkeypatch) -> None:
    """只有一件 ⇒ **逐字走旧路径** `download_blob`（不拼逗号名，旧 hub 形状不变）。"""
    seen: list[str] = []

    def _one(_b, _t, _j, n, **_k) -> bytes:
        seen.append(n)
        return b"one"

    monkeypatch.setattr(dl, "download_blob", _one)
    monkeypatch.setattr(
        dl, "_get_with_retry", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不该走批量"))
    )
    assert dl.download_blobs("http://hub", "t", "j1", [BLOB_OPT]) == {BLOB_OPT: b"one"}
    assert seen == [BLOB_OPT]


def test_batch_issues_exactly_one_get(monkeypatch) -> None:
    """两件 ⇒ 一次 GET（`?name=opt,ref`），省的是建连不是字节。"""
    calls: list[str] = []

    def _get(_b, _t, path, **_k) -> bytes:
        calls.append(path)
        return _frame(BLOB_OPT, OPT_RAW) + _frame(BLOB_REF, REF_RAW)

    monkeypatch.setattr(dl, "_get_with_retry", _get)
    got = dl.download_blobs("http://hub", "t", "j1", [BLOB_OPT, BLOB_REF], log=lambda _m: None)
    assert got == {BLOB_OPT: OPT_RAW, BLOB_REF: REF_RAW}
    assert len(calls) == 1, f"应当是**一次** GET：{calls}"
    assert calls[0].endswith("?name=opt,ref"), f"批量名没拼对：{calls[0]}"


def test_batch_failure_falls_back_to_one_by_one(monkeypatch) -> None:
    """★ 旧 hub 兼容：整批 400/404/解析失败 ⇒ **逐个回退单取**（回退链就是兼容本身）。"""
    per_name: list[str] = []

    def _single(_b, _t, _j, n, **_k) -> bytes:
        per_name.append(n)
        return OPT_RAW if n == BLOB_OPT else REF_RAW

    monkeypatch.setattr(
        dl, "_get_with_retry", lambda *_a, **_k: (_ for _ in ()).throw(OSError("400"))
    )
    monkeypatch.setattr(dl, "download_blob", _single)
    got = dl.download_blobs("http://hub", "t", "j1", [BLOB_OPT, BLOB_REF], log=lambda _m: None)
    assert got == {BLOB_OPT: OPT_RAW, BLOB_REF: REF_RAW}, "回退后两件都得拿到"
    assert sorted(per_name) == [BLOB_OPT, BLOB_REF], f"没有逐个回退：{per_name}"


# ───────────────── ③ `resolve_blobs_batch`：缓存 / sha / 缺件 ─────────────────


def _no_network(monkeypatch) -> None:
    monkeypatch.setattr(
        dl, "download_blobs", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不该走网络"))
    )


def test_cache_hit_costs_zero_bytes_and_reports_the_source(tmp_path: Path, monkeypatch) -> None:
    """缓存命中 ⇒ `hit=True, src="cache"`，且**一次网络都不发**。"""
    _no_network(monkeypatch)
    root = tmp_path / "blob_cache"
    root.mkdir()
    (root / _sha(OPT_RAW)).write_bytes(OPT_RAW)
    got = dl.resolve_blobs_batch(
        wanted={BLOB_OPT: _sha(OPT_RAW)},
        blob_root=root,
        jid="j1",
        base_url="http://hub",
        token="t",
        preloaded=None,
        log=lambda _m: None,
    )
    assert got[BLOB_OPT] == (OPT_RAW, True, "cache")


def test_a_corrupt_cache_entry_is_refetched_not_trusted(tmp_path: Path, monkeypatch) -> None:
    """缓存里有这个 sha 但**字节不符** ⇒ 不认（坏字节会比「没缓存」更糟：它看着像命中）。"""
    root = tmp_path / "blob_cache"
    root.mkdir()
    (root / _sha(OPT_RAW)).write_bytes(b"corrupted")
    logs: list[str] = []
    monkeypatch.setattr(dl, "download_blobs", lambda *_a, **_k: {BLOB_OPT: OPT_RAW})
    got = dl.resolve_blobs_batch(
        wanted={BLOB_OPT: _sha(OPT_RAW)},
        blob_root=root,
        jid="j1",
        base_url="http://hub",
        token="t",
        preloaded=None,
        log=logs.append,
    )
    assert got[BLOB_OPT] == (OPT_RAW, False, "download")
    assert (root / _sha(OPT_RAW)).read_bytes() == OPT_RAW, "重取后要把坏的覆盖掉（否则白付一辈子下载税）"


def test_preloaded_bytes_are_used_as_is(tmp_path: Path, monkeypatch) -> None:
    """`preloaded` 命中 ⇒ `src="preloaded"`（零字节，且**不**写缓存：它本来就随包来了）。"""
    _no_network(monkeypatch)
    got = dl.resolve_blobs_batch(
        wanted={BLOB_OPT: _sha(OPT_RAW)},
        blob_root=tmp_path / "blob_cache",
        jid="j1",
        base_url="http://hub",
        token="t",
        preloaded={"blobs": {BLOB_OPT: OPT_RAW}},
        log=lambda _m: None,
    )
    assert got[BLOB_OPT] == (OPT_RAW, False, "preloaded")


def test_preloaded_bytes_that_do_not_match_the_sha_are_refused(tmp_path: Path, monkeypatch) -> None:
    """`preloaded` 与 sha 不符 ⇒ `RetryableError`（拒用坏字节，不是重下一次就能好）。"""
    _no_network(monkeypatch)
    with pytest.raises(RetryableError):
        dl.resolve_blobs_batch(
            wanted={BLOB_OPT: _sha(OPT_RAW)},
            blob_root=tmp_path / "blob_cache",
            jid="j1",
            base_url="http://hub",
            token="t",
            preloaded={"blobs": {BLOB_OPT: b"wrong"}},
            log=lambda _m: None,
        )


def test_a_missing_piece_is_left_out_not_invented(tmp_path: Path, monkeypatch) -> None:
    """★ 缺件就少一个键：调用方按安全阀响亮失败，本函数**不**降级、**不**编造。"""
    monkeypatch.setattr(dl, "download_blobs", lambda *_a, **_k: {BLOB_OPT: OPT_RAW})
    got = dl.resolve_blobs_batch(
        wanted={BLOB_OPT: _sha(OPT_RAW), BLOB_REF: _sha(REF_RAW)},
        blob_root=tmp_path / "blob_cache",
        jid="j1",
        base_url="http://hub",
        token="t",
        preloaded=None,
        log=lambda _m: None,
    )
    assert set(got) == {BLOB_OPT}, f"缺的那件不许出现在结果里：{sorted(got)}"


def test_downloaded_bytes_that_miss_the_sha_raise_retryable(tmp_path: Path, monkeypatch) -> None:
    """网络字节与 sha 不符 ⇒ `RetryableError`（传输损坏：重下可修，与「确定性拒收」两回事）。"""
    monkeypatch.setattr(dl, "download_blobs", lambda *_a, **_k: {BLOB_OPT: b"trashed"})
    with pytest.raises(RetryableError):
        dl.resolve_blobs_batch(
            wanted={BLOB_OPT: _sha(OPT_RAW)},
            blob_root=tmp_path / "blob_cache",
            jid="j1",
            base_url="http://hub",
            token="t",
            preloaded=None,
            log=lambda _m: None,
        )


# ───────────────── ④ hub 侧：分帧 + 整笔失败 ─────────────────


class _FakeHub:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _job_dir(self, jid: str) -> Path:
        d = self.root / jid
        d.mkdir(parents=True, exist_ok=True)
        return d


class _FakeHandler(BlobRoutes):
    """够用的 handler 替身：`BlobRoutes` 只用到下面这几个成员（继承是为了让类型成立，
    组合类的那套 BaseHTTPRequestHandler 状态本用例不需要）。"""

    def __init__(self, root: Path, path: str) -> None:
        self.hub = _FakeHub(root)
        self.path = path
        self.codes: list[int] = []
        self.payloads: list[object] = []

    def _job_or_404(self) -> str:
        return "j1"

    def _json(self, obj: object, code: int) -> None:
        self.codes.append(code)
        self.payloads.append(obj)

    def _bytes(self, raw: bytes) -> None:
        self.codes.append(200)
        self.payloads.append(raw)

    def _serve_path(self, p: Path, missing: str = "") -> None:
        self.codes.append(200)
        self.payloads.append(p.read_bytes())


def _seed(root: Path, **names: bytes) -> None:
    jd = root / "j1"
    jd.mkdir(parents=True, exist_ok=True)
    for name, raw in names.items():
        blob_path(jd, name).write_bytes(raw)


def test_hub_frames_the_batch_and_the_worker_parses_it_back(tmp_path: Path) -> None:
    """★ 跨侧契约：hub 拼的帧 ⇒ worker 的 `parse_blob_batch` 解得回**逐字节相同**的两件。"""
    root = tmp_path / "jobs"
    _seed(root, opt=OPT_RAW, ref=REF_RAW)
    h = _FakeHandler(root, "/jobs/j1/blob?name=opt,ref")
    BlobRoutes._serve_blob_batch(h, "j1", [BLOB_OPT, BLOB_REF])
    assert h.codes == [200]
    assert dl.parse_blob_batch(h.payloads[0]) == {BLOB_OPT: OPT_RAW, BLOB_REF: REF_RAW}  # type: ignore[arg-type]


def test_hub_404s_the_whole_batch_when_one_piece_is_missing(tmp_path: Path) -> None:
    """★ 缺一件 ⇒ **整笔 404**（让 worker 去逐个回退），绝不「能给的先给」。"""
    root = tmp_path / "jobs"
    _seed(root, opt=OPT_RAW)  # 没有 ref
    h = _FakeHandler(root, "/jobs/j1/blob?name=opt,ref")
    BlobRoutes._serve_blob_batch(h, "j1", [BLOB_OPT, BLOB_REF])
    assert h.codes == [404], f"应整笔 404（半份会让调用方误读成全拿到）：{h.codes}"
    assert h.payloads[0] == {"error": f"no blob {BLOB_REF}"}


def test_hub_400s_an_unknown_name(tmp_path: Path) -> None:
    """未知名 ⇒ 400（与单件路径同一口径：`blob_path` 抛 `ProtocolError`）。"""
    root = tmp_path / "jobs"
    _seed(root, opt=OPT_RAW)
    h = _FakeHandler(root, "/jobs/j1/blob?name=opt,weird")
    BlobRoutes._serve_blob_batch(h, "j1", [BLOB_OPT, "weird"])
    assert h.codes == [400]


def test_a_single_name_still_goes_through_the_old_branch(tmp_path: Path) -> None:
    """`?name=opt`（单名）逐字走旧分支——新 hub 对旧 worker 的响应形状不变。"""
    root = tmp_path / "jobs"
    _seed(root, opt=OPT_RAW)
    h = _FakeHandler(root, "/jobs/j1/blob?name=opt")
    BlobRoutes._get_blob(h)
    assert h.codes == [200]
    assert h.payloads[0] == OPT_RAW
