"""trainer_gate.py — 常驻训练的**准入闸**（plan/single-resident-trainer.plan.md §1 + §3）。

## 为什么需要它

`trainer/run_rl.py` 曾背四种角色（常驻训练循环 / 冒烟预演 / 导出快照 / 预采子进程），
`trainer/run_bc.py` 同理（常驻 BC 编排 / 冒烟）。而常驻写者只允许一个 —— `trainer/run_rl_cluster.py --serve`
（单进程服务所有课程，R3-5）。per-course 常驻进程与共享调度器抢同一批 traj / 账本 / 权重，
正是 2026-09-06「双 trainer 并行写同一 traj 7 分钟」事故的同类面；它还会绕过控制台的开课流程
（漏开课标记 / 暂停意图解禁 / hub 三件套，2026-09-21 事故）。

## 判据（纯函数，无 I/O）

**一个一次性动作都没有 ⇒ 视为常驻训练 ⇒ 拒启**。与 `--course` 无关：无 `--course` 的老全局路径
同样是常驻写者。一次性动作（`ONESHOT_FLAGS`）：`--smoke` / `--collect-only N>0` / `--export-bundle` /
`--echo-config`。

## 2026-10-17（S3）：逃生舱退役 —— 判据从「拦走旁路」变成「没有旁路可走」

S1 曾给过一条运维逃生舱 `--allow-detached-trainer`（放行 + 醒目告警 + 往
`tmp/trainer-detached.json` 记一笔 ⇒ 控制台横幅）。S3 把常驻训练分支**从 run_rl 里删掉**之后，
那条旁路已无处可去：`run_rl.py` 只剩壳（解析 → 一次性腿 / 拒启），常驻训练唯一入口是
`run_rl_cluster.py --serve`。

⇒ 逃生舱、自报文件、横幅数据源（写入侧；dashboard 的读面同批退役）一起删除。**不要再加回来**：
它当初的用途是「S1→S2 之间还有常驻分支可走」的过渡期设计（plan §4），分支没了，它就只是一条
「让操作员以为自己在跑训练、实际什么也没跑」的假动作。

## 为什么住 `worker/` 而不是 `trainer/run_rl.py`

`trainer/` 只许编排（`tests/test_layering.py::test_trainer_holds_only_orchestration_modules`），
而本模块是纯逻辑；`trainer/run_rl.py` 与 `trainer/run_bc.py` 两个入口**同源 import 这一份**
——判据写两份必然 drift（本仓的老 pattern）。
"""

from __future__ import annotations

from typing import Any

#: 一次性腿标志（豁免集合）。**判据是「有没有动作」，不是「有没有课」**——
#: 无 `--course` 的裸 `run_rl.py` 同样是常驻写者（老全局锁时代的形态），一样拒。
ONESHOT_FLAGS: tuple[str, ...] = (
    "smoke",  # --smoke：真跑一轮预演，落位前作废
    "collect_only",  # --collect-only N>0：预采子进程（T4 双缓冲）
    "export_bundle",  # --export-bundle <path>：只读快照导出
    "echo_config",  # --echo-config：纯 dump（对拍 oracle 读 stdout）
)

#: 拒启消息：**必须给出替代**（否则操作员只会去找 `--force` —— 而 `--force` 是锁抢先，
#: 不是准入豁免：闸在锁之前，`--force` 过不了它），并且要说明「本入口已没有常驻分支」
#: （否则下一个人会以为带个旗标就能绕）。
REFUSE_MESSAGE = (
    "[run_rl] 常驻训练只允许单一调度器：trainer/run_rl_cluster.py --serve\n"
    "  · 控制台：bun run dashboard（:8900）→ 组件卡「TrainingLoop」启动\n"
    "  · 无头：bun dashboard/src/launch/cli.ts --script trainer/run_rl_cluster.py\n"
    "  · 一次性腿（豁免）：--smoke / --export-bundle / --collect-only / --echo-config"
)

#: BC 侧的拒启消息（同一判据，替代路径不同：BC 课由共享 trainer 开课服务）。
REFUSE_MESSAGE_BC = (
    "[run_bc] 常驻 BC 训练只允许单一调度器：trainer/run_rl_cluster.py --serve（serve 已带 BC 课）\n"
    "  · 控制台：bun run dashboard（:8900）→ 课程 → 开课（BC 课由共享 trainer 调度）\n"
    "  · 一次性腿（豁免）：--smoke"
)


def oneshot_action_present(args: Any) -> bool:
    """带了任一一次性动作标志吗？（纯函数，无 I/O）

    逐字段 `getattr` 带缺省：BC 解析器比 RL 少几个键，「没有这个字段」= 没传这个动作，不是错误。
    """
    if bool(getattr(args, "smoke", False)):
        return True
    if int(getattr(args, "collect_only", 0) or 0) > 0:
        return True
    if str(getattr(args, "export_bundle", "") or ""):
        return True
    return bool(getattr(args, "echo_config", False))


def resident_trainer_wanted(args: Any) -> bool:
    """本进程要跑**常驻训练循环**吗？（= 一个一次性动作都没带）"""
    return not oneshot_action_present(args)


def refuse_detached_trainer(args: Any, *, message: str = REFUSE_MESSAGE) -> str | None:
    """拒启消息，或 None（放行）。**纯判据** —— 打印/退出全在调用点。"""
    return message if resident_trainer_wanted(args) else None
