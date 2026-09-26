"""tests/test_xla_step_diag.py —— XLA 步耗诊断可读、可判、接线在（2026-09-22）。

事故上下文（`docs/nn/tpu-perf.md` 与 test_tpu_backend_guard.py 同源）：Kaggle v5e-8 上
离线课程 PPO 单步 8~10s，而真机探针实测「一次新编译 7.7s、编译命中后单步执行 ~20-90ms」。
结论指向「每个 chunk 迭代都在重新编译」，但这必须由日志定案，不能靠墙钟猜——于是
`ppo/common.py` 加了快照/差分/格式化三个纯函数，`ppo/engine.py` 每 chunk 迭代打一行。

本文件钉两件事：

  ① **解析正确**：`xla_metrics_snapshot()` 从 XLA 的文本报告里取到正确的量与单位
     （`07s703ms868.692us` → 秒）。测试用的是**真机取回的原文片段**（见 REPORT），
     不是编造的格式——格式漂了，日志就会静默变成 0，那比没有诊断更坏。
  ② **接线在**：engine 的 update 循环里的诊断采样与 `PPO_XLA_DIAG` 开关必须存在
     （接线掉了，上面所有解析都是空转）。
  ③ **打了几行**（2026-09-24 日志节食）：逐窗口**只累计、不打行**，收尾打**一行**
     （原来每 16 步一行 ≈ 23 行/轮，云端几小时把控制台日志面板拖死）。判决要素在
     `TestEngineWiring::test_diag_is_one_line_at_the_end` 里逐项钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 这五个都是纯文本/字典解析，住在免 torch 的 ppo.np_core（2026-09-26 拆分）。
# 本文件的 TestEngineWiring 里对 ppo.common 的 demo_index / _XLA_CACHE_STATE 仍是延迟 import，
# 不影响收集期免 torch。
from ppo.np_core import (
    _parse_xla_duration,
    xla_delta_str,
    xla_enable_compile_cache,
    xla_metrics_delta,
    xla_metrics_snapshot,
)

#: 真机（Kaggle v5litepod-8，torch_xla 2.8.0）`metrics.metrics_report()` 的原文片段。
#: 保留原样的缩进与单位混排（`07s703ms868.692us` / `056ms513.145us` / `001.490us`）。
REPORT = """
Metric: DeviceLockWait
  TotalSamples: 42
  Accumulator: 074.840us
  ValueRate: 318.111us / second
  Percentiles: 1%=000.140us; 5%=000.140us; 50%=001.840us
Metric: LazyTracing
  TotalSamples: 2774
  Accumulator: 131ms794.263us
Metric: CompileTime
  TotalSamples: 1
  Accumulator: 07s703ms868.692us
Metric: ExecuteTime
  TotalSamples: 4
  Accumulator: 132ms617.599us
Metric: TransferToDeviceTime
  TotalSamples: 20
  Accumulator: 637.640us
Metric: TransferFromDeviceTime
  TotalSamples: 2
  Accumulator: 028ms305.457us
Counter: UncachedCompile
  Value: 1
Counter: CachedCompile
  Value: 3
Counter: ExecuteComputation
  Value: 4
Counter: MarkStep
  Value: 2
