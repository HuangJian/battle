"""tools/pytest_durations.py —— 从多份 `pytest --durations=0` 日志里**取最小值**汇总/对账。

## 为什么需要它（docs/nn/engineering.md §43）

本机 CPU 负载会剧烈波动：同一份全量门禁四连跑的 user 时间是 199→225s（1.13×），而墙钟只被
负载**单向拉长** ⇒ 单次 `--durations` 排序出的「最慢用例」里混着噪声。**多次取 min** 是最接近
「干净机器」的估计（比均值/中位数抗噪），也是「改前 vs 改后」唯一可比的量（两批各 4 连跑取 min）。

口径：只看 **call** 阶段（setup/teardown 不算——那是夹具的账）；同一次运行里同名用例只记一条
（参数化用例的 nodeid 自带 `[参数]`，是不同条目）。

## 用法（只读，不跑 pytest）

    # 最慢 N 条（min / mean / 出现次数）
    python tools/pytest_durations.py table tmp/gate-*.log -n 30

    # 导出最慢 N 条的 nodeid（喂给单进程串行复测）
    python tools/pytest_durations.py top 20 tmp/gate-*.log > tmp/slow.ids

    # 总量与头部占比（判断「优化最慢 N 条」到底能省多少）
    python tools/pytest_durations.py total tmp/gate-*.log

    # A/B 逐条对账（改前 4 连跑 vs 改后 4 连跑）
    python tools/pytest_durations.py ab --ids tmp/slow.ids \\
        --left tmp/before-*.log --right tmp/after-*.log

测速配方（§43 全文；**不要**用单次 `--durations` 下结论）：

    NN_GATE_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \\
      bash ../tools/githook/nn-py-safe.sh -m pytest tests/ e2e/ -n 12 \\
      -q -p no:cacheprovider --durations=0 > tmp/gate-1.log   # ×4 次
    # 再用上表取 min；被点名的用例另跑**单进程串行** 3 连跑复测（去并行争用噪声）。
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

#: `--durations=0` 的行形态：`0.42s call     tests/test_x.py::test_y`
_LINE = re.compile(r"^\s*([0-9.]+)s\s+(call|setup|teardown)\s+(\S+)\s*$")


def _load(paths: list[str]) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """→ (每用例最小 call 秒, 累计 call 秒, 出现次数)。"""
    best: dict[str, float] = {}
    total: dict[str, float] = defaultdict(float)
    seen: dict[str, int] = defaultdict(int)
    for path in paths:
        cur: dict[str, float] = {}
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LINE.match(line)
            if m and m.group(2) == "call":
                cur[m.group(3)] = float(m.group(1))
        for nodeid, secs in cur.items():
            if nodeid not in best or secs < best[nodeid]:
                best[nodeid] = secs
            total[nodeid] += secs
            seen[nodeid] += 1
    return best, total, seen


def _ranked(best: dict[str, float], only: str = "") -> list[tuple[str, float]]:
    if only:
        best = {k: v for k, v in best.items() if k.startswith(only)}
    return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))


def cmd_table(args: argparse.Namespace) -> int:
    best, total, seen = _load(args.logs)
    print(f"runs={len(args.logs)} tests={len(best)}（按层看：--only tests/ 或 e2e/）")
    print(f"{'min':>7} {'mean':>7} {'n':>2}  nodeid")
    for nodeid, secs in _ranked(best, args.only)[: args.n]:
        n = seen[nodeid]
        print(f"{secs:7.2f} {total[nodeid] / n:7.2f} {n:2d}  {nodeid}")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    best, _, _ = _load(args.logs)
    for nodeid, _ in _ranked(best, args.only)[: args.count]:
        print(nodeid)
    return 0


def cmd_total(args: argparse.Namespace) -> int:
    best, total, seen = _load(args.logs)
    s_min = sum(best.values())
    s_mean = sum(total[k] / seen[k] for k in best)
    print(f"runs={len(args.logs)} tests={len(best)}")
    print(f"sum_min={s_min:.1f}s sum_mean={s_mean:.1f}s（= CPU 上的净工作量，可跨批次对账）")
    cum = 0.0
    for i, (nodeid, secs) in enumerate(_ranked(best)[: args.n], 1):
        cum += secs
        print(
            f"{i:3d} min={secs:6.2f} cum={cum:7.1f} ({cum / s_min * 100:5.1f}%)  {nodeid}"
        )
    return 0


def cmd_ab(args: argparse.Namespace) -> int:
    left, _, _ = _load(args.left)
    right, _, _ = _load(args.right)
    ids = [
        ln.strip()
        for ln in Path(args.ids).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    print(f"{'before':>7} {'after':>7} {'delta':>7}  nodeid")
    ta = tb = 0.0
    for nodeid in ids:
        va, vb = left.get(nodeid), right.get(nodeid)
        if va is None or vb is None:
            print(f"{'--':>7} {'--':>7} {'--':>7}  {nodeid}（缺：left={va} right={vb}）")
            continue
        ta += va
        tb += vb
        print(f"{va:7.2f} {vb:7.2f} {vb - va:+7.2f}  {nodeid}")
    print(f"{ta:7.2f} {tb:7.2f} {tb - ta:+7.2f}  == TOTAL（{len(ids)} 条）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="多份 pytest --durations 日志的 min 汇总 / 对账（见模块头）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("table", help="最慢 N 条（min/mean/次数）")
    p.add_argument("logs", nargs="+")
    p.add_argument("-n", type=int, default=60)
    p.add_argument("--only", default="", help="只看某个前缀（如 `tests/` / `e2e/`）")
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("top", help="导出最慢 N 条的 nodeid")
    p.add_argument("count", type=int)
    p.add_argument("logs", nargs="+")
    p.add_argument("--only", default="", help="只看某个前缀（如 `tests/` / `e2e/`）")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("total", help="总量与头部占比")
    p.add_argument("logs", nargs="+")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_total)

    p = sub.add_parser("ab", help="逐条 A/B（两批各取 min）")
    p.add_argument("--ids", required=True, help="nodeid 清单（一行一个，`top` 的输出）")
    p.add_argument("--left", nargs="+", required=True, help="A 批日志（改前）")
    p.add_argument("--right", nargs="+", required=True, help="B 批日志（改后）")
    p.set_defaults(func=cmd_ab)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
