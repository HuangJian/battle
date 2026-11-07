"""C3 — 两教师 argmax 一致率（`plan/classic35-path.plan.md` §5 C3）。

**问题**：bc-v6（pick）与 z10.it233（fight）各有一个“最优动作”。若全局按概率混合，
两边 argmax 不一致的状态会被平均成一个谁都不想要的策略。先量一致率；<60% 就按窗口
切分（§7 P2-B），不做全局混合。

**口径**
* 同一批 **z10.it233 on-policy shard**（`tmp/p0-c9/rollouts`）的 `obs/scalars/extra` 上，
  两模型各自做**掩码 argmax**。
* 主读数 = `(move, fire)` 同时一致。`hold` 的合法掩码是**方向相关**的
  （`restrictAdvanceMask` 只对实际执行的 move 求值），shard 只存执行方向的那一版；
  因此 `hold` 只在“两人选了同一个 move，且该 move = shard 执行 move”时精确比较，
  报告 `hold_coverage`，不拿未知掩码冒充完整动作。
* 分层（非互斥）：
  - `pickup_near`：`0 ≤ pickupDist ≤ 6`（BFS 格；v16 起是可达距离）。
  - `enemy_fire_lane`：`POLICY_EXTRA` 的 `leadHit*` 任一为 1（沿该方向开火到达时能命中）。
  - `base_threatened`：`thBreach/thBreachAimed/thDirect/thDirectAimed/thBulletsAtBase`
    任一 >0（瞬时基地威胁，不是累计成本）。
  - `tactical_any`：以上三者任一；`none`：三者都不成立。
* 95% CI 用**按局 cluster bootstrap**（决策步高度相关，不能当独立样本）。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/p0_teacher_agreement.py \
        --shards tmp/p0-c9/rollouts \
        --out tmp/p0-c3/agreement.json --md tmp/p0-c3/agreement.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from biz.reward_library import METRIC_INDEX, METRICS_DIM, METRICS_VERSION
from common.cli import house_parser, run_main
from common.schema import POLICY_EXTRA_DIM, POLICY_EXTRA_LAYOUT
from tools.p0_common import find_shards, read_manifest, select_balanced, sha16

TEACHER_A = "nn-training/weights/bc-v6-train/weights.json"
TEACHER_B = "nn-training/weights/z10-maze/z10-maze.it233.20261106-211711.json"
DEFAULT_SHARDS = "tmp/p0-c9/rollouts"
SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "extra.npy",
    "mask.npy",
    "a_move.npy",
    "a_fire.npy",
    "a_hold.npy",
    "metrics.npy",
    "manifest.json",
)
MIX_THRESHOLD = 0.60
PICKUP_NEAR = 6.0
MASK_DIM = 9
LEAD_HIT_COLS = tuple(i for i, name in POLICY_EXTRA_LAYOUT if name.startswith("leadHit"))
BASE_THREAT_COLS = tuple(
    METRIC_INDEX[n]
    for n in ("thBreach", "thBreachAimed", "thDirect", "thDirectAimed", "thBulletsAtBase")
)
ALL = "all"
STRATA = (ALL, "pickup_near", "enemy_fire_lane", "base_threatened", "tactical_any", "none")
STAT_KEYS = ("n", "move", "fire", "primary", "hold_n", "hold", "full_n", "full")


def empty_stat() -> dict[str, int]:
    return {k: 0 for k in STAT_KEYS}


def add_stat(dst: dict[str, int], src: dict[str, int]) -> None:
    for k in STAT_KEYS:
        dst[k] += int(src[k])


@dataclass(frozen=True)
class GameStat:
    stage: int
    seed: int
    n: int
    stats: dict[str, dict[str, int]]


def masked_argmax(logits: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """逐行掩码 argmax；并列取首，全掩码退末位（与 TS `argmaxCat` 同语义）。"""
    z = np.asarray(logits, dtype=np.float64)
    m = np.asarray(mask, dtype=bool)
    if z.shape != m.shape:
        raise ValueError(f"logits/mask 形状不一致: {z.shape} vs {m.shape}")
    if z.ndim != 2:
        raise ValueError(f"masked_argmax 只接二维，收到 {z.shape}")
    scored = np.where(m, z, -1e9)
    best = np.asarray(np.argmax(scored, axis=1), dtype=np.int64)
    all_masked = ~m.any(axis=1)
    if bool(all_masked.any()):
        best[all_masked] = z.shape[1] - 1
    return best


def action_flags(
    move_a: np.ndarray,
    fire_a: np.ndarray,
    hold_a: np.ndarray,
    move_b: np.ndarray,
    fire_b: np.ndarray,
    hold_b: np.ndarray,
    behavior_move: np.ndarray,
) -> dict[str, np.ndarray]:
    """把两条动作流变成可累加的一致/可比标志。

    `full_known` = 动作已经可判：主头不一致 ⇒ 完整动作必然不一致；主头一致且
    move = shard 执行 move ⇒ hold 掩码有效。其余情况是 `hold` 不可比，不猜。
    """
    move_same = np.asarray(move_a) == np.asarray(move_b)
    fire_same = np.asarray(fire_a) == np.asarray(fire_b)
    primary = move_same & fire_same
    hold_same = np.asarray(hold_a) == np.asarray(hold_b)
    hold_known = primary & (np.asarray(move_a) == np.asarray(behavior_move))
    full_known = (~primary) | hold_known
    full_same = np.where(primary, hold_same, False)
    return {
        "move_same": move_same,
        "fire_same": fire_same,
        "primary": primary,
        "hold_same": hold_same,
        "hold_known": hold_known,
        "full_known": full_known,
        "full_same": full_same,
    }


def classify_states(
    metrics: np.ndarray, extra: np.ndarray, pickup_near: float = PICKUP_NEAR
) -> dict[str, np.ndarray]:
    """只用 t 时刻已有列做状态分层；返回互不替代的掩码。"""
    if metrics.ndim != 2 or metrics.shape[1] < METRICS_DIM:
        raise ValueError(f"metrics 应为 [N,{METRICS_DIM}]，收到 {metrics.shape}")
    if extra.ndim != 2 or extra.shape[1] != POLICY_EXTRA_DIM:
        raise ValueError(f"extra 应为 [N,{POLICY_EXTRA_DIM}]，收到 {extra.shape}")
    pd = metrics[:, METRIC_INDEX["pickupDist"]]
    pickup = (pd >= 0.0) & (pd <= float(pickup_near))
    lead = extra[:, list(LEAD_HIT_COLS)]
    enemy_lane = np.asarray((lead > 0.5).any(axis=1), dtype=bool)
    base = np.asarray((metrics[:, list(BASE_THREAT_COLS)] > 0.0).any(axis=1), dtype=bool)
    tactical = pickup | enemy_lane | base
    return {
        ALL: np.ones(metrics.shape[0], dtype=bool),
        "pickup_near": pickup,
        "enemy_fire_lane": enemy_lane,
        "base_threatened": base,
        "tactical_any": tactical,
        "none": ~tactical,
    }


def load_policy_model(path: str) -> Any:
    """按权重 arch 建 PPOStudent 并严格装载，拒绝缺参数。"""
    from worker.data.weights_io import load_state_into
    from worker.models.arch_meta import build_ppo
    from worker.models.student import PPOStudent

    model = build_ppo(PPOStudent, path)
    missing = load_state_into(model, path)
    if missing:
        raise ValueError(
            f"{path}: 未装载参数 {missing[:8]}（共 {len(missing)}）——不是同架构 PPO checkpoint"
        )
    if not bool(getattr(model, "action_v6", False)):
        raise ValueError(f"{path}: arch.actionV6=false，C3 需要 v6 三头")
    model.eval()
    return model


def process_shard(
    dirpath: str,
    model_a: Any,
    model_b: Any,
    *,
    pickup_near: float = PICKUP_NEAR,
    batch: int = 512,
) -> GameStat:
    """读一个 shard，两模型前向，返回该局分层计数。"""
    import torch

    meta = read_manifest(dirpath)
    stage = meta.get("stage")
    seed = meta.get("seed")
    if not isinstance(stage, int) or not isinstance(seed, int):
        raise ValueError(f"{dirpath}: manifest 缺 int stage/seed")
    mver = meta.get("metrics_version")
    if mver is not None and int(mver) != METRICS_VERSION:
        raise ValueError(
            f"{dirpath}: metrics_version={mver} != {METRICS_VERSION}——语料与当前列宽不同源"
        )

    obs = np.load(os.path.join(dirpath, "obs.npy"), mmap_mode="r")
    scalars = np.load(os.path.join(dirpath, "scalars.npy"), mmap_mode="r")
    extra = np.load(os.path.join(dirpath, "extra.npy"), mmap_mode="r")
    mask = np.load(os.path.join(dirpath, "mask.npy"), mmap_mode="r")
    a_move = np.load(os.path.join(dirpath, "a_move.npy"), mmap_mode="r")
    metrics = np.load(os.path.join(dirpath, "metrics.npy"), mmap_mode="r")

    n = int(obs.shape[0])
    if n <= 0:
        raise ValueError(f"{dirpath}: 空 shard")
    if obs.shape[1:] != (int(model_a.in_ch), int(model_a.board), int(model_a.board)):
        raise ValueError(f"{dirpath}: obs {obs.shape} != 模型输入")
    if scalars.shape != (n, int(model_a.scalar_dim)):
        raise ValueError(f"{dirpath}: scalars {scalars.shape} != ({n},{model_a.scalar_dim})")
    if extra.shape != (n, POLICY_EXTRA_DIM):
        raise ValueError(f"{dirpath}: extra {extra.shape} != ({n},{POLICY_EXTRA_DIM})")
    if mask.shape != (n, MASK_DIM):
        raise ValueError(f"{dirpath}: mask {mask.shape} != ({n},{MASK_DIM})")
    if a_move.shape[0] != n:
        raise ValueError(f"{dirpath}: a_move 长度 {a_move.shape} != N={n}")
    if metrics.ndim != 2 or metrics.shape[0] < n or metrics.shape[1] < METRICS_DIM:
        raise ValueError(f"{dirpath}: metrics {metrics.shape} 短于 N={n}")

    states = classify_states(metrics[:n], extra, pickup_near)
    stats = {name: empty_stat() for name in STRATA}
    behavior_move = np.asarray(a_move[:n], dtype=np.int64)
    with torch.no_grad():
        for i in range(0, n, batch):
            j = min(i + batch, n)
            o = torch.from_numpy(np.array(obs[i:j], dtype=np.float32, copy=True))
            s = torch.from_numpy(np.array(scalars[i:j], dtype=np.float32, copy=True))
            e = torch.from_numpy(np.array(extra[i:j], dtype=np.float32, copy=True))
            out_a = model_a(o, s, e)
            out_b = model_b(o, s, e)
            if len(out_a) < 3 or len(out_b) < 3:
                raise ValueError(f"{dirpath}: 模型输出不足三个头")
            move_a = masked_argmax(out_a[0].detach().cpu().numpy(), mask[i:j, 0:4])
            fire_a = masked_argmax(out_a[1].detach().cpu().numpy(), mask[i:j, 4:7])
            hold_a = masked_argmax(out_a[2].detach().cpu().numpy(), mask[i:j, 7:9])
            move_b = masked_argmax(out_b[0].detach().cpu().numpy(), mask[i:j, 0:4])
            fire_b = masked_argmax(out_b[1].detach().cpu().numpy(), mask[i:j, 4:7])
            hold_b = masked_argmax(out_b[2].detach().cpu().numpy(), mask[i:j, 7:9])
            flags = action_flags(
                move_a, fire_a, hold_a, move_b, fire_b, hold_b, behavior_move[i:j]
            )
            for name in STRATA:
                m = states[name][i:j]
                st = stats[name]
                st["n"] += int(m.sum())
                st["move"] += int((m & flags["move_same"]).sum())
                st["fire"] += int((m & flags["fire_same"]).sum())
                st["primary"] += int((m & flags["primary"]).sum())
                st["hold_n"] += int((m & flags["hold_known"]).sum())
                st["hold"] += int((m & flags["hold_known"] & flags["hold_same"]).sum())
                st["full_n"] += int((m & flags["full_known"]).sum())
                st["full"] += int((m & flags["full_known"] & flags["full_same"]).sum())
    return GameStat(stage=stage, seed=seed, n=n, stats=stats)


def _rate(num: int, den: int) -> float | None:
    return None if den <= 0 else float(num) / float(den)


def _bootstrap_ci(
    games: list[GameStat],
    stratum: str,
    num_key: str,
    den_key: str,
    reps: int,
    seed: int,
) -> list[float] | None:
    if reps <= 0 or len(games) < 2:
        return None
    nums = np.array([g.stats[stratum][num_key] for g in games], dtype=np.float64)
    dens = np.array([g.stats[stratum][den_key] for g in games], dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(games), size=(int(reps), len(games)))
    ns = nums[idx].sum(axis=1)
    ds = dens[idx].sum(axis=1)
    ok = ds > 0
    if not bool(ok.any()):
        return None
    rates = ns[ok] / ds[ok]
    return [float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))]


def build_report(
    games: list[GameStat],
    *,
    shards_root: str,
    n_shards_total: int,
    teacher_a: str,
    teacher_b: str,
    pickup_near: float,
    n_boot: int,
    boot_seed: int,
) -> dict[str, Any]:
    if not games:
        raise ValueError("没有可用 shard")
    agg = {name: empty_stat() for name in STRATA}
    for g in games:
        for name in STRATA:
            add_stat(agg[name], g.stats[name])

    rows: dict[str, Any] = {}
    for offset, name in enumerate(STRATA):
        st = agg[name]
        rows[name] = {
            "n": st["n"],
            "games_with_state": sum(1 for g in games if g.stats[name]["n"] > 0),
            "move": _rate(st["move"], st["n"]),
            "fire": _rate(st["fire"], st["n"]),
            "primary": _rate(st["primary"], st["n"]),
            "primary_ci95": _bootstrap_ci(games, name, "primary", "n", n_boot, boot_seed + offset),
            "hold": _rate(st["hold"], st["hold_n"]),
            "hold_n": st["hold_n"],
            "full": _rate(st["full"], st["full_n"]),
            "full_ci95": _bootstrap_ci(
                games, name, "full", "full_n", n_boot, boot_seed + 100 + offset
            ),
            "full_n": st["full_n"],
            "full_unknown": st["n"] - st["full_n"],
        }

    overall = rows[ALL]
    p = overall["primary"]
    f = overall["full"]
    reasons: list[str] = []
    if p is None:
        verdict = "no_data"
        reasons.append("无决策步")
    elif p < MIX_THRESHOLD:
        verdict = "reject_global_mix"
        reasons.append(f"move+fire 一致率 {p:.1%} < {MIX_THRESHOLD:.0%}")
    elif f is not None and f < MIX_THRESHOLD:
        verdict = "reject_global_mix"
        reasons.append(f"完整动作一致率 {f:.1%} < {MIX_THRESHOLD:.0%}（hold 拉低）")
    else:
        verdict = "candidate"
        reasons.append(f"move+fire 一致率 {p:.1%} ≥ {MIX_THRESHOLD:.0%}")
    if overall["full_unknown"] > 0:
        reasons.append(
            f"{overall['full_unknown']} 步 hold 掩码不可比（move 与执行方向不同），未计入完整动作"
        )
    return {
        "schema": "c3-teacher-agreement-v1",
        "threshold": MIX_THRESHOLD,
        "shards": {
            "root": shards_root,
            "used": len(games),
            "available": n_shards_total,
            "decisions": int(sum(g.n for g in games)),
            "metrics_version": METRICS_VERSION,
        },
        "teachers": {
            "a": {"role": "bc-v6 (pick)", "path": teacher_a, "sha16": sha16(teacher_a)},
            "b": {"role": "z10.it233 (fight)", "path": teacher_b, "sha16": sha16(teacher_b)},
        },
        "pickup_near": float(pickup_near),
        "state_rules": {
            "pickup_near": f"0 <= pickupDist(40) <= {pickup_near:g} (BFS cells)",
            "enemy_fire_lane": "any POLICY_EXTRA leadHitFront/Back/Left/Right > 0.5",
            "base_threatened": "any thBreach/thBreachAimed/thDirect/thDirectAimed/thBulletsAtBase > 0",
            "non_exclusive": True,
        },
        "action_rule": (
            "primary = masked argmax(move) == and masked argmax(fire) ==; "
            "full adds hold only when move==shard executed move (direction-specific mask)."
        ),
        "rows": rows,
        "verdict": {"global_mix": verdict, "reason": "; ".join(reasons)},
        "bootstrap": {"reps": int(n_boot), "seed": int(boot_seed), "cluster": "game"},
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{float(x):.1%}"


def _ci(ci: list[float] | None) -> str:
    return "" if ci is None else f" [{ci[0]:.1%}, {ci[1]:.1%}]"


def render_markdown(res: dict[str, Any]) -> str:
    sh = res["shards"]
    out = [
        "# C3 — 两教师 argmax 一致率",
        "",
        f"- 数据：`{sh['root']}` · {sh['used']}/{sh['available']} shard · {sh['decisions']} 决策步 · metrics v{sh['metrics_version']}",
        f"- A：`{res['teachers']['a']['path']}`（{res['teachers']['a']['role']}，sha16 `{res['teachers']['a']['sha16']}`）",
        f"- B：`{res['teachers']['b']['path']}`（{res['teachers']['b']['role']}，sha16 `{res['teachers']['b']['sha16']}`）",
        f"- 判决线：move+fire 一致率 **< {res['threshold']:.0%} ⇒ 不做全局混合**；95% CI = 按局 cluster bootstrap {res['bootstrap']['reps']} 次",
        "",
        "## 1. 总表（状态层非互斥）",
        "",
        "| 层 | 决策步 | 含该态局 | move | fire | **move+fire [95% CI]** | full [95% CI] (n) | hold (n) |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for name in STRATA:
        r = res["rows"][name]
        out.append(
            f"| {name} | {r['n']} | {r['games_with_state']} | {_pct(r['move'])} | "
            f"{_pct(r['fire'])} | **{_pct(r['primary'])}**{_ci(r['primary_ci95'])} | "
            f"{_pct(r['full'])}{_ci(r['full_ci95'])} ({r['full_n']}) | "
            f"{_pct(r['hold'])} ({r['hold_n']}) |"
        )
    overall = res["rows"][ALL]
    out += [
        "",
        f"**判决：`{res['verdict']['global_mix']}`** — {res['verdict']['reason']}",
        "",
        "## 2. 读法",
        "",
        "- 主读数只用 `(move, fire)`；`hold` 的合法掩码跟方向走，shard 只存执行方向的版本。",
        f"- 完整动作可判覆盖：**{overall['full_n']}/{overall['n']}**；不可判的步只出现在"
        "“主头一致但 move ≠ 执行方向”，不拿未知 hold 掩码猜一致。",
        "- 状态层是**非互斥**切片；`tactical_any` 是三者并集，`none` 是补集。",
        "- `enemy_fire_lane` 用 `leadHit*`（开火到达时能否命中），不是“敌人正在瞄我”的"
        " `threat*`；`base_threatened` 用瞬时威胁计数，不是 C2 的累计成本。",
        "",
        "## 3. 边界",
        "",
        "- B 就是生成这批 shard 的 z10.it233；A 是 bc-v6 最终权重。"
        "同一状态上的 argmax 比较，不重跑仿真，也不改课程。",
        "- 一致率低只否决**全局混合**；按状态窗口切分仍需另行验证（§7 P2-B）。",
        "- 200 局、4 关、hard；跨关/跨难度不可直接外推。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = house_parser(description="C3: two-teacher masked-argmax agreement (read-only)")
    ap.add_argument("--shards", default=DEFAULT_SHARDS)
    ap.add_argument("--teacher-a", default=TEACHER_A)
    ap.add_argument("--teacher-b", default=TEACHER_B)
    ap.add_argument("--pickup-near", type=float, default=PICKUP_NEAR)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument(
        "--max-games", type=int, default=0, help=">0 按 stage 轮转取 N 局（smoke/抽样）"
    )
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--boot-seed", type=int, default=20261108)
    ap.add_argument("--out", default="tmp/p0-c3/agreement.json")
    ap.add_argument("--md", default="tmp/p0-c3/agreement.md")
    args = ap.parse_args()

    for p in (args.teacher_a, args.teacher_b):
        if not os.path.exists(p):
            print(f"✗ 权重不存在: {p}", file=sys.stderr)
            return 2
    shards = find_shards(args.shards, SHARD_FILES)
    if not shards:
        print(f"✗ 没有 shard: {args.shards}", file=sys.stderr)
        return 2
    used = select_balanced(shards, int(args.max_games)) if args.max_games > 0 else shards
    if not used:
        print("✗ --max-games 过滤后为空", file=sys.stderr)
        return 2

    import torch

    torch.set_num_threads(max(1, int(args.threads)))
    try:
        model_a = load_policy_model(args.teacher_a)
        model_b = load_policy_model(args.teacher_b)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"✗ 装载权重: {e}", file=sys.stderr)
        return 2

    games: list[GameStat] = []
    t0 = time.time()
    for k, d in enumerate(used, 1):
        try:
            games.append(
                process_shard(
                    d, model_a, model_b, pickup_near=args.pickup_near, batch=args.batch
                )
            )
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            print(f"✗ {d}: {e}", file=sys.stderr)
            return 2
        if k % 10 == 0 or k == len(used):
            print(f"  [{k}/{len(used)}] {time.time() - t0:.1f}s", file=sys.stderr)
    keys = [(g.stage, g.seed) for g in games]
    if len(set(keys)) != len(keys):
        print("✗ 重复 (stage, seed) shard", file=sys.stderr)
        return 2

    res = build_report(
        games,
        shards_root=args.shards,
        n_shards_total=len(shards),
        teacher_a=args.teacher_a,
        teacher_b=args.teacher_b,
        pickup_near=args.pickup_near,
        n_boot=args.boot,
        boot_seed=args.boot_seed,
    )
    res["elapsed_sec"] = round(time.time() - t0, 1)
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    p = res["rows"][ALL]
    print(f"✓ {out}")
    print(f"✓ {md}")
    print(
        f"  games={len(games)} decisions={p['n']} "
        f"move+fire={_pct(p['primary'])} full={_pct(p['full'])} "
        f"full_cov={p['full_n']}/{p['n']} verdict={res['verdict']['global_mix']}"
    )
    return 0


if __name__ == "__main__":
    run_main(main)
