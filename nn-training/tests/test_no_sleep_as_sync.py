"""tests/test_no_sleep_as_sync.py — 静态守卫：**不许拿 sleep 当同步**（2026-09-24）。

背景（`docs/nn/engineering.md` §21）：CPU 满即时连跑 8 次门禁，红点每轮不同 ——
serve_pool 端到端 / `bulk_sched` 单通道 / `eval_local` 硬顶 / `push_priority` 主副本，
负载轰炸又拖出 `batch_eval`、`control_plane`、`eval_dispatch`、`body_transfer`、
`offline_deliver`、`rollout rescan`、`async_result`。共同特征：**拿绝对数字当同步手段**
—— `sleep(N)` 赌「对方已经到了」、`elapsed < N` 赌「机器够快」。

适用于**两层**：`tests/`（单测层）与 `e2e/`（集成层）—— 两层同一次 xdist 调用里跑，
同样的 flake 机理与同样的纪律。

本守卫钉住最容易复发、也最容易静态判定的那一条：两层的每一处 `.sleep(...)` 调用
都必须**显式标注**它为什么不是同步，且理由必须落在**两族**之内：

  * ``轮询步长`` —— 循环里等的是谓词/状态（`while not pred(): time.sleep(step)`），
    超时只是挂起兜底、不参与判定（`_wait_until` / `_pump` 形）。
  * ``夹具模拟`` —— 睡的是**被模拟对象自身的工作量**：慢节点的一局、桩子进程 hang 住、
    fake HTTP 往返、假的「控制面在途」窗口。

标注方式二选一（同行尾注或上一行注释）：

  ① ``time.sleep(0.2)  # sleep-ok: 夹具模拟的工作量：慢节点一口 1.5s``
  ② ``# sleep-ok: 轮询步长（等的是谓词，兜底只挡挂起）`` + 换行 ``time.sleep(step)``

为什么限定两族而不是「随便写一句」：理由是自由文本时，``# sleep-ok: 等对方先跑`` 也能
通过 —— 而那正是这轮 flake 的成因。两族把「能睡的原因」封成闭集，新加一处 sleep 就必须
当场回答「它属于哪一族、为什么」，答案落不进两族就说明它在拿时长当同步。

为什么用 AST 而不是 grep：桩子进程的源码是以**字符串**内嵌的（
`_STUB_HANG = "... time.sleep(3600) ..."`），那是子进程自己的行为、不是父进程的同步，
按行扫会误报一堆。AST 只看真的调用点。

为什么 `Event.wait(timeout)` / `Thread.join(timeout)` 不在管辖范围：它们阻塞在**信号**
上（有人 `set`/线程退出就立刻醒），超时只是挂起兜底 —— 与「按固定时长决定先后」是两件
事。同理 `_wait_until(pred, timeout=…)` 形的轮询：判据是谓词，只有里面那一行步长 sleep
需要标注。

红检：删掉任一 `# sleep-ok:` 标注 ⇒ 本用例逐条报出 `文件:行: 源码 → 问题`。

## 第二条规则（2026-09-26）：墙钟**上界断言**也要标注

`sleep` 之外还有一族同源 flake：`assert elapsed < 5.0` —— **拿绝对数字当「机器够快」**。
负载一高就假红，而它看起来只是「一个普通断言」。本守卫同时扫**上界型墙钟断言**：

  * 只认**上界**：`t < N` / `t <= N` / `N > t` / `N >= t`（`t` = 墙钟时长）。
    下界（`t >= 1.0`，用来证「夹具真等过」）**不管** —— 负载越高它越成立，不会假红。
  * `t` 的判据（AST）：`time.time()/monotonic()/perf_counter()` 的差，或名字属于时长族
    （`elapsed` / `wall` / `dt` / `took` / `duration` / `sec(s)` / `*_sec`）。
  * 每处上界必须带 `# timing-ok: <理由>`，理由落进四族之一：`上界兜底`（只兜挂起/卡死，
    余量大，真挂起才红）· `契约上界`（上界即契约：零成本 / 立即返回 / 不许阻塞，紧是设计）·
    `夹具模拟`（量的是夹具模拟的工作量）· `相对判据`（阈值由场景推导：budget / 窗口 /
    计划量 × 系数，非绝对墙钟常数）。
  * 为什么逼一个理由：上界断言分两种 —— **真兜挂起**（余量 10×+，可留）与**赌机器速度**
    （余量小，是 flake 源）。当场回答「它属于哪一族」，答不出就是在赌机器够快。

红检：删掉任一 `# timing-ok:` 标注 ⇒ 同样逐条报出。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.helpers import source_scan

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
E2E = ROOT / "e2e"
#: 扫描根（两层同一套判据）：`tests/` 单测层 + `e2e/` 集成层。
SCAN_ROOTS = (TESTS, E2E)

#: 标注关键字 + 理由（同一行尾注或上一行注释里出现即可）。
_MARK = re.compile(r"sleep-ok:\s*(?P<why>[^\r\n]*)")
#: 允许的理由族（前缀匹配）——理由落不进这两族 = 它在拿时长当同步。
FAMILIES = ("轮询步长", "夹具模拟")

#: 上界墙钟断言的标注关键字 + 理由族（同 `sleep` 的设计：理由必须是闭集里的一族）。
_TIMING_MARK = re.compile(r"timing-ok:\s*(?P<why>[^\r\n]*)")
TIMING_FAMILIES = ("上界兜底", "契约上界", "夹具模拟", "相对判据")
#: 墙钟调用的方法名（`time.time() - t0` 里的 `time()`）——`time` / `monotonic` / `perf_counter`。
_TIME_FUNCS = frozenset({"time", "monotonic", "perf_counter", "monotonic_ns", "perf_counter_ns"})
#: 时长命名的变量（`elapsed` / `wall` / `dt` … 或 `*_sec` 后缀）——这些名字就是「秒」。
_DURATION_NAME = re.compile(r"^(?:elapsed|wall|dt|took|duration|sec|secs|seconds)$|_secs?$")


def _sleep_calls(src: str) -> list[tuple[int, str]]:
    """所有 `.sleep(...)` 调用点 → (行号, 该行源码)。字符串里的同名字样不算。"""
    tree = ast.parse(src)
    lines = src.splitlines()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
        ):
            out.append((node.lineno, lines[node.lineno - 1] if node.lineno <= len(lines) else ""))
    return out


def _reason(lines: list[str], lineno: int) -> str | None:
    """这一处 sleep 的标注理由（同行尾注优先，其次上一行注释）；没标注 → None。"""
    for idx in (lineno - 1, lineno - 2):
        if idx < 0:
            continue
        m = _MARK.search(lines[idx])
        if m is not None:
            return m.group("why").strip()
    return None


def _rel(py: Path) -> str:
    """尽力给一个短路径（自证用例把临时文件放到仓库外，那里只能报文件名）。"""
    try:
        return py.relative_to(ROOT).as_posix()
    except ValueError:
        return py.name


def _problems(py: Path) -> list[str]:
    """该文件里每一处不合格 sleep 的 `路径:行: 源码 → 问题`。"""
    src = source_scan.read_text(str(py))
    lines = src.splitlines()
    rel = _rel(py)
    out: list[str] = []
    for lineno, text in _sleep_calls(src):
        why = _reason(lines, lineno)
        if why is None:
            out.append(f"{rel}:{lineno}: {text.strip()} → 未标注（缺 `# sleep-ok: …`）")
        elif not why.startswith(FAMILIES):
            out.append(
                f"{rel}:{lineno}: {text.strip()} → 理由 {why!r} 不在两族内"
                f"（须以 {' 或 '.join(FAMILIES)} 开头）"
            )
    return out


def test_every_sleep_in_tests_declares_a_reason_from_the_two_families() -> None:
    """`tests/` + `e2e/` 里每一处 `.sleep(...)` 都要有落进两族的 `# sleep-ok: <理由>`。"""
    problems: list[str] = []
    for root in SCAN_ROOTS:
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            problems += _problems(py)
    assert not problems, (
        "tests/ 里的 sleep 必须标注理由，且理由要落进「轮询步长」/「夹具模拟」两族"
        "（说明它等的是谓词或模拟的工作量，而不是「等对方先跑」）：\n  "
        + "\n  ".join(problems)
        + "\n规则与背景：docs/nn/engineering.md §21"
    )


def test_the_guard_actually_catches_bad_sleeps(tmp_path: Path) -> None:
    """自证：守卫不是恒真 —— 未标注、理由不在两族、标注到下一处，三种都抓得到。"""
    bad = tmp_path / "bad_case.py"
    bad.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    time.sleep(0.2)\n"  # 未标注
        "    time.sleep(0.3)  # sleep-ok: 等对方先跑\n"  # 理由不在两族
        "    # sleep-ok: 轮询步长（等的是谓词）\n"
        "    time.sleep(0.4)  # 这一处才合法\n",
        encoding="utf-8",
    )
    rel = bad.name
    got = _problems(bad)
    assert len(got) == 2, got
    assert got[0].startswith(f"{rel}:4: time.sleep(0.2) → 未标注"), got[0]
    assert got[1].startswith(f"{rel}:5: time.sleep(0.3)") and "不在两族内" in got[1], got[1]

    good = tmp_path / "good_case.py"
    good.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）\n"
        "    time.sleep(0.1)\n"
        "    time.sleep(0.2)  # sleep-ok: 夹具模拟的工作量：fake HTTP 往返\n",
        encoding="utf-8",
    )
    assert _problems(good) == [], _problems(good)


def test_stub_source_strings_are_not_synchronization() -> None:
    """桩子进程源码里的 `time.sleep`（字符串）不算 —— 那是子进程的行为，不是父进程同步。"""
    src = 'STUB = """\nimport time\ntime.sleep(3600)\n"""\ntime.sleep(0.1)\n'
    calls = _sleep_calls(src)
    assert [ln for ln, _ in calls] == [5], f"字符串里的 sleep 被误判了：{calls}"


# ──────────── 第二条规则：上界型墙钟断言（2026-09-26）────────────


def _is_wallclock(node: ast.AST) -> bool:
    """该表达式是不是**墙钟时长**：`time.time() - t0` 形，或时长命名的变量/属性。"""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        for side in (node.left, node.right):
            if (
                isinstance(side, ast.Call)
                and isinstance(side.func, ast.Attribute)
                and side.func.attr in _TIME_FUNCS
            ):
                return True
    if isinstance(node, ast.Name) and _DURATION_NAME.search(node.id):
        return True
    return isinstance(node, ast.Attribute) and bool(_DURATION_NAME.search(node.attr))


def _upper_bounded(node: ast.Compare) -> ast.AST | None:
    """比较里被**上界**约束的那一侧若是墙钟时长，返回它；否则 None。

    上界 = `t < N` / `t <= N`（左侧）或 `N > t` / `N >= t`（右侧）。下界（`t >= N` / `t > N`）
    不在此列：那是「证夹具真等过」，负载越高越成立，不会假红。
    """
    lefts = [node.left, *node.comparators[:-1]]
    for op, left, right in zip(node.ops, lefts, node.comparators, strict=True):
        if isinstance(op, (ast.Lt, ast.LtE)) and _is_wallclock(left):
            return left
        if isinstance(op, (ast.Gt, ast.GtE)) and _is_wallclock(right):
            return right
    return None


def _timing_asserts(src: str) -> list[tuple[int, str]]:
    """文件里每一处**上界型墙钟断言** → (行号, 该行源码)。字符串里的同名字样不算（AST）。"""
    tree = ast.parse(src)
    lines = src.splitlines()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        hit = next(
            (c for c in ast.walk(node.test) if isinstance(c, ast.Compare) and _upper_bounded(c)),
            None,
        )
        if hit is not None:
            out.append((hit.lineno, lines[hit.lineno - 1] if hit.lineno <= len(lines) else ""))
    return out


def _timing_reason(lines: list[str], lineno: int) -> str | None:
    """这一处上界断言的 `# timing-ok:` 理由（同行尾注优先，其次上一行）；没标注 → None。"""
    for idx in (lineno - 1, lineno - 2):
        if idx < 0:
            continue
        m = _TIMING_MARK.search(lines[idx])
        if m is not None:
            return m.group("why").strip()
    return None


def _timing_problems(py: Path) -> list[str]:
    """该文件里每一处不合格上界墙钟断言的 `路径:行: 源码 → 问题`。"""
    src = source_scan.read_text(str(py))
    lines = src.splitlines()
    rel = _rel(py)
    out: list[str] = []
    for lineno, text in _timing_asserts(src):
        why = _timing_reason(lines, lineno)
        if why is None:
            out.append(f"{rel}:{lineno}: {text.strip()} → 未标注（缺 `# timing-ok: …`）")
        elif not why.startswith(TIMING_FAMILIES):
            out.append(
                f"{rel}:{lineno}: {text.strip()} → 理由 {why!r} 不在四族内"
                f"（须以 {' / '.join(TIMING_FAMILIES)} 开头）"
            )
    return out


def test_every_wallclock_upper_bound_assert_declares_a_reason() -> None:
    """`tests/` + `e2e/` 里每一处上界墙钟断言都要有落进四族的 `# timing-ok: <理由>`。"""
    problems: list[str] = []
    for root in SCAN_ROOTS:
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            problems += _timing_problems(py)
    assert not problems, (
        "上界型墙钟断言（`assert elapsed < N`）必须标注理由，且理由落进「上界兜底 / 契约上界 /"
        " 夹具模拟 / 相对判据」四族（它是真兜挂起、契约上界、量夹具、还是相对判据 —— 而不是"
        "「赌机器够快」）：\n  "
        + "\n  ".join(problems)
        + "\n规则与背景：docs/nn/engineering.md §21"
    )


def test_the_guard_actually_catches_wallclock_asserts(tmp_path: Path) -> None:
    """自证：上界墙钟断言的守卫不恒真 —— 未标注 / 理由不在四族都抓得到；下界与非时长放行。"""
    bad = tmp_path / "bad_timing.py"
    bad.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    t0 = time.time()\n"
        "    elapsed = time.time() - t0\n"
        "    assert elapsed < 5.0\n"  # 未标注
        "    assert elapsed < 5.0  # timing-ok: 赌机器够快\n"  # 理由不在四族
        "    assert wall < 10.0  # timing-ok: 上界兜底（真挂起才红）\n",  # 合法
        encoding="utf-8",
    )
    got = _timing_problems(bad)
    assert len(got) == 2, got
    assert got[0].startswith("bad_timing.py:6: assert elapsed < 5.0 → 未标注"), got[0]
    assert got[1].startswith("bad_timing.py:7:") and "不在四族内" in got[1], got[1]

    good = tmp_path / "good_timing.py"
    good.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    t0 = time.time()\n"
        "    elapsed = time.time() - t0\n"
        "    budget = 1.0\n"
        "    assert elapsed >= 0.3  # 下界：证夹具真等过（负载越高越成立，不标）\n"
        "    assert elapsed <= budget + 0.25  # timing-ok: 相对判据（阈值随预算走）\n"
        "    assert status < 400\n",  # 非时长，不管
        encoding="utf-8",
    )
    assert _timing_problems(good) == [], _timing_problems(good)
