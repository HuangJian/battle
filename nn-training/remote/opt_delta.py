"""remote/opt_delta.py —— opt 回传的 **delta 容器**（R3 / M4，`plan/wire-diet.plan.md` §3 R3 / §12）。

## 布局

```
MAGIC(4) | uint32 BE header_len | header_json(utf-8) | 各项原始字节…
header = {"ofmt": OPT_DELTA_FMT, "base_sha": <64hex 或 "">, "items": [item, …]}
  item = {"path": "state.0.exp_avg", "kind": "delta-f16" | "exact" | "json",
          "dtype": "float16" / …（json 项无）, "shape": […], "nbytes": N, "value": <json 项才有>}
```

语义（两侧同一份契约）：

* `delta-f16`：逐元素 `(new − base)` 存成 f16 —— 量化施加在**增量**上（|Δ| 小），实测绝对
  max 5.43e-5 / 相对 |opt| 4.7e-5（plan §3 R3）；「base 恒为**本轮载入的那一份**」是残差不
  逐轮累积的前提（两档语义与其偏差口径见 plan §12）；
* `exact`：原样搬字节（Adam `step` 等**非连续量**：量化会改语义）。**0-dim 标量一律走这一档**
  —— 真 Adam 的 `step` 是 `torch.tensor(0.0)`（**float32 + 0-dim**），只按 dtype 分叉会漏掉它
  （2026-10-14 云机事故：`self.dim() cannot be 0 to view Half as Byte`，`_raw_bytes` 见下）；
* `json`：标量/列表（`param_groups` 的 lr/betas…）内联在头里，逐值精确。

★ 形态**自述**在头里（`ofmt`），且 `apply_opt_delta` 只认 `delta-f16`：旧端（只认整份 tar）拿到容器
会先撞魔数/形态位，**不会**静默把 delta 当权重落盘。形态位在 manifest（`opt_fmt`）里由发布端写下
（兼容矩阵 §4：发布端 ≡ 验收端；缺键 = 旧形态）。

## 为什么住 `remote/` 而不是 `common/wire_codec.py`

两条既有的层守卫把它拦在 `common/` 之外（实测：`tests/common/test_protocol_split.py` 只看
**全量** import 节点 ⇒ 连函数内 `import torch` 也算「碰了上层」，`tests/common/test_common_layer.py`
的同条只扫顶层）：`common/` 要随 `code.zip` 解到**没有 torch** 的云机上 ⇒ 它连惰性 torch 都不许有。
两侧的消费者（worker 的产物段 / hub 的 `verify_and_land`）都在 `remote/` ⇒ 这就是它的家。
顶层只 import stdlib + `common.errors`（叶子）⇒ 拓扑秩 **L0**。

torch **惰性**且缺了**响亮拒收**（M4 硬约束②）：拿不到 torch 就没法重建 ⇒ 拒收该形态并回退整份
blob，绝不静默降级。

## hub 侧的**落地**（`opt_tar_bytes` / `pack_tar_from_dir` / `land_opt_delta`，2026-10-14）

它们本来住 `remote/hub_client.py`，撞了 1000 行 LOC 预算后按「独立所有者（wire-diet）+ 独立触发
条件（形态位命中）」整块下沉。**住这里而不是新开一个模块**：新模块只要 import 本模块（或
`weights_delta`）就会变成 L1，而 `hub_client` 已经站在 L1 —— `hub_client → 新模块(L1)` 是同层边
（DAG 要求**严格**向下）⇒ 只能把 `hub_client` 抬到 L2，连带 9 个上位模块（`code_snapshot` 与整个
`hub.*` 链一路 +1）。而本模块是 **L0 叶子**（只依赖 stdlib + `common.errors`），所以落地函数住这里
最省事：**格式的所有者同时也管自己那份文件的落盘**。

⇒ 由此得到一条纪律：**本模块的拒收一律 `ProtocolError`**（包括「base 不在」这一档）—— 本模块
不得 import `remote.hub_http`（那会把秩抬到 L1）。两个异常在重试分类上等价
（`trainer/loop_transport.remote_retryable_exceptions()` 同时收两者），而「拿不到 base」与
「容器坏」本来就是同一族：都是「这份 result 解读不了」。
"""

from __future__ import annotations

import io
import json
import struct
import tarfile
from functools import partial
from pathlib import Path
from typing import Any

from common.errors import ProtocolError
from common.hashing import sha256_bytes
from common.wire_codec import container_base_sha, fmt_bit

