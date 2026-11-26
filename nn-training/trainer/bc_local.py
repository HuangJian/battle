"""本机 BC 子进程：`train/bc.py` 的命令行、逐行账本/eval 与权重回读。

从 `trainer/bc_loop.py` 拆出（2026-11-26，单文件 LOC < 1000）：bc_loop 保留课程编排与
云端往返，本模块只拥有「本机训练进程」这一段。`bc_loop` 仍以同名门面导出
`train_local_bc` / `_strip_stamp`，调用点与 monkeypatch 面不变。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from common.log import log
from common.paths import REPO_ROOT
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from trainer.bc_ingest import ledger_bc_epoch
from trainer.bc_round_job import train_tuning_argv
from worker.bc_config import BcCourseConfig
from worker.bc_eval import BcEvalError, BcEvalSpec, dispatch_bc_eval
from worker.bc_ledger import append_ledger

#: nn-training 目录（子进程 cwd 与 bc.py 路径都相对它）。
NN_ROOT = REPO_ROOT / "nn-training"

# BC 训练器（`worker/train/bc.py` / `bc_paddle.py` / `device.py`）的每条日志自带
# `[HH:MM:SS] ` 前缀（2026-11-12：云机 BC 日志逐行可对时）。解析前必须剥掉——
# `line.startswith("[epoch")` 与账本正则都锚在行首，不剥就会**静默停写** bc_epoch。
_STAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def _strip_stamp(line: str) -> str:
    """剥掉行首的 `[HH:MM:SS] `（无戳行原样返回）。"""
    return _STAMP_PREFIX.sub("", line)
_EPOCH_LINE_RE = re.compile(
    r"\[epoch\s+(\d+)/(\d+)\] train_loss=([\d.]+) val_loss=([\d.]+) "
    r"acc move=([\d.]+) fire=([\d.]+)(?: value=[\d.]+ )?lr=([\d.eE+]+)"
)
_EPOCH_HEAD_RE = re.compile(r"\[epoch\s+(\d+)/")
_LOCAL_RELAY_PREFIXES = (
    "[epoch",
    "[train] done",
    "[train] samples",
    "[train] majority",
    "[train] checkpoint",
)


@dataclass(frozen=True, kw_only=True)
class _LocalBcPlan:
    """本机 BC 子进程的解析结果（一次算好，命令行与逐行处理共用）。"""

    course: BcCourseConfig
    data_round_dir: Path
    out_weights: str
    jsonl_path: Path | None
    it: int
    cfg: dict | None
    epochs: int
    batch: int
    eval_on: bool
    ckpt_every: int


def _local_bc_plan(
    course: BcCourseConfig,
    data_round_dir: Path,
    out_weights: str,
    *,
    smoke: bool,
    jsonl_path: Path | None,
    it: int,
    cfg: dict | None,
) -> _LocalBcPlan:
    epochs = 1 if smoke else int(course.train.epochs)
    batch = min(int(course.train.batch), 256) if smoke else int(course.train.batch)
    eval_on = course.eval.enabled and not smoke
    # 本地 eval 权重源 = bc.py 的 ckpt 文件——ckpt_every 对齐 eval 边界
    ckpt_every = (
        int(course.eval.every_epochs) if (eval_on and not smoke) else int(course.train.ckpt_every)
    )
    return _LocalBcPlan(
        course=course,
        data_round_dir=data_round_dir,
        out_weights=out_weights,
        jsonl_path=jsonl_path,
        it=it,
        cfg=cfg,
        epochs=epochs,
        batch=batch,
        eval_on=eval_on,
        ckpt_every=ckpt_every,
    )


def _local_bc_argv(plan: _LocalBcPlan) -> list[str]:
    """本机 `train/bc.py` 的完整命令行（与云端 manifest 同口径的开关都在这里）。"""
    course = plan.course
    cmd = [
        sys.executable,
        "-u",
        str(NN_ROOT / "worker" / "train" / "bc.py"),
        "--data-dir",
        str(plan.data_round_dir),
        "--arch",
        str(course.train.arch),
        "--out",
        str(plan.out_weights),
        "--epochs",
        str(plan.epochs),
        "--batch",
        str(plan.batch),
        "--lr",
        str(course.train.lr),
        "--val-split",
        str(course.train.val_split),
        "--mirror-p",
        str(course.train.mirror_p),
        "--value-coef",
        str(course.train.value_coef),
        "--seed",
        str(course.train.seed),
        "--fire-pos-weight",
        str(course.train.fire_pos_weight),
        *train_tuning_argv(course.train),
        "--ckpt-every",
        str(plan.ckpt_every),
        "--device",
        "cpu",
        "--notes",
        f"run_bc local course={course.name}",
    ]
    # 本地 CLI 也必须能与云端口径同构（2026-11-05）：v6 三头 / 空间头开关以前只在
    # 云端 manifest 里，本地 `train/bc.py` 会把 v6 语料按 legacy 5 类读。
    if course.resolved_action_v6:
        cmd.append("--action-v6")
    if course.train.policy_extra:
        cmd.append("--policy-extra")
    # 路径一律**绝对**：run_bc 本体 chdir 仓库根，而子进程 cwd=NN_ROOT——相对路径
    # 会在 nn-training/ 下找语料（2026-09-13 实测 FileNotFoundError）。
    cmd[cmd.index("--data-dir") + 1] = str(Path(cmd[cmd.index("--data-dir") + 1]).resolve())
    cmd[cmd.index("--out") + 1] = str(Path(cmd[cmd.index("--out") + 1]).resolve())
    return cmd


def _local_record_epoch(plan: _LocalBcPlan, bare: str) -> None:
    """epoch 行 → `bc_epoch` 账本（与云端 `ingest_bc_metrics` 同字段）。"""
    jsonl_path = plan.jsonl_path
    if jsonl_path is None or not bare.startswith("[epoch"):
        return
    m = _EPOCH_LINE_RE.match(bare)
    if not m:
        return
    ledger_bc_epoch(
        jsonl_path,
        plan.it,
        {
            "epoch": int(m.group(1)),
            "train_loss": float(m.group(3)),
            "val_loss": float(m.group(4)),
            "move_acc": float(m.group(5)),
            "fire_acc": float(m.group(6)),
            "lr": float(m.group(7)),
        },
    )


def _local_eval_if_due(
    plan: _LocalBcPlan, bare: str, evaluated: set[int], log
) -> None:
    """eval 边界（epoch % every == 0）→ ckpt 权重 → 多地图 eval。"""
    jsonl_path = plan.jsonl_path
    if not plan.eval_on or jsonl_path is None or not bare.startswith("[epoch"):
        return
    m2 = _EPOCH_HEAD_RE.match(bare)
    if not m2:
        return
    gepoch = int(m2.group(1))
    if (
        not gepoch
        or gepoch % int(plan.course.eval.every_epochs) != 0
        or gepoch in evaluated
    ):
        return
    evaluated.add(gepoch)
    ckpt = Path(f"{plan.out_weights}.ckpt.{gepoch}")
    if not ckpt.exists():
        return
    log(f"[run_bc] it{plan.it} ep{gepoch}: 本地 eval（ckpt 权重）")
    try:
        result = dispatch_bc_eval(
            BcEvalSpec(
                weights_bytes=ckpt.read_bytes(),
                eval_levels=list(plan.course.eval.levels),
                games_per_stage=int(plan.course.eval.games_per_stage),
                it=plan.it,
                epoch=gepoch,
                cfg=plan.cfg,
                course=plan.course,
                course_env=bool(plan.course.eval.course_env),
                meta_path=(jsonl_path.parent / "dist-agent-meta.jsonl"),
                log=log,
            )
        )
        append_ledger(
            jsonl_path, {"event": "bc_eval", "it": int(plan.it), **result}
        )
    except BcEvalError as e:
        log(f"[run_bc] it{plan.it} ep{gepoch}: eval 放弃（{e}）")


def _local_handle_line(
    plan: _LocalBcPlan, bare: str, evaluated: set[int], log
) -> None:
    """中继一行正文：日志回声 → epoch 账本 → eval 触发。"""
    if bare.startswith(_LOCAL_RELAY_PREFIXES):
        log(f"[bc.py] {bare}")
    _local_record_epoch(plan, bare)
    _local_eval_if_due(plan, bare, evaluated, log)


def _local_run_process(plan: _LocalBcPlan, cmd: list[str], log) -> None:
    """起子进程并逐行中继；非零退出把尾段带出。"""
    # §16.3 进度可观测：Popen 逐行**流式**中继（capture_output 会缓冲到进程结束——
    # 60 epoch 的训练跑中零输出 = 无法验证进度/无法预算判活）。
    proc = subprocess.Popen(
        cmd,
        cwd=str(NN_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_POPEN_NO_WINDOW,
    )
    out_lines: list[str] = []
    if proc.stdout is None:  # pragma: no cover — stdout=PIPE 结构性保证非空
        raise SystemExit("[run_bc] local bc.py: 拿不到子进程 stdout（不该发生）")
    evaluated: set[int] = set()
    for raw in proc.stdout:
        line = raw.rstrip()
        out_lines.append(line)
        # 训练器自带时间戳（2026-11-12）——中继与解析都走剥戳后的正文：`log()` 会再
        # 加一道自己的时间戳，账本正则要的是 `[epoch` 在行首。
        _local_handle_line(plan, _strip_stamp(line), evaluated, log)
    rc = proc.wait()
    if rc != 0:
        # 失败把尾段响亮带出（全量输出已随流中继进日志，这里只补尾部定位）
        tail = "\n".join(out_lines[-12:])
        log(f"[run_bc] local bc.py 退出码 {rc}——训练失败，子进程尾段：\n{tail}")
        raise SystemExit(f"[run_bc] local bc.py 退出码 {rc}——训练失败")


def _local_read_metrics(plan: _LocalBcPlan, t0: float) -> dict:
    """回读 bc.py 的 weights.json（versioned archive + active pointer）。"""
    # 注意 extra_meta 是**顶层合并**（sizes/best_val_loss/history 都是 JSON 顶层键，
    # weights_io.save_weights_json）。
    with open(Path(plan.out_weights), encoding="utf-8") as f:
        wj = json.load(f)
    hist = wj.get("history", {})
    sizes = wj.get("sizes", {})
    return {
        "epochs": plan.epochs,
        # 早停时 weights.json 的实际完成数（旧产物没有该键 ⇒ 回落到课程目标）。
        "epochs_completed": int(wj.get("epochs_completed", plan.epochs) or plan.epochs),
        "train_samples": int(sizes.get("train", 0) or 0),
        "val_samples": int(sizes.get("val", 0) or 0),
        "best_val_loss": float(wj.get("best_val_loss", 0.0) or 0.0),
        "move_acc": float(hist.get("move_acc", [0.0])[-1]) if hist.get("move_acc") else 0.0,
        "fire_acc": float(hist.get("fire_acc", [0.0])[-1]) if hist.get("fire_acc") else 0.0,
        "local_sec": round(time.time() - t0, 1),
    }


def train_local_bc(
    course: BcCourseConfig,
    data_round_dir: Path,
    out_weights: str,
    *,
    smoke: bool,
    jsonl_path: Path | None = None,
    it: int = 0,
    cfg: dict | None = None,
    log=log,
) -> dict:
    """--local：本机子进程 train/bc.py（训练机 venv torch；run_bc 本身保持 torch-free）。"""
    plan = _local_bc_plan(
        course,
        data_round_dir,
        out_weights,
        smoke=smoke,
        jsonl_path=jsonl_path,
        it=it,
        cfg=cfg,
    )
    cmd = _local_bc_argv(plan)
    log(
        f"[run_bc] local BC: {' '.join(cmd[:6])} … "
        f"(epochs={plan.epochs} batch={plan.batch})"
    )
    t0 = time.time()
    _local_run_process(plan, cmd, log)
    return _local_read_metrics(plan, t0)


