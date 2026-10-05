#!/usr/bin/env python
"""init_spatial_leg.py — 为 policy-spatial-head 的腿 A/腿 B 造起点权重（v4 S0-c warm-start 态）。

plan/policy-spatial-head.plan.md §4 Step 4/5：两条腿都从 hu150 起跑——
  · 腿 A：主干/fc/value 加载 hu150；走位/开火头换 137 输入并**重新初始化**；
  · 腿 B：同上 + 空间塔（spatial_proj/spatial_fc 全新初始化），头 151。

产物 = 一份新的 `nn-training/weights/<leg>/<leg>.it0...json`（arch 带 policyExtra/
spatialTower 标志）+ run manifest 记 `warmstart_missing`（S0-c 硬要求①）。
课程 `bc=` 指到这份文件后，下游（build_ppo / kickstart ref / TS rollout）按 arch 元数据
自动构建新架构（架构身份的单一来源 = 权重文件，既有契约不变）。

⚠ warm-start 是**显式开关**（`--allow-partial-init`）：不写就拒绝旧 schema 权重
（strict/部署态），防静默随机初始化（plan §4-S0c 三态表）。

用法（必须走 nn-py-safe.sh）：
  bash tools/githook/nn-py-safe.sh nn-training/worker/scripts/init_spatial_leg.py \
    --from nn-training/weights/x20-adv-hurt/x20-adv-hurt.it150.*.json \
    --to nn-training/weights/spatial-legA/spatial-legA.it0.json \
    --policy-extra --allow-partial-init

  （腿 B 加 --spatial-tower）
"""

from __future__ import annotations

import argparse
import importlib.util as _ilu
import json
import sys
from pathlib import Path

if _ilu.find_spec("common") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # nn-training/（common 的家）

import torch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="起点权重（hu150 系，v3 schema）")
    ap.add_argument("--to", dest="dst", required=True, help="输出起点权重路径")
    ap.add_argument("--policy-extra", action="store_true", help="腿 A 档（头 137）")
    ap.add_argument("--spatial-tower", action="store_true", help="腿 B 档（塔 + 头 151）")
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
    if not args.policy_extra and not args.spatial_tower:
        raise SystemExit("拒绝：至少要 --policy-extra 或 --spatial-tower（否则请直接复制起点权重）")
    if args.spatial_tower and not args.policy_extra:
        raise SystemExit("拒绝：--spatial-tower 需要 --policy-extra（头 151 含 extra 9）")

    from worker.data.weights_io import load_state_into, load_weights_json, save_weights_json
    from worker.models.student import PPOStudent, param_count

    torch.manual_seed(args.seed)
    model = PPOStudent(policy_extra=args.policy_extra, spatial_tower=args.spatial_tower)
    not_loaded = load_state_into(model, args.src, allow_legacy_schema=True)
    arch = model.arch()
    save_weights_json(
        model,
        args.dst,
        extra_meta={
            "warmstart_from": args.src,
            "warmstart_missing": not_loaded,
            "warmstart_seed": args.seed,
        },
    )
    meta, _ = load_weights_json(args.dst)
    print(
        json.dumps(
            {
                "out": args.dst,
                "arch": arch,
                "params": param_count(model),
                "warmstart_missing": not_loaded,
                "schema_major": meta.get("schema_major"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
