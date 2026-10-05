#!/usr/bin/env python
"""init_spatial_leg.py — 为 policy-spatial-head 的腿 A/腿 B/对照 A0 造起点权重（v4 S0-c warm-start 态）。

plan/policy-spatial-head.plan.md §4 Step 4/5：各腿从 hu150 起跑——
  · 腿 A：主干/fc/value 加载 hu150；走位/开火头换 137 输入并**重新初始化**；
  · 腿 B：同上 + 空间塔（spatial_proj/spatial_fc 全新初始化），头 151；
  · A0（对照臂）：**保持旧架构**（头 128、无塔）——hu150 的 v3 文件在 v4 loader 下被
    schema 门拒收（`build_ppo`/`load_state_into` 全链严格），故此处做**元数据迁移**
    （全量装载、`warmstart_missing` 应为空，产出 v4 文件供课程 `bc=` 直接使用）。

产物 = 一份新的 `nn-training/weights/<leg>/<leg>.it0...json`（arch 带 policyExtra/
spatialTower 标志；A0 档 = 旧架构标志）+ `warmstart_missing` 入 meta（S0-c 硬要求①）。
课程 `bc=` 指到这份文件后，下游（build_ppo / kickstart ref / TS rollout）按 arch 元数据
自动构建对应架构（架构身份的单一来源 = 权重文件，既有契约不变）。

⚠ warm-start 是**显式开关**（`--allow-partial-init`）：不写就拒绝旧 schema 权重
（strict/部署态），防静默随机初始化（plan §4-S0c 三态表）。

用法（必须走 nn-py-safe.sh）：
  bash tools/githook/nn-py-safe.sh nn-training/worker/scripts/init_spatial_leg.py \
    --from nn-training/weights/x20-adv-hurt/x20-adv-hurt.it150.*.json \
    --to nn-training/weights/spatial-legA/spatial-legA.it0.json \
    --policy-extra --allow-partial-init

  （腿 B 加 --spatial-tower；A0 档用 --legacy-arch 代替两者）
"""

from __future__ import annotations

import argparse
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from typing import Any

if _ilu.find_spec("common") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # nn-training/（common 的家）


def convert(
    src: str,
    dst: str,
    *,
    policy_extra: bool,
    spatial_tower: bool,
    legacy_arch: bool,
    seed: int = 20261005,
) -> dict[str, Any]:
    """造起点权重（腿 A/B 新架构档 或 A0 旧架构迁移档）；返回落盘回执。

    三档互斥：`legacy_arch` | `policy_extra`（± `spatial_tower`）。旧 schema 只在此处
    显式放行（`allow_legacy_schema=True`，S0-c warm-start 态）；A0 档要求全量命中
    （`warmstart_missing` 为空——旧架构与 hu150 逐键同形，不允许静默缺键）。
    """
    import torch

    from worker.data.weights_io import load_state_into, load_weights_json, save_weights_json
    from worker.models.student import PPOStudent, param_count

    if legacy_arch and (policy_extra or spatial_tower):
        raise SystemExit("拒绝：--legacy-arch 与 --policy-extra/--spatial-tower 互斥（A0 档保持旧架构）")
    if not legacy_arch and not policy_extra and not spatial_tower:
        raise SystemExit("拒绝：至少要 --legacy-arch、--policy-extra 或 --spatial-tower 之一")

    torch.manual_seed(seed)
    model = PPOStudent(policy_extra=policy_extra, spatial_tower=spatial_tower)
    not_loaded = load_state_into(model, src, allow_legacy_schema=True)
    if legacy_arch and not_loaded:
        # A0 的契约 = 「hu150 全量迁移」：缺任何键都说明起点/形状不对，拒绝落盘
        # （静默随机初始化会让 A0 与 A 的"唯一差别"不再唯一）。
        raise SystemExit(f"拒绝：--legacy-arch 要求全量装载，但缺 {sorted(not_loaded)}")
    arch = model.arch()
    save_weights_json(
        model,
        dst,
        extra_meta={
            "warmstart_from": src,
            "warmstart_missing": not_loaded,
            "warmstart_seed": seed,
        },
    )
    meta, _ = load_weights_json(dst)
    return {
        "out": dst,
        "arch": arch,
        "params": param_count(model),
        "warmstart_missing": not_loaded,
        "schema_major": meta.get("schema_major"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="起点权重（hu150 系，v3 schema）")
    ap.add_argument("--to", dest="dst", required=True, help="输出起点权重路径")
    ap.add_argument("--policy-extra", action="store_true", help="腿 A 档（头 137）")
    ap.add_argument("--spatial-tower", action="store_true", help="腿 B 档（塔 + 头 151）")
    ap.add_argument(
        "--legacy-arch",
        action="store_true",
        help="A0 档：保持旧架构（头 128、无塔），v3→v4 全量迁移（warmstart_missing 必为空）",
    )
    ap.add_argument(
        "--allow-partial-init",
        action="store_true",
        help="显式允许部分装载（缺失/换形张量随机初始化）——不写即拒绝",
    )
    ap.add_argument("--seed", type=int, default=20261005, help="新张量随机初始化种子（可复现）")
    args = ap.parse_args()

    if not args.allow_partial_init:
        raise SystemExit(
            "拒绝：warm-start 必须显式 --allow-partial-init（S0-c 三态表；防静默随机初始化）"
        )
    if args.spatial_tower and not args.policy_extra:
        raise SystemExit("拒绝：--spatial-tower 需要 --policy-extra（头 151 含 extra 9）")

    info = convert(
        args.src,
        args.dst,
        policy_extra=args.policy_extra,
        spatial_tower=args.spatial_tower,
        legacy_arch=args.legacy_arch,
        seed=args.seed,
    )
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
