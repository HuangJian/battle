"""remote/course_cache.py —— 本机权威副本的**身份映射**（R0 / M0，`plan/wire-diet.plan.md` §3 R0）。

## 为什么需要它（一句话）

内容寻址缓存的键 = **hub 权威字节**的 sha。R1（权重 delta）/ R3（opt delta）让 hub 那份变成
「由 delta 重建的产物」⇒ 与 worker 本机那份原样字节**必然不同 sha** ⇒ 键失配 ⇒ 命中失效。
净账是**负的**（R1 省 200,659 B 上行却多付 387,540 B 下行；R3 同理 614,400）——**没有 R0，
M3/M4 不能上**。

所以本模块把这件事从「按字节寻址」换成「按**身份**寻址」：记下「哪个 job 的产物是哪两份本机
f32 全量文件」，下一轮 `manifest.opt_from == 那个 job_id` 时直接复用本机副本（零字节、零精度
损失）；身份不符 / 文件被 prune / 换机 / 自校不过 ⇒ 返回 None，调用方**回退既有内容寻址路径**
（行为与今天逐字节一致）。

## 磁盘布局（住 `--cache-dir` 的持久根，与 `blob_cache` 同源）

```
<cache_root>/course_cache/<course_fp>/latest.json      # 已确认可用的那一份（唯一的命中判据）
<cache_root>/course_cache/<course_fp>/pending.json     # 本机刚跑完、还没等到回传受理的那一份
```

两份都是 `{job_id, it, run_id, weights:{sha,bytes}, opt:{sha,bytes}, updated_at}`；**字节本体不
复制**——`weights.sha` / `opt.sha` 就是本机 `blob_cache/<sha>` 里的那两份（`train_core` 产物段
本来就会把它们写进去，见 `_cache_produced_weights` / `_cache_blob`）。

## ★ 为什么是「pending → confirm」而不是写完就算

竞速（多副本/多机跑同一个 job）下，**输了的那台**也在本地算出过一份产物，但 hub 的权威链是从
**赢家**那份重建的。若「赢了/输了」不看就写 `latest.json`，下一轮这台机器会拿**自己那条链**当
起点 ⇒ **静默分叉**（与 hub 的 B_n 越来越远，而日志一切正常）。所以：

* 产物落盘时只写 `pending.json`（本机事实：这份 job 跑完了）；
* 回传**受理**（`post_result` 返回 200，或 409 = 别人先赢）才 `confirm()` 升成 `latest.json`；
* 回传**被拒/输掉竞速**（403 lease-mismatch / 4xx）⇒ `invalidate()` 清掉 pending，
  并把「若 latest 正是这份 job」也清掉（那份链已经不该当起点了）。

身份映射是**状态**，不是缓存：它不进 `prune_caches` 的预算（`remote/download.py` 的
`_CACHE_SUBDIRS` 不含它），也不许被 `prune_job_dirs` 当旧 job 目录删掉（见那里的豁免名单）——
但它**自己**有界：每课程只留最近一份（覆盖写）。它引用的那两份 blob 在 `blob_cache` 里，
被预算挤掉时本模块的自校（sha+size）会当场发现并回退，**不会**用坏字节。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

#: `course_cache` 的目录名（`prune_job_dirs` 的豁免名单里必须是这个字面量）。
CACHE_SUBDIR = "course_cache"
#: 已确认可用的那一份（**唯一**的命中判据）。
LATEST_NAME = "latest.json"
#: 本机跑完但还没等到回传受理的那一份。
PENDING_NAME = "pending.json"

__all__ = [
    "CACHE_SUBDIR",
    "LATEST_NAME",
    "PENDING_NAME",
    "cache_root_of",
    "confirm",
    "course_fp_of",
    "hit",
    "identity_dir",
    "invalidate",
    "read_latest",
    "record_pending",
    "settle",
]


def course_fp_of(job: dict | None) -> str:
    """本 job 的课程身份：claim 响应把它放在 **`manifest`** 里，顶层没有。

    ★ 2026-10-21（`plan/ppo-worker-wallclock.plan.md` §4.3 W1a）：`job_round` 的落定回调
    曾只读顶层 `job["course_fp"]`，而 `hub/schedule.py` 的 claim 响应顶层是
    `{job_id, course, status, lease_token, halt, epoch, manifest}` ⇒ 取到恒空串 ⇒
    `settle()` 第一句就早退 ⇒ `latest.json` 永不写 ⇒ **身份命中率恒 0**
    （`DECISIONS §2026-10-14-goalnn-wire-diet-enable-all` 预期的是「~5% 未命中」）。

    读取顺序写在这里（而不是调用点）是为了：判据可单测、只有一份真相、旧形态/测试
    把 `course_fp` 放在顶层时也照旧能用。
    """
    if not isinstance(job, dict):
        return ""
    top = str(job.get("course_fp") or "")
    if top:
        return top
    man = job.get("manifest")
    if isinstance(man, dict):
        return str(man.get("course_fp") or "")
    return ""


def cache_root_of(blob_root: Path | str) -> Path:
    """`blob_cache` 根 → 持久缓存根（两者同源，见 `download.CodeLanded`）。

    刻意由 `blob_root` 反推而不是新加一个「cache_root」形参：调用点全都已经有 `blob_root`
    （它是 `run_job`/`train_core` 的既有入参），多一个同名概念就多一处会漂的真相。
    """
    return Path(blob_root).parent


def identity_dir(cache_root: Path | str, course_fp: str) -> Path:
    """一门课的映射目录（`course_fp` 为空 ⇒ 抛，**不**退化成公共目录）。"""
    fp = str(course_fp or "").strip()
    if not fp:
        raise ValueError("course_cache: course_fp 为空——身份映射必须按课程分区")
    return Path(cache_root) / CACHE_SUBDIR / fp


def _write_json(path: Path, obj: dict) -> None:
    """紧凑 + 排序键 + 原子改名（同内容必同字节，半截文件不可能被读到）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    """读一份映射；缺失/坏字节/不是对象/缺 job_id 一律 None（**不**命中，不抛）。"""
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or not str(got.get("job_id") or ""):
        return None
    return got


