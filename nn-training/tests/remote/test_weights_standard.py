"""tests/remote/test_weights_standard.py —— 权重 delta 的**读侧自动落地**（2026-10-15 事故的修复面）。

事故：离线段回传的 int16 delta 容器被 hub 原样落进 `<traj>/weights.json`、`deliver/` 镜像与
`nn-training/weights/<课>/` 归档；本机消费者（trainer → sampler 节点、本机 PPO 装载、TS 评估）
只认整份 ⇒ 每个节点 `JSON.parse` 报 `Unexpected identifier "BWD1"`，云机下一轮 init 也成了容器。

用户定案（本例钉住它）：**hub 不变，消费者自己判断** —— 读之前 `ensure_standard_weights` 落地
（就地；delta 旁存 `.weights-delta/`，两版都留）；**只认逐字节精确命中的基准**，找不到就响亮
拒绝（落错基准 = 形状对、数值错，比拒绝危险）；**转一次一直复用**（幂等、热路径只读 4 字节）；
云机侧用 `standard_weights_copy`（store 本体保持 delta 给 deliver 腿发，整份写旁路缓存）。
"""

from __future__ import annotations

import base64
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
from remote import weights_delta as wd
from remote import weights_standard as ws
from remote.artifacts import WEIGHTS_ARCHIVE_ROOT_ENV

SHAPES = {"conv.w": (4, 3, 3, 3), "fc.b": (16,), "head.w": (16, 165)}


def _weights_json(params: dict[str, np.ndarray]) -> bytes:
    return json.dumps(
        {
            "format": "nn-weights-json",
            "version": 1,
            "schema_major": 3,
            "arch": {"hidden": 128},
            "num_params": int(sum(int(p.size) for p in params.values())),
            "params": {
                n: {
                    "shape": list(p.shape),
                    "data": base64.b64encode(
                        np.ascontiguousarray(p, dtype="<f4").tobytes()
                    ).decode("ascii"),
                }
                for n, p in params.items()
            },
        },
        indent=2,
    ).encode("utf-8")


def _params(seed: int, *, amp: float = 0.2) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: rng.standard_normal(s).astype(np.float32) * amp for n, s in SHAPES.items()}


def _step(base: dict[str, np.ndarray], *, seed: int, amp: float = 2e-3) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {n: (p + rng.standard_normal(p.shape).astype(np.float32) * amp).astype(np.float32) for n, p in base.items()}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _quiet(_msg: str = "") -> None:
    return None


def _pack(base_bytes: bytes, new_json: bytes) -> bytes:
    return wd.pack_weights_delta(base_bytes, new_json, base_sha=_sha(base_bytes))


def test_standard_file_is_untouched(tmp_path: Path) -> None:
    """整份文件：原样返回、一个字节不动、不建旁存目录（热路径上等于零成本）。"""
    p = tmp_path / "weights.json"
    p.write_bytes(_weights_json(_params(1)))
    before = p.read_bytes()
    assert ws.ensure_standard_weights(p, log=_quiet) == p
    assert p.read_bytes() == before
    assert not (tmp_path / "weights.delta").exists()
    assert ws.is_delta_weights_file(p) is False


def test_ensure_lands_in_place_and_keeps_both_versions(tmp_path: Path) -> None:
    """Delta ⇒ 就地变整份（内容 = base + Δ，量化界内）；delta 原件旁存可回查。"""
    base = _params(1)
    new = _step(base, seed=2)
    anchor = tmp_path / "anchor.it236.json"
    anchor.write_bytes(_weights_json(base))
    blob = _pack(anchor.read_bytes(), _weights_json(new))
    p = tmp_path / "weights.json"
    p.write_bytes(blob)  # hub 原样落下：指针指向容器

    assert ws.ensure_standard_weights(p, course="c", log=_quiet) == p
    got = json.loads(p.read_bytes())
    assert got["format"] == "nn-weights-json" and got["num_params"] > 0
    for n in SHAPES:
        arr = np.frombuffer(base64.b64decode(got["params"][n]["data"]), dtype="<f4")
        assert float(np.abs(arr - new[n].reshape(-1)).max()) <= 1e-4, n
    aside = tmp_path / "weights.delta"  # 两版成对：标准 `.json` + delta `.delta`（用户 2026-10-15）
    assert aside.read_bytes() == blob
    assert anchor.read_bytes() == _weights_json(base)  # 基准本身也不动

    # 幂等 + 复用：再来一次不动文件、不加旁存。
    stamped = p.read_bytes()
    assert ws.ensure_standard_weights(p, course="c", log=_quiet) == p
    assert p.read_bytes() == stamped
    assert sorted(x.name for x in tmp_path.iterdir()) == ["anchor.it236.json", "weights.delta", "weights.json"]


