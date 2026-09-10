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

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import rmtree_best_effort
from remote.protocol import (
    AUTH_HEADER,
    PAYLOAD_NAME,
    PAYLOAD_XZ_PRESET,
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
        r: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            **_POPEN_NO_WINDOW,
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


def iter_shard_dirs(traj_dir: str | Path, it: int, log=lambda msg: None) -> list[Path]:
    """本轮应训 shard 集：it{it} 下全部 rl_s*_seed*/manifest.json 目录（与
    `_serial_ppo` 的 load_episodes 装载口径一致——D1「wver 过滤 + resume 剔除
    后」由 _prepare_iter_dir 已保证目录内只有本轮 wver 匹配的完整 shard）。

    同名 shard 去重（发布端不变量）：同一 seed 只允许一份进 payload——重复
    arcname 的 zip 由解包顺序决定训练吃哪份（偶然语义），且 data_fp 账面与
    expectedGames 不平。正常路径由 dispatch 结算退场输家副本（2026-09-06），
    这里兜底崩溃/历史残留：按 manifest.json mtime 保留最早一份（先写盘者 =
    结算赢家），退役者响亮日志。
    """
    it_dir = Path(traj_dir) / f"it{it}"
    if not it_dir.exists():
        return []
    cands: dict[str, list[Path]] = {}
    for p in it_dir.rglob("rl_s*_seed*/manifest.json"):
        d = p.parent
        if not ((d / "obs.npy").exists() or (d / "metrics.npy").exists()):
            continue
        cands.setdefault(d.name, []).append(d)
    dirs: list[Path] = []
    for name in sorted(cands):
        ds = cands[name]
        if len(ds) > 1:
            ds.sort(key=lambda d: ((d / "manifest.json").stat().st_mtime, str(d)))
            for loser in ds[1:]:
                log(f"[publish] duplicate shard {name}: retire {loser} (keep {ds[0]})")
        dirs.append(ds[0])
    return dirs


def pack_payload_zip(
    shard_dirs: list[Path],
    extra_files: list[Path],
    zip_path: Path,
    manifest: dict,
) -> str:
    """把 shard 目录 + 额外文件（init_weights.json / opt_init.tar.b64）+ manifest
    打成 payload 归档（**tar.xz**，2026-09-10 起；实测体积 −48.7% 而打包耗时持平）。
    返回归档字节 sha256。布局与 worker 的 unpack_payload 约定一致
    （shard 目录整体 + manifest.json + init_weights.json + opt_init.tar.b64）。
    """
    import io
    import tarfile

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(zip_path, "w:xz", preset=PAYLOAD_XZ_PRESET) as tf:
        for d in shard_dirs:
            for f in sorted(d.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{d.name}/{f.name}")
        for p in extra_files:
            if p.exists():
                tf.add(p, arcname=p.name)
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return _sha256_bytes(zip_path.read_bytes())


def pack_code_zip(
    nn_root: str | Path,
    zip_path: str | Path,
    *,
    log=lambda msg: None,
) -> str:
    """打包 nn-training Python 源文件为 code.zip（hub 启动时一次打包，避免后继
    并行修改干扰云端代码一致性）。

    包含：所有 .py + .jsonc 文件（递归），排除 tmp/ weights/ .venv/ __pycache__/
    tests/ rl-config.json。

    返回 zip 字节 sha256。
    """
    import zipfile

    nn_root_p = Path(nn_root).resolve()
    zip_path_p = Path(zip_path)
    zip_path_p.parent.mkdir(parents=True, exist_ok=True)

    _exclude_dirs = {
        "tmp",
        "weights",
        ".venv",
        "__pycache__",
        "tests",
        ".mypy_cache",
        ".ruff_cache",
    }
    _exclude_files = {"rl-config.json"}

    n_files = 0
    with zipfile.ZipFile(zip_path_p, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(nn_root_p):
            dir_p = Path(dirpath)
            # 跳过排除目录（os.walk 修改 dirnames 原地剪枝，避免遍历进入）
            rel = dir_p.relative_to(nn_root_p)
            parts = rel.parts
            if any(p in _exclude_dirs for p in parts):
                dirnames[:] = []
                continue
            for fn in sorted(filenames):
                if fn in _exclude_files:
                    continue
                if not fn.endswith((".py", ".jsonc")):
                    continue
                f = dir_p / fn
                try:
                    if not f.is_file():
                        continue
                except OSError:
                    continue
                # 固定时间戳打包：sha 只由文件内容决定（否则 mtime 参与 →
                # 同内容重打包 sha 漂移，云端代码缓存永远无法命中）
                zi = zipfile.ZipInfo(
                    str(rel / fn).replace("\\", "/"), date_time=(1980, 1, 1, 0, 0, 0)
                )
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                z.writestr(zi, f.read_bytes())
                n_files += 1
    # 清理 dirnames 修改后残留的记录（不出现在 zip 中，已正确跳过）
    sha = _sha256_bytes(zip_path_p.read_bytes())
    if log:
        log(f"code.zip: {n_files} files, {zip_path_p.stat().st_size} bytes, sha256={sha[:12]}…")
    return sha


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
    code_sha256: str,
    code_zip_path: str | Path | None = None,
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
    normalize_ret: bool = False,
    kickstart_kl: float = 0.0,
    ref_weights_b64: str = "",
    ref_weights_fp: str = "",
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
        "code_sha256": code_sha256,
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
        "normalize_ret": bool(normalize_ret),
        "kickstart_kl": float(kickstart_kl),
        "ref_weights_b64": ref_weights_b64,
        "ref_weights_fp": ref_weights_fp,
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
    zip_path = jd / PAYLOAD_NAME
    sha = pack_payload_zip(shard_dirs, extra_files, zip_path, m)
    m["payload_sha256"] = sha
    m = normalize_manifest(m)
    (jd / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    # 复制 code.zip 到 job 目录（hub-server 的 GET /jobs/{id}/code 从此目录服务）
    if code_zip_path:
        czp = Path(code_zip_path)
        if czp.exists():
            shutil.copy2(czp, jd / "code.zip")
    try:
        rmtree_best_effort(tmp_extra_dir)
    except OSError:
        pass
    # 6) jsonl job_pending（磁盘 IPC；幂等去重——同 job_id 不重复追加）
    # 悬空 job 清理（§381）：发布前作废更早迭代/旧 runId 遗留的 pending job——
    # 否则 loop 重启（runId 变 → 同 it 新 jid）后，无 worker 期间滞留的旧 job
    # 会在 GPU 上线时被全部补做（白烧 GPU + PPO 数 ≠ iteration 数）。
    _n_cancelled = cancel_stale_jobs(jsonl_path, it, jid)
    if _n_cancelled:
        log(f"cancelled {_n_cancelled} stale job(s) with it ≤ {it}（旧 runId 遗留，不再派发）")
    _append_ledger(
        jsonl_path,
        {
            "event": "job_pending",
            "job_id": jid,
            "runId": run_id,
            "it": it,
            "ts": time.time(),
        },
    )
    log(
        f"published job {jid} it{it}: shards={len(shard_dirs)} data_fp={fp[:12]}… payload={sha[:12]}…"
    )
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


def cancel_stale_jobs(jsonl_path: str | Path, it: int, keep_job_id: str) -> int:
    """作废滞后迭代 / 旧 runId 遗留的 pending job（写 job_cancelled 账本事件，幂等）。

    §381（2026-09-08）：loop 重启 runId 变 → 同一 it 生成新 jid；无 worker 期间
    发布的旧 pending 会滞留池中，GPU 上线后按发布序全部补做——白烧 GPU 且让
    「PPO 完成数 ≠ iteration 行数」，控制台看起来像丢数据。

    发布新 job 时作废所有 `it <= 当前 it` 且非本次 job_id 的 pending job：
    同 runId 每 it 只发布一次（wait_job 阻塞后才进下一轮），故命中的必然属于
    旧 runId 遗留 / 已被更新的同 it 覆盖。hub 侧（claimable_job_ids）识别
    job_cancelled 后不再派发。返回本次作废数。
    """
    if not isinstance(it, int):
        return 0
    p = Path(jsonl_path)
    if not p.exists():
        return 0
    terminal: dict[str, str] = {}  # jid -> 终态事件（completed / cancelled）
    pendings: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("event")
        jid = e.get("job_id")
        if not (isinstance(ev, str) and isinstance(jid, str)):
            continue
        if ev == "job_pending":
            pendings[jid] = e
        elif ev in ("job_completed", "job_cancelled"):
            terminal[jid] = ev
    n = 0
    for jid, e in pendings.items():
        if jid == keep_job_id or jid in terminal:
            continue
        jit = e.get("it")
        if not (isinstance(jit, int) and jit <= it):
            continue
        _append_ledger(p, {"event": "job_cancelled", "job_id": jid, "it": jit, "ts": time.time()})
        n += 1
    return n


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
    poll_max_sec: float = 60.0,
    log=lambda msg: print(f"[hub] {msg}", flush=True),
) -> dict:
    """阻塞等待 job 完成（worker 已 POST 结果）→ 返回结果 dict。超时抛 HubClientError。

    R9（2026-09-10 c6 it50 事故，plan/feasibility-map.md §12）：**轮询退避**。
    此前网络错误/5xx 一律固定 `poll_sec` 重试——隧道抖动期间这是固定频率猛敲一个
    已经不可达的边缘（cloudflared `region1.v2.argotunnel.com i/o timeout` 持续
    6h），既救不回 job 也放大噪声。连续错误按 2 的幂退避（`poll_max_sec` 封顶），
    一次成功即复位。404（job 还没回）是**正常等待**，不走退避。
    """
    deadline = time.time() + timeout_sec
    err_streak = 0
    while time.time() < deadline:
        try:
            status, body = _request(base_url, token, f"/jobs/{jid}/result", timeout=30.0)
        except Exception as e:
            # 瞬时网络错误（快速隧道抖动/DNS/连接重置）——与 404 同等处理，续等；
            # 2026-09-05：此前单次错误直接抛 HubClientError 会废掉整轮迭代
            # （loop 连击 retry），对 24/7 隧道运营是可靠性缺陷。
            err_streak += 1
            backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
            log(
                f"wait_job: job {jid} 轮询网络错误 ({type(e).__name__}) "
                f"— 连续第 {err_streak} 次，{backoff:.0f}s 后退避重试"
            )
            time.sleep(backoff)
            continue
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
            raise HubClientError(f"wait_job: job {jid} 结果非对象: {type(loaded).__name__}")
        if status == 404:
            err_streak = 0  # 还没回 = 正常排队，复位退避
            time.sleep(poll_sec)
            continue
        if status >= 500:
            # 隧道/边缘瞬时 5xx（Cloudflare 错误页等）——容忍至 deadline
            err_streak += 1
            backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
            log(
                f"wait_job: job {jid} HTTP {status}（瞬时错误）— 连续第 {err_streak} 次，"
                f"{backoff:.0f}s 后退避重试"
            )
            time.sleep(backoff)
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
            return wait_job(
                base_url, token, jid, timeout_sec=timeout_sec, poll_sec=poll_sec, log=log
            )
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
        raise HubClientError("三重校验失败: init_weights_fp 不匹配（云起点 ≠ 当前 args.out）——拒收")
    local_fp = data_fp(iter_shard_dirs(traj_dir, it, log=log))
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
