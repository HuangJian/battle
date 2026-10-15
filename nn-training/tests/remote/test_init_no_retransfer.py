"""tests/remote/test_init_no_retransfer.py —— 「已经持有 ⇒ 不再传」的红线（用户 2026-10-15）。

用户口径（原话）：「如果 ppo 云机已经持有完整版权重时，在新一轮训练任务里，不需要再从 hub
传输给它一份 delta 版权重（这本来就是它回传给 hub 的）。写个测试来保障！」

链路事实（R1 权重 delta，`plan/wire-diet.plan.md` §3）：云机一轮的产物有两份形态 ——
  * **整份 f32**（本机那份，`train_core` 写进 `blob_cache/<sha256(整份)>`）；
  * **delta 容器**（发出去的那份字节，`pack_weights_delta` 的结果，本机同样写进
    `blob_cache/<sha256(delta)>`）。
hub 只做原样收发，于是下一轮 manifest 的 `init_weights_fp` 可能是：
  (a) 我们回传的 **delta** 的 sha（离线腿 / 锚点：hub 存的就是这份容器）；
  (b) 本机那份**整份**的 sha（hub 侧同会话按 `sha256(args.out)` 声明）。
两条都必须是**本机自给、零字节**：只认内容寻址（键是 sha，读到即校），**绝不为一份自己已经
持有的字节走一趟网络**；而且给出之前要把 delta 落地成整份（`remote/weights_standard`），
否则下游 `--weights init_weights.json` 与 `build_ppo` 读 arch 会 `JSON.parse` 报
`Unexpected identifier "BWD1"`（用户 2026-10-15 现场）。

本文件钉四格（每格都是「没有它会静默重复付流量或重复报 BWD1」的那一种）：
  ① 本机持有自己的 delta（hub 声明 (a)）⇒ `src=cache`、wire=0、**一次网络都不发**，落地成整份；
  ② 本机持有自己的整份（hub 声明 (b)）⇒ 同上；
  ③ 反证：本机什么都没有 ⇒ **会**走网络（否则①②的断言就是空的）；
  ④ payload 里硬塞一份 delta（hub 还是发了）⇒ 不额外走网，且**用之前**就已落地成整份。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import download as download_mod
from remote import weights_delta as wd
from remote import weights_standard as ws

SHAPES = {"conv.w": (4, 3, 3, 3), "fc.b": (16,), "head.w": (16, 165)}
#: 坏 hub：任何真发出去的请求都会连接失败（用例据此证明「零传输」不是空话）。
DEAD_HUB = "http://127.0.0.1:9"


def _weights_json(params: dict[str, np.ndarray]) -> bytes:
    return json.dumps(
        {
            "format": "nn-weights-json",
            "version": 1,
            "schema_major": 3,
            "arch": {"hidden": 128},
            "num_params": int(sum(int(p.size) for p in params.values())),
            "params": {
                n: {
                    "shape": list(p.shape),
                    "data": base64.b64encode(
                        np.ascontiguousarray(p, dtype="<f4").tobytes()
                    ).decode("ascii"),
                }
                for n, p in params.items()
            },
        },
        indent=2,
    ).encode("utf-8")


def _params(seed: int, *, amp: float = 0.2) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: rng.standard_normal(s).astype(np.float32) * amp for n, s in SHAPES.items()}


def _step(base: dict[str, np.ndarray], *, seed: int, amp: float = 2e-3) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: (p + rng.standard_normal(p.shape).astype(np.float32) * amp).astype(np.float32) for n, p in base.items()}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _quiet(_msg: str = "") -> None:
    return None


def _job(tmp_path: Path) -> Path:
    jd = tmp_path / "job"
    jd.mkdir(parents=True, exist_ok=True)
    return jd


def _forbid_download(monkeypatch: pytest.MonkeyPatch, called: list[str]) -> None:
    """把 `download_blob` 换成「一被调到就记一笔」的桩（返回空串即可：不该被调到）。"""

    def _fake(*_args, **_kwargs) -> bytes:
        called.append("download")
        return b""

    monkeypatch.setattr(download_mod, "download_blob", _fake)


def _resolve(job_dir: Path, fp: str, *, blob_root: Path, preloaded=None):
    """`_resolve_weights` 的测试包装（与 `test_opt_blob_shape._resolve` 同口径）。"""
    return download_mod._resolve_weights(
        job_dir=job_dir,
        init_weights_fp=fp,
        blob_root=blob_root,
        jid="j1",
        base_url=DEAD_HUB,
        token="t",
        preloaded=preloaded,
        log=_quiet,
    )


def _produce_round(tmp_path: Path) -> tuple[bytes, bytes, Path]:
    """造「本机产出的一轮」：返回 `(整份字节, delta 字节, blob_cache 根)`。

    整份 = 生产者写进 `blob_cache/<sha(整份)>` 的那份；delta = 打包后回传给 hub 的那份
    （新加的那行把它也写进同一缓存，键 = `sha(delta)`）——这里用**真**的打包/缓存函数。
    """
    base = _params(1)
    new = _step(base, seed=2)
    base_raw, new_raw = _weights_json(base), _weights_json(new)
    delta = wd.pack_weights_delta(base_raw, new_raw, base_sha=_sha(base_raw))
    cache = tmp_path / "blob_cache"
    cache.mkdir(exist_ok=True)
    # 生产者的两行：整份（既有）+ delta（本次新增）
    download_mod._cache_produced_weights(cache, new_raw, _quiet)
    download_mod._cache_blob(cache, _sha(delta), delta, _quiet)
    # 上一轮的整份也在缓存里（delta 的 base —— 落地时要用）
    download_mod._cache_blob(cache, _sha(base_raw), base_raw, _quiet)
    return new_raw, delta, cache


def test_holds_own_delta_no_transfer_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """① hub 声明的 init = 我们回传的 delta ⇒ 本地缓存命中、零传输，且返回的已是整份。"""
    new_raw, delta, cache = _produce_round(tmp_path)
    called: list[str] = []
    _forbid_download(monkeypatch, called)
    jd = _job(tmp_path)  # ⚠ 故意**没有** payload init：证明不靠 hub 再传一份

    path, src, wire = _resolve(jd, _sha(delta), blob_root=cache)

    assert src == "cache" and wire == 0
    assert called == [], "本机已持有这份 delta ⇒ 一次网络都不该发"
    assert path is not None
    got = path.read_bytes()
    assert not got.startswith(b"BWD1"), "交给下游的必须是整份（rollout argv / build_ppo 会 JSON.parse）"
    assert json.loads(got)["format"] == "nn-weights-json"
    assert got == wd.apply_weights_delta(
        _weights_json(_params(1)), delta, expect_base_sha=_sha(_weights_json(_params(1)))
    )


def test_holds_own_full_weights_no_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """② hub 声明的 init = 本机那份整份（同会话口径）⇒ 同样零传输（命中即用，不必落地）。"""
    new_raw, _delta, cache = _produce_round(tmp_path)
    called: list[str] = []
    _forbid_download(monkeypatch, called)
    jd = _job(tmp_path)

    path, src, wire = _resolve(jd, _sha(new_raw), blob_root=cache)

    assert src == "cache" and wire == 0 and called == []
    assert path is not None and path.read_bytes() == new_raw


def test_without_local_copy_it_does_go_to_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """③ 反证：本机没有这份字节 ⇒ 该发网络就发（①②的「零传输」不是恒真）。"""
    new_raw, delta, cache = _produce_round(tmp_path)
    (cache / _sha(delta)).unlink()  # 本机不再持有 delta
    calls: list[str] = []

    def _fake_download(base_url, token, jid, name, log=None):
        calls.append(name)
        return delta

    monkeypatch.setattr(download_mod, "download_blob", _fake_download)
    jd = _job(tmp_path)

    path, src, wire = _resolve(jd, _sha(delta), blob_root=cache)

    assert calls == ["init"], "换机首次（hand-off）就该真下这一份"
    assert src == "download" and wire == len(delta)
    # 下回来的**照样**要落地成整份（第一格的下游需求对任何来源都一样）
    assert path is not None and not path.read_bytes().startswith(b"BWD1")


def test_payload_delta_is_landed_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """④ hub 硬塞了一份 delta（payload 里）⇒ 不额外走网，且**用之前**就已落地成整份。"""
    _new_raw, delta, cache = _produce_round(tmp_path)
    called: list[str] = []
    _forbid_download(monkeypatch, called)
    jd = _job(tmp_path)
    (jd / "init_weights.json").write_bytes(delta)  # hub 随包带来的就是这份容器

    path, src, wire = _resolve(jd, _sha(delta), blob_root=cache)

    assert src == "payload" and wire == 0 and called == []
    assert path is not None and not path.read_bytes().startswith(b"BWD1")
    assert json.loads(path.read_bytes())["format"] == "nn-weights-json"


def test_second_entry_does_not_declare_the_landed_file_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⑤ 同一 job 目录**第二次**进来：W0 那道闸不许把已经落地的整份判成坏字节。

    为什么必须钉：读侧落地是**就地**改写 `job_dir/init_weights.json`（下游 rollout argv 与
    `build_ppo` 都按这个**名字**取件），而声明 `init_weights_fp` 仍是**容器**的 sha ⇒ 第二次进来
    时 `sha256(文件) != fp` 必然成立。修复前它会走「坏字节：作废、改走 blob」那条路：删掉一份
    **已经可用**的整份（真走过网的还白付一次 ~380KB 下行；hub 侧拿不到那一刻更是把一轮本可跑的
    活干死）。判据用落地器写下的旁存件（`init_weights.delta` = 本轮那份容器）。
    """
    _new_raw, delta, cache = _produce_round(tmp_path)
    called: list[str] = []
    _forbid_download(monkeypatch, called)
    jd = _job(tmp_path)
    (jd / "init_weights.json").write_bytes(delta)

    first = _resolve(jd, _sha(delta), blob_root=cache)
    assert first[1] == "payload" and first[2] == 0

    second = _resolve(jd, _sha(delta), blob_root=cache)
    assert second[1] == "payload", "第二次进来仍要认这是自己落过地的那一份（不是坏字节）"
    assert second[2] == 0 and called == []
    assert second[0] is not None and not second[0].read_bytes().startswith(b"BWD1")
    assert (jd / "init_weights.delta").read_bytes() == delta, "旁存件仍是这份容器（两版都留）"
