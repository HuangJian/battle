"""tests/test_bundle.py —— **全离线**任务包（`remote/bundle.py`）：导出 → 搬运 → 导入 → 自主跑完。

用户需求（2026-09-17）：「hub 支持打包导出训练任务（课程、初始权重、代码），以 kaggle/colab
官方支持方式上传云机后，云机能全程自主完成训练」。本文件钉三件事：

  1. **包里有跑完整段所需的一切**：导出后逐件对账（plan/manifest/init/opt/code/ts），导入即得到
     可直接续跑的产物目录（起点 checkpoint + TS 树就位）。
  2. **包是不可信输入**：搬运截断（sha/字节数不符）、拿错包（magic 不符）、越界成员（zip-slip）
     一律**拒收且不跑任何一局**。
  3. **导入之后不需要 hub、不需要仓**：`run_standalone` 用包里的 `code.zip` 字节喂 `run_job`
     （节点没有仓库可回退、也不该联网去下载代码），从包里那段起点一路跑到 `end_it`。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.artifacts import ArtifactStore, sha256_bytes
from remote.bundle import (
    BUNDLE_INDEX,
    BUNDLE_MAGIC,
    export_bundle,
    import_bundle,
    read_bundle_index,
)
from remote.protocol import (
    ProtocolError,
    encode_opt_tar,
    encode_weights_json,
    normalize_manifest,
    unpack_payload,
)
from remote.run_loop import run_standalone
from rl.plan import build_plan, dump_plan, planned_iters

INIT_W = b'{"format":"nn-weights-json","params":{"w":0}}'
CODE_ZIP = b"PK\x03\x04" + b"py-code" * 50


def _quiet(_m: str) -> None:
    """日志静音。"""


def _args(**over: object) -> SimpleNamespace:
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "0-3",
        "seed_rotate": 0,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _manifest(plan: dict, *, it: int, code_sha: str) -> dict:
    m = {
        "proto": 1,
        "kind": "run",
        "runId": "run-bundle",
        "it": int(it),
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": code_sha,
        "course": "// course jsonc\n{}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 8,
        "lr": 3e-4,
        "init_weights_fp": sha256_bytes(INIT_W),
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
        "ts_code_sha256": "t" * 64,
        "rollout": {
            "argv": [["tools/sim/export-rl-rollout.ts", "--out", "w0"]],
            "wver": sha256_bytes(INIT_W),
            "workers": 1,
            "game_timeout_sec": 0.0,
            "bun": "bun",
        },
        "plan_sha256": sha256_bytes(dump_plan(plan)),
    }
    return normalize_manifest(m)


def _export(tmp_path: Path, *, it: int = 3, n: int = 3) -> tuple[Path, dict, dict]:
    """造一个任务包（含 opt/code/ts 三件可选与必需件），返回 (zip, index, plan)。"""
    plan = build_plan(
        _args(), it=it - 1, iters_total=it - 1 + n, rotate_seed=5, max_iters=n, log=_quiet
    )
    m = _manifest(plan, it=it, code_sha=sha256_bytes(CODE_ZIP))
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "init_weights.json").write_bytes(INIT_W)
    (src / "code.zip").write_bytes(CODE_ZIP)
    with zipfile.ZipFile(src / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/export-rl-rollout.ts", "// ts\n")
    (src / "opt.tar").write_bytes(b"opt-" + b"x" * 32)
    out = tmp_path / "task.zip"
    index = export_bundle(
        out,
        manifest=m,
        plan_bytes=dump_plan(plan),
        init_weights_path=src / "init_weights.json",
        code_zip_path=src / "code.zip",
        ts_code_zip_path=src / "ts_code.zip",
        opt_tar_path=src / "opt.tar",
        hub_url="https://hub.example",
        note="test export",
    )
    return out, index, plan


# ────────────────────────── 导出 / 导入 ──────────────────────────


def test_export_then_import_lays_out_resumable_artifacts(tmp_path: Path) -> None:
    """导入后 = 可直接续跑的产物目录：起点 checkpoint（权重+动量）+ TS 树 + 索引就位。"""
    zip_path, index, plan = _export(tmp_path, it=3, n=3)
    assert index["magic"] == BUNDLE_MAGIC
    assert (index["it"], index["end_it"]) == (3, 5)
    assert set(index["parts"]) >= {"plan.json", "manifest.json", "init_weights.json", "code.zip"}
    dest = tmp_path / "art"
    got = import_bundle(zip_path, dest)
    assert got["it"] == 3 and got["end_it"] == 5
    assert got["hub_url"] == "https://hub.example"
    # 起点：it-003/weights.json（= 本段输入）与 opt.tar（Adam 动量）
    assert (dest / "it-003" / "weights.json").read_bytes() == INIT_W
    assert (dest / "it-003" / "opt.tar").stat().st_size > 0
    assert (dest / "ts_code" / "tools" / "sim" / "export-rl-rollout.ts").exists()
    assert (dest / "code.zip").read_bytes() == CODE_ZIP
    assert json.loads((dest / BUNDLE_INDEX).read_text(encoding="utf-8"))["run_id"] == "run-bundle"
    assert json.loads((dest / "plan.json").read_text(encoding="utf-8"))["end_it"] == plan["end_it"]


def test_import_rejects_tampered_part(tmp_path: Path) -> None:
    """搬运转场（dataset/Drive）截断或篡改 ⇒ 逐件对账当场拒收——绝不能带着坏起点开跑。"""
    zip_path, _idx, _plan = _export(tmp_path)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(zip_path) as zin, zipfile.ZipFile(broken, "w") as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "init_weights.json":
                raw = raw[:-1] + b"!"  # 改一个字节（长度不变，只有 sha 能抓住）
            zout.writestr(item, raw)
    with pytest.raises(ProtocolError) as ei:
        import_bundle(broken, tmp_path / "art-t")
    assert "init_weights.json 与索引不符" in str(ei.value)


def test_import_rejects_zip_slip_member(tmp_path: Path) -> None:
    """越界成员（zip-slip）一律拒收：这个 zip 的来源是「人搬来的文件」，是最不可信的输入。"""
    zip_path, _idx, _plan = _export(tmp_path)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path) as zin, zipfile.ZipFile(evil, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("../../pwned.txt", b"x")
    with pytest.raises(ProtocolError) as ei:
        import_bundle(evil, tmp_path / "art-e")
    assert "zip-slip" in str(ei.value) or "越界" in str(ei.value)
    assert not (tmp_path.parent / "pwned.txt").exists()


def test_index_rejects_foreign_or_corrupt_zip(tmp_path: Path) -> None:
    """拿错包（magic 不符）/ 损坏 zip：读索引这一步就停，不做任何落盘。"""
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr(BUNDLE_INDEX, json.dumps({"magic": "something-else", "proto": 1}))
    with pytest.raises(ProtocolError) as e1:
        read_bundle_index(other)
    assert "身份不符" in str(e1.value)
    notzip = tmp_path / "not.zip"
    notzip.write_bytes(b"this is not a zip")
    with pytest.raises(ProtocolError):
        read_bundle_index(notzip)


def test_export_refuses_mismatched_plan_and_missing_pieces(tmp_path: Path) -> None:
    """导出侧三道自检：计划 sha 与 manifest 不符 / 缺 code.zip / 缺 ts_code.zip。"""
    zip_path, _idx, plan = _export(tmp_path / "keep")
    src = tmp_path / "keep" / "src"
    m = _manifest(plan, it=3, code_sha=sha256_bytes(CODE_ZIP))
    with pytest.raises(ProtocolError) as e1:
        export_bundle(
            tmp_path / "x1.zip",
            manifest=m,
            plan_bytes=dump_plan(plan) + b" ",
            init_weights_path=src / "init_weights.json",
            code_zip_path=src / "code.zip",
            ts_code_zip_path=src / "ts_code.zip",
        )
    assert "plan_bytes" in str(e1.value)
    with pytest.raises(ProtocolError) as e2:
        export_bundle(
            tmp_path / "x2.zip",
            manifest=m,
            plan_bytes=dump_plan(plan),
            init_weights_path=src / "init_weights.json",
            code_zip_path=tmp_path / "nope.zip",
            ts_code_zip_path=src / "ts_code.zip",
        )
    assert "code.zip" in str(e2.value)
    with pytest.raises(ProtocolError) as e3:
        export_bundle(
            tmp_path / "x3.zip",
            manifest=m,
            plan_bytes=dump_plan(plan),
            init_weights_path=src / "init_weights.json",
            code_zip_path=src / "code.zip",
            ts_code_zip_path=tmp_path / "nope-ts.zip",
        )
    assert "ts_code.zip" in str(e3.value)
    assert zip_path.exists()  # 原包不受失败影响


# ────────────────────────── 导入 → 无 hub 自主跑完 ──────────────────────────


class _FakeRunJob:
    """`run_job` 替身：记录每次调用 + **断言 payload/代码都在 preloaded 里**（全离线的关键）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, base_url: str, token: str, job: dict, **kw: object) -> dict:
        m = normalize_manifest(job["manifest"])
        pl = kw["preloaded"]
        assert isinstance(pl, dict)
        assert pl.get("code_zip"), "全离线必须随 payload 带 code.zip 字节（节点没仓库）"
        assert pl.get("ts_code_zip"), "全离线必须随 payload 带 ts_code.zip 字节（节点没仓库）"
        assert base_url == "" and token == "", "全离线不许有网络依赖"
        self.calls.append({"it": int(m["it"]), "init_fp": m["init_weights_fp"]})
        weights = json.dumps({"it": m["it"], "n": len(self.calls)}).encode()
        return {
            "job_id": m["job_id"],
            "data_fp": m["data_fp"],
            "init_weights_fp": m["init_weights_fp"],
            "weights_json": encode_weights_json(weights),
            "opt_tar_b64": encode_opt_tar(b"opt-" + str(m["it"]).encode()),
            "agg": {
                "policy": 0.1,
                "value": 0.2,
                "entropy": 0.3,
                "kl": 0.01,
                "mean_ret": 0.5,
                "steps": 10,
                "chunks": 1,
            },
            "report": {
                "games": 2,
                "shards": 2,
                "winRate": 0.5,
                "totalSamples": 20,
                "totalTicks": 200,
                "elapsedSec": 1.0,
                "outcomes": {"win": 1, "loss": 1},
            },
            "commit_echo": m["commit"],
            "ppo_sec": 0.5,
            "wire": {},
        }


