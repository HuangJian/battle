#!/usr/bin/env python
"""migrate_obs_channel.py — 旧 schema 学生权重 → 新 schema（补 obs 通道，stem 三段式）。

plan/r5-next.plan.md §3.4（v7 = `OBS_CHANNELS 16→17`，ch16 `dist_enemy`）：
CoordConv 的 stem 输入布局是 `[obs 0..15 | coord 16..17]`，新布局是
`[obs 0..15 | 新 obs 16 | coord 17..18]`。**只做前缀拷贝会把 coord 列当成新通道**
⇒ CoordConv 坏掉（旧权重把 coord 核放在第 16/17 个输入位，新权重在第 17/18 位）。

三段式（K 个新通道通用）：
    new[:, :old_obs]        = old[:, :old_obs]      # 旧 obs 原样
    new[:, old_obs:new_obs] = 0                     # drop0 —— 不是 randn*0.01
    new[:, new_obs:]        = old[:, old_obs:]      # coord 两列原样后移

零列 ⇒ **旧 schema 输入（新通道全 0）下 logits 与源模型逐位相同**（本脚本落盘前自检）。
fc / 动作头 / value 头不动：本波 `SCALAR_DIM` / `POLICY_EXTRA_DIM` / 动作空间均未变；
任何**其它**张量换形都视为「源不是同族旧档」——响亮拒绝，不静默随机 init。

用法（必须走 nn-py-safe.sh）：
  bash tools/githook/nn-py-safe.sh nn-training/worker/scripts/migrate_obs_channel.py \
    --from nn-training/weights/z2-r5guard/z2-r5guard.it500.*.json \
    --to   nn-training/weights/z2-r5nav/z2-r5nav.it0.json
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path
from typing import Any

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.cli import house_parser, run_main
from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM

if _ilu.find_spec("common") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _forward_outputs(model: Any, obs: Any, scalars: Any, extra: Any) -> list[Any]:
    """一次前向的全部头输出（legacy 3 元 / v6 4 元，按模型自己的契约）。"""
    import torch

    with torch.no_grad():
        return [t for t in model(obs, scalars, extra)]


def migrate(src: str, dst: str, *, seed: int = 20261101, verify: bool = True) -> dict[str, Any]:
    """按三段式迁移 stem 通道；`verify=True` 时在旧 schema 输入上自检逐位保真。"""
    import torch

    from worker.data.weights_io import load_state_into, load_weights_json, save_weights_json
    from worker.models.arch_meta import read_arch_kwargs
    from worker.models.student import PPOStudent

    # 源可能是**上一代 schema** ⇒ arch 清单按 warm-start 态读（生产路径仍严格）。
    kwargs = read_arch_kwargs(src, allow_legacy_schema=True)
    meta, params = load_weights_json(src, allow_legacy_schema=True)
    new_obs = int(OBS_CHANNELS)

    old_stem = params.get("stem.weight")
    if old_stem is None or getattr(old_stem, "ndim", 0) != 4:
        raise SystemExit(f"拒绝：{src} 缺 stem.weight（不是学生模型权重档）")
    old_obs = int(old_stem.shape[1]) - 2  # stem 输入 = obs 通道 + 2 coord
    if old_obs == new_obs:
        raise SystemExit(
            f"拒绝：源已有 {old_obs} 个 obs 通道 = 当前 schema {new_obs}（没有可迁移的通道）"
        )
    if old_obs > new_obs:
        raise SystemExit(
            f"拒绝：源 obs 通道 {old_obs} > 当前 {new_obs}（本脚本只做加通道，不做降维）"
        )
    arch_in = (meta.get("arch") or {}).get("in_ch")
    if arch_in is not None and int(arch_in) != old_obs:
        raise SystemExit(
            f"拒绝：arch.in_ch={arch_in} 与 stem 反推的 {old_obs} 不符（权重档自相矛盾）"
        )

    model = PPOStudent(**kwargs)
    not_loaded = load_state_into(model, src, allow_legacy_schema=True)
    # 未装载键只允许是 stem（换形的那一张）。缺别的键 = 源不是同族完整权重档
    # （静默随机 init 会把「迁移成功」伪装成事实）。
    other_missing = sorted(k for k in not_loaded if k != "stem.weight")
    if other_missing:
        raise SystemExit(
            f"拒绝：源缺/换形其它张量 {other_missing}（只支持 stem 纯通道迁移）"
        )
    state = model.state_dict()

    expected_stem = (int(old_stem.shape[0]), new_obs + 2, 3, 3)
    if tuple(state["stem.weight"].shape) != expected_stem:
        raise SystemExit(
            f"拒绝：目标 stem 形状 {tuple(state['stem.weight'].shape)} ≠ 预期 {expected_stem}"
        )

    # 除 stem 外**一张都不许换形**：换形 = 源不是同族旧档（漏迁 = 静默随机 init）。
    others = [
        k
        for k, v in params.items()
        if k != "stem.weight" and (k not in state or tuple(state[k].shape) != tuple(v.shape))
    ]
    if others:
        raise SystemExit(f"拒绝：除 stem 外还有换形/陌生张量 {sorted(others)}（只支持纯通道迁移）")

    with torch.no_grad():
        fresh = state["stem.weight"].clone()
        fresh[:, :old_obs] = old_stem[:, :old_obs]
        fresh[:, old_obs:new_obs] = 0.0  # drop0：逐位保真的关键
        fresh[:, new_obs:] = old_stem[:, old_obs:]
        model.stem.weight.copy_(fresh)
    new_stem = state["stem.weight"].detach()

    verified: bool | None = None
    max_abs = 0.0
    if verify:
        # 源模型 = 同架构 + 旧 in_ch；逐位装载后与迁移后的目标模型比 logits。
        torch.manual_seed(seed)
        src_model = PPOStudent(in_ch=old_obs, **kwargs)
        load_state_into(src_model, src, allow_legacy_schema=True)
        src_model.eval()
        model.eval()
        obs = torch.randint(0, 256, (1, old_obs, BOARD, BOARD), dtype=torch.uint8)
        # 旧 schema 输入 = 新通道全 0（迁移的核心断言；不是随机填）。
        pad = torch.zeros(1, new_obs - old_obs, BOARD, BOARD, dtype=torch.uint8)
        obs_new = torch.cat([obs, pad], dim=1)
        scalars = (torch.rand(1, model.scalar_dim) - 0.5) * 4
        extra = (
            (torch.rand(1, POLICY_EXTRA_DIM) - 0.25) * 1.5 if model.policy_extra else None
        )
        a = _forward_outputs(src_model, obs, scalars, extra)
        b = _forward_outputs(model, obs_new, scalars, extra)
        if len(a) != len(b):
            raise SystemExit(f"拒绝：自检头数不一致（源 {len(a)} / 目标 {len(b)}）")
        ok = True
        for x, y in zip(a, b, strict=True):
            if not torch.equal(x, y):
                ok = False
                max_abs = max(max_abs, float((x - y).abs().max()))
        verified = ok
        if not ok:
            raise SystemExit(
                f"拒绝：旧 schema 输入下 logits 未逐位一致（max|Δ|={max_abs:.3e}）——"
                "迁移表写错了，不要落盘"
            )

    save_weights_json(
        model,
        dst,
        extra_meta={
            "migrated_from": src,
            "migrated_obs_channels": {"from": old_obs, "to": new_obs},
            "migrated_stem_segments": "old_obs | zero(new) | coord(2)",
            "migrated_bit_exact": bool(verified) if verified is not None else None,
        },
    )
    return {
        "out": dst,
        "from_channels": old_obs,
        "to_channels": new_obs,
        "stem_shape": list(new_stem.shape),
        "bit_exact_on_old_schema": verified,
        "new_channel_col_norm": float(new_stem[:, old_obs:new_obs].abs().sum()),
    }


def main() -> None:
    ap = house_parser()
    ap.add_argument("--from", dest="src", required=True, help="旧 schema 学生权重（JSON）")
    ap.add_argument("--to", dest="dst", required=True, help="输出新 schema 起点权重（JSON）")
    ap.add_argument("--seed", type=int, default=20261101, help="自检输入随机种子（可复现）")
    ap.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过旧 schema 逐位自检（只建议在排障时用；正常迁移必须让它绿）",
    )
    args = ap.parse_args()
    info = migrate(args.src, args.dst, seed=args.seed, verify=not args.no_verify)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    run_main(main)
