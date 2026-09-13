"""eval_replays_once — 从指定 iter 的 in-loop eval 导出仿真 replay（控制台「导出 replay」后端）。

对控制台勾选的 (stage, seed) 逐局用**该 eval 的冻结权重**确定性重放：同一 runner
（export-eval-game，贪心 argmax、整局零随机）+ ``--replay`` 输入录制。确定性契约 =
同 (权重, 关, seed) 逐 tick 确定 ⇒ 重放局与 eval_log 账本逐字段一致；脚本对每局做
outcome/ticks/kills 对账，不一致在 manifest 里诚实标记（不静默）。

课程参数（difficulty / max_ticks / stageJson / lives / player_level）经
load_course + apply_course 重建——与训练主循环 / evalA 同一单一事实来源，绝不在
这里手写第二份课程语义。

用法（console POST evalReplays / 本机手动）：
  python nn-training/rl/eval_replays_once.py --course c6-chip --iter 12 \
      --wver <key16> --games tmp/c6-chip/replay-export-games.json \
      --out-dir tmp/c6-chip/replay-export --manifest tmp/c6-chip/replay-export.json

产物：``--out-dir`` 下若干 ``<canonical>.replay``（ReplayBrowser 可直接导入）+
manifest JSON（files/errors/对账结果）；控制台下载端点把 out-dir 打 tar.gz。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "nn-training"))
# 脚本目录 rl/ 会被自动插到 sys.path[0]，其 queue.py 遮蔽 stdlib queue——
# concurrent.futures 内部 `import queue` 即循环炸（实测）。先移除脚本目录项，
# 再导入 concurrent.futures；rl.* 的导入走上面的 nn-training 入口，语义不变。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
while _SCRIPT_DIR in sys.path:
    sys.path.remove(_SCRIPT_DIR)

from concurrent.futures import ThreadPoolExecutor  # noqa: E402 — 必须在 scrub 之后

_FILENAME_RE = re.compile(r"-s(\d+)-[a-z]+-l\d+-t\d+-seed(\d+)\.replay$")


def _sha16(path: Path) -> str | None:
    """文件字节 sha256 前 16 位（= 账本 wver 口径，dist_common.weights_fingerprint 同式）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()[:16]


def resolve_weights(
    traj: Path,
    course: str,
    wver: str,
    it: int,
    weights_root: Path | None = None,
) -> Path | None:
    """按 wver（sha256[:16]）定位权重文件。候选顺序（快→慢）：

    1. ``tmp/<course>/it*/_eval_frozen_weights*.json``（评估派发时刻的冻结快照，
       含 it0 基线的 ``-baseline`` 变体——基线快照落在首个窗口目录里）；
    2. ``tmp/<course>/weights.json``（活动权重——仅最新 iter 可能命中）；
    3. ``nn-training/weights/<course>/*it<N>*.json``（该 iter 归档）；
    4. 整腿扫描（归档命名不含 iter 的兜底）。

    全部不中返回 None（调用方 fail loud——错版本 replay 比没有更糟）。
    """
    cands: list[Path] = []
    cands += sorted(traj.glob("it*/_eval_frozen_weights*.json"))
    cands.append(traj / "weights.json")
    leg = (weights_root or (REPO / "nn-training" / "weights")) / course
    if leg.is_dir():
        cands += sorted(leg.glob(f"*it{it}.json")) + sorted(leg.glob(f"*it{it}.*.json"))
    seen: set[Path] = set()
    for f in cands:
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        if _sha16(f) == wver:
            return f
    if leg.is_dir():
        for f in sorted(leg.glob("*.json")):
            if f in seen:
                continue
            seen.add(f)
            if _sha16(f) == wver:
                return f
    return None


