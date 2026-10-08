"""iter_rollout —— 节点侧「本轮 rollout」执行器（M3，plan/remote-wire-remediation §5.2）。

job kind = `iter` 的语义：**一整轮**上云。节点拿到的 payload 里没有 shard（只有 init
权重 + 可选 blob），shard 由本模块现场产出：

  argv（hub 用 `biz/cmd.build_rollout_cmd` 拼的、逐局一条，路径一律 job 目录内相对路径）
    → 线程池跑 `bun tools/sim/export-rl-rollout.ts …`（cwd = TS 代码根）
    → 每局一个 `w{i}/` 目录 + `_rl_report.json`
    → **逐位**校验实产 shard 集 == 声明集（data_fp 两侧同函数）
    → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）

为什么 argv 从 hub 发而不是在节点重算：hub 的 `build_rollout_cmd` 是三导出器 + 课程
覆盖 + D14 血缘的唯一拼装点；节点重算就等于在协议里复制一份它的知识。用同一个函数的
输出，计划 §5.5① 的「节点 shard 与本机 rollout 逐字节一致」是**构造性质**——命令都
一样，剩下的只有导出器本身的确定性。

执行方式：默认走 **长驻 worker 池**（`worker/serve_pool.py`，`--serve` 协议，与
`sampler-agent` 的 `/v1/task` 路径同一份契约）—— 逐局 spawn 时每局都要重付 bun 启动 +
wasm 编译 + 首用 attestation×3 + 权重解析，本模块实测 **1.45–1.47×**
（`docs/nn/runtime-opt.md` §22；agent 侧同一机制为 §20 的 1.59×）。
**池只发真就绪的 worker**（上报过 `__SERVE_READY__` 且没死；2026-10-07 之前只判「进程没退出」
⇒ 假就绪被当暖 worker 发出去，全部超时回退 + 二次冷启动）；冷启动**分批**（`SPAWN_BATCH_SIZE`），
没就绪的**不杀**（就绪即自动入池）。
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
import threading
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import partial
from pathlib import Path
from typing import Any, cast

# 单局看门狗的口径常量与 eval **共用一份**（`common/game_watch.py`）：点名线 5s、首次尝试硬顶
# 也是 5s（用户口径「单局 >5s 肯定不正常」⇒ 超时原地重跑）、重试上限 ×4、最多 3 次、轮询 0.5s。
# **一律通过模块属性读**（`game_watch.X`）而不是 `from ... import X`：import 会把值抄成第二份
# 绑定，测试 patch 了 `game_watch` 的那一份、调用点却还在读旧绑定（两处不一致就是静默的错口径）。
from common import game_watch, progress_hook, scratch
from common.log_bundle import LogBundle
from common.platform_utils import (
    KILL_REAP_SEC,
    call_bounded,
    cores_note,
    cpu_worker_slots,
    keep_unreaped,
    kill_process_tree,
    popen_own_group,
    reap_bounded,
    sweep_unreaped,
)
from common.proc import bun_version as _bun_version
from common.protocol import (
    ProtocolError,
    RetryableError,
    # ★ 「机器级停滞」（子进程 SIGKILL 之后收不了尸）的异常**定义在 `common/protocol.py`**
    # ——与 `RetryableError` 同册：它是**两腿共用**的分类语义（eval 腿也抛这一类，见
    # `remote/offline_eval`），各腿的重投粒度各自定。这里 import 进来当模块属性，好让
    # `remote/worker.py` 与用例继续按本模块取它（改归属不动调用点）。
    UnreapableChildError,
    data_fp,
    iter_expected_data_fp,
    parse_shard_name,
    shard_name,
)
from worker import serve_pool

#: 每局日志（诊断用；与本地 `run_rollout` 的 `w{i}/rollout.log` 同名同形）。
ROLLOUT_LOG_NAME = "rollout.log"
#: `export-rl-rollout.ts` 每局的聚合摘要（`combine_reports` 的输入）。
REPORT_NAME = "_rl_report.json"
#: 并行度上限（防 hub 侧误配 workers=1000 把节点打爆；16 vCPU 节点的合理值远低于此）。
MAX_WORKERS = 256

# ★ 被杀子进程的**回收**上限 = `common.platform_utils.KILL_REAP_SEC`（全仓唯一那个数，本模块直接用它）。
# 为什么它必须存在（2026-09-25 云机二次取证「rollout 卡死机器半天」）：旧路径是
# `p.kill(); p.wait()` —— **没有上限**。子进程卡在不可中断的 IO 里（云机上被挂住的挂载点/
# 慢盘就是这样，D 状态）时 SIGKILL 要等那个系统调用返回才生效，`wait()` 就跟着无限等；而超时
# 行里的 elapsed 是 kill **之前**算的，日志照旧写着「5.0s」⇒ 现场看起来是「一局超时之后整轮
# 静默挂住 890s」（92 条线程里有一条卡住，`as_completed` 就永远收不齐，排在后面的局连开始都
# 开始不了）。有上限之后：最坏只损失这一局的尝试，绝不让一条线程把整轮当人质。


# ★ `UnreapableChildError` 的**定义**在 `common/protocol.py`（见那里的 docstring：分类语义是
# 两腿共用的）；本模块只 import 它（上面那一段）。rollout 腿的**重投纪律**在
# `run_iter_rollout` 的轮循环里：不就地重跑那一局（重跑会在同一个 `w{i}/` 上再起一个写者，
# 而旧的那个可能还活着 ⇒ 两个进程写同一份 shard = 静默错数据，`_clean_attempt` 存在的全部
# 理由），而是清掉半截产出后与本轮其它没产出的局一起重投；不睡、不报失败、不消耗调用方的
# 重试预算（云机自主段 `run_loop._run_with_retries` 只有 3 次），缺省不限（`ENV_ROUND_RETRY_MAX`
# 是操作员的退出阀）。

#: 本机并行度夹取的覆盖开关（实验/排障用）：正整数 = 直接当上限，`0` = **不夹**
#: （完全按 hub 给的 workers 走）。缺省 = 按本机核数夹取（`cpu_worker_slots()`）。
ENV_WORKERS_CAP = "NN_ROLLOUT_WORKERS_MAX"

#: 「机器级停滞」的轮内重投上限（实验/排障用）：`0`/未设 = **不限**；正整数 = 重投这么久就放弃
#: （上抛 `RetryableError`，交回调用方自己的重试语义）。
#:
#: 为什么缺省是不限：这一类失败是**机器**的病（D 状态 / 挂住的挂载点），现场 890s 之后自己好了；
#: 而外层每条腿的预算都很小（云机自主段 `ITER_RETRIES=2` ⇒ 3 次就把整段判死）——重投放外层
#: 等于用「一台机器短暂卡住」把整段训练判死。放在轮内则：机器一好就接上，且**不空转**
#: （只补没产出的那几局，已结算的局不重跑）。会一直试是指：每一轮都真的在跑活，不是等。
ENV_ROUND_RETRY_MAX = "NN_ROLLOUT_ROUND_RETRY_MAX"


def workers_cap() -> int:
    """**跑 rollout 那台机器**的并行度上限（0 = 不夹）：缺省 = `cpu_worker_slots()`，env 可覆盖。

    ⚠ 「本机」= **执行本函数的进程所在的机器**（节点/云机自己），**不是** hub、也不是导出机。
    核数走 `common.platform_utils.effective_cores()`（容器配额/亲和掩码 > `os.cpu_count()`）：
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


