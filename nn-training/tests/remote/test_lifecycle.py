"""test_lifecycle.py — worker 生命周期上发腿（`remote/lifecycle.py`，2026-10-31 事故的热修）。

事故现场：kaggle 云机在 `09:51:42` 领走 job 之后 18 秒**整机被收走**——云机日志里一个字都
没有（不是 traceback，是连收尸的机会都没有），本机这边也看不到（notebook 日志是浏览器抓
cell 输出上报的，人不在电脑前就断）。本文件钉住补上的那一句「云机自己说的最后一句」：

  ① **形状**：`component="worker-lifecycle"`、`reason` 以「worker 启动 / 读点 / 收线 / 收到
     SIGxxx」开头 —— 控制台按 `reason` 折叠，前缀漂了就等于换了一行；
  ② **无 hub 不发**：没有地址/token 时**零请求**（本地预演、单测、离线无 hub 的场景本来就
     不该有这条腿），且不抛；
  ③ **收线带原因**：`why()` 记的原因必须出现在 `reason` 里（「有序收工」与「被 SIGKILL」
     的分界就是这三行日志）；
  ④ **读点节流**：`reading_sec` 到点才发一次（注入假钟，零墙钟等待），`0` = 关；
  ⑤ **信号**：装上后 `SIGTERM/SIGINT` 指向本对象；收到 SIGINT ⇒ 交回 Python 默认处置
     （`KeyboardInterrupt`，既有 finally 照跑），且**先上报后死**；`close()` 负责还原。
"""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import lifecycle as LC
from remote.lifecycle import (
    LIFECYCLE_COMPONENT,
    Lifecycle,
    LifecycleSpec,
    readings,
    readings_line,
)


class _Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """拦截两条出口（同步 `post_error` / 异步 `report_error`），不让用例碰真网络。"""
    box: list[dict[str, Any]] = []

    def _post(base: str, token: str, payload: dict[str, Any], **_: Any) -> bool:
        box.append({**payload, "block": True})
        return True

    def _report(spec: LC.ErrorReportSpec) -> None:
        box.append(
            {
                **LC.error_payload(
                    worker=spec.worker_id,
                    reason=spec.reason,
                    component=spec.component,
                    detail=spec.detail,
                    course=spec.course,
                    job_id=spec.job_id,
                    it=spec.it,
                    severity=spec.severity,
                ),
                "block": False,
            }
        )

    monkeypatch.setattr(LC, "post_error", _post)
    monkeypatch.setattr(LC, "report_error", _report)
    return box


def _life(tmp_path: Path, clock: _Clock, **kw: Any) -> Lifecycle:
    spec = LifecycleSpec(
        base_url="http://hub.invalid:8787",
        token="tok",
        worker_id="kaggle-c",
        work_dir=tmp_path,
        device="cuda:0",
        now_fn=clock,
    )
    return Lifecycle(replace(spec, **kw))


def test_readings_are_structured(tmp_path: Path) -> None:
    """① 读数（结构化）：磁盘条目必有、device 原样透传；显存在 torch 未加载时为空（不编数）。"""
    r = readings(tmp_path, "cuda:0")
    assert r["device"] == "cuda:0"
    assert r["disk"] and r["disk"][0]["total_gb"] > 0
    assert isinstance(r["gpu"], list)
    assert readings_line(tmp_path, "cuda:0").endswith("device=cuda:0")


