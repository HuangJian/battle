"""replay 转换的**池化路径**：暖 worker 跑转换，且失败一律回退一次性 spawn。

为什么（2026-11-16 云机 P0 ⑤ 实测）：每 shard 起一个新进程要付 ~0.45–0.6 s 启动 + 模块加载，
转换本身只有 126 ms/shard ⇒ 350 shard 白烧 130 s，700 shard 规模把 P0 的「转换 ≤2 min」顶到 3 倍。
修法 = 接进长驻池（`worker/serve_pool.py`，协议唯一实现 `tools/sim/serve-loop.ts`）。

本文件钉的是 **Python 编排面**（转换器本身的 serve 等价性由
`tests/replay-to-shard-serve.test.ts` 逐字节钉住）：
  ① 有池时每个 shard 都是一条池任务（`[converter, '--shard-dir', <tmp>]`），不是冷启动子进程；
  ② 池给不出确定成功（`try_capture` 返回 None）⇒ **回退一次性**且仍产出合格产物（只慢不错）；
  ③ 池在 finally 里被 `close()` + 打一行 summary（否则留下常驻进程、也看不到省了多少）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import replay_corpus as rc
from worker import serve_pool

CONV = """\
import sys, json
from pathlib import Path
import numpy as np
args = sys.argv[1:]
d = Path(args[args.index('--shard-dir') + 1])
n = json.loads((d / 'replay_meta.json').read_text(encoding='utf-8'))['nSamples']
np.save(d / 'obs.npy', np.zeros((n, 2), dtype=np.uint8))
np.save(d / 'scalars.npy', np.zeros((n, 3), dtype=np.float32))
np.save(d / 'extra.npy', np.zeros((n, 4), dtype=np.float32))
print('ok')
"""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _job(tmp_path: Path, n_shards: int = 2) -> tuple[Path, dict, list[Path]]:
    job = tmp_path / 'job'
    job.mkdir()
    conv = CONV.encode()
    (job / rc.CONVERTER_NAME).write_bytes(conv)
    shards: list[Path] = []
    for i in range(n_shards):
        d = job / f'rl_s2000_seed{i}'
        d.mkdir()
        (d / 'replay.json').write_text('{}', encoding='utf-8')
        (d / 'replay_meta.json').write_text(json.dumps({'nSamples': 4}), encoding='utf-8')
        shards.append(d)
    man = {'kind': 'ppo', 'corpus_fmt': 'replay', 'replay_converter_sha256': _sha(conv)}
    return job, man, shards


@pytest.fixture(autouse=True)
def _python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 JS 运行时换成本测试的 python（假转换器是 .py；编排面与运行时无关）。"""
    monkeypatch.setattr(rc, 'resolve_js_runtime', lambda: sys.executable)


class _FakePool:
    """把每条任务**真的**跑掉（python 跑假转换器），并记下任务形状。"""

    def __init__(self) -> None:
        self.tasks: list[list[str]] = []
        self.closed = False
        self.summarised = 0

    def start(self) -> int:
        return 1

    def try_capture(
        self, argv: list[str], timeout_sec: float, *, label: str = '?', attempt: int = 1,
        kind: str = 'eval', where: str = '',
    ) -> tuple[float, list[str]] | None:
        self.tasks.append(list(argv))
        subprocess.run([sys.executable, *argv], check=True, capture_output=True)
        return (0.01, [])

    def summary(self) -> str:
        self.summarised += 1
        return f'[fake-pool] served={len(self.tasks)}'

    def close(self) -> None:
        self.closed = True


class _DeadPool(_FakePool):
    """`try_capture` 一律返回 None（池超时/熔断/握不上手）⇒ 必须回退一次性。"""

    def try_capture(self, argv, timeout_sec, **kw):  # type: ignore[no-untyped-def]
        self.tasks.append(list(argv))
        return None


def test_pool_path_converts_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, man, shards = _job(tmp_path)
    pool = _FakePool()
    monkeypatch.setattr(serve_pool, 'make_pool', lambda *a, **k: pool)
    got = rc.convert_replay_corpus(
        job, shards, man, workers=2, pool_factory=serve_pool.pool_for_bundle
    )

    assert got['convert_jobs'] == 2 and got['reused'] == 0
    assert len(pool.tasks) == 2, '每个 shard 应恰好一条池任务'
    for argv in pool.tasks:
        assert argv[1] == '--shard-dir'
        assert argv[2].endswith('.conv-tmp'), '池任务打的是事务目录，不是已换入的 shard'
    for d in shards:
        for name in ('obs.npy', 'scalars.npy', 'extra.npy'):
            assert (d / name).is_file(), f'{d.name}/{name} 缺失'
        assert json.loads((d / rc.MARKER_NAME).read_text(encoding='utf-8'))['version'] == rc.MARKER_VERSION
    assert pool.closed is True, '池必须在 finally 里关掉，否则留下常驻进程'
    assert pool.summarised == 1, '轮末要留一行池读数'


def test_pool_failure_falls_back_to_oneshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, man, shards = _job(tmp_path)
    pool = _DeadPool()
    monkeypatch.setattr(serve_pool, 'make_pool', lambda *a, **k: pool)
    got = rc.convert_replay_corpus(
        job, shards, man, workers=1, pool_factory=serve_pool.pool_for_bundle
    )

    assert got['convert_jobs'] == 2
    assert len(pool.tasks) == 2, '池先被问过'
    for d in shards:
        for name in ('obs.npy', 'scalars.npy', 'extra.npy'):
            assert (d / name).is_file(), f'回退后仍必须产出（只慢不错）：{d.name}/{name}'
        assert (d / rc.MARKER_NAME).is_file()
    assert pool.closed is True


def test_pool_passed_fixed_mode_and_bundle_entry() -> None:
    """池入口 = 预建 bundle 自己（payload 里没有 TS 树），mode 由池钉成 conv。"""
    seen: dict[str, object] = {}

    def fake_make_pool(*a, **k):  # type: ignore[no-untyped-def]
        seen.update(k)
        return None

    orig = serve_pool.make_pool
    serve_pool.make_pool = fake_make_pool  # type: ignore[assignment]
    try:
        out = serve_pool.pool_for_bundle(
            'bun-stub', Path('/x/replay_converter.js'), 3, lambda _m: None
        )
    finally:
        serve_pool.make_pool = orig  # type: ignore[assignment]

    assert out is None
    assert seen['entry'] == 'replay_converter.js'
    assert seen['fixed_mode'] == 'conv'
    assert seen['for_script'] == str(Path('/x/replay_converter.js'))
