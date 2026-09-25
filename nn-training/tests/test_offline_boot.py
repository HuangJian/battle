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


def test_product_pack_names_track_the_artifact_store() -> None:
    """中途取回认的两个包名也是**抄的一份**（本模块不能 import remote）——改名必须两边一起改。"""
    from remote import artifacts as artifacts_mod
    from remote.artifacts import ArtifactStore

    assert offline_boot.ALL_ZIP == ArtifactStore.ALL_ZIP
    assert offline_boot.LATEST_ZIP == ArtifactStore.LATEST_ZIP
    assert offline_boot.PARTIAL_CANDIDATES == (ArtifactStore.ALL_ZIP, ArtifactStore.LATEST_ZIP)
    # 元信息名写死在 `remote/artifacts.py::_refresh_latest` 里（没有常量可对）：扫源码对账
    src = Path(str(artifacts_mod.__file__)).read_text(encoding="utf-8")
    assert f'writestr("{offline_boot.LATEST_ROW_NAME}"' in src, (
        f"{offline_boot.LATEST_ROW_NAME} 与 artifacts.py 写的那行对不上 —— 中途取回的进度行会退化成 '-'"
    )


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


# ────────────────────── hub 重试上限 → 转「等上传」模式（用户 2026-09-23）──────────────────


def test_hub_fetch_gives_up_after_the_cap_and_switches_to_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hub 取包试满 `CFG.hub_tries` 轮 ⇒ **不再碰 hub**，转入等上传（人一传上来就跑）。

    这是用户 2026-09-23 的口径：云机不能把 `wait_pack_sec`（缺省 30 分钟）全花在
    一个不会成功的请求上 —— 试满 10 轮就切到「上传任务包」模式。
    """
    assert offline_boot.DEFAULT_HUB_TRIES == 10
    up = tmp_path / "up"
    up.mkdir()
    logs: list[str] = []
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(up),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub.invalid"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **k: True)
    calls = {"n": 0}

    def fetch(hub, token, course, dest_dir, log, timeout=0.0):
        calls["n"] += 1
        if calls["n"] == offline_boot.DEFAULT_HUB_TRIES:
            fake_pack(up, course=course)  # 第 10 轮之后用户把包传上来了
        return None

    monkeypatch.setattr(offline_boot, "fetch_task_pack", fetch)
    got = offline_boot.obtain_pack(
        {"course": "c5-gae", "wait_pack_sec": 30, "poll_sec": 0.05, "prompt_upload": False},
        {"HUB_TOKEN": "t"},
        logs.append,
        tmp_path / "w",
    )
    assert got is not None and got.name == "task-c5-gae.zip"
    assert calls["n"] == offline_boot.DEFAULT_HUB_TRIES, "到顶后不许再碰 hub（否则上限等于没设）"
    assert any("等上传" in m and "不再轮询 hub" in m for m in logs), (
        f"切换到上传模式必须响亮说明，实际日志：{logs}"
    )


def test_hub_tries_zero_means_no_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`hub_tries=0` = 不限轮数（旧行为留一个把手：hub 稍后才会导出时用它）。"""
    monkeypatch.setattr(offline_boot, "UPLOAD_GLOBS", (str(tmp_path / "none"),))
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub.invalid"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **k: True)
    calls = {"n": 0}

    def fetch(hub, token, course, dest_dir, log, timeout=0.0):
        calls["n"] += 1
        return None

    monkeypatch.setattr(offline_boot, "fetch_task_pack", fetch)
    with pytest.raises(SystemExit):
        offline_boot.obtain_pack(
            {
                "course": "c5-gae",
                # 轮询下限 0.05s/轮 ⇒ 1s 窗口里约 20 轮，足够越过缺省上限（10）
                "wait_pack_sec": 1.0,
                "poll_sec": 0.05,
                "prompt_upload": False,
                "hub_tries": 0,
            },
            {"HUB_TOKEN": "t"},
            quiet,
            tmp_path / "w",
        )
    assert calls["n"] > offline_boot.DEFAULT_HUB_TRIES, "0 = 不限轮数"


# ────────────── Kaggle 上不使用 tailscale（用户 2026-09-23）──────────────


