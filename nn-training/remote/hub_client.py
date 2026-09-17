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


def iter_bc_shard_dirs(
    traj_dir: str | Path, it: int, round_name: str = "", log=lambda msg: None
) -> list[Path]:
    """本轮 BC 语料 shard 集（plan/bc-cloud-integration.plan.md §4）：
    `<traj>/bc-data/<round>/bc_s*_seed*/`（含 manifest.json）；round 缺省 = `it{it}`，
    smoke 轮传 "smoke"（冒烟语料与真轮隔离，2026-09-13）。

    同名 shard 去重与 PPO（iter_shard_dirs）同策略：按 manifest mtime 保留最早一份
    （先写盘者 = 结算赢家），退役者响亮日志。"""
    data_root = Path(traj_dir) / "bc-data" / (round_name or f"it{it}")
    if not data_root.exists():
        return []
    groups: dict[str, list[Path]] = {}
    for p in data_root.glob("bc_s*_seed*"):
        if p.is_dir() and (p / "manifest.json").is_file():
            groups.setdefault(p.name, []).append(p)
    dirs: list[Path] = []
    for name in sorted(groups):
        ds = groups[name]
        if len(ds) > 1:
            ds.sort(key=lambda d: ((d / "manifest.json").stat().st_mtime, str(d)))
            for loser in ds[1:]:
                log(f"[publish] duplicate bc shard {name}: retire {loser} (keep {ds[0]})")
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

    包含：所有 .py + .jsonc 文件（递归）。
    排除：`tmp/` `weights/` `__pycache__/` `tests/` `rl-config.json`，
    **以及任何以 `.` 开头的目录**（`.venv` / `.venv310bak` / `.mypy_cache` /
    `.ruff_cache` / `.pytest_cache` / `.git` … —— 一网打尽，不靠逐个列举）。

    返回 zip 字节 sha256。
    """
    import zipfile

    nn_root_p = Path(nn_root).resolve()
    zip_path_p = Path(zip_path)
    zip_path_p.parent.mkdir(parents=True, exist_ok=True)

    # 非点目录的显式名单；点目录由下面的 `startswith(".")` 统一覆盖
    _exclude_dirs = {
        "tmp",
        "weights",
        "__pycache__",
        "tests",
    }
    _exclude_files = {"rl-config.json"}

    n_files = 0
    with zipfile.ZipFile(zip_path_p, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(nn_root_p):
            dir_p = Path(dirpath)
            # 跳过排除目录（os.walk 修改 dirnames 原地剪枝，避免遍历进入）
            rel = dir_p.relative_to(nn_root_p)
            parts = rel.parts
            # 显式名单 ∪ **任何点目录**。2026-09-15 实测事故：名单里只写了 `.venv`，
            # 于是解释器升级留下的 `.venv310bak` 被整棵打进 code.zip —— 4584 个 .py /
            # 80.6 MB（占 97% 字节），每次 push 白传 20 MB，云端还解包出一个假 venv
            # 放在 sys.path[0]。逐个列举名字防不住下一个 `.venv312bak`，故一律排除点目录。
            if any(p in _exclude_dirs or p.startswith(".") for p in parts):
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
    commit: str,
    code_sha256: str,
    code_zip_path: str | Path | None = None,
    course: str,
    course_fp: str,
    # 课程短名（plan P4-W2 / S9 归属）：`args.course_name`。三字段分工——`course` =
    # 课程 jsonc 全文快照（D6 重建输入）；`course_name` = 可读短名（审计冗余）；
    # `course_fp` = 血缘（D14 熔断）。空串 = 旧调用，manifest 不添键（字节不变）。
    course_name: str = "",
    # D14 语义版（§2026-09-13-level-extraction）：语料身份 = env+reward 解析值哈希。
    # 与 course_fp 并存进 manifest；worker 装载校验优先比它（预算/路径类课程编辑
    # 只动 course_fp，不得触发 shard 拒收）。空 = 缺席（worker 回退 legacy 比对）。
    corpus_fp: str = "",
    init_weights_path: str = "",
    ckpt_remote_dir: str | Path | None = None,
    reward_formula: str = "",
    formula_hash: str = "",
    metrics_version: int = 0,
    gamma: float = 0.0,
    lam: float = 0.0,
    mode: str,
    epochs: int,
    mb: int,
    lr: float,
    kl_coef: float = 0.0,
    kl_cap: float | None = None,
    adv_norm: str = "auto",
    normalize_ret: bool = False,
    kickstart_kl: float = 0.0,
    # 熵正则系数（2026-09-11 接线）：None = 用引擎常量 ENT_COEF（0.01）。
    ent_coef: float | None = None,
    ref_weights_b64: str = "",
    ref_weights_fp: str = "",
    shuffle: bool = True,
    schedule_raw: list | None = None,
    # 严格样本量配额（target_transitions 路线）：训练侧逐关只收前 ceil(target/关数) 步。
    # 0 = 历史行为（全收）；由 run_rl 的 `_per_stage_quota()` 算好传入。
    per_stage_quota: int = 0,
    # 任务类型（BC 整合，plan/bc-cloud-integration.plan.md §4）：缺省 "ppo" =
    # 原行为逐字节不变；"bc" = 行为克隆 job——manifest 免除 PPO 专有键
    # （protocol.MANIFEST_BC_EXEMPT）并并入 `extra`（arch/val_split/mirror_p/
    # value_coef/ckpt_every）。init_weights_path 空 = BC 无 warm-start（不拷 init
    # 文件，init_weights_fp 恒 "bc"——幂等键分量仍稳定）。
    kind: str = "ppo",
    extra: dict | None = None,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> dict:
    """打包 + 发布 job（磁盘 IPC）：job_root/<job_id>/ + jsonl job_pending 事件。

    返回已归一化 manifest（含 job_id / payload_sha256）。幂等：同 job_id
    （幂等键相同）已发布 → 覆盖 payload、不重复追加 pending。
    """
    job_root_p = Path(job_root)
    kind = str(kind or "ppo")
    schedule_raw = schedule_raw if schedule_raw is not None else []
    # 1) data_fp（D1：排序 shard 路径 + manifest {wver,stage,seed}）
    fp = data_fp(shard_dirs)
    # 2) init_weights_fp（fencing：云回传的 init_weights_fp 必须等于当前 args.out 指纹；
    #    BC 无 warm-start → 恒 "bc" 占位）
    init_weights_fp = _sha256_file(init_weights_path) if init_weights_path else "bc"
    # 3) opt_init tar（上轮 ppo_ckpt_remote；空 = 首轮，D5）
    opt_init = _pack_opt_init(ckpt_remote_dir)
    # 4) manifest 预建（payload_sha256 占位）→ 打包（zip 内 manifest 为占位副本）
    extra_files: list[Path] = []
    tmp_extra_dir = job_root_p / ".extra_tmp"
    tmp_extra_dir.mkdir(parents=True, exist_ok=True)
    m = {
        "proto": 1,
        "runId": run_id,
        "it": it,
        "commit": commit,
        "code_sha256": code_sha256,
        "course": course,
        "course_fp": course_fp,
        "corpus_fp": corpus_fp,
        "mode": mode,
        "epochs": epochs,
        "mb": mb,
        "lr": lr,
        "kl_coef": kl_coef,
        "kl_cap": kl_cap,
        "adv_norm": adv_norm,
        "normalize_ret": bool(normalize_ret),
        "kickstart_kl": float(kickstart_kl),
        "ent_coef": ent_coef,
        "ref_weights_b64": ref_weights_b64,
        "ref_weights_fp": ref_weights_fp,
        "shuffle": shuffle,
        "schedule_raw": schedule_raw,
        "per_stage_quota": int(per_stage_quota),
        "init_weights_fp": init_weights_fp,
        "opt_init": opt_init,
        "data_fp": fp,
        "payload_sha256": "",
    }
    if init_weights_path:
        init_copy = tmp_extra_dir / "init_weights.json"
        shutil.copyfile(init_weights_path, init_copy)
        extra_files.append(init_copy)
    if opt_init:
        opt_copy = tmp_extra_dir / "opt_init.tar.b64"
        opt_copy.write_text(opt_init, encoding="utf-8")
        extra_files.append(opt_copy)
    if kind == "bc":
        # BC manifest：免除 PPO 专有键（与 protocol.MANIFEST_BC_EXEMPT 同表），并入 extra
        for k in ("kl_coef", "kl_cap", "adv_norm", "normalize_ret", "kickstart_kl", "ent_coef"):
            m.pop(k, None)
        m.pop("ref_weights_b64", None)
        m.pop("ref_weights_fp", None)
        m.pop("opt_init", None)
        m.pop("schedule_raw", None)
        for k, v in (extra or {}).items():
            m[k] = v
    else:
        m["reward_formula"] = reward_formula
        m["formula_hash"] = formula_hash
        m["metrics_version"] = metrics_version
        m["gamma"] = gamma
        m["lam"] = lam
    m["kind"] = kind
    m["seed"] = job_seed(run_id, it, init_weights_fp)
    m["job_id"] = make_job_id(m)
    if course_name:
        # P4-W2 归属（S9）：在 job_id 计算**之后**注入——幂等键不含短名，旧链字节不变；
        # normalize_manifest 允许未知/可选键，wire 兼容（D1：未知字段忽略）。
        m["course_name"] = str(course_name)
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


def set_cloud_halt(
    base_url: str,
    token: str,
    halt: bool,
    timeout: float = 15.0,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> bool:
    """§386：向 hub 下发/解除云端停机达令（console 与 TrainingLoop 共用一端点）。

    返回 True = hub 已采纳。网络故障/非 200 → False（记录日志，**停机链路永不
    阻断训练**）——local/push 模式无 hub 时会带空 url 进来，直接短路 False。
    """
    if not base_url or not token:
        return False
    path = "/admin/workers/halt" if halt else "/admin/workers/resume"
    try:
        st, _ = _request(base_url, token, path, timeout=timeout)
    except Exception as e:  # 网络层（tunnel 抖动等）——基础设施不可用，不阻断训练
        log(f"cloud {'halt' if halt else 'resume'} 下发失败（{type(e).__name__}: {e}）")
        return False
    if st != 200:
        log(f"cloud {path} → HTTP {st}（不阻断训练）")
        return False
    log(f"cloud {'halt' if halt else 'resume'} 已下发（hub 采纳）")
    return True


def hub_halted(base_url: str, token: str, timeout: float = 10.0) -> bool | None:
    """读 /admin/workers/status → True=停机中 / False=已清除 / None=未知。

    未配置 hub（local/push）或不可达/非 200/体裁不对 → None（呼叫方按未知处理，
    绝不把"问不到"当成"没停机"）。
    """
    if not base_url or not token:
        return None
    try:
        st, body = _request(base_url, token, "/admin/workers/status", timeout=timeout)
    except Exception:
        return None
    if st != 200:
        return None
    try:
        v = json.loads(body.decode("utf-8")).get("halt")
    except ValueError:
        return None
    return bool(v) if isinstance(v, bool) else None


def clear_halt_on_startup(
    base_url: str,
    token: str,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> bool:
    """TrainingLoop 启动即清空 hub 停机态（2026-09-12 it17 事故复盘）。

    上轮门判 REMEDIATE / 人工停机后未恢复 / worker 自杀残留的 halt 若带进新 run，
    首轮 PPO job 直接进无人区（训练机空等 30min 超时）。启动=需要算力=停机条件
    作废：先读后清，读回确认才算数。

    返回 True = 已确认清除（或本无 halt、无 hub）；False = 仍停机/未知（只告警，
    **永不阻断启动**——PPO 等待期会再次表面化，控制台 PPO 排队告警是第二道网）。
    """
    if not base_url or not token:
        return True  # local/push 无 hub——无事可做即成功
    cur = hub_halted(base_url, token)
    if cur is False:
        log("[run_rl] hub 停机态：启动时检查，本已清除，无事可做")
        return True
    if cur is True:
        log("[run_rl] hub 停机态：检测到遗留 halt（上轮门判/人工停机残留）——启动即清空")
    else:
        log("[run_rl] hub 停机态未知（不可达？）——仍尝试 resume（幂等），失败不阻断启动")
    if not set_cloud_halt(base_url, token, False, log=log):
        log("[run_rl] WARN: hub resume 下发失败——首轮 PPO 可能排队超时，盯控制台 PPO 告警")
        return False
    if hub_halted(base_url, token) is False:
        log("[run_rl] hub 停机态：已清除并回读确认")
        return True
    log("[run_rl] WARN: hub resume 已下发但回读仍为 halt——首轮 PPO 可能排队超时")
    return False


def wait_job(
    base_url: str,
    token: str,
    jid: str,
    *,
    timeout_sec: float = 25 * 60,
    poll_sec: float = 5.0,
    poll_max_sec: float = 60.0,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
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
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
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


def verify_and_land_bc(
    result: dict,
    manifest: dict,
    *,
    traj_dir: str | Path,
    it: int,
    out_weights: str,
    round_name: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> str:
    """BC 结果校验 + 落盘（plan/bc-cloud-integration.plan.md §4；verify_and_land 的 BC 版）。

    校验（无 init-weights fencing——BC 无 warm-start，改对账 manifest 自带指纹）：
      1. init_weights_fp == manifest 值（防 result 错配 job）；
      2. data_fp == 本地对当前轮 bc shard 集重算（防云训练了别的语料，D12 同语义）；
      3. commit_echo == manifest.commit（代码版本一致）。
    落盘：weights_json 原子写 out_weights（无 opt tar——BC v1 不跨轮续训）。
    返回落盘 weights 的指纹（wver）。
    """
    m = normalize_manifest(manifest)
    if result["init_weights_fp"] != m["init_weights_fp"]:
        raise HubClientError(
            "BC 校验失败: init_weights_fp 不匹配（result 与 manifest 错配）——拒收"
        )
    local_fp = data_fp(iter_bc_shard_dirs(traj_dir, it, round_name, log=log))
    if result["data_fp"] != local_fp:
        raise HubClientError(
            f"BC 校验失败: data_fp 不匹配（云={result['data_fp'][:12]}… "
            f"本地={local_fp[:12]}…）——拒收"
        )
    if result["commit_echo"] != m["commit"]:
        raise HubClientError(
            f"BC 校验失败: commit_echo={result['commit_echo'][:12]}… != "
            f"manifest.commit={m['commit'][:12]}…——拒收"
        )
    wj = decode_weights_json(str(result["weights_json"]))
    out_p = Path(out_weights)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_p.with_suffix(out_p.suffix + ".bc.tmp")
    tmp.write_bytes(wj)
    os.replace(tmp, out_p)
    wver = _sha256_file(str(out_p))
    log(f"weights landed -> {out_weights} ({len(wj)} bytes, wver={wver[:12]}…)")
    return wver


def _extract_tar(tar_bytes: bytes, dest: Path) -> None:
    import io

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest)
