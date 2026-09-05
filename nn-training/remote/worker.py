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
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from remote.protocol import (
    AUTH_HEADER,
    HEARTBEAT_SEC,
    ProtocolError,
    RetryableError,
    decode_opt_tar,
    encode_opt_tar,
    encode_weights_json,
    job_seed,
    normalize_manifest,
    unpack_payload,
    validate_result,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ HTTP 客户端

def _request(
    base_url: str,
    token: str,
    path: str,
    timeout: float = 30.0,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={AUTH_HEADER: f"Bearer {token}", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def poll_job(base_url: str, token: str, timeout: float = 30.0) -> dict | None:
    """GET /jobs/next → {job_id, manifest} 或 None（无 job）。"""
    status, body = _request(base_url, token, "/jobs/next", timeout=timeout)
    if status != 200:
        return None
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or not data.get("job_id"):
        return None
    return data


def _get_with_retry(
    base_url: str,
    token: str,
    path: str,
    *,
    timeout: float,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    """GET + 瞬时失败退避重试：网络异常/5xx → 指数退避重试；4xx → ProtocolError。

    传输级抖动（连接重置/读超时/边缘 5xx）在租约窗口内就地消化，不再付
    「放弃本次 → 30min 租约过期 → 重领」的惩罚（2026-09-05，DECISIONS §340）。"""
    last: str = ""
    for attempt in range(1, attempts + 1):
        try:
            status, body = _request(base_url, token, path, timeout=timeout)
        except Exception as e:  # 网络层抖动（URLError/timeout/reset）
            status, body = None, repr(e).encode()
        if status == 200:
            return body
        if status is not None and 400 <= status < 500:
            raise ProtocolError(f"{path} failed: HTTP {status}")
        last = f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        if attempt < attempts:
            backoff = min(2 ** attempt, 8)
            log(f"{path}: 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    raise RetryableError(f"{path} 重试 {attempts} 次仍失败: {last}")


def download_payload(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    return _get_with_retry(
        base_url, token, f"/jobs/{jid}/payload", timeout=300.0, attempts=attempts, log=log
    )


def download_code(
    base_url: str,
    token: str,
    jid: str,
    *,
    attempts: int = 3,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> bytes:
    return _get_with_retry(
        base_url, token, f"/jobs/{jid}/code", timeout=120.0, attempts=attempts, log=log
    )


def post_result(
    base_url: str,
    token: str,
    jid: str,
    result: dict,
    lease_token: str = "",
    timeout: float = 120.0,
    attempts: int = 5,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> int:
    """POST 结果：瞬时失败（网络/5xx）指数退避重试（最贵产物不允许最后一米丢失）；
    4xx = 确定性拒绝立即抛 ProtocolError；409 = hub 已有同 job 结果（幂等，按成功）。"""
    last: str = ""
    for attempt in range(1, attempts + 1):
        try:
            status, body = _request(
                base_url,
                token,
                f"/jobs/{jid}/result",
                timeout=timeout,
                data=json.dumps(result, ensure_ascii=False).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    # H2：结果回传须携带领取时下发的 lease_token（hub 校验后收）
                    **({"X-Lease-Token": lease_token} if lease_token else {}),
                },
            )
        except Exception as e:
            status, body = None, repr(e).encode()
        if status in (200, 201):
            return status
        if status == 409:
            log("result POST 409（hub 已有同 job 结果）——按成功处理")
            return 409
        if status is not None and 400 <= status < 500:
            raise ProtocolError(
                f"result POST rejected: HTTP {status}: {body[:300].decode('utf-8', 'replace')}"
            )
        last = f"HTTP {status}" if status is not None else repr(body.decode("utf-8", "replace")[:120])
        if attempt < attempts:
            backoff = min(2 ** attempt, 16)
            log(f"result POST 瞬时失败({last}) — {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    raise RetryableError(f"result POST 重试 {attempts} 次仍失败: {last}")


def release_job(
    base_url: str,
    token: str,
    jid: str,
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True),
) -> None:
    """瞬时失败后主动还租约（POST /jobs/{id}/release）：job 立即回池可重领，
    不再干等 30min 租约过期。尽力而为：release 本身不可达时由租约过期兜底。"""
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/release",
            timeout=15.0,
            method="POST",
            headers={**({} if not lease_token else {"X-Lease-Token": lease_token})},
        )
    except Exception:
        pass  # release 不可达：租约过期兜底（30min），与旧行为一致


def heartbeat(base_url: str, token: str, jid: str, lease_token: str = "") -> None:
    try:
        _request(
            base_url,
            token,
            f"/jobs/{jid}/heartbeat",
            timeout=15.0,
            method="POST",
            headers={**({} if not lease_token else {"X-Lease-Token": lease_token})},
        )
    except Exception:
        pass  # 心跳失败不致命：下一次轮询/心跳再续


def _persist_result(work_dir: Path, jid: str, result: dict) -> None:
    """结果落盘 _result.json：回传失败后重领同 job 时直接复用，不重算 PPO。"""
    rpath = work_dir / jid / "_result.json"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ PPO 执行

def unpack_opt_tar(tar_bytes: bytes, dest: Path) -> None:
    """opt_init base64 tar → dest。兼容 3.10（无 filter 参数）。"""
    import io

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest)


def pack_opt_tar(src_dir: Path) -> bytes:
    """_ppo_save 目录 → tar bytes（回传用）。

    H5（review-hy）：**只打 model.pt + opt.pt，不打 state.json**——state.json 里的
    numpy RNG 状态从未被读取（worker 每次按 per-job 种子重播，D5 自洽），tar 里躺着
    死数据只会误导。Adam 动量（opt.pt）才是跨轮续跑真正需要的状态。
    """
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name in ("model.pt", "opt.pt"):
            p = src_dir / name
            if p.exists():
                tf.add(p, arcname=name)
    return buf.getvalue()


def _git_head(repo_root: Path = REPO_ROOT) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def _ensure_commit(target: str, repo_root: Path = REPO_ROOT,
                   log=lambda msg: None) -> bool:
    """确保本地 HEAD 等于 target commit。不等则 git fetch + checkout 自动修复。

    返回 True（一致）或 False（重试 5 次后仍不一致）。
    """
    import subprocess as _sp

    for attempt in range(5):
        head = _git_head(repo_root)
        if head and head == target:
            return True
        log(
            f"commit mismatch: HEAD={head[:12] if head else '?'} "
            f"target={target[:12]} — fetching (attempt {attempt + 1}/5)"
        )
        try:
            _sp.run(
                ["git", "fetch", "origin"],
                cwd=str(repo_root), capture_output=True, text=True, timeout=60,
            )
            _sp.run(
                ["git", "checkout", target],
                cwd=str(repo_root), capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            log(f"git fetch/checkout failed: {e}")
    head = _git_head(repo_root)
    return head == target


def run_job(
    base_url: str,
    token: str,
    job: dict,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    echo: bool = False,
    log=lambda msg: print(
        f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True
    ),
) -> dict:
    """执行单个 job：下载 → 校验 → PPO → 产出 weights_json + opt tar → POST。

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

    # ---- 下载 + payload_sha256 校验（D1：防隧道截断；对 job 记录的 manifest 校验） ----
    raw = download_payload(base_url, token, jid)
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        # 传输损坏属瞬时故障：重下即可修复（RetryableError → 释放租约立即重领重下）
        raise RetryableError("payload_sha256 不匹配——传输损坏（重下可修复）")

    job_dir = work_dir / jid
    if job_dir.exists():
        import shutil

        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True)
    zip_path = job_dir / "payload.zip"
    zip_path.write_bytes(raw)
    # zip 内 manifest 是占位副本，解包仅取 shard 目录；权威校验全走 job 记录 manifest。
    # init_weights.json / opt_init.tar.b64 与 shard 目录同落 job_dir 根（解包天然如此）。
    _unused_manifest, shard_dirs = unpack_payload(zip_path, job_dir)

    # ---- commit 校验：下载 code.zip 解压到 sys.path（替代 git 同步，D6） ----
    # 云端 worker 不再依赖 git checkout，而是使用 hub 启动时打包的代码快照。
    code_raw = download_code(base_url, token, jid)
    if hashlib.sha256(code_raw).hexdigest() != manifest["code_sha256"]:
        # per-job 快照 sha 与 manifest 对账：不匹配 = 传输损坏（瞬时，重下可修复）
        raise RetryableError("code_sha256 不匹配——传输损坏（重下可修复）")
    code_zip_path = job_dir / "code.zip"
    code_zip_path.write_bytes(code_raw)
    import zipfile

    code_extract_dir = job_dir / "code"
    code_extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(code_zip_path) as zf:
        zf.extractall(code_extract_dir)
    sys.path.insert(0, str(code_extract_dir))
    log(f"job {jid}: code.zip unpacked ({len(code_raw)} bytes, {len(list(code_extract_dir.rglob('*.py')))} .py files) -> sys.path[0]")

    # ---- mode 红线（v1：仅 per-tick） ----
    if manifest["mode"] != "per-tick":
        raise ProtocolError(f"mode={manifest['mode']!r} != per-tick——远程 v1 红线，拒收")

    # ---- D14 语料血缘：job.course_fp == 每个 shard 的 manifest.course_fp ----
    # （跨课程语料绝不混训——发布端已保证 shard 集按 course_fp 过滤，这里再校验一次）
    _cfp = str(manifest["course_fp"])
    for _sd in shard_dirs:
        try:
            with open(os.path.join(_sd, "manifest.json"), encoding="utf-8") as _f:
                _sm = json.load(_f)
        except (OSError, ValueError) as _e:
            raise ProtocolError(f"D14 course_fp: 读 shard manifest 失败 {_sd}: {_e}") from _e
        if str(_sm.get("course_fp", "")) != _cfp:
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
                "mean_ret": 0.0,
                "steps": 0,
                "chunks": 0,
            },
            "commit_echo": manifest["commit"],
            "ppo_sec": 0.0,
            "smoke": True,
        }
        validate_result(result, manifest, commit_echo_must_match=False)
        _persist_result(work_dir, jid, result)
        log(f"job {jid}: ECHO (smoke) — init 权重原样回传（未跑 PPO）")
        return result

    # ---- 课程上下文：快照全文 → CourseConfig → reward_fn（D1/D6/D13） ----
    import numpy as np

    course_text = manifest["course"]
    course_path = job_dir / "course.jsonc"
    course_path.write_text(course_text, encoding="utf-8")
    from rl.config import load_course

    course = load_course(str(course_path))
    if course.reward_spec().identity() != manifest["formula_hash"]:
        raise ProtocolError(
            f"course formula_hash 与快照不符：manifest={manifest['formula_hash']} "
            f"本地算={course.reward_spec().identity()}"
        )
    from rl.reward_context import update as ctx_update
    from rl.reward_library import build_reward_fn

    reward_fn = build_reward_fn(course.reward_spec())
    ctx_update(
        reward_fn=reward_fn,
        gamma=float(manifest["gamma"]),
        lam=float(manifest["lam"]),
        it=int(manifest["it"]),
        metrics_version=int(manifest["metrics_version"]),
        identity={"course": course.name, "formula_hash": manifest["formula_hash"]},
    )

    # ---- 延迟导入 torch / ppo 后端（B7 同款；本模块顶层零 torch） ----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    import ppo.engine as ppo_engine
    from data.weights_io import load_state_into, save_weights_json

    # ---- per-job 确定性种子（D5）：load/chunk/update 前重新播种 ----
    # numpy RandomState 种子必须 < 2^32：sha256 前 8 个 hex 字符（32 bit）
    seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
    np.random.seed(int(seed_hex[:8], 16))

    # ---- 模型构建：opt_init（_ppo_save tar）优先，否则 init 权重 + 新 opt ----
    # hub 侧免 torch（D2）：模型权重/opt 由 tar 或 weights_json 提供，worker 负责
    # 重建——tar 内 model.pt = 上一轮 PPO 终态（含 Adam 动量，D5）。
    init_w = job_dir / "init_weights.json"
    if not init_w.exists():
        raise ProtocolError("payload 缺 init_weights.json——无法构建模型")
    model = ppo_engine.build_ppo(str(init_w))
    device_t = torch.device(device)
    opt = None
    if manifest.get("opt_init"):
        opt_dir = job_dir / "opt_init"
        unpack_opt_tar(decode_opt_tar(str(manifest["opt_init"])), opt_dir)
        # 必须在 model.to(device_t) 之前加载 state_dict，然后统一移到目标设备
        model.load_state_dict(torch.load(opt_dir / "model.pt", map_location="cpu"))
        model.to(device_t)
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        opt.load_state_dict(torch.load(opt_dir / "opt.pt", map_location=device_t))
        log(f"job {jid}: model/opt 从 opt_init tar 恢复（Adam 动量延续，D5）")
    else:
        # 首轮/无 tar：从 init 权重 warm-start（hub 打包时写入 payload 的 weights_json）
        load_state_into(model, str(init_w))
        model.to(device_t)
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        log(f"job {jid}: 无 opt_init，从 init_weights warm-start + 新 Adam")

    # ---- PPO：load → chunk → update（同一 backend 调用链，D4） ----
    shards_root = str(job_dir)
    t_ppo = time.time()
    episodes = ppo_engine.load_episodes(
        shards_root,
        float(manifest["gamma"]),
        float(manifest["lam"]),
        normalize_adv=str(manifest["adv_norm"]) != "none",
    )
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    chunks = ppo_engine.chunk_episodes(episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"]))
    agg = ppo_engine.ppo_update(
        model,
        opt,
        chunks,
        int(manifest["epochs"]),
        device_t,
        kl_coef=float(manifest["kl_coef"]),
    )
    ppo_sec = round(time.time() - t_ppo, 1)
    log(f"job {jid}: PPO done in {ppo_sec}s, steps={total_steps} chunks={len(chunks)} kl={agg.get('kl')}")

    # ---- 产物：weights_json（save_weights_json，D12/G1）+ _ppo_save tar（D5） ----
    model.to("cpu")
    wj_path = job_dir / "weights.json"
    save_weights_json(model, str(wj_path))
    ckpt_dir = job_dir / "ppo_final"
    from ppo.common import _ppo_save

    _ppo_save(str(ckpt_dir), model, opt, int(manifest["epochs"]))
    opt_tar_b64 = encode_opt_tar(pack_opt_tar(ckpt_dir))

    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_path.read_bytes()),
        "opt_tar_b64": opt_tar_b64,
        "agg": {
            "policy": float(agg.get("policy", 0.0)),
            "value": float(agg.get("value", 0.0)),
            "entropy": float(agg.get("entropy", 0.0)),
            "kl": float(agg.get("kl", 0.0)),
            "mean_ret": float(agg.get("mean_ret", 0.0)),
            "steps": int(total_steps),
            "chunks": len(chunks),
        },
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    validate_result(result, manifest, commit_echo_must_match=False)  # 自查
    _persist_result(work_dir, jid, result)
    return result


# ------------------------------------------------------------------ 主循环

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
    log=lambda msg: print(
        f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True
    ),
) -> int:
    """无状态轮询主循环。返回处理的 job 数。

    once=True：处理一个 job 后退出（M1 假云回环冒烟用）。
    max_idle_sec>0：连续空闲超过该时长退出（M2 会话活性观测用）。
    """
    done = 0
    idle_since = time.time()
    _last_alive_log = time.time()
    while True:
        try:
            job = poll_job(base_url, token)
        except Exception as e:
            log(f"poll failed: {e} — retry in {poll_sec}s")
            time.sleep(poll_sec)
            continue
        if job is None:
            if once:
                break  # --once：无 job 或已处理完都退出（冒烟/单发）
            if max_idle_sec > 0 and time.time() - idle_since > max_idle_sec:
                log(f"idle > {max_idle_sec}s — exit")
                break
            # 每 30s 打一次 alive 日志，让用户知道 worker 在正常运行
            if time.time() - _last_alive_log > 30:
                log(f"polling hub (no job yet, {done} done)")
                _last_alive_log = time.time()
            time.sleep(poll_sec)
            continue
        idle_since = time.time()
        jid = job["job_id"]
        lease_token = str(job.get("lease_token", "") or "")
        log(f"job {jid} claimed — downloading payload")
        # H1（review-hy P0）：心跳必须在 **job 执行期间**持续（下载 + PPO 可能几十分钟，
        # 期间不发心跳 → 租约 30min 过期 → job 回池被重领 → 双跑 + 结果竞态）。
        # 守护心跳线程 60s 周期续租（HEARTBEAT_SEC），job 结束 join。
        _hb_stop = threading.Event()

        def _hb_loop() -> None:
            while not _hb_stop.wait(HEARTBEAT_SEC):
                heartbeat(base_url, token, jid, lease_token)

        hb_thread = threading.Thread(target=_hb_loop, daemon=True, name=f"hb-{jid[:8]}")
        hb_thread.start()
        job_ok = False
        try:
            result = run_job(base_url, token, job, work_dir=work_dir, device=device,
                             torch_threads=torch_threads, echo=echo, log=log)
            post_result(base_url, token, jid, result, lease_token=lease_token)
            log(f"job {jid} done — result accepted")
            done += 1
            job_ok = True
        except RetryableError as e:
            # 瞬时失败（网络/5xx/传输损坏）：主动还租约立即回池——不再付 30min 过期等待
            log(f"job {jid} 瞬时失败: {e} — release 租约回池，立即可重领")
            release_job(base_url, token, jid, lease_token, log=log)
        except ProtocolError as e:
            log(f"job {jid} REJECTED: {e} — skip (not retried)")
            # 确定性拒绝（commit 不符/模式不符）不重试——轮询下一个
        except Exception as e:
            log(f"job {jid} FAILED: {type(e).__name__}: {e} — will re-poll (idempotent)")
            # 瞬态失败（网络/远端关闭）：租约未续会自动回池，重拉同 job 幂等
        finally:
            _hb_stop.set()
            hb_thread.join(timeout=HEARTBEAT_SEC + 5)
        if once:
            # H8（review-hy）：--once 模式 job 失败必须非零退出——冒烟/单发场景
            # 退出码 0 会静默掩盖失败（smoke 只判 returncode）
            return -1 if not job_ok else done
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="remote PPO worker (cloud, stateless)")
    ap.add_argument("--poll", required=True, help="hub-server base URL, e.g. https://hub.example.com")
    ap.add_argument("--token", default="", help="bearer token（与 hub-server 一致）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument("--out", default="tmp/remote-worker", help="work dir (payloads/ckpts)")
    ap.add_argument("--device", default="cpu", help="torch device: cpu / cuda / cuda:0")
    ap.add_argument("--threads", type=int, default=0, help="torch intra-op threads (0=default)")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="处理一个 job 后退出")
    ap.add_argument(
        "--echo",
        action="store_true",
        help="冒烟：跳过 PPO，回显 init 权重为结果（hub-start 冒烟预演；消费方作废本轮）",
    )
    ap.add_argument("--max-idle-sec", type=float, default=0.0, help="空闲超时退出（0=永不）")
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[worker] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print("[worker] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    n = worker_loop(
        args.poll,
        token,
        work_dir=Path(args.out),
        device=args.device,
        torch_threads=args.threads,
        poll_sec=args.poll_sec,
        once=args.once,
        echo=args.echo,
        max_idle_sec=args.max_idle_sec,
    )
    print(f"[worker] done: {n} job(s) processed")
    # H8：--once 失败（返回 -1）→ 非零退出码
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
