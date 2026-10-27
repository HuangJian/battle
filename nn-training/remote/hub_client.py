"""remote/hub_client.py — hub 侧远程 PPO 客户端（TrainingLoop 远程分支使用）。

职责（D11/D12）：训练主循环只做「打包 → 发布 → 轮询/等待 → 校验落位」——
job 队列/租约/鉴权全在旁路 hub-server（hub/server.py）。

**发布 = 磁盘 IPC**（§3.1/附录 C）：训练主循环把 payload.zip + manifest.json
写入 `job_root/<job_id>/`，并追加 `job_pending` 事件到 jsonl 账本；hub-server
（独立进程）重读 jsonl + job 目录即可重建可领取池（D8），训练进程与 server
进程互不阻塞。**等待/取结果 = HTTP**：轮询 server 的 GET /jobs/{id}/result
（worker 回传已由 server 落盘）。

本模块（hub 侧）全程免 torch（D2）：打包做文件搬运 + sha256，另有**可选**的 wire-diet 字节瘦身
（`remote/payload_slim`：字段 f16 + 打包端先算 reward，`plan/wire-diet.plan.md` §3 —— 只碰 npy
字节，仍是 numpy-only）；weights_json 由云 worker 产出（D12 产出方锁死）。顶层零 torch。

⚠ `pack_payload_zip` / 两条发布端自检 / 重打上限已搬到 `remote/payload_pack`（2026-10-14，
LOC 预算：单文件 < 1000 行）——本模块只留「要不要瘦身」的决策与 job 面编排，`publish_job`
经 `from remote.payload_pack import pack_payload_zip` 调它。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只在类型面（运行时由 wire_diet 分支内延迟 import）
    from remote.payload_slim import ShardSlim

# ★ R3/M4 / R1/M3 的两个 delta 容器（`remote.opt_delta` / `remote.weights_delta`）**不在本模块顶层
# import**：hub 进程（`hub.queue_observe → remote.code_snapshot → 本模块`）只借本模块的
# `pack_code_zip`/`pack_ts_code_zip`，而那两个容器模块吃 **numpy/torch** —— 顶层 import 会把它们拖进
# hub 的 import 闭包，让 hub-server 在没装三方包的环境里启动即退（2026-10-14 事故）。它们只在**用它们
# 的那几个函数内**局部 import（`publish_job` / `_pack_opt_init` / `verify_and_land`）；判据 =
# `tests/remote/test_hub_import_graph.py`（进程级：hub 闭包不得含这两个模块）。
from common.code_sentinel import CODE_META_NAME, code_tree_provenance
from common.fs import append_jsonl, extract_tar_bytes
from common.hashing import sha256_bytes, sha256_file
from common.platform_utils import rmtree_best_effort
from common.proc import run_capture
from common.protocol import (
    BLOB_DEMO,
    BLOB_INIT,
    BLOB_OPT,
    BLOB_REF,
    FAIL_NAME,
    PAYLOAD_NAME,
    PLAN_NAME,
    ROLE_FIELD,
    ROLES,
    TS_CODE_NAME,
    blob_path,
    collision_rows,
    d14_corpus_match,
    data_fp,
    decode_opt_tar,
    decode_weights_json,
    iter_expected_data_fp,
    job_seed,
    normalize_manifest,
    role_of,
    validate_rollout_spec,
)
from common.protocol import (
    job_id as make_job_id,
)

# delta 落地（重建 + 押回权威字节）已并入**各自的容器模块**（2026-10-14，1000 行 LOC 预算）——
# 这里**不再留名字门面**（那要求顶层 import 它们，正好把 hub 拖回 numpy/torch；见上面的注释）。
# 调用点一律走函数内局部 import；测试也从容器模块直接 import（与 `pack_payload_zip` 的 `X as X`
# 门面不同：后者只依赖 stdlib + `common.*`，留在顶层对 hub 的闭包无害）。
# 发布端 payload 封装已下沉 `remote.payload_pack`（2026-10-14）：实现搬家、名字留门面。
from remote.payload_pack import pack_payload_zip as pack_payload_zip
from worker.resume import shard_state_init_ok, walk_shard_dirs

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 「HTTP 面」（传输薄壳 `_request` + 回传消费 + 停机达令 + HubClientError）已下沉
# `remote/hub_http.py`（S5 第五刀，2026-09-27）。实现搬家、名字留门面：
# 历史 `from remote.hub_client import …` 一行不改；`HubClientError` 仍从这里可用。
# ⚠ 注入点：patch `remote.hub_http._request`（本模块已无 `_request` 调用点）。
# ---------------------------------------------------------------------------
from remote.hub_http import (
    PROBE_PENDING as PROBE_PENDING,
)
from remote.hub_http import (
    PROBE_READY as PROBE_READY,
)
from remote.hub_http import (
    PROBE_TIMEOUT_SEC as PROBE_TIMEOUT_SEC,
)
from remote.hub_http import (
    PROBE_TRANSIENT as PROBE_TRANSIENT,
)
from remote.hub_http import (
    WAIT_REPORT_SEC as WAIT_REPORT_SEC,
)
from remote.hub_http import (
    HubClientError as HubClientError,
)
from remote.hub_http import (
    JobCancelledError as JobCancelledError,
)
from remote.hub_http import (
    ProbeResult as ProbeResult,
)
from remote.hub_http import (
    TripleCheckError as TripleCheckError,
)
from remote.hub_http import (
    _job_failed_from_body as _job_failed_from_body,
)
from remote.hub_http import (
    _request as _request,
)
from remote.hub_http import (
    _wait_state_note as _wait_state_note,
)
from remote.hub_http import (
    clear_halt_on_startup as clear_halt_on_startup,
)
from remote.hub_http import (
    hub_halted as hub_halted,
)
from remote.hub_http import (
    poll_job as poll_job,
)
from remote.hub_http import (
    probe_job_result as probe_job_result,
)
from remote.hub_http import (
    report_job_failure as report_job_failure,
)
from remote.hub_http import (
    set_cloud_halt as set_cloud_halt,
)
from remote.hub_http import (
    wait_job as wait_job,
)


def git_head(repo_root: Path = REPO_ROOT) -> str:
    """hub 当前 commit（manifest.commit；云 worker 据此 checkout，D6）。

    H4（review-hy）：工作区 dirty-tree 检查在 _remote_ppo（loop_steps.py）调用方
    完成（更详细的 fail-fast 信息）；本函数只做 commit 解析，供 smoke_loopback 等
    各方使用（这些场景可能有未跟踪文件且不生产发布 job）。
    """
    try:
        r = run_capture(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    raise HubClientError("无法解析 git HEAD——远程模式要求 hub 在 git 工作区内")


def _sha256_file(path: str) -> str:
    """文件字节 sha256（唯一实现见 `common.hashing.sha256_file`；本名保留为调用点别名）。"""
    return sha256_file(path)


def _sha256_bytes(b: bytes) -> str:
    """字节串 sha256（同上，别名）。"""
    return sha256_bytes(b)


# ------------------------------------------------------------------ 打包


def iter_shard_dirs(
    traj_dir: str | Path,
    it: int,
    log=lambda msg: None,
    *,
    course_fp: str = "",
    corpus_fp: str = "",
    state_init: bool = False,
    metrics_version: int | None = None,
) -> list[Path]:
    """本轮应训 shard 集：it{it} 下全部 rl_s*_seed*/manifest.json 目录（与
    `_serial_ppo` 的 load_episodes 装载口径一致——D1「wver 过滤 + resume 剔除
    后」由 _prepare_iter_dir 已保证目录内只有本轮 wver 匹配的完整 shard）。

    ★ D14 语料血缘过滤（course_fp/corpus_fp 非空时）：只挑**云 worker 会接受**的
    shard——判据就是 `protocol.d14_corpus_match`（云端逐 shard 拒收用的同一条规则）。
    为什么必须在这里滤（2026-09-20 事故）：it{it} 目录是**累积**的，课程文件被编辑
    过或换过 runId 时里面会同时躺着旧血缘 shard；而云端整份 job 拒收 ⇒ hub 侧
    永远等不到结果、训练轮空转、worker 反复领同一份死活。对账（`resume._scan_shards`）
    早就在滤血缘，只有发布端漏了——一次漏过滤 = 一份永远完不成的 job。

    起始分布（P3.5，`plan/x20-state-init.plan.md`）：`state_init=True` 时只挑带 `initTick`
    的 shard——一份标准开局的 shard 进了 payload，云端训的就经不是本轮的起始分布，
    而 data_fp（按 (stage,seed,wver) 声明）照样匹配（静默换实验那一类）。

    ★ metrics 版本过滤（`metrics_version` 非 None 时）：只挑 manifest `metrics_version`
    与本机 `biz.reward_library.METRICS_VERSION` **相等**的 shard。为什么（2026-09-29 事故）：
    产出 shard 的 rollout 代码可能与训练机（乃至同一台机器的**池暖 worker**）不同版本
    ——节点上跑旧导出器写 45 列、训练机/云 worker 已按 54 列读，云端 grad 第 1 秒就
    `ValueError: metrics 形状应为 [N+1,54]，收到 (74,45)`，同一份字节重领必炸 ⇒ hub 侧
    5 轮连续失败、整门课 `aborted ppo 连续失败`。`data_fp`/`wver` 血缘**看不见**这一层
    （两者都是同血缘、同权重，只是行宽变了）⇒ 必须在发布端按 shard 自己的声明拦下，
    与 D14 血缘、state_init 同一族「云端会整份拒收/必炸，故不进 payload」的判据。

    同名 shard 去重（发布端不变量）：同一 seed 只允许一份进 payload——重复
    arcname 的 zip 由解包顺序决定训练吃哪份（偶然语义），且 data_fp 账面与
    expectedGames 不平。正常路径由 dispatch 结算退场输家副本（2026-09-06），
    这里兜底崩溃/历史残留：按 manifest.json mtime 保留最早一份（先写盘者 =
    结算赢家），退役者响亮日志。
    """
    it_dir = Path(traj_dir) / f"it{it}"
    if not it_dir.exists():
        return []
    expected_mver = int(metrics_version) if metrics_version is not None else None
    cands: dict[str, list[Path]] = {}
    skipped = 0
    skipped_init = 0
    first_init_miss = ""
    skipped_mver = 0
    first_mver_miss = ""
    mver_bad: dict[int | None, int] = {}
    # walk_shard_dirs 而非 rglob：发布与 dup-settle 输家退场（dispatch 结算线程 rmtree）
    # 同轮并发，rglob 会在迭代里抛 FileNotFoundError 把发布打红（2026-09-20 事故：
    # `stream collector failed` 同源；栈顶 pathlib._select_from）。见 biz/resume.py 的说明。
    for d in walk_shard_dirs(it_dir, with_manifest=True):
        if not ((d / "obs.npy").exists() or (d / "metrics.npy").exists()):
            continue
        if (course_fp or corpus_fp) and not _shard_lineage_ok(d, course_fp, corpus_fp, log):
            skipped += 1
            continue
        if state_init and not _shard_state_init_ok(d, log):
            skipped_init += 1
            first_init_miss = first_init_miss or str(d)
            continue
        if expected_mver is not None:
            got_mver = _shard_metrics_version(d)
            if got_mver != expected_mver:
                skipped_mver += 1
                first_mver_miss = first_mver_miss or str(d)
                mver_bad[got_mver] = mver_bad.get(got_mver, 0) + 1
                continue
        cands.setdefault(d.name, []).append(d)
    if skipped:
        log(
            f"[publish] D14: 剔除 {skipped} 个异血缘 shard（it{it} 内混入了别的课程版本/runId 的语料）"
            "——云端会整份拒收，故不进 payload"
        )
    if skipped_init:
        log(
            f"[publish] state_init: 剔除 {skipped_init} 个缺 initTick 的 shard（标准开局产物，"
            f"本轮的起始分布不是它；首例 {first_init_miss}）"
        )
    if skipped_mver:
        # 逐版本点名（谁写的、写了多少）：这一条同时是**节点侧版本漂移的现场证据**——
        # 同一台节点在同一轮内既产 8 又产 9 是池暖 worker 与磁盘代码脱钩的指纹
        # （2026-09-29 事故：a98 一节点两种版本，云端 5 连炸）。
        detail = "、".join(
            f"metrics_version={k if k is not None else '缺字段'}×{v}"
            for k, v in sorted(mver_bad.items(), key=lambda kv: str(kv[0]))
        )
        log(
            f"[publish] metrics: 剔除 {skipped_mver} 个行宽版本不符的 shard"
            f"（本机期望 {expected_mver}；实得 {detail}）——云端 grad 会按本机列数读，"
            f"旧/新导出器产的行宽必炸（不是静默错读），故不进 payload；"
            f"首例 {first_mver_miss}"
        )
    dirs: list[Path] = []
    for name in sorted(cands):
        ds = cands[name]
        if len(ds) > 1:
            # mtime 也可能在 stat 前一刻被删（同一竞态）：取不到就当作已消失的输家，
            # 排到最后（不是赢家），绝不因它整轮红。
            ds.sort(key=lambda d: (_shard_mtime(d), str(d)))
            for loser in ds[1:]:
                log(f"[publish] duplicate shard {name}: retire {loser} (keep {ds[0]})")
        dirs.append(ds[0])
    return dirs


def _previous_job_id(job_root: Path, run_id: str, course_fp: str, it: int) -> str:
    """同一 run/课程的**上一轮** job_id（R0 的 `opt_from` 来源）；扫不到 ⇒ 空串。

    扫 `job_root/*/manifest.json`：job 目录按 job_id 命名、`prune_job_dirs` 默认 keep=2 ⇒
    上一轮那份几乎总在（不在就退回内容寻址，**不**猜）。判据取 (runId, course_fp, it−1)——
    就是幂等键去掉那两个“每轮必变”的分量；`course_fp` 必须在，否则多课程单进程下会把
    别的课程的上一轮当成本课的起点。
    """
    if it <= 0 or not str(course_fp or "") or not str(run_id or ""):
        return ""
    try:
        dirs = sorted(p for p in job_root.iterdir() if p.is_dir())
    except OSError:
        return ""
    for d in dirs:
        try:
            mm = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(mm, dict):
            continue
        if (
            str(mm.get("runId") or "") == str(run_id)
            and str(mm.get("course_fp") or "") == str(course_fp)
            and int(mm.get("it") or -1) == int(it) - 1
        ):
            return str(mm.get("job_id") or d.name)
    return ""


def _shard_metrics_version(d: Path) -> int | None:
    """shard manifest 声明的 `metrics_version`；读不到/非整数一律 `None` = 视为不符。

    只读声明、**不碰 metrics.npy**（发布端逐 shard 只读几百字节的 manifest；行宽是否真的
    等于声明由云端 `ppo.np_core` 的响亮报错兜底）。不抛：清单读不动时的正确结局是
    剔掉这一份，而不是把整轮发布打红。

    ⚠ 本函数的“只读声明”在 wire-diet（`--wire-diet 1`）下**不再是全发布路径的性质**：P1
    会让 `remote.payload_slim` 每 shard 真读一遍 `metrics.npy`（它要现算 reward，并把行宽/
    行失配从「上云后」提前到打包前响亮拦下，`plan/wire-diet.plan.md` §2-6）。本函数自己仍
    只读 manifest —— 那条纪律属于它，不属于整条发布路径。
    """
    try:
        mm = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        raw = mm.get("metrics_version")
        return int(raw) if raw is not None else None
    except (OSError, ValueError, TypeError):
        return None


def _shard_state_init_ok(d: Path, log=lambda msg: None) -> bool:
    """shard 是否真的从人类中段快照起跑（manifest 带正整数 `initTick`）。

    判据函数与本地对账同源（`biz.resume.shard_state_init_ok`，两处共用一条规则）。
    读不到 manifest = 不收（宁可少一份也不把标准开局的局当本轮的语料）。
    """
    try:
        mm = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"[publish] shard {d} manifest 不可读（{e}）——按不满足 state_init 处理")
        return False
    return shard_state_init_ok(mm, True)


def _shard_lineage_ok(
    d: Path, course_fp: str, corpus_fp: str, log=lambda msg: None
) -> bool:
    """shard 的 manifest 是否与本次 job 同血缘（读不到 = 不收，宁可少一份也不整份被云拒）。"""
    try:
        mm = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"[publish] D14: 剔除 {d.name}（shard manifest 不可读: {e}）")
        return False
    if d14_corpus_match(course_fp, corpus_fp, mm):
        return True
    log(
        f"[publish] D14: 剔除 {d.name}（shard 血缘 "
        f"{str(mm.get('course_fp'))[:12]}…/{str(mm.get('corpus_fp') or '-')[:12]}… != "
        f"job {course_fp[:12]}…/{corpus_fp[:12] or '-'}…）"
    )
    return False


def _shard_mtime(d: Path) -> float:
    """shard manifest 的 mtime；目录已被并发删除（输家退场）时返回 +inf（排到最后）。"""
    try:
        return (d / "manifest.json").stat().st_mtime
    except OSError:
        return float("inf")


def iter_bc_shard_dirs(
    traj_dir: str | Path,
    it: int,
    round_name: str = "",
    log=lambda msg: None,
    *,
    course_fp: str = "",
    corpus_fp: str = "",
) -> list[Path]:
    """本轮 BC 语料 shard 集（plan/bc-cloud-integration.plan.md §4）：
    `<traj>/bc-data/<round>/bc_s*_seed*/`（含 manifest.json）；round 缺省 = `it{it}`，
    smoke 轮传 "smoke"（冒烟语料与真轮隔离，2026-09-13）。

    同名 shard 去重与 PPO（iter_shard_dirs）同策略：按 manifest mtime 保留最早一份
    （先写盘者 = 结算赢家），退役者响亮日志。D14 血缘过滤同 PPO（同一判据、同一原因）。"""
    data_root = Path(traj_dir) / "bc-data" / (round_name or f"it{it}")
    if not data_root.exists():
        return []
    groups: dict[str, list[Path]] = {}
    skipped = 0
    for p in data_root.glob("bc_s*_seed*"):
        if not (p.is_dir() and (p / "manifest.json").is_file()):
            continue
        if (course_fp or corpus_fp) and not _shard_lineage_ok(p, course_fp, corpus_fp, log):
            skipped += 1
            continue
        groups.setdefault(p.name, []).append(p)
    if skipped:
        log(
            f"[publish] D14: 剔除 {skipped} 个异血缘 BC shard（{round_name or f'it{it}'} 内混入了"
            "别的课程版本的语料）——云端会整份拒收，故不进 payload"
        )
    dirs: list[Path] = []
    for name in sorted(groups):
        ds = groups[name]
        if len(ds) > 1:
            ds.sort(key=lambda d: ((d / "manifest.json").stat().st_mtime, str(d)))
            for loser in ds[1:]:
                log(f"[publish] duplicate bc shard {name}: retire {loser} (keep {ds[0]})")
        dirs.append(ds[0])
    return dirs




def pack_code_zip(
    nn_root: str | Path,
    zip_path: str | Path,
    *,
    log=lambda msg: None,
) -> str:
    """打包 nn-training Python 源文件为 code.zip（**一个集群会话一次**，避免后继
    并行修改干扰云端代码一致性）。

    ⚠ 2026-10-10（plan/cluster-code-snapshot）：调用方已不再是「每门课首次 publish」，
    而是**启动路径的集群代码快照**（`remote/code_snapshot.py::ensure_cluster_snapshot`，
    锚 = hub / trainer / 导出进程中最先起来的那个，落 `<repo>/tmp/.code-snapshot/`）。
    本函数本身逐字节未变（名单/排除表/固定时间戳全保留）；消费侧只在快照缺失时才直接
    调它（回落 per-course 打包 + WARN）。

    包含：所有 .py + .jsonc 文件（递归）。
    排除：`tmp/` `weights/` `__pycache__/` `tests/` `rl-config.json`，
    **角色裁剪名单**（`e2e` `hub` `ladder` `curricula` `tools` `trainer` + 根 `conftest.py`，
    见下方 `_exclude_dirs` 的逐条判据），
    **以及任何以 `.` 开头的目录**（`.venv` / `.venv310bak` / `.mypy_cache` /
    `.ruff_cache` / `.pytest_cache` / `.git` … —— 一网打尽，不靠逐个列举）。

    ★ `levels/` **刻意不在排除名单里**（硬依赖，排掉 131/181 门课直接 FileNotFoundError）。

    ★ 2026-10-14（plan/offline-code-authority-and-width B2）：包里多一个 `code_meta.jsonc`
    （`CODE_META_NAME`：`commit` / `dirty` / `dirty_files` / `import_ok`）——**只由树内容唯一决定**
    的字段 + 同一套固定时间戳 ⇒ 「同源码同 sha」这条不变量不受影响（有用例钉）。

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
        # ── 角色裁剪（C1，2026-10-13）────────────────────────────────────────────
        # 判据 = 从 20 个云端节点入口（`remote.offline_boot` / `remote.run_loop` /
        # `remote.worker*` / `remote.train_core` / `worker.iter_rollout` …）做 import 闭包，
        # 只落到 `biz` / `common` / `remote` / `worker` 四个顶层包。下面这些**一次都没命中**
        # （`remote/` `worker/` `common/` 里 `from hub` / `from trainer` 也是零）。
        #   `e2e`       —— pytest 端到端用例（＋它自己的 conftest）
        #   `hub`       —— hub 服务端，只在本机跑
        #   `ladder`    —— 阶梯账本 + 计划（本机 `biz/ladder_*` 读）
        #   `curricula` —— 课程库（1.5 MB 原始 / 719 KB 压缩，全包最大单项）。云端加载的是
        #                  **随包下发的课程快照**（`remote/train_core.py:196-201` 把
        #                  `manifest["course"]` 写进 `job_dir/course.jsonc` 再 `load_course`）。
        #                  唯一残留依赖 = 课程 `gates` 里按名引用另一门课
        #                  （`biz/course_spec.py:448-450 _resolve_courses` → `CURRICULA_DIR`）；
        #                  全量 181 门实测只有退役的 `c6b-margin` 一门踩到。
        #   `tools`     —— 本机运维脚本（唯一引用 `remote/colab_bc.py:81` 是本机启动器）
        #   `trainer`   —— 本机训练循环；云端用 `remote/run_loop.py` **顶替**它
        #                  （`remote/bundle.py:342`「不需要 hub、不需要仓」）
        # ⚠ `levels` **不在**这里：课程里的 `"level"` 引用在云端加载时经
        #   `biz/course_resolve.load_course:84-111` → `resolve_level` → `LEVELS_DIR`，
        #   排掉 131/181 门课在 `train_core` 里 FileNotFoundError（实测，含现行 x20 线）。
        #   它只有 15 KB 压缩，为它冒险不划算。
        "e2e",
        "hub",
        "ladder",
        "curricula",
        "tools",
        "trainer",
    }
    # 根 `conftest.py` 只被 pytest 加载（`tests/` 早已排除，但根目录那份不住任何被排除的
    # 目录里 ⇒ 之前一直白进包）。`e2e/conftest.py` 随 `e2e/` 整棵走。
    _exclude_files = {"rl-config.json", "conftest.py"}

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
        # provenance（B2）：固定时间戳 + 内容唯一决定的字段（见 `code_tree_provenance`）。
        meta = zipfile.ZipInfo(CODE_META_NAME, date_time=(1980, 1, 1, 0, 0, 0))
        meta.compress_type = zipfile.ZIP_DEFLATED
        meta.external_attr = 0o644 << 16
        z.writestr(meta, json.dumps(code_tree_provenance(nn_root_p, log), ensure_ascii=False,
                                    sort_keys=True, indent=1))
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
#: `tests/worker/test_remote_iter_real_bun.py` 真跑一遍才是判据（那条测试就是这个事故的哨兵）。
TS_CODE_DIRS: tuple[str, ...] = ("src", "tools")
#: 允许进 zip 的后缀（.ts 源码 + .jsonc 数据 + .wasm 权重——`src/nn/conv/conv_wasm_adapter.ts`
#: 经 `import.meta.url` 读 `src/nn/conv/prebuilt/wasm/conv.wasm`，漏了它节点上卷积直接炸）。
TS_CODE_SUFFIXES: tuple[str, ...] = (".ts", ".jsonc", ".wasm")
#: **二进制加速后端**（2026-09-21）：native features 共享库按平台分（win/linux/darwin ×
#: x64/arm64），`src/nn/conv/conv_native_adapter.ts` 用**模块相对路径** `prebuilt/<平台>/…`
#: 找它（同一目录下还有 wasm 产物，走上面的 .wasm 后缀）。
#: 云机（PPO/TPU 都是 linux-*）没有 clang、也不持仓库 —— **只能靠 ts_code.zip 带过去**。
#: 漏了它的后果是最难查的那种静默：attestation 无库可加载 ⇒ 回落 wasm ⇒ 不报错、只是
#: rollout 每局回到 1338ms（收益归零）。用户 2026-09-21 点名过这条（离线训练任务的 rollout
#: 在 PPO 云机上执行）。
#:
#: 后缀单独一份、且**只对下面这个目录生效**：不给 `.so` 开全局口子（将来 `src/` 下若出现
#: 别的原生产物，应由它自己的白名单决定，而不是被这条规则顺手卷进来）。
#: 六个目标的库全带（~78 KB 原始字节）：云机 arch 在打包时未知，按需挑选反而会多一个
#: “挑错了 ⇒ 静默回落”的失败面。
TS_CODE_BINARY_DIRS: tuple[str, ...] = ("src/nn/conv/prebuilt",)
TS_CODE_BINARY_SUFFIXES: tuple[str, ...] = (".dll", ".so", ".dylib")
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
    """M3：把 rollout 用的 **TS 运行时** 打成 ts_code.zip（`src/**` + `tools/**` 下的
    `.ts/.jsonc/.wasm`，**加** `src/nn/conv/prebuilt/**` 下的 `.dll/.so/.dylib` 共享库），
    返回字节 sha256。

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

        def _add(f: Path, mode: int = 0o644) -> None:
            """固定时间戳写一件（sha 只由内容决定，否则 mtime 参与 → 缓存永远命中不了）。"""
            nonlocal n_files, n_bytes
            arc = str(f.relative_to(repo_p)).replace("\\", "/")
            zi = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = mode << 16
            data = f.read_bytes()
            z.writestr(zi, data)
            n_files += 1
            n_bytes += len(data)

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
                    _add(f)
        # ② 二进制加速后端（native 共享库；见 TS_CODE_BINARY_DIRS 注释）。
        #    排在 ① 之后 ⇒ 只增不减，① 的成员顺序不变（同内容 zip 的 sha 仍稳定）。
        for top in TS_CODE_BINARY_DIRS:
            base = repo_p / top
            if not base.is_dir():
                raise HubClientError(
                    f"pack_ts_code_zip: 加速后端目录不存在 {base}（跑 "
                    "bun tools/agent/native-build.ts --cross 重建）"
                )
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [
                    d for d in dirnames if d not in TS_CODE_EXCLUDE_DIRS and not d.startswith(".")
                ]
                dir_p = Path(dirpath)
                for fn in sorted(filenames):
                    if not fn.endswith(TS_CODE_BINARY_SUFFIXES):
                        continue
                    f = dir_p / fn
                    try:
                        if not f.is_file():
                            continue
                    except OSError:
                        continue
                    # 0o755：dlopen 只需读权限，位只是为了 `ls -l` 一眼认出是原生库。
                    # 注：`ZipFile.extractall` 在 Linux 上未必恢复 unix mode（2026-09-21 评审
                    # ⑨）——无影响，库是给 dlopen 用的；若将来直接 `./conv_features_cli` 执行，
                    # 那边需自行 chmod +x。
                    _add(f, 0o755)
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
    # wire-diet（plan/wire-diet.plan.md §3 P1/P2，2026-10-14）：payload 字节瘦身。
    # 缺省 False = 逐字节旧行为（A/B 通过后才在 trainer 侧翻 1）。
    # `reward_spec` 是**显式**入参而不是读 `biz.reward_context` 的模块级 holder：打包点不止
    # 训练进程一处（hub-server 重发 / smoke_loopback / plan_run），那些进程里 holder 缺省
    # `it=1, reward_fn=None` ⇒ 读 holder 会**静默**按 it=1 算错整轮奖励（评审 P1-4）。
    # None = 不做 P1（照旧带 metrics.npy），只做 P2。
    wire_diet: bool = False,
    reward_spec: object | None = None,
    # 零落盘（plan/payload-zero-disk §3.2，2026-10-27）：`presegmented` = shard 名 → **已压好的
    # tar 段**（dispatch 腿收到 sampler 回传的字节时就压好的那些，`ShardSegments.add`）。
    # 命中即不再读盘/重压；缺的（local 兜底腿 / 预采子进程 / 配额裁剪后补漏）由
    # `_pack_segmented` 现场从盘补 —— **同一个** `shard_segment` 函数体，不是第二条变换逻辑。
    # `diet_slim` = 产出这些段的**那份** `ShardSlim`（`ShardSegments.take` 给）；不传就按
    # `wire_diet` 自建。为什么要传：`payload_fmt` 与 `fmt_line()` 必须描述**真正发出去的字节**
    # —— 段全部命中时打包腿一次成员变换都不会走，自建的 slim 会报「未改」（日志骗人）。
    presegmented: Mapping[str, bytes] | None = None,
    diet_slim: ShardSlim | None = None,
    # demo 混 batch（x20 后续）：demo bank npz 原始字节（调用方读文件一次，见 loop_steps；
    # 3MB 级，blob 走内容寻址，worker 首轮下载后缓存命中）。空 = 关。
    demo_bank_bytes: bytes | None = None,
    demo_bc_coef: float = 0.0,
    demo_per_mb: int = 0,
    # value 门控牵引率 ρ（x26-obs it90 诊断；0.0 = 关，老行为逐字节不变）。
    value_anchor: float = 0.0,
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
    # 半离线（kind="run"，2026-09-17）：`plan.json` 的**字节**（由 `biz.plan.dump_plan`
    # 规范序列化）。节点靠它自主跑完 it+1..end_it——payload 必须带此文件，manifest 记
    # 它的 sha（`plan_sha256`）。传 bytes 而不是 dict：本模块是 `remote/` 层，不 import
    # `trainer/`（方向单一）；规范化序列化只有 `biz.plan.dump_plan` 一份，调用方自己 dump。
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
    # 归属角色（2026-09-25）：空 = 由 `kind` 推（run ⇒ offline，其余 ⇒ online——同一个
    # 快照）；显式传 `offline`/`online` 可覆盖（离线盘手工排活、将来加 kind 时的逃生口）。
    role: str = "",
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
    # R3/M4 / R1/M3：形态位常量与 blob 重打**只在真正发布时**需要 ⇒ 局部 import（理由见模块头）。
    import remote.opt_delta as opt_delta
    import remote.weights_delta as weights_delta

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
    opt_raw = opt_delta.opt_tar_bytes(ckpt_remote_dir)
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
    # demo bank 内容寻址：bank 静态（同 run 内逐轮同字节），sha 键天然跨轮复用。
    # 非 slim 下无内联退路（3MB 进 manifest 不可接受）——响亮拒绝，调用方开 slim。
    demo_raw = demo_bank_bytes or b""
    if demo_raw and not slim:
        raise HubClientError("demo bank 要求 slim=1（内容寻址；非 slim 无内联退路）——拒发")
    use_demo_blob = bool(slim and demo_raw)
    demo_sha = _sha256_bytes(demo_raw) if demo_raw else ""
    # opt-blob-diet（2026-09-24，§3.1/§3.4）：权重的**内容寻址段**。sha 直接复用
    # `init_weights_fp`——它的定义就是 `sha256_file(args.out)`，与内容寻址的要求逐字一致，
    # 所以 **manifest 不新增字段**（加一个同义的 `init_sha` 只会造第二份真相）。
    # raw 字节 = 该轮 `init_weights.json` 原样（= 上一轮 `result.weights_json` 解码后的字节）。
    init_raw = (
        Path(init_weights_path).read_bytes()
        if (init_weights_path and Path(init_weights_path).exists())
        else b""
    )
    use_init_blob = bool(slim and init_raw)
    opt_init = (
        ""
        if use_opt_blob
        else (base64.b64encode(opt_raw).decode("ascii") if opt_raw else "")
    )
    # 4) manifest 预建（payload_sha256 占位）→ 打包（payload 内不再带占位 manifest）
    extra_files: list[Path] = []
    tmp_extra_dir = job_root_p / ".extra_tmp"
    # ⚠ 这个 mkdir 刻意**挪到守卫之后**（下方 4b）：守卫要求「拒发时不留任何痕迹」，
    # 而 `.extra_tmp` 也算痕迹（半份 job 目录比没有 job 更危险）。
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
        "demo_sha": demo_sha if use_demo_blob else "",
        "demo_bc_coef": float(demo_bc_coef),
        "value_anchor": float(value_anchor),
        "demo_per_mb": int(demo_per_mb),
        "slim": bool(slim),
        "data_fp": fp,
        "payload_sha256": "",
    }
    # job 身份：幂等键的五个分量在上面那份字面量里就齐了（下面只补**非键**字段），
    # 所以刻意在这里算出来——守卫要在**任何写盘之前**用它。
    m["job_id"] = make_job_id(m)
    # 4b) **发布端守卫**（2026-09-24 job 身份事故，plan/job-identity-collision.plan.md §3.3）：
    #     同一条 job 身份不许被**别的课程**占用。判据的唯一实现在 `protocol.collision_rows`。
    #     位置：job_id 算完之后、**任何写盘之前**——拒发时不留 job 目录、不留账本行、
    #     连 `.extra_tmp` 都不建（「半份 job」会让控制台显示一条永远等不到工人的 pending）。
    #     `course_fp` 进键之后正常发布撞不出来 ⇒ 这是哨兵：手写 manifest / 回灌历史 job /
    #     跨 hub 搬目录等旁路一旦造出同身份，必须在发布那一刻响亮，而不是等污染被看出来。
    _conflicts = collision_rows(job_root_p, m)
    if _conflicts:
        _who = "、".join(
            f"{r['course'] or '<单课程>'}（job={r['job_id']}）" for r in _conflicts
        )
        raise HubClientError(
            f"job 身份已被别的课程占用——拒发（本课 {job_root_p.parent.name or '<单课程>'} "
            f"course_fp={str(course_fp)[:12]}… job_id={m['job_id']!s}；占用方：{_who}）。"
            "同一个 (runId, course_fp, it, init_weights_fp, data_fp) 只能属于一门课："
            "两门课内容完全相同（course_fp 相同）时会共享一个 job 身份，"
            "而 hub 无法在两者间区分归属（两个训练轮会读到同一份结果）。"
            "处置：给其中一门课不同的课程文件（改注释也算），或换 runId 重启那条腿。"
        )
    tmp_extra_dir.mkdir(parents=True, exist_ok=True)
    if rollout_spec is not None:
        m["ts_code_sha256"] = str(ts_code_sha256)
        m["rollout"] = rollout_spec
        # ★ opt-blob-diet（2026-09-24，§3.4）：kind=iter 不再把 payload 撑成「带权重」。
        # 节点侧 rollout 要的那份权重改由 `init` blob 提供（worker 的 `_resolve_weights`
        # 会在 **rollout 之前**把它落到 `job_dir/init_weights.json`）。
        # 代价 = 发布端多一条守卫：没有权重就根本不该发这份活（否则云上必炸在半路）。
        if not (init_weights_path and Path(init_weights_path).exists()):
            raise HubClientError(
                f"kind={kind!r} 必须有 init 权重（rollout 要用它，PPO 也要用它读 arch）："
                f"init_weights_path={init_weights_path!r} 为空或不存在——拒发"
            )
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
    #
    # ★ opt-blob-diet（2026-09-24，§3.4）：「不带」的门是 **`use_opt_blob`**，不是
    #   `use_init_blob`：`use_opt_blob = slim AND 有 opt 字节` ⇒ 有 opt blob 时必然也有
    #   init blob（权重文件存在 ⇒ `init_weights_fp` 是 64-hex ⇒ 落盘）。所以
    #   `not use_opt_blob or keep_init_weights` 已经等价于「两个 blob 都齐才省掉 payload
    #   里那份」，而且把首轮/冷启动（无 opt blob）留成了 W0 —— 那条路是导出包与
    #   `bundle.py` 依赖的（plan §2.2：离线冗余是特性，不是浪费）。
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
        for k in ("demo_sha", "demo_bc_coef", "demo_per_mb"):
            m.pop(k, None)
        for k, v in (extra or {}).items():
            m[k] = v
    else:
        m["reward_formula"] = reward_formula
        m["formula_hash"] = formula_hash
        m["metrics_version"] = metrics_version
        m["gamma"] = gamma
        m["lam"] = lam
    # ---- R0（M0，plan/wire-diet.plan.md §3 R0）：把「上一轮是哪个 job」写进 manifest ----
    # 判据在 worker 侧（`remote/course_cache`）：`manifest.opt_from == 本机 latest.job_id`
    # ⇒ 本机那份 f32 全量副本就是**权威链的延续点**（零字节、零降质），否则回退内容寻址。
    # 为什么由**这里**写而不是让 worker 自己推「it−1 邻接」：身份映射的键就是 job_id，
    # 让读取端再推一遍 = 第二份真相。扫不到（首轮 / 上一轮已 prune / 换机重开）⇒ **不添键**
    # （旧端忽略未知键，但没有键才是“回退内容寻址”的准确表述）。
    _prev = _previous_job_id(job_root_p, run_id, course_fp, it)
    if _prev:
        m["opt_from"] = _prev
    # ---- R3（M4）：opt 回传的**形态位**（2026-10-14，plan/wire-diet.plan.md §3 R3 / §12）----
    # 有 base（`use_opt_blob` = 真能发 delta）才写；否则**不添键** —— 对外发布路径的 manifest
    # 逐字节不变（与 `payload_fmt` 同纪律）。发布端写下、验收端就是它（兼容矩阵 §4，缺键 = 旧形态）。
    if use_opt_blob:
        m["opt_fmt"] = opt_delta.OPT_DELTA_FMT
    # ---- R1（M3）：权重回传的**形态位**（同 opt_fmt 的纪律）----
    # 有 init 字节（= delta 的基准）才写；否则**不添键** —— 对外发布路径的 manifest 逐字节不变。
    # 基准就是本轮发出去的 init 权重（`init_weights_fp` = `sha256(这条字节)`）⇒ 验收端逐字节可对。
    if use_init_blob:
        m["w_fmt"] = weights_delta.W_DELTA_FMT
    m["kind"] = kind
    m["seed"] = job_seed(run_id, it, init_weights_fp)
    if course_name:
        # P4-W2 归属（S9）：在 job_id 计算**之后**注入——幂等键不含短名，旧链字节不变；
        # normalize_manifest 允许未知/可选键，wire 兼容（D1：未知字段忽略）。
        m["course_name"] = str(course_name)
    if dispatch:
        # 同上：job 身份已定，传输腿的意图是**附加语义**不是身份成分。旧 hub/旧 worker
        # 忽略未知键 ⇒ 缺席即 pull，行为逐字节不变。
        m["dispatch"] = str(dispatch)
    # 归属角色（2026-09-25，plan/online-offline-role-routing §2.1）：**发布时定死**的 job
    # 属性——hub 的派发/认领闸读它，不再读易变的「课程当前 mode」。
    # 与 `dispatch` 同位置（job_id 之后）：归属不是身份成分，重发同一轮不会因为归属变化而
    # 变成两个 job。缺省由 kind 推（同一份快照，不引入第二个读盘点）；显式 `role=` 可覆盖。
    # 旧 hub/旧 worker 忽略未知键 ⇒ 没这个字段的旧 job 由 `role_of` 按 kind 兜底。
    m[ROLE_FIELD] = role if role in ROLES else role_of({"kind": kind})
    # 5) 落盘 job 目录：payload.zip（zip 内 manifest 为占位副本——payload_sha256 尚
    #    未算出）→ 回填真实 sha → 权威 manifest.json（worker 以 job 记录校验，D1）。
    #    normalize_manifest 在回填后调用：payload_sha256 必填非空，占位空串会误拒。
    jid = str(m["job_id"])
    jd = job_root_p / jid
    jd.mkdir(parents=True, exist_ok=True)
    zip_path = jd / PAYLOAD_NAME
    _slim: ShardSlim | None = None
    if wire_diet:
        _slim = diet_slim
        if _slim is None:
            from remote.payload_slim import build_slim

            _slim = build_slim(
                reward_spec=reward_spec,
                it=it,
                metrics_version=metrics_version,
                formula_hash=formula_hash,
                log=log,
            )
    sha = pack_payload_zip(
        shard_dirs,
        extra_files,
        zip_path,
        m,
        member_bytes=(_slim.member_bytes if _slim is not None else None),
        shard_extra=(_slim.shard_extra if _slim is not None else None),
        presegmented=presegmented,
        log=log,
    )
    m["payload_sha256"] = sha
    if presegmented:
        # 验收判据 3（plan §4）：纯分布式腿下两个数该相等（每个 shard 都在 rollout 期压过）。
        _hit = sum(1 for d in shard_dirs if Path(d).name in presegmented)
        log(
            f"[publish] zero-disk it{it}: packed {_hit}/{len(shard_dirs)} presegmented shard"
            "（命中段直接拼接，未读盘/未重压）"
        )
    if _slim is not None:
        # 形态自述（旧端忽略未知键）：读取端以「盘上有哪些文件」为准，这个键是发布端记录/取证面。
        # 什么都没改时**不添键**（对外发布路径的 manifest 逐字节不变）。
        log(f"[publish] {_slim.fmt_line()}（payload_sha256={sha[:12]}…）")
        if _slim.fmt:
            m["payload_fmt"] = _slim.fmt
    # M2 B3：blob 落盘——pull worker 经 /jobs/{id}/blob 取；push 由 push_client 判缓存
    # 后按需随 body 发送（读的就是这两个文件）。
    if use_opt_blob:
        blob_path(jd, BLOB_OPT).write_bytes(opt_raw)
    if use_ref_blob:
        blob_path(jd, BLOB_REF).write_bytes(ref_raw)
    if use_demo_blob:
        blob_path(jd, BLOB_DEMO).write_bytes(demo_raw)
    if use_init_blob:
        blob_path(jd, BLOB_INIT).write_bytes(init_raw)
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
        + (f" init_blob={init_weights_fp[:12]}…" if use_init_blob else "")
    )
    if use_init_blob:
        # payload diet 读数：这一刀省下的就是 tar 里那份重复的 model.pt
        # （实测 268,996 B/轮，见 plan/opt-blob-diet.plan.md §1.1）。
        log(
            f"it{it}: payload diet —— 权重走 init blob（{len(init_raw)} B raw，"
            f"sha={init_weights_fp[:12]}…）不进 payload；tar 里不再有 model.pt"
        )
    return m






def _pack_opt_init(ckpt_remote_dir: str | Path | None) -> str:
    """上轮 ppo_ckpt_remote → **plain base64** tar（旧内联口径，slim=false 逐字节回退）。"""
    import remote.opt_delta as opt_delta  # 局部 import：见模块头（hub 顶层不吃三方依赖）

    raw = opt_delta.opt_tar_bytes(ckpt_remote_dir)
    return base64.b64encode(raw).decode("ascii") if raw else ""


def _append_ledger(jsonl_path: str | Path, event: dict) -> None:
    """追加一条账本事件（唯一实现见 `common.fs.append_jsonl`）。"""
    append_jsonl(jsonl_path, event)


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


def reap_job_dir(
    job_root: str | Path,
    jid: str,
    *,
    run_id: str = "",
    it: int | None = None,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> bool:
    """**带护栏**删除一个 job 目录（M1/M2/M3 共用；防误删本身就是新的事故源）。

    护栏（任一不符 ⇒ 拒删 + WARN，返回 False）：
      1. `<job_root>/<jid>/` 在盘；
      2. `manifest.json` 在盘且 `job_id == jid`（不是别人的目录/半发布残留）；
      3. `run_id` 非空 ⇒ `manifest.runId == run_id`（账本行的 runId——防「账本与目录错位」，
         这正是 2026-10-13 事故里人工 rm 误删新 job 的形状）；
      4. `it` 非 None ⇒ `manifest.it == it`（同上）。

    删除走 `rmtree_best_effort`：失败（占用/权限）只记日志，**永不抛**。
    """
    jd = Path(job_root) / jid
    if not jd.is_dir():
        return False
    try:
        man = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"WARN: {jd} 无 manifest.json/损坏——拒绝删除（护栏）")
        return False
    if not isinstance(man, dict) or str(man.get("job_id") or "") != jid:
        log(f"WARN: {jd} 的 manifest.job_id 与目录名不符——拒绝删除（护栏）")
        return False
    if run_id and str(man.get("runId") or "") != run_id:
        log(
            f"WARN: {jd} 的 manifest.runId={man.get('runId')!r} 与账本 {run_id!r} 不符"
            "——拒绝删除（护栏）"
        )
        return False
    if it is not None and man.get("it") != it:
        log(f"WARN: {jd} 的 manifest.it={man.get('it')!r} 与账本 {it!r} 不符——拒绝删除（护栏）")
        return False
    rmtree_best_effort(jd, ignore_errors=True)
    return True


#: M2 撤单行的 reason 前缀——**写者与读者共用的唯一常量**。
#: 读者 = `trainer/loop_remote_drive.py::_self_healable_cancel`（M1 看门狗：只对「上一代残留
#: 被清理」这一类自愈重发；人工 `/admin/cancel-job`、`cancel_unsettled_jobs` 撤的不许替操作员反悔）。
#: 2026-10-17（plan/export-must-not-cancel-live-job §4）：文案就住这里，改它不会静默漂。
M2_CANCEL_REASON_PREFIX = "上一 runId"


def cancel_jobs_of_previous_runs(
    jsonl_path: str | Path,
    job_root: str | Path,
    run_id: str,
    *,
    hub_url: str = "",
    hub_token: str = "",
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> list[str]:
    """M2：把**上一个 runId** 留在本课账本里的 pending job 整体作废，并兑现到磁盘。

    与 `cancel_stale_jobs` 的分工：那条按 `it <=` 在**发布新活那一刻**清滞后项；这条按
    **runId** 在**腿启动时**清上一代（与 it 无关）——“重启了就该从头再来”的唯一实现。
    `RUN_ID` 是进程级常量（`trainer/dispatch_queue.py`），serve 一进程多课共享：
    同进程内停/开课**不**触发，这是有意语义（那是 M3「重发本轮」的活）。

    每个命中 jid：
      ① 写 `job_cancelled`（幂等净态门；唯一事实源，hub 认领闸按净态对账）；
      ② best-effort `POST /admin/unfreeze`（冻结只住 hub 内存；不清 ⇒ 同 jid 重发死）；
      ③ best-effort `POST /admin/cancel-job`：404（未知）/脱网容忍；**409 = 在飞不撤**
         （2026-10-03 用户裁决）——账本已作废，目录留给在算的 worker 回传；
      ④ 过护栏删目录（`reap_job_dir`：manifest 的 job_id/runId/it 与账本行一致才删）。

    返回被清理的 jid 列表。**永不抛**给调用方——清不掉不能阻断开关课。
    """
    from remote.hub_http import ADMIN_CANCEL_INFLIGHT, admin_cancel_job, unfreeze_job
    from worker.bc_ledger import scan_jobs

    pending, _done, terminal = scan_jobs(jsonl_path)
    out: list[str] = []
    for jid, row in sorted(pending.items()):
        if jid in terminal:
            continue  # 已有终局事件（收口/作废）——净态不是 pending，不碰（保守）
        row_run = str(row.get("runId") or "")
        if not row_run or row_run == run_id:
            continue  # 缺 runId 的历史行 / 本轮自己的活：不碰
        row_it = row.get("it") if isinstance(row.get("it"), int) else None
        # ① 账本作废（幂等）
        cancel_job(
            jsonl_path,
            jid,
            reason=f"{M2_CANCEL_REASON_PREFIX} {row_run[:8]}… 遗留（腿启动清理）",
            it=row_it,
        )
        # ②/③ hub 侧最好努力（不可达/在飞都容忍）
        unfreeze_job(hub_url, hub_token, jid, log=log)
        if admin_cancel_job(hub_url, hub_token, jid, log=log) == ADMIN_CANCEL_INFLIGHT:
            log(f"上一 runId 的 job {jid} 在飞（hub 409 不撤）——账本已作废，目录保留给回传")
            out.append(jid)
            continue
        # ④ 护栏删除
        reap_job_dir(job_root, jid, run_id=row_run, it=row_it, log=log)
        out.append(jid)
    if out:
        log(f"启动清理：作废上一 runId 的 {len(out)} 份残留 job（与 it 无关）")
    return out


def cancel_job(
    jsonl_path: str | Path, job_id: str, *, reason: str = "", it: int | None = None
) -> bool:
    """**单 job 定向作废**（写 `job_cancelled` 账本事件；净态门 + 幂等）。

    与 `cancel_stale_jobs`（发布时按 `it <= 当前` 批量清滞后项）互补：这是「**这一个** job
    已证明永远不会被接受」的收口（2026-10-11 事故；评审 F2）——三重校验拒收后由
    `trainer/loop_remote_job.py::_remote_ppo_land` 调用（人工作废走 hub 的
    `POST /admin/cancel-job`，那是另一条腿）。

    判据 = **净态**（文件序里该 jid 最后一条 pending/completed/cancelled 事件）：
    只有 `job_pending` 才追加作废并返回 True；已 completed/cancelled/账本缺失 ⇒ False
    （幂等可重放——重复拒收不重复记账）。hub 的 `claimable_job_ids` 从同一份账本重算
    （共读），重启后仍然有效（I-5）。
    """
    p = Path(jsonl_path)
    if not p.exists():
        return False
    # 净态折叠**只写一份**：`worker.bc_ledger.job_net_event`（训练侧与 hub 侧、看门狗与本函数
    # 读的是同一条规则；2026-10-17 之前这里是第 2…4 份拷贝，正是 2026-10-16 事故的成因模式）。
    from worker.bc_ledger import JOB_PENDING_EVENT, job_net_event

    ev = job_net_event(p, job_id)
    if ev is None or ev.get("event") != JOB_PENDING_EVENT:
        return False
    event: dict = {"event": "job_cancelled", "job_id": job_id, "reason": reason, "ts": time.time()}
    if it is not None:
        event["it"] = int(it)
    _append_ledger(p, event)
    return True


# ------------------------------------------------------------------ 校验落位


def verify_and_land(
    result: dict,
    manifest: dict,
    *,
    init_weights_path: str,
    traj_dir: str | Path,
    it: int,
    out_weights: str,
    state_init: bool = False,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [hub] {msg}", flush=True),
) -> str:
    """三重校验（D12）+ 落盘（weights_json → args.out；opt tar → ppo_ckpt_remote）。

    校验：
      1. init_weights_fp == 当前 args.out 指纹（fencing：云从 hub 打包的 init 起步）；
      2. data_fp == **发布快照**（`manifest.data_fp`，即打进 payload 的那份集合）；
      3. commit 一致（result.commit_echo == manifest.commit）。
    任一不等 → 响亮拒绝（抛 HubClientError），不落盘。
    返回落盘 weights 的指纹（供 wver 下游直接使用）。

    ★ 2026-10-23（y32 事故，`reports/y32-ppo-job-vs-iter-20261023.md`）：第 2 项**全 kind
    都只认声明集**，不再对 `it{it}` 活目录重算。原实现对本地轮重算活目录，而慢节点的
    **计划局** shard 会在 job 在飞期间才落进来（轮次收官时 dispatch 不等在飞线程）⇒
    集合漂 ⇒ 一份完全正确的回传被判死 ⇒ 整遍 PPO 白烧。实测 22 job / 10 轮（~55% GPU）。
    这么改不丢防线：worker 的 `result["data_fp"]` 逐字抄 `manifest["data_fp"]`
    （`remote/train_core.py:828`）⇒ 两侧恒等，「本地重算」从来只是「目录变了没有」的
    自检，对云端行为零判别力。而 payload 是快照、训练也吃快照 ⇒ 权威只能是声明集
    （与 `kind="iter"` 同一条口径）。活目录的重算降级为**只报不拒**的供给诊断。

    kind="iter"（M3）：本轮 shard 由云节点现产，hub 侧**无本地副本可重算**——「实产集 ==
    声明集」由节点侧在产完后用同一函数校验（不通过就在云上响亮失败，job 不会回传成功
    结果），§5.5① 的逐位对拍是离线验收，不是这条链的守卫。
    """
    # R3/M4 / R1/M3：delta 重建（吃 numpy/torch）只在**落位时**需要 ⇒ 局部 import（理由见模块头）。
    import remote.opt_delta as opt_delta
    import remote.weights_delta as weights_delta

    m = normalize_manifest(manifest)
    if result["init_weights_fp"] != _sha256_file(init_weights_path):
        raise TripleCheckError("三重校验失败: init_weights_fp 不匹配（云起点 ≠ 当前 args.out）——拒收")
    # ★ 第 2 项：**只认发布快照**（理由见 docstring ★ 2026-10-23）。活目录不参与判定。
    declared_fp = str(m["data_fp"])
    if result["data_fp"] != declared_fp:
        raise TripleCheckError(
            f"三重校验失败: data_fp 不匹配（云={result['data_fp'][:12]}… "
            f"声明={declared_fp[:12]}…）——拒收"
        )
    if str(m.get("kind", "ppo") or "ppo") != "iter":
        # 供给诊断（**只报不拒**）：本地活目录 vs 发布快照。发散 = 有 shard 迟到或离场
        # （y32 实测 a96 在 job 在飞期间落盘 2 张）⇒ 本轮语料比计划少几张。那是调度侧
        # 的账（派给 slow 节点的计划局该在 rollout_end 前封盘），落位不该替它判死。
        try:
            live_dirs = iter_shard_dirs(
                traj_dir,
                it,
                log=lambda _m: None,
                course_fp=str(m.get("course_fp") or ""),
                corpus_fp=str(m.get("corpus_fp") or ""),
                state_init=state_init,
            )
            live_fp = data_fp(live_dirs)
        except Exception as e:  # 目录被清/被删都不该阻断落位
            log(f"WARN: it{it} 活目录对账不可做（{e}）——不阻断落位")
        else:
            if live_fp != declared_fp:
                log(
                    f"WARN: it{it} 活目录与发布快照不一致（活 {len(live_dirs)} 张 "
                    f"live={live_fp[:12]}… vs 声明={declared_fp[:12]}…）"
                    "——迟到/离场 shard；本轮按快照训练，不拒收"
                )
    if result["commit_echo"] != m["commit"]:
        raise TripleCheckError(
            f"三重校验失败: commit_echo={result['commit_echo'][:12]}… != "
            f"manifest.commit={m['commit'][:12]}…——拒收"
        )
    # 落盘 weights_json（原子 replace）
    wj = decode_weights_json(str(result["weights_json"]))
    # ---- R1（M3）：形态位命中 ⇒ hub 侧**重建**（缺键 = 旧形态逐字节不变）----
    # 基准 = 本轮发出去的 init 权重（`init_weights_path`：三重校验①已钉住它就是 manifest 的
    # `init_weights_fp`）⇒ 容器里的 `base_sha` 与它相等 = exact 档（worker 下的是我们的 blob）；
    # 不等 = identity 档（worker 用自己那份 f32 副本当基准 —— 靠身份闸 + 结构闸，plan §12）。
    # 缺 base / 容器坏 / 结构不符 ⇒ 抛出去（**绝不**把 delta 当权重落盘）。
    if weights_delta.w_fmt_of(manifest) == weights_delta.W_DELTA_FMT:
        wj = weights_delta.land_weights_delta(wj, base_path=init_weights_path, log=log)
    out_p = Path(out_weights)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_p.with_suffix(out_p.suffix + ".remote.tmp")
    tmp.write_bytes(wj)
    os.replace(tmp, out_p)
    wver = _sha256_file(str(out_p))
    log(f"weights landed -> {out_weights} ({len(wj)} bytes, wver={wver[:12]}…)")
    # 落盘 opt tar（D5：Adam 动量随 job 往返）
    ckpt_dir = Path(traj_dir) / f"it{it}" / "ppo_ckpt_remote"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    opt_tar = decode_opt_tar(str(result["opt_tar_b64"]))
    # ---- R3（M4）：形态位命中 ⇒ hub 侧**重建**（缺键 = 旧形态逐字节不变）----
    # 基准 = 上一轮 landed 的那份（`it{it-1}/ppo_ckpt_remote/opt.pt`，就是本轮 `opt_sha` 打的 tar）。
    # `base_sha` 与**本机发出去的那份**相等 = exact 档（worker 下的是我们的 blob）；不等 = identity 档
    # （worker 用自己那份 f32 副本当基准 —— hub 无权对账，只记一行判别；plan §12）。
    # 缺 base / 缺 torch / 结构不符 ⇒ `apply_opt_delta` 抛出去（**绝不**把 delta 当权重落盘）。
    # `land_opt_delta` 自己按**容器魔数**分派（形态位只决定走不走这条路）：旧 worker / 冒烟回显
    # 送来的整份 tar 在这里按旧形态解包（响亮一行），真容器坏掉仍拒收；`extract` 注入 `_extract_tar`
    # 是为了不让 L0 的容器模块 import 本模块的邻居。
    if opt_delta.opt_fmt_of(manifest) == opt_delta.OPT_DELTA_FMT:
        opt_tar = opt_delta.land_opt_delta(
            opt_tar, traj_dir=traj_dir, it=it, ckpt_dir=ckpt_dir, extract=_extract_tar, log=log
        )
    else:
        _extract_tar(opt_tar, ckpt_dir)
    # M2：把**原始回传字节**落盘（sibling `.tar`）——下一轮 publish 的 opt_sha 必须
    # 由原样字节算出，才能等于云 worker blob_cache 的键（重打 tar 会 sha 漂移 ⇒ 每轮
    # 未命中 ⇒ 1.19MB 白传回来）。写失败不阻断落位（只是退回重打路径）。
    # ⚠ R0/M0（2026-10-14，plan/wire-diet.plan.md §3 R0）先定口径：R1/R3 落地后权威字节
    # 不再是「回传来的原始字节」，而是 hub 的**重建产物**（`B_n = B_{n-1} + Q(A_n − B_{n-1})`）
    # ——它与 worker 本机那份 f32 全量**必然不同 sha** ⇒ 「sha 相等」作为命中判据退役，
    # 接手的是 `remote/course_cache` 的**身份映射**（`manifest.opt_from` 对上本机 `latest`）
    # + 命中自校（sha+size 逐字节验 blob）。今天（R1/R3 未上）本行行为逐字节不变。
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
    local_fp = data_fp(
        iter_bc_shard_dirs(
            traj_dir,
            it,
            round_name,
            log=log,
            course_fp=str(m.get("course_fp") or ""),
            corpus_fp=str(m.get("corpus_fp") or ""),
        )
    )
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
    """opt tar 字节 → `dest`（唯一实现见 `common.fs.extract_tar_bytes`）。"""
    extract_tar_bytes(tar_bytes, dest)
