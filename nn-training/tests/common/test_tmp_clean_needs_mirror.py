"""`tools/tmp-clean.py` 的**转存段**（plan/course-metrics-persistence §3.5 / §5 T10）。

`--apply` 不再是「算了就删」：先调 dashboard CLI 把四个读数文件镜像到备份库，再按覆盖核对
逐目标删除；转存失败/窄窗口竞态只跳「含账本且未覆盖」的目标（退出码仍 0），`--no-mirror`
才是显式放弃。本文件钉六件事：

① 覆盖判据：无 meta ⇒ 未覆盖；append 的 bytes 落后 ⇒ 未覆盖；重写型比 sha256；
② `--apply` 先转存、未覆盖的目标跳过、其余照删；
③ 转存失败 ⇒ 跳过含账本目标（不硬删）但仍退出 0；
④ `--no-mirror` ⇒ 按旧行为直删；
⑤ dry-run 打未覆盖预览，不删也不转存；
⑥ transload 的命令形状（调 CLI，不重写引擎）。

加载方式与 `test_tmp_clean_keeps_deliver.py` 相同：按文件路径加载仓根脚本。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parents[2]
TOOLS = NN_ROOT.parent / "tools"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_tmp_clean_mirror_under_test", TOOLS / "tmp-clean.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline=""：Windows 上不让 `\n` 翻译成 `\r\n`（否则字节数断言全错）。
    p.write_text(body, encoding="utf-8", newline="")


def _old(p: Path) -> None:
    os.utime(p, (0, 0))


def _meta(root: Path, course: str, files: dict[str, dict[str, Any]], activity: float = 0.0) -> None:
    _write(
        root / course / "mirror-meta.json",
        json.dumps(
            {"version": 1, "course": course, "files": files, "lastSourceActivity": activity}
        ),
    )


def test_uncovered_then_covered(tmp_path: Path, monkeypatch: Any) -> None:
    mod = _load()
    tmp = tmp_path / "tmp"
    course = tmp / "c1"
    _write(course / "training_log.jsonl", "a\n")
    monkeypatch.setattr(mod, "TMP", tmp)
    root = tmp_path / "lib"

    assert mod.uncovered_reason(course, root)  # 无 mirror-meta
    _meta(
        root,
        "c1",
        {
            "training_log.jsonl": {
                "bytes": 2,
                "lines": 1,
                "anchor": "",
                "srcMtime": 0,
                "mode": "append",
            }
        },
    )
    assert mod.uncovered_reason(course, root) is None

    _write(course / "training_log.jsonl", "a\nb\n")  # 转存后又追加
    assert mod.uncovered_reason(course, root) == "c1/training_log.jsonl: 落后 2B"


def test_rewrite_compares_sha(tmp_path: Path, monkeypatch: Any) -> None:
    mod = _load()
    tmp = tmp_path / "tmp"
    course = tmp / "c2"
    body = '{"1":{"games":1}}'
    _write(course / ".pool-actuals-cache.json", body)
    monkeypatch.setattr(mod, "TMP", tmp)
    root = tmp_path / "lib"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    _meta(
        root,
        "c2",
        {
            ".pool-actuals-cache.json": {
                "bytes": len(body),
                "lines": 0,
                "anchor": "",
                "srcMtime": 0,
                "mode": "rewrite",
                "sha256": "0" * 64,
            }
        },
    )
    assert mod.uncovered_reason(course, root) == "c2/.pool-actuals-cache.json: sha256 不符"
    _meta(
        root,
        "c2",
        {
            ".pool-actuals-cache.json": {
                "bytes": len(body),
                "lines": 0,
                "anchor": "",
                "srcMtime": 0,
                "mode": "rewrite",
                "sha256": digest,
            }
        },
    )
    assert mod.uncovered_reason(course, root) is None


def _fixture(tmp_path: Path, mod: Any, monkeypatch: Any) -> tuple[Path, Path]:
    tmp = tmp_path / "tmp"
    course = tmp / "c-uncovered"
    _write(course / "training_log.jsonl", "x\n")
    junk = tmp / "junk.log"
    _write(junk, "junk")
    _old(course / "training_log.jsonl")
    _old(junk)
    monkeypatch.setattr(mod, "TMP", tmp)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)  # 打印相对路径用
    monkeypatch.setattr(mod, "mirror_root", lambda: tmp_path / "lib")
    return course, junk


def test_apply_skips_uncovered_and_deletes_rest(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    mod = _load()
    course, junk = _fixture(tmp_path, mod, monkeypatch)
    monkeypatch.setattr(mod, "transload", lambda: (True, "指标转存: 同步 0 课"))
    rc = mod.main(["--apply", "--keep-runs", "0", "--keep-days", "0", "--force"])
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert course.is_dir()  # 未覆盖 ⇒ 不删
    assert not junk.exists()  # 非账本目标照删
    # 行尾清单判据（不是措辞断言：左串 vs 列表 = 数据比较），且转存输出原样回显。
    assert any(line.startswith("[tmp-clean] skipped (未转存)") for line in lines)
    assert [line for line in lines if line.startswith("[tmp-clean] 指标转存")] == [
        "[tmp-clean] 指标转存: 同步 0 课"
    ]


def test_transload_failure_skips_ledger_targets(tmp_path: Path, monkeypatch: Any) -> None:
    mod = _load()
    course, junk = _fixture(tmp_path, mod, monkeypatch)
    monkeypatch.setattr(mod, "transload", lambda: (False, "bun 不在 PATH"))
    rc = mod.main(["--apply", "--keep-runs", "0", "--keep-days", "0", "--force"])
    assert rc == 0
    assert course.is_dir()
    assert not junk.exists()


def test_no_mirror_deletes_like_before(tmp_path: Path, monkeypatch: Any) -> None:
    mod = _load()
    course, junk = _fixture(tmp_path, mod, monkeypatch)
    called = {"n": 0}

    def _boom() -> tuple[bool, str]:
        called["n"] += 1
        return True, ""

    monkeypatch.setattr(mod, "transload", _boom)
    rc = mod.main(["--apply", "--no-mirror", "--keep-runs", "0", "--keep-days", "0", "--force"])
    assert rc == 0
    assert called["n"] == 0
    assert not course.exists()


def test_dry_run_previews_uncovered_without_touching(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    mod = _load()
    course, junk = _fixture(tmp_path, mod, monkeypatch)
    called = {"n": 0}

    def _count() -> tuple[bool, str]:
        called["n"] += 1
        return True, ""

    monkeypatch.setattr(mod, "transload", _count)
    rc = mod.main(["--keep-runs", "0", "--keep-days", "0"])
    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert any(line.startswith("[tmp-clean] 镜像预览") for line in lines)
    assert course.is_dir() and junk.exists()
    assert called["n"] == 0


def test_transload_calls_cli_with_unlimited_budget() -> None:
    mod = _load()
    seen: dict[str, list[str]] = {}

    def fake(cmd: list[str]) -> tuple[bool, str]:
        seen["cmd"] = cmd
        return True, ""

    ok, _detail = mod.transload(runner=fake)
    cmd = seen["cmd"]
    assert ok
    assert cmd[2:5] == ["sync", "--course", "*"]
    assert cmd[5:7] == ["--budget", "0"]
    assert cmd[1].endswith("course-metrics.ts")
