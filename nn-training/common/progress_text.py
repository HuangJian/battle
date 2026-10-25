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

__all__ = ["KIND_TEXT", "UNIT", "WARN_MARK", "progress_text"]

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
