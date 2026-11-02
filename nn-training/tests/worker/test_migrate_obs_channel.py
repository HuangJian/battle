"""ch16 · obs 通道迁移（v7 = `OBS_CHANNELS 16→17`）的三段式与逐位保真。

plan/r5-next.plan.md §3.4 的两个前置之一：**三段迁移 + 逐位保真测试** ——
迁移后的网在**旧 schema 输入**（新通道全 0）上 logits 与迁移前**逐位相等**。

本文件钉住：
  ① stem 的三段映射：旧 obs 列逐位等 / 新通道列**恰为 0** / coord 两列原样后移；
  ② 目标权重过**严格** `build_ppo`（schema 7）且旧 schema 输入下四头 logits `torch.equal`；
  ③ 源已是当前 schema ⇒ 响亮拒绝（没有可迁移的通道，静默拷贝会让人以为迁过了）；
  ④ 除 stem 外任何张量换形 ⇒ 响亮拒绝（只支持纯通道迁移，不静默随机 init）。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM
from worker.data.weights_io import load_state_into, load_weights_json, save_weights_json
from worker.models.student import PPOStudent
from worker.ppo.engine import build_ppo
from worker.scripts.migrate_obs_channel import migrate


def _legacy_source(tmp_path: Path, *, policy_extra: bool = False) -> Path:
    """合成 v6 档（16 obs 通道）起点：旧宽度模型存盘，再把 schema_major 改回 6。"""
    torch.manual_seed(3)
    model = PPOStudent(in_ch=OBS_CHANNELS - 1, policy_extra=policy_extra)
    src = tmp_path / "v6-src.json"
    save_weights_json(model, str(src))
    meta = json.loads(src.read_text(encoding="utf-8"))
    meta["schema_major"] = 6
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return src


def _inputs(old_obs: int, *, extra: bool) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor | None]:
    rng = torch.Generator().manual_seed(11)
    obs = torch.randint(0, 256, (1, old_obs, BOARD, BOARD), generator=rng, dtype=torch.uint8)
    scalars = (torch.rand(1, 46, generator=rng) - 0.5) * 4
    ex = (torch.rand(1, POLICY_EXTRA_DIM, generator=rng) - 0.25) * 1.5 if extra else None
    return obs, scalars, ex


def test_stem_three_stage_mapping(tmp_path: Path) -> None:
    src = _legacy_source(tmp_path)
    dst = tmp_path / "v7-dst.json"
    info = migrate(str(src), str(dst), seed=17)
    assert info["from_channels"] == OBS_CHANNELS - 1
    assert info["to_channels"] == OBS_CHANNELS
    assert info["bit_exact_on_old_schema"] is True
    assert info["new_channel_col_norm"] == 0.0

    _, old = load_weights_json(str(src), allow_legacy_schema=True)
    _, new = load_weights_json(str(dst))
    old_stem = old["stem.weight"]
    new_stem = new["stem.weight"]
    o, n = OBS_CHANNELS - 1, OBS_CHANNELS
    assert torch.equal(new_stem[:, :o], old_stem[:, :o])  # 旧 obs 原位
    assert torch.equal(new_stem[:, o:n], torch.zeros_like(new_stem[:, o:n]))  # drop0
    assert torch.equal(new_stem[:, n:], old_stem[:, o:])  # coord 后移
    # 其余张量逐位搬运（本波 SCALAR_DIM / 动作头都没变）
    for key, value in old.items():
        if key != "stem.weight":
            assert torch.equal(new[key], value), key


def test_old_schema_input_is_bit_exact(tmp_path: Path) -> None:
    src = _legacy_source(tmp_path, policy_extra=True)
    dst = tmp_path / "v7-dst.json"
    info = migrate(str(src), str(dst), seed=17)
    assert info["bit_exact_on_old_schema"] is True

    # 目标过**严格** schema 门（生产路径同款），再按新 schema 喂「新通道 = 0」的输入。
    target = build_ppo(str(dst))
    assert target.in_ch == OBS_CHANNELS
    assert load_state_into(target, str(dst)) == []  # 严格 schema 门 + 全键装载
    source = PPOStudent(in_ch=OBS_CHANNELS - 1, policy_extra=True)
    _, old = load_weights_json(str(src), allow_legacy_schema=True)
    source.load_state_dict(old)
    source.eval()
    target.eval()

    obs16, scalars, extra = _inputs(OBS_CHANNELS - 1, extra=True)
    pad = torch.zeros(1, 1, BOARD, BOARD, dtype=torch.uint8)
    with torch.no_grad():
        a = source(obs16, scalars, extra)
        b = target(torch.cat([obs16, pad], dim=1), scalars, extra)
    assert len(a) == len(b)
    for x, y in zip(a, b, strict=True):
        assert torch.equal(x, y), f"max|Δ|={float((x - y).abs().max()):.3e}"


def test_refuses_source_at_current_schema(tmp_path: Path) -> None:
    torch.manual_seed(5)
    src = tmp_path / "current.json"
    save_weights_json(PPOStudent(), str(src))
    with pytest.raises(SystemExit, match="没有可迁移的通道"):
        migrate(str(src), str(tmp_path / "out.json"))


def test_refuses_missing_non_stem_tensor(tmp_path: Path) -> None:
    """源缺别的键（BC 档没有 value_head）= 不是同族完整 PPO 档 ⇒ 拒绝，不静默随机 init。"""
    src = _legacy_source(tmp_path)
    meta = json.loads(src.read_text(encoding="utf-8"))
    meta["params"].pop("value_head.weight")
    meta["params"].pop("value_head.bias")
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with pytest.raises(SystemExit, match="缺/换形其它张量"):
        migrate(str(src), str(tmp_path / "out.json"))


def test_refuses_non_stem_shape_change(tmp_path: Path) -> None:
    """除 stem 外换形 = 源不是同族旧档（例如把 fc 截窄）⇒ 拒绝，不静默随机 init。"""
    src = _legacy_source(tmp_path)
    meta = json.loads(src.read_text(encoding="utf-8"))
    entry = meta["params"]["fc.weight"]
    arr = np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(entry["shape"])
    cut = arr[:, :4].copy()
    entry["shape"] = list(cut.shape)
    entry["data"] = base64.b64encode(cut.tobytes()).decode("ascii")
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with pytest.raises(SystemExit, match="缺/换形其它张量"):
        migrate(str(src), str(tmp_path / "out.json"))
