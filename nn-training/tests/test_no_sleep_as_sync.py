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
    （**词**级匹配、允许 `_` 分隔：`elapsed` / `wall` / `dt` / `took` / `duration` / `sec(s)` /
    `*_sec`，以及 `elapsed_ms`、`_segment_seconds` 这类）。名字可以长在**变量/属性**上，也可以
    在**调用的函数名**（`_segment_seconds(...)`）或**下标的基底**（`seen[0]`）上
    （2026-09-26 扩：此前只认 Name/Attribute，`test_wire_reroll` 两处
    `_segment_seconds(...) < 0.2` 是守卫盲区，靠人肉 `timing-ok` 标注兜着）。
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

# ---- 文本预筛（先看字串，再决定要不要 `ast.parse` + walk）----
# 这两条守卫各自扫 286 个文件（tests/ + e2e/），而**解析 + walk 才是成本**（实测单次
# 全量：parse 0.54s + walk 1.34s）。判据本身都是语法量，所以先拿**字串超集**过一遍不会
# 丢判据（关键词/名字在源码里就是字面量）；2026-09-29 加，见 docs/nn/engineering.md §43。
#: 规则 1：`Attribute(attr="sleep")` ⇒ 源码里必有 `.sleep`（`.` 与属性名间可插空白）。
_DOT_SLEEP_RE = re.compile(r"\.\s*sleep\b")
#: 规则 2 的墙钟调用面：`time.time()` / `monotonic()` / `perf_counter()` 形。
_TIME_CALL_RE = re.compile(r"\.(?:time|monotonic|perf_counter|monotonic_ns|perf_counter_ns)\b")
#: 规则 2 在**源码文本**上的预筛面：`_DURATION_NAME` 的标识符边界版。
#:
#: 2026-09-29（§43）：原先是裸词根 `elapsed|wall|took|duration|sec|dt`，而 `sec`/`dt`
#: 在 NumPy 测试里到处都是（`dtype`、`section`、`seeds`）⇒ 两条守卫扫的 286 个文件
#: 几乎全部进 parse，预筛形同虚设（实测本用例 2.5s）。带边界后与 `_DURATION_NAME`
#: **同判**：`_` 仍算词分隔（`_dt` / `_segment_seconds` / `seconds_ago` 命中），
#: `dtype` / `width` / `section` 不命中。超集仍然是充分的：AST 认得的名字一定是
#: 源码里的标识符，而以标识符形式出现就必然被这条表达式认出。
_DURATION_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:elapsed|wall|dt|took|duration|sec|secs|seconds)(?![A-Za-z0-9])"
)

#: 上界墙钟断言的标注关键字 + 理由族（同 `sleep` 的设计：理由必须是闭集里的一族）。
_TIMING_MARK = re.compile(r"timing-ok:\s*(?P<why>[^\r\n]*)")
TIMING_FAMILIES = ("上界兜底", "契约上界", "夹具模拟", "相对判据")
#: 墙钟调用的方法名（`time.time() - t0` 里的 `time()`）——`time` / `monotonic` / `perf_counter`。
_TIME_FUNCS = frozenset({"time", "monotonic", "perf_counter", "monotonic_ns", "perf_counter_ns"})
#: 时长命名的**词**——这些名字就是「秒」。词级匹配（`_` 分隔即算），故
#: `elapsed` / `wall` / `wait_sec` / `elapsed_ms` / `_segment_seconds` 都命中；
#: 边界要求 `_` 或首尾，避免 `wallpaper` / `seconds_ago≠` 这类无关词误报。
_DURATION_NAME = re.compile(r"(?:^|_)(?:elapsed|wall|dt|took|duration|sec|secs|seconds)(?:_|$)")


def _named_duration(node: ast.AST) -> bool:
    """这个名字是不是墙钟时长：变量/属性 · 调用的函数名 · 下标的基底。

    覆盖 `elapsed` / `wall` / `elapsed_ms` / `_segment_seconds(...)` / `seen_secs[0]`；
    不认纯常量与无关名字（`cfgs[0]`、`status < 400`）。
    """
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = node.id if isinstance(node, ast.Name) else node.attr
        return bool(_DURATION_NAME.search(name))
    if isinstance(node, ast.Call):
        return _named_duration(node.func)
    if isinstance(node, ast.Subscript):
        return _named_duration(node.value)
    return False


def _sleep_calls(src: str, tree: ast.Module | None = None) -> list[tuple[int, str]]:
    """所有 `.sleep(...)` 调用点 → (行号, 该行源码)。字符串里的同名字样不算。

    `tree` 给了就不再解析（调用方已为别的规则解析过同一份源码，见 `_file_problems`）。
    """
    if not _DOT_SLEEP_RE.search(src):  # 预筛：本文件的 sleep 只会出现在桩子源码字符串里
        return []
    if tree is None:
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


