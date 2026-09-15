"""test_hub_client_code_zip —— code.zip 不得收进点目录（2026-09-15 Colab 事故）。

实测（x3-power push 到 Colab）：`code.zip: 4799 files, 20637451 bytes`，而真实源码只有
215 个文件 / 2.14 MB。差额来自 `nn-training/.venv310bak`（解释器 3.10→3.12 升级留下的
备份，18905 文件 / 1.34 GB）里 `site-packages` 的 4584 个 `.py`（torch/mypy/sympy），
合计 80.6 MB、占 97% 字节。

根因：`pack_code_zip` 的排除表逐个列举目录名，只写了 `.venv`，没写 `.venv310bak`。
修法：**一律排除以 `.` 开头的目录**（不靠列举，防住下一个 `.venv312bak`）。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_client import pack_code_zip


def _make_tree(root: Path) -> None:
    """造一棵覆盖所有排除规则的假 nn_root。"""
    keep = [
        "rl/loop.py",
        "rl/sub/deep.py",
        "curricula/x3-power.jsonc",
        "remote/worker.py",
        "run_rl.py",
    ]
    drop = [
        # 点目录 —— 本次修复的主角
        ".venv310bak/Lib/site-packages/torch/__init__.py",
        ".venv/Lib/site-packages/numpy/core.py",
        ".mypy_cache/3.12/cache.py",
        ".ruff_cache/v1/cache.py",
        ".pytest_cache/v/cache/lastfailed.py",
        ".git/hooks/hook.py",
        # 非点目录的显式名单
        "__pycache__/mod.cpython-312.pyc.py",
        "weights/thing.py",
        "tmp/scratch.py",
        "tests/test_x.py",
        # 非 .py/.jsonc
        "rl/data.npy",
        "rl/notes.md",
    ]
    for rel in keep + drop:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    (root / "rl-config.json").write_text("{}\n", encoding="utf-8")


def test_code_zip_excludes_all_dot_dirs(tmp_path: Path) -> None:
    nn_root = tmp_path / "nn"
    nn_root.mkdir()
    _make_tree(nn_root)
    zip_path = tmp_path / "code.zip"

    pack_code_zip(nn_root, zip_path)

    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())

    # 保留：源码与 jsonc（递归子目录也要在）
    assert names == {
        "rl/loop.py",
        "rl/sub/deep.py",
        "curricula/x3-power.jsonc",
        "remote/worker.py",
        "run_rl.py",
    }


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