def test_imported_bundle_runs_whole_segment_without_hub(tmp_path: Path) -> None:
    """核心承诺：导入 → `run_standalone` 从包里的起点跑到 end_it（无网络、无仓库）。"""
    zip_path, _idx, plan = _export(tmp_path, it=3, n=3)
    dest = tmp_path / "art2"
    import_bundle(zip_path, dest)
    fake = _FakeRunJob()
    res = run_standalone(
        artifacts_dir=dest, run_job_fn=fake, code_cache_dir=tmp_path / "cc", log=_quiet
    )
    want = planned_iters(plan)  # 4, 5
    assert [c["it"] for c in fake.calls] == want
    assert fake.calls[0]["init_fp"] == sha256_bytes(INIT_W)  # 起点就是包里的权重
    assert res["it_end"] == plan["end_it"]
    store = ArtifactStore(dest, run_id="run-bundle")
    for it in [plan["start_it"], *want]:
        assert store.weights_path(it).exists()
    rows = [json.loads(x) for x in (dest / "metrics.jsonl").read_text().splitlines() if x.strip()]
    # 账本只记**真跑过的轮**（起点是 checkpoint 不放账本——`ArtifactStore.checkpoint(row=None)`）
    assert [r["it"] for r in rows] == want
    assert json.loads((dest / "state.json").read_text(encoding="utf-8"))["state"] == "complete"
    # 再跑一次：无事可做（续跑判定认得出同一段；不会从 start_it 重来）
    again = _FakeRunJob()
    res2 = run_standalone(
        artifacts_dir=dest, run_job_fn=again, code_cache_dir=tmp_path / "cc2", log=_quiet
    )
    assert again.calls == []
    assert res2["run_state"] == "noop" and res2["it_end"] == plan["end_it"]


