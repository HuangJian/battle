"""remote/offline_push_task.py — 离线 push 的**训练子进程入口**（2026-10-23，W4）。

由 worker 服务端（`remote/offline_push.py` 的 supervisor）拉起，一个任务一个进程：

```
python -m remote.offline_push_task \
    --task-dir <tasks/<id>> --outbox <tasks/<id>/outbox> \
    --stop-file <tasks/<id>/stop> [--cfg <tasks/<id>/cfg.json>]
```

为什么是**子进程**而不是线程：训练要吃掉全部 CPU/内存，且 `run_loop` 会注册进度钩子、
改 `sys.path`、解 `code.zip` —— 在 notebook 的 kernel 里就地跑会把服务端（收任务 / 答状态）
和训练耦合死：一次 `SystemExit` 或一个 OOM 就连状态面都没了。子进程还让「取消」有一个
干净的落点（`--stop-file` 由服务端的 `/offline/push/cancel` 写，`plan_run` 在轮边界看它）。

本模块只做**接线**（读任务包里的 `task.json` + 服务端写的 `cfg.json` → `run_one_course`）；
真正的差异（不探 hub / 不取包 / 本地打点 / `--outbox`）全在 `offline_boot.run_one_course`
的 `push_mode` 分支里 —— 那里与 pull 腿共用同一段代码（P1-9）。
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import Any

from common.cli import house_parser, run_main


def _argparser() -> argparse.ArgumentParser:
    ap = house_parser("离线 push：一个任务的训练子进程", prog="offline_push_task")
    ap.add_argument("--task-dir", required=True, help="任务目录（含 task.json / pack/ / resume/）")
    ap.add_argument("--outbox", required=True, help="本地 outbox（补传腿只落盘，hub 来拉）")
    ap.add_argument("--stop-file", default="", help="取消哨兵（轮边界检查）")
    ap.add_argument(
        "--cfg",
        default="",
        help="训练参数（服务端写的 cfg.json；缺省 = <task-dir>/cfg.json）",
    )
    return ap


def _child_log(msg: str) -> None:
    """子进程 stdout/stderr 被服务端重定向到 `<task>/run.log`；前缀与 notebook 的 [offline] 同族。"""
    print(f"[offline-push] {msg}", flush=True)


def load_task(task_dir: Path) -> dict:
    """读任务元数据；坏文件走与「读不到训练参数」同一条响亮出口（不是一段 traceback）。"""
    path = task_dir / "task.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"[offline-push] 读不到任务元数据 {path}（{type(e).__name__}: {e}）") from e
    if not isinstance(doc, dict):
        raise SystemExit(f"[offline-push] task.json 不是对象：{path}")
    return doc


def main(argv: list[str] | None = None) -> int:
    args = _argparser().parse_args(argv)
    task_dir = Path(args.task_dir)
    task = load_task(task_dir)
    cfg_path = Path(args.cfg) if args.cfg else task_dir / "cfg.json"
    try:
        cfg: dict[str, Any] = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"[offline-push] 读不到训练参数 {cfg_path}（{type(e).__name__}: {e}）") from e
    if not isinstance(cfg, dict):
        raise SystemExit(f"[offline-push] {cfg_path} 不是对象")

    course = str(task.get("course") or "").strip()
    if not course:
        raise SystemExit("[offline-push] task.json 缺 course")
    pack = task_dir / "pack" / str(task.get("pack_name") or "")
    resume = task_dir / "resume"
    outbox = Path(args.outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    # ★ 2026-10-24（F7）：hub 已有的最高一轮（派发时的 resume 锚点）。老任务包没这个键 ⇒ 0
    # （全部物化；多写几份 hub 会按内容去重的重复体，不丢数据）。
    floor_it = int(task.get("resume_it") or 0)

    _child_log(
        f"course={course} task={task.get('task_id')} it{task.get('it')}→it{task.get('end_it')}"
        f" pack={pack.name} outbox={outbox}"
        + (f" 地板=it{floor_it}" if floor_it > 0 else "")
    )

    # 延迟 import：`offline_boot` 会解 `code.zip` 并把整个仓塞进 sys.path（引导期不能提前）。
    try:
        from remote.offline_boot import run_one_course
    except Exception as e:  # 代码没铺好：说清楚，别让服务端只看到一行 traceback
        raise SystemExit(
            f"[offline-push] 训练入口不可用（{type(e).__name__}: {e}）——"
            "push 模式的 code.zip 没铺进 sys.path？"
        ) from e

    return int(
        run_one_course(
            cfg,
            {},  # push 腿不用 hub 凭据（不探活 / 不取包 / 不取锚点 / 不回传）
            _child_log,
            threading.Event(),  # keepalive 只在取包那条路用；push 腿没有可取的东西
            course=course,
            push_mode=True,
            assigned_pack=pack,
            assigned_resume=resume if resume.is_dir() else None,
            push_outbox=outbox,
            stop_file=str(args.stop_file or ""),
            push_floor_it=floor_it,
        )
        or 0
    )


if __name__ == "__main__":
    # `run_main` 自己 `raise SystemExit(...)`（None→0 / int→该码 / Ctrl-C→130）。
    run_main(main)
