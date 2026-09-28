"""`remote/bc_job.bc_init_resume_path` —— warm-start 起点判定（课程 `train.init_from`）。

进行中的续跑归续跑权重所有（`resume_epoch != 0` ⇒ None）；只有全新开训且
payload 带了 `init_weights.json` 时才返回起点路径。旧 job/从随机起 ⇒ None，
行为逐字节不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.bc_job import bc_init_resume_path


def test_resume_in_progress_owns_weights(tmp_path: Path) -> None:
    """续跑中（resume_epoch>0）：即使 init 文件在，也不重定起点。"""
    (tmp_path / "init_weights.json").write_text("{}", encoding="utf-8")
    assert bc_init_resume_path(tmp_path, 3) is None


def test_fresh_run_with_init_file(tmp_path: Path) -> None:
    """全新开训＋文件在：返回该路径（调用方直接作 `--resume` 值）。"""
    p = tmp_path / "init_weights.json"
    p.write_text("{}", encoding="utf-8")
    assert bc_init_resume_path(tmp_path, 0) == str(p)


def test_fresh_run_without_init_file(tmp_path: Path) -> None:
    """全新开训＋文件缺席（旧 job/从随机起）：None，行为不变。"""
    assert bc_init_resume_path(tmp_path, 0) is None
