"""`run_job` 的参数对象（17 个入参 → 一份 frozen + kw_only spec）。

## 为什么要有它

`run_job` 是 worker 的**作业执行入口**：3 个身份位（`base_url` / `token` / `job`）+ 14 个按名
旋钮。它被四条腿调用（hub 监管轮 `job_round` · 半离线合成轮 `plan_run` · 本机 `worker_server`
· 独立 `run_loop`），而 `job_round` 只见 `run_job_fn` 这个**注入点**（它不能 import
`remote.worker`：那会成环，也会把 torch 链拉进顶层）。于是同一组 13 个关键字实参在注入面
与宿主之间手抄两遍 —— 关键字拼错 Python 不报错，只是**静默落回缺省**：

* `device="cpu"` 落错 = 云机拿 GPU 却按 CPU 跑（慢 10×，日志里只有「PPO 慢了」）；
* `epoch_mult` 落成 `1.0` = 训练侧放大悄悄失效（用户口径 `--epoch-mult` 不生效）；
* `preloaded` 落丢 = 全离线节点回头向空 `base_url` 发 GET（`unknown url type`）；
* `should_cancel` / `on_ppo_start` 落丢 = 取消帧与 `computing_at` 打点双双静默失效
  （前者白烧卡，后者让掉队阈值永远不启动）。

## 为什么单独成文件

`remote/job_round.py` 顶层零 torch、也**不许** import `remote.worker`（成环）——它要构造请求，
就必须有一个谁都能 import 的轻模块。本文件的依赖面只有 stdlib + `common.log_bundle`
（拓扑秩 **L1**，已登记 `tests/helpers/remote_dag.py`）。

字段分档：身份（`base_url` / `token` / `job`）· 落盘（`work_dir` / 两个 code 缓存目录）·
执行旋钮（`device` / `torch_threads` / `backend` / `epoch_mult` / `echo` / `lease_token`）·
随包字节（`preloaded`）· 观测与调度注入点（`prep` / `should_cancel` / `on_ppo_start` / `log`）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.log_bundle import LogBundle


@dataclass(frozen=True, kw_only=True)
class RunJobSpec:
    """一条作业的全部输入（`remote.worker.run_job` 只收它）。"""

    #: hub 地址与 token；半离线合成轮两者都是空串（job 走随包字节）。
    base_url: str
    token: str
    #: `{"job_id": str, "manifest": dict}`（hub 的作业面原样）。
    job: dict
    #: 本 job 的落盘根（payload / code / blob / 产物都在它下面）。
    work_dir: Path
    #: `cpu` / `cuda` / `cuda-dp` / `auto`；"auto" 由入口在 kind 分叉前归一化。
    device: str = "cpu"
    #: torch 线程数（0 = 不设）。
    torch_threads: int = 0
    #: 本节点用哪个框架跑 PPO（`""`/`auto` = 看本机，torch 优先）。
    backend: str = ""
    #: PPO 每轮更新遍数 × N（1.0 = manifest 原样）。
    epoch_mult: float = 1.0
    #: 冒烟：下载/校验全走，但不拉 torch 不跑 PPO（init 权重原样回传）。
    echo: bool = False
    #: push 模式随 body 上传的字节（`payload_zip` / `code_zip` / `ts_code_zip` / `blobs`）。
    preloaded: dict | None = None
    code_cache_dir: Path | None = None
    ts_code_cache_dir: Path | None = None
    #: 租约 token（备份副本为空串）。
    lease_token: str = ""
    #: 日志节食 bundle（入口 / 启动 / 装载读数攒进它，装载完成时打一行）。
    prep: LogBundle | None = None
    #: 每 **epoch 边界** 问一次「结果是不是已经 landed」——是则抛 `JobCancelledError`。
    should_cancel: Callable[[], bool] | None = None
    #: PPO 真正启动前一刻打点（hub 的 `computing_at` 时基）。
    on_ppo_start: Callable[[], None] | None = None
    #: 日志出口；None = 宿主模块自己的 `_log`（延迟解析，patch 仍生效）。
    log: Callable[[str], None] | None = None
