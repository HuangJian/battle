"""C9 —— z10.it233 critic 标定（`plan/classic35-path.plan.md` §5 C9）。

**问题**：z10 的 critic 是冷启动的（value 头随机初始化），课程失败时无法区分
「策略没学好」与「critic 尺度错、优势估计全歪」。本工具只读 on-policy rollout：
逐决策的 `V(s)` 取自 shard 的 `value.npy`，MC return 由 `metrics.npy` 按课程奖励
公式**现算**（γ=课程 gamma），报 R² / 校准斜率 / RMSE。

**口径**
* 统计单位 = **决策步**（主读数）；另报每局均值（game-level）以检查跨局尺度。
* `V(s)` 与该局 MC return `G_t = r_t + γ(1-done_t)G_{t+1}` 配对；奖励在 it=233
  的课程公式下计算（与 z10 训练同源）。
* 校准回归 = OLS `G ~ 1 + V`；`slope` 的理想值 = 1（V 与 return 同尺度），
  `R²` 衡量排序/线性解释力。CI 用**按局 cluster bootstrap**（同一局内高度相关，
  不能把决策步当独立样本）。
* 默认只读：不写账本、不起训练。显式 `--write-returns` 才会给 rollout 目录补
`returns.npy`（value-only BC 的目标）；bootstrap 固定种子，输出可复算。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/c9_critic_calibration.py \\
        --shards tmp/p0-c9/rollouts \\
        --course nn-training/curricula/z10-maze.jsonc --it 233 \\
        --out tmp/p0-c9/critic.json --md tmp/p0-c9/critic.md
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from biz.course_resolve import load_course
from biz.reward_library import METRICS_VERSION, RewardFn, build_reward_fn
from common.cli import house_parser, run_main
from tools.p0_common import find_shards, read_manifest, sha16

DEFAULT_BOOT = 1000
DEFAULT_BOOT_SEED = 20261108
SHARD_FILES = ("value.npy", "metrics.npy", "done.npy", "manifest.json")


@dataclass(frozen=True)
class Game:
    """一局的逐决策 `V(s)` 与 MC return（等长）。"""

    stage: int
    seed: int
    values: np.ndarray
    returns: np.ndarray

    @property
    def n(self) -> int:
        return int(self.values.shape[0])


def mc_returns(rewards: np.ndarray, dones: np.ndarray, gamma: float) -> np.ndarray:
    """`G_t = r_t + γ(1-done_t)G_{t+1}`；末步无 bootstrap。"""
    r = np.asarray(rewards, dtype=np.float64).reshape(-1)
    d = np.asarray(dones, dtype=np.float64).reshape(-1)
    if r.shape != d.shape:
        raise ValueError(f"rewards/dones 长度不一致: {r.shape} vs {d.shape}")
    out = np.empty_like(r)
    acc = 0.0
    for t in range(r.shape[0] - 1, -1, -1):
        acc = float(r[t]) + float(gamma) * (1.0 - float(d[t])) * acc
        out[t] = acc
    return out


def _signed_sums(games: list[Game]) -> dict[str, np.ndarray]:
    """每局的充分统计量；bootstrap 只聚合这些，不重读大数组。"""
    n = np.array([g.n for g in games], dtype=np.float64)
    sx = np.array([g.values.sum(dtype=np.float64) for g in games], dtype=np.float64)
    sy = np.array([g.returns.sum(dtype=np.float64) for g in games], dtype=np.float64)
    sxx = np.array([np.square(g.values, dtype=np.float64).sum() for g in games], dtype=np.float64)
    syy = np.array(
        [np.square(g.returns, dtype=np.float64).sum() for g in games], dtype=np.float64
    )
    sxy = np.array(
        [float(np.dot(g.values, g.returns)) for g in games], dtype=np.float64
    )
    return {"n": n, "sx": sx, "sy": sy, "sxx": sxx, "syy": syy, "sxy": sxy}


def _ols_from_sums(
    n: float, sx: float, sy: float, sxx: float, syy: float, sxy: float
) -> dict[str, float]:
    """OLS `y ~ 1 + x` 的闭式解 + R²/RMSE。"""
    if n < 2:
        raise ValueError("OLS 需要至少 2 个点")
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        raise ValueError("x 方差为 0，无法拟合校准斜率")
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    sse = (
        syy
        - 2.0 * slope * sxy
        + slope * slope * sxx
        - 2.0 * intercept * sy
        + 2.0 * intercept * slope * sx
        + n * intercept * intercept
    )
    sst = syy - sy * sy / n
    r2 = 1.0 - sse / sst if sst > 1e-12 else 1.0
    pearson_den = (n * sxx - sx * sx) * (n * syy - sy * sy)
    pearson = (n * sxy - sx * sy) / float(np.sqrt(pearson_den)) if pearson_den > 0 else 0.0
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": float(r2),
        "pearson": float(pearson),
        "rmse": float(np.sqrt(max(0.0, sse) / n)),
        "mean_value": float(sx / n),
        "mean_return": float(sy / n),
    }


def fit_arrays(values: np.ndarray, returns: np.ndarray) -> dict[str, float]:
    """单组 `(V, G)` 的校准统计；测试与 game-level 共用。"""
    x = np.asarray(values, dtype=np.float64).reshape(-1)
    y = np.asarray(returns, dtype=np.float64).reshape(-1)
    if x.shape != y.shape:
        raise ValueError(f"V/return 长度不一致: {x.shape} vs {y.shape}")
    return _ols_from_sums(
        float(x.size),
        float(x.sum()),
        float(y.sum()),
        float(np.square(x).sum()),
        float(np.square(y).sum()),
        float(np.dot(x, y)),
    )


def _stats_from_games(games: list[Game]) -> dict[str, float | int]:
    sums = _signed_sums(games)
    st = _ols_from_sums(
        float(sums["n"].sum()),
        float(sums["sx"].sum()),
        float(sums["sy"].sum()),
        float(sums["sxx"].sum()),
        float(sums["syy"].sum()),
        float(sums["sxy"].sum()),
    )
    return {"n_games": len(games), "n_decisions": int(sums["n"].sum()), **st}


def _bootstrap_ci(
    games: list[Game], reps: int, seed: int
) -> dict[str, list[float] | None]:
    """按局 cluster bootstrap：同一局内的决策步一起重采样。"""
    if reps <= 0 or len(games) < 2:
        return {"slope": None, "r2": None}
    sums = _signed_sums(games)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(games), size=(int(reps), len(games)))
    slopes = np.empty(int(reps), dtype=np.float64)
    r2s = np.empty(int(reps), dtype=np.float64)
    for i in range(int(reps)):
        take = idx[i]
        st = _ols_from_sums(
            float(sums["n"][take].sum()),
            float(sums["sx"][take].sum()),
            float(sums["sy"][take].sum()),
            float(sums["sxx"][take].sum()),
            float(sums["syy"][take].sum()),
            float(sums["sxy"][take].sum()),
        )
        slopes[i] = st["slope"]
        r2s[i] = st["r2"]
    return {
        "slope": [float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))],
        "r2": [float(np.percentile(r2s, 2.5)), float(np.percentile(r2s, 97.5))],
    }


def _game_level(games: list[Game]) -> dict[str, Any]:
    """每局均值上的跨局校准（n=局数）；防决策步加权把 R² 抬得虚高。"""
    vals = np.array([g.values.mean() for g in games], dtype=np.float64)
    rets = np.array([g.returns.mean() for g in games], dtype=np.float64)
    base = {
        "n_games": len(games),
        "mean_value": float(vals.mean()) if vals.size else None,
        "mean_return": float(rets.mean()) if rets.size else None,
    }
    # 单局 / 同一关所有局均值相同（小样本测试）没有跨局方差；不伪造斜率。
    if len(games) < 2 or float(np.ptp(vals)) < 1e-12:
        return {**base, "slope": None, "intercept": None, "r2": None, "pearson": None, "rmse": None, "degenerate": True}
    return {**base, **fit_arrays(vals, rets)}


def load_game(
    dirpath: str,
    reward_fn: RewardFn | Callable[[np.ndarray, str, float, int], np.ndarray],
    gamma: float,
    it: int,
    expected_metrics_version: int = METRICS_VERSION,
) -> Game:
    """读一个 shard 的 `value.npy` + `metrics.npy` + `done.npy` → Game。"""
    m = read_manifest(dirpath)
    stage = m.get("stage")
    seed = m.get("seed")
    if not isinstance(stage, int) or not isinstance(seed, int):
        raise ValueError(f"{dirpath}: manifest 缺 int stage/seed")
    mver = m.get("metrics_version")
    if mver is not None and int(mver) != int(expected_metrics_version):
        raise ValueError(
            f"{dirpath}: metrics_version={mver} != {expected_metrics_version}——"
            "语料与当前奖励列宽不同源"
        )
    values = np.asarray(np.load(os.path.join(dirpath, "value.npy")), dtype=np.float64).reshape(-1)
    metrics = np.asarray(np.load(os.path.join(dirpath, "metrics.npy")), dtype=np.float64)
    dones = np.asarray(np.load(os.path.join(dirpath, "done.npy")), dtype=np.float64).reshape(-1)
    n = int(values.shape[0])
    if n == 0:
        raise ValueError(f"{dirpath}: 空 rollout")
    if metrics.ndim != 2 or metrics.shape[0] != n + 1:
        raise ValueError(f"{dirpath}: metrics 应为 [N+1, D]，收到 {metrics.shape}（N={n}）")
    if dones.shape[0] != n:
        raise ValueError(f"{dirpath}: done 长度 {dones.shape} != N={n}")
    outcome = str(m.get("outcome", "timeout"))
    score = float(m.get("score", 0.0))
    rewards = np.asarray(reward_fn(metrics, outcome, score, it), dtype=np.float64).reshape(-1)
    if rewards.shape[0] != n:
        raise ValueError(f"{dirpath}: reward 长度 {rewards.shape[0]} != N={n}")
    returns = mc_returns(rewards, dones, gamma)
    if not np.isfinite(values).all() or not np.isfinite(returns).all():
        raise ValueError(f"{dirpath}: V/return 含非有限值")
    return Game(stage=stage, seed=seed, values=values, returns=returns)


def write_returns(dirpath: str, game: Game) -> str:
    """把该局的 MC return 写成 `returns.npy`（value-only BC 的目标）。"""
    path = os.path.join(dirpath, "returns.npy")
    np.save(path, np.asarray(game.returns, dtype=np.float32))
    return path


def load_value_model(weights_path: str) -> Any:
    """按权重 arch 元数据建 PPOStudent 并严格装载（预热后校准用）。"""
    from worker.data.weights_io import load_state_into
    from worker.models.arch_meta import build_ppo
    from worker.models.student import PPOStudent

    model = build_ppo(PPOStudent, weights_path)
    missing = load_state_into(model, weights_path)
    if missing:
        raise ValueError(
            f"{weights_path}: 未装载参数 {missing[:8]}（共 {len(missing)}）——"
            "不是同架构 PPO checkpoint，拒绝用随机头冒充预热 critic"
        )
    model.eval()
    return model


def values_from_weights(dirpath: str, model: Any, batch: int = 256) -> np.ndarray:
    """用 checkpoint 在同一批 obs/scalars/extra 上前向重算 V(s)。"""
    import torch

    obs = np.load(os.path.join(dirpath, "obs.npy"), mmap_mode="r")
    scalars = np.load(os.path.join(dirpath, "scalars.npy"), mmap_mode="r")
    extra = np.load(os.path.join(dirpath, "extra.npy"), mmap_mode="r")
    n = int(obs.shape[0])
    if int(scalars.shape[0]) != n or int(extra.shape[0]) != n:
        raise ValueError(f"{dirpath}: obs/scalars/extra 长度不一致")
    out = np.empty(n, dtype=np.float32)
    with torch.no_grad():
        for i in range(0, n, batch):
            j = min(i + batch, n)
            # mmap 切片是只读的；copy=True 避免 torch 的 non-writable 警告。
            o = torch.from_numpy(np.array(obs[i:j], dtype=np.float32, copy=True))
            s = torch.from_numpy(np.array(scalars[i:j], dtype=np.float32, copy=True))
            e = torch.from_numpy(np.array(extra[i:j], dtype=np.float32, copy=True))
            out[i:j] = model(o, s, e)[-1].squeeze(-1).detach().cpu().numpy()
    if not np.isfinite(out).all():
        raise ValueError(f"{dirpath}: checkpoint 前向出现非有限 V")
    return out.astype(np.float64)


def build_report(
    games: list[Game],
    *,
    course_name: str,
    it: int,
    gamma: float,
    reward_identity: str,
    stages_expected: tuple[int, ...],
    n_boot: int,
    boot_seed: int,
    value_source: str = "value.npy",
    value_weights_sha16: str = "",
) -> dict[str, Any]:
    if not games:
        raise ValueError("没有可用 shard")
    stages = sorted({g.stage for g in games})
    per_stage: dict[str, Any] = {}
    for stage in stages:
        subset = [g for g in games if g.stage == stage]
        per_stage[str(stage)] = {
            "pooled": _stats_from_games(subset),
            "game_level": _game_level(subset),
            "ci95": _bootstrap_ci(subset, n_boot, boot_seed + stage),
        }
    count = {str(s): sum(1 for g in games if g.stage == s) for s in stages}
    missing = [s for s in stages_expected if str(s) not in count]
    return {
        "schema": "c9-critic-calibration-v1",
        "course": course_name,
        "it": int(it),
        "gamma": float(gamma),
        "reward_identity": reward_identity,
        "metrics_version": METRICS_VERSION,
        "value_source": value_source,
        "value_weights_sha16": value_weights_sha16 or None,
        "shards": {
            "games": len(games),
            "stages": [int(s) for s in stages],
            "per_stage": count,
            "missing_expected_stages": missing,
            "expected_per_stage": 50,
        },
        "pooled": {
            **_stats_from_games(games),
            "game_level": _game_level(games),
            "ci95": _bootstrap_ci(games, n_boot, boot_seed),
        },
        "per_stage": per_stage,
        "bootstrap": {"reps": int(n_boot), "seed": int(boot_seed), "cluster": "game"},
    }


def _f(x: float | int | None, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    return f"{float(x):.{digits}f}"


def _pp(x: float | int | None) -> str:
    return "n/a" if x is None else f"{float(x):+.4f}"


def render_markdown(res: dict[str, Any]) -> str:
    sh = res["shards"]
    out = [
        "# C9 — z10.it233 critic 标定",
        "",
        f"- 课程：`{res['course']}` · it=**{res['it']}** · γ=**{res['gamma']}**",
        f"- 奖励：`{res['reward_identity']}` · metrics v{res['metrics_version']}",
        f"- V 来源：`{res.get('value_source', 'value.npy')}`"
        + (f" · sha16 `{res['value_weights_sha16']}`" if res.get("value_weights_sha16") else ""),
        f"- 数据：**{sh['games']} 局 / {sh['per_stage']}**；缺关：{sh['missing_expected_stages'] or '无'}",
        f"- 主读数：决策步级 OLS `MC ~ 1 + V`；CI = 按局 cluster bootstrap "
        f"{res['bootstrap']['reps']} 次（seed={res['bootstrap']['seed']}）",
        "",
        "## 1. 汇总",
        "",
        "| 口径 | 局 | 决策步 | R² | 校准斜率 [95% CI] | 截距 | mean V | mean MC | RMSE |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, st in [("pooled", res["pooled"]), *[(f"stage {k}", v["pooled"]) for k, v in res["per_stage"].items()]]:
        ci = res["pooled"]["ci95"] if name == "pooled" else res["per_stage"][name.split()[-1]]["ci95"]
        slope_ci = f"{_f(st['slope'])} [{_f(ci['slope'][0])}, {_f(ci['slope'][1])}]" if ci.get("slope") else _f(st["slope"])
        out.append(
            f"| {name} | {st['n_games']} | {st['n_decisions']} | {_f(st['r2'])} | "
            f"{slope_ci} | {_f(st['intercept'])} | {_f(st['mean_value'])} | "
            f"{_f(st['mean_return'])} | {_f(st['rmse'])} |"
        )
    out += [
        "",
        "**game-level（每局均值，n=局数）**",
        "",
        "| 口径 | 局 | R² | 斜率 | 截距 | mean V | mean MC |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    gl = res["pooled"]["game_level"]
    out.append(
        f"| pooled | {gl['n_games']} | {_f(gl['r2'])} | {_f(gl['slope'])} | "
        f"{_f(gl['intercept'])} | {_f(gl['mean_value'])} | {_f(gl['mean_return'])} |"
    )
    for k, v in res["per_stage"].items():
        g = v["game_level"]
        out.append(
            f"| stage {k} | {g['n_games']} | {_f(g['r2'])} | {_f(g['slope'])} | "
            f"{_f(g['intercept'])} | {_f(g['mean_value'])} | {_f(g['mean_return'])} |"
        )
    out += [
        "",
        "## 2. 判读",
        "",
        "- `slope≈1` 且截距≈0：V 与 MC return 同尺度；`slope<1` = V 被压缩（保守），"
        "`slope>1` = 过度自信。",
        "- `R²` 低只说明线性解释力弱，不直接等于策略差；决策步级 R² 会被局内方差主导，"
        "必须与 game-level 表一起看。",
        "- 本报告只做标定，不判决「冷 critic 是不是 it5 崩因」——后者要 C9 的 "
        "value-only 预热 + 20 轮 PPO 单变量腿。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = house_parser(description="C9: z10.it233 critic calibration (read-only)")
    ap.add_argument("--shards", default="tmp/p0-c9/rollouts")
    ap.add_argument(
        "--value-weights",
        default="",
        help="用该 PPO 权重在同一批 obs/scalars/extra 上前向重算 V（预热后校准）；默认读 value.npy",
    )
    ap.add_argument("--course", default="nn-training/curricula/z10-maze.jsonc")
    ap.add_argument("--it", type=int, default=233)
    ap.add_argument("--stages", default="16,22,3,6", help="逗号分隔的预期关号（只用于缺关报告）")
    ap.add_argument("--boot", type=int, default=DEFAULT_BOOT)
    ap.add_argument("--boot-seed", type=int, default=DEFAULT_BOOT_SEED)
    ap.add_argument("--out", default="tmp/p0-c9/critic.json")
    ap.add_argument("--md", default="tmp/p0-c9/critic.md")
    ap.add_argument(
        "--write-returns",
        action="store_true",
        help="给每个 shard 写 returns.npy（value-only BC 的 MC 目标；默认只读）",
    )
    args = ap.parse_args()

    course = load_course(args.course)
    spec = course.reward_spec()
    reward_fn = build_reward_fn(spec)
    gamma = float(course.gamma)
    stages_expected = tuple(int(s) for s in args.stages.split(",") if s.strip())
    shard_dirs = find_shards(args.shards, SHARD_FILES)
    if not shard_dirs:
        print(f"✗ 没有 shard（需 value/metrics/done/manifest）: {args.shards}", file=sys.stderr)
        return 2
    value_model = None
    if args.value_weights:
        if not os.path.exists(args.value_weights):
            print(f"✗ --value-weights 不存在: {args.value_weights}", file=sys.stderr)
            return 2
        try:
            value_model = load_value_model(args.value_weights)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"✗ --value-weights: {e}", file=sys.stderr)
            return 2
    games: list[Game] = []
    for d in shard_dirs:
        try:
            g = load_game(d, reward_fn, gamma, args.it)
            if value_model is not None:
                g = replace(g, values=values_from_weights(d, value_model))
            if args.write_returns:
                write_returns(d, g)
            games.append(g)
        except (OSError, ValueError, KeyError) as e:
            print(f"✗ {d}: {e}", file=sys.stderr)
            return 2
    keys = [(g.stage, g.seed) for g in games]
    if len(set(keys)) != len(keys):
        raise SystemExit("✗ 重复的 (stage, seed) shard")
    res = build_report(
        games,
        course_name=course.name,
        it=args.it,
        gamma=gamma,
        reward_identity=spec.identity(),
        stages_expected=stages_expected,
        n_boot=args.boot,
        boot_seed=args.boot_seed,
        value_source=args.value_weights or "value.npy",
        value_weights_sha16=sha16(args.value_weights) if args.value_weights else "",
    )
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    p = res["pooled"]
    print(f"✓ {out}")
    print(f"✓ {md}")
    print(
        f"  pooled: R²={_f(p['r2'])} slope={_f(p['slope'])} "
        f"CI=[{_f(p['ci95']['slope'][0])},{_f(p['ci95']['slope'][1])}] "
        f"meanV={_f(p['mean_value'])} meanMC={_f(p['mean_return'])} n={p['n_decisions']}"
    )
    return 0


if __name__ == "__main__":
    run_main(main)
