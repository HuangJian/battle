"""`ShardSegments` —— payload 零落盘的本轮段账（2026-10-27，plan/payload-zero-disk §3/§6）。

不变量（每条对应方案的一节）：

1. **段 == 盘**（§6.1 的判据本体）：同一个 shard，内存里打出的段与「从落盘目录重打」
   逐字节相同 —— 零落盘**不改变 payload**。`manifest.json` 也在段里，且字节口径与
   `write_shard` 同一处（`common.shard.manifest_bytes`）。
2. **每轮一份**（§3.3 第 1 步）：`begin()` 换轮即清空。上一轮的段漏进这一轮会是一份
   「`data_fp` 对不上但字节合法」的静默错包 —— 本类最危险的失败形态。
3. **开关同源**（§3.2）：段里有没有 `reward.npy` / f16 由**建账时**的 `wire_diet` 决定，
   `take()` 拿它跟读的时候比 —— 热改开关后旧段一律不用（退回读盘）。
4. **local 兜底腿**（§3.4）：段里缺某个 shard ⇒ 现场读盘补，同一个 `shard_segment` 函数体。

为什么在 `tests/remote/`：账本本体住在 `remote/payload_pack.py`（「发布端 payload 字节」的
唯一所有者）—— `common/payload.py` 的 import 面是闭集（`tests/common/test_payload_split.py`
钉着），不许引 lzma / 线程池。
"""

from __future__ import annotations

import io
import json
import lzma
import sys
import tarfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_library import METRICS_DIM, RewardSpec
from common.payload import shard_segment
from common.shard import SHARD_FILES, manifest_bytes, write_shard
from remote.payload_pack import ShardSegments, pack_payload_zip
from remote.payload_slim import build_slim

STAGE = 2000


def _files(tag: int) -> dict[str, bytes]:
    """一个 shard 的内存字节（内容无关紧要 —— 本文件只对「字节怎么搬」负责）。"""
    return {name: (f"{name}-{tag}".encode() * (1 + tag)) for name in SHARD_FILES}


def _manifest(tag: int) -> dict:
    return {"stage": STAGE + tag, "seed": tag, "nSamples": 3 + tag, "wver": "w" * 8}


def _members(payload: Path) -> dict[str, bytes]:
    with tarfile.open(payload, "r:xz") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf if m.isfile()}  # type: ignore[union-attr]


def _expect(name: str, files: dict[str, bytes], manifest: dict) -> dict[str, bytes]:
    """落盘那份的成员全集（`write_shard` 写什么，段里就该有什么）。"""
    out = {f"{name}/{k}": v for k, v in files.items()}
    out[f"{name}/manifest.json"] = manifest_bytes(manifest)
    return out


def test_memory_segment_equals_disk_segment(tmp_path: Path) -> None:
    """不变量 1：内存段 == 盘段（**含** manifest.json，逐字节）。"""
    files, man = _files(3), _manifest(3)
    name = f"rl_s{STAGE + 3}_seed3"
    d = tmp_path / name
    write_shard(files, man, str(d))

    parts = ShardSegments()
    parts.begin(5, wire_diet=False)
    parts.add(name, files, man)
    took = parts.take(5, wire_diet=False)
    assert took is not None
    preseg, _slim = took

    assert lzma.decompress(preseg[name]) == shard_segment(name, shard_dir=d)
    # 反过来也钉一次「manifest.json 真的在段里」：解包端只把带它的目录当 shard。
    got = _members_of_segment(preseg[name])
    assert got == _expect(name, files, man), sorted(got)


def _members_of_segment(seg_compressed: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(seg_compressed)), mode="r:") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf if m.isfile()}  # type: ignore[union-attr]


def test_presegmented_wins_over_disk(tmp_path: Path) -> None:
    """判据本体（§6.1）：段被**原样采用** —— 打包前把盘上内容改掉，结果仍是收到时那份。"""
    root = tmp_path / "it9"
    names = [f"rl_s{STAGE}_seed1", f"rl_s{STAGE}_seed2"]
    files = {n: _files(i + 1) for i, n in enumerate(names)}
    mans = {n: _manifest(i + 1) for i, n in enumerate(names)}
    dirs = []
    for n in names:
        d = root / "dist" / "node-a" / n
        write_shard(files[n], mans[n], str(d))
        dirs.append(d)

    parts = ShardSegments()
    parts.begin(9, wire_diet=False)
    for n in names:
        parts.add(n, files[n], mans[n])
    took = parts.take(9, wire_diet=False)
    assert took is not None

    # 打包前把所有盘上内容改掉：若实现偷偷读盘，下面的断言必红。
    for d in dirs:
        (d / "obs.npy").write_bytes(b"\xff" * 7)

    out = tmp_path / "payload.tar.xz"
    pack_payload_zip(dirs, [], out, {}, chunks=8, presegmented=took[0])
    got = _members(out)
    want: dict[str, bytes] = {}
    for n in names:
        want.update(_expect(n, files[n], mans[n]))
    assert got == want, "预分段必须原样采用（零落盘）"