__all__ = [
    "OPT_DELTA_FMT",
    "OPT_DELTA_MAGIC",
    "apply_opt_delta",
    "base_sha_of",
    "is_container",
    "land_opt_delta",
    "opt_fmt_of",
    "opt_tar_bytes",
    "pack_opt_delta",
    "pack_tar_from_dir",
]

#: 容器魔数（旧端拿到它先撞这个，不会把 delta 当权重落盘）。
OPT_DELTA_MAGIC = b"BOD1"
#: 形态自述值（manifest 的 `opt_fmt` 用同一个字面量）。
OPT_DELTA_FMT = "delta-f16"
#: 参与 delta 的 dtype（其余一律 `exact`）——刻意只认浮点：整数张量做差再量化会改语义。
#: ⚠ 光看 dtype 不够（2026-10-14）：**0-dim 的浮点标量也必须 `exact`** —— Adam 的 `step`
#: 是 float32，量化它是改语义。dtype 门 + `dim() > 0` 两条合起来才是完整判据（在 `pack_opt_delta`）。
_DELTA_DTYPES = frozenset({"float16", "float32", "float64", "bfloat16"})


#: manifest 的**形态位读法**（发布端写、worker 与 hub 都照它分叉的唯一口径）：命中（= `OPT_DELTA_FMT`）
#: ⇒ 回传/落地的都是 delta 容器；否则（含缺键、旧值、非映射）= 旧形态（整份 tar，逐字节旧行为）。
#: 读法本体在 `common.wire_codec.fmt_bit`（**与 R1 的权重 delta 共用一处**：两族容器只差一个值，
#: 各写一份 def 就是同一份判据两处实现 ⇒ 改一处漏一处即静默分叉）；这里只钉「本族用哪个键、哪个值」。
opt_fmt_of = partial(fmt_bit, key="opt_fmt", want=OPT_DELTA_FMT)
#: 容器头里的 `base_sha`（**只读头**，不解 delta 体；读不动 ⇒ 空串）：hub 侧的**档位读数**
#: （plan §12）—— 与 manifest 的 `opt_sha` 相等 = exact 档；不等 = identity 档（worker 用自己那份
#: f32 副本当基准，hub 无权对账，只能记一行判别）。读法本体同样在 `common.wire_codec`。
base_sha_of = partial(container_base_sha, magic=OPT_DELTA_MAGIC)


def _torch():
    """惰性取 torch（顶层零 torch 是本仓硬规）。缺 ⇒ 响亮（M4 硬约束②：不得静默降级）。"""
    try:
        import torch
    except ImportError as e:  # pragma: no cover - 只有环境真缺 torch 才走到
        raise ProtocolError(
            f"opt delta 需要 torch（{e}）——拒收该形态并回退整份 blob，不得静默当权重落盘"
        ) from None
    return torch


