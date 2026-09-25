"""batch_store — EvalBoard 批台账的**唯一所有者**（plan/nn-training-refactor.md §5.5.2）。

契约一句话：`<EVALBOARD_DATA>/batches.jsonl` 与两个请求文件**只有一个所有者**；
`status` / `units` / `node_dist` 的每一次变更都发生在一个**具名转移**里，且
**一次转移 = 一次事务 = 一次落盘**。

**为什么要它**（2026-09-25 实测，不是审美）：拆 `rl/batch_eval.py`（1785 行）时发现
巨团「按链切不动」—— 台账有**八个**独立 read-modify-write 点，每个自己决定「改哪些字段 /
何时落盘 / 从什么状态到什么状态」，任何一条调用链都要横穿它们。真因不是调用图，而是
**台账没有所有者**。本模块把所有权收拢：写面只剩八个具名转移，读面只剩三个只读方法，
落盘策略统一成「改了才落盘 + 原子发布」（六种旧策略里有两种在什么都没变时也整文件重写）。

**两条刻意保留的特例语义**（集中化时最容易被「统一」掉 ⇒ 守卫正面钉在
`tests/test_batch_store_txn.py`，不许只靠「搬得对」）：
  * `units.of == 0`（未定型）时 `mark_unit_done` **不判 done**（否则判决批会在
    `plan_verdict_units` 回填 `of` 之前就被标成 done）；
  * `aborted` 批的在途 unit 只回填 `node_dist`、**不复活**，`requeue` **不改** `aborted`。

**锁**：跨进程仍复用 `train.loop_util` 的 `claim.lock`（**拒绝第三套锁实现**，plan P4-W4），
进程内用模块级 `RLock` 串行化；同一线程同一 root **可重入** ⇒ `claim()` 里直接嵌套
`consume_requests()`，不必再有 thread-local 缝。拿不到锁 ⇒ 本轮**跳过**（下一 idle 窗重试），
绝不无锁写。

**不缓存**：本对象只持有 root，**不缓存台账** —— 每次转移都重新读盘。否则另一个 trainer
进程刚写的批会被自己的内存副本吃掉（多课程并行 = 多进程共享同一 store）。

**字节契约**（python ↔ TS 双侧，不许动；`dashboard/data/evalboard/README.md` 是这份契约）：
`batches.jsonl` runner 单写 · `requests.jsonl` console 单写 append-only ·
`requests.done.jsonl` runner 单写 append-only · `utc_now_iso` 的 **UTC + 毫秒 + Z** 格式
（console 侧 `enqueueCovered` 用**字符串比较**判「批是否已物化」）· `batch_id` 生成式 ·
`units.of == 0 == 未定型`。

**本模块的来历**：`rl/batch_eval.py` 拆分第二步 B2（§5.5.4）。B1 已把纯规划/判据搬到
`rl/batch_plan.py`；B2 收走台账（本模块）；B3 把执行器搬到 `rl/batch_runner.py`。
`rl/batch_eval.py` 逐个再导出本模块的公开名（`X as X`）⇒ 既有调用点与测试一行不改。
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from rl.batch_plan import REPO_ROOT
from rl.log import log
from train.loop_util import acquire_lock, cleanup_lock

#: EvalStore 数据根（`EVALBOARD_DATA` 覆盖）。`data_root()` 同口径。
DEFAULT_DATA_ROOT = REPO_ROOT / "dashboard" / "data" / "evalboard"

BATCHES_FILE = "batches.jsonl"
#: 原子发布的临时名。**固定**（不随机）：上一次崩溃残留的 `.tmp` 会被本次直接覆盖
#: （不累积）⇒ 不需要 `finally` 清理，也不需要改 `.gitignore`（`dashboard/data/evalboard/*`
#: 整目录已忽略；且 `store.ts` 的 `.jsonl` 后缀过滤不会把它当行文件）。
BATCHES_TMP_FILE = "batches.jsonl.tmp"

REQUESTS_FILE = "requests.jsonl"
REQUESTS_DONE_FILE = "requests.done.jsonl"

_CLAIM_LOCK_NAME = "claim.lock"
_CLAIM_WAIT_SEC = 2.0
#: 进程内串行化（跨进程靠 `claim.lock` 文件锁）。模块级 ⇒ 同 root 的多个 store 实例
#: （生产里每处都现建现用）共享同一把 RLock，与拆分前的语义逐字一致。
_claim_local = threading.RLock()
#: 「本线程已经持有这个 root 的文件锁」标记 —— 可重入的全部机制（见 `_tx`）。
_claim_held = threading.local()


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


# ─────────────────────────────── 纯文件读面（无锁、无副作用）───────────────────────────────


def read_batches(root: Path) -> list[dict]:
    """读台账（坏行静默跳过 —— 与 console 侧 `loadBatches` 同口径）。"""
    p = root / BATCHES_FILE
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


def _req_key(r: dict) -> str:
    """请求去重键：优先 `req_id`，缺失时按内容规范化（console 侧一定会给 req_id）。"""
    rid = r.get("req_id")
    if isinstance(rid, str) and rid:
        return rid
    try:
        return json.dumps(r, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        return repr(sorted(str(k) for k in r))


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


def _int_of(v: object) -> int:
    """`iter` 一类的容错整数（console 送来的可能是 null / 字符串 / 缺键）⇒ 坏值一律 0。"""
    if not isinstance(v, (int, float, str)):
        return 0
    try:
        return int(v)
    except ValueError:
        return 0


class BatchStore:
    """EvalBoard 批台账的唯一读写口（含跨进程锁与原子落盘）。

    只持有 `root`，**不持有台账副本**（见模块 docstring「不缓存」）。
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else data_root()

    # ───────────────────────────── 事务与锁 ─────────────────────────────

    @contextmanager
    def _tx(self, op: str) -> Iterator[bool]:
        """一次事务的锁域：yield True = 已持锁；False = 2s 内未取得（调用方本轮跳过）。

        跨进程 `claim.lock` + 进程内 `RLock`；**同线程同 root 可重入**（`_claim_held`）——
        `claim()` 里嵌套 `consume_requests()` 走的正是这条，无需再引入 thread-local 缝。
        """
        with _claim_local:
            key = str(self.root)
            if getattr(_claim_held, "key", "") == key:
                yield True  # 本线程已持锁（嵌套转移：claim → consume_requests → enqueue）
                return
            self.root.mkdir(parents=True, exist_ok=True)
            lock_path = str(self.root / _CLAIM_LOCK_NAME)
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
                if not ok:
                    log(f"[batcheval] claim.lock 忙——跳过本轮 {op}（下一 idle 窗重试）")
                yield ok
            finally:
                _claim_held.key = ""
                if ok:
                    cleanup_lock(lock_path)

    # ───────────────────────────── 读面（无锁、无副作用）─────────────────────────────

    def all(self) -> list[dict]:
        """台账全量快照（新解析，改它不影响盘上）。"""
        return read_batches(self.root)

    def get(self, batch_id: str) -> dict | None:
        for b in self.all():
            if b.get("batch_id") == batch_id:
                return b
        return None

    def done_units(self, batch_id: str) -> set[int]:
        """已结算 unit 下标集（缺失/坏形态 ⇒ 空集）。"""
        b = self.get(batch_id) or {}
        done = (b.get("units") or {}).get("done") or []
        return {int(i) for i in done}

    # ───────────────────────────── 落盘（唯一写点）─────────────────────────────

    def _publish(self, batches: list[dict]) -> None:
        """原子发布台账（**全模块唯一写点**；只被具名转移与下面的 `write_batches` 缝调用）。

        ★ 为什么不是 `Path.write_text`（2026-09-25 修，复现用例
        `tests/test_batch_eval.py::test_batch_ledger_publish_is_atomic`）：它先 `open('w')`
        **原地截断** live 文件再写字节 ⇒「截断」到「写完」之间存在一个**读者可见**的窗口。
        本文件的读者里有一个**无锁的跳语言读者** —— console/TS 的
        `batches.ts::loadBatches`（「runner 单写；console 只读」，坏行**静默跳过**），而它的
        `enqueueBatch` 去重（「同 course+rung+ckpt 的 pending 批已存在则返回它」）**依赖读全**
        ⇒ 落在窗口里就会**重复入队**。探针实测（写者连续重写 60 轮、读者逐字节照抄
        `loadBatches` 读法；终次 log = `nn-training/tmp/probe-batch-store-final.log`）：
        12 批 / 2.7 KB 短读 126/622 = 20.3%，2000 批 / 444 KB 短读 144/376 = 38.3% + 12 坏行。

        `os.replace` 是**同一文件系统内的原子替换**（POSIX `rename(2)` / Win32
        `MoveFileEx(REPLACE_EXISTING)`）⇒ 读者只会看到完整的旧快照或完整的新快照。

        ★ 同族未修（另开一刀）：`dashboard/src/evalboard/batches.ts::rewriteBatches`
        （TS 侧 `claimPending` / `updateBatch`）用同样的截断式 `writeFileSync`。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / BATCHES_TMP_FILE
        tmp.write_text("".join(json.dumps(b) + "\n" for b in batches), encoding="utf-8")
        os.replace(tmp, self.root / BATCHES_FILE)

    # ───────────────────────────── 写面：八个具名转移 ─────────────────────────────
    # 每条 = 一次事务（读 → 改 → 原子写）；`dirty` 才落盘（内容等价，少整文件重写）。
    # `status` 的赋值点闭集 = 下面这五个带状态转移的方法（守卫见
    # `tests/test_batch_store_txn.py::test_status_assignments_live_only_in_the_named_transitions`）。

    def enqueue(
        self,
        *,
        course: str,
        rung_from: str,
        ckpt: str,
        req_ts: str = "",
        **spec: object,
    ) -> dict | None:
        """建 pending 批（幂等：同 key pending 已存在，或已有 `created_ts >= req_ts` 的批 ⇒ None）。

        `spec` 里只有白名单键会被带上（`ladder_pos` / `k_seq` / `init_sha16` /
        `only_rungs`）—— console 将来多塞字段不会污染台账。
        """
        with self._tx("enqueue") as ok:
            if not ok:
                return None
            batches = self.all()
            if any(
                b.get("status") == "pending" and _same_enq_key(b, course, rung_from, ckpt)
                for b in batches
            ):
                return None
            if req_ts and any(
                _same_enq_key(b, course, rung_from, ckpt)
                and str(b.get("created_ts", "")) >= req_ts
                for b in batches
            ):
                return None
            trig = str(spec.get("trigger", "standalone"))
            if trig not in ("main", "standalone", "auto-ladder"):
                trig = "standalone"
            pol = str(spec.get("policy", "nn"))
            if pol not in ("nn", "god"):
                pol = "nn"
            it = _int_of(spec.get("iter", 0))
            batch: dict = {
                "batch_id": _new_batch_id(),
                "course": course,
                "rung_from": rung_from,
                "ckpt": ckpt,
                "requester": str(spec.get("requester", "web")),
                "created_ts": utc_now_iso(),
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
                if spec.get(opt) is not None:
                    batch[opt] = spec[opt]
            batches.append(batch)
            self._publish(batches)
            return batch

    def enqueue_verdict(
        self,
        *,
        corpus: str,
        ckpts: list,
        req_ts: str = "",
        **spec: object,
    ) -> dict | None:
        """建 pending 判决批（P2）：语料 id + `ckpts[]`（多权重同批同种子配对）。

        去重键 = 语料 id + ckpt 标签序列（顺序敏感）；`units.of` 由
        `plan_verdict_units` 展平后经 `set_units_of` 回填（建批时恒 0 = 未定型）。
        """
        with self._tx("enqueue_verdict") as ok:
            if not ok:
                return None
            batches = self.all()
            vkey = _verdict_key_of(corpus, ckpts)
            if any(
                b.get("status") == "pending" and _verdict_key(b) == vkey for b in batches
            ):
                return None
            if req_ts and any(
                _verdict_key(b) == vkey and str(b.get("created_ts", "")) >= req_ts
                for b in batches
            ):
                return None
            pol = str(spec.get("policy", "nn"))
            if pol not in ("nn", "god"):
                pol = "nn"
            it = _int_of(spec.get("iter", 0))
            vbatch: dict = {
                "batch_id": _new_batch_id(),
                "kind": "verdict",
                "corpus": corpus,
                "ckpts": ckpts,
                "requester": str(spec.get("requester", "web")),
                "created_ts": utc_now_iso(),
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
                if spec.get(opt) is not None:
                    vbatch[opt] = spec[opt]
            batches.append(vbatch)
            self._publish(batches)
            return vbatch

    def abort(self, batch_id: str) -> bool:
        """pending / running → aborted（命中即改；done/aborted 不动）。返回是否改了。

        在途 unit 跑完即停（见 `mark_unit_done` 的 aborted 分支）。
        """
        with self._tx("abort") as ok:
            if not ok:
                return False
            batches = self.all()
            for b in batches:
                if b.get("batch_id") == batch_id and b.get("status") in ("pending", "running"):
                    b["status"] = "aborted"
                    self._publish(batches)
                    return True
            return False

    def claim(self, *, consume: bool = True) -> dict | None:
        """取最早可跑批并标 running；无批 ⇒ None。

        可跑 = pending，或 running 且仍有未完成 unit（进程重启 / yield 后孤儿批）。
        `consume` 时先消费 console 请求（本函数是 idle 窗与 kick-once.py 的共同入口 ⇒
        请求的物化自动被覆盖）。
        """
        with self._tx("claim_pending") as ok:
            if not ok:
                return None
            if consume:
                try:
                    self.consume_requests()
                except Exception as e:
                    log(f"[batcheval] consume_requests failed (ignored): {type(e).__name__}: {e}")
            batches = self.all()
            for b in batches:
                st = b.get("status")
                units = b.get("units") or {}
                done = units.get("done") or []
                of = int(units.get("of") or 0)
                incomplete = of > 0 and len(done) < of
                if st == "pending" or (st == "running" and incomplete):
                    dirty = st != "running"
                    b["status"] = "running"
                    if dirty:
                        self._publish(batches)
                    return b
            return None

    def set_units_of(self, batch_id: str, of: int) -> None:
        """只定型 `units.of`（plan 展开后的回写），**不动 status**（原 `_persist_of`）。"""
        with self._tx("set_units_of") as ok:
            if not ok:
                return
            batches = self.all()
            for b in batches:
                if b.get("batch_id") == batch_id:
                    units = b.setdefault("units", {})
                    if units.get("of") != of:
                        units["of"] = of
                        self._publish(batches)
                    break

    def mark_unit_done(self, batch_id: str, unit_idx: int, node_dist: dict) -> None:
        """记一个 unit 结算 + 回填 node_dist，并按 `units.of` 决定批的状态。

        ★ `of == 0`（未定型）⇒ **不判 done**，回 pending 等下窗续跑（判决批在
        `plan_verdict_units` 回填 `of` 之前结算就会踩到）。
        ★ `aborted` 批的在途 unit 只回填 `node_dist`，**不复活**。
        """
        with self._tx("mark_unit_done") as ok:
            if not ok:
                return
            batches = self.all()
            for b in batches:
                if b.get("batch_id") != batch_id:
                    continue
                if b.get("status") == "aborted":
                    # P4：在途 unit 收尾只回填 node_dist，不复活已中止批。
                    if b.get("node_dist") != node_dist:
                        b["node_dist"] = node_dist
                        self._publish(batches)
                    return
                units = b.setdefault("units", {"of": 0, "done": []})
                dirty = b.get("node_dist") != node_dist
                if unit_idx not in units.get("done", []):
                    units["done"].append(unit_idx)
                    dirty = True
                b["node_dist"] = node_dist
                of = int(units.get("of") or 0)
                ndone = len(units.get("done") or [])
                # 未全完 → 回 pending，下一 idle 窗领剩余 unit（yield/重启安全）。
                nxt = "done" if (of > 0 and ndone >= of) else "pending"
                if b.get("status") != nxt:
                    dirty = True
                b["status"] = nxt
                if dirty:
                    self._publish(batches)
                return

    def requeue(self, batch_id: str) -> None:
        """认领后发现跑不了 → 状态回 pending（§3.7 台账是队列，状态流转合法）。

        ★ 不复活 `aborted`（且此时**不落盘**：什么都没改）。
        """
        with self._tx("requeue") as ok:
            if not ok:
                return
            batches = self.all()
            for b in batches:
                if b.get("batch_id") != batch_id:
                    continue
                if b.get("status") not in ("aborted", "pending"):
                    b["status"] = "pending"
                    self._publish(batches)
                return

    def reopen_for_resume(self, batch_id: str) -> None:
        """部分完成（yield/超时）→ running 改回 pending，`units.done` 保留供续跑。"""
        with self._tx("reopen_for_resume") as ok:
            if not ok:
                return
            batches = self.all()
            for b in batches:
                if b.get("batch_id") == batch_id and b.get("status") == "running":
                    b["status"] = "pending"
                    self._publish(batches)
                    break

    # ───────────────────────── 请求面（console 单写 / runner 单消费）─────────────────────────

    def pending_requests(self) -> list[dict]:
        """console 请求（append-only；坏行跳过）。"""
        return read_requests(self.root)

    def done_request_ids(self) -> set[str]:
        """已消费请求 id 集。"""
        return read_done_req_ids(self.root)

    def mark_requests_done(self, ids: set[str] | list[str]) -> None:
        """已消费标记（append-only，runner 唯一写者；`requests.jsonl` 本体永不重写）。

        ★ 刻意保留本地时区的无毫秒 `time.strftime`（`consumed_ts` 只是留痕，不参与任何
        比较 ⇒ 不动它，动它就改了字节契约）。
        """
        ids = list(ids)
        if not ids:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.root / REQUESTS_DONE_FILE, "a", encoding="utf-8") as f:
            for i in ids:
                f.write(json.dumps({"req_id": i, "consumed_ts": ts}) + "\n")

    def consume_requests(self) -> dict:
        """消费 console 请求文件（plan/evalboard-console-ux.md §5.3，P1/P4）。

        console 是 requests.jsonl 的唯一写者（append-only），本方法是唯一消费方：
          enqueue → 无同 key pending 批且未物化则建 pending 批（`enqueue`）；
          abort → pending/running 批标 aborted（`abort`）；
          ladder_start/ladder_stop → 跳过（console ticker 持有，runner 不碰）。
        幂等：重复消费无副作用（去重 + 物化检查 + abort 复用）。
        在 `claim()` 头部调用 ⇒ idle 窗与 kick-once.py 自动覆盖。
        任何失败只记日志，绝不抛出（训练主链零风险）。

        整轮**一把锁**（`_tx`），里面的具名转移再取锁只是可重入的嵌套 —— 这是必要的：
        若「锁忙」只体现在单个转移上，`enqueue` 会返回 None（与「去重跳过」不可区分）
        ⇒ 请求会被标成已消费却**没建批** = 丢请求。锁忙时整轮跳过、请求留给下一 idle 窗。
        """
        counts = {"consumed": 0, "enqueued": 0, "aborted": 0, "skipped": 0}
        try:
            with self._tx("consume_requests") as ok:
                if not ok:
                    return counts
                reqs = self.pending_requests()
                if not reqs:
                    return counts
                done_ids = self.done_request_ids()
                fresh = [r for r in reqs if _req_key(r) not in done_ids]
                if not fresh:
                    return counts
                consumed: list[str] = []
                for r in fresh:
                    kind = str(r.get("kind"))
                    key = _req_key(r)
                    if kind == "enqueue":
                        course = str(r.get("course", ""))
                        rung_from = str(r.get("rung_from", ""))
                        ckpt = str(r.get("ckpt", ""))
                        made = None
                        if course and rung_from and ckpt:
                            made = self.enqueue(
                                course=course,
                                rung_from=rung_from,
                                ckpt=ckpt,
                                req_ts=str(r.get("ts", "")),
                                requester=r.get("requester", "web"),
                                trigger=r.get("trigger", "standalone"),
                                policy=r.get("policy", "nn"),
                                iter=r.get("iter", 0),
                                ladder_pos=r.get("ladder_pos"),
                                k_seq=r.get("k_seq"),
                                init_sha16=r.get("init_sha16"),
                                only_rungs=r.get("only_rungs"),
                            )
                        if made is None:
                            counts["skipped"] += 1
                        else:
                            counts["enqueued"] += 1
                        consumed.append(key)
                    elif kind == "verdict":
                        corpus = str(r.get("corpus", ""))
                        ckpts = [
                            c
                            for c in (r.get("ckpts") or [])
                            if isinstance(c, dict) and str(c.get("path") or "")
                        ]
                        made_v = None
                        if corpus and ckpts:
                            made_v = self.enqueue_verdict(
                                corpus=corpus,
                                ckpts=ckpts,
                                req_ts=str(r.get("ts", "")),
                                requester=r.get("requester", "web"),
                                policy=r.get("policy", "nn"),
                                iter=r.get("iter", 0),
                                init_sha16=r.get("init_sha16"),
                                only_rungs=r.get("only_rungs"),
                            )
                        if made_v is None:
                            counts["skipped"] += 1
                        else:
                            counts["enqueued"] += 1
                        consumed.append(key)
                    elif kind == "abort":
                        if self.abort(str(r.get("batch_id", ""))):
                            counts["aborted"] += 1
                        consumed.append(key)
                    elif kind in ("ladder_start", "ladder_stop"):
                        continue  # console ticker 持有——runner 不消费、不标记
                    else:
                        consumed.append(key)  # 未知 kind：标记消费，防反复扫描
                        counts["skipped"] += 1
                if consumed:
                    self.mark_requests_done(consumed)
                counts["consumed"] = len(consumed)
                if counts["enqueued"] or counts["aborted"]:
                    log(f"[batcheval] consume_requests: {counts}")
                return counts
        except Exception as e:
            log(f"[batcheval] consume_requests failed (ignored): {type(e).__name__}: {e}")
            return counts


def _new_batch_id() -> str:
    """`b-<UTC 紧凑时间>-<4 hex>` —— console 侧只当字符串用，格式是字节契约的一部分。"""
    now = utc_now_iso()
    stamp = now.replace("-", "").replace(":", "").replace("T", "")
    stamp = stamp.replace(".", "").replace("Z", "")
    return f"b-{stamp}-{random.randrange(0x10000):04x}"


# ───────────────── 模块级适配器：既有签名（`rl.batch_eval` 门面再导出）─────────────────
# 所有者仍是 `BatchStore`（上面）；这里只是把「root 作首参」的老签名转给实例方法。
# 测试与 console 触发端按这些名字 import，故**不能删**（§5.5.6）。


def write_batches(root: Path, batches: list[dict]) -> None:
    """原始发布缝（**整表覆盖**）：只给测试/播种用；生产转移一律走具名转移。

    ⚠ 本函数是 `batches.jsonl` 的唯一「整表覆盖」入口 —— 生产代码里除了 `BatchStore._publish`
    的具名转移调用方，谁都不该调它（守卫把这条钉成闭集）。
    """
    BatchStore(root)._publish(batches)


def consume_requests(root: Path) -> dict:
    return BatchStore(root).consume_requests()


def claim_pending(root: Path) -> dict | None:
    return BatchStore(root).claim()


def mark_unit_done(root: Path, batch_id: str, unit_idx: int, node_dist: dict) -> None:
    BatchStore(root).mark_unit_done(batch_id, unit_idx, node_dist)


def mark_requests_done(root: Path, ids: set[str] | list[str]) -> None:
    BatchStore(root).mark_requests_done(ids)
