"""本机产物优先（plan/offline-rerun-local-first §3/§4.1，2026-09-24）。

用户口径：「停止 cell 后再 run，应该要能接着机器上已经跑过的 it 继续跑，而不是从 hub 取
（可能过时的）任务包」。这个文件钉的就是那条判定表：

  * 三件齐全（`state.json` + `plan.json` + `manifest.json`）⇒ **本机优先**：argv **不带**
    `--bundle`（包不参与，plan/manifest/代码/TS 全从产物目录读）；
  * 取包变成**可选**（`obtain_pack(optional=True)`：只试一次、取不到返回 `None`、不抛）；
  * 代码/TS「跟着产物走」：产物目录优先、逐候选过 manifest 的 sha、对不上就**响亮拒**
    （绝不静默换代码，也不覆盖）；
  * `CFG.force_pack` / `CFG.task_zip` = 显式老行为（包覆盖）。

为什么要一个独立文件（而不是塞回 `test_offline_boot.py`）：这条路的判据是**跨文件的**——
offline_boot 的决定 + run_loop 的 `--artifacts` 入口 + bundle 的导入语义三者必须同时成立，
写在一起才能一眼看出「谁在保证什么」。
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

from remote import offline_boot


def _quiet(_msg: str) -> None:
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _zip_bytes(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


CODE_A = _zip_bytes("remote/_marker.py", "VALUE = 'A'\n")
CODE_B = _zip_bytes("remote/_marker.py", "VALUE = 'B'\n")
TS_A = _zip_bytes("tools/sim/export-eval-game.ts", "// A\n")
TS_B = _zip_bytes("tools/sim/export-eval-game.ts", "// B\n")

PACK_INDEX = {
    "magic": "battle2-task-bundle",
    "proto": 1,
    "run_id": "run-pack",
    "it": 1,
    "end_it": 9,
    "created_at": "2026-09-24 10:00:00",
    "commit": "c" * 40,
    "plan_sha256": "p" * 64,
}


def _pack(path: Path, *, code: bytes = CODE_A, ts: bytes | None = TS_A, index: dict | None = None) -> Path:
    """一个够用的任务包（索引 + code.zip [+ ts_code.zip]）——成员名走真常量。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(offline_boot.BUNDLE_INDEX, json.dumps({**PACK_INDEX, **(index or {})}))
        z.writestr(offline_boot.CODE_NAME, code)
        if ts is not None:
            z.writestr(offline_boot.TS_CODE_NAME, ts)
    return path


def _artifacts(
    dest: Path,
    *,
    last_it: int = 3,
    start_it: int = 1,
    end_it: int = 9,
    code: bytes = CODE_A,
    ts: bytes = TS_A,
    run_id: str = "run-local",
    plan_sha256: str = "l" * 64,
    with_plan: bool = True,
    with_manifest: bool = True,
) -> Path:
    """一个**三件齐全**的产物目录（缺件用 `with_*` 造半截目录）。"""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / offline_boot.STATE_NAME).write_text(
        json.dumps(
            {"run_id": run_id, "plan_sha256": plan_sha256, "state": "running", "last_it": last_it}
        ),
        encoding="utf-8",
    )
    if with_plan:
        (dest / offline_boot.PLAN_NAME).write_text(
            json.dumps({"start_it": start_it, "end_it": end_it}), encoding="utf-8"
        )
    if with_manifest:
        (dest / offline_boot.MANIFEST_NAME).write_text(
            json.dumps({"code_sha256": _sha(code), "ts_code_sha256": _sha(ts)}), encoding="utf-8"
        )
    (dest / offline_boot.CODE_NAME).write_bytes(code)
    (dest / offline_boot.TS_CODE_NAME).write_bytes(ts)
    with zipfile.ZipFile(io.BytesIO(ts)) as z:
        z.extractall(dest / offline_boot.TS_TREE_NAME)
    return dest


# ────────────────────────── 常量对账（另一份抄写） ──────────────────────────


