"""hub/offline_push.py —— hub 侧的**离线 push 派发器**（2026-10-24，W5）。

与 `remote/push_dispatch.py`（把 job 推给 GPU worker）平行，方向相反的那一半：离线 worker 只
摆好一个 HTTP 面 + 隧道，**hub 用自己的出站连接**去派单、按轮拉产物、收尾。

    每拍（`OFFLINE_PUSH_POLL_SEC=10s`）
      ├─ 登记表热重载 + 到点探活（`PushWorkers` 的同一套在线/忙闲语义）
      ├─ 版本门：`GET /offline/push/state` 的 `proto` 必须是 `offline_push_v1`
      │   （旧 worker 没有这些端点 ⇒ 404 ⇒ 不派发 + 日志指路升级，评审 P1-7）
      ├─ 修复/续跑：盘上有在途 assignment ⇒ 重新 claim（`mine`）后继续拉
      └─ 派发：空闲 worker × 可领课程 → `claim_offline` → 组外层 zip → 起一条 pump 线程

    每条 assignment 一条 pump 线程（几十 MB 的逐轮 body 绝不阻塞调度拍）：
      `/state` → 新进度打点 → 每 60s 心跳（只续 TTL，活性仍只认 progress）
      → 按 it 升序拉新轮（rev 变了也重拉）→ **共享 ingest** → 更新 cursor
      → `POST /ack`（带 revs，worker 只删 rev 相符的 body）
      → 有 final ⇒ 拉 result → 共享 ingest（盖章 / 滚下一段）→ release → ack(result)
      → worker 报 failed/cancelled ⇒ 先收完已完成的轮，再 release + 收摊

三条**不是口味问题**的判据：

* **权威 = assignment**（评审 P0-5）：course / run_id / pack_sha 都是 hub 选的。worker 自报的
  体一律按 assignment 校正/校验（`hub/offline_ingest._authority_body`），不符 ⇒ 409 +
  `conflict`，**不落盘、不推进 cursor**。
* **缺包 = 请导包，不是跳过**（评审 P0-4）：push-only 部署里没有 pull 盘，导包入口原本只有
  云机的 claim —— 派发器看见「没包」就跳过，那门课永远不会有包。这里接上
  `begin_pending_export` + `trigger_pack_export`（与 claim 缺包分支**同一份**实现）。
* **ack 只看 rev**（评审 P0-3）：`{task_id, result, upto_it, revs}` 交给
  `deliver_outbox.ack_outbox`（worker 侧唯一实现）——迟到的评估行（rev+1）不会被删掉。

本模块**不 import `hub.admin`**（admin 是 L0；控制面只经鸭子类型的 `hub` 读状态）;
秩 **L3**：`remote.push_dispatch`(L2) + `hub.offline_ingest`(L1) + `hub.offline_push_assign`(L0)
+ `remote.offline_push_client`(L0)。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.logutil import tag_logger
from common.protocol import (
    CAP_OFFLINE_PUSH,
    OFFLINE_PUSH_PACK_DIR,
    OFFLINE_PUSH_PROTO,
    OFFLINE_PUSH_RESUME_DIR,
    OFFLINE_PUSH_TASK_NAME,
    ProtocolError,
    push_worker_id_of,
    rotation_order,
)
from hub.offline_ingest import (
    AuthorityConflictError,
    ingest_offline_artifact,
    ingest_offline_result,
    trigger_pack_export,
)
from hub.offline_push_assign import OfflinePushAssignments
from hub.task_pack import pack_index_meta
from remote import offline_push_client as client
from remote.push_dispatch import PushWorkers, run_tick_loop

#: 调度拍（秒）：与 GPU 派发器同一量级；hub 的派发延迟 = 一拍 + 一次 zip 组装。
OFFLINE_PUSH_POLL_SEC = 10.0
#: pump 轮询节拍（秒）：worker 的 outbox 写入是几秒级的，比它快只是白花请求。
OFFLINE_PUSH_PUMP_SEC = 5.0
#: 心跳间隔（秒）：**只续 TTL**；活性仍只认 `progress_offline`（心跳不作活性）。
OFFLINE_PUSH_HEARTBEAT_SEC = 60.0
#: 派发失败后的冷却（秒）：同一门课/同一台机器不在每一拍上重撞。
OFFLINE_PUSH_COOLDOWN_SEC = 120.0
#: 一次 pump 循环里最多拉几轮（防「积压 200 轮」把一拍拖成十分钟）。
OFFLINE_PUSH_ROUNDS_PER_PASS = 8
#: 任务包 zip 的临时前缀（`<tmp>/offline-push-*`）；上传完成后整目录删掉。
_TMP_PREFIX = "offline-push-"

_log_default = tag_logger("hub-offline-push", lambda: time)


@dataclass
class _PumpState:
    """一条 pump 线程的运行期读数（不进 assignment 的字段放这里）。"""

    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    misses: int = 0
    last_seq: int = -1


class OfflinePushWorkers(PushWorkers):
    """离线 push 的登记表（`capability="offline_push"`）+ **协议版本门**。

    在线/忙闲/热重载/退避全部复用基类（第三份登记表会立刻漏掉一个修复）；这里只加一件
    基类不该知道的事：`/state` 的 `proto`。GPU 的 `/ping` 体里没有这个字段，所以版本门
    不能挂在探活上（评审 P1-7）——它要问那个**只有新 worker 才有的端点**。

    探测结果记在每个条目的 `proto_ok` 上（`None` = 还没问过）：派发只看 `True`，
    不知道就不推（与「从没答过 = 不在线」同一条保守方向）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("capability", CAP_OFFLINE_PUSH)
        super().__init__(*args, **kwargs)

    def probe_proto(self, *, timeout: float = 5.0) -> dict[str, bool]:
        """对在线且没探过的条目问一次 `/state`；返回 `id -> 兼容`。"""
        out: dict[str, bool] = {}
        for w in self.snapshot():
            wid = str(w.get("id") or "")
            if not wid or not w.get("online"):
                continue
            if w.get("proto_ok") is not None:
                continue
            st = client.get_state(str(w.get("url") or ""), str(w.get("key") or ""), timeout=timeout)
            ok = bool(st) and str((st or {}).get("proto") or "") == OFFLINE_PUSH_PROTO
            self.note_fields(wid, proto_ok=bool(ok), proto=str((st or {}).get("proto") or ""))
            out[wid] = bool(ok)
            if not ok:
                self._log(
                    f"WARN worker {wid} 不支持离线 push（/offline/push/state 不可用或 proto 不符）"
                    f"——升级它的 notebook 代码后再启用；本拍不向它派单"
                )
        return out

    def eligible(self) -> list[dict]:
        """可接单的 worker：在线、不忙、`proto_ok=True`。"""
        return [
            w
            for w in self.snapshot()
            if bool(w.get("online")) and not bool(w.get("busy")) and w.get("proto_ok") is True
        ]