def test_is_kaggle_is_a_superset_of_the_artifacts_module() -> None:
    """本模块不能 import remote ⇒ 判据重写了一份：凡是 `remote.artifacts` 认的都必须认。

    自这一侧只看多不少（额外认 `KAGGLE_URL_BASE`，本文件 `download_dir()` 已在用它）：
    **漏判**的代价是「在 Kaggle 上去起 tailscale」（2026-09-17 事故那种），多判的代价只是
    少一条本来就通不了的路 —— 所以这个方向的不对称是故意的。
    """
    from remote.artifacts import is_kaggle as artifacts_is_kaggle

    for env, exists, expected in (
        ({"KAGGLE_KERNEL_RUN_TYPE": "Batch"}, lambda p: False, True),
        ({"KAGGLE_URL_BASE": "https://www.kaggle.com"}, lambda p: False, True),  # 额外认的标记
        ({}, lambda p: p == "/kaggle/working", True),
        ({}, lambda p: False, False),
        ({"COLAB_RELEASE_TAG": "2026-01"}, lambda p: p == "/content", False),
    ):
        ours = offline_boot.is_kaggle(env, exists=exists)
        theirs = artifacts_is_kaggle(env, exists=exists)
        assert ours is expected, f"env={env} 我们判 {ours}，期望 {expected}"
        assert (not theirs) or ours, f"env={env} artifacts 判 {theirs} 而我们判 {ours} —— 漏判"


def test_kaggle_skips_tailscale_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kaggle 上**一次都不碰** tailscale_boot：不是「起不来就算了」，是根本不去起。

    2026-09-17 事故就出在这里：userspace 引导会把进程代理改写成只转发 Tailscale IP，
    之后平台 Secrets（公网 HTTPS）够不着 ⇒ 凭据读成空串。
    """
    monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Batch")
    monkeypatch.setattr(
        offline_boot,
        "_load_tailscale_boot",
        lambda: (_ for _ in ()).throw(AssertionError("Kaggle 上不该去加载/启动 tailscale")),
    )
    logs: list[str] = []
    assert offline_boot.ensure_tailscale({"ts_ephemeral": True}, {"TS_AUTHKEY": "k"}, logs.append) == ""
    assert any("Kaggle" in m and "跳过 tailscale" in m for m in logs), logs


def test_kaggle_drops_the_tailnet_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """没隧道就必然不通 ⇒ Kaggle 上不给 tailnet 候选（只留公网 hub_url）。"""
    monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Batch")
    cfg = {"hub_url": "https://x.trycloudflare.com", "hub_port": 9999}
    creds = {"HUB_IP": "100.64.0.5"}
    assert offline_boot.hub_candidates(cfg, creds) == ["https://x.trycloudflare.com"]
    monkeypatch.delenv("KAGGLE_KERNEL_RUN_TYPE")
    assert offline_boot.hub_candidates(cfg, creds) == [
        "https://x.trycloudflare.com",
        "http://100.64.0.5:9999",
    ], "非 Kaggle 时 tailnet 候选照旧"


def test_colab_is_not_kaggle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Colab 不在跳过之列（它有正规网络与 secrets 通道，tailscale 那条路在那里是有效的）。"""
    monkeypatch.delenv("KAGGLE_KERNEL_RUN_TYPE", raising=False)
    monkeypatch.delenv("KAGGLE_URL_BASE", raising=False)
    monkeypatch.setenv("COLAB_RELEASE_TAG", "2026-01")
    assert offline_boot.is_kaggle(exists=lambda p: p == "/content") is False


# ────────────────────── 中途取回（用户 2026-09-23）──────────────────────


def _store_with_latest(art: Path, it: int = 1) -> Path:
    """用**真** ArtifactStore 造一份「跑到一半」的产物目录（

    不用手写 zip：`LATEST.zip` 的形状、`state.json` 的字段都是它的契约，自造一份就等于
    把「中途取回认的包」与「训练写的包」拆成两个真相。
    """
    from remote.artifacts import ArtifactStore

    store = ArtifactStore(art, run_id="run-off1")
    store.checkpoint(it, weights_json=b'{"w":1}', opt_tar=b"opt", row={"stage": 0, "seed": 1})
    return art / ArtifactStore.LATEST_ZIP


