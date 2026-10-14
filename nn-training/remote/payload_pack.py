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

import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

from common.payload import MemberBytes
from common.platform_utils import rmtree_best_effort
from common.protocol import ProtocolError, pack_payload, unpack_payload

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


def pack_payload_zip(
    shard_dirs: list[Path],
    extra_files: list[Path],
    zip_path: Path,
    manifest: dict,
    *,
    member_bytes: Callable[[Path, str], MemberBytes] | None = None,
    shard_extra: Callable[[Path], Mapping[str, bytes]] | None = None,
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
