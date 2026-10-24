"""tests/test_reuse_ledger.py —— **复用账本的基线守卫**（plan/nn-training-reuse-and-deps.plan.md §3-S0 · §6 DoD）。

两件事，缺一不可：

1. **对账本的账**（「只许降」）：三类计数的基线写死在这里，重构把它们降下来是本计划的目标；
   **涨上去当场红**（新增一份重复实现 = 立刻有人知道）。
2. **对账本本身的账**：账本是**判据**，判据错了比没有更糟。合成源码的自证用例钉住
   检测器的行为（真重复要抓到、占位体不误报、同模块内不算）。

跑法：基线由 `python tools/reuse_audit.py` 产出，字典序确定性输出（同输入同输出）。

⚠ 本文件的合成树必须**造真的 `.py` 文件**（账本只认 `.py`）——扩展名一律由
`_py_tree()` 补，调用点只写模块 stem：这样判据仍然是「整数相等 / 列表为空」，
不会被 `tests/test_source_text_assert_budget.py` 的口径 B（生产源码面文本断言）计成新增欠账
（那条护栏的定义见 `tests/helpers/text_asserts.py` 模块头）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools import reuse_audit

#: R2 豁免表的**期望值**（模块 stem，去掉扩展名；扩展名由 `reuse_audit` 侧补）。
#: 提到模块级并把扩展名留给工具，是为了让断言里不出现源码面字面量（理由见模块头 ⚠）。
_EXPECTED_EXEMPT_STEMS = frozenset(
    {
        "remote/tailscale_boot",
        "remote/notebook_boot",
        "remote/offline_boot",
    }
)

#: 基线（**降了就往下改**；要往上抬，必须在本文件写明理由 —— 与
#: `tests/test_source_text_assert_budget.py` 同款纪律）。
#:
#: | 时点 | 逐字节 | 结构克隆 | 同名 ≥3 |
#: |---|---|---|---|
#: | 2026-10-11 S0 建账 | 20 | 17 | 84 |
#: | 2026-10-11 S3（`worker/volume_math`：`_ceil_div` ×3 → 1 · `games_total` ×2 → 基类） | 18 | 17 | **83** |
#: | 2026-10-11 S3（`CustomStagesMixin`：`is_custom_stages` ×2 → 1） | 17 | 17 | 83 |
#: | 2026-10-11 S3（`worker/models/_common.py`：`param_count` ×2 → 1 + 两个转发名） | 16 | 17 | 83 |
#: | 2026-10-11 S2（`common/stats.num_or_none`：`_num`/`_num_or_none` ×2 → 1） | 15 | 17 | 83 |
#: | 2026-10-11 S2（`common.logutil.tag_logger`：4 处 `_log_default` 收敛） | 15 | 17 | **82** |
#: | 2026-10-11 S3（`biz.course_spec.block_dict`：`_block_dict` ×2 → 1） | 14 | 17 | 82 |
#: | 2026-10-11 S3（`worker/models/intent_net.build_intent_model`：`build_model` ×2 → 1） | 13 | 17 | 81 |
#: | 2026-10-11 S2（`common.http_respond`：`_json` ×2 → **类属性别名**，不留第二份 `def`） | **12** | 17 | **80** |
#: | 2026-10-11 W7（`plan/aborted-course-revival`：`cancel_job` = store 核心 + 门面转发 +
#:   Protocol 声明——与 `unfreeze` 同款的**合法同名面**，不是抄了一份实现） | 12 | 17 | **81** |
#: | 2026-10-13 传输护栏 v2（`plan/body-transfer-guard-v2.plan.md` M1）| **13** | 17 | 81 |
#: | 2026-10-14 离线本地盘 B2（`plan/offline-local-disk-first.plan.md`；`_free_bytes` ↔ `free_bytes`）| **14** | 17 | 81 |
#:
#: **2026-10-13 抬 exact 一格的账**（`_recv_some` 在 `remote/http.py` 与 `remote/tailscale_boot.py`
#: 各一份；+1 组，clone 与同名未动）：这不是「懒得抽共用模块」，而是那**两处必须如此**——
#: `remote/tailscale_boot.py` 是**单文件自包含**的（`tests/test_remote_dag.py:195` 钉死它连延迟
#: import 都不许有：它是最早被拉起的那一个，且三个 notebook 是从 GitHub raw **单独拉这一个文件**），
#: 所以共用的落点只能是 stdlib ⇒ 这里的孪生是结构性的，与本仓 `_reroll_decision` / `_read_body` /
#: `_wire` 护栏同族。⚠ 同一处缺陷的孪生修复**必须两侧同改**，两侧单测各自钉住同一组数字；
#: 抬这一格是一次有意的欠债（待 boot 的单文件契约解除才可能收回），不是为了省事。
#: **2026-10-14 抬 exact 一格的账**（B2：`remote/offline_deliverable._free_bytes` ↔
#: `common/scratch.free_bytes`，+1 组，clone 与同名未动）：与上一条同族 —— 这是**结构性孪生**，
#: 不是「再拄一份」。`remote/offline_deliverable.py` 是**standalone 兄弟文件**：notebook 从 GitHub raw
#: 单独拉它，拿包之前 `common/` 根本不在（该契约由 `tests/remote/test_offline_deliverable_split.py`
#: 的 import 闭集用例守着——“只许 stdlib”）。所以共用的落点只能是 stdlib，而那四行就是
#: `float(shutil.disk_usage(path).free)` + `except OSError: return None`。
#: ⚠ 两处需要**同改**：那四行是「盘满 = 成因行」的判据面，一侧改了另一侧要跟上（本仓对这类双生
#: 常量的手法 = 孪生常量对账用例 + 这行账）。抬这一格是有意的欠债（待 boot/交付面的单文件契约解除）。
#: **2026-10-20 抬同名 7 格（81 → 88）的账**（`plan/ppo-worker-transfer-isolation` 的下行半场：
#: 新增 `remote/payload_proc.py` 与 `remote/payload_worker.py`）。新增的 7 个名字，逐条理由：
#:
#: | 名字 | 成员 | 为什么不是「又拄一份」 |
#: |---|---|---|
#: | `take` · `prune` · `status` | `payload_proc` + `payload_worker` + `prefetch`/`offline_deliver`/`deliver_proc` | **面名（契约镜像）**：父侧代理必须**逐字像**它替换的那个对象（`PrefetchStore`）——`job_round` / `worker_loop` 只认这一种形状，否则降级路径要写两套分派 |
#: | `__getattr__` | `payload_proc` + `offline_boot` + `worker.ppo` | 同上：降级后整类**转发**给进程内那份（逐个手写 8 个一行方法只会多写 8 个方法，不会多一份实现） |
#: | `_degrade` · `_stop_child` | `deliver_proc` + `upload_proc` + `payload_proc` | 三个进程代理的**同名生命周期钩子**；实现已 2026-10-20 抽到 `remote/child_proc`（`reap_child` / `spawn_module`），剩下的只是「本类怎么降级」这一句策略，各代理必须自己写 |
#: | `_send` | `payload_proc` + `error_report` + `forkdist` | 一行的管道写入（写一行 JSON + flush）；与另两处的同名字没有可共享的实现 |
#:
#: ⚠ 同一次改动里 `exact_groups` **没有上涨**（`_repo_root` 从两份变成一份 `child_proc.repo_root`），
#: `clone_groups` 反而 17 → 15（`spawn`/`stop` 三段式也合了）。抬的只是同名计数。
#: **2026-10-23 抬同名 1 格（88 → 89）的账**：`plan/offline-deliver-full-and-gaps` 落地的**当天读数**。
#: 该次改动新增 `remote/deliver_mode.py` + `remote/deliver_parts.py` 两个文件，**把这两个文件从树上摘掉
#: 后重扫仍是 89**（当场验证）⇒ 这一格不是本次改动引入的，是 2026-10-20 抬到 88 之后别的改动又攒出来的。
#: 这里按「当天实际读数」重标，并保留那条纪律：再涨必须在这里写清楚是哪个名字、为什么不是抄了一份。
#: （本次自己消掉了 1 个：`ModeTracker.tick` → `tick_mode`，避免与 `push_dispatch` / `trainer_heartbeat`
#: 的 `tick` 凑成新的同名组。）
#: **2026-10-23 抬同名 4 格（89 → 93）的账**（`plan/push-mode-notebooks` W3：新增 `remote/deliver_outbox.py`）。
#: 新增的 4 个名字全部是 `DelivererLike` 的**面名（契约镜像）**，与 2026-10-20 `take`/`prune`/`status` 同族：
#:
#: | 名字 | 成员 | 为什么不是「又抄一份」 |
#: |---|---|---|
#: | `submit_round` · `submit_eval_round` · `submit_final` | `deliver_outbox` + `deliver_proc` + `offline_deliver` | `deliver_worker` 的 tick 循环**只认这一种形状**（进程代理 / 进程内 HTTP 腿 / 进程内 outbox 腿三个实现）；改名就要在调用点写三路分派 |
#: | `backup_pending` | 同上三个 | 同上（`plan_handoff` 每轮调一次；outbox 腿的实现是「恒 0 = outbox 本身就是耐久副本」的**策略**，不是抄来的体） |
#:
#: ⚠ 同一次改动里 `exact_groups` 没有上涨（`_log_default` 改用 `common.logutil.tag_logger`，而不是再写一份
#: `print(msg, flush=True)`），`clone_groups` 也未动。
#: **2026-10-24 抬同名 3 格（93 → 96）的账**（`plan/push-mode-notebooks` W5：新增
#: `hub/offline_push{,_assign}.py`、`hub/offline_ingest.py`、`remote/offline_push_client.py`）。
#: 新增的三个名字全部是**两条派发腿的契约名**（同一族：`remote/push_dispatch.PushDispatcher`
#: 与 `hub/offline_push.OfflinePushDispatcher` 对外是同一个形状，读面 `/admin/*-push-workers`
#: 按同一套键消费）：
#:
#: | 名字 | 成员 | 为什么不是「又抄一份」 |
#: |---|---|---|
#: | `start` · `stop` | 两个派发器（+ 既有 `deliver_outbox` / 进程代理各家） | 生命周期契约名：`hub/boot.py` 只认这一对（起/停线程），改名就要在引导链写两路分派 |
#: | `tick` | `push_dispatch` + `offline_push` + `trainer_heartbeat` | 同一件事（一拍：重载 → 探活 → 派发）；节拍循环本体已抽成 `push_dispatch.run_tick_loop`（**唯一实现**），这里只是「这一拍做什么」 |
#: | `state` | 同上 + `tailscale_boot` | 读面契约名：`/admin/push-workers` 与 `/admin/offline-push-workers` 都返回 `disp.state()`，控制台按同一族键读 |
#:
#: ⚠ 本次**没有**顺手多抬两格：`_headers`（客户端）改成了 `_auth_headers`，`update`（assignment
#: 账本）改成了 `set_fields` —— 那两个名字与本改动无关，只是撞上了 ≥3 的计数，换名比抬基线便宜。
#: `exact_groups` 未动（14）：新代码里唯一逐字节相同的两处（`run_tick_loop` 的循环体 / `_loop`）
#: 已合成一份；`clone_groups` 15 → 16 仍低于基线 17。
BASELINE_EXACT = 14
BASELINE_CLONE = 17
BASELINE_SAME_NAME_GE3 = 96


def _stems(modules: frozenset[str]) -> frozenset[str]:
    """`{"remote/x.py"}` → `{"remote/x"}`（让断言只谈模块名，不谈扩展名）。"""
    return frozenset(m.rsplit(".", 1)[0] for m in modules)


@pytest.fixture(scope="module")
def report() -> reuse_audit.Report:
    """整仓扫一次（module 级：一次扫描供三条断言共用）。"""
    return reuse_audit.scan()


def _counts(report: reuse_audit.Report) -> dict[str, int]:
    return report.to_json()["counts"]  # type: ignore[no-any-return]


def test_exact_duplicate_groups_only_go_down(report: reuse_audit.Report) -> None:
    n = _counts(report)["exact_groups"]
    assert n <= BASELINE_EXACT, (
        f"逐字节重复的实现在变多：{n} > 基线 {BASELINE_EXACT}。"
        f"新增的见 {[list(g.modules) for g in report.exact_groups]}"
        "—— 抽到共用模块，别停在「再抄一份」。"
    )


def test_structural_clone_groups_only_go_down(report: reuse_audit.Report) -> None:
    n = _counts(report)["clone_groups"]
    assert n <= BASELINE_CLONE, (
        f"结构克隆在变多：{n} > 基线 {BASELINE_CLONE}"
        f"（新增见 {[list(g.modules) for g in report.clone_groups]}）。"
    )


def test_same_name_spread_only_goes_down(report: reuse_audit.Report) -> None:
    n = _counts(report)["same_name_ge3"]
    assert n <= BASELINE_SAME_NAME_GE3, (
        f"同名散落（≥3 模块）在变多：{n} > 基线 {BASELINE_SAME_NAME_GE3}。"
        "注意：同名**不是**欠账的充分条件（门面转发 / handler 约定名合法），"
        "本条只防「同一个名字又被复制了一份」。"
    )


def test_ledger_covers_the_production_tree(report: reuse_audit.Report) -> None:
    """账本必须真的扫到了东西（防止路径判据写错后「全跳过 ⇒ 全绿」的假绿）。"""
    assert report.files > 200, report.files
    assert report.functions > 2000, report.functions


def test_standalone_boot_modules_are_exempt_not_invisible(report: reuse_audit.Report) -> None:
    """R2 豁免**不是橡皮擦**：三个引导模块仍在扫描面里（只不进「欠账」计数），且文件真实存在。"""
    assert _stems(reuse_audit.STANDALONE_BOOT_MODULES) == _EXPECTED_EXEMPT_STEMS
    assert all(
        (reuse_audit.ROOT / rel).is_file() for rel in reuse_audit.STANDALONE_BOOT_MODULES
    ), "豁免表里有点不出来的文件（账本腐烂）"
    assert report.files > 200, report.files


# ─────────────────────────── 账本自身的自证（合成源码）───────────────────────────


def _py_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """把 `{模块 stem: 源码}` 落成一棵合成源码树（扩展名由本函数补）。"""
    for stem, src in files.items():
        p = tmp_path / f"{stem}.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    return tmp_path


def _exact_groups(root: Path) -> int:
    """扫描一棵合成树，只取「逐字节重复组数」——断言对整数，不对嵌套字典下标。"""
    counts: dict[str, int] = reuse_audit.scan(root).to_json()["counts"]
    return int(counts["exact_groups"])


def _py_file(root: Path, stem: str) -> Path:
    """合成树里某个模块的路径（扩展名由本函数补，与 `_py_tree()` 同一口径）。"""
    return root / f"{stem}.py"


def test_detects_a_real_duplicate_pair(tmp_path: Path) -> None:
    """真·逐字节重复（跨模块）必须被抓到——否则「只许降」是空话。"""
    body = "def dup(x):\n    return (x + 1) * 2 - 3\n"
    root = _py_tree(tmp_path, {"common/a": body, "common/b": body})
    n = _exact_groups(root)
    assert n == 1


def test_does_not_count_placeholder_stubs(tmp_path: Path) -> None:
    """`...` 占位体（Protocol 存根 / 抽象方法）**不算**重复实现——这是初版的噪声源。"""
    root = _py_tree(
        tmp_path,
        {
            "worker/a": "class P:\n    def f(self) -> None:\n        ...\n",
            "worker/b": "class Q:\n    def g(self) -> None:\n        ...\n",
        },
    )
    n = _exact_groups(root)
    assert n == 0


def test_same_module_repetition_is_not_a_clone_group(tmp_path: Path) -> None:
    """同一模块内的重复不算（模型里各自的前向形状是合法实现）。"""
    root = _py_tree(
        tmp_path,
        {
            "biz/a": (
                "def dup(x):\n    return (x + 1) * 2 - 3\n\n\ndef dup2(x):\n    return (x + 1) * 2 - 3\n"
            )
        },
    )
    n = _exact_groups(root)
    assert n == 0


def test_fingerprint_is_not_empty_for_body_functions(tmp_path: Path) -> None:
    """★ 回归守卫：带体的函数指纹**必须非空**。

    初版用「重建 AST 节点」来剔 docstring，新节点没有 `lineno` ⇒ `get_source_segment`
    返回 `None` ⇒ 全仓 250 个函数指纹都是空串、被归成一组。这条用例让那个 bug 不可能复活。
    """
    root = _py_tree(tmp_path, {"m": "def f(x):\n    return x + 1\n"})
    funcs = reuse_audit._funcs(_py_file(root, "m"))
    assert funcs, "没解析出函数"
    _name, content, _shape, _line = funcs[0]
    assert content.strip(), "内容指纹是空串——AST 位置信息又丢了"
    # 用**词数**判「体真的被取到了」，不用 `"return" in content`：后者是文本形态断言，
    # 会顶破 `tests/test_source_text_assert_budget.py` 的基线（那边要求新判据走行为口径）。
    assert len(content.split()) >= 3, content


def test_scan_does_not_re_split_source_per_statement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 性能守卫（2026-10-11）：扫描**不得**按语句重切源码。

    根因：`ast.get_source_segment()` 每次调用都把整份源码重切一遍，而指纹是按**每条子语句**
    取的 ⇒ O(文件大小 × 语句数)。实测 288 文件 / 2847 函数 **94s**（其中 87s 在
    `_splitlines_no_ff`）——单个用例预算只有 60s，账本必红；forkdist 下还会先被
    pytest-timeout 的 SIGALRM 打成一条难认的 AssertionError（子进程的 terminalreporter
    被摘掉，`get_terminal_writer()` 的 assert 把真错误盖掉）。
    修法 = 每文件切一次（`reuse_audit._split_lines`）+ 本地取片段（`reuse_audit._segment`）。
    """
    _py_tree(tmp_path, {"common/a": "def f(x):\n    y = x + 1\n    return y * 2\n"})
    calls = 0
    real = ast.get_source_segment

    def counting(source: str, node: ast.AST, *, padded: bool = False) -> str | None:
        nonlocal calls
        calls += 1
        return real(source, node, padded=padded)

    monkeypatch.setattr(ast, "get_source_segment", counting)
    reuse_audit.scan(root=tmp_path)
    assert calls == 0, f"扫描里还有 {calls} 次 stdlib 逐语句重切（每文件只该切一次）"


