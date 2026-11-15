"""P2（plan/replay-corpus.plan.md）：worker 侧 replay 语料 pre-pass 的契约测试。

覆盖：
  ① 转换器身份闸（缺文件 / 缺 manifest sha / sha 不符 ⇒ 响亮拒收）；
  ② 逐 shard 转换 + 产物齐全断言 + 完成标记；
  ③ 续跑复用（标记匹配 ⇒ 不重复起子进程）；
  ④ 转换器失败 ⇒ ProtocolError（确定性失败，不静默跳过）；
  ⑤ 转换器换了（标记里的 sha 不符）⇒ 重跑，不复用旧产物。

真正的 replay→obs 数值 identity 由 TS 侧 `tests/sim/export-rl-rollout-replay.test.ts`
钉住；这里用假转换器只验证 worker 编排面。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from remote import replay_corpus as rc

CONV = """\
import sys, json
from pathlib import Path
import numpy as np
args = sys.argv[1:]
d = Path(args[args.index('--shard-dir') + 1])
if (d / 'FAIL').exists():
    print('boom', file=sys.stderr)
    sys.exit(3)
n = json.loads((d / 'replay_meta.json').read_text(encoding='utf-8'))['nSamples']
np.save(d / 'obs.npy', np.zeros((n, 2), dtype=np.uint8))
np.save(d / 'scalars.npy', np.zeros((n, 3), dtype=np.float32))
np.save(d / 'extra.npy', np.zeros((n, 4), dtype=np.float32))
print('ok')
"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _job(tmp_path: Path, n_shards: int = 2, *, converter: bytes | None = None) -> tuple[Path, dict, list[Path]]:
    job = tmp_path / "job"
    job.mkdir()
    conv = converter if converter is not None else CONV.encode()
    (job / rc.CONVERTER_NAME).write_bytes(conv)
    shards: list[Path] = []
    for i in range(n_shards):
        d = job / f"rl_s2000_seed{i}"
        d.mkdir()
        (d / "replay.json").write_text("{}", encoding="utf-8")
        (d / "replay_meta.json").write_text(json.dumps({"nSamples": 4}), encoding="utf-8")
        shards.append(d)
    man = {"kind": "ppo", "corpus_fmt": "replay", "replay_converter_sha256": _sha(conv)}
    return job, man, shards


@pytest.fixture(autouse=True)
def _python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 JS 运行时换成本测试的 python（假转换器是 .py；编排面与运行时无关）。"""
    monkeypatch.setattr(rc, "resolve_js_runtime", lambda: sys.executable)


def test_converter_identity_is_a_hard_gate(tmp_path: Path) -> None:
    job, man, _shards = _job(tmp_path)
    assert rc.verify_converter(job, man)[0].name == rc.CONVERTER_NAME
    # manifest 缺 sha
    with pytest.raises(ProtocolError, match="replay_converter_sha256"):
        rc.verify_converter(job, {**man, "replay_converter_sha256": ""})
    # 文件被换
    (job / rc.CONVERTER_NAME).write_bytes(b"tampered")
    with pytest.raises(ProtocolError, match="sha 不符"):
        rc.verify_converter(job, man)
    # 文件缺失
    (job / rc.CONVERTER_NAME).unlink()
    with pytest.raises(ProtocolError, match=r"缺 replay_converter\.js"):
        rc.verify_converter(job, man)


def test_converts_all_shards_and_writes_marker(tmp_path: Path) -> None:
    job, man, shards = _job(tmp_path)
    got = rc.convert_replay_corpus(job, shards, man, workers=2)
    assert got["shards"] == 2 and got["reused"] == 0
    for d in shards:
        for name in ("obs.npy", "scalars.npy", "extra.npy"):
            assert (d / name).is_file(), f"{d.name}/{name} 缺失"
        marker = json.loads((d / rc.MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["converter_sha256"] == man["replay_converter_sha256"]
        assert marker["version"] == rc.MARKER_VERSION


def test_resume_reuses_matching_markers(tmp_path: Path) -> None:
    job, man, shards = _job(tmp_path)
    rc.convert_replay_corpus(job, shards, man, workers=2)
    got = rc.convert_replay_corpus(job, shards, man, workers=2)
    assert got["reused"] == 2


def test_failed_conversion_is_loud_and_leaves_no_marker(tmp_path: Path) -> None:
    job, man, shards = _job(tmp_path, n_shards=1)
    (shards[0] / "FAIL").write_text("1", encoding="utf-8")
    with pytest.raises(ProtocolError, match="replay 转换失败"):
        rc.convert_replay_corpus(job, shards, man)
    assert not (shards[0] / rc.MARKER_NAME).exists()


def test_changed_converter_invalidates_marker(tmp_path: Path) -> None:
    job, man, shards = _job(tmp_path, n_shards=1)
    rc.convert_replay_corpus(job, shards, man)
    # 换转换器 + 换 manifest sha ⇒ 旧标记不可复用，必须重跑。
    new_conv = (CONV + "\n# v2\n").encode()
    (job / rc.CONVERTER_NAME).write_bytes(new_conv)
    man2 = {**man, "replay_converter_sha256": _sha(new_conv)}
    got = rc.convert_replay_corpus(job, shards, man2)
    assert got["reused"] == 0
    marker = json.loads((shards[0] / rc.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["converter_sha256"] == _sha(new_conv)
    # 重跑一遍才复用
    assert rc.convert_replay_corpus(job, shards, man2)["reused"] == 1
