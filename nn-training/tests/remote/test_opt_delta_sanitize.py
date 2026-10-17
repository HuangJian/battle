"""tests/remote/test_opt_delta_sanitize.py — opt delta-f16 下溢消毒（2026-10-17）。

根因：跨机 opt 走 `delta-f16`（`(new−base).to(float16)`，无缩放），罕见弱特征参数的
`exp_avg_sq` 增量（~1e-11）被抹成 0，而 `exp_avg` 增量（~1e-4）存活 ⇒ 权威 opt 出现
**v=0 且 m≠0**。该特征整轮零梯度时，Adam 每步 `lr·(0.9^k·m)/(√0+ε)`，96 步轮和
`9·lr·m/ε = 32.6`（it10→11 实测）。修复 = 装载/重建时把 `exp_avg_sq` 抬回
`(|m|/K)²`（K=10）的安全下限；详见 `plan/opt-delta-f16-underflow-fix.plan.md`。

本文件钉四件事：

  ① **T1a 行为**：v=0 的 96 步零梯度轮和从 ~32.6 降到 ≤0.05（真 `torch.optim.Adam`，
     `step≈960`；`step=0` 会被 bias correction 抬到 ~98，不能用来核 32.6）；
  ② **T1b 根因**：base v=0、new v≈2.9e-11 时 `delta-f16` 的 v 项确实为 0；消毒器是安全网；
  ③ **T2 helper**：幂等 · 最坏健康态（|m|/√v=7.27）按位 no-op · 边界 · 非 Adam 节点跳过 ·
     元素计数 · amsgrad 显式跳过；
  ④ **T3 P0**：`train_core._load_opt_state` 抬 v 但**不回写**盘上 base；AST 守卫确认
     `run_training_core` 走这个 helper（全链要真 torch + 真课程，不在单测里跑）。

判据尽量走行为/集合/字节，不走源码文本（`tests/test_source_text_assert_budget.py`）。
"""

from __future__ import annotations

import ast
import json
import struct
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import opt_delta as od
from remote import train_core

BASE_SHA = "a" * 64
M = 2.415e-4
LR = 1.5e-4
K = 10.0


def _state(*, m: float = M, v: float = 0.0, step: float = 960.0, elems: int = 1) -> dict:
    """一份确定性的 Adam `state_dict()` 形状（step=960 ≈ it10 的更新步数）。"""
    return {
        "state": {
            0: {
                "step": torch.tensor(step),
                "exp_avg": torch.full((elems,), m),
                "exp_avg_sq": torch.full((elems,), v),
            }
        },
        "param_groups": [
            {
                "lr": LR,
                "betas": [0.9, 0.999],
                "eps": 1e-8,
                "weight_decay": 0.0,
                "amsgrad": False,
                "maximize": False,
                "foreach": None,
                "capturable": False,
                "differentiable": False,
                "fused": None,
                "decoupled_weight_decay": False,
                "params": [0],
            }
        ],
    }


def _round_trip(base: dict, new: dict, **kw) -> dict:
    return od.apply_opt_delta(base, od.pack_opt_delta(base, new, base_sha=BASE_SHA), **kw)


def _run_adam_96(state: dict) -> float:
    """把重建出来的 state 装进真 Adam，跑 96 步零梯度，返回参数总位移。"""
    p = torch.nn.Parameter(torch.zeros(1))
    opt = torch.optim.Adam([p], lr=LR, betas=(0.9, 0.999), eps=1e-8)
    opt.load_state_dict(state)
    before = float(p.item())
    for _ in range(96):
        p.grad = torch.zeros_like(p)
        opt.step()
    return abs(float(p.item()) - before)


# ═════════════════════════════ T1：行为与根因 ═════════════════════════════


def test_underflow_poison_is_neutralized() -> None:
    """T1a：v=0/m≠0 的 96 步零梯度轮和 ≤0.05（未消毒 32.601；消毒后 0.0107）。"""
    got = _round_trip(_state(v=0.0), _state(v=0.0))
    jump = _run_adam_96(got)
    assert jump <= 0.05, f"v=0 动量炸弹未被消毒：96 步轮和 {jump:.6f}（预期 ≤0.05）"


