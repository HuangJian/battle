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
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import rmtree_best_effort
from remote.protocol import (
    AUTH_HEADER,
    BLOB_OPT,
    BLOB_REF,
    FAIL_BODY_MAX,
    FAIL_NAME,
    PAYLOAD_NAME,
    PLAN_NAME,
    TS_CODE_NAME,
    JobFailedError,
    blob_path,
    data_fp,
    decode_opt_tar,
    decode_weights_json,
    iter_expected_data_fp,
    job_seed,
    normalize_manifest,
    pack_payload,
    validate_rollout_spec,
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
    """把 shard 目录 + 额外文件（有 opt blob 时仅 shard；否则含 init_weights.json）
    打成 payload 归档（**tar.xz**，2026-09-10 起；实测体积 −48.7% 而打包耗时持平）。
    返回归档字节 sha256。

    M2（B1/B2/B4）：不再写根级占位 manifest.json、不再写 opt_init.tar.b64；有 opt
    blob 时也不写 init_weights.json（opt tar 已含 model+Adam）。布局 = shard 目录整体
    （+ 可选 init_weights.json）。`manifest` 形参保留只为调用签名兼容。

    2026-09-17：实现改为**转调** `remote.protocol.pack_payload`（多了一个 `extra_files`
    形参）——半离线（kind=run）的逐轮 payload 也要带额外文件，第三个 tar.xz 打包副本
    没有道理，而两份口径本就只差一个 extra 循环。
    """
    return pack_payload(shard_dirs, manifest, zip_path, extra_files=list(extra_files))


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


#: M3 TS 运行时打进 zip 的源码根（相对仓根；其余一律不进）。
#: `tools/` **整棵**（不是只 `tools/sim`）：2026-09-17 实测 `export-rl-rollout.ts`
#: 的依赖闭包会跨出 tools/sim——它 import `../eval/godai-score`（v7 评分口径）。
#: 手写「该包哪几个子目录」就是在猜依赖图；靠
#: `tests/test_remote_iter_real_bun.py` 真跑一遍才是判据（那条测试就是这个事故的哨兵）。
TS_CODE_DIRS: tuple[str, ...] = ("src", "tools")
#: 允许进 zip 的后缀（.ts 源码 + .jsonc 数据 + .wasm 权重——`src/nn/conv-wasm.ts`
#: 经 `import.meta.url` 读 `src/nn/wasm/conv_feats.wasm`，漏了它节点上卷积直接炸）。
TS_CODE_SUFFIXES: tuple[str, ...] = (".ts", ".jsonc", ".wasm")
#: 一律不进 zip 的目录名（含 node_modules —— rollout 零第三方运行时依赖，
#: 只用到 node 内建 `fs`/`path`，所以云机**不必** bun install）。
TS_CODE_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "node_modules",
    "tmp",
    "__pycache__",
    ".venv",
})


