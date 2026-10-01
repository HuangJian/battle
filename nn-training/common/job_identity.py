"""common/job_identity —— 一个 job 的**身份**怎么算、怎么被判重（S5 第七刀，2026-09-27）。

从 `common/protocol.py` 整块搬出（**逐字节不动**）。这里是**身份**（不是语义校验、不是编解码）：

* `idempotency_key` —— D1 幂等键 = `(runId, course_fp, it, init_weights_fp, data_fp)`。
  `course_fp` 是 2026-09-24 加的（`plan/job-identity-collision.plan.md`）：原来的四分量在
  **单进程多课程**（`--serve` 共享 trainer）下会**跨课程全同** ⇒ 两门课发布出同一个 job_id，
  hub 的 per-job 路由取「第一个匹配」⇒ 两个 trainer 读到同一份结果（静默污染）；
* `job_id` —— `sha256(幂等键)[:16]`，同键永远同 id（hub kill -9 后重发布不产生重复 job）；
* `collision_rows` —— 发布端守卫的**唯一判据**：这份身份是否已被**别的课程**占用；判据 =
  「**完整幂等键**相同 且 落在**别的 store**」（不是「四分量相同且 course_fp 不同」——后者
  正是事故配置，修复后已合法）。配 `SIBLING_MANIFEST_GLOB`（扫描面的单一定义）。

**为什么单独成家**：身份是「两个 store 会不会抢同一份活」这件事的唯一答案，与「manifest 字段
合不合法」「字节怎么编解码」是三个不同的问题；`collision_rows` 是**发布那一刻**的哨兵，谁改
发布链都要读它，但它**不需要**协议校验面。依赖面 = stdlib（`hashlib` / `json` / `pathlib`）
⇒ 零反向依赖、零环。

`common/protocol.py` 保留 `X as X` 门面 ⇒ 历史 `from common.protocol import …` 一行不改。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# ------------------------------------------------------------------ idempotency


def idempotency_key(manifest: dict) -> tuple:
    """D1 幂等键 = (runId, **course_fp**, it, init_weights_fp, data_fp)。云 worker 崩溃
    重拉同一 job 时按此去重；hub 账本记 job 状态，不重复发包已完成 job。

    ★ `course_fp` 是 2026-09-24 加的（plan/job-identity-collision.plan.md）：原来的四个
    分量在**单进程多课程**（`--serve` 共享 trainer）下会**跨课程全同** —— runId 是进程级
    （`trainer/queue.py::RUN_ID`）、`init_weights_fp` 同 warm-start、`data_fp` 只哈希
    (shard 目录名, wver, stage, seed) 而 per-stage seed 与课程无关、`it` 同轮 ⇒ 两门课
    发布出**同一个 job_id**，hub 的 per-job 路由取「第一个匹配」⇒ 两个 trainer 读到同一份
    结果，各自落进自己的 `args.out`（静默污染，且让下一轮 `init_weights_fp` 继续相同 ⇒ 自持）。

    为什么用 `course_fp` 而不是课程名：它是 manifest 必填字段、语义就是「课程身份」，
    且在**同一进程内恒定**（课程字节装载时冻结：`biz/config.py::course_from_args` 落
    `args.course_frozen_bytes`）⇒ mid-run 热加载编辑不会换 job id、不产生孤儿。
    ⚠ 它是**文件血缘**哈希，不是语料身份（那是 `corpus_fp`），也不等于 hub 的课程键
    （`<discover-root>/<目录名>`，= 课程文件 stem）——本键只用来分开身份，不用来路由。
    """
    return (
        manifest["runId"],
        manifest["course_fp"],
        manifest["it"],
        manifest["init_weights_fp"],
        manifest["data_fp"],
    )


def job_id(manifest: dict) -> str:
    """job_id 派生：sha256(幂等键)[:16]——同一 job（幂等键相同）永远同一 job_id，
    天然幂等（hub kill -9 重启后重发布不产生重复 job）。"""
    h = hashlib.sha256()
    for part in idempotency_key(manifest):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


#: 兄弟课程的 job 目录相对 job_root 的形状（发布端守卫的扫描面）：
#: `<job_root>/../*/remote-jobs/*/manifest.json`。抽成常量是为了让「扫描面」只有一处定义。
SIBLING_MANIFEST_GLOB = "*/remote-jobs/*/manifest.json"


def collision_rows(job_root: str | Path, manifest: dict) -> list[dict]:
    """发布端守卫的**唯一判据**：这份 job 的身份是否已被**别的课程**占用。

    返回命中的行 `[{course, job_id, manifest_path}, …]`（空 = 放行）。

    为什么需要它（2026-09-24 事故，plan/job-identity-collision.plan.md）：同一个 job 身份
    落在两个 store 时，hub 的 per-job 路由无法区分（`course_of` 只能拒答），两个 trainer
    会读到同一份结果 ⇒ **静默污染**。`idempotency_key` 进了 `course_fp` 之后正常发布已经
    撞不出来，所以这道守卫是**哨兵**：手写 manifest、回灌历史 job、跨 hub 搬目录、回滚代码
    再前进等旁路一旦制造出同身份，必须在**发布那一刻**响亮拒发，而不是等污染被看出来。

    ★ 判据 = 「**完整幂等键**相同 且 落在**别的 store**」——**不是**「四分量相同且 course_fp
    不同」：后者正是本事故的配置，而它在 `course_fp` 进键之后是**合法**的（两门课各有各的
    id），拿它当判据会把已经修好的场景全部拒掉。

    成本：一次 glob（兄弟课程数 × 每课 `remote-jobs/` 现存 job 目录数）+ **至多 1 次**
    manifest 读取（见下方快速闸）。扫描根不存在（节点侧/自定义 `job_root` 布局）⇒ 返回空：
    它是 best-effort 哨兵，扫不到就放过，绝不误伤。

    ⚠ 快速闸的前提是「**目录名 == 该 manifest 的 `job_id`**」——这是**发布端的写入不变量**
    （`publish_job` 先算 id、再以它为目录名），也是 `course_of` 认归属用的同一条。手写 manifest
    若把某个键写进**别的**目录名，它连路由都对不上（`wait_job(<该键的 id>)` 会 404），
    不构成本守卫要防的「两个 store 抢同一身份」。
    """
    root = Path(job_root)
    own_course = root.parent.name  # `<root>/<课程>/remote-jobs` ⇒ 本课目录名
    try:
        want = idempotency_key(manifest)
    except KeyError:
        return []  # 本份 manifest 自己就不完整（调用方另有校验）——守卫不越权报错
    want_jid = job_id(manifest)
    scan_root = root.parent.parent
    hits: list[dict] = []
    for mp in sorted(scan_root.glob(SIBLING_MANIFEST_GLOB)):
        # ★ 快速闸（E0 实测驱动）：job 身份 = `job_id`，而 **job 目录名就是 job_id**
        # （`course_of` 的归属证据用的是同一条不变量）⇒ 只有**同名目录**才可能是冲突。
        # 没有这一闸，每次发布要把兄弟课程的全部 manifest 都 `json.loads` 一遍：
        # 真语料 82 份（含内联 opt_init/course 全文）实测 **399ms/次**；加上它 = 一次
        # glob + 至多 1 次读取。
        if mp.parent.name != want_jid:
            continue
        # 相对扫描根取第一段 = 课程目录名（不靠数 `parents`，免得扫描面一变就错位）
        course = mp.relative_to(scan_root).parts[0]
        if course == own_course:
            continue  # 本课自己的历史 job（含重发布的那一份）——不算冲突
        try:
            other = json.loads(mp.read_text(encoding="utf-8"))
            if not isinstance(other, dict):
                continue
            same = idempotency_key(other) == want
        except (OSError, ValueError, KeyError):
            # 缺键（更旧/手写的 manifest）或读不动 ⇒ 跳过这一行：守卫不把「读不懂」
            # 升级成「拒发」，否则一次历史残留就能挡住整条腿。
            continue
        if same:
            hits.append(
                {
                    "course": course,
                    "job_id": str(other.get("job_id") or mp.parent.name),
                    "manifest_path": str(mp),
                }
            )
    return hits
