"""tests/test_tpu_backend_guard.py —— `--device tpu` 必须真是 TPU 后端（2026-09-22 事故）。

用户报障（Kaggle TPU 实例 v5litepod-8，离线课程 x20-demo-mix）：

  「却使用了 CPU 来跑 PPO！之前跑在线课程，是能正常启用 TPU 的」
  —— Kaggle 面板 TPU 利用率近 0、CPU 满；PPO 单步 **8~9s**（本地 CPU 基准 ~4.7s/step、
     TPU 工作参考 ~44ms）。

本文件钉住两件事（都在 `ppo/common.py` + `remote/worker.py`，纯函数 + 源码守线）：

  ① **判据**：`tpu_backend_missing_reason()` 从**后端指纹**判「这不是 TPU」——
     `device_type != TPU` 或设备属性里没有 `coords`/`core_on_chip`（XLA 的 CPU 插件没有
     这两个键）。**刻意不用 `world_size()`**：torch_xla 源码里无复制时它恒为 1
     （同一次真机实测：world_size=1 而 global_device_count=8、device_type=TPU、
     2048² matmul 比 CPU 快 12×）——拿它当判据会既漏又误。
  ② **接线**：worker 的 tpu 分支必须把指纹与一次速度自检打进日志，并在后端不对时
     **拒跑**（`ProtocolError`）。在 CPU 上跑完整段「看起来完全正常、只慢两个数量级」，
     正是最该响的那类静默降级。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ppo.common import (
    _SPEED_PROBE,
    TPU_ATTR_KEYS,
    tpu_backend_missing_reason,
    xla_device_speed_probe,
    xla_fingerprint,
)

#: 真机取回来的两个指纹（PROBE1 @ Kaggle v5litepod-8 / XLA CPU 插件的形态）。
TPU_FP = {
    "device_type": "TPU",
    "global_device_count": 8,
    "addressable_device_count": 8,
    "replication_devices": 0,
    "world_size": 1,
    "attrs": "{'coords': [0, 0, 0], 'core_on_chip': 0, 'num_cores': 1}",
}
CPU_FP = {
    "device_type": "CPU",
    "global_device_count": 1,
    "addressable_device_count": 1,
    "replication_devices": None,
    "world_size": 1,
    "attrs": "{'device_type': 'CPU'}",
}


class TestReason:
    def test_real_tpu_fingerprint_passes(self) -> None:
        assert tpu_backend_missing_reason(TPU_FP) == ""

    def test_cpu_backend_is_rejected(self) -> None:
        why = tpu_backend_missing_reason(CPU_FP)
        assert "CPU" in why, "拒跑理由要说清后端是什么"

    def test_env_says_tpu_but_attrs_lack_tpu_marks_is_rejected(self) -> None:
        """最阴的一种：PJRT_DEVICE=TPU（device_type 说 TPU）而设备其实不是 TPU。

        这正是「日志看着没问题、算力却在 CPU 上」的形态——判据必须落在**设备属性**上，
        不能只看环境位。
        """
        fp = {**TPU_FP, "attrs": "{'device_type': 'CPU', 'num_cores': 1}"}
        assert "TPU 指纹" in tpu_backend_missing_reason(fp)

    def test_unreadable_fingerprint_does_not_block(self) -> None:
        """读不到（torch_xla 未装 / 属性不可读）时**不**判负：宁可放过，也不能把能跑的拦死。"""
        for fp in (
            {"device_type": None, "attrs": None},
            {"device_type": "TPU", "attrs": None},
            {"device_type": None, "attrs": "{'coords': [0, 0, 0]}"},
        ):
            assert tpu_backend_missing_reason(fp) == ""

    def test_world_size_alone_is_not_a_judgement(self) -> None:
        """world_size=1 不得被当作「不是 TPU」——真机 TPU 上它就是 1。"""
        assert tpu_backend_missing_reason({**TPU_FP, "world_size": 1}) == ""


class TestFingerprint:
    def test_missing_torch_xla_returns_nulls_and_never_raises(self) -> None:
        """诊断函数不能反过来把训练搞挂：任何一项读不到都记 None，不抛。"""
        fp = xla_fingerprint()
        assert set(fp) == {
            "device_type",
            "global_device_count",
            "addressable_device_count",
            "replication_devices",
            "world_size",
            "attrs",
        }
        # 本机（Windows dev box）没有 torch_xla ⇒ 形如全 None；有的话也必须是可读值
        assert fp["device_type"] is None or isinstance(fp["device_type"], str)

    def test_tpu_attr_keys_are_the_documented_fingerprint(self) -> None:
        assert TPU_ATTR_KEYS == ("coords", "core_on_chip")


class TestSpeedProbe:
    def test_probe_returns_seconds_and_is_cached_per_device(self) -> None:
        """同进程只测一次（它含一次 XLA 编译；按 job 跑会白付 300 次）。"""
        dev = f"__cache-probe-{id(self)}__"
        first = xla_device_speed_probe(dev, n=64)
        assert first is None or first > 0.0
        if first is not None:
            assert _SPEED_PROBE[dev] == first
            assert xla_device_speed_probe(dev, n=64) == first, "第二次必须走缓存"

    def test_probe_failure_is_none_not_raise(self) -> None:
        assert xla_device_speed_probe(object(), n=64) is None


class TestWorkerWiring:
    """源码守线：tpu 分支必须报指纹并拒跑（这条接线掉了，上面所有判据都是空转）。"""

    def setup_method(self) -> None:
        self.src = (ROOT / "remote" / "worker.py").read_text(encoding="utf-8")

    def test_tpu_branch_logs_fingerprint_and_speed(self) -> None:
        assert "xla_fingerprint(device_t)" in self.src
        assert "xla_device_speed_probe(device_t)" in self.src
        assert "global_device_count" in self.src

    def test_tpu_branch_refuses_a_non_tpu_backend(self) -> None:
        assert "tpu_backend_missing_reason(_fp)" in self.src
        # 拒跑正文里要有修法（PJRT_DEVICE 必须在任何 torch_xla 初始化之前设好）
        assert "PJRT_DEVICE=TPU" in self.src

    def test_tpu_branch_enables_the_persistent_compile_cache(self) -> None:
        """持久化编译缓存必须在 `xla_device()`/指纹/速度自检**之前**开启（torch_xla 要求
        它先于任何计算）；接线掉了，缓存被挤出后的重编就白付（§134 ragged tail ~14s/轮）。"""
        assert "xla_enable_compile_cache(work_dir / 'xla-compile-cache')" in self.src
        i_cache = self.src.index("xla_enable_compile_cache(work_dir")
        i_dev = self.src.index("device_t = xla_device()", i_cache - 400)
        assert i_cache < i_dev, "缓存初始化必须先于第一次碰设备"
