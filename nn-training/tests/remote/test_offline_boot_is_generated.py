"""`remote/offline_boot.py` 是**生成物**（`python.refactor.md` §7.6 / §10-B8）。

发行物必须等于 `tools/build_offline_boot.py::build()`：这条判据不是风格，是**单文件运输契约**的
落地方式 —— notebook 从 GitHub raw 单独拉这一个文件，所以它是单文件；而 3554 行不可导航，
所以源码拆在 `remote/_offline_boot/*.pyfrag`、构建期拼接。两头都要成立，缺一头就退化：

* 少了「提交物 == 分片」，发行物会变成第二份真相（有人手改一行，分片那边永远不知道）；
* 少了「分片目录自洽」，一个表外分片 / 一个误命名的 `.py` 会让同一段代码被拼两次或不进发行物。

为什么分片用 `.pyfrag`（门禁口径，别改成 `.py`）：生产树的静态门禁都按 `rglob("*.py")` 扫，
分片若叫 `.py`，同一行字会被数两遍 —— 要么抬基线（本仓禁），要么给每个门禁加排除名单
（= 让门禁看不见代码）。`.pyfrag` 保证「被检查的东西」与「被运输的东西」是同一个文件。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tools import build_offline_boot as gen

NN_ROOT = Path(__file__).resolve().parents[2]

#: 分段头形态：`# ═══ part 3/12: 03_pack — <人读标题> ═══`。
_PART_HEAD = re.compile(r"^# ═══ part (\d+)/(\d+): (\S+) — .+ ═══$")


def test_committed_artifact_is_what_the_fragments_build() -> None:
    """发行物 == 分片拼接（手改生成物 = 红）。顺手要求它能编译：拼歪的第一现场在这里。"""
    built = gen.build()
    shipped = gen.TARGET.read_bytes().decode("utf-8")
    assert shipped == built, (
        "remote/offline_boot.py 与 remote/_offline_boot/*.pyfrag 不一致 —— "
        "跑 `python tools/build_offline_boot.py` 重建后一起提交（不要手改生成物）"
    )
    ast.parse(built)


def test_fragment_dir_has_no_strays_and_every_part_is_complete() -> None:
    """分片目录自洽：表外分片 / 混进来的 `.py` / 缺片 / 不以换行收尾的段落都是红。"""
    assert gen.problems() == []
    paths = gen.part_paths()  # 缺片直接抛
    for p in paths:
        text = p.read_bytes().decode("utf-8")
        assert text.endswith("\n"), f"{p.name} 没有以换行收尾 —— 拼接会把下一段的分段头粘到本段末行"


def test_each_fragment_starts_with_its_own_ordered_part_header() -> None:
    """分片首行 = `part N/M: <文件名主干>`，且 N 与自己在下表里的位置一致（文件名与标题不许漂）。"""
    total = len(gen.PARTS)
    for idx, p in enumerate(gen.part_paths(), start=1):
        first = p.read_bytes().decode("utf-8").split("\n", 1)[0]
        m = _PART_HEAD.match(first)
        assert m is not None, f"{p.name} 首行不是分段头：{first!r}"
        assert int(m.group(1)) == idx and int(m.group(2)) == total, (
            f"{p.name} 的分段头编号与位置不符：{first!r}"
        )
        assert m.group(3) == p.name.split(".")[0], f"{p.name} 的分段头写的不是自己：{first!r}"


def test_the_fragments_live_next_to_the_artifact_they_build() -> None:
    """分片目录与发行物同层（`remote/`）—— 分开住会让「它拼的是哪一份」要靠猜。

    形态写成「同父目录 + 名字前缀」而不是拼全路径：后者要写 `.py` 字面量，而文本断言计数口径 B
    会把「表达式里出现 `.py`」一律算成源码判据（见 `tests/helpers/text_asserts.py` 的已知边界）。
    这里判的是**位置关系**，不靠那个后缀。
    """
    assert gen.FRAG_DIR.parent == gen.TARGET.parent == NN_ROOT / "remote"
    assert gen.TARGET.name.startswith("offline_boot")
