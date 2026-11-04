"""eval_backfill — 离线补齐课程的 A 层评测缺口（旁路，不碰训练主循环）。

**为什么需要它**：进程闸（`worker/eval_gate.py`）的队列住内存，重启整批丢；
`select_delayed_eval_it` 只看 `it-1` ⇒ 丢掉的轮次成**永久缺号**。收官 drain 只在训练真正
结束才跑，且逐轮串行。2026-11-04 实测：z6-porter / z6-speed 都从 it145 起全缺（各 26 轮）。

**它做什么**：读课程配置（`eval_every` / `eval_stages` / `eval_games_per_stage`）+ 权重归档
+ 账本，算出「该评但没评完」的轮，按归档权重逐轮调 `EvalDispatcher` —— 与 in-loop / 手动
evalA **同一条路**：同语料（`a_eval_seed_list` 固定池）、同节点门（codeHash / bun / evalSupport
/ bootId）、同 schema、同 `settle_eval_summary` 落账。不是第二套评估器。

**「充分利用集群算力」落在三处**：

1. **不走进程闸**：直调 `dispatch_eval_round`。闸是给训练主循环排优先级的（rollout 绝对优先），
   补账跟当前读数抢 FIFO 只会互相拖。
2. **零空档流水线**：一轮收工立刻派下一轮，节点池在两次派发之间不空转（人手一个个点 evalA
   的间隔就是纯损失）。
3. **吃满空闲槽 / 有 rollout 就自动让位**：`eval_slot_cap` 按节点自报 `inflight` 削并发，
   集群空闲时 eval 拿满 `concurrency`，rollout 到场就退到 0 —— 让位是派发器的既有语义，
   本脚本不另加判断。

⚠ `--inflight>1` **不会真的提速**：第二轮 ping 时看到第一轮占满节点，`c=0`，只会在启动
瞬间抢一次槽。默认 1 是诚实的默认值；多轮并发只在「节点数 > 单轮吃得下的槽」时才划算，
而本集群 6 节点 37 槽已被一轮吃满。

用法：
  # 先看缺口（不动任何东西）
  python nn-training/tools/eval_backfill.py --courses z6-porter z6-speed --dry-run

  # 全量补（串行流水线，吃满空闲集群）
  python nn-training/tools/eval_backfill.py --courses z6-porter z6-speed

  # 只补一段 / 只跑几轮试水
  python nn-training/tools/eval_backfill.py --courses z6-speed --from 145 --to 200 --max-rounds 2

  # 本机也下场（8 槽，会与脚本本身抢核；训练在跑时别开）
  python nn-training/tools/eval_backfill.py --courses z6-porter --local-slots 8
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_NN = str(REPO / "nn-training")
if _NN not in sys.path:
    sys.path.insert(0, _NN)

from common.cli import house_parser, run_main
from common.log import log


@dataclass
class RoundPlan:
    """一轮待补的评测量。"""

    course: str
    it: int
    ckpt: str
    missing: int  # 这一轮账本里还缺多少局（仅用于 dry-run 预算）


@dataclass
class RoundResult:
    course: str
    it: int
    ok: bool
    sec: float = 0.0
    games: int = 0
    win_rate: float | None = None
    dropped: int | None = None
    nodes: dict[str, int] = field(default_factory=dict)
    err: str = ""


# ---------------------------------------------------------------- 账本 / 归档扫描


def _read_ledger(eval_jsonl: Path) -> tuple[dict[int, list[dict]], dict[int, set[tuple[int, int]]]]:
    """一次扫完账本：`iter -> [eval_summary...]` 与 `iter -> {(stage,seed)}`（去重后的落盘局）。"""
    summaries: dict[int, list[dict]] = {}
    landed: dict[int, set[tuple[int, int]]] = {}
    if not eval_jsonl.exists():
        return summaries, landed
    with eval_jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            it = row.get("iter")
            if not isinstance(it, int):
                continue
            if row.get("event") == "eval_summary":
                summaries.setdefault(it, []).append(row)
            elif row.get("event") == "eval":
                try:
                    landed.setdefault(it, set()).add((int(row["stage"]), int(row["seed"])))
                except (KeyError, TypeError, ValueError):
                    continue
    return summaries, landed


def _archive_weights(backup_dir: str, backup_prefix: str) -> dict[int, str]:
    """归档权重表 `it -> path`。同名目录里同 it 多份时取**字典序最后**（= 时间戳最新）。"""
    base = Path(backup_dir)
    if not base.is_absolute():
        base = REPO / base
    if not backup_prefix:
        return {}
    pat = re.compile(rf"^{re.escape(backup_prefix)}\.it(\d+)\..*\.json$")
    out: dict[int, str] = {}
    try:
        for p in sorted(base.glob(f"{backup_prefix}.it*.*.json")):
            m = pat.match(p.name)
            if m:
                out[int(m.group(1))] = str(p)
    except OSError as e:
        log(f"[backfill] WARN 扫归档失败 {base}: {e}")
    return out


def _is_covered(summaries: list[dict], landed: set[tuple[int, int]] | None, expected: int) -> bool:
    """覆盖判定（与 `trainer/loop_eval.py::_eval_covered` 同口径 + 行数双保险）。

    `dropped == 0` 的 summary 是 in-loop 的判定尺；再加一道「去重落盘局数 ≥ 期望局数」，
    防止 summary 自报干净而账本行实际没落（2026-11-04 的 `games=84 / dropped=616` 陈旧行
    就是这么来的）。
    """
    if not any(r.get("dropped") == 0 for r in summaries):
        return False
    return len(landed or ()) >= expected


class CoursePlanner:
    """一门课的评估计划：args / cfg / 缺口轮。"""

    def __init__(self, course: str, *, local_slots: int | None, window: float | None) -> None:
        from common import distribution
        from worker.config import apply_course, load_course

        self.course = course
        self.course_path = REPO / "nn-training" / "curricula" / f"{course}.jsonc"
        if not self.course_path.exists():
            raise SystemExit(f"[backfill] 课程文件不存在: {self.course_path}")
        course_obj = load_course(str(self.course_path))

        ns = argparse.Namespace(mode=course_obj.mode, total_stages=35)
        apply_course(ns, course_obj)
        ns.course_path = str(self.course_path)
        try:
            ns.course_frozen_bytes = self.course_path.read_bytes()
        except OSError:
            pass
        # 课程里没写的字段（主循环由 CLI 默认值 + rl-config 拼出）在这里补同源值，
        # 否则派发器裸取 `args.max_ticks` 会 AttributeError（同 eval_a_once 的注释）。
        cfg = dict(distribution.load_dist_config() or {})
        rl_cfg = cfg.get("rl") or {}
        ns.max_ticks = int(getattr(ns, "max_ticks", 0) or rl_cfg.get("max_ticks") or 12000)
        ns.difficulty = str(getattr(ns, "difficulty", "") or rl_cfg.get("difficulty") or "hard")
        ns.eval_window_sec = float(
            window if window is not None else (rl_cfg.get("eval_window_sec") or 1500)
        )
        if local_slots is not None:
            # 只在内存里覆盖，绝不回写 rl-config.json（同 eval_course_once --dist-local）。
            cfg["policy"] = {**(cfg.get("policy") or {}), "evalLocalSlots": int(local_slots)}
        self.args = ns
        self.cfg = cfg
        self.local_slots = int((cfg.get("policy") or {}).get("evalLocalSlots", 4))
        self.traj = self._resolve_traj(ns)
        self.eval_jsonl = self.traj / "eval_log.jsonl"
        self.eval_every = max(1, int(getattr(ns, "eval_every", 1) or 1))
        self.expected = self._expected_games(ns)

    @staticmethod
    def _resolve_traj(ns) -> Path:
        traj = Path(getattr(ns, "traj", "") or "") or (REPO / "tmp" / str(ns.course_name))
        return traj if traj.is_absolute() else REPO / traj

    @staticmethod
    def _expected_games(ns) -> int:
        from biz.course import parse_range
        from worker.eval_track import a_eval_seed_list

        spec = str(getattr(ns, "eval_stages", "") or "")
        stages = parse_range(spec) if spec else list(range(int(ns.total_stages)))
        n_seeds = int(getattr(ns, "eval_games_per_stage", 0) or 0)
        return len(stages) * len(a_eval_seed_list(0, n_seeds))

    def rounds(self, *, it_from: int, it_to: int) -> tuple[list[RoundPlan], int]:
        """缺口轮（升序）+ 总缺口局数。"""
        arch = _archive_weights(
            str(getattr(self.args, "backup_dir", "") or ""),
            str(getattr(self.args, "backup_prefix", "") or ""),
        )
        summaries, landed = _read_ledger(self.eval_jsonl)
        plans: list[RoundPlan] = []
        missing_total = 0
        for it in sorted(arch):
            if it < 1 or it % self.eval_every != 0:
                continue
            if it < it_from or it > it_to:
                continue
            got = landed.get(it) or set()
            if _is_covered(summaries.get(it) or [], got, self.expected):
                continue
            gap = max(0, self.expected - len(got))
            plans.append(RoundPlan(self.course, it, arch[it], gap))
            missing_total += gap
        return plans, missing_total

    def run_round(self, plan: RoundPlan, *, bun: str) -> RoundResult:
        """派发一轮并等收工，返回账本里的读数。"""
        from trainer.eval_dispatch import dispatch_eval_round

        res = RoundResult(course=plan.course, it=plan.it, ok=False)
        it_dir = self.traj / f"it{plan.it}"
        it_dir.mkdir(parents=True, exist_ok=True)
        gate = threading.Event()
        gate.set()  # 补账不在训练的 PPO 窗口里，没有要让位的东西（同 eval_a_once）
        t0 = time.time()
        try:
            dispatch_eval_round(
                bun,
                plan.ckpt,
                it_dir,
                self.args,
                self.cfg,
                f"backfill.{plan.course}.{plan.it}",
                plan.it,
                rollout_winrate=None,
                local_gate=gate,
                baseline=False,
                # 本机份额 0 ⇒ 兜底模式且无快照 ⇒ 纯节点（不会偷偷占本机核）。
                local_fallback_only=self.local_slots <= 0,
            )
        except Exception as e:  # 派发器自身吞异常，这里是最后一道保险
            res.err = f"{type(e).__name__}: {str(e)[:200]}"
            log(f"[backfill] {plan.course} it{plan.it}: 派发异常 {res.err}")
        res.sec = round(time.time() - t0, 1)
        # 收工后从账本读这一轮的最新 summary（派发器自己落账，不在这里重算）。
        summaries, _ = _read_ledger(self.eval_jsonl)
        rows = [r for r in (summaries.get(plan.it) or []) if r.get("wver")]
        if rows:
            last = rows[-1]
            res.games = int(last.get("games") or 0)
            res.win_rate = last.get("winRate")
            res.dropped = last.get("dropped")
            res.nodes = dict(last.get("nodes") or {})
            res.ok = _is_covered(
                [last], _read_ledger(self.eval_jsonl)[1].get(plan.it), self.expected
            )
        # 逐局中间产物（local-eval/*.json）随收工清掉：账本行已带全字段；冻结快照保留
        # （eval_replays_once 按 wver 解析它）。
        shutil.rmtree(self.traj / f"_eval-it{plan.it}" / "local-eval", ignore_errors=True)
        return res


# ---------------------------------------------------------------- 调度


def _print_plan(plans: list[RoundPlan], expected: int) -> None:
    by_course: dict[str, list[RoundPlan]] = {}
    for p in plans:
        by_course.setdefault(p.course, []).append(p)
    log(
        f"[backfill] 计划：{len(plans)} 轮 / {sum(p.missing for p in plans)} 局"
        f"（每轮 {expected} 局）"
    )
    for course, items in by_course.items():
        its = [p.it for p in items]
        log(
            f"[backfill]   {course}: {len(items)} 轮 {its[0]}..{its[-1]}"
            f"（缺 {sum(p.missing for p in items)} 局）"
        )


def main() -> int:
    ap = house_parser(
        description="离线补齐课程 A 层评测缺口（与 in-loop 同语料 / 同节点门 / 同账本）"
    )
    ap.add_argument("--courses", nargs="+", required=True, help="课程名（curricula/<name>.jsonc）")
    ap.add_argument("--from", dest="it_from", type=int, default=1, help="只补 iter ≥ 此值的轮")
    ap.add_argument("--to", dest="it_to", type=int, default=10**9, help="只补 iter ≤ 此值的轮")
    ap.add_argument("--max-rounds", type=int, default=0, help="本次最多补几轮（0 = 不限）")
    ap.add_argument("--inflight", type=int, default=1, help="同时在飞的轮数（默认 1，见模块头注）")
    ap.add_argument(
        "--local-slots",
        type=int,
        default=None,
        help="本机份额（缺省跟 policy.evalLocalSlots；0 = 纯节点）",
    )
    ap.add_argument(
        "--window", type=float, default=None, help="单轮评估窗口秒数（缺省跟 rl-config）"
    )
    ap.add_argument("--bun", default="bun", help="bun 可执行文件")
    ap.add_argument("--dry-run", action="store_true", help="只打印缺口，不派发")
    args = ap.parse_args()

    from common.platform_utils import force_utf8_stdio

    force_utf8_stdio()

    planners = {
        c: CoursePlanner(c, local_slots=args.local_slots, window=args.window) for c in args.courses
    }
    plans: list[RoundPlan] = []
    for c, pl in planners.items():
        got, _ = pl.rounds(it_from=args.it_from, it_to=args.it_to)
        log(
            f"[backfill] {c}: 缺口 {len(got)} 轮（eval_every={pl.eval_every}，"
            f"每轮 {pl.expected} 局，账本 {pl.eval_jsonl}）"
        )
        plans.extend(got)
    # 按 iter 升序、同 iter 按课程名交错：两门课的缺口同时收窄，趋势线可比。
    plans.sort(key=lambda p: (p.it, p.course))
    if args.max_rounds > 0:
        plans = plans[: args.max_rounds]
    if not plans:
        log("[backfill] 无缺口 —— 什么都不用做")
        return 0
    _print_plan(plans, planners[args.courses[0]].expected)
    if args.dry_run:
        detail = ", ".join(f"{p.course}:it{p.it}(缺{p.missing})" for p in plans[:12])
        log(f"[backfill] dry-run 明细（前 12）：{detail}{' …' if len(plans) > 12 else ''}")
        return 0

    results: list[RoundResult] = []
    lock = threading.Lock()
    sem = threading.Semaphore(max(1, int(args.inflight)))

    def _worker(plan: RoundPlan) -> None:
        try:
            planner = planners[plan.course]
            log(f"[backfill] ▶ {plan.course} it{plan.it}（缺 {plan.missing} 局）— 开始派发")
            res = planner.run_round(plan, bun=args.bun)
            with lock:
                results.append(res)
            if res.ok:
                log(
                    f"[backfill] ✔ {plan.course} it{plan.it} {res.sec}s "
                    f"winRate={res.win_rate} games={res.games} nodes={res.nodes}"
                )
            else:
                log(
                    f"[backfill] ✘ {plan.course} it{plan.it} {res.sec}s "
                    f"games={res.games} dropped={res.dropped} — 未补齐，可重跑本脚本"
                )
        finally:
            sem.release()

    threads: list[threading.Thread] = []
    try:
        for plan in plans:
            sem.acquire()
            t = threading.Thread(
                target=_worker, args=(plan,), daemon=True, name=f"bf-{plan.course}-{plan.it}"
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log("[backfill] 中断：等已派发的轮自然收工（它们各自落账，不会丢）")
        raise

    ok_n = sum(1 for r in results if r.ok)
    log(f"[backfill] 收工：{ok_n}/{len(results)} 轮补齐")
    bad = [r for r in results if not r.ok]
    if bad:
        log("[backfill] 未补齐：" + ", ".join(f"{r.course}:it{r.it}" for r in bad))
        return 1
    return 0


if __name__ == "__main__":
    run_main(main)
