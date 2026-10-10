"""文本断言的**计数护栏**（2026-10-09，`plan/nn-training-test-debt-cleanup.plan.md` §3-S3-2）。

口径 = `tests/helpers/text_asserts.py` 模块头（全仓**唯一**的计数定义）。这里只做一件事：
**只许降，不许升**。

为什么需要它（本 plan 的原始痛点）：`assert "…" in <源码/输出文本>` 这类判据挂在**措辞**上——
重构必炸、改标题字也炸，而且它绿不代表行为对。T5 清掉一批后，若没有这条护栏，下一次重构
又会顺手撒一批回去。

基线（= 本文件写死的那两个数；历史水位只留在这里）：

| 时点 | 形态口径 | 生产源码文本口径 |
|---|---|---|
| 2026-10-09 清理前（HEAD `e7cbc0a8`） | 1488 条 / 228 文件 | 1084 条 / 106 文件 |
| 2026-10-09 T1/T3/T2 删除后（写死在这里） | **1435 条** | **1082 条** |
| 2026-10-10 新增 `tests/remote/test_rollout_node_notebook.py`（抬 SOURCE 一格） | 1435 条 | **1091 条** |
| 2026-10-11 aistudio 传输硬化新增 5 个用例文件（抬 FORM 一格，+6） | **1441 条** | 1091 条 |

降 53 条全是本次删掉的用例（T1 的 6 条账本门端到端、T3 的 2 条 legacy 重复点）带走的。

**2026-10-10 抬 SOURCE 一格的账**（plan/rollout-node-auto-register v2；形态口径**没抬**，当时 = 1435）：
采样节点 notebook 改薄 cell 后要一条接线守卫（同 `test_offline_notebook.py` 那族）：它检查的对象**就是**
cell 文本与 `remote/rollout_node.py` 的源码 —— 「cell 能不能 `compile()`」「CFG 是否覆盖模块读的每个键」
「凭据读取是否先于 `ensure()`」「公网下载是否包 `_public_net()`」「`finally` 是否 unregister + 清子进程」。
这些契约没有可调用的运行时入口（notebook 不在 pytest 里执行），只能读源码。抬格前已先把能改的都改成
非文本判据（字段成员、状态码、`.startswith`、AST 结构、`set ==` 等：形态口径 20 条 → 0），
剩下的 9 条全是「只读源码才能判」的那一类。

要抬基线：**不**直接改数字——先在 commit message 里写清「为什么这条判据只能看文本」，
再改；抬一格是一次有意的欠债，不是顺手。降了就同步改小（历史高水位只留在这里）。

**2026-10-11 抬 FORM 一格的账**（plan/aistudio-transfer-hardening；形态 1435 → 1441 = +6，SOURCE 未动）：
这 6 条全在**新增的 5 个用例文件**里（`test_yield_adaptive.py` 3 · `test_prefetch_adaptive.py` 2 ·
`test_control_pool.py` 1），且全是**对日志行**的子串断言 —— 断言的就是**交付物本身**：
方案要求 A1/A2「必须是**可观测地关**（打一行带原因 + 回退旋钮）」，B3 要求「日志必须写明重试次数」
（旧文案只写「失败」会把「6s×2」读成「一次没接上」）。这些日志行是给**操作员**的接口，
被测物没有可调用的结构化出口来替代它们（行为面已在同文件里另有用例钉住：「自停后零 peek」
「降到 never 语义 + 退出还原」）。⚠ 这**不**意味着告警（分位/白传）的判据可以继续挂在文本上：
本轮 32 条阈值/读数判据全部是**常量相等 / 集合相等 / 布尔返回**形态（口径 A 零命中）。
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import text_asserts as TA

ROOT = Path(__file__).resolve().parents[1]

#: 形态口径基线（2026-10-09 T1/T3/T2 删除后实测 = 1435；清理前 = 1488；
#: 2026-10-11 aistudio 传输硬化抬到 1441，理由见模块头）。
BASELINE_FORM = 1441
#: 生产源码文本口径基线（同上；清理前 = 1084；2026-10-10 因新 notebook 接线守卫抬到 1091，理由见模块头）。
BASELINE_SRC = 1091
#: 计数异常下界：护栏最怕的是「口径坏了 ⇒ 恒 0 ⇒ 恒绿」。
SANITY_FLOOR = (400, 200)


def test_text_assert_counts_only_go_down() -> None:
    form, src, per_file = TA.totals(ROOT)
    assert form > SANITY_FLOOR[0] and src > SANITY_FLOOR[1], (
        f"计数异常偏小（形态 {form} / 源码面 {src}）—— 扫描面或口径坏了，护栏会恒绿"
    )
    top = sorted(per_file.items(), key=lambda kv: -kv[1][1])[:5]
    hint = "；".join(f"{rel}={s}" for rel, (_, s) in top)
    assert form <= BASELINE_FORM, (
        f"文本形态断言 {form} > 基线 {BASELINE_FORM}（多的见 {hint}）—— "
        f"新判据请改成行为断言；确需文本判据就在本文件抬一格并写明理由"
    )
    assert src <= BASELINE_SRC, (
        f"生产源码文本断言 {src} > 基线 {BASELINE_SRC}（多的见 {hint}）—— "
        f"同上：源码长什么样 ≠ 行为对"
    )