def _delta_payload(raw: bytes, suffix: str) -> bytes:
    """从 `BOD1` 容器里取某个 delta 项的原始 payload（独立解析，不调被测容器）。"""
    off = len(od.OPT_DELTA_MAGIC)
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
    off += hdr_len
    for it in hdr["items"]:
        if it["kind"] == "json":
            continue
        n = int(it["nbytes"])
        blob = raw[off : off + n]
        off += n
        if str(it["path"]).endswith(suffix):
            return blob
    raise AssertionError(f"容器里没有 {suffix} 项")


def test_f16_transport_loses_the_v_increment() -> None:
    """T1b：base v=0、new v≈2.9e-11 ⇒ f16 增量项为 0；消毒后 v 被抬到 m²/100。"""
    base = _state(v=0.0)
    raw = od.pack_opt_delta(base, _state(v=2.9e-11), base_sha=BASE_SHA)
    assert _delta_payload(raw, ".exp_avg_sq") == b"\x00\x00"
    got = od.apply_opt_delta(base, raw)
    assert float(got["state"][0]["exp_avg_sq"][0]) == pytest.approx((M / K) ** 2)


# ═════════════════════════════ T2：helper 语义 ═════════════════════════════


def test_sanitize_is_idempotent_and_counts_elements() -> None:
    st = _state(v=0.0, elems=2)
    assert od.sanitize_adam_sq(st) == 2
    want = torch.full((2,), (M / K) ** 2)
    assert torch.allclose(st["state"][0]["exp_avg_sq"], want)
    assert od.sanitize_adam_sq(st) == 0
    assert torch.allclose(st["state"][0]["exp_avg_sq"], want)


def test_sanitize_leaves_the_worst_healthy_state_alone() -> None:
    """最坏健康态 |m|/√v = 7.27 ⇒ v = m²/52.8 > m²/100，按位 no-op。"""
    ratio = 7.27
    st = _state(m=1.0, v=1.0 / ratio**2)
    before = st["state"][0]["exp_avg_sq"].clone()
    assert od.sanitize_adam_sq(st) == 0
    assert torch.equal(st["state"][0]["exp_avg_sq"], before)


def test_sanitize_boundary_and_non_adam_nodes() -> None:
    floor = (M / K) ** 2
    st = _state(v=floor * (1 - 1e-6))
    assert od.sanitize_adam_sq(st) == 1
    assert float(st["state"][0]["exp_avg_sq"][0]) == pytest.approx(floor)

    weird = {
        "state": {"0": {"exp_avg": torch.zeros(2), "exp_avg_sq": torch.zeros(3)}},
        "param_groups": [{"lr": 1e-4}],
        "junk": [1, {"exp_avg": 1.0}],
    }
    assert od.sanitize_adam_sq(weird) == 0


def test_sanitize_skips_amsgrad_nodes_instead_of_half_fixing() -> None:
    st = _state(v=0.0)
    st["state"][0]["max_exp_avg_sq"] = torch.zeros(1)
    assert od.sanitize_adam_sq(st) == 0
    assert float(st["state"][0]["exp_avg_sq"][0]) == 0.0


# ═════════════════════════════ T3：P0 装载路 ═════════════════════════════


def test_load_opt_state_sanitizes_and_keeps_the_base_pristine(tmp_path: Path) -> None:
    p = tmp_path / "opt.pt"
    torch.save(_state(v=0.0), p)
    loaded, raised = train_core._load_opt_state(p)
    assert raised == 1
    assert float(loaded["state"][0]["exp_avg_sq"][0]) == pytest.approx((M / K) ** 2)
    # 不消毒基座：盘上那份必须仍是 v=0（回传 delta 的 base 逐字不变）
    on_disk = torch.load(p, map_location="cpu")
    assert float(on_disk["state"][0]["exp_avg_sq"][0]) == 0.0


def test_train_core_restore_uses_the_sanitizing_helper() -> None:
    tree = ast.parse((ROOT / "remote" / "train_core.py").read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "run_training_core"
    )
    names = {
        n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert names >= {"_load_opt_state"}


# ═════════════════════════════ P1：落地日志 ═════════════════════════════


def test_apply_logs_one_line_when_it_raises_v() -> None:
    lines: list[str] = []
    got = _round_trip(_state(v=0.0), _state(v=0.0), log=lines.append)
    assert len(lines) == 1
    assert float(got["state"][0]["exp_avg_sq"][0]) == pytest.approx((M / K) ** 2)
