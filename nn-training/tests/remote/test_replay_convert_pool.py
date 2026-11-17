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


class _RecordingPool(_FakePool):
    """只记任务形状、不跑子进程 —— 用于验证 verify-only 不落产物。"""

    def try_capture(
        self, argv, timeout_sec, *, label='?', attempt=1, kind='eval', where=''
    ):  # type: ignore[no-untyped-def]
        self.tasks.append(list(argv))
        return (0.01, [])


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


def test_convert_workers_scales_with_physical_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省宽度 = 物理核数（write）/ ⌈11/8 × 物理核⌉（verify-only），再受 shard 数收敛。

    8 物理/16 逻辑的标定：write 8w 最稳、verify-only 11w 最快。宽度必须跟着机器走，
    不能把 8/11 写死（4 核云机会过订阅，16 物理核又白留一半）。
    """
    # (物理核, write, verify-only)
    for cores, write, verify in (
        (1, 1, 2),
        (2, 2, 3),
        (4, 4, 6),
        (6, 6, 9),
        (8, 8, 11),
        (12, 12, 17),
    ):
        monkeypatch.setattr(rc, 'physical_cores', lambda c=cores: c)
        assert rc.convert_workers() == write, f'physical={cores}'
        assert rc.convert_workers(verify_only=True) == verify, f'physical={cores}'
    # 显式值优先（不被核数夹取），非法值收敛到 ≥1
    monkeypatch.setattr(rc, 'physical_cores', lambda: 8)
    assert rc.convert_workers(1) == 1
    assert rc.convert_workers(12) == 12
    assert rc.convert_workers(0) == 1
    assert rc.convert_workers(-3) == 1
    # 少于宽度时按 shard 数收敛（别为 3 个 shard 起 11 个 worker）
    assert rc.convert_workers(shards=3) == 3
    assert rc.convert_workers(shards=350) == 8
    assert rc.convert_workers(verify_only=True, shards=3) == 3
    assert rc.convert_workers(verify_only=True, shards=350) == 11


def test_str_shard_dirs_from_unpack_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**生产形状**：`unpack_payload` 扫出来的是 str 目录（`download.py:810`）。

    2026-11-17 云端第一次真跑 replay：`_corpus_bytes` 里 `sd / "replay.json"` 收到 str ⇒
    `TypeError: unsupported operand type(s) for /: 'str' and 'str'`，job 报 FAILED 后
    无限重领，队列看着像卡住（日志只有一行 FAILED，没有 traceback）。这里把入口归一
    钉住：str 与 Path 两种形状必须产出同一份读数与产物。
    """
    job, man, shards = _job(tmp_path)
    pool = _FakePool()
    monkeypatch.setattr(serve_pool, 'make_pool', lambda *a, **k: pool)

    as_str = [str(d) for d in shards]
    got = rc.convert_replay_corpus(
        job, as_str, man, workers=2, pool_factory=serve_pool.pool_for_bundle
    )

    assert got['convert_jobs'] == 2 and got['reused'] == 0
    assert got['shards'] == 2
    for d in shards:
        for name in ('obs.npy', 'scalars.npy', 'extra.npy'):
            assert (d / name).is_file(), f'str 入口也必须真的转出来：{d.name}/{name}'
    # 池任务的 label 取的是 `sd.name`（str 上没有 .name ⇒ 入口归一前必炸）
    assert all(argv[2].endswith('.conv-tmp') for argv in pool.tasks)
    assert pool.closed is True


def test_verify_only_uses_11_width_and_skips_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify-only 是诊断路径：缺省 11（受 shard 收敛），且不写产物/标记/事务目录。"""
    job, man, shards = _job(tmp_path)
    pool = _RecordingPool()
    seen: list[int] = []

    def fake_factory(runtime: str, converter: Path, workers: int):
        seen.append(workers)
        return pool

    monkeypatch.setattr(rc, 'physical_cores', lambda: 8)
    got = rc.convert_replay_corpus(job, shards, man, verify_only=True, pool_factory=fake_factory)

    assert seen == [2], '11 受 2 个 shard 收敛'
    assert got['convert_workers'] == 2
    assert got['convert_jobs'] == 2 and got['reused'] == 0
    for argv in pool.tasks:
        assert argv[1] == '--shard-dir'
        assert not argv[2].endswith('.conv-tmp'), 'verify-only 打真实 shard，不建事务目录'
        assert argv[-1] == '--verify-only'
    for d in shards:
        assert not (d / rc.MARKER_NAME).exists(), 'verify-only 不写完成标记'
        for name in ('obs.npy', 'scalars.npy', 'extra.npy'):
            assert not (d / name).exists(), f'verify-only 不写 {name}'
    assert pool.closed is True and pool.summarised == 1


def test_pool_width_and_readout_follow_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """池宽度与读数都跟着解析出的宽度走（不是跟着缺省值走）。"""
    job, man, shards = _job(tmp_path)
    pool = _FakePool()
    seen: list[int] = []

    def fake_factory(runtime: str, converter: Path, workers: int):
        seen.append(workers)
        return pool

    monkeypatch.setattr(rc, 'physical_cores', lambda: 7)
    got = rc.convert_replay_corpus(job, shards, man, pool_factory=fake_factory)
    # 2 个 shard ⇒ 宽度收敛到 2（不为 2 个 shard 起 7 个 worker）
    assert seen == [2], '工厂拿到的是 min(核数, shard 数)'
    assert got['convert_workers'] == 2, '读数里要能看见实际宽度'

    # 显式 width 覆盖（同样受 shard 数约束）
    got2 = rc.convert_replay_corpus(job, shards, man, workers=3, pool_factory=fake_factory)
    assert seen == [2, 2] and got2['convert_workers'] == 2
