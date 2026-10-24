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

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.logutil import tag_logger
from common.progress_text import progress_text
from common.protocol import (
    CAP_OFFLINE_PUSH,
    OFFLINE_PUSH_PACK_DIR,
    OFFLINE_PUSH_PROTO,
    OFFLINE_PUSH_RESUME_DIR,
    OFFLINE_PUSH_TASK_NAME,
    PUSH_DEAD_MISSES,
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
from hub.offline_pack_fresh import pack_still_fresh
from hub.offline_push_assign import OfflinePushAssignments
from hub.offline_push_workers import OfflinePushWorkers
from hub.task_pack import pack_index_meta
from remote import offline_push_client as client
from remote.push_dispatch import run_tick_loop

#: 调度拍（秒）：与 GPU 派发器同一量级；hub 的派发延迟 = 一拍 + 一次 zip 组装。
OFFLINE_PUSH_POLL_SEC = 10.0
#: pump 轮询节拍（秒）：worker 的 outbox 写入是几秒级的，比它快只是白花请求。
OFFLINE_PUSH_PUMP_SEC = 5.0
#: 心跳间隔（秒）：**只续 TTL**；活性仍只认 `progress_offline`（心跳不作活性）。
OFFLINE_PUSH_HEARTBEAT_SEC = 60.0
#: 派发失败后的冷却（秒）：同一门课/同一台机器不在每一拍上重撞。
OFFLINE_PUSH_COOLDOWN_SEC = 120.0
#: 「已请控制台导包」之后的**兜底**等待（秒）：导包通常是几十秒，拿失败那把 120s 的尺子
#: 等量就是 2026-10-24 真机那 2 分半（注册成功 → 受理）。真正挡重触发的是 `pending_export`
#: （在窗内就不重触发），这一个只是「触发没递出去」时的退路。
PACK_EXPORT_WAIT_SEC = 20.0
#: 一次 pump 循环里最多拉几轮（防「积压 200 轮」把一拍拖成十分钟）。
OFFLINE_PUSH_ROUNDS_PER_PASS = 8
#: 任务包 zip 的临时前缀（`<tmp>/offline-push-*`）；上传完成后整目录删掉。
_TMP_PREFIX = "offline-push-"
#: pump 连续读不到 `/state` 这么多拍 ⇒ 本拍停止拉取/心跳（**不 release**：等 hold stale）。
#: 基数是 ping 的 `PUSH_DEAD_MISSES=3`（30s 一拍），pump 是 5s 一拍 —— 直接复用只有 15s，
#: 隧道抖一下就被判死；×4 ≈ 一分钟，仍远小于 hold stale（900s）。
OFFLINE_PUSH_DEAD_MISSES = PUSH_DEAD_MISSES * 4
#: 失败派发后的冷却已在 `cooldown_sec`；这里只给「临时 zip 目录」的清扫龄期：
#: 正常路径 `_pump` 的 finally 会删，只有进程被杀才会遗留（计划 §6.3「失败/启动时清扫」）。
TMP_SWEEP_AGE_SEC = 3600.0
#: 临时目录清扫的节流（秒）：每次调度拍都 rglob 一遍是白 IO。
TMP_SWEEP_EVERY_SEC = 600.0
#: `settling` 的重试间隔（秒）：ack 没成时**不丢单**，但也不该每拍都撞一次。
SETTLE_RETRY_SEC = 60.0

_log_default = tag_logger("hub-offline-push", lambda: time)


@dataclass
class _PumpState:
    """一条 pump 线程的运行期读数（不进 assignment 的字段放这里）。"""

    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    misses: int = 0
    last_seq: int = -1


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
        self._cooldown: dict[str, tuple[float, float]] = {}
        self._tmp_swept_at = 0.0
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
        self._sweep_tmp(now)
        resumed = self._resume_pumps(now)
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

    def _sweep_tmp(self, now: float) -> None:
        """扫掉进程被杀遗留的任务包临时目录（计划 §6.3「启动/失败时清扫」）。

        节流到 `TMP_SWEEP_EVERY_SEC`：每拍把系统临时目录 rglob 一遍是白 IO。正常路径由
        `_pump` 的 finally 删，只有 SIGKILL / 断电才会留下（几百 MB 一份，不管会吃满盘）。
        """
        if now - self._tmp_swept_at < TMP_SWEEP_EVERY_SEC:
            return
        self._tmp_swept_at = now
        root = Path(tempfile.gettempdir())
        try:
            entries = list(root.glob(f"{_TMP_PREFIX}*"))
        except OSError:
            return
        for p in entries:
            try:
                if now - p.stat().st_mtime < TMP_SWEEP_AGE_SEC:
                    continue
                shutil.rmtree(p, ignore_errors=True)
            except OSError:
                continue

    # ------------------------------------------------------------ 续跑（hub 重启后）
    def _resume_pumps(self, now: float) -> list[str]:
        """盘上有在途 assignment 且没有活线程 ⇒ 重新 claim（`mine`）后接着拉。

        ★ 不重新上传任务包：worker 那边可能已经在跑了（`tasks/<id>/outbox` 里有轮次）。
        重派会换 `task_id`，旧 outbox 就永远没人 ack。这里只做「换 token + 继续拉」——
        hub 重启换 token 不影响旧 worker 的产物（评审 §6.8 的 hub 重启那一行）。

        `settling`（收摊重试，评审 F3）**不 claim**：它只剩 ack/release/drop 三件本地/幂等
        动作，worker 的 ack 只认 task_id；抢租约反而可能把下一任持有人的单顶掉。
        """
        resumed: list[str] = []
        for course, rec in self.assignments.snapshot().items():
            phase = str(rec.get("phase") or "")
            if phase not in ("dispatching", "running", "settling"):
                continue
            with self._lock:
                live = course in self._pumps and self._pumps[course].thread is not None
            if live:
                continue
            if phase == "settling":
                if now - float(rec.get("settle_at") or 0.0) < SETTLE_RETRY_SEC:
                    continue
                wid = str(rec.get("worker_id") or "")
                url, key = self._endpoint_of(wid)
                if not url:
                    self._log(f"收摊重试 {course}：worker（{wid}）已不在登记表 —— 丢账本（worker 自会留档）")
                    self.assignments.drop(course)
                    continue
                self._start_pump(course, rec, url, key)
                resumed.append(course)
                continue
            wid = str(rec.get("worker_id") or "")
            url, key = self._endpoint_of(wid)
            if not url:
                self._log(f"在途单 {course} 的 worker（{wid}）已不在登记表 —— 释放租约，等重派")
                self._release(course, str(rec.get("lease_token") or ""))
                self.assignments.drop(course)
                continue
            if phase == "dispatching":
                # 提议阶段（云机还没应答 / hub 崩在受理与接管之间）⇒ **不 claim**：让 pump
                # 重投同一单（确定性 task_id ⇒ 要么首次受理、要么 duplicate），受理后再接管。
                self._start_pump(course, rec, url, key)
                resumed.append(course)
                self._log(f"提议续投 {course}（worker={wid}，等云机受理）")
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
            if self._cooling(wid, now):
                continue  # 这台机器刚被拒（413 容量 / 派发失败）：冷却窗内不重撞
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
                # ★ 2026-10-24（用户口径）：**包就绪 ≠ 接管**。这里只「提议」——录一笔
                #   `dispatching` 的 assignment（**不 claim、不写 hold**），把包推给云机；
                #   云机应答受理（202）之后才由 pump `claim_offline`，那才叫接管。
                #   旧顺序是先 claim 再上传：上传失败/云机拒收时 hold 已经挂在身上，
                #   控制台上显示「已被接管」而云机连包都没收到。
                meta = self._pack_meta(pack)
                pack_it = int(meta.get("it") or 0)
                anchor = self._anchor(course, pack_it)
                resume_it = int(anchor.get("it") or 0) if anchor else pack_it
                rec = {
                    "course": course,
                    "worker_id": push_worker_id_of(wid),
                    "task_id": "",
                    "pack_sha256": str((row.get("pack") or {}).get("sha256") or ""),
                    "pack_name": pack.name,
                    "pack_path": str(pack),
                    "run_id": str(meta.get("run_id") or ""),
                    "lease_token": "",
                    #: hub 这边已经有的最高一轮（= 任务包起点 / 续跑锚点）：worker 的 outbox
                    #: 以它为地板，不再物化这些轮（评审 F7）。
                    "resume_it": int(resume_it),
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
                    f"提议 {course} -> {push_worker_id_of(wid)}"
                    f"（pack={pack.name} run={rec['run_id'] or '-'}）——等云机受理后才接管"
                )
                break  # 一台机器一次只接一单（训练完一门才提议下一门）
        if started:
            self._wake.set()
        return started

    def _cooling(self, key: str, now: float) -> bool:
        at, sec = self._cooldown.get(key, (0.0, 0.0))
        return at > 0.0 and now - at < (sec or self.cooldown_sec)

    def _cool(self, key: str, now: float | None = None, *, sec: float = 0.0) -> None:
        """冷却（可选**自定义窗**：导包等待不该套用「失败」那把 120s 的尺子）。"""
        self._cooldown[key] = (self._now() if now is None else float(now), float(sec or 0.0))

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
            # ★ 2026-10-24 真机：注册成功 → 受理等了 **2 分 30 秒**。原因是触发导包后被
            #   `cooldown_sec`(120s) 挡住 —— 那是给「失败」用的窗，不该压在「导包在途」上。
            #   导包在途（`pending_export` 在窗内）⇒ **不重触发、也不冷却**：包一落地，下一拍
            #   就走上面的 `pack.is_file()` 分支立刻派单。
            if row.get("pending_export"):
                return None
            if self._cooling(course, now):
                return None
            verdict, why = self.hub.begin_pending_export(course, push_worker_id_of(wid))
            if verdict != "pending":
                self._log(f"{course}: 缺包且无法记 pending_export（{verdict}: {why}）")
                self._cool(course, now)
                return None
            out = trigger_pack_export(self.hub, course)
            # 短窗：只为「这次触发没递出去」兜底（递出去了上面那条 pending_export 就接住了）。
            self._cool(course, now, sec=PACK_EXPORT_WAIT_SEC)
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
    def build_task_zip(
        self, course: str, pack: Path, run_id: str = "", *, resume_it: int = 0
    ) -> tuple[Path, dict]:
        """组外层 zip（`task.json` + `pack/` + 可选 `resume/`）→ `(临时路径, 元信息)`。

        `pack` 本身是 zip ⇒ `ZIP_STORED` 直写（再压一遍只是白烧 CPU）；`resume/` 取
        `hub.resume_anchor()` 里**比包更新**的那一轮（锚点只发整份：delta 只活在回传腿与
        hub 落地器之间，云机拿回去也解不开），并**补上 `resume.json`**（评审 F2）——
        `run_loop.apply_resume_overlay` 的入口判据就是它，少了它锚点文件全在也不会被采纳。

        `task_id` 是**确定性**的（计划 §6.3：`sha256(course, pack_sha, resume_it)[:16]`）：
        hub 在「上传成功、还没记账」之间崩掉后重派，worker 看到的是同一单 ⇒ 幂等 duplicate，
        而不是把上一单的 outbox 变成孤儿（评审 F4/F9）。
        """
        meta = self._pack_meta(pack)
        pack_it = int(meta.get("it") or 0)
        floor = int(resume_it) if int(resume_it or 0) > pack_it else pack_it
        pack_sha = self._sha256(pack)
        task_id = self._task_id(course, pack_sha, floor)
        tmp = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX))
        out = tmp / f"task-{task_id}.zip"
        anchor = self._anchor(course, pack_it) if floor > pack_it else None
        if anchor is not None and int(anchor.get("it") or 0) != floor:
            # 派发与组包之间锚点前进了：这一单按派发时敲定的地板走（task_id 才会稳定）；
            # 那些新轮 worker 没有也不吃亏 —— 极少数情形下它会从包起点重跑，outbox 地板
            # 仍会把它们挡住（不重发已知轮）。
            self._log(
                f"{course}: 续跑锚点在派发后变了（it{floor} → it{anchor.get('it')}）"
                "——本单仍按派发时的地板走，锚点不带"
            )
            anchor = None
        doc = {
            "proto": OFFLINE_PUSH_PROTO,
            "task_id": task_id,
            "course": course,
            "pack_name": pack.name,
            "pack_sha256": pack_sha,
            "run_id": str(run_id or meta.get("run_id") or ""),
            "it": pack_it,
            "end_it": int(meta.get("end_it") or 0),
            #: ★ 派发时 hub 已持有的最高一轮（F7）：worker 的 `--floor-it` 就是它。
            "resume_it": int(floor),
            "created_at": float(self._now()),
        }
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(OFFLINE_PUSH_TASK_NAME, json.dumps(doc, ensure_ascii=False))
            with open(pack, "rb") as src, zf.open(f"{OFFLINE_PUSH_PACK_DIR}/{pack.name}", "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if anchor is not None:
                self._write_resume(zf, anchor)
        return out, {
            "task_id": task_id,
            "resume_it": int(floor),
            "pack_sha256": pack_sha,
            "run_id": doc["run_id"],
            "it": pack_it,
            "end_it": int(meta.get("end_it") or 0),
        }

    def _write_resume(self, zf: zipfile.ZipFile, anchor: dict) -> int:
        """把锚点铺进 `resume/`（`resume.json` + `it-NNN/{weights,opt,row}`）；返回件数。"""
        it = int(anchor.get("it") or 0)
        it_dir = Path(str(anchor.get("_dir") or ""))
        raw_files = anchor.get("files")
        files: dict = raw_files if isinstance(raw_files, dict) else {}
        names = [str(n) for n in files.values() if str(n)] or ["weights.json", "opt.tar", "row.json"]
        wrote = 0
        for name in names:
            part = it_dir / name
            if not part.is_file():
                continue
            with open(part, "rb") as s2, zf.open(
                f"{OFFLINE_PUSH_RESUME_DIR}/it-{it:03d}/{name}", "w"
            ) as d2:
                shutil.copyfileobj(s2, d2, 1 << 20)
            wrote += 1
        if not wrote:
            self._log(f"续跑锚点 it{it} 的件一个都读不到（{it_dir}）——任务包不带锚点")
            return 0
        meta = {k: v for k, v in anchor.items() if not str(k).startswith("_")}
        meta["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        zf.writestr(
            f"{OFFLINE_PUSH_RESUME_DIR}/resume.json",
            json.dumps(meta, ensure_ascii=False, indent=1),
        )
        return wrote

    def _anchor(self, course: str, pack_it: int) -> dict | None:
        """比包更新的续跑锚点（**整份**）；没有/不比包新 ⇒ None。"""
        try:
            anchor = self.hub.resume_anchor(course)
        except Exception as e:
            self._log(f"{course}: 取续跑锚点失败（{type(e).__name__}: {e}）——本单从包起点跑")
            return None
        if not anchor or int(anchor.get("it") or 0) <= pack_it:
            return None
        return dict(anchor)

    @staticmethod
    def _task_id(course: str, pack_sha: str, floor_it: int) -> str:
        """`sha256(course, pack_sha, floor_it)[:16]`（计划 §6.3；形状闸要 16 位小写 hex）。"""
        raw = f"{course}\x00{pack_sha}\x00{int(floor_it)}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @staticmethod
    def _sha256(path: Path) -> str:
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
        """一条 assignment 的整条腿（提议 → **云机受理** → 接管 → 逐轮拉取 → ack → 收尾）。"""
        tmp_zip: Path | None = None
        try:
            rec = self.assignments.get(course) or rec
            if str(rec.get("phase")) == "settling":
                self._settle_retry(course, url, key, rec)
                return
            if not self._worker_registered(rec):
                # 需求 5：节点被停用/移除 ⇒ 连包都不该再推（这条提议从没成立过，不占用课程）。
                self._log(f"{course}: 云机节点已停用/移除 —— 放弃这次提议（未接管）")
                self._drop(course, rec, reason="worker-disabled")
                return
            if not rec.get("task_id"):
                tmp_zip, meta = self.build_task_zip(
                    course,
                    Path(str(rec["pack_path"])),
                    str(rec.get("run_id") or ""),
                    resume_it=int(rec.get("resume_it") or 0),
                )
                status, body = self._upload(url, key, tmp_zip, course)
                adopted = status != 202 and self._adopt_existing(course, url, key, meta)
                if status != 202 and not adopted:
                    self._log(
                        f"{course}: 任务派发失败（HTTP {status}：{body.get('error') or body.get('status') or '-'}）"
                        "——释放租约，稍后换机/换课"
                    )
                    self._drop(course, rec, reason="dispatch-failed")
                    return
                if status == 202:
                    got = str(body.get("task_id") or "")
                    if got and got != str(meta["task_id"]):
                        self._log(
                            f"{course}: worker 受理的 task_id（{got}）与派发的（{meta['task_id']}）不符"
                            "——收摊（不 ack 别人的单）"
                        )
                        self._drop(course, rec, reason="task-id-mismatch")
                        return
                    # 受理 ≠ 接管：先把 task_id 记上（cancel/ack 要带它），lease 仍然空着。
                    rec = self.assignments.set_fields(
                        course,
                        task_id=str(meta["task_id"]),
                        resume_it=int(meta["resume_it"]),
                        pack_sha256=str(meta["pack_sha256"]),
                    ) or rec
                    self._log(
                        f"{course}: 云机已受理（task={meta['task_id']}，地板 it{meta['resume_it']}）"
                        "——现在才接管"
                    )
                else:
                    rec = self.assignments.get(course) or rec
                    self._log(f"{course}: worker 已在跑同一单（task={meta['task_id']}）——接管，不重传")
            # ★ 接管（hold）**只**发生在这里：云机已受理（刚受理 / 早就受理 / 重启后捡回来）。
            #   顺序是硬要求（用户口径 2026-10-24）：包就绪不接管，云机应答才接管。
            if not rec.get("lease_token"):
                # 需求 4（同日）：**导包结束 → 云机接管**这段里本机可能又跑完一轮。接管前拿
                # 当下的清单复核一次：包过期 / 被重导过 ⇒ 不接管，先按最新进度重导，下一拍再提议。
                fresh, stale_why = self._still_fresh(course, rec)
                if not fresh:
                    self._log(
                        f"{course}: 云机已受理，但包在提议之后**过期**了（{stale_why}）"
                        "——不接管：撤单并按最新进度重新导包，等下一拍用新包再提议"
                    )
                    out = trigger_pack_export(self.hub, course)
                    self._log(f"{course}: 已请控制台重新导包（{out['state']}）：{out['note']}")
                    self._settle_assignment(
                        course, url, key, rec, cancel=True, free_worker=False, reason="pack-stale"
                    )
                    self._cool(course)
                    return
                lease, why = self._claim(course, rec)
                if why:
                    self._log(
                        f"{course}: 云机已受理、但这门课本拍接管不到（{why}）——撤单"
                        "（云机在轮边界停；本机从不曾持有它，不 release 别人的租约）"
                    )
                    # `free_worker=False`：**不**替云机删 outbox —— 它的子进程可能已经在跑，
                    # result=True 的 ack 会在它脚下把 outbox 删掉。cancel 让它自己停在轮边界。
                    self._settle_assignment(
                        course, url, key, rec, cancel=True, free_worker=False, reason="claim-lost"
                    )
                    return
                rec = self.assignments.set_fields(
                    course,
                    lease_token=str(lease.get("token") or ""),
                    phase="running",
                ) or rec
                self.workers.note_fields(self._bare(str(rec.get("worker_id") or "")), busy=True)
                self._log(f"{course}: 接管成立（云机已受理；本机开始逐轮拉取）")
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

    def _worker_registered(self, rec: dict) -> bool:
        """这台云机还在登记表里吗（控制台「停用」= 回写 `enabled:false` ⇒ 热重载后消失）。

        **只看「在不在表里」，不看 `online`**：隧道抖一下（`online=False`）不该释放课程 ——
        那正是需求 3「hub 断线训练照跑、回来补传」要保住的情形。停用/移除才会让它从
        `snapshot()` 里彻底消失。
        """
        bare = self._bare(str(rec.get("worker_id") or ""))
        if not bare:
            return True
        return any(str(w.get("id")) == bare for w in self.workers.snapshot())

    def _claim(self, course: str, rec: dict) -> tuple[dict, str]:
        """接管一门课 ⇒ `(lease, "")` / `({}, 拒因)`。**只在云机受理之后调用**。

        `claim_offline` 自己会把「同一 worker 续领 / 过期 / 别人 stale」都判成可领，
        所以这里不做额外判断；异常一律折成拒因（pump 线程不许死）。
        """
        wid = str(rec.get("worker_id") or "")
        try:
            lease, why = self.hub.claim_offline(course, wid)
            return dict(lease or {}), str(why or "")
        except Exception as e:
            return {}, f"claim 异常 {type(e).__name__}: {e}"

    def _still_fresh(self, course: str, rec: dict) -> tuple[bool, str]:
        """接管前复核：提议时那份包**现在**还是不是最新的（判据见 `hub.offline_pack_fresh`）。"""
        try:
            rows = self.hub.offline_tasks(worker=str(rec.get("worker_id") or ""))
        except Exception:
            return True, ""  # 清单读不到 ⇒ 不编：接管仍可能成功，claim 会给出真拒因
        return pack_still_fresh(rows, {**rec, "course": course})

    @staticmethod
    def _bare(worker_id: str) -> str:
        wid = str(worker_id or "")
        return wid[5:] if wid.startswith("push:") else wid

    def _adopt_existing(self, course: str, url: str, key: str, meta: dict) -> bool:
        """上传没拿到 202（超时 / 409 busy）时问一句：worker 是不是**已经在跑我们这一单**？

        确定性 `task_id` 让这件事可判（评审 F4/F9）：hub 在「worker 已受理、hub 还没记账」
        之间崩掉/断线，重派会撞 409 或超时 —— 那不是失败，是同一单的第二次握手。
        """
        state = self._client.get_state(url, key)
        task = state.get("task") if isinstance(state, dict) else None
        if not isinstance(task, dict):
            return False
        tid = str(task.get("task_id") or "")
        if not (
            tid == str(meta.get("task_id") or "")
            and str(task.get("course") or "") == course
            and str(task.get("pack_sha256") or "") == str(meta.get("pack_sha256") or "")
        ):
            return False
        phase = str(state.get("phase") or "")
        if phase not in ("running", "cancelling") and bool(state.get("ack")):
            # 同 id 的上一单已经完整跑完并被 ack：worker 只是还记得它，重投只会 duplicate
            # （不会重跑）⇒ 不可接管，照常收摊等下一拍。
            self._log(f"{course}: worker 上的同 id 任务已收尾（task={tid}）——不接管")
            return False
        self.assignments.set_fields(
            course,
            task_id=tid,
            resume_it=int(meta.get("resume_it") or 0),
            pack_sha256=str(meta.get("pack_sha256") or ""),
            # phase/lease 不动：接管由 `_pump` 在「云机已受理」之后统一做（用户口径 2026-10-24）。
        )
        return True

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
        # 失败过的课/机器冷却一窗：否则下一拍会立刻重撞同一个坑（413 容量 / 包坏了）。
        self._cool(course)
        wid = self._bare(str(rec.get("worker_id") or ""))
        if wid:
            self._cool(wid)
        if reason:
            self._log(f"{course}: 收摊（{reason}）")

    def _release(self, course: str, lease: str) -> None:
        if not str(lease or ""):
            # 提议还没走到接管 ⇒ 我们**从来没持有过**这门课：一次网络调用都不发。
            # （`release_offline` 对「无租约」会顺手清 hold 镜像 —— 那在提议失败时是别人的东西。）
            return
        try:
            self.hub.release_offline(course, lease)
        except Exception as e:
            self._log(f"{course}: release 失败（{type(e).__name__}: {e}）——等 TTL")

    def _pump_loop(self, course: str, url: str, key: str, st: _PumpState) -> None:
        last_beat = 0.0
        settle_on_exit = True
        while not self._stop.is_set() and not st.stop.is_set():
            rec = self.assignments.get(course)
            if rec is None:
                return
            if str(rec.get("phase")) == "settling":
                if self._now() - float(rec.get("settle_at") or 0.0) < SETTLE_RETRY_SEC:
                    return  # 还没到重试窗口：让出线程，到点后 `_resume_pumps` 再起
                self._settle_retry(course, url, key, rec)
                return
            if not self._worker_registered(rec):
                # 需求 5（用户口径 2026-10-24）：控制台停用这台「自主云机」⇒ hub **立刻停止**
                # 向它请求结果，并把抢占的课程释放回池子（`release_offline` 清 hold ⇒ 别人能领）。
                # **不 cancel、不 ack(result)**：云机的子进程可能还在跑，它的 outbox 是证据，
                # 别替它的下一任主人删。产物没入账的轮次留在那台机器上（按 task_id 分目录）。
                self._log(
                    f"{course}: 云机节点（{rec.get('worker_id')}）已被停用/移除 —— "
                    "停止拉取结果并释放这门课（不 cancel；云机上的 outbox 留作证据）"
                )
                self._settle_assignment(
                    course, url, key, rec, cancel=False, free_worker=False, reason="worker-disabled"
                )
                return
            if not self._still_mine(course, rec):
                self._log(
                    f"{course}: 租约已不属于我们（换主/停课）——收下已入账的轮后退出，不 cancel"
                )
                self._settle_assignment(course, url, key, rec, cancel=False, free_worker=False, reason="lease-lost")
                return
            state = self._client.get_state(url, key)
            if state is None:
                st.misses += 1
                if st.misses >= OFFLINE_PUSH_DEAD_MISSES:
                    # 不 release：租约到期由 hold 的 TTL 管，接管由 900s 无进度触发。
                    self._log(
                        f"{course}: 连续 {st.misses} 拍读不到 /state —— 本拍起停止拉取/心跳"
                        "（不 release；等 hold stale 判死，或隧道恢复后下一拍续跑）"
                    )
                    settle_on_exit = False
                    break
                time.sleep(self.pump_sec)
                continue
            st.misses = 0
            proto = str(state.get("proto") or "")
            if proto and proto != OFFLINE_PUSH_PROTO:
                self._log(
                    f"{course}: worker 协议不符（{proto!r} ≠ {OFFLINE_PUSH_PROTO}）"
                    "——取消这一单并等它升级"
                )
                self._settle_assignment(course, url, key, rec, cancel=True, free_worker=True, reason="proto-mismatch")
                return
            rec, backlog = self._ingest_rounds(course, url, key, rec, state)
            if rec is None:
                return  # 冲突：已在 _ingest_rounds 里收摊
            now = self._now()
            if now - last_beat >= self.heartbeat_sec:
                last_beat = now
                _ttl, why = self.hub.heartbeat_offline(course, str(rec.get("lease_token") or ""))
                if why in ("expired", "revoked", "taken"):
                    self._log(f"{course}: 心跳被拒（{why}）——hub 侧已换主，停止拉取")
                    self._settle_assignment(course, url, key, rec, cancel=False, free_worker=False, reason="lease-lost")
                    return
                self._note_progress(course, url, key, rec, st)
            # ★ 收尾必须等积压拉完（评审 F1）：final 一 ack，worker 就删 outbox —— 还没拉的
            # 轮次会**永久丢**。顺序也重要：先拉轮、再判 final、最后才看 terminal。
            if not backlog:
                if self._finish_if_ready(course, url, key, rec, state):
                    return
                self._settle_terminal(course, url, key, rec, state)
            time.sleep(self.pump_sec)
        if settle_on_exit:
            rec = self.assignments.get(course)
            if rec is not None and str(rec.get("phase")) in ("dispatching", "running"):
                # hub 收线（进程退出/`stop()`）：把已入账的 rev 收下（腾 worker 的盘），
                # **不 release、不丢账本、不 cancel** —— 重启后 `_resume_pumps` 要接着拉同一单；
                # 先释放租约会让别人再派一遍（worker 还在跑），丢账本会让 outbox 变孤儿。
                self._log(f"{course}: pump 收线 —— 收下已入账的轮（不 release；重启后续跑）")
                status = self._ack(course, url, key, rec, result=False)
                if status not in (200, 404, 409):
                    self._log(f"{course}: 收线时 ack 没成（{status}）——已入账的轮重启后再收")

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
                f"{course}: "
                + progress_text(
                    seq=seq,
                    kind=str(prog.get("kind") or ""),
                    it=int(prog.get("it", 0) or 0),
                    done=int(prog.get("done", 0) or 0),
                    total=int(prog.get("total", 0) or 0),
                )
            )
        elif why in ("expired", "revoked", "taken"):
            st.stop.set()

    def _ingest_rounds(
        self, course: str, url: str, key: str, rec: dict, state: dict
    ) -> tuple[dict | None, bool]:
        """把 `state.rounds` 里**没入过账的 rev** 拉下来入账；返回 `(assignment, 有积压)`。

        **有积压** = 还有轮次没入账（下载失败 / 这一拍只拉了前 N 轮 / metrics 没到手）：
        收尾（final / terminal）必须等它变 False（评审 F1）。冲突 ⇒ `(None, True)` 且已收摊。

        每轮两件：权重体（`oversize` 的轮没有）+ 统计体（评审 F5：`oversize` 轮的唯一读数面）。
        两件都到手（或统计体 404 = 这轮本来没有）才算 `done_revs[it]=rev` —— 只记权重不记
        统计，会得到一个「hub 永远重拉、worker 永远删不掉」的死循环。
        """
        raw_rounds = state.get("rounds")
        rounds: dict = raw_rounds if isinstance(raw_rounds, dict) else {}
        done_revs = {int(k): int(v) for k, v in (rec.get("revs") or {}).items()}
        cursor = int(rec.get("cursor_it") or 0)
        pulled = 0
        backlog = False
        for key_it in sorted(rounds, key=lambda k: int(k)):
            it = int(key_it)
            ent = rounds[key_it] if isinstance(rounds[key_it], dict) else {}
            try:
                rev = int(ent.get("rev") or 0)
            except (TypeError, ValueError):
                continue
            if it <= cursor and done_revs.get(it) == rev:
                continue
            if pulled >= OFFLINE_PUSH_ROUNDS_PER_PASS:
                backlog = True
                break
            # `file` 为空 = 这一轮没有权重体（oversize），但它仍有统计体可拉。
            has_body = bool(str(ent.get("file") or "")) and not bool(ent.get("oversize"))
            if has_body:
                status, body = self._fetch_round(course, url, key, it)
                if status is None:
                    backlog = True
                    continue
                if status == 409:
                    self._conflict(course, url, key, rec, f"it{it} 入账冲突")
                    return None, True
                if status != 200 or body is None:
                    backlog = True
                    continue
                status, res = self._ingest_artifact(course, body, rec)
                if status == 409:
                    self._conflict(course, url, key, rec, f"it{it} 入账冲突（{res.get('error') or '-'}）")
                    return None, True
                if status != 200:
                    backlog = True
                    continue
            mstat = self._ingest_metrics(course, url, key, it, rec)
            if mstat is None or (mstat >= 500):
                backlog = True
                continue
            if mstat == 409:
                self._conflict(course, url, key, rec, f"it{it} 统计体权威冲突")
                return None, True
            done_revs[it] = rev
            cursor = max(cursor, it)
            pulled += 1
            rec = self.assignments.set_fields(
                course, cursor_it=cursor, revs={str(k): v for k, v in done_revs.items()}
            ) or rec
        if pulled or rec.get("revs"):
            # 每拍都 ack 一次（不只在新轮到手时）：上一次 ack 可能断在「hub 已入账、worker
            # 没收到」之间 —— 不重发就永远腾不出空间（幂等，代价是一个小 JSON）。
            self._ack(course, url, key, rec, result=False)
        return self.assignments.get(course) or rec, backlog

    def _fetch_round(
        self, course: str, url: str, key: str, it: int
    ) -> tuple[int | None, dict | None]:
        dest = self._tmp_path("round", course, it)
        out = self._client.download_round(url, key, it, dest)
        if out is None:
            return None, None
        status = int(out[0])
        if status != 200:
            return status, None
        try:
            body = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self._log(f"{course}: it{it} 的 body 读不动（{e}）——下一拍重试")
            return None, None
        finally:
            try:
                dest.unlink()
            except OSError:
                pass
        if not isinstance(body, dict):
            self._log(f"{course}: it{it} 的 body 不是对象——跳过")
            return 400, None
        return 200, body

    def _ingest_metrics(self, course: str, url: str, key: str, it: int, rec: dict) -> int | None:
        """这一轮的统计体 → 状态码（`404` = 本来没有；`None` = 网络失败，下一拍重试）。"""
        dest = self._tmp_path("metrics", course, it)
        out = self._client.download_metrics(url, key, it, dest)
        if out is None:
            return None
        status = int(out[0])
        if status == 404:
            return 404
        if status != 200:
            return status
        try:
            body = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self._log(f"{course}: it{it} 的统计体读不动（{e}）——下一拍重试")
            return None
        finally:
            try:
                dest.unlink()
            except OSError:
                pass
        if not isinstance(body, dict):
            self._log(f"{course}: it{it} 的统计体不是对象——跳过")
            return 400
        status, res = self._ingest_artifact(course, body, rec, parts="metrics")
        if status not in (200, 404):
            self._log(
                f"{course}: it{it} 的统计体被拒（HTTP {status}：{res.get('error') or '-'}）"
                "——权重体照收，这一轮仍算入账"
            )
        return status

    @staticmethod
    def _tmp_path(kind: str, course: str, it: int) -> Path:
        tag = hashlib.sha256(str(course).encode("utf-8")).hexdigest()[:10]
        return Path(tempfile.gettempdir()) / f"{_TMP_PREFIX}{kind}-{os.getpid()}-{tag}-{int(it)}.json"

    def _conflict(self, course: str, url: str, key: str, rec: dict, text: str) -> None:
        """权威/字节冲突：取消这一单 → ack（只带已入账 revs）→ release + 丢账本。"""
        self.conflicts += 1
        self._log(f"{course}: {text} —— 取消这一单，等人裁决")
        self._settle_assignment(
            course, url, key, rec, cancel=True, free_worker=True, reason="conflict", failed=True
        )

    def _ingest_artifact(
        self, course: str, body: dict, rec: dict, *, parts: str = ""
    ) -> tuple[int, dict]:
        try:
            return ingest_offline_artifact(
                self.hub,
                course,
                body,
                parts=parts,
                run_id=str(rec.get("run_id") or ""),
                lease_token=str(rec.get("lease_token") or ""),
                source="push",
                log=self._log,
            )
        except AuthorityConflictError as e:
            return 409, {"error": str(e)}
        except (ProtocolError, ValueError) as e:
            return 400, {"error": str(e)}

    def _ack(self, course: str, url: str, key: str, rec: dict, *, result: bool) -> int | None:
        """`POST /ack` → 状态码（`None` = 没答）。`result=True` 会让 worker 删 final 并腾出 busy。"""
        payload = {
            "task_id": str(rec.get("task_id") or ""),
            "result": bool(result),
            "upto_it": int(rec.get("cursor_it") or 0),
            "revs": {str(k): int(v) for k, v in (rec.get("revs") or {}).items()},
        }
        out = self._client.post_ack(url, key, payload)
        if out is None:
            self._log(f"{course}: ack 没答（worker 不可达）——稍后重试（幂等）")
            return None
        return int(out[0])

    def _close(self, course: str, rec: dict, *, reason: str, failed: bool = False) -> None:
        """release + 丢账本（收摊的最后一步；不含 ack/cancel —— 那两件可能失败）。"""
        self._release(course, str(rec.get("lease_token") or ""))
        self.assignments.drop(course)
        if failed:
            self.failed += 1
        if reason:
            self._log(f"{course}: 收摊完成（{reason}）")

    def _settle_assignment(
        self,
        course: str,
        url: str,
        key: str,
        rec: dict,
        *,
        cancel: bool,
        free_worker: bool,
        reason: str,
        failed: bool = False,
    ) -> bool:
        """收摊：可选 cancel → 按 rev ack → release → 丢账本；没答的那一步记成 `settling` 重试。

        两处「有答的失败」不重试：
        * cancel/ack **404/409** = worker 已经没有这一单（换单/重启丢了 outbox）——再发一万次
          也是同一个答案，留着只会把账本卡死；
        * 5xx / 连不上 = 没答，**不丢单**：记 `settling`（`settle_at` 起跳），由
          `_resume_pumps` 在 `SETTLE_RETRY_SEC` 后原样重放（cancel/ack 都幂等）。

        `free_worker` 决定 ack 带不带 `result`：只有它会让 worker 删 final、清 busy。租约丢了
        / hub 收线（下一任还要接着拉）时传 False —— 那单的 outbox 是证据，不能替别人删。
        """
        if cancel:
            out = self._client.cancel_task(url, key, task_id=str(rec.get("task_id") or ""))
            if out is None or int(out[0]) >= 500:
                return self._park_settling(
                    course, reason, cancel=True, free=free_worker, failed=failed
                )
            if int(out[0]) not in (200, 202):
                self._log(
                    f"{course}: cancel 返回 HTTP {out[0]}（{out[1].get('error') or '-'}）"
                    "——worker 已不在这一单上，不再重试"
                )
                self._close(course, rec, reason=reason, failed=failed)
                return True
        status = self._ack(course, url, key, rec, result=bool(free_worker))
        if status in (404, 409):
            self._log(f"{course}: ack 返回 HTTP {status}（worker 已换单/无此单）——不再重试")
        elif status != 200:
            return self._park_settling(
                course, reason, cancel=False, free=free_worker, failed=failed
            )
        self._close(course, rec, reason=reason, failed=failed)
        return True

    def _park_settling(
        self, course: str, reason: str, *, cancel: bool, free: bool, failed: bool = False
    ) -> bool:
        """收摊没答 ⇒ 把**重放所需**的四件事落到 assignment，返回 False（= 还没收完）。

        返回 True 只发生在「账本已经没了」这一种情形（没什么可重试的，调用方可以停）。
        """
        rec = self.assignments.set_fields(
            course,
            phase="settling",
            settle_reason=str(reason or ""),
            settle_cancel=bool(cancel),
            settle_free=bool(free),
            settle_failed=bool(failed),
            settle_at=float(self._now()),
        )
        if rec is None:
            return True
        self._log(
            f"{course}: 收摊没答（{reason}）——记成 settling，"
            f"{int(SETTLE_RETRY_SEC)}s 后重放（cancel={bool(cancel)} free={bool(free)}）"
        )
        return False

    def _settle_retry(self, course: str, url: str, key: str, rec: dict) -> bool:
        """`settling` 的重放（`_pump` 与 `_resume_pumps` 共用）；收完返回 True。"""
        return self._settle_assignment(
            course,
            url,
            key,
            rec,
            cancel=bool(rec.get("settle_cancel")),
            free_worker=bool(rec.get("settle_free")),
            reason=str(rec.get("settle_reason") or "settling"),
            failed=bool(rec.get("settle_failed")),
        )

    def _finish_if_ready(
        self, course: str, url: str, key: str, rec: dict, state: dict
    ) -> bool:
        """有段末 result ⇒ 拉回来入账（盖章 / 滚下一段）→ release → ack(result) → 收摊。"""
        if not bool(state.get("final_ready")):
            return False
        dest = self._tmp_path("result", course, 0)
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
            self.conflicts += 1
            self._log(f"{course}: 结果体权威不符——取消这一单（final 只能丢弃），等人裁决")
            self._settle_assignment(
                course, url, key, rec, cancel=True, free_worker=True,
                reason="conflict-result", failed=True,
            )
            return True
        if status != 200:
            self._log(f"{course}: 结果入账被拒（HTTP {status}）——下一拍重试")
            return False
        self._log(
            f"{course}: 段末已入账（run={rec.get('run_id') or '-'}，"
            f"state={body.get('state') or '-'}）——release + ack，任务结束"
        )
        self._settle_assignment(course, url, key, rec, cancel=False, free_worker=True, reason="done")
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
            + "——已收的轮已入账，收摊"
        )
        self._settle_assignment(
            course, url, key, rec,
            cancel=False, free_worker=True, reason=phase, failed=(phase == "failed"),
        )

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