def pack_ts_code_zip(
    repo_root: str | Path,
    zip_path: str | Path,
    *,
    log=lambda msg: None,
) -> str:
    """M3：把 rollout 用的 **TS 运行时** 打成 ts_code.zip（仅 `src/**` + `tools/sim/**`
    下的 `.ts/.jsonc/.wasm`），返回字节 sha256。

    与 `pack_code_zip`（Python 侧，另走 zip+extract 到 sys.path）**完全独立**：这条
    链的消费者是 `bun`（节点上直接 `bun tools/sim/export-rl-rollout.ts`），不需要
    venv/依赖安装，也不需要进 sys.path。

    ⚠ 零第三方运行时依赖是本方案成立的前提（已核：exporter 链的非相对 import 只有
    `fs`/`path`，仓根 package.json 无 dependencies）。哪天给 exporter 链引入了真的
   运行时依赖，这里必须跟着变（否则云机报 module not found 而非静默错）。

    固定时间戳打包：sha 只由内容决定（同内容 → 同 sha → 节点侧 ts_code 缓存可命中）。
    """
    import zipfile

    repo_p = Path(repo_root).resolve()
    zip_path_p = Path(zip_path)
    zip_path_p.parent.mkdir(parents=True, exist_ok=True)
    n_files = 0
    n_bytes = 0
    with zipfile.ZipFile(zip_path_p, "w", zipfile.ZIP_DEFLATED) as z:
        for top in TS_CODE_DIRS:
            base = repo_p / top
            if not base.is_dir():
                raise HubClientError(f"pack_ts_code_zip: 源码目录不存在 {base}")
            for dirpath, dirnames, filenames in os.walk(base):
                # 点目录一网打尽（.git/.venv/.mypy_cache…）——同 pack_code_zip 的教训
                dirnames[:] = [
                    d for d in dirnames if d not in TS_CODE_EXCLUDE_DIRS and not d.startswith(".")
                ]
                dir_p = Path(dirpath)
                for fn in sorted(filenames):
                    if not fn.endswith(TS_CODE_SUFFIXES) or fn.endswith(".d.ts"):
                        continue
                    f = dir_p / fn
                    try:
                        if not f.is_file():
                            continue
                    except OSError:
                        continue
                    arc = str(f.relative_to(repo_p)).replace("\\", "/")
                    zi = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o644 << 16
                    data = f.read_bytes()
                    z.writestr(zi, data)
                    n_files += 1
                    n_bytes += len(data)
    sha = _sha256_bytes(zip_path_p.read_bytes())
    if log:
        log(
            f"ts_code.zip: {n_files} files, {n_bytes} bytes raw -> "
            f"{zip_path_p.stat().st_size} bytes, sha256={sha[:12]}…"
        )
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
    # M2（plan/remote-wire-remediation §4.2）：协议瘦身开关。
    # True → opt/ref 走内容寻址 sha（不再内联），并去掉 payload 内冗余文件；
    # False → 逐字节回到旧行为（内联 base64、带 opt_init.tar.b64）。
    slim: bool = False,
    # echo 冒烟需要 payload 内的 init_weights.json（无 opt 才需要；冒烟轮可能带 opt
    # 但仍要回显）——调用方按 args.smoke 传 True 强制带上。
    keep_init_weights: bool = False,
    # 任务类型（BC 整合，plan/bc-cloud-integration.plan.md §4）：缺省 "ppo" =
    # 原行为逐字节不变；"bc" = 行为克隆 job——manifest 免除 PPO 专有键
    # （protocol.MANIFEST_BC_EXEMPT）并并入 `extra`（arch/val_split/mirror_p/
    # value_coef/ckpt_every）。init_weights_path 空 = BC 无 warm-start（不拷 init
    # 文件，init_weights_fp 恒 "bc"——幂等键分量仍稳定）。
    kind: str = "ppo",
    extra: dict | None = None,
    # M3（plan/remote-wire-remediation §5.2）：kind="iter" = 一整轮上云。
    # rollout_spec 已经过 `protocol.validate_rollout_spec`（argv 白名单 + 相对路径 +
    # 逐局 stage/seed）——本函数不再二次信任，直接进 manifest。
    # ts_code_zip_path = 节点跑 rollout 用的 TS 运行时 zip（拷进 job 目录供 /ts_code 取）。
    rollout_spec: dict | None = None,
    ts_code_sha256: str = "",
    ts_code_zip_path: str | Path | None = None,
    # 半离线（kind="run"，2026-09-17）：`plan.json` 的**字节**（由 `rl.plan.dump_plan`
    # 规范序列化）。节点靠它自主跑完 it+1..end_it——payload 必须带此文件，manifest 记
    # 它的 sha（`plan_sha256`）。传 bytes 而不是 dict：本模块是 `remote/` 层，不 import
    # `rl/`（方向单一）；规范化序列化只有 `rl.plan.dump_plan` 一份，调用方自己 dump。
    plan_bytes: bytes | None = None,
    # 全离线（2026-09-17）：`register=False` = **只建 job 目录、不记账本也不进待领池**。
    # 用途：把这一段任务打成可上传云机的任务包（`remote/bundle.py`）——包里的 manifest
    # 必须由训练侧生成（课程/超参/血缘的解析者），但这条腿不发 job（云机不在网络上），
    # 记一条 `job_pending` 只会让控制台看到一条永远等不到工人的待领任务。
    register: bool = True,
    # hub 中介 push 派发（2026-09-18）：非空 = `manifest.dispatch` 告知 hub「这份活由你推给
    # GPU worker，别等 pull 来领」。由训练侧 `--remote-transport hubpush` 写定。
    # **在 job_id 算完之后注入**（同 course_name）：切传输方式不改变 job 身份——同一轮的活
    # 换个传输腿走，幂等键没必要跟着变（变了会让重发变成两个 job，账本上出现两条）。
    dispatch: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> dict:
    """打包 + 发布 job（磁盘 IPC）：job_root/<job_id>/ + jsonl job_pending 事件。

    返回已归一化 manifest（含 job_id / payload_sha256）。幂等：同 job_id
    （幂等键相同）已发布 → 覆盖 payload、不重复追加 pending。

    kind="iter"（M3）：payload 里**没有 shard**（只有 init_weights.json + 可选 blob），
    shard 由节点自己跑 rollout 现产；data_fp 改对**声明集**（argv 里的逐局 stage/seed
    + rollout.wver）算——节点跑完后对实产目录复算，两侧同函数，相等 ⇔ 实产集 ==
    声明集（计划 §5.5①）。
    """
    job_root_p = Path(job_root)
    kind = str(kind or "ppo")
    schedule_raw = schedule_raw if schedule_raw is not None else []
    if rollout_spec is not None:
        rollout_spec = validate_rollout_spec(rollout_spec)
        # kind=run 是 kind=iter 的**延长**（同一轮语义 + 计划尾巴）：两者都要
        # rollout 规格与 TS 运行时，校验口径完全共享。
        if kind not in ("iter", "run"):
            raise HubClientError(
                f"rollout_spec 只对 kind='iter'/'run' 有意义，收到 kind={kind!r}"
            )
        if not ts_code_sha256:
            raise HubClientError(f"kind={kind!r} 必须带 ts_code_sha256（节点要它定位 TS 运行时）")
        if shard_dirs:
            raise HubClientError(
                f"kind={kind!r} 不接受本地 shard（实得 {len(shard_dirs)} 个）——"
                "上云轮由节点现产，混着发会双份采集"
            )
    if kind == "run":
        if not plan_bytes:
            raise HubClientError(
                "kind='run' 必须带 plan_bytes（半离线段的唯一输入：没有计划，节点跑完本轮"
                "就不知道下一轮跑哪些局）——拒发"
            )
        if rollout_spec is None:
            raise HubClientError("kind='run' 必须带 rollout_spec（本 job 自己那一轮的采集规格）")
    # 1) data_fp（D1：排序 shard 路径 + manifest {wver,stage,seed}；iter = 声明集）
    fp = iter_expected_data_fp(rollout_spec) if rollout_spec else data_fp(shard_dirs)
    # 2) init_weights_fp（fencing：云回传的 init_weights_fp 必须等于当前 args.out 指纹；
    #    BC 无 warm-start → 恒 "bc" 占位）
    init_weights_fp = _sha256_file(init_weights_path) if init_weights_path else "bc"
    # 3) opt/ref 内容寻址（M2 B3）：raw 字节 sha256；未开瘦身则内联 base64（旧口径）
    opt_raw = _opt_tar_bytes(ckpt_remote_dir)
    opt_sha = _sha256_bytes(opt_raw) if opt_raw else ""
    use_opt_blob = bool(slim and opt_sha)
    ref_raw = b""
    if slim and ref_weights_b64:
        try:
            ref_raw = base64.b64decode(ref_weights_b64.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            ref_raw = b""
    # ref_sha 口径与 ref_weights_fp 同一（调用方对 raw 权重取 sha）——有字节才寻址。
    use_ref_blob = bool(slim and ref_weights_fp and ref_raw)
    opt_init = (
        ""
        if use_opt_blob
        else (base64.b64encode(opt_raw).decode("ascii") if opt_raw else "")
    )
    # 4) manifest 预建（payload_sha256 占位）→ 打包（payload 内不再带占位 manifest）
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
        "ref_weights_b64": "" if use_ref_blob else ref_weights_b64,
        "ref_weights_fp": ref_weights_fp,
        "shuffle": shuffle,
        "schedule_raw": schedule_raw,
        "per_stage_quota": int(per_stage_quota),
        "init_weights_fp": init_weights_fp,
        "opt_init": opt_init,
        "opt_sha": opt_sha if use_opt_blob else "",
        "ref_sha": str(ref_weights_fp) if use_ref_blob else "",
        "opt_bytes": len(opt_raw) if use_opt_blob else 0,
        "ref_bytes": len(ref_raw) if use_ref_blob else 0,
        "slim": bool(slim),
        "data_fp": fp,
        "payload_sha256": "",
    }
    if rollout_spec is not None:
        m["ts_code_sha256"] = str(ts_code_sha256)
        m["rollout"] = rollout_spec
        # kind=iter：节点**必须**拿得到 init 权重——它要用这份权重去跑 rollout（不只是
        # PPO 初始化）。M2 B4「有 opt blob 就不传 init_weights.json」在这里不成立。
        keep_init_weights = True
    if kind == "run":
        assert plan_bytes is not None  # 上面已拒发（类型收窄给 mypy）
        # 计划随 payload 走（与 init_weights.json 同层）：节点解包后 `verify_plan_file(job_dir, …)`
        # 就地拿到它。manifest 只记 sha——全文可达百 KB，不该让 hub 每轮轮询都解析一遍。
        plan_copy = tmp_extra_dir / PLAN_NAME
        plan_copy.write_bytes(plan_bytes)
        extra_files.append(plan_copy)
        m["plan_sha256"] = _sha256_bytes(plan_bytes)
    # M2（B4）：有 opt blob 时不传 init_weights.json（worker 从 opt 恢复即完整
    # model+Adam）。echo 冒烟要保持回显能力，keep_init_weights 时照旧带上。
    if init_weights_path and (not use_opt_blob or keep_init_weights):
        init_copy = tmp_extra_dir / "init_weights.json"
        shutil.copyfile(init_weights_path, init_copy)
        extra_files.append(init_copy)
    # M2（B2）：不再打 payload 内 opt_init.tar.b64 —— 全仓无读取方（opt 只从
    # manifest["opt_init"] / opt_sha blob 取）。
    if kind == "bc":
        # BC manifest：免除 PPO 专有键（与 protocol.MANIFEST_BC_EXEMPT 同表），并入 extra
        for k in ("kl_coef", "kl_cap", "adv_norm", "normalize_ret", "kickstart_kl", "ent_coef"):
            m.pop(k, None)
        m.pop("ref_weights_b64", None)
        m.pop("ref_weights_fp", None)
        m.pop("opt_init", None)
        m.pop("schedule_raw", None)
        for k in ("opt_sha", "ref_sha", "opt_bytes", "ref_bytes", "slim"):
            m.pop(k, None)
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
    if dispatch:
        # 同上：job 身份已定，传输腿的意图是**附加语义**不是身份成分。旧 hub/旧 worker
        # 忽略未知键 ⇒ 缺席即 pull，行为逐字节不变。
        m["dispatch"] = str(dispatch)
    # 5) 落盘 job 目录：payload.zip（zip 内 manifest 为占位副本——payload_sha256 尚
    #    未算出）→ 回填真实 sha → 权威 manifest.json（worker 以 job 记录校验，D1）。
    #    normalize_manifest 在回填后调用：payload_sha256 必填非空，占位空串会误拒。
    jid = str(m["job_id"])
    jd = job_root_p / jid
    jd.mkdir(parents=True, exist_ok=True)
    zip_path = jd / PAYLOAD_NAME
    sha = pack_payload_zip(shard_dirs, extra_files, zip_path, m)
    m["payload_sha256"] = sha
    # M2 B3：blob 落盘——pull worker 经 /jobs/{id}/blob 取；push 由 push_client 判缓存
    # 后按需随 body 发送（读的就是这两个文件）。
    if use_opt_blob:
        blob_path(jd, BLOB_OPT).write_bytes(opt_raw)
    if use_ref_blob:
        blob_path(jd, BLOB_REF).write_bytes(ref_raw)
    m = normalize_manifest(m)
    (jd / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    # 重发同一个 job（同幂等键 → 同 job_id，逐轮重试走的正是这条路）必须清掉上一次的
    # 确定性失败标记（2026-09-17）——否则重发出来的 job 在 hub 侧仍算「已失败」：
    # 可领取池排除它（无人重跑）、wait_job 立刻 410（重试无效）。旧标记只对旧那一次有效。
    stale_fail = jd / FAIL_NAME
    if stale_fail.exists():
        try:
            stale_fail.unlink()
            log(f"republished job {jid}: 清掉上一次的失败标记（重发即重试）")
        except OSError as e:
            log(f"WARN: 失败标记未清掉（{e}）——重发可能被 hub 当成已失败")
    # 复制 code.zip 到 job 目录（hub-server 的 GET /jobs/{id}/code 从此目录服务）
    if code_zip_path:
        czp = Path(code_zip_path)
        if czp.exists():
            shutil.copy2(czp, jd / "code.zip")
    # M3：ts_code.zip 同规（GET /jobs/{id}/ts_code）——内容寻址缓存的前提是节点能拿到
    # 原始字节并与 manifest.ts_code_sha256 对账。
    if rollout_spec is not None and ts_code_zip_path:
        tzp = Path(ts_code_zip_path)
        if not tzp.exists():
            raise HubClientError(f"kind='iter' 的 ts_code.zip 不存在: {tzp}")
        shutil.copy2(tzp, jd / TS_CODE_NAME)
    try:
        rmtree_best_effort(tmp_extra_dir)
    except OSError:
        pass
    # 6) jsonl job_pending（磁盘 IPC；幂等去重——同 job_id 不重复追加）
    if register:
        # 悬空 job 清理（§381）：发布前作废更早迭代/旧 runId 遗留的 pending job——
        # 否则 loop 重启后无 worker 期间滞留的旧 job 会在 GPU 上线时被全部补做。
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
    else:
        log(f"job {jid} it{it}: 不记账本/不进待领池（打包导出用，`register=False`）")
    log(
        f"published job {jid} it{it}: "
        + (
            f"kind=iter games={len(rollout_spec['argv'])} "
            f"ts_code={str(ts_code_sha256)[:12]}… "
            if rollout_spec
            else f"shards={len(shard_dirs)} "
        )
        + f"data_fp={fp[:12]}… payload={sha[:12]}…"
    )
    return m


def _opt_tar_bytes(ckpt_remote_dir: str | Path | None) -> bytes:
    """上轮 ppo_ckpt_remote → raw tar bytes（model.pt + opt.pt，D5）；空 = b""。

    H5（review-hy）：不打 state.json（numpy RNG 无人读，worker 按 per-job 种子重播）。

    M2：**优先读 `<ckpt>/../ppo_ckpt_remote.tar`**（verify_and_land 落盘的**原始
    回传字节**）。只有原样字节的 sha256 才与云 worker 本地 blob_cache 的键一致 ——
    重打 tar 会因 uid/gid/mtime 规范化差异导致 sha 漂移、每轮都缓存未命中（1.19MB
    白传回来）。历史 run / 冷启动无该文件 → 从目录重打（可能未命中，走 blob 传一次）。
    """
    if not ckpt_remote_dir:
        return b""
    d = Path(ckpt_remote_dir)
    tar_path = d.parent / (d.name + ".tar")
    if tar_path.exists():
        return tar_path.read_bytes()
    names = [n for n in ("model.pt", "opt.pt") if (d / n).exists()]
    if not names:
        return b""
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tfb:
        for n in names:
            tfb.add(d / n, arcname=n)
    return buf.getvalue()


def _pack_opt_init(ckpt_remote_dir: str | Path | None) -> str:
    """上轮 ppo_ckpt_remote → **plain base64** tar（旧内联口径，slim=false 逐字节回退）。"""
    raw = _opt_tar_bytes(ckpt_remote_dir)
    return base64.b64encode(raw).decode("ascii") if raw else ""


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

    from remote.net_http import urlopen as _urlopen

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={AUTH_HEADER: f"Bearer {token}", **(headers or {})},
        method=method,
    )
    try:
        # 回环（本机 hub）绕开环境代理——`no_proxy` 里的 `127.*` 通配 Python 不认，
        # 见 remote/net_http.py 模块头。
        with _urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def report_job_failure(
    base_url: str,
    token: str,
    jid: str,
    reason: str,
    *,
    kind: str = "",
    detail: str = "",
    worker: str = "",
    lease_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> bool:
    """回报**确定性**失败原因（`POST /jobs/{id}/fail`）——训练侧随即从
    `wait_job` 拿到 410 + 原因立刻停腿，不再等满超时。

    返回 True = hub 采纳（或已记录过）。尽力而为：回报本身不可达时返回 False，
    由超时兜底（与 release_job 同策略）——**永不让回报本身炸掉 worker 主循环**。

    只用于「这台机器干不了」的确定性失败（bun 缺失 / TS 运行时取不到 / argv 非法）。
    瞬时失败（网络/5xx）走 release 回池，**不**报这里——那会把可恢复的 job 钉死。
    重发同 job（同幂等键 → 同 job_id）时 publish_job 会清除失败标记。
    """
    if not base_url or not jid or not reason:
        return False
    body = json.dumps(
        {
            "reason": str(reason)[:2000],
            "kind": str(kind)[:200],
            "detail": str(detail)[:4000],
            "worker": str(worker)[:200],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    if len(body) > FAIL_BODY_MAX:  # 已按字段截断，兜底防御（hub 也会 400）
        return False
    try:
        st, resp = _request(
            base_url,
            token,
            f"/jobs/{jid}/fail",
            timeout=15.0,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"X-Lease-Token": lease_token} if lease_token else {}),
            },
        )
    except Exception as e:
        log(f"job {jid} 失败回报未送达（{type(e).__name__}）——训练侧将走超时兜底")
        return False
    if st == 200:
        log(f"job {jid} 失败原因已回报（hub 采纳）: {str(reason)[:160]}")
        return True
    log(f"job {jid} 失败回报被拒：HTTP {st}: {resp[:200].decode('utf-8', 'replace')}")
    return False


