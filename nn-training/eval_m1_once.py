"""eval_m1_once — 一次性 m1 干净评估入口（内置关 × 种子段 × 权重）。

**为什么是 Python 而不是 TS 里再写一套调度**（用户 2026-09-19 裁定，与
`eval_course_once.py` 同一决定）：节点通信与重试机制早已在 Python 侧且经长期实战
检验——`dist_common.fetch_task`（ping/门/退避重试/权重下发/wver 409）、
`BatchEvalRunner`（失败连击停用 + 单局重排队 + 窗口 yield + 断点去重）、
`policy.evalLocalSlots`（本机份额）。TS 侧只在**无分派**时用本机 worker 池跑
（那是游戏引擎本身，不是节点通信）。

本文件只做**入口翻译**：把「内置关索引 + 种子段 + policy + 权重 + 难度」翻成 B 层的
(stage, unit) 跑完，再把逐局行按 `tools/sim/m1-eval.ts` 的产物契约写成 JSONL。

契约要点：
  * 逐局行随 `scorable`（scoreV7 的完整输入：finalState + telemetry）原样回传——
    TS 侧用同一份 `scoreRun` 打分，不做字段级搬运 ⇒ 不会两端漂移。为此 runner 以
    `include_scorable=True` 打开（默认关，A/B/C 层的行逐字节不变）。
  * 内置关 = `stageId` 0..34，`stageJson` 空 ⇒ agent/export-eval-game 走内置关解析。
  * lives/level **不做覆盖**（unit 不带这两个键）：难度默认说了算，与 TS 分派路径
    不传 livesOverride 的语义一致（写死 3 会把难度默认硬编码成常数）。
  * 断点续跑 = runDir 里的 eval_log（同 wver 的 (stage,seed) 由 `_done_keys` 跳过）；
    `fresh=true` 先清空该文件。

用法（必须走官方解释器包装，见 AGENTS §0.1 规则 13）：
  bun tools/sim/m1-eval.ts --stages all --seeds 1-10 --policy goal ...   # TS 侧自动调用
  bash tools/githook/nn-py-safe.sh nn-training/eval_m1_once.py --spec tmp/spec.json
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

# 入口放 nn-training/ 顶层（不能放 rl/：脚本目录进 sys.path[0] 会把 rl/queue.py 认成
# stdlib `queue` ⇒ concurrent.futures 导入即炸）。仓库根 = 本文件上溯 1 层。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nn-training"))

# 本入口的 stdout 是**调用方的产物通道**（`tools/sim/m1-eval.ts` 的 JSON 报告就写在
# stdout，`rl/eval_m1.py` 解析它取 perGame），而训练栈的 `rl.log.log()` 按设计写 stdout
#（run_rl 的日志流）。这里整体改道 stderr——否则调用方的 stdout 会被日志行污染，
# 逐局行解析**静默失败**（2026-09-19 实测：[dist] weights[…] 行混进了 stdout）。
sys.stdout = sys.stderr

#: 可经 agent 分派的 policy（kind 见 rl/batch_eval.KIND_FOR_POLICY）。其余 policy
#: （intent / intent-oracle / goal-god）无远端对应物，TS 侧只在本机跑，不进这里。
DISPATCHABLE = ("nn", "intent-exec", "goal", "god")


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def plan_units(spec: dict) -> list[dict]:
    """spec → B 层 unit 列表（纯函数，可单测）。

    调用方显式给 units（TS 侧负责语料/重跑子集），本函数只做**校验 + 归一**：
    每 unit 一个 stageId（内置关索引）+ 一组 seed，空 seed / 越界 stage 直接拒。
    这样 TS 的「错误局重跑」只需把子集 spec 再发一次，语料口径仍只有一处。
    """
    policy = str(spec.get("policy") or "nn")
    out: list[dict] = []
    for u in spec.get("units") or []:
        stage_id = int(u["stageId"])
        seeds = [int(s) for s in (u.get("seeds") or [])]
        if not seeds:
            continue
        if stage_id < 0:
            raise ValueError(f"stageId must be >= 0 (built-in stage index), got {stage_id}")
        out.append(
            {
                "rung": f"{policy}·s{stage_id}",
                "stageId": stage_id,
                "seeds": seeds,
                # 内置关：空 stageJson（agent/export 侧按 stage 索引解析）
                "stageJson": "",
                "maxTicks": int(spec.get("maxTicks") or 36000),
                "difficulty": str(spec.get("difficulty") or "hard"),
                "ckpt": str(spec.get("weights") or ""),
            }
        )
    return out


def to_m1_row(row: dict) -> dict:
    """B 层逐局行 → m1-eval 的 JSONL 契约（纯函数，可单测）。

    字段名与 `tools/sim/m1-eval.ts` 的 `perGame` 同源（`rl/eval_m1.py` 按它入账），
    另加 `node`（本批谁跑的，节点侧自报）与 `scorable`（scoreV7 原始输入）。
    """
    scorable = row.get("scorable") if isinstance(row.get("scorable"), dict) else None
    final = (scorable or {}).get("finalState") or {}
    truthy = lambda v: v == 1 or v is True  # noqa: E731  # 行里 win/cleared 是 0/1，顶层是 bool
    return {
        "stage": row.get("stage"),
        "seed": row.get("seed"),
        "node": row.get("node"),
        "ok": str(row.get("outcome") or "") != "error",
        "outcome": row.get("outcome"),
        "win": truthy(row.get("win")),
        "cleared": truthy(row.get("cleared")),
        "ticks": row.get("ticks") or 0,
        "kills": row.get("kills") or 0,
        "lives": final.get("lives"),
        "baseAlive": final.get("baseAlive"),
        "firstKillTick": row.get("firstKillTick"),
        "enemyTotal": row.get("enemyTotal"),
        "playerDeaths": row.get("playerDeaths"),
        "playerShots": row.get("playerShots"),
        "powerUpsCollected": row.get("powerUpsCollected"),
        "playerLevel": row.get("playerLevel"),
        "cellsVisited": row.get("cellsVisited"),
        "scorable": scorable,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="one-shot m1 eval (reuses BatchEvalRunner)")
    ap.add_argument("--spec", required=True, help="JSON spec（TS 侧生成）")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))

    policy = str(spec.get("policy") or "nn")
    if policy not in DISPATCHABLE:
        _log(f"[m1-once] policy {policy} is not dispatchable ({'|'.join(DISPATCHABLE)})")
        return 2

    run_dir = Path(spec["runDir"]).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    # 临时批次：EvalBoard 数据根指向本次 runDir（心跳/台账不进控制台既有数据）
    os.environ.setdefault("EVALBOARD_DATA", str(run_dir / "evalboard"))

    import dist_common
    from rl.batch_eval import ONESHOT_EVAL_KIND, BatchEvalRunner
    from rl.queue import RUN_ID

    units = plan_units(spec)
    if not units:
        _log("[m1-once] no units in spec")
        return 2
    games = sum(len(u["seeds"]) for u in units)

    cfg = dict(dist_common.load_dist_config(str(spec.get("distCfgPath") or dist_common.CONFIG_PATH)) or {})
    if spec.get("noNodes"):
        cfg = {**cfg, "nodes": []}
    policy_cfg = dict(cfg.get("policy") or {})
    # 本机份额：显式 spec.localSlots 时在**内存里**覆盖（绝不回写配置文件）；缺省由
    # 配置决定（policy.evalLocalSlots → EVAL_LOCAL_SLOTS_DEFAULT），与训练循环同源。
    if spec.get("localSlots") is not None:
        policy_cfg["evalLocalSlots"] = int(spec["localSlots"])
    if spec.get("taskTimeoutSec"):
        policy_cfg["taskTimeoutSec"] = int(spec["taskTimeoutSec"])
    cfg["policy"] = policy_cfg
    nodes = [n for n in (cfg.get("nodes") or []) if n.get("enabled", True)]

    eval_log = run_dir / "eval_log.jsonl"
    if spec.get("fresh") and eval_log.exists():
        eval_log.unlink()  # --fresh：清空断点台账（缺省保留 = 续跑）

    _log(
        f"[m1-once] policy={policy} stages={len(units)} games={games} "
        f"difficulty={spec.get('difficulty')} max_ticks={spec.get('maxTicks')} "
        f"nodes={len(nodes)} localSlots={policy_cfg.get('evalLocalSlots')} "
        f"kind={ONESHOT_EVAL_KIND} runDir={run_dir}"
    )

    batch = {
        "batch_id": f"m1-{spec.get('iterId') or int(time.time() * 1000)}",
        "iter": int(spec.get("iter") or 0),
        "course": "m1-builtin",
        "policy": policy,
        "units": {},
    }
    args = SimpleNamespace(
        mode="per-tick",
        out="",
        eval_window_sec=float(spec.get("windowSec") or 86400),
    )
    bun = shutil.which("bun") or "bun"
    epoch = dist_common.compute_engine_epoch()
    weights_path = str(spec.get("weights") or "") or None

    for i, unit in enumerate(units):
        # policy nn 走学生权重；intent-exec/goal 走各自权重文件（同 rl_path 语义：
        # agent 按 (kind, wver) 查桶）；god 无权重语义（占位）。kind 一律用一次性
        # 专用桶 ONESHOT_EVAL_KIND（不蹭训练作业每轮重写的 'rollout'，见其注释）。
        rl_path = None if policy == "god" else weights_path
        # init_sha16 用单位置零外的权重指纹（agent 元数据列，缺因置空串即可）
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
            include_scorable=True,
        )
        t0 = time.time()
        res = runner.run()
        _log(
            f"[m1-once] unit {i + 1}/{len(units)} {unit['rung']}: "
            f"settled={res.get('settled')}/{res.get('total')} dropped={res.get('dropped')} "
            f"({time.time() - t0:.1f}s)"
        )

    if not eval_log.exists():
        _log("[m1-once] no rows written (all units deferred?)")
        return 1
    raw = [
        json.loads(line) for line in eval_log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    rows = [to_m1_row(r) for r in raw if isinstance(r, dict) and r.get("event") == "eval"]
    rows.sort(key=lambda r: (int(r["stage"] or 0), int(r["seed"] or 0)))
    out_path = Path(spec["out"]).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    by_node: dict[str, int] = {}
    for r in rows:
        by_node[str(r["node"])] = by_node.get(str(r["node"]), 0) + 1
    provenance = ", ".join(f"{k}={v}" for k, v in sorted(by_node.items()))
    _log(f"[m1-once] provenance: {provenance}（共 {len(rows)} 局，请求 {games}）")
    _log(f"[m1-once] wrote {len(rows)} rows -> {out_path}")
    if len(rows) < games:
        _log(
            f"[m1-once] incomplete rows {len(rows)}/{games} — 节点忙/不可达/窗口截断时属预期；"
            f"重跑同一 spec（不加 --fresh）即续跑缺口"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
