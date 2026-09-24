"""remote/worker.py — 云端 PPO worker（无状态、可重连；`python -m remote_worker`）。

循环语义（D1/D8/D12）：轮询 hub-server → 下载 payload → 解包 → 校验
（payload_sha256 / commit / mode）→ 复用 ppo backend 链路
（load_episodes → chunk_episodes → ppo_update）→ 同 commit 调
`save_weights_json` 产出 weights_json（D12/G1 产出方锁死）→ `_ppo_save`
打 tar（model/opt/RNG，D5）→ POST 结果。断线重连 / 幂等重拉由本模块自理。

所有 torch 依赖**延迟到 run_job 内**导入——本模块顶层零 torch（hub 侧
（hub_server / hub_client）与协议单测均不拉 torch；云端才真正加载）。

确定性（D5）：per-job 种子 = hash(runId, it, init_weights_fp)，load/chunk/update
前重新播种——同 job 重发 chunk 逐字节一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

from common.protocol import (
    PAYLOAD_NAME,
    CodeChangedError,
    ProtocolError,
    RetryableError,
    encode_opt_tar,
    encode_weights_json,
    normalize_manifest,
    pack_result_v2,
    validate_result,
)
from common.protocol import (
    d14_corpus_match as protocol_d14_corpus_match,
)

# BC 作业（S4 第六步之二 → `remote/bc_job.py`）：**显式转发**（e2e / tests 直接 import
# 这些名字，见该模块头部；本组无 monkeypatch 接缝）。
from remote.bc_job import (
    _bc_device as _bc_device,
)
from remote.bc_job import (
    _bc_fetch_resume as _bc_fetch_resume,
)
from remote.bc_job import (
    _bc_load_local_resume as _bc_load_local_resume,
)
from remote.bc_job import (
    _bc_local_resume_dir as _bc_local_resume_dir,
)
from remote.bc_job import (
    _bc_post_epoch as _bc_post_epoch,
)
from remote.bc_job import (
    _bc_store_local_resume as _bc_store_local_resume,
)
from remote.bc_job import (
    _run_bc_job as _run_bc_job,
)
from remote.bc_job import (
    normalize_ppo_device as normalize_ppo_device,
)
from remote.bc_job import (
    resolve_bc_seed as resolve_bc_seed,
)

# 下载簇（S4 第七刀 → `remote/download.py`）：**显式转发**。宿主（run_job / _prefetch_fill）
# 经这些转发名调用 ⇒ patch `remote.worker.download_*` 仍有效；但**组内互调**（`_resolve_blob`
# → `download_blob`）解析在本模块 ⇒ 那类测试要 patch `remote.download`。
from remote.download import (
    _cache_blob as _cache_blob,
)
from remote.download import (
    _ensure_ts_code as _ensure_ts_code,
)
from remote.download import (
    _progress_logger as _progress_logger,
)
from remote.download import (
    _resolve_blob as _resolve_blob,
)
from remote.download import (
    download_blob as download_blob,
)
from remote.download import (
    download_code as download_code,
)
from remote.download import (
    download_payload as download_payload,
)
from remote.download import (
    download_ts_code as download_ts_code,
)
from remote.http import (
    _POLL_WARN_AT as _POLL_WARN_AT,
)

# HTTP 传输核心（S4 第五步 → `remote/http.py`）：**显式转发**，见该模块头部。
# 注入点分档：patch 后调宿主函数的仍 patch `remote.worker`；patch 后调
# `_get_with_retry` / `_read_body` 的必须 patch `remote.http`。
from remote.http import (
    BODY_CHUNK as BODY_CHUNK,
)
from remote.http import (
    BODY_IDLE_TIMEOUT_SEC as BODY_IDLE_TIMEOUT_SEC,
)
from remote.http import (
    BODY_PROGRESS_MIN_SEC as BODY_PROGRESS_MIN_SEC,
)
from remote.http import (
    BODY_TOTAL_TIMEOUT_SEC as BODY_TOTAL_TIMEOUT_SEC,
)
from remote.http import (
    _get_opener as _get_opener,
)
from remote.http import (
    _get_with_retry as _get_with_retry,
)
from remote.http import (
    _opener as _opener,
)
from remote.http import (
    _read_body as _read_body,
)
from remote.http import (
    _request as _request,
)
from remote.http import (
    _sched_headers as _sched_headers,
)
from remote.http import (
    _warn_non_200 as _warn_non_200,
)
from remote.iter_rollout import run_iter_rollout

# 作业工作区 / 产物落盘 / TAR / git 物化（S4 第六步 → `remote/job_fs.py`）：**显式转发**。
# 本组无 monkeypatch 接缝（全仓都是直接调用）⇒ 转发即够。
from remote.job_fs import (
    JOB_DIR_KEEP as JOB_DIR_KEEP,
)
from remote.job_fs import (
    _git_head as _git_head,
)
from remote.job_fs import (
    _persist_result as _persist_result,
)
from remote.job_fs import (
    pack_opt_tar as pack_opt_tar,
)
from remote.job_fs import (
    prune_job_dirs as prune_job_dirs,
)
from remote.job_fs import (
    unpack_opt_tar as unpack_opt_tar,
)
from remote.job_fs import (
    unpack_payload_or_fail as unpack_payload_or_fail,
)

# 作业取活 / 生命周期 / 回传面（S4 第八刀 → `remote/job_lifecycle.py`）：**显式转发**。
# 宿主（`run_job` / `worker_loop` / `_prefetch_fill`，仍在本模块）经这些转发名调用 ⇒
# patch `remote.worker.X` 照旧有效；但**簇内互调**（`acquire_job` → `peek_jobs` /
# `request_priority` / `claim_job` / `_priority_rank`、`start_cancel_watcher` → `job_status`、
# `report_job_failure` → `worker_tag`）与本簇直调 `_request` / `_wire_add` 的调用点解析在
# `remote.job_lifecycle` ⇒ 那类测试必须 patch **该模块**（见该模块头部与
# `tests/test_job_lifecycle_split.py`）。
from remote.job_lifecycle import (
    _failure_detail as _failure_detail,
)
from remote.job_lifecycle import (
    _priority_rank as _priority_rank,
)
from remote.job_lifecycle import (
    abandon_job as abandon_job,
)
from remote.job_lifecycle import (
    acquire_job as acquire_job,
)
from remote.job_lifecycle import (
    claim_job as claim_job,
)
from remote.job_lifecycle import (
    heartbeat as heartbeat,
)
from remote.job_lifecycle import (
    job_body_error as job_body_error,
)
from remote.job_lifecycle import (
    job_ready as job_ready,
)
from remote.job_lifecycle import (
    job_started as job_started,
)
from remote.job_lifecycle import (
    job_status as job_status,
)
from remote.job_lifecycle import (
    peek_jobs as peek_jobs,
)
from remote.job_lifecycle import (
    post_result as post_result,
)
from remote.job_lifecycle import (
    release_job as release_job,
)
from remote.job_lifecycle import (
    report_job_failure as report_job_failure,
)
from remote.job_lifecycle import (
    request_priority as request_priority,
)
from remote.job_lifecycle import (
    start_cancel_watcher as start_cancel_watcher,
)
from remote.job_lifecycle import (
    worker_tag as worker_tag,
)

# 每 job 一轮（S4 第十刀 → `remote/job_round.py`）：宿主**只** import `run_one_round`
# （`RoundOutcome` 的字段只在 worker_loop 里读，不给本模块添转发面）。宿主在调用点读
# `run_job` 这个**值**注入（`run_job_fn=run_job`）⇒ `W.run_job` 的 patch 仍生效；
# 本簇其余名字（`job_ready` / `abandon_job` / `release_job` / `report_job_failure` /
# `start_cancel_watcher` / `peek_jobs` / `download_payload` / `PREFETCH_ROUND_SEC`）的解析
# 已随块搬到 `remote.job_round` ⇒ 拦截它们要 patch **该模块**（见其头部与
# `tests/test_job_round_split.py`）。
from remote.job_round import run_one_round
from remote.prefetch import (
    PREFETCH_DEPTH_DEFAULT,
    PrefetchStore,
)
from remote.result_upload import (
    RESULT_UPLOAD_MODE_DEFAULT,
    RESULT_UPLOAD_MODES,
    ResultUploader,
)
from remote.train_core import (
    run_training_core as run_training_core,
)

# 传输账 / 低速重抽 / bulk 节流（S4 拆分 → `remote/wire.py`）。这里是**显式转发**：
# 状态与实现同住 `wire`，避免模块全局在拆分后变成两份账（见 `remote/wire.py` 头部）。
# `_BEST_RATE` 是重绑式的会话标量 —— 它的注入点是 `remote.wire._BEST_RATE`。
from remote.wire import (
    _BEST_RATE as _BEST_RATE,
)
from remote.wire import (
    _BULK as _BULK,
)
from remote.wire import (
    _WIRE as _WIRE,
)
from remote.wire import (
    WIRE_MAX_JOBS as WIRE_MAX_JOBS,
)
from remote.wire import (
    WIRE_MIN_RATE as WIRE_MIN_RATE,
)
from remote.wire import (
    WIRE_PROBE_BYTES as WIRE_PROBE_BYTES,
)
from remote.wire import (
    WIRE_PROBE_SEC as WIRE_PROBE_SEC,
)
from remote.wire import (
    WIRE_RATE_SAMPLE_MIN_BYTES as WIRE_RATE_SAMPLE_MIN_BYTES,
)
from remote.wire import (
    WIRE_REROLL_BUDGET_SEC as WIRE_REROLL_BUDGET_SEC,
)
from remote.wire import (
    WIRE_REROLL_MAX as WIRE_REROLL_MAX,
)
from remote.wire import (
    WireSlowError as WireSlowError,
)
from remote.wire import (
    _bulk_pace as _bulk_pace,
)
from remote.wire import (
    _min_rate as _min_rate,
)
from remote.wire import (
    _note_rate as _note_rate,
)
from remote.wire import (
    _reroll_decision as _reroll_decision,
)
from remote.wire import (
    _wire_add as _wire_add,
)
from remote.wire import (
    _wire_block as _wire_block,
)
from remote.wire import (
    _wire_bucket as _wire_bucket,
)
from remote.wire import (
    _wire_flush as _wire_flush,
)
from remote.wire import (
    _wire_hit as _wire_hit,
)
from remote.wire import (
    _wire_note_reroll as _wire_note_reroll,
)
from remote.wire import (
    _wire_start as _wire_start,
)
from remote.wire import (
    _wire_time as _wire_time,
)
from remote.wire import (
    set_bulk_log as set_bulk_log,
)
from remote.worker_proc import (
    HOT_RELOAD_EXIT as HOT_RELOAD_EXIT,
)
from remote.worker_proc import (
    _release_cloud_machine as _release_cloud_machine,
)
from remote.worker_proc import (
    _request_reload as _request_reload,
)
from remote.worker_proc import (
    supervise_worker as supervise_worker,
)

# ------------------------------------------------------------------ PPO 执行


# D14 比对规则的**唯一实现**住 `common.protocol`（发布端 `hub_client.iter_shard_dirs`
# 打包时用同一条规则挑选 shard）——这里只做名字转发，保持既有 import/调用面不变。
d14_corpus_match = protocol_d14_corpus_match


def run_job(
    base_url: str,
    token: str,
    job: dict,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    echo: bool = False,
    preloaded: dict | None = None,
    code_cache_dir: Path | None = None,
    ts_code_cache_dir: Path | None = None,
    lease_token: str = "",
    # ---- 半离线（kind=run；2026-09-17）产物目录与本次预算 ----
    # artifacts_dir：产物根（缺省按 Kaggle /kaggle/working / Colab Drive / 工作目录自动解析）。
    # run_max_iters / run_budget_sec：本次自主段的额外上限（0 = 只认计划）——后者是
    # Kaggle 会话到期前「干净停机」的把手。
    artifacts_dir: str | Path | None = None,
    run_max_iters: int = 0,
    run_budget_sec: float = 0.0,
    # ---- 调度面（2026-09-22，plan/transfer-scheduling R2-5）----
    # `should_cancel()`：每 **epoch 边界** 问一次「结果是不是已经 landed」——是则抛
    # `JobCancelledError`（停算丢弃，零回传/零 fail）。不在 chunk 内层加回调（用户拍板：
    # 不值得为 15s→1s 去动 `ppo_update` 内层结构）。
    # `on_ppo_start()`：PPO 真正启动前一刻打点（hub 把它记成 `computing_at` = 掉队阈值
    # 的**唯一**时基；下载/解包/权重装载都已完成的那个瞬间）。
    should_cancel: Any = None,
    on_ppo_start: Any = None,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> dict:
    """执行单个 job：下载 → 校验 → PPO → 产出 weights_json + opt tar → POST。

    preloaded（push 模式，2026-09-05）：{"payload_zip": bytes, "code_zip"?: bytes,
    "ts_code_zip"?: bytes}——HUB 把 payload/code 随 POST /job 直接上传（worker_server），
    跳过下载步骤；code 仍走 sha 缓存目录（code_zip 缺省时要求本地缓存已命中或
    服务器自行处理）。ts_code_zip 同规（M3 kind=iter 专用，其余 job 恒缺席）。

    echo=True（冒烟）：下载/校验全走，但不拉 torch 不跑 PPO——init 权重原样回传
    并带 smoke 标记（消费方作废本轮）；hub-start --smoke-only 的 Kaggle 交互预演。

    返回 hub 侧需要的 result dict（未 POST——调用方决定上传时机；本函数
    负责完成 PPO 与产物）。幂等：已完成（result 已落盘）的 job 由 hub 返回
    404/409，调用方跳过。
    """
    jid = job["job_id"]
    manifest = normalize_manifest(job["manifest"])

    # ---- 结果复用（2026-09-05）：上次已算完但回传失败 → 本地缓存直接重传，不重算 PPO ----
    cached_path = work_dir / jid / "_result.json"
    if cached_path.exists():
        try:
            cached: dict = json.loads(cached_path.read_text(encoding="utf-8"))
            validate_result(cached, manifest, commit_echo_must_match=False)
            log(f"job {jid}: 复用上次算完的结果（上次回传失败）——直接重传")
            return cached
        except Exception:
            pass  # 缓存缺失/跨 manifest/损坏 → 清场走全流程

    # ---- payload 来源（D1：sha256 校验防截断/损坏，两条路径同规） ----
    # M0 统一计量：payload 大小与拿到它的墙钟（push = 随 POST body 抵达，下载耗时归 hub；
    # pull = 真下载时间）。其余拆分（unpack/opt_restore/grad）各自包在下面。
    t_dl = time.time()
    if preloaded is not None and "payload_zip" in preloaded:
        raw = preloaded["payload_zip"]
        log(f"job {jid}: payload from push ({len(raw)} bytes)")
        payload_dl_sec = 0.0
    else:
        raw = download_payload(base_url, token, jid)
        payload_dl_sec = round(time.time() - t_dl, 3)
        log(f"job {jid}: payload downloaded ({len(raw)} bytes in {payload_dl_sec:.1f}s)")
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        # 传输损坏属瞬时故障：重下即可修复（RetryableError → 释放租约立即重领重下）
        raise RetryableError("payload_sha256 不匹配——传输损坏（重下可修复）")

    job_dir = work_dir / jid
    if job_dir.exists():
        from platform_utils import rmtree_best_effort

        rmtree_best_effort(job_dir)
    job_dir.mkdir(parents=True)
    # 磁盘：本 job 之后最多留 JOB_DIR_KEEP 个目录（放开头 = 失败轮也照样清理）
    prune_job_dirs(work_dir, JOB_DIR_KEEP, log=log)
    zip_path = job_dir / PAYLOAD_NAME
    zip_path.write_bytes(raw)
    # zip 内 manifest 是占位副本，解包仅取 shard 目录；权威校验全走 job 记录 manifest。
    # init_weights.json / opt_init.tar.b64 与 shard 目录同落 job_dir 根（解包天然如此）。
    t_unpack = time.time()
    # 解包失败 = 内容决定性失败（走 ProtocolError → 确定性上报，不重认领）——见 §4 事故。
    _unused_manifest, shard_dirs = unpack_payload_or_fail(zip_path, job_dir)
    unpack_sec = round(time.time() - t_unpack, 3)

    # ---- commit 校验：下载 code.zip 解压到 sys.path（替代 git 同步，D6） ----
    # 云端 worker 不再依赖 git checkout，而是使用 hub 启动时打包的代码快照。
    # ---- code.zip 内容寻址缓存（2026-09-05）：同 sha 只下载/解压一次 ----
    # code 在多次迭代间通常不变（sha 只由源码内容决定，pack 侧时间戳已固定化），
    # 缓存命中即省一次隧道下载 + 解压。缓存目录按 sha 隔离，tmp 原子改名防半截。
    # 多课程共享 worker（P3b C3）：code_cache 留共享根（按 sha 内容寻址，跨课复用），
    # 只有 job 目录按源分区——调用方经 code_cache_dir 传入共享根。
    code_root = code_cache_dir if code_cache_dir is not None else work_dir / "code_cache"
    #: M2 B3 blob 缓存根（跨课共享，同 code_cache 约定；键 = raw sha256）。
    blob_root = code_root.parent / "blob_cache"
    code_cache_dir = code_root / manifest["code_sha256"]
    if code_cache_dir.exists():
        sys.path.insert(0, str(code_cache_dir))
        _wire_hit(jid, "code")  # 零字节命中也要进本 job 的传输账（否则摘要读数失真）
        log(f"job {jid}: code cache 命中（{manifest['code_sha256'][:12]}…）——跳过下载解压")
    else:
        if preloaded is not None and "code_zip" in preloaded:
            code_raw = preloaded["code_zip"]
        else:
            code_raw = download_code(base_url, token, jid)
        if hashlib.sha256(code_raw).hexdigest() != manifest["code_sha256"]:
            # per-job 快照 sha 与 manifest 对账：不匹配 = 传输损坏（瞬时，重下可修复）
            raise RetryableError("code_sha256 不匹配——传输损坏（重下可修复）")
        import zipfile

        code_extract_tmp = code_root / (manifest["code_sha256"] + ".tmp")
        code_extract_tmp.mkdir(parents=True, exist_ok=True)
        code_zip_path = job_dir / "code.zip"
        code_zip_path.write_bytes(code_raw)
        with zipfile.ZipFile(code_zip_path) as zf:
            zf.extractall(code_extract_tmp)
        code_cache_dir.parent.mkdir(parents=True, exist_ok=True)
        code_extract_tmp.rename(code_cache_dir)
        sys.path.insert(0, str(code_cache_dir))
        log(
            f"job {jid}: code.zip unpacked ({len(code_raw)} bytes, "
            f"{len(list(code_cache_dir.rglob('*.py')))} .py files) -> sys.path[0]"
        )

    # ---- 热替换护栏（2026-09-11）：本进程已 import 的代码版本必须 == 本 job 要求 ----
    # sys.path.insert 只影响**尚未导入**的模块；已进 sys.modules 的 ppo/rl 不会重读。
    # 不查就会用旧代码跑出新结果：零报错、commit_echo 也照样回显，只有 sha 能揭穿。
    global _ACTIVE_CODE_SHA
    _job_sha = str(manifest["code_sha256"])
    if _ACTIVE_CODE_SHA is None:
        _ACTIVE_CODE_SHA = _job_sha
        log(f"job {jid}: 本进程加载代码 sha={_job_sha[:12]}…（后续 job 若变化将自重启）")
    elif _job_sha != _ACTIVE_CODE_SHA:
        raise CodeChangedError(_ACTIVE_CODE_SHA, _job_sha)

    # ---- 设备归一化（**必须在 kind 分叉之前**）----
    # BC 与 PPO 两条链都要用 device，且都要能接住未经解析的 "auto"：push-first 引导在
    # `notebook_runtime.resolve_device()` 之前就把 worker spawn 了（只做
    # `device_resolved or device` 兜底），字面量 "auto" 会一路传进来。
    # 只归一化一次、放在分叉前，两条链共用 —— 2026-09-15 两次事故的教训：
    # 第一次只改了 PPO 的分派条件（else 仍 `torch.device(device)`），第二次才发现
    # BC 分叉也是直接透传原始 device。
    _raw_dev = str(device or "cpu").strip().lower()
    device = normalize_ppo_device(device)
    if device != _raw_dev:
        log(
            f"job {jid}: --device {_raw_dev!r} 未经解析，worker 兜底为 {device}"
            "（调用方应先 resolve_device；要多卡须显式 --device cuda-dp）"
        )

    # ---- kind 分叉（BC 整合，plan/bc-cloud-integration.plan.md §3）：bc 任务走独立
    # 执行链（语料 npy shard 训练，无 reward/γ/λ/init-weights 语义）；ppo 任务继续
    # 走下方 per-tick 红线 + PPO 链路（默认行为零变化）。
    if str(manifest["kind"]) == "bc":
        return _run_bc_job(
            jid=jid,
            manifest=manifest,
            job_dir=job_dir,
            shard_dirs=shard_dirs,
            device=device,
            torch_threads=torch_threads,
            echo=echo,
            base_url=base_url,
            token=token,
            lease_token=lease_token,
            log=log,
        )

    # ---- M3 kind 分叉：iter = 一整轮上云（节点自己跑 rollout 产 shard）----
    #
    # 位置很关键：必须在 mode 红线 / D14 / echo **之前**——因为本轮真正要校验的 shard
    # 是刚跑出来的（payload 里没有 shard），D14 必须看到它们；echo 轮则整个跳过 rollout
    # （回显冒烟不跑任何重计算，只验 payload/code/ts_code 三样传输）。
    iter_info: dict | None = None
    ts_code_bytes = 0
    ts_code_hit = False
    # ---- 半离线（kind=run）：先把计划接过来校验（**跑第一局之前**）----
    # 三道门（sha / 形状 / 全段对集指纹）都在 `remote/plan_run.verify_plan_file` 里；失败 =
    # 计划与 hub 侧不一致，此时不跑任何一局，也不写任何产物。
    #
    # 为什么是 `plan_run` 而不是 `run_loop`：执行引擎已下沉到 L2（`worker` 在它上面），
    # 而 `plan_run` **不 import worker**——「一轮怎么跑」由我们**注入自己**（`run_job_fn=run_job`，
    # 见下面 run_plan_job 的尾巴）。这条注入把原先 `run_loop ⇄ worker` 的延迟环拆掉了
    # （`tests/helpers/remote_dag.py` 的 DEFERRED_CYCLES 因此为空）。仍然是**函数内**延迟
    # import：保持本模块顶层的 import 面不变。
    run_plan: dict | None = None
    run_plan_sha = ""
    if str(manifest["kind"]) == "run":
        from remote.plan_run import verify_plan_file

        run_plan, run_plan_sha = verify_plan_file(job_dir, manifest, log=log)
    if str(manifest["kind"]) in ("iter", "run"):
        ts_root = (
            ts_code_cache_dir
            if ts_code_cache_dir is not None
            else code_root.parent / "ts_code_cache"
        )
        _ts_dir, ts_code_bytes, ts_code_hit = _ensure_ts_code(
            base_url, token, jid, manifest, ts_root=ts_root, preloaded=preloaded, log=log
        )
        if echo:
            log(f"job {jid}: kind=iter + echo——跳过 rollout（只验传输链）")
        else:
            iter_info = run_iter_rollout(
                job_dir, manifest["rollout"], ts_dir=_ts_dir, log=log
            )
            shard_dirs = list(iter_info["shard_dirs"])

    # ---- mode 红线（v1：仅 per-tick） ----
    if manifest["mode"] != "per-tick":
        raise ProtocolError(f"mode={manifest['mode']!r} != per-tick——远程 v1 红线，拒收")

    # ---- D14 语料血缘：job.course_fp == 每个 shard 的 manifest.course_fp ----
    # （跨课程语料绝不混训——发布端已保证 shard 集按 course_fp 过滤，这里再校验一次）
    _cfp = str(manifest["course_fp"])
    _corpus = str(manifest.get("corpus_fp", "") or "")
    for _sd in shard_dirs:
        try:
            with open(os.path.join(_sd, "manifest.json"), encoding="utf-8") as _f:
                _sm = json.load(_f)
        except (OSError, ValueError) as _e:
            raise ProtocolError(f"D14 course_fp: 读 shard manifest 失败 {_sd}: {_e}") from _e
        if not d14_corpus_match(_cfp, _corpus, _sm):
            raise ProtocolError(
                f"D14 course_fp 不匹配：job={_cfp[:12]}… shard={str(_sm.get('course_fp'))[:12]}… "
                f"（{_sd}）——跨课程语料混入，拒收"
            )

    # ---- 冒烟回显（--echo，2026-09-05）：不拉 torch、不跑 PPO——把 payload 携带的
    # init 权重与指纹原样回传为 job 结果（hub-start --smoke-only 的 Kaggle 交互预演，
    # 用户拍板：真课程 + 作废轮，不建虚拟课程）。三重校验按构造必过
    # （init_weights_fp/data_fp/commit_echo 均为 manifest 回显）；消费方
    # （rl/loop_steps._remote_ppo）见 result["smoke"] 作废本轮（it 不前进）。
    if echo:
        init_w = job_dir / "init_weights.json"
        if not init_w.exists():
            raise ProtocolError("payload 缺 init_weights.json——echo 冒烟无法回显")
        # opt tar：manifest 携带则原样回显（Adam 动量 = 发布时状态）；首轮无 tar 时
        # 回空 tar（解包为空目录——作废轮的落位产物会被 _prepare_iter_dir 清场，
        # 真 PPO 轮会重新落位覆盖，永不消费空 tar）
        opt_b64 = str(manifest.get("opt_init") or "")
        if not opt_b64:
            import io as _io

            _buf = _io.BytesIO()
            with tarfile.open(fileobj=_buf, mode="w:"):
                pass
            opt_b64 = encode_opt_tar(_buf.getvalue())
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(init_w.read_bytes()),
            "opt_tar_b64": opt_b64,
            "agg": {
                "policy": 0.0,
                "value": 0.0,
                "entropy": 0.0,
                "kl": 0.0,
                "kickstart": 0.0,
                "mean_ret": 0.0,
                "steps": 0,
                "chunks": 0,
            },
            "commit_echo": manifest["commit"],
            "ppo_sec": 0.0,
            "smoke": True,
        }
        result["wire"] = _wire_block(
            payload_bytes=len(raw),
            payload_dl_sec=payload_dl_sec,
            unpack_sec=unpack_sec,
        )
        # 两遍收敛：result_bytes 自己的十进制位数要算进体长（第一遍以占位 0 计）。
        result["wire"]["result_bytes"] = len(pack_result_v2(result))
        result["wire"]["result_bytes"] = len(pack_result_v2(result))
        validate_result(result, manifest, commit_echo_must_match=False)
        _persist_result(work_dir, jid, result)
        log(f"job {jid}: ECHO (smoke) — init 权重原样回传（未跑 PPO）")
        return result


    # ---- 训练核（2026-09-24）：从算子到产物整段在 `remote/train_core.py`（L4）----
    # 本模块只剩「作业壳」：网络 / 校验 / 分叉 / 上报。核不认识 hub 的作业面，也不 import
    # 本模块（守卫钉住）；它读的模块全局都是**它自己命名空间**的（seam-free，见那边头部）。
    result, course = run_training_core(
        base_url,
        token,
        jid=jid,
        job_dir=job_dir,
        work_dir=work_dir,
        manifest=manifest,
        iter_info=iter_info,
        blob_root=blob_root,
        payload_bytes=len(raw),
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        device=device,
        torch_threads=torch_threads,
        preloaded=preloaded,
        should_cancel=should_cancel,
        on_ppo_start=on_ppo_start,
        log=log,
    )
    # ---- 半离线尾巴（kind=run）：本轮跑完 → 把计划里剩下的轮次自己跑完 ----
    # 位置在前面的自查**之前**：合并结果是「末轮形状 + iters 明细」，自查要用最终形状。
    if str(manifest["kind"]) == "run" and not echo:
        from remote.plan_run import run_plan_job

        assert run_plan is not None  # kind=run 必过 verify_plan_file（上面已抛）
        result = run_plan_job(
            job_id=jid,
            manifest=manifest,
            job_dir=job_dir,
            work_dir=work_dir,
            plan=run_plan,
            plan_sha256=run_plan_sha,
            first_result=result,
            course=course,
            device=device,
            torch_threads=torch_threads,
            code_cache_dir=code_cache_dir,
            ts_code_cache_dir=ts_code_cache_dir,
            artifacts_dir=artifacts_dir,
            max_iters=run_max_iters,
            budget_sec=run_budget_sec,
            # 产物补传：本 job 就是从这条连接上领来的（地址与 token 手边就有）——半离线段
            # 因此默认开着补传：hub 中途失联也不至于「跑完一整段、控制面一无所知」。
            hub_url=base_url,
            hub_token=token,
            # 补传的**归位键**：hub 在轮询面里告诉我们这份活属于哪门课（多课程
            # hub 里没有它，每条补传都会被 400 「无法归属课程」拒掉——而 manifest 里的
            # `course_name` 是课程文件的 name 字段，与 hub 的课程键不是一回事）。
            hub_course=str(job.get("course") or ""),
            # ★ 注入：引擎不替我们决定「一轮怎么跑」——把**自己**传进去。原先引擎里那个
            # `_real_run_job` 兜底会反向 import 本模块（`run_loop ⇄ worker` 环的成因）。
            run_job_fn=run_job,
            log=log,
        )
    validate_result(result, manifest, commit_echo_must_match=False)  # 自查
    _persist_result(work_dir, jid, result)
    return result


# ------------------------------------------------------------------ 主循环


#: 本进程**已 import 的**代码 sha（对比 manifest["code_sha256"] 揭穿热替换）
_ACTIVE_CODE_SHA: str | None = None








def worker_loop(
    base_url: str,
    token: str,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    poll_sec: float = 5.0,
    once: bool = False,
    echo: bool = False,
    max_idle_sec: float = 0.0,
    restart_argv: list[str] | None = None,
    hub_urls: list[str] | None = None,
    # 半离线（kind=run）：产物根与本次自主段上限（透传给 run_job；缺省 = 自动解析/只认计划）
    artifacts_dir: str | Path | None = None,
    run_max_iters: int = 0,
    run_budget_sec: float = 0.0,
    # 离线训练模式（2026-09-19）：本会话能自己跑完整段 ⇒ 带能力头领离线课
    offline_ok: bool = False,
    # 软持有预取深度（P2，2026-09-22）：0 = 关预取（只调度不预取，§7 的回退档）
    prefetch_depth: int = PREFETCH_DEPTH_DEFAULT,
    # 结果回传模式（P2.5，2026-09-22）：async = 回传**不占关键路径**（缺省）；sync = 旧行为
    result_upload: str = RESULT_UPLOAD_MODE_DEFAULT,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> int:
    """无状态轮询主循环。返回处理的 job 数。

    once=True：处理一个 job 后退出（M1 假云回环冒烟用）。
    max_idle_sec>0：连续空闲超过该时长退出（M2 会话活性观测用）。
    restart_argv：热替换时**监督器重启**用的参数列表（不含解释器与 `-m remote.worker`）；
      传值 = 有监督器（supervise_worker / 新版 main()）→ 热替换以 HOT_RELOAD_EXIT
      退出，由监督器用同一套参数重新拉起子进程加载新代码；
      None = 无监督器 → 热替换降级为提示人工重启并返回。
    hub_urls：多 hub 轮询（P3b §3.8：`--poll` 可重复/逗号分隔，同 token）。
      None/空 → [base_url]（默认行为零变化）。多 hub 时 round-robin 串行 run_job
      （单 GPU 互挤否决项），work_dir 按 hub 索引分区（`hub0/<jid>`…），code_cache
      留共享根。异 commit job 由既有 _ACTIVE_CODE_SHA 守卫转 86 + 监督器重拉。
    """
    done = 0
    set_bulk_log(log)  # 调度器的排队/让路/抢占日志与 worker 同一条流
    # 身份只算一次（旧路径每个轮询都调 worker_tag()：hostname 系统调用不贵但没必要
    # 每秒一次；而 hub 的登记表靠这个值去重，值必须稳定）。
    worker_id = worker_tag()
    idle_since = time.time()
    _last_alive_log = time.time()
    _polls_since_log = 0
    _polls_since_accept = 0
    # 多 hub 轮询（P3b §3.8）：None/空 → 单 hub（默认行为零变化）；多 hub round-robin。
    hubs = [u for u in (hub_urls or [base_url]) if u]
    if not hubs:
        hubs = [base_url]
    multi = len(hubs) > 1
    shared_code_cache = work_dir / "code_cache"
    hi = 0
    # §386：停机状态感知（过渡尝试一次；halt 清除后复位，下次停机可再试）——按 hub 独立。
    halt_seen: dict[int, bool] = {}
    # P2.5 异步回传（2026-09-22）：把 `out` 从关键路径上摘下来。`post_result` 按**调用时**
    # 解析（供测试 monkeypatch）；`sync` = 逐字回退到改造前（`--result-upload sync`）。
    uploader = ResultUploader(upload=post_result, mode=result_upload, log=log)

    # P2 软持有暂存区（按 hub 分区；`blob_cache` 共享根与 run_job 的口径一致）。
    pf_stores: dict[int, PrefetchStore] = {}
    try:
        while True:
            base_url = hubs[hi]
            # work 分区（P3b C3）：多 hub 按源分区 hub0/<jid>…（分区才语义正确）；
            # 单 hub 沿用旧根（默认行为零变化）。code_cache 永远共享根。
            part_dir = work_dir / f"hub{hi}" if multi else work_dir
            if multi:
                part_dir.mkdir(parents=True, exist_ok=True)
            if prefetch_depth > 0 and hi not in pf_stores:
                pf_stores[hi] = PrefetchStore(
                    part_dir, log=log, blob_root=shared_code_cache.parent / "blob_cache"
                )
            pf_store = pf_stores.get(hi)
            try:
                _polls_since_log += 1
                _polls_since_accept += 1
                # 取活三件套（2026-09-22）：peek（候选，**不认领**）→ priority（job 边界
                # 问询）→ claim（独占）。旧轮询面已退役（R1-9：不兼容旧 worker 是用户
                # 授权的**一次性**切换）——旧 worker 会拿到 404 而不是静默错跑。
                job = acquire_job(
                    base_url,
                    token,
                    worker_id=worker_id,
                    offline_ok=offline_ok,  # 能力自报：能自己跑完整段
                    # P2：已被别人落盘的 job（none 级）就地丢掉本地预取副本——再预取就白花带宽。
                    on_drop=(pf_store.drop if pf_store is not None else None),
                    log=log,
                )
            except Exception as e:
                log(f"acquire failed: {e} — retry in {poll_sec}s")
                time.sleep(poll_sec)
                hi = (hi + 1) % len(hubs)
                continue
            got_halt = isinstance(job, dict) and job.get("halt") is True
            if got_halt and not halt_seen.get(hi):
                # §386：停机命令随任务同发——先尝试真停机（Colab unassign）；
                # 停不掉（Kaggle 无 API）→ **照常执行下面的任务**（云机活着就不闲置，能继续训练）。
                halt_seen[hi] = True
                log("云端停机达令已送达：先尝试停机宿主实例（停不掉则照常执行任务）")
                _release_cloud_machine(log)
            elif not got_halt and halt_seen.get(hi):
                halt_seen[hi] = False  # 停机条件消失（hub resume）→ 复位
            if job is None:
                if once:
                    break  # --once：无 job 或已处理完都退出（冒烟/单发）
                if max_idle_sec > 0 and time.time() - idle_since > max_idle_sec:
                    log(f"idle > {max_idle_sec}s — exit")
                    break
                # 每 60s 打一次 alive 日志，让用户知道 worker 在正常运行
                # （附带周期内请求数——验证轮询周期真在生效 + 附带连续空闲秒数便于判断孤儿 job）。
                if time.time() - _last_alive_log > 60:
                    log(
                        f"polling hub (no job yet, {done} done, {_polls_since_log} polls, idle {int(time.time() - idle_since)}s)"
                    )
                    _last_alive_log = time.time()
                    _polls_since_log = 0
                time.sleep(poll_sec)
                hi = (hi + 1) % len(hubs)  # 空闲也轮转：多 hub 下一个也得被问到
                continue
            if job.get("halt") is True and not job.get("job_id"):
                # 纯停机达令、无任务：不退出、继续等待（云机活着=随时可续训）。
                # 这里 job 非 None → 上面的 None 分支存活日志会被吞——补一条同频日志，
                # 否则停机期日志静默会被误读为"worker 罢工"（2026-09-11 现场）。
                if time.time() - _last_alive_log > 60:
                    log(
                        f"cloud halted, polling hub (no job yet, {done} done, "
                        f"{_polls_since_log} polls, idle {int(time.time() - idle_since)}s)"
                    )
                    _last_alive_log = time.time()
                    _polls_since_log = 0
                time.sleep(poll_sec)
                hi = (hi + 1) % len(hubs)  # 纯停机达令也轮转
                continue
            idle_since = time.time()
            _polls_since_log = 0  # claim 即上报：alive 行下次只数 claim 之后的轮询，不与本行重复
            round_ = run_one_round(
                base_url,
                token,
                job,
                part_dir=part_dir,
                # 多 hub 才分区 code_cache；`multi` 是宿主概念，本模块不收（见 job_round 文档）
                code_cache_dir=shared_code_cache if multi else None,
                pf_store=pf_store,
                polls_since_accept=_polls_since_accept,
                worker_id=worker_id,
                device=device,
                torch_threads=torch_threads,
                echo=echo,
                artifacts_dir=artifacts_dir,
                run_max_iters=run_max_iters,
                run_budget_sec=run_budget_sec,
                restart_argv=restart_argv,
                offline_ok=offline_ok,
                prefetch_depth=prefetch_depth,
                uploader=uploader,
                # ★ 注入点：宿主在**调用点**读 `run_job` 这个值 ⇒ patch `remote.worker.run_job`
                #   仍生效（引用即接缝）；漏传 = 调用当场 TypeError，无兜底。
                run_job_fn=run_job,
                log=log,
            )
            if round_.uploaded:
                done += 1
                # 口径微调（P2.5）：async 下此刻还不知道 hub 收没收，所以这一格的含义从
                # 「距上次**被接受**」变成「距上次**产出结果**」（收没收看落定行/收尾行）。
                # 它是存活日志里的诊断读数（是不是在疯狂轮询却不产活），不是判据。
                _polls_since_accept = 0
            if round_.stop:
                return done
            job_ok = round_.ok
            if once:
                # H8（review-hy）：--once 模式 job 失败必须非零退出——冒烟/单发场景
                # 退出码 0 会静默掩盖失败（smoke 只判 returncode）。
                # P2.5：async 下成败只能等回传落定才知道，所以 --once 必须先 drain 再判。
                uploader.drain()
                if round_.uploaded:
                    _out = uploader.outcome(round_.jid)
                    if _out is not None:
                        job_ok = _out.ok
                return -1 if not job_ok else done
            hi = (hi + 1) % len(hubs)  # 跑完一个换下一 hub（round-robin 公平）
        return done
    finally:
        # P2.5（2026-09-22）：**每条**退出路径都要过这里——`break`（空闲/停机）、
        # `--once` 的 return、热替换的 SystemExit(86)、以及任何异常。队列里可能还有
        # 没送出去的结果；不等它落定就退出 = 静默丢掉最贵的产物（训练侧要等租约
        # 过期才看得出来，正是「3.5 小时静默」那一类事故）。
        _drained = uploader.close()
        _st = uploader.stats()
        log(
            f"回传收尾: mode={_st['mode']} 提交={_st['submitted']} "
            f"成功={_st['ok']} 失败={_st['failed']} 未落定={_st['pending']} "
            f"同步退化={_st['sync_fallback']}"
        )
        if not _drained or _st['failed']:
            log(
                "★ 回传收尾有未落定/失败的结果——训练侧会在租约过期后才发现，"
                "请按上面的 jid 排查（hub 日志 / 网络）"
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="remote PPO worker (cloud, stateless)")
    ap.add_argument(
        "--poll",
        required=True,
        action="append",
        help="hub-server base URL（可重复/逗号分隔多 hub，同 token；"
        "P3b 多课程共享 worker：本地采集与云端 PPO 可执行不同课程任务）",
    )
    ap.add_argument("--token", default="", help="bearer token（与 hub-server 一致）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument("--out", default="tmp/remote-worker", help="work dir (payloads/ckpts)")
    ap.add_argument("--device", default="cpu", help="torch device: cpu / cuda / cuda:0")
    ap.add_argument("--threads", type=int, default=0, help="torch intra-op threads (0=default)")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="处理一个 job 后退出")
    # P2.5（2026-09-22）：回传模式。async（缺省）= 回传与下一份 job 并发（把 `out`
    # 从关键路径上摘下来）；sync = 旧行为（回传占关键路径）——现场逃生口。
    ap.add_argument(
        "--result-upload",
        choices=RESULT_UPLOAD_MODES,
        default=RESULT_UPLOAD_MODE_DEFAULT,
        help="结果回传模式：async=不占关键路径（缺省）/ sync=旧行为（逃生口）",
    )
    ap.add_argument(
        "--echo",
        action="store_true",
        help="冒烟：跳过 PPO，回显 init 权重为结果（hub-start 冒烟预演；消费方作废本轮）",
    )
    ap.add_argument("--max-idle-sec", type=float, default=0.0, help="空闲超时退出（0=永不）")
    # P2 软持有预取：把一个 job 的下载叠到上一个 job 的 PPO 上（§2.6）。
    # 0 = 关预取（只调度不预取，plan §7 的回退档）。深度缺省 3（跨课程轮转取）。
    ap.add_argument(
        "--prefetch-depth",
        type=int,
        default=PREFETCH_DEPTH_DEFAULT,
        help=f"软持有预取深度（0=关；缺省 {PREFETCH_DEPTH_DEFAULT}）",
    )
    # ---- 半离线（kind=run；2026-09-17）----
    # 产物目录：缺省按 Kaggle /kaggle/working → Colab Drive → <work>/artifacts 自动解析。
    ap.add_argument(
        "--artifacts",
        default="",
        help="半离线产物目录（kind=run 的交付面；缺省自动：Kaggle /kaggle/working / Colab Drive）",
    )
    ap.add_argument(
        "--run-max-iters",
        type=int,
        default=0,
        help="本次自主段最多再跑几轮（0=只认计划；计划本身也有上限）",
    )
    ap.add_argument(
        "--run-budget-sec",
        type=float,
        default=0.0,
        help="本次自主段最多跑多少秒（0=不限；Kaggle 会话到点前干净停机的把手）",
    )
    # ---- 离线训练模式（2026-09-19）----
    # 能力自报：本会话能自己跑完整段（kind=run）。hub 只把**离线课**的 job 放给带标的
    # worker；不带标就领不到（离线课不实时派发，但不是谁都领得到的公共池）。
    # 这是能力声明，不是课程绑定：带标 worker 照样领在线课（课程与 worker 正交）。
    ap.add_argument(
        "--offline",
        action="store_true",
        help="自报「能自主跑完整段」：领离线课的整段 job（kind=run）；带标仍可领在线课",
    )
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    # 2026-09-08 双 tmp 统一：相对 work 路径锚定仓库根（remote/ 上溯 3 层），
    # 不再落到 nn-training/tmp（此前 spawn cwd=nn-training 时相对路径走偏）。
    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parents[2] / out
    out.mkdir(parents=True, exist_ok=True)
    # P3b：--poll 可重复/逗号分隔（同 token）；单值 = 旧行为。顺序即轮询顺序。
    raw_polls = args.poll if isinstance(args.poll, list) else [args.poll]
    hub_urls = [u.strip().rstrip("/") for p in raw_polls for u in str(p).split(",") if u.strip()]
    if not hub_urls:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] ERROR: --poll 为空", flush=True)
        sys.exit(1)
    if len(hub_urls) > 1:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] 多 hub 轮询（round-robin）：{hub_urls}", flush=True)
    if os.environ.get("REMOTE_WORKER_CHILD") == "1":
        # ── 子进程模式（由监督器 supervise_worker / 新版 main() 拉起）──
        # 热替换必须退 HOT_RELOAD_EXIT(86) 让监督器重拉：worker_loop 以
        # restart_argv「非空 = 有监督器」判定走退出码（空 = 无监督器返回）。
        # 这里传本进程的参数列表（与监督器那侧同源）即可，子进程不自己重拉。
        n = worker_loop(
            hub_urls[0],
            token,
            work_dir=out,
            device=args.device,
            torch_threads=args.threads,
            poll_sec=args.poll_sec,
            once=args.once,
            echo=args.echo,
            max_idle_sec=args.max_idle_sec,
            restart_argv=sys.argv[1:],
            hub_urls=hub_urls,
            artifacts_dir=args.artifacts or None,
            run_max_iters=args.run_max_iters,
            run_budget_sec=args.run_budget_sec,
            offline_ok=args.offline,
            prefetch_depth=args.prefetch_depth,
            result_upload=args.result_upload,
        )
        print(f"[{time.strftime('%H:%M:%S')}] [worker] done: {n} job(s) processed", flush=True)
        # H8：--once 失败（返回 -1）→ 非零退出码
        sys.exit(0 if n >= 0 else 1)
    # ── 监督器模式（默认入口）──
    # worker 由子进程承担（REMOTE_WORKER_CHILD=1 直跑上面的分支），输出逐行转发到
    # 本进程 stdout —— notebook 里本进程是 kernel，转发保住单元格输出流，换代码不会
    # 再打掉 kernel（2026-09-11 线上事故修复，见 _request_reload 的 docstring）。
    # sys.argv[1:] 就是可重放的热替换参数（argv[0] 可能是 -m 或脚本路径，统一由
    # supervise_worker 用 `-m remote.worker` 重建，故这里只取参数部分）。
    rc = supervise_worker(sys.argv[1:])
    print(f"[{time.strftime('%H:%M:%S')}] [worker-supervisor] worker exited: rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
