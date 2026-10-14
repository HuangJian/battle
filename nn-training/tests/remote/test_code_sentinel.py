"""test_code_sentinel —— 导入哨兵：**两份实现不许漂**、分类不许含混、必须**有界**。

（plan/offline-code-authority-and-width B3/B5，2026-10-14）

被钉的三件事：

  1. **两份实现同判**：权威实现住 `common/code_sentinel.py`（打包侧用），而
     `remote/offline_boot.py` 是**自包含引导模块**（raw 单文件拉取，跑在「包到手之前」，
     且 `common/proc.py` 明写它不许依赖 `common`）⇒ 它内联了一份同形实现。判据是**两者对
     同一批树给出的 `(ok, kind)` 逐条相同** —— 不是「长得像」，是同一份行为。
  2. **分类**（评审 P0-3 的硬要求）：缺**第三方**（numpy/…）⇒ `env`（调用方放行，因为换候选
     也换不来一个 numpy）；本仓模块坏了（现场那个 `METRICS_NAME`）⇒ `code`（换候选/拒跑）。
     收到 `worker.ppo.common` 那种顶层 `import torch` 的模块会让这条判据退化成本机环境的照妖镜。
  3. **有界**：`timeout=` 到点就返回（挂住的 import 没有进程内取消手段，只有子进程能杀）。
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import code_sentinel
from remote import offline_boot


def _tree(**files: str) -> str:
    """一棵（解到磁盘的）最小代码树 ⇒ 目录路径；键里的 `__` = 路径分隔符。"""
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="sentinel-tree-"))
    for name, text in files.items():
        p = d / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return str(d)


GOOD = {
    "common__shard.py": "METRICS_NAME = 'm'\n",
    "worker__ppo__np_core.py": "from common.shard import METRICS_NAME\n",
    "remote__run_loop.py": "MARKER = 1\n",
}
MIXED = {**GOOD, "common__shard.py": "OTHER = 1\n"}  # np_core 已改 / shard 未改（现场形状）
ENV_MISSING = {**GOOD, "worker__ppo__np_core.py": "import bcity_no_such_third_party\n"}
BROKEN_SYNTAX = {**GOOD, "remote__run_loop.py": "def broken(:\n"}


@pytest.mark.parametrize("files", [GOOD, MIXED, ENV_MISSING, BROKEN_SYNTAX])
def test_both_implementations_agree_on_the_same_tree(files: dict[str, str]) -> None:
    """★ 漂移守卫：引导模块里那份内联实现与 `common/code_sentinel.py` **同判**（含分类）。"""
    tree = _tree(**files)
    canon = code_sentinel.run_import_sentinel(tree)
    local = offline_boot._run_import_sentinel(Path(tree))
    assert (canon[0], canon[1]) == (local[0], local[1]), f"两份实现判得不一致：{canon} vs {local}"


def test_constants_are_the_same_bytes() -> None:
    """清单 / 超时 / 本仓顶层包三组常量逐字相同（照 `test_offline_local_first` 的常量对账口径）。"""
    assert offline_boot._SENTINEL_MODULES == code_sentinel.SENTINEL_MODULES
    assert offline_boot._SENTINEL_TIMEOUT_SEC == code_sentinel.SENTINEL_TIMEOUT_SEC
    assert offline_boot._SENTINEL_REPO_TOPS == code_sentinel.REPO_TOPS


def test_a_self_consistent_tree_passes() -> None:
    ok, kind, detail = code_sentinel.run_import_sentinel(_tree(**GOOD))
    assert (ok, kind, detail) == (True, "", "")


def test_a_mixed_blood_tree_is_code_not_env() -> None:
    """★ 现场那个 `METRICS_NAME`：判 `code`（换候选能修），而且细节里**带着那条真错误**。"""
    ok, kind, detail = code_sentinel.run_import_sentinel(_tree(**MIXED))
    assert ok is False and kind == "code", (kind, detail)
    assert "METRICS_NAME" in detail and "common.shard" in detail, detail


def test_a_missing_third_party_is_env_not_code() -> None:
    """★ 环境缺件（无 numpy/torch 的机器）必须与「代码坏」分开 —— 否则整批候选被误杀。

    判据只钉 `kind`（分类是契约；细节文本只进日志，不在这里当判据 —— 见本文件头）。
    """
    ok, kind, detail = code_sentinel.run_import_sentinel(_tree(**ENV_MISSING))
    assert ok is False and kind == "env", (kind, detail)


def test_a_syntax_error_is_code() -> None:
    ok, kind, _detail = code_sentinel.run_import_sentinel(_tree(**BROKEN_SYNTAX))
    assert ok is False and kind == "code"


def test_the_sentinel_is_bounded(tmp_path: Path) -> None:
    """挂住的 import（`time.sleep`）到点即回：60s 的默认值是兜底，`timeout=` 可压到测试尺度。"""
    # 快照一个「睡死」的模块：直接把哨兵清单换成它
    (tmp_path / "sleepy.py").write_text("import time; time.sleep(30)\n", encoding="utf-8")
    t0 = time.monotonic()
    ok, kind, detail = code_sentinel.run_import_sentinel(
        tmp_path, modules=("sleepy",), timeout=1.0
    )
    assert (ok, kind) == (False, "code"), (kind, detail)
    # timing-ok: 上界兜底（`timeout=1.0` 必须真把睡 30s 的模块掐掉，20s 只挡挂起）
    assert time.monotonic() - t0 < 20, detail


def test_provenance_meta_stays_path_free(tmp_path: Path) -> None:
    """`import_ok` 是布尔、`code_meta.jsonc` 里**不含路径**（细节只进日志）：挥发字段一进包就破 sha。"""
    import json

    tree = tmp_path / "nn"
    (tree / "common").mkdir(parents=True)
    (tree / "common" / "shard.py").write_text("OTHER = 1\n", encoding="utf-8")
    logs: list[str] = []
    meta = code_sentinel.code_tree_provenance(tree, logs.append)
    assert meta["import_ok"] is False
    assert "code" in logs[-1], logs
    assert str(tree) not in json.dumps(meta), "provenance 里不许出现绝对路径"
