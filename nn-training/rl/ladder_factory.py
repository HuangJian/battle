"""ladder_factory.py —— I3（roadmap §4-I3）：阶梯工厂（数据不是代码，§2.4）。

从单一模板生成 c01..c20 的关卡 + 课程 + 计划台账初稿。参数钉死（总纲 §4-I3）：

  · 几何 = 空场 arena 常量（源 = levels/arena4.jsonc：边界钢环 + 四角 4 出生点 + 中央
    player_spawn；无基地无掩体——用户定案 D3）
  · count / lives / max_ticks 每级参数化；lives：count≤7→1、8-14→2、15-20→3（D1/D2）；
    max_ticks = ceil(2400 × count / 4)（终局标准一次性立案候选，Phase 1 DECISIONS 过会）
  · forces 长**恒 20**（断言；spawn 取循环 `enemies[i % len]`，World.ts:501-505，
    count≤20 时与截断等价——ms F4）
  · 掉落规则全阶梯 modern（D9）：bonusEnemyEveryNpawns=4 + score 里程碑掉宝，
    classic 规则只进经典 tier
  · **无 gates 块**（I2：阶梯课程一律不配 gates——G4 cloud halt 自杀不再重演）
  · 剂量公式化（hy R5）：`wChip = W_CHIP_K / 承伤基数(count)`，承伤基数 =
    `150 + (count-4)×34`（v2 实测 c4=150 / c5≈196 / c6=218 的线性拟合；
    W_CHIP_K = 0.03×150 = 4.5，即 c04 落在已验证的 chip03 剂量上、c06 ≈ 0.0206 与
    跨关换算表一致）。**v2 时代数值方向继承、数值不继承**——HP 可见后边际效应必变，
    c05 两档小剂量扫描重标定。c01-c03 承伤基数极小 **不上 wChip**（公式项不出现）
  · **wDmg 全阶梯移除**（N3 定案：1 命下致死命中归 terminal 计价；非致命星盾命中
    待事件子类拆分后再计价）
  · rollout_games（课程键 = seed_rotate）：c01-c03 = **600**（hy E3：短局 transition
    量反推，c04 的 1/5~1/10 局长必须放大批量），c04+ = 150
  · 腿矩阵（hy X1）：c06/c07 = **3 腿**（暖启 / BC 重起 / 假说），其余 = 2 腿
    （暖启基线 + 假说）；c01-c04 首腿 = bc（D5 BC 优先写死）

产物（扁平名 = resolve_level / resolve_course 的原生查找形态）：
  levels/ladder-cNN.jsonc · curricula/ladder-cNN.jsonc · ladder/plan.jsonc
  （plan = I5 LEDGER 的初稿数据：legs/dose/rollout/eval 种子批次/shard_keep_policy；
   总纲写的嵌套 levels/ladder/ 布局与 resolve_level 不符，按仓库事实改扁平——deviation 注记）

用法：`cd nn-training && python -m rl.ladder_factory [--dry-run] [--force]`
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARENA = NN_ROOT / "levels" / "arena4.jsonc"
DEFAULT_OUT_LEVELS = NN_ROOT / "levels"
DEFAULT_OUT_COURSES = NN_ROOT / "curricula"
DEFAULT_PLAN = NN_ROOT / "ladder" / "plan.jsonc"

LEVEL_COUNT = 20
#: forces 长**恒 20**（ms F4：循环语义 `i % len`，长 20 时 count≤20 与截断等价）
FORCES_LEN = 20
#: wChip 剂量常数 = 已验证的 c4-chip03 剂量 0.03 × 承伤基数 150
W_CHIP_K = 4.5
#: v2 时代承伤基数线性拟合（c4=150 实测锚点，c5/c6 由 +34/敌 拟合）
DAMAGE_BASE_C04 = 150.0
DAMAGE_BASE_SLOPE = 34.0
MAX_TICKS_C04 = 2400


def tier_lives(count: int) -> int:
    """D1/D2：count ≤7 → 1 命、8-14 → 2 命、15-20 → 3 命。"""
    if not 1 <= count <= LEVEL_COUNT:
        raise ValueError(f"count {count} 超出阶梯 1..{LEVEL_COUNT}")
    if count <= 7:
        return 1
    if count <= 14:
        return 2
    return 3


def max_ticks_for(count: int) -> int:
    """终局标准（D7）：与 count 线性比例，c04 = 2400（现线锚点）。一次性立案后不再逐调。"""
    return math.ceil(MAX_TICKS_C04 * count / 4)


def damage_base(count: int) -> float:
    """承伤基数（v2 实测线性拟合）：c04=150，每 +1 敌 +34（c6=218 与实测一致）。"""
    return DAMAGE_BASE_C04 + (count - 4) * DAMAGE_BASE_SLOPE


def wchip_for(count: int) -> float | None:
    """剂量公式：wChip = K / 承伤基数；count<4（基数过小）→ None = 公式项不出现（R5）。"""
    if count < 4:
        return None
    return round(W_CHIP_K / damage_base(count), 4)


_FORMULA_BASE = (
    "wKill*kills + wHit*enemyHits + wPickup*powerUpsCollected + wStar*starsCollected"
    " - wStuck*min(max(0, stuckTicks-300), 900) - wShot*playerShots - wTick*ticks"
)
_FORMULA_CHIP = _FORMULA_BASE + " - wChip*playerDamageTaken"


def formula_for(count: int) -> str:
    """c01-c03：无 wChip 项（R5）；c04+：含 wChip。wDmg 全阶梯移除（N3）。"""
    return _FORMULA_CHIP if count >= 4 else _FORMULA_BASE


def params_for(count: int) -> dict[str, float]:
    params: dict[str, float] = {
        "wKill": 3.0,
        "wHit": 0.3,
        "wPickup": 2.5 if count >= 5 else 1.5,  # c05 起 wPickup 扎根（G2 并入，D9 bonus 车全开）
        "wStar": 1.0,
        "wStuck": 0.02,
        "wShot": 0.01,
        "wTick": 0.01,
    }
    chip = wchip_for(count)
    if chip is not None:
        params["wChip"] = chip
    return params


def seed_rotate_for(count: int) -> int:
    """rollout 批量（hy E3）：短局放大——c01-c03 600，c04+ 150。"""
    return 600 if count <= 3 else 150


def legs_for(count: int) -> dict[str, Any]:
    """腿矩阵（hy X1 + D5）：攻坚级 c06/c07 3 腿；c01-c04 首腿 = bc（BC 优先写死）。"""
    first = "bc" if count <= 4 else "warm"
    n = 3 if count in (6, 7) else 2
    kinds = [first, "hypothesis"]
    if n == 3:
        kinds.insert(1, "bc-restart")
    return {"count": n, "kinds": kinds}


def load_arena_source(path: Path = DEFAULT_ARENA) -> dict[str, Any]:
    """几何常量源（levels/arena4.jsonc）：grid/forces/出生点/player_spawn。"""
    from rl.jsonc import load as _load_jsonc

    src = _load_jsonc(str(path))
    stage = src["stages"][0]
    forces = str(stage["forces"])
    if len(forces) != FORCES_LEN:
        raise ValueError(
            f"forces 长恒 {FORCES_LEN}（ms F4 钉死），源文件 {path} 给了 {len(forces)}"
        )
    return {
        "grid": stage["grid"],
        "forces": forces,
        "player_spawn": stage["player_spawn"],
        "enemy_spawns": stage["enemy_spawns"],
        "difficulty": str(src.get("difficulty", "hard")),
    }


def level_name(count: int) -> str:
    return f"ladder-c{count:02d}"


def emit_level(count: int, arena: dict[str, Any]) -> dict[str, Any]:
    """关卡文件（环境语义唯一持有者）：D3 空场常量几何 + count/lives/max_ticks 参数。"""
    return {
        "name": level_name(count),
        "stages": [
            {
                "name": level_name(count),
                "grid": arena["grid"],
                "forces": arena["forces"],
                "count": count,
                "player_spawn": arena["player_spawn"],
                "enemy_spawns": arena["enemy_spawns"],
            }
        ],
        "difficulty": arena["difficulty"],
        "max_ticks": max_ticks_for(count),
        "player": {"lives": tier_lives(count), "level": 0},
    }


def emit_course(count: int) -> dict[str, Any]:
    """课程文件：引用 level（环境键禁重复声明），reward = 剂量公式产物，无 gates（I2）。"""
    name = level_name(count)
    return {
        "version": 5,
        "name": name,
        "mode": "per-tick",
        "level": name,
        "reward": {
            "formula": formula_for(count),
            "params": params_for(count),
            "terminal": {"stage_clear": 2.0, "lives_exhausted": -1.0, "timeout": -2.0},
            "scheme": "toy",
        },
        "dodge": "",
        # bc 由 Phase 2 的 I6 BC 管线产出后落位（BC 优先写死，D5）；
        # PPO 腿的 warm-start = 上一级毕业生（§4.7），由 runbook 起腿时覆盖。
        "bc": f"nn-training/weights/{name}-bc/bc-latest.json",
        "iters": 60,
        "max_hours": 12,
        "workers": 8,
        "kickstart_ref": True,
        "warmup_iters": 0,
        "lr": 0.00015,
        "epochs": 4,
        "mb": 512,
        "normalize_ret": True,
        "gamma": 0.998,
        "lam": 0.95,
        "ppo_schedule": [
            {"until_iter": 40, "kl_coef": 0.2, "lr": 0.00015, "kl_cap": 0.2},
            {"kl_coef": 0.03, "lr": 0.00005},
        ],
        "out": f"tmp/{name}/weights.json",
        "traj": f"tmp/{name}",
        "ent_break": 0.25,
        "backup_dir": f"nn-training/weights/{name}",
        "backup_prefix": name,
        "eval_stages": "2000-2000",
        "eval_games_per_stage": 200,
        "eval_every": 5,
        "seed_rotate": seed_rotate_for(count),
    }


def plan_doc(arena: dict[str, Any]) -> dict[str, Any]:
    """I5 LEDGER 初稿数据：每级 legs/dose/rollout/eval 种子批次/shard 保留策略。"""
    levels: list[dict[str, Any]] = []
    for c in range(1, LEVEL_COUNT + 1):
        chip = wchip_for(c)
        levels.append(
            {
                "level": level_name(c),
                "count": c,
                "lives": tier_lives(c),
                "max_ticks": max_ticks_for(c),
                "rollout_games": seed_rotate_for(c),
                "legs": legs_for(c),
                "dose": {
                    "damage_base": damage_base(c),
                    "wChip": chip,
                    "recalibrate_at": "c05 two-dose scan (hy R5)" if c == 5 else None,
                },
                "eval_seed_batches": {"grad_round1": "0-199", "grad_round2": "200-399"},
                "status": "pending",
            }
        )
    return {
        "version": 1,
        "name": "nn-ladder",
        "drop_profile": "modern (bonusEnemyEveryNSpawns=4 + score milestone; D9)",
        "geometry": {
            "source": "levels/arena4.jsonc",
            "grid": "empty arena, steel border only (D3)",
            "spawn_points": len(arena["enemy_spawns"]),
            "player_spawn": arena["player_spawn"],
        },
        "max_ticks_rule": "ceil(2400 * count / 4) — DECISIONS 立案候选（D7 一次性）",
        "shard_keep_policy": "keep latest 2 iters per leg; archive graduated weights (.xz 惯例)",
        "gates": None,  # I2：阶梯课程一律不配 gates
        "levels": levels,
    }


def generate(
    out_levels: Path = DEFAULT_OUT_LEVELS,
    out_courses: Path = DEFAULT_OUT_COURSES,
    plan_path: Path = DEFAULT_PLAN,
    arena_path: Path = DEFAULT_ARENA,
    dry_run: bool = False,
) -> list[str]:
    """生成全部产物；返回写入的文件路径列表（dry_run 只校验不写盘）。"""
    arena = load_arena_source(arena_path)
    written: list[str] = []
    out_levels.mkdir(parents=True, exist_ok=True)
    out_courses.mkdir(parents=True, exist_ok=True)
    for c in range(1, LEVEL_COUNT + 1):
        lvl, course = emit_level(c, arena), emit_course(c)
        targets = [
            (out_levels / f"{level_name(c)}.jsonc", lvl),
            (out_courses / f"{level_name(c)}.jsonc", course),
        ]
        for path, doc in targets:
            if not dry_run:
                path.write_text(
                    json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            written.append(str(path))
    if not dry_run:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(plan_doc(arena), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    written.append(str(plan_path))
    return written


def preflight(out_levels: Path, out_courses: Path) -> list[str]:
    """CI 预检（总纲 I3）：对生成的课程跑 load_course + validate_reward，返回错误列表。"""
    from rl.config import load_course
    from rl.reward_validation import validate_reward

    errors: list[str] = []
    for c in range(1, LEVEL_COUNT + 1):
        course_path = out_courses / f"{level_name(c)}.jsonc"
        try:
            course = load_course(course_path)
        except Exception as e:
            errors.append(f"{course_path.name}: load_course 失败（{type(e).__name__}: {e}）")
            continue
        try:
            report = validate_reward(course.reward_spec())
            errors.extend(f"{course_path.name}: {msg}" for msg in report.errors)
        except Exception as e:
            errors.append(f"{course_path.name}: validate_reward 异常（{type(e).__name__}: {e}）")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description="I3 阶梯工厂：生成 c01..c20 关卡/课程/计划")
    ap.add_argument("--out-levels", type=Path, default=DEFAULT_OUT_LEVELS)
    ap.add_argument("--out-courses", type=Path, default=DEFAULT_OUT_COURSES)
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--arena", type=Path, default=DEFAULT_ARENA)
    ap.add_argument("--dry-run", action="store_true", help="只校验模板与参数，不写盘")
    args = ap.parse_args()

    written = generate(args.out_levels, args.out_courses, args.plan, args.arena, args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "WROTE"
    print(f"[ladder-factory] {mode} {len(written)} files")
    if not args.dry_run:
        errs = preflight(args.out_levels, args.out_courses)
        if errs:
            for e in errs:
                print(f"[ladder-factory] PREFLIGHT FAIL: {e}")
            raise SystemExit(1)
        print("[ladder-factory] preflight OK：load_course + validate_reward 全绿（20 级）")


if __name__ == "__main__":
    main()