def test_delta_suffixed_input_lands_to_the_json_sibling(tmp_path: Path) -> None:
    """`.delta` 是 delta 版的正身：整份写到兄弟 `.json`，`.delta` 一个字节不动。"""
    base = _params(2)
    anchor = tmp_path / "anchor.json"
    anchor.write_bytes(_weights_json(base))
    blob = _pack(anchor.read_bytes(), _weights_json(_step(base, seed=3)))
    d = tmp_path / "weights.delta"
    d.write_bytes(blob)

    got = ws.ensure_standard_weights(d, course="c", log=_quiet)
    assert got == tmp_path / "weights.json" and json.loads(got.read_bytes())["format"] == "nn-weights-json"
    assert d.read_bytes() == blob, "delta 版不要被就地改写（它就是 `.delta`）"
    assert ws.is_delta_weights_file(d) is True
    # 名字骗人也没关系：内容才是权威（`.delta` 里装整份 ⇒ 不算 delta；`.json` 里装容器 ⇒ 算）。
    liar = tmp_path / "liar.delta"
    liar.write_bytes(got.read_bytes())
    assert ws.is_delta_weights_file(liar) is False


def test_ensure_walks_a_chain_of_deltas(tmp_path: Path) -> None:
    """链式往返：基准**自己也是 delta** ⇒ 先把它落地再比 sha（逐字节对上才用）。

    真实形状：一条段从第 2 轮起，容器头的 base_sha 指的是**上一轮的落地件**——本机可能还没
    读过上一轮（离线流一直在推）⇒ 只能靠递归落地把链根找出来。
    """
    base = _params(1)
    anchor = tmp_path / "anchor.it236.json"
    anchor.write_bytes(_weights_json(base))
    b1 = _pack(anchor.read_bytes(), _weights_json(_step(base, seed=2)))
    p1 = tmp_path / "it-1.json"
    p1.write_bytes(b1)
    landed1 = wd.apply_weights_delta(anchor.read_bytes(), b1, expect_base_sha=_sha(anchor.read_bytes()))
    b2 = _pack(landed1, _weights_json(_step(base, seed=3)))
    p2 = tmp_path / "it-2.json"
    p2.write_bytes(b2)  # 第 2 轮的基准 = 第 1 轮的**落地件**（盘上没有，只能从 b1 递归解出来）

    ws.ensure_standard_weights(p2, course="c", log=_quiet)
    assert p2.read_bytes() == wd.apply_weights_delta(landed1, b2, expect_base_sha=_sha(landed1))
    assert p1.read_bytes() == b1  # 递归只读：只落地被请求的那一份


def test_missing_base_fails_loud_and_does_not_modify(tmp_path: Path) -> None:
    """基准不在 ⇒ `ProtocolError`，消息带 base_sha 与修法；文件一个字节不动（绝不当权重用）。"""
    blob = _pack(_weights_json(_params(7)), _weights_json(_params(8)))
    p = tmp_path / "weights.json"
    p.write_bytes(blob)
    with pytest.raises(ProtocolError) as ei:
        ws.ensure_standard_weights(p, course="c", log=_quiet)
    # 消息必须点名缺的是哪个基准（前 16 位足够定位；修法两条也在消息里，不做文本判据）
    assert wd.base_sha_of(blob)[:16] in str(ei.value)
    assert p.read_bytes() == blob


def test_archive_root_is_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """归档根（`nn-training/weights/<课>/`）是候选根之一 —— 现场 it237 的基准只在这儿齐。"""
    archive = tmp_path / "arch"
    monkeypatch.setenv(WEIGHTS_ARCHIVE_ROOT_ENV, str(archive))
    base = _params(3)
    (archive / "y5-term").mkdir(parents=True)
    anchor = archive / "y5-term" / "y5-term.it236.json"
    anchor.write_bytes(_weights_json(base))
    blob = _pack(anchor.read_bytes(), _weights_json(_step(base, seed=4)))
    p = tmp_path / "weights.json"
    p.write_bytes(blob)

    ws.ensure_standard_weights(p, course="y5-term", log=_quiet)
    got = json.loads(p.read_bytes())
    assert got["format"] == "nn-weights-json" and got["num_params"] > 0


def test_traj_tree_is_searched(tmp_path: Path) -> None:
    """`<traj>/remote-jobs/*/blob.init`、`deliver/*/it-*/` 也算候选（traj 由文件位置推出）。"""
    traj = tmp_path / "y5-pace"
    (traj / "remote-jobs" / "offline" / "abc" / "it-236").mkdir(parents=True)
    base = _params(5)
    anchor = traj / "remote-jobs" / "offline" / "abc" / "it-236" / "weights.json"
    anchor.write_bytes(_weights_json(base))
    blob = _pack(anchor.read_bytes(), _weights_json(_step(base, seed=6)))
    (traj / "weights.json").write_bytes(blob)

    ws.ensure_standard_weights(traj / "weights.json", log=_quiet)
    assert json.loads((traj / "weights.json").read_bytes())["format"] == "nn-weights-json"


