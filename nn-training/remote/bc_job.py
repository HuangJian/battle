"""remote/bc_job.py — worker 的 **BC 作业**（S4 第六步之二，2026-09-23）。

从 `remote/worker.py` 整簇搬出来的「云端 BC job 怎么跑」：`_run_bc_job`（D14 语料血缘校验 →
resume 接续 → `train/bc.py::train` → 每 epoch 回传 → 结果自查/落盘）+ 它的一圈助手：
`_bc_fetch_resume` / `_bc_local_resume_dir` / `_bc_store_local_resume` / `_bc_load_local_resume`
（hub 优先、本地兜底的断点续训）+ `_bc_post_epoch` + `_bc_device`（设备透传与响亮拒绝）+
`normalize_ppo_device`（`auto` 兜底归一化，PPO 分支也用）+ `resolve_bc_seed`（课程种子优先）。

## 依赖方向

`bc_job → {http, job_fs}`（都向下，DAG）：`_bc_fetch_resume` / `_bc_post_epoch` / `_run_bc_job`
经 `remote.http._request` 收发；`_run_bc_job` 用 `remote.job_fs._persist_result` 落盘。
**不** import `remote.worker`（`worker` 用自别名转发回来）。torch 与 `train/bc.py` 仍是
**函数内延迟 import**（本模块顶层零 torch，与 worker 同规）。

## 注入点

本组**无 monkeypatch 接缝**（全仓对 `_bc_*` / `normalize_ppo_device` / `resolve_bc_seed` /
`_run_bc_job` 只有直接调用与 `from remote.worker import …`）⇒ `worker.py` 的显式转发就够。
唯一与传输层有关的接缝是 `_request` 本身，而它已在 `remote.http`（第五步）。

## 为什么 `d14_corpus_match` 在本模块也有一份

worker 侧那份是 `common.protocol.d14_corpus_match` 的**转发别名**（`run_job` 仍在用，不动）；
本模块按同样口径从 `common.protocol` 取一次。两者是**同一个函数对象**的独立绑定，不是状态。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from common.protocol import (
    ProtocolError,
    RetryableError,
    d14_corpus_match,
    decode_weights_json,
    encode_weights_json,
    job_seed,
    validate_result,
)
from remote.http import _request
from remote.job_fs import _persist_result

__all__ = [
    "_bc_device",
    "_bc_fetch_resume",
    "_bc_load_local_resume",
    "_bc_local_resume_dir",
    "_bc_post_epoch",
    "_bc_store_local_resume",
    "_run_bc_job",
    "normalize_ppo_device",
    "resolve_bc_seed",
]

def _bc_fetch_resume(
    base_url: str,
    token: str,
    jid: str,
    *,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> tuple[int, bytes] | None:
    """GET /jobs/{id}/resume → (epoch, weights_bytes) | None（404 = 全新训练）。

    网络错误/5xx → RetryableError（release 后重领再查——resume 未知就开训可能
    白扔已有进度，一致性优先）；4xx 其它 = 确定性拒收。"""
    last = ""
    for attempt in range(1, 4):
        try:
            status, body = _request(base_url, token, f"/jobs/{jid}/resume", timeout=30.0)
        except Exception as e:
            status, body = None, repr(e).encode()
        if status == 200:
            try:
                d = json.loads(body.decode("utf-8"))
                return int(d["epoch"]), decode_weights_json(str(d["weights"]))
            except (ValueError, KeyError, TypeError) as e:
                raise ProtocolError(f"bc resume 体非法: {e}") from e
        if status == 404:
            return None
        last = f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        if attempt < 3:
            log(f"bc resume 查询瞬时失败({last})——退避重试 {attempt}/3")
            time.sleep(min(2**attempt, 8))
    raise RetryableError(f"bc resume 查询 3 次仍失败: {last}")


def _bc_local_resume_dir(work_dir: Path) -> Path:
    """worker 本地 resume 存储（push 模式无 hub 时的持久层；pull 模式作缓存）。
    ⚠ 独立于 job_dir——job_dir 被重领清场，resume 必须在它外面才能活过重领。"""
    d = Path(work_dir) / "bc-resume"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bc_store_local_resume(work_dir: Path, jid: str, body: dict) -> None:
    """本地 resume 原子覆盖写 + 目录收敛（保留最近 2 个 job 的 resume）。"""
    import os as _os

    d = _bc_local_resume_dir(work_dir)
    tmp = d / f"{jid}.json.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    _os.replace(tmp, d / f"{jid}.json")
    files = sorted(
        (f for f in d.glob("*.json") if not f.name.endswith(".tmp")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for stale in files[2:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _bc_load_local_resume(work_dir: Path, jid: str) -> tuple[int, bytes] | None:
    p = _bc_local_resume_dir(work_dir) / f"{jid}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return int(d["epoch"]), decode_weights_json(str(d["weights"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"[{time.strftime('%H:%M:%S')}] [worker] bc local resume 损坏（忽略，全新训练）: {e}", flush=True)
        return None


def _bc_post_epoch(
    base_url: str,
    token: str,
    jid: str,
    body: dict,
    lease_token: str,
    *,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """POST /jobs/{id}/epoch（每 epoch 权重+指标回传；租约校验在 hub 侧）。

    best-effort：2 次退避重试后仍失败只 WARN——resume 停在最后成功 epoch，
    下个 epoch 自带重试；绝不因回传失败打断训练。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(1, 3):
        try:
            status, resp = _request(
                base_url,
                token,
                f"/jobs/{jid}/epoch",
                timeout=60.0,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    **({"X-Lease-Token": lease_token} if lease_token else {}),
                },
            )
        except Exception as e:
            status, resp = None, repr(e).encode()
        if status in (200, 201):
            return
        last = (
            f"HTTP {status}"
            if status is not None
            else repr(resp.decode("utf-8", "replace")[:120])
        )
        if attempt < 2:
            time.sleep(2)
    log(f"WARN: epoch {body.get('epoch')} 回传失败({last})——resume 停留在上一成功 epoch")