def test_missing_segment_falls_back_to_disk(tmp_path: Path) -> None:
    """不变量 4：段里缺的 shard 现场读盘补（local 兜底腿 / 预采子进程走的就是这条）。"""
    a, b = f"rl_s{STAGE}_seed1", f"rl_s{STAGE}_seed2"
    files_a, files_b = _files(1), _files(2)
    man_a, man_b = _manifest(1), _manifest(2)
    da = tmp_path / "dist" / "node-a" / a
    db = tmp_path / "dist" / "local" / b
    write_shard(files_a, man_a, str(da))
    write_shard(files_b, man_b, str(db))
    # B 只有盘上的版本（local 腿），且打包前被改写过 ⇒ 打包必须拿到**改写后**的字节。
    (db / "obs.npy").write_bytes(b"disk-only-b")

    parts = ShardSegments()
    parts.begin(4, wire_diet=False)
    parts.add(a, files_a, man_a)  # 只收 A（dispatch 腿）；B 没进段
    took = parts.take(4, wire_diet=False)
    assert took is not None

    out = tmp_path / "payload.tar.xz"
    pack_payload_zip([da, db], [], out, {}, chunks=8, presegmented=took[0])
    got = _members(out)
    assert got[f"{a}/obs.npy"] == files_a["obs.npy"], "有段 ⇒ 原样用段（不读盘）"
    assert got[f"{b}/obs.npy"] == b"disk-only-b", "没段 ⇒ 必须读盘"
    # 两条腿的成员集同一个口径（manifest.json 也在）
    assert set(got) == set(_expect(a, files_a, man_a)) | {
        f"{b}/{k}" for k in [*files_b, "manifest.json"]
    }


def test_parts_are_cleared_when_the_round_changes() -> None:
    """不变量 2：换轮即清空，且**上一轮的段不许被这一轮取到**（静默错包）。"""
    parts = ShardSegments()
    parts.begin(1, wire_diet=False)
    parts.add(f"rl_s{STAGE}_seed1", _files(1), _manifest(1))
    assert parts.take(1, wire_diet=False) is not None

    parts.begin(2, wire_diet=False)
    assert parts.take(2, wire_diet=False) is None, "新一轮还没有段"
    assert parts.take(1, wire_diet=False) is None, "上一轮的段必须已经清掉"

    parts.add(f"rl_s{STAGE}_seed2", _files(2), _manifest(2))
    got = parts.take(2, wire_diet=False)
    assert got is not None
    assert set(got[0]) == {f"rl_s{STAGE}_seed2"}


def test_take_rejects_a_different_wire_diet() -> None:
    """不变量 3：`wire_diet` 在轮中途被热改 ⇒ 旧段不可用（它们与 manifest 自述不符）。"""
    parts = ShardSegments()
    parts.begin(3, wire_diet=False)
    parts.add(f"rl_s{STAGE}_seed1", _files(1), _manifest(1))
    assert parts.take(3, wire_diet=True) is None, "段是按 wire_diet=0 建的，不能冒充瘦身后的字节"
    assert parts.take(3, wire_diet=False) is not None


