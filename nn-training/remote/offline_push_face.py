"""remote/offline_push_face.py — 离线 push 的 **worker 侧纯 HTTP 面**（2026-10-23，W4）。

方向：hub 是**出站客户端**（经 worker 的稳定隧道）来调本服务 —— state / progress / round /
result / task / ack / cancel。本模块只管「收任务、答状态、递文件」，**不**起训练、**不**碰
cloudflared、**不**注册节点：那些是 supervisor（`remote/offline_push.py`）的事。

契约（`plan/push-mode-notebooks.plan.md` §6.2；端点是跨端契约，常量住
`common/protocol.py`）：

    GET  /ping                         {ok,busy,queued,done,offline_push,proto}
    GET  /offline/push/state           当前任务 + outbox 摘要（**版本门落在这里**，评审 P1-7）
    GET  /offline/push/progress?since=N 最新进度事件（`seq <= since` ⇒ `unchanged`）
    GET  /offline/push/round?it=N      一轮的完整 wire body（与 `/offline/artifact` 同字节）
    GET  /offline/push/result          段末 result body（未收尾 202）
    POST /offline/push/task            外层 zip（流式写临时文件；202/409/413/400）
    POST /offline/push/ack             `{task_id, result, upto_it, revs}` → 按 rev 删 body
    POST /offline/push/cancel          写 stop 文件（轮边界停，产物自洽）

三条不是口味问题的设计：

  * **一单一个 outbox**（`<work>/tasks/<task_id>/outbox`）：所有任务共用一个 outbox 会把
    A 课的轮次留在 B 课的 `state.json` 里，hub 按 `it` 拉就会拿到别人的权重。一单一份 +
    ack 只作用于当前任务 = 串课不可能发生。
  * **收包先落暂存再换入**：`tasks/<id>` 只在「整包校验通过」后才出现。半截包留在
    `incoming/`，重启时整目录清扫 —— 一个坏包不能变成一单「已受理」。
  * **ack 的判据只有一份**（`deliver_outbox.ack_outbox`）：服务进程不跑补传腿，它只读同一份
    `state.json`；两边各写一份「删哪些」就是两套 rev 语义（P0-3 要防的就是这个）。

本模块是 **L2**（`deliver_outbox`(L1) + `common.*` + stdlib）；它不 import `deliver_parts`
（ack 不需要装配器）。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import threading
import time
import zipfile
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from common.http_respond import respond_json
from common.logutil import tag_logger
from common.protocol import (
    OFFLINE_PUSH_ACK_PATH,
    OFFLINE_PUSH_CANCEL_PATH,
    OFFLINE_PUSH_METRICS_PATH,
    OFFLINE_PUSH_OUTBOX_MAX_BYTES,
    OFFLINE_PUSH_PACK_DIR,
    OFFLINE_PUSH_PROGRESS_NAME,
    OFFLINE_PUSH_PROGRESS_PATH,
    OFFLINE_PUSH_PROTO,
    OFFLINE_PUSH_RESULT_PATH,
    OFFLINE_PUSH_RESUME_DIR,
    OFFLINE_PUSH_ROUND_PATH,
    OFFLINE_PUSH_STATE_PATH,
    OFFLINE_PUSH_TASK_BODY_MAX,
    OFFLINE_PUSH_TASK_NAME,
    OFFLINE_PUSH_TASK_PATH,
)
from remote.deliver_outbox import (
    FINAL_NAME,
    ROUND_FILE_FMT,
    ack_outbox,
    metrics_path,
    read_state,
)

AUTH_HEADER = "Authorization"
#: 收包的读块（1MiB）：流式写盘，绝不把 2GiB 读进内存（`Content-Length` 只当上界）。
_READ_CHUNK = 1024 * 1024
#: `/state` 的 `error` 字段最多带多少行 run.log 尾巴。
_ERROR_TAIL_LINES = 20
#: `incoming/` 里超过这个时间还没换入的暂存目录 = 上次进程被杀留下的（启动清扫）。
STALE_INCOMING_SEC = 3600.0


class _CapacityError(Exception):
    """outbox 总量超过上限 ⇒ 413（与「包坏了」的 400 分开：hub 的退避决策不同）。"""


#: 缺省日志（`tag_logger` + 本模块自己的 `time`；名字与 `remote/*` 其余模块不重名）。
_log_face = tag_logger("offline-push-face", lambda: time)


def _read_json_doc(path: Path) -> dict:
    """读一个小 JSON（缺/坏 ⇒ 空 dict）。**永不抛**：读面不该因半截写入炸掉。

    读失败与解失败分开接（不是一句 `except (OSError, ValueError)`）：前者是「还没写」（正常），
    后者是「写坏了」（值得在排障时一眼看出区别），而两者的返回值都是空 —— 调用方不用分。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def safe_extract(zip_path: Path, dest: Path) -> None:
    """解外层任务包到 `dest`：**防 zip-slip**、拒绝符号链接成员、只收普通文件。

    为什么不用 `ZipFile.extractall`：成员名是远端给的（`../../etc/...` 或绝对路径），
    而任务包落在云机的可写盘上 —— 一次路径穿越就能覆盖代码/凭据。这里逐个成员自己写。
    """
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts):
                raise ValueError(f"任务包成员名非法：{info.filename!r}")
            if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
                raise ValueError(f"任务包成员是绝对路径：{info.filename!r}")
            if info.is_dir():
                (dest.joinpath(*parts)).mkdir(parents=True, exist_ok=True)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # 符号链接：整包拒收（没有正当用途，只会绕开路径闸）
                raise ValueError(f"任务包里有符号链接：{info.filename!r}")
            out = dest.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, _READ_CHUNK)