def _manifest_for(
    games: list[dict[str, int]],
    out_dir: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """按 canonical 文件名把产物 .replay 映射回 (stage, seed)。

    注意 buildReplayFilename 的 stage 段 = stageIndex + 1（1-based 显示口径），
    映射时减回 0-based 账本口径。"""
    by_key: dict[tuple[int, int], str] = {}
    for p in sorted(out_dir.glob("*.replay")):
        m = _FILENAME_RE.search(p.name)
        if m:
            by_key[(int(m.group(1)) - 1, int(m.group(2)))] = p.name
    files: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for g in games:
        stage, seed = int(g["stage"]), int(g["seed"])
        name = by_key.get((stage, seed))
        if name:
            files.append({"stage": stage, "seed": seed, "file": name})
        else:
            errors.append({"stage": stage, "seed": seed, "error": "未产出 .replay"})
    return files, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Export deterministic sim replays for in-loop eval games")
    ap.add_argument("--course", required=True)
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument("--wver", required=True, help="eval 账本 key16（sha256 权重指纹前 16 位）")
    ap.add_argument("--games", required=True, help="JSON 文件：[{'stage': int, 'seed': int}, ...]")
    ap.add_argument("--out-dir", required=True, help=".replay 产物目录")
    ap.add_argument("--manifest", required=True, help="manifest JSON 输出路径（traj 直下）")
    ap.add_argument("--bun", default="bun")
    ap.add_argument("--workers", type=int, default=4, help="并行重放进程数")
    ap.add_argument("--timeout", type=float, default=600.0, help="单局超时（秒）")
    args = ap.parse_args()

    from platform_utils import force_utf8_stdio

    force_utf8_stdio()

    from rl.config import apply_course, load_course, stage_json_for_args
    from rl.eval_local import run_local_eval_game
    from rl.log import log

    t0 = time.time()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO / manifest_path
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO / out_dir

    games_raw: list[dict] = []
    games_path = Path(args.games)
    if not games_path.is_absolute():
        games_path = REPO / games_path  # 与 out-dir/manifest 同基准（REPO），不随 cwd 漂移
    try:
        games_raw = json.loads(games_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"[replay-exp] games 文件不可读: {e}")
        return 2
    games = [
        {"stage": int(g["stage"]), "seed": int(g["seed"])}
        for g in games_raw
        if isinstance(g, dict) and "stage" in g and "seed" in g
    ]
    if not games:
        log("[replay-exp] games 列表为空")
        return 2

    course_path = REPO / "nn-training" / "curricula" / f"{args.course}.jsonc"
    if not course_path.exists():
        course_path = Path(args.course)
    course = load_course(str(course_path))
    ns = argparse.Namespace(mode=course.mode, total_stages=35)
    apply_course(ns, course)

    traj = Path(str(getattr(ns, "traj", "") or "")) or (REPO / "tmp" / args.course)
    if not traj.is_absolute():
        traj = REPO / traj

    weights = resolve_weights(traj, args.course, args.wver, args.iter)
    if weights is None:
        log(
            f"[replay-exp] FAIL: 未找到 wver={args.wver} 的权重文件"
            f"（快照/活动/weights/{args.course} 全不匹配）——归档已清理或权重已更新，无法确定性重放"
        )
        return 3
    log(f"[replay-exp] it{args.iter} course={course.name} weights={weights.name}")

    max_ticks = int(getattr(ns, "max_ticks", 2400) or 2400)
    difficulty = str(getattr(ns, "difficulty", "hard") or "hard")
    lives = getattr(ns, "lives_override", None)
    level = getattr(ns, "player_level", None)

    # 上一轮产物清场（同课程重导出不残留旧文件；manifest 在 traj 直下不受影响）。
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.replay"):
        try:
            old.unlink()
        except OSError:
            pass
    tmp_reports = traj / "replay-export-tmp"
    shutil.rmtree(tmp_reports, ignore_errors=True)
    tmp_reports.mkdir(parents=True, exist_ok=True)

    # 对账基准：eval_log 里该 (iter, wver) 的逐局行（含远程节点局）。
    ledger: dict[tuple[int, int], dict] = {}
    try:
        with open(traj / "eval_log.jsonl", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (
                    r.get("event") == "eval"
                    and r.get("iter") == args.iter
                    and r.get("wver") == args.wver
                ):
                    ledger[(int(r["stage"]), int(r["seed"]))] = r
    except OSError:
        pass

    workers = max(1, min(int(args.workers), 8))
    ok = 0
    failed = 0
    mismatch: list[dict] = []
    done = 0

    def one(g: dict[str, int]) -> None:
        nonlocal ok, failed, done
        stage, seed = g["stage"], g["seed"]
        game_dir = tmp_reports / f"s{stage}_sd{seed}"
        try:
            man = run_local_eval_game(
                args.bun,
                str(weights),
                stage,
                seed,
                game_dir,
                max_ticks,
                difficulty,
                timeout_sec=float(args.timeout),
                wver=args.wver,
                stage_json=stage_json_for_args(ns, stage) or "",
                lives_override=lives if lives is not None else None,
                player_level=level if level is not None else None,
                policy="nn",
                replay_dir=str(out_dir),
            )
        except Exception as e:  # noqa: BLE001 — 单局失败不拖垮整批
            failed += 1
            log(f"[replay-exp] fail s{stage} seed{seed}: {e}")
            return
        # 确定性对账：重放局 vs 账本（outcome/ticks/kills 任一不一致 = 标记，不静默）。
        row = ledger.get((stage, seed))
        if row:
            for k in ("outcome", "ticks", "kills"):
                if row.get(k) is not None and row.get(k) != man.get(k):
                    mismatch.append(
                        {"stage": stage, "seed": seed, "field": k,
                         "ledger": row.get(k), "resim": man.get(k)}
                    )
        ok += 1
        done += 1
        if done % 5 == 0 or done == len(games):
            log(f"[replay-exp] progress {done}/{len(games)} ok={ok} fail={failed} "
                f"elapsed={time.time() - t0:.0f}s")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, games))

    files, errors = _manifest_for(games, out_dir)
    shutil.rmtree(tmp_reports, ignore_errors=True)

    payload = {
        "ok": len(files) > 0,
        "course": args.course,
        "iter": args.iter,
        "wver": args.wver,
        "weightsPath": str(weights.relative_to(REPO)) if weights.is_relative_to(REPO) else str(weights),
        "difficulty": difficulty,
        "maxTicks": max_ticks,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec": round(time.time() - t0, 1),
        "requested": len(games),
        "files": files,
        "errors": errors,
        "mismatches": mismatch,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[replay-exp] DONE files={len(files)} fail={len(errors)} mismatch={len(mismatch)} "
        f"sec={payload['sec']}")
    return 0 if files else 1


if __name__ == "__main__":
    raise SystemExit(main())