def round_retry_max() -> int:
    """轮内重投上限（`0` = 不限）；见 `ENV_ROUND_RETRY_MAX`。非法值 → 回落不限（响亮的事交给日志）。

    ⚠ 与 `workers_cap()` 同一个形状：**执行 rollout 那台机器**读自己的 env（不是 hub/导出机）。
    """
    raw = (os.environ.get(ENV_ROUND_RETRY_MAX) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 0


def resolve_bun(name: str = "") -> str:
    """节点侧 bun 可执行路径。找不到 → ProtocolError（响亮，绝不静默换 python）。

    ★ 2026-09-25 重裁：**在线 worker 盘（tailscale / cloudflared 两条隧道）不装 bun、不跑 rollout**
    （`tailscale_boot.ensure()` 已不再装 bun）。会拿到 `kind=iter` 的只有**自己跑 rollout 的盘**：
    `battle.offline.ipynb`（Kaggle/TPU 走 cloudflared 隧道）与采样节点 `rollout.cloudflared.ipynb`。
    所以这条拒单不是在报「盘坏了」，而是在报「这份活派错了盘」—— 消息里直接把这条说清楚。
    """
    want = str(name or "").strip() or "bun"
    found = shutil.which(want)
    if not found:
        raise ProtocolError(
            f"节点上找不到 {want!r}（kind=iter 需要 bun 在节点上跑 rollout）——"
            "本 worker 所属的盘不跑 rollout（在线 worker 盘不装 bun）："
            "请改用自己跑 rollout 的盘（battle.offline.ipynb / rollout.cloudflared.ipynb），"
            "或把这门课的 rollout 放回本机（rollout_src=local）"
        )
    return found


def bun_version(bun: str) -> str:
    """`bun --version`（节点启动自检行用；失败返回空串，不致命）。

    唯一实现见 `common.proc.bun_version`（显式 UTF-8 解码的坑见其模块 docstring，
    正是本处原先注释引的 §30）。本处只钉**节点侧口径**：超时 30s、失败空串、
    非零退出码一律当失败。1 参签名保留——测试与服池用
    `monkeypatch.setattr(iter_rollout, "bun_version", …)` 打桩。
    """
    return _bun_version(bun, timeout=30.0, fallback="", require_zero=True)


#: argv 里必须“按 job 目录解析”的路径 flag（见 `_exec_argv`）。
_JOB_REL_FLAGS: tuple[str, ...] = ("--out", "--weights")


def _exec_argv(argv: list[str], exec_dir: Path) -> list[str]:
    """把 argv 里的 job 相对路径换算成绝对路径（**不改协议**：协议层只允许相对，
    节点侧才知道自己的真实目录）。

    `exec_dir` = **本轮的 exec 根**：回退档就是 job 目录（逐字同今天），换根档是 scratch 的 `r/`
    （plan §2 P0-2 的「两个根的分工」）。路径全在**节点侧**变换 ⇒ wire 形状/`data_fp` 一个字节不改。

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
            out[j] = str((exec_dir / out[j]).resolve())
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


def _kill_and_reap(p: Any, *, label: str, log, where: str) -> bool:
    """SIGKILL 整个进程组 + **有界**回收；返回 False = 收不了尸（调用方按 `UnreapableChildError` 走）。

    `p` 取 `Any`（不写死 `subprocess.Popen`）：契约只有「pid / kill / wait(timeout) / poll」，
    而平台层那几个原语（`common.platform_utils`）本来就是按 Any 写的 —— 写死具体类会让「收不了尸」
    这一态在用例里没法用替身造出来（真态要不可中断的 IO）。

    顺手 `sweep_unreaped()`（零等待）把之前收不了尸的进程再碰一次：它们可能早就退出了，
    而机器上本来就已经被一堆进程压着，僵尸再占着 pid/句柄只有坏处。
    """
    sweep_unreaped()
    kill_process_tree(p)  # 进程组：bun 自己带的子进程（编译缓存 worker/子工具）一起带走
    if reap_bounded(p, KILL_REAP_SEC):
        return True
    keep_unreaped(p)  # 之后非阻塞地再试（见 common.platform_utils.sweep_unreaped）
    log(
        f"WARN rollout 单局子进程杀不掉：{label} —— SIGKILL 之后 {KILL_REAP_SEC:g}s 内回收不了"
        f"（pid={p.pid}，很可能卡在不可中断的 IO 里）——本局不就地重跑（同一目录上可能还有活写者），"
        f"交回整轮：清掉它的半截产出后与本轮其它没产出的局一起重投（不是失败，云机不停）；"
        f"现场 {where}"
    )
    return False


def _clean_partial(job_dir: Path, exec_dir: Path, argv: list[str]) -> Any:
    """清理的 partial：**回退档（两个根相同）与今天逐字相同**（连关键字都不多传一个）。

    为什么这点重要：换根是新增档 ⇒ 回退档的调用形状不能变（既有用例里那些两参替身
    `def clean(jd, argv)` 就是它的守卫）。换根档才多传 `exec_root=`。
    """
    if Path(exec_dir) == Path(job_dir):
        return partial(_clean_attempt, job_dir, argv)
    return partial(_clean_attempt, job_dir, argv, exec_root=Path(exec_dir))


def _clean_attempt(job_dir: Path, argv: list[str], *, exec_root: Path | None = None) -> None:
    """删掉一次失败尝试可能留下的半截产出（**就地重跑前必须做**）。

    `exec_root` 非空 = 换根档：**两个根都扫**（本轮的产出写在 scratch 的 exec 根下，半截也在那儿；
    而上一轮/回退档的残留在 job 目录）—— 见 plan §3 的 R1（漏一边就是「目录齐、obs 截断」）。

    为什么：卡死/被杀的 bun 可能已经写完 `manifest.json` 而 `obs.npy` 只写了一半——
    `scan_shard_dirs` 只认「名字合法 + 有 manifest.json」，半截目录会被当成产出，
    于是重跑成功与否都不影响它留在实产集里（读数静默错一局）。

    只删这些根**里面**的东西（out 目录 + 同 (stage,seed) 的 shard 目录），任何越界路径一
    律跳过——这个函数的输入全部来自协议层校验过的 argv，但删除是没得撤销的动作，值一道闸。
    """
    roots: list[Path] = [Path(job_dir)]
    if exec_root is not None and Path(exec_root) != Path(job_dir):
        roots.append(Path(exec_root))
    resolved = [r.resolve() for r in roots]
    targets: list[Path] = []
    out = _game_out_dir(argv)
    if out:
        targets += [r / out for r in roots]
    stage = seed = None
    try:
        stage = int(argv[argv.index("--stages") + 1])
        seed = int(argv[argv.index("--seeds") + 1])
    except (ValueError, IndexError):
        pass
    if stage is not None and seed is not None:
        name = shard_name(stage, seed)
        # 递归扫：`scan_shard_dirs` 认的是「job 目录下任何位置的合法 shard 目录」，这里必须**同口径**
        # （宽一边就是半截 shard 留在盘上被当成产出）。
        for r in roots:
            targets += list(r.rglob(name))
    for t in targets:
        try:
            if not t.exists():
                continue
            rt = t.resolve()
            if not any(r in rt.parents for r in resolved):
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
    exec_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
    log=lambda msg: None,
    attempt: int = 1,
    abandoned: threading.Event | None = None,
) -> float:
    """跑一局：`bun <argv...>`（cwd = TS 代码根），日志落 `exec_dir/out_dir/rollout.log`。

    `exec_dir` = **本轮的 exec 根**（回退档 = job 目录，换根档 = scratch 的 `r/`）：`--out`/
    `--weights` 都相对它解析、产出也写在它下面——见 plan §2 P0-2 的「两个根的分工」。

    返回墙钟秒。失败语义：
      * 超过 `timeout_sec`（**本次尝试的硬顶**，由 `_run_one_game_with_retries` 按尝试次数算：
        首次 = plan 给的上限或 `DEFAULT_GAME_TIMEOUT_SEC`，重试放宽 `RETRY_TIMEOUT_FACTOR` 倍）
        → SIGKILL 整个进程组 + **有界**回收 + RetryableError（调用方就地重跑）；
        **回收不了**（D 状态 / 挂住的挂载点）⇒ `UnreapableChildError`（本局不重跑、整轮交回重发，
        见那个类的 docstring 与 `KILL_REAP_SEC`）；
      * rc != 0 → RetryableError（同上；本机路径是把 stderr 尾巴抛出去让 loop 重试）；
      * 日志写不进去（磁盘）→ OSError 原样上抛（worker 侧统一按失败处理）。

    ★ 这条路径上**任何一次等待都必须有上限**（看门狗、kill 后的回收）：训练轮里 92 条线程
    同时在跑，其中一条卡在无上限的 syscall 上，整轮就再也收不齐 —— 排在它后面的局连开始都
    开始不了，而日志里只有一行「已结算 N 局」不再动（2026-09-25「卡死机器半天」的真身）。

    等待用**轮询**而不是一次 `p.wait(timeout=...)`：轮询让「单局异常慢」在卡住期间就能被
    点名（软告警），而不是等硬顶到了才知道某一局有问题（2026-09-22 it34 的 651s 就是这么
    发生的：10 局卡死，日志里只有计数）。

    `abandoned`（P1-1，2026-10-07 评审）：本尝试所在的那条链已被整条链路的上界放弃 ⇒
    **不再开写**（`mkdir` 之前）且**`Popen` 一返回就处决刚起的进程**。没有它时，被放弃的
    线程照样会把整局跑完并往 `w{i}/` 写 —— 那正是「同一目录上可能还有活写者」（同一份 shard
    两个写者 = 静默错数据）的唯一可闭缺口；评审点名的 exec 握手窗口就是第二次检查。
    """
    label = _game_label(argv)
    if abandoned is not None and abandoned.is_set():
        raise UnreapableChildError(
            f"rollout 单局已超界（整条链路上界内没返回）：{label}"
            "——本尝试已被放弃，不再开写（避免与下一轮写者同目录竞争）"
        ) from None
    wdir = exec_dir / out_dir
    wdir.mkdir(parents=True, exist_ok=True)
    log_path = wdir / ROLLOUT_LOG_NAME
    t0 = time.time()
    warned = False
    with open(log_path, "w", encoding="utf-8") as lf:
        # `popen_own_group`：自带进程组（POSIX）⇒ 超时可以连 bun 自己带的子进程一起 SIGKILL（
        # 只杀父进程会留下孤儿继续吃 CPU/内存，机器越跑越卡）；代价是 Ctrl+C 不再自动传到它，
        # 而这条路径本来就总是自己 kill（超时/rc≠0/收池都各有出口）。
        p = subprocess.Popen(
            [bun, *_exec_argv(argv, exec_dir)],
            cwd=str(ts_dir),
            stdout=lf,
            stderr=subprocess.STDOUT,
            **popen_own_group(),
        )
        if abandoned is not None and abandoned.is_set():
            # ★ exec 握手窗口（评审 P1-4 点名的那一档）：超界那一刻这个进程已经在跑，只是父线程
            # 刚刚拿回 pid —— 就地处决，不让它继续写 `w{i}/`（本局不产出，交整轮重投）。
            _kill_and_reap(p, label=label, log=log, where=str(log_path))
            raise UnreapableChildError(
                f"rollout 单局超界后自清：{label}（pid={p.pid}）——被放弃的尝试刚起完进程就被"
                "发现，本局不产出（交回整轮重投）"
            ) from None
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
                    # ★ 这里曾经是 `p.kill(); p.wait()`（无上限）：机器一卡，这一局的线程就永远
                    # 停在 waitpid 上，而日志里看不出来（elapsed 上面已经算过，照样是 5.0s）。
                    msg = (
                        game_watch.hard_cap_line(
                            "rollout", label, elapsed, timeout_sec, str(log_path)
                        )
                        + f"（{' '.join(argv[:4])}…）"
                    )
                    if not _kill_and_reap(p, label=label, log=log, where=str(log_path)):
                        raise UnreapableChildError(
                            msg
                            + f"；且 SIGKILL 之后 {KILL_REAP_SEC:g}s 内回收不了（pid={p.pid}）"
                            "——本局不再重跑（同一目录上可能还有活写者）；整轮交回 worker，"
                            "**立即**重领重投同一份活（不睡/不报失败/云机不停）"
                        ) from None
                    raise RetryableError(msg) from None
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
    exec_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
    log=lambda msg: None,
    explicit: bool = False,
    pool: serve_pool.ServePool | None = None,
    abandoned: threading.Event | None = None,
    *,
    job_dir: Path | None = None,
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

    全部尝试都失败 → RetryableError（整轮交给 worker 的既有重试语义）。**例外**：
    `UnreapableChildError`（子进程 SIGKILL 后收不了尸 / 清理超界 / 本尝试已被放弃）**直接上抛**，
    不重跑这一局——重跑会在同一个 `w{i}/` 上再起一个写者（那个可能还活着），两个进程写同一份
    shard = 静默错数据。

    `job_dir` 只在**换根档**才需要（= 真 job 目录）：清理要两个根都扫（R1），而跑局一律在 `exec_dir`。
    缺席 ⇒ 与 `exec_dir` 同一个（回退档：调用形状与今天逐字相同）。
    """
    real_job = Path(exec_dir) if job_dir is None else Path(job_dir)
    label = _game_label(argv)
    last: Exception | None = None
    for attempt in range(1, game_watch.GAME_MAX_ATTEMPTS + 1):
        if abandoned is not None and abandoned.is_set():
            # 链已超界（`_run_one_game_bounded` 置位）：本尝试不再开写/不再重跑。
            raise UnreapableChildError(
                f"rollout 单局被放弃（整条链路上界内没返回）：{label}"
                "——本尝试不再开写/不再重跑（避免与下一轮写者同目录竞争）"
            ) from None
        cap = game_watch.attempt_timeout_sec(timeout_sec, attempt, explicit=explicit)
        if attempt > 1:
            # ★ 顺序即判据（评审 P0-2）：重试行**先于**清理 —— 旧形态把清理排在它前面，
            # 挂住的挂载点上「重试过的局 0 个」就是这么来的（不是没重试，是那行永远打不出来）。
            log(game_watch.retry_line("rollout", label, attempt, last, cap))
            # 上一次可能留了半截 shard（见 _clean_attempt）——清理**有上界**（评审 P0-1）。
            cleaned, _ = call_bounded(
                _clean_partial(real_job, exec_dir, argv),
                game_watch.CLEAN_CEILING_SEC,
                name=f"clean-{label}",
            )
            if not cleaned:
                # ★ 超界 ⇒ 机器级停滞，**不就地重跑**：被放弃的那条 daemon 线程还在跑 rmtree，
                # 它删的正是新一次尝试要写的同一批路径（`--out`/shard 名都没变）⇒「目录齐、obs
                # 截断」= 静默错数据（`_clean_attempt` 存在的全部理由）。交整轮重投：那里先做一次
                # 有界清理，且两条链的重投都在新写者开写前整目录清场（见 plan §2 P0-2）。
                where = str(Path(exec_dir) / out_dir / ROLLOUT_LOG_NAME)
                log(
                    game_watch.clean_ceiling_line(
                        "rollout", label, game_watch.CLEAN_CEILING_SEC, attempt, where
                    )
                )
                raise UnreapableChildError(
                    f"rollout 半截产出清不掉（attempt {attempt}）：{label}"
                    f"（{game_watch.CLEAN_CEILING_SEC:g}s 内 rmtree 没返回——挂载点 IO 还没好）"
                    "——本局不就地重跑（删除者可能晚到）；整轮交回 worker，**立即**重领重投"
                    f"；现场 {where}"
                ) from None
        if pool is not None:
            # 送进池的 argv 必须与一次性路径**逐条相同**（含 job 相对路径的绝化）——worker 的
            # cwd 是 TS 代码根，而 `--out`/`--weights` 是相对 job 目录的（见 `_exec_argv`）。
            # 漏了这一步会让 worker 拿着错的权重路径直接报错（只慢不错地回落，但池就白建了）。
            served = pool.try_pool(
                _exec_argv(argv, exec_dir),
                Path(exec_dir) / out_dir / ROLLOUT_LOG_NAME,
                cap,
                label=label,
                attempt=attempt,
            )
            if served is not None:
                return served, attempt
        try:
            return (
                _run_one_game(
                    bun,
                    argv,
                    exec_dir,
                    ts_dir,
                    out_dir,
                    cap,
                    log=log,
                    attempt=attempt,
                    abandoned=abandoned,
                ),
                attempt,
            )
        except UnreapableChildError:
            # 机器层面的卡住（D 状态）：原地重跑会在同一个 `w{i}/` 上再起一个写者 ⇒ 直接上抛，
            # 让整轮走「交回重发」（重发会把 job 目录整个 rmtree 掉，干净）。
            raise
        except RetryableError as e:
            last = e
    raise RetryableError(
        f"rollout 单局连续 {game_watch.GAME_MAX_ATTEMPTS} 次失败：{label}"
        f"（最后一次：{last}）——这一局产不出 shard，整轮交回重发"
    )


def _run_one_game_bounded(
    bun: str,
    argv: list[str],
    exec_dir: Path,
    ts_dir: Path,
    out_dir: str,
    timeout_sec: float,
    log=lambda msg: None,
    explicit: bool = False,
    pool: serve_pool.ServePool | None = None,
    *,
    ceiling_sec: float | None = None,
    job_dir: Path | None = None,
) -> tuple[float, int]:
    """跑一局，但**本线程永远在墙钟上界内返回**：真活交给一条可放弃的 daemon 线程。

    为什么需要它（2026-10-06 Kaggle 离线轮「整轮停滞十几分钟」取证）：一局的路径上有若干
    **无法从 Python 里取消**的阻塞点——挂住的挂载点上的 `mkdir`/`open`/`write`/`rmtree`，
    以及 `Popen` 等子进程 exec 成功的那一手 `os.read`（子进程卡在 D 状态的 execve，父进程
    就跟着无限等）。它们既不返回也不抛 ⇒ 那一局的 future 永远不结算 ⇒ 轮循环的 `wait()`
    永远收不齐 ⇒ **轮内重投那套护栏一次都触发不了**（它的触发条件是「有 future 抛了
    `UnreapableChildError`」）。现场：每 120s 一条「整轮停滞：… 还有 55 局在飞」，一局都不
    结算，十几分钟不动；单局看门狗（5s 硬顶）此刻根本没被走到——卡的那一步在它**之前**。

    上界超了就是**机器级停滞**（机器卡在不可取消的 IO 上，与收不了尸同一族）：扔
    `UnreapableChildError` 交回整轮重投（只补没产出的局）——本局**不**就地重跑（同一个
    `w{i}/` 上可能还有活写者，`_clean_attempt` 存在的全部理由）。被放弃的那条线程是 daemon：
    不 join、不拖住任何东西，机器一好它自己结束。

    `ceiling_sec=None` ⇒ 按 `game_watch.game_ceiling_sec()` 现场算（尝试次数 × 宽容的硬顶 +
    回收预算 + 余量）；用例可传毫秒级的值（判据与绝对长度无关）。

    `exec_dir` / `job_dir` 同 `_run_one_game_with_retries`（回退档两个同值 ⇒ 形状与今天逐字相同）。
    """
    label = _game_label(argv)
    ceiling = (
        game_watch.game_ceiling_sec(timeout_sec, explicit=explicit)
        if ceiling_sec is None
        else float(ceiling_sec)
    )
    #: 被放弃时告诉那条链「你已经被放弃了」：它在 `Popen` 返回后据此就地处决自己的子进程
    #: （P1-1，评审 P1-4 的 exec 握手窗口）——不置位 = 那条链会把整局跑完并往 `w{i}/` 写。
    abandoned = threading.Event()
    ok, res = call_bounded(
        lambda: _run_one_game_with_retries(
            bun,
            argv,
            exec_dir,
            ts_dir,
            out_dir,
            timeout_sec,
            log,
            explicit,
            pool,
            abandoned,
            job_dir=job_dir,
        ),
        ceiling,
        name=f"rollout-{label}",
    )
    if not ok:
        abandoned.set()
        # 现场 = 日志真身处（换根档在 scratch；回退档就是 job 目录）。
        where = str(Path(exec_dir) / out_dir / ROLLOUT_LOG_NAME)
        log(game_watch.ceiling_line("rollout", label, ceiling, where))
        raise UnreapableChildError(
            f"rollout 单局超界（{ceiling:g}s 内整条链路都没返回）：{label}"
            f"——大概率卡在不可中断的 IO（挂住的挂载点：mkdir/open/写盘/子进程 exec 握手）；"
            "本局不再跑（同一目录上可能还有活写者）；整轮交回 worker，**立即**重领重投"
            f"同一份活（不睡/不报失败/云机不停）；现场 {where}"
        ) from None
    return cast(tuple[float, int], res)


def _scan_bounded(what: str, fn: Any, log, *, where: str) -> Any:
    """轮末扫盘的**有上界的等**（`verify_shards` / `collect_reports` / `collect_shard_manifests`）。

    为什么（2026-10-06 §32.4 ⑤ 的下一个位置）：这三步都在**主线程**上做全量文件 IO（rglob 扫
    shard、逐局读 `manifest.json`/`_rl_report.json`、`data_fp` 还要哈希每个 shard 的内容）——挂住
    的挂载点上它们一样**不返回也不抛**；而这里连「整轮停滞」都没有（那行只在轮循环里打）⇒ 整轮
    对日志**完全静默**（比游戏线程那一档更难查，因为连“还在飞”的读数都没有）。

    超界 ⇒ 机器级停滞（`UnreapableChildError`，与一局超界同一档）：本轮产物结不了算 ⇒ 交回
    worker 还租约 + **立即**重领重投（不睡/不报失败/云机不停）。`fn` 自己抛的异常（如
    `verify_shards` 的 `ProtocolError`）由 `call_bounded` 原样重抛 —— 确定性拒收**不许**被这条
    上界改成「机器卡住」（那是把内容的错记在机器头上）。
    """
    ceiling = float(game_watch.SCAN_CEILING_SEC)
    ok, value = call_bounded(fn, ceiling, name=what)
    if not ok:
        log(game_watch.scan_ceiling_line("rollout", what, ceiling, where))
        raise UnreapableChildError(
            f"rollout 轮末 {what} 超界（{ceiling:g}s 内没返回）：{where}"
            "——大概率卡在不可中断的 IO（挂住的挂载点：rglob/读 manifest/_rl_report、"
            "data_fp 哈希）；本轮产物结不了算，交回 worker 还租约 + **立即**重领重投"
            "（不睡/不报失败/云机不停）"
        ) from None
    return value


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

    本函数只管 iter 特有的一条前置：整轮的 argv 指向**同一个**脚本（准入是**按轮**判的：混脚本
    的轮无法用一句话回答「这一轮该不该建池」——池本身已能混 mode，这条前置保的是准入的
    确定性，不是池的能力）；剩下的（总开关 + `--serve` 白名单）与 eval 腿共用
    `serve_pool.make_pool` 同一个准入。
    """
    if not argvs:
        return None
    scripts = {str(a[0]) for a in argvs if a}
    if len(scripts) != 1:
        return None
    # 池同质（入口固定），`for_script` 只用作准入门槛（见 `make_pool` 的注释）。
    return serve_pool.make_pool(bun, ts_dir, workers, log, for_script=argvs[0][0])


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
    from worker.reports import combine_reports, compact_per_game

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
        # （`cpu_worker_slots`：≤4 核全给，否则 max(cores−2, 0.8·cores)），两个数混在一个标签里
        # 就是下一次误读。
        # ★ 2026-10-03（Colab 误读现场）：光一个「可用核 4」会被读成「这台机器只有 4 核」——
        # 现在整行报**出处**（cpu_count / 亲和 / cgroup 配额三源 + 取小），见 `cores_note()`。
        rb.add(
            "并发夹取",
            f"{requested_workers}→{workers}（跑这一轮的机器核数：{cores_note()}，"
            f"并行槽上限 {cap}｜{ENV_WORKERS_CAP}=0 可关）",
        )
    rb.add("bun", f"{bun} ({ver or '?'})")
    # ---- 热路径落点（plan/rollout-local-scratch §2 P0-1/P0-2）----
    # 三样热数据（TS 树的读、每局 shard 的写、权重的读）全在网络挂载点上 ⇒ 换到节点本地盘，
    # 结算一局搬回一局（P0-3）。换不过去就**逐字**走今天的行为（不搬、不 drain、不换根）。
    exec_jd = jd
    s_layout: scratch.ScratchLayout | None = None
    gate = scratch.InstallGate()
    need = scratch.need_bytes_for(len(argvs))
    picked = scratch.open_scratch(jd, need_bytes=need, log=rb.note) if argvs else None
    if picked is not None:
        ts_new = picked.ts_for(tsd.name)
        ok_tree, tree_res = call_bounded(
            partial(scratch.install_tree, tsd, ts_new, log=rb.note),
            game_watch.SCAN_CEILING_SEC,
            name="scratch-ts",
        )
        ok_mirror, mirror_res = (False, False)
        if ok_tree and tree_res:
            ok_mirror, mirror_res = call_bounded(
                partial(scratch.mirror_inputs, jd, picked.exec_dir, argvs, log=rb.note),
                game_watch.SCAN_CEILING_SEC,
                name="scratch-mirror",
            )
        if ok_tree and tree_res and ok_mirror and mirror_res:
            s_layout, tsd, exec_jd = picked, ts_new, picked.exec_dir
            rb.note(f"热路径换到本地盘：TS 树 {ts_new}｜每局产出 {picked.exec_dir}")
        else:
            rb.note(
                "WARN rollout 本地盘换根失败（TS 树/权重装不过去或超界）"
                "——本轮回落原地（逐字走今天的行为：不搬、不 drain、不换根）"
            )
    if s_layout is not None:
        rb.add(
            "产出落点",
            f"{s_layout.root}（本地盘；可用 {(scratch.free_bytes(s_layout.root) or 0.0) / 1e9:.1f}GB"
            f"｜本轮需 {need / 1e6:.0f}MB）——结算一局搬回一局到 job 目录",
        )
    else:
        rb.add("产出落点", f"{jd}（回退原地＝今天的行为：不搬、不 drain、不换根）")
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
            # 读数语义（P0-1）：`ready_n` = **真打过 `__SERVE_READY__` 且没死**的个数（旧形态只数
            # 「进程没退出」，于是 94/94 那种假满）。`unready` = 这次还在冷启动、被**留下**的个数
            # （不杀：杀掉 = 这批冷启动白付 + 补位再付一次；就绪后自动可发放）。
            rb.note(
                f"长驻 worker 池：{ready_n}/{workers} 真就绪"
                + (f"（另 {pool.unready} 个仍在冷启动，就绪即入池）" if pool.unready else "")
                + f"（{argvs[0][0]}）——逐局进程启动/权重解析只付一次，"
                "单局失败自动回退一次性 spawn"
            )
        elif pool.unready:
            # 零真就绪但有一批在冷启动 ⇒ **不退池**（P0-1 不杀）：那批就绪后随轮入池；现在关池
            # 等于把它们全扔掉、余下局每局重付一次冷启动（旧形态的放大器之一）。
            rb.note(
                f"长驻 worker 池：0/{workers} 真就绪（{pool.unready} 个仍在冷启动，就绪即入池）"
                "——本波先走一次性 spawn（就绪一个省一次冷启动）"
            )
        else:
            rb.note("长驻 worker 池起不来 ⇒ 本轮全部走一次性 spawn")
            pool.close()
            pool = None
    game_secs: list[float] = [0.0] * len(argvs)
    game_attempts: list[int] = [1] * len(argvs)
    ok = False
    round_retries = 0  # 「整轮重投」次数（类：机器级停滞）
    retry_cap = round_retry_max()
    drained_n = 0
    drain_failed_n = 0

    def _drain_one(i: int, out_rel: str) -> bool:
        """结算一局就搬一局（主线程、有界、**闸内**原子安装）。

        三条硬约束见 plan §2 P0-3：暂存在 job 目录之外（F1）· 确定性 errno 响亮上抛（F3）·
        超界作废安装票（F4）。返回 False ⇒ 本局**不算结算**（与机器级停滞同口径交整轮重投）。
        """
        nonlocal drained_n, drain_failed_n
        label = _game_label(argvs[i])
        src, dst = Path(exec_jd) / out_rel, jd / out_rel
        ticket = gate.ticket(out_rel)
        done, res = call_bounded(
            partial(scratch.drain_tree, src, dst, ticket=ticket, log=log),
            float(game_watch.DRAIN_CEILING_SEC),
            name=f"drain-{label}",
        )
        if not done:
            ticket.cancel()  # ★ 评审 F4：被放弃的 copier 从此装不进去（锁内复核）
            drain_failed_n += 1
            log(
                game_watch.drain_ceiling_line(
                    "rollout", label, game_watch.DRAIN_CEILING_SEC, str(src)
                )
            )
            return False
        if not res:
            drain_failed_n += 1
            log(game_watch.drain_fail_line("rollout", label, drained_n, drain_failed_n, str(src)))
            return False
        drained_n += 1
        return True

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            last_log_at = t0
            settled_n = 0
            # `pending` = 还没拿到结果的局（**跨重投存活**）：机器级停滞重投时只补这几个，
            # 已结算的局一个字都不重跑（用户口径：不许空转烧配额）。
            pending: list[int] = [i for i in range(len(argvs))]
            while pending:
                # 一局**整条链路**的墙钟上界（见 `game_watch.game_ceiling_sec`）：它才是「一局
                # 永不放跑整轮」的那条闸。不设它的话，任何一步不可取消的阻塞（挂住的挂载点上的
                # mkdir/open/写盘、`Popen` 等 exec 的握手）都会让 future 永不结算 ⇒ 下面的 `wait`
                # 永远收不齐 ⇒ 重投那套护栏一次也触发不了（2026-10-06 现场）。每次尝试重新算：
                # 常量可被用例 patch，展开的值会进超界行（读数与判据同一份）。
                ceiling_sec = game_watch.game_ceiling_sec(timeout_sec, explicit=explicit)
                futs = {
                    ex.submit(
                        _run_one_game_bounded,
                        bun,
                        argvs[i],
                        exec_jd,
                        tsd,
                        argvs[i][argvs[i].index("--out") + 1],
                        timeout_sec,
                        log,
                        explicit,
                        pool,
                        ceiling_sec=ceiling_sec,
                        job_dir=jd,
                    ): i
                    for i in pending
                }
                # 循环用**带超时的 wait** 而不是 `as_completed`：`as_completed` 只在「有局结算」时
                # 才醒，而进度行与心跳都挂在结算上 ⇒ 所有线程一起卡住时它们一起哑，日志进入完全
                # 静默（2026-09-25 现场：5s 的 270/336 之后 890s 一行都没有）。带超时的 wait 每
                # `GAME_POLL_SEC` 给我们一次说话的机会 —— 停滞就按 `STALL_WARN_SEC` 点名（带还在
                # 飞的局身份，见 game_watch.stall_line）。
                inflight = set(futs)
                last_settle = time.time()
                stalled_at = 0.0
                stuck: list[int] = []  # 本次尝试里「机器级停滞」没产出结果的局
                while inflight:
                    finished, inflight2 = wait(
                        inflight, timeout=game_watch.GAME_POLL_SEC, return_when=FIRST_COMPLETED
                    )
                    inflight = inflight2
                    now = time.time()
                    if not finished:
                        if (now - last_settle) >= game_watch.STALL_WARN_SEC and (
                            now - stalled_at
                        ) >= game_watch.STALL_WARN_SEC:
                            stalled_at = now
                            log(
                                game_watch.stall_line(
                                    "rollout",
                                    len(inflight),
                                    now - last_settle,
                                    [_game_label(argvs[futs[f]]) for f in inflight],
                                )
                            )
                        continue
                    for fut in finished:
                        i = futs[fut]
                        try:
                            game_secs[i], game_attempts[i] = fut.result()
                        except UnreapableChildError:
                            # **机器级**：这一局没产出，但整轮**不失败**——记下来，等本次尝试
                            # 其它在飞的局收完（它们可能只是慢），再由外层重投补它（见循环头）。
                            stuck.append(i)
                        else:
                            # ★ 换根档：**先搬回再算结算**（评审 F7）——搬不回去的局不算产出，
                            # 与机器级停滞同口径并入 stuck（下一轮重投补它）。排进度/打点**之前**，
                            # 所以 hold 判活看到的 settled 数永远是「真落地的」。
                            if s_layout is not None and not _drain_one(
                                i, argvs[i][argvs[i].index("--out") + 1]
                            ):
                                stuck.append(i)
                            else:
                                settled_n += 1
                        # 进度行**按时间**节流（`game_watch.progress_due`，缺省每分钟一句）：原来的
                        # 「每 10 局一句」在高并发轮上是每秒数行 —— 云端离线课的日志就是被它刷屏的
                        # （用户口径 2026-09-23）。最后一句恒打（轮结束的唯一落点）。
                        rb.add("进度", f"{settled_n}/{len(argvs)} games settled ({now - t0:.0f}s)")
                        # ★M3 / Q2：轮内**打点**（每结算一局上报一个完成事件）。真正的 HTTP 与
                        # 时间节流在打点层（云机侧 `remote/offline_boot.py`，每 240s 最多一句）；
                        # 本机训练没有注册者 ⇒ `report` 立刻返回（一次 dict 查找，零副作用）。
                        progress_hook.report("iter", done=settled_n, total=len(argvs))
                        if game_watch.progress_due(settled_n, len(argvs), now, last_log_at):
                            last_log_at = now
                            rb.beat("kind=iter rollout", now=now)
                    last_settle = now
                if not stuck:
                    break
                round_retries += 1
                if retry_cap and round_retries > retry_cap:
                    # 只有操作员显式设了上限才走这里（缺省不限）：上抛交回调用方自己的重试语义。
                    raise RetryableError(
                        f"rollout 机器级停滞：轮内已重投 {round_retries - 1} 次仍有 "
                        f"{len(stuck)} 局没产出（收不了尸/超界；{ENV_ROUND_RETRY_MAX}={retry_cap} "
                        "是操作员设的上限）"
                        f"——剩余 {len(stuck)} 局交回上层重试；现场见各局 w*/rollout.log"
                    ) from None
                log(
                    f"WARN rollout 整轮重投第 {round_retries} 次：本次尝试有 {len(stuck)} 局没产出"
                    f"（机器级停滞：收不了尸/单局超界，已结算 {settled_n}/{len(argvs)} 局）——"
                    f"只补这 {len(stuck)} 局，"
                    f"先清掉它们的半截产出（旧写者可能还在）；不报失败、不退租约、云机不停"
                    + ("" if not retry_cap else f"（上限 {retry_cap} 次）")
                )
                for i in stuck:
                    # 清理也是**文件 IO**（rglob + rmtree）：挂住的挂载点上它会一样永不返回 ——
                    # 主线程绝不能在这里被卡住（那同样会让整轮对日志静默）。有界地放弃它：
                    # 旧写者本身正卡在不可中断的 IO 里，删不动也只能带着半截产出往下走
                    # （下一轮的同名写入会截断它，兜底口径见 `_clean_attempt`）。
                    cleaned, _ = call_bounded(
                        partial(_clean_attempt, jd, argvs[i]),
                        ceiling_sec,
                        name=f"clean-{_game_label(argvs[i])}",
                    )
                    if not cleaned:
                        log(
                            f"WARN rollout 半截产出清不掉：{_game_label(argvs[i])}"
                            f"（{ceiling_sec:g}s 内没返回——挂载点 IO 还没好）"
                            "——不就地等，照常重投这一局（不是失败，云机不停）"
                        )
                pending = sorted(stuck)
        ok = True
    finally:
        if pool is not None:
            # 池的收益与代价（served/spawned/killed/fallback）进同一行的收尾字段。
            rb.note(pool.summary(), final_only=True)
            pool.close()
        if s_layout is not None:
            # 轮末只清**自己那一份**（评审 F5）：ts 家内容寻址、永不清（跨轮/两腿复用）。
            ok_clean, _ = call_bounded(
                partial(scratch.clear_dir, s_layout.exec_dir),
                float(game_watch.DRAIN_CEILING_SEC),
                name="scratch-clean",
            )
            ok_stage, _ = call_bounded(
                partial(scratch.drop_drain_stage, jd),
                float(game_watch.DRAIN_CEILING_SEC),
                name="scratch-stage",
            )
            if not (ok_clean and ok_stage):
                log(
                    f"WARN rollout scratch 轮末没清完（{game_watch.DRAIN_CEILING_SEC:g}s 内没返回）："
                    f"{s_layout.job}——本地盘占着就占着（下一轮同名会重建），不阻塞收尾"
                )
        if not ok:
            # 中断也要交代现场（看门狗口径/池计数/已结算到哪一局）——否则「为什么被杀了」
            # 无从归因。重试/慢局那几行已经在抛出前各自打过了。
            rb.emit("kind=iter rollout 中断")
    if round_retries:
        # 重投过就要可见（它是“机器卡过”的唯一记分）——正常轮恒空。
        rb.add("整轮重投", f"{round_retries} 次（机器级停滞：收不了尸/超界）")
    # ★ 轮末三步都在主线程上做全量文件 IO（rglob + 逐局读 JSON + 内容哈希）：挂住的挂载点上
    # 它们永不返回，而这里没有轮循环、也就没有「整轮停滞」那行能说话 ⇒ 整轮对日志完全静默。
    # 每一步都有自己的上界（`game_watch.SCAN_CEILING_SEC`），超界按机器级停滞上交。
    where = str(jd)
    expected_fp = iter_expected_data_fp(spec)
    shard_dirs = _scan_bounded(
        "verify_shards", lambda: verify_shards(jd, expected_fp), log, where=where
    )
    reports = _scan_bounded("collect_reports", lambda: collect_reports(jd, spec), log, where=where)
    # `combine_reports` 是纯 CPU 聚合（不碰盘）：不包（上界只给**不可取消的等**）。
    report = combine_reports(reports)
    per_game = _scan_bounded(
        "collect_shard_manifests",
        lambda: collect_shard_manifests(shard_dirs),
        log,
        where=where,
    )
    # 逐局压缩画像随轮账本行走：云机离线腿没人把单局 manifest 拉回本机（轮末 prune 就删了），
    # 而控制台的「耗时/击杀/残血/道具」列是**逐局**聚合的 ⇒ 不带它那几列永远空（见
    # `biz/reports.compact_per_game` 的 docstring）。在线腿已有 `dist/<节点>/rl_s*/` 路也不冲突
    # （同一份数据，读方优先用账本里的这一块）。
    report["perGame"] = compact_per_game(per_game)
    report["shards"] = len(shard_dirs)
    report["elapsedSec"] = round(time.time() - t0, 3)
    report["perGameSecs"] = game_secs
    # 与本机 rollout 的 manifest 同规（`biz/cmd` 的 --node-label 决定；上云 = "node"）。
    report["rolloutSrc"] = "node"
    rb.add("shards", len(shard_dirs))
    rb.add("games", report["games"])
    rb.add("winRate", report["winRate"])
    rb.add("samples", report["totalSamples"])
    rb.add("逐局画像", f"{len(report['perGame'])}/{len(shard_dirs)} 行")
    if not report["perGame"]:
        # 四列数据源没了是**要看的**（控制台耗时/击杀/残血/道具会恒空）。
        rb.note("逐局画像 0 行 = 控制台耗时/击杀/残血/道具恒空，查单局 manifest")
    if s_layout is not None:
        # 搬回的读数（正常轮 85 成 / 0 败）：它是「本地盘 → job 目录」这一段的唯一记分。
        rb.add("搬回", f"{drained_n} 局/失败 {drain_failed_n} 局（本地盘 → job 目录，主线程单线程）")
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
            # 起出来但还没就绪就被留下的 worker 数（P0-1；长期不归零 = 冷启动被压住/根本起不来）
            "unready": pool.unready,
            "fallback": pool.fallback,
            "reasons": dict(pool.fallback_reasons),
        },
    }