def _problems(py: Path, src: str | None = None, tree: ast.Module | None = None) -> list[str]:
    """该文件里每一处不合格 sleep 的 `路径:行: 源码 → 问题`（`src`/`tree` 可由调用方预热）。"""
    src = source_scan.read_text(str(py)) if src is None else src
    lines = src.splitlines()
    rel = _rel(py)
    out: list[str] = []
    for lineno, text in _sleep_calls(src, tree):
        why = _reason(lines, lineno)
        if why is None:
            out.append(f"{rel}:{lineno}: {text.strip()} → 未标注（缺 `# sleep-ok: …`）")
        elif not why.startswith(FAMILIES):
            out.append(
                f"{rel}:{lineno}: {text.strip()} → 理由 {why!r} 不在两族内"
                f"（须以 {' 或 '.join(FAMILIES)} 开头）"
            )
    return out


def _file_problems(py: Path) -> tuple[list[str], list[str], list[str]]:
    """一个文件的三类问题：`(sleep 标注, 上界墙钟标注, 时间戳对比标注)`。

    **一次 `ast.parse` 供三条规则**（2026-10-29 门禁提速）：三条规则看的是同一批文件、
    同一份 AST，拆成三条用例时实测在门禁里各付一次全仓解析（共 12.6s CPU，其中 2/3 是
    重付的）。预筛仍然是三条各自的超集预筛（并集不命中才跳过解析）。
    """
    src = source_scan.read_text(str(py))
    need_sleep = bool(_DOT_SLEEP_RE.search(src))
    need_timing = "assert" in src and bool(
        _TIME_CALL_RE.search(src) or _DURATION_WORD_RE.search(src)
    )
    need_order = "assert" in src and bool(_TIME_CALL_RE.search(src))
    if not (need_sleep or need_timing or need_order):
        return [], [], []
    tree = ast.parse(src)
    return (
        _problems(py, src, tree),
        _timing_problems(py, src, tree),
        _order_problems(py, src, tree),
    )


