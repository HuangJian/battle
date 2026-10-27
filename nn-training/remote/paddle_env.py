"""Paddle 后端的**运行时环境适配**（2026-10-27）：线程数 + 设备归一。

为什么单独一个模块：`remote/train_core.py` 的**有效 LOC 已到 999**（预算 1000，
`tests/test_python_loc_budget.py`），而 Paddle 适配这几行只能塞在那里 —— 再 +1 就红。
拆出来的判据是「独立所有者 + 独立触发条件」：本模块只管**进程级运行环境**（线程池 / 设备
字符串），与「训练核怎么跑一轮」无关，也不碰任何训练状态。
"""

from __future__ import annotations

import os
from typing import Any

from common.protocol import ProtocolError

#: Paddle 侧的线程旋钮走**环境变量**（Paddle 3.3 没有 `set_num_threads`）。
#: 两个都设：`OMP_NUM_THREADS` 管 OpenMP 并行区，`MKL_NUM_THREADS` 管 oneDNN/MKL 的 GEMM ——
#: 只设一个等于只管一半线程池。
THREAD_ENVS: tuple[str, ...] = ("OMP_NUM_THREADS", "MKL_NUM_THREADS")


def apply_thread_env(n: int, *, env: dict[str, str] | None = None) -> dict[str, str]:
    """把 `--threads N` 翻译成 Paddle 能听懂的环境变量，返回**实际写入的键值**（空 = 没写）。

    ★ 必须在 `import paddle` / `import numpy` **之前**调用 —— 这两个库在 import 期就把线程池
    建好了，之后再改环境变量对已建好的池无效（Paddle 也没有 `set_num_threads` 可以补）。

    `setdefault` 语义：**已有的不覆盖**。launcher / 平台（AI Studio、K8s 的 limits）可能已经
    按容器配额设过 `OMP_NUM_THREADS`，那才是真权威 —— 我们只在它缺席时补一个缺省。

    `n <= 0` ⇒ 不动（与 torch 侧 `--threads 0` = 「保留 launcher 缺省」同义）。
    """
    if int(n) <= 0:
        return {}
    e = os.environ if env is None else env
    want = str(int(n))
    wrote: dict[str, str] = {}
    for k in THREAD_ENVS:
        if k not in e:
            e[k] = want
            wrote[k] = want
    return wrote


def apply(n: int, prep: Any) -> None:
    """`apply_thread_env` + 把结果写进装载日志（`prep.add`）。给 train_core 的单行调用面。"""
    wrote = apply_thread_env(n)
    if wrote:
        prep.add("线程", " ".join(f"{k}={v}" for k, v in wrote.items()))


#: torch 专属的实验臂 —— 在 Paddle 侧**响亮拒收**（静默退回 CPU 会看起来一切正常、只是慢
#: 两个数量级，比直接失败危险得多）。
TORCH_ONLY_DEVICES: tuple[str, ...] = ("tpu", "xla", "cuda-dp", "dp", "mps")


def check_device(jid: str, device: Any) -> str:
    """归一设备字符串；torch 专属臂抛 `ProtocolError`。返回给 `paddle.set_device` 用的串。"""
    dev = str(device)
    if dev in TORCH_ONLY_DEVICES:
        raise ProtocolError(
            f"job {jid}: paddle 后端不支持 --device {dev!r}"
            "（XLA / DataParallel / Apple MPS 是 torch 专属实验臂）—— 用 cpu / cuda"
        )
    return dev
