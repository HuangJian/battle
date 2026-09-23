"""eval_course_once — 一次性课程评估入口：任意课程文件 × 任意权重 × 种子段。

**为什么是 Python 而不是再写一套 TS 调度器**（用户 2026-09-19 质问，判定成立）：
节点通信与重试机制早已在 Python 侧且经长期实战检验——

  * 节点门 / codeHash 判据：`dist_common.check_code_hash`（SSOT = codehash-files.txt）
  * ping + 任务下发 + 退避重试 + 权重下发 + wver/409：`dist_common.fetch_task` / `post_weights_parallel`
  * 失败连击停用（nodeFailStreak）、EVAL_TASK_ATTEMPTS 重排队：`BatchEvalRunner`
  * 本机份额：`policy.evalLocalSlots`（缺省 `eval_local.EVAL_LOCAL_SLOTS_DEFAULT`）；
    机器级配置链（`rl.local_slots`）由调用方 TS 侧 `dist-node-gate.configLocalSlots`
    解析后以 `spec.localSlots` 显式传入 —— 本文件**不重读** rl-config 的槽位语义
  * 队列与尾竞速：`rl/queue.py`

TS 侧曾把这些又实现了一遍（探测/重试/rescan/停用/本机槽位），既漂移又漏护栏。
本文件只做**入口翻译**：把「课程文件 + 多权重 + 种子段 + 局数」翻成 B 层的
(batch, unit) 逐个跑完，再把逐局行按 `tools/sim/eval-course-ckpt.ts` 的产物契约写成
JSONL。节点的活全部交给 `BatchEvalRunner`。

批次身份：**临时批次**（用户 2026-09-19 拍板）——不登记语料、不写 EvalBoard 台账，
`EVALBOARD_DATA` 指向本次运行的 runDir，跑完只留 JSONL 与 runDir 内的原始账本。

用法（必须走官方解释器包装，见 AGENTS §5）：
  bun tools/sim/eval-course-ckpt.ts --course ... --weights ...            # TS 侧自动调用
  bash tools/githook/nn-py-safe.sh nn-training/rl/eval_course_once.py --spec tmp/spec.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# 入口放在 nn-training/ 顶层（不能放 rl/：脚本目录会在 sys.path[0]，rn-training/rl/queue.py
# 会遮蔽 stdlib `queue` ⇒ concurrent.futures 导入即爆循环导入）。仓库根 = 本文件上溯 1 层。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nn-training"))

# 本入口的 stdout 是**调用方的产物通道**（`--out` 缺省时 eval-course-ckpt 的行就走
# stdout），而训练栈的 `rl.log.log()` 按设计写 stdout（run_rl 的日志流）。整体改道
# stderr——否则调用方的 stdout 会被日志行污染（2026-09-19 实测于 m1 链路）。
sys.stdout = sys.stderr


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _stage_plan(games: int, n_stages: int, seed0: int) -> list[list[int]]:
    """每关的种子段：与 TS `buildCourseJobs` 同一映射（stageLocal = g % n，seed = seed0 + g // n）。

    独立实现一次（单测对拍），避免两端各写一份隐式约定。
    """
    out: list[list[int]] = [[] for _ in range(n_stages)]
    for g in range(games):
        out[g % n_stages].append(seed0 + g // n_stages)
    return out


def _row_id(seed0: int, n_stages: int, games: int, weight_idx: int, stage_local: int, seed: int) -> int:
    """`buildCourseJobs` 的 id 反解：g = (seed - seed0) * n + stageLocal，id = wi * games + g。"""
    g = (seed - seed0) * n_stages + stage_local
    return weight_idx * games + g


def weight_key16(w: dict) -> str:
    """单元/行的权重身份键 —— 与 B 层行 `ckpt_sha16` **同式**：

    * nn：权重文件 sha256 前 16 hex（B 层 `wver[:16]`）；
    * god（path 为空）：`god-<engineEpoch 前 12>`（B 层 god 分支逐字同式）。

    为什么元数据必须按它分键（2026-09-19）：`meta[(stage, seed)]` 是**关卡内**身份，
    同一次调用跑两个权重时两张表键完全相同 ⇒ 后一个权重把前者的 label/weightIdx 全盖掉，
    于是逐行 label 全错、`_row_id` 的 `wi * games` 也全错（多权重产物实际不可用）。
    """
    import dist_common

    path = str(w.get("path") or "")
    if not path:
        return f"god-{dist_common.compute_engine_epoch()[:12]}"
    return dist_common.weights_fingerprint(path)[:16]


def build_course_units(
    weights: list[dict],
    stages: list[dict],
    plan: list[list[int]],
    *,
    max_ticks: int,
    difficulty: str,
    lives: int,
    level: int,
    base_stage_id: int = 2000,
) -> tuple[list[dict], dict[tuple[str, int, int], dict]]:
    """(units, meta)：**每个权重一个单元**、跨该权重的全部关卡（纯函数，单测覆盖）。

    meta 键 = `(weight_key16, stageId, seed)`（多权重调用下逐权重分表，见 weight_key16）。

    为什么不再按关卡拆单元（用户 2026-09-19 第 2 条：「不要分什么 u0/u1/u2 阶段，一直持续
    不停派发，直到所有 eval 任务完成」）：单元边界在 B 层是**串行屏障**——权重下发 → 派发 →
    等全部结算，才开下一个单元，且每个单元开头都会重新 ping + 传一次权重。4 关 = 4 段串行，
    中间必然出现「CPU 满一阵、掉一阵」的锯齿。

    现在一个权重一条**连续**派发流（`unit["pairs"]` 取代 stageId × seeds 展开，
    `unit["stageParams"]` 带逐关参数），直到它的全部 (关, 种子) 结算完。
    unit 级字段仍保留（= 第一关），供只认旧字段的消费方读。
    """
    units: list[dict] = []
    meta: dict[tuple[str, int, int], dict] = {}
    for wi, w in enumerate(weights):
        key16 = weight_key16(w)
        pairs: list[list[int]] = []
        stage_params: dict[str, dict] = {}
        for si, seeds in enumerate(plan):
            if not seeds:
                continue
            stage_id = base_stage_id + si
            stage = stages[si] if isinstance(stages[si], dict) else {}
            stage_params[str(stage_id)] = {
                "stageJson": json.dumps(stages[si], ensure_ascii=False),
                "maxTicks": max_ticks,
                "difficulty": difficulty,
                "lives": lives,
                "level": level,
            }
            for seed in seeds:
                pairs.append([stage_id, int(seed)])
                meta[(key16, stage_id, int(seed))] = {
                    "label": str(w["label"]),
                    "weightIdx": wi,
                    "stageName": str(stage.get("name") or "custom"),
                }
        if not pairs:
            continue
        first_id = int(pairs[0][0])
        units.append(
            {
                "rung": f"{w['label']}·all{len(stage_params)}",
                "stageId": first_id,
                "seeds": [p[1] for p in pairs if p[0] == first_id],
                "pairs": pairs,
                "stageParams": stage_params,
                "maxTicks": max_ticks,
                "difficulty": difficulty,
                "stageJson": stage_params[str(first_id)]["stageJson"],
                "lives": lives,
                "level": level,
                "ckpt": w["path"],
            }
        )
    return units, meta


def _reset_run_ledgers(run_dir: Path) -> list[str]:
    """清掉本次 runDir 里的旧账本（eval_log + dist-agent-meta），返回被删文件名。

    为什么必须两个都清：`eval_log.jsonl` 每次运行都会 unlink 重建，而
    `dist-agent-meta.jsonl` 只是**追加** ⇒ 同一个 `--out` 重跑（runDir 复用）时，
    meta 里混着上一轮的记录：上一轮跑的局会以第二副面孔出现。2026-09-19 实测
    `tmp/x20-powered-zeroshot.jsonl.run/dist-agent-meta.jsonl` 在 200 局的重跑里
    积了 343 条（114 条是**上一轮**的远端局，标着远端 + 本地各一条）——按它判读
    “这批局谁跑的/是否降级本地”会得出反的结论（本文件的 provenance 汇总因此一度被误读）。
    """
    removed: list[str] = []
    for name in ("eval_log.jsonl", "dist-agent-meta.jsonl"):
        p = run_dir / name
        if p.exists():
            p.unlink()
            removed.append(name)
    return removed


def _to_tool_row(row: dict, meta: dict, n_stages: int, seed0: int, games: int) -> dict:
    """B 层逐局行 → eval-course-ckpt 的 JSONL 契约（字段名保持既有产物不变）。

    元数据按 `(ckpt_sha16, stage, seed)` 取：B 层行自带 `ckpt_sha16`（= 权重身份），
    多权重调用据此各归各的 label/weightIdx（见 weight_key16）。
    """
    stage_local = int(row["stage"]) - 2000
    seed = int(row["seed"])
    m = meta[(str(row.get("ckpt_sha16") or ""), int(row["stage"]), seed)]

    def bool_of(key: str) -> bool:
        return row.get(key) == 1 or row.get(key) is True

    def arr(key: str) -> list:
        v = row.get(key)
        return v if isinstance(v, list) else []

    return {
        "label": m["label"],
        "id": _row_id(seed0, n_stages, games, m["weightIdx"], stage_local, seed),
        "stageId": int(row["stage"]),
        "stageName": m["stageName"],
        "seed": seed,
        "outcome": row.get("outcome"),
        "win": bool_of("win"),
        "cleared": bool_of("cleared"),
        "ticks": row.get("ticks") or 0,
        "kills": row.get("kills") or 0,
        "enemyHits": row.get("enemyHits") or 0,
        "playerHits": row.get("playerHits") or 0,
        "playerDamageTaken": row.get("playerDamageTaken") or 0,
        "playerShots": row.get("playerShots") or 0,
        "powerUpsCollected": row.get("powerUpsCollected") or 0,
        "score": row.get("score") or 0,
        "hitsByKind": arr("hitsByKind"),
        "killsByKind": arr("killsByKind"),
        "exposureByKind": arr("exposureByKind"),
        "firstHitKind": row.get("firstHitKind"),
        "firstKillKind": row.get("firstKillKind"),
        "killOrder": arr("killOrder"),
        "killerKinds": arr("killerKinds"),
        # 来源（B 层行自带）：判读「这批局谁跑的」不再靠 TS 自报
        "node": row.get("node"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="one-shot course eval (reuses BatchEvalRunner)")
    ap.add_argument("--spec", required=True, help="JSON spec（TS 侧生成）")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))

    run_dir = Path(spec["runDir"]).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    # 诊断工具：缺省打**事件级**日志（逐局派发/返回 + 在飞采样 + 阶段墙钟），
    # 供排查「CPU 满一阵又掉档」。`EVAL_TRACE_EVENTS=0` 可关（批量跑不想刷日志时）。
    os.environ.setdefault("EVAL_TRACE_EVENTS", "1")
    # 临时批次：EvalBoard 数据根指向本次 runDir（心跳/台账都不进控制台既有数据）
    os.environ.setdefault("EVALBOARD_DATA", str(run_dir / "evalboard"))

    import dist_common
    from rl.batch_eval import ONESHOT_EVAL_KIND, BatchEvalRunner, data_root
    from rl.jsonc import load as jsonc_load
    from rl.queue import RUN_ID

    course_path = str(spec["course"])
    course = jsonc_load(course_path)
    stages = course.get("stages") or []
    if not stages:
        _log(f"[course-once] course has no custom stages: {course_path}")
        return 2
    player = course.get("player") or {}
    max_ticks = int(course.get("max_ticks") or spec.get("maxTicks") or 36000)
    lives = int(player.get("lives", 3))
    level = int(player.get("level", 0))
    difficulty = str(course.get("difficulty") or spec.get("difficulty") or "hard")
    policy = str(spec.get("policy") or "nn")
    games = int(spec["games"])
    seed0 = int(spec.get("seed0") or 0)
    weights = [{"label": str(w["label"]), "path": str(w["path"])} for w in spec.get("weights") or []]
    if policy != "god" and not weights:
        _log("[course-once] --policy nn needs at least one weight")
        return 2
    n_stages = len(stages)
    plan = _stage_plan(games, n_stages, seed0)

    # 配置文件：显式 --dist-nodes 优先（`load_dist_config` 接受路径），否则默认 rl-config.json
    cfg = dict(dist_common.load_dist_config(str(spec.get("distCfgPath") or dist_common.CONFIG_PATH)) or {})
    if spec.get("noNodes"):
        cfg = {**cfg, "nodes": []}
    # 本机槽位：默认由配置决定（`policy.evalLocalSlots` → `EVAL_LOCAL_SLOTS_DEFAULT`，
    # 与 A/B/C 层同一来源）；调用方显式给了 --dist-local 时才在**内存里**覆盖
    # （绝不回写配置文件）。
    if spec.get("localSlots") is not None:
        cfg["policy"] = {**(cfg.get("policy") or {}), "evalLocalSlots": int(spec["localSlots"])}
    nodes = [n for n in (cfg.get("nodes") or []) if n.get("enabled", True)]
    _log(
        f"[course-once] course={course_path} stages={n_stages} games/weights={games} "
        f"policy={policy} weights={len(weights)} difficulty={difficulty} max_ticks={max_ticks} "
        f"lives={lives} level={level} nodes={len(nodes)} runDir={run_dir}"
    )

    eval_log = run_dir / "eval_log.jsonl"
    # 临时批次：每次运行都是干净的账（续跑靠显式 --resume，暂不提供）。
    _reset_run_ledgers(run_dir)
    batch = {
        "batch_id": f"adhoc-{spec.get('iterId') or int(time.time() * 1000)}",
        "iter": 0,
        "course": Path(course_path).stem,
        "policy": policy,
        "units": {},
    }
    # args.local_slots 不用：本机份额已在上面解析进 cfg["policy"]["evalLocalSlots"]
    #（显式 spec.localSlots = TS 侧 configLocalSlots 的解析结果，含 rl.local_slots）
    args = SimpleNamespace(mode="per-tick", out="", eval_window_sec=float(spec.get("windowSec") or 86400))
    bun = shutil.which("bun") or "bun"
    epoch = dist_common.compute_engine_epoch()

    units, meta = build_course_units(
        weights if policy != "god" else [{"label": "god", "path": ""}],
        stages,
        plan,
        max_ticks=max_ticks,
        difficulty=difficulty,
        lives=lives,
        level=level,
    )
    if not units:
        _log("[course-once] no pairs to evaluate (empty seed plan?)")
        return 2
    _log(
        f"[course-once] units={len(units)}（每权重一个连续单元，跨 {n_stages} 关）"
        f" games/weight={games}"
    )

    for i, unit in enumerate(units):
        rl_path: str | None = None if policy == "god" else str(unit["ckpt"])
        runner = BatchEvalRunner(
            bun,
            rl_path,
            eval_log,
            args,
            cfg,
            batch,
            unit,
            i,
            len(units),
            RUN_ID,
            epoch,
            policy,
            None,
            "",  # init_sha16（按位置，缺省空串）
            # 专用权重桶（见 ONESHOT_EVAL_KIND）：训练作业每轮重写 'rollout' 桶，
            # 一次性评估那份固定权重会被节点侧的保留份数收敛扫掉（→ ENOENT/10054）。
            # **按关键字**：它前面还有 init_sha16，位置写错会静默回落 'rollout'。
            kind=ONESHOT_EVAL_KIND,
        )
        t0 = time.time()
        res = runner.run()
        _log(
            f"[course-once] unit {i + 1}/{len(units)} {unit['rung']}: "
            f"settled={res.get('settled')}/{res.get('total')} dropped={res.get('dropped')} "
            f"({time.time() - t0:.1f}s)"
        )

    if not eval_log.exists():
        _log("[course-once] no rows written (all units deferred?)")
        return 1
    raw = [
        json.loads(line)
        for line in eval_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [_to_tool_row(r, meta, n_stages, seed0, games) for r in raw]
    rows.sort(key=lambda r: r["id"])
    out_path = Path(spec["out"]).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    # 来源汇总（节点侧自报，不再靠 TS 侧猜）
    by_node: dict[str, int] = {}
    for r in rows:
        by_node[str(r["node"])] = by_node.get(str(r["node"]), 0) + 1
    provenance = ", ".join(f"{k}={v}" for k, v in sorted(by_node.items()))
    _log(f"[course-once] provenance: {provenance}（共 {len(rows)} 局）")
    _log(f"[course-once] wrote {len(rows)} rows -> {out_path} (evalboard data root {data_root()})")

    expected = games * (len(weights) if policy != "god" else 1)
    if len(rows) != expected:
        _log(f"[course-once] incomplete rows {len(rows)}/{expected} — 节点忙/不可达时属预期，重跑即可")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
