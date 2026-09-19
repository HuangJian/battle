"""batch_eval — B 层批次执行（plan/rl-eval-system.md §10.3 P2）。

BatchEvalRunner：结构参考 EvalDispatcher，复用 `fetch_task(mode='eval')` /
`run_local_eval_game`；语料由 `ladder.json` + `batches.jsonl` 驱动
（不要复用 dispatch_eval_bg：EvalDispatcher 无外部语料入口，§10.3）。

关键契约：
  - 节点门（§6.6）：enabled ∧ ping ∧ evalSupport ∧ stageJsonSupport ∧
    bunVersion 一致 ∧ **codeHash 一致**（= rollout 门同一判据；2026-09-17 起不再比
    ping.engineEpoch——见 dist_common.check_code_hash）——**严格拒派，不静默降级**（P2 DoD）。
  - iterId 命名空间 `{runId}.b{batchShort}u{unit}`（runner.ts）：agent taskKey
    无 policy 分量，命名空间隔离是 god/nn 不串键的唯一保证。
  - 窗口（§6.5）：只在窗口开时派新局；在途局自然跑完（taskTimeoutSec 封顶）；
    剩余局按 batch_unit 下窗续跑（units.done 进 batches.jsonl 台账）。
  - 确定性契约（§3.4/P2 DoD）：同 ckpt + 同 seed 集 + 同 stage ⇒ gameplay
    字段逐字节一致（node/elapsedSec/phase/ts 不要求）。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import random
import shutil
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import dist_common
from rl.eval_local import (
    EVAL_LOCAL_SLOTS_DEFAULT,
    EVAL_TASK_ATTEMPTS,
    eval_census_fields,
    eval_loot_fields,
    run_local_eval_game,
)
from rl.jsonc import load as jsonc_load
from rl.log import log
from rl.queue import _record_agent_meta, bun_version, mm
from train.loop_util import acquire_lock, cleanup_lock

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "dashboard" / "data" / "evalboard"
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


def utc_now_iso() -> str:
    """UTC ISO-8601 带毫秒 + Z —— 与 console 侧 `new Date().toISOString()` 同格式。

    `consume_requests` / `enqueueCovered` 用**字符串比较**判断"批是否已物化"
    （`batch.created_ts >= req.ts`），两侧格式必须逐字符可比。本地时间的
    `time.strftime` 不带毫秒不带 Z，在 UTC+8 下恰好"看起来更晚"而侥幸正确，
    换到 UTC 或负偏移时区就会误判为未物化 ⇒ 重复建批。
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def data_root() -> Path:
    """EvalStore 数据根（EVALBOARD_DATA 覆盖；默认 dashboard/data/evalboard）。"""
    return Path(os.environ.get("EVALBOARD_DATA", str(DEFAULT_DATA_ROOT)))


def _heartbeat(**patch: object) -> None:
    """R4-G1：写 EvalBoard 心跳（失败静默——心跳绝不打断单元）。"""
    try:
        from rl.eval_heartbeat import write_state

        write_state(**patch)
    except Exception:
        pass


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


# ────────────────────── EvalBoard 批队列跨进程互斥（plan P4-W4 / A1） ──────────────────
# 多课程并行 = 多个 trainer 进程各自的 EvalBoard 线程读写同一 store 的
# `batches.jsonl`，而认领/标记全是 read-modify-write；无锁时两个进程会同时认领同一
# 批（双花）或互相覆盖 units.done（丢批）。这里复用 `train.loop_util` 的 PID 锁
# （**拒绝第三套锁实现**，plan P4-W4）：跨进程用文件锁 `claim.lock`，进程内另用
# RLock 串行化（同一进程的 eval 线程 / 主循环 idle 窗本就并发）。
# 拿不到锁（另一进程正在认领）→ 跳过本轮，下一 idle 窗重试，_que_ 绝不无锁写。
_CLAIM_LOCK_NAME = "claim.lock"
_CLAIM_WAIT_SEC = 2.0
_claim_local = threading.RLock()
_claim_held = threading.local()


@contextmanager
def _claim_guard(root: Path):
    """yield True = 已持锁；False = 2s 内未取得（调用方跳过本轮）。可重入。"""
    with _claim_local:
        key = str(root)
        if getattr(_claim_held, "key", "") == key:
            yield True  # 本线程已持锁（嵌套调用：claim_pending → consume_requests）
            return
        root.mkdir(parents=True, exist_ok=True)
        lock_path = str(root / _CLAIM_LOCK_NAME)
        deadline = time.time() + _CLAIM_WAIT_SEC
        ok = False
        while True:
            if acquire_lock(lock_path, tag="batcheval claim"):
                ok = True
                break
            if time.time() >= deadline:
                break
            time.sleep(0.2)
        _claim_held.key = key if ok else ""
        try:
            yield ok
        finally:
            _claim_held.key = ""
            if ok:
                cleanup_lock(lock_path)


