"""remote/hub_client.py — hub 侧远程 PPO 客户端（TrainingLoop 远程分支使用）。

职责（D11/D12）：训练主循环只做「打包 → 发布 → 轮询/等待 → 校验落位」——
job 队列/租约/鉴权全在旁路 hub-server（remote/hub_server.py）。

**发布 = 磁盘 IPC**（§3.1/附录 C）：训练主循环把 payload.zip + manifest.json
写入 `job_root/<job_id>/`，并追加 `job_pending` 事件到 jsonl 账本；hub-server
（独立进程）重读 jsonl + job 目录即可重建可领取池（D8），训练进程与 server
进程互不阻塞。**等待/取结果 = HTTP**：轮询 server 的 GET /jobs/{id}/result
（worker 回传已由 server 落盘）。

本模块（hub 侧）全程免 torch（D2）：打包只做文件搬运 + sha256；weights_json
由云 worker 产出（D12 产出方锁死）。顶层零 torch。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tarfile
import time
from pathlib import Path

from remote.protocol import (
    AUTH_HEADER,
    data_fp,
    decode_opt_tar,
    decode_weights_json,
    job_seed,
    normalize_manifest,
)
from remote.protocol import (
    job_id as make_job_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class HubClientError(RuntimeError):
    """hub 侧远程 PPO 失败（发布/等待/校验任一环）。"""


def git_head(repo_root: Path = REPO_ROOT) -> str:
    """hub 当前 commit（manifest.commit；云 worker 据此 checkout，D6）。

    H4（review-hy）：工作区 dirty-tree 检查在 _remote_ppo（loop_steps.py）调用方
    完成（更详细的 fail-fast 信息）；本函数只做 commit 解析，供 smoke_loopback 等
    各方使用（这些场景可能有未跟踪文件且不生产发布 job）。
    """
    import subprocess

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
    raise HubClientError("无法解析 git HEAD——远程模式要求 hub 在 git 工作区内")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ 打包

def iter_shard_dirs(traj_dir: str | Path, it: int) -> list[Path]:
    """本轮应训 shard 集：it{it} 下全部 rl_s*_seed*/manifest.json 目录（与
    `_serial_ppo` 的 load_episodes 装载口径一致——D1「wver 过滤 + resume 剔除
    后」由 _prepare_iter_dir 已保证目录内只有本轮 wver 匹配的完整 shard）。
    """
    it_dir = Path(traj_dir) / f"it{it}"
    if not it_dir.exists():
        return []
    dirs = sorted({p.parent for p in it_dir.rglob("rl_s*_seed*/manifest.json")})
    return [d for d in dirs if (d / "obs.npy").exists() or (d / "metrics.npy").exists()]


def pack_payload_zip(
    shard_dirs: list[Path],
    extra_files: list[Path],
    zip_path: Path,
    manifest: dict,
) -> str:
    """把 shard 目录 + 额外文件（init_weights.json / opt_init.tar.b64）+ manifest
    打成 payload.zip。返回 zip 字节 sha256。zip 布局与 worker 的 unpack_payload
    约定一致（shard 目录整体 + manifest.json + init_weights.json + opt_init.tar.b64）。
    """
    import zipfile

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for d in shard_dirs:
            for f in sorted(d.iterdir()):
                if f.is_file():
                    z.write(f, arcname=f"{d.name}/{f.name}")
        for p in extra_files:
            if p.exists():
                z.write(p, arcname=p.name)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return _sha256_bytes(zip_path.read_bytes())


# ------------------------------------------------------------------ 发布（磁盘 IPC）

def publish_job(
    *,
    job_root: str | Path,
    jsonl_path: str | Path,
    run_id: str,
    it: int,
    traj_dir: str | Path,
    shard_dirs: list[Path],
    init_weights_path: str,
    ckpt_remote_dir: str | Path | None,
    commit: str,
    course: str,
    course_fp: str,
    reward_formula: str,
    formula_hash: str,
    metrics_version: int,
    gamma: float,
    lam: float,
    mode: str,
    epochs: int,
    mb: int,
    lr: float,
    kl_coef: float,
    kl_cap: float | None,
    adv_norm: str,
    shuffle: bool,
    schedule_raw: list,
    log=lambda msg: print(f"[hub] {msg}", flush=True),
) -> dict:
    """打包 + 发布 job（磁盘 IPC）：job_root/<job_id>/ + jsonl job_pending 事件。

    返回已归一化 manifest（含 job_id / payload_sha256）。幂等：同 job_id
    （幂等键相同）已发布 → 覆盖 payload、不重复追加 pending。
    """
    job_root_p = Path(job_root)
    # 1) data_fp（D1：排序 shard 路径 + manifest {wver,stage,seed}）
    fp = data_fp(shard_dirs)
    # 2) init_weights_fp（fencing：云返回的 init_weights_fp 必须等于当前 args.out 指纹）
    init_weights_fp = _sha256_file(init_weights_path)
    # 3) opt_init tar（上轮 ppo_ckpt_remote；空 = 首轮，D5）
    opt_init = _pack_opt_init(ckpt_remote_dir)
    # 4) manifest 预建（payload_sha256 占位）→ 打包（zip 内 manifest 为占位副本）
    extra_files: list[Path] = []
    tmp_extra_dir = job_root_p / ".extra_tmp"
    tmp_extra_dir.mkdir(parents=True, exist_ok=True)
    init_copy = tmp_extra_dir / "init_weights.json"
    shutil.copyfile(init_weights_path, init_copy)
    extra_files.append(init_copy)
    if opt_init:
        opt_copy = tmp_extra_dir / "opt_init.tar.b64"
        opt_copy.write_text(opt_init, encoding="utf-8")
        extra_files.append(opt_copy)
    m = {
        "proto": 1,
        "runId": run_id,
        "it": it,
        "commit": commit,
        "course": course,
        "course_fp": course_fp,
        "reward_formula": reward_formula,
        "formula_hash": formula_hash,
        "metrics_version": metrics_version,
        "gamma": gamma,
        "lam": lam,
        "mode": mode,
        "epochs": epochs,
        "mb": mb,
        "lr": lr,
        "kl_coef": kl_coef,
        "kl_cap": kl_cap,
        "adv_norm": adv_norm,
        "shuffle": shuffle,
        "schedule_raw": schedule_raw,
        "init_weights_fp": init_weights_fp,
        "opt_init": opt_init,
        "data_fp": fp,
        "payload_sha256": "",
    }
    m["seed"] = job_seed(run_id, it, init_weights_fp)
    m["job_id"] = make_job_id(m)
    # 5) 落盘 job 目录：payload.zip（zip 内 manifest 为占位副本——payload_sha256 尚
    #    未算出）→ 回填真实 sha → 权威 manifest.json（worker 以 job 记录校验，D1）。
    #    normalize_manifest 在回填后调用：payload_sha256 必填非空，占位空串会误拒。
    jid = str(m["job_id"])
    jd = job_root_p / jid
    jd.mkdir(parents=True, exist_ok=True)
    zip_path = jd / "payload.zip"
    sha = pack_payload_zip(shard_dirs, extra_files, zip_path, m)
    m["payload_sha256"] = sha
    m = normalize_manifest(m)
    (jd / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        shutil.rmtree(tmp_extra_dir)
    except OSError:
        pass
    # 6) jsonl job_pending（磁盘 IPC；幂等去重——同 job_id 不重复追加）
    _append_ledger(jsonl_path, {
        "event": "job_pending",
        "job_id": jid,
        "runId": run_id,
        "it": it,
        "ts": time.time(),
    })
    log(f"published job {jid} it{it}: shards={len(shard_dirs)} data_fp={fp[:12]}… payload={sha[:12]}…")
    return m


def _pack_opt_init(ckpt_remote_dir: str | Path | None) -> str:
    """上轮 ppo_ckpt_remote → base64 tar；空 = ""。

    H5（review-hy）：只打 model.pt + opt.pt（Adam 动量，D5）——state.json 的 numpy
    RNG 状态无人读取（worker 按 per-job 种子重播），不往返死数据。"""
    if not ckpt_remote_dir:
        return ""
    d = Path(ckpt_remote_dir)
    names = [n for n in ("model.pt", "opt.pt") if (d / n).exists()]
    if not names:
        return ""
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for n in names:
            tf.add(d / n, arcname=n)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _append_ledger(jsonl_path: str | Path, event: dict) -> None:
    p = Path(jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def mark_job_completed(jsonl_path: str | Path, jid: str) -> None:
    """验收落位后写 job_completed 账本事件（§3.1 双态；幂等）。"""
    p = Path(jsonl_path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "job_completed" and e.get("job_id") == jid:
            return  # 已存在
    _append_ledger(p, {"event": "job_completed", "job_id": jid, "ts": time.time()})


# ------------------------------------------------------------------ 等待（HTTP）

def _request(
    base_url: str,
    token: str,
    path: str,
    timeout: float = 30.0,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

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


def wait_job(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout_sec: float = 25 * 60,
    poll_sec: float = 5.0,
    log=lambda msg: print(f"[hub] {msg}", flush=True),
) -> dict:
    """阻塞等待 job 完成（worker 已 POST 结果）→ 返回结果 dict。超时抛 HubClientError。

    H3（review-hy）：默认超时 25min **严格小于** LEASE_SEC=30min——否则 hub 判超时的
    瞬间恰是 job 回池的瞬间，存在「hub 放弃 / 云恰好回传」的双花窗口。超时前先
    GET /jobs/{id}/status 二次确认（done → 直接取结果；leased → 云还在跑，继续等）。
    云 worker 侧有 60s 守护心跳续租（H1），正常 PPO 不会被 30min 租约打断。
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status, body = _request(base_url, token, f"/jobs/{jid}/result", timeout=30.0)
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
            raise HubClientError(f"wait_job: job {jid} 结果非对象: {type(loaded).__name__}")
        if status == 404:
            time.sleep(poll_sec)
            continue
        raise HubClientError(f"wait_job: HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
    # H3：超时前二次确认——leased（云仍在跑）→ 延长等待；done → 直接取结果
    s_status, s_body = _request(base_url, token, f"/jobs/{jid}/status", timeout=15.0)
    if s_status == 200:
        state = json.loads(s_body.decode("utf-8")).get("state")
        if state == "done":
            st2, body2 = _request(base_url, token, f"/jobs/{jid}/result", timeout=15.0)
            if st2 == 200:
                loaded = json.loads(body2.decode("utf-8"))
                if isinstance(loaded, dict):
                    return loaded
        elif state == "leased":
            log(f"wait_job: job {jid} 仍在 leased（云 PPO 执行中）——再等 {timeout_sec}s")
            return wait_job(base_url, token, jid, timeout_sec=timeout_sec, poll_sec=poll_sec, log=log)
    raise HubClientError(f"wait_job: job {jid} 超时（>{timeout_sec}s）未完成")


# ------------------------------------------------------------------ 校验落位

def verify_and_land(
    result: dict,
    manifest: dict,
    *,
    init_weights_path: str,
    traj_dir: str | Path,
    it: int,
    out_weights: str,
    log=lambda msg: print(f"[hub] {msg}", flush=True),
) -> str:
    """三重校验（D12）+ 落盘（weights_json → args.out；opt tar → ppo_ckpt_remote）。

    校验：
      1. init_weights_fp == 当前 args.out 指纹（fencing：云从 hub 打包的 init 起步）；
      2. data_fp == 本地重算（对本轮 shard 集重算比对——防云训练了别的语料）；
      3. commit 一致（result.commit_echo == manifest.commit）。
    任一不等 → 响亮拒绝（抛 HubClientError），不落盘。
    返回落盘 weights 的指纹（供 wver 下游直接使用）。
    """
    m = normalize_manifest(manifest)
    if result["init_weights_fp"] != _sha256_file(init_weights_path):
        raise HubClientError(
            "三重校验失败: init_weights_fp 不匹配（云起点 ≠ 当前 args.out）——拒收"
        )
    local_fp = data_fp(iter_shard_dirs(traj_dir, it))
    if result["data_fp"] != local_fp:
        raise HubClientError(
            f"三重校验失败: data_fp 不匹配（云={result['data_fp'][:12]}… "
            f"本地={local_fp[:12]}…）——拒收"
        )
    if result["commit_echo"] != m["commit"]:
        raise HubClientError(
            f"三重校验失败: commit_echo={result['commit_echo'][:12]}… != "
            f"manifest.commit={m['commit'][:12]}…——拒收"
        )
    # 落盘 weights_json（原子 replace）
    wj = decode_weights_json(str(result["weights_json"]))
    out_p = Path(out_weights)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_p.with_suffix(out_p.suffix + ".remote.tmp")
    tmp.write_bytes(wj)
    os.replace(tmp, out_p)
    wver = _sha256_file(str(out_p))
    log(f"weights landed -> {out_weights} ({len(wj)} bytes, wver={wver[:12]}…)")
    # 落盘 opt tar（D5：Adam 动量随 job 往返）
    opt_tar = decode_opt_tar(str(result["opt_tar_b64"]))
    ckpt_dir = Path(traj_dir) / f"it{it}" / "ppo_ckpt_remote"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    _extract_tar(opt_tar, ckpt_dir)
    log(f"opt ckpt landed -> {ckpt_dir}")
    return wver


def _extract_tar(tar_bytes: bytes, dest: Path) -> None:
    import io

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest)
