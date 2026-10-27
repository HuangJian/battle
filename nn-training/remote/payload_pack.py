"""remote/payload_pack.py — 发布端 payload 封装：tar.xz 打包 + 自检 + 显式扰动重打。

从 `remote/hub_client` 整块搬出（2026-10-14，wire-diet `plan/wire-diet.plan.md` §3）：搬的是
「发布端 payload 字节」这一件事的全部 —— 唯一打包入口 `pack_payload_zip` + 两条自检判据
（§4.5 reader 全链试解 / §4.6 旧判别反向探测）+ 重打上限。搬出后 `hub_client` 只留「要不要瘦身」
的决策与 job 面编排。

**为什么要搬**：用户口径的单文件 LOC 预算 < 1000 行（`tests/test_python_loc_budget.py`）；
hub_client 已到 993 行，wire-diet 的钩子面（+~9 行）必越线。拆法按「独立所有者 + 独立触发
条件」找连通分量 —— 「发布时把字节打成归档并验它」与「job 队列/租约/落位编排」正是两个所有者、
两个触发条件。搬出后本模块只 import `common.*`（+ stdlib）⇒ 拓扑秩 **L0**，`hub_client` 站在它
上面仍是 L1（`tests/helpers/remote_dag.py` 的层号不动其他人）。

红线（原样随行，**别在这里"顺手升级"**）：

* `_old_heuristic_misjudges` **故意**调旧启发式（`zipfile.is_zipfile` 的 EOCD 形似字节）——
  它的存在意义就是**模拟旧 worker**（事故：3501788 字节的**完好** tar.xz 被判成 zip，同一份
  字节每 5 分钟复现、零告警空转 3.5 小时）；
* 重打必须**显式扰动**（打包是确定性的：shard 排序 + tar 记源文件 mtime + lzma 确定性 ⇒ 不扰动
  就逐字节相同 ⇒ 同一份字节上旧判别的判定也相同 ⇒ 重打闭环永不收敛），且有次数上限
  （`PAYLOAD_SELFCHECK_ATTEMPTS`），触顶**响亮放弃**（不发布）；
* 自检走的是**同一条**打包路径 ⇒ 它验的正是变换（wire-diet 的 f16 / 打包端 reward）**之后**的
  字节，不是变换前的。
"""

from __future__ import annotations

import hashlib
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from common.payload import (
    PAYLOAD_PERTURB_NAME,
    PAYLOAD_XZ_CHUNKS,
    PAYLOAD_XZ_PRESET,
    TAR_END,
    MemberBytes,
    root_segment,
    shard_segment,
)
from common.platform_utils import rmtree_best_effort
from common.protocol import ProtocolError, pack_payload, unpack_payload
from common.shard import manifest_bytes

#: 发布端自检的重打上限（plan/accident.plan.md §4.5/§4.6，2026-09-21）。
#:
#: 为什么必须有上限：打包是**确定性**的（shard 排序 + tar 记源文件 mtime、lzma 确定性） ⇒
#: 不扰动就逐字节相同 ⇒ 同一份字节上旧判别的判定也相同 ⇒ 重打闭环永不收敛（上一版计划写的
#: “换 job_id 重打 ⇒ 换了字节”**不成立**，评审已判死改为“显式扰动 + 次数上限”）。
#: 三次是“换 xz 产出的字节形状再试两次”的数量，再多只是在赌压缩器的字节巧合。
PAYLOAD_SELFCHECK_ATTEMPTS = 3


def _old_heuristic_misjudges(payload: Path) -> bool:
    """§4.6 反向探测：**旧判别**（`zipfile.is_zipfile` 的 EOCD 启发式）会不会把这份
    tar.xz 误判成 zip？

    为什么要替**旧**判据操心：worker 侧的判别已改成“先 tar 后 zip”（§4.4），但**旧 worker**
    （code.zip 发版前的云机）仍走启发式。命中时它会 `BadZipFile`——现在（§4.0）会响亮报
    确定性失败，但仍然是白烧一轮认领。发布端能在**发之前**看出这份字节会被误判 ⇒ 重打。

    单独抽成函数（而不是内联 `zipfile.is_zipfile`）：① 判据可测试（单测把它换成“真”
    来走重打链）；② 它是**故意**调旧启发式的唯一处——下次有人想“顺手升级”它成新判别时，
    看这行注释：**它的存在意义就是模拟旧 worker**（事故：3501788 字节的**完好** tar.xz
    被判成 zip，同一份字节每 5 分钟复现、零告警空转 3.5 小时）。
    """
    return zipfile.is_zipfile(payload)