def _claim_locked(fn):
    """把 batches.jsonl 读改写入口包进跨进程锁；未取得锁 → 返回 None（跳过本轮）。"""

    @functools.wraps(fn)
    def wrapper(root, *args, **kwargs):
        with _claim_guard(Path(root)) as ok:
            if not ok:
                log(f"[batcheval] claim.lock 忙——跳过本轮 {fn.__name__}（下一 idle 窗重试）")
                return None
            return fn(root, *args, **kwargs)

    return wrapper


def read_batches(root: Path) -> list[dict]:
    p = root / "batches.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_batches(root: Path, batches: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "batches.jsonl").write_text(
        "".join(json.dumps(b) + "\n" for b in batches), encoding="utf-8"
    )


REQUESTS_FILE = "requests.jsonl"
REQUESTS_DONE_FILE = "requests.done.jsonl"


def _req_key(r: dict) -> str:
    rid = r.get("req_id")
    if isinstance(rid, str) and rid:
        return rid
    try:
        return json.dumps(r, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        return repr(sorted(str(k) for k in r))


def read_requests(root: Path) -> list[dict]:
    """读 console 请求文件（append-only，console 唯一写者；坏行跳过）。"""
    p = root / REQUESTS_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and isinstance(r.get("kind"), str):
            out.append(r)
    return out


def read_done_req_ids(root: Path) -> set[str]:
    """已消费请求 id 集（requests.done.jsonl；缺失即空集）。"""
    p = root / REQUESTS_DONE_FILE
    if not p.exists():
        return set()
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and isinstance(r.get("req_id"), str):
            out.add(str(r["req_id"]))
    return out


def mark_requests_done(root: Path, ids: set[str] | list[str]) -> None:
    """已消费标记（append-only，runner 唯一写者；requests.jsonl 本体永不重写）。"""
    ids = list(ids)
    if not ids:
        return
    root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(root / REQUESTS_DONE_FILE, "a", encoding="utf-8") as f:
        for i in ids:
            f.write(json.dumps({"req_id": i, "consumed_ts": ts}) + "\n")


def _verdict_key_of(corpus: str, ckpts: list) -> str:
    """判决批去重键：语料 id + ckpt 标签序列（顺序敏感——同批多 ckpt 的配对语义）。"""
    labels = [str((c or {}).get("label") or (c or {}).get("path") or "") for c in ckpts]
    return f"verdict|{corpus}|{','.join(labels)}"


def _verdict_key(b: dict) -> str:
    return _verdict_key_of(str(b.get("corpus") or ""), list(b.get("ckpts") or []))


def _same_enq_key(b: dict, course: str, rung_from: str, ckpt: str) -> bool:
    return (
        b.get("course") == course
        and b.get("rung_from") == rung_from
        and b.get("ckpt") == ckpt
    )


@_claim_locked
def consume_requests(root: Path) -> dict:
    """消费 console 请求文件（plan/evalboard-console-ux.md §5.3，P1/P4）。

    console 是 requests.jsonl 的唯一写者（append-only），本函数是唯一消费方：
      enqueue → 无同 key pending 批且未物化则建 pending 批；
      abort → pending/running 批标 aborted（在途 unit 跑完即停，见 mark_unit_done 守卫）；
      ladder_start/ladder_stop → 跳过（console ticker 持有，runner 不碰）。
    幂等：重复消费无副作用（去重 + 物化检查 + abort 复用）。
    在 claim_pending 头部调用 ⇒ idle 窗与 kick-once.py 自动覆盖。
    任何失败只记日志，绝不抛出（训练主链零风险）。
    """
    counts = {"consumed": 0, "enqueued": 0, "aborted": 0, "skipped": 0}
    try:
        reqs = read_requests(root)
        if not reqs:
            return counts
        done_ids = read_done_req_ids(root)
        fresh = [r for r in reqs if _req_key(r) not in done_ids]
        if not fresh:
            return counts
        batches = read_batches(root)
        dirty = False
        consumed: list[str] = []
        for r in fresh:
            kind = str(r.get("kind"))
            key = _req_key(r)
            if kind == "enqueue":
                course = str(r.get("course", ""))
                rung_from = str(r.get("rung_from", ""))
                ckpt = str(r.get("ckpt", ""))
                if not course or not rung_from or not ckpt:
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                if any(
                    b.get("status") == "pending"
                    and _same_enq_key(b, course, rung_from, ckpt)
                    for b in batches
                ):
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                req_ts = str(r.get("ts", ""))
                if req_ts and any(
                    _same_enq_key(b, course, rung_from, ckpt)
                    and str(b.get("created_ts", "")) >= req_ts
                    for b in batches
                ):
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                # 必须与 console 侧（TS `new Date().toISOString()`）同格式：UTC +
                # 毫秒 + Z。此前用本地时间 strftime（无毫秒无 Z），而
                # enqueueCovered/consume_requests 用**字符串比较**判"是否已物化"
                # ⇒ UTC+8 恰好成立、UTC/负偏移时区会误判为未物化而重复建批。
                now = utc_now_iso()
                stamp = now.replace("-", "").replace(":", "").replace("T", "")
                stamp = stamp.replace(".", "").replace("Z", "")
                try:
                    it = int(r.get("iter", 0))
                except (TypeError, ValueError):
                    it = 0
                trig = str(r.get("trigger", "standalone"))
                if trig not in ("main", "standalone", "auto-ladder"):
                    trig = "standalone"
                pol = str(r.get("policy", "nn"))
                if pol not in ("nn", "god"):
                    pol = "nn"
                batch: dict = {
                    "batch_id": f"b-{stamp}-{random.randrange(0x10000):04x}",
                    "course": course,
                    "rung_from": rung_from,
                    "ckpt": ckpt,
                    "requester": str(r.get("requester", "web")),
                    "created_ts": now,
                    "status": "pending",
                    "units": {"of": 2, "done": []},
                    "k_seq": 0,
                    "window_seq": 0,
                    "trigger": trig,
                    "iter": it,
                    "node_dist": {},
                    "elapsed_sec": None,
                    "policy": pol,
                }
                for opt in ("ladder_pos", "k_seq", "init_sha16", "only_rungs"):
                    if r.get(opt) is not None:
                        batch[opt] = r[opt]
                batches.append(batch)
                dirty = True
                consumed.append(key)
                counts["enqueued"] += 1
            elif kind == "verdict":
                # 判决批（P2）：语料 id + ckpts[]（多权重同批同种子配对）。
                corpus = str(r.get("corpus", ""))
                raw_ckpts = r.get("ckpts") or []
                ckpts = [
                    c
                    for c in raw_ckpts
                    if isinstance(c, dict) and str(c.get("path") or "")
                ]
                if not corpus or not ckpts:
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                vkey = _verdict_key_of(corpus, ckpts)
                if any(
                    b.get("status") == "pending" and _verdict_key(b) == vkey
                    for b in batches
                ):
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                req_ts = str(r.get("ts", ""))
                if req_ts and any(
                    _verdict_key(b) == vkey and str(b.get("created_ts", "")) >= req_ts
                    for b in batches
                ):
                    consumed.append(key)
                    counts["skipped"] += 1
                    continue
                now = utc_now_iso()
                stamp = now.replace("-", "").replace(":", "").replace("T", "")
                stamp = stamp.replace(".", "").replace("Z", "")
                try:
                    it = int(r.get("iter", 0))
                except (TypeError, ValueError):
                    it = 0
                pol = str(r.get("policy", "nn"))
                if pol not in ("nn", "god"):
                    pol = "nn"
                vbatch: dict = {
                    "batch_id": f"b-{stamp}-{random.randrange(0x10000):04x}",
                    "kind": "verdict",
                    "corpus": corpus,
                    "ckpts": ckpts,
                    "requester": str(r.get("requester", "web")),
                    "created_ts": now,
                    "status": "pending",
                    # units.of 由 plan_verdict_units 展平后回写（= ckpts × 关卡数）。
                    "units": {"of": 0, "done": []},
                    "k_seq": 0,
                    "window_seq": 0,
                    "trigger": "verdict",
                    "iter": it,
                    "node_dist": {},
                    "elapsed_sec": None,
                    "policy": pol,
                }
                for opt in ("init_sha16", "only_rungs"):
                    if r.get(opt) is not None:
                        vbatch[opt] = r[opt]
                batches.append(vbatch)
                dirty = True
                consumed.append(key)
                counts["enqueued"] += 1
            elif kind == "abort":
                bid = str(r.get("batch_id", ""))
                for b in batches:
                    if b.get("batch_id") == bid and b.get("status") in (
                        "pending",
                        "running",
                    ):
                        b["status"] = "aborted"
                        dirty = True
                        counts["aborted"] += 1
                        break
                consumed.append(key)
            elif kind in ("ladder_start", "ladder_stop"):
                continue  # console ticker 持有——runner 不消费、不标记
            else:
                consumed.append(key)  # 未知 kind：标记消费，防反复扫描
                counts["skipped"] += 1
        if dirty:
            write_batches(root, batches)
        if consumed:
            mark_requests_done(root, consumed)
        counts["consumed"] = len(consumed)
        if counts["enqueued"] or counts["aborted"]:
            log(f"[batcheval] consume_requests: {counts}")
        return counts
    except Exception as e:
        log(f"[batcheval] consume_requests failed (ignored): {type(e).__name__}: {e}")
        return counts


@_claim_locked
def claim_pending(root: Path) -> dict | None:
    """取最早可跑批并标 running。

    可跑 = pending，或 running 且仍有未完成 unit（进程重启 / yield 后孤儿批）。
    console 请求先经 consume_requests 物化为批（§5.3）；续跑靠本函数的 running 分支。
    """
    try:
        consume_requests(root)
    except Exception as e:
        log(f"[batcheval] consume_requests failed (ignored): {type(e).__name__}: {e}")
    batches = read_batches(root)
    for b in batches:
        st = b.get("status")
        units = b.get("units") or {}
        done = units.get("done") or []
        of = int(units.get("of") or 0)
        incomplete = of > 0 and len(done) < of
        if st == "pending" or (st == "running" and incomplete):
            b["status"] = "running"
            write_batches(root, batches)
            return b
    return None


@_claim_locked
def mark_unit_done(root: Path, batch_id: str, unit_idx: int, node_dist: dict) -> None:
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch_id:
            if b.get("status") == "aborted":
                # P4：在途 unit 收尾只回填 node_dist，不复活已中止批。
                b["node_dist"] = node_dist
                write_batches(root, batches)
                return
            units = b.setdefault("units", {"of": 0, "done": []})
            if unit_idx not in units.get("done", []):
                units["done"].append(unit_idx)
            b["node_dist"] = node_dist
            of = int(units.get("of") or 0)
            ndone = len(units.get("done") or [])
            if of > 0 and ndone >= of:
                b["status"] = "done"
            else:
                # 单元未全完：回 pending，下一 idle 窗领剩余 unit（yield/重启安全）。
                b["status"] = "pending"
            write_batches(root, batches)
            return


class BatchEvalRunner:
    """一批次中一个 100 局单元的派发器（阻塞版，调用方放后台线程跑）。

    任何失败只记日志，绝不抛出。窗口关闭（window_event 未置位且超时）即停派
    新局，在途局自然收完；剩余 seed 下窗续跑（units.done 已落台账）。
    """

    def __init__(
        self,
        bun: str,
        rl_path: str | None,
        eval_log: Path,
        args,
        cfg: dict,
        batch: dict,
        unit: dict,
        unit_idx: int,
        unit_of: int,
        run_id: str,
        engine_epoch: str,
        policy: str = "nn",
        window_event: threading.Event | None = None,
        init_sha16: str = "",
        kind: str | None = None,
        include_scorable: bool = False,
    ) -> None:
        self.bun = bun
        self.rl_path = rl_path
        self.eval_log = eval_log
        self.args = args
        self.cfg = cfg
        self.batch = batch
        self.unit = unit
        self.unit_idx = unit_idx
        self.unit_of = unit_of
        self.run_id = run_id
        self.engine_epoch = engine_epoch
        self.policy = policy
        self.window_event = window_event
        self.init_sha16 = init_sha16
        # kind：None = 由 policy 推（见 KIND_FOR_POLICY）；显式给值只为一处例外——
        # 一次性评估入口要复用既有 agent 桶命名时。
        self.kind = kind or kind_for_policy(policy)
        # include_scorable：把 agent 报告的原始 `scorable`（scoreV7 的完整输入：
        # finalState + telemetry）原样落到逐局行。默认关 ⇒ A/B/C 层的行**逐字节不变**；
        # eval_m1_once 打开它，把 TS 侧的打分输入原样带回（不做字段级搬运 = 不会漂移）。
        self.include_scorable = include_scorable

    def run(self) -> dict:
        try:
            return self._run()
        except Exception as e:
            log(f"[batcheval] unit error (ignored): {type(e).__name__}: {str(e)[:200]}")
            return {"settled": 0, "total": 0, "dropped": 0}

    def _run(self) -> dict:
        args = self.args
        unit = self.unit
        policy_cfg = self.cfg.get("policy", {})
        status_timeout = float(policy_cfg.get("statusTimeoutSec", 3))
        task_timeout = float(policy_cfg.get("taskTimeoutSec", 900))
        fail_streak_max = int(policy_cfg.get("nodeFailStreak", 3))
        window = float(getattr(args, "eval_window_sec", 1500) or 1500)
        deadline = time.time() + window
        god = self.policy == "god"
        if god:
            # god 局无权语义，但 agent 的 /v1/task **一律**按 (kind, wver) 查缓存桶
            #（无 god 豁免；weightsOf 是全量 sha 精确查表，2026-09-19 核实）⇒
            # 必须 POST 占位 `{}` 并把它的 sha 当 wver 传。key16 仍是行身份（显示/去重）。
            weights_bytes = b"{}"
            wver = hashlib.sha256(weights_bytes).hexdigest()
            key16 = f"god-{self.engine_epoch[:12]}"
        else:
            if not self.rl_path:
                log("[batcheval] nn unit without weights — skipped")
                return {"settled": 0, "total": 0, "dropped": 0}
            wver = dist_common.weights_fingerprint(self.rl_path)
            key16 = wver[:16]
            with open(self.rl_path, "rb") as f:
                weights_bytes = f.read()
        iter_base = batch_iter_id(self.run_id, str(self.batch.get("batch_id")))
        iter_id = f"{iter_base}u{self.unit_idx}"
        kind = self.kind
        # unit 可**缺** lives/level：缺 = 不做覆盖（difficulty / 关卡默认说了算）。
        # ladder/corpora 的 unit 恒带值（旧行为逐字不变）；一次性评估工具跑内置关时
        # 没有课程覆盖语义，传 3 之类会把「难度默认」硬编码成常数（改难度即错）。
        unit_lives = None if unit.get("lives") is None else int(unit["lives"])
        unit_level = None if unit.get("level") is None else int(unit["level"])
        seeds = [int(s) for s in unit["seeds"]]
        pairs = [(int(unit["stageId"]), s) for s in seeds]
        total = len(pairs)
        done_before = self._done_keys(key16)
        todo = [p for p in pairs if p not in done_before]
        if not todo:
            log(f"[batcheval] {unit['rung']} u{self.unit_idx}: already settled — skip")
            return {"settled": total, "total": total, "dropped": 0}
        t_start = time.time()
        # R4-G1 心跳：单元开始（console 只读，显示当前批/单元/rung）。
        _heartbeat(
            window_open=(
                self.window_event.is_set() if self.window_event is not None else True
            ),
            batch_id=str(self.batch.get("batch_id")),
            unit_idx=self.unit_idx,
            unit_of=self.unit_of,
            rung=str(unit.get("rung", "")),
            remaining_units=max(0, self.unit_of - self.unit_idx),
            engine_epoch=self.engine_epoch,
        )

        snapshot_path: str | None = None
        local_slots = max(0, int(policy_cfg.get("evalLocalSlots", EVAL_LOCAL_SLOTS_DEFAULT)))
        if not god and self.rl_path and local_slots > 0:
            try:
                snap = self.eval_log.parent / f"_batcheval_frozen_{self.unit_idx}.json"
                shutil.copyfile(self.rl_path, str(snap))
                snapshot_path = str(snap)
            except OSError as e:
                log(f"[batcheval] WARN weights snapshot failed — local off: {e}")
        # god 局不读权重文件（export 侧 weightsText='{}'）：无 rl_path 时占位串即可
        # （从不读取）；nn 局必须读派发时刻冻结快照。
        local_weights = snapshot_path or ((self.rl_path or "no-weights-god") if god else None)

        local_bun = bun_version(self.bun)
        # 节点门指纹（2026-09-17 统一）：与 rollout 门同源 = SSOT 清单的 codeHash。
        code_hash_local = dist_common.compute_code_hash()
        alive = []
        for n in self.cfg.get("nodes", []):
            if not n.get("enabled", True):
                continue
            nid = str(n.get("id") or n.get("url") or "?")
            ping = dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
            if ping is None:
                continue
            if not ping.get("evalSupport"):
                log(f"[batcheval] node {nid}: lacks evalSupport — skipped")
                continue
            if not ping.get("stageJsonSupport"):
                log(f"[batcheval] node {nid}: lacks stageJsonSupport — skipped")
                continue
            if mm(str(ping.get("bunVersion", "?"))) != mm(local_bun):
                log(f"[batcheval] node {nid}: bun version mismatch — skipped")
                continue
            why = dist_common.check_code_hash(ping, code_hash_local)
            if why:
                # B/C 严格：拒派不静默降级（P2 DoD；A 层门在 eval_dispatch，同一判据）。
                log(f"[batcheval] node {nid}: {why} — refused")
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
        if not alive and local_weights is None:
            log("[batcheval] no eval-capable node and no local — unit deferred")
            return {"settled": 0, "total": total, "dropped": total}

        nodes_ok = []
        # god 也 POST：占位 `{}` 的 sha 就是它的 wver（见上）——不 POST 则 /v1/task
        # 必然 409（此为 C 层 god 批此前「无节点可用」表象的真因）。
        assert weights_bytes is not None
        nodes_ok = dist_common.post_weights_parallel(
            alive,
            iter_id,
            wver,
            weights_bytes,
            timeout=min(300.0, max(60.0, task_timeout)),
            kind=kind,
            log=log,
        )
        if not nodes_ok and local_weights is None:
            log("[batcheval] all weight POSTs failed — unit deferred")
            return {"settled": 0, "total": total, "dropped": total}
        if not nodes_ok and (local_weights is None or local_slots <= 0):
            log("[batcheval] god unit: no node and no local slot — deferred")
            return {"settled": 0, "total": total, "dropped": total}

        log(
            f"[batcheval] {unit['rung']} u{self.unit_idx}/{self.unit_of} "
            f"policy={self.policy}: dispatch {len(todo)} games "
            f"->{[(n['id'], n['c']) for n in nodes_ok]}"
            + (f" [local ×{local_slots}]" if local_weights else "")
        )

        pending: deque[tuple[int, int]] = deque(todo)
        lock = threading.Lock()
        seen: set[tuple[int, int]] = set()
        attempts: dict[tuple[int, int], int] = {}
        streaks = {nd["id"]: 0 for nd in nodes_ok}
        settled = [0]
        node_games: dict[str, int] = {}
        jsonl_lock = threading.Lock()

        def window_open() -> bool:
            # 关窗即停派新局（§6.5 / 用户 2026-09-11：rollout 抢占让出集群）。
            # window_event 置位 = evalboard 可派；清位 = yield。None 退化为 deadline。
            if self.window_event is None:
                return time.time() < deadline
            return self.window_event.is_set()

        def record(manifest: dict, nd_id: str, task: tuple[int, int]) -> None:
            win = 1 if manifest.get("win") else 0
            cleared = 1 if manifest.get("cleared") else 0
            # x5⑧③：掉落三列与 A-eval record() 同源（eval_loot_fields）。
            loot = eval_loot_fields(manifest)
            # Phase 0 逐敌种画像七列与 A-eval 同源（eval_census_fields）。
            census = eval_census_fields(manifest)
            row = {
                "event": "eval",
                "iter": self.batch.get("iter", 0),
                "wver": key16,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": task[0],
                "seed": task[1],
                "node": nd_id,
                "outcome": manifest.get("outcome"),
                "win": win,
                "cleared": cleared,
                "ticks": manifest.get("ticks"),
                "score": manifest.get("score"),
                "kills": manifest.get("kills"),
                "enemyHits": manifest.get("enemyHits"),
                "hitRate": manifest.get("hitRate"),
                "powerUpsCollected": manifest.get("powerUpsCollected"),
                "powerUpsSpawned": loot["powerUpsSpawned"],
                "starsCollected": loot["starsCollected"],
                "playerDamageTaken": manifest.get("playerDamageTaken"),
                "playerHits": manifest.get("playerHits"),
                "policy": manifest.get("policy", self.policy),
                "enemyTotal": manifest.get("enemyTotal"),
                "playerDeaths": manifest.get("playerDeaths"),
                "playerShots": manifest.get("playerShots"),
                "playerLevel": manifest.get("playerLevel"),
                "cellsVisited": manifest.get("cellsVisited"),
                "firstKillTick": manifest.get("firstKillTick"),
                "stuckTicks": manifest.get("stuckTicks"),
                "puSpawnBomb": manifest.get("puSpawnBomb"),
                "puSpawnTank": manifest.get("puSpawnTank"),
                "puSpawnFreeze": manifest.get("puSpawnFreeze"),
                "puSpawnShield": manifest.get("puSpawnShield"),
                "puSpawnStar": manifest.get("puSpawnStar"),
                "puGotBomb": manifest.get("puGotBomb"),
                "puGotTank": manifest.get("puGotTank"),
                "puGotFreeze": manifest.get("puGotFreeze"),
                "puGotShield": manifest.get("puGotShield"),
                "puGotOther": loot["puGotOther"],
                "elapsedSec": manifest.get("elapsedSec"),
                "hitsByKind": census["hitsByKind"],
                "killsByKind": census["killsByKind"],
                "exposureByKind": census["exposureByKind"],
                "firstHitKind": census["firstHitKind"],
                "firstKillKind": census["firstKillKind"],
                "killOrder": census["killOrder"],
                "killerKinds": census["killerKinds"],
                # B 层归属（ingest → EvalStore 直读）
                "batch_id": self.batch.get("batch_id"),
                "batch_unit": {"idx": self.unit_idx, "of": self.unit_of},
                "rung": unit["rung"],
                "ckpt_sha16": key16,
                "init_sha16": self.init_sha16,
                "source": "B" if self.policy == "nn" else "C",
            }
            # scoreV7 的原始输入（finalState + telemetry）原样带上——只有显式打开的
            # 调用方（eval_m1_once）会看到这一列；A/B/C 层的行不留任何新键。
            if self.include_scorable:
                row["scorable"] = manifest.get("scorable")
            with jsonl_lock:
                with open(self.eval_log, "a", encoding="utf-8") as jf:
                    jf.write(json.dumps(row) + "\n")
                _record_agent_meta(
                    self.eval_log.parent / "dist-agent-meta.jsonl",
                    {
                        "node": nd_id,
                        "mode": "eval",
                        "it": self.batch.get("iter", 0),
                        "stage": task[0],
                        "seed": task[1],
                        "ok": True,
                        "win": win,
                        "elapsedSec": manifest.get("elapsedSec"),
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            with lock:
                settled[0] += 1
                node_games[nd_id] = node_games.get(nd_id, 0) + 1

        def fetch_manifest(nd: dict, task: tuple[int, int]) -> dict:
            if nd["id"] == "local":
                assert local_weights is not None
                m = run_local_eval_game(
                    self.bun,
                    local_weights,
                    task[0],
                    task[1],
                    self.eval_log.parent
                    / "local-batcheval"
                    / f"u{self.unit_idx}_s{task[0]}_seed{task[1]}",
                    max_ticks=int(unit["maxTicks"]),
                    difficulty=str(unit["difficulty"]),
                    timeout_sec=task_timeout,
                    # 全量 wver：agent 按完整 sha 存桶；且本局 manifest.wver 就是它
                    #（validate 用同一个值对账）。key16 只作行字段。
                    wver=wver,
                    stage_json=str(unit["stageJson"]),
                    lives_override=unit_lives,
                    player_level=unit_level,
                    policy=self.policy,
                )
            else:
                m, _files = dist_common.fetch_task(
                    nd["url"],
                    nd["key"],
                    iter_id=iter_id,
                    # 全量 wver（agent 桶按完整 sha 存；key16 会 409 —— 2026-09-19 实测）。
                    wver=wver,
                    stage=task[0],
                    seed=task[1],
                    max_ticks=int(unit["maxTicks"]),
                    difficulty=str(unit["difficulty"]),
                    timeout=task_timeout,
                    mode="eval",
                    kind=kind,
                    stage_json=str(unit["stageJson"]),
                    lives_override=unit_lives,
                    player_level=unit_level,
                    policy=self.policy,
                )
            why = dist_common.validate_eval_result(m, wver)
            if why:
                raise dist_common.DistError(0, why)
            return m

        def worker(nd: dict) -> None:
            while time.time() < deadline:
                task = None
                with lock:
                    if streaks.get(nd["id"], 0) >= fail_streak_max:
                        return
                    # 关窗（rollout 抢占）本地与远端一并停派；在途局自然收完。
                    if pending and window_open():
                        task = pending.popleft()
                        attempts[task] = attempts.get(task, 0) + 1
                if task is None:
                    if not pending:
                        return
                    if not window_open():
                        # yield：退出 worker，剩余 seed 留给下一次 idle 窗口续跑。
                        return
                    time.sleep(min(5.0, max(0.1, deadline - time.time())))
                    continue
                attempt = attempts[task]
                try:
                    manifest = fetch_manifest(nd, task)
                    record(manifest, nd["id"], task)
                    ok = True
                    err = ""
                except Exception as e:
                    ok = False
                    err = str(e)[:200]
                with lock:
                    if ok:
                        seen.add(task)
                        streaks[nd["id"]] = 0
                    else:
                        streaks[nd["id"]] = streaks.get(nd["id"], 0) + 1
                        if attempt < EVAL_TASK_ATTEMPTS and task not in seen:
                            pending.append(task)
                            log(f"[batcheval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")

        threads = []
        for nd in nodes_ok:
            for _ in range(nd["c"]):
                threads.append(threading.Thread(target=worker, args=(nd,), daemon=True))
        if local_weights is not None and local_slots > 0:
            for _ in range(local_slots):
                threads.append(
                    threading.Thread(target=worker, args=({"id": "local"},), daemon=True)
                )
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join(timeout=max(30.0, window + task_timeout))
        # dropped = todo 中未结算（断点续跑由 units.done + eval_done_keys 双保险）
        dropped = len(todo) - len(seen)
        log(
            f"[batcheval] {unit['rung']} u{self.unit_idx} DONE settled={len(seen)}/{len(todo)} "
            f"dropped={dropped} sec={round(time.time() - t_start, 1)}"
        )
        try:
            if dropped == 0:
                mark_unit_done(
                    data_root(), str(self.batch.get("batch_id")), self.unit_idx, dict(node_games)
                )
            else:
                # 部分完成（yield/超时）：不标 unit done，批回 pending 供 idle 续跑；
                # 已结算 seed 由 _done_keys 跳过，不重复计。
                log(
                    f"[batcheval] {unit['rung']} u{self.unit_idx}: partial "
                    f"({dropped} left) — reopen batch for resume"
                )
                _reopen_for_resume(data_root(), str(self.batch.get("batch_id")))
        except Exception as e:
            log(f"[batcheval] WARN mark_unit_done failed: {e}")
        # R4-G1 心跳：单元结束（清 rung；window_open 留给 loop_core 的开关窗写点）。
        _heartbeat(
            batch_id=str(self.batch.get("batch_id")), rung=None, remaining_units=0
        )
        return {"settled": len(seen), "total": len(todo), "dropped": dropped}

    def _done_keys(self, key16: str) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        try:
            if self.eval_log.exists():
                for line in self.eval_log.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(r, dict) and r.get("event") == "eval" and r.get("wver") == key16:
                        try:
                            out.add((int(r["stage"]), int(r["seed"])))
                        except (KeyError, TypeError, ValueError):
                            continue
        except OSError:
            pass
        return out


def dispatch_batch_bg(
    bun: str,
    rl_path: str | None,
    eval_log: Path,
    args,
    cfg: dict,
    batch: dict,
    unit: dict,
    unit_idx: int,
    unit_of: int,
    run_id: str,
    engine_epoch: str,
    policy: str = "nn",
    window_event: threading.Event | None = None,
    init_sha16: str = "",
) -> threading.Thread:
    """后台起一个单元（调用方 join，语义同 dispatch_eval_bg）。"""
    runner = BatchEvalRunner(
        bun,
        rl_path,
        eval_log,
        args,
        cfg,
        batch,
        unit,
        unit_idx,
        unit_of,
        run_id,
        engine_epoch,
        policy,
        window_event,
        init_sha16,
    )
    t_ = threading.Thread(target=runner.run, daemon=True, name=f"batcheval-u{unit_idx}")
    t_.start()
    return t_


def maybe_dispatch_batch(
    bun: str,
    rl_path: str | None,
    traj_dir: Path,
    args,
    cfg: dict,
    run_id: str,
    it: int,
    window_event: threading.Event | None = None,
) -> threading.Thread | None:
    """B 层轮次分配钩子（§6.4）：调用方保证 `not eval_on_round`（A/B 确定性分配，
    无饥饿）。认领最早 pending 批，跑其中第一个未完成单元；无批/无单元 → None。

    per-tick + policy nn：rl_path 必需（学生权重）；policy god：权重无关，
    任何模式可跑。intent/goal + policy nn：B 层 nn 单元语义不适用（意图权重另
    桶），拒绝并记日志（未来扩展点，不静默跑错）。
    """
    root = data_root()
    batch = claim_pending(root)
    if batch is None:
        return None
    # 判决批（P2）：语料来自注册表，unit 自带 ckpt ⇒ 不需要 rl_path，也不吃 mode
    # 分歧（显式判决与训练模式无关：这一局的权重就是 unit 的 ckpt）。
    verdict = str(batch.get("kind") or "ladder") == "verdict"
    policy = str(batch.get("policy", "nn"))
    if not verdict and policy == "nn" and getattr(args, "mode", "per-tick") != "per-tick":
        log(
            f"[batcheval] batch {batch.get('batch_id')}: nn unit 不适用于 mode={args.mode} — 退回队列"
        )
        _requeue(root, batch)
        return None
    try:
        units = units_for_batch(batch)
    except Exception as e:
        log(f"[batcheval] plan 失败 ({e}) — 退回队列")
        _requeue(root, batch)
        return None
    units, nxt, unit = select_next_unit(
        units, set((batch.get("units") or {}).get("done", [])), batch.get("only_rungs")
    )
    if nxt is None or unit is None:
        return None
    # 判决批的权重在 unit 上（多 ckpt 同批）；ladder 批回落批次级 rl_path。
    unit_weights = str(unit.get("ckpt") or "") or rl_path
    if policy == "nn" and not unit_weights:
        log("[batcheval] nn unit without weights — 退回队列")
        _requeue(root, batch)
        return None
    epoch = dist_common.compute_engine_epoch()
    eval_log = traj_dir.parent / "eval_log.jsonl"
    batch.setdefault("units", {})["of"] = len(units)
    _persist_of(root, str(batch.get("batch_id")), len(units))
    return dispatch_batch_bg(
        bun,
        unit_weights,
        eval_log,
        args,
        cfg,
        batch,
        unit,
        nxt,
        len(units),
        run_id,
        epoch,
        policy,
        window_event,
        str(batch.get("init_sha16", "")),
    )


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


@_claim_locked
def _persist_of(root: Path, batch_id: str, of: int) -> None:
    """plan 展开后 units.of 回写台账（回归位使 of 2→3；defer 时也不丢）。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch_id:
            b.setdefault("units", {})["of"] = of
            break
    write_batches(root, batches)


@_claim_locked
def _requeue(root: Path, batch: dict) -> None:
    """认领后发现跑不了 → 状态改回 pending（§3.7 台账是队列，状态流转合法）。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch.get("batch_id"):
            if b.get("status") != "aborted":
                b["status"] = "pending"
                write_batches(root, batches)
            break


@_claim_locked
def _reopen_for_resume(root: Path, batch_id: str) -> None:
    """部分完成（yield/超时）→ running 改回 pending，units.done 保留供续跑。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch_id and b.get("status") == "running":
            b["status"] = "pending"
            break
    write_batches(root, batches)
