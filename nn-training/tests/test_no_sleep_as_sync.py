"""tests/test_no_sleep_as_sync.py — 静态守卫：**不许拿 sleep 当同步**（2026-09-24）。

背景（`docs/nn/engineering.md` §21）：CPU 满即时连跑 8 次门禁，红点每轮不同 ——
serve_pool 端到端 / `bulk_sched` 单通道 / `eval_local` 硬顶 / `push_priority` 主副本，
负载轰炸又拖出 `batch_eval`、`control_plane`、`eval_dispatch`、`body_transfer`、
`offline_deliver`、`rollout rescan`、`async_result`。共同特征：**拿绝对数字当同步手段**
—— `sleep(N)` 赌「对方已经到了」、`elapsed < N` 赌「机器够快」。

本守卫钉住最容易复发、也最容易静态判定的那一条：`tests/` 里每一处 `.sleep(...)` 调用
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
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

#: 标注关键字 + 理由（同一行尾注或上一行注释里出现即可）。
_MARK = re.compile(r"sleep-ok:\s*(?P<why>[^\r\n]*)")
#: 允许的理由族（前缀匹配）——理由落不进这两族 = 它在拿时长当同步。
FAMILIES = ("轮询步长", "夹具模拟")


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
    src = py.read_text(encoding="utf-8")
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
    """`tests/` 里每一处 `.sleep(...)` 都要有落进两族的 `# sleep-ok: <理由>`。"""
    problems: list[str] = []
    for py in sorted(TESTS.rglob("*.py")):
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
