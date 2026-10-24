"""hub/offline_push_assign.py —— 离线 push 的**在途 assignment 账本**（2026-10-24，W5）。

一笔 assignment = 「hub 已经把哪一单派给了哪台 worker，收到哪儿了」。它必须**落盘**：hub 重启
（`systemctl restart` / 一次 OOM / 换 token）之后要能接着拉同一单，而不是从头派一遍——
重派会换 `task_id`，worker 那边已跑完的轮次就成了孤儿（`tasks/<旧id>/outbox` 里躺着，
没人再 ack 它，最终撞 outbox 容量闸）。

文件形状（**一个端口一份**，避免同 traj 根下两个 hub 互相覆盖）：

    <root>/offline-push.<port>.json
    {"c1": {"course":"c1","worker_id":"push:offline-kaggle","task_id":"…","pack_sha256":"…",
            "run_id":"…","lease_token":"…","cursor_it":3,
            "revs":{"1":0,"2":1,"3":0},"phase":"running","conflict":"","updated_at":…}}

`cursor_it` = **已入账的最高连续轮**（只进不退：产物是不可变快照，重拉只补 rev 变了的轮）；
`revs` = 已入账那一轮当时的 rev —— 下次 `/ack` 要把它带回去（`ack_outbox` 只删 rev 相符的
body，评审 P0-3）。`phase`：`dispatching` / `running` / `done` / `failed` / `conflict` / `lost`。

为什么写 `revs` 而不是 `acked_it` 一个数：迟到的评估行（`submit_eval_round`）会把同一 `it`
的 rev +1，hub 必须能看出「这一轮我入过账的那版是 rev=0，现在 worker 上是 rev=1」——一个
数字表达不了这件事（那是 P0-3 的原始形状）。

秩 **L0**：只碰 stdlib（`json` / `os.replace` / `Path` / `threading`），课程目录由调用方给。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["ASSIGN_PHASES", "OfflinePushAssignments", "assign_path"]

#: assignment 的阶段（读面/日志共用一份；未知值原样保留 —— 不假装认识未来加的态）。
ASSIGN_PHASES = ("dispatching", "running", "done", "failed", "conflict", "lost")


def assign_path(root: str | Path, port: int) -> Path:
    """assignment 文件落点：`<root>/offline-push.<port>.json`（端口隔离，见模块头注）。"""
    return Path(root) / f"offline-push.{int(port)}.json"


class OfflinePushAssignments:
    """进程内 + 落盘的在途 assignment 表（**唯一写者**：派发器）。

    读面（`/admin/offline-push-workers`）拿 `snapshot()` 的拷贝；pump 线程经 `update()` 推进
    cursor —— 所以每次写都 `os.replace` 原子换入（半截文件会让重启后的 hub 丢掉全部在途单）。
    并发：一把 `Lock`（pump 线程与调度拍都会写）。刻意不做「部分字段合并的乐观锁」：写的是
    同一份 dict 的短临界区，版本号只会多一层没人读的机器。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self._log = log or (lambda _m: None)
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self.load()

    # ------------------------------------------------------------ 落盘
    def load(self) -> dict[str, dict]:
        """从盘上读回（缺文件/坏文件 ⇒ 空表，**绝不抛**：重启后能起服务比恢复历史重要）。"""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            doc = json.loads(raw)
        except ValueError as e:
            self._log(f"WARN assignment 文件读不动（{self.path}）：{e} —— 按空表起")
            return {}
        items: dict[str, dict] = {}
        if isinstance(doc, dict):
            for course, rec in doc.items():
                if isinstance(rec, dict) and str(course).strip():
                    items[str(course)] = {**rec, "course": str(course)}
        with self._lock:
            self._items = items
        return self.snapshot()

    def _save_locked(self) -> None:
        """原子写（临时文件 + `os.replace`）；写失败只记一行 —— 内存里的 cursor 仍是对的。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._items, f, ensure_ascii=False, indent=0, sort_keys=True)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            self._log(f"WARN assignment 落盘失败（{self.path}）：{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 读写
    def get(self, course: str) -> dict | None:
        with self._lock:
            rec = self._items.get(str(course))
            return dict(rec) if rec else None

    def put(self, rec: dict) -> dict:
        """整笔换入（新派发时用）；返回存下来的拷贝。"""
        course = str(rec.get("course") or "").strip()
        if not course:
            raise ValueError("assignment 缺 course")
        out = {**rec, "course": course, "updated_at": float(self._now())}
        with self._lock:
            self._items[course] = out
            self._save_locked()
        return dict(out)

    def set_fields(self, course: str, **fields: Any) -> dict | None:
        """按字段推进（cursor / revs / phase / conflict）；没有这笔 ⇒ None（不凭空造）。"""
        key = str(course)
        with self._lock:
            rec = self._items.get(key)
            if rec is None:
                return None
            rec.update(fields)
            rec["updated_at"] = float(self._now())
            self._save_locked()
            return dict(rec)

    def drop(self, course: str) -> bool:
        key = str(course)
        with self._lock:
            hit = self._items.pop(key, None) is not None
            if hit:
                self._save_locked()
        return hit

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {c: dict(r) for c, r in self._items.items()}

    def of_worker(self, worker_id: str) -> dict[str, dict]:
        """某台 worker 名下的在途单（`worker_id` 是 `push:<id>`；派发时避让已占用的机器）。"""
        wid = str(worker_id)
        return {c: dict(r) for c, r in self.snapshot().items() if str(r.get("worker_id")) == wid}

    def mark(self, course: str, phase: str, *, conflict: str = "") -> dict | None:
        """阶段推进（`done`/`failed`/`conflict`/`lost`）——终局仍留在表里，等人看/被下拍清。"""
        fields: dict[str, Any] = {"phase": str(phase)}
        if conflict:
            fields["conflict"] = str(conflict)
        return self.set_fields(course, **fields)
