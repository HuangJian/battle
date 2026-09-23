"""bc_loop —— BC（行为克隆）课程引擎：让**单进程 supervisor** 也能带 BC 课（R3-4）。

**为什么需要它**：`run_bc.py` 原本是「一个进程服务一门 BC 课程」的脚本（`main()` 里一大段
procedural 编排），而多课程并行之后训练侧收敛为**一个进程**（`rl/loop_serve.py` 的
`run_rl_cluster.py --serve`，2026-09-19）。BC 课要进那个 supervisor，缺的不是调度器（R2c 已好），
而是**一个能被它驱动的引擎**——本模块就是那个引擎，并把编排器拆成两层：

| 层 | 归属 | 干什么 |
|---|---|---|
| 引擎（本模块） | `rl/bc_loop.py` | 一轮怎么跑（采集/发布 → 等回传 → 落位归档）、账本指针语义、收官 |
| 入口（薄壳） | `run_bc.py` | 只做进程级一次性准备（utf8/chdir/git push）+ 解析 + 建引擎 + **阻塞式**驱动 |

**一轮被切成三段（可重入）**：`run_one_round(it)` 每次最多做一件事就返回——

1. `ROUND_WAIT`：这一段**没完**（已发布、正在等 GPU 回传；或本机训练还没跑完）⇒ 调度器把这门课
   放下、去跑别的课程，过一会儿回来问同一轮；
2. `ROUND_NEXT`：这一轮**真的完了**（账本已写 `bc_round_completed`）；
3. `ROUND_SMOKE_STOP`：冒烟轮（真跑一轮但落位即作废）。

**两条不可交易的性质**（都有用例钉住）：

- **重入不重发布**：jid 先从内存会话取，取不到就扫盘（`find_round_job`）。重发 job = 新 jid =
  bc-resume 失效 = **从头训**（`DEFAULT_WAIT_SEC` 的注释里记着这条代价，那是 GPU 时间的白烧）。
- **让位不改 task 语义**：只有「等远端」这一段是让位点；本机训练（`--local`）与 push 直推照旧
  阻塞（它们的墙钟花在本机/邻居节点上，没有可让的余地）。这跟 RL 的 `eval_join` **刻意不进让位表**
  是同一条纪律：让位点必须落在「本机没在干活」的那一段上。

BC 的轮指针与 RL **不同源**（`bc_round_completed` vs `iteration`），读法归 `rl/bc_ledger.py` 一份，
本模块与只读计划视图（`rl/loop_plan.py`）都走它。
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
from dataclasses import dataclass, field
from pathlib import Path

from common.protocol import decode_weights_json
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from remote.hub_client import (
    git_head,
    iter_bc_shard_dirs,
    mark_job_completed,
    pack_code_zip,
    publish_job,
    verify_and_land_bc,
)
from rl.archive import backup_weights
from rl.bc_config import (
    BC_SUFFIX,
    BcCourseConfig,
    bc_corpus_identity_fp,
    load_bc_course,
    resolve_bc_course,
    round_seeds,
)
from rl.bc_dispatch import BcDispatchError, dispatch_bc_corpus, landed_pairs
from rl.bc_eval import BcEvalError, dispatch_bc_eval
from rl.bc_ledger import ROUND_DONE_EVENT, append_ledger, bc_progress, completed_jobs
from rl.log import log
from rl.loop_round import ROUND_NEXT, ROUND_SMOKE_STOP, ROUND_WAIT, RoundOutcome
from rl.queue import REPO_ROOT

#: nn-training 目录（入口路径/子进程 cwd 都相对它——与 `run_bc.py` 的 `NN_ROOT` 同一个）。
NN_ROOT = REPO_ROOT / "nn-training"

#: per-job 等待上限（秒）。**0 = 无上限**（2026-09-14 用户定案）。
#: 为什么默认不设上限：超时中断的代价不是"少训一点"——重跑会 publish **新 job**
#: （新 jid），而 bc-resume 按 jid 存（hub `/jobs/{jid}/resume` 与 worker 本地
#: `bc-resume/<jid>`）⇒ 新 job 取不到旧进度 ⇒ **从头训**，那一轮 GPU 时间白烧。
#: 唯一真实需要上限的场景是"云机始终没起来"——那个交给 wait 的
#: 「无进展告警」兜底（而不是砍掉一条正常在训的 job）。
DEFAULT_WAIT_SEC = 0.0

#: bc-data 轮目录保留数（磁盘有界性；PPO 侧 keep_iters 同语义）
BC_DATA_KEEP = 3

#: 无进展告警阈值（秒）：等 job 时超过这么久没有新 epoch 入账 ⇒ 提示检查云机 worker（一次）。
IDLE_WARN_SEC = 900.0

#: job 结果轮询与退避（秒）——与 `wait_job` 同款。
POLL_SEC = 5.0
POLL_MAX_SEC = 60.0

#: 本进程的 runId（账本 `run_start` 分段锚；进程级，故所有课程共享同一个 == 与单课程时一致）。
RUN_ID = f"bc-{secrets.token_hex(8)}"

# ---- 归档 / 磁盘有界 --------------------------------------------------------


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


# ---- 语料采集 --------------------------------------------------------------


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
        tripped = stats.get("failed_nodes") or []
        raise BcDispatchError(
            f"it{it}: {stats['failed']} 个语料任务彻底失败（重跑 run_bc 断点续跑补齐）"
            + (
                f"；已熔断节点：{'、'.join(tripped)}——先 /v1/ping 看该节点 codeHash"
                " 与节点侧日志，再决定 /v1/update 或停用"
                if tripped
                else ""
            )
        )


# ---- 任务发布 --------------------------------------------------------------


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
    # D14 血缘过滤：与云端逐 shard 拒收同判据（protocol.d14_corpus_match）——
    # 异血缘 shard 不进 payload（2026-09-20 事故：混入即整份被云退回）。
    shard_dirs = iter_bc_shard_dirs(
        traj, it, round_name, log=log, course_fp=course_fp, corpus_fp=corpus_fp
    )
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
        extra=bc_job_extra(course, it, smoke=is_smoke),
        log=log,
    )
    return manifest


def bc_job_extra(course: BcCourseConfig, it: int, *, smoke: bool = False) -> dict:
    """BC job manifest 的 `extra`（worker 侧消费：arch/val_split/mirror_p/value_coef/
    ckpt_every/train_seed/fire_pos_weight/notes）。

    抽成纯函数是为了让单测锁住「**原值直传**」这条纪律：2026-09-14 事故 ——
    `fire_pos_weight` 曾被写成 `float(course.train.fire_pos_weight)`，而课程值允许
    `"auto"` ⇒ `float("auto")` 直接 ValueError、BC 启动即崩。同理 `train_seed` 必须
    int()（worker 用 int() 消费），其余数值键照旧收窄。
    """
    return {
        "arch": str(course.train.arch),
        "val_split": float(course.train.val_split),
        "mirror_p": float(course.train.mirror_p),
        "value_coef": float(course.train.value_coef),
        "ckpt_every": int(course.train.ckpt_every),
        # 训练种子（2026-09-14）：课程 `train.seed` 显式下发到云端 job ——
        # ① 同课程重跑可复现；② R1（v2/v3 obs 对照）两臂能同 seed ⇒ 同 val 划分
        # ⇒ val_loss 可比。键名用 `train_seed` 而非 `seed`：manifest 里 `seed` 已有
        # hex per-job 占位的历史口径（tests/test_bc_epoch_e2e.py fixture），不复用。
        "train_seed": int(course.train.seed),
        # fire 头正例权重（2026-09-14）：语料 fire 正例仅 ~7%，不补偿则 fire_acc
        # 低于"永不发射"常数基线（实测 0.770 < 0.927）。**原值直传**：数字或 "auto"
        # （由 worker → train/bc.py::resolve_fire_pos_weight 解析）。
        "fire_pos_weight": course.train.fire_pos_weight,
        "notes": f"bc course={course.name} it={it} smoke={smoke}",
    }


def bc_run_start_event(course: BcCourseConfig, course_key: str, run_id: str = "") -> dict:
    """`run_start` 事件载荷（2026-09-14 分段锚）。

    console 的 epoch/eval 面板按「最后一次 run_start 之后」过滤 bc_epoch / bc_eval
    （`dashboard/src/server/api/ledger.ts::bcRowsFromLedgerTail`）。为什么需要它：同一份
    training_log.jsonl 会被多轮复用（traj 不变、只换语料口径），两轮数据同挂 `it=1`
    —— 不分段就会被拼成一条曲线（实测：上一轮 59 行 + 本轮 150 行）。
    抽成纯函数以便单测锁住字段（console 侧依赖 `event` 与 `runId`）。
    """
    return {
        "event": "run_start",
        "runId": run_id or RUN_ID,
        "course": course_key,
        "iters": int(course.iters),
        "epochs": int(course.train.epochs),
        "seed": int(course.train.seed),
        "fire_pos_weight": course.train.fire_pos_weight,
        "ts": time.time(),
    }


# ---- 账本（事件写入；读法在 rl/bc_ledger.py） -------------------------------


def ledger_bc_epoch(jsonl_path: Path, it: int, row: dict) -> None:
    """bc_epoch 账本事件（控制台 epoch 指标面板数据源）。"""
    append_ledger(
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


def run_epoch_eval(
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
        append_ledger(jsonl_path, {"event": "bc_eval", "it": int(it), **result})
    except BcEvalError as e:
        log(f"[run_bc] it{it} ep{epoch}: eval 放弃（{e}）")
    except Exception as e:  # eval 失败绝不打断训练等待
        log(f"[run_bc] it{it} ep{epoch}: eval 异常 {type(e).__name__}: {e}")


# ---- 等待（非阻塞探针 + 阻塞包装） -----------------------------------------

#: 探针结论：`ready`（结果已到）/ `pending`（正常排队中）/ `transient`（网络错或 5xx，退避重问）。
READY = "ready"
PENDING = "pending"
TRANSIENT = "transient"


@dataclass
class BcWait:
    """一次「等某个 job 回传」的会话（阻塞版 `wait_bc_round` 与让位版 `BcLoop` **共用记账**）。

    为什么是对象而不是循环里的局部变量：等待带三份跨调用必须保留的记账 —— 已入账的 epoch 数
    （防重复写账本/重复派 eval）、上次有进展的时刻（无进展告警）、错误连击（退避）。单进程
    supervisor 下「等」会被切成「让位 → 过一会儿回来问」，那三份记账必须活过这个断裂。
    """

    #: 在等的 job（结果按 jid 回家，所以它是这段等待的身份）。
    jid: str
    #: 已入账的 epoch 行数（增量游标）。
    seen_metrics: int = 0
    #: 上一次有进展（有新 epoch 入账）的时刻。
    last_progress: float = field(default_factory=time.time)
    #: 无进展告警是否已打（只打一次，避免刷屏）。
    idle_warned: bool = False
    #: 连续网络错/5xx 次数（退避用；404 = 正常排队，清零）。
    err_streak: int = 0

    def backoff_sec(self) -> float:
        """瞬时错误后的退避（2 的幂，封顶 `POLL_MAX_SEC`）。"""
        factor = float(2 ** max(self.err_streak - 1, 0))
        return min(POLL_SEC * factor, POLL_MAX_SEC)

    def poll_once(
        self,
        *,
        hub_url: str,
        token: str,
        jsonl_path: Path,
        it: int,
        course: BcCourseConfig,
        cfg: dict | None,
        logger=log,
    ) -> tuple[str, dict | None]:
        """问一次结果（**不睡、不重试、不对「还没好」抛错**）。

        分类与 hub 上的 RL 探针同规（`probe_job_result`）：`ready` / `pending`(404) /
        `transient`(网络错、5xx)；**其余状态码与坏结果抛错**——「还没好」与「永远好不了」
        必须分开，混在一起就是一个静默挂死的等待环。

        `_request` **函数内导入**（原 `run_bc.wait_bc_round` 同款）：它是 hub_client 的私有
        助手，按名字导入会把绑定钉死在 import 时刻——测试/故障注入靠 `monkeypatch`
        `hub_client._request` 换掉整条 HTTP 链路，顶层导入会让补丁失效（症状：单测真去连
        hub 并挂满超时）。
        """
        from remote.hub_client import _request

        try:
            status, body = _request(hub_url, token, f"/jobs/{self.jid}/result", timeout=30.0)
        except Exception as e:
            self.err_streak += 1
            logger(
                f"wait_bc_round: {self.jid} 轮询网络错误 ({type(e).__name__})"
                f"——退避 {self.backoff_sec():.0f}s"
            )
            return TRANSIENT, None
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                return READY, loaded
            raise RuntimeError(f"wait_bc_round: job {self.jid} 结果非对象")
        if status == 404:
            self.err_streak = 0  # 还没回 = 正常排队
        elif status >= 500:
            self.err_streak += 1
            logger(
                f"wait_bc_round: {self.jid} HTTP {status}（瞬时）——退避 {self.backoff_sec():.0f}s"
            )
            return TRANSIENT, None
        else:
            raise RuntimeError(
                f"wait_bc_round: HTTP {status}: {body[:200].decode('utf-8', 'replace')}"
            )
        # ---- job 在跑：拉每 epoch 指标增量（控制台可见）+ eval 边界 ----
        ingest_bc_metrics(
            self,
            hub_url=hub_url,
            token=token,
            jsonl_path=jsonl_path,
            it=it,
            course=course,
            cfg=cfg,
            logger=logger,
        )
        return PENDING, None


def ingest_bc_metrics(
    wait: BcWait,
    *,
    hub_url: str,
    token: str,
    jsonl_path: Path,
    it: int,
    course: BcCourseConfig,
    cfg: dict | None,
    logger=log,
) -> int:
    """拉一次 job 的每 epoch 指标增量 → `bc_epoch` 账本事件 + eval 边界（返回新游标）。

    失败**不抛**（指标/评估是观测面，绝不打断训练等待）；无进展超时只告警一次。
    """
    from remote.hub_client import _request  # 同 `poll_once`：可被 monkeypatch 替换

    eval_cfg = course.eval
    try:
        mstatus, mbody = _request(hub_url, token, f"/jobs/{wait.jid}/bc-metrics", timeout=15.0)
        if mstatus == 200:
            rows = json.loads(mbody.decode("utf-8")).get("rows") or []
            new_rows = rows[wait.seen_metrics :]
            if new_rows:
                wait.seen_metrics = len(rows)
                wait.last_progress = time.time()
                wait.idle_warned = False
                for r in new_rows:
                    ledger_bc_epoch(jsonl_path, it, r)
                latest = int(rows[-1].get("epoch", 0) or 0)
                logger(f"[run_bc] it{it}: epoch 指标 +{len(new_rows)} 行（最新 ep{latest}）入账")
                if eval_cfg.enabled:
                    for r in new_rows:
                        ep = int(r.get("epoch", 0) or 0)
                        if ep and ep % int(eval_cfg.every_epochs) == 0:
                            run_epoch_eval(
                                hub_url, token, wait.jid, jsonl_path, it, ep, course, cfg, logger
                            )
    except Exception as e:
        logger(f"[run_bc] WARN 指标轮询失败（不致命）: {type(e).__name__}: {e}")
    # 无上限模式下唯一需要人介入的情形：云机没起来 ⇒ 永远等不到 epoch。
    # 只提示一次（避免刷屏），进程继续等。
    if not wait.idle_warned and time.time() - wait.last_progress > IDLE_WARN_SEC:
        wait.idle_warned = True
        logger(
            f"[run_bc] ⚠ job {wait.jid} 已 {int(IDLE_WARN_SEC / 60)} 分钟无新 epoch 入账"
            "——确认云机 GPU worker 是否已启动并连上 hub："
            "`python -m remote.worker --poll <hub_url> --token <token> --device cuda`"
        )
    return wait.seen_metrics


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
    """BC 专用结果等待环（**阻塞包装**：内部是 `BcWait.poll_once` + 退避；让位版见 `BcLoop`）。

    404 = 正常等待；5xx/网络错误按 2 的幂退避（wait_job 同款）；轮询间隙拉
    /jobs/{id}/bc-metrics 增量行 → bc_epoch 账本事件（控制台可见）；新 epoch 命中
    `eval.every_epochs` 边界 → hub resume 权重快照 → 多地图干净评估 → bc_eval 事件。

    `wait_sec <= 0` = **无上限**（默认，见 DEFAULT_WAIT_SEC）。超时是**错误**（不是"算了"）：
    重发 job 会让 bc-resume 失效 ⇒ 从头训。

    单进程 supervisor 驱动的 BC 课**不走本函数**（它一次只问一句就把执行权让出去）——
    两者共用 `BcWait`/`poll_once`，所以轮询语义只有一份。
    """
    wait = BcWait(jid=jid)
    deadline = None if wait_sec <= 0 else time.time() + wait_sec
    while deadline is None or time.time() < deadline:
        status, result = wait.poll_once(
            hub_url=hub_url,
            token=token,
            jsonl_path=jsonl_path,
            it=it,
            course=course,
            cfg=cfg,
            logger=log,
        )
        if status == READY:
            return result or {}
        time.sleep(wait.backoff_sec() if status == TRANSIENT else POLL_SEC)
    raise RuntimeError(f"wait_bc_round: job {jid} 超时（>{wait_sec}s）未完成")


# ---- 本机训练 --------------------------------------------------------------


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
    ckpt_every = (
        int(course.eval.every_epochs) if (eval_on and not smoke) else int(course.train.ckpt_every)
    )
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
        "--fire-pos-weight",
        str(course.train.fire_pos_weight),
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
                ledger_bc_epoch(
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
                                    append_ledger(
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


# ---- 传输裁决 --------------------------------------------------------------


def resolve_transport(
    *,
    local: bool,
    mode: str,
    remote: bool,
    push_url: str,
    hub_url: str,
    token: str,
) -> str:
    """BC 传输裁决（纯函数，2026-09-15 对齐 run_rl）→ 'local' | 'push' | 'hub'。

    优先级「push（env `REMOTE_PUSH_NODE`，冒烟预演）> hub」；2026-09-19 起**不再**读课程
    `push_node_url`（课程与节点正交），所以非冒烟场景下 auto 恒落 `hub`（发布到 hub、等
    worker 领取）。显式 `--remote-transport push|pull` 仍可钉死（headless 用）。

    非法组合响亮 SystemExit（不静默回落）。
    """
    if local or mode == "local":
        return "local"
    if mode == "pull":
        if not (remote and hub_url and token):
            raise SystemExit(
                "[run_bc] --remote-transport pull 需要 --remote + hub_url + token"
                "（本地 hub：控制台 local preset 注入本机 hub）"
            )
        return "hub"
    if mode == "push":
        if not push_url:
            raise SystemExit(
                "[run_bc] --remote-transport push 但没有 push 节点（REMOTE_PUSH_NODE 为空）"
            )
        return "push"
    if mode != "auto":
        raise SystemExit(f"[run_bc] 未知 --remote-transport {mode!r}")
    if push_url:
        return "push"
    if remote and hub_url and token:
        return "hub"
    raise SystemExit(
        "[run_bc] 无法确定传输：--local / REMOTE_PUSH_NODE / "
        "--remote + rl.remote_hub_url 三选一（控制台共享 trainer 走 --remote + hub）"
    )


# ---- 课程运行时（解析一次；main 与 serve 共用） -----------------------------


@dataclass
class BcRuntime:
    """一门 BC 课程在**本进程**里的全部解析结果（`run_bc.py` 与 supervisor 共用一份）。

    「解析」= 课程文件 → 语料身份 → 路径派生 → 传输裁决 → hub/push 地址。**只算一次**：
    这些值在一轮之内不会变，重算只会引入「两处解析得出两个答案」的可能（例如 mid-run
    编辑课程文件）。
    """

    course: BcCourseConfig
    #: 课程键（文件名的 stem；账本/归档/lock 都用它）。
    course_key: str
    course_path: Path
    #: 课程文件 sha256（D14 血缘：job 与 shard 靠它认亲）。
    course_fp: str
    #: 课程文件全文（随 job 下发，worker 侧重建输入）。
    course_text: str
    #: 语料身份指纹（env+reward 解析值哈希）。
    corpus_fp: str
    traj: Path
    #: 落位权重路径（`verify_and_land_bc` 原子写；smoke 轮 = `weights.smoke.json`）。
    out_weights: str
    jsonl_path: Path
    job_root: Path
    data_root: Path
    #: `hub` / `push` / `local`（见 `resolve_transport`）。
    transport: str
    hub_url: str
    push_url: str
    token: str
    #: 课程声明的轮数（0 = 不限）。
    iters: int
    smoke: bool
    wait_sec: float
    #: 本进程 runId（`run_start` 分段锚；与 `RUN_ID` 同值，便于测试注入）。
    run_id: str = ""
    #: 远端配置快照（bc_dispatch 派发语料时要传给节点）。
    cfg: dict | None = None


def resolve_bc_runtime(
    args: argparse.Namespace, *, cfg: dict | None = None, run_id: str = ""
) -> BcRuntime:
    """BC CLI 参数 → `BcRuntime`（解析链**只此一份**；与改造前 `main()` 的启动段逐条同义）。

    `cfg` 注入是为了让 supervisor 复用同一份 dist 配置（缺省自己读 rl-config.json）；
    `push_url` 只认 **env `REMOTE_PUSH_NODE`**（2026-09-19 起不再读课程 `push_node_url`：
    课程与 worker 节点正交，见 `loop_steps._gpu_push_nodes` 同一条口径）。
    """
    course_path = resolve_bc_course(args.course)
    course_key = course_path.name[: -len(BC_SUFFIX)]
    course_bytes = course_path.read_bytes()
    course_fp = hashlib.sha256(course_bytes).hexdigest()
    course_text = course_bytes.decode("utf-8")
    course = load_bc_course(str(course_path))
    corpus_fp = bc_corpus_identity_fp(course)
    traj = Path(course.resolve_traj(course_key))
    out_weights = str(
        Path(traj / "weights.smoke.json") if args.smoke else Path(course.resolve_out(course_key))
    )
    if cfg is None:
        import dist_common as dc

        cfg = dc.load_dist_config()
    rl_block = (cfg or {}).get("rl") or {}
    # 直推目标只从 env 来（冒烟预演注入本机的伪节点）。真实的 push 执行面走 hub 中介派发：
    # BC 课在共享 trainer 里没有 env，resolve_transport(auto) 因而落在 `hub`（发布到 hub、
    # 等 worker 领取）——与「所有 worker 都可能接到任何课程的活」同一个模型。
    push_url = str(os.environ.get("REMOTE_PUSH_NODE") or "")
    token = str(args.remote_token or rl_block.get("remote_token") or "")
    # 共享 hub（2026-09-18）：URL 是**全局**事实（一条隧道/一个 hub 服务所有课程）⇒
    # 只认单键 `rl.remote_hub_url`（python 侧与 run_rl 同口径）。旧 per-course 的
    # `rl.remote_hubs[<课>]` 不再读：留着它会把 BC 课程指向一个已不存在的每课隧道。
    hub_url = str(args.remote_hub_url or rl_block.get("remote_hub_url") or "")
    transport = resolve_transport(
        local=bool(args.local),
        mode=str(getattr(args, "remote_transport", "auto") or "auto"),
        remote=bool(args.remote),
        push_url=push_url,
        hub_url=hub_url,
        token=token,
    )
    return BcRuntime(
        course=course,
        course_key=course_key,
        course_path=course_path,
        course_fp=course_fp,
        course_text=course_text,
        corpus_fp=corpus_fp,
        traj=traj,
        out_weights=out_weights,
        jsonl_path=traj / "training_log.jsonl",
        job_root=Path(args.remote_job_root) if args.remote_job_root else traj / "remote-jobs",
        data_root=Path(course.resolve_data_dir(course_key)),
        transport=transport,
        hub_url=hub_url,
        push_url=push_url,
        token=token,
        iters=int(course.iters),
        smoke=bool(args.smoke),
        wait_sec=float(getattr(args, "wait_sec", DEFAULT_WAIT_SEC) or 0.0),
        run_id=run_id or RUN_ID,
        cfg=cfg,
    )


def bc_argparser(
    description: str = "BC training orchestrator (云-HUB-LAN)",
) -> argparse.ArgumentParser:
    """BC 课程的参数表（**唯一一份**：`run_bc.py` 的 CLI 与 supervisor 的开课都走它）。"""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--course", required=True, help="BC 课程（curricula/<name>.bc.jsonc）")
    ap.add_argument(
        "--remote", action="store_true", help="发布到共享 hub（pull 模式，云机 poll 领取）"
    )
    ap.add_argument("--local", action="store_true", help="语料就绪后本机 train/bc.py 训练")
    ap.add_argument("--smoke", action="store_true", help="冒烟：尺寸压缩真一轮，落位即作废")
    ap.add_argument(
        "--wait-sec",
        type=float,
        default=DEFAULT_WAIT_SEC,
        help="单 job 等待上限（秒）；0 = 无上限（默认——超时重发 job 会让 bc-resume 失效、从头训）",
    )
    # hub 传输覆盖（对齐 run_rl：显式传参压过 rl-config remote_hubs[course]；本地
    # hub_server E2E / 多 hub 实验用）
    ap.add_argument("--remote-hub-url", default="", help="hub-server base URL（覆盖 rl-config）")
    ap.add_argument("--remote-token", default="", help="bearer token（覆盖 rl-config）")
    ap.add_argument("--remote-job-root", default="", help="job 根目录（覆盖 <traj>/remote-jobs）")
    ap.add_argument(
        "--remote-transport",
        default="auto",
        choices=("auto", "pull", "push", "local"),
        help="传输裁决（对齐 run_rl，2026-09-15）：auto=env REMOTE_PUSH_NODE 直推否则 hub；"
        "pull=强制走 hub（等 worker 自己来领）；push=强制直推 push 节点（无节点则响亮失败）；"
        "local=本机 train/bc.py（同 --local）",
    )
    return ap


def bc_course_args(course: str, argv: list[str] | None = None) -> argparse.Namespace:
    """课程 stem → 生效 BC args（**与 `run_bc.py --course <stem>` 同一份解析**）。

    supervisor 按课程表开课，故这里拒绝 `--course`（避免「课程表里的课」与「argv 里的课」
    两个来源打架——与 `rl/loop_serve.py::course_args` 同一条纪律）。
    """
    extra = list(argv or [])
    if any(a == "--course" or a.startswith("--course=") for a in extra):
        raise SystemExit("[bc] 课程由课程列表给出，不要在附加参数里再传 --course")
    return bc_argparser().parse_args([*extra, "--course", course])


# ---- 盘上的 job（重入/重启后**不重发布**） ----------------------------------


def find_round_job(
    job_root: Path, it: int, *, completed_jids: set[str] | None = None, log=log
) -> tuple[str, dict] | None:
    """盘上找**这一轮已发布、还没收口**的 job → `(jid, manifest)`；没有则 None。

    为什么必须找：BC 的续训按 jid 存（hub `/jobs/{jid}/resume` 与 worker 本地
    `bc-resume/<jid>`）⇒ **重发 = 新 jid = 取不到旧进度 = 从头训**。所以「让位后再来问」
    与「进程重启后续跑」都必须先认领已有的那份 job。

    多个候选（历史失败尝试的残留）⇒ 取 manifest mtime 最新的一份，其余**响亮**记一行
    （不做静默回收：那些 job 可能仍在某台云机上跑着）。
    """
    done = completed_jids or set()
    cands: list[tuple[float, str, dict]] = []
    try:
        entries = sorted(Path(job_root).glob("*/manifest.json"))
    except OSError:
        return None
    for mp in entries:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or int(m.get("it", -1)) != int(it):
            continue
        if str(m.get("kind", "")) != "bc":
            continue
        jid = str(m.get("job_id") or mp.parent.name)
        if jid in done:
            continue
        try:
            mt = mp.stat().st_mtime
        except OSError:
            mt = 0.0
        cands.append((mt, jid, m))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if len(cands) > 1:
        others = ", ".join(j for _t, j, _m in cands[1:])
        log(
            f"[run_bc] it{it}: 盘上有 {len(cands)} 个未收口 bc job，按 manifest 时间取最新 "
            f"{cands[0][1]}（其余不回收，可能仍在云机上跑：{others}）"
        )
    return cands[0][1], cands[0][2]


# ---- 收官 / 归档 -----------------------------------------------------------


def finish_all_rounds(
    jsonl_path: str | Path,
    iters: int,
    *,
    hub_url: str = "",
    token: str = "",
    log=lambda _m: None,
) -> None:
    """全轮完成的收尾（2026-09-14）——三件事：

    1. 打含 `ALL DONE` 的**尾行**：console 的 exit-watchdog 用
       `tailNormalCompletion`（日志尾行 includes('ALL DONE')，大小写敏感）判「正常完成」。
       原来只打小写 `all rounds done` ⇒ BC 正常跑完被标成「TrainingLoop 意外退出——
       非正常退出」红告警（实测 2026-09-14 07:32，训练其实已全部成功归档）。
    2. 落 `run_complete` 账本事件（RL 侧 `loop_core._park_after_completion` 同款）：
       console 的「✅ 训练已完成」info 横幅由账本尾行派生。
    3. **停机操作**：向本课 hub 下发停机达令（用户 2026-09-14 定案「任务完成后执行
       停机提示/操作」）。效力说明（不粉饰）：
         · 对**已在轮询、当前无 job 可领**的 pull worker：达令随 job 下发，此刻无 job
           ⇒ 它收不到、不会因此停机。这类 worker 的真实释放姿势 = 云机侧 `--once`
           （跑完即退出）或人工关机，日志里明确提示。
         · 对**此后**领取本课 job 的 worker：达令生效（不再空烧配额）。
         · 同时让 hub 的停机态与「训练已结束」一致（`/admin/workers/halt` 是
           console 停机横幅的同一端点），避免"训练结束但 hub 仍是运行态"的漂移。

    BC 完成即退出进程（不学 RL 的 parking：BC 无 idle 期评估业务），故收敛在以上三件。
    """
    if hub_url and token:
        try:
            from remote.hub_client import set_cloud_halt

            if set_cloud_halt(hub_url, token, True, log=log):
                log("[run_bc] 停机操作已执行：hub 停机达令下发成功（本课不再需要 worker）")
            else:
                log("[run_bc] 停机操作未生效：hub 不可达/拒绝（不阻断收尾）")
        except Exception as e:  # 停机链路永不阻断收尾
            log(f"[run_bc] 停机操作异常（不阻断收尾）: {type(e).__name__}: {e}")
    else:
        log("[run_bc] 停机操作跳过：非 hub 传输（local/push 无 hub 可下发）")
    log(
        "[run_bc] 云机可释放：本轮语料与训练 job 均已结束，本课程不会再派 job"
        "（云机侧用 `--once` 可在处理完一个 job 后自动退出；否则请直接关机）"
    )
    try:
        from rl.events import write_run_complete

        write_run_complete(
            Path(jsonl_path),
            int(iters),
            int(iters),
            f"BC 全轮完成（it{iters}/{iters}），weights 已落位归档",
        )
    except Exception as e:  # 落账失败只影响 console 横幅派生，不改变训练结果
        log(f"[run_bc] run_complete 落账失败（仅 console 横幅派生缺失）: {e}")
    # ⚠️ `ALL DONE` 必须是**最后一行**（2026-09-14 实测踩坑）：console 的
    # `exit-watchdog.tailNormalCompletion` 只取日志的**最后一个非空行**判断是否正常完成
    # ——把它写在停机提示之前 ⇒ 尾行是"云机可释放…" ⇒ 明明跑完仍被标「意外退出」
    # （第一版就是这样：10:38:30 完成、10:38:35 console 依旧弹红告警）。
    log("[run_bc] ALL DONE — 全轮完成，weights 已落位归档")


def archive_round(
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


# ---- 引擎（supervisor 与 main 共用一份实现） -------------------------------


class BcLoop:
    """BC 课程引擎：把「一轮」切段成**可重入**的三步，让 supervisor 能在等 GPU 时让位。

    与 `rl.loop_core.TrainingLoop` 对齐的**子集**（serve 只依赖这些）：
    `_setup()` · `run_one_round(it)` · `finish_course(it)` · `release_torch()` ·
    `ledger_next_it(fallback)`（指针语义归引擎，见 `LoopRunner._ledger_next_it`），
    外加读面用的 `inflight_job_id(it)`。

    ★ **本类不做进程级准备**（git push / chdir / 锁 / 日志重定向）：那些在 `run_bc.main()`
    与 `rl/loop_serve.py` 里各做一次（进程级副作用的归属必须显式，重复做会付两份代价）。
    """

    def __init__(
        self,
        runtime: BcRuntime,
        *,
        log=log,
        now=time.time,
        sleep=time.sleep,
    ) -> None:
        self.runtime = runtime
        self.log = log
        self.now = now
        self.sleep = sleep
        #: 本进程已发布的 job（`it → (jid, manifest)`）——重入时先用它，避免重发布。
        self._jobs: dict[int, tuple[str, dict]] = {}
        #: 每轮的等待会话（跨「让位-再来问」保留 epoch 游标与告警状态）。
        self._waits: dict[int, BcWait] = {}
        #: push 直推已提交的轮（提交是内存态：重启后需要重提交，与 RL 的「换节点重提交」同规）。
        self._submitted: set[int] = set()
        self._setup_done = False
        #: 共用的 args 视图（`LoopRunner` 依赖 `loop.args.traj`；`iters` 由调用方另传）。
        self.args = argparse.Namespace(
            traj=str(runtime.traj),
            iters=int(runtime.iters),
            smoke=bool(runtime.smoke),
            course_name=runtime.course.name,
            # RL 侧的读面字段（`LoopRunner`/`loop_plan` 只读 traj，这里补齐以免 AttributeError）
            out=runtime.out_weights,
            mode="bc",
        )

    # ---- 生命周期 ------------------------------------------------------

    def _setup(self) -> None:
        """首次执行前的一次性动作：账本**分段锚**（`run_start`）。

        为什么在 `_setup` 而不是构造时：supervisor 会「开课但暂时没活」（账本指针已到末轮），
        那种课不该在账本里留一条 run_start（它会让控制台的 epoch 面板把上一轮的曲线截断）。
        冒烟轮**不写**（冒烟不得污染真轮的账本语义）。
        """
        if self._setup_done:
            return
        self._setup_done = True
        if not self.runtime.smoke:
            append_ledger(
                self.runtime.jsonl_path,
                bc_run_start_event(
                    self.runtime.course, self.runtime.course_key, self.runtime.run_id
                ),
            )

    def release_torch(self) -> None:
        """本机 torch 栈的释放点（BC 侧是**空操作**：本机训练走 `sys.executable` 子进程）。

        显式实现而不是不写：引擎池的驱逐路径会调它，缺了会被 `getattr` 静默跳过——那时
        「BC 课不需要释放」与「忘了实现」在日志里长得一样。
        """
        return None

    def _ensure_local_ppo_stack(self) -> None:
        """RL 的惰性本机栈钩子（BC 无本机 PPO 栈）——同 `release_torch`，显式空操作。"""
        return None

    # ---- 读面 ----------------------------------------------------------

    def ledger_next_it(self, fallback: int = 1) -> int:
        """下一轮 = 账本里第一个**未完成**的轮（`bc_round_completed` 是唯一依据）。"""
        return bc_progress(self.runtime.jsonl_path, self.runtime.iters).next_it

    def inflight_job_id(self, it: int) -> str | None:
        """这门课此刻在等的 job（控制台「在等什么」的那一列靠它点名）。"""
        got = self._jobs.get(it)
        return got[0] if got else None

    # ---- 一轮（可重入） --------------------------------------------------

    def run_one_round(self, it: int) -> RoundOutcome:
        """跑一轮的一段：最多做一件事就返回（`ROUND_WAIT` = 还没完，`ROUND_NEXT` = 真的完了）。"""
        rt = self.runtime
        self._setup()
        if it in bc_progress(rt.jsonl_path, rt.iters).completed:
            # 账本已结算（重放/重入/别的进程刚跑完）⇒ 不重做，也不重复写账本。
            return RoundOutcome(ROUND_NEXT, it)
        got = self._inflight_or_start(it)
        if got is None:  # 本机训练：整轮已在本机做完
            return RoundOutcome(ROUND_NEXT, it)
        jid = got[0]
        manifest = got[1]
        wait = self._waits.get(it)
        if wait is None or wait.jid != jid:
            wait = self._waits[it] = BcWait(jid=jid)
        status, result = wait.poll_once(
            hub_url=rt.hub_url,
            token=rt.token,
            jsonl_path=rt.jsonl_path,
            it=it,
            course=rt.course,
            cfg=rt.cfg,
            logger=self.log,
        )
        if status != READY:
            return RoundOutcome(
                ROUND_WAIT, it, detail=f"等远端 BC job 回传：bc@{it}（jid={jid[:12]}）"
            )
        if (result or {}).get("smoke"):
            # 对端冒烟回显（占位 worker）：本轮作废，不落盘不归档。
            mark_job_completed(rt.jsonl_path, jid)
            self.log(f"[run_bc] it{it}: job {jid} 是冒烟回显——本轮作废（不落盘不归档）")
            self.log("BC SMOKE PASS (echo void)")
            self._waits.pop(it, None)
            return RoundOutcome(ROUND_SMOKE_STOP, it)
        self._land(it, jid, manifest, result or {})
        self._waits.pop(it, None)
        if rt.smoke:
            # 真跑完的冒烟轮：落位即作废（权重只落 `weights.smoke.json`、不归档、不写完成
            # 事件），打完 `BC SMOKE PASS` 就收工——与改造前 `main()` 的 `if args.smoke: return`
            # 同义（差别是这里只结束**这门课**，不退进程）。
            return RoundOutcome(ROUND_SMOKE_STOP, it)
        return RoundOutcome(ROUND_NEXT, it)

    def _inflight_or_start(self, it: int) -> tuple[str, dict] | None:
        """认领本轮已有的 job（内存 → 盘上），没有就开一轮（采集/发布 或 本机训练）。

        返回 `(jid, manifest)`；`None` = 本机训练路径（整轮已在这一次调用里做完）。
        """
        rt = self.runtime
        got = self._jobs.get(it)
        if got is not None:
            jid, manifest = got
            if rt.transport == "push" and it not in self._submitted:
                self._submit_push(it, jid)
            return jid, manifest
        # 冒烟轮**不认领盘上 job**：它的语料口径/尺寸都被压缩过，复用等于拿冒烟语料冒充真语料。
        if not rt.smoke:
            found = find_round_job(
                rt.job_root, it, completed_jids=completed_jobs(rt.jsonl_path), log=self.log
            )
            if found is not None:
                jid, manifest = found
                self._jobs[it] = found
                self.log(
                    f"[run_bc] it{it}: 盘上已有未收口 job {jid[:12]}——**不重发布**"
                    "（重发会让 bc-resume 失效、从头训）"
                )
                if rt.transport == "push":
                    self._submit_push(it, jid)
                return jid, manifest
        return self._start_round(it)

    def _start_round(self, it: int) -> tuple[str, dict] | None:
        """开一轮：采集语料（幂等）→ 本机训练 **或** 发布远端 job。"""
        rt = self.runtime
        data_round_dir = rt.data_root / ("smoke" if rt.smoke else f"it{it}")
        collect_corpus(
            rt.course,
            rt.course_fp,
            rt.corpus_fp,
            it,
            data_round_dir,
            smoke=rt.smoke,
            cfg=rt.cfg,
            log=self.log,
        )
        if rt.transport == "local":
            metrics = train_local_bc(
                rt.course,
                data_round_dir,
                rt.out_weights,
                smoke=rt.smoke,
                jsonl_path=rt.jsonl_path,
                it=it,
                cfg=rt.cfg,
                log=self.log,
            )
            self._close_round(it, metrics, smoke=rt.smoke)
            return None
        manifest = publish_bc_job(
            course=rt.course,
            course_fp=rt.course_fp,
            corpus_fp=rt.corpus_fp,
            course_text=rt.course_text,
            it=it,
            traj=rt.traj,
            job_root=rt.job_root,
            jsonl_path=rt.jsonl_path,
            round_name=("smoke" if rt.smoke else ""),
            log=self.log,
        )
        jid = str(manifest["job_id"])
        self._jobs[it] = (jid, manifest)
        if rt.transport == "push":
            self._submit_push(it, jid)
            if rt.smoke:
                self.log(
                    "[run_bc] push 模式暂不支持中途指标/eval 可视化（最终结果照常回传）"
                    "——需要请改 pull 模式"
                )
        self.log(f"[run_bc] it{it}: 已发布 bc job {jid[:12]} via {rt.transport}——等回传")
        return jid, manifest

    def _submit_push(self, it: int, jid: str) -> None:
        """直推（`--remote-transport push`）：发布即提交；**一进程内每轮只提交一次**。

        重启/换进程后重新提交：提交是内存态（无法从盘上判断节点是否已收下），而
        「再提交一次」的代价远小于「以为提交过、其实没有 ⇒ 永远等不到回传」。
        """
        rt = self.runtime
        from remote.push_client import submit_job

        payload_path = rt.job_root / jid / "payload.tar.xz"
        code_zip = rt.job_root / "code.zip"
        manifest = self._jobs[it][1] if it in self._jobs else {}
        submit_job(
            rt.push_url,
            rt.token,
            manifest,
            payload_path.read_bytes(),
            code_zip.read_bytes(),
            log=lambda m: self.log(f"[push] {m}"),
        )
        self._submitted.add(it)

    def _land(self, it: int, jid: str, manifest: dict, result: dict) -> None:
        """落位 + 归档 + 账本（顺序与改造前 `main()` 逐条同义）。"""
        rt = self.runtime
        verify_and_land_bc(
            result,
            manifest,
            traj_dir=str(rt.traj),
            it=it,
            out_weights=rt.out_weights,
            round_name=("smoke" if rt.smoke else ""),
            log=self.log,
        )
        mark_job_completed(rt.jsonl_path, jid)
        self._close_round(it, dict(result.get("metrics") or {}), smoke=rt.smoke)

    def _close_round(self, it: int, metrics: dict, *, smoke: bool) -> None:
        """一轮收口：归档 → 账本完成事件 → 磁盘有界性（冒烟轮只落 tmp、不写完成事件）。"""
        rt = self.runtime
        archive_round(rt.course, rt.course_key, it, rt.out_weights, metrics, smoke=smoke)
        if not smoke:
            append_ledger(
                rt.jsonl_path, {"event": ROUND_DONE_EVENT, "it": int(it), "ts": time.time()}
            )
        prune_bc_data(rt.traj, it)
        prune_remote_jobs(rt.job_root)
        if smoke:
            self.log("BC SMOKE PASS")

    def finish_course(self, it: int) -> None:
        """全轮跑完的收尾（`ALL DONE` 尾行 + `run_complete` + 本课 hub 停机达令）。

        **不**做进程退出（单进程 supervisor 还要服务别的课）——退出策略归调用方。
        """
        rt = self.runtime
        if rt.smoke:
            return  # 冒烟轮不写收官事件（账本零污染）
        finish_all_rounds(
            rt.jsonl_path,
            int(rt.iters),
            hub_url=rt.hub_url if rt.transport == "hub" else "",
            token=rt.token,
            log=self.log,
        )

    # ---- 阻塞式驱动（单课程入口 `run_bc.py` 用；supervisor 走自己的调度环） ----

    def run_blocking(self) -> None:
        """单课程阻塞驱动：等外部时**原地重问**（无让位对象），语义与改造前的 for 循环一致。"""
        rt = self.runtime
        it = 1
        while rt.iters <= 0 or it <= rt.iters:
            outcome = self.run_one_round(it)
            if outcome.status == ROUND_WAIT:
                self.sleep(POLL_SEC)  # 阻塞语义：本进程只服务这一门课，就地等
                it = outcome.it
                continue
            if outcome.status == ROUND_SMOKE_STOP:
                return
            if outcome.status == ROUND_NEXT:
                it = outcome.it + 1
                continue
            return
        self.finish_course(it)
