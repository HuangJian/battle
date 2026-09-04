"""bootstrap.py 变体选择的表驱动测试（纯逻辑，无网络、无 subprocess、无 torch）。

**为什么需要**：变体选择错了的代价很高 —— 无 GPU 的机器白下 ~3GB 的 nvidia-* 依赖，
有 GPU 的机器装 CPU 构建则完全用不上显卡。而这条逻辑在本机只能覆盖 CPU 分支
（AMD APU 无独显），NVIDIA / ROCm / XPU / MPS 四条路径**没有真机可自证**。
因此把选择逻辑做成纯函数、用注入的假 `run_fn` 覆盖全部组合，是唯一能守住的防线。

改 `CUDA_LADDER` 或 `VARIANT_GROUPS` 时，这张表必须同步更新。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap

# ---------------------------------------------------------------------------
# 假命令执行器：模拟 nvidia-smi 的存在与输出
# ---------------------------------------------------------------------------


def _runner(stdout_map: dict[str, tuple[int, str]]):
    """构造一个假的 run()：按命令首项返回预设输出，未预设的返回失败。"""

    def _run(cmd: list[str], **_kw):
        key = cmd[0]
        if key in stdout_map:
            rc, out = stdout_map[key]
            return rc, out, ""
        return 127, "", "not found"

    return _run


NVIDIA_SMI_L = "GPU 0: NVIDIA GeForce RTX 4070 (UUID: GPU-xxxx)\n"
SMI_HEADER_129 = "| NVIDIA-SMI 570.00   Driver Version: 570.00   CUDA Version: 12.9 |\n"
SMI_HEADER_118 = "| NVIDIA-SMI 520.00   Driver Version: 520.00   CUDA Version: 11.8 |\n"
SMI_HEADER_NONE = "| NVIDIA-SMI 470.00   Driver Version: 470.00                      |\n"


# ---------------------------------------------------------------------------
# CUDA driver ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("runtime", "expected"),
    [
        ("13.0", "cu128"),  # cu130 无 torch 2.7.1 构建 → 向下兼容落到 cu128
        ("12.9", "cu128"),
        ("12.8", "cu128"),  # 边界：>= 12.8
        ("12.7", "cu126"),  # 12.8 以下落 cu126
        ("12.6", "cu126"),  # 边界：>= 12.6
        ("12.5", "cu118"),
        ("11.8", "cu118"),  # 边界：>= 11.8
        ("11.7", None),  # 太老，没有可用 CUDA 变体
        (None, None),  # 拿不到 runtime
        ("garbage", None),  # 解析失败
    ],
)
def test_cuda_ladder(runtime, expected) -> None:
    assert bootstrap.cuda_variant_for_runtime(runtime) == expected


# ---------------------------------------------------------------------------
# 硬件探测
# ---------------------------------------------------------------------------


def test_detect_cuda_with_runtime() -> None:
    gpu = bootstrap.detect_gpu(
        "Linux", _runner({"nvidia-smi": (0, SMI_HEADER_129), "nvidia-smi -L": (0, NVIDIA_SMI_L)})
    )
    assert gpu["backend"] == "cuda"
    assert gpu["cuda_runtime"] == "12.9"


def test_detect_cuda_without_runtime_falls_through() -> None:
    """nvidia-smi 能列出 GPU 却报不出 CUDA Version → 不能算 cuda（装了也白装）。"""
    gpu = bootstrap.detect_gpu(
        "Linux", _runner({"nvidia-smi": (0, SMI_HEADER_NONE), "nvidia-smi -L": (0, NVIDIA_SMI_L)})
    )
    assert gpu["backend"] == "none"


def test_detect_none_when_no_tool() -> None:
    gpu = bootstrap.detect_gpu("Linux", _runner({}))
    assert gpu["backend"] == "none"


def test_detect_macos_arm_is_mps(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "arm64")
    gpu = bootstrap.detect_gpu("Darwin", _runner({}))
    assert gpu["backend"] == "mps"


def test_detect_macos_intel_is_none(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    gpu = bootstrap.detect_gpu("Darwin", _runner({}))
    assert gpu["backend"] == "none"


def test_detect_rocm_on_linux(monkeypatch) -> None:
    # 注意：不能按 `str(p) == "/dev/kfd"` 比对 —— Windows 上 Path("/dev/kfd")
    # 的字符串是 "\dev\kfd"。用末尾两段比对，跨平台都成立。
    monkeypatch.setattr(bootstrap.Path, "exists", lambda self: self.parts[-2:] == ("dev", "kfd"))
    gpu = bootstrap.detect_gpu("Linux", _runner({}))
    assert gpu["backend"] == "rocm"


def test_detect_xpu(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_has", lambda name: name == "sycl-ls")
    monkeypatch.setattr(bootstrap.Path, "exists", lambda self: False)
    gpu = bootstrap.detect_gpu("Windows", _runner({}))
    assert gpu["backend"] == "xpu"


# ---------------------------------------------------------------------------
# 变体选择
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "gpu", "expected", "why"),
    [
        ("Linux", {"backend": "cuda", "cuda_runtime": "12.9"}, "cu128", "新驱动 → 最高可用变体"),
        ("Windows", {"backend": "cuda", "cuda_runtime": "11.8"}, "cu118", "老驱动 → cu118"),
        (
            "Linux",
            {"backend": "cuda", "cuda_runtime": None},
            "cpu",
            "有硬件但拿不到 runtime → 降级 CPU，绝不返回装不上的名字",
        ),
        ("Linux", {"backend": "rocm", "cuda_runtime": None}, "rocm", "ROCm 仅 Linux"),
        ("Windows", {"backend": "rocm", "cuda_runtime": None}, "cpu", "ROCm 无 Windows 构建"),
        ("Windows", {"backend": "xpu", "cuda_runtime": None}, "xpu", "Intel XPU"),
        ("Darwin", {"backend": "xpu", "cuda_runtime": None}, "cpu", "XPU 无 macOS 构建"),
        (
            "Darwin",
            {"backend": "mps", "cuda_runtime": None},
            "cpu",
            "Apple Silicon：MPS 内置于 PyPI 原生 wheel，用 cpu 变体即可",
        ),
        ("Windows", {"backend": "none", "cuda_runtime": None}, "cpu", "无加速器"),
    ],
)
def test_pick_variant(system, gpu, expected, why) -> None:
    variant, reason = bootstrap.pick_variant(system, gpu)
    assert variant == expected, f"{why}: 得到 {variant}（{reason}）"


def test_pick_variant_explicit_wins() -> None:
    """显式指定压过探测结果 —— 排查"装错了变体"时必须能强制。"""
    gpu = {"backend": "none", "cuda_runtime": None}
    variant, reason = bootstrap.pick_variant("Linux", gpu, requested="cu128")
    assert variant == "cu128"
    assert "显式" in reason


def test_pick_variant_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        bootstrap.pick_variant(
            "Linux", {"backend": "none", "cuda_runtime": None}, requested="cu999"
        )


def test_pick_variant_always_returns_installable_name() -> None:
    """不变式：任何探测结果都不得产出一个装不上的变体名。"""
    systems = ["Linux", "Windows", "Darwin"]
    backends = ["cuda", "rocm", "xpu", "mps", "none"]
    runtimes = [None, "11.7", "11.8", "12.6", "12.9", "garbage"]
    for s in systems:
        for b in backends:
            for r in runtimes:
                v, _ = bootstrap.pick_variant(s, {"backend": b, "cuda_runtime": r})
                assert v in bootstrap.VARIANT_GROUPS, f"{s}/{b}/{r} -> {v}"


# ---------------------------------------------------------------------------
# uv 命令构造
# ---------------------------------------------------------------------------


def test_sync_cmd_cpu_uses_default_groups() -> None:
    """CPU 走 default-groups —— 裸 `uv sync` 就装 CPU 变体，不需要任何参数。"""
    assert bootstrap.sync_cmd("cpu") == ["uv", "sync"]


def test_sync_cmd_gpu_disables_default_groups() -> None:
    """GPU 变体必须关掉默认组，否则 uv 会因 conflicts 报错（装两个 torch）。"""
    assert bootstrap.sync_cmd("cu128") == [
        "uv",
        "sync",
        "--no-default-groups",
        "--group",
        "dev",
        "--group",
        "torch-cu128",
    ]


@pytest.mark.parametrize("variant", sorted(bootstrap.VARIANT_GROUPS))
def test_sync_cmd_covers_all_variants(variant) -> None:
    cmd = bootstrap.sync_cmd(variant)
    assert cmd[0] == "uv" and cmd[1] == "sync"
    if variant != "cpu":
        assert "--no-default-groups" in cmd
        assert bootstrap.VARIANT_GROUPS[variant] in cmd