def _bc_device(dev_str: str) -> str:
    """BC 任务的设备透传（2026-09-13 多卡：train/bc.py 自带 cuda-dp 语义与
    单卡/无卡响亮退化——worker 不再代为砍成单卡）；tpu/xla 确定性拒绝
    （train/bc.py 无 xla 路径），绝不静默降级——设备语义错了的 benchmark
    数据比没有更糟。"""
    s = str(dev_str).lower()
    if s in ("tpu", "xla"):
        raise ProtocolError(f"bc 任务不支持设备 {dev_str!r}（v1 仅 cpu/cuda/cuda-dp）")
    return s or "cpu"


def normalize_ppo_device(device: object, *, cuda_available: bool | None = None) -> str:
    """把 PPO 的 ``--device`` 归一化成 torch 认得的字符串；``auto`` 在此兜底解析。

    为什么需要（2026-09-15 Colab push-first 事故）：push-first 引导在
    `notebook_runtime.resolve_device()` 解析设备**之前**就把 worker spawn 起来了
    （cell 与 `push_bootstrap.run_push_first` 都只做 `device_resolved or device` 兜底），
    于是字面量 ``"auto"`` 被一路送到 `torch.device("auto")`，整轮 job 报
    ``Expected one of cpu, cuda, ... device type at start of device string: auto``。

    兜底规则：``auto`` → 有 CUDA 用 ``cuda``（**单卡**），否则 ``cpu``。
    **刻意不自动升 ``cuda-dp``**：DataParallel 会改变梯度归约顺序，是"新开一条实验臂"
    的开关而非透明加速（见 `run_job` 多卡段注释，以及 `notebook_runtime.resolve_device`
    的「显式 cuda 绝不悄悄升级」纪律）。要多卡必须显式 ``--device cuda-dp``。
    """
    s = str(device or "cpu").strip().lower()
    if s != "auto":
        return s
    if cuda_available is None:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        except Exception:  # torch 缺失 / 驱动异常 → 按无 CUDA 处理
            cuda_available = False
    return "cuda" if cuda_available else "cpu"


