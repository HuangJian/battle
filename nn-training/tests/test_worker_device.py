"""test_worker_device —— PPO worker 必须接受 `--device auto`（2026-09-15 Colab 事故）。

实测（push-first，Colab T4）：
  `worker_server 就绪` → `job accepted (HTTP 202)` → 紧接着
  `job ef... FAILED: Expected one of cpu, cuda, ipu, xpu, ... device type at start of
   device string: auto`

根因：push-first 的引导在 `notebook_runtime.resolve_device()` 解析设备**之前**就把
worker spawn 起来了（cell 与 `push_bootstrap.run_push_first` 都只做
`device_resolved or device` 兜底），于是字面量 `"auto"` 一路送到
`torch.device("auto")` —— 而 `run_job` 里的设备分派只有 `tpu/xla`、`cuda-dp` 两个分支，
`auto` 落进最后的 `else`。

`normalize_ppo_device` 是那条缺失的归一化：`auto` → 有 CUDA 用单卡 `cuda`、否则 `cpu`。
刻意**不**自动升 `cuda-dp`（DataParallel 改变梯度归约顺序，是实验臂开关，必须显式）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.worker import normalize_ppo_device


class TestAutoResolution:
    def test_auto_with_cuda_becomes_single_cuda(self) -> None:
        assert normalize_ppo_device("auto", cuda_available=True) == "cuda"

    def test_auto_without_cuda_becomes_cpu(self) -> None:
        assert normalize_ppo_device("auto", cuda_available=False) == "cpu"

    def test_auto_never_becomes_cuda_dp(self) -> None:
        """多卡必须显式要 —— 自动升 DP 会悄悄改变梯度归约顺序。"""
        assert normalize_ppo_device("auto", cuda_available=True) != "cuda-dp"

    def test_auto_is_case_insensitive(self) -> None:
        assert normalize_ppo_device("AUTO", cuda_available=False) == "cpu"
        assert normalize_ppo_device(" auto ", cuda_available=False) == "cpu"

    def test_auto_without_injection_does_not_raise(self) -> None:
        """不传 cuda_available 时自己去问 torch —— 无论有没有卡都不能抛。"""
        assert normalize_ppo_device("auto") in ("cpu", "cuda")


class TestPassthrough:
    def test_explicit_devices_pass_through_untouched(self) -> None:
        for d in ("cpu", "cuda", "cuda:0", "cuda-dp", "dp", "tpu", "xla", "mps"):
            assert normalize_ppo_device(d, cuda_available=True) == d

    def test_empty_and_none_fall_back_to_cpu(self) -> None:
        """空串/None 不是合法 torch 设备 —— 兜到 cpu，别把空串喂给 torch。"""
        assert normalize_ppo_device("", cuda_available=True) == "cpu"
        assert normalize_ppo_device(None, cuda_available=True) == "cpu"

    def test_whitespace_is_stripped(self) -> None:
        assert normalize_ppo_device(" cuda:1 ", cuda_available=True) == "cuda:1"


def test_run_job_normalizes_device_once_before_kind_fork() -> None:
    """调用点源码守卫：`run_job` 必须在 **kind 分叉之前** 归一化设备，且全会只此一处。

    两次真实事故的回归形态（`run_job` 太大、无法单测驱动，只能源码守）：
      ① 只把 PPO 的分派条件换成 `dev_str`，else 仍写 `torch.device(device)`
         ⇒ `auto` 照样喂进 torch（日志「兜底为 cuda」打了、job 仍炸 auto）；
      ② 归一化只写在 PPO 分支里 ⇒ BC 分叉（`device=device` → `_bc_device`）仍透传 `auto`。
    所以：一次、且在分叉前。
    """
    src = (ROOT / "remote" / "worker.py").read_text(encoding="utf-8")
    flat = " ".join(src.split())

    assert flat.count("normalize_ppo_device(device)") == 1, (
        "run_job 应只归一化一次；每条链各自归一化必然漏掉其中一条"
    )
    assert flat.index("device = normalize_ppo_device(device)") < flat.index('"kind"]) == "bc"'), (
        "设备归一化必须早于 BC/PPO 分叉，否则其中一条链会拿到未解析的 auto"
    )

    # `torch.device(...)` 的实参 —— 走 AST，不用子串：
    # 注释里为说明本次事故必须写出 `torch.device(device)` 这个字样，
    # 子串断言会把它当成真代码而误报（本仓已知坑）。
    #
    # 2026-09-24（S9）：这两个 `torch.device(...)` 调用点在**训练核**里（模型构建 / cuda-dp 包装），
    # 随 `run_job` 的 650-1076 行一起下沉到 `remote/train_core.py`；上面的「归一化一次且在
    # 分叉前」仍在作业壳（`worker.run_job`）——两半各查各的模块。
    core_src = (ROOT / "remote" / "train_core.py").read_text(encoding="utf-8")
    fn = next(
        n
        for n in ast.parse(core_src).body
        if isinstance(n, ast.FunctionDef) and n.name == "run_training_core"
    )
    dev_args: list[str] = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Call) and ast.unparse(n.func) == "torch.device" and n.args):
            continue
        a = n.args[0]
        # 字符串常量取**值**（ast.unparse 会渲染成单引号，不适合直接比字面量）
        dev_args.append(str(a.value) if isinstance(a, ast.Constant) else ast.unparse(a))
    assert dev_args == ["cuda", "dev_str"], (
        f"run_job 里 torch.device 的实参应只有字面 'cuda'（cuda-dp 分支）与归一化后的 "
        f"dev_str；实得 {dev_args} —— 出现 'device' 说明原始值又漏进去了"
    )
