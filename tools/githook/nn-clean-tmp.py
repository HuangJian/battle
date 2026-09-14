"""清理仓库根 tmp/pytest-tmp 下过期测试临时目录（沙箱零弹窗版）。

2026-09-08 双 tmp 统一：pytest 临时目录随 conftest.py 迁到仓库根 tmp/，
本脚本目标同步迁移（不再清理 nn-training/tmp）。

**为什么用 `python -S` 启动**（2026-09-02，用户拍板方案）：
WorkBuddy 沙箱的删除保护通过 sitecustomize 注入（`python -c "import shutil;
print(shutil.rmtree.__module__)"` 正常模式输出 `sitecustomize`）。`-S` 跳过 site
初始化 → sitecustomize 不注入 → shutil.rmtree 为 CPython 原版 → 删除不触发沙箱
删除确认（交互式弹窗 / 无交互失败）。这是**绕过沙箱删除保护**的手段——用户知情
并批准，且**严格限界**：

  * 只删除 `tmp/pytest-tmp/` 下的**直接子目录**（测试临时目录）；
  * 按目录 mtime 保留最近 KEEP_DAYS 天（默认 1，环境变量 NN_TMP_KEEP_DAYS 可调）；
  * 绝不触碰目录本身、其他路径、或非子目录内容。

用法（cwd 任意，脚本自定位目标）：
  python -S tools/githook/nn-clean-tmp.py
  NN_TMP_KEEP_DAYS=2 python -S tools/githook/nn-clean-tmp.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

KEEP_DAYS = float(os.environ.get("NN_TMP_KEEP_DAYS", "1"))
# 脚本位于 tools/githook/ → parents[2] = 仓库根
TARGET = Path(__file__).resolve().parents[2] / "tmp" / "pytest-tmp"


def remove_listed(list_path: Path) -> int:
    """按清单删除（2026-09-14：测试 session 结束时交来「通过测试的临时目录」）。

    与 KEEP_DAYS 全量清理的分工：那是**兜底**（1 天窗口，扫全目录，忙一天就来不及——
    实测堆到 15612 个目录 / 1.8 GB，把门禁前置清理拖成 5 分钟）；这是**随手清**，
    测试通过即删自己那份，失败的留给 debug。

    安全边界（即使清单被污染也删不到 tmp/ 之外）：只接受 **TARGET 正下方一层** 的
    真实目录 —— 父目录必须等于 TARGET（大小写不敏感比较，适配 Windows）。
    """
    if not list_path.is_file():
        return 0
    base = str(TARGET.resolve()).lower()
    removed = skipped = 0
    for raw in list_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            rp = Path(s).resolve()
        except OSError:
            skipped += 1
            continue
        if str(rp.parent).lower() != base or not rp.is_dir():
            skipped += 1
            continue
        try:
            shutil.rmtree(rp)  # -S 下为原版 rmtree（沙箱保护未注入）
            removed += 1
        except OSError:
            skipped += 1
    if removed or skipped:
        print(f"[nn-clean-tmp] listed-pass removed {removed}, skipped {skipped}")
    try:
        # 清单由本进程负责删：调用方（conftest.sessionfinish）是 **detached 起进程后
        # 立即返回**、不等也不回收，所以删除责任在消费方这边。
        list_path.unlink()
    except OSError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理 tmp/pytest-tmp（过期 / 按清单）")
    ap.add_argument(
        "--paths",
        default="",
        help="只删清单文件里列出的目录（每行一个绝对路径）；缺省 = 按 KEEP_DAYS 全量清理",
    )
    args = ap.parse_args(argv)
    if not TARGET.is_dir():
        return 0
    if args.paths:
        return remove_listed(Path(args.paths))
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
        print(f"[nn-clean-tmp] removed {removed} expired dir(s) (keep {KEEP_DAYS:g}d), kept {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
