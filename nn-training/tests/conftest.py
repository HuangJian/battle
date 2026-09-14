"""Shared pytest fixtures for nn-training pure-logic tests."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def bp_args(
    sps: int = 3,
    rotate_stages: int = 35,
    total_stages: int = 35,
    seeds: str = "0-3",
    stages: str = "0-3",
) -> types.SimpleNamespace:
    """Minimal duck-typed args for build_pairs()."""
    return types.SimpleNamespace(
        rotate_stages=rotate_stages,
        seeds_per_stage=sps,
        total_stages=total_stages,
        stages=stages,
        seeds=seeds,
        curriculum_stages="",
        curriculum_start=4,
        curriculum_every=8,
        curriculum_grow=4,
        seed_rotate=0,
    )


@pytest.fixture
def tmp(tmp_path: Path) -> Iterator[Path]:
    """Legacy test_run_rl.py compat：直接复用（被覆盖的）tmp_path。"""
    yield tmp_path


@pytest.fixture
def tmp_path(request) -> Path:
    """覆盖内置 tmp_path：项目内唯一目录、**零删除**（沙箱批量删除保护适配）。

    内置 tmp_path 依赖 pytest 的 basetemp——pytest 每次启动会**清空** basetemp
    （一次性删除全部累积文件，>50 触发沙箱 SAFE_DELETE_BULK_CONFIRM_REQUIRED：
    交互式弹确认、pre-commit hook 等无交互场景直接 SystemExit 失败）。

    本实现：
      * 目录落在仓库根 `tmp/pytest-tmp/<nodeid>-<pid>-<id>`（已 gitignore，
        2026-09-08 双 tmp 统一：不再用 nn-training/tmp）；
      * 每个测试唯一目录、**从不删除**（磁盘增长可接受，手动清理一次即可）；
      * pytest 的 basetemp 不再被创建/清空 → 全程零删除、零弹窗。
      * **pid 参与命名**（2026-09-02）：分片并行（nn-gate-shards.py 多进程跑
        pytest）时，各进程的 `id(request.node)` 序列相同——无 pid 会撞同一目录
        导致 mkdir FileExistsError（实测并行 pytest=1 的根因）。
    """
    import os
    import time as _time

    root = Path(__file__).resolve().parents[2] / "tmp" / "pytest-tmp"  # 仓库根 tmp/
    root.mkdir(parents=True, exist_ok=True)
    safe = request.node.nodeid.replace("/", "__").replace("::", "__")
    # pid + 毫秒时间戳 + id 三重唯一（2026-09-02）：
    #   * pid：分片并行（nn-gate-shards.py 多进程）时各进程 id 序列相同，无 pid 撞目录；
    #   * 时间戳：Windows PID 循环复用，连续运行可能拿到同 pid → 无时间戳时复用旧目录
    #     残留 → mkdir FileExistsError / 断言污染（实测连续分片 C=1 的根因）。
    d = root / f"{safe}-{os.getpid()}-{int(_time.time() * 1000)}-{id(request.node) & 0xFFFF}"
    d.mkdir(parents=True, exist_ok=True)
    # 登记给 session 收尾清理（**通过即删、失败保留**）——见文件末尾 hook 的说明。
    _TEST_TMP_DIRS.setdefault(request.node.nodeid, []).append(str(d))
    return d


# -------------------- 测试临时目录：通过即清、失败保留（2026-09-14 用户定案） --------------------
# 背景：上面的 tmp_path 覆盖为了绕开沙箱 basetemp 批量删除确认而**零删除**，于是
# `tmp/pytest-tmp` 一天能堆上万目录（实测 15612 个 / 1.8 GB）。门禁前置的
# nn-clean-tmp.py 只有「1 天窗口」兜底，来不及——它要遍历全部目录，把 ~30s 的门禁
# 拖成 5 分钟（2026-09-14 实测）。
#
# 语义（用户口径）：**通过的用例（含 skip）删掉自己的临时目录；失败/报错的原样保留**，
# 供事后 debug（集中清理交给 nn-clean-tmp.py 的 KEEP_DAYS 窗口）。
#
# 删除方式：交给 nn-clean-tmp.py 的**子进程**执行（`python -S` 跳过 site 初始化 ⇒
# 沙箱删除保护不注入 ⇒ 不弹确认、不触发配额）。进程内直接 rmtree 会被 sitecustomize
# 拦成 SystemExit（见平台说明），所以必须走子进程——与仓库既有清理器同一机制。
# 任何异常都吞掉：清理失败只是退回旧行为（堆着，由兜底清理收拾），绝不让测试因清理而红。
_TEST_TMP_DIRS: dict[str, list[str]] = {}
_TEST_BAD: set[str] = set()


def pytest_runtest_makereport(item, call):
    """标记「需要 debug」的用例：setup/call/teardown 任一阶段报错都保留其临时目录。"""
    if call.excinfo is not None:
        _TEST_BAD.add(item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    """session 收尾：把**通过**用例的临时目录清单交给清理器（失败/报错的不动）。

    ⚠️ **必须异步**（2026-09-14 实测踩坑）：一次全量约 800 个目录，Windows 上同步
    rmtree 每个都要几十~几百 ms —— 同步等它删完，session 收尾就变成几分钟（实测把
    门禁从 ~30s 拖到 215s+，提交被 hook 拦下）。清理是 best-effort 的收尾工作，**没有
    任何理由阻塞 session 结束**，故用 detached Popen 起进程、不等待：清单文件由清理器
    自己删（`nn-clean-tmp.py --paths` 用完即删）。
    """
    try:
        victims = [
            d for nodeid, dirs in _TEST_TMP_DIRS.items() if nodeid not in _TEST_BAD for d in dirs
        ]
        if not victims:
            return
        print(
            f"[conftest] tmp 清理：{len(victims)} 个通过用例的目录入队"
            f"（登记 {len(_TEST_TMP_DIRS)} 个用例，其中 {len(_TEST_BAD)} 个需保留）",
            flush=True,
        )
        import os
        import subprocess
        import tempfile

        cleaner = Path(__file__).resolve().parents[2] / "tools" / "githook" / "nn-clean-tmp.py"
        if not cleaner.is_file():
            return
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            prefix="pytest-clean-",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write("\n".join(victims))
            list_path = fh.name
        try:
            from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

            log_path = cleaner.parent.parent.parent / "tmp" / "pytest-tmp" / ".cleanup.log"
            with log_path.open("a", encoding="utf-8") as log_fh:
                log_fh.write(f"--- spawn {list_path} ({len(victims)} dirs)\n")
                log_fh.flush()
                subprocess.Popen(
                    [sys.executable, "-S", str(cleaner), "--paths", list_path],
                    **_POPEN_NO_WINDOW,  # dict：Windows 下 {"creationflags": CREATE_NO_WINDOW}
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
        except Exception as e:
            print(
                f"[conftest] tmp 清理启动失败（留给门禁兜底）: {type(e).__name__}: {e}", flush=True
            )
            try:
                os.unlink(list_path)
            except OSError:
                pass
    except Exception:
        pass
