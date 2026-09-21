"""课程热加载（DECISIONS §2026-09-13-hot-reload）：每 iter 重读课程文件，按语料身份分流。

分类学沿用 §2026-09-13-level-extraction：
- **非语料改动**（corpus_identity_fp 不变；B/C 类）：白名单字段直接写回 args——消费点
  每轮活读（iters/gamma/lam/epochs/mb/eval_*/ent_break*，loop_core:207 / loop_steps
  _course_iter），**下一 iter 即生效**；结构绑定字段（bc/workers/out/traj 等）记
  restart-only，响亮日志「停止→启动后生效」，不静默吞。
- **语料身份改动**（corpus_fp 变了 = A 类破坏性）：拒绝热应用，写 `course_edit` 事件
  （控制台横幅）+ 响亮日志，**沿用启动期配置继续训练**。云端不可见：D13 课程快照 /
  course_fp 用启动冻结字节（`args.course_frozen_bytes`），编辑内容不进任何 payload。

纯逻辑（plan_reload/apply_hot_fields）与副作用（_hot_reload_course 方法）分离，可单测。
"""

from __future__ import annotations

from typing import Any

#: 消费点每轮活读 args 的字段（loop_core:207 iters / loop_steps._course_iter
#: gamma,lam / 每评估轮 eval_* / breaker ent_break* / schedule 基值 lr,epochs,mb）——
#: 热写回 args 即下一 iter 生效。
HOT_FIELDS: tuple[str, ...] = (
    "iters",
    "max_hours",
    "eval_stages",
    "eval_games_per_stage",
    "eval_every",
    "gamma",
    "lam",
    "lr",
    "epochs",
    "mb",
    "ent_break",
    "ent_break_consec",
    "ent_break_max_winrate",
)

#: 结构绑定、热写回 args 也不生效的字段（启动期冻结：池拓扑/路径/账本/ref 快照/
#: 模型手术/一次构建的 update_kwargs）——只记日志提示「停止→启动后生效」。
RESTART_ONLY_FIELDS: tuple[str, ...] = (
    "bc",
    "workers",
    "stream",
    "keep_iters",
    "warmup_iters",
    "kickstart_ref",
    # 缰绳初值（§5.1）：kk 调度的初值在启动自检/args 合并时读一次，热改它不会让已经
    # 排好的衰减曲线回头——与 kickstart_ref 同类，记 restart-only（响亮提示），不静默吞。
    "kickstart_init",
    "normalize_ret",
    "out",
    "traj",
    "backup_dir",
    "backup_prefix",
    "freeze",
    "freeze_heads",
    "clip_eps",
    "vf_coef",
    "ent_coef",
    "max_grad_norm",
)


#: restart-only 里**课程键 ≠ args dest** 的字段（只需列异名；同名的 dest 就是键名）。
#: 为什么需要：`apply_hot_fields` 拿 args 当前值当「变更前」的对照，异名字段不映射就会
#: 拿 None 去比课程值 ⇒ 每次热加载都恒报「变了」（假变更行，日志在骗人）。
RESTART_ONLY_ALIASES: dict[str, str] = {"kickstart_init": "kickstart_kl"}


def plan_reload(old: Any, new: Any) -> tuple[str, list[str], list[str]]:
    """比较启动课程与盘上新课程 → (verdict, hot_changed, restart_only_changed)。

    verdict：`"same"`（逐字段相等）/ `"apply"`（有改动且语料身份未变）/
    `"rejected"`（corpus_identity_fp 变了 = A 类破坏性，整单拒绝——包括与 hot/restart
    字段同时出现的 env/reward 改动，不做部分应用，避免半新半旧配置）。
    """
    from rl.config import corpus_identity_fp

    if corpus_identity_fp(new) != corpus_identity_fp(old):
        return ("rejected", [], [])
    hot = [f for f in HOT_FIELDS if getattr(new, f, None) != getattr(old, f, None)]
    restart = [f for f in RESTART_ONLY_FIELDS if getattr(new, f, None) != getattr(old, f, None)]
    if not hot and not restart:
        return ("same", [], [])
    return ("apply", hot, restart)


def apply_hot_fields(args: Any, new_course: Any) -> list[str]:
    """白名单字段写回 args + 换挂 course_obj → 返回实际变更字段名（含 restart-only 记账）。

    `max_hours` 只改 args——deadline 重算是调用方（loop 持有 self._deadline）的职责。
    """
    changed: list[str] = []
    for f in HOT_FIELDS:
        nv = getattr(new_course, f, None)
        if getattr(args, f, None) != nv:
            setattr(args, f, nv)
            changed.append(f)
    for f in RESTART_ONLY_FIELDS:
        av = getattr(args, RESTART_ONLY_ALIASES.get(f, f), None)
        if getattr(new_course, f, None) != av:
            changed.append(f + "*")  # `*` = restart-only，日志口径
    args.course_obj = new_course
    return changed


def changed_field_names(old: Any, new: Any) -> list[str]:
    """全字段 diff（model_dump 键级），供拒绝事件的字段清单。"""
    a, b = old.model_dump(), new.model_dump()
    return sorted(k for k in b if a.get(k) != b.get(k))