def test_module_constants_track_the_artifact_store() -> None:
    """产物目录的三个件名/TS 名在本模块各抄了一份（拿包之前不能 import remote）——必须同字。"""
    from common.protocol import TS_CODE_NAME
    from remote.artifacts import ArtifactStore
    from remote.bundle import CODE_NAME, TS_TREE_NAME

    assert offline_boot.STATE_NAME == ArtifactStore.STATE_NAME
    assert offline_boot.PLAN_NAME == ArtifactStore.PLAN_NAME
    assert offline_boot.MANIFEST_NAME == ArtifactStore.MANIFEST_NAME
    assert offline_boot.CODE_NAME == CODE_NAME
    assert offline_boot.TS_TREE_NAME == TS_TREE_NAME
    assert offline_boot.TS_CODE_NAME == TS_CODE_NAME


# ────────────────────────── 本机产物判据 ──────────────────────────


def test_local_artifacts_requires_all_three(tmp_path: Path) -> None:
    dest = _artifacts(tmp_path / "run")
    got = offline_boot.local_artifacts(dest)
    assert got is not None
    assert (got["last_it"], got["start_it"], got["end_it"]) == (3, 1, 9)
    assert got["code_sha256"] == _sha(CODE_A) and got["ts_code_sha256"] == _sha(TS_A)

    # 缺计划 / 缺清单 / 缺状态：都不算「有产物」（半截目录另走响亮拒，见下）
    assert offline_boot.local_artifacts(_artifacts(tmp_path / "a", with_plan=False)) is None
    assert offline_boot.local_artifacts(_artifacts(tmp_path / "b", with_manifest=False)) is None
    assert offline_boot.local_artifacts(tmp_path / "nope") is None


def test_local_artifacts_rejects_a_garbage_state(tmp_path: Path) -> None:
    dest = _artifacts(tmp_path / "run")
    (dest / offline_boot.STATE_NAME).write_text("not json", encoding="utf-8")
    assert offline_boot.local_artifacts(dest) is None


def test_describe_local_vs_pack_flags_a_different_segment() -> None:
    local = {"last_it": 7, "start_it": 1, "end_it": 9, "plan_sha256": "a" * 64}
    same = offline_boot.describe_local_vs_pack(local, {"it": 1, "end_it": 9, "plan_sha256": "a" * 64})
    assert "同一段" in same and "不是同一段" not in same
    diff = offline_boot.describe_local_vs_pack(local, {"it": 1, "end_it": 40, "plan_sha256": "b" * 64})
    assert "不是同一段" in diff, "异段必须响亮标注（不许静默换段）"
    assert "it7" in diff and "it40" in diff


# ────────────────────────── argv：本机优先不带 --bundle ──────────────────────────


def test_build_run_argv_local_first_drops_the_bundle(tmp_path: Path) -> None:
    dest = tmp_path / "run"
    argv = offline_boot.build_run_argv(
        {"course": "c5-gae"}, tmp_path / "task-c5-gae.zip", dest, "h", "f", local_first=True
    )
    assert "--bundle" not in argv, "本机优先 ⇒ 包不参与（plan/manifest/代码/TS 全从产物目录读）"
    assert argv[:2] == ["--artifacts", str(dest)]

    # 没拿到包（optional 取失败）同样只走 --artifacts
    none_pack = offline_boot.build_run_argv({"course": "c5-gae"}, None, dest, "h", "f")
    assert none_pack[:2] == ["--artifacts", str(dest)] and "--bundle" not in none_pack

    # 老行为逐字不变（有包 + 不本机优先）
    legacy = offline_boot.build_run_argv({"course": "c5-gae"}, tmp_path / "t.zip", dest, "h", "f")
    assert legacy[:4] == ["--bundle", str(tmp_path / "t.zip"), "--artifacts", str(dest)]


# ────────────────────────── 代码/TS 跟着产物走 ──────────────────────────


def _code_dir(tmp_path: Path) -> str:
    return str(tmp_path / "code")


def test_ensure_code_prefers_the_artifacts_copy(tmp_path: Path) -> None:
    """产物目录里那份与 manifest 相符 ⇒ 用它（包是**备源**，不抢）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_B)  # 包是另一个 commit
    logs: list[str] = []
    try:
        got = offline_boot.ensure_code(pack, logs.append, _code_dir(tmp_path), dest=dest)
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'A'\n"
        assert any("本机产物" in m for m in logs), logs
    finally:
        sys.path[:] = [p for p in sys.path if p != _code_dir(tmp_path)]


def test_ensure_code_falls_back_to_the_pack_and_repairs_the_artifacts(tmp_path: Path) -> None:
    """产物目录那份**坏了**（与 manifest 不符）⇒ 用包里同 sha 的副本修复它。

    为什么必须修：`run_loop` 读的是产物目录那份（`_read_opt_file(root, "code.zip")`），
    不修就会在 worker 侧报「传输损坏」——一条指向错误原因的报错。
    """
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A)
    (dest / offline_boot.CODE_NAME).write_bytes(CODE_B)  # 现盘坏了
    logs: list[str] = []
    try:
        offline_boot.ensure_code(pack, logs.append, _code_dir(tmp_path), dest=dest)
        assert (dest / offline_boot.CODE_NAME).read_bytes() == CODE_A
        assert any("修复" in m for m in logs), logs
    finally:
        sys.path[:] = [p for p in sys.path if p != _code_dir(tmp_path)]


def test_ensure_code_refuses_and_writes_nothing_when_no_candidate_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部候选都对不上 ⇒ 响亮拒且**不写任何东西**（spy：`extractall` 一次都没被调用）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    (dest / offline_boot.CODE_NAME).write_bytes(CODE_B)  # 本机那份也不符
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=_zip_bytes("remote/_m.py", "VALUE = 'C'\n"))
    calls: list[str] = []

    class _SpyZip(zipfile.ZipFile):
        def extractall(self, *a, **k):  # type: ignore[no-untyped-def]
            calls.append("extractall")
            return super().extractall(*a, **k)

    code_dir = _code_dir(tmp_path)
    monkeypatch.setattr(zipfile, "ZipFile", _SpyZip)
    with pytest.raises(SystemExit) as ei:
        offline_boot.ensure_code(pack, _quiet, code_dir, dest=dest)
    msg = str(ei.value.code)
    assert "对不上" in msg and "task_zip" in msg and "清空重跑" in msg, msg
    assert calls == [], "拒不匹配的代码时不得解包（不写 CODE_DIR）"
    assert not Path(code_dir).exists()


def test_ensure_code_says_so_when_there_is_no_source_at_all(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        offline_boot.ensure_code(None, _quiet, _code_dir(tmp_path), dest=tmp_path / "run")
    assert "没有可用的 code.zip" in str(ei.value.code)


def test_ensure_ts_tree_uses_the_artifacts_tree_without_unpacking(tmp_path: Path) -> None:
    dest = _artifacts(tmp_path / "run")
    (dest / offline_boot.TS_CODE_NAME).unlink()  # 只有树、没有 zip：照样直接用它
    logs: list[str] = []
    offline_boot.ensure_ts_tree(None, logs.append, dest=dest)
    assert any("不重解" in m for m in logs), logs
    assert not (dest / offline_boot.TS_CODE_NAME).exists(), "已有的树不该被重解覆盖"


def test_ensure_ts_tree_fills_from_the_pack_when_the_tree_is_gone(tmp_path: Path) -> None:
    dest = _artifacts(tmp_path / "run")
    import shutil

    shutil.rmtree(dest / offline_boot.TS_TREE_NAME)
    (dest / offline_boot.TS_CODE_NAME).unlink()
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", ts=TS_A)
    offline_boot.ensure_ts_tree(pack, _quiet, dest=dest)
    assert (dest / offline_boot.TS_CODE_NAME).read_bytes() == TS_A
    assert (dest / offline_boot.TS_TREE_NAME / "tools" / "sim" / "export-eval-game.ts").is_file()


def test_ensure_ts_tree_fills_a_dest_that_does_not_exist_yet(tmp_path: Path) -> None:
    """★ 现场回归（2026-09-25 云机实测）：**首次跑**时产物目录还不存在（它由 `run_loop` 建）。

    报障原文：`代码就位: /tmp/worker-code（…来源 任务包 …）` 之后当场
    `未捕获异常 FileNotFoundError: .../battle-offline/x20-dodge-l1/run/ts_code.zip` ——
    整个 cell 死在补 TS 运行时那一步（写 zip 时父目录还没有）。判据：不抛、目录被建出来、
    树也铺好（否则 `run_standalone` 的 `ensure_ts_cache_layout` 找不到运行时）。
    """
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", ts=TS_A)
    dest = tmp_path / "w" / "run"
    assert not dest.exists(), "本用例的前提：全新一跑（产物目录还没有）"
    offline_boot.ensure_ts_tree(pack, _quiet, dest=dest)
    assert (dest / offline_boot.TS_CODE_NAME).read_bytes() == TS_A
    assert (dest / offline_boot.TS_TREE_NAME / "tools" / "sim" / "export-eval-game.ts").is_file()


def test_ensure_ts_tree_refuses_a_mismatched_pack(tmp_path: Path) -> None:
    """「本机 manifest + 包里的 TS」= 混血（rollout 与权重血统不符）⇒ 拒跑。"""
    dest = _artifacts(tmp_path / "run", ts=TS_A)
    import shutil

    shutil.rmtree(dest / offline_boot.TS_TREE_NAME)
    (dest / offline_boot.TS_CODE_NAME).unlink()
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", ts=TS_B)
    with pytest.raises(SystemExit, match="TS 运行时与本机产物对不上"):
        offline_boot.ensure_ts_tree(pack, _quiet, dest=dest)


# ────────────────────────── run_one_course：判定表 ──────────────────────────


def _run_course(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cfg: dict | None = None,
    pack: Path | None = None,
    obtain_kwargs: dict | None = None,
) -> tuple[list[str], list[tuple[str, bool]]]:
    """跑一次 `run_one_course`（run_loop 注入 + 代码/TS 用 stub），返回 (argv, 调用记录)。"""
    calls: list[tuple[str, bool]] = []

    def fake_obtain(cfg2, creds, log, work, stop=None, **kw):
        calls.append(("obtain_pack", bool(kw.get("optional"))))
        return pack

    def fake_code(p, log, code_dir=None, **kw):
        calls.append(("ensure_code", bool(kw.get("dest"))))

    def fake_ts(p, log, **kw):
        calls.append(("ensure_ts_tree", True))

    monkeypatch.setattr(offline_boot, "obtain_pack", fake_obtain)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg2, creds: [])
    monkeypatch.setattr(offline_boot, "ensure_code", fake_code)
    monkeypatch.setattr(offline_boot, "ensure_ts_tree", fake_ts)
    seen: list[str] = []

    def fake_run_loop(argv: list[str]) -> int:
        seen.extend(argv)
        return 0
    base = {
        "course": "c5-gae",
        "work_dir": str(tmp_path / "w"),
        "device": "cpu",
        "live_backfeed": False,
        **(cfg or {}),
    }
    rc = offline_boot.run_one_course(
        base, {}, _quiet, None, course="c5-gae", multi=False, run_loop_main=fake_run_loop
    )
    assert rc == 0
    return seen, calls


def test_run_one_course_local_first_never_imports_the_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = _artifacts(tmp_path / "w" / "run")
    assert dest.is_dir()
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A, index={"it": 1, "end_it": 9})
    argv, calls = _run_course(tmp_path, monkeypatch, pack=pack)
    assert "--bundle" not in argv, argv
    assert argv[:2] == ["--artifacts", str(dest)]
    assert ("obtain_pack", True) in calls, "本机优先时取包必须是 optional（不等、不抛）"


def test_run_one_course_local_first_runs_without_any_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1 的硬判据：取不到包（optional ⇒ None）也照样起跑。"""
    _artifacts(tmp_path / "w" / "run")
    argv, calls = _run_course(tmp_path, monkeypatch, pack=None)
    assert argv[:2] == ["--artifacts", str(tmp_path / "w" / "run")]
    assert "--bundle" not in argv