def _payload_reader_selfcheck(
    payload: Path, shard_dirs: list[Path], extra_files: list[Path]
) -> tuple[bool, str]:
    """§4.5：走 **reader 全链**试解（`protocol.unpack_payload` 本体）并与打包输入对账。

    为什么不是“能不能打开”：本次事故的毒包 tar 打开**完全正常**（3476 个成员全在），
    病在判别层——所以自检必须走“判别 + 解包”同一条路（`unpack_payload` 内部调
    `_extract_archive`），再断言产物与打包输入**逐名对得上**。

    返回 `(是否通过, 不通过的原因)`。**只有**内容决定性失败（`ProtocolError`）判不通过：
    “解不开”是这份字节的性质，重打有意义；而 `OSError`（磁盘/权限）重打也治不了，原样上抛
    让发布失败得响亮。

    成本（实测，2026-09-21 本机 16 核）：合成 payload 240 shard / 480 文件 / 1.77MB ⇒
    `pack` 0.67s、本函数 **1.18s**（+1.2s/轮发布，约 pack 的 1.8x，成本几乎全在“建 480 个
    文件”，与字节量关系不大）；反向探测 ≈0.000s。每轮多 1.2s 换毒包在上传前被拦下，
    不拿云端和训练侧的时间赌（事故那 3.5h = 25200s）。

    试解目录不用 `tempfile.TemporaryDirectory`：它在回收时走 `shutil.rmtree`，而本沙箱的
    删除保护 shim 会让它 `raise SystemExit`（`BaseException`，`ignore_errors` 拦不住，会当场
    打死调用线程——`common.platform_utils.rmtree_best_effort` 的注释里就是这条事故）。改用 job 目录
    下的 `.selfcheck/`（点目录：打包器/扫描器一律跳过）+ `rmtree_best_effort`。
    """
    check_dir = payload.parent / ".selfcheck"
    try:
        rmtree_best_effort(check_dir, ignore_errors=True)  # 上一次的残置（删不掉就重建）
        check_dir.mkdir(parents=True, exist_ok=True)
        _m, shard_out = unpack_payload(payload, check_dir)
        got = {Path(d).name for d in shard_out}
        want = {Path(d).name for d in shard_dirs}
        if got != want:
            return False, (
                f"解包出的 shard 目录与打包输入不符（缺 {sorted(want - got)} /"
                f" 多 {sorted(got - want)}）"
            )
        missing = [
            Path(f).name
            for f in extra_files
            if Path(f).is_file() and not (check_dir / Path(f).name).exists()
        ]
        if missing:
            return False, f"解包后缺额外文件 {missing}"
    except ProtocolError as e:
        return False, f"reader 全链试解失败：{e}"
    finally:
        rmtree_best_effort(check_dir, ignore_errors=True)
    return True, ""



def compress_segment(seg: bytes) -> bytes:
    """一段 tar → 已压好的段（`preset` 的**唯一**来源：分块打包与零落盘入口同口径）。

    同内容必同字节（lzma 确定性）⇒ 「收到就压」的段与打包时现压的段可以直接互换，
    这也是 `ShardSegments` 的段能被 `presegmented` **原样采用**的前提。
    """
    import lzma as _lzma

    return _lzma.compress(seg, preset=PAYLOAD_XZ_PRESET)


