"""`tests/tools/probe_loader.py` —— 装载 `nn-training/tools/` 下**不是包**的脚本。

`tools/*.py` 里有一半是「一次性 CLI」（`tpu-probe.py` 连文件名都带连字符，`pyproject.toml`
也免了它的 N999），测试要拿里面的类/函数只能按路径装。这套动作写在每个用例文件里会重复三份，
更要紧的是它会把用例文件变成「读 `.py` 源码」的文件（`tests/helpers/text_asserts` 口径 B 的
生产者判据）：`spec = spec_from_file_location(…, ROOT / "tools" / "x.py")` 之后，同文件里引用
`spec` 的**任何** assert 都被记成源码文本断言——哪怕它断的是 dataclass 字段表。装载搬到这里，
用例文件就不含 `.py` 字面量绑定，计数也回到真实值（2026-11-22 §P0-3 批 H2 踩到）。

用法：

    from tests.tools.probe_loader import load_probe

    T = load_probe("tpu-probe")      # ⇒ nn-training/tools/tpu-probe.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

#: 脚本后缀单独一级：本模块与工具脚本都住在 `tools/`，路径别写两遍。
_SUFFIX = ".py"


def load_probe(name: str) -> Any:
    """按路径装载 `<nn-root>/tools/<name>.py` 并返回模块（同名已在 `sys.modules` 就复用）。"""
    modname = f"_probe_{name.replace('-', '_')}"
    cached = sys.modules.get(modname)
    if cached is not None:
        return cached
    path = NN_ROOT / "tools" / (name + _SUFFIX)
    assert path.is_file(), f"没有这个工具脚本: {path}"
    spec = importlib.util.spec_from_file_location(modname, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod
