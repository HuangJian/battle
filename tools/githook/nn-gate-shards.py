"""nn-training 测试分片并行门禁（沙箱零删除兼容，2026-09-02）。

为什么不用 pytest-xdist：xdist 的 worker 强制使用 pytest 默认 basetemp
（系统 %TEMP%/pytest-of-ustch），启动 GC 清理旧目录会触发沙箱删除确认
（无交互场景直接失败）。本脚本用 **N 个独立 pytest 进程**按文件轮转分片并行：
每个进程都跑 nn-training 的 conftest（覆盖 tmp_path fixture → 项目内唯一目录、
从不删除、不创建 basetemp）→ 全程零删除、零弹窗。

用法（由 pre-commit hook 调用，cwd = nn-training/）：
  python tools/githook/nn-gate-shards.py <tests_dir> <n_shards>

分片按文件轮转（i % n）保证均衡；任一 shard 失败 → 退出码 1（set -e 让 hook
停住）。新增测试文件自动落入轮转，无需维护清单。
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: nn-gate-shards.py <tests_dir> <n_shards>", file=sys.stderr)
        return 2
    tests_dir, n_shards = sys.argv[1], int(sys.argv[2])
    files = sorted(glob.glob(os.path.join(tests_dir, "test_*.py")))
    if not files:
        print(f"nn-gate: no test files under {tests_dir}", file=sys.stderr)
        return 2
    groups: list[list[str]] = [[] for _ in range(n_shards)]
    for i, f in enumerate(files):
        groups[i % n_shards].append(f)

    procs: list[subprocess.Popen] = []
    for g in groups:
        if not g:
            continue
        # 每个 shard 独立进程：import torch 并行、conftest 覆盖 tmp_path（零删除）
        cmd = [sys.executable, "-m", "pytest", "-q", *g]
        procs.append(subprocess.Popen(cmd, cwd=os.getcwd()))
    rc = 0
    for p in procs:
        if p.wait() != 0:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