def resolve_bc_seed(manifest: dict) -> tuple[int, str]:
    """BC job 训练种子 → (seed, 来源标签)。

    2026-09-14：`manifest["train_seed"]`（来自课程 `train.seed`，run_bc 经 extra 注入）
    **优先**——R1（v2 vs v3 obs 对照）必须两臂同 seed 才能得到同一 val 划分
    （`train/bc.py` 用 seed 做 `make_loaders` 的切分），否则 best_val_loss 不可比；
    同时让同课程云端重跑可复现。
    缺省（旧 job 无该键）回退 per-job 确定性种子（D5：同一 job 重发 chunk 逐字节一致
    —— 但它含 runId，而 runId 每轮启动都变 ⇒ 复现性只限同一 job 的重发）。

    键名刻意不用 `seed`：manifest 里 `seed` 已有历史口径（hex per-job 种子占位，
    见 tests/test_bc_epoch_e2e.py 的 fixture，以及 dist_common 的 stage/seed 语义）。
    """
    explicit = manifest.get("train_seed")
    if explicit is None:
        seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
        return int(seed_hex[:8], 16), "per-job"
    return int(explicit), "course"


def _run_bc_job(
    *,
    jid: str,
    manifest: dict,
    job_dir: Path,
    shard_dirs: list[str],
    device: str,
    torch_threads: int,
    echo: bool,
    base_url: str = "",
    token: str = "",
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> dict:
    """云端 BC job（plan/bc-cloud-integration.plan.md §3；每 epoch 回传 2026-09-13）：
    D14 语料血缘校验 →（--echo 冒烟回显）→ **resume 接续**（hub bc-resume 优先，
    本地 bc-resume/ 兜底——中断重领同 job 从最后完成的 epoch 接着训，绝不从头重训）
    → 复用 `train/bc.py::train` 训练（on_epoch 回调每 epoch 回传权重+指标：hub
    POST /jobs/{id}/epoch 租约校验防旧 worker 覆盖新 resume + 本地原子覆盖）→
    BC 权重 + metrics 回传。

    语料 = payload 解包出的 npy shard 目录（obs/scalars/actions/masks/conditions/
    returns + manifest），bc.py 的 scan_shards 对 job_dir 递归扫描即全部装载。
    """
    # ---- D14 语料血缘：与 PPO 同规（BC shard manifest 同样携带 course_fp/corpus_fp）----
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
                f"D14 corpus_fp 不匹配：job={_corpus[:12] or _cfp[:12]}… "
                f"shard={str(_sm.get('corpus_fp') or _sm.get('course_fp'))[:12]}… "
                f"（{_sd}）——跨课程语料混入，拒收"
            )

    t0 = time.time()

    # ---- 冒烟回显（--echo，2026-09-05 同语义）：不拉 torch 不训练——占位 weights +
    # smoke 标记（消费方 run_bc 作废轮，不落盘不归档）。三重校验按构造必过。
    if echo:
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(b'{"smoke-placeholder":true}'),
            "opt_tar_b64": "",
            "metrics": {
                "epochs": 0,
                "train_samples": 0,
                "val_samples": 0,
                "best_val_loss": 0.0,
            },
            "commit_echo": manifest["commit"],
            "bc_sec": 0.0,
            "smoke": True,
        }
        validate_result(result, manifest, commit_echo_must_match=False)
        _persist_result(job_dir.parent, jid, result)
        log(f"job {jid}: ECHO (bc smoke) — 占位权重回传（未跑 BC）")
        return result

    # ---- 延迟 import torch + BC 训练器（B7 同款；本模块顶层零 torch）----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    from types import SimpleNamespace

    from data.weights_io import save_weights_json
    from train.bc import train as bc_train

    # 训练种子（2026-09-14 修正）：课程/调用方指定优先 —— 见 resolve_bc_seed。
    seed_int, seed_src = resolve_bc_seed(manifest)
    dev = _bc_device(device)
    out_path = job_dir / "bc-weights.json"
    total_epochs = int(manifest["epochs"])

    # ---- resume 接续：hub 优先（跨会话/跨 worker 持久），本地 bc-resume/ 兜底 ----
    resume: tuple[int, bytes] | None = None
    if base_url:
        resume = _bc_fetch_resume(base_url, token, jid, log=log)
    if resume is None:
        resume = _bc_load_local_resume(job_dir.parent, jid)
    resume_epoch, resume_weights = resume if resume else (0, b"")
    if resume_epoch:
        log(f"job {jid}: resume 接续——从 epoch {resume_epoch} 继续（共 {total_epochs}）")

    # 每 epoch 回传回调：本地原子覆盖 + hub POST（均 best-effort——失败只 WARN，
    # resume 停在最后一次成功回传的 epoch，下个 epoch 重试）。
    def _on_epoch(gepoch: int, raw_model: Any, m: dict) -> None:
        tmp = job_dir / "bc-epoch-tmp.json"
        save_weights_json(raw_model, str(tmp), extra_meta={"epoch": gepoch, "ckpt": True})
        body = {
            "epoch": int(gepoch),
            "weights": encode_weights_json(tmp.read_bytes()),
            "metrics": dict(m),
        }
        try:
            tmp.unlink()
        except OSError:
            pass
        _bc_store_local_resume(job_dir.parent, jid, body)
        if base_url:
            _bc_post_epoch(base_url, token, jid, body, lease_token, log=log)

    if resume_epoch >= total_epochs > 0:
        # 上轮已完成全部 epoch、只差最终回传（epoch POST 成功但 result 丢失）：
        # resume 权重即终态——零重训直接产出。
        log(f"job {jid}: resume 已达 {resume_epoch}/{total_epochs} —— 零重训直接回传终态")
        wj_bytes = resume_weights
        result_metrics: dict[str, Any] = {
            "epochs": total_epochs,
            "train_samples": 0,
            "val_samples": 0,
            "best_val_loss": 0.0,
            "resumed_complete": True,
        }
    else:
        ns = SimpleNamespace(
            data_dir=str(job_dir),
            arch=str(manifest["arch"]),
            out=str(out_path),
            notes=f"bc-job {jid} course={manifest.get('course_name', '')}",
            resume=None,
            epoch_offset=0,
            ckpt_every=0,  # 云端持久化走 on_epoch 回传（job_dir 重领即清场，盘上 ckpt 无意义）
            checkpoint=None,
            epochs=total_epochs - resume_epoch,
            batch=int(manifest["mb"]),
            lr=float(manifest["lr"]),
            val_split=float(manifest.get("val_split", 0.1)),
            mirror_p=float(manifest.get("mirror_p", 0.5)),
            seed=seed_int,
            num_workers=0,
            device=dev,
            value_coef=float(manifest.get("value_coef", 0.0) or 0.0),
            # fire 头正例权重（2026-09-14）：'auto' 或数字；旧 job 无此键 → 0.0
            # （关闭 = 历史语义，重放旧 job 行为不变）。
            fire_pos_weight=manifest.get("fire_pos_weight", 0.0),
            on_epoch=_on_epoch,
        )
        if resume_epoch:
            resume_in = job_dir / "bc-resume-in.json"
            resume_in.write_bytes(resume_weights)
            ns.resume = str(resume_in)
            ns.epoch_offset = resume_epoch
        log(
            f"job {jid}: BC start arch={ns.arch} epochs={ns.epochs} batch={ns.batch} "
            f"lr={ns.lr} device={dev} seed={seed_int}({seed_src}) shards={len(shard_dirs)}"
            + (f" resume@{resume_epoch}" if resume_epoch else "")
        )
        metrics = bc_train(ns)
        hist = metrics.get("history", {})
        sizes = metrics.get("sizes", {})
        result_metrics = {
            "epochs": total_epochs,
            "train_samples": int(sizes.get("train", 0) or 0),
            "val_samples": int(sizes.get("val", 0) or 0),
            "best_val_loss": float(metrics.get("best_val_loss", 0.0) or 0.0),
            "move_acc": float(hist.get("move_acc", [0.0])[-1]) if hist.get("move_acc") else 0.0,
            "fire_acc": float(hist.get("fire_acc", [0.0])[-1]) if hist.get("fire_acc") else 0.0,
            "params": int(metrics.get("params", 0) or 0),
        }
        if resume_epoch:
            result_metrics["resumed_from"] = resume_epoch
        wj_bytes = Path(str(metrics["out"])).read_bytes()

    bc_sec = round(time.time() - t0, 1)
    if "metrics" in dir():
        pass
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_bytes),
        "opt_tar_b64": "",
        "metrics": result_metrics,
        "commit_echo": manifest["commit"],
        "bc_sec": bc_sec,
    }
    validate_result(result, manifest, commit_echo_must_match=False)  # 自查
    # _persist_result 落 work_dir/<jid>/_result.json —— job_dir.parent 即 run_job 语义
    # 里的 work_dir（pull 与 push 两条路径同构），重领同 job 的缓存复用才能读到。
    _persist_result(job_dir.parent, jid, result)
    return result
