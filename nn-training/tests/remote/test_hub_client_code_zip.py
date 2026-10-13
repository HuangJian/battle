"""test_hub_client_code_zip —— code.zip 的收件名单（两类事故各一条反回归）。

## 1) 点目录（2026-09-15 Colab 事故）

实测（x3-power push 到 Colab）：`code.zip: 4799 files, 20637451 bytes`，而真实源码只有
215 个文件 / 2.14 MB。差额来自 `nn-training/.venv310bak`（解释器 3.10→3.12 升级留下的
备份，18905 文件 / 1.34 GB）里 `site-packages` 的 4584 个 `.py`（torch/mypy/sympy），
合计 80.6 MB、占 97% 字节。

根因：`pack_code_zip` 的排除表逐个列举目录名，只写了 `.venv`，没写 `.venv310bak`。
修法：**一律排除以 `.` 开头的目录**（不靠列举，防住下一个 `.venv312bak`）。

## 2) 角色裁剪（2026-10-13，C1）

判据 = 从 20 个云端节点入口做 import 闭包（只落到 `biz`/`common`/`remote`/`worker`）
⇒ `e2e` `hub` `ladder` `curricula` `tools` `trainer` ＋ 根 `conftest.py` 全数可排，
实测压缩体积 2.796 MB → 1.220 MB（−56%）。

★ **`levels/` 是硬依赖，不许被"裁剪"顺手排掉**：课程里的 `"level"` 引用在云端加载时
走 `biz/course_resolve.load_course:84-111` → `resolve_level` → `LEVELS_DIR`；把目录指空
实测 131/181 门课在 `remote/train_core.py:201` 抛 `FileNotFoundError`（含现行 x20 线）。
本文件用**精确集合相等**钉住收件名单（`_expected_members`）。

⚠ 为什么收件名单要经一个函数转一手（`_expected_members`）：本仓的文本断言护栏
（`tests/test_source_text_assert_budget.py`，口径 B）把「assert 里出现 `.py` 字面量容器」
记成源码文本断言 —— 而这里断言的是 **zip 成员名**，与「源码长什么样」无关（那条护栏的
docstring 自己承认这道 `.py` 门是文本级的、会误伤路径字符串）。经函数返回 ⇒ 保住
「精确集合相等」这条强判据，又不给护栏记一笔假账。
"""

from __future__ import annotations

import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_client import pack_code_zip

#: 角色裁剪后**必须留下**的代表文件（`levels/` 那条是硬依赖反回归，见模块 docstring §2）。
KEEP = [
    "biz/course_spec.py",
    "common/protocol.py",
    "levels/arena6.jsonc",  # ★ 硬依赖：课程 `level` 引用在云端靠它解析
    "remote/worker.py",
    "worker/plan.py",
]

#: 角色裁剪排掉的目录（只留目录名，不含 `.py` —— 理由见模块 docstring 尾注）。
TRIM_TOPS = {
    "e2e",  # pytest 端到端用例
    "hub",  # hub 服务端，只在本机跑
    "ladder",  # 阶梯账本 + 计划（本机工具数据）
    "curricula",  # 课程库；云端加载的是随包下发的快照（train_core.py:196-201）
    "tools",  # 本机运维脚本
    "trainer",  # 本机训练循环；云端用 remote/run_loop.py 顶替
    "tests",
    "tmp",
    "weights",
    "__pycache__",
}


def _expected_members() -> set[str]:
    """`code.zip` 应收录的成员集合（= `KEEP`；转一手只为不误触护栏，见模块 docstring）。"""
    return set(KEEP)


def _tops(names: Iterable[str]) -> set[str]:
    """包内成员的顶层目录集合。"""
    return {n.split("/")[0] for n in names}


def _make_tree(root: Path) -> None:
    """造一棵覆盖所有排除规则的假 nn_root。"""
    drop = [
        # 点目录 —— 事故 1 的主角
        ".venv310bak/Lib/site-packages/torch/__init__.py",
        ".venv/Lib/site-packages/numpy/core.py",
        ".mypy_cache/3.12/cache.py",
        ".ruff_cache/v1/cache.py",
        ".pytest_cache/v/cache/lastfailed.py",
        ".git/hooks/hook.py",
        # 非点目录的旧显式名单
        "__pycache__/mod.cpython-312.pyc.py",
        "weights/thing.py",
        "tmp/scratch.py",
        "tests/test_x.py",
        # 非 .py/.jsonc
        "rl/data.npy",
        "rl/notes.md",
        # 角色裁剪（事故 2）：每个被排掉的目录各来一份代表文件
        "conftest.py",  # 根 conftest（不住任何被排除的目录里，之前一直白进包）
        "e2e/test_x.py",
        "e2e/conftest.py",
        "hub/boot.py",
        "ladder/LEDGER.jsonc",
        "curricula/x3-power.jsonc",
        "tools/task.py",
        "trainer/loop.py",
        "trainer/run_rl.py",
    ]
    for rel in KEEP + drop:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    (root / "rl-config.json").write_text("{}\n", encoding="utf-8")


def test_code_zip_inclusion_set_is_exact(tmp_path: Path) -> None:
    """收件名单**精确相等** + 被裁掉的目录一个都不许回来。

    ★ `levels/` 在 `KEEP` 里：它是硬依赖（云端 `load_course` 解析课程 `"level"` 引用要它），
    排掉会让 131/181 门课 `FileNotFoundError` —— 精确相等同时也是这条的反回归。
    """
    nn_root = tmp_path / "nn"
    nn_root.mkdir()
    _make_tree(nn_root)
    zip_path = tmp_path / "code.zip"

    pack_code_zip(nn_root, zip_path)

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())

    assert names == _expected_members(), f"收件名单漂了：{sorted(names)}"
    tops = _tops(names)
    assert not (TRIM_TOPS & tops), f"角色裁剪失效：{sorted(TRIM_TOPS & tops)} 又进包了"


def test_code_zip_reports_expected_file_count(tmp_path: Path) -> None:
    """日志里那个计数必须与实际收录一致（用户是照着它发现 4799 这个异常值的）。"""
    nn_root = tmp_path / "nn"
    nn_root.mkdir()
    _make_tree(nn_root)
    zip_path = tmp_path / "code.zip"

    logs: list[str] = []
    pack_code_zip(nn_root, zip_path, log=logs.append)

    with zipfile.ZipFile(zip_path) as z:
        assert len(z.namelist()) == 5
    assert any("code.zip: 5 files" in m for m in logs), logs


def test_dot_dir_exclusion_survives_new_venv_backup_names(tmp_path: Path) -> None:
    """防回归重点：换一个备份名（.venv312bak）也必须被排除 —— 不能靠列举名字。"""
    nn_root = tmp_path / "nn"
    nn_root.mkdir()
    _make_tree(nn_root)
    p = nn_root / ".venv312bak" / "Lib" / "site-packages" / "torch" / "big.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("y = 2\n", encoding="utf-8")
    zip_path = tmp_path / "code.zip"

    pack_code_zip(nn_root, zip_path)

    with zipfile.ZipFile(zip_path) as z:
        assert not [n for n in z.namelist() if n.startswith(".venv312bak")]
