"""tests/remote/test_opt_delta.py — opt delta 容器（R3 / M4，`plan/wire-diet.plan.md` §3 R3 / §12）。

钉住六件事（每条都对应「没有它会静默出错」）：

  ① **往返**：`pack(base, new)` → `apply(base, …)` 与 `new` 在 f16 量化界内一致（浮点张量）；
     `step` 与 `param_groups` **逐值精确**（它们不是连续量，量化会改语义）；
  ② **决定性**：同输入两次打包逐字节相同 —— 这是两侧对账（hub 重建 = worker 本机）的前提；
  ③ **真的变小**：容器 ≤ 整份 `torch.save` 的 60%（M4 的全部理由，plan §3 R3 实测 467 KB → 95 KB）；
  ④ **基准不符 ⇒ 拒收**：`base_sha` 对不上说明本机那份不是它的 base（换机/错轮/被 prune）；
  ⑤ **结构不一致 / 体损伤 ⇒ 拒收**：少项、多项、截断、尾部余料、魔数坏、`ofmt` 不认；
  ⑥ **缺 torch ⇒ 响亮拒收**：M4 硬约束②——绝不静默降级（那会把 delta 当权重落盘）。

摊平规则在用例里**独立重实现**（`_leaf_map`）：判据要能被另一份实现复现，不能靠被测模块
自己证明自己（AGENTS §8：codec/数据优先独立重实现对账）。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import opt_delta as od

BASE_SHA = "a" * 64


def _state(seed: int, *, n: int = 3, elems: int = 256) -> dict:
    """一份确定性的「`opt.state_dict()` 那一族」状态（含 `step` 与 `param_groups`）。"""
    g = torch.Generator().manual_seed(seed)
    st = {}
    for pid in range(n):
        st[str(pid)] = {
            "step": torch.tensor([7.0 + pid]),
            "exp_avg": torch.randn(elems, generator=g) * 3e-2,
            "exp_avg_sq": torch.rand(elems, generator=g) * 1e-4,
        }
    return {
        "state": st,
        "param_groups": [{"lr": 3e-4, "betas": [0.9, 0.95], "eps": 1e-8, "params": [0, 1, 2]}],
    }


def _leaf_map(obj, prefix: str = "") -> dict[str, object]:
    """独立重实现的摊平（**不调** `opt_delta._opt_items`）。"""
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_leaf_map(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_leaf_map(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def _pack(base: dict, new: dict, *, base_sha: str = BASE_SHA) -> bytes:
    return od.pack_opt_delta(base, new, base_sha=base_sha)


def test_round_trip_keeps_step_exact_and_momentum_within_f16() -> None:
    """① 往返：动量在 f16 界内，`step` / `param_groups` 逐值不变，且 `base` 不动。"""
    base, new = _state(1), _state(2)
    snapshot = base["state"]["0"]["exp_avg"].clone()
    got = od.apply_opt_delta(base, _pack(base, new), expect_base_sha=BASE_SHA)
    want_leaves, got_leaves = _leaf_map(new), _leaf_map(got)
    assert sorted(want_leaves) == sorted(got_leaves), "结构（路径集合）必须逐字相同"
    worst = 0.0
    for path, want in want_leaves.items():
        have = got_leaves[path]
        if torch.is_tensor(want):
            assert torch.is_tensor(have)
            if path.endswith("step"):
                assert torch.equal(have, want), f"{path} 必须逐值精确（非连续量不许量化）"
                continue
            assert have.shape == want.shape
            worst = max(worst, float((have.float() - want.float()).abs().max()))
        else:
            assert have == want, f"{path} 必须逐值精确"
    assert worst <= 6e-4, f"动量往返误差 {worst} 超出 f16 界（plan §3 R3 实测 max 5.43e-5）"
    assert torch.equal(base["state"]["0"]["exp_avg"], snapshot), "apply 不许改 base（新树）"


def test_pack_is_deterministic_and_smaller_than_full_state() -> None:
    """②③ 两次打包逐字节相同；容器 ≤ 整份 `torch.save` 的 60%。"""
    base, new = _state(1), _state(2)
    one, two = _pack(base, new), _pack(base, new)
    assert one == two, "打包必须决定性（否则 hub 重建与 worker 本机无从对账）"
    buf = io.BytesIO()
    torch.save(new, buf)
    full = len(buf.getvalue())
    assert len(one) <= 0.6 * full, f"delta 容器 {len(one)} 相对整份 {full} 不够小"


def test_apply_refuses_base_sha_mismatch() -> None:
    """④ 基准不符 ⇒ 拒收（换了机 / 错了轮 / 被 prune 的那份不是它的 base）。"""
    base, new = _state(1), _state(2)
    raw = _pack(base, new)
    assert od.apply_opt_delta(base, raw, expect_base_sha=BASE_SHA)  # 对得上就该过
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, raw, expect_base_sha="b" * 64)


def test_apply_refuses_structure_mismatch_and_body_damage() -> None:
    """⑤ 少项 / 多项 / 截断 / 尾部余料 / 魔数坏 / ofmt 不认 —— 全部响亮。"""
    base, new = _state(1), _state(2)
    raw = _pack(base, new)
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(_state(1, n=2), raw)  # 本机少一组参数
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, raw + b"x")  # 尾部余料
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, raw[:-3])  # 截断
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, b"XXXX" + raw[4:])  # 魔数坏
    with pytest.raises(ProtocolError):
        od.pack_opt_delta(base, {**_state(3), "extra": {"0": torch.zeros(2)}}, base_sha=BASE_SHA)
    # `ofmt` 是 manifest 的形态位（兼容矩阵 §4），容器自己乱写一个也得被拒
    hdr_len = int.from_bytes(raw[4:8], "big")
    hdr = json.loads(raw[8 : 8 + hdr_len].decode("utf-8"))
    hdr["ofmt"] = "delta-i16"
    forged_hdr = json.dumps(hdr, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    forged = od.OPT_DELTA_MAGIC + len(forged_hdr).to_bytes(4, "big") + forged_hdr + raw[8 + hdr_len :]
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, forged)


def test_missing_torch_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """⑥ 缺 torch ⇒ 响亮拒收（M4 硬约束②；不许静默把 delta 当权重落盘）。"""
    base, new = _state(1), _state(2)
    raw = _pack(base, new)
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ProtocolError):
        od.apply_opt_delta(base, raw)
