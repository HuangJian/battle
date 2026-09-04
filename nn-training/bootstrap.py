#!/usr/bin/env python3
"""bootstrap.py — 一条指令搭起 nn-training 的 Python 训练环境。

**只用标准库**（不 import torch/numpy），因此在任何 3.10+ 解释器上都能跑 ——
包括"venv 里还没有 torch"的冷启动场景，这正是本脚本存在的意义。

    python bootstrap.py                 # 探测 → 建环境 → 装 → 自检 → 落档案
    python bootstrap.py --check         # 只体检，输出机器档案，不改任何东西
    python bootstrap.py --recreate      # 强制重建 venv
    python bootstrap.py --variant cu128 # 跳过探测，显式指定 torch 变体
    python bootstrap.py --no-install-uv # 不允许自动装 uv（缺则只给安装提示）

为什么需要它（背景）：
  torch 的 CPU / CUDA / ROCm / XPU 构建分布在不同的 index 上，且编码在 local
  version 里（`2.7.1+cu128`）。PyPI 上的默认 torch 是 CUDA 构建，无 GPU 的机器
  装它会白下 ~3GB 的 nvidia-* 依赖；反过来，有 GPU 的机器装 CPU 构建又用不上显卡。
  本脚本在装之前先探测硬件，再把结果翻译成 uv 的 dependency-group 名。

设计参见 plan/python-env-bootstrap-and-device.md §3.2 / §3.3。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# 变体名 ↔ uv dependency-group 名。
# 清单是 2026-09-04 实测 download.pytorch.org 得出的（针对 torch 2.7.1）：
#   cpu / cu118 / cu126 / cu128 / xpu → Windows + Linux 有构建
#   rocm                              → 仅 Linux
#   cu124 / cu130                     → 无 2.7.1 构建（属其它 torch 版本），不设
# 改 torch 版本时必须重测这张表，不要凭记忆改。
VARIANT_GROUPS: dict[str, str] = {
    "cpu": "torch-cpu",
    "cu118": "torch-cu118",
    "cu126": "torch-cu126",
    "cu128": "torch-cu128",
    "rocm": "torch-rocm",
    "xpu": "torch-xpu",
}

# CUDA driver runtime → 该 driver 能跑的最高 CUDA 变体（降序匹配，取第一个满足的）。
# 注意：cu130 不在表里，因为 torch 2.7.1 没有 cu130 构建 —— 高版本 driver 向下兼容，
# 落到 cu128 完全可用。
CUDA_LADDER: list[tuple[tuple[int, int], str]] = [
    ((12, 8), "cu128"),
    ((12, 6), "cu126"),
    ((11, 8), "cu118"),
]


# ---------------------------------------------------------------------------
# 进程 / 平台工具
# ---------------------------------------------------------------------------


def _popen_kwargs(**extra: Any) -> dict[str, Any]:
    """Windows 下隐藏子进程控制台窗口（避免黑窗抢焦点）。"""
    kw: dict[str, Any] = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    kw.update(extra)
    return kw


def run(cmd: list[str], *, timeout: int = 120, check: bool = False) -> tuple[int, str, str]:
    """跑一条命令，返回 (returncode, stdout, stderr)。超时/异常不算崩溃。"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            **_popen_kwargs(),
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except (OSError, subprocess.SubprocessError) as e:  # 命令不存在 / 超时
        return 127, "", f"{type(e).__name__}: {e}"


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ---------------------------------------------------------------------------
# 硬件探测（纯函数，可离线表驱动测试）
# ---------------------------------------------------------------------------


def cuda_runtime_from_nvidia_smi(run_fn: Callable[..., tuple[int, str, str]] = run) -> str | None:
    """`nvidia-smi` 输出里的 "CUDA Version: X.Y" = 该 driver 支持的最高 CUDA runtime。

    拿不到（没装 / 没卡 / 输出异常）就返回 None，由调用方降级。
    """
    rc, out, _err = run_fn(["nvidia-smi", "-L"], timeout=30)
    if rc != 0 or not out.strip():
        return None
    rc2, out2, _err2 = run_fn(["nvidia-smi"], timeout=30)
    if rc2 != 0:
        return None
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out2)
    if not m:
        return None
    return f"{int(m.group(1))}.{int(m.group(2))}"


