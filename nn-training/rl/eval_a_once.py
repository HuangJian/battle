"""eval_a_once — 手动触发课程设计评估（A 层），写 eval_log.jsonl。

与训练主循环每 eval_every 轮跑的干净评估同口径：
  固定语料 (eval_stages × EVAL_SEEDS[:n]) · 贪心 export-eval-game · 逐局 + summary 落账。

用法（console evalA 按钮 / 本机）：
  python nn-training/rl/eval_a_once.py --course c4-dodge \
    --ckpt nn-training/weights/c4-dodge/c4-dodge.it27.xxx.json --iter 27

与 EvalBoard B 层（evalProbeRun / kick-once.py）无关——那是另一套语料与账本。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "nn-training"))


def _write_summary_for_wver(eval_jsonl: Path, key16: str, it: int, t0: float) -> int:
    """从账本中同 wver 的全部 event=eval 行聚合，为 it 写一份 eval_summary。

    用于「同权重已在别轮评完」：控制台按 iter 挂 evalData，缺本 iter 的 summary
    则指标表永远显示空。行归属仍是原 iter，summary 只是本 iter 的读数入口。
    """
    wins = 0
    clears = 0
    n = 0
    outcomes: dict[str, int] = {}
    nodes: dict[str, int] = {}
    try:
        with open(eval_jsonl, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("event") != "eval" or r.get("wver") != key16:
                    continue
                n += 1
                wins += 1 if r.get("win") else 0
                clears += 1 if r.get("cleared") else 0
                oc = str(r.get("outcome") or "?")
                outcomes[oc] = outcomes.get(oc, 0) + 1
                nd = str(r.get("node") or "?")
                nodes[nd] = nodes.get(nd, 0) + 1
    except OSError:
        return 0
    if n == 0:
        return 0
    summary = {
        "event": "eval_summary",
        "iter": it,
        "wver": key16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec": round(time.time() - t0, 1),
        "games": n,
        "wins": wins,
        "winRate": round(wins / n, 4),
        "clears": clears,
        "clearRate": round(clears / n, 4),
        "outcomes": outcomes,
        "dropped": 0,
        "rolloutWinRate": None,
        "nodes": dict(sorted(nodes.items())),
        "kills_mean": None,
        "zero_kill_frac": None,
        "phits_mean": None,
        "pickup_mean": None,
        "timeout_frac": None,
        "course_fp": "",
        "reused_wver": True,
    }
    with open(eval_jsonl, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(summary) + "\n")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one A-layer clean eval for a course ckpt")
    ap.add_argument("--course", required=True)
    ap.add_argument("--ckpt", required=True, help="权重文件路径（该 iter 归档或活动 weights.json）")
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument("--bun", default="bun", help="bun 可执行文件（export-eval-game runner）")
    ap.add_argument("--max-games", type=int, default=0, help=">0 时截断局数（冒烟）")
    args = ap.parse_args()

    import dist_common
    from platform_utils import POPEN_NO_WINDOW  # noqa: F401  # win32 子进程窗口
    from rl.config import apply_course, load_course, stage_json_for_args
    from rl.eval_local import EVAL_SEEDS, eval_done_keys, run_local_eval_game, settle_eval_summary
    from rl.log import log

    course_path = REPO / "nn-training" / "curricula" / f"{args.course}.jsonc"
    if not course_path.exists():
        # 兼容 --course 传绝对/相对路径
        course_path = Path(args.course)
    course = load_course(str(course_path))
    # 极简 args：apply_course 扁平覆盖进命名空间
    ns = argparse.Namespace(mode=course.mode, total_stages=35)
    apply_course(ns, course)

    traj = Path(getattr(ns, "traj", "")) or (REPO / "tmp" / args.course)
    if not traj.is_absolute():
        traj = REPO / traj
    traj.mkdir(parents=True, exist_ok=True)
    # 与训练同册：tmp/<course>/eval_log.jsonl
    eval_jsonl = traj / "eval_log.jsonl"

    ckpt = Path(args.ckpt)
    if not ckpt.is_absolute():
        for base in (REPO, REPO / "nn-training"):
            cand = base / ckpt
            if cand.exists():
                ckpt = cand
                break
        else:
            ckpt = REPO / ckpt
    if not ckpt.exists():
        log(f"[evalA] ckpt 不存在: {ckpt}")
        return 2

    wver = dist_common.weights_fingerprint(str(ckpt))
    key16 = wver[:16]
    n_seeds = max(0, int(getattr(ns, "eval_games_per_stage", 0) or 0))
    spec = str(getattr(ns, "eval_stages", "") or "")
    if spec:
        from rl.course import parse_range

        stages = parse_range(spec)
    else:
        stages = list(range(int(getattr(ns, "total_stages", 35))))
    pairs = [(s, sd) for s in stages for sd in EVAL_SEEDS[:n_seeds]]
    if not pairs:
        log("[evalA] 课程未配置 eval 语料（eval_stages/eval_games_per_stage）")
        return 2
    if args.max_games > 0:
        pairs = pairs[: args.max_games]

    todo = [p for p in pairs if p not in eval_done_keys(eval_jsonl, key16)]
    log(f"[evalA] it{args.iter} course={course.name} wver={key16[:12]}… "
        f"pairs={len(pairs)} todo={len(todo)} → {eval_jsonl}")
    if not todo:
        # 同 wver 已在别轮评完（归档 itN 权重常与 itN+1 的 A 层评估同指纹）。
        # 控制台按 iter 挂 evalData——不为本 iter 写 summary 则表上永远是空。
        n = _write_summary_for_wver(eval_jsonl, key16, args.iter, time.time())
        log(f"[evalA] 该权重语料已评过 — 已为 it{args.iter} 回填 eval_summary（n={n}）")
        return 0 if n > 0 else 1

    t0 = time.time()
    lock = threading.Lock()
    wins = [0]
    cleared_total = [0]
    outcomes: dict[str, int] = {}
    node_games: dict[str, int] = {}
    seen: set[tuple[int, int]] = set()
    done = 0
    failed = 0
    max_ticks = int(getattr(ns, "max_ticks", 2400) or 2400)
    difficulty = str(getattr(ns, "difficulty", "hard") or "hard")
    lives = getattr(ns, "lives_override", None)
    level = getattr(ns, "player_level", None)
    tmp_out = traj / "evalA-tmp" / f"it{args.iter}"
    tmp_out.mkdir(parents=True, exist_ok=True)

    for stage, seed in todo:
        sj = stage_json_for_args(ns, stage) or ""
        out_dir = tmp_out / f"s{stage}_sd{seed}"
        try:
            man = run_local_eval_game(
                args.bun,
                str(ckpt),
                stage,
                seed,
                out_dir,
                max_ticks,
                difficulty,
                timeout_sec=300.0,
                wver=wver,
                stage_json=sj,
                lives_override=lives if lives is not None else None,
                player_level=level if level is not None else None,
                policy="nn",
            )
        except Exception as e:
            failed += 1
            log(f"[evalA] fail s{stage} seed{seed}: {e}")
            continue
        dims = man.get("dims") or {}
        dim_vals = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in dims.items()}
        win = 1 if man.get("win") else 0
        cleared = 1 if man.get("cleared") else 0
        row = {
            "event": "eval",
            "iter": args.iter,
            "wver": key16,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stage": stage,
            "seed": seed,
            "node": "local-evalA",
            "outcome": man.get("outcome"),
            "win": win,
            "cleared": cleared,
            "ticks": man.get("ticks"),
            "score": man.get("score"),
            "quality": man.get("quality"),
            "dims": dim_vals,
            "kills": man.get("kills"),
            "enemyHits": man.get("enemyHits"),
            "hitRate": man.get("hitRate"),
            "powerUpsCollected": man.get("powerUpsCollected"),
            "playerDamageTaken": man.get("playerDamageTaken"),
            "playerHits": man.get("playerHits"),
            "policy": man.get("policy", "nn"),
            "enemyTotal": man.get("enemyTotal"),
            "playerDeaths": man.get("playerDeaths"),
            "playerShots": man.get("playerShots"),
            "playerLevel": man.get("playerLevel"),
            "cellsVisited": man.get("cellsVisited"),
            "firstKillTick": man.get("firstKillTick"),
            "stuckTicks": man.get("stuckTicks"),
            "puSpawnBomb": man.get("puSpawnBomb"),
            "puSpawnTank": man.get("puSpawnTank"),
            "puSpawnFreeze": man.get("puSpawnFreeze"),
            "puSpawnShield": man.get("puSpawnShield"),
            "puSpawnStar": man.get("puSpawnStar"),
            "puGotBomb": man.get("puGotBomb"),
            "puGotTank": man.get("puGotTank"),
            "puGotFreeze": man.get("puGotFreeze"),
            "puGotShield": man.get("puGotShield"),
            "elapsedSec": man.get("elapsedSec"),
        }
        with lock:
            with open(eval_jsonl, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(row) + "\n")
            seen.add((stage, seed))
            wins[0] += win
            cleared_total[0] += cleared
            node_games["local-evalA"] = node_games.get("local-evalA", 0) + 1
            outcomes[str(man.get("outcome"))] = outcomes.get(str(man.get("outcome")), 0) + 1
        done += 1
        if done % 10 == 0:
            log(f"[evalA] progress {done}/{len(todo)} wins={wins[0]} elapsed={time.time()-t0:.0f}s")

    course_fp = ""
    try:
        course_fp = __import__("hashlib").sha256(course_path.read_bytes()).hexdigest()
    except OSError:
        pass
    settle_eval_summary(
        eval_jsonl,
        key16,
        args.iter,
        pairs,
        len(pairs),
        seen,
        wins,
        cleared_total,
        outcomes,
        node_games,
        lock,
        t0,
        None,
        course_fp,
    )
    shutil.rmtree(tmp_out, ignore_errors=True)
    log(f"[evalA] DONE it{args.iter} ok={done} fail={failed} wins={wins[0]}/{len(seen)}")
    return 0 if failed == 0 or done > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
