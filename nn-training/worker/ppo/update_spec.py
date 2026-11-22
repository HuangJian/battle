"""`ppo_update` 的参数对象（torch / Paddle 两侧共用一份形状词表）。

## 为什么要有它

`ppo_update` 是**四个后端**（torch / Paddle / goal / intent）共用的同一个概念，torch 侧
20 个参数、Paddle 侧 21 个：前 5 个位置语义（model / opt / chunks / epochs / device），
其后 16 个全是带缺省的关键字。关键字拼错 Python 不报错 —— 只是**静默落回缺省**：

* `kl_coef` 少一位小数 ⇒ 这一轮没有 KL 惩罚，日志里只有「kl 偏大」；
* `target_kl` / `steps_out` 写错 = 「提前停」与「步数回传」静默失效；
* `lazy=True` 拼成 `lazy=1` 没差别，但拼成 `lazzy` 就是**整轮全量上卡**（显存翻倍）。

参数对象把「名字错了」变成 `TypeError`，并把「这个后端到底认哪些旋钮」写进类型：
`TorchUpdateSpec` 有 `target_kl`，`PaddleUpdateSpec` 有 `steps_out` / `log` —— 各自没有
对方独有的字段，所以**不存在**「传了却被忽略」的静默承诺。

## 为什么不是一个 spec

两个后端的能力面真的不同（torch 没有 `steps_out`/`log`，Paddle 没有 `target_kl`）。合成一个
超集 spec 就得在入口里判「你传了这个但我不管」，那等于把静默失效从签名搬进入口。共用的是
`BaseUpdateSpec`（19 个真正同名的旋钮），差异留在两个子类里。

## 为什么端口 `update(...)` 仍然是关键字

`trainer/stream.py` 用一个调用面驱动四个后端（`c.backend.update(model, opt, chs, epochs,
device, **update_kwargs)`），而 goal / intent 的 `update_kwargs` 与本模块不同（`seed` /
`value_warmup_epochs`）。引擎侧因此保留一个 5 参数 + `**kwargs` 的 `update` 适配器：前 5 个
是**固定的位置语义**（端口契约），其余原样进 spec —— 未知键在那里立刻 `TypeError`，不再是
静默缺省。直接调用 `ppo_update` 的地方（本机训练核、parity 工具、测试）则一律传 spec。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from common.log_bundle import LogBundle
from worker.ppo.demo import DemoBank
from worker.ppo.rng import PPORng


def _noop_log(_msg: str) -> None:
    """Paddle 侧的缺省日志出口（与旧形参的 `lambda _msg: None` 逐字同义）。"""


@dataclass(frozen=True, kw_only=True)
class BaseUpdateSpec:
    """torch / Paddle 两侧**同名同义**的 19 个旋钮。"""

    #: 网络与优化器（调用方持有，引擎只读不改身份）。
    model: object
    opt: object
    #: minibatch 列表，或 `lazy=True` 时的 `LazyChunks`（两个后端都当不透明句柄用）。
    chunks: object
    epochs: int
    #: `torch.device` / 字符串；None 只在 Paddle 侧合法（进程级设备已设）。
    device: object | None = None
    #: 非空 ⇒ 每 epoch 落盘 checkpoint 并支持断点续跑（语义见 `engine.ppo_update` docstring）。
    ckpt_path: str | None = None
    #: 每个 epoch 末尾的同步回调 `(ep_done, model)`（双缓冲预采的触发点）。
    on_epoch_done: Callable[..., None] | None = None
    #: 对采样策略（`lp_old`）的 KL 惩罚系数；0.0 = 关（缺省路径数学逐字节不变）。
    kl_coef: float = 0.0
    #: BC 冻结快照 + kickstart KL 系数（两者就绪才生效）。
    ref_model: object | None = None
    kickstart_kl: float = 0.0
    #: 熵正则系数；None = 模块常量 `ENT_COEF`（0.0 是合法值，不能 `or` 兜底）。
    ent_coef: float | None = None
    #: demo 混 batch：语料银行 + 系数 + 每 minibatch 抽几个。
    demo_bank: DemoBank | None = None
    demo_bc_coef: float = 0.0
    demo_per_mb: int = 0
    #: 进度出口（`LogBundle`）与前缀。
    progress: LogBundle | None = None
    progress_head: str = "[ppo] PPO"
    #: value 门控牵引系数（0.0 = 关）。
    value_anchor: float = 0.0
    #: 三条显式随机流；None = 种子 0 派生（只为冒烟/工具）。
    rng: PPORng | None = None
    #: True ⇒ `chunks` 逐 chunk 物化，不建全量上卡副本。
    lazy: bool = False


@dataclass(frozen=True, kw_only=True)
class TorchUpdateSpec(BaseUpdateSpec):
    """torch 侧（`worker.ppo.engine.ppo_update`）。"""

    #: >0 ⇒ 某 epoch 的平均 approx KL 超过即停（0 = 关，步数与数学逐字节不变）。
    target_kl: float = 0.0


@dataclass(frozen=True, kw_only=True)
class PaddleUpdateSpec(BaseUpdateSpec):
    """Paddle 侧（`worker.ppo.engine_paddle.ppo_update`）。"""

    #: 引擎**自己数出来的**步数写回这里（调用方不许用 `epochs × len(chunks)` 重算）。
    steps_out: dict[str, int] | None = None
    #: 侧路日志（Paddle 侧没有 `LogBundle`）。
    log: Callable[[str], None] = _noop_log
