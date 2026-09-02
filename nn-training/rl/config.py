"""RLConfig —— 训练启动参数校验层（P1-3 轻量版，2026-09-02）。

背景：`args`（argparse.Namespace）穿透 15 个模块，`getattr(args, "x", default)`
的静默默认让**拼错/非法组合在训练中途才暴露**。全量改造成 frozen dataclass 并
替换所有下游签名的风险/收益不成比例（三后端仍在演进）。

本模块折中：**保留 Namespace 穿透，但把关键参数收成 RLConfig 并在启动期校验**——
互斥、范围、非法值在训练开始前 fail fast；校验规则集中一处（未来迁移 dataclass
时规则直接复用）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RLConfig:
    """训练启动关键参数（校验视图；不承载全部 argparse 字段）。"""

    mode: str = "per-tick"
    iters: int = 0  # <=0 = 无限
    stream: int = 1
    double_buffer: int = 0
    precollect_games: int = 0
    precollect_samples: int = 0
    workers: int = 8
    local_slots: int = 0
    mb: int = 512
    epochs: int = 4
    lr: float = 3e-4
    seed: int = 7
    keep_iters: int = 3
    stop_loss_at: int = 0
    stop_loss_delta: float = 0.0
    adv_norm: str = "auto"
    eval_seeds: int = 10
    eval_at: str = ""

    def validate(self) -> list[str]:
        """返回配置错误列表（空 = 合法）。启动期调用，任一错误即阻止训练。"""
        errs: list[str] = []
        if self.mode not in ("per-tick", "intent", "goal"):
            errs.append(f"mode={self.mode!r} 非法（per-tick/intent/goal）")
        if self.iters < -1:
            errs.append(f"iters={self.iters} 非法（-1/0=无限，正数=有限轮）")
        if self.stream not in (0, 1):
            errs.append(f"stream={self.stream} 非法（0/1）")
        if self.double_buffer not in (0, 1):
            errs.append(f"double_buffer={self.double_buffer} 非法（0/1）")
        if self.precollect_games > 0 and self.precollect_samples > 0:
            errs.append(
                "precollect_games 与 precollect_samples 互斥——双缓冲只能选一种"
                "提前采样的度量口径（游戏数 或 样本量）"
            )
        if self.precollect_games < 0 or self.precollect_samples < 0:
            errs.append("precollect_games/precollect_samples 不能为负")
        if self.workers < 1:
            errs.append(f"workers={self.workers} 非法（≥1）")
        if self.local_slots < 0:
            errs.append(f"local_slots={self.local_slots} 非法（≥0）")
        if self.mb < 1:
            errs.append(f"mb={self.mb} 非法（≥1）")
        if self.epochs < 1:
            errs.append(f"epochs={self.epochs} 非法（≥1）")
        if self.lr <= 0:
            errs.append(f"lr={self.lr} 非法（>0）")
        if self.keep_iters < 0:
            errs.append(f"keep_iters={self.keep_iters} 非法（≥0）")
        if self.adv_norm not in ("auto", "global", "wave", "none"):
            errs.append(f"adv_norm={self.adv_norm!r} 非法（auto/global/wave/none）")
        if self.eval_seeds < 1:
            errs.append(f"eval_seeds={self.eval_seeds} 非法（≥1）")
        if self.stop_loss_at < 0:
            errs.append(f"stop_loss_at={self.stop_loss_at} 非法（≥0）")
        return errs


def validate_args(args) -> None:
    """从 argparse.Namespace 构建 RLConfig 并校验；非法即 SystemExit（启动期 fail fast）。

    P1-3：`--precollect-games 5 --precollect-samples 1000` 这类互斥组合此前要到
    训练中途才暴露，现在启动即报。
    """
    cfg = RLConfig(
        mode=getattr(args, "mode", "per-tick"),
        iters=getattr(args, "iters", 0),
        stream=int(getattr(args, "stream", 1)),
        double_buffer=int(getattr(args, "double_buffer", 0) or 0),
        precollect_games=int(getattr(args, "precollect_games", 0) or 0),
        precollect_samples=int(getattr(args, "precollect_samples", 0) or 0),
        workers=getattr(args, "workers", 8),
        local_slots=int(getattr(args, "local_slots", 0) or 0),
        mb=getattr(args, "mb", 512),
        epochs=getattr(args, "epochs", 4),
        lr=float(getattr(args, "lr", 3e-4)),
        seed=getattr(args, "seed", 7),
        keep_iters=getattr(args, "keep_iters", 3),
        stop_loss_at=getattr(args, "stop_loss_at", 0),
        stop_loss_delta=float(getattr(args, "stop_loss_delta", 0.0)),
        adv_norm=getattr(args, "adv_norm", "auto"),
        eval_seeds=getattr(args, "eval_seeds", 10),
        eval_at=getattr(args, "eval_at", ""),
    )
    errs = cfg.validate()
    if errs:
        from rl.log import log

        for e in errs:
            log(f"[config] ERROR: {e}")
        raise SystemExit(
            "[run_rl] 启动参数非法（见上）——修复后重试；"
            "这些错误此前要等训练中途才暴露（P1-3 启动期校验）"
        )
