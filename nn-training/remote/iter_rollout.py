"""iter_rollout —— 节点侧「本轮 rollout」执行器（M3，plan/remote-wire-remediation §5.2）。

job kind = `iter` 的语义：**一整轮**上云。节点拿到的 payload 里没有 shard（只有 init
权重 + 可选 blob），shard 由本模块现场产出：

  argv（hub 用 `rl/cmd.build_rollout_cmd` 拼的、逐局一条，路径一律 job 目录内相对路径）
    → 线程池 spawn `bun tools/sim/export-rl-rollout.ts …`（cwd = job 目录）
    → 每局一个 `w{i}/` 目录 + `_rl_report.json`
    → **逐位**校验实产 shard 集 == 声明集（data_fp 两侧同函数）
    → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）

为什么 argv 从 hub 发而不是在节点重算：hub 的 `build_rollout_cmd` 是三导出器 + 课程
覆盖 + D14 血缘的唯一拼装点；节点重算就等于在协议里复制一份它的知识。用同一个函数的
输出，计划 §5.5① 的「节点 shard 与本机 rollout 逐字节一致」是**构造性质**——命令都
一样，剩下的只有导出器本身的确定性。

本模块**不碰 torch / 不碰 PPO**：rollout 完就把 shard 目录交回 `worker.run_job` 的既有
PPO 链路（load_episodes → chunk_episodes → ppo_update）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from remote.protocol import (
    ProtocolError,
    RetryableError,
    data_fp,
    iter_expected_data_fp,
    parse_shard_name,
)

#: 每局日志（诊断用；与本地 `run_rollout` 的 `w{i}/rollout.log` 同名同形）。
ROLLOUT_LOG_NAME = "rollout.log"
#: `export-rl-rollout.ts` 每局的聚合摘要（`combine_reports` 的输入）。
REPORT_NAME = "_rl_report.json"
#: 并行度上限（防 hub 侧误配 workers=1000 把节点打爆；16 vCPU 节点的合理值远低于此）。
MAX_WORKERS = 256


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


def _run_one_game(
    bun: str,
    argv: list[str],
    job_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
) -> float:
    """跑一局：`bun <argv...>`（cwd = TS 代码根），日志落 `job_dir/out_dir/rollout.log`。

    返回墙钟秒。失败语义：
      * 单局超时 → RetryableError（节点卡住 = 基础设施问题，作废重发该轮）；
      * rc != 0 → RetryableError（同上；本机路径是把 stderr 尾巴抛出去让 loop 重试）；
      * 日志写不进去（磁盘）→ OSError 原样上抛（worker 侧统一按失败处理）。
    """
    wdir = job_dir / out_dir
    wdir.mkdir(parents=True, exist_ok=True)
    log_path = wdir / ROLLOUT_LOG_NAME
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as lf:
        p = subprocess.Popen(
            [bun, *_exec_argv(argv, job_dir)],
            cwd=str(ts_dir),
            stdout=lf,
            stderr=subprocess.STDOUT,
            **_POPEN_NO_WINDOW,
        )
        try:
            rc = p.wait(timeout=(timeout_sec or None))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise RetryableError(
                f"rollout 单局超时（>{timeout_sec:g}s）：{' '.join(argv[:4])}…（见 {log_path}）"
            ) from None
    if rc != 0:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        raise RetryableError(f"rollout 单局 rc={rc}（{' '.join(argv[:4])}…）：\n{tail}")
    return round(time.time() - t0, 3)


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


def verify_shards(job_dir: Path, expected_fp: str) -> list[Path]:
    """实产 shard 集 vs 声明集：`data_fp` 两侧同函数，相等 ⇔ 集合逐条相同。

    不等 → ProtocolError（确定性：漏局/多局/`wver` 不符都不会因为重试而变好）。
    这一条替代了本机路径的「hub 侧对本地 shard 重算 data_fp」——上云轮没有本地副本，
    所以把复算搬到产出地，等价保证「节点训练用的语料 == hub 声明的那一批」。
    """
    dirs = scan_shard_dirs(job_dir)
    if not dirs:
        raise ProtocolError(
            f"kind=iter 跑完但 job 目录没有任何 shard（{job_dir}）——"
            "检查 rollout argv 的 --out 与导出器是否真的写盘"
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
    from rl.reports import combine_reports

    jd = Path(job_dir)
    tsd = Path(ts_dir) if ts_dir is not None else jd
    if not tsd.is_dir():
        raise ProtocolError(f"TS 代码根不存在: {tsd}（ts_code.zip 解包失败？）")
    t0 = time.time()
    bun = resolve_bun(str(spec.get("bun") or ""))
    ver = bun_version(bun)
    argvs: list[list[str]] = list(spec["argv"])
    workers = max(1, min(int(spec.get("workers") or 1), len(argvs), MAX_WORKERS))
    timeout_sec = float(spec.get("game_timeout_sec") or 0.0)
    log(
        f"kind=iter rollout: {len(argvs)} games, workers={workers}, "
        f"bun={bun} ({ver or '?'}), timeout={timeout_sec or 'off'}, ts_root={tsd}"
    )
    game_secs: list[float] = [0.0] * len(argvs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                _run_one_game,
                bun,
                argv,
                jd,
                tsd,
                argv[argv.index("--out") + 1],
                timeout_sec,
            ): i
            for i, argv in enumerate(argvs)
        }
        for done_n, fut in enumerate(as_completed(futs), 1):
            i = futs[fut]
            game_secs[i] = fut.result()  # 异常在 worker 侧统一处理
            if done_n % 10 == 0 or done_n == len(argvs):
                log(
                    f"kind=iter rollout: {done_n}/{len(argvs)} games settled "
                    f"({time.time() - t0:.0f}s)"
                )
    shard_dirs = verify_shards(jd, iter_expected_data_fp(spec))
    reports = collect_reports(jd, spec)
    report = combine_reports(reports)
    report["shards"] = len(shard_dirs)
    report["elapsedSec"] = round(time.time() - t0, 3)
    report["perGameSecs"] = game_secs
    # 与本机 rollout 的 manifest 同规（`rl/cmd` 的 --node-label 决定；上云 = "node"）。
    report["rolloutSrc"] = "node"
    log(
        f"kind=iter rollout done: shards={len(shard_dirs)} games={report['games']} "
        f"winRate={report['winRate']} samples={report['totalSamples']} "
        f"in {report['elapsedSec']}s"
    )
    return {
        "report": report,
        "shard_dirs": [str(d) for d in shard_dirs],
        "rollout_sec": report["elapsedSec"],
        "bun_version": ver,
        "game_secs": game_secs,
        "bun": bun,
        "workers": workers,
    }