def test_tests_declare_their_timing_and_sync_reasons() -> None:
    """`tests/` + `e2e/` 的**三条规则一次跑完**（分条说明见模块头与各规则小节）：

    ① 每一处 `.sleep(...)` 要有落进「轮询步长 / 夹具模拟」两族的 `# sleep-ok: <理由>`；
    ② 每一处**上界型**墙钟断言要有落进四族的 `# timing-ok: <理由>`；
    ③ 每一处「比较两个事件时间戳」要有落进两族的 `# order-ok: <理由>`。
    三条均为独立断言（消息各自指向对应规则与文档），红了一眼能看出是哪一条。
    """
    sleep_problems: list[str] = []
    timing_problems: list[str] = []
    order_problems: list[str] = []
    for root in SCAN_ROOTS:
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            got_sleep, got_timing, got_order = _file_problems(py)
            sleep_problems += got_sleep
            timing_problems += got_timing
            order_problems += got_order
    assert not sleep_problems, (
        "tests/ 里的 sleep 必须标注理由，且理由要落进「轮询步长」/「夹具模拟」两族"
        "（说明它等的是谓词或模拟的工作量，而不是「等对方先跑」）：\n  "
        + "\n  ".join(sleep_problems)
        + "\n规则与背景：docs/nn/engineering.md §21"
    )
    assert not timing_problems, (
        "上界型墙钟断言（`assert elapsed < N`）必须标注理由，且理由落进「上界兜底 / 契约上界 /"
        " 夹具模拟 / 相对判据」四族（它是真兜挂起、契约上界、量夹具、还是相对判据 —— 而不是"
        "「赌机器够快」）：\n  "
        + "\n  ".join(timing_problems)
        + "\n规则与背景：docs/nn/engineering.md §21"
    )
    assert not order_problems, (
        "跨进程比较两个事件时间戳的断言必须标 `# order-ok:` 理由（两族）——Windows 的"
        " `time.time()` 量化到 15.6ms，两个读数比大小会落到负数（假红）：\n  "
        + "\n  ".join(order_problems)
        + "\n规则与背景：docs/nn/engineering.md §21 ③"
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
    """该表达式是不是**墙钟时长**：`time.time() - t0` 形，或时长命名的表达式。

    命名面覆盖变量/属性 · 调用的函数名 · 下标的基底（见 `_named_duration`）；
    `time.time()` 差的任一侧也认（`time.time() - t0` 里两侧都未必叫时长名）。
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        for side in (node.left, node.right):
            if (
                isinstance(side, ast.Call)
                and isinstance(side.func, ast.Attribute)
                and side.func.attr in _TIME_FUNCS
            ):
                return True
            if _named_duration(side):
                return True
    return _named_duration(node)


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


def _timing_asserts(src: str, tree: ast.Module | None = None) -> list[tuple[int, str]]:
    """文件里每一处**上界型墙钟断言** → (行号, 该行源码)。字符串里的同名字样不算（AST）。"""
    # 预筛（超集）：要命中就得有一个 `assert` 语句，且它的比较式里带墙钟名/调用。
    if "assert" not in src or not (_TIME_CALL_RE.search(src) or _DURATION_WORD_RE.search(src)):
        return []
    if tree is None:
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


def _timing_problems(py: Path, src: str | None = None, tree: ast.Module | None = None) -> list[str]:
    """该文件里每一处不合格上界墙钟断言的 `路径:行: 源码 → 问题`。"""
    src = source_scan.read_text(str(py)) if src is None else src
    lines = src.splitlines()
    rel = _rel(py)
    out: list[str] = []
    for lineno, text in _timing_asserts(src, tree):
        why = _timing_reason(lines, lineno)
        if why is None:
            out.append(f"{rel}:{lineno}: {text.strip()} → 未标注（缺 `# timing-ok: …`）")
        elif not why.startswith(TIMING_FAMILIES):
            out.append(
                f"{rel}:{lineno}: {text.strip()} → 理由 {why!r} 不在四族内"
                f"（须以 {' / '.join(TIMING_FAMILIES)} 开头）"
            )
    return out


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


def test_the_guard_recognizes_calls_and_subscripts(tmp_path: Path) -> None:
    """扩面自证（2026-09-26）：函数调用与下标也是墙钟面。

    `test_wire_reroll` 两处 `_segment_seconds(lines[-1], …) < 0.2` 曾是守卫盲区
    （只认 Name/Attribute），靠人肉 `timing-ok` 标注兜着；这里把它们钉成守卫的职责。
    """
    bad = tmp_path / "bad_calls.py"
    bad.write_text(
        "def f(lines, seen_secs):\n"
        "    assert _segment_seconds(lines[-1], \"payload\") < 0.2\n"  # 未标注（Call + 词级名）
        "    assert seen_secs[0] < 0.5\n",  # 未标注（下标基底）
        encoding="utf-8",
    )
    got = _timing_problems(bad)
    assert len(got) == 2, got
    assert got[0].startswith("bad_calls.py:2: assert _segment_seconds("), got[0]
    assert got[1].startswith("bad_calls.py:3: assert seen_secs[0] < 0.5"), got[1]

    good = tmp_path / "good_calls.py"
    good.write_text(
        "def f(cfgs, status):\n"
        "    assert cfgs[0] < 5\n"  # 下标基底非时长名
        "    assert status < 400\n"  # 非时长
        "    assert len(cfgs) < 10\n",  # 调用名非时长
        encoding="utf-8",
    )
    assert _timing_problems(good) == [], _timing_problems(good)


# ──────────── 第三条规则：断言里比较两个**事件时间戳**（2026-10-21）────────────

#: 标注关键字 + 理由族（闭集，同前两条的设计：自由文本会让「等对方先跑」蒙混过关）。
_ORDER_MARK = re.compile(r"order-ok:\s*(?P<why>[^\r\n]*)")
#: 允许的理由族：
#:   * `粗序事实` —— 只信**先后**、不信毫秒差（跨进程/跨时钟的粗序；判据不该带毫秒补偿）；
#:   * `契约即时间` —— 契约本身就是时间上的先后/间隔，这条断言**就是**契约。
ORDER_FAMILIES = ("粗序事实", "契约即时间")


def _is_clock_call(node: ast.AST) -> bool:
    """这次调用是不是一次**时钟读数**（`time.time()` / `monotonic()` / `perf_counter()` 形）。"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _TIME_FUNCS
    )


