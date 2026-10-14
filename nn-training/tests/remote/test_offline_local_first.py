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
import os
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

import remote.offline_deliverable as deliverable_mod
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


def _mini_code(value: str, *, shard_has_metrics: bool = True) -> bytes:
    """一个**能过导入哨兵**的最小代码树（三个哨兵模块都在、且互相自洽）。

    为什么不能再用「单个 `remote/_marker.py`」当候选（B3 之后）：候选要先过导入哨兵，缺
    `common.shard` 会被判「代码树内部不自洽」而换下一个 —— 那正是要钉的行为，不是要绕开的。
    `shard_has_metrics=False` = **混血树**（np_core 已经 import 那个名字 / shard 还没定义它）。
    """
    files = {
        "common/shard.py": 'METRICS_NAME = "m"\n' if shard_has_metrics else "OTHER = 1\n",
        "worker/ppo/np_core.py": "from common.shard import METRICS_NAME\n",
        "remote/run_loop.py": "MARKER = 'run_loop'\n",
        "remote/_marker.py": f"VALUE = {value!r}\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


CODE_A = _mini_code("A")
CODE_B = _mini_code("B")
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


def _pack(
    path: Path,
    *,
    code: bytes | None = CODE_A,
    ts: bytes | None = TS_A,
    index: dict | None = None,
    plan: dict | None = None,
) -> Path:
    """一个够用的任务包（索引 + code.zip [+ ts_code.zip] [+ plan.json]）——成员名走真常量。

    `plan` 只在「包是不是本机进度的加长段」那组用例里给（`pack_extends_local` 读它）：
    缺 `plan.json` 的包 ⇒ 判定为「不收包」= 本机优先，这是**刻意保留的保守默认**。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(offline_boot.BUNDLE_INDEX, json.dumps({**PACK_INDEX, **(index or {})}))
        if code is not None:  # `code=None` = 包缺代码（老包 / 只带计划的那类）
            z.writestr(offline_boot.CODE_NAME, code)
        if ts is not None:
            z.writestr(offline_boot.TS_CODE_NAME, ts)
        if plan is not None:
            z.writestr(offline_boot.PLAN_NAME, json.dumps(plan))
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


def _local(tmp_path: Path) -> Path:
    """本段**产物根**（★ 2026-10-14）：产物已默认落**节点本地盘**，所以用例一律把它**显式**钉在这里。

    为什么不省这一步：缺省值是真 `/tmp/battle2-offline/<课>` —— 那是跨用例共享的位置，
    前一个用例的产物会让后一个用例的 `local_artifacts()` 意外命中（分支听天由命）。
    """
    return tmp_path / "local"


def _round(dest: Path, it: int) -> Path:
    """给一份产物目录补上「那一轮的权重」（`it-<it>/weights.json`）——

    `local_artifacts` 不看它（只要三件），而**恢复腿的判据看**（备份只有三件 JSON、没有权重 ⇒
    恢复过来也接不上）。这里手写而不走 `ArtifactStore`：备份侧那份来源可能是别人写的。
    """
    d = dest / f"it-{it}"
    d.mkdir(parents=True, exist_ok=True)
    (d / deliverable_mod.WEIGHTS_NAME).write_text('{"w":1}', encoding="utf-8")
    return d


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


# ──────────── 续段判据（2026-10-14，plan/offline-segment-restart §2-J1） ────────────


def test_pack_extends_local_needs_same_start_and_a_longer_end() -> None:
    """纯函数判定表：**同起点 ∧ 更长终点** 才收包；其余一律维持本机优先。"""
    local = {"last_it": 100, "start_it": 0, "end_it": 100}
    # 收：起点不退回、终点加长 —— 这正是「停课 → 改大 iters → 开课」在盘上的指纹
    assert offline_boot.pack_extends_local(local, {"start_it": 100, "end_it": 500}) is True
    # 不收：终点等长（没有「新段」可言）
    assert offline_boot.pack_extends_local(local, {"start_it": 100, "end_it": 100}) is False
    # 不收：终点更短
    assert offline_boot.pack_extends_local(local, {"start_it": 100, "end_it": 50}) is False
    # 不收：起点更早 ⇒ 会让本机已跑的轮次**重跑**
    assert offline_boot.pack_extends_local(local, {"start_it": 0, "end_it": 500}) is False
    # 不收：起点更晚 ⇒ 本机中间那几轮直接从云机消失
    assert offline_boot.pack_extends_local(local, {"start_it": 150, "end_it": 500}) is False
    # 不收：读不到计划（空 / 缺键 / 坏值 / 本机侧缺件）——保守方向 = 现状
    assert offline_boot.pack_extends_local(local, {}) is False
    assert offline_boot.pack_extends_local(local, {"start_it": None, "end_it": 500}) is False
    assert offline_boot.pack_extends_local({}, {"start_it": 0, "end_it": 1}) is False


def test_read_pack_plan_reads_the_plan_and_never_raises(tmp_path: Path) -> None:
    """包内的 `plan.json` 是权威起点/终点（与 `run_loop` 将要用的是同一份字节）；读不到 ⇒ `{}`。"""
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", plan={"start_it": 100, "end_it": 500})
    assert offline_boot.read_pack_plan(pack) == {"start_it": 100, "end_it": 500}
    assert offline_boot.read_pack_plan(_pack(tmp_path / "e" / "task-c5-gae.zip")) == {}
    not_zip = tmp_path / "f.zip"
    not_zip.write_bytes(b"not a zip")
    assert offline_boot.read_pack_plan(not_zip) == {}
    assert offline_boot.read_pack_plan(tmp_path / "missing.zip") == {}


def test_describe_local_vs_pack_says_it_adopts() -> None:
    """收包这一档也必须在那一行里说出来（静默换段与静默**不收**包一样难查）。"""
    local = {"last_it": 100, "start_it": 0, "end_it": 100, "plan_sha256": "a" * 64}
    idx = {"it": 101, "end_it": 500, "plan_sha256": "b" * 64}
    line = offline_boot.describe_local_vs_pack(local, idx, adopt=True)
    assert "收包" in line and "一条不重跑" in line and "本机优先" not in line
    # 缺省（不收包）逐字维持旧文案
    assert "本机优先" in offline_boot.describe_local_vs_pack(local, idx)


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


# ────────────────────────── 代码/TS 就位：权威源 + 哨兵 + 换码事务 ──────────────────────────
#
# B 腿改序（plan/offline-code-authority-and-width，2026-10-14）：`code.zip` / `ts_code.zip` 是
# **工具态**（可重建、与进度无关）⇒ 权威源 = 任务包，其次 hub `/code`，本机缓存只作最后兜底。
# 旧序（本机优先）会让「旧但自洽」的本机快照必然打败包里的新代码 —— 现场就是这么跑出
# `ImportError: cannot import name 'METRICS_NAME'`（本机 np_core 已改 / 本机 shard 未改，
# 而包里那份好代码连看都没看）。judge 与事务都在 `ensure_code` 里，这一节逐条钉住。


def _code_dir(tmp_path: Path) -> str:
    return str(tmp_path / "code")


def test_ensure_code_takes_the_pack_and_writes_the_segment_contract(tmp_path: Path) -> None:
    """★ 权威序：包里有代码、本机是旧的那份 ⇒ **用包**，并把段契约的 sha 改写过去。

    不回写 `manifest.json` 的 `code_sha256` ⇒ 下游 `download._ensure_code` 按旧 sha 键缓存：
    cache miss 报「传输损坏」（指向错误原因），cache hit **静默跑旧代码**（评审 P0-1）。
    """
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_B)
    logs: list[str] = []
    try:
        got = offline_boot.ensure_code(pack, logs.append, _code_dir(tmp_path), dest=dest)
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'B'\n"
        assert (dest / offline_boot.CODE_NAME).read_bytes() == CODE_B, "选中谁就要写回产物目录"
        man = json.loads((dest / offline_boot.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert man["code_sha256"] == _sha(CODE_B), "段契约必须跟着代码走"
        # 只留 DoD#3 那条读数行（旧 sha12 → 新 sha12 的对照：操作员一眼看出「换了哪份」）
        assert any("段契约已改写" in m and _sha(CODE_A)[:12] in m for m in logs), logs
    finally:
        sys.path[:] = [p for p in sys.path if p != _code_dir(tmp_path)]


def test_ensure_code_refuses_a_pack_that_contradicts_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包里的字节与索引背书不符（包坏了）⇒ **硬拒且不解包**（绝不静默降级到旧代码）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    pack = _pack(
        tmp_path / "d" / "task-c5-gae.zip", code=CODE_B, index={"code_sha256": _sha(CODE_A)}
    )
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
    assert "对不上" in msg and "重新导出" in msg and "task_zip" in msg, msg
    assert calls == [], "拒坏包时不得解包（不写 CODE_DIR）"
    assert not Path(code_dir).exists()
    assert (dest / offline_boot.CODE_NAME).read_bytes() == CODE_A, "本机那份一个字节都不许动"


def test_ensure_code_uses_the_local_copy_only_as_a_last_resort(tmp_path: Path) -> None:
    """没有任何权威源（无包 + 无 hub）⇒ 本机兜底，而且**响亮**（工具态本该以包为准）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    logs: list[str] = []
    try:
        got = offline_boot.ensure_code(None, logs.append, _code_dir(tmp_path), dest=dest)
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'A'\n"
        assert any("兜底" in m and "WARN" in m for m in logs), logs
    finally:
        sys.path[:] = [p for p in sys.path if p != _code_dir(tmp_path)]


def test_ensure_code_says_so_when_there_is_no_source_at_all(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        offline_boot.ensure_code(None, _quiet, _code_dir(tmp_path), dest=tmp_path / "run")
    assert "没有可用的 code.zip" in str(ei.value.code)


def test_ensure_code_skips_a_mixed_blood_candidate_and_uses_the_next_one(tmp_path: Path) -> None:
    """★ 哨兵的硬判据：包里的树**内部不自洽**（np_core 已改 / shard 未改）⇒ 训练之前就换候选。

    这就是 2026-10-14 现场那个 `METRICS_NAME` 的形状 —— 判据必须出现在**候选循环里**，
    而不是等云机跑到 `train_core` 里 `import worker.ppo.engine` 才炸（那时 rollout 全白跑）。
    """
    dest = _artifacts(tmp_path / "run", code=CODE_A)  # 本机那份是自洽的
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=_mini_code("C", shard_has_metrics=False))
    logs: list[str] = []
    try:
        got = offline_boot.ensure_code(pack, logs.append, _code_dir(tmp_path), dest=dest)
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'A'\n"
        joined = "\n".join(logs)
        assert "过不了导入哨兵" in joined and "METRICS_NAME" in joined, logs
        man = json.loads((dest / offline_boot.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert man["code_sha256"] == _sha(CODE_A), "本机 sha 本来就对 ⇒ 事务不该多写一行"
    finally:
        sys.path[:] = [p for p in sys.path if p != _code_dir(tmp_path)]


def test_ensure_code_lets_an_env_missing_candidate_through(tmp_path: Path) -> None:
    """缺**第三方**（numpy/…）= 环境缺件，不是代码坏 ⇒ 放行 + WARN（换候选也换不来一个 numpy）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("common/shard.py", 'METRICS_NAME = "m"\n')
        z.writestr("worker/ppo/np_core.py", "import bcity_no_such_third_party\n")
        z.writestr("remote/run_loop.py", "MARKER = 1\n")
        z.writestr("remote/_marker.py", "VALUE = 'E'\n")
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=buf.getvalue())
    logs: list[str] = []
    code_dir = _code_dir(tmp_path)
    try:
        got = offline_boot.ensure_code(pack, logs.append, code_dir, dest=tmp_path / "run")
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'E'\n"
        assert any("环境缺件" in m for m in logs), logs
    finally:
        sys.path[:] = [p for p in sys.path if p != code_dir]


def test_hub_code_is_fetched_only_when_the_pack_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 惰性：包里有代码 ⇒ 一个字节都不去 hub 取（旧实现是**急切**构造，每会话白拉 1.19MB）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_A)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_B)
    fetched: list[str] = []

    def fake_fetch(url: str, *a: object, **k: object) -> bytes:
        fetched.append(url)
        return CODE_A

    monkeypatch.setattr(offline_boot, "_fetch_guarded", fake_fetch)
    code_dir = _code_dir(tmp_path)
    try:
        offline_boot.ensure_code(pack, _quiet, code_dir, dest=dest, hub="http://hub", token="t")
        assert fetched == [], "包里有代码 ⇒ 不该碰 hub"
    finally:
        sys.path[:] = [p for p in sys.path if p != code_dir]


def test_hub_code_becomes_the_authority_when_the_pack_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包缺件（老包）⇒ 用 hub 那份，**并把它写成这一段的权威**（产物目录 + manifest）。"""
    dest = _artifacts(tmp_path / "run", code=CODE_B)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=None)
    monkeypatch.setattr(offline_boot, "_fetch_guarded", lambda *a, **k: CODE_A)
    logs: list[str] = []
    code_dir = _code_dir(tmp_path)
    try:
        got = offline_boot.ensure_code(
            pack, logs.append, code_dir, dest=dest, hub="http://hub", token="t"
        )
        assert (Path(got) / "remote" / "_marker.py").read_text(encoding="utf-8") == "VALUE = 'A'\n"
        assert (dest / offline_boot.CODE_NAME).read_bytes() == CODE_A
        man = json.loads((dest / offline_boot.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert man["code_sha256"] == _sha(CODE_A) != _sha(CODE_B)
    finally:
        sys.path[:] = [p for p in sys.path if p != code_dir]


# ────────────────────────── TS 运行时：同一套权威序 ──────────────────────────


def test_ensure_ts_tree_uses_the_artifacts_tree_without_unpacking(tmp_path: Path) -> None:
    dest = _artifacts(tmp_path / "run")
    (dest / offline_boot.TS_CODE_NAME).unlink()  # 只有树、没有 zip：照样直接用它
    offline_boot.ensure_ts_tree(None, _quiet, dest=dest)
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
    dest = _local(tmp_path)
    assert not dest.exists(), "本用例的前提：全新一跑（产物目录还没有）"
    offline_boot.ensure_ts_tree(pack, _quiet, dest=dest)
    assert (dest / offline_boot.TS_CODE_NAME).read_bytes() == TS_A
    assert (dest / offline_boot.TS_TREE_NAME / "tools" / "sim" / "export-eval-game.ts").is_file()


def test_ensure_ts_tree_takes_the_pack_ts_and_rewrites_the_segment_sha(tmp_path: Path) -> None:
    """★ 权威序对 ts 同样成立（B1「ts 同」）：包里的 TS 是权威 ⇒ 重铺 + 改写 `ts_code_sha256`。

    不回写 ⇒ 下游 `download._ensure_ts_code` 按旧 sha 键缓存/对账：报「传输损坏」，或旧 cache
    命中**静默跑旧 TS**（rollout 与权重血统不符，读数看起来完全正常，只有 sha 能揭穿）。
    """
    dest = _artifacts(tmp_path / "run", ts=TS_A)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", ts=TS_B)
    logs: list[str] = []
    offline_boot.ensure_ts_tree(pack, logs.append, dest=dest)
    assert (dest / offline_boot.TS_CODE_NAME).read_bytes() == TS_B
    assert (dest / offline_boot.TS_TREE_NAME / "tools" / "sim" / "export-eval-game.ts").is_file()
    man = json.loads((dest / offline_boot.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert man["ts_code_sha256"] == _sha(TS_B)
    assert any("段契约已改写" in m and "ts_code_sha256" in m for m in logs), logs
    # ↑ 格式与 code 腿同一条：DoD#3 的对照行必须**点名是哪个键**换的（ts 与 code 分开）


def test_ensure_ts_tree_reuses_when_the_pack_matches_the_local_bytes(tmp_path: Path) -> None:
    """同字节 ⇒ 复用现有树（重铺会把盘上的树整个抹掉重来；幂等才是对的行为）。"""
    dest = _artifacts(tmp_path / "run", ts=TS_A)
    marker = dest / offline_boot.TS_TREE_NAME / "sentinel.txt"
    marker.write_text("keep\n", encoding="utf-8")
    offline_boot.ensure_ts_tree(
        _pack(tmp_path / "d" / "task-c5-gae.zip", ts=TS_A), _quiet, dest=dest
    )
    assert marker.read_text(encoding="utf-8") == "keep\n", "同字节 ⇒ 不该重解"

# ────────────────────────── run_one_course：判定表 ──────────────────────────


def _run_course(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cfg: dict | None = None,
    pack: Path | None = None,
    obtain_kwargs: dict | None = None,
    hub: str = "",
    log_sink: list[str] | None = None,
) -> tuple[list[str], list[tuple[str, bool]]]:
    """跑一次 `run_one_course`（run_loop 注入 + 代码/TS 用 stub），返回 (argv, 调用记录)。

    `hub` 非空 ⇒ 假装探得到 hub（段末交付面/备份那几条判据需要它）；`log_sink` 收日志。
    """
    calls: list[tuple[str, bool]] = []

    def fake_obtain(cfg2, creds, log, work, stop=None, **kw):
        calls.append(("obtain_pack", bool(kw.get("optional"))))
        return pack

    def fake_code(p, log, code_dir=None, **kw):
        calls.append(("ensure_code", bool(kw.get("dest"))))

    def fake_ts(p, log, **kw):
        calls.append(("ensure_ts_tree", True))

    monkeypatch.setattr(offline_boot, "obtain_pack", fake_obtain)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg2, creds: ([hub] if hub else []))
    monkeypatch.setattr(offline_boot, "probe_hub", lambda *a, **kw: True)
    monkeypatch.setattr(offline_boot, "fetch_resume", lambda *a, **kw: None)  # 别真发 HTTP
    monkeypatch.setattr(offline_boot, "ensure_code", fake_code)
    monkeypatch.setattr(offline_boot, "ensure_ts_tree", fake_ts)
    seen: list[str] = []

    def fake_run_loop(argv: list[str]) -> int:
        seen.extend(argv)
        return 0
    base = {
        "course": "c5-gae",
        "work_dir": str(tmp_path / "w"),
        # 产物根显式钉住（缺省是真 `/tmp` ⇒ 跨用例互相看得见对方的产物；见 `_local`）
        "local_root": str(_local(tmp_path)),
        "device": "cpu",
        "live_backfeed": False,
        **(cfg or {}),
    }
    logs = log_sink if log_sink is not None else []
    rc = offline_boot.run_one_course(
        base, {}, logs.append, None, course="c5-gae", multi=False, run_loop_main=fake_run_loop
    )
    assert rc == 0
    return seen, calls


def test_run_one_course_local_first_never_imports_the_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = _artifacts(_local(tmp_path))
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
    _artifacts(_local(tmp_path))
    argv, calls = _run_course(tmp_path, monkeypatch, pack=None)
    assert argv[:2] == ["--artifacts", str(_local(tmp_path))]
    assert "--bundle" not in argv


def test_run_one_course_force_pack_takes_the_legacy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CFG.force_pack=true` = 显式老行为：包覆盖本机计划/清单（走 `--bundle`）。"""
    _artifacts(_local(tmp_path))
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A)
    argv, calls = _run_course(tmp_path, monkeypatch, cfg={"force_pack": True}, pack=pack)
    assert argv[:2] == ["--bundle", str(pack)]
    assert ("obtain_pack", False) in calls, "老行为下取包仍是硬前置（取不到就该响亮报错）"


def test_run_one_course_explicit_task_zip_also_forces_the_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CFG.task_zip` 与 `force_pack` 同义（用户显式指了一个包 ⇒ 就是它）。"""
    _artifacts(_local(tmp_path))
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", code=CODE_A)
    argv, _calls = _run_course(
        tmp_path, monkeypatch, cfg={"task_zip": str(pack)}, pack=pack
    )
    assert argv[:2] == ["--bundle", str(pack)]


def test_run_one_course_adopts_a_pack_that_extends_the_local_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-14 现场（plan/offline-segment-restart §5-DoD）：本机段跑满 it100，
    人在控制台把 iters 改大并重导 ⇒ 包是 it100→it500 ⇒ **收包**（走 `--bundle`），
    不再拿旧段计划跑成 `noop`。"""
    dest = _artifacts(_local(tmp_path), last_it=100, start_it=0, end_it=100)
    pack = _pack(
        tmp_path / "d" / "task-c5-gae.zip",
        index={"it": 101, "end_it": 500},
        plan={"start_it": 100, "end_it": 500},
    )
    argv, calls = _run_course(tmp_path, monkeypatch, pack=pack)
    assert argv[:2] == ["--bundle", str(pack)], argv
    assert ("obtain_pack", True) in calls, "本机有产物 ⇒ 取包仍是不等待的那种（判据的输入）"
    assert str(dest) in argv, "产物目录照旧给出去（接续点与缓存还在那儿）"


def test_run_one_course_keeps_local_first_for_a_pack_that_does_not_extend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """起点更早的包（旧包 / 会让本机轮次重跑）⇒ 一个字都不换段：判定表只松这两条。"""
    _artifacts(_local(tmp_path), last_it=100, start_it=0, end_it=100)
    pack = _pack(
        tmp_path / "d" / "task-c5-gae.zip",
        index={"it": 1, "end_it": 100},
        plan={"start_it": 0, "end_it": 100},
    )
    argv, _calls = _run_course(tmp_path, monkeypatch, pack=pack)
    assert "--bundle" not in argv, argv
    assert argv[:2] == ["--artifacts", str(_local(tmp_path))]


def test_run_one_course_keeps_local_first_when_the_pack_has_no_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """包读不出计划 ⇒ 保守不收（`read_pack_plan` 的空 dict 走的就是这一支）。"""
    _artifacts(_local(tmp_path), last_it=100, start_it=0, end_it=100)
    pack = _pack(tmp_path / "d" / "task-c5-gae.zip", index={"it": 101, "end_it": 500})
    argv, _calls = _run_course(tmp_path, monkeypatch, pack=pack)
    assert "--bundle" not in argv, argv
    assert argv[:2] == ["--artifacts", str(_local(tmp_path))]


def test_run_one_course_refuses_a_half_broken_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有 `state.json` 而缺计划/清单：让包补齐会重置本机进度 ⇒ 响亮拒（列出两条出路）。"""
    dest = _artifacts(_local(tmp_path), with_plan=False)
    (dest / offline_boot.PLAN_NAME).unlink(missing_ok=True)
    with pytest.raises(SystemExit) as ei:
        _run_course(tmp_path, monkeypatch, pack=_pack(tmp_path / "d" / "t.zip"))
    msg = str(ei.value.code)
    assert "不完整" in msg and "清空" in msg, msg


def test_run_one_course_pure_offline_does_not_name_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`live_backfeed=False`（或够不着 hub）时 `tok_file` 曾经从未赋值 ⇒ NameError。"""
    _artifacts(_local(tmp_path))
    argv, _calls = _run_course(tmp_path, monkeypatch, cfg={"live_backfeed": False})
    assert "--no-deliver" in argv


# ──────────────── 两个根 + 恢复腿（plan/offline-local-disk-first §3.2/§3.5） ────────────────


def test_local_root_default_lands_on_the_node_local_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省落点 = 节点**本地盘**：`/tmp/battle2-offline/<课>`（拍板-1）。"""
    monkeypatch.delenv(offline_boot.LOCAL_ROOT_ENV, raising=False)
    want = Path(offline_boot.DEFAULT_LOCAL_PARENT) / offline_boot.LOCAL_ROOT_NAME / "c5-gae"
    assert offline_boot.local_root_path({"course": "c5-gae"}, "c5-gae", multi=False) == want
    # 多课：再加一层课程名（两门课共用一个产物根 = 第二门接错课的续跑点）
    assert offline_boot.local_root_path({}, "c6-gae", multi=True) == want.parent / "c6-gae"


def test_local_root_env_wins_even_for_a_single_course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式（env / `CFG.local_root`）> 缺省；单课**不再**加课程层（操作员说了算）。"""
    monkeypatch.setenv(offline_boot.LOCAL_ROOT_ENV, str(tmp_path / "explicit"))
    assert offline_boot.local_root_path({}, "c5-gae", multi=False) == tmp_path / "explicit"
    assert offline_boot.local_root_path({}, "c5-gae", multi=True) == tmp_path / "explicit" / "c5-gae"


def test_unusable_local_root_falls_back_to_the_net_side_with_a_reason(tmp_path: Path) -> None:
    """★ 拍板-2：本地盘取不到 ⇒ **回落网络侧产物根 + 成因**（不改成响亮失败 —— 产物根没有第二处落脚）。

    这里用「本地根的父路径是个文件」造出真实的 `mkdir` 失败（不抛、不用 chmod，与 root 身份无关）。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"x")
    cfg = {"course": "c5-gae", "work_dir": str(tmp_path / "w"), "local_root": str(blocker / "sub")}
    got, why = offline_boot.resolve_local_root(cfg, "c5-gae", multi=False)
    assert got == offline_boot.net_artifacts_dir(cfg, "c5-gae", multi=False) == tmp_path / "w" / "run"
    assert "NotADirectoryError" in why and str(blocker / "sub") in why, why


def test_usable_local_root_gets_no_reason_and_is_created(tmp_path: Path) -> None:
    cfg = {"course": "c5-gae", "work_dir": str(tmp_path / "w"), "local_root": str(tmp_path / "local")}
    got, why = offline_boot.resolve_local_root(cfg, "c5-gae", multi=False)
    assert (got, why) == (tmp_path / "local", "")
    assert got.is_dir(), "判据过 = 目录已被建出来（探针的文件与目录一起验）"


# ──────────────── 持久面容量判据 B2（plan/offline-local-disk-first §3.2/§8） ────────────────


def test_artifact_budget_scales_with_the_segment_length() -> None:
    """B2 的算式：轮数 × 每轮估算 × 余量比值；取不到轮数 ⇒ 缺省段长（**不猜负数**）。"""
    need = deliverable_mod.artifact_need_bytes
    assert need(0) == need(deliverable_mod.SEGMENT_ROUNDS_DEFAULT)
    assert need(-5) == need(deliverable_mod.SEGMENT_ROUNDS_DEFAULT)
    assert need(200) == pytest.approx(2 * need(100))
    assert need(100) == pytest.approx(
        deliverable_mod.ARTIFACT_BYTES_PER_ROUND * 100 * deliverable_mod.ARTIFACT_MIN_FREE_RATIO
    )


def test_local_disk_too_small_for_the_segment_falls_back_to_the_net_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ B2：本地盘装不下**这一段的产物** ⇒ 回落网络侧产物根，成因里带 free/need。

    「装不下」而不是「写不进」：产物根落本地盘之后段中盘满没有等价逃生门（scratch 两个候选盘
    各有自己的容量闸），所以这一判据必须在**开段前**做——`_free_bytes` 是它的注入缝（真盘读数
    在这里不可能凑出「刚好装不下」）。
    """
    monkeypatch.setattr(deliverable_mod, "_free_bytes", lambda _p: 100_000_000.0)  # 100MB
    cfg = {"course": "c5-gae", "work_dir": str(tmp_path / "w"), "local_root": str(tmp_path / "local")}
    got, why = offline_boot.resolve_local_root(cfg, "c5-gae", multi=False, rounds=100)
    assert got == offline_boot.net_artifacts_dir(cfg, "c5-gae", multi=False)
    assert "容量不够" in why and "free=100MB" in why and "need=600MB" in why, why
    # 判据走的是「先探针、后容量」（容量读不到一个不存在的路径），所以目录本身在，但必须**空**
    assert list((tmp_path / "local").iterdir()) == [], "回落了就不该在本地根留下探针文件"


def test_a_longer_segment_needs_more_room(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`CFG.max_iters` 是本段轮数的权威（`run_one_course` 把它递给 `rounds`）：同一个盘上
    长段被推去网络盘、短段留在本地盘——「段长进了判据」的可观测面。"""
    local = tmp_path / "local"
    cfg = {"course": "c5-gae", "work_dir": str(tmp_path / "w"), "local_root": str(local)}
    monkeypatch.setattr(deliverable_mod, "_free_bytes", lambda _p: 5_000_000_000.0)  # 5GB
    got, why = offline_boot.resolve_local_root(cfg, "c5-gae", multi=False, rounds=100)
    assert (got, why) == (local, "")
    got_long, why_long = offline_boot.resolve_local_root(cfg, "c5-gae", multi=False, rounds=5000)
    assert (got_long, bool(why_long)) == (tmp_path / "w" / "run", True)


def test_run_one_course_falls_back_and_says_so_when_the_disk_is_too_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：容量不够 ⇒ `--artifacts` 指网络侧产物根 + 一行 WARN（拍板-2 的响亮口径）。"""
    monkeypatch.setattr(deliverable_mod, "_free_bytes", lambda _p: 100_000_000.0)
    logs: list[str] = []
    argv, _calls = _run_course(tmp_path, monkeypatch, pack=None, log_sink=logs)
    assert argv[:2] == ["--artifacts", str(tmp_path / "w" / "run")]
    # 「回落 + **响亮一行**」的交付物就是那一行字（拍板-2）：判据由 free/need 两个读数承载
    warn = next(line for line in logs if "本地产物根不可用" in line)
    assert "free=100MB" in warn and "need=600MB" in warn, warn


def test_run_one_course_writes_the_artifacts_to_the_local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 拍板-1 的**生效点**（评审 P0-1）：`--artifacts` 指本地产物根，网络侧一个字节都不写。"""
    local = _local(tmp_path)
    _artifacts(local)
    argv, calls = _run_course(tmp_path, monkeypatch, pack=None)
    assert argv[:2] == ["--artifacts", str(local)]
    # 备份抽屉的落点要递给 run_loop（C1 的消费方）；`index()` 找不到就 ValueError —— 不必再断言在场
    assert argv[argv.index("--backup-root") + 1] == str(tmp_path / "w" / "run" / "backup")
    assert not (tmp_path / "w" / "run").exists(), "补传全成功的一段：网络侧产物根不该存在"
    assert ("obtain_pack", True) in calls  # 本机优先（产物在本地根里被认出来了）


def test_backup_restores_an_empty_local_root_then_runs_local_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ DoD-9：本地空 + 备份齐 ⇒ 反向恢复到本地根，**并且恢复之后就走本机优先**。

    没有这条，`--artifacts` 搬本地盘就等于把「cell 死后续跑」静默删掉（评审 P1-3 的配套）。"""
    local = _local(tmp_path)
    backup = tmp_path / "w" / "run" / "backup"
    _artifacts(backup, last_it=3)  # 备份抽屉：三件齐全
    _round(backup, 3)  # + 那一轮的权重（`backup_ready` 的实质判据）
    assert offline_boot.local_artifacts(local) is None, "前提：本地空"
    argv, calls = _run_course(tmp_path, monkeypatch, pack=None)
    assert argv[:2] == ["--artifacts", str(local)]
    assert argv.count("--bundle") == 0, f"恢复之后本机有完整段落 ⇒ 不是取包起跑：{argv}"
    restored = offline_boot.local_artifacts(local)
    assert restored is not None and restored["last_it"] == 3, restored
    assert (local / "it-3" / "weights.json").is_file(), "那一轮的权重也要在（跑了才能接着跑）"
    assert not (local / f".restore-{os.getpid()}").exists(), "暂存不许留在落点里"
    assert ("obtain_pack", True) in calls, "恢复 ⇒ 本机优先：取包必须是 optional"


def test_backup_never_overwrites_a_local_segment(tmp_path: Path) -> None:
    """★ DoD-9 的另一半：本地**不是空**时一个字都不动（本地更新是常态，备份只是恢复窗口）。"""
    local = _local(tmp_path)
    _artifacts(local, last_it=5)
    backup = tmp_path / "w" / "run" / "backup"
    _artifacts(backup, last_it=9)
    _round(backup, 9)
    logs: list[str] = []
    got = offline_boot.restore_from_backup(local, backup, logs.append)
    assert got is not None and got["last_it"] == 5, got
    assert not (local / "it-9").exists(), "备份里的新轮次不许覆盖本地"
    assert json.loads((local / offline_boot.STATE_NAME).read_text(encoding="utf-8"))["last_it"] == 5
    assert any("不看网络盘备份" in m for m in logs), logs


def test_backup_incomplete_is_not_a_restore_source(tmp_path: Path) -> None:
    """备份只有三件 JSON、没有那一轮的权重 ⇒ **不算齐**（恢复过来也跑不了）。"""
    local = _local(tmp_path)
    backup = tmp_path / "w" / "run" / "backup"
    _artifacts(backup, last_it=3)
    assert offline_boot.restore_from_backup(local, backup, _quiet) is None, "缺权重 ⇒ 不算齐"
    assert not (local / offline_boot.STATE_NAME).exists(), "不齐 ⇒ 不搬"


def test_install_backup_is_self_consistent(tmp_path: Path) -> None:
    """字节面：`install_backup` 把三件 + 轮次 + 全量包搬过去，**暂存目录不留**。"""
    backup = tmp_path / "b"
    dest = tmp_path / "d"
    _artifacts(backup, last_it=4)
    _round(backup, 4)
    (backup / offline_boot.ALL_ZIP).write_bytes(b"full")
    assert deliverable_mod.install_backup(backup, dest, _quiet) is True
    assert offline_boot.local_artifacts(dest) is not None
    assert (dest / offline_boot.ALL_ZIP).read_bytes() == b"full"
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        [
            offline_boot.STATE_NAME,
            offline_boot.PLAN_NAME,
            offline_boot.MANIFEST_NAME,
            offline_boot.ALL_ZIP,
            "it-4",
        ]
    ), sorted(p.name for p in dest.iterdir())


# ─────────────────── 段末交付面（B4，`plan/offline-local-disk-first` §3.4）───────────────────


def _stub_download_dir(monkeypatch: pytest.MonkeyPatch, out: Path) -> None:
    """把交付物的下载落点换成 tmp（缺省是 cwd / Kaggle 的 `/kaggle/working`）。"""
    monkeypatch.setattr(offline_boot._load_deliverable(), "download_dir", lambda _cfg: out)


def test_segment_end_skips_the_delivery_zip_when_backfeed_was_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 拍板-1/3（B4）：补传全成功（无备份抽屉）+ 人没显式点 `download_dir` ⇒

    **不**把一个大 zip 拷到慢挂点上（那正是本计划要移除的那类等待），只说清产物在本地根。"""
    local = _local(tmp_path)
    _artifacts(local)
    (local / "artifacts.zip").write_bytes(b"full")
    out = tmp_path / "out"
    _stub_download_dir(monkeypatch, out)
    logs: list[str] = []
    _run_course(tmp_path, monkeypatch, hub="http://hub", log_sink=logs)
    assert not (out / "deliver-c5-gae.zip").exists(), "常态不该落交付档"
    assert any("没有交付档" in m and str(local) in m for m in logs), logs


def test_segment_end_writes_the_delivery_zip_when_the_drawer_has_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有轮次没能回传（网络侧抽屉在）⇒ 收尾仍打一份**能导入控制台**的交付档。"""
    local = _local(tmp_path)
    _artifacts(local)
    (local / "artifacts.zip").write_bytes(b"full")
    drawer = tmp_path / "w" / "run" / "backup"
    drawer.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "out"
    _stub_download_dir(monkeypatch, out)
    logs: list[str] = []
    _run_course(tmp_path, monkeypatch, hub="http://hub", log_sink=logs)
    assert (out / "deliver-c5-gae.zip").read_bytes() == b"full"
    assert any("有备份抽屉" in m for m in logs), logs


def test_segment_end_without_hub_always_writes_the_delivery_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ DoD-12：**没有 hub**（`--no-deliver`）= 纯手工递送 —— 交付档是唯一动线，无条件落。"""
    local = _local(tmp_path)
    _artifacts(local)
    (local / "artifacts.zip").write_bytes(b"full")
    out = tmp_path / "out"
    _stub_download_dir(monkeypatch, out)
    logs: list[str] = []
    _run_course(tmp_path, monkeypatch, log_sink=logs)
    assert (out / "deliver-c5-gae.zip").exists()
    assert any("本段无 hub" in m for m in logs), logs


def test_segment_end_explicit_download_dir_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """人显式给了 `CFG.download_dir` ⇒ 那块钱由人主动付（照样打档，即便补传全成功）。"""
    local = _local(tmp_path)
    _artifacts(local)
    (local / "artifacts.zip").write_bytes(b"full")
    out = tmp_path / "out"
    logs: list[str] = []
    _run_course(
        tmp_path,
        monkeypatch,
        cfg={"download_dir": str(out)},
        hub="http://hub",
        log_sink=logs,
    )
    assert (out / "deliver-c5-gae.zip").exists()


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

    def fetch(hub, token, course, dest_dir, log, timeout=0.0, *, lease: str = ""):
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

    def fake_one(cfg, creds, log, stop, course=None, multi=False, lease="", progress=None):
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