def test_run_one_course_force_pack_takes_the_legacy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CFG.force_pack=true` = 显式老行为：包覆盖本机计划/清单（走 `--bundle`）。"""
    _artifacts(tmp_path / "w" / "run")
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A)
    argv, calls = _run_course(tmp_path, monkeypatch, cfg={"force_pack": True}, pack=pack)
    assert argv[:2] == ["--bundle", str(pack)]
    assert ("obtain_pack", False) in calls, "老行为下取包仍是硬前置（取不到就该响亮报错）"


def test_run_one_course_explicit_task_zip_also_forces_the_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CFG.task_zip` 与 `force_pack` 同义（用户显式指了一个包 ⇒ 就是它）。"""
    _artifacts(tmp_path / "w" / "run")
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A)
    argv, _calls = _run_course(
        tmp_path, monkeypatch, cfg={"task_zip": str(pack)}, pack=pack
    )
    assert argv[:2] == ["--bundle", str(pack)]


def test_run_one_course_refuses_a_half_broken_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有 `state.json` 而缺计划/清单：让包补齐会重置本机进度 ⇒ 响亮拒（列出两条出路）。"""
    dest = _artifacts(tmp_path / "w" / "run", with_plan=False)
    (dest / offline_boot.PLAN_NAME).unlink(missing_ok=True)
    with pytest.raises(SystemExit) as ei:
        _run_course(tmp_path, monkeypatch, pack=_pack(tmp_path / "d" / "t.zip"))
    msg = str(ei.value.code)
    assert "不完整" in msg and "清空" in msg, msg


