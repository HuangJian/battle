#!/usr/bin/env python3
"""tools/build_offline_boot.py — `remote/offline_boot.py` 的**生成器**（`python.refactor.md` §7.6 / §10-B8）。

## 为什么它是生成物

`offline_boot.py` 是 **standalone 运输单元**：notebook 的引导格把它从 GitHub raw 单独拉进容器，
那时 `code.zip` 还没进 `sys.path`、`remote` 包根本不存在 ⇒ 它顶层不得 import `remote.*`，
也**必须**是单文件（`ipynb/battle.offline.ipynb` 的拉取名单按文件名）。3554 行单文件不可导航，
但「拆成可 import 的模块」会直接破掉这条运输契约。

所以拆的是**源码**，不是发行物：`remote/_offline_boot/*.pyfrag` 按 `PARTS` 的顺序逐字节拼成
`remote/offline_boot.py`。运行期仍是同一份单文件，磁盘上也有了 12 段可分别阅读的源码。

## 为什么分片是 `.pyfrag` 而不是 `.py`

生产树的静态门禁（print / 宽捕获 / `Any` / 函数形状 / LOC / 层级账本 / 抑制符）都按
`rglob("*.py")` 扫**发行物**。分片若叫 `.py`，同一行字会被数两遍 —— 要么抬基线（本仓禁），
要么给每个门禁加一份排除名单（= 让门禁看不见代码）。`.pyfrag` 让「被检查的东西」与「被运输的
东西」是**同一个文件**：分片只被本脚本读，`remote/offline_boot.py` 仍是唯一的 AST 输入。
代价（写在明处）：分片自身不被 mypy/ruff 解析 —— 它们的一致性与语法由「重建后跑 mypy + 用例」
兜住，跑偏的形态会先在发行物上红。

## 用法

    python tools/build_offline_boot.py            # 重建发行物（无变化则不写盘）
    python tools/build_offline_boot.py --check    # 只对账：生成物 ≠ 分片文本 ⇒ 退出码 1（门禁用）

守卫：`tests/remote/test_offline_boot_is_generated.py`（提交物必须等于 `build()`；
分片目录里不许出现 `.py`，也不许有表外分片）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import house_parser, run_main

NN_ROOT = Path(__file__).resolve().parents[1]
FRAG_DIR = NN_ROOT / "remote" / "_offline_boot"
TARGET = NN_ROOT / "remote" / "offline_boot.py"

#: 拼接顺序 = **数据**，不是字母序的副产品（改分片名 / 增删分片 = 改这张表 + 重建）。
#: 分段头（`# ═══ part N/M: …`）住在每个分片自己的第一行，所以「哪一段在什么位置」读哪一份都对得上。
PARTS: tuple[str, ...] = (
    "01_front.pyfrag",
    "02_consts.pyfrag",
    "03_pack.pyfrag",
    "04_fetch.pyfrag",
    "05_code.pyfrag",
    "06_run.pyfrag",
    "07_queue.pyfrag",
    "08_progress.pyfrag",
    "09_session.pyfrag",
    "10_batch.pyfrag",
    "11_bc.pyfrag",
    "12_entry.pyfrag",
)

#: 文件头。**只有注释**（不进 AST），所以后面那三引号仍是模块 docstring。
BANNER = (
    "# ════════════════════════════════════════════════════════════════════════════════\n"
    "# 生成物 —— 不要手改这个文件。\n"
    "# 源码 = remote/_offline_boot/*.pyfrag（按 tools/build_offline_boot.py 的 PARTS 顺序拼接）。\n"
    "# 重建： python nn-training/tools/build_offline_boot.py\n"
    "# 对账： python nn-training/tools/build_offline_boot.py --check\n"
    "# ════════════════════════════════════════════════════════════════════════════════\n"
)


def part_paths() -> list[Path]:
    """按 `PARTS` 顺序给出分片路径（缺失即抛 —— 拼一半的发行物比不拼更糟）。"""
    missing = [n for n in PARTS if not (FRAG_DIR / n).is_file()]
    if missing:
        raise FileNotFoundError(f"分片缺失：{missing}（目录 {FRAG_DIR}）")
    return [FRAG_DIR / n for n in PARTS]


def problems() -> list[str]:
    """分片目录的自洽问题（表外分片 / 混进来的 `.py`）。空表 = 正常。"""
    on_disk = {p.name for p in FRAG_DIR.glob("*.pyfrag")}
    out = sorted(
        f"表外分片：{n}（PARTS 里没有它 —— 它永远不会被拼进发行物）" for n in on_disk - set(PARTS)
    )
    out += sorted(
        f"分片目录里的 `.py`：{p.name}（会被静态门禁当第二份生产代码计数）"
        for p in FRAG_DIR.glob("*.py")
    )
    return out


def build() -> str:
    """发行物全文 = 文件头 + 各分片（逐字节，**段间空一行**）。

    空行只加在拼接缝上（分片自身不动）：分片文件里分段头永远在第 1 行，读单段时不会看到
    「头紧贴着上一段的 `return`」；发行物里则每段之间都有视觉分隔。
    """
    parts = [p.read_bytes().decode("utf-8") for p in part_paths()]
    return BANNER + parts[0] + "".join("\n" + p for p in parts[1:])


def main() -> int:
    ap = house_parser(description="把 remote/_offline_boot/*.pyfrag 拼成 remote/offline_boot.py")
    ap.add_argument("--check", action="store_true", help="只对账（生成物与分片不一致 ⇒ 退出码 1）")
    args = ap.parse_args()

    bad = problems()
    if bad:
        for line in bad:
            print(line)
        return 2

    text = build()
    current = TARGET.read_bytes().decode("utf-8") if TARGET.exists() else ""
    if args.check:
        if current == text:
            print(
                f"{TARGET.relative_to(NN_ROOT)} 与 {len(PARTS)} 个分片一致（{len(text.splitlines())} 行）"
            )
            return 0
        print(
            f"{TARGET.relative_to(NN_ROOT)} 与分片**不一致** —— 有人手改了生成物，或改了分片没重建。"
            f"跑 `python tools/build_offline_boot.py` 重建后一起提交。"
        )
        return 1
    if current == text:
        print(f"无变化（{len(text.splitlines())} 行 / {len(PARTS)} 个分片）")
        return 0
    TARGET.write_bytes(text.encode("utf-8"))
    print(
        f"已重建 {TARGET.relative_to(NN_ROOT)}（{len(text.splitlines())} 行 / {len(PARTS)} 个分片）"
    )
    return 0


if __name__ == "__main__":
    run_main(main)