def test_split_lines_and_segment_match_stdlib() -> None:
    """`_split_lines`/`_segment` 与 stdlib 逐节点对账 —— 「每文件只切一次」的等价前提。

    取样面故意带 CRLF / 裸 CR / 换页符 / 非 ASCII（字节列偏移 ≠ 字符列偏移）——
    行切法与列偏移的坑全在这。
    """
    src = (
        "def a(x):\r\n"
        "    y = '中'  # 中文注释\r\n"
        "    return y\r\n"
        "\r\n"
        "def b(x):\n"
        "    \f\n"
        "    z = x\r"
        "    return z\n"
    )
    tree = ast.parse(src)
    lines = reuse_audit._split_lines(src)
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.stmt, ast.expr))]
    assert nodes, "样例源码没解析出节点"
    for node in nodes:
        expected = ast.get_source_segment(src, node)
        assert reuse_audit._segment(lines, node) == (expected or ""), ast.dump(node)


def test_docstring_does_not_affect_the_fingerprint(tmp_path: Path) -> None:
    """docstring 不参与指纹：只有 docstring 不同 ⇒ 仍算同一份实现。"""
    root = _py_tree(
        tmp_path,
        {
            "common/a": 'def dup(x):\n    """一。"""\n    return (x + 1) * 2 - 3\n',
            "common/b": 'def dup(x):\n    """二，措辞完全不同。"""\n    return (x + 1) * 2 - 3\n',
        },
    )
    n = _exact_groups(root)
    assert n == 1