def test_run_one_course_pure_offline_does_not_name_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`live_backfeed=False`（或够不着 hub）时 `tok_file` 曾经从未赋值 ⇒ NameError。"""
    _artifacts(tmp_path / "w" / "run")
    argv, _calls = _run_course(tmp_path, monkeypatch, cfg={"live_backfeed": False})
    assert "--no-deliver" in argv


# ────────────────────────── obtain_pack(optional=True) ──────────────────────────


def test_obtain_pack_optional_returns_none_instead_of_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机优先：只试一次、不等、不弹上传框、**不 SystemExit**。"""
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: [])
    monkeypatch.setattr(
        offline_boot, "prompt_upload", lambda log: pytest.fail("optional 模式不该弹上传框")
    )
    logs: list[str] = []
    got = offline_boot.obtain_pack(
        {"course": "c5-gae", "wait_pack_sec": 1800}, {}, logs.append, tmp_path / "w", optional=True
    )
    assert got is None
    assert any("只试一次" in m for m in logs), logs
    assert any("没取到包" in m for m in logs), logs


def test_obtain_pack_optional_still_uses_the_hub_when_it_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """能拿到就拿（包是代码/TS 的备源），但只问一次。"""
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub.invalid"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **k: True)
    n = {"calls": 0}

    def fetch(hub, token, course, dest_dir, log, timeout=0.0):
        n["calls"] += 1
        return _pack(Path(dest_dir) / f"task-{course}.zip")

    monkeypatch.setattr(offline_boot, "fetch_task_pack", fetch)
    got = offline_boot.obtain_pack(
        {"course": "c5-gae", "wait_pack_sec": 1800}, {}, _quiet, tmp_path / "w", optional=True
    )
    assert got is not None and got.name == "task-c5-gae.zip"
    assert n["calls"] == 1, "optional 模式只该问一次"

