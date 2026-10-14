"""tests/remote/test_weights_delta.py — R1/M3 的权重 delta 容器（`plan/wire-diet.plan.md` §3 R1）。

钉住四件事（都是「没有它会静默出错」）：

  ① **往返**：`apply(pack(base, new), base)` 重建出的 JSON 与 `new` 在量化误差界内一致，且
     **非 params 键/键序原样保留**（`arch`/`num_params`/extra_meta 一个不丢）；
  ② **决定性**：同输入两遍 pack / 两遍 apply 都是**逐字节**相同（传输形态不许带随机性）；
  ③ **误差界**：相对 RMS ≤ 2e-4（plan §3 R1 实测 1.44e-4；`scale/2` 是硬上界）；
  ④ **响亮拒收**：魔数/头/长度/形态位/基准 sha/项结构/载荷余料 —— 一条都不许静默降级。

外加：形态位读法 `w_fmt_of` 三档（命中 / 缺键 / 脏值）与 `base_sha_of` 的只读头行为。
"""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import weights_delta as wd


def _params(rng: np.random.Generator, names: dict[str, tuple[int, ...]], *, amp: float) -> dict:
    return {n: rng.standard_normal(s).astype(np.float32) * amp for n, s in names.items()}


def _weights_json(params: dict, *, extra: dict | None = None) -> bytes:
    """一份 nn-weights-json（与 `save_weights_json` 同形：indent=2 + 同键序）。"""
    meta = {
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
    }
    if extra:
        meta.update(extra)
    return json.dumps(meta, indent=2).encode("utf-8")


SHAPES = {"conv.w": (4, 3, 3, 3), "fc.b": (16,), "head.w": (16, 165)}
BASE_SHA = "a" * 64


def _pair(*, amp: float = 2e-3, seed: int = 1) -> tuple[bytes, bytes]:
    """(base, new)：new = base + 一个「训练一步」量级的 Δ（amp ≈ plan 里的真实更新 RMS）。"""
    rng = np.random.default_rng(seed)
    base = _params(rng, SHAPES, amp=0.2)
    delta = _params(rng, SHAPES, amp=amp)
    new = {k: (base[k] + delta[k]).astype(np.float32) for k in base}
    return _weights_json(base, extra={"epoch": 3}), _weights_json(new, extra={"epoch": 3})


def _decoded(blob: bytes) -> dict:
    obj = json.loads(blob.decode("utf-8"))
    assert isinstance(obj, dict)
    return obj


