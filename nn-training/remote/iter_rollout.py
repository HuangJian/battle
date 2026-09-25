"""iter_rollout —— 节点侧「本轮 rollout」执行器（M3，plan/remote-wire-remediation §5.2）。

job kind = `iter` 的语义：**一整轮**上云。节点拿到的 payload 里没有 shard（只有 init
权重 + 可选 blob），shard 由本模块现场产出：

  argv（hub 用 `rl/cmd.build_rollout_cmd` 拼的、逐局一条，路径一律 job 目录内相对路径）
    → 线程池跑 `bun tools/sim/export-rl-rollout.ts …`（cwd = TS 代码根）
    → 每局一个 `w{i}/` 目录 + `_rl_report.json`
    → **逐位**校验实产 shard 集 == 声明集（data_fp 两侧同函数）
    → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）

为什么 argv 从 hub 发而不是在节点重算：hub 的 `build_rollout_cmd` 是三导出器 + 课程
覆盖 + D14 血缘的唯一拼装点；节点重算就等于在协议里复制一份它的知识。用同一个函数的
输出，计划 §5.5① 的「节点 shard 与本机 rollout 逐字节一致」是**构造性质**——命令都
一样，剩下的只有导出器本身的确定性。

执行方式：默认走 **长驻 worker 池**（`remote/serve_pool.py`，`--serve` 协议，与
`sampler-agent` 的 `/v1/task` 路径同一份契约）—— 逐局 spawn 时每局都要重付 bun 启动 +
wasm 编译 + 首用 attestation×3 + 权重解析，本模块实测 **1.45–1.47×**
（`docs/nn/runtime-opt.md` §22；agent 侧同一机制为 §20 的 1.59×）。
池只覆盖「省掉每局启动」：单局任何不确定（超时/worker 死掉/ERR/取不到位）都**当场回退**
一次性 `Popen`，路径与池不存在时逐字节相同 ⇒ 只慢不错、绝不丢局。关池：`NN_SERVE_POOL=0`。

本模块**不碰 torch / 不碰 PPO**：rollout 完就把 shard 目录交回 `worker.run_job` 的既有
PPO 链路（load_episodes → chunk_episodes → ppo_update）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from log_bundle import LogBundle
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import cpu_worker_slots, effective_cores

# 单局看门狗的口径常量与 eval **共用一份**（`remote/game_watch.py`）：点名线 5s、首次尝试硬顶
# 也是 5s（用户口径「单局 >5s 肯定不正常」⇒ 超时原地重跑）、重试上限 ×4、最多 3 次、轮询 0.5s。
# **一律通过模块属性读**（`game_watch.X`）而不是 `from ... import X`：import 会把值抄成第二份
# 绑定，测试 patch 了 `game_watch` 的那一份、调用点却还在读旧绑定（两处不一致就是静默的错口径）。
from remote import game_watch, serve_pool
from remote.protocol import (
    ProtocolError,
    RetryableError,
    data_fp,
    iter_expected_data_fp,
    parse_shard_name,
    shard_name,
)

#: 每局日志（诊断用；与本地 `run_rollout` 的 `w{i}/rollout.log` 同名同形）。
ROLLOUT_LOG_NAME = "rollout.log"
#: `export-rl-rollout.ts` 每局的聚合摘要（`combine_reports` 的输入）。
REPORT_NAME = "_rl_report.json"
#: 并行度上限（防 hub 侧误配 workers=1000 把节点打爆；16 vCPU 节点的合理值远低于此）。
MAX_WORKERS = 256

#: 本机并行度夹取的覆盖开关（实验/排障用）：正整数 = 直接当上限，`0` = **不夹**
#: （完全按 hub 给的 workers 走）。缺省 = 按本机核数夹取（`cpu_worker_slots()`）。
ENV_WORKERS_CAP = "NN_ROLLOUT_WORKERS_MAX"


def workers_cap() -> int:
    """**跑 rollout 那台机器**的并行度上限（0 = 不夹）：缺省 = `cpu_worker_slots()`，env 可覆盖。

    ⚠ 「本机」= **执行本函数的进程所在的机器**（节点/云机自己），**不是** hub、也不是导出机。
    核数走 `platform_utils.effective_cores()`（容器配额/亲和掩码 > `os.cpu_count()`）：
    Kaggle 的 TPU 会话 `os.cpu_count()` 报**宿主机**的 224，而 cgroup 只给 **96** 核 ⇒ 上限 92。
    所以日志里那行 `workers=220` 本身就是一个误读的产物（220 = 按 224 核算出来的），现在它会被
    夹成 92（`并发夹取=220→92`）——这正是本轮要拿掉的 2.3× 超订。裸机/Windows 无 cgroup，
    `effective_cores()` = `os.cpu_count()`，行为不变。
    夹取也**只降不升**（`min`）：hub 给的比本机口径小就照 hub 的（离线段那个「覆盖导出机规模」
    的动作在 `run_loop.open_run_context`，同样在云机上求值，也走同一条核数口径）。

    为什么必须有这道闸（2026-09-25 云机卡死）：`spec.workers` 可能是**导出机**的规模
    （离线腿已在 plan 侧用 `--rollout-workers` 覆盖，但 kind=iter 直给的值没人夹），而节点侧
    原来唯一的闸是 `MAX_WORKERS=256` —— 8~16 核节点上 200+ 并发意味着每局都被挤过 5s 硬顶，
    那正是「成批超时 → 回退放大 → 整轮停摆」的入口条件。夹取只改并行度：
    `workers` 不进 `data_fp`（见 `protocol.iter_declared_entries`），argv/wver 一个字不动。
    """
    raw = (os.environ.get(ENV_WORKERS_CAP) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass  # 非法值 ⇒ 回落到核数口径（响亮的事交给日志，不在这里抛）
    return cpu_worker_slots()


def resolve_bun(name: str = "") -> str:
    """节点侧 bun 可执行路径。找不到 → ProtocolError（响亮，绝不静默换 python）。"""
    want = str(name or "").strip() or "bun"
    found = shutil.which(want)
    if not found:
        raise ProtocolError(
            f"节点上找不到 {want!r}（kind=iter 需要 bun 跑 rollout）——"
            "bun 必须随节点引导装好，且装 bun 要发生在装 tailnet/代理之前"
        )
    return found


def bun_version(bun: str) -> str:
    """`bun --version`（启动自检行用；失败返回空串，不致命）。

    encoding=utf-8：裸 text=True 在 zh-CN Windows 按 cp936 解码，读线程死亡时
    stdout=None（§30 / test_remote_iter_real_bun GBK 事故同源）。
    """
    try:
        p = subprocess.run(
            [bun, "--version"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **_POPEN_NO_WINDOW,
        )
        return (p.stdout or "").strip().splitlines()[0] if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


#: argv 里必须“按 job 目录解析”的路径 flag（见 `_exec_argv`）。
_JOB_REL_FLAGS: tuple[str, ...] = ("--out", "--weights")


def _exec_argv(argv: list[str], job_dir: Path) -> list[str]:
    """把 argv 里的 job 相对路径换算成绝对路径（**不改协议**：协议层只允许相对，
    节点侧才知道自己的真实目录）。

    为什么必须换算：导出器脚本本身（argv[0] = `tools/sim/export-rl-rollout.ts`）是相对
    **TS 代码根**解析的，而 `--out`/`--weights` 是相对 **job 目录**的——两个根不是同一个
    （TS 树住在共享的 `ts_code_cache/<sha>/`，job 目录住 job 私有数据）。所以进程 cwd 定
    为 TS 根，job 侧的两个路径必须先绝化。
    """
    out = list(argv)
    for flag in _JOB_REL_FLAGS:
        if flag not in out:
            continue
        j = out.index(flag) + 1
        if j < len(out):
            out[j] = str((job_dir / out[j]).resolve())
    return out


def _game_label(argv: list[str]) -> str:
    """一局的身份（`s3/d7` 式）——诊断行必须能直接说清「是哪一局卡了」。

    只有 argv 里的 `--stages`/`--seeds`（协议层已校验存在且是十进制整数）。缺了就退回问号，
    绝不抛：诊断信息的生成不能成为新的失败点。
    """

    def val(flag: str) -> str:
        try:
            return argv[argv.index(flag) + 1]
        except (ValueError, IndexError):
            return "?"

    return game_watch.game_label(val("--stages"), val("--seeds"))


def _game_out_dir(argv: list[str]) -> str:
    """argv 里的 `--out`（协议层已校验为 job 目录内相对路径）。"""
    try:
        return argv[argv.index("--out") + 1]
    except (ValueError, IndexError):
        return ""


def _clean_attempt(job_dir: Path, argv: list[str]) -> None:
    """删掉一次失败尝试可能留下的半截产出（**就地重跑前必须做**）。

    为什么：卡死/被杀的 bun 可能已经写完 `manifest.json` 而 `obs.npy` 只写了一半——
    `scan_shard_dirs` 只认「名字合法 + 有 manifest.json」，半截目录会被当成产出，
    于是重跑成功与否都不影响它留在实产集里（读数静默错一局）。

    只删 job 目录**里面**的东西（out 目录 + 同 (stage,seed) 的 shard 目录），任何越界路径一
    律跳过——这个函数的输入全部来自协议层校验过的 argv，但删除是没得撤销的动作，值一道闸。
    """
    root = job_dir.resolve()
    targets: list[Path] = []
    out = _game_out_dir(argv)
    if out:
        targets.append(job_dir / out)
    stage = seed = None
    try:
        stage = int(argv[argv.index("--stages") + 1])
        seed = int(argv[argv.index("--seeds") + 1])
    except (ValueError, IndexError):
        pass
    if stage is not None and seed is not None:
        name = shard_name(stage, seed)
        targets += list(job_dir.rglob(name))
    for t in targets:
        try:
            if not t.exists():
                continue
            if root not in t.resolve().parents:
                continue
            if t.is_dir():
                shutil.rmtree(t, ignore_errors=True)
            else:
                t.unlink(missing_ok=True)
        except OSError:
            pass


def _run_one_game(
    bun: str,
    argv: list[str],
    job_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
    log=lambda msg: None,
    attempt: int = 1,
) -> float:
    """跑一局：`bun <argv...>`（cwd = TS 代码根），日志落 `job_dir/out_dir/rollout.log`。

    返回墙钟秒。失败语义：
      * 超过 `timeout_sec`（**本次尝试的硬顶**，由 `_run_one_game_with_retries` 按尝试次数算：
        首次 = plan 给的上限或 `DEFAULT_GAME_TIMEOUT_SEC`，重试放宽 `RETRY_TIMEOUT_FACTOR` 倍）
        → kill 子进程 + RetryableError（调用方 `_run_one_game_with_retries` 就地重跑）；
      * rc != 0 → RetryableError（同上；本机路径是把 stderr 尾巴抛出去让 loop 重试）；
      * 日志写不进去（磁盘）→ OSError 原样上抛（worker 侧统一按失败处理）。

    等待用**轮询**而不是一次 `p.wait(timeout=...)`：轮询让「单局异常慢」在卡住期间就能被
    点名（软告警），而不是等硬顶到了才知道某一局有问题（2026-09-22 it34 的 651s 就是这么
    发生的：10 局卡死，日志里只有计数）。
    """
    wdir = job_dir / out_dir
    wdir.mkdir(parents=True, exist_ok=True)
    log_path = wdir / ROLLOUT_LOG_NAME
    label = _game_label(argv)
    t0 = time.time()
    warned = False
    with open(log_path, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(
            [bun, *_exec_argv(argv, job_dir)],
            cwd=str(ts_dir),
            stdout=lf,
            stderr=subprocess.STDOUT,
            **_POPEN_NO_WINDOW,
        )
        while True:
            try:
                rc = p.wait(timeout=game_watch.GAME_POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.time() - t0
                # 默认口径下软告警与硬顶同值 ⇒ 只打超时行（它自己带着局身份）；调用方把上限
                # 调高时这一层才有独立价值（跑完但慢的局也要被点名）。
                if (
                    not warned
                    and not game_watch.warn_is_redundant(timeout_sec)
                    and elapsed >= game_watch.SLOW_GAME_WARN_SEC
                ):
                    warned = True
                    log(
                        game_watch.slow_warn_line(
                            "rollout", label, elapsed, timeout_sec, attempt, str(log_path)
                        )
                    )
                if elapsed >= timeout_sec:
                    p.kill()
                    p.wait()
                    raise RetryableError(
                        game_watch.hard_cap_line(
                            "rollout", label, elapsed, timeout_sec, str(log_path)
                        )
                        + f"（{' '.join(argv[:4])}…）"
                    ) from None
    if rc != 0:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        raise RetryableError(f"rollout 单局 rc={rc}（{label}；{' '.join(argv[:4])}…）：\n{tail}")
    return round(time.time() - t0, 3)


def _run_one_game_with_retries(
    bun: str,
    argv: list[str],
    job_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
    log=lambda msg: None,
    explicit: bool = False,
    pool: serve_pool.ServePool | None = None,
) -> tuple[float, int]:
    """一局最多跑 `GAME_MAX_ATTEMPTS` 次（超时/rc≠0 都原地重跑），返回 `(墙钟秒, 尝试次数)`。

    `pool` 非空时**先试长驻 worker**（`§20/§21` 的 1.59×）：池里跑成 = 直接返回，否则立刻回退
    下面的一次性 `Popen` —— 回退路径与本参数不存在时逐字节相同（池只可能让它更快）。

    为什么重试是**必须**的：单局的失败几乎总是环境性的（宿主机一阵饥饿、bun 起不来、
    半截写盘），而这一局是**确定性**的（种子在 argv 里）——重跑同一 argv 要么拿到同一份
    结果，要么再次响亮失败。没有重试的旧行为是「一局卡住 → 撞硬顶 → 整轮（328 局）作废重发」，
    代价比多跑几局大得多。

    每次尝试的上限按 `attempt_timeout_sec` 算：首次 = `timeout_sec`（用户口径 5s），重试放宽
    ×`RETRY_TIMEOUT_FACTOR`（除非 plan 显式给了上限——那是配置说了算，不做解释）。

    全部尝试都失败 → RetryableError（整轮交给 worker 的既有重试语义）。
    """
    label = _game_label(argv)
    last: Exception | None = None
    for attempt in range(1, game_watch.GAME_MAX_ATTEMPTS + 1):
        cap = game_watch.attempt_timeout_sec(timeout_sec, attempt, explicit=explicit)
        if attempt > 1:
            _clean_attempt(job_dir, argv)  # 上一次可能留了半截 shard（见 _clean_attempt）
            log(game_watch.retry_line("rollout", label, attempt, last, cap))
        if pool is not None:
            # 送进池的 argv 必须与一次性路径**逐条相同**（含 job 相对路径的绝化）——worker 的
            # cwd 是 TS 代码根，而 `--out`/`--weights` 是相对 job 目录的（见 `_exec_argv`）。
            # 漏了这一步会让 worker 拿着错的权重路径直接报错（只慢不错地回落，但池就白建了）。
            served = pool.try_pool(
                _exec_argv(argv, job_dir),
                job_dir / out_dir / ROLLOUT_LOG_NAME,
                cap,
                label=label,
                attempt=attempt,
            )
            if served is not None:
                return served, attempt
        try:
            return (
                _run_one_game(
                    bun, argv, job_dir, ts_dir, out_dir, cap, log=log, attempt=attempt
                ),
                attempt,
            )
        except RetryableError as e:
            last = e
    raise RetryableError(
        f"rollout 单局连续 {game_watch.GAME_MAX_ATTEMPTS} 次失败：{label}"
        f"（最后一次：{last}）——这一局产不出 shard，整轮交回重发"
    )


def scan_shard_dirs(job_dir: Path) -> list[Path]:
    """扫出 job 目录下的 shard 目录（`rl_s{stage}_seed{seed}` 且带 manifest.json）。

    **递归**扫（与 `hub_client.iter_shard_dirs` 同一口径）：本模块产的 shard 落在
    `w{i}/rl_s{stage}_seed{seed}`（一进程一局一目录，与本机 rollout 同形），而
    payload 解包出来的 shard 是**平铺**在 job 目录下——两种布局都要认。
    只认名字合法 + 带 manifest.json 的目录（半个目录不算产出）。
    """
    out: list[Path] = []
    if not job_dir.exists():
        return out
    for p in sorted(job_dir.rglob("rl_s*_seed*")):
        if not p.is_dir() or parse_shard_name(p.name) is None:
            continue
        if (p / "manifest.json").exists():
            out.append(p)
    return out


def _first_rollout_log_tail(job_dir: Path, max_lines: int = 10) -> str:
    """取第一个局的 rollout.log 尾（诊断「跑完却没 shard」时直接把现场摆出来）。

    失败静默返回空串——诊断信息永远是 best-effort。
    """
    for w in sorted(job_dir.glob("w*")):
        if not w.is_dir():
            continue
        log_p = w / ROLLOUT_LOG_NAME
        if not log_p.exists():
            continue
        try:
            lines = log_p.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-max_lines:]) or "(空日志)"
        except OSError:
            return ""
    return ""


def verify_shards(job_dir: Path, expected_fp: str) -> list[Path]:
    """实产 shard 集 vs 声明集：`data_fp` 两侧同函数，相等 ⇔ 集合逐条相同。

    不等 → ProtocolError（确定性：漏局/多局/`wver` 不符都不会因为重试而变好）。
    这一条替代了本机路径的「hub 侧对本地 shard 重算 data_fp」——上云轮没有本地副本，
    所以把复算搬到产出地，等价保证「节点训练用的语料 == hub 声明的那一批」。
    """
    dirs = scan_shard_dirs(job_dir)
    if not dirs:
        # ★ 2026-09-22 教训：空局（0 samples、rc=0）也会走到这里——把首个局日志尾
        # 摆进报错，一屏内就能看出是「导出器空局（如 stage 解析失败）」还是「写盘失败」。
        tail = _first_rollout_log_tail(job_dir)
        raise ProtocolError(
            f"kind=iter 跑完但 job 目录没有任何 shard（{job_dir}）——"
            "检查 rollout argv 的 --out 与导出器是否真的写盘"
            + (f"\n首个局 rollout.log 尾：\n{tail}" if tail else "")
        )
    actual = data_fp(dirs)
    if actual != expected_fp:
        names = sorted(d.name for d in dirs)
        raise ProtocolError(
            f"kind=iter shard 集与声明不符：实产={actual[:12]}… 声明={expected_fp[:12]}… "
            f"（实产 {len(dirs)} 局：{names[:5]}{'…' if len(names) > 5 else ''}）——拒收"
        )
    return dirs


def collect_reports(job_dir: Path, spec: dict) -> list[dict[str, Any]]:
    """按 argv 顺序读每局 `_rl_report.json`（缺一即 ProtocolError）。"""
    reports: list[dict[str, Any]] = []
    for i, argv in enumerate(spec["argv"]):
        # --out 已验证存在且是相对路径（protocol.validate_rollout_spec）
        j = argv.index("--out") + 1
        rp = job_dir / argv[j] / REPORT_NAME
        try:
            reports.append(json.loads(rp.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise ProtocolError(f"kind=iter 第 {i} 局缺/坏报告 {rp}: {e}") from e
    return reports


def collect_shard_manifests(shard_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    """读每个 shard 目录里的**单局** `manifest.json`（缺/坏则跳过，返回里只留读到的）。

    为什么逐局画像不能用 `collect_reports`（2026-09-23 定位）：那是每局的
    `_rl_report.json` = **批次摘要**（`games`/`winRate`/`totalTicks`/`scoreStats`…），
    它的 `stage`/`seed` 是**复数数组**（`stages`/`seeds`）、且没有 `kills` 这些单局字段
    ⇒ `compact_per_game` 的「无 (stage,seed) 就丢」会把**每一行**都丢掉 ⇒ `perGame` 恒为
    `[]` ⇒ 落地方不写 `it<N>/per-game.json` ⇒ 控制台的「耗时/击杀/残血/道具」四列在
    云机腿上永远空。**不是没回传，是从没进过回传体**。

    单局 manifest 是唯一带齐那套字段、且与本机腿（读方按 manifest 扫描）**同名同形**的来源
    ——读方因此不需要任何翻译层。`scan_shard_dirs` 已保证每个 shard 目录都有它；真缺了
    也只少一局读数，不该让整轮回传失败（计数会打进轮末日志，不静默）。
    """
    out: list[dict[str, Any]] = []
    for d in shard_dirs:
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict):
            out.append(m)
    return out


def _make_pool(
    bun: str, ts_dir: Path, argvs: list[list[str]], workers: int, log
) -> serve_pool.ServePool | None:
    """按 spec 决定要不要建池；建不了就返回 None（整轮退回逐局 spawn，行为同上云前）。

    本函数只管 iter 特有的一条前置：整轮的 argv 指向**同一个**脚本（池按脚本建，混脚本就得混池
    —— iter 不会混）；剩下的（总开关 + `--serve` 白名单）与 eval 腿共用
    `serve_pool.make_pool` 同一个准入。
    """
    if not argvs:
        return None
    scripts = {str(a[0]) for a in argvs if a}
    if len(scripts) != 1:
        return None
    return serve_pool.make_pool(bun, argvs[0][0], ts_dir, workers, log)


def run_iter_rollout(
    job_dir: str | Path,
    spec: dict,
    *,
    ts_dir: str | Path | None = None,
    log=lambda msg: print(f"[iter] {msg}", flush=True),
) -> dict[str, Any]:
    """执行整轮 rollout，返回 `{report, shard_dirs, rollout_sec, bun_version, game_secs}`。

    spec 必须是已经过 `protocol.validate_rollout_spec` 的归一化字典（argv 白名单 +
    相对路径 + 逐局 stage/seed 都在那里把关）。

    `ts_dir` = 解包后的 TS 代码根（带 `src/` `tools/`）；缺省 = job 目录（单测里桩脚本
    是绝对路径，不需要独立的 TS 根）。真实节点侧必须传，否则 `tools/sim/...` 找不到。
    """
    from rl.reports import combine_reports, compact_per_game

    jd = Path(job_dir)
    tsd = Path(ts_dir) if ts_dir is not None else jd
    if not tsd.is_dir():
        raise ProtocolError(f"TS 代码根不存在: {tsd}（ts_code.zip 解包失败？）")
    t0 = time.time()
    bun = resolve_bun(str(spec.get("bun") or ""))
    ver = bun_version(bun)
    argvs: list[list[str]] = list(spec["argv"])
    requested_workers = max(1, int(spec.get("workers") or 1))
    cap = workers_cap() or MAX_WORKERS  # 0 = 显式不夹
    workers = max(1, min(requested_workers, len(argvs), MAX_WORKERS, cap))
    # 首次尝试的硬顶：plan 给了正数就完全按它；**0/缺省就是节点兜底** `DEFAULT_GAME_TIMEOUT_SEC`
    # （旧口径「0 = 不限」= 卡住的局可以永远等下去；本机历史行为不能当云机的安全策略）。
    requested = float(spec.get("game_timeout_sec") or 0.0)
    explicit = requested > 0
    timeout_sec = requested if explicit else game_watch.DEFAULT_GAME_TIMEOUT_SEC
    # ★ 2026-09-24（日志节食）：本轮的「设置 + 看门狗口径 + 池 + 进度 + 收尾 + 单局耗时」
    # 攒成**一行**（完成时打；未完成时每 60s 心跳一次）。原来这七行随轮刷，云端整段跑几
    # 小时会把控制台日志面板拖死（用户报障）。例外：**重试**与**慢局点名**仍是「发生了才
    # 打」的独立行 —— 它们是事故信号，不该被埋进汇总里。
    rb = LogBundle(log)
    rb.add("games", len(argvs))
    rb.add("workers", workers)
    if workers < min(requested_workers, len(argvs)):
        # 夹取必须可见：否则「hub 说 220、实际跑 12」会变成一个静默的口径分叉。
        # 只在**本机上限**真的掐住了才报（「游戏数比并发数少」是常事，不是夹取）。
        # 「本机」= 跑这一轮的节点/云机（`effective_cores()`），带上核数以免被误读成 hub/导出机。
        # 核数与槽位要分开报：核数是**事实**（容器配额），槽位才是夹取用的口径
        # （`cpu_worker_slots` = max(cores−4, 0.8·cores)），两个数混在一个标签里就是下一次误读。
        rb.add(
            "并发夹取",
            f"{requested_workers}→{workers}（跑这一轮的机器可用核 {effective_cores()}，"
            f"并行槽上限 {cap}｜{ENV_WORKERS_CAP}=0 可关）",
        )
    rb.add("bun", f"{bun} ({ver or '?'})")
    rb.add("ts_root", tsd)
    rb.note(
        f"单局看门狗：软告警 >{game_watch.SLOW_GAME_WARN_SEC:g}s（正常一局亚秒级），"
        f"首次尝试硬顶 {timeout_sec:g}s"
        + (
            "（plan 指定，每次尝试都用它）"
            if explicit
            else f"（plan 未指定，用节点兜底 {game_watch.DEFAULT_GAME_TIMEOUT_SEC:g}s）"
        )
        + f"；超时/rc≠0 原地重跑同一 argv，最多 {game_watch.GAME_MAX_ATTEMPTS} 次"
        + (
            "（上限不放大）"
            if explicit
            else f"（重试上限 ×{game_watch.RETRY_TIMEOUT_FACTOR:g} = "
            f"{game_watch.attempt_timeout_sec(timeout_sec, 2):g}s）"
        )
    )
    # 长驻 worker 池：逐局 spawn 的启动成本（bun + wasm 编译 + attestation×3 + 权重解析）
    # 每局重付一次，实测 1.59×（§20）。池不可用/跑挂都回退一次性路径 —— 只慢不错。
    pool = _make_pool(bun, tsd, argvs, workers, log)
    if pool is not None:
        ready_n = pool.start()
        if ready_n:
            rb.note(
                f"长驻 worker 池：{ready_n}/{workers} 就绪（{argvs[0][0]}）——"
                "逐局进程启动/权重解析只付一次，单局失败自动回退一次性 spawn"
            )
        else:
            rb.note("长驻 worker 池起不来 ⇒ 本轮全部走一次性 spawn")
            pool.close()
            pool = None
    game_secs: list[float] = [0.0] * len(argvs)
    game_attempts: list[int] = [1] * len(argvs)
    ok = False
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(
                    _run_one_game_with_retries,
                    bun,
                    argv,
                    jd,
                    tsd,
                    argv[argv.index("--out") + 1],
                    timeout_sec,
                    log,
                    explicit,
                    pool,
                ): i
                for i, argv in enumerate(argvs)
            }
            last_log_at = t0
            for done_n, fut in enumerate(as_completed(futs), 1):
                i = futs[fut]
                game_secs[i], game_attempts[i] = fut.result()  # 异常在 worker 侧统一处理
                # 进度行**按时间**节流（`game_watch.progress_due`，缺省每分钟一句）：原来的
                # 「每 10 局一句」在高并发轮上是每秒数行 —— 云端离线课的日志就是被它刷屏的
                # （用户口径 2026-09-23）。最后一句恒打（轮结束的唯一落点）。
                now = time.time()
                rb.add("进度", f"{done_n}/{len(argvs)} games settled ({now - t0:.0f}s)")
                if game_watch.progress_due(done_n, len(argvs), now, last_log_at):
                    last_log_at = now
                    rb.beat("kind=iter rollout", now=now)
        ok = True
    finally:
        if pool is not None:
            # 池的收益与代价（served/spawned/killed/fallback）进同一行的收尾字段。
            rb.note(pool.summary(), final_only=True)
            pool.close()
        if not ok:
            # 中断也要交代现场（看门狗口径/池计数/已结算到哪一局）——否则「为什么被杀了」
            # 无从归因。重试/慢局那几行已经在抛出前各自打过了。
            rb.emit("kind=iter rollout 中断")
    shard_dirs = verify_shards(jd, iter_expected_data_fp(spec))
    reports = collect_reports(jd, spec)
    report = combine_reports(reports)
    per_game = collect_shard_manifests(shard_dirs)
    # 逐局压缩画像随轮账本行走：云机离线腿没人把单局 manifest 拉回本机（轮末 prune 就删了），
    # 而控制台的「耗时/击杀/残血/道具」列是**逐局**聚合的 ⇒ 不带它那几列永远空（见
    # `rl/reports.compact_per_game` 的 docstring）。在线腿已有 `dist/<节点>/rl_s*/` 路也不冲突
    # （同一份数据，读方优先用账本里的这一块）。
    report["perGame"] = compact_per_game(per_game)
    report["shards"] = len(shard_dirs)
    report["elapsedSec"] = round(time.time() - t0, 3)
    report["perGameSecs"] = game_secs
    # 与本机 rollout 的 manifest 同规（`rl/cmd` 的 --node-label 决定；上云 = "node"）。
    report["rolloutSrc"] = "node"
    rb.add("shards", len(shard_dirs))
    rb.add("games", report["games"])
    rb.add("winRate", report["winRate"])
    rb.add("samples", report["totalSamples"])
    rb.add("逐局画像", f"{len(report['perGame'])}/{len(shard_dirs)} 行")
    if not report["perGame"]:
        # 四列数据源没了是**要看的**（控制台耗时/击杀/残血/道具会恒空）。
        rb.note("逐局画像 0 行 = 控制台耗时/击杀/残血/道具恒空，查单局 manifest")
    # 单局耗时分布：<5s 这条线（以及重试次数）要靠每轮的真数据校准，不靠猜。
    # `final_only`：轮末才有，心跳里不该出现半个分布。
    rb.note(
        game_watch.game_time_summary(
            "rollout",
            list(zip(game_secs, [_game_label(a) for a in argvs], strict=True)),
            retried=sum(1 for a in game_attempts if a > 1),
        ),
        final_only=True,
    )
    rb.emit(f"kind=iter rollout done in {report['elapsedSec']}s")
    return {
        "report": report,
        "shard_dirs": [str(d) for d in shard_dirs],
        "rollout_sec": report["elapsedSec"],
        "bun_version": ver,
        "game_secs": game_secs,
        "bun": bun,
        "workers": workers,
        # 池的诊断计数（不在 report 里：它要过线，节点本地的观测不该改 wire 形状）
        "serve_pool": None
        if pool is None
        else {
            "served": pool.served,
            "spawned": pool.spawned,
            "killed": pool.killed,
            "fallback": pool.fallback,
            "reasons": dict(pool.fallback_reasons),
        },
    }