class ShardSegments:
    """本轮 shard 段的**内存账**（shard 名 → 已压好的 tar 段）—— payload 零落盘。

    plan/payload-zero-disk §3 的落地：dispatch 腿（sampler 节点 HTTP 回传）的 shard 字节本来
    就在内存里（`fetch_task` 的 `files`），旧流程落盘、打包时再读回来重压一遍。这里在**收到
    那一刻**就打段 + 压好（`add` 跑在 dispatch 线程里，天然与后续取活并行），打包时
    `_pack_segmented` 只做「拼接 + sha256」。

    四条不变量（`tests/remote/test_payload_parts.py` 钉着）：

    1. **段 == 盘**：同一 shard，内存段与「从落盘目录重打」逐字节相同 ⇒ 零落盘不改变 payload。
       所以 `manifest.json` 的字节必须走 `common.shard.manifest_bytes`（与 `write_shard` 同一处）。
    2. **每轮一份**：`begin(it)` 换轮即清空。上一轮的段漏进这一轮会是「`data_fp` 对不上但字节
       合法」的静默错包 —— 本类最危险的失败形态。
    3. **开关同源**：段里有没有 `reward.npy` / f16 是**建账时**的 `wire_diet` 决定的，`take()`
       拿它跟读的时候比 —— 热改开关后旧段一律不用（退回读盘，宁可慢也不发一份与 manifest
       自述不符的字节）。
    4. **一个 shard 只归一个线程**：`add` 里对 slim 的变换与写入都在锁内（slim 的计数器是共享
       可变状态）；lzma 压缩在锁外（那是最贵的一段，别把 dispatch 线程串起来）。

    缺的 shard（local 兜底腿 / 预采子进程 / 配额裁剪后补漏）**不需要**在这里处理：
    `_pack_segmented` 按名取不到段就现场读盘，同一个 `shard_segment` 函数体。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parts: dict[str, bytes] = {}
        self._slim: Any = None
        #: 这份账属于哪一轮（-1 = 还没有轮）。`take()` 只认同一个 it。
        self.it = -1
        #: 建账时的 `wire_diet`（见不变量 3）。
        self.wire_diet = False

    # ------------------------------------------------------------ 轮生命周期
    def begin(self, it: int, *, wire_diet: bool, slim: Any = None) -> None:
        """切到第 `it` 轮（同一 it 重复调用无副作用 —— 动态采集一轮里有多次派发）。

        `slim` 由调用方（`trainer/rollout_phase.begin_payload_parts`）建好传进来：本模块是
        payload **字节**的叶子所有者，不反过来依赖 `remote.payload_slim`（那会把 DAG 层号
        抬起来、连坐一串上位模块；`tests/test_remote_dag.py` 会当场红）。

        每轮**只一份** slim：rollout 期 `add` 用它算变换/计数，打包期 `take` 把同一个实例交给
        `publish_job` ⇒ `payload_fmt` / `fmt_line()` 描述的是真正发出去的那份字节（段全部命中时
        打包腿一个成员变换都不走，另建一份会报「未改」——日志骗人）。
        """
        it = int(it)
        if it == self.it:
            return
        with self._lock:
            self._parts.clear()
            self._slim = slim
            self.it = it
            self.wire_diet = bool(wire_diet)

    # ------------------------------------------------------------ dispatch 回调
    def add(self, name: str, files: Mapping[str, bytes], manifest: Mapping) -> None:
        """收到一个 shard：当场打段 + 压好存住（`RolloutDispatcher.on_shard` 的实现）。

        `name` 是 `rl_s{stage}_seed{seed}`（= 落盘目录名 = `_pack_segmented` 的键），三处
        必须逐字相同，否则 `presegmented` 静默查不到（不报错，只是白干一场）。
        """
        members = {str(k): bytes(v) for k, v in files.items()}
        # `write_shard` 落盘时补写 manifest.json ⇒ 段里必须有同一份字节（不变量 1）。
        members["manifest.json"] = manifest_bytes(dict(manifest))
        with self._lock:
            slim = self._slim
            on_member: Callable[[str, str, bytes], MemberBytes] | None = None
            extra: Mapping[str, bytes] | None = None
            if slim is not None:
                # ★ 交给 slim 的是 `members`（含 `manifest.json`）而不是 dispatch 的 `files`：
                #   `fetch_task` 回传的文件集**不含** manifest（它单独作为返回值的第一个），
                #   而 P1（打包端算 reward）要从 manifest 取 outcome/score/metrics_version。
                #   内存源缺这个成员时 `_reward_bytes` 会响亮抛 ProtocolError —— 实测踩过。

                def on_member(_nm: str, fname: str, _data: bytes) -> MemberBytes:
                    got: MemberBytes = slim.member_bytes_of(name, members, fname)
                    return got

                extra = slim.shard_extra_of(name, members)
            seg = shard_segment(name, members, on_member=on_member, extra_members=extra)
        comp = compress_segment(seg)  # 锁外（不变量 4）
        with self._lock:
            self._parts[name] = comp

    # ------------------------------------------------------------ 打包读取
    def take(self, it: int, *, wire_diet: bool) -> tuple[Mapping[str, bytes], Any] | None:
        """本轮可用的 `(presegmented, slim)`；轮号或开关对不上 / 一个段都没有 ⇒ `None`。

        返回的是**浅拷贝**（值是不可变 bytes，拷贝只复制引用）：调用方（`publish_job`）
        不该拿到一个还在被 dispatch 线程写的字典。
        """
        with self._lock:
            if int(it) != self.it or bool(wire_diet) != self.wire_diet or not self._parts:
                return None
            return dict(self._parts), self._slim


def _pack_segmented(
    shard_dirs: Sequence[Path],
    zip_path: Path,
    *,
    extra_files: Sequence[Path],
    perturb: bytes | None,
    member_bytes: Callable[[Path, str], MemberBytes] | None,
    shard_extra: Callable[[Path], Mapping[str, bytes]] | None,
    chunks: int,
    presegmented: Mapping[str, bytes] | None,
) -> str:
    """分块并行打包：每 shard 一段 tar → 各自 lzma → 按名排序拼接 + 根级段 + `TAR_END`。

    **为什么这段在 remote/ 而不是 common/payload.py**：`common.payload` 的 import 面是
    **闭集**（`tests/common/test_payload_split.py` 钉着），不许引 `lzma` / `concurrent.futures`；
    而分块并行同时需要这两个。字节构造（`ustar_header` / `shard_segment` / `root_segment`）
    留在 common（纯 stdlib 基础），调度留在这一层。

    `presegmented`（shard 名 → **已压好的段字节**）= dispatch 腿的零落盘入口：那边收到
    sampler 回传的字节就把段压好存住，这里直接取用；缺的（local 兜底腿 / 补漏）现场从盘补，
    **同一个 `shard_segment` 函数体**，没有第二条变换逻辑。
    """
    import concurrent.futures as cf

    dirs = {Path(d).name: Path(d) for d in shard_dirs}
    names = sorted(dirs)  # 确定性：同内容必同字节序 ⇒ 同 sha

    def seg_one(nm: str) -> bytes:
        pre = (presegmented or {}).get(nm)
        if pre is not None:
            return pre
        d = dirs[nm]
        on_member: Callable[[str, str, bytes], MemberBytes] | None = None
        if member_bytes is not None:

            def on_member(_nm: str, fname: str, _data: bytes, _d: Path = d) -> MemberBytes:
                return member_bytes(_d, fname)

        extra = shard_extra(d) if shard_extra is not None else None
        seg = shard_segment(nm, shard_dir=d, on_member=on_member, extra_members=extra)
        return compress_segment(seg)

    parts: list[bytes] = []
    if names:
        with cf.ThreadPoolExecutor(max_workers=max(1, min(chunks, len(names)))) as ex:
            parts = list(ex.map(seg_one, names))

    root: dict[str, bytes] = {}
    for xf in extra_files:
        fx = Path(xf)
        if fx.is_file():
            root[fx.name] = fx.read_bytes()
    if perturb is not None:
        root[PAYLOAD_PERTURB_NAME] = bytes(perturb)
    if root:
        parts.append(compress_segment(root_segment(root)))
    parts.append(compress_segment(TAR_END))

    tmp = zip_path.with_suffix(zip_path.suffix + ".tmp")
    h = hashlib.sha256()
    with open(tmp, "wb") as fh:
        for b in parts:
            fh.write(b)
            h.update(b)
    tmp.replace(zip_path)
    return h.hexdigest()

def pack_payload_zip(
    shard_dirs: list[Path],
    extra_files: list[Path],
    zip_path: Path,
    manifest: dict,
    *,
    member_bytes: Callable[[Path, str], MemberBytes] | None = None,
    shard_extra: Callable[[Path], Mapping[str, bytes]] | None = None,
    chunks: int = PAYLOAD_XZ_CHUNKS,
    presegmented: Mapping[str, bytes] | None = None,
    log=lambda msg: None,
) -> str:
    """把 shard 目录 + 额外文件（有 opt blob 时仅 shard；否则含 init_weights.json）
    打成 payload 归档（**tar.xz**，2026-09-10 起；实测体积 −48.7% 而打包耗时持平），
    **并走发布端自检**（§4.5 reader 全链 + §4.6 旧判别反向探测），不通过就显式扰动重打。
    返回归档字节 sha256。

    自检为何长在这里：本函数是发布侧**唯一**打包入口（`publish_job` 只调它；重发/逐轮重试
    走的也是它），把检查放在入口而不是各调用点 ⇒ 不存在“某个调用者忘了自检”的路径。
    自检失败到顶 ⇒ `ProtocolError` 响亮放弃（**不发布**）：宁可不发，也不发一份会把云端
    与训练侧一起毒到天亮的包。

    M2（B1/B2/B4）：不再写根级占位 manifest.json、不再写 opt_init.tar.b64；有 opt
    blob 时也不写 init_weights.json（opt tar 已含 model+Adam）。布局 = shard 目录整体
    （+ 可选 init_weights.json）。`manifest` 形参保留只为调用签名兼容。

    2026-09-17：实现改为**转调** `common.protocol.pack_payload`（多了一个 `extra_files`
    形参）——半离线（kind=run）的逐轮 payload 也要带额外文件，第三个 tar.xz 打包副本
    没有道理，而两份口径本就只差一个 extra 循环。

    重打（`attempt > 1`）时用 `PAYLOAD_PERTURB_NAME` 写一段 per-attempt nonce ⇒ 字节
    确实变了；但若扰动后**依旧**被误判/仍解不开，说明不是字节巧合而是结构性问题 ⇒ 触顶
    响亮放弃（见 `PAYLOAD_SELFCHECK_ATTEMPTS`），**不**无限重打。

    `member_bytes` / `shard_extra`（wire-diet，2026-10-14）：直接透传给
    `common.payload.pack_payload`（逐成员替换/丢弃 + 每 shard 新成员）。自检走的是**同一条**
    打包路径，所以它验的正是变换后的字节（不是变换前的）。缺省 None = 逐字节旧行为。
    """
    last_why = ""
    for attempt in range(1, PAYLOAD_SELFCHECK_ATTEMPTS + 1):
        perturb = None if attempt == 1 else f"repack-{attempt}".encode("ascii")
        if chunks > 1:
            # ★ 2026-10-27：分块并行（默认 8 块）—— 见 `_pack_segmented` 的 docstring。
            #   `chunks == 1` 是**逐字节旧行为的总开关**：分块与 `presegmented` 一起跳过
            #   （plan/payload-zero-disk §4 判据 6：改回 1 就回到单流 tarfile）。
            #   **自检照走**（外层 for attempt 循环把它俩同等对待，否则分块路径没有护栏）。
            sha = _pack_segmented(
                shard_dirs,
                Path(zip_path),
                extra_files=list(extra_files),
                perturb=perturb,
                member_bytes=member_bytes,
                shard_extra=shard_extra,
                chunks=int(chunks),
                presegmented=presegmented,
            )
        else:
            sha = pack_payload(
                shard_dirs,
                manifest,
                zip_path,
                extra_files=list(extra_files),
                perturb=perturb,
            member_bytes=member_bytes,
            shard_extra=shard_extra,
        )
        p = Path(zip_path)
        # ① §4.6 旧判别反向探测（新 worker 无感；旧 worker 会在这一步炸）
        if _old_heuristic_misjudges(p):
            last_why = (
                "旧判别（zipfile.is_zipfile 的 EOCD 启发式）把这份 tar.xz 误判成 zip"
                "——旧 worker 会 BadZipFile"
            )
            ok = False
        else:
            # ② §4.5 reader 全链自检
            ok, last_why = _payload_reader_selfcheck(p, shard_dirs, extra_files)
        if ok:
            if attempt > 1:
                log(
                    f"[publish] payload 自检第 {attempt} 次通过（前 {attempt - 1} 次不通过"
                    f"，已显式扰动重打）；sha256={sha[:12]}…"
                )
            return sha
        if attempt < PAYLOAD_SELFCHECK_ATTEMPTS:
            log(
                f"[publish] payload 自检不通过（第 {attempt}/{PAYLOAD_SELFCHECK_ATTEMPTS} 次）："
                f"{last_why} —— 显式扰动后重打"
            )
    raise ProtocolError(
        f"payload 自检连续 {PAYLOAD_SELFCHECK_ATTEMPTS} 次不通过，**放弃发布**：{last_why}"
        "（显式扰动重打也没能换掉这个字节形状；结构性问题，不是字节巧合）"
    )
