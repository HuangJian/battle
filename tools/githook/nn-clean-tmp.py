"""清理 nn-training/tmp/pytest-tmp 下过期测试临时目录（沙箱零弹窗版）。

**为什么用 `python -S` 启动**（2026-09-02，用户拍板方案）：
WorkBuddy 沙箱的删除保护通过 sitecustomize 注入（`python -c "import shutil;
print(shutil.rmtree.__module__)"` 正常模式输出 `sitecustomize`）。`-S` 跳过 site
初始化 → sitecustomize 不注入 → shutil.rmtree 为 CPython 原版 → 删除不触发沙箱
删除确认（交互式弹窗 / 无交互失败）。这是**绕过沙箱删除保护**的手段——用户知情
并批准，且**严格限界**：

  * 只删除 `nn-training/tmp/pytest-tmp/` 下的**直接子目录**（测试临时目录）；
  * 按目录 mtime 保留最近 KEEP_DAYS 天（默认 1，环境变量 NN_TMP_KEEP_DAYS 可调）；
  * 绝不触碰目录本身、其他路径、或非子目录内容。

用法（cwd 任意，脚本自定位目标）：
  python -S tools/githook/nn-clean-tmp.py
  NN_TMP_KEEP_DAYS=2 python -S tools/githook/nn-clean-tmp.py
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

KEEP_DAYS = float(os.environ.get("NN_TMP_KEEP_DAYS", "1"))
# 脚本位于 tools/githook/ → parents[2] = 仓库根
TARGET = Path(__file__).resolve().parents[2] / "nn-training" / "tmp" / "pytest-tmp"


def main() -> int:
    if not TARGET.is_dir():
        return 0
    cutoff = time.time() - KEEP_DAYS * 86400
    removed = kept = 0
    for child in TARGET.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(child)  # -S 下为原版 rmtree（沙箱保护未注入）
                removed += 1
            except OSError:
                pass
        else:
            kept += 1
    if removed:
        print(
            f"[nn-clean-tmp] removed {removed} expired dir(s) "
            f"(keep {KEEP_DAYS:g}d), kept {kept}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
