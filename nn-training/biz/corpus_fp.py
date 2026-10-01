"""corpus_fp —— **语料身份指纹**（D14 语义版）与它的版本常量（2026-09-30 从 `biz/config.py` 下沉）。

**为什么要单独一个模块**：身份 = 「决定一个样本是什么」的那组字段的哈希。它由**游戏域**定义
（env 编码布局 / mode / stages / difficulty / max_ticks / seed_rotate / seeds / player / dodge /
reward 公式与参数 / 起始分布 / 动态采集规则版本 …），而不是训练运行参数（预算、路径、优化器、
schedule 都刻意排除）。因此它的家是 `biz/`（游戏业务），**不是** `worker/`（训练运行面）——
后者只消费它：D14 在四个 funnel（本地对账 / hub 打包 / 云端装载 / 加载侧）用它判「异血缘拒收」。

`VOLUME_RULE_V1` 也住这里：动态采集的**规则版本**是语料身份的一个分量（规则变了 ⇒ 一个样本是
什么变了 ⇒ §15.5「改语料 = 新实验」），所以常量与身份同住；`worker/volume_waves.py` 反向 import 它。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from biz.course_spec import CourseConfig

#: 动态采集规则版本（原住 `biz/volume_waves.py`，2026-09-30 随语料身份一起下沉）。
#: 任何改变「怎么算够了」的规则改动都要 bump 它 —— 它进 `corpus_identity_fp` 的 payload，
#: 于是规则变更自动让旧 shard 成为异血缘（D14 拒收），而不是静默混训（§15.5）。
VOLUME_RULE_V1 = 1

def corpus_identity_fp(course: CourseConfig) -> str:
    """语料身份指纹（D14 语义版）：sha256(canonical(env+reward))。

    覆盖 = 决定「一个样本是什么」的全部字段：**obs 编码布局（schema major + 指纹）** /
    mode / stages（解析后）/ difficulty / max_ticks / seed_rotate / seeds / player /
    dodge / reward(formula+params+terminal+scheme)，以及**激活时**的
    `paired_rotate_seed`（配对 rotateSeed，2026-09-21 §2）与 `target_transitions`。
    **刻意排除** iters/max_hours/eval_*/out/traj/bc/optimizer/schedule 等预算、测量、
    路径与优化器键——这些改动不构成语料混入，mid-run 编辑课程不得触发 D14 拒收
    （DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25 的配置修改分类学）。哈希**解析后**的值：
    内联 stages 与 level 引用同形同指纹；关卡文件内的注释/格式变动不影响身份。

    ⚠ schema 必须在内（2026-09-13 补，与 BC 侧 bc_corpus_identity_fp 同一坑）：
    身份决定 D14 混训分流——漏掉 schema ⇒ v2(14ch) 与 v3(16ch) 语料被判为同一身份
    ⇒ 允许混入同一训练（形状不同的 shard 拼一起）。远端结果缓存键虽含 runId（RL 侧
    每次启动新 runId，续跑复用 runId 时同 BC 一样裸奔），但 D14 分流不看 runId，
    只有身份本身含 schema 才能把跨 era 语料挡在**混入之前**（加载侧
    data.npyio.verify_shard_schema 是最后一道，到那一步已经在崩了）。
    """
    from common.schema import MOVE_LABEL_SEMANTICS, OBS_SCHEMA_MAJOR, SCHEMA_FINGERPRINT

    stages = (
        [s.model_dump() for s in course.stages]
        if isinstance(course.stages, list)
        else course.stages
    )
    payload = {
        # 编码布局：schema bump / 指纹变化 ⇒ 「一个样本是什么」已变，身份必须跟着变
        "obs_schema_major": OBS_SCHEMA_MAJOR,
        "obs_schema_fingerprint": SCHEMA_FINGERPRINT,
        # 动作标签映射版本（plan/new-era-stop #7）：“同一个 a_move 字节代表什么”
        # 也是「一个样本是什么」的一部分。B案把 index 0 从 keep 改为 **STOP** ⇒ 旧
        # policy rollout shard 里的 a_move==0 行语义已翻转 ⇒ 必须让它们成为**异血缘**
        # （D14 在四个 funnel 统一拒收），而不是静默混训。**无条件进 payload**：这是
        # 一次全局标签语义变更（不是某条课程的开关），漂移就是目的。demo/BC 语料不走
        # 本函数（用 bc_corpus_identity_fp，刻意不含此键）⇒ 它们标注的 null→0 物理上
        # 本来就正确，祖父保留。
        "move_label_semantics": MOVE_LABEL_SEMANTICS,
        "mode": course.mode,
        "stages": stages,
        "difficulty": course.difficulty,
        "max_ticks": course.max_ticks,
        "seed_rotate": course.seed_rotate,
        "seeds": course.seeds,
        "player": course.player.model_dump(),
        "dodge": course.dodge,
        "reward": course.reward.model_dump(),
    }
    # 动态采集（2026-09-15）：target_transitions 与 seed_rotate 同类（决定**抽哪些**
    # 样本），进身份；规则版本常量随之进（规则变更必须能让 D14 一眼分辨）。
    # ⚠ **仅在键激活时进 payload**：无条件加入会让每一条既有课程的指纹全体漂移
    # （D14 血缘断裂、在跑的腿 shard 被当异身份），与「缺席 = 老行为逐字节不变」
    # （§2.1）直接矛盾。est/max_games 刻意**不进**：前者是首轮兜底估计、运行期由
    # trailing 均值覆盖（预算/参数类，同 iters/max_hours 分类学），后者是硬顶不是语料。
    if course.target_transitions > 0:
        payload["volume_rule"] = VOLUME_RULE_V1
        payload["target_transitions"] = course.target_transitions
    # 配对 rotateSeed（2026-09-21 §2）：与 seed_rotate 同类（决定**抽哪些**样本），故进身份；
    # 但同样**仅在激活时**——无条件加入会让所有既有课程的指纹全体漂移（在跑的腿把已落盘
    # shard 判成异身份，先例 `tests/worker/test_rollout_volume.py:492-499`）。
    if course.paired_rotate_seed is not None:
        payload["paired_rotate_seed"] = int(course.paired_rotate_seed)
    # 起始分布（plan/x20-state-init.plan.md P2/P3.5，2026-09-25）：**决定一个样本从哪个世界
    # 开始**，与 seed_rotate/mode 同类——「标准开局 300 tick 后的观察」与「中段状态交棒后的
    # 观察」不是同一种货，混进同一轮训练/dimension 统计就是换实验而不换账。故进身份：
    #   · 课程中途加/删/改 `state_init`（含换 bank 或改切点）⇒ corpus_fp 变 ⇒ 旧 shard 在
    #     **所有** funnel（本地对账 / hub 打包 / 云端装载）被 D14 自动排除，零额外参数；
    #   · 反向「老节点忽略 --init-snapshot 却产出同指纹 shard」不由这里兜（同一份配置指纹
    #     相同），由 `biz.resume.shard_state_init_ok` 的 `initTick` 护栏兜（P3.5）。
    # ⚠ 同样**仅在激活时**（同上面两条的理由）：不加会让既有一切课程指纹漂移。
    # bank 路径进身份但只算**文件名**：绝对/相对写法（cwd 不同）不得改变语料身份。
    if course.state_init is not None:
        si = course.state_init
        payload["state_init"] = {
            "bank": Path(si.bank).name,
            "cut_from": si.cut_from,
            "cut_to": si.cut_to,
            "cut_step": si.cut_step,
            "rotate_cuts": bool(si.rotate_cuts),
        }
    # 决策事件补充（§6 R2）：transition 粒度（Δt 分布）变了 ⇒ 样本身份变；
    # 同样**仅在激活时**（无条件加入会让一切既有课程指纹漂移，在跑的腿 shard 被判异身份）。
    if bool(getattr(course, "decision_events", False)):
        payload["decision_events"] = True
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