# ------------------------------------------------- X1：取不到包**跳过**，配置错误照旧**立即停**
#
# 评审 X1：plan 1（切离线自动导包）说「全被跳过 ⇒ 非零退出」，plan 2（向 hub 问清单）说
# 「清单为空 = 正常收工」——两句都对，但**必须按来源分开**：CFG 点名要跑的课取不到包是
# 异常（响亮），队伍里本来就没活干是正常（安静）。这里钉前一半。


def _run_many(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    courses: list[str],
    outcomes: list[object],
    lines: list[str] | None = None,
) -> tuple[int, list[str], list[str]]:
    """跑一次 `run()`（`run_one_course` 换成给定结局：异常对象 = 抛，int = rc）。

    返回 `(rc, 被点到的课, 日志行)`；**不吞 `SystemExit`**——要钉「响亮失败」的用例自己套
    `pytest.raises`，并把 `lines` 传进来（异常不带走日志）。
    """
    seen: list[str] = []
    it = iter(outcomes)

    def fake_one(cfg, creds, log, stop, course=None, multi=False):
        seen.append(str(course))
        nxt = next(it)
        if isinstance(nxt, BaseException):
            raise nxt
        assert isinstance(nxt, int), nxt  # 结局只有两类：异常对象 / rc
        return nxt

    monkeypatch.setattr(offline_boot, "courses_of", lambda cfg: list(courses))
    monkeypatch.setattr(offline_boot, "run_one_course", fake_one)
    bag = lines if lines is not None else []
    rc = offline_boot.run({"course": list(courses)}, bag.append, lambda _k, _d=None: "", None)
    return rc, seen, bag


