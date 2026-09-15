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
  code_dir (已解包的 code.zip 目录，push 模式起服务用) ·
  work_dir (可选覆盖 /tmp 工作目录——单测注入 tmp_path 用)

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


def run_pull_worker(cfg: dict[str, Any], log) -> int:
    """连接 hub 轮询领 job（PPO 与 BC 同 worker）。返回退出码：
    0 = 干净退出（空闲满/会话到顶）；-2 = 配置致命（不重启）；其它非 0 = 可重启的失败。"""
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    hub_url = str(cfg["hub_url"])
    hub_token = str(cfg["hub_token"])
    # work_dir 可被 cfg 覆盖（单测注入 tmp 目录；缺省 /tmp——Kaggle/Colab 语义）
    work_dir = Path(str(cfg.get("work_dir") or "/tmp/remote-worker"))
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
    收下后入 sys.path、在新进程里跑）。

    already_serving（push-first 升级后）：bootstrap 已起好 worker_server/cloudflared，
    本函数只做守候 + 日志，**不再**重复安装/拉起。"""
    already = cfg.get("already_serving") or None
    work_dir = Path(str(cfg.get("work_dir") or "/tmp/remote-worker-serve"))
    work_dir.mkdir(parents=True, exist_ok=True)
    boot_dir = Path(str(cfg.get("code_dir") or "/tmp/worker-code"))
    serve_log = work_dir / "serve.log"

    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    if already:
        serve_pid = int(already.get("serve_pid") or 0)
        cf_pid = already.get("cf_pid")
        cf_url = already.get("cf_url")
        log(f"接管已就绪 push 服务（serve_pid={serve_pid} tunnel={cf_url or '?'}）")
        serve_tail_off = 0
        last_status_at = 0.0
        as_last_done = -1
        as_last_busy: bool | None = None
        deadline = time.time() + int(cfg["max_session_hours"]) * 3600
        t0 = time.time()

        def _drain() -> None:
            nonlocal serve_tail_off
            try:
                size = serve_log.stat().st_size
            except OSError:
                return
            if size < serve_tail_off:
                serve_tail_off = 0
            if size <= serve_tail_off:
                return
            try:
                with open(serve_log, encoding="utf-8", errors="replace") as f:
                    f.seek(serve_tail_off)
                    chunk = f.read()
                serve_tail_off = size
            except OSError:
                return
            for line in chunk.splitlines():
                if line.strip():
                    log(f"[serve] {line.rstrip()}")

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
                _drain()
                now = time.time()
                if now - last_status_at >= 15:
                    st = _ping()
                    if st is not None:
                        busy = bool(st.get("busy"))
                        queued = int(st.get("queued") or 0)
                        done = int(st.get("done") or 0)
                        if done != as_last_done or busy != as_last_busy or queued > 0:
                            log(
                                f"worker 状态: busy={busy} queued={queued} done={done}"
                                + ("（在跑 job）" if busy else "（空闲，等 hub 推送）")
                            )
                            as_last_done, as_last_busy = done, busy
                        else:
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

    # ── 等待 job（会话上限内守着 + 持续日志，对齐 pull 模式可观测性）──
    # 历史：隧道 URL 打完后只剩 sleep(30)——notebook cell 上看起来「挂死」。
    # 现在：① 转发 serve.log 新行（job 受理/执行/完成都在里面，worker_server 已带时间戳）
    #       ② 周期 /ping 状态（busy/queued/done）③ 空闲心跳，与 pull 的轮询日志同节奏。
    deadline = time.time() + int(cfg["max_session_hours"]) * 3600
    t_wait0 = time.time()
    serve_tail_off = 0
    last_status_at = 0.0
    last_done = -1
    last_busy: bool | None = None

    def _drain_serve_log() -> None:
        nonlocal serve_tail_off
        try:
            size = serve_log.stat().st_size
        except OSError:
            return
        if size < serve_tail_off:
            serve_tail_off = 0  # 日志被截断/重开
        if size <= serve_tail_off:
            return
        try:
            with open(serve_log, encoding="utf-8", errors="replace") as f:
                f.seek(serve_tail_off)
                chunk = f.read()
            serve_tail_off = size
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.rstrip()
            if line:
                log(f"[serve] {line}")

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
            _drain_serve_log()
            now = time.time()
            if now - last_status_at >= 15:
                st = _ping_status()
                if st is not None:
                    busy = bool(st.get("busy"))
                    queued = int(st.get("queued") or 0)
                    done = int(st.get("done") or 0)
                    if done != last_done or busy != last_busy or queued > 0:
                        log(
                            f"worker 状态: busy={busy} queued={queued} done={done}"
                            + ("（在跑 job）" if busy else "（空闲，等 hub 推送）")
                        )
                        last_done, last_busy = done, busy
                    else:
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
    # 已解析则复用：push-first 的 cell 会在 **spawn worker_server 之前**先解析一次
    # （spawn 走的是 `--device`，不先解析就会把字面量 "auto" 传给 worker —— 2026-09-15 事故）。
    # 复用同时保证：① 设备探测只跑一次、日志只出一份；② resolve_device 在 TPU 分支打的
    # PJRT_DEVICE 环境位发生在 spawn **之前**，能被 worker 子进程继承。
    if not cfg.get("device_resolved"):
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
