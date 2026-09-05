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


def download_payload(base_url: str, token: str, jid: str) -> bytes:
    status, body = _request(base_url, token, f"/jobs/{jid}/payload", timeout=300.0)
    if status != 200:
        raise ProtocolError(f"payload download failed: HTTP {status}")
    return body


def post_result(
    base_url: str,
    token: str,
    jid: str,
    result: dict,
    lease_token: str = "",
    timeout: float = 120.0,
) -> int:
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
    if status not in (200, 201):
        raise ProtocolError(f"result POST failed: HTTP {status}: {body[:300].decode('utf-8', 'replace')}")
    return status


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


def run_job(
    base_url: str,
    token: str,
    job: dict,
    *,
    work_dir: Path,
    device: str = "cpu",
    torch_threads: int = 0,
    log=lambda msg: print(
        f"[{time.strftime('%H:%M:%S')}] [worker] {msg}", flush=True
    ),
) -> dict:
    """执行单个 job：下载 → 校验 → PPO → 产出 weights_json + opt tar → POST。

    返回 hub 侧需要的 result dict（未 POST——调用方决定上传时机；本函数
    负责完成 PPO 与产物）。幂等：已完成（result 已落盘）的 job 由 hub 返回
    404/409，调用方跳过。
    """
    jid = job["job_id"]
    manifest = normalize_manifest(job["manifest"])

    # ---- 下载 + payload_sha256 校验（D1：防隧道截断；对 job 记录的 manifest 校验） ----
    raw = download_payload(base_url, token, jid)
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        raise ProtocolError("payload_sha256 不匹配——传输损坏（拒收）")

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

    # ---- commit 校验（D6：两端代码一致） ----
    local_head = _git_head()
    if local_head and local_head != manifest["commit"]:
        raise ProtocolError(
            f"commit 不匹配：manifest={manifest['commit'][:12]} 本地={local_head[:12]}——"
            "云 worker 需 checkout hub 的 commit（git fetch + checkout）"
        )

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
    opt = None
    if manifest.get("opt_init"):
        opt_dir = job_dir / "opt_init"
        unpack_opt_tar(decode_opt_tar(str(manifest["opt_init"])), opt_dir)
        model.load_state_dict(torch.load(opt_dir / "model.pt", map_location="cpu"))
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        opt.load_state_dict(torch.load(opt_dir / "opt.pt", map_location="cpu"))
        log(f"job {jid}: model/opt 从 opt_init tar 恢复（Adam 动量延续，D5）")
    else:
        # 首轮/无 tar：从 init 权重 warm-start（hub 打包时写入 payload 的 weights_json）
        load_state_into(model, str(init_w))
        opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
        log(f"job {jid}: 无 opt_init，从 init_weights warm-start + 新 Adam")

    device_t = torch.device(device)
    model.to(device_t)

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
                             torch_threads=torch_threads, log=log)
            post_result(base_url, token, jid, result, lease_token=lease_token)
            log(f"job {jid} done — result accepted")
            done += 1
            job_ok = True
        except ProtocolError as e:
            log(f"job {jid} REJECTED: {e} — skip (not retried)")
            # 确定性拒绝（commit 不符/模式不符/损坏）不重试——轮询下一个
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
        max_idle_sec=args.max_idle_sec,
    )
    print(f"[worker] done: {n} job(s) processed")
    # H8：--once 失败（返回 -1）→ 非零退出码
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