def set_cloud_halt(
    base_url: str,
    token: str,
    halt: bool,
    timeout: float = 15.0,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
    course: str = "",
) -> bool:
    """§386：向 hub 下发/解除云端停机达令（console 与 TrainingLoop 共用一端点）。

    返回 True = hub 已采纳。网络故障/非 200 → False（记录日志，**停机链路永不
    阻断训练**）——local/push 模式无 hub 时会带空 url 进来，直接短路 False。

    `course`（2026-09-18 单 hub 化）：一个 hub 服务所有并行课程，故达令必须**按课程**
    下发（`?course=`）——否则 A 课的门禁 ABORT 会把 B 课的云机一起停掉。空串 = 全课程
    （旧语义：单课程 hub / 没有课程上下文的调用方）。
    """
    if not base_url or not token:
        return False
    path = "/admin/workers/halt" if halt else "/admin/workers/resume"
    if course:
        path += "?course=" + urllib.parse.quote(course)
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


def hub_halted(base_url: str, token: str, timeout: float = 10.0, course: str = "") -> bool | None:
    """读 /admin/workers/status → True=停机中 / False=已清除 / None=未知。

    未配置 hub（local/push）或不可达/非 200/体裁不对 → None（呼叫方按未知处理，
    绝不把"问不到"当成"没停机"）。`course` = 只看那一门课（空串 = 全课程都停才 True）。
    """
    if not base_url or not token:
        return None
    path = "/admin/workers/status"
    if course:
        path += "?course=" + urllib.parse.quote(course)
    try:
        st, body = _request(base_url, token, path, timeout=timeout)
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
    course: str = "",
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
    cur = hub_halted(base_url, token, course=course)
    if cur is False:
        log("[run_rl] hub 停机态：启动时检查，本已清除，无事可做")
        return True
    if cur is True:
        log("[run_rl] hub 停机态：检测到遗留 halt（上轮门判/人工停机残留）——启动即清空")
    else:
        log("[run_rl] hub 停机态未知（不可达？）——仍尝试 resume（幂等），失败不阻断启动")
    if not set_cloud_halt(base_url, token, False, log=log, course=course):
        log("[run_rl] WARN: hub resume 下发失败——首轮 PPO 可能排队超时，盯控制台 PPO 告警")
        return False
    if hub_halted(base_url, token, course=course) is False:
        log("[run_rl] hub 停机态：已清除并回读确认")
        return True
    log("[run_rl] WARN: hub resume 已下发但回读仍为 halt——首轮 PPO 可能排队超时")
    return False