def _timestamp_names(tree: ast.Module) -> set[str]:
    """文件里**收集过时钟读数**的名字。

    三种收法：`x.append(time.time())`（事件时间戳列表）· `x = [time.time()]` ·
    `x = time.time()`（单个读数）。命中这些名字的**下标**（`x[0]`）也算一次时间戳读数。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and _is_clock_call(node.args[0])
            and isinstance(node.func.value, ast.Name)
        ):
            names.add(node.func.value.id)
        if isinstance(node, ast.Assign):
            hit = (
                isinstance(node.value, ast.List) and any(_is_clock_call(e) for e in node.value.elts)
            ) or _is_clock_call(node.value)
            if hit:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


def _is_ts_read(node: ast.AST, names: set[str]) -> bool:
    """这个表达式是一次**时间戳读数**吗？

    四种形态：直接时钟调用 · 时间戳名字 · 时间戳列表的下标 · **带偏移的读数**
    （`ready[0] - 0.1` / `max(ready[0], ready[1])` —— 那条缝常被写成「减个补偿再比」，
    形状上仍是时间戳）。偏移只认**常量**那一侧：两个时间戳相减是**时长**（归第二条规则管）。
    """
    if _is_clock_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return node.value.id in names
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        for side, other in ((node.left, node.right), (node.right, node.left)):
            if (
                isinstance(other, ast.Constant)
                and isinstance(other.value, (int, float))
                and _is_ts_read(side, names)
            ):
                return True
        return False
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("max", "min")
        and bool(node.args)
        and all(_is_ts_read(a, names) for a in node.args)
    )


def _ts_pair_asserts(src: str, tree: ast.Module | None = None) -> list[tuple[int, str]]:
    """断言里**比较两个时间戳读数**的位置 → (行号, 该行源码)。"""
    if "assert" not in src or not _TIME_CALL_RE.search(src):
        return []
    if tree is None:
        tree = ast.parse(src)
    names = _timestamp_names(tree)
    if not names:
        return []
    lines = src.splitlines()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for cmp in (c for c in ast.walk(node.test) if isinstance(c, ast.Compare)):
            lefts = [cmp.left, *cmp.comparators[:-1]]
            for op, left, right in zip(cmp.ops, lefts, cmp.comparators, strict=True):
                if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    continue
                if _is_ts_read(left, names) and _is_ts_read(right, names):
                    out.append(
                        (
                            node.lineno,
                            lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        )
                    )
                    break
            else:
                continue
            break
    return out


def _order_reason(lines: list[str], lineno: int) -> str | None:
    """这一处比较的 `# order-ok:` 理由（同行尾注优先，其次上一行）；没标注 → None。"""
    for idx in (lineno - 1, lineno - 2):
        if idx < 0:
            continue
        m = _ORDER_MARK.search(lines[idx])
        if m is not None:
            return m.group("why").strip()
    return None


def _order_problems(py: Path, src: str | None = None, tree: ast.Module | None = None) -> list[str]:
    """该文件里每一处不合格「比较两个时间戳」的 `路径:行: 源码 → 问题`。"""
    src = source_scan.read_text(str(py)) if src is None else src
    lines = src.splitlines()
    rel = _rel(py)
    out: list[str] = []
    for lineno, text in _ts_pair_asserts(src, tree):
        why = _order_reason(lines, lineno)
        if why is None:
            out.append(f"{rel}:{lineno}: {text.strip()} → 未标注（缺 `# order-ok: …`）")
        elif not why.startswith(ORDER_FAMILIES):
            out.append(
                f"{rel}:{lineno}: {text.strip()} → 理由 {why!r} 不在两族内"
                f"（须以 {' / '.join(ORDER_FAMILIES)} 开头）"
            )
    return out


def test_the_guard_actually_catches_two_timestamp_comparisons(tmp_path: Path) -> None:
    """自证：第三条守卫不恒真 —— 未标注 / 理由不在两族都抓得到；与常量比、与非时间戳比放行。"""
    bad = tmp_path / "bad_order.py"
    bad.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    spawns = []\n"
        "    ready = []\n"
        "    spawns.append(time.time())\n"
        "    ready.append(time.time())\n"
        "    assert spawns[0] <= ready[0]\n"  # 未标注
        "    assert spawns[0] >= ready[0] - 0.1  # order-ok: 等对方先跑\n",  # 理由不在两族
        encoding="utf-8",
    )
    got = _order_problems(bad)
    # 两处都抓到：第一处「未标注」，第二处「理由不在两族内」
    assert len(got) == 2 and got[0].endswith("未标注（缺 `# order-ok: …`）"), got
    assert got[1].endswith("不在两族内（须以 粗序事实 / 契约即时间 开头）"), got

    good = tmp_path / "good_order.py"
    good.write_text(
        "import time\n"
        "\n"
        "def f():\n"
        "    spawns = []\n"
        "    ready = []\n"
        "    spawns.append(time.time())\n"
        "    ready.append(time.time())\n"
        "    assert spawns[0] > 0\n"  # 与常量比：不是两个时间戳
        "    assert spawns[0] <= ready[0]  # order-ok: 粗序事实（只信先后，不信毫秒差）\n"
        "    assert status < 400\n",  # 非时间戳，不管
        encoding="utf-8",
    )
    assert _order_problems(good) == [], _order_problems(good)
