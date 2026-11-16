"""G9：生产包不得新增「宽泛捕获 + 什么也不做」（`except …: pass`）
（`python.refactor.md` §P0-7、§8）。

存量按**文件计数**冻结在 `tests/golden/silent_except_baseline.json`：只拦增长，不要求一次清零。
口径 = AST 里 `ExceptHandler` 同时满足两件事：① 捕获面是宽泛的（`Exception` /
`BaseException` / 裸 `except:`，与 G4 同一份定义）；② 函数体**只有一个 `pass`**（连一行留痕都
没有）。捕获面具体（`except (OSError, ValueError)` / `except FileNotFoundError`）的不计 ——
那些类型本身就是文档；体内除 `pass` 还有别的语句的也不计 —— 那已经不是「静默」。

为什么要单独一条（G4 已经管住宽泛捕获）：**「捕了并处理」和「捕了假装没发生」是两种事故**。
2026-11-14 的实例就在日志层自己身上：`LogRouter` 镜像写失败与句柄关闭失败都是裸 `pass`，
于是「这门课的日志文件正在丢行」与「这门课本来就没开日志」在控制台上长得一模一样，只有事后
比对课日志才发现少了半天（见 `DECISIONS.md` §2026-11-14-…-log-router-visibility）。G4 拦得住
「新增 `except Exception`」，却拦不住把新分支写成 `pass` —— 这条补的就是后半句。

处置口径（不是一律喊 WARN）：
  * **该有替代路径的**（降级测量、可选依赖、陈旧文件清理）→ 保留 `pass` 但写清为什么静默，
    并让它留在本基线里；
  * **失败即信息丢失的**（日志落盘、账本写回、重试判定）→ 换成可见出口（`log_line` /
    `warn_once` / 计数器），顺手把基线调小；
  * 热点路径（每 tick / 每行日志）去重要按 key 去重，别把控制台刷成噪音。
  * **第三种处置：窄捕**（2026-11-15 补）。捕获面本来就只该是某几种异常时，`except (OSError,
    ValueError): pass` 既不属「宽」也不属「静默」—— 它把「本模块自己的 bug」放出去，正是这条
    门禁想要的那半条命。第八批 `remote/lifecycle.py`（4 处）+ `remote/colab_bc.py`（4 处）就走
    了这条：遗言读数（`/proc` / NVML / statvfs）与探测腿（venv python 能不能 import torch）
    的失败面都是具体的；两处**真信息丢失**（state 文件读不了、SIGTERM 自投失败）改成留一行，
    而不是窄捕了事 —— 后者意味着「进程活着而 hub 当它已下线」（重复计费）却没有任何痕迹。
    该批 51 → **44**（colab_bc 4→0，lifecycle 3→0）；扫描地板当时手改 50 → 40（2026-11-15 起
    改为派生式 `tests/helpers/budget.sanity_floor`，不再需要人工回填）。
    紧接着 `remote/hub_http.py` 的 2 处 `except Exception: pass` 也走窄捕（同一条 `NET_ERRORS`）
    ⇒ 44 → **42**；那两处原本把「探测回执读不了」与「本模块 bug」抹平，正是本条要抓的形状。
    `remote/notebook_runtime.py` 的 token 文件 `chmod` 同批改 `except OSError`（Colab FUSE 挂载点
    不一定支持 chmod）⇒ 44 → **40**；该文件另 6 处 `except Exception` 也在同批收窄（见 G4 记账表）。
    `common/distribution.py` 的 `weights_cached_on_node` 同批走窄捕（形状显式判 `isinstance`）
    ⇒ 42 → **41**；同文件另 1 处（`_closer` 对任意响应对象尽力 `close()`）是第三条边界，保留。
  * **留在基线里的每一处都要在**现场**写明理由**（2026-11-15 补，`_marker_audit`）。
    `python.refactor.md` 附录 C 把 26 处 A 类逐条定性成「静默就是契约」—— 但那张表在仓库根，
    改这几行代码的人看不到它，于是同一批 `except Exception: pass` 每隔几个月就被重新审计一遍。
    规矩：**在 `pass` 上方写一行以 `契约静默（G9 冻结` 开头的注释**（说清为什么静默），
    由 `test_frozen_silent_sites_say_why_in_place` 钉住「每处都在场有理由、且没有孤儿注释」。
    这**不是**豁免机制：标记不减轻 G9 的计数，新增静默宽捕照样红。
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

from tests.helpers.budget import sanity_floor

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "silent_except_baseline.json"

#: 生产包；与 G3 / G4 同一扫描面。`tools/` 不在内，`worker/scripts/` 在下方按前缀豁免。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 与 G4 同一份宽泛定义（`tests/test_broad_except_budget.py::BROAD_NAMES`）。
BROAD_NAMES = frozenset({"Exception", "BaseException"})

#: 自检锚点：扫描必须命中的两个已知文件（`common/log.py` 的镜像/关闭路径与
#: `worker/forensics.py` 的降级测量都还在基线里 —— 前者是「该有替代路径才静默」的典型，
#: 后者是「静默即降级」的合法样板；`remote/job_lifecycle.py` 那 6 处控制面 POST 已于
#: 2026-11-14 改成留痕，不再适合当锚点）。
#: 扫描入口自检的锚：必须**两个方向都有存量**的文件。2026-11-15 换掉 `worker/forensics.py`
#: （那 5 处改成「降级但记入快照」后体内不再只有 `pass`，它已不是静默样本）⇒ 换成
#: `remote/child_proc.py`（行泵的读流 EOF 静默，那条是刻意的）。
_ANCHORS = ("common/log.py", "remote/child_proc.py")


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """体内只有一个 `pass`（注释不算节点，所以「只有注释」也算静默）。"""
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            n = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
            )
            if n:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


#: 「静默就是契约」的就近标记前缀（附录 C 的 A 类 26 处逐条写在各自的 `pass` 上方）。
#: 为什么不新增清单文件：清单离代码远 ⇒ 改代码的人永远看不到它（附录 C 的结论一样）。
#: 前缀之外的理由文字自由写；前缀**逐字**一致，好让 `grep -rn` 一次数全 26 处。
MARKER = "契约静默（G9 冻结"


@cache
def _marker_audit() -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """`(缺标记的静默宽捕, 孤儿标记)` —— 前者按处、后者按**行**，都以 `{文件: [行号]}` 返回。

    * 缺标记 = 本文件认定的「宽泛捕获 + 只有 `pass`」却找不到那句话：留在基线里等于欠一张
      说明（下一个人只能从零判断它该不该宽）。
    * 孤儿标记 = 注释还在，但它已经不在任何一个静默宽捕里（那处窄捕了 / 删了）—— 留着会
      让下一次统计虚高，也会误导读代码的人以为这儿的静默还有契约依据。
    """
    missing: dict[str, list[int]] = {}
    stray: dict[str, list[int]] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
            text_lines = src.splitlines()
            marked: set[int] = set()  # 已带标记的静默宽捕（按 `except` 行号）
            inside: set[int] = set()  # 真正落在那些处理块里的标记注释行
            for h in ast.walk(tree):
                if not isinstance(h, ast.ExceptHandler):
                    continue
                if not (_is_broad(h) and _is_silent(h)):
                    continue
                if MARKER not in (ast.get_source_segment(src, h) or ""):
                    continue
                marked.add(h.lineno)
                inside |= {
                    i
                    for i in range(h.lineno + 1, (h.end_lineno or h.lineno) + 1)
                    if MARKER in text_lines[i - 1]
                }
            # 标记行不落在任何「已认定的静默宽捕」块里 = 注释没跟着代码走（那处窄捕 / 删了，
            # 或换到了别的语句上）。嵌套情形不算：内层处理块标记了，外层段里也会扫到这一行。
            for i, line in enumerate(text_lines, start=1):
                if MARKER in line and i not in inside:
                    stray.setdefault(rel, []).append(i)
            for h in ast.walk(tree):
                if (
                    isinstance(h, ast.ExceptHandler)
                    and _is_broad(h)
                    and _is_silent(h)
                    and h.lineno not in marked
                ):
                    missing.setdefault(rel, []).append(h.lineno)
    return missing, stray


def test_frozen_silent_sites_say_why_in_place() -> None:
    """基线里的每一处静默都要**在现场**说清「这是契约，不是旧债」（理由就地表达，不靠清单）。"""
    missing, stray = _marker_audit()
    gaps = "\n".join(f"  {rel}: 第 {lns} 行" for rel, lns in sorted(missing.items()))
    assert not missing, (
        f"这些「宽泛捕获 + 什么也不做」没写在现场的静默理由（前缀 `{MARKER}…`）：\n{gaps}\n"
        "  要么给它一个可见出口 / 窄捕（顺手把基线调小），要么在 `pass` 上方写明为什么静默是对的。"
    )
    stale = "\n".join(f"  {rel}: 第 {lns} 行" for rel, lns in sorted(stray.items()))
    assert not stray, (
        f"这些「{MARKER}…」注释已经对不上任何静默宽捕（那段代码窄捕 / 删掉了）：\n{stale}\n"
        "  注释跟着代码走 —— 顺手删掉它，别让下一次统计把已经修好的地方又算成契约。"
    )


def test_no_new_silent_broad_except() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了「宽泛捕获 + 什么也不做」（`except Exception: pass`）：\n"
        f"{detail}\n"
        "  失败即信息丢失的路径要给可见出口（`common.logutil.log_line` / `LogRouter.warn_once`，"
        "热点按 key 去重）；确实该降级的，写清理由并把这条留在 `tests/golden/silent_except_baseline.json`。"
    )


def test_baseline_only_shrinks() -> None:
    """基线里的文件计数只许持平或下降；清理完随手把基线数改小（否则额度会一直放行旧写法）。"""
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的静默宽捕获已经减少，但 `tests/golden/silent_except_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（或删掉归零的条目），让额度不再回流。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际扫描确实命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    for rel in _ANCHORS:
        assert baseline.get(rel, 0) > 0, f"基线丢了已知文件 {rel}"
        assert actual.get(rel, 0) > 0, "扫描入口坏了（AST 没数到已知 `except …: pass`）"
    assert sum(actual.values()) >= sanity_floor(baseline), (
        f"生产面计数 {sum(actual.values())} 低于基线九成（{sanity_floor(baseline)}）"
        "—— 扫描可能漏了包（这条只防扫描入口坏，不是质量目标）"
    )
    # 文件数不再单独设下限：存量被清到 20 出头之后，任何绝对值都只是「清理到哪就该手改到哪」
    # 的回填；「扫描面坏了」由上面的派生根数地板兜住（21 个文件全丢 ⇒ 条数必然低于九成）。
    assert not any(rel.startswith(EXEMPT_PREFIXES) for rel in baseline)
    assert not any(rel.startswith("tools/") for rel in baseline)


def test_specific_handlers_and_handled_cases_are_not_counted() -> None:
    """口径自检：具体异常、体内还有别的语句、`except*` 都不算。"""
    src = """
try:
    pass
except (OSError, ValueError):
    pass
except FileNotFoundError:
    pass
except Exception:
    log_line("x", "y")
"""
    tree = ast.parse(src)
    silent = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
    ]
    assert silent == []

    bare = ast.parse("try:\n    pass\nexcept:\n    pass\n")
    assert (
        sum(
            1
            for node in ast.walk(bare)
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
        )
        == 1
    )
