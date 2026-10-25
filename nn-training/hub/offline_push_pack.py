"""hub/offline_push_pack.py —— 离线 push 的**外层任务包组装**（2026-10-25 从 `hub/offline_push` 拆出）。

为什么单独一块：`hub/offline_push.py` 顶到 LOC 预算（1000 行，`tests/test_python_loc_budget.py`），
而「把一份课程包 + 可选续跑锚点组装成 worker 收的 zip」是**独立所有者**（触发条件 = 派发时组包一次），
与调度拍、pump 线程没有共享状态。它只做文件 IO + 常量拼装：**不连网、不碰租约、不写账本**。

契约（`build_task_zip` 的返回）：
  * `task_id` **确定性**（计划 §6.3：`sha256(course, pack_sha, resume_it)[:16]`）—— hub 在
    「上传成功、还没记账」之间崩掉后重派，worker 看到的是同一单 ⇒ 幂等 duplicate，
    而不是把上一单的 outbox 变成孤儿（评审 F4/F9）；
  * `resume_it` = 派发时敲定的**地板**（取 `max(包起点, 锚点)`，由调用方算好传进来）；
  * `anchor` 非空时把 `resume/`（`resume.json` + `it-NNN/{weights,opt,row}`）一并写进去
    （评审 F2：`run_loop.apply_resume_overlay` 的入口判据就是 `resume.json`）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.protocol import (
    OFFLINE_PUSH_PACK_DIR,
    OFFLINE_PUSH_PROTO,
    OFFLINE_PUSH_RESUME_DIR,
    OFFLINE_PUSH_TASK_NAME,
)
from hub.task_pack import pack_index_meta

#: 任务包 zip 的临时前缀（`<tmp>/offline-push-*`）；上传完成后整目录删掉。
TMP_PREFIX = "offline-push-"

__all__ = ["TMP_PREFIX", "build_task_zip", "write_resume"]


def build_task_zip(
    *,
    course: str,
    pack: Path,
    run_id: str,
    resume_it: int,
    task_id: str,
    pack_sha256: str,
    anchor: dict | None,
    now: float,
    log: Callable[[str], None],
) -> tuple[Path, dict]:
    """组外层 zip → `(临时路径, 元信息)`。入参见模块头；`pack` 本身是 zip ⇒ `ZIP_STORED` 直写。"""
    meta = pack_index_meta(pack)
    pack_it = int(meta.get("it") or 0)
    tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    out = tmp / f"task-{task_id}.zip"
    doc: dict[str, Any] = {
        "proto": OFFLINE_PUSH_PROTO,
        "task_id": task_id,
        "course": course,
        "pack_name": pack.name,
        "pack_sha256": pack_sha256,
        "run_id": run_id,
        "it": pack_it,
        "end_it": int(meta.get("end_it") or 0),
        #: ★ 派发时 hub 已持有的最高一轮（F7）：worker 的 `--floor-it` 就是它。
        "resume_it": int(resume_it),
        "created_at": float(now),
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(OFFLINE_PUSH_TASK_NAME, json.dumps(doc, ensure_ascii=False))
        with open(pack, "rb") as src, zf.open(f"{OFFLINE_PUSH_PACK_DIR}/{pack.name}", "w") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if anchor is not None:
            write_resume(zf, anchor, log)
    return out, {
        "task_id": task_id,
        "resume_it": int(resume_it),
        "pack_sha256": pack_sha256,
        "run_id": doc["run_id"],
        "it": pack_it,
        "end_it": doc["end_it"],
    }


def write_resume(zf: zipfile.ZipFile, anchor: dict, log: Callable[[str], None]) -> int:
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
        log(f"续跑锚点 it{it} 的件一个都读不到（{it_dir}）——任务包不带锚点")
        return 0
    meta = {k: v for k, v in anchor.items() if not str(k).startswith("_")}
    meta["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    zf.writestr(
        f"{OFFLINE_PUSH_RESUME_DIR}/resume.json",
        json.dumps(meta, ensure_ascii=False, indent=1),
    )
    return wrote