def test_payload_of_offline_round_carries_init_weights(tmp_path: Path) -> None:
    """逐轮 payload 仍带 `init_weights.json`（与在线上云轮同构；替身/真身都靠它取起点）。"""
    zip_path, _idx, _plan = _export(tmp_path, it=2, n=1)
    dest = tmp_path / "art3"
    import_bundle(zip_path, dest)

    seen: list[bytes] = []

    def _spy(base_url: str, token: str, job: dict, **kw: object) -> dict:
        pl = kw["preloaded"]
        assert isinstance(pl, dict)
        z = tmp_path / "payload.tar.xz"
        z.write_bytes(pl["payload_zip"])
        out = tmp_path / "unpack"
        out.mkdir(parents=True, exist_ok=True)
        unpack_payload(z, out)
        seen.append((out / "init_weights.json").read_bytes())
        return _FakeRunJob()(base_url, token, job, **kw)

    run_standalone(artifacts_dir=dest, run_job_fn=_spy, code_cache_dir=tmp_path / "cc3", log=_quiet)
    assert seen and seen[0] == INIT_W


# ────────────────────────── demo bank 离线件 ──────────────────────────

DEMO_RAW = b"demo-npz" + b"y" * 64


def test_export_import_carries_demo_blob(tmp_path: Path) -> None:
    """demo 腿任务包自动带 demo.npz：job 目录 blob.demo → 包件 → 导入落盘（sha 对账）。"""
    from remote.protocol import blob_path

    plan = build_plan(
        _args(), it=2, iters_total=4, rotate_seed=5, max_iters=3, log=_quiet
    )
    m = _manifest(plan, it=3, code_sha=sha256_bytes(CODE_ZIP))
    m["demo_sha"] = sha256_bytes(DEMO_RAW)
    m["demo_bc_coef"] = 0.02
    m["demo_per_mb"] = 128
    m = normalize_manifest(m)
    src = tmp_path / "srcd"
    src.mkdir(parents=True, exist_ok=True)
    (src / "init_weights.json").write_bytes(INIT_W)
    (src / "code.zip").write_bytes(CODE_ZIP)
    with zipfile.ZipFile(src / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/export-rl-rollout.ts", "// ts\n")
    jd = tmp_path / "job"
    jd.mkdir(parents=True, exist_ok=True)
    blob_path(jd, "demo").write_bytes(DEMO_RAW)
    out = tmp_path / "task-demo.zip"
    index = export_bundle(
        out,
        manifest=m,
        plan_bytes=dump_plan(plan),
        init_weights_path=src / "init_weights.json",
        code_zip_path=src / "code.zip",
        ts_code_zip_path=src / "ts_code.zip",
        job_dir=jd,
        hub_url="https://hub.example",
        note="test demo export",
    )
    assert "demo.npz" in index["parts"], index["parts"].keys()
    dest = tmp_path / "artd"
    import_bundle(out, dest)
    assert (dest / "demo.npz").read_bytes() == DEMO_RAW


def test_seed_demo_blob_cache_from_artifacts(tmp_path: Path) -> None:
    """启动期种子：产物目录 demo.npz → blob_cache/<sha>；二次调用命中；缺件响亮拒绝。"""
    from remote.artifacts import sha256_bytes as _sha
    from remote.run_loop import _seed_demo_blob_cache

    raw = b"demo-npz" + b"z" * 32
    sha = _sha(raw)
    art = tmp_path / "art"
    art.mkdir(parents=True, exist_ok=True)
    (art / "demo.npz").write_bytes(raw)
    work = tmp_path / "work"
    m = {"demo_sha": sha}
    _seed_demo_blob_cache(
        manifest=m, job_dir=tmp_path / "jd", work_dir=work, artifacts_root=art, log=_quiet
    )
    assert (work / "blob_cache" / sha).read_bytes() == raw
    # 二次调用走缓存命中（删源文件仍能过）
    (art / "demo.npz").unlink()
    _seed_demo_blob_cache(
        manifest=m, job_dir=tmp_path / "jd", work_dir=work, artifacts_root=art, log=_quiet
    )
    # 缺件：响亮拒绝（不等 it1 PPO 才炸）
    with pytest.raises(ProtocolError):
        _seed_demo_blob_cache(
            manifest=m,
            job_dir=tmp_path / "jd2",
            work_dir=tmp_path / "work2",
            artifacts_root=tmp_path / "empty",
            log=_quiet,
        )
    # 未开 demo（无 sha）：静默 no-op
    _seed_demo_blob_cache(
        manifest={}, job_dir=tmp_path, work_dir=tmp_path / "work3", artifacts_root=None,
        log=_quiet,
    )
    assert not (tmp_path / "work3" / "blob_cache").exists()
