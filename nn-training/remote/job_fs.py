"""remote/job_fs.py — worker 的**作业工作区 / 产物落盘 / TAR / git 物化**（S4 第六步之一，2026-09-23）。

从 `remote/worker.py` 搬出来的「本地这一侧怎么摆作业的物料与产物」：`REPO_ROOT` ·
`JOB_DIR_KEEP` + `prune_job_dirs`（工作目录轮转，豁免内容寻址缓存与预取暂存）·
`unpack_opt_tar` / `pack_opt_tar`（opt 快照的 TAR 收放）· `unpack_payload_or_fail`（归档层异常
统一转 `ProtocolError`）· `_persist_result`（结果落盘，回传失败重领时幂等复用）· `_git_head`
（当前 HEAD 读数，给证据/对账用）。

## 为什么它是「底座」

这些函数**零宿主依赖**（不读 worker 的作业状态、不调 `_request`），只依赖 stdlib 与
`common.*` / `remote.prefetch`。它们是 `run_job` 与 `remote/bc_job.py` 的**共同前置**：
`_persist_result` 两处都用，BC 拆出去必须先把这类「非 BC 专属的作业 I/O」放到下面。
依赖方向：`job_fs ← {worker, bc_job}`，与 `http` / `wire` 同形（无环）。

## 注入点

本组**无 monkeypatch 接缝**（全仓对 `prune_job_dirs` / `unpack_payload_or_fail` / `_persist_result`
的引用都是**直接调用**，无 `setattr`）⇒ `worker.py` 的显式转发就够，测试一行不改。
唯一要注意的是 `REPO_ROOT`：它只被 `_git_head` 使用，随组搬迁；
本模块在 `remote/` 下，`Path(__file__).resolve().parent.parent` 与原值同一个 nn-training 根。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

from common.fs import extract_tar_bytes
from common.proc import run_capture
from common.protocol import ProtocolError, unpack_payload
from remote.prefetch import PREFETCH_DIR_NAME

__all__ = [
    "JOB_DIR_KEEP",
    "REPO_ROOT",
    "_git_head",
    "_persist_result",
    "pack_opt_tar",
    "prune_job_dirs",
    "unpack_opt_tar",
    "unpack_payload_or_fail",
]

REPO_ROOT = Path(__file__).resolve().parent.parent


#: worker 侧保留的 job 目录数（含在跑的本 job）。2026-09-11：c6b 单 job ≈280 MB
#: （600 shard × 439 KB 解包后 + payload 归档 + code.zip），20 轮 ≈5.6 GB。
#: ⚠ hub 侧 keep_iters=3 只轮转本地 it* 与 remote-jobs——**管不到**这里的
#:   /tmp/remote-worker（云）与 tmp/remote-worker-serve（本机 self 节点）。
#: 保留 2 个：上一个 job 的 _result.json 要留给"回传失败后重领同 job"的幂等路径。
JOB_DIR_KEEP = 2


def _persist_result(work_dir: Path, jid: str, result: dict) -> None:
    """结果落盘 _result.json：回传失败后重领同 job 时直接复用，不重算 PPO。"""
    rpath = work_dir / jid / "_result.json"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def prune_job_dirs(
    work_dir: Path, keep: int = JOB_DIR_KEEP, log=lambda msg: None, bundle: Any = None
) -> int:
    """按 mtime 保留最近 `keep` 个 job 目录，其余删除。返回删除个数。

    只动 work_dir 下的 job 目录（按 jid 命名），**跳过 code_cache / blob_cache /
    ts_code_cache / prefetch**（内容寻址缓存按 sha 复用；`prefetch/` 是 P2 的软持有暂存区，
    删掉 = 下一轮预取白做）。删除失败（占用/沙箱保护）跳过，不抛。

    ⚠ 新增内容寻址缓存目录时必须加进这份豁免名单（2026-09-17 M2 事故：`blob_cache`
    漏了名单 → 每轮被当旧 job 目录删掉 → 缓存永远未命中，而现象看起来是「协议没生效」）。
    """
    try:
        dirs = [
            d
            for d in work_dir.iterdir()
            if d.is_dir()
            and d.name not in ("code_cache", "blob_cache", "ts_code_cache", PREFETCH_DIR_NAME)
        ]
    except OSError:
        return 0
    if len(dirs) <= keep:
        return 0
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    from platform_utils import rmtree_best_effort

    removed = 0
    for d in dirs[keep:]:
        try:
            # 计数必须挂在返回值上（与 rl/workdir_sweep 同策略）：沙箱删除保护拦截
            # 时 rmtree_best_effort 返回 False，无条件 +1 会把没删掉的也算进 n。
            if rmtree_best_effort(d, ignore_errors=True):
                removed += 1
        except BaseException as e:  # 含 SystemExit：沙箱删除守卫会打死调用线程
            log(f"prune: 跳过 {d.name}（{type(e).__name__}）")
    if removed:
        # 日志节食：给了 bundle 就攒进调用方那一行（prune 与 payload/设备/装载同属
        # 「本 job 准备」阶段）。
        if bundle is not None:
            bundle.add("prune", f"删 {removed} 个旧 job 目录（保留最近 {keep} 个）")
        else:
            log(f"prune: 删除 {removed} 个旧 job 目录（保留最近 {keep} 个）")
    return removed


def unpack_payload_or_fail(payload_path, job_dir) -> tuple[dict, list[str]]:
    """解包 payload；**内容决定性**失败统一转 `ProtocolError`（重认领不会自愈）。

    ★ 2026-09-21（plan/accident.plan.md §4 根因）：`worker_loop` 的 `except Exception`
    分支把「解包失败」一律当瞬态重认领 ⇒ 同一份字节每 5 分钟复现一次、零告警，空转
    3.5 小时（实测：**完好** tar.xz 被 `zipfile.is_zipfile` 启发式误判成 zip ⇒
    BadZipFile；job 永不回传，训练侧只看到 3×1800s 超时）。归档层异常在这里就转
    `ProtocolError`：该分支早已有 `report_job_failure` → hub 落终局 failed → 训练侧
    `JobFailedError` 停腿（整条链现成，不必改循环）。

    只转**归档/内容**类异常；网络/远端关闭那类真瞬态仍走 `except Exception` 重认领
    （`OSError` 刻意不在这里吞——磁盘/权限类也可能瞬时）。
    """
    import zipfile

    try:
        return unpack_payload(payload_path, job_dir)
    except ProtocolError:
        raise
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
        raise ProtocolError(
            f"payload 归档不可读（内容决定性，重领同一份字节不会自愈）：{type(e).__name__}: {e}"
        ) from e


def unpack_opt_tar(tar_bytes: bytes, dest: Path) -> None:
    """opt_init base64 tar → dest。兼容 3.10（无 filter 参数）。

    唯一实现见 `common.fs.extract_tar_bytes`（与 `remote/hub_client._extract_tar`
    原是同款孪生，两侧都写了「Python < 3.12 无 filter」这条注释）。
    """
    extract_tar_bytes(tar_bytes, dest)


def pack_opt_tar(src_dir: Path) -> bytes:
    """_ppo_save 目录 → tar bytes（回传用）。

    H5（review-hy）：**不打 state.json**——state.json 里的 numpy RNG 状态从未被读取
    （worker 每次按 per-job 种子重播，D5 自洽），tar 里躺着死数据只会误导。

    ★ 2026-09-24（opt-blob-diet，plan/opt-blob-diet.plan.md §2.1-1）：**只打 `opt.pt`**。
    `model.pt` 从此不进 tar —— 它与同一个 POST 里的 `result.weights_json` 是**同一份权重**，
    只是传了两遍（实测 268,996 B/轮 = 上行的 35.4%）。权重改走内容寻址的 `init` blob
    （sha = `manifest.init_weights_fp`），模型恢复读 worker 自己解析出来的
    `job_dir/init_weights.json`（见 `_resolve_weights`）。

    Adam 动量（`opt.pt`）是本 tar 的**唯一**成员：它是跨轮续跑真正需要的、**每轮必变**的
    状态。红线（minimize-payload §1.3-1）：不得量化/降质/裁剪。

    ⚠ **形状是与 hub 的同一份契约**：`run_job` 的 restore 段按「有没有 `model.pt`」分流
    （有 = 旧形状走 legacy 路径，无 = 新形状读 `init_weights.json`），所以本函数的成员
    集合与那条分流**必须同 commit 改**（§5 E1+E2 的硬约束）。
    """
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        p = src_dir / "opt.pt"
        if p.exists():
            tf.add(p, arcname="opt.pt")
    return buf.getvalue()


def _git_head(repo_root: Path = REPO_ROOT) -> str:
    try:
        r = run_capture(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""