def _parse_cuda_ver(v: str) -> tuple[int, int]:
    a, _, b = v.partition(".")
    return int(a), int(b or "0")


def cuda_variant_for_runtime(runtime: str | None) -> str | None:
    """driver 支持的最高 runtime → 可用的最高 CUDA 变体。None = 没有可用 CUDA 变体。"""
    if not runtime:
        return None
    try:
        rv = _parse_cuda_ver(runtime)
    except ValueError:
        return None
    for min_ver, variant in CUDA_LADDER:
        if rv >= min_ver:
            return variant
    return None


def detect_gpu(
    system: str | None = None,
    run_fn: Callable[..., tuple[int, str, str]] = run,
) -> dict[str, Any]:
    """探测本机可用加速后端。返回 {backend, cuda_runtime, detail}。

    backend: "cuda" | "rocm" | "xpu" | "mps" | "none"
    **只判断"有硬件 + 有驱动"，不判断 torch 是否真的能用** —— 后者由装后自检负责
    （驱动版本与 wheel 的 CUDA 版本不匹配时，is_available() 会静默返回 False，
     只有真跑一次 matmul 才暴露）。
    """
    system = system or platform.system()
    detail: list[str] = []

    if system == "Darwin":
        # macOS 无 NVIDIA / ROCm 构建；Apple Silicon 的 MPS 由 PyPI 原生 wheel 提供，
        # 不需要特殊 index —— 所以这里永远返回 cpu 变体。
        if platform.machine() in ("arm64", "aarch64"):
            return {"backend": "mps", "cuda_runtime": None, "detail": "Apple Silicon (MPS)"}
        return {"backend": "none", "cuda_runtime": None, "detail": "Intel Mac — no accelerator"}

    # NVIDIA
    runtime = cuda_runtime_from_nvidia_smi(run_fn)
    if runtime:
        detail.append(f"nvidia-smi CUDA Version {runtime}")
        return {"backend": "cuda", "cuda_runtime": runtime, "detail": "; ".join(detail)}

    # AMD ROCm（Linux only）
    if system == "Linux" and (Path("/dev/kfd").exists() or _has("rocminfo")):
        detail.append("/dev/kfd or rocminfo present")
        return {"backend": "rocm", "cuda_runtime": None, "detail": "; ".join(detail)}

    # Intel XPU（Win/Linux）
    if system in ("Linux", "Windows") and (_has("sycl-ls") or _has("sycl-ls.exe")):
        detail.append("sycl-ls present")
        return {"backend": "xpu", "cuda_runtime": None, "detail": "; ".join(detail)}

    return {"backend": "none", "cuda_runtime": None, "detail": "no accelerator detected"}


def pick_variant(
    system: str,
    gpu: dict[str, Any],
    requested: str | None = None,
) -> tuple[str, str]:
    """变体选择：显式指定优先，否则按探测结果。返回 (variant, reason)。

    保证返回的变体在 VARIANT_GROUPS 里 —— 探测结果不可用时一律降级到 cpu，
    绝不返回一个装不上的名字。
    """
    if requested:
        if requested not in VARIANT_GROUPS:
            raise SystemExit(
                f"[bootstrap] 未知变体 {requested!r}。可选：{', '.join(sorted(VARIANT_GROUPS))}"
            )
        return requested, "显式指定"

    backend = gpu["backend"]

    if backend == "cuda":
        v = cuda_variant_for_runtime(gpu.get("cuda_runtime"))
        if v:
            return v, f"NVIDIA GPU，driver 支持 CUDA {gpu['cuda_runtime']}"
        return "cpu", "检测到 NVIDIA 硬件但拿不到可用 CUDA runtime，降级 CPU"

    if backend == "rocm":
        if system == "Linux":
            return "rocm", "AMD GPU + ROCm（仅 Linux）"
        return "cpu", "ROCm 无 Windows 构建，降级 CPU"

    if backend == "xpu":
        if system in ("Linux", "Windows"):
            return "xpu", "Intel GPU（XPU）"
        return "cpu", "XPU 无 macOS 构建，降级 CPU"

    if backend == "mps":
        # Apple Silicon：MPS 内置在 PyPI 原生 wheel 里，用 cpu 变体即可拿到。
        return "cpu", "Apple Silicon — MPS 由 PyPI 原生 wheel 提供，用 cpu 变体"

    return "cpu", "未检测到加速器"