def _course_from_pack_name(path: str | Path) -> str:
    """`task-<课>.zip` → `<课>`（不符合习惯命名 ⇒ 空串，不猜）。

    与 `remote/offline_boot.course_from_pack_name` **同一条命名约定**（那个在 L8：面是 L2，
    import 它会把这层顶到 L9）；两边都要改的时候改这里对照的那条正则。
    """
    m = re.fullmatch(r"task-(.+)\.zip", Path(path).name, re.IGNORECASE)
    return (m.group(1).strip() if m else "")


def task_id_of(task: Mapping) -> str:
    """任务 id 的形状闸（它要拼进目录名 ⇒ 只收 `[0-9a-f]{16}`）。"""
    tid = str(task.get("task_id") or "").strip().lower()
    if len(tid) != 16 or any(c not in "0123456789abcdef" for c in tid):
        raise ValueError(f"task_id 形状非法（要 16 位 hex）：{tid!r}")
    return tid


def validate_task(task: Mapping, *, staging: Path) -> dict:
    """校验 `task.json` + 包 sha256（**收包时就拒**：坏包不能变成一单已受理）。"""
    if str(task.get("proto") or "") != OFFLINE_PUSH_PROTO:
        raise ValueError(
            f"任务包协议不符：{task.get('proto')!r}（本机 {OFFLINE_PUSH_PROTO}）——升级 worker 侧代码"
        )
    tid = task_id_of(task)
    course = str(task.get("course") or "").strip()
    if not course:
        raise ValueError("task.json 缺 course")
    pack_name = str(task.get("pack_name") or "").strip()
    if not pack_name.lower().endswith(".zip") or "/" in pack_name or "\\" in pack_name:
        raise ValueError(f"pack_name 非法：{pack_name!r}")
    pack = staging / OFFLINE_PUSH_PACK_DIR / pack_name
    if not pack.is_file():
        raise ValueError(f"任务包里没有 {OFFLINE_PUSH_PACK_DIR}/{pack_name}")
    want = str(task.get("pack_sha256") or "").strip().lower()
    if not want:
        raise ValueError("task.json 缺 pack_sha256（无法证明这是 hub 导出的那份包）")
    h = hashlib.sha256()
    with open(pack, "rb") as f:
        for chunk in iter(lambda: f.read(_READ_CHUNK), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got != want:
        raise ValueError(f"任务包 sha256 不符（任务说 {want[:12]}…，实得 {got[:12]}…）")
    in_name = _course_from_pack_name(pack)
    if in_name and in_name != course:
        raise ValueError(f"包名里的课程（{in_name}）与 task.course（{course}）不一致")
    return {"task_id": tid, "course": course, "pack": pack, "pack_name": pack_name}


class PushFaceState:
    """一次只跑一单的状态表（`/ping` 的 busy、`/state` 的 phase、ack 都读它）。"""

    def __init__(
        self,
        *,
        work_dir: str | Path,
        log: Callable[[str], None] = _log_face,
        clock: Callable[[], float] = time.time,
        #: outbox 总容量闸（`OFFLINE_PUSH_OUTBOX_MAX_BYTES`）= 停止接新单的上界；
        #: **当前任务照跑**（计划 §6.4a：不静默丢轮）。`<=0` = 不设闸（测试/人工排查用）。
        outbox_cap_bytes: int = OFFLINE_PUSH_OUTBOX_MAX_BYTES,
    ) -> None:
        self.work = Path(work_dir)
        self.log = log
        self._now = clock
        self._outbox_cap = int(outbox_cap_bytes or 0)
        self._lock = threading.RLock()
        #: 当前任务（`task.json` 的内容 + 派生的路径）。
        self.task: dict | None = None
        self.task_dir: Path | None = None
        self.outbox: Path | None = None
        #: idle | running | done | failed | cancelled
        self.phase = "idle"
        #: 子进程退出码（`run_child` 填；`/state` 回给 hub）。
        self.rc: int | None = None
        #: 最后一次 ack 的摘要（幂等重放时原样回）。
        self.last_ack: dict | None = None
        #: 已完成的单数（`/ping.done`）。
        self.done_total = 0
        #: 起子进程的钩子（supervisor 注入：真正 spawn 的是它，face 不碰 subprocess）。
        self._starter: Callable[[dict, Path, Path], None] | None = None
        self._prepare_dirs()

    # ------------------------------------------------------------ 路径

    def _prepare_dirs(self) -> None:
        (self.work / "incoming").mkdir(parents=True, exist_ok=True)
        (self.work / "tasks").mkdir(parents=True, exist_ok=True)
        self._sweep_incoming()

    def _sweep_incoming(self) -> None:
        """清掉上次进程被杀留下的暂存目录（超过 `STALE_INCOMING_SEC`）。"""
        cutoff = float(self._now()) - STALE_INCOMING_SEC
        root = self.work / "incoming"
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        for p in entries:
            try:
                if p.stat().st_mtime < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                continue

    def set_starter(self, starter: Callable[[dict, Path, Path], None]) -> None:
        self._starter = starter

    def task_paths(self, task_id: str) -> tuple[Path, Path, Path]:
        """`(task_dir, outbox, stop_file)` —— 唯一拼法（supervisor 的 `run_child` 也用它）。"""
        tdir = self.work / "tasks" / task_id
        return tdir, tdir / "outbox", tdir / "stop"

    # ------------------------------------------------------------ 只读面

    def busy(self) -> bool:
        """hub 的 `busy`：在跑，或**跑完但 final 未 ack**（ack 前不派下一单）。"""
        with self._lock:
            if self.task is None:
                return False
            if self.phase in ("running", "cancelling"):
                return True
            if self.phase in ("done", "failed", "cancelled"):
                return not bool((self.last_ack or {}).get("acked"))
            return False

    def _error_tail(self) -> str:
        if self.task_dir is None or self.phase not in ("failed", "cancelled"):
            return ""
        p = self.task_dir / "run.log"
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-_ERROR_TAIL_LINES:])

    def snapshot(self) -> dict:
        """`/offline/push/state` 的体（**proto 恒在**：hub 用它做版本门，评审 P1-7）。"""
        with self._lock:
            task = dict(self.task) if isinstance(self.task, dict) else None
            out = str(self.outbox) if self.outbox is not None else ""
            phase = self.phase
            rc = self.rc
            acked = bool((self.last_ack or {}).get("acked"))
        doc: dict[str, Any] = {
            "ok": True,
            "offline_push": True,
            "proto": OFFLINE_PUSH_PROTO,
            "phase": phase,
            "busy": self.busy(),
            "ack": acked,
            "rc": rc,
            "task": task,
            "outbox": out,
        }
        if task is not None and out:
            st = read_state(out)
            rounds = st.get("rounds")
            doc["rounds"] = dict(rounds) if isinstance(rounds, Mapping) else {}
            doc["final_ready"] = bool(st.get("final_ready"))
            doc["final_acked"] = bool(st.get("final_acked"))
        tail = self._error_tail()
        if tail:
            doc["error"] = tail
        return doc

    def progress(self, since: int = 0) -> dict:
        """`/offline/push/progress?since=N`：进度文件原样返回（`seq` 单调、跨重启续上）。"""
        with self._lock:
            out = self.outbox
        if out is None:
            return {"seq": 0, "unchanged": True, "at": 0.0, "kind": "", "it": 0}
        doc = _read_json_doc(Path(out) / OFFLINE_PUSH_PROGRESS_NAME)
        seq = int(doc.get("seq", 0) or 0) if isinstance(doc, dict) else 0
        if seq <= int(since):
            return {"seq": seq, "unchanged": True, "at": float(doc.get("at", 0.0) or 0.0)}
        doc["unchanged"] = False
        doc["seq"] = seq
        return doc

    def round_body(self, it: int) -> bytes | None:
        with self._lock:
            out = self.outbox
        if out is None:
            return None
        p = Path(out) / "rounds" / ROUND_FILE_FMT.format(it=int(it))
        try:
            return p.read_bytes()
        except OSError:
            return None

    def metrics_body(self, it: int) -> bytes | None:
        """`/offline/push/metrics?it=N`：这一轮的统计体（`metrics/itNNNN.json`）。

        它独立于权重体存在（评审 P1-3）：`oversize` 的轮**没有** round body，但统计照落 ——
        少了这条腿，控制台就看不到那些轮（2026-10-23 y31-null 的教训）。
        """
        with self._lock:
            out = self.outbox
        if out is None:
            return None
        try:
            return metrics_path(out, int(it)).read_bytes()
        except OSError:
            return None

    def outbox_usage(self) -> int:
        """所有任务 outbox 的**总字节**（容量闸的输入：一单一份，历史单也要算）。"""
        root = self.work / "tasks"
        try:
            dirs = list(root.iterdir())
        except OSError:
            return 0
        total = 0
        for d in dirs:
            out = d / "outbox"
            if not out.is_dir():
                continue
            for p in out.rglob("*"):
                try:
                    if p.is_file():
                        total += int(p.stat().st_size)
                except OSError:
                    continue
        return total

    def result_body(self) -> bytes | None:
        with self._lock:
            out = self.outbox
        if out is None:
            return None
        try:
            return (Path(out) / FINAL_NAME).read_bytes()
        except OSError:
            return None

    # ------------------------------------------------------------ 收任务

    def accept_task_zip(self, tmp_path: Path, *, size: int) -> tuple[int, dict]:
        """校验 + 换入一单 → `(状态码, 应答)`；**坏包只留响应，不污染 `tasks/`**。"""
        staging = self.work / "incoming" / f"in-{secrets.token_hex(6)}"
        try:
            safe_extract(tmp_path, staging)
            task = _read_json_doc(staging / OFFLINE_PUSH_TASK_NAME)
            if not task:
                raise ValueError(f"任务包缺 {OFFLINE_PUSH_TASK_NAME}")
            meta = validate_task(task, staging=staging)
            tid = str(meta["task_id"])
            tdir, outbox, _stop = self.task_paths(tid)
            with self._lock:
                same = bool(self.task and self.task.get("task_id") == tid)
                if same:
                    self.log(f"任务 {tid} 重复投递 —— 幂等受理（当前 {self.phase}）")
                    return 202, {
                        "status": "duplicate",
                        "task_id": tid,
                        "phase": self.phase,
                        "proto": OFFLINE_PUSH_PROTO,
                    }
                if self.busy():
                    return 409, {
                        "error": "busy",
                        "task_id": str((self.task or {}).get("task_id") or ""),
                        "proto": OFFLINE_PUSH_PROTO,
                    }
                # 容量闸（计划 §6.4a）：超过 `OFFLINE_PUSH_OUTBOX_MAX_BYTES` ⇒ **停止接新单**，
                # 当前任务照跑。放在 busy/duplicate 之后：那两种都不占新空间。
                used = self.outbox_usage()
                if self._outbox_cap > 0 and used >= self._outbox_cap:
                    raise _CapacityError(
                        f"outbox 已用 {used / 1024**3:.2f}GiB（上限 "
                        f"{self._outbox_cap / 1024**3:.2f}GiB）——停止接新单；"
                        "等 hub 把已完成的轮 ack 掉再来"
                    )
                # 换入：先删旧的同 id 目录（理论上不存在；重复单在上面已早退），再 rename。
                if tdir.exists():
                    shutil.rmtree(tdir, ignore_errors=True)
                tdir.parent.mkdir(parents=True, exist_ok=True)
                staging.replace(tdir)
                outbox.mkdir(parents=True, exist_ok=True)
                self.task = {**task, "task_id": tid, "course": meta["course"]}
                self.task_dir = tdir
                self.outbox = outbox
                self.phase = "running"
                self.rc = None
                self.last_ack = None
            self.log(
                f"受理任务 {tid}（course={meta['course']} it{task.get('it')}→it{task.get('end_it')}，"
                f"{size / 1024**2:.1f}MB）→ {tdir}"
            )
        except _CapacityError as e:
            shutil.rmtree(staging, ignore_errors=True)
            self.log(f"拒收任务包（容量）：{e}")
            return 413, {"error": str(e), "proto": OFFLINE_PUSH_PROTO}
        except (ValueError, OSError, zipfile.BadZipFile) as e:
            shutil.rmtree(staging, ignore_errors=True)
            self.log(f"拒收任务包：{type(e).__name__}: {e}")
            return 400, {"error": str(e), "proto": OFFLINE_PUSH_PROTO}
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        starter = self._starter
        if starter is None:
            with self._lock:
                self.phase = "failed"
            return 500, {"error": "no starter installed", "proto": OFFLINE_PUSH_PROTO}
        starter(self.task or {}, self.task_dir, self.outbox)  # type: ignore[arg-type]
        return 202, {
            "status": "accepted",
            "task_id": tid,
            "phase": self.phase,
            "proto": OFFLINE_PUSH_PROTO,
        }

    # ------------------------------------------------------------ ack / cancel / 进程回填

    def ack(self, body: Mapping) -> tuple[int, dict]:
        """`POST /offline/push/ack`：按 rev 收下（判据只在 `deliver_outbox.ack_outbox`）。"""
        with self._lock:
            task = self.task
            out = self.outbox
            phase = self.phase
        if task is None or out is None:
            return 404, {"error": "no task", "proto": OFFLINE_PUSH_PROTO}
        tid = str(body.get("task_id") or "").strip()
        if tid and tid != str(task.get("task_id")):
            return 409, {
                "error": f"task_id 不符（当前 {task.get('task_id')}）",
                "proto": OFFLINE_PUSH_PROTO,
            }
        revs_raw = body.get("revs")
        revs: dict[int, int] | None = None
        if isinstance(revs_raw, Mapping):
            revs = {}
            for k, v in revs_raw.items():
                try:
                    revs[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
        out_doc = ack_outbox(
            out,
            upto_it=int(body.get("upto_it", -1)),
            result=bool(body.get("result", True)),
            revs=revs,
            log=self.log,
        )
        if bool(body.get("result", True)):
            with self._lock:
                # `done_total` 只在**这单第一次**被收下时 +1：hub 的 ack 是幂等重试，
                # 每重试一次就把 `/ping.done` 抬一格会让控制台看到比真实完成数更多的单。
                first = not bool((self.last_ack or {}).get("acked"))
                self.last_ack = {"acked": True, "task_id": task.get("task_id"), **out_doc}
                if first:
                    self.done_total += 1
                self.phase = "idle" if phase in ("done", "failed", "cancelled") else phase
        return 200, {
            "ok": True,
            "task_id": task.get("task_id"),
            "phase": self.phase,
            "proto": OFFLINE_PUSH_PROTO,
            **out_doc,
        }

    def cancel(self, body: Mapping | None = None) -> tuple[int, dict]:
        """`POST /offline/push/cancel`：写 stop 文件（**轮边界**停，见 `plan_run._drive`）。

        `body.task_id` 给了就必须是当前这单：hub 的 cancel 与 ack 一样是**网络重试**，
        迟到的那次若落在下一单上，会取消一门正在跑的课（评审 F3）。
        """
        tid = str((body or {}).get("task_id") or "").strip()
        with self._lock:
            task = self.task
            tdir = self.task_dir
            if task is None or tdir is None:
                return 404, {"error": "no task", "proto": OFFLINE_PUSH_PROTO}
            if tid and tid != str(task.get("task_id")):
                return 409, {
                    "error": f"task_id 不符（当前 {task.get('task_id')}）",
                    "proto": OFFLINE_PUSH_PROTO,
                }
            if self.phase in ("done", "failed", "cancelled"):
                return 202, {"status": self.phase, "proto": OFFLINE_PUSH_PROTO}
            try:
                (tdir / "stop").write_text("cancel\n", encoding="utf-8")
            except OSError as e:
                return 500, {"error": f"stop 写入失败：{e}", "proto": OFFLINE_PUSH_PROTO}
            if self.phase == "running":
                self.phase = "cancelling"
        self.log(f"任务 {task.get('task_id')} 收到取消 —— 子进程会在下一个轮边界停")
        return 202, {"status": "cancelling", "proto": OFFLINE_PUSH_PROTO}

    def note_child_exit(self, rc: int) -> None:
        """子进程退出 → phase（`cancelled` 只在**真的有** stop 文件时成立）。"""
        with self._lock:
            stop = bool(self.task_dir and (self.task_dir / "stop").exists())
            if self.phase == "cancelling" or (stop and rc == 0):
                self.phase = "cancelled"
            else:
                self.phase = "done" if rc == 0 else "failed"
            self.rc = int(rc)
            phase = self.phase
        self.log(f"任务 {self.task.get('task_id') if self.task else '?'} 子进程退出 rc={rc} → {phase}")


class _Handler(BaseHTTPRequestHandler):
    """所有端点（线程 per 请求；大 body 下载不挡调度）。"""

    state: PushFaceState
    token: str
    log: Callable[[str], None]

    def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志
        pass

    _json = respond_json

    def _token_ok(self) -> bool:
        auth = self.headers.get(AUTH_HEADER, "")
        return secrets.compare_digest(auth, f"Bearer {self.token}")

    def _send_bytes(self, raw: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        qs = parse_qs(urlparse(self.path).query)
        try:
            if not self._token_ok():
                self._json({"error": "unauthorized"}, 401)
                return
            if path == "/ping":
                st = self.state
                self._json(
                    {
                        "ok": True,
                        "offline_push": True,
                        "proto": OFFLINE_PUSH_PROTO,
                        "busy": st.busy(),
                        "queued": 0,
                        "done": int(st.done_total),
                    }
                )
                return
            if path == OFFLINE_PUSH_STATE_PATH:
                self._json(self.state.snapshot())
                return
            if path == OFFLINE_PUSH_PROGRESS_PATH:
                since = int((qs.get("since") or ["0"])[0] or 0)
                self._json(self.state.progress(since))
                return
            if path == OFFLINE_PUSH_ROUND_PATH:
                it = int((qs.get("it") or ["-1"])[0] or -1)
                raw = self.state.round_body(it) if it >= 0 else None
                if raw is None:
                    self._json({"error": f"no body for it{it}", "proto": OFFLINE_PUSH_PROTO}, 404)
                else:
                    self._send_bytes(raw)
                return
            if path == OFFLINE_PUSH_METRICS_PATH:
                it = int((qs.get("it") or ["-1"])[0] or -1)
                raw = self.state.metrics_body(it) if it >= 0 else None
                if raw is None:
                    self._json({"error": f"no metrics for it{it}", "proto": OFFLINE_PUSH_PROTO}, 404)
                else:
                    self._send_bytes(raw)
                return
            if path == OFFLINE_PUSH_RESULT_PATH:
                raw = self.state.result_body()
                if raw is None:
                    self._json({"status": self.state.phase, "proto": OFFLINE_PUSH_PROTO}, 202)
                else:
                    self._send_bytes(raw)
                return
            self._json({"error": "not found"}, 404)
        except Exception as e:  # 单请求不崩服务
            self.log(f"GET {path} ERROR: {type(e).__name__}: {e}")
            self._json({"error": "internal"}, 500)

    def _read_body_to_file(self, dest: Path) -> int:
        """按 `Content-Length` 流式落盘 → 字节数；超上限抛 `ValueError`（调用方 413）。"""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("缺 Content-Length")
        if length > OFFLINE_PUSH_TASK_BODY_MAX:
            raise ValueError(f"任务包 {length}B 超上限 {OFFLINE_PUSH_TASK_BODY_MAX}B")
        left = length
        with open(dest, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(_READ_CHUNK, left))
                if not chunk:
                    raise ValueError("body 提前截断")
                f.write(chunk)
                left -= len(chunk)
        return length

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if not self._token_ok():
                self._json({"error": "unauthorized"}, 401)
                return
            if path == OFFLINE_PUSH_TASK_PATH:
                tmp = self.state.work / "incoming" / f"body-{secrets.token_hex(6)}.zip"
                try:
                    size = self._read_body_to_file(tmp)
                except ValueError as e:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    code = 413 if "超上限" in str(e) else 400
                    self._json({"error": str(e)}, code)
                    return
                status, doc = self.state.accept_task_zip(tmp, size=size)
                self._json(doc, status)
                return
            if path == OFFLINE_PUSH_ACK_PATH:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError as e:
                    self._json({"error": f"ack 体不是 JSON：{e}"}, 400)
                    return
                if not isinstance(body, dict):
                    self._json({"error": "ack 体要是对象"}, 400)
                    return
                status, doc = self.state.ack(body)
                self._json(doc, status)
                return
            if path == OFFLINE_PUSH_CANCEL_PATH:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    body = {}
                status, doc = self.state.cancel(body if isinstance(body, dict) else {})
                self._json(doc, status)
                return
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self.log(f"POST {path} ERROR: {type(e).__name__}: {e}")
            self._json({"error": "internal"}, 500)


def make_face_server(
    state: PushFaceState,
    port: int,
    token: str,
    *,
    host: str = "0.0.0.0",
    log: Callable[[str], None] = _log_face,
) -> ThreadingHTTPServer:
    """构造 worker 侧 HTTP 服务（调用方 `serve_forever()`；测试可 `server_close()`）。"""
    if not token:
        raise ValueError("offline push worker 需要 push_token（hub 用它鉴权）")
    handler = type(
        "OfflinePushFaceHandler",
        (_Handler,),
        {"state": state, "token": token, "log": log},
    )
    return ThreadingHTTPServer((host, port), handler)


def tail_text(zip_path: Path) -> str:  # pragma: no cover - 排障用的一行摘要
    """任务包的一行摘要（`task.json` 的 course/it/end_it；解不开给空串）。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            doc = json.loads(zf.read(OFFLINE_PUSH_TASK_NAME).decode("utf-8"))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return ""
    if not isinstance(doc, dict):
        return ""
    return f"{doc.get('course')} it{doc.get('it')}→it{doc.get('end_it')}"


__all__ = [
    "OFFLINE_PUSH_RESUME_DIR",
    "PushFaceState",
    "make_face_server",
    "safe_extract",
    "validate_task",
]