def _job_failed_from_body(jid: str, body: bytes) -> JobFailedError:
    """410/status=failed 的响应体 → JobFailedError（原因取 error/reason，详情取 fail_detail）。

    消息形如 `job <id> 失败: bun 未安装 … [kind=ProtocolError]`——外部（训练主循环的
    确定性失败分支）靠 `JobFailedError` 类型收兵，人靠这行字定位现场。损坏体不丢
    失败事实（退回通用文案）。
    """
    reason, kind, detail = "节点报告确定性失败（无原因文本）", "", ""
    try:
        loaded = json.loads(body.decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        raw = loaded.get("error") or loaded.get("reason")
        if isinstance(raw, str) and raw:
            reason = raw
        if isinstance(loaded.get("fail_kind"), str):
            kind = loaded["fail_kind"]
        if isinstance(loaded.get("fail_detail"), str):
            detail = loaded["fail_detail"]
    suffix = f" [kind={kind}]" if kind else ""
    return JobFailedError(f"job {jid} 失败: {reason}{suffix}", kind=kind, detail=detail)


#: 非阻塞探针的三态（`probe_job_result` 的 `state`）。
PROBE_READY = "ready"  # 结果已落 hub，可取
PROBE_PENDING = "pending"  # 还没回（正常排队）——**不等于失败**，过一会儿再问
PROBE_TRANSIENT = "transient"  # 网络抖动 / 5xx——可重试的「没答」

#: 非阻塞探针的单次请求超时（秒）。**单进程调度器会同步调它**（问一句就走），所以
#: 必须短：探针只可能给出「就绪 / 还没好 / 瞬时错」，把「瞬时错」误当「还没好」的代价
#: 只是再等一轮，永远不会误判成成功——所以宁可短，也不让一次网络卡顿堵住整条调度链。
PROBE_TIMEOUT_SEC = 10.0


class ProbeResult(NamedTuple):
    """一次非阻塞探测的结论。

    `state` ∈ {ready, pending, transient}；`result` 仅在 ready 时非空；`detail` 是
    transient 的诊断（异常类型或 HTTP 状态），供调用方的日志/退避用。
    终局失败（410 / status=failed）**不走三态**——它抛 `JobFailedError`，与阻塞等待的
    收兵口径一致（「还没好」与「永远好不了」必须分开）。
    """

    state: str
    result: dict | None = None
    detail: str = ""


def probe_job_result(
    base_url: str,
    token: str,
    jid: str,
    *,
    path: str = "/jobs/{jid}/result",
    timeout: float = PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """**一次**请求探测 job 结果——状态码分类的**唯一**实现（hub / 节点两条链路共用）。

    节点侧（worker_server）的结果端点路径不同（`/job/{jid}/result`），故 `path` 可注入；
    除路径外两条链路的语义**必须一致**：否则「同一份 job 在 hub 上判 pending、在节点上
    判 transient」这类分叉会各自演化（历史上 hub 与 push 两段轮询就是这么漂开的）。

    网络异常/5xx = transient；202/404 = pending；410 = 终局失败（抛）；其余 = 协议错误（抛）。
    """
    try:
        # `timeout` 走**关键字**：测试里的假 `_request` 常把它声明成 keyword-only
        # （位置传参会 TypeError，症状是「探针一调就炸」而不是「问了没答」）。
        status, body = _request(base_url, token, path.format(jid=jid), timeout=timeout)
    except Exception as e:  # 瞬时网络错误：与 404 一样是「没答」，不是失败
        return ProbeResult(PROBE_TRANSIENT, None, f"{type(e).__name__}")
    if status == 200:
        loaded = json.loads(body.decode("utf-8"))
        if isinstance(loaded, dict):
            return ProbeResult(PROBE_READY, loaded)
        raise HubClientError(f"probe_job_result: job {jid} 结果非对象: {type(loaded).__name__}")
    if status in (202, 404):
        return ProbeResult(PROBE_PENDING)
    if status == 410:
        # 终局：节点已报**确定性失败**（`POST /jobs/{id}/fail`），原因在体内。
        raise _job_failed_from_body(jid, body)
    if status >= 500:
        return ProbeResult(PROBE_TRANSIENT, None, f"HTTP {status}")
    raise HubClientError(
        f"probe_job_result: HTTP {status}: {body[:200].decode('utf-8', 'replace')}"
    )


def poll_job(
    base_url: str, token: str, jid: str, *, timeout: float = PROBE_TIMEOUT_SEC
) -> dict | None:
    """非阻塞探一次 hub 结果：就绪 → 结果；未就绪 / 瞬时错 → None（终局失败照抛）。

    单进程调度器的让位判据就靠它（R2c-3）：**问一句就走**——不等、不睡、不轮询。
    """
    return probe_job_result(base_url, token, jid, timeout=timeout).result


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

    单次探测与状态码分类在 `probe_job_result`（与 `poll_job` 同一实现）——本函数只负责
    「退避策略 + 超时收尾」，这正是它与非阻塞版该有的唯一区别。
    """
    deadline = time.time() + timeout_sec
    err_streak = 0
    while time.time() < deadline:
        probe = probe_job_result(base_url, token, jid, timeout=30.0)
        if probe.state == PROBE_READY:
            assert probe.result is not None  # ready 必带结果（probe_job_result 保证）
            return probe.result
        if probe.state == PROBE_PENDING:
            err_streak = 0  # 还没回 = 正常排队，复位退避
            time.sleep(poll_sec)
            continue
        # 瞬时错误（隧道抖动/DNS/5xx）——与 404 同等续等，但按 2 的幂退避；
        # 2026-09-05：此前单次错误直接抛 HubClientError 会废掉整轮迭代
        # （loop 连击 retry），对 24/7 隧道运营是可靠性缺陷。
        err_streak += 1
        backoff = min(poll_sec * (2 ** (err_streak - 1)), poll_max_sec)
        log(
            f"wait_job: job {jid} 轮询瞬时错误 ({probe.detail}) "
            f"— 连续第 {err_streak} 次，{backoff:.0f}s 后退避重试"
        )
        time.sleep(backoff)
        continue
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
        elif state == "failed":
            # 终局（2026-09-17）：收尾确认时也要认失败——否则又多等一个超时窗口。
            st2, body2 = _request(base_url, token, f"/jobs/{jid}/result", timeout=15.0)
            raise _job_failed_from_body(jid, body2 if st2 == 410 else s_body)
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

    kind="iter"（M3）：本轮 shard 由云节点现产，hub 侧**无本地副本可重算**——data_fp
    改比 manifest 的**声明集**值（hub 发布时算的），而「实产集 == 声明集」由节点侧
    在产完后用同一函数校验（不通过就在云上响亮失败，job 不会回传成功结果）。
    所以这里的第 2 项从「本地重算」变成「两边都是声明集」：节点能回传成功结果本身就
    蕴含了实产集相符（协议层不可绕）；§5.5① 的逐位对拍是离线验收，不是这条链的守卫。
    """
    m = normalize_manifest(manifest)
    if result["init_weights_fp"] != _sha256_file(init_weights_path):
        raise HubClientError("三重校验失败: init_weights_fp 不匹配（云起点 ≠ 当前 args.out）——拒收")
    if str(m.get("kind", "ppo") or "ppo") == "iter":
        local_fp = str(m["data_fp"])
    else:
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
    # M2：把**原始回传字节**落盘（sibling `.tar`）——下一轮 publish 的 opt_sha 必须
    # 由原样字节算出，才能等于云 worker blob_cache 的键（重打 tar 会 sha 漂移 ⇒ 每轮
    # 未命中 ⇒ 1.19MB 白传回来）。写失败不阻断落位（只是退回重打路径）。
    try:
        (ckpt_dir.parent / "ppo_ckpt_remote.tar").write_bytes(opt_tar)
    except OSError as e:
        log(f"WARN: 原始 opt tar 落盘失败（{e}）——下一轮 opt_sha 将走重打路径")
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
