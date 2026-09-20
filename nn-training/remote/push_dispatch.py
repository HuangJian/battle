"""remote/push_dispatch.py — hub 中介的 push 派发（2026-09-18，P1 余下）。

用户口径（2026-09-18）：
  · push 模式下，hub 按**队列顺序**轮番向**空闲** ppo worker 推送任务；
  · 已推送任务超时则**回落队首**并改为推送**其它** worker；
  · 离线模式的课程不实时派发 ppo，但仍接收 it 权重/指标回传；
  · 课程数 < worker 数时沿用单课程那样的竞速（那条判据在 pull 侧的 `race_decision`）。

形状（为什么这样切）：
  * **注册表**（`PushWorkers`）：读 rl-config `nodes[]` 里 `gpu_push` 的条目——控制台的
    「worker 登记入口」回写的正是它们——并周期 `GET /ping` 探活；热重载按 mtime，控制台
    写完配置不必重启 hub（这个入口的全部意义就在这）。判据与训练侧 `_gpu_push_nodes`
    **同一把尺子**（`gpu_push` + `enabled`），否则会出现「训练侧认为该推这台、hub 认为
    一台都没有」——症状是 job 永远躺在队首。
  * **派发器**（`PushDispatcher`）：每拍只做「挑活 + 挑 worker」，**每课程至多一份在途**
    （轮 N+1 依赖轮 N 的权重，课程内本就有硬序；跨课程并行）；上传/等待/回灌放在**每份
    job 一条线程**里——几十 MB 的 POST 绝不能阻塞调度拍。
  * 结果入账与云机 POST 上来那条路**共用同一个函数**（`accept_result`）：两条腿的校验
    （job_id/data_fp/init_weights_fp/commit_echo 对账 + 首写锁定 + 租约校验）不可能一个
    有一个无——那正是「推模式静默写出错位结果」的入口。

与 pull 的关系：租约同源（`claim(..., worker_id="push:<id>")` + 心跳续租），所以「hub 代持
的推送」与「云机自领」在多课程调度面上是同一类占用——`/admin/queue` 的 inflight 持有人
一眼能分清是哪条腿（`push:` 前缀），避让记录（`_stale_holders`）也按同一身份比对。

本模块**不 import hub_server**（避免环）：hub 以鸭子类型传入，只用它的公开调度面
（`courses/mode_of/claimable_job_ids/claim/heartbeat/release/store_result/store_job_failure/
_job_dir`）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock
from typing import Any

from remote import net_http
from remote.protocol import (
    AUTH_HEADER,
    BLOB_NAMES,
    CLAIM_TTL_SEC,
    COURSE_MODE_OFFLINE,
    PUSH_DEAD_MISSES,
    PUSH_PING_SEC,
    PUSH_POLL_SEC,
    PUSH_TIMEOUT_SEC,
    TS_CODE_NAME,
    ProtocolError,
    RetryableError,
    blob_path,
    find_payload,
    normalize_manifest,
    pick_push_worker,
    push_job_wants_hub_push,
    push_worker_from_node,
    push_worker_id_of,
    rotation_order,
    validate_result,
)

#: 缺省登记来源：仓库的 `nn-training/rl-config.json`（控制台 worker 登记入口回写的那个文件）。
DEFAULT_PUSH_CONFIG = Path(__file__).resolve().parents[1] / "rl-config.json"


def _log_default(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [hub-push] {msg}", flush=True)


def _http(
    url: str,
    key: str,
    path: str,
    *,
    timeout: float,
    method: str = "GET",
    data: bytes | None = None,
) -> tuple[int, bytes] | None:
    """对 GPU worker_server 发一次请求；**网络层失败返回 None**（调用方按「没答」处理）。

    4xx/5xx 是**有答**（带状态码），只有连不上/超时/读断才是 None —— 分开这两种是刻意的：
    「worker 忙（409）」不该判它离场，而「连不上」必须尽快触发回落队首换人。
    """
    req = urllib.request.Request(
        f"{url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={AUTH_HEADER: f"Bearer {key}"},
    )
    try:
        with net_http.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception:
        return None


# ---------------------------------------------------------------- 结果入账（两条腿共用）


def accept_result(
    hub: Any,
    jid: str,
    result: dict,
    lease_token: str,
    *,
    log: Any = None,
) -> tuple[int, str]:
    """把一份 worker 回传结果校验并入账；返回 `(HTTP 状态, 说明)`。

    与 hub `_post_result` 的校验**逐条同源**（对账 → 租约 → 首写锁定），抽出来是为了让
    「云机 POST 上来」与「hub 代发后取回」两条腿不可能一条校验一条不校验——推模式若跳过
    对账，错的权重会被静默落盘成一轮「看起来正常」的训练。

    `log` 只在 400 时用（拒收原因必须留痕：云端结果对不上账是事故级信号）。
    """
    jd = hub._job_dir(jid)
    if not (jd / "manifest.json").exists():
        return 404, "unknown job"
    try:
        manifest = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
        normalize_manifest(manifest)
        validate_result(result, manifest, commit_echo_must_match=True)
    except (ValueError, ProtocolError) as e:
        if log:
            log(f"result rejected for job {jid}: {e}")
        return 400, f"result rejected: {e}"
    if not hub.result_token_ok(jid, lease_token):
        return 403, "lease mismatch — 非本 job 租约持有人"
    if not hub.store_result(jid, result):
        return 409, "result already stored (duplicate write-back)"
    return 200, "accepted"


# ---------------------------------------------------------------- 注册表 + 探活


class PushWorkers:
    """push worker 登记表 + 周期探活（hub 中介推送的唯一输入）。

    来源三条，按优先级合并（后写的覆盖先写的同 id 条目）：
      ① rl-config `nodes[]` 里 `gpu_push` 且 enabled 的条目（**控制台 worker 登记入口**
         回写的目标）——按 mtime 热重载；
      ② 运行时 `add()` 的条目（`POST /admin/push-workers`，volatile）；
      ③ `remove()` 的 id 进删除名单（覆盖面 ① 的条目，volatile）。

    「在线」= 探活答过 200 且连续失败未达 `PUSH_DEAD_MISSES`。**从没答过 = 不在线**：
    宁可这一拍不推（等下一次探活），也不要往一台可能是死的机器上推几十 MB。
    探活失败但未达阈值时置 `busy=True`（状态未知 ⇒ 当它在忙）——否则一次隧道抖动就会
    让调度器认为它空闲并二次推送同一门课。
    """

    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        now_fn: Any = time.time,
        log: Any = _log_default,
        ping_sec: float = PUSH_PING_SEC,
        ping_timeout: float = 3.0,
    ) -> None:
        self.path = Path(config_path) if config_path else DEFAULT_PUSH_CONFIG
        self.ping_sec = float(ping_sec)
        self.ping_timeout = float(ping_timeout)
        self._now = now_fn or time.time
        self._log = log or _log_default
        self._lock = Lock()
        self._workers: dict[str, dict] = {}
        self._extra: dict[str, dict] = {}
        self._removed: set[str] = set()
        self._mtime: float = -1.0
        self._last_ping: float = 0.0
        self.reloads = 0

    # ---- 登记表 ----
    def reload(self, force: bool = False) -> bool:
        """从 rl-config 重读登记表（mtime 变了才读）。返回是否真的重读了。

        保留既有探活状态（重读配置不该把在线/忙闲清零——控制台改一个节点不该让所有
        节点「重新上线」并触发一轮重复派发）。
        """
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        with self._lock:
            if not force and mtime == self._mtime:
                return False
            self._mtime = mtime
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
            cfg = json.loads(raw)
        except (OSError, ValueError) as e:
            self._log(f"WARN 读 push 登记表 {self.path} 失败（{e}）——沿用上一份")
            return False
        nodes = cfg.get("nodes") if isinstance(cfg, dict) else None
        if not isinstance(nodes, list):
            return False
        fresh: dict[str, dict] = {}
        for n in nodes:
            w = push_worker_from_node(n)
            if w is not None:
                fresh[w["id"]] = w
        with self._lock:
            # 运行时 add() 的条目必须在重载后仍在表里（它们住 `_extra` 就是为了这个）：
            # 否则控制台/冒烟临时挂的那台会在下一次 mtime 变化时静默消失。
            for wid, w in self._extra.items():
                fresh.setdefault(wid, {**_WORKER_DEFAULTS, **w})
            for wid, w in list(fresh.items()):
                merged = {**_WORKER_DEFAULTS, **w}
                old = self._workers.get(wid)
                if old is not None:
                    merged.update({k: old[k] for k in _KEEP_ON_RELOAD if k in old})
                merged["online"] = bool(merged.get("online"))
                merged["busy"] = bool(merged.get("busy"))
                fresh[wid] = merged
            self._workers = fresh
            self.reloads += 1
        return True

    def add(self, rec: dict) -> dict:
        """运行时登记一个 worker（id 缺省用 url）；返回归一条目。"""
        w = push_worker_from_node({**rec, "gpu_push": True})
        if w is None:
            raise ProtocolError("worker 条目需要非空 url（gpu_push 由本端点隐含）")
        with self._lock:
            self._extra[w["id"]] = w
            self._removed.discard(w["id"])
            self._workers.setdefault(w["id"], {**_WORKER_DEFAULTS, **w})
        return w

    def remove(self, wid: str) -> bool:
        """撤下一个 worker：进删除名单（覆盖面①）并从运行时表里去掉。"""
        wid = str(wid or "")
        if not wid:
            return False
        with self._lock:
            hit = self._extra.pop(wid, None) is not None or wid in self._workers
            self._removed.add(wid)
            self._workers.pop(wid, None)
        return hit

    def snapshot(self) -> list[dict]:
        """有效 worker 列表（含探活状态）——纯拷贝，调用方可随意改。"""
        with self._lock:
            merged: dict[str, dict] = {}
            for wid, w in self._workers.items():
                if wid in self._removed:
                    continue
                merged[wid] = {**w}
            for wid, w in self._extra.items():
                if wid in self._removed:
                    continue
                merged[wid] = {**w, **{k: v for k, v in merged.get(wid, {}).items()
                                       if k in _KEEP_ON_RELOAD}}
        return list(merged.values())

    def last_ping_at(self) -> float:
        return self._last_ping

    def is_online(self, wid: str) -> bool:
        with self._lock:
            w = self._workers.get(wid)
        return bool(w and w.get("online"))

    def note_push(self, wid: str, ok: bool) -> None:
        """记一次推送结局（观测用；失败的那次会让 `misses` 之外的计数动起来）。"""
        with self._lock:
            w = self._workers.get(wid)
            if w is None:
                return
            key = "pushed_ok" if ok else "pushed_fail"
            w[key] = int(w.get(key, 0)) + 1

    # ---- 探活 ----
    def ping_all(self) -> dict[str, str]:
        """周期探活（GET /ping）：更新在线/忙闲/排队深度；返回 `id -> ok|miss`。"""
        now = self._now()
        out: dict[str, str] = {}
        for w in self.snapshot():
            wid = str(w["id"])
            res = _http(w["url"], w.get("key", ""), "/ping", timeout=self.ping_timeout)
            status = None if res is None else res[0]
            ok = status == 200
            body: dict = {}
            if ok and res is not None:
                try:
                    loaded = json.loads(res[1].decode("utf-8"))
                    body = loaded if isinstance(loaded, dict) else {}
                except ValueError:
                    body = {}
            with self._lock:
                rec = self._workers.get(wid)
                if rec is None:
                    continue
                if ok:
                    rec["online"] = True
                    rec["misses"] = 0
                    rec["last_ok"] = now
                    rec["last_err"] = ""
                    rec["busy"] = bool(body.get("busy"))
                    rec["queued"] = int(body.get("queued") or 0)
                    rec["done"] = int(body.get("done") or 0)
                else:
                    rec["misses"] = int(rec.get("misses", 0)) + 1
                    if rec["misses"] >= PUSH_DEAD_MISSES:
                        rec["online"] = False
                    # 状态未知 ⇒ 当它在忙：一次抖动不能让调度器把它当空闲并二次推送。
                    rec["busy"] = True
                    rec["last_err"] = "no-answer" if status is None else f"HTTP {status}"
            out[wid] = "ok" if ok else "miss"
        self._last_ping = now
        return out

    def state(self) -> dict:
        """`/admin/push-workers` 的观测体。"""
        return {
            "config": str(self.path),
            "config_mtime": self._mtime,
            "reloads": self.reloads,
            "last_ping_ago": round(self._now() - self._last_ping, 1) if self._last_ping else None,
            "ping_sec": self.ping_sec,
            "workers": self.snapshot(),
            "removed": sorted(self._removed),
        }


#: 条目的统一形状：任何来源（配置 / 运行时 add）进来都带这套字段——消费者（挑选器、
#: `/admin/push-workers`）不必再防 KeyError，而「没探活过」永远表达为 `online=False`。
_WORKER_DEFAULTS = {
    "online": False,
    "busy": False,
    "queued": 0,
    "done": 0,
    "misses": 0,
    "last_ok": None,
    "last_err": "",
}

#: 重读配置/合并时保留的运行时字段（探活状态与计数）。
_KEEP_ON_RELOAD = (
    "online",
    "busy",
    "queued",
    "done",
    "misses",
    "last_ok",
    "last_err",
    "pushed_ok",
    "pushed_fail",
)


# ---------------------------------------------------------------- 派发器


class PushDispatcher:
    """hub 中介 push 派发器：按队列顺序推给空闲 worker，超时回落队首换 worker。

    一拍（`tick`）做四件事：热重载登记表 → 到点探活 → 挑活挑人 → 起一条 job 线程。
    **每课程至多一份在途**：课程内的轮次有硬序（轮 N+1 的 init 权重就是轮 N 的产出），
    同时推两份没有意义；跨课程并行才是要的并行度。

    只推**队首**：队首不是 push job（`manifest.dispatch != "push"`）⇒ 这门课本拍不推。
    不越过它推后面的——那会把轮次跑成乱序（pull 侧一直在按序取队首）。
    """

    def __init__(
        self,
        hub: Any,
        workers: PushWorkers,
        token: str,
        *,
        poll_sec: float = PUSH_POLL_SEC,
        timeout_sec: float = PUSH_TIMEOUT_SEC,
        push_attempts: int = 3,
        now_fn: Any = time.time,
        log: Any = _log_default,
    ) -> None:
        self.hub = hub
        self.workers = workers
        self.token = token  # 保留（worker authKey 优先；缺 authKey 的节点用它）
        self.poll_sec = float(poll_sec)
        self.timeout_sec = float(timeout_sec)
        # 单次 job POST 的重试次数（透传给 push_client.submit_job）。生产缺省 3：
        # 409「队满」/428/5xx 这类瞬时拒绝退避重试（2s/4s），换台前先等一等。
        # **测试**里「409 ⇒ 回落队首换一台」是调度层行为，不该陪跑这条重试梯子：
        # 实测 test_worker_refusing_job_requeues_to_another 为此白等 6s（2+4）。
        self.push_attempts = int(push_attempts)
        self._now = now_fn or time.time
        self._log = log or _log_default
        self._lock = Lock()
        self._inflight: dict[str, dict] = {}
        self._avoid: dict[str, set[str]] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.pushed = 0
        self.requeued = 0
        self.gave_up = 0

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="hub-push", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # 单拍异常绝不打死派发器
                self._log(f"派发拍异常（不致命）：{type(e).__name__}: {e}")
            self._wake.wait(self.poll_sec)
            self._wake.clear()

    # ---- 单拍（可单测：不依赖线程） ----
    def tick(self, *, now: float | None = None, ping: bool | None = None) -> dict:
        """一拍：重载 → 探活（到点才打）→ 派发。返回可读摘要（日志/测试/观测用）。"""
        now = self._now() if now is None else float(now)
        reloaded = self.workers.reload()
        pinged: dict[str, str] = {}
        due = ping if ping is not None else (
            now - self.workers.last_ping_at() >= self.workers.ping_sec
        )
        if due:
            pinged = self.workers.ping_all()
        started = self._dispatch()
        self.ticks += 1
        return {
            "ticks": self.ticks,
            "reloaded": reloaded,
            "pinged": pinged,
            "started": started,
            "inflight": sorted(self._inflight),
        }

    def _worker_counts(self) -> dict[str, int]:
        with self._lock:
            recs = list(self._inflight.values())
        counts: dict[str, int] = {}
        for rec in recs:
            wid = str(rec.get("worker") or "")
            counts[wid] = counts.get(wid, 0) + 1
        return counts

    def _course_inflight(self, course: str) -> bool:
        with self._lock:
            return any(r.get("course") == course for r in self._inflight.values())

    def _dispatch(self) -> list[str]:
        """挑活 + 挑人 + 起线程；返回本拍推出去的 job_id 列表。"""
        started: list[str] = []
        hub = self.hub
        for course in rotation_order(list(hub.courses()), getattr(hub, "_cursor", None)):
            if hub.mode_of(course) == COURSE_MODE_OFFLINE:
                continue  # 离线课不实时派发（只收回传）
            if self._course_inflight(course):
                continue  # 该课已有一份在途（课程内串行）
            queue = hub.claimable_job_ids(course)
            if not queue:
                continue
            jid = queue[0]
            jd = hub._job_dir(jid)
            try:
                manifest = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not push_job_wants_hub_push(manifest):
                continue  # 队首这轮归 pull/本机 —— 不越序推后面的
            with self._lock:
                avoid = set(self._avoid.get(jid, ()))
            worker = pick_push_worker(self.workers.snapshot(), self._worker_counts(), avoid)
            if worker is None:
                continue  # 没有空闲 worker —— 等下一拍
            wid = str(worker["id"])
            lease = hub.claim(jid, ttl=CLAIM_TTL_SEC, worker_id=push_worker_id_of(wid))
            if lease is None:
                continue  # 活租约在持 / 并发领取竞负
            rec = {
                "job_id": jid,
                "course": course,
                "worker": wid,
                "lease": lease,
                "state": "upload",
                "started": self._now(),
            }
            with self._lock:
                self._inflight[jid] = rec
            t = threading.Thread(
                target=self._run_one,
                args=(course, jid, worker, lease, manifest, jd, rec),
                name=f"hub-push-{jid[:10]}",
                daemon=True,
            )
            t.start()
            started.append(jid)
        return started

    # ---- 一份 job 的整条腿（跑在自己的线程里） ----
    def _run_one(
        self,
        course: str,
        jid: str,
        worker: dict,
        lease: str,
        manifest: dict,
        jd: Path,
        rec: dict,
    ) -> None:
        wid = str(worker["id"])
        url = str(worker["url"])
        key = str(worker.get("key") or self.token)
        t0 = self._now()
        try:
            payload_path = find_payload(jd)
            if payload_path is None:
                # 本地盘上没有 payload：与 worker 无关，别把它拉黑。
                self._requeue(jid, lease, wid, "payload 不在盘上", avoid_worker=False)
                return
            payload_bytes = payload_path.read_bytes()
            code_path = jd / "code.zip"
            code_bytes = code_path.read_bytes() if code_path.exists() else None
            ts_path = jd / TS_CODE_NAME
            ts_bytes = ts_path.read_bytes() if ts_path.exists() else None
            blobs = {
                n: p.read_bytes()
                for n in BLOB_NAMES
                if (p := blob_path(jd, n)).exists()
            }
            from remote.push_client import submit_job

            rec["state"] = "upload"
            send = submit_job(
                url,
                key,
                manifest,
                payload_bytes,
                code_bytes,
                blobs=blobs,
                ts_code_zip=ts_bytes,
                attempts=self.push_attempts,
                log=lambda m: self._log(f"{jid} -> {wid}: {m}"),
            )
            self.pushed += 1
            self.workers.note_push(wid, True)
            # 传输实测回写 job 的 wire 账（训练侧 `_wire_from_result(is_push=True)` 读它）：
            # 直推那条腿是训练侧自己记的，推经 hub 这条腿只能 hub 记——不记就是一轮
            # iteration 行的 up_bytes/up_sec 全空（多课程并行时无法按腿分组看流量）。
            self.hub.record_push_wire(
                jid, int(send["body_bytes"]), int(send["payload_bytes"]), float(send["upload_sec"])
            )
            self._log(
                f"{jid} 已推送 {wid}（payload={send['payload_bytes']}B "
                f"code={send['code_bytes']}B ts={send['ts_code_bytes']}B "
                f"blob={send['blob_bytes']}B 上传 {send['upload_sec']}s attempts={send['attempts']}）"
            )
            rec["state"] = "waiting"
            self._await_result(course, jid, wid, url, key, lease, rec, t0)
        except (RetryableError, ProtocolError) as e:
            # 推送本身没成：多半是这台 worker 此刻接不了（409 队满 / 428 缺缓存 /
            # 隧道抖动）——回落队首，换其它 worker 再试。
            self._requeue(jid, lease, wid, f"{type(e).__name__}: {e}", avoid_worker=True)
        except Exception as e:  # 线程绝不静默死掉
            self._requeue(jid, lease, wid, f"{type(e).__name__}: {e}", avoid_worker=True)
        finally:
            with self._lock:
                self._inflight.pop(jid, None)
            self._wake.set()

    def _await_result(
        self,
        course: str,
        jid: str,
        wid: str,
        url: str,
        key: str,
        lease: str,
        rec: dict,
        t0: float,
    ) -> None:
        """等 worker 出结果：续租 + 探活判死 + 到点回落。结果就地入账（两条腿同一函数）。"""
        deadline = self._now() + self.timeout_sec
        while not self._stop.is_set():
            if self._now() > deadline:
                self._requeue(
                    jid, lease, wid, f"推送后 {self.timeout_sec:.0f}s 无结果", avoid_worker=True
                )
                return
            if not self.workers.is_online(wid):
                self._requeue(
                    jid,
                    lease,
                    wid,
                    f"worker 连续 {PUSH_DEAD_MISSES} 次探活失败（判定离场）",
                    avoid_worker=True,
                )
                return
            # hub 是这份 job 的租约持有人：不续租就会被 pull worker 领走（同一份活两处跑）。
            self.hub.heartbeat(jid, lease)
            res = _http(url, key, f"/job/{jid}/result", timeout=30.0)
            if res is None:
                time.sleep(self.poll_sec)
                continue
            status, body = res
            if status == 200:
                try:
                    result = json.loads(body.decode("utf-8"))
                except ValueError as e:
                    self._requeue(jid, lease, wid, f"结果不是 JSON（{e}）", avoid_worker=True)
                    return
                if not isinstance(result, dict):
                    self._requeue(jid, lease, wid, "结果不是对象", avoid_worker=True)
                    return
                code_, why = accept_result(self.hub, jid, result, lease, log=self._log)
                if code_ == 200:
                    self._log(f"{jid} <- {wid} 结果已回灌（{self._now() - t0:.0f}s）")
                elif code_ in (400, 403, 409):
                    # 结果本身不合契约：换一台重跑是合理的（不把这份结果投毒进账本）。
                    self._requeue(jid, lease, wid, f"结果入账被拒 HTTP {code_}: {why}",
                                  avoid_worker=True)
                else:
                    self._log(f"{jid} 结果入账返回 HTTP {code_}（{why}）——继续等")
                return
            if status == 410:
                # 节点判定「这份活在这台机器上跑不成」（能力缺失类）：终局，落 fail.json，
                # 训练侧随后从 /jobs/{id}/result 拿 410 + 原因立即停腿（不再等满超时）。
                try:
                    loaded = json.loads(body.decode("utf-8"))
                    fb = loaded if isinstance(loaded, dict) else {}
                except ValueError:
                    fb = {}
                self.hub.store_job_failure(
                    jid,
                    {
                        "reason": str(fb.get("error") or "worker 判定跑不成")[:2000],
                        "kind": str(fb.get("fail_kind") or "")[:200],
                        "detail": "hub push dispatch（hub 代发后取回）",
                        "worker": wid,
                        "ts": self.hub._now(),
                    },
                )
                self._log(f"{jid} 被 {wid} 判定终局失败：{fb.get('error')}")
                return
            if status in (409, 428):
                self._requeue(jid, lease, wid, f"worker 拒绝这份活（HTTP {status}）",
                              avoid_worker=True)
                return
            if status >= 500:
                time.sleep(self.poll_sec)
                continue
            self._requeue(jid, lease, wid, f"HTTP {status}", avoid_worker=True)
            return
        self._requeue(jid, lease, wid, "派发器停机", avoid_worker=False)

    def _requeue(self, jid: str, lease: str, wid: str, why: str, *, avoid_worker: bool) -> None:
        """回落队首（放掉租约 ⇒ job 立刻可再领）并记下「别再给这台」。"""
        self.hub.release(jid, lease)
        self.requeued += 1
        if avoid_worker and wid:
            with self._lock:
                self._avoid.setdefault(jid, set()).add(wid)
            self.workers.note_push(wid, False)
        self._log(f"{jid} 回落队首（{why}）—— 改推其它 worker（避免 {wid if avoid_worker else '-'}）")

    # ---- 观测 ----
    def state(self) -> dict:
        with self._lock:
            inflight = [dict(r) for r in self._inflight.values()]
            avoid = {k: sorted(v) for k, v in self._avoid.items()}
        return {
            "running": self._thread is not None and not self._stop.is_set(),
            "poll_sec": self.poll_sec,
            "timeout_sec": self.timeout_sec,
            "ticks": self.ticks,
            "pushed": self.pushed,
            "requeued": self.requeued,
            "gave_up": self.gave_up,
            "inflight": inflight,
            "avoid": avoid,
        }
