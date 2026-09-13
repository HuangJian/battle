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
    wait_job,
)
from rl.archive import backup_weights
from rl.bc_config import (
    BcCourseConfig,
    bc_corpus_identity_fp,
    load_bc_course,
    resolve_bc_course,
    round_seeds,
)
from rl.bc_dispatch import BcDispatchError, dispatch_bc_corpus, landed_pairs
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
    """补齐本轮语料：断点续跑过滤 → bc_dispatch 并发派发 → 无 shard 则响亮失败。"""
    seeds = round_seeds(course, it)
    stages = course.stage_ids
    want = {(s, seed) for s in stages for seed in seeds}
    have = landed_pairs(data_round_dir)
    todo = sorted(want - have)
    if not todo:
        log(f"[run_bc] it{it}: 语料已齐（{len(have)} shards on disk）— 跳过采集")
        return
    max_ticks = int(course.max_ticks)
    wins_only = bool(course.corpus.wins_only)
    near_miss = int(course.corpus.near_miss_times)
    if smoke:
        ov = smoke_overrides(course)
        max_ticks = ov["max_ticks"]
        wins_only = ov["wins_only"]
        near_miss = ov["near_miss_times"]
        seeds = seeds[:1]
        todo = sorted({(s, seeds[0]) for s in stages} - have)
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
    course_key: str,
    course_fp: str,
    corpus_fp: str,
    course_text: str,
    it: int,
    traj: Path,
    job_root: Path,
    jsonl_path: Path,
    smoke: bool,
    log=log,
) -> dict:
    """打包 + 发布 BC job（hub/push 共用；返回归一化 manifest）。"""
    shard_dirs = iter_bc_shard_dirs(traj, it, log=log)
    if not shard_dirs:
        raise SystemExit(
            f"[run_bc] it{it}: 无完整 BC shard（{traj / 'bc-data' / f'it{it}'} 空）——无法发布"
        )
    commit = git_head()
    code_zip_path = job_root / "code.zip"
    code_sha = pack_code_zip(NN_ROOT, code_zip_path, log=log)
    epochs = 1 if smoke else int(course.train.epochs)
    batch = min(int(course.train.batch), 256) if smoke else int(course.train.batch)
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
            "notes": f"bc course={course.name} it={it} smoke={smoke}",
        },
        log=log,
    )
    _ = course_key
    return manifest


def train_local_bc(
    course: BcCourseConfig,
    data_round_dir: Path,
    out_weights: str,
    *,
    smoke: bool,
    log=log,
) -> dict:
    """--local：本机子进程 train/bc.py（训练机 venv torch；run_bc 本身保持 torch-free）。"""
    epochs = 1 if smoke else int(course.train.epochs)
    batch = min(int(course.train.batch), 256) if smoke else int(course.train.batch)
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
        "--device",
        "cpu",
        "--notes",
        f"run_bc local course={course.name}",
    ]
    log(f"[run_bc] local BC: {' '.join(cmd[:6])} … (epochs={epochs} batch={batch})")
    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=str(NN_ROOT), timeout=6 * 3600, **_POPEN_NO_WINDOW
    )
    if proc.returncode != 0:
        raise SystemExit(f"[run_bc] local bc.py 退出码 {proc.returncode}——训练失败")
    # bc.py 落盘 versioned archive + active pointer；回读 metrics（sizes/best_val）
    out_p = Path(out_weights)
    with open(out_p, encoding="utf-8") as f:
        wj = json.load(f)
    meta = wj.get("meta", {})
    hist = meta.get("history", {})
    metrics = {
        "epochs": epochs,
        "train_samples": int(meta.get("sizes", {}).get("train", 0) or 0),
        "val_samples": int(meta.get("sizes", {}).get("val", 0) or 0),
        "best_val_loss": float(meta.get("best_val_loss", 0.0) or 0.0),
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
    job_root = traj / "remote-jobs"
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
    token = str(rl_block.get("remote_token") or "")
    hub_url = str((rl_block.get("remote_hubs") or {}).get(course_key) or rl_block.get("remote_hub_url") or "")
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
            data_round_dir = data_root / f"it{it}"
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
                    course, data_round_dir, out_weights, smoke=args.smoke, log=log
                )
                _archive_round(course, course_key, it, out_weights, metrics, smoke=args.smoke)
            else:
                manifest = publish_bc_job(
                    course=course,
                    course_key=course_key,
                    course_fp=course_fp,
                    corpus_fp=corpus_fp,
                    course_text=course_text,
                    it=it,
                    traj=traj,
                    job_root=job_root,
                    jsonl_path=jsonl_path,
                    smoke=args.smoke,
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
                    result = wait_job(
                        hub_url, token, jid, timeout_sec=args.wait_sec, log=log
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
        append_weights_md_row(
            Path(dst).parent,
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
