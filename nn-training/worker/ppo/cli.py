"""goal / intent 两个 numpy 头的 CLI 外壳（B8 ④ 的收尾）。

两个 `main()` 的差别只有 **九句文案 + 五个函数**（建网 / 载权重 / 导权重 / 装载 episodes /
update），其余是同一串顺序：

    建 parser → 播种 / 线程 / denormal / device → init 分支 → update 分支
    （载权重 → 装载 → 切块 → Adam → 可选 kickstarting ref → update → 导出 → 收尾行）

`--help` 的文案、参数顺序、默认值与日志前缀都**逐字**保留 —— 判据是
「`-m worker.ppo.goal --help` 与切分前逐字节一致」，见 `docs/nn/engineering.md` §223。

为什么值得合：两份 `main` 里任何一处修正（例如「`--threads 0` 要保留启动器默认」）都得在
另一边再改一次，而它不跑不会红。顺序只该有一份，文案进 `CliSpec` 数据。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import torch

from common.cli import house_parser
from worker.ppo.common import log
from worker.ppo.np_core import chunk_episodes
from worker.ppo.rng import PPORng


@dataclass(frozen=True)
class CliSpec:
    """一个头的 CLI 面：文案（`--help` 逐字）+ 五个函数 + 两个开关。"""

    #: 日志前缀，如 `"ppo_goal"`（日志行恒为 `[{tag}] …`）。
    tag: str
    help_init: str
    help_resume: str
    help_data: str
    #: `None` = 该参数**不带** help（两个头本来就不一致，逐字保留）。
    help_warmup: str | None
    help_kl: str | None
    help_ref: str
    #: 装载行里的步数名词：`goal-steps` / `intent-steps`。
    step_word: str
    #: init 行的括号内容：`goal-BC + random value` / `B′/BC intent + random value`。
    init_desc: str
    #: 收尾行是否带 `bc=` 列（goal 有，intent 没有 —— schema 兼容占位）。
    with_bc: bool
    lr: float
    build_net: Callable[[str | None], Any]
    load_weights: Callable[[Any, str], None]
    export_weights: Callable[[Any, str], None]
    load_episodes: Callable[[str], list[dict]]
    update: Callable[..., dict]


def _parser(spec: CliSpec):
    """建 parser：**参数顺序与 help 文案逐字照旧**（`--help` 输出是对外契约）。"""
    ap = house_parser()
    ap.add_argument("--init-from", type=str, default=None, help=spec.help_init)
    ap.add_argument("--resume", type=str, default=None, help=spec.help_resume)
    ap.add_argument("--data", type=str, default=None, help=spec.help_data)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--lr", type=float, default=spec.lr)
    ap.add_argument("--value-warmup-epochs", type=int, default=0, help=spec.help_warmup)
    ap.add_argument("--kl-coef", type=float, default=0.0, help=spec.help_kl)
    ap.add_argument("--ref-weights", type=str, default=None, help=spec.help_ref)
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=8)
    return ap


def run(spec: CliSpec) -> None:
    """`main()` 的唯一实现；行为与切分前逐字节一致（只有文案来自 `spec`）。"""
    args = _parser(spec).parse_args()

    # 全局播种已退役（§P1-7）：训练序列只由 `PPORng.from_seed(args.seed)` 决定。
    ppo_rng = PPORng.from_seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    try:
        torch.set_flush_denormal(True)
    except (RuntimeError, AttributeError):
        pass
    device = torch.device(args.device)

    if args.init_from and not args.data:
        model = spec.build_net(args.init_from)
        spec.load_weights(model, args.init_from)  # 主干+头迁移；value 头保持随机
        spec.export_weights(model, args.out)
        log(f"[{spec.tag}] init RL weights ({spec.init_desc}) -> {args.out}")
        log(f"[{spec.tag}] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    if not (args.resume and args.data):
        # 显式 raise（G7：生产包的运行时校验不靠 `assert`，`-O` 下会被去掉）——
        # 类型与文案与切分前的 assert 逐字相同。
        raise AssertionError("--resume and --data required in update mode")
    model = spec.build_net(args.resume)
    spec.load_weights(model, args.resume)
    model.to(device)

    episodes = spec.load_episodes(args.data)
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    log(f"[{spec.tag}] total {spec.step_word}={total_steps}")

    chunks = chunk_episodes(episodes, args.mb, rng=ppo_rng.chunk)
    log(f"[{spec.tag}] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    # kickstarting 参考策略：显式 ref-weights（冻结快照）或当前权重（策略尚未被本 CLI 更新）。
    ref_model = None
    if args.kl_coef > 0:
        ref_src = args.ref_weights or args.resume
        ref_model = spec.build_net(ref_src)
        spec.load_weights(ref_model, ref_src)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
        log(f"[{spec.tag}] kickstarting: ref={ref_src} kl_coef={args.kl_coef}")
    agg = spec.update(
        model,
        opt,
        chunks,
        args.epochs,
        device,
        seed=args.seed,
        value_warmup_epochs=args.value_warmup_epochs,
        ref_model=ref_model,
        kl_coef=args.kl_coef,
        rng=ppo_rng,
    )

    model.to("cpu")
    spec.export_weights(model, args.out)
    bc = f"bc={agg['bc']:.4f} " if spec.with_bc else ""
    log(
        f"[{spec.tag}] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} {bc}"
        f"early_stopped={agg['early_stopped']} mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )
