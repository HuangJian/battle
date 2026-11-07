"""权重归档命名（`worker/archive.py::backup_weights`）。

两条契约：
* **缺省逐字节不变**：RL 仍是 `{prefix}.it{it}.{ts}{suffix}`（本文件钉住）；
* **BC 每 epoch**（#12）：`label=ep{N}` ⇒ `{prefix}.ep{N}.{ts}{suffix}`，同一 label 幂等
  （重发/补发不产生第二份）。
"""

from __future__ import annotations

from pathlib import Path

from worker.archive import backup_weights


def _src(tmp_path: Path) -> Path:
    p = tmp_path / "weights.json"
    p.write_text('{"w": 1}', encoding="utf-8")
    return p


def test_default_name_is_still_it(tmp_path: Path) -> None:
    """RL 口径：`it{it}`；新增 `label` 不得改变缺省命名。"""
    dst = backup_weights(str(_src(tmp_path)), 4, prefix="rl-x", backup_dir=str(tmp_path / "arc"))
    assert dst is not None
    assert Path(dst).name.startswith("rl-x.it4.")


def test_label_ep_is_idempotent(tmp_path: Path) -> None:
    """BC：`ep{N}` 命名；同一 epoch 第二次归档返回旧路径、只留一份。"""
    src = _src(tmp_path)
    bdir = tmp_path / "arc"
    first = backup_weights(str(src), 1, prefix="bc-x", backup_dir=str(bdir), label="ep3")
    second = backup_weights(str(src), 2, prefix="bc-x", backup_dir=str(bdir), label="ep3")
    assert first is not None and first == second
    assert Path(first).name.startswith("bc-x.ep3.")
    assert len(list(bdir.glob("bc-x.ep3.*.json"))) == 1

    other = backup_weights(str(src), 1, prefix="bc-x", backup_dir=str(bdir), label="ep4")
    assert other is not None and other != first
    assert len(list(bdir.glob("bc-x.ep*.*.json"))) == 2
