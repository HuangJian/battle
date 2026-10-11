"""common/cli.py —— 入口脚本的 **argparse / 退出码约定**（唯一实现；plan/nn-training-reuse-and-deps.plan.md §S2）。

**用户裁决（2026-10-11，plan §S2「下一刀准备」）**：`--help` 文案与退出码**都不是契约**，
**只保 flags 语义**（参数名 / 含义 / 默认值逐字不变），并且**一次性全迁**（不搞「新入口专用」的过渡态）。

⇒ 本模块就两件事，刻意不做第三件：

* `house_parser(...)` —— 全仓 `argparse.ArgumentParser(...)` 的唯一构造点（55 处）。**不动
  `formatter_class`**：`ArgumentDefaultsHelpFormatter` 会给 help 追加 ` (default: %(default)s)`，
  而 argparse 随后拿 `help % params` 展开 —— 凡是 help 里写了字面 `%`（「命中率 100%」这种）
  的入口都会在 `--help` 时炸 `ValueError`。**不预置旋钮**：`--out` / `--seed` / `--json` 的默认值
  与必需性各入口不同，预置 = 改语义（用户口径正是「只保 flags 语义」）。
* `run_main(main)` —— 统一出口：`None → 0`（与 `sys.exit(None)` 同款）· `int` → 该退出码 ·
  `KeyboardInterrupt → 130`（shell 的 SIGINT 约定，且不打整段 traceback）。

签名只留实测存在的两个形态（55 处全是 `()` / `(description)` / `(description, prog)`）——
多一个「以防万一」的旋钮就是把语义决策搬进中央文件。

⚠ 本模块**只管约定**，不做「参数解析框架」：`add_argument` 调用点一行不改
（`tests/test_cli_house.py` 钉住「没有裸 `argparse.ArgumentParser`」与退出约定三态）。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import NoReturn

__all__ = ["house_parser", "run_main"]


def house_parser(description: str | None = None, *, prog: str | None = None) -> argparse.ArgumentParser:
    """建一个入口 parser（当前 = `argparse.ArgumentParser` 的透传）。

    透传是**有意的**：全仓迁过来之后，「入口 parser 长什么样」只有这一个定义点；
    下次要统一什么（`formatter_class` / `parents` / 错误前缀）只需改这里 + 一次门禁。
    """
    return argparse.ArgumentParser(description=description, prog=prog)


def run_main(main: Callable[[], int | None]) -> NoReturn:
    """跑 `main()` 并**按下图退出**：`None → 0` · `int → 该码` · `Ctrl-C → 130`。"""
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n中断（KeyboardInterrupt）", flush=True)
        raise SystemExit(130) from None
    raise SystemExit(0 if rc is None else int(rc))
