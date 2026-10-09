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

降 53 条全是本次删掉的用例（T1 的 6 条账本门端到端、T3 的 2 条 legacy 重复点）带走的。

要抬基线：**不**直接改数字——先在 commit message 里写清「为什么这条判据只能看文本」，
再改；抬一格是一次有意的欠债，不是顺手。降了就同步改小（历史高水位只留在这里）。
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import text_asserts as TA

ROOT = Path(__file__).resolve().parents[1]

#: 形态口径基线（2026-10-09 T1/T3/T2 删除后实测；清理前 = 1488）。
BASELINE_FORM = 1435
#: 生产源码文本口径基线（同上；清理前 = 1084）。
BASELINE_SRC = 1082
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
