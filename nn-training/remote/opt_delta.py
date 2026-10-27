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

## 下溢消毒（2026-10-17，DECISIONS §2026-10-17-goalnn-opt-delta-f16-underflow）

`delta-f16` 会把 `exp_avg_sq` 的小增量（~1e-11）抹成 0，而 `exp_avg` 增量存活 ⇒ 权威 opt 出现
**v=0 且 m≠0**（真 Adam 不可能态）。该特征零梯度轮每步 `lr·m/ε`，96 步轮和 ~32.6
（`docs/nn/experiments.md` §115）。`sanitize_adam_sq` 在装入活优化器（`train_core`）与重建落地
（`apply_opt_delta`）两处把 `exp_avg_sq` 抬回 `(|m|/K)²`（K=10）的安全下限；传输格式不变。

★ **base 不消毒**：回传 delta 的 base 与 `land_opt_delta` 的 base 必须逐字保持原样 ——
两侧 base 相同是 delta 语义前提。消毒只发生在「装入活优化器」与「写出新权威」两处。

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
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from common.adam_sq import ADAM_SQ_FLOOR_K, sanitize_adam_sq_np
from common.errors import ProtocolError
from common.hashing import sha256_bytes
from common.wire_codec import container_base_sha, fmt_bit

__all__ = [
    "ADAM_SQ_FLOOR_K",
    "OPT_DELTA_FMT",
    "OPT_DELTA_MAGIC",
    "apply_opt_delta",
    "apply_opt_delta_np",
    "base_sha_of",
    "is_container",
    "land_opt_delta",
    "opt_fmt_of",
    "opt_tar_bytes",
    "pack_opt_delta",
    "pack_tar_from_dir",
    "rebuilt_opt_tar",
    "sanitize_adam_sq",
    "sanitize_adam_sq_np",
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


def _np():
    """惰性取 numpy（同 `_torch`：顶层零第三方依赖，hub 的启动闭包才拉得动本模块）。"""
    try:
        import numpy
    except ImportError as e:  # pragma: no cover - Paddle 机器必有 numpy
        raise ProtocolError(f"opt 状态转换需要 numpy（{e}）") from None
    return numpy


def _family(x: Any) -> str:
    """叶子的张量族：`"numpy"` / `"torch"` / `""`（非张量）。

    不用 `isinstance(x, numpy.ndarray)`：本模块顶层零第三方依赖（hub 的启动闭包要拉得动它），
    而 numpy 叶子只可能来自**已经 import 了 numpy** 的 Paddle worker ⇒ 按类型的模块名判定即可，
    不为一次判定把 numpy 拉进来。torch 张量的 `.detach`/`.dim` 是两族的分界（numpy 数组没有）。
    """
    if type(x).__module__.split(".", 1)[0] == "numpy" and hasattr(x, "tobytes"):
        return "numpy"
    if hasattr(x, "detach") and hasattr(x, "dtype") and hasattr(x, "dim"):
        return "torch"
    return ""


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


def sanitize_adam_sq(state: Any) -> int:
    """把 Adam 状态里被 f16 delta 下溢抹成 0 的 `exp_avg_sq` 抬回安全下限。

    规则：`exp_avg_sq <- max(exp_avg_sq, (|exp_avg| / K)^2)`，`K = ADAM_SQ_FLOOR_K = 10`。
    返回被抬高的**元素数**（不是参数组数）；幂等；不碰 `step` / `param_groups`。

    ## 为什么 K=10

    真 Adam 的 `|m|/sqrt(v)` 上界不是单步尖峰的 3.16，而是（β1=0.9, β2=0.999）
    `(1-β1)/sqrt(1-β2) / sqrt(1-β1^2/β2) = 7.27`（Cauchy-Schwarz；单步尖峰只是它的
    一个特例）。健康态因此满足 `v >= m^2/52.8`；下限取 `m^2/100` 还比最坏健康态低
    ~1.9 倍 ⇒ 对健康态按位 no-op，只有真下溢/真污染才会触发。触发后单步 <= K*lr；
    96 步（m/v 同步衰减）轮和 <= 90*lr ≈ 0.0135（lr=1.5e-4），对比未消毒的 v=0：
    `9*lr*m/eps = 32.6`（m=2.415e-4）。

    只抬 v、不动 m：m 是（f16 量化后的）真值，v 是被传输抹掉的那一半。
    amsgrad 的 denominator 用 `max_exp_avg_sq`；当前训练是 plain Adam，helper 显式跳过
    整个 amsgrad 节点（不 half-fix）；启用 amsgrad 时必须扩展本函数。
    """
    t = _torch()
    found: list[dict] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if "exp_avg" in node and "exp_avg_sq" in node:
                m = node["exp_avg"]
                v = node["exp_avg_sq"]
                if t.is_tensor(m) and t.is_tensor(v) and tuple(m.shape) == tuple(v.shape):
                    found.append(node)
                    return
            for child in node.values():
                collect(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                collect(child)

    collect(state)
    raised = 0
    for node in found:
        if node.get("max_exp_avg_sq") is not None:
            # amsgrad：denominator = max_exp_avg_sq；half-fix exp_avg_sq 会骗人。
            continue
        m = node["exp_avg"]
        v = node["exp_avg_sq"]
        if v.numel() == 0:
            continue
        floor = (m.detach().abs() / ADAM_SQ_FLOOR_K).square()
        n = int((v.detach() < floor).sum().item())
        if n:
            v.copy_(t.maximum(v, floor))
            raised += n
    return raised


def pack_opt_delta(base: dict, new: dict, *, base_sha: str) -> bytes:
    """worker 侧：`new` 相对 `base` 的 delta 容器（两者都是 `opt.state_dict()` 那一族形状）。

    `base_sha` = **本机实际载入的那份 base opt tar 的 sha256**（不是「hub 那份」的抽象说法）：
    exact 档下它会与 manifest 的 `opt_sha` 相等，hub 侧即可逐字节对账；identity 档下它只是
    「我拿哪份当基准」的记录（hub 无法核对，靠 `manifest.opt_from` 的身份闸，见 plan §12）。

    路径缺口一律 `ProtocolError`：结构性不一致时**拒绝**，而不是猜一个基准。

    ★ 叶子可以是 **torch 张量**（torch worker 的 `opt.state_dict()`）或 **numpy 数组**
    （Paddle worker：`worker.ppo.ckpt_convert.paddle_state_to_torch` 译出来的那份，基准则是
    `common.torch_pickle` 读出来的）——**两族逐条同规则**（浮点且非 0-dim 且同形 ⇒ delta-f16，
    其余原样），容器格式只有一个。两族**混用**一律拒（那是「基准与新版不是同一份状态」）。
    """
    known: dict[str, Any] = dict(_opt_items(base))
    items: list[dict] = []
    blobs: list[bytes] = []
    for path, leaf in _opt_items(new):
        if path not in known:
            raise ProtocolError(f"delta：基准里没有 {path}（结构不一致——拒发而不是猜）")
        b = known[path]
        fam = _family(leaf)
        if fam != _family(b):
            raise ProtocolError(
                f"delta：{path} 两侧类型不一致（{fam or '非张量'} vs {_family(b) or '非张量'}）"
            )
        if fam == "numpy":
            np = _np()
            dtype = str(leaf.dtype)
            shape = [int(x) for x in leaf.shape]
            # 与 torch 分支同一条两段判据：dtype 门 + `ndim > 0`（0-dim 的 Adam `step` 走 exact）。
            if dtype in _DELTA_DTYPES and leaf.ndim > 0 and b.shape == leaf.shape:
                raw = np.ascontiguousarray(
                    (leaf.astype(np.float32) - b.astype(np.float32)).astype(np.float16)
                ).tobytes()
                items.append(
                    {"path": path, "kind": "delta-f16", "dtype": "float16", "shape": shape,
                     "nbytes": len(raw)}
                )
            else:
                raw = np.ascontiguousarray(leaf).tobytes()
                items.append(
                    {"path": path, "kind": "exact", "dtype": dtype, "shape": shape,
                     "nbytes": len(raw)}
                )
            blobs.append(raw)
            continue
        if fam == "":
            try:
                json.dumps(leaf)
            except (TypeError, ValueError) as e:
                raise ProtocolError(f"delta：{path} 既不是张量也不是 JSON 值（{e}）") from None
            items.append({"path": path, "kind": "json", "value": leaf})
            continue
        t = _torch()
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


def _parse_delta_header(
    raw: bytes, expect_base_sha: str
) -> dict[str, tuple[str, Any, str, tuple[int, ...]]]:
    """容器 → `{path: (kind, payload, dtype, shape)}`（`payload` 是 bytes 或 json 值）。

    **两族共用**（torch 的 `apply_opt_delta` 与 numpy 的 `apply_opt_delta_np`）：魔数/头长/`ofmt`/
    `base_sha`/项结构/尾部多余字节的拒收面只此一处，改一处两边同时变 —— 各写一份必然分叉。
    不碰 torch/numpy（叶子值原样交出，由各自的 rebuild 解释）。
    """
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
    return declared


def apply_opt_delta(
    base: dict,
    raw: bytes,
    *,
    expect_base_sha: str = "",
    log: Callable[[str], None] | None = None,
) -> dict:
    """hub 侧：delta 容器加回 `base`（本机那份 base opt 的 state_dict）⇒ **新的** state_dict。

    调用方拿到后 `opt.load_state_dict(...)`（形状/键与 `base` 逐字相同）。`expect_base_sha` 非空时
    逐字节对账基准（exact 档用；identity 档留空，理由见 plan §12）。

    重建后调用 `sanitize_adam_sq`（f16 下溢消毒）；`log` 非空且抬了项时打一行计数。
    注意：`apply_opt_delta` 也被读侧落地（`opt_standard`）调用 ⇒ 本函数是 **hub 与读侧共用** 的消毒点。

    响亮拒收（**不**静默降级）：缺 torch · 魔数/头/长度不符 · `ofmt` 不认 · `base_sha` 与期望不符 ·
    项结构（path/shape）与 `base` 对不上 · 基准不是映射 · 尾部多余字节。
    """
    t = _torch()
    declared = _parse_delta_header(raw, expect_base_sha)

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
    raised = sanitize_adam_sq(out)
    if raised and log is not None:
        log(
            f"opt delta 重建：exp_avg_sq 下溢消毒抬高 {raised} 个元素"
            "（v=0/m≠0 动量炸弹，DECISIONS §2026-10-17-goalnn-opt-delta-f16-underflow）"
        )
    return out


def apply_opt_delta_np(
    base: dict,
    raw: bytes,
    *,
    expect_base_sha: str = "",
    log: Callable[[str], None] | None = None,
) -> dict:
    """`apply_opt_delta` 的 **numpy 版**（Paddle worker 读侧落地用，torch 免费的机器也能跑）。

    语义逐条同源：同一个 `_parse_delta_header`（魔数/头/`base_sha`/项结构/尾部字节）· 同一条
    `delta-f16` 相加规则 · 同一个 `sanitize_adam_sq_np` 消毒点。差别只在容器类型：`base` 的叶子
    是 numpy 数组（`common.torch_pickle.load_torch_pickle` 从 hub 的 `opt.pt` 读出来的），
    重建结果也是 numpy 数组 —— 直接喂 `worker.ppo.ckpt_convert.paddle_state_to_torch`。

    `dtype` 头里写的是 torch 的拼写（`float32` / `float16` / `bfloat16`…）：numpy 只认前两类，
    **bfloat16 无法在 numpy 里落成数组** ⇒ 响亮拒收（绝不静默降精度：那是改语义）。
    """
    np = _np()
    declared = _parse_delta_header(raw, expect_base_sha)

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
            dt = np.dtype(dtype)
        except TypeError:
            raise ProtocolError(
                f"delta {prefix} 的 dtype={dtype!r} numpy 落不了（bfloat16？）——拒收"
            ) from None
        arr = np.frombuffer(bytearray(payload), dtype=dt).reshape(shape).copy()
        if kind == "exact":
            return arr
        if tuple(node.shape) != shape:
            raise ProtocolError(f"delta {prefix} 形状不符：容器 {shape} != 本机 {tuple(node.shape)}")
        return node.astype(np.float32) + arr.astype(np.float32)

    out = rebuild(base)
    if declared:
        raise ProtocolError(f"delta 多出 base 里没有的项：{sorted(declared)[:3]}… —— 拒收")
    if not isinstance(out, dict):
        raise ProtocolError("delta 基准不是映射（state_dict 应为 dict）——拒收")
    raised = sanitize_adam_sq_np(out)
    if raised and log is not None:
        log(
            f"opt delta 重建（numpy）：exp_avg_sq 下溢消毒抬高 {raised} 个元素"
            "（v=0/m≠0 动量炸弹，DECISIONS §2026-10-17-goalnn-opt-delta-f16-underflow）"
        )
    return out


def rebuilt_opt_tar(
    container: bytes,
    base: dict,
    *,
    out_dir: str | Path,
    log: Callable[[str], None] | None = None,
) -> bytes:
    """`base`（**上一轮实际装载的** state_dict）+ `BOD1` 容器 ⇒ 整份 tar 字节（`opt.pt` 单件）。

    **这是重建的唯一实现**：两侧共用一份 ——

      * hub 侧落地（`land_opt_delta`，published 腿的回传受理）：base 从上一轮 landed 的
        `it{n-1}/ppo_ckpt_remote/opt.pt` 读；
      * 云机/本机**读侧**落地（`remote.opt_standard`，离线段自己下一轮的 init）：base 从本机
        产物链上的那份整份 tar 解出。

    `out_dir` 既收 `opt.pt`（下一轮的基准目录，hub 侧就靠它），又决定返回 tar 的成员元数据
    ——同一份输入两次调用的字节可能因 mtime 不同而相异，**权威性由调用方落盘的那份说话**
    （`base_sha` 只对**输入**逐字节对账，不对输出）。

    `log` 透传给 `apply_opt_delta`：下溢消毒抬了项时由调用方决定往哪记一行。
    """
    import torch

    if not is_container(container):
        raise ProtocolError("rebuilt_opt_tar 只接 BOD1 容器（整份 tar 走原路解包）——拒收")
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rebuilt = apply_opt_delta(base, container, log=log)
    torch.save(rebuilt, d / "opt.pt")
    return pack_tar_from_dir(d, ["opt.pt"])


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
    return rebuilt_opt_tar(
        opt_blob, torch.load(base_p, map_location="cpu"), out_dir=ckpt_dir, log=log
    )