def sync_cmd(variant: str, *, frozen: bool = False) -> list[str]:
    """变体 → uv 命令。

    CPU 走 default-groups（裸 `uv sync` 即可）；GPU 变体必须关掉默认组再指定，
    否则 uv 会因 conflicts 明确报错（这是好事 —— 不会静默装两个 torch）。
    """
    group = VARIANT_GROUPS[variant]
    cmd = ["uv", "sync"]
    if variant != "cpu":
        cmd += ["--no-default-groups", "--group", "dev", "--group", group]
    if frozen:
        cmd.append("--frozen")
    return cmd


# ---------------------------------------------------------------------------
# 环境搭建
# ---------------------------------------------------------------------------


def find_uv() -> str | None:
    return shutil.which("uv") or shutil.which("uv.exe")


def install_uv(system: str) -> str | None:
    """尝试装 uv。返回可执行文件路径，失败返回 None。

    网络拉取第三方安装脚本会写 PATH —— 默认允许（否则称不上"一条指令"），
    但日志会明说在做什么，可用 --no-install-uv 关闭。
    """
    if system == "Windows":
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "ByPass",
            "-Command",
            "irm https://astral.sh/uv/install.ps1 | iex",
        ]
    else:
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
    rc, out, err = run(cmd, timeout=300)
    if rc == 0:
        # 安装器把 uv 放到 ~/.local/bin 或 %USERPROFILE%\.local\bin，当前 PATH 可能还没有
        for cand in (
            Path.home() / ".local" / "bin" / ("uv.exe" if system == "Windows" else "uv"),
            Path.home() / ".cargo" / "bin" / ("uv.exe" if system == "Windows" else "uv"),
        ):
            if cand.exists():
                return str(cand)
        return find_uv()
    print(f"[bootstrap] uv 安装失败（rc={rc}）：{(err or out)[-500:]}")
    return None


def venv_python(system: str) -> Path:
    if system == "Windows":
        return HERE / ".venv" / "Scripts" / "python.exe"
    return HERE / ".venv" / "bin" / "python"


def venv_base_missing(vpy: Path) -> tuple[bool, str]:
    """venv 存在但它的 base interpreter 没了（pyenv 卸版本、managed python 被清理…）。

    这种情况 `uv sync` 救不回来，必须重建 —— 而没有任何默认检测会告诉你。
    """
    cfg = vpy.parent.parent / "pyvenv.cfg"
    if not cfg.exists():
        return False, ""
    home = None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("home"):
            home = line.split("=", 1)[1].strip()
            break
    if home and not Path(home).exists():
        return True, home
    if not vpy.exists():
        return True, str(vpy)
    return False, ""


# 装后自检：真跑一次 matmul + backward 并读回 CPU。
# `is_available()` 会撒谎（驱动/wheel 的 CUDA 版本不匹配时返回 True 却在真正
# 计算时炸，或反之返回 False 而驱动其实是好的）—— 只有真跑才算数。
PROBE_SRC = r"""
import json, sys
import torch

out = {
    "torch": torch.__version__,
    "torch_cuda": torch.version.cuda,
    "cuda_available": bool(torch.cuda.is_available()),
    "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
    "cuda_device": None,
    "mps_available": bool(getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)()),
    "xpu_available": bool(getattr(torch, "xpu", None) and torch.xpu.is_available()),
}

def smoke(dev):
    a = torch.randn(64, 64, device=dev, requires_grad=True)
    b = torch.randn(64, 64, device=dev)
    c = (a @ b).sum()
    c.backward()
    return float(c.detach().to("cpu"))

device, err = None, None
for cand in ["cuda", "xpu", "mps", "cpu"]:
    try:
        if cand == "cuda" and not out["cuda_available"]:
            continue
        if cand == "xpu" and not out["xpu_available"]:
            continue
        if cand == "mps" and not out["mps_available"]:
            continue
        smoke(cand)
        device, err = cand, None
        break
    except Exception as e:
        err = f"{cand}: {type(e).__name__}: {e}"
        continue

out["device"] = device
out["device_error"] = err
if device == "cuda":
    out["cuda_device"] = torch.cuda.get_device_name(0)
print("@@JSON@@" + json.dumps(out))
"""


