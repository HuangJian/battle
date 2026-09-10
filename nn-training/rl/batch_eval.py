"""batch_eval — B 层批次执行（plan/rl-eval-system.md §10.3 P2）。

BatchEvalRunner：结构参考 EvalDispatcher，复用 `fetch_task(mode='eval')` /
`run_local_eval_game`；语料由 `ladder.json` + `batches.jsonl` 驱动
（不要复用 dispatch_eval_bg：EvalDispatcher 无外部语料入口，§10.3）。

关键契约：
  - 节点门（§6.6）：enabled ∧ ping ∧ evalSupport ∧ stageJsonSupport ∧
    bunVersion 一致 ∧ engine_epoch 一致——**严格拒派，不静默降级**（P2 DoD）。
  - iterId 命名空间 `{runId}.b{batchShort}u{unit}`（runner.ts）：agent taskKey
    无 policy 分量，命名空间隔离是 god/nn 不串键的唯一保证。
  - 窗口（§6.5）：只在窗口开时派新局；在途局自然跑完（taskTimeoutSec 封顶）；
    剩余局按 batch_unit 下窗续跑（units.done 进 batches.jsonl 台账）。
  - 确定性契约（§3.4/P2 DoD）：同 ckpt + 同 seed 集 + 同 stage ⇒ gameplay
    字段逐字节一致（node/elapsedSec/phase/ts 不要求）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import dist_common
from rl.eval_local import (
    EVAL_LOCAL_SLOTS_DEFAULT,
    EVAL_TASK_ATTEMPTS,
    run_local_eval_game,
)
from rl.log import log
from rl.queue import _record_agent_meta, bun_version, mm

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "tools" / "training" / "data" / "evalboard"
LADDER_JSON = REPO_ROOT / "tools" / "training" / "evalboard" / "ladder.json"

REGRESSION_EVERY = 3  # 与 runner.ts 同值（双侧镜像，改一侧必须同步另一侧）
BATCH_STAGE_BASE = 2000
EVAL_SEED0 = 860001
SEGMENT_LEN = 100


def data_root() -> Path:
    """EvalStore 数据根（EVALBOARD_DATA 覆盖；默认 tools/training/data/evalboard）。"""
    return Path(os.environ.get("EVALBOARD_DATA", str(DEFAULT_DATA_ROOT)))


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


_KIND_CHAR = {"basic": "a", "fast": "b", "power": "c", "armor": "d", "player": "a"}


def _forces_of(stage: dict) -> str:
    return "".join(_KIND_CHAR.get(k, "a") for k in stage.get("enemies", []))


def batch_iter_id(run_id: str, batch_id: str) -> str:
    """B/C 批 iterId 基（+ f"u{unit}"；与 runner.ts batchIterId 同式）。"""
    short = hashlib.sha256(batch_id.encode()).hexdigest()[:8]
    return f"{run_id}.b{short}"


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


def claim_pending(root: Path) -> dict | None:
    """取最早 pending 批并标 running（console POST 只 append pending，§6.7）。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("status") == "pending":
            b["status"] = "running"
            write_batches(root, batches)
            return b
    return None


