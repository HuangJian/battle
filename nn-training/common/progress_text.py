"""common/progress_text.py —— 进度行的**人读文本**（唯一实现）。

为什么要有它：进度行是操作员在云机日志 / hub 日志里**唯一**能看到的那条线，而它原来长这样
—— `进度 seq=8 iter it102 6/17`：

* `seq` 是**事件编号**，被当成轮号读过不止一次（2026-10-24 真机：`y34-hitfix` 明明在 it102，
  进度行却显示 `it0`）；
* 「在干什么」只有一个 `kind`（`iter`），PPO 与评估两段**根本没有事件** ⇒ 云机上看起来就是
  「一直停在采集局」；
* 「有没有问题」完全不可见。

现在两边（hub 的 `hub/offline_push.py` 与云机 supervisor 的 `remote/offline_push.py`）共用这一份措辞，
一行回答四件事：**第几条消息 · 第几轮 · 在干什么 · 进度（要不要紧）**。

`seq` 本身不动（它是 `progress.json` 的单调游标，hub 靠 `?since=N` 拉增量），只是**不再裸着给人看**。
"""

from __future__ import annotations

__all__ = [
    "KIND_TEXT",
    "LOG_PERCENT_STEP",
    "UNIT",
    "WARN_MARK",
    "progress_text",
    "should_log_progress",
]

#: 问题面的标记（`retry` 之类）：判据不许挂在它上面（`tests/helpers/text_asserts.py` 的口径），
#: 它只是给**人**看的那一眼。
WARN_MARK = "⚠"

#: 事件类型 → 人话（未登记的类型原样输出，不编）。
KIND_TEXT = {
    "seg-start": "段开始",
    "round-start": "本轮开始",
    "round-done": "本轮完成",
    "round": "本轮",
    "iter": "采集局",
    "ppo": "训练 PPO",
    "eval": "评估",
    "retry": "重试",
}

#: 各类型的计数单位（缺席 ⇒ 只给数字）：iter/eval 是局，ppo 是遍。
UNIT = {"iter": "局", "eval": "局", "ppo": "遍"}

#: 进度**日志行**的百分比步长（2026-10-27 用户口径：「自主云机的 log 要精简要节制」）。
#: 一轮 46 局的采集 = 46 条 `进度 第197轮 采集局：9/46 局`，而云机 + hub 两条腿各打一份
#: ⇒ 网页日志面板被「同一件事」的碎行塞满。每 `LOG_PERCENT_STEP`% 说一句（外加完成那一句、
#: 阶段/轮号变化那几句）就够人判断「在动 / 还剩多少」；事故信号（`retry`）一律照打。
#: ⚠ 只管**日志行**：发报（`/offline/progress`，hold 活性）按事件全量走，节流在打点层的时间窗。
LOG_PERCENT_STEP = 25


def progress_text(
    *, kind: str, it: int, done: int, total: int, seq: int | None = None
) -> str:
    """把一条进度事件翻成一句人话（`进度[第8条] 第102轮 采集局：6/17 局`）。

    * `seq` = **传输侧**的游标（第几条消息），只有 push 腿有（`progress.json` 的单调序号）；
      传 `None` 就整段不出现 —— 这一截是**唯一**允许两条腿不一样的地方（2026-10-25 用户口径：
      「不管 pull/push，rollout/ppo/eval 的输出日志应该一致，仅仅网络传输模块各自不同」）。
    * `it` = 第几轮；`done/total` = 已完成 / 共多少（`iter`/`eval` 数局，`ppo` 数遍）；
    * `retry` 是**问题面** ⇒ 加 `⚠` 前缀。
    """
    s = "进度" if seq is None else f"进度[第{int(seq)}条]"
    k = KIND_TEXT.get(str(kind or ""), str(kind or ""))
    if not k and not int(it):
        return s + " 还没收到进度（子进程刚起）"
    head = s + (f" 第{int(it)}轮" if int(it) else "")
    if str(kind or "") == "retry":
        return head + f" {WARN_MARK} 重试（第 {int(done)} 次）"
    out = head + (f" {k}" if k else "")
    if int(total) > 0:
        unit = UNIT.get(str(kind or ""), "")
        tail = f"{int(done)}/{int(total)}" if int(done) else f"共 {int(total)}"
        out += f"：{tail} {unit}".rstrip()
    return out


def _bucket(done: int, total: int) -> int:
    """进度落在哪个 `LOG_PERCENT_STEP` 宽的桶里（`total <= 0` ⇒ 0；不抛）。"""
    if int(total) <= 0:
        return 0
    return (int(done) * 100 // int(total)) // LOG_PERCENT_STEP


def should_log_progress(
    prev: tuple[str, int, int, int] | None, *, kind: str, it: int, done: int, total: int
) -> bool:
    """这一条进度事件**要不要打一行日志**（纯函数；`prev` = 上一条打过的那四个值）。

    打的条件（其余一律吞掉，发报不受影响）：

    * 第一句（`prev is None`）；
    * **换阶段 / 换轮**（`kind`、`it` 变了）——新的一段开始必须立刻可见；
    * `retry`（问题面：每次都要看见，不许被节流吃掉）；
    * 完成那一句（`done >= total`）；
    * 跨过 `LOG_PERCENT_STEP` 的桶（25% ⇒ 46 局一轮最多 4 句，而不是 46 句）。

    `total` **不进**判据里的「换阶段」那一条：流式采集的 `total` 每派一局就 +1，拿它当
    变化键 = 每局都打一行（那正是要治的那个形态）。`total` 只参与百分比桶的计算。
    """
    if prev is None:
        return True
    pkind, pit, pdone, ptotal = prev
    if (str(kind or ""), int(it)) != (str(pkind or ""), int(pit)):
        return True
    if str(kind or "") == "retry":
        return True
    if int(total) > 0 and int(done) >= int(total):
        return True
    return _bucket(done, total) > _bucket(pdone, ptotal)
