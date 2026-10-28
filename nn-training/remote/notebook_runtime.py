"""remote/notebook_runtime.py — 云端 notebook（battle.cloudflared.ipynb / battle.tailscale.ipynb）的运行时逻辑。

为什么在 code.zip 里（2026-09-13 单 cell 重构）：notebook cell 只留「参数 + 保活 +
/code 引导」，设备探测 / pull / push / 崩溃退避重启全部住在本模块——随 hub 下发的
code.zip 走，修运行时逻辑不用重发 notebook（重跑 cell 即拉到新版）。cell 端契约：
先把解包目录插进 sys.path，再 `from remote.notebook_runtime import run_notebook`。

cfg 键（cell 的 CFG dict）：
  mode ("pull"|"push") · hub_url · hub_token · push_port · push_token ·
  registry_url (自动注册的 hub；留空用 hub_url) · node_id (留空 = `gpu-<env>`) · label ·
  cloudflared_path · device ("auto"|cuda|cuda-dp|tpu|cpu) · use_multi_gpu ·
  max_session_hours · poll_interval_sec · idle_floor_sec · max_worker_restarts ·
  keepalive_stop (threading.Event，cell 的保活线程停机柄) · log (callable) ·
  code_dir (已解包的 code.zip 目录，push 模式起服务用) ·
  work_dir (可选覆盖 /tmp 工作目录——单测注入 tmp_path 用)

★ 后端不在这里选：`resolve_device` 用 `remote.bc_job.resolve_ppo_backend("auto")` 问出
「worker 会落在哪个框架」，再探测**那个**框架的设备（notebook 没有 `--backend` 通道，
worker 恒按 auto 解析 ⇒ 两边按构造是同一份判据）。写回：`device_resolved` ·
`backend_resolved` · 可见卡数 `gpu_count`（下游 `worker_restart_argv` 用）。

TPU 硬约束（2026-09-10/11 线上事故，勿退化）：
  1. torch / torch_xla 版本严格配对（镜像自带）——绝不 pip 覆盖；
  2. PJRT_DEVICE=TPU 只影响 worker 子进程；
  3. ★ 内核/notebook 进程绝不 import torch_xla：/dev/vfio*（v5e）、/dev/accel*
     （v3/v4）是独占 PCI 直通设备——本进程碰过，worker 子进程必报
     `TPU initialization failed: open(/dev/vfio/0): Device or resource busy`。
     探测只查「装没装 / 谁占着」（find_spec / metadata / /proc/*/fd），不 open 设备。
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

from common.env_probe import probe_cloud_env
from common.logutil import tag_logger
from common.pid_probe import pid_alive
from common.proc import run_capture
from remote.push_tunnel import find_cloudflared, start_quick_tunnel, tunnel_url_of

# cell 端 /code 引导（fetch+unpack+sys.path）已完成；本模块只管运行时。


#: 2026-10-11（plan/nn-training-reuse-and-deps §S2）：4 份同形 `_log_default` 收敛为工厂；
#: `lambda: time` 保留「每次重读本模块 `time`」的注入缝（7 处假钟用例钉着）。
_log_default = tag_logger("battle-rl", lambda: time)

def _epoch_mult_of(cfg: dict[str, Any]) -> float:
    """`CFG.epoch_mult`：PPO 每轮更新遍数 × N（缺省 1.0 = 课程/manifest 原样）。

    与离线盘 `offline_boot.build_run_argv` 同一条校验：有限正数，非法值**开跑前**拒绝；
    空值/None 当作缺省 1.0。
    """
    raw = cfg.get("epoch_mult", 1.0)
    if raw is None or raw == "":
        raw = 1.0
    try:
        v = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"CFG.epoch_mult 必须是正数（当前 {raw!r}）") from e
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"CFG.epoch_mult 必须是有限正数（当前 {raw!r}）")
    return v


#: push 守候期「过程日志静默多久才补一行心跳」（秒）。job 在跑（`busy=true`）而 serve.log
#: 还在往外吐时，那行 `守候中… busy=True` 只是刷屏（2026-10-26 用户口径）——但**完全静默**
#: 也不能当作正常（长 PPO 步 / 卡死），所以留一条补位心跳。
_SERVE_QUIET_SEC = 60.0

#: 心跳行的最小间隔（秒）。2026-10-26 用户口径：「不 busy 的时候，每三分钟刷一次」——
#: 空等时那行 15s 一条毫无信息量（busy/queued/done 一个都不变），三分钟够证明「还活着」。
_HEARTBEAT_SEC = 180.0


def _hub_open(req: Any, timeout: float = 15) -> Any:
    """显式 ProxyHandler —— Colab userspace 模式下 urlopen() 不读 HTTP_PROXY（2026-09-16 实测）。
    无代理时回退 urlopen（测试 mock 路径）。"""
    import urllib.request

    proxies: dict[str, str] = {}
    for k in ("http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["http"] = v
            break
    for k in ("https_proxy", "HTTPS_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["https"] = v
            break
    if proxies:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies)).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


# ════════════════════════ 设备探测：backend → CUDA → TPU → CPU ════════════════════════


def _tpu_device_nodes() -> list[str]:
    """本 runtime 映射进来的 TPU 设备节点（只 glob 目录名，绝不 open）。"""
    return sorted(glob.glob("/dev/vfio/*")) + sorted(glob.glob("/dev/accel*"))


def _tpu_holders() -> list[tuple[int, str, str]]:
    """谁正占着 TPU 设备节点（pid + 节点 + cmdline）；扫 /proc/*/fd，同样不 open 设备。"""
    out: list[tuple[int, str, str]] = []
    for fdlink in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            tgt = os.readlink(fdlink)
        except OSError:
            continue
        if not tgt.startswith("/dev/vfio"):
            continue
        pid = int(fdlink.split("/")[2])
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            cmd = "?"
        out.append((pid, tgt, cmd))
    return out


def _run_probe(log, code: str, what: str) -> dict | None:
    """在**子进程**里跑一段「最后打印一行 JSON」的探测代码，返回解析结果。

    为什么一律走子进程（2026-09-16 Kaggle 实测）：引导（tailscale 登录）后 **kernel 进程内**
    `import torch` 会无声杀死整个会话——`import torch` 前一行的日志还在、无任何 traceback 即
    断连；拆到子进程后同环境 import 正常（S2/S4/D1-D3 全部存活）。paddle 走同一条纪律：
    CUDA 上下文一旦在 kernel 里建起来，worker 子进程继承的是脏状态。

    返回三态（`_probe_cuda` / `_probe_paddle` 共用同一契约）：
      * `{"v","n","names"}` —— 框架可用（`n=0` 就是真没 GPU）；
      * `{"err"}` —— **装了但 import 崩**（FATAL 档，不能当「无 GPU」回落）；
      * `None` —— 探测子进程起不来 / 输出不可解析（真·未知）。
    """
    import json as _json

    try:
        # run_capture 显式 UTF-8：这台机器上 torch 可能吐出带中文的 warning/错误行，
        # 裸 text=True 会在读线程里抛 UnicodeDecodeError ⇒ stdout 变成 None（§19）。
        r = run_capture([sys.executable, "-u", "-c", code], timeout=60)
    except BaseException as e:  # 子进程起不来/超时——探测失败按未知
        log(f"{what} 探测子进程异常（{type(e).__name__}: {e}）—— 按未知处理")
        return None
    if r.returncode != 0:
        # 子进程非零退出：把它的 stderr 尾巴带出来 —— 这就是「装坏了」最直接的证据
        tail = ((r.stderr or "") or (r.stdout or "")).strip().splitlines()[-1:] or [""]
        log(f"{what} 探测子进程退出 rc={r.returncode}（{tail[0][:200]}）")
        return {"err": f"probe exit rc={r.returncode}: {tail[0][:200]}"}
    try:
        return cast(dict[str, Any], _json.loads((r.stdout or "").strip().splitlines()[-1]))
    except (ValueError, IndexError):
        log(f"{what} 探测子进程输出不可解析：{(r.stdout or '').strip()[:200]!r} —— 按未知处理")
        return None


def _probe_cuda(log) -> dict | None:
    """子进程探测 torch/CUDA 可见性（kernel 本体永不 import torch）。

    2026-09-16 Kaggle 实证：引导（tailscale 登录）后 **kernel 进程内** `import torch`
    会无声杀死整会话——`import torch` 前一行的日志还在、无任何 traceback 即断连；
    而拆到子进程后同环境 import torch 正常（S2/S4/D1-D3 隔离实验全部存活）。探测放
    子进程后，最坏情况只是探测子进程退一层 → 按无 CUDA 落 TPU/CPU，kernel 照常继续；
    与「内核绝不 import torch_xla」是同一纪律的延伸。

    返回：
      * `{"v": 版本, "n": 卡数, "names": [...]}` —— torch 正常（n=0 就是真没 GPU）；
      * `{"err": "<原因>"}` —— **torch 本身装坏了**（import 就崩，例如
        `OSError: libcudart.so.11.0: cannot open shared object file`）。★ 2026-10-26 加：
        旧实现把这一档和「无 CUDA」混成一个 `None`，于是设备判定照常回 CPU、训练照常开跑，
        直到一轮 rollout 采完（云机 35 关 · 47 批 · 十几分钟）进 PPO 才 `OSError` 崩——
        **一整轮采集白烧**。调用方（`resolve_device`）必须 FATAL，不是回落。
      * `None` —— 探测子进程起不来 / 输出不可解析（真·未知）。
    """
    _probe = (
        "import json\n"
        "try:\n"
        "    import torch\n"
        "except BaseException as e:\n"
        "    print(json.dumps({'err': type(e).__name__ + ': ' + str(e)}), flush=True)\n"
        "    raise SystemExit(0)\n"
        "try:\n"
        "    n = torch.cuda.device_count() if torch.cuda.is_available() else 0\n"
        "    names = [torch.cuda.get_device_name(i) for i in range(n)]\n"
        "except BaseException as e:\n"
        "    print(json.dumps({'err': 'cuda probe: ' + type(e).__name__ + ': ' + str(e)}), flush=True)\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'v': torch.__version__, 'n': n, 'names': names}), flush=True)\n"
    )
    return _run_probe(log, _probe, "torch")


def _probe_paddle(log) -> dict | None:
    """子进程探测 paddle 可见性（kernel 本体永不 import paddle）。

    ★ 2026-10-27：这条腿是补的。旧版 `resolve_device` 只认 torch —— `import torch` 失败就
    FATAL，哪怕本机 paddle 装得好好的、`auto` 后端本来就会落到 paddle（AI Studio 训练镜像
    正是这个形态：paddlepaddle-gpu 在、torch 根本不存在）。用户口径：「不应该继续探测 paddle
    吗」。现在后端先由 `_resolve_backend` 定，再探测**那个**框架。

    `paddle.device.cuda.get_device_name` 在 CPU-only 构建上会抛（实测
    `ValueError: ... not supported in CPU-only PaddlePaddle`）⇒ 逐个 try，拿不到就 `?`。
    """
    _probe = (
        "import json\n"
        "try:\n"
        "    import paddle\n"
        "except BaseException as e:\n"
        "    print(json.dumps({'err': type(e).__name__ + ': ' + str(e)}), flush=True)\n"
        "    raise SystemExit(0)\n"
        "try:\n"
        "    n = int(paddle.device.cuda.device_count()) if paddle.is_compiled_with_cuda() else 0\n"
        "    names = []\n"
        "    for i in range(n):\n"
        "        try:\n"
        "            names.append(str(paddle.device.cuda.get_device_name(i)))\n"
        "        except BaseException:\n"
        "            names.append('?')\n"
        "except BaseException as e:\n"
        "    print(json.dumps({'err': 'cuda probe: ' + type(e).__name__ + ': ' + str(e)}), flush=True)\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'v': paddle.__version__, 'n': n, 'names': names}), flush=True)\n"
    )
    return _run_probe(log, _probe, "paddle")


def _resolve_backend() -> str:
    """本机 `auto` 会落到哪个训练后端 —— **问 worker 的同一个函数**，不另立判据。

    notebook 这条腿没有任何地方能传 `--backend`：pull 的 `worker_restart_argv` 不带它，
    push 的 `remote_worker_serve` 也没有这个参数 ⇒ worker 恒按 **auto** 解析。这里同样问
    auto，两边的框架选择**按构造**相同（torch 装了就是 torch，没装才 paddle）。
    哪天给 notebook 加了 `--backend` 通道，这里必须改成读 cfg —— 否则日志上的 backend
    和实际跑的框架会分叉（那就是第二个事实源）。
    """
    from remote.bc_job import resolve_ppo_backend

    return resolve_ppo_backend("auto")


def _resolve_device_paddle(cfg: dict[str, Any], log) -> str:
    """Paddle 后端的设备解析（AI Studio 训练镜像：paddlepaddle-gpu 在、torch 不存在）。

    与 torch 腿同形：`auto` = 有卡 `cuda` / 没卡 `cpu`；显式值原样返回（不悄悄改）。
    torch 专属实验臂（`cuda-dp` 的 DataParallel、`tpu`/`xla` 的 XLA、`mps`）与训练核
    `paddle_env.check_device` **同一份名单** ⇒ 在开跑前就拒，而不是等 hub 推完 job、
    采完一轮才在 PPO 里炸。`paddle.set_device` 认 `cuda` / `cuda:N`（实测正则匹配 gpu），
    所以 `worker_restart_argv` 的一卡一 worker 写法原样可用。
    """
    from remote.paddle_env import TORCH_ONLY_DEVICES

    d = str(cfg["device"])
    if d in TORCH_ONLY_DEVICES:
        raise SystemExit(
            f"[FATAL] device={d!r} 是 torch 专属实验臂，paddle 后端没有对应实现"
            "（DataParallel / XLA / MPS）—— 用 auto / cuda / cpu。\n"
            '  多卡请走 CFG["workers"]（一卡一 worker），不要用 cuda-dp。'
        )
    info = _probe_paddle(log)
    if info is not None and info.get("err"):
        raise SystemExit(
            f"[FATAL] paddle 不可用（{info['err']}）——拒绝开始训练。\n"
            "  本机 `auto` 后端 = paddle（没装 torch），设备也只能由 Paddle 判；\n"
            "  继续跑只会在 PPO `import paddle` 时崩掉，照样白烧一轮采集。\n"
            "  修法：装与本机 CUDA 匹配的 paddlepaddle-gpu"
            "（`python -m pip install paddlepaddle-gpu`），或装上 torch 让 auto 回到 torch 后端，"
            "然后重跑本 cell。"
        )
    if info is not None and int(info.get("n") or 0) > 0:
        n_gpu = int(info["n"])
        # 与 torch 腿同一个理由：卡数交给下游 argv（`worker_restart_argv` 判一卡一 worker）。
        cfg["gpu_count"] = n_gpu
        log(f"paddle {info.get('v')}，CUDA 可见 {n_gpu} 张 GPU")
        names = list(info.get("names") or [])
        for i in range(n_gpu):
            log(f"  [{i}] {names[i] if i < len(names) else '?'}")
        if d == "auto":
            if n_gpu > 1 and cfg.get("use_multi_gpu"):
                log('  paddle 没有 DataParallel 实验臂 —— 多卡走 CFG["workers"]（一卡一 worker）')
            elif n_gpu > 1:
                log('  -> 只用第 0 张卡（多卡走 CFG["workers"]）')
            return "cuda"
        log(f"  显式指定 {d}（按指定执行）")
        return d
    if info is None:
        log("paddle 探测未知（子进程没起来/输出不可解析）→ 按 CPU 开跑")
    else:
        log("Paddle 无 CUDA（CPU-only 构建或没卡）→ CPU")
    return "cpu" if d == "auto" else d


def resolve_device(cfg: dict[str, Any], log) -> str:
    """设备解析：**先定后端**（auto → torch / paddle），再按后端探测设备。

    · 后端 torch：显式指定优先；auto = CUDA（多卡按 use_multi_gpu）→ TPU → CPU。
    · 后端 paddle：见 `_resolve_device_paddle`。

    探测一律走子进程；TPU 探测仍用 find_spec + /dev 扫描 —— 三条路 kernel 本体都不
    import torch / paddle / torch_xla。
    """
    backend = _resolve_backend()
    cfg["backend_resolved"] = backend
    log(f"设备探测中（device={cfg['device']}，backend=auto→{backend}）…")
    if backend == "paddle":
        return _resolve_device_paddle(cfg, log)
    info = _probe_cuda(log)
    # ★ 2026-10-26：**torch 装坏了必须在开跑前就 FATAL**，不能回落 CPU。
    #   现场（云机 python 3.10）：`torch` 装了但 CUDA runtime 缺 ⇒ `import torch` 抛
    #   `OSError: libcudart.so.11.0: cannot open shared object file`。旧实现把这档并进
    #   「无 CUDA」⇒ 设备判 CPU、训练照常开跑 ⇒ 35 关采了 47 批（十几分钟）到 PPO
    #   `import torch` 才崩 ⇒ 整轮采集白烧，且日志最后一行的锅甩给 PPO。
    #   torch 是 PPO 的硬依赖：import 不了就等于这条路完全不可跑，早停是唯一正确处置。
    if info is not None and info.get("err"):
        # ★ 2026-10-27：这条 FATAL 现在只在「torch **装了**（find_spec 看得到 ⇒ auto 选它）
        #   但 import 就崩」时触发。**没装** torch 的机器走不到这里 —— auto 已经落到 paddle。
        #   所以修法要给出两条路：修 torch，或让 auto 落到 paddle（卸掉这份坏 torch）。
        import importlib.util as _iu

        try:
            has_paddle = _iu.find_spec("paddle") is not None
        except (ImportError, ValueError):
            has_paddle = False
        raise SystemExit(
            f"[FATAL] torch 不可用（{info['err']}）——拒绝开始训练。\n"
            "  继续跑只会白烧一轮采集：rollout 不需要 torch，但 PPO 一 import 就崩。\n"
            "  backend=auto 只在 **没装** torch 时才改走 paddle；这份 torch 装是装了、import 就崩"
            f" ⇒ auto 选不到 paddle（本机 paddle：{'有' if has_paddle else '没有'}）。\n"
            "  修法：① 装上与本机 python/cuda 匹配的 torch（`pip install torch --index-url "
            "https://download.pytorch.org/whl/cpu` 走 CPU 版最省事），或补上被缺的 CUDA runtime；\n"
            "        ② 本机有可用 paddle 时，卸掉/改名这份坏 torch（`pip uninstall torch`）"
            "让 auto 落到 paddle。然后重跑本 cell。"
        )
    if info is not None and int(info.get("n") or 0) > 0:
        n_gpu = int(info["n"])
        # ★ 2026-10-21：把卡数交给下游 argv 生成（`worker_restart_argv` 判「一卡一 worker」
        #   还是「一卡 N worker」）。以前这个数只用于打日志，于是 1 卡机上 `workers=2`
        #   会拿到 `cuda:1` ⇒ invalid device ordinal（每单炸一次）。
        cfg["gpu_count"] = n_gpu
        log(f"torch {info.get('v')}, CUDA 可见 {n_gpu} 张 GPU")
        names = list(info.get("names") or [])
        for i in range(n_gpu):
            log(f"  [{i}] {names[i] if i < len(names) else '?'}")
        d = str(cfg["device"])
        if d == "cuda-dp":
            if n_gpu > 1:
                log(f"  -> DataParallel 跨 {n_gpu} 卡（梯度归约顺序变化，与单卡 run 数值不可逐位比）")
                return "cuda-dp"
            log(f"  -> 指定 cuda-dp 但只可见 {n_gpu} 张卡——退化为单卡 cuda（与 worker 同语义）")
            return "cuda"
        if d in ("auto", "cuda"):
            # DP 只在 auto + use_multi_gpu + 真多卡时启用——显式 "cuda" 绝不悄悄升级
            if d == "auto" and cfg["use_multi_gpu"] and n_gpu > 1:
                log(f"  -> DataParallel 跨 {n_gpu} 卡（梯度归约顺序变化，与单卡 run 数值不可逐位比）")
                return "cuda-dp"
            if n_gpu > 1:
                log("  -> 只用第 0 张卡（要跨卡把 use_multi_gpu 改 True 或显式 cuda-dp）")
            return "cuda"
        log(f"  显式指定 {d} 与 CUDA 环境不符——按指定执行")
        return d

    import importlib.metadata as md
    import importlib.util as iu

    if iu.find_spec("torch_xla") is not None:
        try:
            xla_ver: str = md.version("torch_xla")
        except Exception:
            xla_ver = "（已安装，版本未知）"
        os.environ.setdefault("PJRT_DEVICE", "TPU")
        log(f"torch_xla {xla_ver}（内核未导入——设备留给 worker 子进程）")
        log(f"  TPU 设备节点: {_tpu_device_nodes() or '（没看到 /dev/vfio* 或 /dev/accel*）'}")
        holders = _tpu_holders()
        for pid, tgt, cmd in holders:
            tag = "★本内核★" if pid == os.getpid() else "其它进程"
            log(f"  ⚠ {tgt} 已被占用: pid={pid} [{tag}] {cmd[:90]}")
        if holders:
            log("  ⚠ 占用存在时 worker 子进程必报 busy；占用者是本内核 → 只能 Runtime → Restart session")
        else:
            log("  vfio 无占用 —— 设备空闲，worker 子进程可正常领取")
        if cfg["device"] in ("auto", "tpu"):
            return "tpu"
        log(f"  显式指定 {cfg['device']}（TPU 环境下强行指定）")
        return str(cfg["device"])

    log("无 CUDA / 无 TPU（torch_xla 未安装）→ CPU")
    return "cpu" if cfg["device"] == "auto" else str(cfg["device"])


# ════════════════════════ Pull 模式 runner ════════════════════════


def worker_restart_argv(
    cfg: dict[str, Any],
    *,
    work_dir: Path,
    token_file: Path,
    max_idle: int,
    instance: int = 0,
    workers: int = 1,
) -> list[str]:
    """单个云机 worker 的重启 argv（监督器用它重拉子进程）。

    ★ 2026-10-21（`plan/ppo-worker-wallclock.plan.md` §4.2 W-DP / 评审 P0-2）：
    「一机 N worker」不需要新 launcher，也不需要控制台改动 —— 启动参数就在本函数里。
    `workers > 1` 时每个实例：**单卡**（`--device cuda:<i>`，显式单卡不会被 `auto` 升成 DP）+
    各自的 `--out`（`job_fs.prune_job_dirs` 会互删在飞目录）+ **共享的 `--cache-dir`**
    （否则 `course_cache/<course_fp>/latest.json` 各自一份 ⇒ 身份命中率掉到 ~1/N）。
    线程数按 vCPU 均分（缺省 `0` = torch/OMP 自己的缺省，**不是** 8——那个 8 只在
    `python -m worker.ppo.engine` 的 standalone CLI 上）。

    `workers == 1` 时逐字节等于改造前（顺序/字面量都不动）。
    """
    hub_url = str(cfg["hub_url"])
    # ★ 2026-10-21（第二次修正）：可见卡数 < workers ⇒ **共享同一张卡**（`cuda`）。
    #   上一版一律 `cuda:<instance>`，在**单卡机**（Colab T4 / aistudio V100 16GB 都是 1 张）
    #   上 `workers=2` 会让第二个子进程拿到 `cuda:1` ⇒ `invalid device ordinal`，每单炸一次。
    #   共享腿的语义：两个独立进程各自跑整单（数学逐位不变），只靠 GPU 并发吃利用率——
    #   这正是「步耗 0.49s 而利用率个位数」时可试的聚合腿。`gpu_count` 未知（旧 cfg / 测试）
    #   时保持旧行为，不做静默降级。
    gpus = int(cfg.get("gpu_count") or 0)
    # ★ 2026-10-21（第三次修正）：`cfg["threads"]` = **显式** torch intra-op 线程数（0/缺省 =
    #   逐字旧行为 `"0"`）。真机实测：Colab T4 可见 2 vCPU（`sched_getaffinity=2`、
    #   `cpu.max=max`），但 `omp_get_max_threads()`、`mkl_get_max_threads()` 都是 **1**，且
    #   `OMP_NUM_THREADS=4` **被忽略** ⇒ env 这条路无效，只有 `torch.set_num_threads(N)` 能改
    #   （`train_core.py:248` 正是它）。所以留一个显式旋钮，否则「1 线程」永远调不了。
    #   语义：`workers > 1` 时它是**每 worker** 的值（不自动除）——自动除只用于缺省推导。
    explicit_threads = int(cfg.get("threads") or 0)
    if workers > 1 and str(cfg["device_resolved"]).startswith("cuda"):
        device = "cuda" if (gpus and gpus < workers) else f"cuda:{int(instance)}"
        cpus = int(os.cpu_count() or 1)
        threads = str(
            explicit_threads if explicit_threads > 0 else max(1, cpus // max(1, int(workers)))
        )
    else:
        device = str(cfg["device_resolved"])
        threads = str(explicit_threads) if explicit_threads > 0 else "0"
    argv = [
        "--poll", hub_url,
        "--token-file", str(token_file),
        "--out", str(work_dir),
        "--device", device,
        "--threads", threads,
        "--poll-sec", str(cfg["poll_interval_sec"]),
        "--max-idle-sec", str(max_idle),
        # ★ 2026-10-20（plan/ppo-worker-transfer-isolation）：云机 PPO worker 的回传腿跑在
        # **独立子进程**里（`remote/upload_worker.py`）——与自主 worker 的补传腿同形
        # （`plan/offline-deliver-isolation` §P1）。不这么开场，云机 PPO 线程会继续与
        # `json.dumps` + gzip + HTTP 抢 GIL（2026-10-06 那条 40× 事故的通道）。
        "--result-upload", "process",
    ]
    if str(cfg.get("cache_dir") or "").strip():
        argv += ["--cache-dir", str(cfg["cache_dir"])]
    if cfg.get("offline_worker"):
        argv.append("--offline")
    # ★ 2026-10-26：训练侧放大（PPO 每轮更新遍数 × N）。**非缺省才传** ⇒ 旧 cfg 的 argv
    #   逐字不变（与离线盘 `build_run_argv` 同一条纪律）。
    _em = _epoch_mult_of(cfg)
    if _em != 1.0:
        argv += ["--epoch-mult", f"{_em:g}"]
    return argv


def run_pull_worker(cfg: dict[str, Any], log) -> int:
    """连接 hub 轮询领 job（PPO 与 BC 同 worker）。返回退出码：
    0 = 干净退出（空闲满/会话到顶）；-2 = 配置致命（不重启）；其它非 0 = 可重启的失败。"""
    from urllib.error import HTTPError
    from urllib.request import Request

    hub_url = str(cfg["hub_url"])
    hub_token = str(cfg["hub_token"])
    # work_dir 可被 cfg 覆盖（单测注入 tmp 目录；缺省 /tmp——Kaggle/Colab 语义）
    work_dir = Path(str(cfg.get("work_dir") or "/tmp/remote-worker"))
    work_dir.mkdir(parents=True, exist_ok=True)

    log(f"连接 hub: {hub_url}（/ping 探测…）")
    try:
        req = Request(f"{hub_url.rstrip('/')}/ping", headers={"Authorization": f"Bearer {hub_token}"})
        with _hub_open(req, timeout=15) as resp:
            resp.read()
        log("hub ping OK")
    except HTTPError as e:
        log(f"FATAL: hub /ping HTTP {e.code} — {'token 不匹配' if e.code in (401, 403) else 'hub 异常'}")
        return -2
    except Exception as e:
        log(f"FATAL: hub 不可达（{type(e).__name__}: {e}）——检查隧道 URL 是否过期")
        return -2

    # cell 已把 code 解包目录插进 sys.path[0]——worker 监督器直接从那里起
    from remote.worker import supervise_worker

    # token 走 --token-file（H10：不进进程列表）；热替换 = 子进程退 86 → 监督器
    # 用同参重拉（fresh 进程加载新代码），kernel 与 cell 的输出流不断。
    max_idle = max(int(cfg["idle_floor_sec"]), (int(cfg["max_session_hours"]) - 1) * 3600)
    # ★ W-DP（2026-10-21，plan/ppo-worker-wallclock §4.2）：一机 N worker。缺省 1 ⇒ 逐字节旧行为
    # （argv 顺序与字面量都不变）；N>1 时每个实例单卡 + 各自 `--out` + 共享 `--cache-dir`。
    n_workers = max(1, int(cfg.get("workers") or 1))
    if n_workers > 1 and not str(cfg.get("device_resolved", "")).startswith("cuda"):
        log(f"workers={n_workers} 但 device={cfg.get('device_resolved')} 不是 cuda ⇒ 只起 1 个 worker")
        n_workers = 1
    elif n_workers > 1 and 0 < int(cfg.get("gpu_count") or 0) < n_workers:
        # 可见卡数不够 ⇒ 全部落到设备 0。写清楚：这不是「一卡一 worker」。
        log(
            f"workers={n_workers} > 可见卡数 {int(cfg.get('gpu_count') or 0)} "
            "⇒ 全部共享 cuda（同一张卡内并发，单 job 数学不变；显存与主机线程按 N 分）"
        )
    # token 走 --token-file（H10：不进进程列表）；热替换 = 子进程退 86 → 监督器
    # 用同参重拉（fresh 进程加载新代码），kernel 与 cell 的输出流不断。
    specs: list[tuple[str, list[str]]] = []
    for i in range(n_workers):
        wd = work_dir if n_workers == 1 else work_dir.with_name(f"{work_dir.name}-w{i}")
        wd.mkdir(parents=True, exist_ok=True)
        token_file = wd / "hub.token"
        token_file.write_text(hub_token, encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except Exception:
            pass
        specs.append(
            (
                "worker" if n_workers == 1 else f"w{i}",
                worker_restart_argv(
                    cfg,
                    work_dir=wd,
                    token_file=token_file,
                    max_idle=max_idle,
                    instance=i,
                    workers=n_workers,
                ),
            )
        )
    if str(cfg.get("cache_dir") or "").strip():
        log(f"持久缓存根：{cfg['cache_dir']}（code/ts_code/blob 跨会话复用）")
    # 离线训练模式（2026-09-19）：`CFG["offline_worker"]` 打开 ⇒ 自报**归属**「本会话属于
    # 离线盘」——hub 只把归属为 offline 的整段 job 放给这台机器，**且**它不再兼领在线盘的活
    # （2026-09-25 语义从「能力」升为「归属」：有能力的盘接走不属于它的 job 就是那次事故；
    # 旧口径「带标仍可领在线课」已作废）。CLI 名与头的字面量不变（混合部署兼容）；
    # 缺省关 ⇒ 在线盘，行为逐字不变。
    if cfg.get("offline_worker"):
        log("已自报归属=离线盘（只领整段 job，不再兼领在线活）")
    log(
        f"worker 启动（max_idle={max_idle}s, poll={cfg['poll_interval_sec']}s, "
        f"device={cfg['device_resolved']}, workers={n_workers}, "
        f"threads={int(cfg.get('threads') or 0) or '框架缺省'}）"
    )
    if n_workers == 1:
        try:
            return supervise_worker(specs[0][1])
        except KeyboardInterrupt:
            log("收到中断——worker 已终止")
            return 0
    # 多实例：每个实例一个监督器线程（各自管子进程与热替换；输出行会交错，但每行完整）。
    rcs: dict[str, int] = {}

    def _run(name: str, argv: list[str]) -> None:
        try:
            rcs[name] = supervise_worker(argv)
        except KeyboardInterrupt:
            rcs[name] = 0

    threads = [
        threading.Thread(target=_run, args=(name, argv), name=f"sup-{name}", daemon=True)
        for name, argv in specs
    ]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log("收到中断——worker 已终止")
        return 0
    bad = [rc for rc in rcs.values() if rc != 0]
    return 0 if not bad else int(bad[0])


# ════════════════════════ Push 模式 runner ════════════════════════


def _pid_alive(pid: int | None) -> bool:
    """存活探测（委托唯一实现 `common.pid_probe.pid_alive`）。

    2026-09-17 收口：此处原是**函数内的嵌套闭包**，且直接 `os.kill(int(pid), 0)` —— 两个问题：
    ① 在 Windows 上 `os.kill(pid, 0)` 是 `TerminateProcess(handle, 0)`，而本函数用来判断
    bootstrap 已起的 `serve_pid` 还活着吗（只读查询）⇒ 会**把 worker_server 直接杀掉**；
    ② 嵌套定义使其不可被测试导入，只能靠人读代码发现。现在提到模块层并全程委托唯一实现。
    """
    return pid_alive(pid)


def _tunnel_url_of(path: Path) -> str:
    """从 cloudflared 日志里取当前 quick tunnel URL（取不到 ⇒ 空串）。

    实现已下沉到 `remote/push_tunnel.tunnel_url_of`（离线 push 同用一份）；保留本名是
    为了让既有测试/调用点的 import 面不变。
    """
    return tunnel_url_of(path)


def _hub_base_of(cfg: dict[str, Any]) -> str:
    """worker 眼里的 hub 基址（`registry_url` > `hub_url`，去尾斜杠）。

    注册与「结果就绪通知」用**同一个**地址：两条都是 worker → hub 的小消息，注册能通的
    地址通知也能通；多一个旋钮只会在隧道换名时漂开（一个换了一个没换）。
    """
    return str(cfg.get("registry_url") or cfg.get("hub_url") or "").strip().rstrip("/")


def _push_registry(cfg: dict[str, Any], log) -> Any:
    """GPU push 腿的注册器（`registry_url` > `hub_url`；缺 hub/token ⇒ 关闭态）。

    两把 token 不要混：`hub_token` = 注册鉴权（hub 的 admin 面）；`push_token` = 本节点的
    `authKey`（hub 用它调 worker 的 `/ping` 与 `/job`）。缺任何一把都不能假装注册成功。
    """
    from remote.push_register import Registry, default_node_id

    hub = _hub_base_of(cfg)
    return Registry(
        hub=hub,
        hub_token=str(cfg.get("hub_token") or "").strip(),
        node_id=str(cfg.get("node_id") or "").strip()
        or default_node_id("gpu_push", probe_cloud_env()),
        kind="gpu_push",
        label=str(cfg.get("label") or ""),
        log=log,
        # ★ 2026-10-28：必须是**代理感知**的那条 opener，不能用缺省的 `urlopen`。
        #   真机（Colab + userspace Tailscale，hub 在 tailnet 上 `100.124.208.62:8787`）：
        #   `/code` 下载成功而 `POST /admin/nodes/register` 一路 timed out——差别只在下载那侧显式
        #   挂了 `localhost:1055` 的 tailnet 代理（`notebook_boot._build_opener`），注册却走默认
        #   opener（`urlopen()` 不读 HTTP_PROXY，2026-09-16 实测）。注册/注销与下载共用同一条
        #   opener，`_hub_open` 无代理时本就回落 `urlopen` ⇒ 非 tailnet 部署逐字节不变。
        #   守门：`tests/remote/test_notebook_runtime.py::test_push_registry_register_goes_through_proxy_aware_opener`
        urlopen=_hub_open,
    )


def _register_or_warn(reg: Any, url: str, push_token: str, log) -> None:
    """首拍注册 + 人话横幅；失败 ⇒ 打印**可粘贴**的 ★ 手工片段（而不是静默假成功）。

    实现住在 `remote.push_register.register_or_warn`（与离线 push 腿**同一份**）：两条腿的差别
    只有 `reg.kind`，而「首拍 422 该不该重试 / 该怎么说」这种话术抄两份必然漂开。
    """
    from remote.push_register import register_or_warn

    register_or_warn(reg, url, push_token, log)


def _refresh_registration(reg: Any, cf_log: Path, cur_url: str, push_token: str, log) -> str:
    """每拍（15s）：隧道换名 ⇒ 重注册；未换 ⇒ 只在未注册时重试（退避由 Registry 自己管）。"""
    url = _tunnel_url_of(cf_log) or cur_url
    if url and url != cur_url:
        log(f"★ 隧道换名（quick tunnel 重连）：{url} —— 将重注册")
        cur_url = url
    reg.ensure(cur_url, push_token)
    return cur_url


def run_push_worker(cfg: dict[str, Any], log) -> int:
    """启动 worker_server + cloudflared 隧道，等 hub 推 job。返回码同 pull。

    bootstrap 语义：cell 已经从 hub /code 解包好代码（cfg["code_dir"]）——服务进程
    的 PYTHONPATH 指向它；job 真正执行用的是 hub 随 job 下发的 code.zip（worker_server
    收下后入 sys.path、在新进程里跑）。

    already_serving（push-first 升级后）：bootstrap 已起好 worker_server/cloudflared，
    本函数只做守候 + 日志，**不再**重复安装/拉起；此时**也要**注册 —— bootstrap 的内联
    注册只覆盖「服务已起、URL 已有」那一瞬，URL 换名/重启后的重注册住在这里。

    自动注册（2026-10-23，plan/push-mode-notebooks §5.2）：URL 拿到后 `Registry.ensure()`；
    退出（含 Ctrl-C / 会话到顶 / serve 早退）一律 `unregister()` ⇒ hub 上 `enabled=false`。
    """
    already = cfg.get("already_serving") or None
    work_dir = Path(str(cfg.get("work_dir") or "/tmp/remote-worker-serve"))
    work_dir.mkdir(parents=True, exist_ok=True)
    boot_dir = Path(str(cfg.get("code_dir") or "/tmp/worker-code"))
    if cfg.get("code_dir") and not (boot_dir / "remote").is_dir():
        # 2026-10-26 真机（battle.cloudflared push + source=github）：cell 把**仓库根**铺进
        # code_dir，而 `remote/` 实际住 `nn-training/` 下 ⇒ 子进程 `-m remote.remote_worker_serve`
        # 一条 ImportError，宿主要白等满 wait_ping 的 30s 才判死，日志只剩「serve 未就绪」。
        # code_dir 的契约只有一条：根 = `nn-training/` 的内容。不满足按配置致命处理（-2 = 不重启）。
        log(
            f"FATAL: code_dir={boot_dir} 下没有 remote/ 包（契约：根 = nn-training/ 的内容）"
            "—— 引导阶段把代码铺错层了，检查 cell 的代码来源"
        )
        return -2
    serve_log = work_dir / "serve.log"
    cf_log = work_dir / "cloudflared.log"
    reg = _push_registry(cfg, log)
    cfg["node_id"] = reg.node_id  # push-first 内联注册/升级后复用同一个 id
    push_tok = str(cfg.get("push_token") or "")
    # ★ 2026-10-26：训练侧放大（PPO 每轮更新遍数 × N）——它在**起服务时**定下，
    #   所以 push-first 升级前就得校验；非缺省才进 argv（旧 cfg 行为逐字不变）。
    epoch_mult = _epoch_mult_of(cfg)
    if epoch_mult != 1.0:
        log(f"训练放大：PPO epochs×{epoch_mult:g}（只改更新遍数；不改采样/评估）")

    if already:
        serve_pid = int(already.get("serve_pid") or 0)
        cf_pid = already.get("cf_pid")
        cf_url = str(already.get("cf_url") or "") or _tunnel_url_of(cf_log)
        log(f"接管已就绪 push 服务（serve_pid={serve_pid} tunnel={cf_url or '?'}）")
        _register_or_warn(reg, cf_url, push_tok, log)
        serve_tail_off = 0
        last_status_at = 0.0
        as_last_done = -1
        as_last_queued = -1
        as_last_busy: bool | None = None
        #: 同主分支：忙且过程日志还在流 ⇒ 不刷「守候中…」（见那里的注释）。
        last_serve_line_at = 0.0
        last_beat_at = 0.0
        deadline = time.time() + int(cfg["max_session_hours"]) * 3600
        t0 = time.time()

        def _drain() -> int:
            nonlocal serve_tail_off, last_serve_line_at
            try:
                size = serve_log.stat().st_size
            except OSError:
                return 0
            if size < serve_tail_off:
                serve_tail_off = 0
            if size <= serve_tail_off:
                return 0
            try:
                with open(serve_log, encoding="utf-8", errors="replace") as f:
                    f.seek(serve_tail_off)
                    chunk = f.read()
                serve_tail_off = size
            except OSError:
                return 0
            n = 0
            for line in chunk.splitlines():
                if line.strip():
                    log(f"[serve] {line.rstrip()}")
                    n += 1
            if n:
                last_serve_line_at = time.time()
            return n

        def _ping() -> dict | None:
            try:
                from urllib.request import Request, urlopen

                req = Request(
                    f"http://127.0.0.1:{cfg['push_port']}/ping",
                    headers={"Authorization": f"Bearer {cfg['push_token']}"},
                )
                with urlopen(req, timeout=4) as r:
                    obj = json.loads(r.read().decode("utf-8", "replace"))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        try:
            while True:
                if not _pid_alive(serve_pid):
                    _drain()
                    log(f"worker_server 退出 (pid {serve_pid})")
                    return 0
                if cf_pid and not _pid_alive(int(cf_pid)):
                    # 2026-10-26 真机：这里只 ping 127.0.0.1、只看 serve 存活 ⇒ 隧道死了完全看不出来
                    # （hub 侧是 Cloudflare 1033/530，本机日志一路「守候中」）。bootstrap 起的
                    # cloudflared 没了只能重开 cell（重建隧道 + 重注册），故按配置致命收摊。
                    _drain()
                    log(
                        f"⚠ cloudflared 已退出 (pid {cf_pid}) —— 隧道没了，hub 再也够不到本机"
                        "（本机只 ping 127.0.0.1，看不出隧道死活）⇒ 请重跑本 cell"
                    )
                    return -2
                _drain()
                now = time.time()
                if now - last_status_at >= 15:
                    cf_url = _refresh_registration(reg, cf_log, cf_url, push_tok, log)
                    st = _ping()
                    if st is not None:
                        busy = bool(st.get("busy"))
                        queued = int(st.get("queued") or 0)
                        done = int(st.get("done") or 0)
                        if done != as_last_done or busy != as_last_busy or queued != as_last_queued:
                            log(
                                f"worker 状态: busy={busy} queued={queued} done={done}"
                                + ("（在跑 job）" if busy else "（空闲，等 hub 推送）")
                            )
                            as_last_done, as_last_busy, as_last_queued = done, busy, queued
                        elif busy and now - last_serve_line_at < _SERVE_QUIET_SEC:
                            pass  # 过程日志还在流 ⇒ 不刷「守候中…」（同主分支口径）
                        elif now - last_beat_at >= _HEARTBEAT_SEC:
                            last_beat_at = now
                            log(
                                f"守候中… busy={busy} queued={queued} done={done}"
                                f" wait={int(now - t0)}s"
                            )
                    else:
                        log("worker /ping 暂不可达（瞬断/重启中）——继续守候")
                    last_status_at = now
                if time.time() > deadline:
                    log(f"会话到顶 ({cfg['max_session_hours']}h)——干净收摊")
                    return 0
                time.sleep(5)
        except KeyboardInterrupt:
            log("收到中断")
            if _pid_alive(serve_pid):
                try:
                    os.kill(serve_pid, 15)
                except OSError:
                    pass
            if cf_pid:
                try:
                    os.kill(int(cf_pid), 15)
                except OSError:
                    pass
            return 0
        finally:
            # 会话收线：hub 上 enabled=false（下个会话注册时会恢复）。
            reg.unregister()

    # ── cloudflared 查找 / 自动安装（与离线 push 同一条实现：`remote/push_tunnel`）──
    cf_bin = find_cloudflared(str(cfg.get("cloudflared_path") or ""), log=log)
    if not cf_bin:
        return -2

    # ── 启动 worker_server ──
    serve_env = dict(os.environ)
    serve_env["PYTHONPATH"] = (str(boot_dir) + os.pathsep + serve_env.get("PYTHONPATH", "")).rstrip(os.pathsep)
    # 结果就绪通知的目标（与注册同址）；缺 hub 地址 ⇒ 不传，服务端回落纯轮询。
    hub_base = _hub_base_of(cfg)
    with open(serve_log, "w") as log_f:
        serve_argv = [sys.executable, "-u", "-m", "remote.remote_worker_serve",
                      "--port", str(cfg["push_port"]), "--token", str(cfg["push_token"]),
                      "--work", str(work_dir), "--device", str(cfg["device_resolved"])]
        if epoch_mult != 1.0:
            serve_argv += ["--epoch-mult", f"{epoch_mult:g}"]
        if hub_base:
            serve_argv += ["--hub-url", hub_base]
        serve_proc = subprocess.Popen(
            serve_argv,
            stdout=log_f, stderr=subprocess.STDOUT, env=serve_env,
        )
    log(f"worker_server 启动 (PID {serve_proc.pid})，等就绪…")

    from urllib.request import Request, urlopen

    def _ping_ok() -> bool:
        try:
            req = Request(
                f"http://127.0.0.1:{cfg['push_port']}/ping",
                headers={"Authorization": f"Bearer {cfg['push_token']}"},
            )
            with urlopen(req, timeout=5) as r:
                return bool(r.status == 200)
        except Exception:
            return False

    t0 = time.time()
    while time.time() - t0 < 30 and not _ping_ok():
        time.sleep(1)
    if not _ping_ok():
        log(f"worker_server 30s 未就绪——查 {serve_log}")
        serve_proc.kill()
        return -1
    log("worker_server 就绪")

    # ── cloudflared 隧道 ──
    cf_proc, cf_url = start_quick_tunnel(
        cf_bin, int(cfg["push_port"]), cf_log, wait_sec=60, log=log
    )
    if cf_url:
        log(f"★ 隧道 URL: {cf_url}")
        _register_or_warn(reg, cf_url, push_tok, log)
    else:
        log(f"⚠ 隧道 URL 未取得（超时/出错）——hub 推送将不可达；日志见 {cf_log}")

    # ── 等待 job（会话上限内守着 + 持续日志，对齐 pull 模式可观测性）──
    # 历史：隧道 URL 打完后只剩 sleep(30)——notebook cell 上看起来「挂死」。
    # 现在：① 转发 serve.log 新行（job 受理/执行/完成都在里面，worker_server 已带时间戳）
    #       ② 周期 /ping 状态（busy/queued/done）③ 空闲心跳，与 pull 的轮询日志同节奏。
    deadline = time.time() + int(cfg["max_session_hours"]) * 3600
    t_wait0 = time.time()
    serve_tail_off = 0
    last_status_at = 0.0
    last_done = -1
    #: `queued` 也按**变化**报（原来是 `queued > 0` 就报 ⇒ push_ahead 预推一份时每拍一条，同族刷屏）。
    last_queued = -1
    last_busy: bool | None = None
    #: 最近一次转发 serve.log 的时刻（2026-10-26 用户口径：PPO 在跑时**过程日志已经在刷**，
    #: 别在下面再叠一行「守候中… busy=True」把现场淹掉）。反过来它也是判据：忙但过程日志
    #: **静默够久**（`_SERVE_QUIET_SEC`）时心跳照打，别把「卡住」当成正常。
    last_serve_line_at = 0.0
    #: 上一行心跳的时刻（`_HEARTBEAT_SEC` 限流；0 ⇒ 第一拍就来一条，别让人干等三分钟）。
    last_beat_at = 0.0

    def _drain_serve_log() -> int:
        nonlocal serve_tail_off, last_serve_line_at
        try:
            size = serve_log.stat().st_size
        except OSError:
            return 0
        if size < serve_tail_off:
            serve_tail_off = 0  # 日志被截断/重开
        if size <= serve_tail_off:
            return 0
        try:
            with open(serve_log, encoding="utf-8", errors="replace") as f:
                f.seek(serve_tail_off)
                chunk = f.read()
            serve_tail_off = size
        except OSError:
            return 0
        n = 0
        for line in chunk.splitlines():
            line = line.rstrip()
            if line:
                log(f"[serve] {line}")
                n += 1
        if n:
            last_serve_line_at = time.time()
        return n

    def _ping_status() -> dict | None:
        try:
            req = Request(
                f"http://127.0.0.1:{cfg['push_port']}/ping",
                headers={"Authorization": f"Bearer {cfg['push_token']}"},
            )
            with urlopen(req, timeout=4) as r:
                body = r.read()
            import json as _json

            obj = _json.loads(body.decode("utf-8", "replace"))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    log("进入推送守候（转发 serve.log + /ping 状态；中断本 cell 停机）")
    try:
        while True:
            if serve_proc.poll() is not None:
                _drain_serve_log()
                log(f"worker_server 退出 (code {serve_proc.returncode})")
                return serve_proc.returncode or 0
            if cf_proc is not None and cf_proc.poll() is not None:
                # 2026-10-26 真机（kaggleO）：cloudflared 死了而 cell 一路打「守候中 busy=False」——
                # 本地 /ping 全绿、hub 侧却是 Cloudflare 1033/530，且 `_refresh_registration` 只在
                # **URL 换名**时才重注册（进程死了日志不动 ⇒ 永远是那条死 URL）。
                # 返回 -1 ⇒ `run_notebook` 整体重启本 runner：换新隧道 + 重新注册。
                log(
                    f"⚠ cloudflared 已退出 (code {cf_proc.returncode}) —— 隧道没了，hub 再也够不到本机"
                    "；整体重启（换一条隧道并重注册）"
                )
                return -1
            _drain_serve_log()
            now = time.time()
            if now - last_status_at >= 15:
                cf_url = _refresh_registration(reg, cf_log, cf_url or "", push_tok, log)
                st = _ping_status()
                if st is not None:
                    busy = bool(st.get("busy"))
                    queued = int(st.get("queued") or 0)
                    done = int(st.get("done") or 0)
                    if done != last_done or busy != last_busy or queued != last_queued:
                        log(
                            f"worker 状态: busy={busy} queued={queued} done={done}"
                            + ("（在跑 job）" if busy else "（空闲，等 hub 推送）")
                        )
                        last_done, last_busy, last_queued = done, busy, queued
                    elif busy and now - last_serve_line_at < _SERVE_QUIET_SEC:
                        # PPO 在跑且过程日志还在流 ⇒ 不刷「守候中…」（2026-10-26 用户口径）。
                        pass
                    elif now - last_beat_at >= _HEARTBEAT_SEC:
                        # 没过程日志可转（空闲 / 忙但静默）⇒ 心跳限流到三分钟一条。
                        last_beat_at = now
                        log(
                            f"守候中… busy={busy} queued={queued} done={done}"
                            f" wait={int(now - t_wait0)}s"
                        )
                else:
                    log("worker /ping 暂不可达（瞬断/重启中）——继续守候")
                last_status_at = now
            if time.time() > deadline:
                log(f"会话到顶 ({cfg['max_session_hours']}h)——干净收摊")
                return 0
            time.sleep(5)
    except KeyboardInterrupt:
        log("收到中断")
        return 0
    finally:
        reg.unregister()
        if cf_proc is not None and cf_proc.poll() is None:
            cf_proc.kill()
            log("cloudflared 已停")
        if serve_proc.poll() is None:
            serve_proc.kill()
            log("worker_server 已停")


# ════════════════════════ 入口：banner → runner → 崩溃退避重启 ════════════════════════


def run_notebook(cfg: dict[str, Any]) -> int:
    """cell 委托入口：设备解析 → runner（pull/push）→ 崩溃退避重启 → 保活停机。

    返回最终 rc（0 = 干净；-2 = 配置致命）。KeyboardInterrupt 穿透到 cell
    （interrupt = 停机），由调用方 kernel 收尾——子进程由各 runner 的 finally 回收。
    """
    log = cfg.get("log") or _log_default
    keep_stop = cfg.get("keepalive_stop")
    code_dir = cfg.get("code_dir")
    if code_dir:
        log(f"运行时代码: {code_dir}")
    # 已解析则复用：push-first 的 cell 会在 **spawn worker_server 之前**先解析一次
    # （spawn 走的是 `--device`，不先解析就会把字面量 "auto" 传给 worker —— 2026-09-15 事故）。
    # 复用同时保证：① 设备探测只跑一次、日志只出一份；② resolve_device 在 TPU 分支打的
    # PJRT_DEVICE 环境位发生在 spawn **之前**，能被 worker 子进程继承。
    if not cfg.get("device_resolved"):
        cfg["device_resolved"] = resolve_device(cfg, log)
    t_session = time.time()
    rc = 0
    print("\n" + "=" * 62)
    _em = _epoch_mult_of(cfg)
    print(f"  Battle GPU Worker | mode={cfg['mode']} device={cfg.get('device_resolved')} "
          f"backend={cfg.get('backend_resolved') or '?'} "
          f"| 任务类型 ppo+bc" + (f" | PPO epochs×{_em:g}" if _em != 1.0 else ""))
    print(f"  会话上限 {cfg['max_session_hours']}h | 停止 = 中断本 cell")
    print("=" * 62 + "\n")
    try:
        attempt = 0
        while True:
            if cfg["mode"] == "pull":
                rc = run_pull_worker(cfg, log)
            elif cfg["mode"] == "push":
                rc = run_push_worker(cfg, log)
            else:
                log(f"未知 mode={cfg['mode']!r}（可选 'pull' / 'push'）")
                rc = -2
            if rc in (0, -2):
                break  # 干净退出 / 配置致命（token/URL 错——重启无意义）
            attempt += 1
            if attempt > int(cfg["max_worker_restarts"]):
                log(f"FATAL: worker 连续失败 {attempt} 次（rc={rc}）——放弃自动重启")
                break
            backoff = min(30 * attempt, 300)
            log(f"worker 异常退出 (rc={rc})——{backoff}s 后第 {attempt}/{cfg['max_worker_restarts']} 次自动重启")
            time.sleep(backoff)
        return rc
    finally:
        if keep_stop is not None:
            keep_stop.set()
        elapsed = (time.time() - t_session) / 60
        log(f"会话结束: rc={rc}, 时长 {elapsed:.1f} min")


def sha12(raw: bytes) -> str:
    """code.zip 前 12 位 sha256（cell 引导日志用；与 hub 日志对账）。"""
    return hashlib.sha256(raw).hexdigest()[:12]