def mark_unit_done(root: Path, batch_id: str, unit_idx: int, node_dist: dict) -> None:
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch_id:
            units = b.setdefault("units", {"of": 0, "done": []})
            if unit_idx not in units.get("done", []):
                units["done"].append(unit_idx)
            b["node_dist"] = node_dist
            if len(units.get("done", [])) >= units.get("of", 0):
                b["status"] = "done"
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
            key16 = f"god-{self.engine_epoch[:12]}"
            weights_bytes = None
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
        seeds = [int(s) for s in unit["seeds"]]
        pairs = [(int(unit["stageId"]), s) for s in seeds]
        total = len(pairs)
        done_before = self._done_keys(key16)
        todo = [p for p in pairs if p not in done_before]
        if not todo:
            log(f"[batcheval] {unit['rung']} u{self.unit_idx}: already settled — skip")
            return {"settled": total, "total": total, "dropped": 0}
        t_start = time.time()

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
            why = dist_common.check_engine_epoch(ping, self.engine_epoch)
            if why:
                # B/C 严格：拒派不静默降级（P2 DoD；A 层过渡逻辑在 eval_dispatch）。
                log(f"[batcheval] node {nid}: {why} — refused")
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
        if not alive and local_weights is None:
            log("[batcheval] no eval-capable node and no local — unit deferred")
            return {"settled": 0, "total": total, "dropped": total}

        nodes_ok = []
        if not god:
            assert weights_bytes is not None
            for nd in alive:
                try:
                    dist_common.post_weights(
                        nd["url"],
                        nd["key"],
                        iter_id,
                        wver,
                        weights_bytes,
                        timeout=min(300.0, max(60.0, task_timeout)),
                    )
                    nodes_ok.append(nd)
                except dist_common.DistError as e:
                    log(f"[batcheval] weights POST to {nd['id']} failed ({e}) — excluded")
            if not nodes_ok and local_weights is None:
                log("[batcheval] all weight POSTs failed — unit deferred")
                return {"settled": 0, "total": total, "dropped": total}
        else:
            nodes_ok = alive
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
            # 关窗即停派新局（§6.5）；循环本就以 deadline 为界，在途局自然收完。
            if self.window_event is None:
                return time.time() < deadline
            return self.window_event.is_set()

        def record(manifest: dict, nd_id: str, task: tuple[int, int]) -> None:
            win = 1 if manifest.get("win") else 0
            cleared = 1 if manifest.get("cleared") else 0
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
                "elapsedSec": manifest.get("elapsedSec"),
                # B 层归属（ingest → EvalStore 直读）
                "batch_id": self.batch.get("batch_id"),
                "batch_unit": {"idx": self.unit_idx, "of": self.unit_of},
                "rung": unit["rung"],
                "ckpt_sha16": key16,
                "init_sha16": self.init_sha16,
                "source": "B" if self.policy == "nn" else "C",
            }
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
                    wver=key16,
                    stage_json=str(unit["stageJson"]),
                    lives_override=int(unit["lives"]),
                    player_level=int(unit["level"]),
                    policy=self.policy,
                )
            else:
                m, _files = dist_common.fetch_task(
                    nd["url"],
                    nd["key"],
                    iter_id=iter_id,
                    wver=key16,
                    stage=task[0],
                    seed=task[1],
                    max_ticks=int(unit["maxTicks"]),
                    difficulty=str(unit["difficulty"]),
                    timeout=task_timeout,
                    mode="eval",
                    stage_json=str(unit["stageJson"]),
                    lives_override=int(unit["lives"]),
                    player_level=int(unit["level"]),
                    policy=self.policy,
                )
            why = dist_common.validate_eval_result(m, key16)
            if why:
                raise dist_common.DistError(0, why)
            return m

        def worker(nd: dict) -> None:
            is_local = nd["id"] == "local"
            while time.time() < deadline:
                task = None
                with lock:
                    if streaks.get(nd["id"], 0) >= fail_streak_max:
                        return
                    if pending:
                        # B 层本地/远端同快照，无预留；窗口关闭即停派新局（§6.5）。
                        if not is_local and not window_open():
                            pass
                        else:
                            task = pending.popleft()
                            attempts[task] = attempts.get(task, 0) + 1
                if task is None:
                    if not pending:
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
            mark_unit_done(
                data_root(), str(self.batch.get("batch_id")), self.unit_idx, dict(node_games)
            )
        except Exception as e:
            log(f"[batcheval] WARN mark_unit_done failed: {e}")
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
    policy = str(batch.get("policy", "nn"))
    if policy == "nn" and getattr(args, "mode", "per-tick") != "per-tick":
        log(
            f"[batcheval] batch {batch.get('batch_id')}: nn unit 不适用于 mode={args.mode} — 退回队列"
        )
        _requeue(root, batch)
        return None
    try:
        ladder = load_ladder()
    except Exception as e:
        log(f"[batcheval] ladder.json 不可用 ({e}) — 退回队列")
        _requeue(root, batch)
        return None
    try:
        units = plan_units(ladder, int(batch.get("ladder_pos", 0)), int(batch.get("k_seq", 0)))
    except Exception as e:
        log(f"[batcheval] plan 失败 ({e}) — 退回队列")
        _requeue(root, batch)
        return None
    units, nxt, unit = select_next_unit(
        units, set((batch.get("units") or {}).get("done", [])), batch.get("only_rungs")
    )
    if nxt is None or unit is None:
        return None
    if policy == "nn" and not rl_path:
        log("[batcheval] nn unit without weights — 退回队列")
        _requeue(root, batch)
        return None
    epoch = dist_common.compute_engine_epoch()
    eval_log = traj_dir.parent / "eval_log.jsonl"
    batch.setdefault("units", {})["of"] = len(units)
    _persist_of(root, str(batch.get("batch_id")), len(units))
    return dispatch_batch_bg(
        bun,
        rl_path,
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


def _persist_of(root: Path, batch_id: str, of: int) -> None:
    """plan 展开后 units.of 回写台账（回归位使 of 2→3；defer 时也不丢）。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch_id:
            b.setdefault("units", {})["of"] = of
            break
    write_batches(root, batches)


def _requeue(root: Path, batch: dict) -> None:
    """认领后发现跑不了 → 状态改回 pending（§3.7 台账是队列，状态流转合法）。"""
    batches = read_batches(root)
    for b in batches:
        if b.get("batch_id") == batch.get("batch_id"):
            b["status"] = "pending"
            break
    write_batches(root, batches)
