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
from collections.abc import Sequence
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
    """
    zpath = Path(out_path)
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_suffix(zpath.suffix + ".tmp")
    with tarfile.open(tmp, "w:xz", preset=PAYLOAD_XZ_PRESET) as tf:
        for d in shard_dirs:
            p = Path(d)
            if not p.is_dir():
                raise ProtocolError(f"pack_payload: shard 目录不存在 {p}")
            for f in sorted(p.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{p.name}/{f.name}")
        # 额外文件（落归档**根**：init_weights.json / plan.json 等按名取用）。
        # 2026-09-17：从 hub_client.pack_payload_zip 合并进来——原来两个打包器
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
