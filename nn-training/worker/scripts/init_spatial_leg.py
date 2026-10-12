#!/usr/bin/env python
"""init_spatial_leg.py — 为 policy-spatial-head 的腿 A/腿 B/对照 A0 造起点权重（v4 S0-c warm-start 态）。

plan/policy-spatial-head.plan.md §4 Step 4/5：各腿从 hu150 起跑——
  · 腿 A：主干/fc/value 加载旧腿；走位/开火头换 165 输入并**迁移列**（旧 137 列原位拷）；
  · 腿 B：同上 + 空间塔（spatial_proj/spatial_fc 全新初始化），头 189；
  · A0（对照臂）：**保持旧架构**（头 128、无塔）——hu150 的 v3 文件在 v4 loader 下被
    schema 门拒收（`build_ppo`/`load_state_into` 全链严格），故此处做**元数据迁移**
    （全量装载、`warmstart_missing` 应为空，产出 v4 文件供课程 `bc=` 直接使用）。

产物 = 一份新的 `nn-training/weights/<leg>/<leg>.it0...json`（arch 带 policyExtra/
spatialTower 标志；A0 档 = 旧架构标志）+ `warmstart_missing` 入 meta（S0-c 硬要求①）。
课程 `bc=` 指到这份文件后，下游（build_ppo / kickstart ref / TS rollout）按 arch 元数据
自动构建对应架构（架构身份的单一来源 = 权重文件，既有契约不变）。

obs-v5（plan/obs-v5-vector-gap.plan.md §2.7）：起点宽了 —— pf 137→165（腿 A）/ 151→189
（腿 B）、fc 94→104、value 128→165/189。loader 对**换形**张量只会整块跳过（随机 init），
而这里要的是**列迁移**：旧列逐列拷进新张量的对应位置，新列用 `normal_(0,0.01)` 重初始化
（尺度见 `student.py::_init_value_head` 的说明）。
  · 腿 A：fc 拷 94 列；move/fire 拷 137 列（hidden 128 + 旧 extra 9）；value 拷**前 128 列**
    （旧 value 只吃 hidden，语义仍是 hidden）；
  · 腿 B：**分段映射**（不是平铺拷！）——旧 `[tower 0–111 | scalars 112–141 | extra 142–150]`
    → 新 `[tower 0–111 | scalars 112–151 | extra 152–188]`：拷 [0..111] + [112..141]
    + [142..150]→[152..160]，其余重 init（新 scalars s30–39 占 [142..151]、新 extra 占
    [161..188]）；value 头**全重 init**（旧吃 h、新吃 tower 起头的 pf，无对应前缀）。
    平铺拷 151 会把旧 extra 灌进 s30–s38 = 静默错位（评审 F2）。

⚠ warm-start 是**显式开关**（`--allow-partial-init`）：不写就拒绝旧 schema 权重
（strict/部署态），防静默随机初始化（plan §4-S0c 三态表）。

用法（必须走 nn-py-safe.sh；下例即 x25 的起始腿：x24 峰 burst-it220 → v5 腿 A）：
  bash tools/githook/nn-py-safe.sh nn-training/worker/scripts/init_spatial_leg.py \
    --from nn-training/weights/x24-stack7-burst/x24-stack7-burst.it220.*.json \
    --to nn-training/weights/x25-obs-v5/x25-obs-v5.it0.json \
    --policy-extra --allow-partial-init

  · 源可以是任何「旧 schema」权重：v3 的 hu150 系（legacy 头 128）或 v4 的 leg A 腿
    （头 137 = hidden 128 + 旧 extra 9，value 128）——两种都靠**偏移表**而不是绝对宽识别；
  · 腿 B 加 --spatial-tower（头 189）；A0 对照组用 --legacy-arch 代替两者。
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
from common.schema import POLICY_EXTRA_DIM, SCALAR_DIM

if _ilu.find_spec("common") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # nn-training/（common 的家）


# ════════════════════════════════════════════════════════════════
# obs-v5 列迁移表（plan/obs-v5-vector-gap.plan.md §2.7）
# ════════════════════════════════════════════════════════════════
# 三种模式：
#   · ("drop", n) —— 新张量尾巴上多了 n 列（新 scalars/新 extra）：拷前 `宽−n` 列；
#     用 schema 差值而非写死绝对宽 ⇒ 不随 h/head_hidden 扫参失效；
#   · ("segments", 表) —— 旧布局与新布局**不是**同一段头的（腿 B 的 scalars/extra 夹层）：
#     逐段 `(src_start, dst_start, count)` 搬；
#   · 表里不列的名字 = 不迁移（腿 B 的 value 头：旧吃 h、新吃 tower 起头的 pf ⇒ 全重 init）。
_SCALAR_DELTA = SCALAR_DIM - 30  # v4 → v5：+10（s30–s39）
_EXTRA_DELTA = POLICY_EXTRA_DIM - 9  # v4 → v5：+28（extra 9–36）
# 旧 pf = 腿 B `[tower 0–111 | scalars 112–141 | extra 142–150]`（151 列）；
# 新 pf = `[tower 0–111 | scalars 112–151 | extra 152–188]`（189 列）⇒ 三段映射。
_LEG_B_SEGMENTS: tuple[tuple[int, int, int], ...] = (
    (0, 0, 112),  # tower：原位
    (112, 112, 30),  # scalars s0–s29：原位（新 s30–s39 落在 [142..151]，重 init）
    (142, 152, 9),  # 旧 extra 0–8 → 新 extra 0–8（新 extra 9–36 落在 [161..188]，重 init）
)
_MIGRATION_LEGACY: dict[str, tuple[str, Any]] = {
    "fc.weight": ("drop", _SCALAR_DELTA),  # pooled+scalars 宽了 10 列
}
_MIGRATION_LEG_A: dict[str, tuple[str, Any]] = {
    "fc.weight": ("drop", _SCALAR_DELTA),
    "move_head.weight": ("drop", _EXTRA_DELTA),  # hidden+extra：137 → 165
    "fire_head.weight": ("drop", _EXTRA_DELTA),
    "value_head.weight": ("drop", POLICY_EXTRA_DIM),  # 旧只吃 hidden 128 ⇒ 拷新头前 128 列
}
_MIGRATION_LEG_B: dict[str, tuple[str, Any]] = {
    "fc.weight": ("drop", _SCALAR_DELTA),
    "move_head.weight": ("segments", _LEG_B_SEGMENTS),
    "fire_head.weight": ("segments", _LEG_B_SEGMENTS),
}


def _migrate_columns(
    model: Any,
    params: dict[str, Any],
    *,
    table: dict[str, tuple[str, Any]],
    gen: Any,
) -> dict[str, dict[str, Any]]:
    """按冻结表把旧列拷进新张量、新列 `normal_(0,0.01)` 重初始化（原地改 `model`）。

    · 同类同形（`old.shape == new.shape`）⇒ 跳过：loader 已整块装载，不是「部分迁移」；
    · 形状不符（既不是同形、也不是「宽−n / 分段」两张合法形态）⇒ **响亮拒绝**
      （静默跳过 = 旧 extra 灌进 scalars 那类错位，评审 F2）。
    返回 {张量名: {old_cols, mode, segments}} 供 meta 记账（`warmstart_partial`）。
    """
    import torch

    state = model.state_dict()
    done: dict[str, dict[str, Any]] = {}
    for name, (mode, spec) in table.items():
        new = state.get(name)
        old = params.get(name)
        if new is None or old is None or old.shape == new.shape:
            continue
        if old.ndim != 2 or old.shape[0] != new.shape[0]:
            raise SystemExit(
                f"拒绝：{name} 形状不支持 obs-v5 列迁移"
                f"（源 {tuple(old.shape)} → 目标 {tuple(new.shape)}）"
            )
        with torch.no_grad():
            fresh = new.clone()
            keep = torch.zeros(new.shape[1], dtype=torch.bool)
            if mode == "drop":
                old_cols = new.shape[1] - int(spec)
                if old.shape[1] != old_cols:
                    raise SystemExit(
                        f"拒绝：{name} 旧列数 {old.shape[1]} ≠ 预期 {old_cols}"
                        f"（新宽 {new.shape[1]} − 新列 {spec}）"
                    )
                fresh[:, :old_cols] = old
                keep[:old_cols] = True
            else:
                old_cols = sum(count for _, _, count in spec)
                src_total = max(src_start + count for src_start, _, count in spec)
                if old.shape[1] != src_total:
                    raise SystemExit(
                        f"拒绝：{name} 旧列数 {old.shape[1]} ≠ 分段表覆盖的 {src_total}（腿 B 三段映射）"
                    )
                for src_start, dst_start, count in spec:
                    fresh[:, dst_start : dst_start + count] = old[:, src_start : src_start + count]
                    keep[dst_start : dst_start + count] = True
            idx = torch.nonzero(~keep, as_tuple=False).flatten()
            fresh[:, idx] = torch.randn(len(idx), generator=gen, dtype=fresh.dtype) * 0.01
            new.copy_(fresh)
        done[name] = {
            "old_cols": old_cols,
            "mode": mode,
            "segments": [list(s) for s in spec] if mode == "segments" else None,
        }
    return done


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
    # obs-v5 列迁移（§2.7）：换形张量不是「缺失」，而是「拷旧列 + 新列 0.01 初始化」。
    table = _MIGRATION_LEG_B if spatial_tower else _MIGRATION_LEG_A if policy_extra else _MIGRATION_LEGACY
    _, params = load_weights_json(src, allow_legacy_schema=True)
    gen = torch.Generator().manual_seed(seed)  # 与模型 init 分开 ⇒ 新列可复现
    partial = _migrate_columns(model, params, table=table, gen=gen)
    not_loaded = [k for k in not_loaded if k not in partial]
    if legacy_arch and not_loaded:
        # A0 的契约 = 「hu150 全量迁移」：缺任何键都说明起点/形状不对，拒绝落盘
        # （静默随机初始化会让 A0 与 A 的"唯一差别"不再唯一）。列迁移之后才判。
        raise SystemExit(f"拒绝：--legacy-arch 要求全量装载，但缺 {sorted(not_loaded)}")
    arch = model.arch()
    save_weights_json(
        model,
        dst,
        extra_meta={
            "warmstart_from": src,
            "warmstart_missing": not_loaded,
            "warmstart_partial": partial,
            "warmstart_seed": seed,
        },
    )
    meta, _ = load_weights_json(dst)
    return {
        "out": dst,
        "arch": arch,
        "params": param_count(model),
        "warmstart_missing": not_loaded,
        "warmstart_partial": sorted(partial),
        "schema_major": meta.get("schema_major"),
    }


def main() -> None:
    ap = house_parser()
    ap.add_argument("--from", dest="src", required=True, help="起点权重（hu150 系，v3 schema）")
    ap.add_argument("--to", dest="dst", required=True, help="输出起点权重路径")
    ap.add_argument("--policy-extra", action="store_true", help="腿 A 档（头 165）")
    ap.add_argument("--spatial-tower", action="store_true", help="腿 B 档（塔 + 头 189）")
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
        raise SystemExit("拒绝：--spatial-tower 需要 --policy-extra（头 189 含 extra 37）")

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
    run_main(main)