def post_check(vpy: Path) -> dict[str, Any]:
    rc, out, err = run([str(vpy), "-c", PROBE_SRC], timeout=600)
    for line in out.splitlines():
        if line.startswith("@@JSON@@"):
            loaded: dict[str, Any] = json.loads(line[len("@@JSON@@") :])
            return loaded
    return {
        "torch": None,
        "device": None,
        "device_error": f"probe failed rc={rc}: {(err or out)[-800:]}",
    }


def cache_cross_fs_warning() -> str | None:
    """uv 缓存在别的盘 → 无法硬链接 → 大包（torch ~800MB）每次全拷贝，明显变慢。"""
    cache = os.environ.get("UV_CACHE_DIR")
    if cache:
        p = Path(cache)
    else:
        if sys.platform == "win32":
            p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "uv" / "cache"
        else:
            p = Path.home() / ".cache" / "uv"
    try:
        if p.resolve().drive and HERE.resolve().drive and p.resolve().drive != HERE.resolve().drive:
            return (
                f"uv 缓存 ({p}) 与项目 ({HERE}) 不在同一个盘 → 无法硬链接，"
                f"torch 这类大包每次安装走全拷贝（慢）。"
                f"建议：set UV_CACHE_DIR 到项目同盘，例如 {HERE / 'tmp' / '.uv-cache'}"
            )
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="nn-training 环境 bootstrap")
    ap.add_argument("--check", action="store_true", help="只体检，不改任何东西")
    ap.add_argument("--recreate", action="store_true", help="强制重建 venv")
    ap.add_argument("--variant", choices=sorted(VARIANT_GROUPS), help="跳过探测，显式指定变体")
    ap.add_argument("--python", help="指定解释器版本，如 3.12")
    ap.add_argument(
        "--no-install-uv", action="store_true", help="不允许自动安装 uv（缺则只给提示）"
    )
    args = ap.parse_args()

    system = platform.system()

    def log(*a: Any) -> None:
        print("[bootstrap]", *a, flush=True)

    gpu = detect_gpu(system)
    try:
        variant, reason = pick_variant(system, gpu, args.variant)
    except SystemExit as e:
        print(e)
        return 2

    log(f"平台      : {system} {platform.machine()} (python {platform.python_version()})")
    log(f"加速器    : {gpu['backend']} — {gpu['detail']}")
    log(f"torch 变体: {variant}  ({reason})")

    warn = cache_cross_fs_warning()
    if warn:
        log("⚠ " + warn)

    if args.check:
        vpy = venv_python(system)
        if not vpy.exists():
            log(f"环境未就绪：{vpy} 不存在。运行 `python bootstrap.py` 搭建。")
            return 1
        res = post_check(vpy)
        log(f"已装 torch : {res.get('torch')}")
        log(f"可用设备   : {res.get('device')}")
        if res.get("device_error"):
            log(f"⚠ 设备自检 : {res['device_error']}")
        log(
            json.dumps({"gpu": gpu, "variant": variant, "probe": res}, ensure_ascii=False, indent=2)
        )
        return 0 if res.get("torch") else 1

    # --- 环境健康度：venv 的 base interpreter 没了就重建 ---
    vpy = venv_python(system)
    if (args.recreate or venv_python_exists_but_broken(vpy)) and (HERE / ".venv").exists():
        log(f"删除失效的 .venv（base interpreter 已不存在）: {HERE / '.venv'}")
        shutil.rmtree(HERE / ".venv", ignore_errors=True)

    # --- 确保 uv ---
    uv = find_uv()
    if uv is None and not args.no_install_uv:
        log("未找到 uv，尝试安装…")
        uv = install_uv(system)
    if uv is None:
        print(
            "\n[bootstrap] 缺少 uv，且未自动安装成功。请手动执行：\n"
            "  Windows : powershell -ExecutionPolicy ByPass -c "
            '"irm https://astral.sh/uv/install.ps1 | iex"\n'
            "  Unix    : curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            "装好后重跑 `python bootstrap.py`（或加 --no-install-uv 跳过自动安装）。"
        )
        return 3

    # --- 同步依赖 ---
    cmd = sync_cmd(variant)
    # 冷机器路径：uv 刚被 install_uv() 装到 ~/.local/bin，**当前会话的 PATH 还没有
    # 它**（安装器只写注册表/未来 shell 的 PATH）。裸 `uv` 会 "command not found"。
    # find_uv/install_uv 返回的是绝对路径（含目录分隔符）时就整体换掉 cmd[0]。
    if uv and ("/" in uv or "\\" in uv):
        cmd[0] = uv
    if args.python:
        cmd += ["--python", args.python]
    log("执行: " + " ".join(cmd))
    t0 = time.time()
    rc, out, err = run(cmd, timeout=1800)
    if out.strip():
        print(out.rstrip())
    if rc != 0:
        print(err.rstrip())
        log(
            f"uv sync 失败（rc={rc}）。若报 index 解析错误，检查网络能否访问 "
            f"download.pytorch.org，或用 --variant cpu 强制 CPU 变体。"
        )
        return 4
    log(f"uv sync 完成，用时 {time.time() - t0:.1f}s")

    # --- 装后自检 ---
    vpy = venv_python(system)
    if not vpy.exists():
        log(f"ERROR: 同步后仍找不到 {vpy}")
        return 5
    res = post_check(vpy)

    # --- 自愈：半截安装 ---
    # uv 判断"已安装"看的是 site-packages 里的目录是否存在，而 dist-info 是最后才写的。
    # 上一次 sync 被 Ctrl-C / 断电打断时，会留下一个有 torch/ 却没有 __init__.py 的
    # 残骸 —— 之后每次 `uv sync` 都认为装好了、直接跳过，症状是 import torch 成功但
    # 没有 __version__，任何真实调用都炸。is_available() 查不出来，uv 自己也查不出来。
    if not res.get("torch"):
        log(f"自检失败: {res.get('device_error')}")
        log("多半是上次安装被中断留下的半截 torch —— 先强制重装该包…")
        rc2, _o2, _e2 = run([*cmd, "--reinstall-package", "torch"], timeout=1800)
        res = post_check(vpy) if rc2 == 0 else res
        if not res.get("torch"):
            log("重装无效 —— 整个 venv 重建…")
            shutil.rmtree(HERE / ".venv", ignore_errors=True)
            rc3, _o3, _e3 = run(cmd, timeout=1800)
            res = post_check(vpy) if rc3 == 0 else res

    log(f"torch     : {res.get('torch')}")
    log(f"可用设备  : {res.get('device')}")
    if res.get("device_error"):
        log(f"⚠ 设备自检: {res['device_error']}")

    # --- 落机器档案 ---
    profile = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "system": system,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "gpu": gpu,
        "variant": variant,
        "variant_reason": reason,
        "uv": uv,
        "probe": res,
    }
    prof_path = HERE / ".venv" / "machine-profile.json"
    prof_path.parent.mkdir(parents=True, exist_ok=True)
    prof_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    log(f"机器档案  : {prof_path}")

    if not res.get("torch"):
        log("ERROR: torch 无法导入 —— 环境未就绪。")
        return 6
    if variant != "cpu" and res.get("device") == "cpu":
        log(
            f"⚠ 选了 {variant} 变体，但自检只拿到 cpu 设备 —— "
            f"通常是驱动与 wheel 的 CUDA 版本不匹配。可尝试 --variant cpu 或降级变体。"
        )
    log("环境就绪。训练： uv run --frozen -u run_rl.py --course <name>")
    return 0


def venv_python_exists_but_broken(vpy: Path) -> bool:
    missing, what = venv_base_missing(vpy)
    if missing:
        print(f"[bootstrap] venv 的 base interpreter 已不存在: {what}")
    return missing


if __name__ == "__main__":
    sys.exit(main())