class OfflinePushDispatcher:
    """hub 侧的离线 push 派发器（调度拍 + 每单一条 pump 线程）。

    `assignments` / `port` 决定在途账本落点（`<root>/offline-push.<port>.json`）；`root`
    缺省 = `hub.traj_root()`（与 `task_pack` 同一份 traj 根），测试可显式给 `tmp_path`。
    """

    def __init__(
        self,
        hub: Any,
        workers: OfflinePushWorkers,
        token: str,
        *,
        root: str | Path | None = None,
        port: int = 0,
        poll_sec: float = OFFLINE_PUSH_POLL_SEC,
        pump_sec: float = OFFLINE_PUSH_PUMP_SEC,
        heartbeat_sec: float = OFFLINE_PUSH_HEARTBEAT_SEC,
        cooldown_sec: float = OFFLINE_PUSH_COOLDOWN_SEC,
        now_fn: Any = time.time,
        log: Any = _log_default,
        client_mod: Any = client,
    ) -> None:
        self.hub = hub
        self.workers = workers
        self.token = str(token or "")
        self.poll_sec = float(poll_sec)
        self.pump_sec = float(pump_sec)
        self.heartbeat_sec = float(heartbeat_sec)
        self.cooldown_sec = float(cooldown_sec)
        self._now = now_fn or time.time
        self._log = log or _log_default
        self._client = client_mod
        self.port = int(port or 0)
        path_root = Path(root) if root is not None else self._default_root()
        self.assignments = OfflinePushAssignments(
            path_root / f"offline-push.{self.port}.json", now=self._now, log=self._log
        )
        self._lock = threading.Lock()
        self._pumps: dict[str, _PumpState] = {}
        self._cooldown: dict[str, float] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.dispatched = 0
        self.failed = 0
        self.conflicts = 0

    # ------------------------------------------------------------ 生命周期
    def _default_root(self) -> Path:
        root = None
        try:
            root = self.hub.traj_root()
        except Exception:
            root = None
        return Path(root) if root is not None else Path.cwd()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=run_tick_loop,
            kwargs={
                "stop": self._stop,
                "wake": self._wake,
                "poll_sec": self.poll_sec,
                "tick": self.tick,
                "log": self._log,
            },
            name="hub-offline-push",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            pumps = list(self._pumps.values())
        for st in pumps:
            st.stop.set()


    # ------------------------------------------------------------ 单拍
    def tick(self, *, now: float | None = None, ping: bool | None = None) -> dict:
        """一拍：重载 → 探活（到点）→ 版本门 → 续跑 → 派发。返回可读摘要。"""
        now = self._now() if now is None else float(now)
        reloaded = self.workers.reload()
        pinged: dict[str, str] = {}
        due = ping if ping is not None else (
            now - self.workers.last_ping_at() >= self.workers.ping_sec
        )
        if due:
            pinged = self.workers.ping_all()
        probed = self.workers.probe_proto()
        resumed = self._resume_pumps()
        started = self._dispatch(now)
        self.ticks += 1
        return {
            "ticks": self.ticks,
            "reloaded": reloaded,
            "pinged": pinged,
            "probed": probed,
            "resumed": resumed,
            "started": started,
            "assignments": sorted(self.assignments.snapshot()),
        }

    # ------------------------------------------------------------ 续跑（hub 重启后）
    def _resume_pumps(self) -> list[str]:
        """盘上有在途 assignment 且没有活线程 ⇒ 重新 claim（`mine`）后接着拉。

        ★ 不重新上传任务包：worker 那边可能已经在跑了（`tasks/<id>/outbox` 里有轮次）。
        重新派一遍会换 `task_id`，旧 outbox 就永远没人 ack。这里只做「换 token + 继续拉」——
        hub 重启换 token 不影响旧 worker 的产物（评审 §6.8 的 hub 重启那一行）。
        """
        resumed: list[str] = []
        for course, rec in self.assignments.snapshot().items():
            if str(rec.get("phase")) not in ("dispatching", "running"):
                continue
            with self._lock:
                live = course in self._pumps and self._pumps[course].thread is not None
            if live:
                continue
            wid = str(rec.get("worker_id") or "")
            url, key = self._endpoint_of(wid)
            if not url:
                self._log(f"在途单 {course} 的 worker（{wid}）已不在登记表 —— 释放租约，等重派")
                self._release(course, str(rec.get("lease_token") or ""))
                self.assignments.drop(course)
                continue
            lease, why = self.hub.claim_offline(course, wid)
            if why:
                self._log(f"在途单 {course} 重新 claim 被拒（{why}）——丢账本，等下一拍")
                self.assignments.drop(course)
                continue
            rec = self.assignments.set_fields(
                course, lease_token=str(lease.get("token") or ""), phase="running"
            ) or rec
            self._start_pump(course, rec, url, key)
            resumed.append(course)
            self._log(f"在途单 {course} 续跑（worker={wid} cursor=it{rec.get('cursor_it', 0)}）")
        return resumed

    def _endpoint_of(self, worker_id: str) -> tuple[str, str]:
        wid = str(worker_id or "")
        bare = wid[5:] if wid.startswith("push:") else wid
        for w in self.workers.snapshot():
            if str(w.get("id")) == bare:
                return str(w.get("url") or ""), str(w.get("key") or self.token)
        return "", ""

    # ------------------------------------------------------------ 派发
    def _dispatch(self, now: float) -> list[str]:
        started: list[str] = []
        free = self.workers.eligible()
        if not free:
            return started
        busy_courses = set(self.assignments.snapshot())
        for worker in free:
            wid = str(worker["id"])
            if self.assignments.of_worker(push_worker_id_of(wid)):
                continue  # 这台机器名下已有在途单（一单一份 outbox，串行）
            rows = {str(r["course"]): r for r in self.hub.offline_tasks(worker=push_worker_id_of(wid))}
            for course in rotation_order(list(self.hub.courses()), getattr(self.hub, "_cursor", None)):
                if course not in rows or course in busy_courses:
                    continue
                if self._cooling(course, now):
                    continue
                row = rows[course]
                if not bool(row.get("claimable")):
                    continue
                pack = self._pack_for(course, row, wid, now)
                if pack is None:
                    continue
                lease, why = self.hub.claim_offline(course, push_worker_id_of(wid))
                if why:
                    continue
                meta = self._pack_meta(pack)
                rec = {
                    "course": course,
                    "worker_id": push_worker_id_of(wid),
                    "task_id": "",
                    "pack_sha256": str((row.get("pack") or {}).get("sha256") or ""),
                    "pack_name": pack.name,
                    "pack_path": str(pack),
                    "run_id": str(meta.get("run_id") or ""),
                    "lease_token": str(lease.get("token") or ""),
                    "cursor_it": 0,
                    "revs": {},
                    "phase": "dispatching",
                    "conflict": "",
                    "created_at": now,
                    "updated_at": now,
                }
                self.assignments.put(rec)
                busy_courses.add(course)
                self.dispatched += 1
                started.append(course)
                self._start_pump(course, rec, str(worker["url"]), str(worker.get("key") or self.token))
                self._log(
                    f"派发 {course} -> {push_worker_id_of(wid)}"
                    f"（pack={pack.name} run={rec['run_id'] or '-'}）"
                )
                break  # 一台机器一次只接一单
        if started:
            self._wake.set()
        return started

    def _cooling(self, key: str, now: float) -> bool:
        at = float(self._cooldown.get(key, 0.0) or 0.0)
        return at > 0.0 and now - at < self.cooldown_sec

    def _cool(self, key: str, now: float | None = None) -> None:
        self._cooldown[key] = self._now() if now is None else float(now)

    def _pack_meta(self, pack: Path) -> dict:
        try:
            return pack_index_meta(pack)
        except Exception:
            return {"run_id": "", "it": 0, "end_it": 0}

    def _pack_for(self, course: str, row: dict, wid: str, now: float) -> Path | None:
        """这门课现在能不能派：有包（且不是过期包）→ 返回路径；否则「请导包」并返回 None。

        ★ P0-4：**缺包 ⇒ 请导包，不是跳过**。推模式把云机从 claim 上拿走之后，导包链就没有
        别的入口了 —— 这里跳过，那门课永远不会出包。
        """
        try:
            pack = Path(self.hub.task_pack_path(course))
        except ProtocolError as e:
            self._log(f"{course}: 课程名不可用（{e}）——跳过")
            return None
        if not pack.is_file() or row.get("pack") is None:
            if self._cooling(course, now):
                return None
            verdict, why = self.hub.begin_pending_export(course, push_worker_id_of(wid))
            if verdict != "pending":
                self._log(f"{course}: 缺包且无法记 pending_export（{verdict}: {why}）")
                self._cool(course, now)
                return None
            out = trigger_pack_export(self.hub, course)
            self._cool(course, now)
            self._log(f"{course}: 缺包 ⇒ 请控制台导包（{out['state']}）：{out['note']}")
            return None
        if not str(self._pack_meta(pack).get("run_id") or ""):
            # 没有 run_id 的包 = 身份不可知 ⇒ 权威面（P0-5）无从校验，宁可这一拍不派。
            # 真包一定带它（`remote/bundle.export_bundle` 写 `tools.task.json`），所以这条
            # 命中意味着包被手改过/导出器坏了 —— 说清楚，别静默跑一段「无主」的产物。
            self._log(f"{course}: 任务包 {pack.name} 缺 run_id —— 本拍不派（重导一份包）")
            return None
        if str(row.get("stale_reason") or ""):
            # 旧包比没包更危险（云机会从旧起点重跑几十轮）⇒ 先重导，这一拍不派。
            if not self._cooling(course, now):
                out = trigger_pack_export(self.hub, course)
                self._cool(course, now)
                self._log(
                    f"{course}: 包过期（{row.get('stale_reason')}）⇒ 请控制台重导"
                    f"（{out['state']}）——本拍不派"
                )
            return None
        return pack

    # ------------------------------------------------------------ 外层任务包
    def build_task_zip(self, course: str, pack: Path, run_id: str = "") -> Path:
        """组外层 zip（`task.json` + `pack/` + 可选 `resume/`）→ 临时路径。

        `pack` 本身是 zip ⇒ `ZIP_STORED` 直写（再压一遍只是白烧 CPU）；`resume/` 取
        `hub.resume_anchor()` 里**比包更新**的那一轮（锚点只发整份：delta 只活在回传腿与
        hub 落地器之间，云机拿回去也解不开）。
        """
        meta = self._pack_meta(pack)
        task_id = self._new_task_id()
        tmp = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX))
        out = tmp / f"task-{task_id}.zip"
        resume = self._anchor_for(course, int(meta.get("it") or 0))
        doc = {
            "proto": OFFLINE_PUSH_PROTO,
            "task_id": task_id,
            "course": course,
            "pack_name": pack.name,
            "pack_sha256": self._sha256(pack),
            "run_id": str(run_id or meta.get("run_id") or ""),
            "it": int(meta.get("it") or 0),
            "end_it": int(meta.get("end_it") or 0),
            "created_at": float(self._now()),
        }
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(OFFLINE_PUSH_TASK_NAME, json.dumps(doc, ensure_ascii=False))
            with open(pack, "rb") as src, zf.open(f"{OFFLINE_PUSH_PACK_DIR}/{pack.name}", "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if resume is not None:
                it_dir, files = resume
                for name in files:
                    part = it_dir / name
                    if not part.is_file():
                        continue
                    with open(part, "rb") as s2, zf.open(
                        f"{OFFLINE_PUSH_RESUME_DIR}/it-{int(it_dir.name.split('-')[-1]):03d}/{name}", "w"
                    ) as d2:
                        shutil.copyfileobj(s2, d2, 1 << 20)
        self.assignments.set_fields(course, task_id=task_id)
        return out

    def _anchor_for(self, course: str, pack_it: int) -> tuple[Path, list[str]] | None:
        """比包更新的续跑锚点（`(it-目录, 件名)`）；没有/不比包新 ⇒ None。"""
        try:
            anchor = self.hub.resume_anchor(course)
        except Exception:
            return None
        if not anchor or int(anchor.get("it") or 0) <= pack_it:
            return None
        files = anchor.get("files") if isinstance(anchor.get("files"), dict) else {}
        names = [str(n) for n in (files.values() if files else ()) if str(n)]
        if not names:
            names = ["weights.json", "opt.tar", "row.json"]
        return Path(str(anchor.get("_dir") or "")), names

    @staticmethod
    def _new_task_id() -> str:
        import secrets

        return secrets.token_hex(8)

    @staticmethod
    def _sha256(path: Path) -> str:
        import hashlib

        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------ pump
    def _start_pump(self, course: str, rec: dict, url: str, key: str) -> None:
        st = _PumpState()
        with self._lock:
            old = self._pumps.get(course)
            if old is not None and old.thread is not None and old.thread.is_alive():
                return
            self._pumps[course] = st
        st.thread = threading.Thread(
            target=self._pump,
            args=(course, dict(rec), url, key, st),
            name=f"hub-offline-push-{course[:12]}",
            daemon=True,
        )
        st.thread.start()

    def _pump(self, course: str, rec: dict, url: str, key: str, st: _PumpState) -> None:
        """一条 assignment 的整条腿（上传 → 逐轮拉取 → ack → 收尾）。"""
        tmp_zip: Path | None = None
        try:
            if not rec.get("task_id"):
                tmp_zip = self.build_task_zip(course, Path(str(rec["pack_path"])), str(rec.get("run_id") or ""))
                rec = self.assignments.get(course) or rec
                status, body = self._upload(url, key, tmp_zip, course)
                if status != 202:
                    self._log(
                        f"{course}: 任务派发失败（HTTP {status}：{body.get('error') or body.get('status') or '-'}）"
                        "——释放租约，稍后换机/换课"
                    )
                    self._drop(course, rec, reason="dispatch-failed")
                    return
                self.assignments.set_fields(course, phase="running")
                self.workers.note_fields(
                    str(rec.get("worker_id") or "").removeprefix("push:"), busy=True
                )
                self._log(f"{course}: 任务已受理（task={rec.get('task_id') or body.get('task_id')}）")
            self._pump_loop(course, url, key, st)
        except Exception as e:  # 线程绝不静默死掉
            self._log(f"{course}: pump 异常 {type(e).__name__}: {e}")
            self._drop(course, self.assignments.get(course) or rec, reason="pump-error")
        finally:
            if tmp_zip is not None:
                shutil.rmtree(tmp_zip.parent, ignore_errors=True)
            with self._lock:
                if self._pumps.get(course) is st:
                    self._pumps.pop(course, None)

    def _upload(self, url: str, key: str, zip_path: Path, course: str) -> tuple[int, dict]:
        out = self._client.post_task(url, key, zip_path)
        if out is None:
            return 0, {"error": "worker 不可达"}
        if out[0] == 202 and out[1].get("status") == "duplicate":
            self._log(f"{course}: worker 已受理同一单（duplicate）——继续拉")
        return int(out[0]), dict(out[1])

    def _drop(self, course: str, rec: dict, *, reason: str = "") -> None:
        """释放租约 + 丢账本（任务没派成/跑不了）。**不动** worker 的 outbox（可能还有轮次）。"""
        lease = str(rec.get("lease_token") or "")
        if lease:
            self._release(course, lease)
        self.assignments.drop(course)
        self.failed += 1
        if reason:
            self._log(f"{course}: 收摊（{reason}）")

    def _release(self, course: str, lease: str) -> None:
        try:
            self.hub.release_offline(course, lease)
        except Exception as e:
            self._log(f"{course}: release 失败（{type(e).__name__}: {e}）——等 TTL")

    def _pump_loop(self, course: str, url: str, key: str, st: _PumpState) -> None:
        last_beat = 0.0
        while not self._stop.is_set() and not st.stop.is_set():
            rec = self.assignments.get(course)
            if rec is None:
                return
            if not self._still_mine(course, rec):
                self._log(f"{course}: 租约已不属于我们（换主/停课）——停止拉取，不 cancel")
                self.assignments.drop(course)
                return
            state = self._client.get_state(url, key)
            if state is None:
                st.misses += 1
                time.sleep(self.pump_sec)
                continue
            st.misses = 0
            proto = str(state.get("proto") or "")
            if proto and proto != OFFLINE_PUSH_PROTO:
                self._log(
                    f"{course}: worker 协议不符（{proto!r} ≠ {OFFLINE_PUSH_PROTO}）"
                    "——取消这一单并等它升级"
                )
                self._client.cancel_task(url, key)
                self._drop(course, rec, reason="proto-mismatch")
                return
            rec = self._ingest_rounds(course, url, key, rec)
            if rec is None:
                return  # 冲突/坏体：已在 _ingest_rounds 里收摊
            now = self._now()
            if now - last_beat >= self.heartbeat_sec:
                last_beat = now
                _ttl, why = self.hub.heartbeat_offline(course, str(rec.get("lease_token") or ""))
                if why in ("expired", "revoked", "taken"):
                    self._log(f"{course}: 心跳被拒（{why}）——hub 侧已换主，停止拉取")
                    self.assignments.drop(course)
                    return
                self._note_progress(course, url, key, rec, st)
            if self._finish_if_ready(course, url, key, rec, state):
                return
            self._settle_terminal(course, url, key, rec, state)
            time.sleep(self.pump_sec)

    def _still_mine(self, course: str, rec: dict) -> bool:
        """课程还开着、租约 token 还是我们的（**不调网络**：两个本地读面）。"""
        try:
            if not self.hub.course_open(course):
                return False
            hold = self.hub.hold_of(course)
            return str(hold.get("token") or "") == str(rec.get("lease_token") or "") or not hold
        except Exception:
            return True

    def _note_progress(self, course: str, url: str, key: str, rec: dict, st: _PumpState) -> None:
        """worker 有新进度 ⇒ `progress_offline`（活性只认它）。失败不致命。"""
        prog = self._client.get_progress(url, key, since=st.last_seq)
        if not prog or prog.get("unchanged"):
            return
        seq = int(prog.get("seq") or 0)
        ok, why = self.hub.progress_offline(course, str(rec.get("lease_token") or ""))
        if ok:
            st.last_seq = seq
            self._log(
                f"{course}: 进度 seq={seq} {prog.get('kind') or ''} it{prog.get('it', 0)}"
                f" {prog.get('done', 0)}/{prog.get('total', 0)}"
            )
        elif why in ("expired", "revoked", "taken"):
            st.stop.set()

    def _ingest_rounds(self, course: str, url: str, key: str, rec: dict) -> dict | None:
        """把 `state.rounds` 里**没入过账的 rev** 拉下来入账；返回更新后的 assignment。

        冲突（同轮不同字节 / 体自称的 run_id 不符）⇒ `conflict`：取消 worker 的该课、release、
        丢账本，**不推进 cursor**（评审 §6.8 内容冲突那一行）。
        """
        state = self._client.get_state(url, key)
        if state is None:
            return rec
        rounds = state.get("rounds") if isinstance(state.get("rounds"), dict) else {}
        done_revs = {int(k): int(v) for k, v in (rec.get("revs") or {}).items()}
        cursor = int(rec.get("cursor_it") or 0)
        pulled = 0
        for key_it in sorted(rounds, key=lambda k: int(k)):
            if pulled >= OFFLINE_PUSH_ROUNDS_PER_PASS:
                break
            it = int(key_it)
            try:
                rev = int((rounds[key_it] or {}).get("rev") or 0)
            except (TypeError, ValueError):
                continue
            if it <= cursor and done_revs.get(it) == rev:
                continue
            dest = Path(tempfile.gettempdir()) / f"offline-push-round-{course}-{it}.json"
            out = self._client.download_round(url, key, it, dest)
            if out is None or out[0] != 200:
                continue
            try:
                body = json.loads(dest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self._log(f"{course}: it{it} 的 body 读不动（{e}）——下一拍重试")
                continue
            finally:
                try:
                    dest.unlink()
                except OSError:
                    pass
            if not isinstance(body, dict):
                self._log(f"{course}: it{it} 的 body 不是对象——跳过")
                continue
            status, res = self._ingest_artifact(course, body, rec)
            if status == 409:
                self._client.cancel_task(url, key)
                self.assignments.mark(course, "conflict", conflict=str(res.get("error") or "conflict"))
                self.conflicts += 1
                self._release(course, str(rec.get("lease_token") or ""))
                self.assignments.drop(course)
                self._log(f"{course}: it{it} 入账冲突 —— 取消这一单，等人裁决")
                return None
            if status != 200:
                self._log(f"{course}: it{it} 入账被拒（HTTP {status}）——下一拍重试")
                continue
            done_revs[it] = rev
            cursor = max(cursor, it)
            pulled += 1
            rec = self.assignments.set_fields(course, cursor_it=cursor, revs={str(k): v for k, v in done_revs.items()}) or rec
        if pulled:
            self._ack(course, url, key, rec, result=False)
        return self.assignments.get(course) or rec

    def _ingest_artifact(self, course: str, body: dict, rec: dict) -> tuple[int, dict]:
        try:
            return ingest_offline_artifact(
                self.hub,
                course,
                body,
                run_id=str(rec.get("run_id") or ""),
                lease_token=str(rec.get("lease_token") or ""),
                source="push",
                log=self._log,
            )
        except AuthorityConflictError as e:
            return 409, {"error": str(e)}
        except (ProtocolError, ValueError) as e:
            return 400, {"error": str(e)}

    def _ack(self, course: str, url: str, key: str, rec: dict, *, result: bool) -> None:
        payload = {
            "task_id": str(rec.get("task_id") or ""),
            "result": bool(result),
            "upto_it": int(rec.get("cursor_it") or 0),
            "revs": {str(k): int(v) for k, v in (rec.get("revs") or {}).items()},
        }
        out = self._client.post_ack(url, key, payload)
        if out is None:
            self._log(f"{course}: ack 没答（worker 不可达）——下一拍重试（幂等）")
            return
        if out[0] != 200:
            self._log(f"{course}: ack 返回 HTTP {out[0]}（{out[1].get('error') or '-'}）")

    def _finish_if_ready(
        self, course: str, url: str, key: str, rec: dict, state: dict
    ) -> bool:
        """有段末 result ⇒ 拉回来入账（盖章 / 滚下一段）→ release → ack(result) → 收摊。"""
        if not bool(state.get("final_ready")):
            return False
        dest = Path(tempfile.gettempdir()) / f"offline-push-result-{course}.json"
        out = self._client.download_result(url, key, dest)
        if out is None or out[0] != 200:
            if out is not None and out[0] == 202:
                return False  # 还没收尾（state 与文件不同步的一瞬）
            self._log(f"{course}: 结果体拉不下来（{out if out is None else out[0]}）——下一拍重试")
            return False
        try:
            body = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self._log(f"{course}: 结果体读不动（{e}）——下一拍重试")
            return False
        finally:
            try:
                dest.unlink()
            except OSError:
                pass
        if not isinstance(body, dict):
            self._log(f"{course}: 结果体不是对象——下一拍重试")
            return False
        status, _res = self._ingest_result(course, body, rec)
        if status == 409:
            self.assignments.mark(course, "conflict", conflict="result authority mismatch")
            self.conflicts += 1
            self._release(course, str(rec.get("lease_token") or ""))
            self.assignments.drop(course)
            return True
        if status != 200:
            self._log(f"{course}: 结果入账被拒（HTTP {status}）——下一拍重试")
            return False
        self._release(course, str(rec.get("lease_token") or ""))
        self.assignments.set_fields(course, phase="done")
        self._ack(course, url, key, rec, result=True)
        self.assignments.drop(course)
        self._log(
            f"{course}: 段末已入账（run={rec.get('run_id') or '-'}，"
            f"state={body.get('state') or '-'}）——release + ack，任务结束"
        )
        return True

    def _ingest_result(self, course: str, body: dict, rec: dict) -> tuple[int, dict]:
        try:
            return ingest_offline_result(
                self.hub,
                course,
                body,
                run_id=str(rec.get("run_id") or ""),
                source="push",
                log=self._log,
            )
        except AuthorityConflictError as e:
            return 409, {"error": str(e)}
        except (ProtocolError, ValueError) as e:
            return 400, {"error": str(e)}

    def _settle_terminal(self, course: str, url: str, key: str, rec: dict, state: dict) -> None:
        """worker 报 failed/cancelled/done 且没有 result 体 ⇒ 收完轮次后 release + 收摊。"""
        phase = str(state.get("phase") or "")
        if phase not in ("failed", "cancelled", "done"):
            return
        if bool(state.get("final_ready")):
            return  # 下一拍 _finish_if_ready 会处理
        tail = str(state.get("error") or "").strip().splitlines()
        self._log(
            f"{course}: worker 报 {phase}（rc={state.get('rc')}）"
            + (f"，run.log 尾：{tail[-1][:160]}" if tail else "")
            + "——已收的轮已入账，release"
        )
        self._release(course, str(rec.get("lease_token") or ""))
        self._ack(course, url, key, rec, result=True)
        self.assignments.mark(course, "failed" if phase == "failed" else "done")
        self.assignments.drop(course)
        if phase == "failed":
            self.failed += 1

    # ------------------------------------------------------------ 观测
    def state(self) -> dict:
        return {
            "capability": CAP_OFFLINE_PUSH,
            "ticks": self.ticks,
            "dispatched": self.dispatched,
            "failed": self.failed,
            "conflicts": self.conflicts,
            "assignments": sorted(self.assignments.snapshot().values(), key=lambda r: str(r.get("course"))),
            "workers": self.workers.state(),
        }


__all__ = [
    "OFFLINE_PUSH_COOLDOWN_SEC",
    "OFFLINE_PUSH_HEARTBEAT_SEC",
    "OFFLINE_PUSH_POLL_SEC",
    "OFFLINE_PUSH_PUMP_SEC",
    "OfflinePushDispatcher",
    "OfflinePushWorkers",
]
