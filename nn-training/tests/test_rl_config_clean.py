"""`tools/rl_config_clean.py` 契约（plan/rl-config-cleanup.plan.md §4 S1/S2、§6.1）。

要钉住的性质：
  · **dry-run 零写盘**（sha256 + mtime 双验，且不产生备份）；
  · **备份先于删除**：`--apply` 先落 `.bak.<ts>` 且 sha 与源一致，再动原文件；
  · **只删白名单键**：未知键（未来的手写键）一个字不碰；
  · **脱敏**：`remote_token` / `nodes[].authKey` 原文不出现在任何输出里；
  · **结构残缺拒删**：缺 `policy`/`rl`/`nodes` ⇒ 不写坏唯一的开训入口；
  · **courses 只删点名条目**；
  · 矩阵纯函数：来源优先级（课程 > level > rl-config > 缺省）与「全绿」判定。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parent.parent


def _load() -> Any:
    """按**文件路径**加载 `tools/rl_config_clean.py`（`tools/` 无 `__init__.py`，同 course_compare 先例）。"""
    spec = importlib.util.spec_from_file_location(
        "_rl_config_clean_under_test", NN_ROOT / "tools" / "rl_config_clean.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


clean = _load()

TOKEN = "SECRET-TOKEN-do-not-print"
AUTH = "SECRET-AUTHKEY-do-not-print"


def _cfg() -> dict[str, Any]:
    return {
        "version": 1,
        "policy": {
            "taskTimeoutSec": 900,
            "minDiskFreeMB": 2048,
            "streamKlCapIntent": 0.5,
            "streamWaveGamesIntent": 200,
            "upgradeBranch": "",
        },
        "intent_rl": {"iters": 0, "bc": "tmp/intent-weights-Bp.json"},
        "nodes": [{"id": "self", "url": "http://127.0.0.1:8443", "authKey": AUTH}],
        "rl": {
            "hub_port": 8787,
            "mb": 512,
            "remote_token": TOKEN,
            "stream": 0,
            "double_buffer": 0,
            "precollect_early": 0,
        },
        "courses": {"a": {"rollout_src": "run"}, "b": {}},
        "future_handwritten_key": {"keep": True},
    }


def _write(tmp_path: Path, cfg: dict[str, Any]) -> Path:
    p = tmp_path / "rl-config.json"
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ 删除（纯）

def test_deletions_hit_only_the_whitelist() -> None:
    out = clean.apply_deletions(_cfg())
    assert "intent_rl" not in out
    assert "upgradeBranch" not in out["policy"]
    assert "minDiskFreeMB" not in out["policy"]
    assert "streamKlCapIntent" not in out["policy"]
    assert "streamWaveGamesIntent" not in out["policy"]
    for gone in ("stream", "double_buffer", "precollect_early"):
        assert gone not in out["rl"]
    # 非白名单：原样保留
    assert out["policy"]["taskTimeoutSec"] == 900
    assert out["rl"]["mb"] == 512
    assert out["nodes"] == _cfg()["nodes"]
    assert out["future_handwritten_key"] == {"keep": True}
    assert out["courses"] == _cfg()["courses"]


def test_drop_course_only_removes_named_entries() -> None:
    out = clean.apply_deletions(_cfg(), ["a"])
    assert "a" not in out["courses"] and "b" in out["courses"]


def test_incomplete_structure_is_refused() -> None:
    broken = {k: v for k, v in _cfg().items() if k != "nodes"}
    try:
        clean.apply_deletions(broken)
    except AssertionError as e:
        assert "nodes" in str(e)
    else:  # pragma: no cover
        raise AssertionError("结构残缺竟然通过了清洗")


# ------------------------------------------------------------------ 写盘 / 备份

def test_dry_run_writes_nothing(tmp_path: Path, capsys: Any) -> None:
    p = _write(tmp_path, _cfg())
    before, mtime = _sha(p), p.stat().st_mtime_ns
    rc = clean.main(["--config", str(p)])
    assert rc == 0
    assert _sha(p) == before and p.stat().st_mtime_ns == mtime
    assert not list(tmp_path.glob("*.bak.*")), "dry-run 不应产生备份"


def test_apply_backs_up_then_deletes(tmp_path: Path) -> None:
    p = _write(tmp_path, _cfg())
    original = _sha(p)
    rc = clean.main(["--config", str(p), "--apply"])
    assert rc == 0
    backups = list(tmp_path.glob("rl-config.json.bak.*"))
    assert len(backups) == 1
    assert _sha(backups[0]) == original, "备份必须先于删除、且 sha 与源一致"
    new = json.loads(p.read_text(encoding="utf-8"))
    assert "intent_rl" not in new and "stream" not in new["rl"]
    assert new["future_handwritten_key"] == {"keep": True}


def test_incomplete_structure_refuses_to_write(tmp_path: Path) -> None:
    broken = {k: v for k, v in _cfg().items() if k != "policy"}
    p = _write(tmp_path, broken)
    before = _sha(p)
    rc = clean.main(["--config", str(p), "--apply"])
    assert rc == 2
    assert _sha(p) == before
    assert not list(tmp_path.glob("*.bak.*"))


# ------------------------------------------------------------------ 脱敏

def test_secrets_never_appear_in_output(tmp_path: Path, capsys: Any) -> None:
    p = _write(tmp_path, _cfg())
    assert clean.main(["--config", str(p)]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out and AUTH not in out
    assert "len=" in out  # 只留长度占位


def test_redact_reports_length_only() -> None:
    assert clean.redact("abc") == "…（len=3）"
    assert clean.redact(12345) == "…"


# ------------------------------------------------------------------ 矩阵纯函数

def test_resolve_key_source_precedence() -> None:
    rl = {"rl": {"mb": 512}}
    # 课程 > level > rl-config > 缺省
    assert clean.resolve_key_source("mb", course_keys={"mb": 1024}, level_keys={"mb": 2048}, rl_cfg=rl) == (1024, "course")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={"mb": 2048}, rl_cfg=rl) == (2048, "level")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={}, rl_cfg=rl) == (512, "rl-config")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={}, rl_cfg={}) == (None, "default")


def test_is_green_requires_every_course_to_declare() -> None:
    assert clean.is_green("mb", ["course", "level"]) is True
    assert clean.is_green("mb", ["course", "rl-config"]) is False
    assert clean.is_green("mb", []) is False


def test_whitelist_paths_are_the_documented_set() -> None:
    """防止有人顺手往白名单里加键（那是「未知键一律删」的滑坡）。"""
    assert set(clean.DELETE_PATHS) == {
        "intent_rl",
        "policy.upgradeBranch",
        "policy.minDiskFreeMB",
        "policy.streamKlCapIntent",
        "policy.streamWaveGamesIntent",
        "rl.stream",
        "rl.double_buffer",
        "rl.precollect_early",
    }