def read_latest(cache_root: Path | str, course_fp: str) -> dict | None:
    """已确认的那份映射（没有/坏了 = None）。"""
    try:
        d = identity_dir(cache_root, course_fp)
    except ValueError:
        return None
    return _read_json(d / LATEST_NAME)


def record_pending(
    cache_root: Path | str,
    course_fp: str,
    *,
    job_id: str,
    it: int,
    run_id: str,
    weights: tuple[str, int],
    opt: tuple[str, int],
    log=lambda _m: None,
) -> None:
    """产物落盘后记下本机事实 `(job_id → blob_cache 里的两份 f32 全量)`。

    `weights`/`opt` = `(sha256, 字节数)`——两个 sha 就是 `blob_cache/<sha>` 的键。写失败只记一行
    （映射是**优化**，不是正确性依赖；丢了它只是下一轮回退到内容寻址）。
    """
    try:
        _write_json(
            identity_dir(cache_root, course_fp) / PENDING_NAME,
            {
                "job_id": str(job_id),
                "it": int(it),
                "run_id": str(run_id or ""),
                "course_fp": str(course_fp),
                "weights_sha": str(weights[0]),
                "weights_bytes": int(weights[1]),
                "opt_sha": str(opt[0]),
                "opt_bytes": int(opt[1]),
                "updated_at": time.time(),
            },
        )
    except (OSError, ValueError) as e:
        log(f"course_cache: pending 没写成（{type(e).__name__}: {e}）——只丢优化，不改本轮")


def confirm(
    cache_root: Path | str, course_fp: str, job_id: str, *, log=lambda _m: None
) -> bool:
    """回传**受理后**把 pending 升成 latest（返回是否升了）。

    只认「pending 就是这份 job」——不然会把别人那轮的 pending 当成自己的起点。
    """
    try:
        d = identity_dir(cache_root, course_fp)
    except ValueError:
        return False
    pend = _read_json(d / PENDING_NAME)
    if pend is None or str(pend.get("job_id")) != str(job_id):
        log(f"course_cache: 没有本 job({job_id}) 的 pending——不升 latest（换机/重领/已作废）")
        return False
    try:
        _write_json(d / LATEST_NAME, pend)
    except (OSError, ValueError) as e:
        log(f"course_cache: latest 没写成（{type(e).__name__}: {e}）——只丢优化，不改本轮")
        return False
    return True


def invalidate(
    cache_root: Path | str, course_fp: str, job_id: str, *, log=lambda _m: None
) -> None:
    """回传被拒/输了竞速 ⇒ 作废这份身份（本机那条链**不是** hub 权威链的延续点）。

    pending 与 latest 都按 job_id 严格对齐才动：那一轮可能是别人赢的，而 latest 若指向
    别的 job（更早那份）也不该被这一刻误伤。
    """
    try:
        d = identity_dir(cache_root, course_fp)
    except ValueError:
        return
    for name in (PENDING_NAME, LATEST_NAME):
        got = _read_json(d / name)
        if got is None or str(got.get("job_id")) != str(job_id):
            continue
        try:
            (d / name).unlink(missing_ok=True)
            log(f"course_cache: 作废 {name}（job {job_id} 不是本机赢下的那份）")
        except OSError as e:
            log(f"course_cache: 作废 {name} 失败（{type(e).__name__}: {e}）")


