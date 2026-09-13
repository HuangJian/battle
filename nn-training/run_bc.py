"""run_bc.py — BC 训练编排器（云-HUB-LAN 体系；plan/bc-cloud-integration.plan.md §6）。

把 BC（行为克隆）从本地手工流程升级为与 PPO 同构的分布式管线：
  每轮 it：
    1. 账本已有 `bc_round_completed(it)` → 跳过（断点续跑）；
    2. 语料缺额 → rl/bc_dispatch.py 派 LAN 节点采 God-AI 语料（mode=bc）；
    3. publish_job(kind=bc) → 云端 worker 跑 train/bc.py → 回传 BC 权重；
    4. verify_and_land_bc → `tmp/<course>/weights.json`；
    5. 归档 backup_weights → `nn-training/weights/<prefix>/…it<N>.<ts>.json` + WEIGHTS.md 行。

三种传输（--course 之外按环境自动选）：
  push   ：env REMOTE_PUSH_NODE（控制台冒烟/伪 GPU 节点）或 courses.<课>.push_node_url
           → push_client 直推 GPU 节点 worker_server；
  hub    ：--remote → 发布到 per-course hub（云机 `remote_worker --poll` 领取）；
  local  ：--local → 语料就绪后本机子进程 train/bc.py（训练机 venv torch）。

--smoke：语料 1 局/max_ticks≤300/epochs=1 的真一轮（伪 GPU 节点可达）→ 落位即作废
（不覆盖 out、不归档），打 `BC SMOKE PASS`——控制台 smokeTrain 的三里程碑之一。

本模块 torch-free（local 模式经子进程消费 torch；云端训练在 worker 内）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import force_utf8_stdio
from remote.hub_client import (
    git_head,
    iter_bc_shard_dirs,
    mark_job_completed,
    pack_code_zip,
    publish_job,
    verify_and_land_bc,
)
from remote.protocol import decode_weights_json
from rl.archive import backup_weights
from rl.bc_config import (
    BcCourseConfig,
    bc_corpus_identity_fp,
    load_bc_course,
    resolve_bc_course,
    round_seeds,
)
from rl.bc_dispatch import BcDispatchError, dispatch_bc_corpus, landed_pairs
from rl.bc_eval import BcEvalError, dispatch_bc_eval
from rl.log import log

REPO_ROOT = Path(__file__).resolve().parents[1]
NN_ROOT = Path(__file__).resolve().parent

#: per-job 等待上限（BC 训练时长方差大：epochs × 语料量；默认 2h，--wait-sec 可调）
DEFAULT_WAIT_SEC = 2 * 3600.0

#: bc-data 轮目录保留数（磁盘有界性；PPO 侧 keep_iters 同语义）
BC_DATA_KEEP = 3

RUN_ID = f"bc-{secrets.token_hex(8)}"


# ------------------------------------------------------------------ 账本


def _append_ledger(jsonl_path: str | Path, event: dict) -> None:
    p = Path(jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def completed_rounds(jsonl_path: str | Path) -> set[int]:
    """账本里已完成的 BC 轮号（`bc_round_completed` 事件；断点续跑依据）。"""
    out: set[int] = set()
    p = Path(jsonl_path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "bc_round_completed":
            try:
                out.add(int(e["it"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


# ------------------------------------------------------------------ 归档


def append_weights_md_row(
    weights_dir: str | Path,
    archive_path: str | Path,
    *,
    epochs: int,
    samples: tuple[int, int],
    best_val: float,
    accs: tuple[float, float],
    note: str,
) -> None:
    """训练机侧 WEIGHTS.md 注册行（与 train/bc.py 的表头/列同格式；torch-free 复刻——
    run_bc 不 import torch，bc.py 的 _append_weights_md 不可复用）。"""
    md_path = Path(weights_dir) / "WEIGHTS.md"
    try:
        trained_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        sha = "n/a"
        try:
            raw: bytes = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(REPO_ROOT),
                stderr=subprocess.DEVNULL,
                timeout=15,
                **_POPEN_NO_WINDOW,
            )
            sha = raw.decode().strip() or "n/a"
        except Exception:
            pass
        row = (
            f"| {trained_at} | `{Path(archive_path).name}` | {epochs} "
            f"| {samples[0]}/{samples[1]} | {best_val:.4f} "
            f"| {accs[0]:.3f}/{accs[1]:.3f}/n/a "
            f"| {sha} | {note} |\n"
        )
        md_path.parent.mkdir(parents=True, exist_ok=True)
        if not md_path.exists():
            log(f"[run_bc] WARN: {md_path} 不存在（bc.py 首跑尚未建立）——仅追加本行")
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(row)
    except OSError as e:
        log(f"[run_bc] WARN: WEIGHTS.md 追加失败（非致命）: {e}")


def prune_bc_data(traj: Path, it: int, keep: int = BC_DATA_KEEP) -> None:
    """bc-data 旧轮目录收敛（保留最近 keep 轮；best-effort，沙箱删除保护跳过）。"""
    root = traj / "bc-data"
    if not root.exists():
        return
    for d in root.glob("it*"):
        try:
            k = int(d.name[2:])
        except ValueError:
            continue
        if k < it - keep + 1:
            try:
                shutil.rmtree(d)
                log(f"[run_bc] pruned {d}")
            except OSError as e:
                log(f"[run_bc] WARN prune {d} skipped: {e}")


def prune_remote_jobs(job_root: Path, keep: int = 3) -> None:
    """remote-jobs 旧 job 目录收敛（按 mtime 保留最近 keep 个；与 worker 侧同策略）。"""
    if not job_root.exists():
        return
    dirs = sorted(
        (d for d in job_root.iterdir() if d.is_dir() and d.name not in (".extra_tmp",)),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in dirs[keep:]:
        try:
            shutil.rmtree(d)
        except OSError:
            pass


# ------------------------------------------------------------------ 语料


def smoke_overrides(course: BcCourseConfig) -> dict:
    """--smoke 尺寸压缩：1 局 / max_ticks≤300 / epochs=1（真一轮，分钟级内）。

    wins_only 同步关闭：300 tick 内不可能 stage_clear，wins-only 会把唯一一局也
    过滤掉（实测教训 2026-09-13）——冒烟要的是全链打通，不是语料质量。"""
    return {
        "games_per_stage": 1,
        "max_ticks": min(int(course.max_ticks), 300),
        "epochs": 1,
        "batch": min(int(course.train.batch), 256),
        "wins_only": False,
        "near_miss_times": 1,
    }


def collect_corpus(
    course: BcCourseConfig,
    course_fp: str,
    corpus_fp: str,
    it: int,
    data_round_dir: Path,
    *,
    smoke: bool,
    cfg: dict | None,
    log=log,
) -> None:
    """补齐本轮语料：断点续跑过滤 → bc_dispatch 并发派发 → 无 shard 则响亮失败。

    smoke 轮**不做断点续跑**（todo = 全量重采）——smoke 覆盖（wins_only/max_ticks）
    与真跑口径不同，复用盘上 shard 会拿冒烟语料冒充真语料（2026-09-13 实测：
    smoke 的 timeout shard 落进 it1 被真跑复用，语料污染）。"""
    seeds = round_seeds(course, it)
    stages = course.stage_ids
    want = {(s, seed) for s in stages for seed in seeds}
    have = landed_pairs(data_round_dir)
    max_ticks = int(course.max_ticks)
    wins_only = bool(course.corpus.wins_only)
    near_miss = int(course.corpus.near_miss_times)
    if smoke:
        ov = smoke_overrides(course)
        max_ticks = ov["max_ticks"]
        wins_only = ov["wins_only"]
        near_miss = ov["near_miss_times"]
        seeds = seeds[:1]
        want = {(s, seeds[0]) for s in stages}
        todo = sorted(want)  # smoke 全量重采（不复用盘上 shard，见 docstring）
    else:
        todo = sorted(want - have)
        if not todo:
            log(f"[run_bc] it{it}: 语料已齐（{len(have)} shards on disk）— 跳过采集")
            return
    log(
        f"[run_bc] it{it}: 需采 {len(todo)} 局（已有 {len(have)}）stages={stages} "
        f"seeds={seeds[0]}..{seeds[-1]} wins_only={wins_only}"
    )
    sj_for = {s: course.custom_stage_payload(s) for s in stages}
    stats = dispatch_bc_corpus(
        tasks=todo,
        out_dir=data_round_dir,
        it=it,
        corpus_fp=corpus_fp,
        course_fp=course_fp,
        difficulty=course.difficulty,
        max_ticks=max_ticks,
        stage_json_for=lambda s: sj_for.get(s),
        lives_override=course.player.lives,
        player_level=course.player.level,
        wins_only=wins_only,
        near_miss_times=near_miss,
        cfg=cfg,
        task_timeout_sec=max(120.0, float(max_ticks) * 0.5 + 120.0),
        iter_suffix="-smoke" if smoke else "",
        log=log,
    )
    if stats["failed"] > 0:
        raise BcDispatchError(
            f"it{it}: {stats['failed']} 个语料任务彻底失败（重跑 run_bc 断点续跑补齐）"
        )


# ------------------------------------------------------------------ 任务发布


def publish_bc_job(
    *,
    course: BcCourseConfig,
    course_fp: str,
    corpus_fp: str,
    course_text: str,
    it: int,
    traj: Path,
    job_root: Path,
    jsonl_path: Path,
    round_name: str = "",
    log=log,
) -> dict:
    """打包 + 发布 BC job（hub/push 共用；返回归一化 manifest）。

    round_name：语料轮目录名（真轮缺省 it{it}；smoke 轮 "smoke"——采集/发布/落位
    三处必须同目录，否则发布找不到 shard）。"""
    shard_dirs = iter_bc_shard_dirs(traj, it, round_name, log=log)
    if not shard_dirs:
        raise SystemExit(
            f"[run_bc] it{it}: 无完整 BC shard（{traj / 'bc-data' / f'it{it}'} 空）——无法发布"
        )
    commit = git_head()
    code_zip_path = job_root / "code.zip"
    code_sha = pack_code_zip(NN_ROOT, code_zip_path, log=log)
    is_smoke = round_name == "smoke"
    epochs = 1 if is_smoke else int(course.train.epochs)
    batch = min(int(course.train.batch), 256) if is_smoke else int(course.train.batch)
    manifest = publish_job(
        job_root=job_root,
        jsonl_path=jsonl_path,
        run_id=RUN_ID,
        it=it,
        traj_dir=str(traj),
        shard_dirs=shard_dirs,
        commit=commit,
        code_sha256=code_sha,
        code_zip_path=code_zip_path,
        course=course_text,
        course_fp=course_fp,
        course_name=course.name,
        corpus_fp=corpus_fp,
        mode="bc",
        epochs=epochs,
        mb=batch,
        lr=float(course.train.lr),
        kind="bc",
        extra={
            "arch": str(course.train.arch),
            "val_split": float(course.train.val_split),
            "mirror_p": float(course.train.mirror_p),
            "value_coef": float(course.train.value_coef),
            "ckpt_every": int(course.train.ckpt_every),
            "notes": f"bc course={course.name} it={it} smoke={is_smoke}",
        },
        log=log,
    )
    return manifest


def _ledger_bc_epoch(jsonl_path: Path, it: int, row: dict) -> None:
    """bc_epoch 账本事件（控制台 epoch 指标面板数据源）。"""
    _append_ledger(
        jsonl_path,
        {
            "event": "bc_epoch",
            "it": int(it),
            "epoch": int(row.get("epoch", 0) or 0),
            "train_loss": row.get("train_loss"),
            "val_loss": row.get("val_loss"),
            "move_acc": row.get("move_acc"),
            "fire_acc": row.get("fire_acc"),
            "lr": row.get("lr"),
            "ts": time.time(),
        },
    )


def _run_epoch_eval(
    hub_url: str,
    token: str,
    jid: str,
    jsonl_path: Path,
    it: int,
    epoch: int,
    course: BcCourseConfig,
    cfg: dict | None,
    log=log,
) -> None:
    """从 hub resume 取该 epoch 权重快照 → 多地图干净评估 → bc_eval 账本事件。"""
    import json as _json

    from remote.hub_client import _request

    try:
        st, body = _request(hub_url, token, f"/jobs/{jid}/resume", timeout=30.0)
        if st != 200:
            log(f"[run_bc] it{it} ep{epoch}: resume 未就绪（HTTP {st}）——本边界跳过 eval")
            return
        d = _json.loads(body.decode("utf-8"))
        weights = decode_weights_json(str(d["weights"]))
        result = dispatch_bc_eval(
            weights_bytes=weights,
            eval_levels=list(course.eval.levels),
            games_per_stage=int(course.eval.games_per_stage),
            it=it,
            epoch=epoch,
            cfg=cfg,
            log=log,
        )
        _append_ledger(jsonl_path, {"event": "bc_eval", "it": int(it), **result})
    except BcEvalError as e:
        log(f"[run_bc] it{it} ep{epoch}: eval 放弃（{e}）")
    except Exception as e:  # eval 失败绝不打断训练等待
        log(f"[run_bc] it{it} ep{epoch}: eval 异常 {type(e).__name__}: {e}")


def wait_bc_round(
    *,
    hub_url: str,
    token: str,
    jid: str,
    jsonl_path: Path,
    it: int,
    course: BcCourseConfig,
    cfg: dict | None,
    wait_sec: float,
    log=log,
) -> dict:
    """BC 专用结果等待环（wait_job 语义 + 每 epoch 指标入账 + eval 边界派发）。

    404 = 正常等待；5xx/网络错误按 2 的幂退避（wait_job 同款）；轮询间隙拉
    /jobs/{id}/bc-metrics 增量行 → bc_epoch 账本事件（控制台可见）；新 epoch 命中
    `eval.every_epochs` 边界 → hub resume 权重快照 → 多地图干净评估 → bc_eval 事件。"""
    eval_cfg = course.eval
    seen_metrics = 0
    evaluated: set[int] = set()
    deadline = time.time() + wait_sec
    err_streak = 0
    poll_sec = 5.0
    poll_max_sec = 60.0
    from remote.hub_client import _request

    while time.time() < deadline:
        try:
            status, body = _request(hub_url, token, f"/jobs/{jid}/result", timeout=30.0)
        except Exception as e:
            err_streak += 1
            backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
            log(f"wait_bc_round: {jid} 轮询网络错误 ({type(e).__name__})——退避 {backoff:.0f}s")
            time.sleep(backoff)
            continue
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
            raise RuntimeError(f"wait_bc_round: job {jid} 结果非对象")
        if status == 404:
            err_streak = 0  # 还没回 = 正常排队
        elif status >= 500:
            err_streak += 1
            backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
            log(f"wait_bc_round: {jid} HTTP {status}（瞬时）——退避 {backoff:.0f}s")
            time.sleep(backoff)
            continue
        else:
            raise RuntimeError(f"wait_bc_round: HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
        # ---- job 在跑：拉每 epoch 指标增量 ----
        try:
            mstatus, mbody = _request(hub_url, token, f"/jobs/{jid}/bc-metrics", timeout=15.0)
            if mstatus == 200:
                rows = json.loads(mbody.decode("utf-8")).get("rows") or []
                new_rows = rows[seen_metrics:]
                if new_rows:
                    seen_metrics = len(rows)
                    for r in new_rows:
                        _ledger_bc_epoch(jsonl_path, it, r)
                    latest = int(rows[-1].get("epoch", 0) or 0)
                    log(f"[run_bc] it{it}: epoch 指标 +{len(new_rows)} 行（最新 ep{latest}）入账")
                    if eval_cfg.enabled:
                        for r in new_rows:
                            ep = int(r.get("epoch", 0) or 0)
                            if ep and ep % int(eval_cfg.every_epochs) == 0 and ep not in evaluated:
                                evaluated.add(ep)
                                _run_epoch_eval(
                                    hub_url, token, jid, jsonl_path, it, ep, course, cfg, log=log
                                )
        except Exception as e:
            log(f"[run_bc] WARN 指标轮询失败（不致命）: {type(e).__name__}: {e}")
        time.sleep(poll_sec)
    raise RuntimeError(f"wait_bc_round: job {jid} 超时（>{wait_sec}s）未完成")


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
    epochs = 1 if smoke else int(course.train.epochs)
    batch = min(int(course.train.batch), 256) if smoke else int(course.train.batch)
    eval_on = course.eval.enabled and not smoke
    # 本地 eval 权重源 = bc.py 的 ckpt 文件——ckpt_every 对齐 eval 边界
    ckpt_every = int(course.eval.every_epochs) if (eval_on and not smoke) else int(course.train.ckpt_every)
    cmd = [
        sys.executable,
        "-u",
        str(NN_ROOT / "train" / "bc.py"),
        "--data-dir",
        str(data_round_dir),
        "--arch",
        str(course.train.arch),
        "--out",
        str(out_weights),
        "--epochs",
        str(epochs),
        "--batch",
        str(batch),
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
        "--ckpt-every",
        str(ckpt_every),
        "--device",
        "cpu",
        "--notes",
        f"run_bc local course={course.name}",
    ]
    # 路径一律**绝对**：run_bc 本体 chdir 仓库根，而子进程 cwd=NN_ROOT——相对路径
    # 会在 nn-training/ 下找语料（2026-09-13 实测 FileNotFoundError）。
    cmd[cmd.index("--data-dir") + 1] = str(Path(cmd[cmd.index("--data-dir") + 1]).resolve())
    cmd[cmd.index("--out") + 1] = str(Path(cmd[cmd.index("--out") + 1]).resolve())
    log(f"[run_bc] local BC: {' '.join(cmd[:6])} … (epochs={epochs} batch={batch})")
    t0 = time.time()
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
    import re as _re

    out_lines: list[str] = []
    if proc.stdout is None:  # pragma: no cover — stdout=PIPE 结构性保证非空
        raise SystemExit("[run_bc] local bc.py: 拿不到子进程 stdout（不该发生）")
    _evaluated: set[int] = set()
    for raw in proc.stdout:
        line = raw.rstrip()
        out_lines.append(line)
        if line.startswith(
            ("[epoch", "[train] done", "[train] samples", "[train] majority", "[train] checkpoint")
        ):
            log(f"[bc.py] {line}")
        # 本地模式同权：epoch 行 → bc_epoch 账本；eval 边界 → ckpt 权重 → 多地图 eval
        if jsonl_path is not None and line.startswith("[epoch"):
            m = _re.match(
                r"\[epoch\s+(\d+)/(\d+)\] train_loss=([\d.]+) val_loss=([\d.]+) "
                r"acc move=([\d.]+) fire=([\d.]+)(?: value=[\d.]+ )?lr=([\d.eE+]+)",
                line,
            )
            if m:
                gepoch = int(m.group(1))
                _ledger_bc_epoch(
                    jsonl_path,
                    it,
                    {
                        "epoch": gepoch,
                        "train_loss": float(m.group(3)),
                        "val_loss": float(m.group(4)),
                        "move_acc": float(m.group(5)),
                        "fire_acc": float(m.group(6)),
                        "lr": float(m.group(7)),
                    },
                )
            if eval_on:
                m2 = _re.match(r"\[epoch\s+(\d+)/", line)
                if m2:
                    gepoch = int(m2.group(1))
                    if (
                        gepoch
                        and gepoch % int(course.eval.every_epochs) == 0
                        and gepoch not in _evaluated
                    ):
                        _evaluated.add(gepoch)
                        ckpt = Path(f"{out_weights}.ckpt.{gepoch}")
                        if ckpt.exists():
                            log(f"[run_bc] it{it} ep{gepoch}: 本地 eval（ckpt 权重）")
                            try:
                                result = dispatch_bc_eval(
                                    weights_bytes=ckpt.read_bytes(),
                                    eval_levels=list(course.eval.levels),
                                    games_per_stage=int(course.eval.games_per_stage),
                                    it=it,
                                    epoch=gepoch,
                                    cfg=cfg,
                                    log=log,
                                )
                                if jsonl_path is not None:
                                    _append_ledger(
                                        jsonl_path, {"event": "bc_eval", "it": int(it), **result}
                                    )
                            except BcEvalError as e:
                                log(f"[run_bc] it{it} ep{gepoch}: eval 放弃（{e}）")
    rc = proc.wait()
    if rc != 0:
        # 失败把尾段响亮带出（全量输出已随流中继进日志，这里只补尾部定位）
        tail = "\n".join(out_lines[-12:])
        log(f"[run_bc] local bc.py 退出码 {rc}——训练失败，子进程尾段：\n{tail}")
        raise SystemExit(f"[run_bc] local bc.py 退出码 {rc}——训练失败")
    # bc.py 落盘 versioned archive + active pointer；回读 metrics——注意 extra_meta
    # 是**顶层合并**（sizes/best_val_loss/history 都是 JSON 顶层键，weights_io.save_weights_json）。
    out_p = Path(out_weights)
    with open(out_p, encoding="utf-8") as f:
        wj = json.load(f)
    hist = wj.get("history", {})
    sizes = wj.get("sizes", {})
    metrics = {
        "epochs": epochs,
        "train_samples": int(sizes.get("train", 0) or 0),
        "val_samples": int(sizes.get("val", 0) or 0),
        "best_val_loss": float(wj.get("best_val_loss", 0.0) or 0.0),
        "move_acc": float(hist.get("move_acc", [0.0])[-1]) if hist.get("move_acc") else 0.0,
        "fire_acc": float(hist.get("fire_acc", [0.0])[-1]) if hist.get("fire_acc") else 0.0,
        "local_sec": round(time.time() - t0, 1),
    }
    return metrics


# ------------------------------------------------------------------ 主流程


def main() -> None:
    force_utf8_stdio()
    os.chdir(str(REPO_ROOT))
    ap = argparse.ArgumentParser(description="BC training orchestrator (云-HUB-LAN)")
    ap.add_argument("--course", required=True, help="BC 课程（curricula/<name>.bc.jsonc）")
    ap.add_argument(
        "--remote", action="store_true", help="发布到 per-course hub（pull 模式，云机 poll 领取）"
    )
    ap.add_argument("--local", action="store_true", help="语料就绪后本机 train/bc.py 训练")
    ap.add_argument("--smoke", action="store_true", help="冒烟：尺寸压缩真一轮，落位即作废")
    ap.add_argument("--wait-sec", type=float, default=DEFAULT_WAIT_SEC)
    # hub 传输覆盖（对齐 run_rl：显式传参压过 rl-config remote_hubs[course]；本地
    # hub_server E2E / 多 hub 实验用）
    ap.add_argument("--remote-hub-url", default="", help="hub-server base URL（覆盖 rl-config）")
    ap.add_argument("--remote-token", default="", help="bearer token（覆盖 rl-config）")
    ap.add_argument("--remote-job-root", default="", help="job 根目录（覆盖 <traj>/remote-jobs）")
    args = ap.parse_args()

    course_path = resolve_bc_course(args.course)
    course_key = course_path.name[: -len(".bc.jsonc")]
    course_bytes = course_path.read_bytes()
    course_fp = hashlib.sha256(course_bytes).hexdigest()
    course_text = course_bytes.decode("utf-8")
    course = load_bc_course(str(course_path))
    corpus_fp = bc_corpus_identity_fp(course)
    traj = Path(course.resolve_traj(course_key))
    out_weights = str(
        Path(traj / "weights.smoke.json") if args.smoke else Path(course.resolve_out(course_key))
    )
    jsonl_path = traj / "training_log.jsonl"
    job_root = Path(args.remote_job_root) if args.remote_job_root else traj / "remote-jobs"
    data_root = Path(course.resolve_data_dir(course_key))
    log(
        f"[run_bc] course={course.name} key={course_key} iters={course.iters} "
        f"traj={traj} out={out_weights} corpus_fp={corpus_fp[:12]}… "
        f"smoke={args.smoke} local={args.local}"
    )

    # ---- 单实例锁（与 run_rl 同实现、按课命名；双开响亮拒启）----
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock
    from train.loop_util import acquire_lock, cleanup_lock, course_lock_path

    lock_path = course_lock_path(str(NN_ROOT), course_key, "run_bc")
    if not _acquire_run_rl_lock(lock_path):
        raise SystemExit(
            f"[run_bc] another run_bc is running for this course "
            f"(holder pid in {lock_path}) — refusing to start"
        )

    # ---- git push 串行化（节点代码同步依赖 origin；与 run_rl 同规）----
    from rl.archive import ensure_current_branch_pushed

    _push_lock = str(REPO_ROOT / ".git_push.lock")
    if acquire_lock(_push_lock, tag="git push"):
        try:
            ensure_current_branch_pushed(REPO_ROOT)
        finally:
            cleanup_lock(_push_lock)
    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        **_POPEN_NO_WINDOW,
    ).stdout.strip()

    # ---- 传输解析：push（env/config）> hub（--remote）> local（--local）----
    import dist_common as dc

    if current_branch and current_branch != "HEAD":
        dc.set_upgrade_branch(current_branch)
    cfg = dc.load_dist_config()
    rl_block = (cfg or {}).get("rl") or {}
    push_url = str(
        os.environ.get("REMOTE_PUSH_NODE")
        or ((cfg or {}).get("courses", {}).get(course_key, {}) or {}).get("push_node_url")
        or ""
    )
    token = str(args.remote_token or rl_block.get("remote_token") or "")
    hub_url = str(
        args.remote_hub_url
        or (rl_block.get("remote_hubs") or {}).get(course_key)
        or rl_block.get("remote_hub_url")
        or ""
    )
    if args.local:
        transport = "local"
    elif push_url:
        transport = "push"
    elif args.remote and hub_url and token:
        transport = "hub"
    else:
        raise SystemExit(
            "[run_bc] 无法确定传输：--local / REMOTE_PUSH_NODE|push_node_url / "
            "--remote + rl.remote_hubs[course] 三选一（控制台 preset 会注入）"
        )
    log(f"[run_bc] transport={transport} push_url={push_url or '-'} hub_url={hub_url or '-'}")

    if transport == "hub":
        # 启动即清 hub 停机态（2026-09-12 it17 复盘同款；失败不阻断）
        from remote.hub_client import clear_halt_on_startup

        clear_halt_on_startup(hub_url, token, log=log)

    code_bytes: bytes | None = None
    try:
        done_rounds = completed_rounds(jsonl_path)
        for it in range(1, int(course.iters) + 1):
            if it in done_rounds:
                log(f"[run_bc] it{it}: 账本已有 bc_round_completed — 跳过（断点续跑）")
                continue
            # smoke 轮语料落独立目录——真轮目录（断点续跑语义）永不接触冒烟 shard
            data_round_dir = data_root / ("smoke" if args.smoke else f"it{it}")
            collect_corpus(
                course,
                course_fp,
                corpus_fp,
                it,
                data_round_dir,
                smoke=args.smoke,
                cfg=cfg,
                log=log,
            )
            if transport == "local":
                metrics = train_local_bc(
                    course,
                    data_round_dir,
                    out_weights,
                    smoke=args.smoke,
                    jsonl_path=jsonl_path,
                    it=it,
                    cfg=cfg,
                    log=log,
                )
                _archive_round(course, course_key, it, out_weights, metrics, smoke=args.smoke)
            else:
                manifest = publish_bc_job(
                    course=course,
                    course_fp=course_fp,
                    corpus_fp=corpus_fp,
                    course_text=course_text,
                    it=it,
                    traj=traj,
                    job_root=job_root,
                    jsonl_path=jsonl_path,
                    round_name=("smoke" if args.smoke else ""),
                    log=log,
                )
                jid = str(manifest["job_id"])
                if transport == "push":
                    from remote.push_client import submit_job, wait_result

                    payload_path = job_root / jid / "payload.tar.xz"
                    if code_bytes is None:
                        code_bytes = (job_root / "code.zip").read_bytes()
                    submit_job(
                        push_url,
                        token,
                        manifest,
                        payload_path.read_bytes(),
                        code_bytes,
                        log=lambda m: log(f"[push] {m}"),
                    )
                    result = wait_result(
                        push_url,
                        token,
                        jid,
                        timeout_sec=args.wait_sec,
                        log=lambda m: log(f"[push] {m}"),
                    )
                else:
                    if transport == "push":
                        log(
                            "[run_bc] push 模式暂不支持中途指标/eval 可视化（最终结果照常回传）"
                            "——需要请改 pull 模式"
                        )
                    result = wait_bc_round(
                        hub_url=hub_url,
                        token=token,
                        jid=jid,
                        jsonl_path=jsonl_path,
                        it=it,
                        course=course,
                        cfg=cfg,
                        wait_sec=args.wait_sec,
                        log=log,
                    )
                if result.get("smoke"):
                    log(f"[run_bc] it{it}: job {jid} 是冒烟回显——本轮作废（不落盘不归档）")
                    mark_job_completed(jsonl_path, jid)
                    log("BC SMOKE PASS (echo void)")
                    return
                verify_and_land_bc(
                    result,
                    manifest,
                    traj_dir=str(traj),
                    it=it,
                    out_weights=out_weights,
                    round_name=("smoke" if args.smoke else ""),
                    log=log,
                )
                mark_job_completed(jsonl_path, jid)
                metrics = dict(result.get("metrics") or {})
                _archive_round(course, course_key, it, out_weights, metrics, smoke=args.smoke)
            if not args.smoke:
                # smoke 轮账本零污染（不写完成事件——真跑同一轮不被跳过）
                _append_ledger(
                    jsonl_path,
                    {"event": "bc_round_completed", "it": it, "ts": time.time()},
                )
            prune_bc_data(traj, it)
            prune_remote_jobs(job_root)
            if args.smoke:
                log("BC SMOKE PASS")
                return
        log("[run_bc] all rounds done")
    finally:
        _cleanup_run_rl_lock(lock_path)


def _archive_round(
    course: BcCourseConfig,
    course_key: str,
    it: int,
    out_weights: str,
    metrics: dict,
    *,
    smoke: bool,
) -> None:
    """落位后归档（deliverable 5）：backup_weights + WEIGHTS.md 行。smoke 轮只落 tmp。"""
    if smoke:
        log(f"[run_bc] it{it}: smoke 轮不归档（weights 仅落 {out_weights}）")
        return
    prefix = course.resolve_backup_prefix(course_key)
    backup_dir = str(course.backup_dir) if course.backup_dir else None
    dst = backup_weights(out_weights, it, prefix=prefix, backup_dir=backup_dir)
    if dst:
        log(f"[run_bc] it{it}: weights archived -> {dst}")
        m = metrics or {}
        # 注册行进**中央** registry（nn-training/weights/WEIGHTS.md，与 bc.py 本地
        # 训练同表）——backup_dir 子目录只是归档桶，不是注册表
        append_weights_md_row(
            REPO_ROOT / "nn-training" / "weights",
            dst,
            epochs=int(m.get("epochs", 0) or 0),
            samples=(int(m.get("train_samples", 0) or 0), int(m.get("val_samples", 0) or 0)),
            best_val=float(m.get("best_val_loss", 0.0) or 0.0),
            accs=(float(m.get("move_acc", 0.0) or 0.0), float(m.get("fire_acc", 0.0) or 0.0)),
            note=f"bc {course.name} it{it}",
        )
    else:
        log(f"[run_bc] it{it}: WARN 归档失败（非致命）——权重在 {out_weights}")


if __name__ == "__main__":
    main()
