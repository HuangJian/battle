"""G8：`common/` 的出站副作用（HTTP / git 子进程）只减不增
（`python.refactor.md` §8、§10-B8）。

`common/` 是 L0（stdlib-only 的纯层），但这条线路上仍留着 HTTP 出口与 `git ls-files -s` /
`git cat-file --batch` 两个子进程。B8 的方向是把这些副作用搬去 `remote/`，本门禁只做一件事
——**拦住「再往 L0 加一处 IO」**：存量按「模块 + 原语名」冻结，只许降，降了必须改基线
（否则旧额度会回流）。

2026-11-16：codeHash 实现从 `distribution.py` 拆到 `codehash.py`，两个 git 子进程跟着搬了家。
门禁的扫描面因此**扩成两个模块**（按「模块 + 原语」记账），否则「搬个家」会被读成「副作用消失」，
而实际上它们还在 L0 里，只是换了文件 —— 那正是这类门禁最容易自欺的地方。

口径：AST 里 `Call` 的被调名命中 `IO_PRIMITIVES` 才计数。`_request(...)` 这类**内部漏斗**
不计（它是同一处出口被多处复用，数它会把「调用点多」误报成「出口多」）；
URL 编码 / `urlparse` 这类纯函数也不计。
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

#: 出站副作用原语（网络 / 子进程），按**点分调用名**匹配：
#: 既认 `from common.proc import run_capture` 的裸名，也认 `subprocess.run` 这类限定名。
#: 泛化的 `run` / `Request` 不单独收 —— 只收限定到 `subprocess` / `urllib.request` 的形态，
#: 否则任何对象的 `.run()` 都会被计进来。改名或新增原语 = 改这个集合 + 基线。
IO_DOTTED = frozenset(
    {
        "net_http.racing_urlopen",
        "net_http.urlopen_no_proxy",
        "urllib.request.Request",
        "urllib.request.urlopen",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_output",
    }
)
#: 裸名（模块内直接 import 的原语）。
IO_BARE = frozenset({"run_capture", "racing_urlopen", "urlopen_no_proxy"})

#: 存量（按 `模块|原语`）。目标是全部归零：搬去 `remote/` 后这里应当变成空表。
BASELINE = {
    "common.distribution|net_http.racing_urlopen": 1,  # 唯一的网络出口
    "common.distribution|urllib.request.Request": 1,  # _request 的请求面
    "common.codehash|run_capture": 1,  # git ls-files -s
    "common.codehash|subprocess.run": 1,  # git cat-file --batch
}

#: 扫描面 = L0 里**所有**碰出站 IO 的模块（新增一处 IO 就得先在这里露面）。
L0_IO_MODULES = ("common.distribution", "common.codehash")


def _module_source(name: str) -> str:
    """按模块对象定位源码（不用 `.py` 路径字符串：`tests/helpers/text_asserts.py` 的口径 B
    会把「文件里出现 `.py` 字面量」当成源码文本断言面，而这里的判据是整数计数）。"""
    from importlib import import_module

    mod = import_module(name)
    return Path(str(mod.__file__)).read_text(encoding="utf-8")


@cache
def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in L0_IO_MODULES:
        tree = ast.parse(_module_source(name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = ast.unparse(node.func)
            if dotted in IO_DOTTED or dotted in IO_BARE:
                key = f"{name}|{dotted}"
                counts[key] = counts.get(key, 0) + 1
    return counts


def test_no_new_io_primitives_in_l0() -> None:
    actual = _scan()
    growth = {
        name: (n, BASELINE.get(name, 0))
        for name, n in sorted(actual.items())
        if n > BASELINE.get(name, 0)
    }
    detail = "\n".join(f"  {name}: {n} 处（基线 {base}）" for name, (n, base) in growth.items())
    assert not growth, (
        "`common/` 新增了出站副作用：\n"
        f"{detail}\n"
        "  `common/` 是 L0：HTTP / git 子进程属于 `remote/`（B8 的方向是搬出去，不是加进来）。"
    )


def test_baseline_only_shrinks() -> None:
    actual = _scan()
    stale = {
        name: (base, actual.get(name, 0))
        for name, base in sorted(BASELINE.items())
        if actual.get(name, 0) < base
    }
    detail = "\n".join(f"  {name}: 基线 {base}，实际 {n}" for name, (base, n) in stale.items())
    assert not stale, (
        "以下副作用已经减少，但 `BASELINE` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线改成实际值（归零的键直接删掉），额度才不会回流。"
    )


def test_scan_sees_the_known_io_surface() -> None:
    actual = _scan()
    assert actual.get("common.distribution|net_http.racing_urlopen") == 1, actual
    assert actual.get("common.codehash|subprocess.run") == 1, "扫描入口坏了（git 子进程没数到）"
    assert set(actual) == set(BASELINE), actual
    assert sum(actual.values()) == sum(BASELINE.values()), actual
