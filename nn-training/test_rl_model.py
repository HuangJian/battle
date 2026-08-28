"""test_rl_model.py — rl_model.py（ResNet 教师网络，P1 参考实现）常驻回归测试。

rl_model 标为 "NOT used by the live RL pipeline"（教师参考）；本测试守住其契约
不被静默破坏：forward 形状 / get_action_and_value 采样 / 参数规模 / RF 假设锚点。

运行（经统一启动器进入 venv）：
  python test_rl_model.py
"""
from __future__ import annotations

import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM  # noqa: E402
from rl_model import RLNet, count_params, TOTAL_ACTION_DIM  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def test_forward_shapes() -> None:
    torch.manual_seed(0)
    m = RLNet()
    obs = torch.zeros(3, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    sc = torch.zeros(3, SCALAR_DIM)
    logits, value = m(obs, sc)
    check(tuple(logits.shape) == (3, TOTAL_ACTION_DIM),
          f"action_logits (3,{TOTAL_ACTION_DIM})（got {tuple(logits.shape)}）")
    check(tuple(value.shape) == (3, 1), f"value (3,1)（got {tuple(value.shape)}）")


def test_get_action_and_value() -> None:
    torch.manual_seed(1)
    m = RLNet()
    obs = torch.zeros(4, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    sc = torch.zeros(4, SCALAR_DIM)
    action, log_prob, entropy, value = m.get_action_and_value(obs, sc)
    check(tuple(action.shape) == (4, 2), f"action (4,2)（got {tuple(action.shape)}）")
    check(tuple(log_prob.shape) == (4,), "log_prob (4,)")
    check(tuple(entropy.shape) == (4,), "entropy (4,)")
    check(tuple(value.shape) == (4,), "value (4,)")
    # 提供 action 时 log_prob 可复算（deterministic 分支）
    action2, lp2, _e2, _v2 = m.get_action_and_value(obs, sc, action)
    check(torch.allclose(lp2, log_prob, atol=1e-6), "给定 action 的 log_prob 复算一致")
    # get_value 与 forward 一致
    v_only = m.get_value(obs, sc)
    check(torch.allclose(v_only, value, atol=1e-6), "get_value == forward value")


def test_param_scale() -> None:
    n = count_params(RLNet())
    check(900_000 <= n <= 1_100_000, f"教师参数 ~950K（实际 {n}）")


def main() -> None:
    test_forward_shapes()
    test_get_action_and_value()
    test_param_scale()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
