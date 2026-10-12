"""init_spatial_leg 三档回归（plan/policy-spatial-head.plan.md §4 Step 2 S0-c / Step 4-5 起点）。

腿 A/腿 B 的起点由 `--policy-extra [--spatial-tower]` 造（新头/塔随机初始化）；
**A0 对照臂**的起点 = hu150 旧架构——v3 文件在 v4 loader 下被 schema 门拒收
（`build_ppo`/`load_state_into` 全链严格），故 `--legacy-arch` 档做**全量元数据迁移**
（`warmstart_missing=[]`），课程 `bc=` 才可直接使用（否则 A0 一开课即被 worker 拒收）。

本文件钉住：
  ① legacy-arch：产物过 `build_ppo`（旧架构）+ 严格 `load_state_into`，missing 为空；
  ② policy-extra：产物 arch 标志正确、换形张量走列迁移（partial = 四张）、严格装载通过；
     ⚠ 源必须是 v4 **policy_extra** 系（pf 137）——legacy 系被响亮拒绝（下面的 ④/⑥）；
  ③ 互斥与拒绝：legacy_arch 与 policy_extra 同给 / 三者都不给 ⇒ SystemExit。

obs-v5（plan/obs-v5-vector-gap.plan.md §2.7 / A10）追加：
  ④ **列迁移**：cus 94→104、move/fire 137→165、value 128→165——旧列**逐位等**、
     新列 `normal_(0,0.01)` 重 init（量级 ≤0.05）、未迁移张量逐位等（非新增列路径一致）；
  ⑤ 腿 B 的**分段**映射（旧 `[tower|s30|extra9]` → 新 `[tower|s40|extra37]`）：拷的三段逐位等、
     新 scalars/extra 两段重 init、value 头不在分段表内（全重 init）；
  ⑥ 形状不合法（既非同行、也非「宽−n/分段」）⇒ **响亮拒绝**（不静默跳过）。
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

from common.schema import OBS_SCHEMA_MAJOR
from worker.data.weights_io import load_state_into, load_weights_json, save_weights_json
from worker.models.student import PPOStudent
from worker.ppo.engine import build_ppo
from worker.scripts.init_spatial_leg import convert


def _truncate_tail_value(meta: dict, in_cols: int) -> None:
    """就地截 `value_head.weight` 的输入列（腿 B 的 128 宽旧头）。"""
    entry = meta["params"]["value_head.weight"]
    arr = np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(entry["shape"])
    tail = arr[:, :in_cols].copy()
    entry["shape"] = list(tail.shape)
    entry["data"] = base64.b64encode(tail.tobytes()).decode("ascii")


def _saved(path: Path, model: torch.nn.Module) -> Path:
    """存盘并返回路径（_truncate_tail / 测试正文都要真文件，不传内存张量）。"""
    save_weights_json(model, str(path))
    return path


def _truncate_tail(src: Path, cols: dict[str, int]) -> Path:
    """把 `src` 里指定张量的输入列截回 v4 宽度（合成 v4 起点；原地重写文件）。

    新档模型存盘后截尾 = 一份「形状与 v4 一致」的合成起点——比手搓 JSON 更接近真实
    （stem/blocks/bias 全部真值），且截法（前缀）与 §2.7 的迁移方向一致。
    """
    meta = json.loads(src.read_text(encoding="utf-8"))
    for name, in_cols in cols.items():
        entry = meta["params"][name]
        shape = list(entry["shape"])
        arr = np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(shape)
        tail = arr[:, :in_cols].copy()
        entry["shape"] = list(tail.shape)
        entry["data"] = base64.b64encode(tail.tobytes()).decode("ascii")
    meta["schema_major"] = 4  # v4 文件：strict 装载必须靠 allow_legacy_schema 放行
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return src


def _legacy_source(tmp_path: Path) -> Path:
    """合成一份**旧 schema（v3）**起点：默认档模型存盘后把 meta 的 schema_major 改回 3。

    ⚠ 尺寸必须走默认档（h=64/d=8）——`convert()` 的产物模型是默认档，窄模型（h=8/d=1）
    会让卷积权重整片换形、覆盖率跌破 COVERAGE_RAISE 而响亮拒绝（这正是它该有的行为）。
    """
    torch.manual_seed(7)
    src = tmp_path / "legacy-src.json"
    save_weights_json(PPOStudent(), str(src))
    meta = json.loads(src.read_text(encoding="utf-8"))
    meta["schema_major"] = 3
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return src


def test_legacy_arch_passthrough_full_migration(tmp_path: Path) -> None:
    """A0 档：全量迁移（missing=[]）⇒ build_ppo 得旧架构、严格装载通过。"""
    src = _legacy_source(tmp_path)
    dst = tmp_path / "legA0.json"
    info = convert(
        str(src), str(dst), policy_extra=False, spatial_tower=False, legacy_arch=True, seed=3
    )
    assert info["warmstart_missing"] == []
    assert info["schema_major"] == OBS_SCHEMA_MAJOR  # 产物总是当前 schema（obs-v5 = 5）
    model = build_ppo(str(dst))
    assert model.policy_extra is False
    assert model.spatial_tower is False
    load_state_into(model, str(dst))  # strict（默认拒绝旧 schema）必须通过
    meta = json.loads(dst.read_text(encoding="utf-8"))
    assert meta["arch"].get("policyExtra", False) is False


def test_leg_a_from_legacy_source_is_refused(tmp_path: Path) -> None:
    """腿 A 起点必须是 v4 **policy_extra** 系（pf 137）——legacy 系（pf 128）响亮拒绝。

    obs-v5 的列迁移假设源里有 `hidden(128)+extra(9) = 137` 列（§2.7 的「拷 137」）。
    legacy 源没那 9 列 ⇒ 以前的实现会静默重 init 三张头（旧测试就当它是成功），
    那正是「用错权重族却不知道」的坑；现在改成拒绝。
    """
    src = _legacy_source(tmp_path)  # legacy 头 128（无 extra 列）
    with pytest.raises(SystemExit):
        convert(
            str(src),
            str(tmp_path / "legA.json"),
            policy_extra=True,
            spatial_tower=False,
            legacy_arch=False,
            seed=3,
        )


def test_leg_a_column_migration_old_cols_bit_exact(tmp_path: Path) -> None:
    """④ 腿 A 列迁移：旧列逐位等、新列 0.01 量级、未迁移张量逐位等（A10）。"""
    torch.manual_seed(11)
    src = _truncate_tail(
        _saved(tmp_path / "legA-v4.json", PPOStudent(policy_extra=True)),
        {"fc.weight": 94, "move_head.weight": 137, "fire_head.weight": 137, "value_head.weight": 128},
    )
    dst = tmp_path / "legA-v5.json"
    info = convert(
        str(src), str(dst), policy_extra=True, spatial_tower=False, legacy_arch=False, seed=5
    )
    # 四张换形张量全部走列迁移 ⇒ 不再算「缺失」；新列是「部分迁移」
    assert info["warmstart_missing"] == []
    assert info["warmstart_partial"] == [
        "fc.weight",
        "fire_head.weight",
        "move_head.weight",
        "value_head.weight",
    ]
    old = load_weights_json(str(src), allow_legacy_schema=True)[1]
    new = load_weights_json(str(dst))[1]
    for name, keep in (
        ("fc.weight", 94),
        ("move_head.weight", 137),
        ("fire_head.weight", 137),
    ):
        assert torch.equal(new[name][:, :keep], old[name]), f"{name} 旧列不是逐位等"
        tail = new[name][:, keep:]
        assert tail.shape[1] > 0
        assert float(tail.abs().max()) <= 0.05, f"{name} 新列量级过大：{float(tail.abs().max())}"
        assert float(tail.std()) <= 0.02, f"{name} 新列 std 过大：{float(tail.std())}"
        if tail.shape[0] > 1:
            # 新列必须逐元素独立随机：广播式 `randn(k)` 会让所有行全同（rank-1），
            # 对称性没打破。首行展开后必须与全片不等（固定 seed 下确定性判据）。
            assert not torch.equal(tail, tail[0:1].expand_as(tail)), f"{name} 新列跨行全同（广播 init）"
    # value 新列是纯 0（门控纪律：零起步＋训练期牵引，见 test_value_anchor.py）——
    # 旧 128 列逐位等，新 37 列逐位零（不是 0.01 随机）。
    assert torch.equal(new["value_head.weight"][:, :128], old["value_head.weight"])
    assert torch.equal(
        new["value_head.weight"][:, 128:], torch.zeros_like(new["value_head.weight"][:, 128:])
    ), "value 新列必须是纯 0（drop0），噪声起步正是 it90 诊断的 35 倍爆炸源"
    # 非迁移张量：全量逐位等（「非新增列路径一致」）
    for name in ("stem.weight", "blocks.0.dw.weight", "fc.bias", "value_head.bias"):
        assert torch.equal(new[name], old[name]), f"{name} 不应动"
    model = build_ppo(str(dst))
    load_state_into(model, str(dst))  # strict（默认拒旧 schema）必须通过


def test_leg_b_segmented_migration(tmp_path: Path) -> None:
    """⑤ 腿 B 分段映射：三段逐位等 + 新两段重 init + value 不在表内。"""
    torch.manual_seed(12)
    src = _saved(tmp_path / "legB-v4.json", PPOStudent(policy_extra=True, spatial_tower=True))
    # 新布局 [tower 112 | s40 112..151 | extra37 152..188] → 旧布局 [tower | s30 | extra9]
    meta = json.loads(src.read_text(encoding="utf-8"))
    for name in ("move_head.weight", "fire_head.weight"):
        entry = meta["params"][name]
        shape = list(entry["shape"])
        arr = np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(shape)
        legacy_row = np.concatenate([arr[:, 0:112], arr[:, 112:142], arr[:, 161:170]], axis=1).copy()
        assert legacy_row.shape[1] == 151
        entry["shape"] = list(legacy_row.shape)
        entry["data"] = base64.b64encode(legacy_row.tobytes()).decode("ascii")
    # v4 腿 B 的 value 头 = 128 宽（只吃 h）⇒ 新档 189 无对应前缀：全重 init
    _truncate_tail_value(meta, 128)
    meta["schema_major"] = 4
    src.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    dst = tmp_path / "legB-v5.json"
    info = convert(
        str(src), str(dst), policy_extra=True, spatial_tower=True, legacy_arch=False, seed=6
    )
    # value 头全重 init（无对应前缀）⇒ 它仍留在 missing 里；其余两张走分段迁移
    assert info["warmstart_missing"] == ["value_head.weight"]
    assert info["warmstart_partial"] == ["fire_head.weight", "move_head.weight"]
    old = load_weights_json(str(src), allow_legacy_schema=True)[1]
    new = load_weights_json(str(dst))[1]
    segs = ((0, 0, 112), (112, 112, 30), (142, 152, 9))
    for name in ("move_head.weight", "fire_head.weight"):
        for src_start, dst_start, count in segs:
            assert torch.equal(
                new[name][:, dst_start : dst_start + count], old[name][:, src_start : src_start + count]
            ), f"{name} 段 {src_start}→{dst_start} 不是逐位等"
        # 重 init 的两段：新 scalars s30–39 = [142..151]、新 extra 9–36 = [161..188]
        for lo, hi in ((142, 152), (161, 189)):
            seg = new[name][:, lo:hi]
            assert float(seg.abs().max()) <= 0.05, f"{name}[{lo}:{hi}] 量级过大"
            # 同上：多行张量的新段必须跨行有差异（广播 init 会全行全同）
            assert not torch.equal(seg, seg[0:1].expand_as(seg)), f"{name}[{lo}:{hi}] 新段跨行全同"
    # value 头：新档 189 宽、整张都是 0.01 尺度（`_init_value_head`），没被迁移污染
    assert new["value_head.weight"].shape[1] == 189
    assert float(new["value_head.weight"].abs().max()) <= 0.05
    # fc 同样按「尾部 +10 列」迁移（与腿 A 同规）
    assert torch.equal(new["fc.weight"][:, :94], old["fc.weight"][:, :94])
    model = build_ppo(str(dst))
    load_state_into(model, str(dst))


def test_leg_a_to_b_migration_extra9_segment(tmp_path: Path) -> None:
    """⑦ 腿 A 源（头 137）→ 腿 B：旧 extra9 拷进新 [152:161]，其余全重 init。

    新 pf `[tower 0–111 | s40 112–151 | extra37 152–188]` 里旧头只有 extra9 有对应槽
    （hidden 128 在新 pf 无槽）；fc 照旧拷 94；value 无前缀、全重 init（仍在 missing）。
    """
    torch.manual_seed(14)
    src = _truncate_tail(
        _saved(tmp_path / "legA2B-v4.json", PPOStudent(policy_extra=True)),
        {"fc.weight": 94, "move_head.weight": 137, "fire_head.weight": 137, "value_head.weight": 128},
    )
    dst = tmp_path / "legA2B-v5.json"
    info = convert(
        str(src), str(dst), policy_extra=True, spatial_tower=True, legacy_arch=False, seed=8
    )
    assert sorted(info["warmstart_missing"]) == [
        "spatial_fc.bias",
        "spatial_fc.weight",
        "spatial_proj.bias",
        "spatial_proj.weight",
        "value_head.weight",
    ]  # 腿 A 源本就没有塔：塔四键是架构缺失（构造 seed 随机 init），value 是换形未迁
    assert info["warmstart_partial"] == ["fc.weight", "fire_head.weight", "move_head.weight"]
    old = load_weights_json(str(src), allow_legacy_schema=True)[1]
    new = load_weights_json(str(dst))[1]
    assert torch.equal(new["fc.weight"][:, :94], old["fc.weight"][:, :94])
    for name in ("move_head.weight", "fire_head.weight"):
        # 唯一有对应槽的旧列：extra9 [128:137] → [152:161] 逐位等
        assert torch.equal(new[name][:, 152:161], old[name][:, 128:137]), f"{name} extra9 段不是逐位等"
        # 其余 180 列全重 init：量级对＋跨行有差异
        rest = torch.cat([new[name][:, :152], new[name][:, 161:]], dim=1)
        assert rest.shape[1] == 180
        assert float(rest.abs().max()) <= 0.05, f"{name} 新列量级过大"
        assert not torch.equal(rest, rest[0:1].expand_as(rest)), f"{name} 新列跨行全同"
    assert new["value_head.weight"].shape[1] == 189
    assert float(new["value_head.weight"].abs().max()) <= 0.05
    model = build_ppo(str(dst))
    assert model.spatial_tower is True
    load_state_into(model, str(dst))


def test_leg_b_from_legacy_source_is_refused(tmp_path: Path) -> None:
    """腿 B 源必须是 151（v4 腿 B）或 137（v4 腿 A）宽——legacy 系（128）响亮拒绝。"""
    src = _legacy_source(tmp_path)  # 头 128：hidden 有槽对不上（新 pf 无 hidden 槽）
    with pytest.raises(SystemExit):
        convert(
            str(src),
            str(tmp_path / "x.json"),
            policy_extra=True,
            spatial_tower=True,
            legacy_arch=False,
        )


def test_migration_rejects_unexpected_shape(tmp_path: Path) -> None:
    """⑥ 形状既非同行、也非合法「宽−n/分段」⇒ 响亮拒绝（不静默错位）。"""
    torch.manual_seed(13)
    src = _truncate_tail(
        _saved(tmp_path / "legA-bad.json", PPOStudent(policy_extra=True)),
        {"move_head.weight": 100},  # 既不是 165 也不是 137
    )
    with pytest.raises(SystemExit):
        convert(
            str(src),
            str(tmp_path / "x.json"),
            policy_extra=True,
            spatial_tower=False,
            legacy_arch=False,
        )


def test_modes_are_mutually_exclusive_and_none_refused(tmp_path: Path) -> None:
    """互斥/缺档都必须响亮拒绝（不放宽 = 防静默建错架构）。"""
    src = _legacy_source(tmp_path)
    dst = tmp_path / "x.json"
    with pytest.raises(SystemExit):
        convert(str(src), str(dst), policy_extra=True, spatial_tower=False, legacy_arch=True)
    with pytest.raises(SystemExit):
        convert(str(src), str(dst), policy_extra=False, spatial_tower=False, legacy_arch=False)