def settle(
    cache_root: Path | str,
    course_fp: str,
    job_id: str,
    *,
    status: int | None,
    log=lambda _m: None,
) -> str:
    """回传**落定**后的处置（策略单点：`job_round` 的 settled 回调只转述这里的结论）。

    * `200`（本份被 hub 收下）⇒ `confirm`（pending → latest）；
    * `403`/`409`（租约不符 / 别人先赢；409 分不清是自己重试还是别人赢，按最严处置）
      ⇒ `invalidate`（本机那条链**不是**权威链的延续点）；
    * 其余（含 `None` = 回传没送达）⇒ 不动：`pending` 留着无害（命中判据是 `latest`），
      当下作废它反而有风险（备份/半离线尾巴可能稍后把这份送上去）。

    返回做过的动作（`confirm` / `invalidate` / `none`）——调用点日志与测试的读数。
    """
    if not str(course_fp or "") or not str(job_id or ""):
        return "none"
    code = int(status or 0)
    if code == 200:
        confirm(cache_root, course_fp, job_id, log=log)
        return "confirm"
    if code in (403, 409):
        invalidate(cache_root, course_fp, job_id, log=log)
        return "invalidate"
    return "none"


def _blob(blob_root: Path | str, sha: str, nbytes: int) -> bytes | None:
    """`blob_cache/<sha>` 的自校读：存在 + 字节数 + sha256 全对才返回（否则 None）。"""
    s = str(sha or "")
    if not s:
        return None
    p = Path(blob_root) / s
    try:
        if int(nbytes) > 0 and p.stat().st_size != int(nbytes):
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != s:
        return None  # 键是 sha、内容却不是它 ⇒ 坏件（与 `_cache_blob` 的修法同判据）
    # ★ 2026-10-21（plan/ppo-worker-wallclock §4.3 ⑦）：读命中也要 touch mtime。
    #   `prune_caches` 按 mtime 丢最旧（`download.py:prune_caches`），而身份链依赖的这两份
    #   副本**只在这里被读**——命中时根本不走 `_cache_blob`（那条路径命中才 touch）。
    #   不 touch ⇒ 它们的「年龄」永远停在生产那一刻 ⇒ 多 worker 共享缓存 / 长断点续跑 /
    #   code_cache 大进大出时，热件会按「谁先来谁先死」被挤掉（不是 LRU）。
    try:
        os.utime(p, None)
    except OSError:
        pass
    return raw


def hit(
    *,
    cache_root: Path | str,
    blob_root: Path | str,
    course_fp: str,
    opt_from: str,
    expect_it: int | None = None,
    log=lambda _m: None,
) -> dict[str, bytes] | None:
    """身份命中 ⇒ `{"weights": bytes, "opt": bytes}`；任何一条不成立 ⇒ None（调用方回退）。

    三条判据全过才算命中（缺一条都是**静默用错起点**）：
      ① `latest.job_id == manifest.opt_from`（身份，不是字节）+ `latest.it == expect_it`
         （给了就比：hub 的 `opt_from` 指向 it−1 的那份，数值与前缀两向对账）；
      ② 两份本机副本都还在 `blob_cache` 里且 sha+size 自校通过（被 prune / 换机 ⇒ 自动落回）；
      ③ `course_fp` 分区一致（`latest.json` 住 `<course_fp>` 目录里，天然满足；这里再对一次
         防止有人手工搬目录）。
    """
    if not str(opt_from or ""):
        return None
    try:
        d = identity_dir(cache_root, course_fp)
    except ValueError:
        return None
    got = _read_json(d / LATEST_NAME)
    if got is None or str(got.get("job_id")) != str(opt_from):
        return None
    if str(got.get("course_fp") or course_fp) != str(course_fp):
        return None
    if expect_it is not None and int(got.get("it") or -1) != int(expect_it):
        log(
            f"course_cache: job {opt_from} 命中但 it={int(got.get('it') or -1)} != 期望 {expect_it}"
            "——回退内容寻址（宁可多下字节，不可错接一条链）"
        )
        return None
    weights = _blob(blob_root, str(got.get("weights_sha") or ""), int(got.get("weights_bytes") or 0))
    opt = _blob(blob_root, str(got.get("opt_sha") or ""), int(got.get("opt_bytes") or 0))
    if weights is None or opt is None:
        log(
            f"course_cache: 身份命中 job {opt_from} 但本机副本自校不过"
            "（被 prune / 换机 / 坏件）——回退内容寻址"
        )
        return None
    return {"weights": weights, "opt": opt}