def _npy(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _spec() -> RewardSpec:
    return RewardSpec(
        formula="wKill*kills + wWin*where(clearTick>=0, 1, 0)",
        params={"wKill": 3.0, "wWin": 48.0},
        terminal={"timeout": -35.0, "lives_exhausted": -35.0},
        scheme="toy",
    )


def _rl_files(n: int = 5) -> tuple[dict[str, bytes], dict]:
    """一个**能被 slim 变换**的 per-tick RL shard（真 npy）+ 它的 manifest。"""
    rng = np.random.default_rng(7)
    files = {name: b"x" for name in SHARD_FILES}
    files.update(
        {
            "obs.npy": _npy(rng.integers(0, 2, size=(n, 4, 4), dtype=np.uint8)),
            "scalars.npy": _npy(rng.random((n, 3), dtype=np.float32)),
            "extra.npy": _npy(rng.random((n, 2), dtype=np.float32)),
            "lp_move.npy": _npy(rng.random(n, dtype=np.float32)),
            "lp_fire.npy": _npy(rng.random(n, dtype=np.float32)),
            "metrics.npy": _npy(rng.random((n + 1, METRICS_DIM), dtype=np.float64)),
        }
    )
    man = {
        "stage": STAGE,
        "seed": 1,
        "nSamples": n,
        "outcome": "lives_exhausted",
        "score": 0.0,
        "metrics_version": 12,
    }
    return files, man


def test_slim_transforms_land_in_the_segment() -> None:
    """§3.2：变换发生在**收到时**，且同一份 slim 的读数（`fmt`）跟着段一起交给打包端。"""
    files, man = _rl_files()
    spec = _spec()
    name = f"rl_s{STAGE}_seed1"
    parts = ShardSegments()
    parts.begin(
        7,
        wire_diet=True,
        slim=build_slim(reward_spec=spec, it=7, metrics_version=12, formula_hash="fh"),
    )
    parts.add(name, files, man)
    took = parts.take(7, wire_diet=True)
    assert took is not None
    preseg, slim = took

    got = _members_of_segment(preseg[name])
    keys = set(got)
    assert keys.isdisjoint({f"{name}/metrics.npy"}), "P1：metrics 换成 reward.npy（不再有 metrics 成员）"
    assert {f"{name}/reward.npy"} <= keys
    meta = json.loads(got[f"{name}/reward_meta.json"])
    assert meta == {
        "reward_version": meta["reward_version"],
        "it": 7,
        "metrics_version": 12,
        "formula_hash": "fh",
    }
    # P2：f16 字段换了精度，obs（不在名单）原样。
    assert np.load(io.BytesIO(got[f"{name}/scalars.npy"])).dtype == np.float16
    assert got[f"{name}/obs.npy"] == files["obs.npy"]
    # 打包端的读数来自**同一份** slim（段全部命中时打包腿一次变换都不会走）。
    assert slim.fmt == "reward+f16", slim.fmt


def test_publish_job_uses_the_round_segments_and_their_slim(tmp_path: Path) -> None:
    """§3.2 的接线：`publish_job` 把本轮段**原样**打进 payload，且 `payload_fmt` 来自
    产出它们的那份 slim（另建一份会报「未改」—— 日志骗人）。"""
    from remote.hub_client import publish_job

    files, man = _rl_files()
    name = f"rl_s{STAGE}_seed1"
    it_dir = tmp_path / "it1"
    d = it_dir / "dist" / "a97" / name
    write_shard(files, man, str(d))

    parts = ShardSegments()
    parts.begin(
        1,
        wire_diet=True,
        slim=build_slim(reward_spec=_spec(), it=1, metrics_version=12, formula_hash="fh"),
    )
    parts.add(name, files, man)
    took = parts.take(1, wire_diet=True)
    assert took is not None

    # 盘上内容改掉：命中的段被原样采用 ⇒ 这些字节不该出现在 payload 里。
    (d / "obs.npy").write_bytes(b"\x00disk")

    m = publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=tmp_path / "training_log.jsonl",
        run_id="r",
        it=1,
        traj_dir=str(it_dir),
        shard_dirs=[d],
        commit="c" * 40,
        code_sha256="z" * 64,
        course="{}",
        course_fp="f" * 64,
        reward_formula="score",
        formula_hash="fh",
        metrics_version=12,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=8,
        lr=3e-4,
        wire_diet=True,
        reward_spec=_spec(),
        presegmented=took[0],
        diet_slim=took[1],
        log=lambda _m: None,
    )
    payload = tmp_path / "jobs" / str(m["job_id"]) / "payload.tar.xz"
    got = _members(payload)
    keys = set(got)
    assert keys.isdisjoint({f"{name}/metrics.npy"}), "段里有 P1 的变换 ⇒ payload 里不该再有 metrics"
    assert {f"{name}/reward.npy"} <= keys
    assert got[f"{name}/obs.npy"] == files["obs.npy"], "段被原样采用（不是被改写后的盘）"
    # `payload_fmt` 来自**产出那些段的那份** slim（另建一份会报「未改」——日志骗人）
    assert m["payload_fmt"] == "reward+f16"
