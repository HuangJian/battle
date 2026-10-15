"""tests/remote/test_delta_files.py —— `remote/delta_files` 的契约（L0 叶子：名字/形态/候选根）。

为什么单独钉这个叶子（而不是散在 `test_weights_standard` / `test_opt_standard` 里）：

  ① **魔数是两份字面量**。`delta_files` 里写死 `b"BWD1"` / `b"BOD1"` 是刻意的（它不许 import
     `remote.weights_delta` / `remote.opt_delta`：那两个吃 numpy/torch，而 hub 的启动闭包要靠
     本模块判形 —— `tests/remote/test_hub_import_graph.py` 钉死）。「刻意重复」必须**有对账**，
     否则就是两份会漂开的真相：本文件把那两处常量与两个容器模块的常量逐字节比一次。
  ② 名字约定（整份 = `.json`/`.tar`、压缩版 = `.delta`，同目录同主名成对）与「按内容定名」
     （`named_for`）是 hub 落位端与两个落地器共用的口径，漂了会让 hub 树里出现「名字说整份、
     身子是容器」的件。
  ③ `any_container_file`（任一族的容器判据）是 2026-10-15 收成一份的共用行：曾经在
     `hub/deltas`、`hub/store_offline`、`remote/run_loop` 各写一遍（其中两份逐字节相同，
     复用账本当场标红）。它「只读 4 字节、读不动就 False」的语义是热路径与坏盘上的行为承诺。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import delta_files as df
from remote import opt_delta as od
from remote import weights_delta as wd

# ─────────────────────────── ① 魔数对账 ───────────────────────────


def test_magics_match_the_two_container_modules_byte_for_byte() -> None:
    """`delta_files` 的魔数常量与两个容器模块的**逐字节一致**（两份字面量必须有对账）。"""
    assert df.WEIGHTS_DELTA_MAGIC == wd.W_DELTA_MAGIC == b"BWD1"
    assert df.OPT_DELTA_MAGIC == od.OPT_DELTA_MAGIC == b"BOD1"
    assert df.DELTA_MAGICS == (wd.W_DELTA_MAGIC, od.OPT_DELTA_MAGIC)
    # 两族魔数不得相同：`any_container_file` 只答「是不是压缩版」，同值会让「哪一族」也分不开。
    assert len(set(df.DELTA_MAGICS)) == 2


def test_the_leaf_imports_nothing_but_stdlib() -> None:
    """L0 的硬约束：顶层只 import stdlib（`common.*` / `remote.*` / numpy / torch 一概不碰）。

    这条**必须**在源码上钉（而不是靠 import 成功与否）：hub 的启动闭包只装本模块，一旦它拖上
    `common.*` 就升到 L1（层号账本 `tests/test_remote_dag.py`），拖上 numpy/torch 则 hub-server
    在 2026-10-14 那类无 torch 的机器上直接起不来。
    """
    tree = ast.parse((ROOT / "remote" / "delta_files.py").read_text(encoding="utf-8"))
    # 连函数体内的 import 一起收（`tests/helpers/remote_dag.py` 同一口径）："延迟 import 上层"
    # 对 L0 同样是依赖，只是把失败推迟到调用点而已。
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            tops.add(node.module.split(".")[0])
    assert tops <= {"__future__", "collections", "pathlib"}, f"L0 叶子长了依赖：{sorted(tops)}"


# ─────────────────────────── ② 名字与形态 ───────────────────────────


def test_variants_put_the_usable_name_first_and_the_container_second() -> None:
    """两态候选名：整份在前（能直接用）、压缩版在后；`find_existing` 也按此序。"""
    assert df.variants("weights.json") == ("weights.json", "weights.delta")
    assert df.variants("opt.tar") == ("opt.tar", "opt.delta")
    assert df.delta_name("weights.json") == "weights.delta"
    assert df.delta_name("opt.tar") == "opt.delta"


def test_named_for_follows_the_content_not_the_caller(tmp_path: Path) -> None:
    """定名按**内容**：装着容器的件不许叫整份名（否则读方/控制台会把它当可装载权重）。"""
    assert df.named_for("weights.json", b"BWD1....") == "weights.delta"
    assert df.named_for("opt.tar", b"BOD1....") == "opt.delta"
    assert df.named_for("weights.json", b'{"format":') == "weights.json"
    assert df.named_for("opt.tar", b"ustar\x00") == "opt.tar"
    # 不足 4 字节的短件：不当容器（`raw[:4]` 比整串安全，短串不会命中）
    assert df.named_for("weights.json", b"BW") == "weights.json"


def test_find_existing_prefers_the_full_file_and_falls_back_to_the_pair(tmp_path: Path) -> None:
    """两种名字都在时取整份；只有压缩版时也要找得到（旧数据/落地失败的那一轮）。"""
    d = tmp_path / "it-237"
    d.mkdir()
    assert df.find_existing(d, "weights.json") is None
    (d / "weights.delta").write_bytes(b"BWD1")
    assert df.find_existing(d, "weights.json") == d / "weights.delta"
    (d / "weights.json").write_bytes(b"{}")
    assert df.find_existing(d, "weights.json") == d / "weights.json"


def test_any_container_file_reads_four_bytes_and_never_raises(tmp_path: Path) -> None:
    """`any_container_file`：两族都认；不存在/是目录/读不动一律 `False`（**绝不抛**）。"""
    d = tmp_path / "files"
    d.mkdir()
    (d / "weights.delta").write_bytes(wd.W_DELTA_MAGIC + b"payload")
    (d / "opt.delta").write_bytes(od.OPT_DELTA_MAGIC + b"payload")
    (d / "weights.json").write_bytes(b'{"format":"nn-weights-json"}')
    (d / "short").write_bytes(b"BW")

    assert df.any_container_file(d / "weights.delta") is True
    assert df.any_container_file(d / "opt.delta") is True
    assert df.any_container_file(d / "weights.json") is False
    assert df.any_container_file(d / "short") is False
    assert df.any_container_file(d / "missing") is False
    assert df.any_container_file(d) is False  # 目录：`open` 抛 OSError 也要落到 False
    # 与「是不是**这一族**」分开：那一个由 `container_file` 回答（本体解包前必须问清族）。
    assert df.container_file(d / "weights.delta", wd.W_DELTA_MAGIC) is True
    assert df.container_file(d / "weights.delta", od.OPT_DELTA_MAGIC) is False


def test_head4_is_a_four_byte_read_only(tmp_path: Path) -> None:
    """`head4` 只读 4 字节（热路径上每 tick/每轮都要问一次形态）。"""
    p = tmp_path / "big.bin"
    p.write_bytes(b"BWD1" + b"\x00" * (1 << 16))
    assert df.head4(p) == b"BWD1"
    assert df.head4(tmp_path / "nope") == b""


# ─────────────────────────── ③ 候选根与扫件 ───────────────────────────


def test_search_roots_order_is_sibling_then_round_then_traj(tmp_path: Path) -> None:
    """候选根顺序 = 命中概率：同目录 → 兄弟轮目录 → traj 树 → 归档 → 额外根。"""
    traj = tmp_path / "y5-term"
    it_dir = traj / "remote-jobs" / "offline" / "run-a" / "it-237"
    it_dir.mkdir(parents=True)
    (traj / "training_log.jsonl").write_bytes(b"")

    roots = df.search_roots(
        it_dir / "weights.json",
        traj_root=traj,
        extra_roots=(tmp_path / "extra",),
        course_dir=tmp_path / "weights" / "y5-term",
    )
    assert roots == [
        it_dir,
        it_dir.parent,
        traj,
        tmp_path / "weights" / "y5-term",
        tmp_path / "extra",
    ]
    # 只有字节、没有文件（`land_opt_bytes` 那种入口）：同目录那两格缺席，其余照旧
    assert df.search_roots(None, traj_root=traj, extra_roots=(tmp_path / "extra",)) == [
        traj,
        tmp_path / "extra",
    ]


def test_traj_of_finds_the_course_dir_by_either_anchor(tmp_path: Path) -> None:
    """`<traj>` = 祖先里第一个有 `remote-jobs/` 或 `training_log.jsonl` 的目录。"""
    traj = tmp_path / "y5-term"
    (traj / "remote-jobs").mkdir(parents=True)
    deep = traj / "deliver" / "run-a" / "it-007" / "weights.json"
    deep.parent.mkdir(parents=True)
    assert df.traj_of(deep) == traj

    other = tmp_path / "nocourse"
    log = other / "it-3" / "weights.json"
    log.parent.mkdir(parents=True)
    (other / "training_log.jsonl").write_bytes(b"")
    assert df.traj_of(log) == other
    assert df.traj_of(tmp_path / "loose" / "weights.json") is None


def test_candidate_files_excludes_the_input_and_dedups(tmp_path: Path) -> None:
    """扫件：**不含调用方手里那份**（它就是待比对的输入），同名重复只留一次，按内容盲扫。"""
    d = tmp_path / "it-007"
    d.mkdir()
    me = d / "weights.json"
    me.write_bytes(b"{}")
    sib = d / "weights.delta"
    sib.write_bytes(b"BWD1")
    roots = [d, d, tmp_path / "missing"]
    got = df.candidate_files(me, roots, ("weights*.json", "weights*.delta"))
    assert got == [sib], got
    # 根本身是文件 ⇒ 直接算一个候选（调用方拿单个文件当额外根）
    assert df.candidate_files(me, [sib], ("*.json",)) == [sib]
