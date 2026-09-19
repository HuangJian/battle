"""tests/test_offline_boot.py —— 全离线 notebook 运行时（`remote/offline_boot.py`）。

离线模式与在线模式的差别全在「谁在什么时候把什么送过去」，所以钉的是四条性质：

  1. **取包两条源在同一个等待循环里轮**（hub 取 / 人手传）—— 先排完 hub 再等人，会让
     「传完之后又等满一个 hub 超时」这种事发生；
  2. **到点没包 ⇒ 响亮退出**，正文就是两条下一步（不猜、不静默重试、不半途当成功）；
  3. **回传开关决定 argv 形状**（有 hub ⇒ 补传腿；关掉 ⇒ `--no-deliver`）—— 这是
     「实时回传 / 跑完统一打包」唯一的分叉点，不能靠人读代码保持一致；
  4. **引导代码来自包里那份 `code.zip`**（云机上没有仓），交付物按 `deliver-<课>.zip`
     命名（控制台靠文件名对账课程，改名 = 把对账交给运气）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from remote import bundle as bundle_mod
from remote import offline_boot

PACK_INDEX = {
    "magic": bundle_mod.BUNDLE_MAGIC,
    "proto": bundle_mod.BUNDLE_PROTO,
    "run_id": "run-off1",
    "it": 3,
    "end_it": 8,
    "created_at": "2026-09-19 10:00:00",
    "commit": "c" * 40,
    "hub_url": "https://hub.example",
}


def _code_zip(py: str = "MARKER = 1\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("remote/_marker.py", py)
    return buf.getvalue()


def fake_pack(
    d: Path,
    *,
    course: str = "c5-gae",
    index: dict | None = None,
    code: bytes | None = None,
    with_code: bool = True,
    name: str | None = None,
) -> Path:
    """造一个**够用**的任务包（只有索引 + code.zip；其余件与本文件的被测性质无关）。"""
    idx = {**PACK_INDEX, **(index or {})}
    p = d / (name or f"task-{course}.zip")
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(bundle_mod.BUNDLE_INDEX, json.dumps(idx, ensure_ascii=False))
        if with_code:
            z.writestr(bundle_mod.CODE_NAME, code if code is not None else _code_zip())
    return p


def quiet(_msg: str) -> None:
    pass


# ────────────────────── 与导出侧（remote/bundle）对账 ──────────────────────


def test_constants_track_the_exporter() -> None:
    """索引名/代码件名/magic 各留一份常量（本模块不能 import remote），必须逐字同步。"""
    assert offline_boot.BUNDLE_INDEX == bundle_mod.BUNDLE_INDEX
    assert offline_boot.CODE_NAME == bundle_mod.CODE_NAME
    assert offline_boot.BUNDLE_MAGIC == bundle_mod.BUNDLE_MAGIC


def test_read_pack_index_accepts_a_pack_written_by_the_exporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真导出器写出来的包，我们的读索引必须认得（常量对齐的**行为**证明，不只是等值）。

    `normalize_manifest` 是唯一的拦路虎（它要求完整血缘），这里只替换它——导出的其余
    每一步（task.json 的字段、code.zip 的摆放、zip 结构）都走真代码。
    """
    plan = json.dumps({"start_it": 3, "end_it": 8}).encode("utf-8")
    from remote.artifacts import sha256_bytes

    manifest = {
        "kind": "run",
        "runId": "run-off1",
        "it": 3,
        "plan_sha256": sha256_bytes(plan),
        "commit": "c" * 40,
    }
    monkeypatch.setattr(bundle_mod, "normalize_manifest", lambda m: m)  # type: ignore[arg-type]
    w = tmp_path / "w"
    w.mkdir()
    (w / "init_weights.json").write_bytes(b"w")
    (w / "code.zip").write_bytes(_code_zip())
    with zipfile.ZipFile(w / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/x.ts", "// ts\n")
    out = tmp_path / "task-c5-gae.zip"
    bundle_mod.export_bundle(
        out,
        manifest=manifest,
        plan_bytes=plan,
        init_weights_path=w / "init_weights.json",
        code_zip_path=w / "code.zip",
        ts_code_zip_path=w / "ts_code.zip",
    )
    idx = offline_boot.read_pack_index(out)
    assert idx["magic"] == bundle_mod.BUNDLE_MAGIC
    assert idx["run_id"] == "run-off1" and idx["it"] == 3 and idx["end_it"] == 8
    assert offline_boot.is_task_pack(out) is True
    assert offline_boot.course_from_pack_name(out) == "c5-gae"


def test_read_pack_index_rejects_foreign_and_broken_zips(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(ValueError, match="不是任务包"):
        offline_boot.read_pack_index(junk)
    assert offline_boot.is_task_pack(junk) is False

    wrong_magic = fake_pack(tmp_path / "a", index={"magic": "something-else"})
    with pytest.raises(ValueError, match="身份不符"):
        offline_boot.read_pack_index(wrong_magic)

    not_zip = tmp_path / "b" / "task-x.zip"
    not_zip.parent.mkdir(parents=True, exist_ok=True)
    not_zip.write_bytes(b"not a zip at all")
    assert offline_boot.is_task_pack(not_zip) is False


def test_module_level_code_does_not_need_the_remote_package() -> None:
    """拿到包**之前** `remote` 还不在 sys.path 上——本模块顶层不得 import 它（剥注释后扫）。"""
    src = Path(offline_boot.__file__).read_text(encoding="utf-8")
    bad = [
        ln
        for ln in src.splitlines()
        if (ln.startswith("import remote") or ln.startswith("from remote"))
        and not ln.startswith("#")
    ]
    assert not bad, f"顶层 import 了 remote（拿包前不存在）: {bad}"


def test_module_imports_without_remote_on_sys_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """比源码守卫更强的版本：真的在没有 `remote` 的 sys.path 上 import 一次。

    用一个空目录当仓库根、把 nn-training 从 sys.path 摘掉再 import 本文件——它能过，
    才说明「先有包才有代码」这条时序真的成立。
    """
    here = Path(offline_boot.__file__).resolve()
    name = "_offline_boot_standalone"
    spec = importlib.util.spec_from_file_location(name, here)
    assert spec is not None and spec.loader is not None
    saved = list(sys.path)
    saved_mods = {k: sys.modules.pop(k) for k in list(sys.modules) if k.startswith("remote")}
    try:
        sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != here.parent.parent]
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # 只要顶层不 import remote 就能过
        assert mod.CODE_DIR == offline_boot.CODE_DIR
    finally:
        sys.path[:] = saved
        sys.modules.update(saved_mods)


# ────────────────────── 取包：两条源、一个循环 ──────────────────────


def test_find_uploaded_pack_prefers_the_newest_task_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = tmp_path / "up"
    old = fake_pack(up, course="c5-gae", index={"run_id": "old"})
    junk = up / "notes.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("x.txt", "x")
    import os
    import time

    os.utime(old, (time.time() - 600, time.time() - 600))
    new = fake_pack(up, course="c6-ppo", index={"run_id": "new"})
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(up),))
    got = offline_boot.find_uploaded_pack({}, quiet)
    assert got is not None and got.resolve() == new.resolve(), "该挑最新的那个包（人刚传上来的）"


def test_find_uploaded_pack_honours_explicit_task_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "nothing"),))
    p = fake_pack(tmp_path / "d", course="c5-gae")
    assert offline_boot.find_uploaded_pack({"task_zip": str(p)}, quiet) == p
    # 指向不存在的路径 → 退回扫落点（而不是直接报错，用户可能只是路径打错了）
    assert offline_boot.find_uploaded_pack({"task_zip": str(tmp_path / "nope.zip")}, quiet) is None


def test_obtain_pack_uses_explicit_zip_without_touching_the_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    p = fake_pack(tmp_path / "d", course="c5-gae")

    def boom(*a, **k):
        raise AssertionError("显式给了 task_zip 就不该去碰 hub")

    monkeypatch.setattr(offline_boot, "probe_hub", boom)
    got = offline_boot.obtain_pack(
        {"course": "c5-gae", "task_zip": str(p)}, {}, quiet, tmp_path / "w"
    )
    assert got == p


def test_obtain_pack_falls_back_to_upload_when_the_hub_has_no_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hub 在线但还没导出 ⇒ 同一轮里继续看落点；人一传上来就开跑（不再等 hub）。"""
    up = tmp_path / "up"
    up.mkdir()
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(up),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub.invalid"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **k: True)
    calls = {"n": 0}

    def fetch(hub, token, course, dest_dir, log, timeout=0.0):
        calls["n"] += 1
        if calls["n"] == 1:
            fake_pack(up, course=course)  # 模拟「用户在这期间把包传上来了」
        log("hub 上还没有包")
        return None

    monkeypatch.setattr(offline_boot, "fetch_task_pack", fetch)
    got = offline_boot.obtain_pack(
        {"course": "c5-gae", "wait_pack_sec": 5, "poll_sec": 0.05, "prompt_upload": False},
        {"HUB_TOKEN": "t"},
        quiet,
        tmp_path / "w",
    )
    assert got is not None and got.name == "task-c5-gae.zip"


def test_obtain_pack_from_hub_writes_into_the_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "w"
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub.invalid"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **k: True)
    real = fake_pack(tmp_path / "src", course="c5-gae")
    monkeypatch.setattr(
        offline_boot, "fetch_task_pack", lambda hub, tok, course, dest, log, timeout=0.0: _copy(real, dest)
    )
    got = offline_boot.obtain_pack({"course": "c5-gae", "wait_pack_sec": 1}, {}, quiet, work)
    assert got is not None and got.parent == work and offline_boot.is_task_pack(got)


def _copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    dest.write_bytes(src.read_bytes())
    return dest


def test_obtain_pack_without_hub_or_upload_exits_with_both_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: [])
    with pytest.raises(SystemExit) as ei:
        offline_boot.obtain_pack(
            {"course": "c5-gae", "wait_pack_sec": 0, "prompt_upload": False}, {}, quiet, tmp_path / "w"
        )
    msg = str(ei.value.code)
    assert "导出任务包" in msg and "task-<课>.zip" in msg, f"两条下一步都要写清楚，实际：{msg}"


def test_obtain_pack_aborts_on_halt_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: [])
    stop = threading.Event()
    stop.set()
    with pytest.raises(SystemExit, match="停机信号"):
        offline_boot.obtain_pack(
            {"course": "c5-gae", "wait_pack_sec": 0, "prompt_upload": False}, {}, quiet, tmp_path / "w", stop=stop
        )


def test_hub_candidates_prefers_cfg_url_and_ignores_placeholder() -> None:
    assert offline_boot.hub_candidates({"hub_url": "http://<本地TS_IP>:8787"}, {}) == []
    assert offline_boot.hub_candidates({"hub_url": "https://x.trycloudflare.com/"}, {}) == [
        "https://x.trycloudflare.com"
    ]
    got = offline_boot.hub_candidates({"hub_port": 9999}, {"HUB_IP": "100.64.0.5"})
    assert got == ["http://100.64.0.5:9999"]


# ────────────────────── 回传开关 → argv ──────────────────────


def test_build_run_argv_live_backfeed_uses_the_deliver_leg(tmp_path: Path) -> None:
    argv = offline_boot.build_run_argv(
        {
            "course": "c5-gae",
            "device": "cuda",
            "live_backfeed": True,
            "budget_sec": 3600,
            "max_iters": 3,
            "threads": 8,
        },
        tmp_path / "task-c5-gae.zip",
        tmp_path / "run",
        "https://hub.example",
        "/tmp/hub.token",
    )
    assert argv[:4] == ["--bundle", str(tmp_path / "task-c5-gae.zip"), "--artifacts", str(tmp_path / "run")]
    assert "--hub-url" in argv and argv[argv.index("--hub-url") + 1] == "https://hub.example"
    assert "--hub-token-file" in argv and "--no-deliver" not in argv
    assert argv[argv.index("--budget-sec") + 1] == "3600.0"
    # 归位键：多课程 hub 的补传靠它落进本课（缺了就 400「无法归属课程」）
    assert argv[argv.index("--hub-course") + 1] == "c5-gae"


def test_build_run_argv_backfeed_carries_the_course(tmp_path: Path) -> None:
    """开回传 ⇒ argv 带 `--hub-course <CFG.course>`；纯离线则**不带**（不需要也不该有）。"""
    cfg = {"course": "c5-gae", "live_backfeed": True}
    live = offline_boot.build_run_argv(cfg, tmp_path / "t.zip", tmp_path / "run", "h", "f")
    assert live[live.index("--hub-course") + 1] == "c5-gae"
    pure = offline_boot.build_run_argv(
        {**cfg, "live_backfeed": False}, tmp_path / "t.zip", tmp_path / "run", "h", "f"
    )
    assert "--hub-course" not in pure
    # CFG 里没填课程（cell 会在 run() 里响亮拒跑）⇒ 不给空串形参，宁可不带
    no_course = offline_boot.build_run_argv(
        {"live_backfeed": True}, tmp_path / "t.zip", tmp_path / "run", "h", "f"
    )
    assert "--hub-course" not in no_course


def test_build_run_argv_offline_packaging_switch(tmp_path: Path) -> None:
    """关掉回传（或够不着 hub）⇒ 显式 `--no-deliver`：跑完统一打包，手动导入。"""
    for cfg, hub, tok in (
        ({"live_backfeed": False}, "https://hub.example", "/tmp/hub.token"),
        ({"live_backfeed": True}, "", ""),  # 回传开着但没 hub —— 同样纯离线
    ):
        argv = offline_boot.build_run_argv(cfg, tmp_path / "t.zip", tmp_path / "run", hub, tok)
        assert "--no-deliver" in argv and "--hub-url" not in argv
    default_on = offline_boot.build_run_argv({}, tmp_path / "t.zip", tmp_path / "run", "h", "f")
    assert "--no-deliver" not in default_on, "缺省是实时回传（用户口径）"


# ────────────────────── 引导 / 交付物 ──────────────────────


def test_ensure_code_lays_the_pack_code_on_sys_path(tmp_path: Path) -> None:
    pack = fake_pack(tmp_path / "d", course="c5-gae", code=_code_zip("VALUE = 42\n"))
    code_dir = tmp_path / "code"
    try:
        got = offline_boot.ensure_code(pack, quiet, code_dir=str(code_dir))
        assert got == code_dir and (code_dir / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 42\n"
        assert str(code_dir) in sys.path
    finally:
        if str(code_dir) in sys.path:
            sys.path.remove(str(code_dir))


def test_ensure_code_fails_loudly_when_code_zip_is_missing(tmp_path: Path) -> None:
    pack = fake_pack(tmp_path / "d", course="c5-gae", with_code=False)
    with pytest.raises(SystemExit, match=r"code\.zip"):
        offline_boot.ensure_code(pack, quiet, code_dir=str(tmp_path / "code"))


def test_package_deliverable_names_the_zip_after_the_course(tmp_path: Path) -> None:
    art = tmp_path / "run"
    art.mkdir()
    (art / "artifacts.zip").write_bytes(b"zip-bytes")
    out = tmp_path / "out"
    got = offline_boot.package_deliverable(art, "c5-gae", out, quiet)
    assert got == out / "deliver-c5-gae.zip" and got.read_bytes() == b"zip-bytes"
    logs: list[str] = []
    assert offline_boot.package_deliverable(tmp_path / "nope", "c5-gae", out, logs.append) is None
    assert any("没找到" in m for m in logs)


def test_write_token_file_is_owner_only(tmp_path: Path) -> None:
    import os

    p = offline_boot.write_token_file(tmp_path, "s3cr3t")
    assert Path(p).read_text(encoding="utf-8") == "s3cr3t"
    if os.name == "posix":
        assert (Path(p).stat().st_mode & 0o777) == 0o600
    assert offline_boot.write_token_file(tmp_path, "") == ""


def test_course_from_pack_name_only_speaks_when_it_is_sure(tmp_path: Path) -> None:
    assert offline_boot.course_from_pack_name("task-c5-gae.zip") == "c5-gae"
    assert offline_boot.course_from_pack_name(tmp_path / "deliver-c5-gae.zip") == ""
    assert offline_boot.course_from_pack_name("task-.zip") == ""
