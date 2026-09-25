"""eval_a_once — 手动触发课程设计评估（A 层），写 eval_log.jsonl。

与训练主循环每 eval_every 轮的干净评估**走同一条路**（`rl/eval_dispatch.py::EvalDispatcher`
经 `dispatch_eval_round`），不是「另一套本机评估」：

  · 语料同口径：`eval_stages` × `a_eval_seed_list(it, eval_games_per_stage)`（双轨 n==50
    → 锚点 50 + 当轮轮转 50，与 in-loop 逐字节同语料）；去重同口径
    （`eval_done_keys(..., min_iter=1)`）。
  · 算力同池：**派发到全部过门节点**（evalSupport / stageJsonSupport / bun 版本 / codeHash
    同一套判据，并行 ping + 并行权重下发）+ 本机份额（`policy.evalLocalSlots`，缺省 4）。
  · 账本同 schema：`node=<节点 id>` / `wallSec` / 掉落三列 / 逐敌种七列，并由同一把
    `settle_eval_summary` 落 summary。

**为什么必须同路**（2026-09-22 用户指令）：此前本脚本自带一个「本机串行」循环 —— 400 局 ×
~0.85s ≈ 340s（实测 x20-noexplore it177 = 339.2s，账本 `nodes` 只有 `local-evalA: 400`），
而 in-loop 同一份语料只要 50–64s（三台节点分摊，本机只吃 100–118 局）。手动 evalA 是
**同一次评估的手动触发**：读数必须与 in-loop 可比（同语料 / 同节点池 / 同 schema），
否则指标表里两种行不可比，还会把「本机串行」误读成「评估变慢」。

与 EvalBoard B 层（evalProbeRun / kick-once.py）无关——那是另一套语料与账本。

用法（console evalA 按钮 / 本机）：
  python nn-training/rl/eval_a_once.py --course c4-dodge \
    --ckpt nn-training/weights/c4-dodge/c4-dodge.it27.xxx.json --iter 27
  # 本机份额覆盖（缺省跟 policy.evalLocalSlots）：--local-slots 0（纯节点）/ 8（大机器）

it0 基线（`--baseline`，2026-09-24）：**离线开课**那一刻由控制台补派一次（云腿永远产不出
这一格——`remote/offline_eval.due()` 对 it<1 恒 False，而离线课本机不跑训练 ⇒ 主循环的
基线派发也不在场上）。权重缺省取**课程活动权重** `out`（= 任务包 manifest 里 init_weights
的同一份字节 ⇒ 与云腿的段起点同 wver，账本可配对）；`--iter` 恒 0，已落账(wver 命中)即早退。
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
# 脚本目录 rl/ 会被自动插到 sys.path[0]，其 queue.py 遮蔽 stdlib queue ——
# `concurrent.futures` 内部 `import queue` 即循环炸（实测）。本脚本现在**要派发到节点**
# （dist_common.ping_nodes_parallel / post_weights_parallel 走 concurrent.futures），
# 所以必须在导入派发器之前先把脚本目录项摘掉；rl.* 的导入走上面的 nn-training 入口，
# 语义不变（同 eval_replays_once.py）。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
while _SCRIPT_DIR in sys.path:
    sys.path.remove(_SCRIPT_DIR)


def _read_summary(eval_jsonl: Path, key16: str, it: int) -> dict | None:
    """账本里本 (iter, wver) 的**最后一条** `eval_summary`（无则 None）。

    行序 = 追加序 ⇒ 最后一条是最新读数（重复触发/manual 与 in-loop 混跑时以最新为准）。
    """
    found: dict | None = None
    try:
        with open(eval_jsonl, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(r, dict)
                    and r.get("event") == "eval_summary"
                    and r.get("wver") == key16
                    and r.get("iter") == it
                ):
                    found = r
    except OSError:
        return None
    return found


def _write_summary_for_wver(eval_jsonl: Path, key16: str, it: int, t0: float) -> int:
    """从账本中同 wver 的全部 event=eval 行聚合，为 it 写一份 eval_summary。

    用于「同权重已在别轮评完」：`EvalDispatcher` 在那种情况会早退**不写 summary**
    （in-loop 侧有 `_drain` 的覆盖判定兜底，手动触发没有），而控制台按 iter 挂
    evalData、缺本 iter 的 summary 则指标表永远显示空。行归属仍是原 iter，summary
    只是本 iter 的读数入口。双轨：按 seed 落段拆出 anchor_wr/rotor_wr/overfit_gap_pp
    （与 settle 同口径）。返回聚合到的局数（0 = 账本里根本没有该 wver 的局）。
    """
    from rl.eval_local import is_anchor_seed, is_rotor_seed, overfit_gap_pp

    wins = 0
    clears = 0
    n = 0
    outcomes: dict[str, int] = {}
    nodes: dict[str, int] = {}
    a_wins = a_n = r_wins = r_n = 0
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
                if "source" in r:
                    continue
                n += 1
                w = 1 if r.get("win") else 0
                wins += w
                clears += 1 if r.get("cleared") else 0
                oc = str(r.get("outcome") or "?")
                outcomes[oc] = outcomes.get(oc, 0) + 1
                nd = str(r.get("node") or "?")
                nodes[nd] = nodes.get(nd, 0) + 1
                try:
                    sd = int(r.get("seed"))
                except (TypeError, ValueError):
                    continue
                if is_anchor_seed(sd):
                    a_n += 1
                    a_wins += w
                elif is_rotor_seed(sd):
                    r_n += 1
                    r_wins += w
    except OSError:
        return 0
    if n == 0:
        return 0
    a_wr = (a_wins / a_n) if a_n else None
    r_wr = (r_wins / r_n) if r_n else None
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
        "anchor_wr": round(a_wr, 4) if a_wr is not None else None,
        "rotor_wr": round(r_wr, 4) if r_wr is not None else None,
        "overfit_gap_pp": overfit_gap_pp(a_wr, r_wr),
        "reused_wver": True,
    }
    with open(eval_jsonl, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(summary) + "\n")
    return n


def resolve_eval_ckpt(ckpt_arg: str, ns, baseline: bool) -> str:
    """evalA 权重解析（单一事实来源）。

    显式 --ckpt 永远优先；`--baseline` 缺省取课程 `bc`（起点冻结权重），**绝不取
    live `out`**——out 每轮被训练覆盖，停课→重开后补派的基线会读到新权重，把别轮
    读数写进 it0 槽（2026-09-25 x20-dodge-l1/L3 实测：各 200 局污染）。
    bc 缺席 ⇒ 返回空串，调用方响亮拒（不静默拿 out 顶）。
    """
    ckpt_arg = str(ckpt_arg or "")
    if not ckpt_arg and baseline:
        ckpt_arg = str(getattr(ns, "bc", "") or "")
    return ckpt_arg


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one A-layer clean eval for a course ckpt")
    ap.add_argument("--course", required=True)
    ap.add_argument(
        "--ckpt",
        default="",
        help="权重文件路径（该 iter 归档或活动 weights.json）；--baseline 时可省略（取课程 bc 起点冻结权重）",
    )
    ap.add_argument("--iter", type=int, required=True)
    ap.add_argument(
        "--baseline",
        action="store_true",
        help="it0 基线模式：评课程 bc 起点冻结权重 W(0)（iter 必为 0），账本写 iter=0 行",
    )
    ap.add_argument("--bun", default="bun", help="bun 可执行文件（export-eval-game runner）")
    ap.add_argument(
        "--local-slots",
        type=int,
        default=None,
        help="本机份额（缺省 = policy.evalLocalSlots / 缺省 4；0 = 纯节点）",
    )
    args = ap.parse_args()

    import dist_common
    from platform_utils import force_utf8_stdio

    force_utf8_stdio()

    from rl.config import apply_course, load_course
    from rl.eval_dispatch import dispatch_eval_round
    from rl.eval_local import BASELINE_EVAL_ITER
    from rl.log import log

    # it0 基线 = 「W(0) 的读数」这件事本身：iter 不是 0 就不是基线（落进别的 iter 槽会被
    # 控制台当成那一轮的读数）。响亮拒，不静默改口。
    if args.baseline and args.iter != BASELINE_EVAL_ITER:
        log(
            f"[evalA] --baseline 的 --iter 必为 {BASELINE_EVAL_ITER}（收到 {args.iter}）"
            "——it0 基线是起点权重的读数"
        )
        return 2

    course_path = REPO / "nn-training" / "curricula" / f"{args.course}.jsonc"
    if not course_path.exists():
        # 兼容 --course 传绝对/相对路径
        course_path = Path(args.course)
    course = load_course(str(course_path))
    # 极简 args：apply_course 扁平覆盖进命名空间（与训练主循环同一单一事实来源）
    ns = argparse.Namespace(mode=course.mode, total_stages=35)
    apply_course(ns, course)
    # D14 课程血缘：主循环用**启动期冻结**的字节（args.course_frozen_bytes），本脚本此刻冻结。
    ns.course_path = str(course_path)
    try:
        ns.course_frozen_bytes = course_path.read_bytes()
    except OSError:
        pass

    traj = Path(getattr(ns, "traj", "") or "") or (REPO / "tmp" / args.course)
    if not traj.is_absolute():
        traj = REPO / traj
    traj.mkdir(parents=True, exist_ok=True)
    # 与训练同册：tmp/<course>/eval_log.jsonl
    eval_jsonl = traj / "eval_log.jsonl"

    # 权重来源：显式 --ckpt 优先；--baseline 缺省取课程 bc（起点冻结权重，见
    # resolve_eval_ckpt；禁取 live out——`Path("")` 是 `.`（存在！）⇒ 必须先判空再 Path()，
    # 否则会把一个目录当权重去算指纹。
    ckpt_arg = resolve_eval_ckpt(str(args.ckpt or ""), ns, bool(args.baseline))
    if not ckpt_arg:
        log("[evalA] 缺 --ckpt（非 baseline 模式必须给权重路径）")
        return 2
    ckpt = Path(ckpt_arg)
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

    # 分派配置：与主循环同一份 rl-config.json（节点池 / policy）。
    cfg = dict(dist_common.load_dist_config() or {})
    if args.local_slots is not None:
        # 只在**内存里**覆盖（同 eval_course_once.py --dist-local），绝不回写 rl-config.json。
        cfg["policy"] = {**(cfg.get("policy") or {}), "evalLocalSlots": int(args.local_slots)}
    rl_cfg = cfg.get("rl") or {}
    # 主循环的 args 由 CLI 默认值 + rl-config 拼出；本脚本没有 CLI 默认值那层，
    # 缺字段直接补 rl-config 同源值（否则派发器裸取 args.max_ticks 会 AttributeError）。
    ns.max_ticks = int(getattr(ns, "max_ticks", 0) or rl_cfg.get("max_ticks") or 12000)
    ns.difficulty = str(getattr(ns, "difficulty", "") or rl_cfg.get("difficulty") or "hard")
    ns.eval_window_sec = float(rl_cfg.get("eval_window_sec") or 1500)

    wver = dist_common.weights_fingerprint(str(ckpt))
    key16 = wver[:16]
    # 幂等早退（离线课「停课→重开」/重试不重派）：判据与主循环 `baseline_summary_landed`
    # 逐字同口径——`event=eval_summary ∧ iter=0 ∧ 同 wver`（本文件的 `_read_summary` 就是
    # 同一把尺，读的也是同一册 `traj/eval_log.jsonl`）。
    if args.baseline and _read_summary(eval_jsonl, key16, BASELINE_EVAL_ITER) is not None:
        log(f"[evalA] it0 基线已落账（wver={key16[:12]}…）——跳过派发")
        return 0
    enabled = [
        str(n.get("id") or n.get("url") or "?")
        for n in (cfg.get("nodes") or [])
        if n.get("enabled", True)
    ]
    log(
        f"[evalA] it{args.iter} course={course.name} wver={key16[:12]}… "
        f"max_ticks={ns.max_ticks} difficulty={ns.difficulty} "
        f"→ 派发（与 in-loop 同路：节点池 {enabled or '（无，仅本机）'} + 本机份额）"
        + ("【it0 基线：课程 bc 起点冻结权重】" if args.baseline else "")
    )

    # 本机份额没有要让位的东西（手动触发的评估不在训练的 PPO 窗口里）⇒ gate 立即置位；
    # 不置位本机槽位会一路空等到 deadline。
    gate = threading.Event()
    gate.set()
    # 与 in-loop 同布局：冻结权重快照 + local-eval/ 都落 traj/it<N>/（eval_replays_once
    # 的权重解析也按 traj/it*/_eval_frozen_weights*.json 找）。
    it_dir = traj / f"it{args.iter}"
    it_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    dispatch_eval_round(
        args.bun,
        str(ckpt),
        it_dir,
        ns,
        cfg,
        f"evalA.{args.iter}",
        args.iter,
        rollout_winrate=None,
        local_gate=gate,
        baseline=args.baseline,
    )
    summary = _read_summary(eval_jsonl, key16, args.iter)
    if summary is None:
        # 派发器早退（同 wver 语料已评完）时不会写 summary，也可能一局都没落盘
        # （节点全不可达 + 本机份额 0）——前者按同 wver 回填读数，后者回填 0 行。
        n = _write_summary_for_wver(eval_jsonl, key16, args.iter, time.time())
        log(f"[evalA] 本次无 summary — 按同 wver 回填（n={n}）")
        summary = _read_summary(eval_jsonl, key16, args.iter)
    # 逐局产物（local-eval/_eval_report.json 等）随收工清掉：账本行已带全部字段，
    # 这是手动触发的一次性评估，不留每次几百 MB 的中间目录（同旧 evalA-tmp 清理）。
    shutil.rmtree(it_dir / "local-eval", ignore_errors=True)
    if not summary or not summary.get("games"):
        log(f"[evalA] FAIL it{args.iter}：一局都没落账（无可用节点且本机份额为 0？）")
        return 1
    log(
        f"[evalA] DONE it{args.iter} sec={summary.get('sec')} games={summary.get('games')} "
        f"winRate={summary.get('winRate')} nodes={summary.get('nodes')} "
        f"elapsed={time.time() - t0:.0f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
