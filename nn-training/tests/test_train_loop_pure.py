"""test_train_loop_pure.py — train_loop.py 纯函数常驻回归测试。

只测无 IO / 无子进程的纯函数（_fmt_dur / parse_val_loss_from_output），
不启动训练循环、不碰锁/信号/线程。

运行（经统一启动器进入 venv）：
  python test_train_loop_pure.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import train_loop  
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"ok: {msg}")


def test_fmt_dur() -> None:
    cases = {
        0: "0s",
        45: "45s",
        59: "59s",
        60: "1m00s",
        200: "3m20s",
        3599: "59m59s",
        3600: "1h00m",
        3905: "1h05m",
    }
    for sec, want in cases.items():
        got = train_loop._fmt_dur(sec)
        check(got == want, f"_fmt_dur({sec}) == {want!r}（got {got!r}）")


def test_parse_val_loss_prefers_archive() -> None:
    """输出里同时出现 resume 旧权重与新 archive 时，取 LAST `_valX.json`（本轮结果）。"""
    text = (
        "[train] resuming from weights/weights.20260818-170055_ep40_val1.2431.json\n"
        "[train] done in 12.3s -> archive: "
        "weights/weights.20260828-093000_ep60_val1.1876.json (active: weights/weights.json)\n"
    )
    got = train_loop.parse_val_loss_from_output(text)
    check(got == 1.1876, f"取最后 archive val（got {got}）")


def test_parse_val_loss_fallback() -> None:
    """无 archive 文件名时回落 val_loss= 行。"""
    text = "[epoch  1/40] train_loss=1.9 val_loss=1.8432 acc move=0.3 fire=0.7"
    got = train_loop.parse_val_loss_from_output(text)
    check(got == 1.8432, f"回落 val_loss= 解析（got {got}）")


def test_parse_val_loss_none() -> None:
    check(train_loop.parse_val_loss_from_output("no numbers here") is None, "无可解析内容 → None")
    check(train_loop.parse_val_loss_from_output("") is None, "空串 → None")


def main() -> None:
    test_fmt_dur()
    test_parse_val_loss_prefers_archive()
    test_parse_val_loss_fallback()
    test_parse_val_loss_none()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
