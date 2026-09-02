"""RLConfig —— 训练启动参数校验层（P1-3，2026-09-02；pydantic 版）。

背景：`args`（argparse.Namespace）穿透 15 个模块，`getattr(args, "x", default)`
的静默默认让**拼错/非法组合在训练中途才暴露**。全量改造成并替换所有下游签名的
风险/收益不成比例（三后端仍在演进）。

本模块折中：**保留 Namespace 穿透，但把关键参数收成 RLConfig 并在启动期校验**。

2026-09-02（库复用）：手写校验迁移到 **pydantic**（成熟库）——字段类型自动
强制（str→int/float）、范围/互斥用 field_validator / model_validator 声明式表达，
`ValidationError` 一次聚合全部错误。`validate()` 保留为兼容接口（返回错误列表，
供测试与旧调用方）；`validate_args` 是 run_rl 启动期的 fail fast 入口。
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError, field_validator, model_validator


class RLConfig(BaseModel):
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
    reward: str = ""

    @field_validator("workers")
    @classmethod
    def _workers_ge1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers 非法（≥1）")
        return v

    @field_validator("mb", "epochs", "eval_seeds")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("该参数非法（≥1）")
        return v

    @field_validator("local_slots", "keep_iters", "stop_loss_at", "precollect_games", "precollect_samples")
    @classmethod
    def _nonneg_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("该参数非法（≥0）")
        return v

    @field_validator("lr")
    @classmethod
    def _lr_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("lr 非法（>0）")
        return v

    @field_validator("stream", "double_buffer")
    @classmethod
    def _flag01(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("该参数非法（0/1）")
        return v

    @field_validator("adv_norm")
    @classmethod
    def _adv_norm_valid(cls, v: str) -> str:
        if v not in ("auto", "global", "wave", "none"):
            raise ValueError("adv_norm 非法（auto/global/wave/none）")
        return v

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ("per-tick", "intent", "goal"):
            raise ValueError("mode 非法（per-tick/intent/goal）")
        return v

    @field_validator("reward")
    @classmethod
    def _reward_valid(cls, v: str) -> str:
        # P1-12：'' / 'v7' / 'toy:<arm>'——防拼错静默走默认
        if v and v != "v7" and not v.startswith("toy:"):
            raise ValueError("reward 非法（'' 按 stage 解析 / 'v7' / 'toy:<arm>'）")
        if v.startswith("toy:") and len(v) <= len("toy:"):
            raise ValueError("reward 非法（toy:<arm> 需要具体 arm）")
        return v

    @model_validator(mode="after")
    def _check_mutual_exclusion(self):
        if self.precollect_games > 0 and self.precollect_samples > 0:
            raise ValueError(
                "precollect_games 与 precollect_samples 互斥——双缓冲只能选一种"
                "提前采样的度量口径（游戏数 或 样本量）"
            )
        return self

    def collect_errors(self) -> list[str]:
        """兼容接口：返回配置错误列表（空 = 合法）。

        pydantic 语义下非法配置在**构造时**即抛 ValidationError；本方法供
        需要"构造后收集错误"的调用方（重跑全部校验器并捕获）。
        """
        try:
            self.model_validate(self.model_dump())
        except ValidationError as e:
            return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        return []


def _errors_of(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]


def validate_args(args) -> None:
    """从 argparse.Namespace 构建 RLConfig 并校验；非法即 SystemExit（启动期 fail fast）。

    P1-3：`--precollect-games 5 --precollect-samples 1000` 这类互斥组合此前要到
    训练中途才暴露，现在启动即报（pydantic 一次聚合全部错误）。
    """
    from rl.log import log

    try:
        RLConfig(
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
            reward=getattr(args, "reward", ""),
        )
    except ValidationError as e:
        for err in _errors_of(e):
            log(f"[config] ERROR: {err}")
        raise SystemExit(
            "[run_rl] 启动参数非法（见上）——修复后重试；"
            "这些错误此前要等训练中途才暴露（P1-3 启动期校验）"
        ) from None
