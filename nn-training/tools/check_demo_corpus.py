#!/usr/bin/env python3
"""check_demo_corpus.py — v3 → v5 人类语料逐元素对账（可复跑）。

用途（plan/y22-phaseB-demo-data.plan.md §3-S3）：S1 重导后、S2 建库前，
把 v5 目录与旧 v3 目录按同名 shard 逐项对齐。任何一条红都表示「这批决策点
与当年不是同一批」，此时**不许建库**。

为什么逐元素而不是「分布差 <0.5pp」：决策谓词自 2026-09-27 未改、World/
Simulation/replay 自 2026-09-29 未改，v5 只在 scalars 末尾追加 s30–s39
（schema 注释：0–29 序号/语义一字不动）。所以 scalars[:30] / obs / actions /
masks / conditions 应逐字节相同；差 0.1pp 也是信号，不是噪声。

用法（仓根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/check_demo_corpus.py \
        --old nn-training/data/human-x20-corpus \
        --new nn-training/data/human-x20-corpus-v5 \
        --report tmp/y22-corpus-check.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

NN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NN_ROOT))

from common.cli import house_parser, run_main
from common.schema import OBS_SCHEMA_MAJOR, POLICY_EXTRA_DIM, SCALAR_DIM

#: 这一批语料的已知规模（plan §1 实测；换语料时应显式改这里并重跑报告）。
EXPECTED_N = 101153
#: 这一批语料的已知击杀总数（159×20 + 1×21）。
EXPECTED_KILLS = 3201


def _shards(root: Path) -> dict[str, Path]:
    return {p.name: p for p in sorted(root.glob("shard_*")) if p.is_dir()}


def _load_npy(d: Path, name: str) -> np.ndarray:
    return np.asarray(np.load(d / name))


def _fail(failures: list[str], msg: str) -> None:
    failures.append(msg)
    print(f"[FAIL] {msg}")


def main() -> int:
    ap = house_parser(description="v3 → v5 人类语料逐元素对账")
    ap.add_argument("--old", default=str(NN_ROOT / "data" / "human-x20-corpus"))
    ap.add_argument("--new", default=str(NN_ROOT / "data" / "human-x20-corpus-v5"))
    ap.add_argument("--report", default="", help="可选：把结果写成 JSON")
    args = ap.parse_args()

    old_root = Path(args.old)
    new_root = Path(args.new)
    old = _shards(old_root)
    new = _shards(new_root)
    failures: list[str] = []

    if len(old) != 160:
        _fail(failures, f"旧目录 shard 数 {len(old)} != 160")
    if set(old) != set(new):
        missing = sorted(set(old) - set(new))
        extra = sorted(set(new) - set(old))
        _fail(failures, f"shard 集合不等：缺 {missing[:5]}… 多 {extra[:5]}…")
    dup = sorted(n for n in new if n.endswith("_2"))
    if dup:
        _fail(failures, f"出现重名后缀 shard（_cal 撞名？）：{dup[:5]}")

    total_n = 0
    total_kills = 0
    old_total_n = 0
    counts_old = {"move": np.zeros(5, dtype=np.int64), "fire": np.zeros(2, dtype=np.int64)}
    counts_new = {"move": np.zeros(5, dtype=np.int64), "fire": np.zeros(2, dtype=np.int64)}
    checked = 0

    for name in sorted(set(old) & set(new)):
        od, nd = old[name], new[name]
        om = json.loads((od / "manifest.json").read_text(encoding="utf-8"))
        nm = json.loads((nd / "manifest.json").read_text(encoding="utf-8"))
        old_total_n += int(om["nSamples"])
        total_n += int(nm["nSamples"])
        total_kills += int(nm.get("kills", 0) or 0)

        if int(om["nSamples"]) != int(nm["nSamples"]):
            _fail(failures, f"{name}: nSamples {nm['nSamples']} != {om['nSamples']}")
            continue
        if int(nm.get("obsSchemaMajor", 0) or 0) != OBS_SCHEMA_MAJOR:
            _fail(failures, f"{name}: obsSchemaMajor {nm.get('obsSchemaMajor')} != {OBS_SCHEMA_MAJOR}")
        if int(nm.get("kills", -1)) != int(om.get("kills", -2)):
            _fail(failures, f"{name}: kills {nm.get('kills')} != {om.get('kills')}")

        oobs = _load_npy(od, "obs.npy")
        nobs = _load_npy(nd, "obs.npy")
        osc = _load_npy(od, "scalars.npy")
        nsc = _load_npy(nd, "scalars.npy")
        oa = _load_npy(od, "actions.npy")
        na = _load_npy(nd, "actions.npy")
        omk = _load_npy(od, "masks.npy")
        nmk = _load_npy(nd, "masks.npy")
        ocd = _load_npy(od, "conditions.npy")
        ncd = _load_npy(nd, "conditions.npy")
        nex = _load_npy(nd, "extra.npy")

        if nobs.shape != oobs.shape:
            _fail(failures, f"{name}: obs shape {nobs.shape} != {oobs.shape}")
        elif not np.array_equal(nobs, oobs):
            _fail(failures, f"{name}: obs 不是逐元素相同")
        if nsc.shape != (oobs.shape[0], SCALAR_DIM):
            _fail(failures, f"{name}: scalars shape {nsc.shape}")
        elif not np.array_equal(nsc[:, :30], osc):
            _fail(failures, f"{name}: scalars[:, :30] 不是逐元素相同")
        if nex.shape != (oobs.shape[0], POLICY_EXTRA_DIM):
            _fail(failures, f"{name}: extra shape {nex.shape}")
        if na.shape != oa.shape:
            _fail(failures, f"{name}: actions shape {na.shape} != {oa.shape}")
        elif not np.array_equal(na, oa):
            _fail(failures, f"{name}: actions 不是逐元素相同")
        if nmk.shape != omk.shape:
            _fail(failures, f"{name}: masks shape {nmk.shape} != {omk.shape}")
        elif not np.array_equal(nmk, omk):
            _fail(failures, f"{name}: masks 不是逐元素相同")
        if ncd.shape != ocd.shape:
            _fail(failures, f"{name}: conditions shape {ncd.shape} != {ocd.shape}")
        elif not np.array_equal(ncd, ocd):
            _fail(failures, f"{name}: conditions 不是逐元素相同")

        if na.size:
            if int(na[:, 0].min()) < 0 or int(na[:, 0].max()) >= 5:
                _fail(failures, f"{name}: move action 越界")
            if int(na[:, 1].min()) < 0 or int(na[:, 1].max()) >= 2:
                _fail(failures, f"{name}: fire action 越界")
            counts_old["move"] += np.bincount(oa[:, 0], minlength=5)
            counts_old["fire"] += np.bincount(oa[:, 1], minlength=2)
            counts_new["move"] += np.bincount(na[:, 0], minlength=5)
            counts_new["fire"] += np.bincount(na[:, 1], minlength=2)
        checked += 1

    if total_n != EXPECTED_N:
        _fail(failures, f"总样本 {total_n} != {EXPECTED_N}")
    if old_total_n != EXPECTED_N:
        _fail(failures, f"旧总样本 {old_total_n} != {EXPECTED_N}")
    if total_kills != EXPECTED_KILLS:
        _fail(failures, f"总击杀 {total_kills} != {EXPECTED_KILLS}")
    if not np.array_equal(counts_old["move"], counts_new["move"]):
        _fail(failures, "动作分布（move）不相等")
    if not np.array_equal(counts_old["fire"], counts_new["fire"]):
        _fail(failures, "动作分布（fire）不相等")

    summary = {
        "checked_shards": checked,
        "old_total_n": old_total_n,
        "new_total_n": total_n,
        "total_kills": total_kills,
        "move_counts": counts_new["move"].tolist(),
        "fire_counts": counts_new["fire"].tolist(),
        "failures": failures,
    }
    print(
        "[corpus-check] "
        f"shards={checked} N={total_n} kills={total_kills} "
        f"move={counts_new['move'].tolist()} fire={counts_new['fire'].tolist()} "
        f"failures={len(failures)}"
    )
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[corpus-check] report -> {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    run_main(main)
