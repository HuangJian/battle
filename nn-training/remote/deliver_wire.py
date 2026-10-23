"""remote/deliver_wire.py — 补传**实时体**的投影（2026-10-22，`plan/offline-deliver-stall` §9.6）。

五件事，全部是无状态纯函数（一次 `stat` + 一次读文件是唯一 IO）：

  * `EVAL_ROW_WIRE_KEYS` / `slim_eval_rows` —— 逐局评估行的**白名单投影**（评审 P0-5）；
  * `wire_row` —— 账本行摘掉 `perGame`/`perGameSecs`（Q2 的答案；**补传体不摘**，见 `keep_per_game`）；
  * `pick_anchors` / `read_eval_iters` —— **锚点判据**：`eval_log.jsonl` 里有读数的那几轮（`pending_anchors` 的输入）；
  * `is_full_round` —— **写盘侧**的锚点判据（`plan/anchor-full-rounds` §3）：首轮 ∪ `it % eval_every == 0`；
  * `note_opt_wire` —— 补传腿的**形态读数**累计（S4：`opt_form` / 实际省下的线上字节）。

为什么单独成文件（不是「为拆而拆」）：`tests/test_python_loc_budget.py` 把
`remote/offline_deliver.py` 顶在 1000 行（2026-10-21 刚从 1074 搬到 965，本轮 +100 又超），
而这三块的依赖面是**零**（stdlib + 一个 `Path`）——把它们留在腿本体的唯一后果是下次
加一行就要再搬一次。与 `remote/deliver_ledger` 同一条纪律：**纯数据/判据**下沉，腿本体只留
「什么时候调、怎么记日志」。

⚠ 本模块**不得** import `remote.*` 或 `common.*`：它要能被单独测、也要在任何一层下面
（拓扑秩 L0，见 `tests/helpers/remote_dag.py`）。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 实时体里**逐局评估行**保留的键（白名单；评审 P0-5 / Q2）。
#:
#: 为什么是白名单而不是「去掉几个大键」：实时体的读者只有两类——① hub 的合并去重
#: （`worker.eval_rows.eval_row_key`，逐字要 `event/iter/wver/stage/seed`）；② 控制台的
#: 逐局表与趋势列（`dashboard/src/server/iters.ts` 的 `EvalGameRow` 与 `readEvalSummaries`
#: 聚合）。剩下的（metrics v8–v13 各族、分敌种画像、`moveHist/decisions/stopRuns/idleTicks`）
#: 是**分析面**，不是实时面：它们逐字留在 `eval_log.jsonl` 里，而那份文件由抽屉与离线包
#: 整份带走（`remote/backup_drawer` / `remote/deliver_zip`）——少走一次 1.24MB 的实时体，
#: 不等于那份读数消失。
#: ⚠ 加分析列时**不要**往这里塞：先问「控制台这一屏要不要在包到达之前看见它」。
EVAL_ROW_WIRE_KEYS = (
    # 去重键 + 归属（hub `merge_eval_rows` 的判据，少一个就会被并成一行/落哨兵键）
    "event",
    "iter",
    "wver",
    "stage",
    "seed",
    # 控制台逐局表（`iters.ts::readEvalGames`）与残血推算（`residualHpFromFields`）
    "time",
    "node",
    "outcome",
    "win",
    "cleared",
    "ticks",
    "score",
    "quality",
    "kills",
    "playerDamageTaken",
    "playerDeaths",
    "puGotTank",
    "powerUpsCollected",
    # `iters.ts:237` 的 spawn 反推来源（`dims.loot`）
    "dims",
)

#: 账本行里**不走实时体**的逐局明细（Q2）：它们只随离线包/抽屉到达。
#: 为什么摘它：`perGame` 是行里唯一按局数线性长的字段（34 局 ≈ 45KB），而 hub 落它的唯一
#: 去处是 `<课程>/it<N>/per-game.json` —— 那个文件本来就是「随包到达」的读数面。
#: 代价（明写）：包到之前，控制台该轮的逐局弹窗是空的；聚合列不受影响。
PER_GAME_WIRE_KEYS = ("perGame", "perGameSecs")

#: `eval_log.jsonl` 里算「这一轮有读数」的两种事件（`source` 行是 B/C evalboard 的，不算）。
EVAL_EVENTS = ("eval", "eval_summary")


def slim_eval_row(row: dict) -> dict:
    """一行逐局评估读数 → **实时体投影**（白名单 `EVAL_ROW_WIRE_KEYS`，见那里的为什么）。"""
    return {k: row[k] for k in EVAL_ROW_WIRE_KEYS if k in row}


def slim_eval_rows(rows: list[dict]) -> tuple[list[dict], int, int]:
    """批量投影，返回 `(投影后的行, 投影前字节, 投影后字节)`。

    为什么要报两个字节数：只改形状不报数字的话，读日志的人分不清「今天体小是因为
    没评估」还是「因为白名单生效了」。
    """
    if not rows:
        return rows, 0, 0
    before = len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    out = [slim_eval_row(r) for r in rows]
    after = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return out, before, after


def wire_row(row: dict | None, *, keep_per_game: bool = False) -> tuple[dict | None, int]:
    """实时体里的账本行：摘掉 `perGame`/`perGameSecs`，返回 `(行, 被摘掉的局数)`。

    浅拷（不就地 `pop`）：产物目录里的 `metrics.jsonl` 才是交付面，它一个字节也不能变。

    `keep_per_game=True` = **补传体**（`/offline/artifact`）的调用面（`plan/anchor-full-rounds` S6）：
    那条腿是云机逐局明细的**唯一**实时通道 —— 权重体送出过的轮次会被记进 `_metrics_sent`
    （`offline_deliver` 的 `_sync`），于是它们**不会**再走统计批，摘掉这一份就是永久丢。
    默认 False = 旧行为（在线实时体若将来恢复，仍按 Q2 摘）。
    """
    if keep_per_game:
        return row, 0
    if not isinstance(row, dict) or not any(k in row for k in PER_GAME_WIRE_KEYS):
        return row, 0
    out = {k: v for k, v in row.items() if k not in PER_GAME_WIRE_KEYS}
    pg = row.get("perGame")
    return out, (len(pg) if isinstance(pg, list) else 0)


#: 形态计数的初始值：`(上一次发出去的形态, 本会话累计省下的线上字节, 最近一份整份的线上字节)`。
#: 三个数一起走，因为第三个是前两个的参照值 —— 分开存就会出现「省字节用了一个参照、读数
#: 用了另一个」的分叉可能。
OPT_WIRE_ZERO: tuple[str, int, int] = ("none", 0, 0)


def note_opt_wire(
    state: tuple[str, int, int], form: str, wire_bytes: int
) -> tuple[str, int, int]:
    """一次投递后的形态累计（`plan/anchor-full-rounds` S4）——纯函数，单元可测。

    `wire_bytes` = 这一轮 opt **实际发出去的线上字节**（`len(body["opt_tar_b64"])`，不是估算）：
    发了多少只有一个权威读数，就是这次 POST 的体。

    * `full` ⇒ 更新参照值（整份就是那个“本来要花多少”的尺子），本轮省零；
    * `delta` ⇒ 省 `参照 − 本轮`；**参照没见过**（本会话没发过整份）⇒ 不计（宁少不多：一个
      编出来的基数会把“省了多少”变成假读数）。
    """
    before_form, saved, full = state
    if not wire_bytes:
        return str(form or before_form), saved, full
    if form == "full":
        return "full", saved, max(int(full), int(wire_bytes))
    if form == "delta" and full:
        return "delta", saved + max(0, int(full) - int(wire_bytes)), full
    return str(form or before_form), saved, full


def is_full_round(it: int, *, first_it: int, eval_every: int) -> bool:
    """这一轮**写盘时**要不要整份（`plan/anchor-full-rounds` §3，单一判据）。

    锚点轮 = 首轮（建基准那一轮）∪ `it % eval_every == 0`（计划判据）。与 `pick_anchors` 的
    `every` 兜底分支**逐字同一条**，但**不是**它的读数分支：写盘那一刻 `eval_log.jsonl` 里
    还没有这一轮的读数（评估在这轮落盘之后才跑）。两处不一致时的方向是安全的 ——
    **多写整份无害，少写整份才会毁掉「eval 轮可单独装载」**。

    `eval_every <= 0` ⇒ 只有首轮是整份（调用方应在 `eval_every` 缺失时**根本不装**这个判据，
    否则退化成「首轮之后全是 delta、一个落点都没有」）。
    """
    it, first, step = int(it), int(first_it), int(eval_every)
    return it == first or (step > 0 and it % step == 0)


def pick_anchors(
    pending: list[int],
    eval_iters: set[int],
    *,
    with_newest: bool = False,
    every: int = 0,
) -> list[int]:
    """`pending` 里**这一拍该发**的锚点轮（`pending_anchors` 的全部逻辑，纯函数便于单测）。

    锚点 = 有 A 层评估读数的轮次（间隔 = `eval_every`，plan D-2，不加新旋钮）；
    `with_newest`（段末/收线）额外带**最新那一轮**（D-1 末轮强制补发），否则最后一段
    （距上一个锚点不足 `eval_every`）的进度永远差几轮，续跑要多跑一遍。

    没有 A 层评估的腿（`eval_log.jsonl` 缺失/为空）：
      * `every > 0` ⇒ **按 `it % every == 0` 兜底**（plan D-2 的原口径，2026-10-23 补上），
        调用方（`pending_anchors`）负责打那一行 WARN；
      * `every <= 0` ⇒ 退回全量（**旧行为**，只留给不传 `every` 的历史调用点）：没有锚点时
        「只发锚点」等价于「一轮也不发」。

    ⚠ 这条兜底**只服务 `anchors` 档**（`plan/offline-deliver-full-and-gaps` §3.5）：`full` / `bulk`
    档取 `pending()` 全量，根本不走这个函数。
    """
    if not pending:
        return []
    if not eval_iters:
        step = int(every or 0)
        if step <= 0:
            return list(pending)
        out = [it for it in pending if it % step == 0]
        if with_newest and pending[-1] not in out:
            out.append(pending[-1])
        return out
    out = [it for it in pending if it in eval_iters]
    if with_newest and pending[-1] not in out:
        out.append(pending[-1])
    return out


def read_eval_iters(
    path: Path, cache: tuple[tuple[int, int] | None, set[int]] = (None, set())
) -> tuple[tuple[int, int] | None, set[int]]:
    """`(失效键, 有这一轮评估读数的 it 集合)`；`cache` 命中时零解析。

    判据与 `OfflineDeliverer._eval_rows_for` **逐字同源**：`event in EVAL_EVENTS` 且没有
    `source`。两处都从同一份文件取，所以「锚点说这一轮有读数」与「体里真带了读数」
    不会各说一套。缓存按 `(size, mtime_ns)` 失效——该文件是 append-only，对不上就重解析，
    永远不会读到旧集合。读不到 ⇒ 空集（下游按「没有锚点」处理）。
    """
    try:
        st = path.stat()
        key: tuple[int, int] | None = (int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return None, set()
    cached_key, cached = cache
    if cached_key == key:
        return key, cached
    its: set[int] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue  # 半截行（被 kill）——与 `_eval_rows_for` 同口径地跳过
        if not isinstance(r, dict) or "source" in r:
            continue
        if r.get("event") not in EVAL_EVENTS:
            continue
        it = r.get("iter")
        if isinstance(it, int) and not isinstance(it, bool):
            its.add(it)
    return key, its