def test_run_skips_a_course_without_a_pack_and_keeps_going(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一门取不到包 ⇒ 继续第二门，末尾汇总点名 + **非零 rc**（跳过不算跑完）。"""
    rc, seen, lines = _run_many(
        tmp_path, monkeypatch, ["a", "b"], [offline_boot.PackUnavailableError("没包"), 0]
    )
    assert rc == 1, lines
    assert seen == ["a", "b"], "取不到包的课不该吃掉后面那门"
    joined = "\n".join(lines)
    assert "课程 a 跳过（取不到任务包）" in joined
    assert "本会话完成 1 门 / 跳过 1 门" in joined and "a" in joined


def test_run_all_skipped_is_a_loud_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一门都没跑 ⇒ 响亮失败（**不谎报成功**），且把课名点出来 + 两条路（hub 取包 / 手动送包）。"""
    with pytest.raises(SystemExit) as ei:
        _run_many(
            tmp_path,
            monkeypatch,
            ["a", "b"],
            [offline_boot.PackUnavailableError("没包"), offline_boot.PackUnavailableError("没包")],
        )
    msg = str(ei.value)
    assert "没有任何一门课拿到任务包" in msg and "a" in msg and "b" in msg
    assert "导出任务包" in msg and "task_zip" in msg, "光说失败不够：要给人两条下一步"


def test_run_still_stops_at_a_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """课程名不一致/产物目录不完整这类 `SystemExit` 照旧**立即停**（继续跑没有意义）。

    判据用日志拿：把那门课点名为「未跑完 + 剩余列表」，且**没有**给它印「跳过」那一行。
    """
    lines: list[str] = []
    with pytest.raises(SystemExit):
        _run_many(tmp_path, monkeypatch, ["a", "b"], [SystemExit("课程名不一致"), 0], lines)
    joined = "\n".join(lines)
    assert "课程 a 未跑完（课程名不一致）" in joined and "剩余 1 门课未执行：b" in joined
    assert "跳过" not in joined, "配置错误不是「跳过」——混了会让人以为课被安静地丢了"


def test_run_stops_at_a_failed_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """训练真失败（rc≠0）⇒ 停在当场、把剩余课列出来（与「跳过」区分开）。"""
    rc, seen, lines = _run_many(tmp_path, monkeypatch, ["a", "b"], [3, 0])
    assert rc == 3 and seen == ["a"]
    assert "课程 a 退出 rc=3" in "\n".join(lines)


# ------------------------------------------------- F2/S3：hub 的正文只能读一次、诊断要进日志


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    import email.message

    return urllib.error.HTTPError(
        "http://hub/offline/task-pack", code, "err", email.message.Message(), io.BytesIO(body)
    )


class _RaisingFetcher:
    """`_load_tailscale_boot()` 的替身：`fetch_guarded` 直接抛（把护栏换成一次固定应答）。"""

    def __init__(self, err: BaseException) -> None:
        self._err = err

    def fetch_guarded(self, *_a: object, **_kw: object) -> bytes:
        raise self._err


def test_http_error_parts_reads_the_body_exactly_once() -> None:
    """F2：HTTPError 的 fp **读完即空** ⇒ 正文与字段必须从同一份 bytes 出。

    这条断言钉的是**危险本身**：第二次读只能拿到「（空正文）」，所以现场那条「先打正文、
    再解析 `path`/`known_courses`」的写法会打出「hub 说：（空正文）」。
    """
    body = b'{"error":"no pack","path":"D:/tmp/c/task-c.zip","known_courses":["a"]}'
    err = _http_error(404, body)
    text, doc = offline_boot._http_error_parts(err)
    assert text == "no pack"
    assert doc["path"].endswith("task-c.zip") and doc["known_courses"] == ["a"]
    assert offline_boot._http_error_body(err) == "（空正文）"


def test_fetch_task_pack_logs_the_hub_diagnostics_on_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S3：404 的 `path`/`known_courses`/触发回执必须进日志（三条病因此前长得一模一样）。"""
    body = json.dumps(
        {
            "error": "没有任务包 task-c5-gae.zip——先在控制台「导出任务包」（随时可导，不必停训）",
            "course": "c5-gae",
            "path": "D:/traj/c5-gae/task-c5-gae.zip",
            "known_courses": ["c5-gae"],
            "triggered": True,
            "trigger_note": "已替你触发一次重导，稍后重试（导出约需数分钟）",
            "give_up": False,
        }
    ).encode("utf-8")
    monkeypatch.setattr(offline_boot, "_load_tailscale_boot", lambda: _RaisingFetcher(_http_error(404, body)))
    lines: list[str] = []
    got = offline_boot.fetch_task_pack("http://hub", "tok", "c5-gae", tmp_path, lines.append)
    assert got is None
    joined = "\n".join(lines)
    assert "hub 说：没有任务包" in joined
    assert "hub 找的落点：D:/traj/c5-gae/task-c5-gae.zip" in joined
    assert "含本门" in joined, "本门在 known_courses 里 ⇒ 病因是「没这个包」而不是「不认识这门课」"
    assert "已替你触发一次重导" in joined


def test_fetch_task_pack_flags_a_course_the_hub_does_not_know(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """反向判据：本门**不在** `known_courses` 里 ⇒ 日志必须指向「hub 还没发现这门课」。"""
    body = json.dumps(
        {"error": "没有任务包", "course": "c5-gae", "known_courses": ["other"], "triggered": False}
    ).encode("utf-8")
    monkeypatch.setattr(offline_boot, "_load_tailscale_boot", lambda: _RaisingFetcher(_http_error(404, body)))
    lines: list[str] = []
    offline_boot.fetch_task_pack("http://hub", "tok", "c5-gae", tmp_path, lines.append)
    joined = "\n".join(lines)
    assert "**没有本门**" in joined and "--traj-root" in joined


def test_fetch_task_pack_prints_the_409_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """409（过期）的正文就是下一步——只打一个数字等于把原因留在云机上。"""
    body = json.dumps({"error": "任务包已过期（起点权重 sha 不符）", "stale": True}).encode("utf-8")
    monkeypatch.setattr(offline_boot, "_load_tailscale_boot", lambda: _RaisingFetcher(_http_error(409, body)))
    lines: list[str] = []
    offline_boot.fetch_task_pack("http://hub", "tok", "c5-gae", tmp_path, lines.append)
    joined = "\n".join(lines)
    assert "409" in joined and "起点权重 sha 不符" in joined