def test_package_partial_copies_the_latest_pack_under_the_console_name(tmp_path: Path) -> None:
    """跑到一半：把 `LATEST.zip` 复制成 `deliver-<课>.zip`（控制台导入靠文件名对账课程）。"""
    work = tmp_path / "w"
    latest = _store_with_latest(work / "run", it=3)
    logs: list[str] = []
    made = offline_boot.package_partial(
        {"course": "c5-gae", "work_dir": str(work), "download_dir": str(tmp_path / "out")},
        logs.append,
    )
    assert made == [tmp_path / "out" / "deliver-c5-gae.zip"]
    assert made[0].read_bytes() == latest.read_bytes(), "中途包必须逐字节就是盘中那份"
    joined = "\n".join(logs)
    assert "deliver-c5-gae.zip" in joined and "含到 it3" in joined and "导入产物" in joined


def test_package_partial_prefers_the_full_pack_when_it_exists(tmp_path: Path) -> None:
    """跑完（或干净停机）之后：`artifacts.zip` 更全 ⇒ 优先它，而不是最新一轮小包。"""
    work = tmp_path / "w"
    _store_with_latest(work / "run", it=2)
    (work / "run" / offline_boot.ALL_ZIP).write_bytes(b"full-pack")
    made = offline_boot.package_partial(
        {"course": "c5-gae", "work_dir": str(work), "download_dir": str(tmp_path / "out")}, quiet
    )
    assert made and made[0].read_bytes() == b"full-pack"


def test_package_partial_is_a_noop_and_says_why_when_there_is_nothing(tmp_path: Path) -> None:
    """第一轮 checkpoint 之前没东西可打 —— 说清是哪个目录空，不抛、不造空包。"""
    logs: list[str] = []
    made = offline_boot.package_partial(
        {"course": "c5-gae", "work_dir": str(tmp_path / "w"), "download_dir": str(tmp_path / "out")},
        logs.append,
    )
    assert made == [] and not (tmp_path / "out").exists()
    assert any("没有可打包的产物" in m and "run" in m for m in logs), logs


def test_package_partial_walks_the_course_queue(tmp_path: Path) -> None:
    """多课程：每门课各自一份包；没有产物的那门只报一句（不挡别的课）。"""
    dl = tmp_path / "dl"
    _store_with_latest(dl / "battle-offline" / "c5-gae" / "run", it=1)
    logs: list[str] = []
    made = offline_boot.package_partial(
        {"course": ["c5-gae", "c6-gae"], "download_dir": str(dl)}, logs.append
    )
    assert [p.name for p in made] == ["deliver-c5-gae.zip"]
    assert any("c6-gae" in m and "没有可打包的产物" in m for m in logs), logs


def test_boot_self_fingerprint_is_present() -> None:
    """引导模块自带「内存指纹」常量 —— notebook 加载后打它，用来识破旧模块。

    2026-09-25 真机事故：notebook 的 `_load_boot` 刷新磁盘成功（日志 sha12 = 新版），
    但同 kernel 里 `sys.modules` 还留着上一次 Run 导入的旧版 ⇒ `import` 直接命中它，
    **内存跑旧代码、日志打磁盘新版**。现场 traceback 是两版混血：帧行号 870 配的源码文本
    是磁盘新版的 `if (root / TS_TREE_NAME).is_dir():`，而实际动作是旧版 870 行的
    `write_bytes` —— 于是首次起跑（`<课>/run` 还不存在）写 `ts_code.zip` 直接
    FileNotFoundError（新版那行 mkdir 根本没跑）。修法两件：notebook 导入前
    `sys.modules.pop`（见 tests/test_offline_notebook.py），以及本常量 ——
    磁盘 sha 骗得过，模块对象骗不过（旧版没有它 ⇒ 日志里出现 `<missing>`）。
    """
    assert isinstance(offline_boot.BOOT_SELF, str) and offline_boot.BOOT_SELF.strip(), (
        "BOOT_SELF 是「内存里那一份」的唯一可读指纹，删了 notebook 的加载日志就只剩磁盘 sha"
    )