def _opt_items(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """（可嵌套的）state_dict 摊平成 `(path, leaf)`；顺序 = 遍历顺序（**两侧同一份规则**）。"""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _opt_items(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += _opt_items(v, f"{prefix}.{i}")
    else:
        out.append((prefix, obj))
    return out


def _raw_bytes(t, tensor: Any) -> bytes:
    """张量 → 连续原始字节（`view(uint8)` 对全部 dtype 都成立，含 bfloat16）。

    ★ `reshape(-1)` **不可省**（2026-10-14 云机事故，`tests/remote/test_opt_delta.py` ⑦ 族）：
    `Tensor.view(dtype)` 对 **0-dim** 张量直接抛 `self.dim() cannot be 0 to view Half as Byte`
    —— 而真 Adam 的 `step` 就是 `torch.tensor(0.0)`（**0-dim**，`torch/optim/adam.py` 的
    `state["step"]`）。两个分支（`delta-f16` / `exact`）都调本函数 ⇒ 只改分支判据救不了。
    先摊平成 1-dim 再 `view`：dtype 重解释的语义逐字不变（**形状由容器头里的 `shape` 负责**，
    读端 `reshape(shape)` 原样还原成 0-dim）。形状不丢，读数不变。
    """
    return bytes(tensor.detach().contiguous().reshape(-1).view(t.uint8).numpy().tobytes())


def pack_opt_delta(base: dict, new: dict, *, base_sha: str) -> bytes:
    """worker 侧：`new` 相对 `base` 的 delta 容器（两者都是 `opt.state_dict()` 那一族形状）。

    `base_sha` = **本机实际载入的那份 base opt tar 的 sha256**（不是「hub 那份」的抽象说法）：
    exact 档下它会与 manifest 的 `opt_sha` 相等，hub 侧即可逐字节对账；identity 档下它只是
    「我拿哪份当基准」的记录（hub 无法核对，靠 `manifest.opt_from` 的身份闸，见 plan §12）。

    路径缺口一律 `ProtocolError`：结构性不一致时**拒绝**，而不是猜一个基准。
    """
    t = _torch()
    known: dict[str, Any] = dict(_opt_items(base))
    items: list[dict] = []
    blobs: list[bytes] = []
    for path, leaf in _opt_items(new):
        if path not in known:
            raise ProtocolError(f"delta：基准里没有 {path}（结构不一致——拒发而不是猜）")
        b = known[path]
        leaf_is_t = t.is_tensor(leaf)
        if leaf_is_t != t.is_tensor(b):
            raise ProtocolError(f"delta：{path} 两侧类型不一致（张量 vs 非张量）")
        if not leaf_is_t:
            try:
                json.dumps(leaf)
            except (TypeError, ValueError) as e:
                raise ProtocolError(f"delta：{path} 既不是张量也不是 JSON 值（{e}）") from None
            items.append({"path": path, "kind": "json", "value": leaf})
            continue
        dtype = str(leaf.dtype).replace("torch.", "")
        shape = [int(x) for x in leaf.shape]
        # `leaf.dim() > 0` 是同一条合约的第二半（2026-10-14）：`_DELTA_DTYPES` 只排**整数**
        # 张量，而 Adam 的 `step` 是 **float32 且 0-dim** ⇒ 光按 dtype 分叉会把它送进量化分支
        # （本模块头部第 17 行与 `tests/remote/test_opt_delta.py` ⑦-b 都要求它走 `exact`：
        # 量化 step 是**改语义**，不是量化误差）。0-dim ⇒ 一律原样搬字节。
        if dtype in _DELTA_DTYPES and leaf.dim() > 0 and tuple(b.shape) == tuple(leaf.shape):
            raw = _raw_bytes(t, (leaf.float() - b.float()).to(t.float16))
            items.append(
                {"path": path, "kind": "delta-f16", "dtype": "float16", "shape": shape,
                 "nbytes": len(raw)}
            )
        else:
            # `step`（0-dim 标量 / 非同形 / 非浮点张量）：原样搬字节 —— 非连续量量化会改语义。
            # 真 Adam 的 `step` 落在**这一支**（float32 但 0-dim，见上面的判据），
            # `_raw_bytes` 的 `reshape(-1)` 也是为它准备的。
            raw = _raw_bytes(t, leaf)
            items.append(
                {"path": path, "kind": "exact", "dtype": dtype, "shape": shape,
                 "nbytes": len(raw)}
            )
        blobs.append(raw)
    hdr = json.dumps(
        {"ofmt": OPT_DELTA_FMT, "base_sha": str(base_sha or ""), "items": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return OPT_DELTA_MAGIC + struct.pack(">I", len(hdr)) + hdr + b"".join(blobs)


def is_container(raw: bytes) -> bool:
    """这份回传字节**是不是** delta 容器（只看魔数）——验收端的分派判据。

    是容器 ⇒ 重建；不是 ⇒ 按旧形态（整份 tar）落盘，响亮记一行。理由与 `weights_delta`
    同条：发布端与节点代可能错位，旧代节点的 job 不该全废，而「是不是容器」逐字节可判
    ——真正的容器坏掉（截断/头坏/结构不符）仍然**拒收**。
    """
    return raw.startswith(OPT_DELTA_MAGIC)


def apply_opt_delta(base: dict, raw: bytes, *, expect_base_sha: str = "") -> dict:
    """hub 侧：delta 容器加回 `base`（本机那份 base opt 的 state_dict）⇒ **新的** state_dict。

    调用方拿到后 `opt.load_state_dict(...)`（形状/键与 `base` 逐字相同）。`expect_base_sha` 非空时
    逐字节对账基准（exact 档用；identity 档留空，理由见 plan §12）。

    响亮拒收（**不**静默降级）：缺 torch · 魔数/头/长度不符 · `ofmt` 不认 · `base_sha` 与期望不符 ·
    项结构（path/shape）与 `base` 对不上 · 基准不是映射 · 尾部多余字节。
    """
    t = _torch()
    if not raw.startswith(OPT_DELTA_MAGIC):
        raise ProtocolError("delta 容器缺 BOD1 魔数")
    off = len(OPT_DELTA_MAGIC)
    if len(raw) < off + 4:
        raise ProtocolError("delta 容器截断：读不到头长度")
    (hdr_len,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    try:
        hdr = json.loads(raw[off : off + hdr_len].decode("utf-8"))
        items = hdr["items"]
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"delta 头解析失败: {e}") from None
    off += hdr_len
    ofmt = str(hdr.get("ofmt") or "")
    if ofmt != OPT_DELTA_FMT:
        raise ProtocolError(f"delta ofmt={ofmt!r} 不认（只认 {OPT_DELTA_FMT!r}）——拒收")
    got_sha = str(hdr.get("base_sha") or "")
    if expect_base_sha and got_sha != str(expect_base_sha):
        raise ProtocolError(
            f"delta 基准不符：容器 {got_sha[:12]}… != 本机 {str(expect_base_sha)[:12]}… —— 拒收"
        )
    declared: dict[str, tuple[str, Any, str, tuple[int, ...]]] = {}
    for it in items:
        path = str(it.get("path") or "")
        kind = str(it.get("kind") or "")
        if not path or path in declared:
            raise ProtocolError(f"delta item 路径缺失或重复：{path!r}")
        if kind == "json":
            declared[path] = (kind, it.get("value"), "", ())
            continue
        if kind not in ("delta-f16", "exact"):
            raise ProtocolError(f"delta item {path} 的 kind={kind!r} 不认")
        n = int(it.get("nbytes") or 0)
        blob = raw[off : off + n]
        if len(blob) != n:
            raise ProtocolError(f"delta 体截断：{path} 期望 {n} 字节，实得 {len(blob)}")
        off += n
        declared[path] = (
            kind,
            blob,
            str(it.get("dtype") or ""),
            tuple(int(x) for x in it.get("shape") or ()),
        )
    if off != len(raw):
        raise ProtocolError(f"delta 尾部有 {len(raw) - off} 字节多余数据")

    def rebuild(node: Any, prefix: str = "") -> Any:
        if isinstance(node, dict):
            return node.__class__(
                (k, rebuild(v, f"{prefix}.{k}" if prefix else str(k))) for k, v in node.items()
            )
        if isinstance(node, (list, tuple)):
            return node.__class__(rebuild(v, f"{prefix}.{i}") for i, v in enumerate(node))
        got = declared.pop(prefix, None)
        if got is None:
            raise ProtocolError(f"delta 缺 {prefix}（结构不一致——拒收而不是猜）")
        kind, payload, dtype, shape = got
        if kind == "json":
            return payload
        try:
            dt = getattr(t, dtype)
        except AttributeError:
            raise ProtocolError(f"delta {prefix} 的 dtype={dtype!r} 不认") from None
        tensor = t.frombuffer(bytearray(payload), dtype=dt).reshape(shape).clone()
        if kind == "exact":
            return tensor
        if tuple(node.shape) != shape:
            raise ProtocolError(f"delta {prefix} 形状不符：容器 {shape} != 本机 {tuple(node.shape)}")
        return node.float() + tensor.float()

    out = rebuild(base)
    if declared:
        raise ProtocolError(f"delta 多出 base 里没有的项：{sorted(declared)[:3]}… —— 拒收")
    if not isinstance(out, dict):
        raise ProtocolError("delta 基准不是映射（state_dict 应为 dict）——拒收")
    return out


def pack_tar_from_dir(d: Path, names: list[str]) -> bytes:
    """目录 → tar bytes（`names` 顺序即成员顺序）；空列表 = `b""`。

    两个调用点：① `opt_tar_bytes` 的**重打**退路（历史 run / 冷启动）；② `land_opt_delta`
    ——重建成 `opt.pt` 后由它押出**权威字节**（下一轮 publish 的 `opt_sha` 就是这几字节）。
    实现只此一处，免得两条路各自演化出不同的成员顺序。
    """
    if not names:
        return b""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tfb:
        for n in names:
            tfb.add(d / n, arcname=n)
    return buf.getvalue()


def opt_tar_bytes(ckpt_remote_dir: str | Path | None) -> bytes:
    """上轮 ppo_ckpt_remote → raw tar bytes（D5）；空 = b""。

    H5（review-hy）：不打 state.json（numpy RNG 无人读，worker 按 per-job 种子重播）。

    M2：**优先读 `<ckpt>/../ppo_ckpt_remote.tar`**（`verify_and_land` 落盘的**原始回传字节**）。
    只有原样字节的 sha256 才与云 worker 本地 blob_cache 的键一致 —— 重打 tar 会因 uid/gid/mtime
    规范化差异导致 sha 漂移、每轮都缓存未命中（1.19MB 白传回来）。历史 run / 冷启动无该文件 →
    从目录重打（可能未命中，走 blob 传一次）。

    ★ 2026-09-24（opt-blob-diet，§4）：新形状的 tar **只装 `opt.pt`**（`worker.pack_opt_tar`）
    且**优先路径**读的就是那个原样字节，所以稳态下这里恒为 opt-only。而**下面的回退分支刻意保留
    `("model.pt", "opt.pt")` 旧形状**——它只在历史 run / 冷启动（盘上没有 `.tar`）时走到，那时目录
    里通常有改造前写下的 `model.pt`：让 worker 走 W4 的 legacy 路径最安全。代价只是这个重打 tar 的
    sha 与 worker 的 blob_cache 键不同 ⇒ 多付一次下载（罕见事件）。**这是有意的兼容退路，不是漏改**
    （评审 F6）。
    """
    if not ckpt_remote_dir:
        return b""
    d = Path(ckpt_remote_dir)
    tar_path = d.parent / (d.name + ".tar")
    if tar_path.exists():
        return tar_path.read_bytes()
    names = [n for n in ("model.pt", "opt.pt") if (d / n).exists()]
    return pack_tar_from_dir(d, names)


def land_opt_delta(
    opt_blob: bytes, *, traj_dir: str | Path, it: int, ckpt_dir: Path, extract, log
) -> bytes:
    """hub 侧的 opt 落地：delta 容器 → **重建** → 写 `ckpt_dir/opt.pt` → 押回权威 tar 字节。

    基准 = 上一轮 landed 的那份（`it{it-1}/ppo_ckpt_remote/opt.pt` —— 就是本轮 `opt_sha` 打的 tar）。
    `base_sha` 与**本机发出去的那份**相等 = **exact** 档（worker 下的是我们的 blob）；不等 =
    **identity** 档（worker 用自己那份 f32 副本当基准 ⇒ hub 无权对账，只记一行判别；plan §12）。

    分派：不是容器（旧 worker / 冒烟回显的空 tar）⇒ 按**旧形态**解包到 `ckpt_dir`。注入 tar 工具
    而不是让本模块 import `remote.job_fs`（那会把秩抬到 L1）：调用方传 `extract` 进来。

    响亮拒收（**绝不**把 delta 当权重落盘）：缺 base / 缺 torch / 容器坏 / 结构不符 —— 全抛。
    返回的字节就是**权威字节**：调用方把它写成 sibling `.tar`，下一轮 publish 的 `opt_sha` 由它算。
    """
    if not is_container(opt_blob):
        log(
            "WARN: 形态位是 opt delta 但回传的不是 BOD1 容器（旧 worker / 冒烟回显？）——按旧形态解包"
        )
        try:
            extract(opt_blob, ckpt_dir)
        except (tarfile.TarError, OSError, EOFError, ValueError) as e:
            # 名实不符且解包也不行 = 确定性坏件。**必须**转成 `ProtocolError`：tarfile 的
            # 原生异常不在 `remote_retryable_exceptions()` 里，泄出去会落到 loop_core 的
            # 通用兜底（连败即杀进程）——那是 2026-09-16 事故的形状。
            raise ProtocolError(
                f"形态位说 opt delta、回传的既不是 BOD1 容器也不是合法 tar（{e}）——拒收"
            ) from None
        return opt_blob
    import torch

    base_dir = Path(traj_dir) / f"it{it - 1}" / "ppo_ckpt_remote"
    base_p = base_dir / "opt.pt"
    if not base_p.exists():
        raise ProtocolError(
            f"opt 形态位={OPT_DELTA_FMT!r} 但 base 不在（{base_p}：上一轮 landed ckpt 被 prune / "
            "换了 traj 目录）——拒收；重发旧形态或关掉 --wire-diet"
        )
    reported = base_sha_of(opt_blob)
    published = sha256_bytes(opt_tar_bytes(base_dir))
    log(
        f"opt delta 落地：base_sha={'exact' if reported == published else 'identity'} 档"
        f"（容器 {reported[:12]}… / 本机 {published[:12]}…）"
    )
    rebuilt = apply_opt_delta(torch.load(base_p, map_location="cpu"), opt_blob)
    torch.save(rebuilt, ckpt_dir / "opt.pt")
    return pack_tar_from_dir(ckpt_dir, ["opt.pt"])
