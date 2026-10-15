"""tests/remote/test_opt_standard.py —— opt 的**读侧落地**（2026-10-15 事故的 opt 那一半）。

现场（`tmp/y5-term/remote-jobs/offline/c2ad414cd2ced1dd/it-237/opt.tar`，300 945 B）：头 4 字节
= `BOD1`、`base_sha=15bea9c1…`；把这份字节喂给消费者（`common.fs.extract_tar_bytes`）当场
`ReadError: invalid header`，而它的基准就在本机（`tmp/y5-term/it236/ppo_ckpt_remote.tar` 与两个
job 目录的 `blob.opt` 逐字节命中）。hub 侧落地（`land_opt_delta`）只管 published 腿那一半，
离线段那台机器**自己下一轮的 init** 就是这份容器 ⇒ 整轮死在 restore。

本例钉住修复口径（与 weights 同一条）：
  ① 整份原样返回（热路径零成本、一个字节不动）；
  ② 产物里的容器 ⇒ 整份落旁路缓存，**产物本体保持容器**（deliver 腿要发的就是它）；
  ③ 两种命名成对：整份 = `.tar`、容器 = `.delta`（输入是 `.delta` 时不改它；名字骗人时以内容为准）；
  ④ 声明必须可兑现：`landed_opt_sha` 把整份种子进内容寻址缓存（离线段没有下载通道）；
  ⑤ 第二轮的基准 = 第一轮的**落地件**（链上一步；不用走全链）；
  ⑥ 缺基准 ⇒ 响亮拒收且文件一字不动（拿错动量 = 形状对、数值错的静默降质，比拒收危险）；
  ⑦ 消费界面的红线（修复前的复现）：容器解不开、落地件解得出。

tar 的打包/解包在用例里**独立重实现**（`_tar_bytes` / `_leaf_map`），不复用被测模块的
`pack_tar_from_dir`：判据要能被另一份实现复现（AGENTS §8）。
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from common.fs import extract_tar_bytes
from remote import opt_delta as od
from remote import opt_standard as os_std


def _state(seed: int, *, n: int = 2, elems: int = 64) -> dict:
    """一份确定性的「`opt.state_dict()` 那一族」状态（与 `test_opt_delta._state` 同一族）。"""
    g = torch.Generator().manual_seed(seed)
    st = {}
    for pid in range(n):
        st[str(pid)] = {
            "step": torch.tensor([7.0 + pid]),
            "exp_avg": torch.randn(elems, generator=g) * 3e-2,
            "exp_avg_sq": torch.rand(elems, generator=g) * 1e-4,
        }
    return {
        "state": st,
        "param_groups": [{"lr": 3e-4, "betas": [0.9, 0.95], "eps": 1e-8, "params": list(range(n))}],
    }


def _tar_bytes(state: dict, tmp: Path, name: str = "opt") -> bytes:
    """state → 单件 tar 字节（独立重实现：torch.save + tarfile 手打）。

    成员名恒为 `opt.pt`（真形状：`worker.pack_opt_tar` 打的就只有它）；`name` 只用来区分
    临时目录，免得同 seed 的两次调用互相覆盖。
    """
    d = tmp / f"_tar-{name}"
    d.mkdir(parents=True, exist_ok=True)
    torch.save(state, d / "opt.pt")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tfb:
        tfb.add(d / "opt.pt", arcname="opt.pt")
    return buf.getvalue()


def _load_tar(tar_bytes: bytes, tmp: Path, tag: str) -> dict:
    """tar 字节 → state（独立重实现：解包后直接 torch.load，不碰被测模块）。"""
    d = tmp / f"_un-{tag}"
    extract_tar_bytes(tar_bytes, d)
    state = torch.load(d / "opt.pt", map_location="cpu")
    assert isinstance(state, dict), f"opt.pt 应装一份 state_dict，实得 {type(state).__name__}"
    return state


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _quiet(_msg: str = "") -> None:
    return None


def _assert_close(got: dict, want: dict, *, tol: float = 1e-3) -> None:
    """落地件与「本轮产出的那份」在 f16 量化界内一致；`step` / `param_groups` 逐值精确。"""
    for pid, leaf in want["state"].items():
        for k, v in leaf.items():
            g = got["state"][pid][k]
            if k == "step":
                assert torch.equal(g, v), f"step 必须逐值精确（{pid}）"
            else:
                assert float((g - v).abs().max()) <= tol, f"{pid}.{k} 超出量化界"
    assert got["param_groups"] == want["param_groups"]


def test_standard_tar_is_untouched(tmp_path: Path) -> None:
    """① 整份文件：原样返回、一个字节不动、不建旁存（热路径上等于零成本）。"""
    tar = _tar_bytes(_state(1), tmp_path)
    p = tmp_path / "opt.tar"
    p.write_bytes(tar)
    assert os_std.ensure_standard_opt(p, log=_quiet) == p
    assert p.read_bytes() == tar
    assert not (tmp_path / "opt.delta").exists()
    assert os_std.is_delta_opt_file(p) is False


def test_product_copy_keeps_the_container_and_lands_beside_it(tmp_path: Path) -> None:
    """② 产物口径：`<store>/it-N/opt.tar` 保持容器（deliver 腿要发的字节），整份进旁路缓存。"""
    base = _state(1)
    new = _state(2)
    base_tar = _tar_bytes(base, tmp_path, name="base.pt")
    container = od.pack_opt_delta(base, new, base_sha=_sha(base_tar))
    store = tmp_path / "store"
    (store / "it-237").mkdir(parents=True)
    (store / "it-236").mkdir()
    anchor = store / "it-236" / "ppo_ckpt_remote.tar"
    anchor.write_bytes(base_tar)
    p = store / "it-237" / "opt.tar"
    p.write_bytes(container)

    got = os_std.standard_opt_copy(p, store, log=_quiet)
    assert got == store / os_std.OPT_STANDARD_CACHE_DIR / f"{_sha(container)[:16]}.tar"
    assert p.read_bytes() == container, "产物本体必须保持容器（deliver 腿发的就是它）"
    _assert_close(_load_tar(got.read_bytes(), tmp_path, "copy"), new)

    # 转一次一直复用：把基准删掉，第二次仍拿缓存（证明没再解一遍）。
    anchor.unlink()
    assert os_std.standard_opt_copy(p, store, log=_quiet) == got


def test_pair_naming_follows_the_content(tmp_path: Path) -> None:
    """③ 两版成对：`.tar` 里装容器 ⇒ 容器旁存 `.delta`、`.tar` 就地变整份；`.delta` 不动。"""
    base_tar = _tar_bytes(_state(3), tmp_path, name="base.pt")
    container = od.pack_opt_delta(_state(3), _state(4), base_sha=_sha(base_tar))
    d = tmp_path / "it-1"
    d.mkdir()
    (d / "ppo_ckpt_remote.tar").write_bytes(base_tar)  # 基准 = 上一轮那份
    p = d / "opt.tar"
    p.write_bytes(container)

    assert os_std.ensure_standard_opt(p, log=_quiet) == p
    assert (d / "opt.delta").read_bytes() == container, "容器要旁存一份（两版都留，名字自述）"
    _assert_close(_load_tar(p.read_bytes(), tmp_path, "inplace"), _state(4))
    stamped = p.read_bytes()
    assert os_std.ensure_standard_opt(p, log=_quiet) == p
    assert p.read_bytes() == stamped and (d / "opt.delta").read_bytes() == container

    # `.delta` 是 delta 版的正身：整份写到兄弟 `.tar`，它自己一个字节不动。
    d2 = tmp_path / "it-2"
    d2.mkdir()
    (d2 / "ppo_ckpt_remote.tar").write_bytes(base_tar)
    src = d2 / "opt.delta"
    src.write_bytes(container)
    got = os_std.ensure_standard_opt(src, log=_quiet)
    assert got == d2 / "opt.tar" and src.read_bytes() == container
    # 名字骗人没关系：内容才是权威（`.delta` 里装整份 ⇒ 不算容器）。
    liar = tmp_path / "liar.delta"
    liar.write_bytes(got.read_bytes())
    assert os_std.is_delta_opt_file(liar) is False


def test_landed_sha_is_seeded_into_the_blob_cache(tmp_path: Path) -> None:
    """④ 声明必须可兑现：整份种子进内容寻址缓存（键 = 它自己的 sha）⇒ `opt_sha` 能解析。"""
    base_tar = _tar_bytes(_state(5), tmp_path, name="base.pt")
    container = od.pack_opt_delta(_state(5), _state(6), base_sha=_sha(base_tar))
    store = tmp_path / "store"
    (store / "it-10").mkdir(parents=True)
    (store / "it-9").mkdir()
    (store / "it-9" / "ppo_ckpt_remote.tar").write_bytes(base_tar)
    p = store / "it-10" / "opt.tar"
    p.write_bytes(container)
    cache = tmp_path / "blob_cache"

    sha = os_std.landed_opt_sha(p, store, blob_roots=[cache], log=_quiet)
    assert len(sha) == 64 and (cache / sha).is_file()
    assert not od.is_container((cache / sha).read_bytes()), "种子进去的必须是整份 tar"
    assert p.read_bytes() == container
    assert os_std.landed_opt_sha(p, store, blob_roots=[cache], log=_quiet) == sha
    # 文件不在 ⇒ 空串（调用方按「本段没有动量」跑，绝不幽灵声明）。
    assert os_std.landed_opt_sha(store / "it-10" / "nothing.tar", store, log=_quiet) == ""


def test_second_round_lands_on_the_first_landing(tmp_path: Path) -> None:
    """⑤ 链上一步：第二轮的基准 = 第一轮的**落地件**（内容寻址缓存里 O(1) 命中）。"""
    start_tar = _tar_bytes(_state(7), tmp_path, name="base.pt")
    store = tmp_path / "store"
    (store / "it-20").mkdir(parents=True)
    (store / "it-20" / "ppo_ckpt_remote.tar").write_bytes(start_tar)  # 段起点（整份）
    cache = tmp_path / "blob_cache"

    c1 = od.pack_opt_delta(_state(7), _state(8), base_sha=_sha(start_tar))
    p1 = store / "it-21"
    p1.mkdir()
    (p1 / "opt.tar").write_bytes(c1)
    sha1 = os_std.landed_opt_sha(p1 / "opt.tar", store, blob_roots=[cache], log=_quiet)
    assert sha1 and (cache / sha1).is_file()

    # 第二轮：基准 = 第一轮实际装载的那份整份（= 落地件）⇒ 容器头写的就是它。
    c2 = od.pack_opt_delta(_state(8), _state(9), base_sha=sha1)
    p2 = store / "it-22"
    p2.mkdir()
    (p2 / "opt.tar").write_bytes(c2)
    sha2 = os_std.landed_opt_sha(p2 / "opt.tar", store, blob_roots=[cache], log=_quiet)
    assert sha2 and (cache / sha2).is_file()
    _assert_close(_load_tar((cache / sha2).read_bytes(), tmp_path, "r2"), _state(9))
    # 产物本体都不动（deliver 腿要发的字节 = 回传的那份容器）。
    assert (p1 / "opt.tar").read_bytes() == c1 and (p2 / "opt.tar").read_bytes() == c2


def test_missing_base_fails_loud_and_does_not_modify(tmp_path: Path) -> None:
    """⑥ 基准不在 ⇒ `ProtocolError`（消息带 base_sha 与两条修法），文件一字不动。"""
    container = od.pack_opt_delta(_state(11), _state(12), base_sha="b" * 64)
    p = tmp_path / "it-1"
    p.mkdir()
    src = p / "opt.tar"
    src.write_bytes(container)
    with pytest.raises(ProtocolError) as ei:
        os_std.standard_opt_copy(src, tmp_path / "store", blob_roots=[tmp_path / "cache"], log=_quiet)
    assert "b" * 16 in str(ei.value)
    assert src.read_bytes() == container
    assert not (tmp_path / "store" / os_std.OPT_STANDARD_CACHE_DIR).exists()


def test_consumer_boundary_is_the_reason(tmp_path: Path) -> None:
    """⑦ 修复前的复现（现场那份字节的形状）：容器解不开 / 落地件解得出。"""
    base_tar = _tar_bytes(_state(13), tmp_path, name="base.pt")
    container = od.pack_opt_delta(_state(13), _state(14), base_sha=_sha(base_tar))
    d = tmp_path / "it-1"
    d.mkdir()
    (d / "ppo_ckpt_remote.tar").write_bytes(base_tar)
    src = d / "opt.tar"
    src.write_bytes(container)

    with pytest.raises(Exception):  # noqa: B017 —— 复现的就是「解不开」（ReadError 一族）
        extract_tar_bytes(container, tmp_path / "_un-today")
    landed = os_std.ensure_standard_opt(src, log=_quiet)
    _assert_close(_load_tar(landed.read_bytes(), tmp_path, "fixed"), _state(14))


def test_store_side_cache_is_a_candidate_root(tmp_path: Path) -> None:
    """⑧ 云机侧旁路缓存（`<store>/.opt-standard/<sha16>.tar`）也是基准候选根。

    同 weights 那半的理由：链根会被清理策略删掉（`biz/course_archive` 对 offline 轮的 `opt.tar`
    只留关键轮），而**上一轮的落地件**一直躺在旁路缓存里、逐字节就是本轮的 base。
    """
    store = tmp_path / "store"
    (store / "it-20").mkdir(parents=True)
    start_tar = _tar_bytes(_state(41), tmp_path, name="base.pt")
    (store / "it-20" / "ppo_ckpt_remote.tar").write_bytes(start_tar)
    c1 = od.pack_opt_delta(_state(41), _state(42), base_sha=_sha(start_tar))
    (store / "it-21").mkdir()
    p1 = store / "it-21" / "opt.tar"
    p1.write_bytes(c1)
    std1 = os_std.standard_opt_copy(p1, store, log=_quiet)
    assert std1 == store / os_std.OPT_STANDARD_CACHE_DIR / f"{_sha(c1)[:16]}.tar"
    (store / "it-20" / "ppo_ckpt_remote.tar").unlink()  # 链根被清掉

    c2 = od.pack_opt_delta(_state(42), _state(43), base_sha=_sha(std1.read_bytes()))
    (store / "it-22").mkdir()
    p2 = store / "it-22" / "opt.tar"
    p2.write_bytes(c2)

    # 不给 blob_roots（hub 侧落地 / 清扫工具那种调用形态）：只能靠候选文件找到基准。
    got = os_std.ensure_standard_opt(p2, log=_quiet)
    _assert_close(_load_tar(got.read_bytes(), tmp_path, "cache-root"), _state(43))


def test_mutually_referencing_containers_fail_loud_instead_of_recursing(tmp_path: Path) -> None:
    """⑨ 防环真的管用：两份容器互为候选、基准都不在盘上 ⇒ **响亮报错**，不许递归到解释器上限。

    修复前：`_landed_bytes_for → _base_tar_bytes` 这条环**从不传 `depth`/`seen`**
    （`seen` 只出现在形参里）⇒ `_MAX_CHAIN` 永不触发，成环 = `RecursionError`
    （既不是 `ProtocolError`，调用方那些 `except ProtocolError` 也拦不住它）。
    """
    store = tmp_path / "store"
    (store / "it-1").mkdir(parents=True)
    (store / "it-2").mkdir()
    # 两份容器互相看得见（`it*/opt.tar` 在同一条候选面上），而各自声明的 base_sha 谁也不是。
    (store / "it-1" / "opt.tar").write_bytes(
        od.pack_opt_delta(_state(51), _state(52), base_sha="a" * 64)
    )
    (store / "it-2" / "opt.tar").write_bytes(
        od.pack_opt_delta(_state(53), _state(54), base_sha="b" * 64)
    )

    with pytest.raises(ProtocolError) as ei:
        os_std.standard_opt_copy(store / "it-1" / "opt.tar", tmp_path / "out", log=_quiet)
    assert "a" * 16 in str(ei.value)
