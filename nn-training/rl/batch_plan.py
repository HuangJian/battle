"""batch_plan — 批语料规划 + 判据 / 门（B 层纯函数面）。

2026-09-25（S25/B1）从 `rl/batch_eval.py` **纯搬**出来（成员逐字节对账见
`tests/test_batch_plan_split.py`）。它只回答两个问题：

  ① **这一批要跑哪些 unit** —— ladder 批读 `dashboard/src/evalboard/ladder.json`，
     verdict 批读课程关卡文件的 `stages[]`（`plan_units` / `plan_verdict_units` /
     `units_for_batch`）；`select_next_unit` 再按 `units.done` 挑下一个。
  ② **这台节点能不能派 / 这个错误要不要退避** —— `node_gate_reason`（节点门）与
     `is_transient_error`（背压/瞬断；单一实现仍在 `dist_common`，这里只是 B 层薄转发）。

因此本模块**零锁、零台账读写**：`batches.jsonl` 的读改写仍全住 `rl/batch_eval.py`
（B2 会把它们收进 `BatchStore`）。`rl/batch_eval.py` 顶部逐个再导出本模块的公开名
（`X as X`）⇒ 既有 `from rl.batch_eval import plan_units` 等调用点一行不改，
且 `batch_eval.X is batch_plan.X` 恒真。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import dist_common
from rl.jsonc import load as jsonc_load
from rl.queue import mm

REPO_ROOT = Path(__file__).resolve().parents[2]

LADDER_JSON = REPO_ROOT / "dashboard" / "src" / "evalboard" / "ladder.json"
#: 判决语料注册表（P2，2026-09-19「中方案」）：课程关卡文件 + 池外 seed 段 + 多 ckpt。
#: 与 ladder.json 分家：rung 的语义是「arena 阶梯几何 + seg=k%16 推进」，判决语料塞进
#: rungs 会污染阶梯推进/去重键（详见该文件 _comment）；本文件只声明语料身份。
CORPORA_JSON = REPO_ROOT / "dashboard" / "src" / "evalboard" / "corpora.json"
LEVELS_DIR = REPO_ROOT / "nn-training" / "levels"

REGRESSION_EVERY = 3  # 与 runner.ts 同值（双侧镜像，改一侧必须同步另一侧）
BATCH_STAGE_BASE = 2000
EVAL_SEED0 = 860001
SEGMENT_LEN = 100


def is_transient_error(e: BaseException) -> bool:
    """背压/瞬断判定（薄转发；**单一实现** = `dist_common.is_transient_error`）。

    A（rollout）/B/C（本层）三层共用同一判据：503 busy、502 隧道、10054 连接重置、
    超时一律不计节点失败 streak。本名保留以兼容既有引用与单测。
    """
    return dist_common.is_transient_error(e)


def node_gate_reason(ping: dict, local_bun: str, code_hash_local: str) -> str | None:
    """节点门判据（纯函数，单测覆盖）：None = 可用，否则拒派原因。

    门 = evalSupport ∧ stageJsonSupport ∧ bun major.minor 一致 ∧ codeHash 一致
    （codeHash 与 rollout 门同源，实现在 `dist_common.check_code_hash`）。
    """
    if not ping.get("evalSupport"):
        return "lacks evalSupport"
    if not ping.get("stageJsonSupport"):
        return "lacks stageJsonSupport"
    node_ver = str(ping.get("bunVersion", "?"))
    if mm(node_ver) != mm(local_bun):
        return f"bun version mismatch (node {node_ver} vs local {local_bun})"
    return dist_common.check_code_hash(ping, code_hash_local)


#: policy → agent 权重桶 `kind`。agent 的 `/v1/task` 按 **(kind, wver)** 精确查缓存桶
#: （无 policy 豁免，2026-09-19 核实）⇒ 上传权重与查询任务的 kind **必须同值**，
#: 否则一律 409 wver-not-cached。B 层（policy nn）与 C 层（policy god）用 'rollout'；
#: intent/goal 是各自独立的权重桶（agent 侧 `latestWeightsOfKind('intent'|'goal')`）。
KIND_FOR_POLICY: dict[str, str] = {
    "nn": "rollout",
    "god": "rollout",
    "intent-exec": "intent",
    "goal": "goal",
    "goal-god": "rollout",
}


def kind_for_policy(policy: str) -> str:
    """policy → 权重桶 kind（未知 policy 回落 'rollout'，与旧调用方逐字一致）。"""
    return KIND_FOR_POLICY.get(str(policy), "rollout")


def load_ladder() -> dict:
    with open(LADDER_JSON, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("rungs"), list):
        raise ValueError(f"ladder.json 形态非法: {LADDER_JSON}")
    return doc


def plan_units(ladder: dict, ladder_pos: int, k: int) -> list[dict]:
    """批次语料规划（runner.ts planBatch 的 Python 镜像：同 seg 当前+下一关，
    k%3==2 加回归位段 0；rung 数据取 ladder.json，不另立一套）。

    每 unit：{rung, lives, level, difficulty, maxTicks, mapHash, stageJson,
    stageId, seed0, seeds}。
    """
    rungs = ladder["rungs"]
    if not (0 <= ladder_pos < len(rungs)):
        raise ValueError(f"ladderPos {ladder_pos} 越界（{len(rungs)} 关）")
    seg = k % 16
    units: list[dict] = []

    def _unit(r: dict, s: int) -> dict:
        sj = json.dumps(
            {
                "name": r["stage"]["name"],
                "grid": [],
                "tiles26": r["stage"]["tiles"],
                "forces": _forces_of(r["stage"]),
                "count": r["stage"].get("enemyCount") or len(r["stage"].get("enemies", [])),
                **(
                    {"player_spawn": r["stage"]["playerSpawn"]}
                    if r["stage"].get("playerSpawn")
                    else {}
                ),
                **(
                    {"enemy_spawns": r["stage"]["enemySpawns"]}
                    if r["stage"].get("enemySpawns")
                    else {}
                ),
            }
        )
        return {
            "rung": r["id"],
            "lives": r["lives"],
            "level": r["level"],
            "difficulty": r["difficulty"],
            "maxTicks": r["max_ticks"],
            "mapHash": r["mapHash"],
            "stageJson": sj,
            # 与 runner.ts stageJsonHash 同式（agent resultCache 键分量）。
            "stageJsonHash": hashlib.sha256(sj.encode()).hexdigest()[:16],
            "stageId": BATCH_STAGE_BASE + int(r["idx"]),
            "seed0": EVAL_SEED0 + SEGMENT_LEN * (s % 16),
            "seeds": [EVAL_SEED0 + SEGMENT_LEN * (s % 16) + i for i in range(100)],
        }

    units.append(_unit(rungs[ladder_pos], seg))
    if ladder_pos + 1 < len(rungs):
        units.append(_unit(rungs[ladder_pos + 1], seg))
    if k % REGRESSION_EVERY == REGRESSION_EVERY - 1:
        units.append(_unit(rungs[ladder_pos], 0))
    return units


def corpora_path() -> Path:
    """语料注册表路径（`EVALBOARD_CORPORA` 覆盖——与 `EVALBOARD_DATA` 同惯例，冒烟/单测用）。"""
    return Path(os.environ.get("EVALBOARD_CORPORA", str(CORPORA_JSON)))


def load_corpora() -> dict:
    """读判决语料注册表（dashboard/src/evalboard/corpora.json）。"""
    path = corpora_path()
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("corpora"), list):
        raise ValueError(f"corpora.json 形态非法: {path}")
    return doc


def corpus_doc(corpus_id: str) -> dict:
    """按 id 取语料声明；未知 id → ValueError（响亮失败，不默默跑空语料）。"""
    for c in load_corpora()["corpora"]:
        if isinstance(c, dict) and str(c.get("id")) == corpus_id:
            return c
    raise ValueError(f"未知判决语料 id: {corpus_id!r}（见 {corpora_path()}）")


def plan_verdict_units(corpus: dict, ckpts: list[dict]) -> list[dict]:
    """判决批语料规划（纯函数，可单测）：一个 unit = 一个 (ckpt × 关卡)。

    - 语料 = 课程关卡文件的 `stages[]` **原样**进 stageJson（与
      `eval-course-ckpt.ts` 同形：`JSON.stringify(stage)` ⇒ 这里用紧凑 separators
      对齐字节，否则同关会在 agent 侧变成两个不同缓存键）。
    - stage id = `BATCH_STAGE_BASE + 关内下标`（2000+i，与课程自定义关同基址）。
    - seed 段 = `seed0 + 0..games_per_stage-1`；**每个 ckpt 每个关跑同一批种子**
      ⇒ 跨 ckpt 天然逐局配对（§3.5④ 要求同 seed 集，不许事后求交集）。
    - unit 自带 `ckpt`/`ckpt_label`：多 ckpt 同批的关键（runner 按 unit 的权重发车，
      而不是批次级的单一 `rl_path`）。
    """
    level = str(corpus.get("level") or "")
    if not level:
        raise ValueError("corpus.level 缺失")
    lv = jsonc_load(str(LEVELS_DIR / f"{level}.jsonc"))
    stages = lv.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"关卡文件无 stages: {level}")
    seed0 = int(corpus.get("seed0") or 0)
    gps = int(corpus.get("games_per_stage") or 0)
    if seed0 <= 0 or gps <= 0:
        raise ValueError(f"corpus seed0/games_per_stage 非法: {seed0}/{gps}")
    player = lv.get("player") or {}
    lives = int(player.get("lives") or 3)
    plevel = int(player.get("level") or 0)
    difficulty = str(lv.get("difficulty") or "hard")
    max_ticks = int(lv.get("max_ticks") or 36000)
    seeds = [seed0 + i for i in range(gps)]
    units: list[dict] = []
    for ci, ck in enumerate(ckpts):
        path = str((ck or {}).get("path") or "")
        if not path:
            raise ValueError("ckpts[].path 缺失")
        label = str((ck or {}).get("label") or path or f"ckpt{ci}")
        for si, st in enumerate(stages):
            sj = json.dumps(st, separators=(",", ":"), ensure_ascii=False)
            units.append(
                {
                    "unit_idx": len(units),
                    "rung": f"{corpus.get('id')}#{(st or {}).get('name') or si}",
                    "lives": lives,
                    "level": plevel,
                    "difficulty": difficulty,
                    "maxTicks": max_ticks,
                    # 与 runner.ts stageJsonHash 同式（agent resultCache 键分量）。
                    "mapHash": hashlib.sha256(sj.encode()).hexdigest()[:16],
                    "stageJson": sj,
                    "stageJsonHash": hashlib.sha256(sj.encode()).hexdigest()[:16],
                    "stageId": BATCH_STAGE_BASE + si,
                    "seed0": seed0,
                    "seeds": seeds,
                    "ckpt": path,
                    "ckpt_label": label,
                }
            )
    return units


def units_for_batch(batch: dict) -> list[dict]:
    """批 → 展开的 unit 列表（ladder 批走 ladder.json；verdict 批走语料注册表）。

    `maybe_dispatch_batch` / `kick-once.py` 共用同一份展开，避免判决批只在训练
    主循环里能用、一次性 kick 却跑不了（两处各写一份必然漂移）。
    """
    if str(batch.get("kind") or "ladder") == "verdict":
        return plan_verdict_units(
            corpus_doc(str(batch.get("corpus") or "")), list(batch.get("ckpts") or [])
        )
    return plan_units(load_ladder(), int(batch.get("ladder_pos", 0) or 0), int(batch.get("k_seq", 0) or 0))


_KIND_CHAR = {"basic": "a", "fast": "b", "power": "c", "armor": "d", "player": "a"}


def _forces_of(stage: dict) -> str:
    return "".join(_KIND_CHAR.get(k, "a") for k in stage.get("enemies", []))


def batch_iter_id(run_id: str, batch_id: str) -> str:
    """B/C 批 iterId 基（+ f"u{unit}"；与 runner.ts batchIterId 同式）。"""
    short = hashlib.sha256(batch_id.encode()).hexdigest()[:8]
    return f"{run_id}.b{short}"


def select_next_unit(
    units: list[dict], done: set[int], only: list[str] | None
) -> tuple[list[dict], int | None, dict | None]:
    """下一待跑单元（纯函数，可单测）：only_rungs 过滤后序号重排（确定性 ⇒ 跨窗一致）。"""
    if only:
        units = [u for u in units if u.get("rung") in only]
        if not units:
            return units, None, None
    for i, u in enumerate(units):
        if i not in done:
            return units, i, u
    return units, None, None