def test_cloud_chain_lands_byte_exact_on_the_local_side(tmp_path: Path) -> None:
    """两端链一致（离线双机口径）：云机「delta 基准 = 它实际装载的整份」，本机只拿到
    base + 两轮 delta 也能逐字节重建 —— 这就是离线段逐轮回传之后本机侧的落地条件。

    云机上一轮用的是 `standard_weights_copy`（产物本体保持 delta、旁路缓存整份）⇒ 下一轮
    delta 的 `base_sha` 指的是**落地件**；本机的链式往返把同一份落地件算出来 ⇒ 逐字节对上。
    """
    store = tmp_path / "store"
    (store / "it-236").mkdir(parents=True)
    base_raw = _weights_json(_params(11))
    (store / "it-236" / "weights.json").write_bytes(base_raw)
    new1 = _weights_json(_step(_params(11), seed=12))
    d1 = _pack(base_raw, new1)
    (store / "it-237").mkdir()
    (store / "it-237" / "weights.json").write_bytes(d1)
    std1 = ws.standard_weights_copy(store / "it-237" / "weights.json", store, course="c", log=_quiet)
    assert json.loads(std1.read_bytes())["format"] == "nn-weights-json"
    # 云机第二轮：基准 = 本轮实际装载的那份整份（旁路缓存）
    d2 = _pack(std1.read_bytes(), _weights_json(_step(_params(11), seed=13)))
    assert wd.base_sha_of(d2) == _sha(std1.read_bytes())

    local = tmp_path / "local"
    local.mkdir()
    (local / "anchor.json").write_bytes(base_raw)  # 本机链的根（归档/锚点）
    (local / "it-237.json").write_bytes(d1)
    (local / "it-238.json").write_bytes(d2)
    ws.ensure_standard_weights(local / "it-238.json", course="c", log=_quiet)
    assert (local / "it-238.json").read_bytes() == wd.apply_weights_delta(
        std1.read_bytes(), d2, expect_base_sha=_sha(std1.read_bytes())
    )
    # 产物本体（store）保持 delta —— deliver 腿要发的字节、hub 侧判据全都不变。
    assert (store / "it-237" / "weights.json").read_bytes() == d1


def test_store_side_cache_is_a_candidate_root(tmp_path: Path) -> None:
    """云机侧旁路缓存（`<store>/.weights-standard/<sha16>.json`）也必须是基准候选根。

    为什么需要（不是"对称好看"）：**链根会被清理策略删掉**（`biz/course_archive` 对 offline 轮的
    `weights.json` 判「转存后删」；换机只带缓存、段起点被 prune 同理）⇒ 链根不在本机时，那条链
    就只能一路递归到断头。而云机自己那份落地件一直躺在旁路缓存里（`standard_weights_copy` 写的），
    它**逐字节就是下一轮的 base** —— 扫不到它，每轮都要把整条链重算一遍（或整条解不出来）。
    """
    store = tmp_path / "store"
    (store / "it-236").mkdir(parents=True)
    base_raw = _weights_json(_params(21))
    (store / "it-236" / "weights.json").write_bytes(base_raw)
    d1 = _pack(base_raw, _weights_json(_step(_params(21), seed=22)))
    (store / "it-237").mkdir()
    (store / "it-237" / "weights.json").write_bytes(d1)
    std1 = ws.standard_weights_copy(store / "it-237" / "weights.json", store, course="c", log=_quiet)
    assert std1 == store / ws.STANDARD_CACHE_DIR / f"{_sha(d1)[:16]}.json"
    (store / "it-236" / "weights.json").unlink()  # 链根被清掉：只剩旁路缓存里那份落地件

    d2 = _pack(std1.read_bytes(), _weights_json(_step(_params(21), seed=23)))
    (store / "it-238").mkdir()
    p2 = store / "it-238" / "weights.json"
    p2.write_bytes(d2)

    assert ws.ensure_standard_weights(p2, course="c", log=_quiet) == p2
    assert p2.read_bytes() == wd.apply_weights_delta(
        std1.read_bytes(), d2, expect_base_sha=_sha(std1.read_bytes())
    )