def test_no_hub_means_no_request(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """② 没有 hub 地址/ token ⇒ 一条都不发（这是「旧调用方零影响」的判据）。"""
    lc = Lifecycle(LifecycleSpec(base_url="", token="", worker_id="kaggle-c", work_dir=tmp_path))
    lc.start()
    lc.tick()
    lc.close(done=3)
    assert sent == []


def test_start_and_close_are_blocking_and_named(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """③ 启动/收线走**同步**档（进程马上要没了，异步线程活不到发完）。"""
    lc = _life(tmp_path, _Clock())
    lc.start()
    lc.why("空闲超时 >28800s")
    lc.close(done=5)

    assert [e["block"] for e in sent] == [True, True]
    assert [e["component"] for e in sent] == [LIFECYCLE_COMPONENT, LIFECYCLE_COMPONENT]
    assert sent[0]["reason"].startswith("worker 启动")
    assert sent[1]["reason"] == "worker 收线（空闲超时 >28800s）"
    assert sent[1]["detail"].endswith("done=5")
    assert sent[0]["worker"] == "kaggle-c"


def test_tick_is_throttled(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """④ 读点按 `reading_sec` 节流（未到点零请求，到点一条，且走异步档）。"""
    clock = _Clock()
    lc = _life(tmp_path, clock, reading_sec=900.0)
    lc.start()
    sent.clear()

    lc.tick()  # 距 start 0s
    clock.t = clock.t + 899
    lc.tick()
    assert sent == []

    clock.t = clock.t + 1
    lc.tick()
    assert len(sent) == 1
    assert sent[0]["reason"].startswith("读点")
    assert sent[0]["block"] is False  # 异步：绝不占关键路径

    lc.tick()  # 同一分钟内再调不重复发
    assert len(sent) == 1


def test_tick_disabled_when_zero(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """`reading_sec=0` = 关读点（噪声逃生口）。"""
    clock = _Clock()
    lc = _life(tmp_path, clock, reading_sec=0.0)
    lc.start()
    sent.clear()
    clock.t += 10_000
    lc.tick()
    assert sent == []


def test_signals_installed_then_restored(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """⑤ 装信号 / 还原（观测腿不得改变进程退出后的信号语义）。"""
    if not hasattr(signal, "SIGTERM"):
        pytest.skip("平台无 SIGTERM")
    before = signal.getsignal(signal.SIGTERM)
    lc = _life(tmp_path, _Clock())
    lc.start()
    handler = lc._on_signal
    assert signal.getsignal(signal.SIGTERM) == handler  # bound method 恒等
    lc.close(done=0)
    assert signal.getsignal(signal.SIGTERM) == before


class _SignalShim:
    """把 `remote.lifecycle.signal` 换成只记账的壳（**只 patch 被测模块**，不污染全局 signal）。"""

    Signals = signal.Signals
    SIG_DFL = signal.SIG_DFL
    SIGTERM = signal.SIGTERM
    SIGINT = signal.SIGINT

    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self.calls = calls

    def signal(self, sig: int, handler: Any) -> None:
        self.calls.append(("signal", (sig, handler)))

    @staticmethod
    def default_int_handler(sig: int, frame: Any) -> None:
        # 与真实现同义：抛 KeyboardInterrupt（用例要的正是这条语义）
        raise KeyboardInterrupt


def test_sigterm_reports_before_dying(
    tmp_path: Path, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGTERM：**先上报、后死**（而不是先死、什么都没留下）。"""
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(LC, "signal", _SignalShim(calls))
    monkeypatch.setattr(
        LC,
        "os",
        SimpleNamespace(
            getpid=lambda: 0, kill=lambda pid, s: calls.append(("kill", s)), stat=os.stat
        ),
    )

    lc = _life(tmp_path, _Clock())
    lc._on_signal(signal.SIGTERM, None)

    assert sent and sent[0]["severity"] == "err"
    assert sent[0]["reason"] == "worker 收到 SIGTERM（开始收工）"
    assert ("kill", signal.SIGTERM) in calls  # 上报之后才自投一发（rc=143 语义不变）


def test_sigint_defers_to_python_default(
    tmp_path: Path, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGINT：上报后交回默认处置 ⇒ `KeyboardInterrupt`（既有 finally 照跑）。"""
    monkeypatch.setattr(LC, "signal", _SignalShim([]))  # 不真的改处置
    lc = _life(tmp_path, _Clock())
    with pytest.raises(KeyboardInterrupt):
        lc._on_signal(signal.SIGINT, None)
    assert sent[0]["reason"] == "worker 收到 SIGINT（开始收工）"


def test_close_without_why_marks_unknown(tmp_path: Path, sent: list[dict[str, Any]]) -> None:
    """没标注原因（异常/热替换路径）时不许编原因：写明「未标注」。"""
    lc = _life(tmp_path, _Clock())
    lc.close(done=1)
    assert sent[0]["reason"].startswith("worker 收线（未标注")


# ── 读数缺席 ≠ 读数崩掉（2026-11-15）──────────────────────────────────
# `readings` 是「遗言的最后一段」：任何一处取不到都只该让那一项缺席，绝不该把整条读数（乃至
# 上报腿）弄丢。判据是「窄捕后的行为」——以前是 `except Exception: pass/continue`，连本模块
# 自己的 bug 一起吞掉，于是「这台机器没 GPU」与「torch 读显存时炸了」在控制台上长得一样。

def test_readings_survive_missing_proc_and_unreadable_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pathlib

    from remote import lifecycle as lc_mod

    real_path = pathlib.Path

    def _fake_path(arg: Any = ".", *a: Any, **k: Any) -> Any:
        # 只替换 `/proc/self/status` 这一次读（Windows / 权限不足的形状），其余照常。
        if str(arg) == "/proc/self/status":
            return SimpleNamespace(read_text=lambda *_: (_ for _ in ()).throw(OSError("no /proc")))
        return real_path(arg, *a, **k)

    monkeypatch.setattr(lc_mod, "Path", _fake_path)
    monkeypatch.setattr(
        lc_mod.shutil, "disk_usage", lambda *_: (_ for _ in ()).throw(OSError("statvfs 挂了"))
    )

    r = readings(tmp_path, "cpu")
    assert r["rss_gb"] is None  # 取不到就不编数（不是 0，也不是 NaN）
    assert r["disk"] == []  # 盘读不到 ⇒ 这一项缺席，但整条读数还在
    assert r["device"] == "cpu"


def test_readings_survive_nvml_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """torch 在场、CUDA 也「可用」，但真去读显存时 NVML 抽风 ⇒ 只丢 `gpu` 这一项。"""

    class _Cuda:  # 与 torch.cuda 同形状（只用到 is_available/device_count/mem_get_info）
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def device_count() -> int:
            return 1

        @staticmethod
        def mem_get_info(index: int) -> Any:
            raise RuntimeError("NVML ERROR: driver not loaded")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=_Cuda))
    r = readings(tmp_path, "cuda:0")
    assert r["gpu"] == []
    assert r["disk"]  # 别的项照常


def test_sigterm_selfkill_failure_is_reported(
    tmp_path: Path, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """自投 SIGTERM 失败 = 进程活着而 hub 当它已下线 ⇒ **必须留一行**（且不抛）。

    这是本模块里唯一会改变进程结局的失败：静默的话现场只有「这台机器明明在计费，hub 却说它
    早下线了」，且与「没人处理 SIGTERM」的形状完全一样。
    """
    monkeypatch.setattr(LC, "signal", _SignalShim([]))
    monkeypatch.setattr(
        LC,
        "os",
        SimpleNamespace(
            getpid=lambda: 0,
            kill=lambda pid, s: (_ for _ in ()).throw(OSError("kill 被拒")),
            stat=os.stat,
        ),
    )
    lines: list[str] = []
    lc = _life(tmp_path, _Clock(), log=lines.append)
    lc._on_signal(signal.SIGTERM, None)  # 不抛

    assert sent and sent[0]["reason"].startswith("worker 收到 SIGTERM")  # 上报照旧先发
    assert len(lines) == 1
    assert "自投失败" in lines[0] and "OSError" in lines[0]
