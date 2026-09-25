"""loop_remote_fail —— **远端失败策略** mixin（2026-09-25 S4 第二十二刀拆出）。

判据同源：「**远端失败的唯一处置策略**」——节点已回报原因的确定性失败
（`JobFailedError` ⇒ 不重试、写 ABORT、停腿）与「重试多少次都不会自愈」的鉴权/闭锁类
HTTP（同样立即停腿），其余只计连败配额（连败 3 次停腿；**没有**本机降级）。

为什么要单独一个判据：拆相之后「发布失败 / 取结果失败 / 落位失败」是**同一类**失败，
必须过同一份判决——三段各自演化出不同的连败/停腿口径，正是本仓最贵的一类分叉
（x3-step 事故就是「专为远端失败写的停腿判决一行没写」）。

依赖方向：本簇是**叶子**（不调任何兄弟方法，除自身的 `_abort_node_failure`）；
`TrainingRemoteDrive` 把它当第二个基类（与 `TrainingRemoteJob` 并列）。
"""

from __future__ import annotations

from typing import Any

from common.protocol import JobFailedError
from rl.events import write_gate_verdict
from rl.log import log
from rl.loop_transport import fatal_remote_http


class TrainingRemoteFail:
    """远端失败的唯一处置策略：确定性失败立即停腿，可重试失败计连败配额。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 「有意并存」的理由同 `rl/loop_remote_push.py`。
    _jsonl_path: Any
    #: 远端连续失败 → 写 ABORT 后停腿（loop 在 PPO 步后检查）。
    _leg_abort: bool
    #: 远端连续失败计数（成功即复位）。
    _remote_fail: int

    def _abort_node_failure(self, it: int, e: BaseException, *, where: str) -> None:
        """节点已回报原因的**确定性**失败 → 写 ABORT 判决 + 停腿标记。

        2026-09-17（plan/remote-wire-remediation §5.3 缺口）：此前这类失败（bun 装不上 /
        TS 运行时取不到 / argv 非法）在训练侧只表现为 `wait_job` 25 分钟超时——
        「能力缺失」被写成「网络/排队问题」，而且每次重试再白烧一个超时窗口。现在原因
        随 `JobFailedError` 直接到达，判决里写的是真原因（人一眼能修）。

        不重试、不降级：节点缺的是运行时能力，换机/换轮都一样；修完节点重跑同课即可
        （重发同 job 会清失败标记，见 hub_client.publish_job）。
        """
        reason = str(e)[:300]
        write_gate_verdict(
            self._jsonl_path,
            it,
            "ABORT",
            f"{where} 节点确定性失败（原因已随 /jobs/{{id}}/fail 回传）：{reason}",
            decider="loop",
        )
        log(f"[run_rl] GATE ABORT it{it}: {where} 节点报确定性失败——不重试，立即停腿：{reason}")
        self._leg_abort = True

    def _handle_remote_failure(self, it: int, e: BaseException) -> bool:
        """远端失败的**唯一**处置策略：`True` = 调用方原样上抛（本轮失败）。

        ★ 2026-09-21（§3 单一 PPO 路径）：**没有**"降级到本机"这一档 —— 返回值恒为 True，
        连败 3 次即 ABORT 停腿（原先的 `False = 已降级本机` 分支已连同旗标一并删除）。
        要本机算，操作员在控制台起本机 worker（与云机同一认领协议）。

        为什么要抽出来：三相拆分之后，「发布失败」「取结果失败」「落位失败」是**同一类**
        失败，必须过同一份判决——三段各自演化出不同的连败计数/停腿口径，正是本仓最贵的
        一类分叉（x3-step 事故就是「专为远端失败写的停腿判决一行没写」）。
        """
        if isinstance(e, JobFailedError):
            # 节点已回报原因的**确定性**失败（2026-09-17）：不消耗连败配额、不重试
            # ——重试只会再派给另一台同样干不了的机器，或等回同一个 410。
            self._abort_node_failure(it, e, where="远端 PPO")
            return True
        # 401/403/400：token 不对、IP 被 hub 闭锁、或请求本身有问题——**重试多少次都不会
        # 自愈**，继续消耗连败配额只是重复 publish 同一 job 并把停腿拖后（x3-step 事故：
        # 5×30s 空转 + 账本 5 条同 id job_pending，最后照样死）。
        fatal = fatal_remote_http(e)
        if fatal:
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"远端 PPO 不可重试失败 HTTP {fatal}——检查 --remote-token 与 hub 日志 "
                f"AUTH FAIL / BLOCKED 行：{str(e)[:200]}",
                decider="loop",
            )
            log(
                f"[run_rl] GATE ABORT it{it}: 远端 HTTP {fatal}（鉴权/闭锁类，非网络抖动）"
                f"——不再重试，立即停腿"
            )
            self._leg_abort = True
            return True
        # 只对**可重试**失败计账（确定性失败在上面两档已提前 return——它们不消耗配额）。
        self._remote_fail += 1
        log(
            f"[run_rl] remote ppo it{it} FAILED ({type(e).__name__}: {str(e)[:200]}) — "
            f"consecutive={self._remote_fail}"
        )
        # ★ 单一 PPO 路径（2026-09-21 §3）：**没有**就地降级——连败 3 次即 ABORT 停腿。
        # loop 自己不具计算能力；想要本机算，操作员在控制台起本机 worker（与云机同一认领
        # 协议），而不是训练进程偷偷把 job 算在自己身上（C 腿事故的根）。
        if self._remote_fail >= 3:
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"远端 PPO 连续失败 {self._remote_fail} 次（单一 PPO 路径：无本机降级）",
                decider="loop",
            )
            log(f"[run_rl] GATE ABORT it{it}: 远端不可用（无本机降级）——停腿")
            self._leg_abort = True
        return True
