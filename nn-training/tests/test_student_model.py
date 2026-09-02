"""test_student_model.py — student_model.py（CoordConv-ConvMixer-Lite）常驻回归测试。

覆盖（纯 CPU，秒级）：
  1) StudentNet forward 形状（(B,5)/(B,2)）与参数预算 ≤200K；
  2) PPOStudent 三头（+value head (B,1)），train_bc --value-coef 路径的模型面；
  3) coord_channels 数值公式（x=round(col/25*255)、y=round(row/25*255)）——
     MUST match TS runtime（plan §4.3 注释钉死）；
  4) arch() 元数据（kind/h/d/head_hidden）供 weights_io 序列化；
  5) 确定性：同 seed 双实例输出逐位一致（kaiming init 种子化）。

运行（经统一启动器进入 venv）：
  python test_student_model.py
"""

from __future__ import annotations

import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.student import (
    DEFAULT_D,
    DEFAULT_H,
    PPOStudent,
    StudentNet,
    coord_channels,
    param_count,
)
from schema import BOARD, FIRE_DIM, MOVE_DIM, OBS_CHANNELS, SCALAR_DIM

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def _dummy(batch: int = 2, device="cpu"):
    obs = torch.zeros(batch, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8, device=device)
    sc = torch.zeros(batch, SCALAR_DIM, dtype=torch.float32, device=device)
    return obs, sc


def test_student_forward_shape_and_budget() -> None:
    torch.manual_seed(0)
    m = StudentNet()
    obs, sc = _dummy()
    mv, fr = m(obs, sc)
    check(tuple(mv.shape) == (2, MOVE_DIM), f"move logits (2,{MOVE_DIM}) (got {tuple(mv.shape)})")
    check(tuple(fr.shape) == (2, FIRE_DIM), f"fire logits (2,{FIRE_DIM}) (got {tuple(fr.shape)})")
    n = param_count(m)
    check(n <= 200_000, f"参数预算 ≤200K（实际 {n}）")
    check(n < 80_000, f"67.5K 甜点量级（实际 {n}）")


def test_ppo_student_value_head() -> None:
    torch.manual_seed(1)
    m = PPOStudent()
    obs, sc = _dummy()
    mv, fr, val = m(obs, sc)
    check(tuple(val.shape) == (2, 1), f"value head (2,1)（got {tuple(val.shape)}）")
    # value head 是额外参数；学生主干与 StudentNet 同参数
    n_student = param_count(StudentNet())
    n_ppo = param_count(m)
    check(
        n_ppo == n_student + 129,  # 128+1 = head_hidden→1
        f"PPOStudent = StudentNet + value_head（{n_ppo} vs {n_student}+129）",
    )


def test_coord_channels_formula() -> None:
    coords = coord_channels(BOARD, "cpu")
    check(tuple(coords.shape) == (2, BOARD, BOARD), "coord 形状 (2,26,26)")
    check(coords.dtype == torch.uint8, "coord dtype uint8（与 obs 同尺度）")
    # 公式：ch0[row][col] = round(col/25*255)；ch1 = round(row/25*255)
    col = torch.tensor([0, 1, 12, 25], dtype=torch.float32)
    exp_x = (col / (BOARD - 1) * 255).round().to(torch.uint8)
    got_x = coords[0, 0, [0, 1, 12, 25]]
    check(torch.equal(got_x, exp_x), f"ch0 沿列 = round(col/25*255)（got {got_x.tolist()}）")
    row = torch.tensor([0, 13, 25], dtype=torch.float32)
    exp_y = (row / (BOARD - 1) * 255).round().to(torch.uint8)
    got_y = coords[1, [0, 13, 25], 0]
    check(torch.equal(got_y, exp_y), f"ch1 沿行 = round(row/25*255)（got {got_y.tolist()}）")
    check(coords[0, 0, 0].item() == 0 and coords[0, 0, 25].item() == 255, "坐标通道端点 0/255")


def test_arch_metadata() -> None:
    m = StudentNet()
    a = m.arch()
    check(a["kind"] == "student", "arch.kind == student")
    check(a["h"] == DEFAULT_H and a["d"] == DEFAULT_D, "arch 记录 h/d")
    check(a["head_hidden"] == 128, "arch 记录 head_hidden")
    check(
        a["in_ch"] == OBS_CHANNELS and a["board"] == BOARD and a["scalar_dim"] == SCALAR_DIM,
        "arch 记录输入维度",
    )


def test_deterministic_init() -> None:
    torch.manual_seed(99)
    m1 = StudentNet()
    torch.manual_seed(99)
    m2 = StudentNet()
    for (k1, p1), (k2, p2) in zip(m1.state_dict().items(), m2.state_dict().items(), strict=True):
        check(k1 == k2 and torch.equal(p1, p2), f"同 seed 初始化逐位一致（{k1}）")


def main() -> None:
    test_student_forward_shape_and_budget()
    test_ppo_student_value_head()
    test_coord_channels_formula()
    test_arch_metadata()
    test_deterministic_init()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


def test_stem_input_normalization_folded() -> None:
    """P1-10：输入归一化折进 stem 权重——初始 stem.weight 量级 ≈ kaiming/255。

    数学等价于 forward 里 input/255，但权重文件格式不变（TS 运行时零改动）。
    """
    import torch

    m = StudentNet(h=16, d=2)
    # stem.weight 的 kaiming 边界 ≈ sqrt(6/(fan_in))，fan_in=16*9=144
    bound = (6.0 / 144.0) ** 0.5
    assert float(m.stem.weight.abs().max()) <= bound / 255.0 + 1e-6, (
        "stem.weight 应已 ×1/255（输入归一化折进权重）"
    )


if __name__ == "__main__":
    main()
    test_stem_input_normalization_folded()
