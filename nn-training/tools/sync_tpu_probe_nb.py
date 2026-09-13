"""tools/sync_tpu_probe_nb.py —— 把探针脚本单源同步进 tpu-probe notebook。

探针脚本的正本在仓库 `nn-training/tools/tpu-probe.py`；`ipynb/tpu-probe.ipynb`
的 `%%writefile tpu_probe.py` cell 是它的逐字节生成副本（notebook 在 Colab/Kaggle
上要自包含运行，不能依赖仓库）。用法：

    python tools/sync_tpu_probe_nb.py            # 同步（写回 notebook）
    python tools/sync_tpu_probe_nb.py --check    # 只检查漂移；漂移 exit 1

drift 由 `tests/test_tpu_probe_notebook.py` 在 python-gate 常驻拦截——改探针
逻辑只改 tools/tpu-probe.py，然后跑一次本工具重新生成 notebook。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = NN_ROOT / "tools" / "tpu-probe.py"
NB_PATH = NN_ROOT / "ipynb" / "tpu-probe.ipynb"
WRITEFILE_MAGIC = "%%writefile tpu_probe.py"


def cell_text(cell: dict) -> str:
    """nbformat cell 的 source 拼成完整文本（list / str 两种存法都支持）。"""
    src = cell["source"]
    if isinstance(src, list):
        return "".join(src)
    return str(src)


def find_script_cell(nb: dict) -> int:
    """返回 %%writefile tpu_probe.py cell 的下标；缺失或多于一个则报错退出。"""
    hits = [i for i, cell in enumerate(nb["cells"])
            if cell["cell_type"] == "code" and cell_text(cell).startswith(WRITEFILE_MAGIC)]
    if len(hits) != 1:
        raise SystemExit(f"ERROR: 期望恰好 1 个 {WRITEFILE_MAGIC!r} cell，找到 {len(hits)} 个")
    return hits[0]


def embedded_body(nb: dict) -> str:
    """notebook 内嵌脚本正文（去掉 %%writefile 行）。"""
    return cell_text(nb["cells"][find_script_cell(nb)])[(len(WRITEFILE_MAGIC) + 1):]


def sync(check_only: bool) -> int:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    desired = WRITEFILE_MAGIC + "\n" + script
    current = cell_text(nb["cells"][find_script_cell(nb)])
    if current == desired:
        print(f"OK: {NB_PATH.name} 脚本 cell 与 {SCRIPT_PATH.name} 一致（{len(script)} bytes）")
        return 0
    if check_only:
        print(f"DRIFT: {NB_PATH.name} 的 {WRITEFILE_MAGIC} cell 与 {SCRIPT_PATH.name} 不一致")
        print("  -> 跑 `python tools/sync_tpu_probe_nb.py` 重新生成")
        return 1
    nb["cells"][find_script_cell(nb)]["source"] = [WRITEFILE_MAGIC + "\n", *script.splitlines(keepends=True)]
    # 与 notebook 现有格式逐字节一致的序列化（已验证 round-trip 稳定）
    NB_PATH.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SYNCED: {NB_PATH.name} 脚本 cell 已从 {SCRIPT_PATH.name} 重新生成（{len(script)} bytes）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 tools/tpu-probe.py 同步进 ipynb/tpu-probe.ipynb")
    parser.add_argument("--check", action="store_true", help="只检查漂移，不写回；漂移时 exit 1")
    return sync(parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
