"""tests/test_tpu_probe_notebook.py —— tpu-probe notebook 的单源 drift 守卫。

`ipynb/tpu-probe.ipynb` 的 `%%writefile tpu_probe.py` cell 必须与
`tools/tpu-probe.py` 逐字节一致：notebook 在 Colab/Kaggle 上自包含运行全靠这份
内嵌副本。改探针逻辑只能改 tools/tpu-probe.py，再跑 tools/sync_tpu_probe_nb.py
重新生成——手改 notebook 会在这里被拦下（AGENTS §8：独立重实现校验，不 import 被测工具）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NN = Path(__file__).resolve().parent.parent
if str(NN) not in sys.path:
    sys.path.insert(0, str(NN))

from tests.subproc_util import run_utf8

SCRIPT = NN / "tools" / "tpu-probe.py"
NB = NN / "ipynb" / "tpu-probe.ipynb"
SYNC_TOOL = NN / "tools" / "sync_tpu_probe_nb.py"
MAGIC = "%%writefile tpu_probe.py"


def script_cell_bodies() -> list[str]:
    """notebook 里全部以 %%writefile tpu_probe.py 开头的 code cell 文本。"""
    nb = json.loads(NB.read_text(encoding="utf-8"))
    bodies = []
    for cell in nb["cells"]:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", "")
        text = "".join(src) if isinstance(src, list) else str(src)
        if text.startswith(MAGIC):
            bodies.append(text)
    return bodies


def test_embedded_probe_script_matches_repo_copy() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    bodies = script_cell_bodies()
    assert len(bodies) == 1, f"期望恰好 1 个 {MAGIC} cell，实际 {len(bodies)} 个"
    assert bodies[0] == MAGIC + "\n" + script, (
        "tpu-probe.ipynb 内嵌脚本与 tools/tpu-probe.py 漂移——"
        "跑 `python tools/sync_tpu_probe_nb.py` 重新生成（不要手改 notebook）"
    )


def test_sync_tool_check_mode_agrees() -> None:
    """sync 工具自身的 --check 也要通过（防工具 rot）。

    run_utf8：裸 text=True 在 zh-CN Windows 按 cp936 解码子进程 UTF-8 输出
    （PYTHONIOENCODING=utf-8），读线程 UnicodeDecodeError → stdout=None。
    """
    r = run_utf8(
        [sys.executable, str(SYNC_TOOL), "--check"],
        timeout=60,
    )
    assert r.returncode == 0, f"sync --check 报漂移:\n{(r.stdout or '')}\n{(r.stderr or '')}"
