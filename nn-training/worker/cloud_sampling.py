"""worker/cloud_sampling —— 自主云机**采样覆盖**的纯逻辑（2026-11-15）。

## 为什么在这里

课程文件里的 `autonomous_cloud_overrides` 是**按设备分档**的完整采样策略
（`gpu` / `tpu`）。同一门课可能先后在 GPU / TPU 云机交替训练（用户 2026-11-15 更正），
所以选档发生在**云机执行面**（`remote/plan_run`），而不是课程加载/导出面：

* 顶层 `target_transitions` / `rotation_*` 仍是 hub 基线 —— 导出计划与 `est_s_all` 的来源；
* 档只覆盖**本段每轮**的 `target_transitions` 与轮换配方；
* 档生效时 notebook 的 `sample_mult` / `epoch_mult` 不再叠加（调用方归 1）。

## 分层

L4 纯函数，只依赖 `worker.volume_waves.parse_stages_arg`（L3）。不 import `worker.plan`
（同为 L4，没有上下关系）——窗口/配额的最终重解仍在 `worker.plan.volume_for_round`，这里只做
「选档 + 把 target/topup/rotation 写进计划副本」。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from worker.volume_waves import parse_stages_arg

#: 允许的档名（与 `CloudSamplingProfile` 的字段一一对应）。
PROFILE_KEYS: tuple[str, ...] = ("gpu", "tpu")


def device_profile(device: object) -> str | None:
    """已解析 device → 档名；CPU/未知 ⇒ None。

    `notebook_runtime.resolve_device` 的返回值：`cuda` / `cuda:N` / `cuda-dp` / `tpu` /
    `xla` / `xla:N` / `cpu`。这里只做**前缀归一**，不重新探测（探测只属于开跑前那一处）。
    """
    d = str(device or "").strip().lower()
    if not d:
        return None
    if d == "gpu" or d.startswith("cuda"):
        return "gpu"
    if d in ("tpu", "xla") or d.startswith("tpu") or d.startswith("xla"):
        return "tpu"
    return None


def profile_for_course(course: Any, device: object) -> dict[str, Any] | None:
    """课程 + 本机 device → 生效档；没块/CPU ⇒ None；是云机但缺该档 ⇒ `ValueError`。

    返回 `{name, target_transitions, rotation:{mode, per_it, pinned}}`（可直接进账本）。
    缺档**不回落**：GPU/TPU 各写一份是块的全部意义，静默用另一档或顶层 = 换实验不换账。
    """
    block = getattr(course, "autonomous_cloud_overrides", None) if course is not None else None
    if block is None:
        return None
    name = device_profile(device)
    if name is None:
        return None
    raw = getattr(block, name, None)
    if raw is None:
        raise ValueError(
            f"课程有 autonomous_cloud_overrides 但没有 {name} 档（device={device!r}）——"
            "GPU/TPU 各写一份完整策略；缺档不静默回落（那会换实验而不换账）"
        )
    target = int(getattr(raw, "target_transitions", 0) or 0)
    if target <= 0:
        raise ValueError(f"autonomous_cloud_overrides.{name}.target_transitions 必须 > 0（收到 {target}）")
    mode = str(getattr(raw, "rotation_mode", "") or "").strip()
    per_it = int(getattr(raw, "rotation_per_it", 0) or 0)
    if mode not in ("even", "pinned"):
        raise ValueError(f"autonomous_cloud_overrides.{name}.rotation_mode={mode!r} 非法（even/pinned）")
    if mode == "even" and per_it < 1:
        raise ValueError(
            f"autonomous_cloud_overrides.{name}.rotation_per_it 在 even 下必须 ≥1（收到 {per_it}）"
        )
    pinned_raw = str(getattr(raw, "rotation_pinned", "") or "").strip()
    try:
        pinned = parse_stages_arg(pinned_raw) if pinned_raw else []
    except ValueError as e:
        raise ValueError(
            f"autonomous_cloud_overrides.{name}.rotation_pinned 非法（{pinned_raw!r}）：{e}"
        ) from e
    return {
        "name": name,
        "target_transitions": target,
        "rotation": {"mode": mode, "per_it": per_it, "pinned": pinned},
    }


def plan_for_profile(plan: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    """计划副本：target/topup 按档改，rotation 换成档配方；其余字段原样。

    只改**标量/硬顶**，不重解窗口——那一步仍是 `worker.plan.volume_for_round`（与本机链、
    云机链同一份 `volume_block_for_window`），这里抄第二份会立刻分叉。
    """
    volume = plan.get("volume")
    if not isinstance(volume, Mapping):
        raise ValueError(
            "云端覆盖需要计划带 volume 块（hub 基线）：没有它就没有 target/est_s_all 可重解"
        )
    if not isinstance(volume.get("est_s_all"), Mapping):
        raise ValueError(
            "计划 volume 缺 est_s_all：档的窗口可能超出导出轮窗口，必须用全关估计重解"
            "（把课程顶层 rotation_mode 打开后重新导包）"
        )
    base_target = int(volume.get("target_transitions") or 0)
    if base_target <= 0:
        raise ValueError("计划 volume.target_transitions 必须 > 0（否则没有可放大的基线）")
    target = int(profile["target_transitions"])
    if target <= 0:
        raise ValueError(f"档 {profile.get('name')!r} 的 target_transitions 必须 > 0")
    scaled = dict(volume)
    scaled["target_transitions"] = target
    if target != base_target:
        topup = dict(scaled.get("topup") or {})
        cap = int(topup.get("max_games_per_stage", 0) or 0)
        if cap > 0:
            # 显式硬顶跟着档 target 抬（0 = 无顶保持 0）；不抬会把放大后的配额卡在半路。
            topup["max_games_per_stage"] = max(cap, math.ceil(cap * target / base_target))
            scaled["topup"] = topup
    rotation = dict(profile["rotation"])
    return {**dict(plan), "volume": scaled, "rotation": rotation}
