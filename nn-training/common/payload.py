"""common/payload —— 语料**归档**（shard 目录 -> 一个文件）怎么打、怎么解（S5 第八刀，2026-09-27）。

从 `common/protocol.py` 整块搬出（**逐字节不动**）。这里是「一份语料怎么变成一个可传输的字节串」
的唯一实现，与它旁边的三件事分开：

* **打包**：`pack_payload`（tar.xz preset=3；比 zip/deflate 小 48.7% 而耗时持平）·
  `_add_bytes`（把内存字节写进 tar，免得为 manifest 落临时文件）；
* **解包**：`unpack_payload` + `_extract_archive`——**双读** tar 系 / legacy zip，判别靠**行为**
  （先试 tar：`r:*` 的 magic/透明度判别；zip 的 `PK\x03\x04` 永远过不了 tar 头验证）；打不开 =
  **内容决定性**失败 ⇒ `ProtocolError`（不静默继续）；解出**空包**同样响亮；
* **容器名**：`PAYLOAD_NAME` / `PAYLOAD_LEGACY_NAMES` / `PAYLOAD_PERTURB_NAME` / `PAYLOAD_XZ_PRESET`
  / `INIT_WEIGHTS_NAME` + `find_payload`（优先新名 tar.xz，回退旧名 zip）。

**为什么单独成家**：这是**字节容器**面；与「manifest 字段合不合法」「这份活是谁」
「HTTP 体上限多少」是四件事。`unpack_payload` 的两条响亮判据（空包 / 双读失败）都是
**事故复盘**（2026-09-21：EOCD 启发式把完好 tar.xz 误判成 zip ⇒ 毒包每 5 分钟复现、零告警空转
3.5 小时），只有在一处才好守。

依赖面 = stdlib（`hashlib` / `io` / `json` / `tarfile` / `zipfile` / `pathlib` / `typing`）+
`common.errors`（失败类型叶子）。**不** import `common.protocol`（无环）。

`common/protocol.py` 保留 `X as X` 门面 ⇒ 历史 `from common.protocol import …` 一行不改。
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from common.errors import ProtocolError

# ------------------------------------------------------------------ payload
# ---- payload 容器（2026-09-10：zip/deflate -> tar.xz）----
# 实测（20 个真实 c5-margin shard，裸 22.3 MB，×7.5 折算 150 份）：
#   ZIP_DEFLATED(6)  241,382 B / 1.8 s
#   ZIP_LZMA         187,443 B / 9.0 s   （只 −22.3% 且慢 5×，已否决）
#   tar.xz(preset=3) 123,788 B / 1.9 s   <== 采用：体积 −48.7%，打包耗时持平
# 折算真实 payload 3.83 MB -> ~1.96 MB，下载 2.3 s -> ~1.2 s。stdlib，无新依赖。
# 解析端**双读**（zipfile.is_zipfile 判别）⇒ 旧 hub 产的 payload.zip 与新 hub 产的
# payload.tar.xz 对新旧 worker 都能工作。
PAYLOAD_NAME = "payload.tar.xz"
PAYLOAD_LEGACY_NAMES: tuple[str, ...] = ("payload.zip",)

#: kind=iter 的 payload 内要点名的 init 权重文件名（节点跑 rollout 的 --weights）。
INIT_WEIGHTS_NAME = "init_weights.json"
#: 发布端自检要求重打时写进归档根的**扰动**文件名（plan/accident.plan.md §4.6，2026-09-21）。
#: 它**不是数据**：存在的唯一意义是让重打的字节与上一次不同——打包是确定性的
#: （shard 排序 + tar 记源文件 mtime、lzma 确定性），不扰动就会逐字节相同，旧判别
#: （`zipfile.is_zipfile` 的 EOCD 启发式）在同一份字节上**永远**为真 ⇒ 重打闭环不收敛。
#: worker 侧忽略根级未知文件（只按名取 init_weights.json / plan.json，shard 靠扫描
#: 带 manifest.json 的子目录），故向前兼容。
PAYLOAD_PERTURB_NAME = "payload.perturb"
# 标注成 Literal：typeshed 的 tarfile.open("w:xz") 重载要求 preset 为 Literal[0..9]，
# 普通 int 过不了 mypy。**改档位时这里要同步改**（比如变 5 就写 Literal[5]）。
#
# ⛔ **B6（preset 3→6）已量、不采用，别再重测**（2026-09-17，3 份**真** payload ×
#    240 shard、走本函数、每份独立跑）：体积只 **−2.6…−3.0%**（~34 KB），打包却
#    **+188…+199%**（3.2s → 9.4s，即关键路径 **+6.2…+6.5s/轮**）；解包未见收益
#    （5.4→5.8 / 5.7→6.3 / 6.4→5.9 s，噪声内）。按隧道实测 ~3.5 Mbps，那 34 KB 只值
#    ~0.08s 传输 ⇒ **用 +6.3s CPU 换 0.08s 是净亏**，且计划 §4.2 的门槛是「收益 <10%
#    就别做」。同批真数据另证 B1/B2/B4：同一批 job 原盘 payload 2,045,276/2,034,536/
#    2,074,996 B → 仅打 shard 后 1,198,096/1,187,392/1,227,432 B（**−41.4%/−41.6%/−40.8%**）。
PAYLOAD_XZ_PRESET: Literal[3] = 3

#: payload 分块并行的**块数**（2026-10-27）。`1` = 逐字节旧行为（tarfile 边打边压、单流）。
#: 为什么默认 8：`tarfile.addfile`/`TarInfo.tobuf()` 是纯 Python 持 GIL（实测 648 shard
#: 打段 8.5 s，比 lzma 压缩 5.0 s 还贵）；换成手写头 + 线程池后**打段+压 7.6 s**
#: （单流基线 50.0 s，6.6×），体积只 +1.8%（同口径比 tarfile 版）。xz 支持串联流，
#: 所以分块对**解包侧零改动**（实测 648 段：成员 9072/9072、字节逐位一致）。
#: 块数不是越大越好：块越小 → 每块字典重建 → 压缩率越差（8 块 +0.4%、16 块 +0.6%）。
PAYLOAD_XZ_CHUNKS = 8


#: 「这个成员**不要**进归档」的哨兵（与 `bytes` / `None` 分列：`None` = 原样保留）。
#: 为什么要有第三种结局：wire-diet（`plan/wire-diet.plan.md` §3 P1）要**丢掉** metrics.npy、
#: **替换** scalars.npy 之类的字段、其余原样——把三种压成两个返回值，调用方就得再传一份
#: 「要丢谁」的名单，那就是第二个真相。
class _DropMember:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 只为日志/断言可读
        return "DROP_MEMBER"


#: `pack_payload(member_bytes=…)` 的返回值之一：丢弃该成员（见 `_DropMember`）。
DROP_MEMBER = _DropMember()

#: `member_bytes` 的返回类型：替换字节 / 原样（None）/ 丢弃（`DROP_MEMBER`）。
MemberBytes = bytes | None | _DropMember


def find_payload(job_dir: str | Path) -> Path | None:
    """定位 job 目录下的 payload（优先新名 tar.xz，回退旧名 zip）——新旧互通。"""
    jd = Path(job_dir)
    for name in (PAYLOAD_NAME, *PAYLOAD_LEGACY_NAMES):
        p = jd / name
        if p.exists():
            return p
    return None


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    """把一个内存字节串写进 tar（避免为 manifest 落临时文件）。"""
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


# ---------------------------------------------------------------- 手写 ustar 段（2026-10-27）
# 为什么不用 `tarfile` 打段：`TarInfo.tobuf()` + `addfile` 是纯 Python 且持 GIL
# （实测 8424 个头 8.1 s，比 lzma 压缩 5.0 s 还贵），`addfile` 还会 `copyfileobj` 一遍、
# `BytesIO.getvalue()` + 切片各复制一遍。改成「手写 512B 头 + bytearray 直接拼」后
# 648 shard 打段 8.47 s → **3.42 s**（其中 2.1 s 是读盘），且与 `TarInfo`（固定字段口径）
# **逐字节相同**（`tests/common/test_payload_segment.py` 钉着）。

#: ustar 头的固定字段（**统一固定**，不模仿 `tf.add` 的源文件 mtime/mode/uid/uname）。
#: 代价：payload 字节与旧路径不同；收益：**同内容必同 sha**（旧路径受文件 mtime 影响会漂）。
TAR_MTIME = 0
TAR_MODE = 0o644


def ustar_header(name: str, size: int) -> bytes:
    """一个 512B ustar 成员头（与 `TarInfo(name, size, mtime=0, mode=0o644, uid=gid=0,
    uname=gname='').tobuf()` 逐字节相同）。

    超过 100 字节的名字要走 PAX 扩展头 —— 本实现**响亮拒绝**（不静默降级成错误的头）：
    我们的成员名最长是 `rl_s{stage}_seed{seed}/{file}`，实测 25–38 字节。
    """
    nb = name.encode()
    if len(nb) > 100:
        raise ValueError(f"tar 成员名超过 100 字节（需 PAX 扩展头，本实现不支持）：{name!r}")
    h = bytearray(512)
    h[0 : len(nb)] = nb
    h[100:108] = b"%07o\x00" % TAR_MODE
    h[108:116] = b"%07o\x00" % 0  # uid
    h[116:124] = b"%07o\x00" % 0  # gid
    h[124:136] = b"%011o\x00" % size
    h[136:148] = b"%011o\x00" % TAR_MTIME
    h[148:156] = b" " * 8  # checksum 先填空格，再按「空格参与求和」补回
    h[156] = 0x30  # typeflag '0' = 普通文件
    h[257:263] = b"ustar\x00"
    h[263:265] = b"00"
    h[148:156] = b"%06o\x00 " % sum(h)
    return bytes(h)


def shard_segment(
    shard_name: str,
    members: Mapping[str, bytes] | None = None,
    *,
    shard_dir: str | Path | None = None,
    on_member: Callable[[str, str, bytes], bytes | _DropMember | None] | None = None,
    extra_members: Mapping[str, bytes] | None = None,
) -> bytes:
    """把一个 shard 打成 **一段 tar 字节**（成员头 + 数据 + 512 对齐；**无结尾 1024B 标记**）。

    为什么是「一段」而不是整份 tar：分块并行压缩 + 「收到就压」都要求段边界稳定。
    xz 支持**串联流**，把 N 段各自压完按序拼接仍是合法 `.tar.xz`
    （实测 648 段：`tarfile` 完整解回 8424/8424 成员、字节逐位一致）。

    `members` 给了 = 用内存 dict（dispatch 腿，shard 还没落盘）；否则从 `shard_dir` 读盘
    （local 兜底腿 / 打包时补漏）。**两条腿走同一行代码**，只差数据来源。

    `on_member(name, filename, data)` 是逐成员变换（`member_bytes` 的 slim 语义）：
    返回 `bytes` = 替换、`None` = 原样、`DROP_MEMBER` = 丢弃。
    `extra_members` = 该 shard 额外要加的成员（`shard_extra` 现算的 `reward.npy` 等），
    与磁盘成员**同名即拒**（重复 arcname 会由解包顺序决定训练吃哪份）。
    """
    if members is None:
        if shard_dir is None:
            raise ValueError("shard_segment 需要 members（内存）或 shard_dir（读盘）之一")
        p = Path(shard_dir)
        if not p.is_dir():
            raise ProtocolError(f"shard_segment: shard 目录不存在 {p}")
        members = {f.name: f.read_bytes() for f in sorted(p.iterdir()) if f.is_file()}

    out = bytearray()
    seen: set[str] = set()
    for fname in sorted(members):
        data = members[fname]
        got = on_member(shard_name, fname, data) if on_member is not None else None
        if isinstance(got, _DropMember):
            continue
        seen.add(fname)
        payload = data if got is None else got
        out += ustar_header(f"{shard_name}/{fname}", len(payload))
        out += payload
        pad = (-len(payload)) % 512
        if pad:
            out += bytes(pad)
    for xname, xdata in sorted((extra_members or {}).items()):
        if xname in seen:
            raise ProtocolError(
                f"shard_segment: shard {shard_name} 的额外成员 {xname} 与盘上成员同名"
                "——重复 arcname 会由解包顺序决定训练吃哪份（偶然语义），拒绝打包"
            )
        xb = bytes(xdata)
        out += ustar_header(f"{shard_name}/{xname}", len(xb))
        out += xb
        pad = (-len(xb)) % 512
        if pad:
            out += bytes(pad)
    return bytes(out)


#: tar 的结尾标记（两段 512B 零块）—— 串联流的**最后一段**必须带上它。
TAR_END = bytes(1024)


def root_segment(members: Mapping[str, bytes]) -> bytes:
    """归档**根级**成员的一段 tar（`extra_files` / `perturb`：arcname 不带 shard 前缀）。

    与 `shard_segment` 同一个头/对齐口径，只是名字没有 `{shard}/` 前缀。
    顺序 = 成员名排序（确定性）。
    """
    out = bytearray()
    for name in sorted(members):
        data = bytes(members[name])
        out += ustar_header(name, len(data))
        out += data
        pad = (-len(data)) % 512
        if pad:
            out += bytes(pad)
    return bytes(out)


def _extract_archive(src: Path, dest: Path) -> None:
    """解包 payload 归档：**双读** tar 系 / zip（按内容**行为**判别，不看扩展名）。

    ★ 判别顺序 = **先 tar 后 zip**（2026-09-21 事故，plan/accident.plan.md §4）：
    原实现先问 `zipfile.is_zipfile()` —— 那是 stdlib 的 EOCD 形似字节启发式，xz 压缩
    数据里恰好出现该形状时**会误报为 True**。实测事故：3501788 字节的**完好** tar.xz
    被判成 zip ⇒ `BadZipFile: Bad offset for central directory` ⇒ worker 当瞬态重认领
    ⇒ 同一份毒包每 5 分钟复现、零告警空转 3.5 小时。

    反过来先试 tar 是**严格更可靠**的判别：tar 系靠 magic/透明度（`r:*`）认领，zip 的
    local header（`PK\x03\x04`）永远过不了 tar 头验证 ⇒ 行为判别天然 try/except，不再
    依赖任何启发式；legacy zip 包走后面的回退分支（tar.xz 化之前的包仍可解）。

    打不开 = **内容决定性**失败 ⇒ `ProtocolError`（不是 `BadZipFile`/`TarError`）：
    同一份字节重领永远不会自愈，必须让 worker 走确定性上报而不是重认领。
    `OSError`（磁盘/权限）例外——那是基础设施瞬时故障，原样抛出交由重认领处理。
    """
    try:
        with tarfile.open(src, "r:*") as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # Python < 3.12 无 filter 参数
                tf.extractall(dest)
        return
    except OSError:
        raise  # 磁盘/权限类 = 瞬时基础设施问题，不归容器判别
    except Exception as e:  # 非 tar 系（含 legacy zip）/ 损坏 —— 落 zip 分支再判一次
        tar_err: BaseException = e
    if not zipfile.is_zipfile(src):
        raise ProtocolError(
            f"payload 容器双读失败（tar 侧 {type(tar_err).__name__}: {tar_err}）——"
            "内容确定性失败，重领同一份字节不会自愈"
        ) from tar_err
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
    except Exception as e:
        raise ProtocolError(
            f"payload zip 解包失败（内容确定性）：{type(e).__name__}: {e}"
        ) from e


def pack_payload(
    shard_dirs: Sequence[str | Path],
    manifest: dict,
    out_path: str | Path,
    *,
    extra_files: Sequence[str | Path] | None = None,
    perturb: bytes | None = None,
    member_bytes: Callable[[Path, str], MemberBytes] | None = None,
    shard_extra: Callable[[Path], Mapping[str, bytes]] | None = None,
) -> str:
    """把 shard 目录（npy + manifest.json）打成 **tar.xz**，写 `out_path`。

    容器演进（2026-09-10）：原为 zip/deflate —— 实测 tar.xz(preset=3) 体积 −48.7%
    而打包耗时持平（stdlib、无新依赖），解析端 `unpack_payload` 双读兼容。
    布局不变：每个 shard 目录整体进入（目录名 rl_s{stage}_seed{seed}/…），根下再写
    一份 manifest.json（payload_sha256 占位空串——最终哈希由调用方对**本函数产出的
    字节**计算后回填 job 记录，worker 以 job 记录的 payload_sha256 对原始下载字节
    校验，D1——防隧道截断）。

    返回文件字节 sha256。调用方拿到后应把 sha 写入 job 记录/账本。

    `perturb`（§4.6，2026-09-21）：非 None 时把这段字节以 `PAYLOAD_PERTURB_NAME` 写进
    归档根——发布端自检要求重打时用它**显式扰动**字节（确定性打包下唯一的换字节手段）。

    `member_bytes`（wire-diet，2026-10-14）：**成员级字节变换**的入口，逐 shard 成员文件调用
    `member_bytes(shard_dir, filename)`，返回值三种：`bytes` = 用这段字节替换该成员（走
    `_add_bytes`，`TarInfo` 的 mtime/uid/gid 取缺省 ⇒ **打包仍决定性**）· `DROP_MEMBER` = 该成员
    不进归档 · `None` = 原样保留（仍走 `tf.add`，成员头照旧记源文件 mtime）。
    **不覆盖** `extra_files`（根级额外文件，如 init_weights.json）——变换面刻意只到 shard 成员。

    `shard_extra`（wire-diet，2026-10-14）：**每 shard 目录新加的成员**（`{文件名: 字节}`，
    arcname 由本函数加上 shard 目录名前缀、按名排序写入）。现实需要来自 P1：`reward.npy` /
    `reward_meta.json` 在源目录里**不存在**，逐文件回调（只对它看到的磁盘成员调用）造不出来。
    与磁盘成员**同名**即 `ProtocolError` —— 重复 arcname 由解包顺序决定训练吃哪份（偶然语义，
    与发布端「同名 shard 去重」同一条不变量）。
    缺省两个都是 None = 逐字节旧行为（`member_bytes is None` 时一行都没多走）。
    """
    zpath = Path(out_path)
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_suffix(zpath.suffix + ".tmp")
    with tarfile.open(tmp, "w:xz", preset=PAYLOAD_XZ_PRESET) as tf:
        for d in shard_dirs:
            p = Path(d)
            if not p.is_dir():
                raise ProtocolError(f"pack_payload: shard 目录不存在 {p}")
            seen: set[str] = set()
            for f in sorted(p.iterdir()):
                if not f.is_file():
                    continue
                arc = f"{p.name}/{f.name}"
                if member_bytes is None:
                    tf.add(f, arcname=arc)
                    seen.add(f.name)
                    continue
                got = member_bytes(p, f.name)
                # 三种结局按类型分派（不用 `is DROP_MEMBER` 比较：那是值比较，`isinstance` 才让
                # 类型检查器也把 `got` 缩到 bytes——`bytes(DROP_MEMBER)` 是能编译但荒谬的一路）。
                if isinstance(got, _DropMember):
                    continue
                seen.add(f.name)
                if got is None:
                    tf.add(f, arcname=arc)
                else:
                    _add_bytes(tf, arc, got)
            for xname, xdata in sorted((shard_extra(p) if shard_extra else {}).items()):
                if xname in seen:
                    raise ProtocolError(
                        f"pack_payload: shard {p.name} 的额外成员 {xname} 与盘上成员同名"
                        "——重复 arcname 会由解包顺序决定训练吃哪份（偶然语义），拒绝打包"
                    )
                _add_bytes(tf, f"{p.name}/{xname}", bytes(xdata))
        # 额外文件（落归档**根**：init_weights.json / plan.json 等按名取用）。
        # 2026-09-17：从 `hub_client.pack_payload_zip` 合并进来（2026-10-14 起那份住在
        # `remote.payload_pack`）——原来两个打包器
        # （一个带 extra 一个不带）各自维护 tar.xz 口径，半离线段又需要一个带
        # extra 的，第三个副本毫无道理：统一到这里，hub 侧那个改为转调。
        for xf in extra_files or ():
            fx = Path(xf)
            if fx.is_file():
                tf.add(fx, arcname=fx.name)
        # 显式扰动（仅发布端自检要求重打时；见 `PAYLOAD_PERTURB_NAME`）。写在 extra 之后、
        # 归档根，非 shard 目录 ⇒ 不进 shard 名单、不影响任何按名取用的文件。
        if perturb is not None:
            _add_bytes(tf, PAYLOAD_PERTURB_NAME, bytes(perturb))
        # M2（B1）：不再写根级占位 manifest.json —— worker.py:896 一直把它当
        # `_unused_manifest` 丢弃（~0.89MB/轮纯冗余）。权威 manifest 走 job 记录
        # （peek/claim 返回），本函数只负责搬运 shard 数据。`manifest` 形参保留
        # 只为调用方签名兼容（不再进字节）。
    tmp.replace(zpath)
    return hashlib.sha256(zpath.read_bytes()).hexdigest()



def unpack_payload(payload_path: str | Path, dest: str | Path) -> tuple[dict, list[str]]:
    """解包 payload → (manifest, shard_dir_paths)。**双读**：zip 与 tar.xz 都支持。

    shard_dir_paths 为解包后落在 dest 下的各 shard 目录（含 manifest.json），
    供 worker 的 load_episodes 消费。返回的 manifest 为归档内副本（payload_sha256
    为占位空串）——**不作权威校验**；worker 必须用 job 记录（peek/claim 返回）
    的 manifest 做 payload_sha256 / commit / mode 等全部校验（本函数只解包）。
    """
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    _extract_archive(Path(payload_path), dest_p)
    # 解包产物**非空**断言（2026-09-21，§4）：判别反转后"tar 打开成功却解出空包"是新
    # 路径特有的失败形态（旧实现误判时是直接抛错）。空包必须响亮——否则下游按"零 shard"
    # 静默继续（PPO 拿到空语料 = 比报错更坏的静默失败）。
    if not any(dest_p.iterdir()):
        raise ProtocolError(
            f"payload 解包产物为空（{payload_path}）——容器判别/解包路径异常，拒绝静默继续"
        )
    # M2（B1）：新 hub 产的 payload 不含根级 manifest.json（占位副本已删）——
    # 有则读、无则返回 {}（旧 payload 仍兼容；权威校验全走 job 记录 manifest）。
    mp = dest_p / "manifest.json"
    if mp.exists():
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {}
    shard_dirs: list[str] = []
    for p in sorted(dest_p.iterdir()):
        if p.is_dir() and (p / "manifest.json").exists():
            shard_dirs.append(str(p))
    return manifest, shard_dirs