def test_cloud_copy_keeps_store_delta_and_reuses_cache(tmp_path: Path) -> None:
    """云机口味：store 本体保持 delta（deliver 腿要发的字节），整份进旁路缓存且只转一次。"""
    store = tmp_path / "store"
    (store / "it-236").mkdir(parents=True)
    base = _params(9)
    anchor = store / "it-236" / "weights.json"
    anchor.write_bytes(_weights_json(base))
    (store / "it-237").mkdir(parents=True)
    blob = _pack(anchor.read_bytes(), _weights_json(_step(base, seed=10)))
    (store / "it-237" / "weights.json").write_bytes(blob)

    got = ws.standard_weights_copy(store / "it-237" / "weights.json", store, course="c", log=_quiet)
    assert (store / "it-237" / "weights.json").read_bytes() == blob  # store 原封
    assert json.loads(got.read_bytes())["format"] == "nn-weights-json"
    # 转一次一直复用：把基准删掉，第二次仍能拿到缓存（证明没再解一遍）。
    anchor.unlink()
    assert ws.standard_weights_copy(store / "it-237" / "weights.json", store, course="c", log=_quiet) == got


# --------------------------------------------------------------- v2：预算 / 负缓存 / 不吞预算


def _missing_pack(seed: int) -> bytes:
    """一份**基准不在盘上**的 delta 容器（base_sha 是假的，逐字节搜索必然 miss）。"""
    return wd.pack_weights_delta(
        _weights_json(_params(seed)),
        _weights_json(_step(_params(seed), seed=seed + 1)),
        base_sha="f" * 64,
    )


def test_mutually_referencing_deltas_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7 个互为候选 + 基准全缺：v1 是 7!（现场 232! 的缩影），v2 每个路径只读一次。

    用读计数钩子（不用计时）：未改动代码上读次数远超 3N，v2 上 ≤ 3N（根 2 次 + 6 个候选）。
    N 取 7 而不是 50——50! 只会把测试挂死，红不了也绿不了。
    """
    n = 7
    paths = [tmp_path / f"d{i}.json" for i in range(n)]
    for i, p in enumerate(paths):
        p.write_bytes(_missing_pack(300 + i))

    reads = {"n": 0}
    real = Path.read_bytes

    def counted(self: Path) -> bytes:
        reads["n"] += 1
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    with pytest.raises(ws.BaseNotFoundError):
        ws.ensure_standard_weights(paths[0], course="c", log=_quiet)
    assert reads["n"] >= n, f"读计数钩子没生效？只读到 {reads['n']} 次（n={n}）"
    assert reads["n"] <= n + 3, f"候选被重复读：{reads['n']} 次（n={n}，根 2 次 + 候选各 1 次）"


def test_same_path_is_read_once_and_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一路径在两条分支下被访问 ⇒ 磁盘**只读一次**，预算只计一次（DoD §2.4）。"""
    p = tmp_path / "w.json"
    p.write_bytes(b"X" * 10)
    reads = {"n": 0}
    real = Path.read_bytes

    def counted(self: Path) -> bytes:
        reads["n"] += 1
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    ctx = ws._SearchContext()
    assert ctx.read(p) == ctx.read(p) == b"X" * 10
    assert reads["n"] == 1
    assert (ctx.bytes_read, ctx.candidates_read) == (10, 1)


def test_budget_exceeded_is_not_swallowed_by_the_parent(tmp_path: Path) -> None:
    """预算在**子层**撞顶 ⇒ 最终异常必须是 SearchBudgetExceededError，而不是「基准不在本机」。

    v1 的父层 `except ProtocolError: continue` 会把预算当一次候选 miss 吞掉；v2 单独重抛。
    """
    a, b, c = (tmp_path / f"{x}.json" for x in "abc")
    for seed, p in ((400, a), (401, b), (402, c)):
        p.write_bytes(_missing_pack(seed))

    ctx = ws._SearchContext()
    # 根 a + 候选 b 还能读；b 再递归读 c 时撞顶（差 1 字节）。
    limit = len(a.read_bytes()) + len(b.read_bytes()) + len(c.read_bytes()) - 1
    ctx.max_bytes = limit
    with pytest.raises(ws.SearchBudgetExceededError) as ei:
        ws._standard_bytes(a, course="c", traj_root=None, extra_roots=(), ctx=ctx)
    # 结构化读面：撞的是字节预算、限额是多少、当时累计读了多少（不挂中文文案）。
    assert (ei.value.kind, ei.value.limit) == ("bytes", limit)
    assert 0 < ei.value.used <= limit
    # 若父层把它当「这个候选不行」吞掉，这里会是 BaseNotFoundError（v1 的 bug）。
    assert not isinstance(ei.value, ws.BaseNotFoundError)


def test_deterministic_failures_are_marked_non_retryable() -> None:
    """`round_failure` 的判据是异常属性，不是中文文案。"""
    assert ws.BaseNotFoundError("x").retryable is False
    assert ws.ChainTooLongError("x").retryable is False
    assert ws.CycleDetectedError("x").retryable is False
    assert ws.SearchBudgetExceededError("x").retryable is False
