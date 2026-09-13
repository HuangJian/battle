"""remote/notebook_runtime.py — 云端 notebook（battle-rl.ipynb）的运行时逻辑。

为什么在 code.zip 里（2026-09-13 单 cell 重构）：notebook cell 只留「参数 + 保活 +
/code 引导」，设备探测 / pull / push / 崩溃退避重启全部住在本模块——随 hub 下发的
code.zip 走，修运行时逻辑不用重发 notebook（重跑 cell 即拉到新版）。cell 端契约：
先把解包目录插进 sys.path，再 `from remote.notebook_runtime import run_notebook`。

cfg 键（cell 的 CFG dict）：
  mode ("pull"|"push") · hub_url · hub_token · push_port · push_token ·
  cloudflared_path · device ("auto"|cuda|cuda-dp|tpu|cpu) · use_multi_gpu ·
  max_session_hours · poll_interval_sec · idle_floor_sec · max_worker_restarts ·
  keepalive_stop (threading.Event，cell 的保活线程停机柄) · log (callable) ·
  code_dir (已解包的 code.zip 目录，push 模式起服务用)

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
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# cell 端 /code 引导（fetch+unpack+sys.path）已完成；本模块只管运行时。


def _log_default(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [battle-rl] {msg}", flush=True)


# ════════════════════════ 设备探测：CUDA → TPU → CPU ════════════════════════


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


def resolve_device(cfg: dict[str, Any], log) -> str:
    """设备解析：显式指定优先；auto = CUDA（多卡按 use_multi_gpu）→ TPU → CPU。"""
    log(f"设备探测中（device={cfg['device']}）…")
    try:
        import torch
    except ImportError:
        log("torch 不可用 → CPU（job 执行会失败——Kaggle/Colab 请选 GPU/TPU 运行时）")
        return "cpu"

    if torch.cuda.is_available():
        n_gpu = torch.cuda.device_count()
        log(f"torch {torch.__version__}, CUDA 可见 {n_gpu} 张 GPU")
        for i in range(n_gpu):
            log(f"  [{i}] {torch.cuda.get_device_name(i)}")
        if cfg["device"] not in ("auto", "cuda", "cuda-dp"):
            log(f"  显式指定 {cfg['device']} 与 CUDA 环境不符——按指定执行")
            return str(cfg["device"])
        if cfg["use_multi_gpu"] and n_gpu > 1:
            log(f"  -> DataParallel 跨 {n_gpu} 卡（梯度归约顺序变化，与单卡 run 数值不可逐位比）")
            return "cuda-dp"
        if n_gpu > 1:
            log("  -> 只用第 0 张卡（要跨卡把 use_multi_gpu 改 True）")
        return "cuda"

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


def run_pull_worker(cfg: dict[str, Any], log) -> int:
    """连接 hub 轮询领 job（PPO 与 BC 同 worker）。返回退出码：
    0 = 干净退出（空闲满/会话到顶）；-2 = 配置致命（不重启）；其它非 0 = 可重启的失败。"""
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    hub_url = str(cfg["hub_url"])
    hub_token = str(cfg["hub_token"])
    work_dir = Path("/tmp/remote-worker")
    work_dir.mkdir(parents=True, exist_ok=True)

    log(f"连接 hub: {hub_url}（/ping 探测…）")
    try:
        req = Request(f"{hub_url.rstrip('/')}/ping", headers={"Authorization": f"Bearer {hub_token}"})
        with urlopen(req, timeout=15) as resp:
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
    token_file = work_dir / "hub.token"
    token_file.write_text(hub_token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except Exception:
        pass
    max_idle = max(int(cfg["idle_floor_sec"]), (int(cfg["max_session_hours"]) - 1) * 3600)
    restart_argv = [
        "--poll", hub_url,
        "--token-file", str(token_file),
        "--out", str(work_dir),
        "--device", str(cfg["device_resolved"]),
        "--threads", "0",
        "--poll-sec", str(cfg["poll_interval_sec"]),
        "--max-idle-sec", str(max_idle),
    ]
    log(f"worker 启动（max_idle={max_idle}s, poll={cfg['poll_interval_sec']}s, device={cfg['device_resolved']}）")
    try:
        return supervise_worker(restart_argv)
    except KeyboardInterrupt:
        log("收到中断——worker 已终止")
        return 0


# ════════════════════════ Push 模式 runner ════════════════════════


def run_push_worker(cfg: dict[str, Any], log) -> int:
    """启动 worker_server + cloudflared 隧道，等 hub 推 job。返回码同 pull。

    bootstrap 语义：cell 已经从 hub /code 解包好代码（cfg["code_dir"]）——服务进程
    的 PYTHONPATH 指向它；job 真正执行用的是 hub 随 job 下发的 code.zip（worker_server
    收下后入 sys.path、在新进程里跑）。"""
    work_dir = Path("/tmp/remote-worker-serve")
    work_dir.mkdir(parents=True, exist_ok=True)
    boot_dir = Path(str(cfg.get("code_dir") or "/tmp/worker-code"))

    # ── cloudflared 查找 / 自动安装 ──
    cf_bin = str(cfg.get("cloudflared_path") or "") or subprocess.getoutput(
        "where cloudflared 2>nul || which cloudflared 2>/dev/null"
    ).strip()
    if not cf_bin:
        log("cloudflared 未找到，自动安装…")
        try:
            cf_bin = "/usr/local/bin/cloudflared"
            subprocess.run(
                ["curl", "-fsSL",
                 "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                 "-o", cf_bin],
                check=True, timeout=60,
            )
            os.chmod(cf_bin, 0o755)
            log(f"cloudflared 已安装 -> {cf_bin}")
        except Exception as e:
            log(f"cloudflared 自动安装失败: {e}——push 模式需要它暴露端口；或改用 pull 模式")
            return -2

    # ── 启动 worker_server ──
    serve_log = work_dir / "serve.log"
    serve_env = dict(os.environ)
    serve_env["PYTHONPATH"] = (str(boot_dir) + os.pathsep + serve_env.get("PYTHONPATH", "")).rstrip(os.pathsep)
    with open(serve_log, "w") as log_f:
        serve_proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "remote_worker_serve",
             "--port", str(cfg["push_port"]), "--token", str(cfg["push_token"]),
             "--work", str(work_dir), "--device", str(cfg["device_resolved"])],
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
    cf_log = work_dir / "cloudflared.log"
    with open(cf_log, "w") as log_f:
        cf_proc = subprocess.Popen(
            [cf_bin, "tunnel", "--url", f"http://localhost:{cfg['push_port']}", "--logfile", str(cf_log)],
            stdout=log_f, stderr=subprocess.STDOUT,
        )
    cf_url = None
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            urls = re.findall(
                r"https://[a-z0-9-]+\.trycloudflare\.com",
                cf_log.read_text(encoding="utf-8", errors="replace"),
            )
            if urls:
                cf_url = urls[-1]
                break
        except Exception:
            pass
        if cf_proc.poll() is not None:
            log(f"cloudflared 提前退出 (code {cf_proc.returncode})")
            break
        time.sleep(2)
    if cf_url:
        log(f"★ 隧道 URL: {cf_url}")
        log("把它配到 hub 侧 rl-config 对应节点的 url（gpu_push 节点），job 会自动推送至此")
    else:
        log(f"⚠ 隧道 URL 未取得（超时/出错）——hub 推送将不可达；日志见 {cf_log}")

    # ── 等待 job（会话上限内守着）──
    deadline = time.time() + int(cfg["max_session_hours"]) * 3600
    try:
        while True:
            if serve_proc.poll() is not None:
                log(f"worker_server 退出 (code {serve_proc.returncode})")
                return serve_proc.returncode or 0
            if time.time() > deadline:
                log(f"会话到顶 ({cfg['max_session_hours']}h)——干净收摊")
                return 0
            time.sleep(30)
    except KeyboardInterrupt:
        log("收到中断")
        return 0
    finally:
        if cf_proc.poll() is None:
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
    cfg["device_resolved"] = resolve_device(cfg, log)
    t_session = time.time()
    rc = 0
    print("\n" + "=" * 62)
    print(f"  Battle GPU Worker | mode={cfg['mode']} device={cfg.get('device_resolved')} "
          f"| 任务类型 ppo+bc")
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
