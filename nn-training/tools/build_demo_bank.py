#!/usr/bin/env python3
"""Build a single demo bank npz from BC shards (one-time, reproducible).

**两种语料口径（2026-11-21 起）**：

* legacy（默认，逐字节旧行为）—— 读 `{obs,scalars,extra,actions,masks}.npy`：
  `obs (N,OBS_CHANNELS,BOARD,BOARD) u1`、`scalars (N,SCALAR_DIM) f4`、
  `extra (N,POLICY_EXTRA_DIM) f4`、`actions (N,2) i8 [move5,fire2]`、`masks (N,7) u1`。
* `--v6` —— 读 v6 语料（`export-godai-labels.ts --action-v6` 的产物）：
  `a_move.npy(0..3) / a_fire.npy(0..2) / a_hold.npy(0..1) / mask.npy(9)`。
  落盘仍是**契约规定的五个键**：`actions` 取 `[a_move, a_fire]`（各 2 列，消费者只用
  `actions[:, 0]` 当 move 标签，`a_hold` 不进 bank），`masks` 原样 9 列
  （`[move4 | fire3 | hold2]`）。
  ⚠ 掩码宽度必须与**本课动作空间**同宽：v6 课用 9 列、legacy 课用 7 列。混用会被
  `worker/ppo/engine._setup_demo` / `engine_paddle._prepare_demo` 当场拒收（2026-11-21 加的闸）
  —— 因为 7 列标签是 legacy 语义（0=STOP）、9 列是 v6（0=up），静默错位时 loss 照降。

**体积**：bank 会被整份物化到设备（obs 每帧 `OBS_CHANNELS*BOARD*BOARD` = 11,492 B）。
1M 帧 = **11.7 GB**（+scalars/extra 0.43 GB），装不下也传不动 ⇒ `--max-frames` 按**关分层等量抽样**
（每关均分，关内跨 shard 均匀取帧），默认 0 = 全收（老行为）。实测按 150–200k 预算（1.7–2.3 GB）。

**防守块（2026-11-21 加，`--defense-frames`）**：除了「按关分层」的普通块，再单独切一块
**基地有压力**的帧，两块的配额各自显式。为什么需要：

* NN 的败因 84–92% 是 `base_destroyed`，而语料里压力状态只占 19%（`s38>=0.1`）⇒ 均匀抽等于
  把"该学的那一面"稀释到 1/5。
* **子集里 27–30% 来自 `base_destroyed` 局**（教师自己也没守住的过程帧，与阈值几乎无关）
  ⇒ 默认只从 `--defense-outcome stage_clear`（教师守住/过关的局）里取。
* **不按动作筛**：实测教师在这些状态里「朝基地」vs「背离」是 **51.5% / 48.5%**（近抛硬币），
  按动作筛出来的只是"教师一半时候的选择"，不是更优策略，还会把 CE 变成教师身上不存在的
  确定性任务、与 RL 优势打架。要"只教回防"就是**偏好注入**而非模仿，那时应改奖励/动作约束，不在这里改。

筛选口径全部取自 obs 编码器的既有定义（不新造信号）：`s38`=基地压力（0..1）、
`s17·39`=到基地格数、`s24`=基地 HP；`--defense-p` / `--defense-cells` 给阈值。

维度一律从 `common.schema` 读（本脚本已因硬编码 9 漂过一次）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import house_parser, run_main
from common.schema import (
    BOARD,
    MASK_DIM,
    MASK_V6_DIM,
    OBS_CHANNELS,
    OBS_SCHEMA_MAJOR,
    POLICY_EXTRA_DIM,
    SCALAR_DIM,
)

NN_ROOT = Path(__file__).resolve().parent.parent  # nn-training/ (script lives in tools/)

#: `s17` 到基地距离的归一化分母是 `FIELD_DIAG = BOARD·CELL·1.5`（obs-encoder.ts:683）
#: ⇒ 格数 = `s17 × BOARD × 1.5`。写在这里而不是读 TS，因为 schema 维度本脚本也是同源镜像。
CELLS_PER_S17 = BOARD * 1.5
#: v6 move4 解码顺序（`src/nn/action-space-v6.ts::MOVE4_DECODE`）——只用于**报告**朝向，
#: 不参与筛选（见模块头"不按动作筛"）。
_DIRV = np.array([[0.0, -1.0], [0.0, 1.0], [-1.0, 0.0], [1.0, 0.0]], dtype=np.float32)


def _shard_arrays(d: Path, v6: bool) -> dict[str, np.ndarray]:
    """一个 shard 的五行原始数组（v6 时把 a_move/a_fire 拼回 2 列 actions）。"""
    if v6:
        ac = np.stack(
            [
                np.load(d / "a_move.npy").astype(np.int64),
                np.load(d / "a_fire.npy").astype(np.int64),
            ],
            axis=1,
        )
        mm = np.load(d / "mask.npy")
    else:
        ac = np.load(d / "actions.npy")
        mm = np.load(d / "masks.npy")
    return {
        # obs 走 mmap：1M 帧的语料里 96% 的字节在这一列上，而分层抽样每 shard 只取几十行。
        # 不 mmap 就要为取 60 帧读完整份 shard（实测这一步是整条构建的主要耗时）。
        "obs": np.load(d / "obs.npy", mmap_mode="r"),
        "scalars": np.load(d / "scalars.npy"),
        "extra": np.load(d / "extra.npy"),  # 旧 shard 缺 extra 在此响亮失败（须重导）
        "actions": ac,
        "masks": mm,
    }


def _check_shard(d: Path, a: dict[str, np.ndarray], mask_dim: int) -> None:
    o, s, ex, ac, mm = a["obs"], a["scalars"], a["extra"], a["actions"], a["masks"]
    assert o.shape[0] == s.shape[0] == ac.shape[0] == mm.shape[0] == ex.shape[0], d
    assert tuple(o.shape[1:]) == (OBS_CHANNELS, BOARD, BOARD), (
        d,
        o.shape,
        f"期望 (N,{OBS_CHANNELS},{BOARD},{BOARD})——旧 schema 语料须重导",
    )
    assert s.ndim == 2 and s.shape[1] == SCALAR_DIM, (
        d,
        s.shape,
        f"期望 (N,{SCALAR_DIM})——旧 schema 语料须重导",
    )
    assert ex.ndim == 2 and ex.shape[1] == POLICY_EXTRA_DIM, (
        d,
        ex.shape,
        f"期望 (N,{POLICY_EXTRA_DIM})——旧 schema 语料须重导",
    )
    assert ac.ndim == 2 and ac.shape[1] == 2, (d, ac.shape, "actions 必须是 (N,2)")
    assert mm.ndim == 2 and mm.shape[1] == mask_dim, (
        d,
        mm.shape,
        f"掩码宽必须是 {mask_dim}（legacy 7 / v6 9），实际 {mm.shape[1]}",
    )
    md_path = d / "manifest.json"
    assert md_path.is_file(), f"缺 manifest.json：{d}"
    md = json.loads(md_path.read_text(encoding="utf-8"))
    assert int(md.get("obsSchemaMajor", 0) or 0) >= OBS_SCHEMA_MAJOR, (
        d,
        md.get("obsSchemaMajor"),
        f"obsSchemaMajor < {OBS_SCHEMA_MAJOR}——旧目录须重导",
    )


def _general_quota(
    args: argparse.Namespace, meta: list[tuple[Path, int, int]], stages: list[int]
) -> dict[Path, int]:
    """普通块每 shard 取多少行。`--skip-general` ⇒ 全 0；`--max-frames` ⇒ 按关分层等量；否则全收。

    分层是**每关等量**，关内再按 shard 样本数比例分（整数除 + 余数给大 shard）；关内样本不足时
    余量补给同关其它 shard —— **不跨关搬**：跨关搬等于让简单的关挤掉难关的配额，
    把"按关分层"这件事悄悄废掉。
    """
    quota: dict[Path, int] = {}
    if args.skip_general:
        for d, _st, _n in meta:
            quota[d] = 0
        return quota
    if not (args.max_frames and args.max_frames > 0):
        for d, _st, n in meta:
            quota[d] = n
        return quota
    per_stage = args.max_frames // max(1, len(stages))
    for s in stages:
        grp = [(d, n) for d, st, n in meta if st == s]
        tot = max(1, sum(n for _, n in grp))
        take = min(per_stage, tot)
        assigned = 0
        for i, (d, n) in enumerate(grp):
            k = take * n // tot if i < len(grp) - 1 else take - assigned
            assigned += k
            quota[d] = min(k, n)
        # 关内样本不足时把余量补给同关其它 shard（保配额，不跨关搬）
        short = take - sum(quota[d] for d, _ in grp)
        if short > 0:
            for d, n in grp:
                if short <= 0:
                    break
                add = min(short, n - quota[d])
                quota[d] += add
                short -= add
    return quota


def _defense_selection(
    args: argparse.Namespace, meta: list[tuple[Path, int, int]], rng: np.random.Generator
) -> dict[Path, np.ndarray]:
    """防守块：逐 shard 扫 scalars 找候选帧，再按候选数比例分配配额。返回每 shard 的行号。

    只读 scalars（每 shard ~90 KB）；obs 仍走 mmap 只取被选中的行。
    `--defense-frames 0` ⇒ `{}`（老行为）。候选为 0 时**响亮失败**——静默产出空 bank 会让
    "这一课到底有没有教师项"变成训练完才知道的事。
    """
    def_idx: dict[Path, np.ndarray] = {}
    if args.defense_frames <= 0:
        return def_idx
    cand: dict[Path, np.ndarray] = {}
    def_cand = 0
    for d, _st, _n in meta:
        if args.defense_outcome:
            md = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            if str(md.get("outcome", "")) != args.defense_outcome:
                continue
        sc = np.load(d / "scalars.npy")
        if sc.shape[0] == 0:
            continue
        s38 = np.clip(sc[:, 38].astype(np.float32), 0.0, 1.0)
        cells = sc[:, 17].astype(np.float32) * CELLS_PER_S17
        m = np.flatnonzero((s38 >= args.defense_p) & (cells <= args.defense_cells))
        if m.size:
            cand[d] = m
            def_cand += int(m.size)
    if def_cand == 0:
        raise SystemExit(
            f"[demo-bank] 防守块无候选（s38>={args.defense_p} ∧ <= {args.defense_cells} 格"
            f"{' ∧ outcome=' + args.defense_outcome if args.defense_outcome else ''}）"
            "——阈值太严或 outcome 拼错"
        )
    # 按候选数比例分配（不是按 shard 均分：压力帧在难关本来就多，均分会让简单的关被重复抽）
    want = min(args.defense_frames, def_cand)
    picked = 0
    items = sorted(cand.items())
    for i, (d, m) in enumerate(items):
        k = round(want * m.size / def_cand) if i < len(items) - 1 else want - picked
        k = max(0, min(k, m.size))
        if k:
            # 候选内均匀铺开（候选本身是时序相邻的，等距取仍会成段 ⇒ 先洗再截）
            sel = np.sort(rng.permutation(m)[:k])
            def_idx[d] = sel
            picked += k
    # 补齐：小 shard 的 `round` 归零 + 末位被自身候选数截断 ⇒ 会欠；按剩余候选补到 want。
    if picked < want:
        for d, m in items:
            if picked >= want:
                break
            have = def_idx.get(d)
            rest = m if have is None else np.setdiff1d(m, have, assume_unique=True)
            if rest.size == 0:
                continue
            add = min(want - picked, rest.size)
            extra = np.sort(rng.permutation(rest)[:add])
            def_idx[d] = extra if have is None else np.union1d(have, extra)
            picked += add
    picked = sum(int(v.size) for v in def_idx.values())
    print(
        f"[demo-bank] 防守块候选 {def_cand} → 取 {picked}"
        f"（s38>={args.defense_p} ∧ <={args.defense_cells:g} 格"
        f"{' ∧ outcome=' + args.defense_outcome if args.defense_outcome else ''}）"
    )
    return def_idx


def main() -> None:
    ap = house_parser()
    ap.add_argument("--corpus", default=str(NN_ROOT / "data" / "human-x20-corpus"))
    ap.add_argument("--out", default=str(NN_ROOT / "data" / "human-x20-corpus" / "demo_bank.npz"))
    ap.add_argument(
        "--v6",
        action="store_true",
        help="v6 语料（a_move/a_fire/a_hold/mask(9)）；落盘 masks 仍是 9 列",
    )
    ap.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="0=全收；>0 = 按关分层等量抽样到该帧数（bank 会整份上设备，别给太大）",
    )
    ap.add_argument("--seed", type=int, default=20261121, help="抽样用的 RNG 种子")
    ap.add_argument(
        "--defense-frames",
        type=int,
        default=0,
        help="0=不切防守块（老行为）；>0 = 另外取这么多**基地有压力**的帧与普通块合并",
    )
    ap.add_argument("--defense-p", type=float, default=0.2, help="s38（基地压力）阈值")
    ap.add_argument(
        "--defense-cells", type=float, default=12.0, help="玩家到基地的格数上界（s17·39）"
    )
    ap.add_argument(
        "--defense-outcome",
        default="stage_clear",
        help="防守块只从该 outcome 的 shard 取（空串 = 不筛）；默认排除教师自己丢基地的局",
    )
    ap.add_argument(
        "--skip-general",
        action="store_true",
        help=(
            "只建防守块、不要普通块。**教师样本该只花在 rollout 采不到的状态上**：普通状态由"
            "自 rollout 覆盖（on-policy、advantage 已做完信用分配），而「别忘掉起点行为」这件事"
            "由 kickstart_ref 的 KL 缰绳负责 ⇒ 普通块是重复付费。"
        ),
    )
    args = ap.parse_args()

    if args.defense_frames > 0 and not args.v6:
        raise SystemExit(
            "[demo-bank] --defense-frames 需要 --v6：筛选信号 s38/s17 是 v7 obs 的 scalars 列，"
            "legacy 语料的列序不同（会静默按错列筛）"
        )

    mask_dim = MASK_V6_DIM if args.v6 else MASK_DIM
    shards = sorted(Path(args.corpus).glob("shard_*"))
    if not shards:
        raise SystemExit(f"no shards in {args.corpus}")

    # 先读一遍 manifest 拿到每 shard 的 (stage, nSamples)，用于分层配额。
    meta: list[tuple[Path, int, int]] = []
    for d in shards:
        md = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        meta.append((d, int(md["stage"]), int(md["nSamples"])))
    stages = sorted({s for _, s, _ in meta})

    # 每 shard 的取样数：先把"每关配额"按 shard 样本数比例分下去（整数 + 余数给大 shard）。
    quota = _general_quota(args, meta, stages)

    rng = np.random.default_rng(args.seed)

    # ── 防守块（可选）：逐 shard 扫 scalars 找候选帧，再按候选数比例分配配额 ──────────
    def_idx = _defense_selection(args, meta, rng)

    obs_l, sc_l, ex_l, ac_l, m_l = [], [], [], [], []
    total = 0
    kept_def = kept_gen = dropped_dup = 0
    def_stats = {"n": 0, "s38": 0.0, "cells": 0.0, "toward": 0, "fire": 0}
    for d in shards:
        k = quota[d]
        dsel = def_idx.get(d)
        if k <= 0 and dsel is None:
            continue
        a = _shard_arrays(d, args.v6)
        _check_shard(d, a, mask_dim)
        n = a["obs"].shape[0]
        gen = np.empty(0, dtype=np.int64)
        if k > 0:
            # 均匀铺开取（不是取前 k 条）：一局内决策帧是时序连续的，取段会引入时段偏置。
            gen = (
                np.unique(np.linspace(0, n - 1, k).round().astype(int))
                if k < n
                else np.arange(n)
            )
        if dsel is not None:
            # 防守块优先（它稀有）；普通块去重后再落盘，保证两块配额都可核。
            before = gen.size
            gen = np.setdiff1d(gen, dsel)
            dropped_dup += int(before - gen.size)
        idx = np.union1d(gen, dsel if dsel is not None else np.empty(0, dtype=np.int64))
        if idx.size == 0:
            continue
        kept_gen += int(gen.size)
        if dsel is not None:
            kept_def += int(dsel.size)
            sc_d = a["scalars"][dsel].astype(np.float32)
            cells_d = sc_d[:, 17].astype(np.float32) * CELLS_PER_S17
            mv = np.clip(a["actions"][dsel, 0].astype(np.int64), 0, 3)
            dot = _DIRV[mv, 0] * sc_d[:, 18] + _DIRV[mv, 1] * sc_d[:, 37]
            def_stats["n"] += int(dsel.size)
            def_stats["s38"] += float(sc_d[:, 38].sum())
            def_stats["cells"] += float(cells_d.sum())
            def_stats["toward"] += int((dot > 0.3).sum())
            def_stats["fire"] += int((a["actions"][dsel, 1] != 0).sum())
        obs_l.append(a["obs"][idx])
        sc_l.append(a["scalars"][idx].astype(np.float32))
        ex_l.append(a["extra"][idx].astype(np.float32))
        ac_l.append(a["actions"][idx].astype(np.int64))
        m_l.append(a["masks"][idx])
        total += len(idx)

    obs = np.concatenate(obs_l).astype(np.uint8)
    sc = np.concatenate(sc_l).astype(np.float32)
    ex = np.concatenate(ex_l).astype(np.float32)
    ac = np.concatenate(ac_l).astype(np.int64)
    mm = np.concatenate(m_l).astype(np.uint8)
    assert ac.shape[1] == 2, ac.shape
    assert mm.shape[1] == mask_dim, (mm.shape, mask_dim)
    # 抽样后打散：bank 是"每步均匀抽 per_mb 个"，若按关顺序落盘，前若干轮会全抽到同一关。
    perm = rng.permutation(total)
    obs, sc, ex, ac, mm = obs[perm], sc[perm], ex[perm], ac[perm], mm[perm]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, obs=obs, scalars=sc, extra=ex, actions=ac, masks=mm)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    mb = out.stat().st_size / 1e6
    dev_gb = total * (OBS_CHANNELS * BOARD * BOARD) / 1e9
    print(
        f"[demo-bank] v6={args.v6} shards={len(shards)} N={total} out={out} "
        f"{mb:.1f}MB sha={sha}"
    )
    print(
        f"[demo-bank] 上设备估算 obs 段 {dev_gb:.2f} GB"
        f"（+scalars {(total * SCALAR_DIM * 4) / 1e9:.2f} GB"
        f" +extra {(total * POLICY_EXTRA_DIM * 4) / 1e9:.2f} GB）"
    )
    print(
        "[demo-bank] keys=['actions', 'extra', 'masks', 'obs', 'scalars'] "
        f"obs{obs.shape} scalars{sc.shape} extra{ex.shape} "
        f"actions{ac.shape} masks{mm.shape}"
    )
    # 标签边缘（判断"这份 bank 教的是不是'永不开火'"+ move 先验）
    mv = np.bincount(ac[:, 0], minlength=5 if not args.v6 else 4) / max(1, total)
    print(f"[demo-bank] move 边缘 {np.round(mv, 4).tolist()}（legacy 5 类 / v6 4 类）")
    if args.defense_frames > 0:
        # 别叫 `n`：上面循环里的 `n` 是 shard 帧数（int），mypy 会按第一处赋值钉住类型。
        dn = int(def_stats["n"]) or 1
        print(
            f"[demo-bank] 防守块实测 n={def_stats['n']}（占 bank {def_stats['n'] / max(1, total):.1%}）"
            f" 普通块 n={kept_gen}（去重丢弃 {dropped_dup}）"
        )
        print(
            f"[demo-bank] 防守块画像 s38 均值 {def_stats['s38'] / dn:.3f} · 到基地均值 "
            f"{def_stats['cells'] / dn:.1f} 格 · 朝基地 {def_stats['toward'] / dn:.1%} · "
            f"fire!=0 {def_stats['fire'] / dn:.1%}（后两项是**报告**，不参与筛选）"
        )


if __name__ == "__main__":
    run_main(main)
