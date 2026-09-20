"""nn-training 根 conftest —— 全局 per-test **耗时预算**（2026-09-20，用户口径）。

用户口径：单个测试 >5s 警告、>10s **报错**。理由（2026-09-20 实测）：一个安静的
26.5s 用例看起来「只是慢」，实际是三条线程停在 `all_settled.wait()` 上**纯空闲等**
生产超时（CPU 占用低、墙钟长）——耗时预算是抓这类退化的唯一廉价信号：不占 CPU 的
等待、真实的 sleep、串行化的网络超时，全都会在耗时上现形。

口径与环境变量（CLI 覆盖优先）：
  · 警告  5s   → `NN_TEST_WARN_S`（`--test-warn-s`）
  · 报错 10s   → `NN_TEST_FAIL_S`（`--test-fail-s`）
  · 个别用例确需更长 → 显式 `@pytest.mark.time_budget(seconds)`（**必须**在标记里
    写理由，见 pyproject markers 说明）。预算按「call 阶段」计（不含 fixture/收集）。

定位到根 conftest（而不是只放 tests/）是因为门禁跑 `pytest tests/ e2e/`：两层都要
被同一条规则覆盖。实现用 `pytest_runtest_makereport` 改写 outcome —— 超预算的用例
直接**判失败**（不是 teardown 报错），这样 `-x`、xdist、summary 全是标准语义。
"""

from __future__ import annotations

import os

import pytest

WARN_ENV = "NN_TEST_WARN_S"
FAIL_ENV = "NN_TEST_FAIL_S"
WARN_DEFAULT = 5.0
FAIL_DEFAULT = 10.0


def pytest_addoption(parser) -> None:
    group = parser.getgroup("nn-budget")
    group.addoption(
        "--test-warn-s",
        action="store",
        type=float,
        default=float(os.environ.get(WARN_ENV, WARN_DEFAULT)),
        help=f"单测耗时警告阈值（秒，缺省 {WARN_DEFAULT:g}；env {WARN_ENV}）",
    )
    group.addoption(
        "--test-fail-s",
        action="store",
        type=float,
        default=float(os.environ.get(FAIL_ENV, FAIL_DEFAULT)),
        help=f"单测耗时失败阈值（秒，缺省 {FAIL_DEFAULT:g}；env {FAIL_ENV}）",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "time_budget(seconds): 显式放宽该用例的耗时预算（须附理由；缺省 5s 警告 / 10s 失败）",
    )


def _budget_of(item) -> tuple[float, float]:
    """该用例的 (warn, fail)；标记 `time_budget(N)` 可显式抬高。"""
    warn = item.config.getoption("--test-warn-s")
    fail = item.config.getoption("--test-fail-s")
    marker = item.get_closest_marker("time_budget")
    if marker is not None and marker.args:
        warn = fail = float(marker.args[0])
    return warn, fail


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # 只算 call 阶段（fixture 建拆与收集不算）；已失败的用例不叠加噪音。
    if report.when != "call" or report.outcome != "passed":
        return
    warn, fail = _budget_of(item)
    secs = report.duration
    if secs > fail:
        report.outcome = "failed"
        report.longrepr = (
            f"耗时预算超限：本用例 call 阶段 {secs:.2f}s > {fail:g}s（用户口径 >10s 报错）。\n"
            f"墙钟长而 CPU 低的典型成因是纯空闲等待（生产超时/固定 sleep）——请改成"
            f"事件驱动，或用 policy 旋钮把配速调小（见 docs/nn.progress.md §98）。\n"
            f"确需更长时用 @pytest.mark.time_budget(<秒数>) 显式放宽并写明理由。"
        )
    elif secs > warn:
        # 警告：不改 outcome，只在终端留痕（`-W` 类告警会淹在别处，用 warnings 更好检索）。
        import warnings

        warnings.warn(
            f"{item.nodeid} 耗时 {secs:.2f}s 超过警告阈值 {warn:g}s",
            stacklevel=1,
        )