def _arr(entry: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(entry["shape"])


def test_round_trip_rebuilds_within_the_quantization_bound() -> None:
    """① 往返：重建值在 `scale/2` 内，且非 params 键（含 extra_meta）与键序一字不差。"""
    base_json, new_json = _pair()
    raw = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    got = _decoded(wd.apply_weights_delta(base_json, raw))
    want = _decoded(new_json)
    assert list(got) == list(want), "键序必须与写端同形（params 插回原下标）"
    assert {k: v for k, v in got.items() if k != "params"} == {
        k: v for k, v in want.items() if k != "params"
    }, "arch/num_params/extra_meta 一个都不能丢"
    scale = float(json.loads(raw[4 + 4 :][: struct.unpack(">I", raw[4:8])[0]].decode("utf-8"))["scale"])
    worst = 0.0
    biggest = 0.0
    for n in want["params"]:
        w = _arr(want["params"][n])
        worst = max(worst, float(np.abs(_arr(got["params"][n]) - w).max()))
        biggest = max(biggest, float(np.abs(w).max()))
    # 硬上界 = 量化 (scale/2) + **重建那一步的 fp32 叠加舍入**（≤ 1 ulp：`base + q·scale`）。
    assert worst <= scale / 2 + float(np.spacing(np.float32(biggest))), (
        f"重建误差 {worst} 超出 scale/2={scale / 2} + fp32 舍入"
    )


def test_relative_rms_error_stays_at_the_measured_level() -> None:
    """③ 误差界：相对 RMS（误差 RMS / Δ RMS）≤ 2e-4 —— plan §3 R1 实测 1.44e-4。"""
    base_json, new_json = _pair(amp=2e-3)
    raw = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    got = _decoded(wd.apply_weights_delta(base_json, raw))
    b, n, g = (_decoded(base_json)["params"], _decoded(new_json)["params"], got["params"])
    err, num = 0.0, 0
    delta_sq = 0.0
    for name in n:
        d = (_arr(n[name]) - _arr(b[name])).astype(np.float64)
        e = (_arr(g[name]) - _arr(n[name])).astype(np.float64)
        err += float((e**2).sum())
        delta_sq += float((d**2).sum())
        num += d.size
    rel = (err / num) ** 0.5 / ((delta_sq / num) ** 0.5)
    assert rel <= 2e-4, f"相对 RMS {rel:.3e} 超出实测上界（plan §3 R1：1.44e-4）"


def test_packing_and_applying_are_byte_deterministic() -> None:
    """② 决定性：同输入两遍 ⇒ 同字节（传输形态不许带随机性/时间戳）。"""
    base_json, new_json = _pair()
    a = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    b = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    assert a == b
    assert wd.apply_weights_delta(base_json, a) == wd.apply_weights_delta(base_json, b)


def test_zero_delta_rebuilds_values_exactly() -> None:
    """Δ 全零（训练没动权重）⇒ scale 退 1.0、q 全 0 ⇒ 重建**逐值等于** base（不是「近似」）。"""
    base_json, _ = _pair()
    raw = wd.pack_weights_delta(base_json, base_json, base_sha=BASE_SHA)
    got = _decoded(wd.apply_weights_delta(base_json, raw))
    for name, entry in json.loads(base_json.decode("utf-8"))["params"].items():
        assert np.array_equal(_arr(got["params"][name]), _arr(entry)), name


def test_exact_band_reports_the_delta_and_checks_the_base_sha() -> None:
    """基准 sha 只在被要求时对账（exact 档）；identity 档（期望空）不拦但读数照给。"""
    base_json, new_json = _pair()
    raw = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    assert wd.base_sha_of(raw) == BASE_SHA
    wd.apply_weights_delta(base_json, raw, expect_base_sha=BASE_SHA)  # 不抛
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(base_json, raw, expect_base_sha="b" * 64)


def test_loud_rejections() -> None:
    """④ 一条都不许静默：魔数 / 长度 / 形态位 / 结构 / 载荷余料。"""
    base_json, new_json = _pair()
    raw = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(base_json, b"XXXX" + raw[4:])  # 魔数坏
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(base_json, raw[: len(raw) - 2])  # 载荷被截断
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(base_json, raw + b"\x00\x00")  # 尾部余料
    # `wfmt` 自述乱写一个（manifest 的形态位与之同名，容器自己也得自述对）
    hdr_len = int.from_bytes(raw[4:8], "big")
    hdr = json.loads(raw[8 : 8 + hdr_len].decode("utf-8"))
    hdr["wfmt"] = "delta-i8"
    forged = json.dumps(hdr, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(
            base_json, wd.W_DELTA_MAGIC + len(forged).to_bytes(4, "big") + forged + raw[8 + hdr_len :]
        )
    # 基准结构不符：换一份「少一个参数」的 base ⇒ 结构闸拦下（**不**按名字猜）
    thin = json.loads(base_json.decode("utf-8"))
    del thin["params"]["fc.b"]
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(json.dumps(thin, indent=2).encode("utf-8"), raw)
    # 形状不符：同集合、不同形状
    shaped = json.loads(base_json.decode("utf-8"))
    shaped["params"]["fc.b"]["shape"] = [4, 4]
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(json.dumps(shaped, indent=2).encode("utf-8"), raw)
    # base 不是 JSON / 不是 nn-weights-json
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(b"not json", raw)
    with pytest.raises(ProtocolError):
        wd.apply_weights_delta(b"{}", raw)


def test_packer_refuses_structural_gaps() -> None:
    """发端也不猜：新权重缺参数 / 多参数 / 形状变了 ⇒ 拒发（否则 hub 侧只剩猜）。"""
    base_json, new_json = _pair()
    short = json.loads(new_json.decode("utf-8"))
    del short["params"]["fc.b"]
    with pytest.raises(ProtocolError):
        wd.pack_weights_delta(base_json, json.dumps(short, indent=2).encode(), base_sha=BASE_SHA)
    extra = json.loads(new_json.decode("utf-8"))
    extra["params"]["surprise"] = dict(extra["params"]["fc.b"])
    with pytest.raises(ProtocolError):
        wd.pack_weights_delta(base_json, json.dumps(extra, indent=2).encode(), base_sha=BASE_SHA)
    wrong = json.loads(base_json.decode("utf-8"))
    wrong["params"]["head.w"]["shape"] = [1, 4]  # base 侧形状坏 ⇒ 自身 decode 就拦
    with pytest.raises(ProtocolError):
        wd.pack_weights_delta(json.dumps(wrong, indent=2).encode(), new_json, base_sha=BASE_SHA)


def test_w_fmt_of_is_the_single_read_point() -> None:
    """形态位读法：只有恰好等于 `W_DELTA_FMT` 才算命中；缺键 / 旧值 / 脏值一律回**旧形态**。"""
    assert wd.w_fmt_of({"w_fmt": wd.W_DELTA_FMT}) == wd.W_DELTA_FMT
    assert wd.w_fmt_of({}) == ""
    assert wd.w_fmt_of({"w_fmt": None}) == ""
    assert wd.w_fmt_of({"w_fmt": "delta-f16"}) == ""
    assert wd.w_fmt_of({"w_fmt": "delta-i16 "}) == ""
    assert wd.w_fmt_of(None) == ""


def test_base_sha_of_reads_only_the_header() -> None:
    """`base_sha_of` 只读头：截断到只剩头也能读出来；魔数不对 ⇒ 空串。"""
    base_json, new_json = _pair()
    raw = wd.pack_weights_delta(base_json, new_json, base_sha=BASE_SHA)
    hdr_len = int.from_bytes(raw[4:8], "big")
    assert wd.base_sha_of(raw[: 8 + hdr_len]) == BASE_SHA
    assert wd.base_sha_of(b"nope") == ""