"""


class TestDurationParsing:
    def test_mixed_units_are_summed(self) -> None:
        assert abs(_parse_xla_duration("07s703ms868.692us") - 7.703868692) < 1e-9

    def test_sub_second_units(self) -> None:
        assert abs(_parse_xla_duration("132ms617.599us") - 0.132617599) < 1e-9
        assert abs(_parse_xla_duration("637.640us") - 0.000637640) < 1e-9
        assert abs(_parse_xla_duration("001.490us") - 0.000001490) < 1e-9

    def test_empty_is_zero_not_raise(self) -> None:
        assert _parse_xla_duration("") == 0.0


class TestSnapshot:
    def test_real_report_parses_to_seconds_and_counts(self) -> None:
        """本机没有 torch_xla，故把报告文本注入到假模块上——解析逻辑与真机同一份代码。"""
        import types

        fake = types.ModuleType("torch_xla.debug.metrics")
        fake.metrics_report = lambda: REPORT  # type: ignore[attr-defined]
        pkg = types.ModuleType("torch_xla")
        dbg = types.ModuleType("torch_xla.debug")
        saved = {
            k: sys.modules.get(k)
            for k in ("torch_xla", "torch_xla.debug", "torch_xla.debug.metrics")
        }
        sys.modules["torch_xla"] = pkg
        sys.modules["torch_xla.debug"] = dbg
        sys.modules["torch_xla.debug.metrics"] = fake
        try:
            snap = xla_metrics_snapshot()
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        assert abs(snap["t.CompileTime"] - 7.703868692) < 1e-9
        assert abs(snap["t.ExecuteTime"] - 0.132617599) < 1e-9
        assert abs(snap["t.LazyTracing"] - 0.131794263) < 1e-9
        assert snap["c.UncachedCompile"] == 1.0
        assert snap["c.CachedCompile"] == 3.0
        assert snap["c.ExecuteComputation"] == 4.0
        assert snap["c.MarkStep"] == 2.0
        # 不关心的指标不进快照；Percentiles/TotalSamples 里的数字也不得混进来
        # （第一版 `(.*)$` + re.S 会把报告剩下所有指标的耗时错加进 CompileTime）。
        assert "t.DeviceLockWait" not in snap
        assert set(snap) == {
            "t.CompileTime",
            "t.ExecuteTime",
            "t.LazyTracing",
            "t.TransferToDeviceTime",
            "t.TransferFromDeviceTime",
            "c.UncachedCompile",
            "c.CachedCompile",
            "c.ExecuteComputation",
            "c.MarkStep",
        }

    def test_without_torch_xla_it_is_empty_not_raise(self) -> None:
        """非 XLA 机器（本机）必须返回 {}——调用点据此整体跳过，零开销。"""
        assert xla_metrics_snapshot() == {}


class TestDelta:
    def test_delta_is_step_cost_and_absent_keys_are_ignored(self) -> None:
        prev = {"t.CompileTime": 7.70, "c.UncachedCompile": 1.0}
        cur = {"t.CompileTime": 15.40, "c.UncachedCompile": 2.0, "t.ExecuteTime": 0.09}
        d = xla_metrics_delta(prev, cur)
        assert abs(d["t.CompileTime"] - 7.70) < 1e-9
        assert d["c.UncachedCompile"] == 1.0
        assert abs(d["t.ExecuteTime"] - 0.09) < 1e-9, "快照里新出现的键按 0 起算"

    def test_delta_str_names_every_quantity_the_verdict_needs(self) -> None:
        line = xla_delta_str(
            {
                "t.CompileTime": 7.70,
                "t.ExecuteTime": 0.09,
                "t.LazyTracing": 0.01,
                "c.UncachedCompile": 1.0,
                "c.CachedCompile": 3.0,
                "c.ExecuteComputation": 4.0,
            }
        )
        for token in ("编译=7.70s", "新=1", "命中=3", "执行=0.090s/4次", "追踪=0.010s"):
            assert token in line, f"判读所需的量缺失：{token}"


class TestPersistentCompileCache:
    """持久化编译缓存：把「缓存被挤出 ⇒ 重编」换成「读盘同一份程序」，不动任何数值。

    真机依据（§134）：一轮只用一次的 ragged 形状每轮多付 ~14s；但缓存容量旋钮在 XLA/Libtpu
    的公开 flag 表里**不存在**（只有 GPU 侧的 command-buffer cache），所以走「落盘」这条路。
    """

    def test_disabled_is_a_noop(self) -> None:
        assert "已停用" in xla_enable_compile_cache("/nonexistent/ignored", enabled=False)

    def test_without_torch_xla_it_reports_and_never_raises(self) -> None:
        """本机（Windows dev box）没有 torch_xla ⇒ 报「不可用」，绝不抛。"""
        assert "不可用" in xla_enable_compile_cache("/nonexistent/ignored")

    def test_initializes_once_and_is_idempotent(self, tmp_path) -> None:
        """必须在任何计算前调一次；同进程再调不得重复触发（否则 torch_xla 会抛）。"""
        import sys
        import types

        from ppo.common import _XLA_CACHE_STATE

        calls: list[str] = []
        fake_rt = types.ModuleType("torch_xla.runtime")
        fake_rt.initialize_cache = lambda p: calls.append(str(p))  # type: ignore[attr-defined]
        saved = {k: sys.modules.get(k) for k in ("torch_xla", "torch_xla.runtime")}
        sys.modules["torch_xla"] = types.ModuleType("torch_xla")
        sys.modules["torch_xla.runtime"] = fake_rt
        _XLA_CACHE_STATE.clear()
        try:
            cache_dir = str(tmp_path / "xla-cache")
            first = xla_enable_compile_cache(cache_dir)
            second = xla_enable_compile_cache(str(tmp_path / "other"))
            assert Path(cache_dir).is_dir(), "目录必须被创建（否则 initialize_cache 会抛）"
        finally:
            for key, val in saved.items():
                if val is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = val
            _XLA_CACHE_STATE.clear()
        assert calls == [cache_dir], "同进程第二次不得再调 initialize_cache"
        assert "已开启" in first
        assert "幂等" in second

    def test_missing_api_reports_unavailable(self) -> None:
        """老版 torch_xla 没有该 API ⇒ 响亮记一行「不可用」，不得抛。"""
        import sys
        import types

        from ppo.common import _XLA_CACHE_STATE

        saved = {k: sys.modules.get(k) for k in ("torch_xla", "torch_xla.runtime")}
        sys.modules["torch_xla"] = types.ModuleType("torch_xla")
        sys.modules["torch_xla.runtime"] = types.ModuleType("torch_xla.runtime")
        _XLA_CACHE_STATE.clear()
        try:
            msg = xla_enable_compile_cache("/nonexistent/ignored")
        finally:
            for key, val in saved.items():
                if val is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = val
            _XLA_CACHE_STATE.clear()
        assert "不可用" in msg and "initialize_cache" in msg


class TestDemoIndexBuffer:
    """demo 混 batch 的索引必须走**复用的设备张量**（否则 XLA 每步重编译）。

    真机定案（2026-09-22）：把 host numpy 索引直接交给高级索引 ⇒ 同一批 B/flags 下连续两步
    各 `新=2`（新编译）；复用同一设备缓冲 / 先 mark 物化 ⇒ `新=0`。离线课程 8~10s/步就是
    这个（单步 2 次新编译 ≈11s；编译命中的那一步 0.31s）。
    """

    def test_reuses_the_same_tensor_and_keeps_values(self) -> None:
        import numpy as np
        import torch

        from ppo.common import demo_index

        buf = torch.zeros(3, dtype=torch.int64)
        a = demo_index(buf, np.array([2, 0, 1], dtype=np.int64))
        assert a is buf, "返回的必须是那张缓冲本身（图签名靠它恒定）"
        assert a.tolist() == [2, 0, 1]
        b = demo_index(buf, np.array([1, 1, 0], dtype=np.int64))
        assert b is buf, "第二次调用也不得新建张量"
        assert b.tolist() == [1, 1, 0]

    def test_selection_is_identical_to_plain_numpy_indexing(self) -> None:
        """数值逐位相同：换索引来路不得抽到别的样本。"""
        import numpy as np
        import torch

        from ppo.common import demo_index

        bank = torch.arange(15, dtype=torch.float32).reshape(5, 3)
        buf = torch.zeros(4, dtype=torch.int64)
        for _ in range(5):
            idx = np.random.randint(0, 5, size=4).astype(np.int64)
            assert torch.equal(bank[demo_index(buf, idx)], bank[idx])

    def test_none_buffer_returns_the_numpy_index_unchanged(self) -> None:
        """非 demo 路径（buf=None）行为与接线前逐字节一致。"""
        import numpy as np

        from ppo.common import demo_index

        idx = np.array([3, 1], dtype=np.int64)
        assert demo_index(None, idx) is idx


class TestEngineWiring:
    """源码守线：诊断行必须真的接在 update 循环里，且可关。"""

    def setup_method(self) -> None:
        self.src = (ROOT / "ppo" / "engine.py").read_text(encoding="utf-8")

    def test_engine_samples_diag_and_has_the_switch(self) -> None:
        assert "PPO_XLA_DIAG" in self.src
        assert "xla_metrics_delta(diag_prev, _snap)" in self.src
        assert "PPO_XLA_DIAG_FIRST" in self.src and "PPO_XLA_DIAG_EVERY" in self.src

    def test_diag_is_one_line_at_the_end(self) -> None:
        """日志节食的钉子：**不许**回到逐窗口的行，且判决要素一个都不能丢。

        为什么是源码断言：这是「行数」性质（跑一次算不出来），而它正是当初把浏览器卡死的
        那个变量；哪天有人为了调试把它改回逐窗口打印，这条必须先红。
        """
        assert "[ppo] diag s=" not in self.src, "逐窗口一行 = 日志节食被回退（~23 行/轮）"
        for keep in (
            "diag_worst",  # 最慢窗口（判决的第一现场）
            "diag_compile_dom",  # 编译主导窗口计数（说人话：墙钟买的是编译）
            "diag_reset",  # XLA metrics 被重置的窗口计数（假读数护栏）
            "图签名",  # 形状/分支有没有变
            "累计编译",  # 编译税的总额
            "到 epoch",  # 逐 epoch 的汇总（系列不能丢）
        ):
            assert keep in self.src, keep

    def test_diag_is_taken_after_mark_step(self) -> None:
        """读数必须在 mark_step 之后（编译/执行已被 drain），否则那些量恒为 0。"""
        i_mark = self.src.index("xla_mark_step(device)\n            now = time.time()")
        i_snap = self.src.index("_snap = xla_metrics_snapshot()", i_mark)
        assert i_snap > i_mark

    def test_tensors_are_materialized_before_the_loop(self) -> None:
        """chunk/demo 张量必须在进循环前物化：否则每个 chunk 的**首次使用**会把 host→device
        转移内联进那张图 ⇒ 各付一次全图编译（真机 epoch1 累计 41 次新编译、epoch2 后降到
        0.9~2.4s/步——变体集被填满的形态）。"""
        i_conv = self.src.index("tensored = tensored_chunks(chunks, device)")
        i_loop = self.src.index("for ep in range(start_epoch, epochs):")
        i_pre = self.src.rindex("xla_mark_step(device)", i_conv, i_loop)
        assert i_conv < i_pre < i_loop, "预物化必须夹在转换之后、循环之前"
        assert "is_xla(device)" in self.src

    def test_demo_buffer_has_a_self_describing_log_line(self) -> None:
        """日志里没这行 ⇒ 跑的包里没有该修复（别拿旧包结果判修法）。"""
        assert "demo 索引复用缓冲已启用" in self.src

    def test_demo_index_goes_through_the_reused_buffer(self) -> None:
        """回归钉：不得再把 host numpy 索引直接交给高级索引（那是每步重编译的源头）。"""
        assert "demo_index(demo_idx_dev, _didx)" in self.src
        assert "demo_t[\"obs\"][_didx]" not in self.src
        assert "demo_idx_dev = torch.zeros(" in self.src

    def test_diag_baseline_only_advances_inside_the_sampling_branch(self) -> None:
        """未取样的步不推进基线：否则两次采样之间的编译会被静默丢掉。"""
        assert self.src.count("diag_prev = _snap") == 1, "基线只该在取样分支内推进"
        i_blk = self.src.index("if _done <= diag_first or _done % diag_every == 0")
        assert self.src.index("diag_prev = _snap", i_blk) > i_blk

