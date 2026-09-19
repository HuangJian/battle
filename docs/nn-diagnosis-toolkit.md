# NN 诊断工具集（源码快照）

> **为什么有这份文件**：`docs/nn-diagnosis-methodology.md` 里引用的脚本原本都写在 `tmp/` 下，
> 而 `tmp/` 随时会被清理 ⇒ 那些引用会变成死链。本文件把脚本的**完整源码**固化下来，
> 与方法论文档一起存活。
>
> **来源**：2026-09-19 对 x20 线（20 敌 1 命）「均杀卡在 ~7」的一次完整排查。
> **状态**：均为当时**可用**的版本（逐帧数据的 `tickHashes` 自检全部通过）；
> ⚠ **python 分析脚本里的路径是硬编码的**（`D:\github\battle2\tmp\...`），复用时改顶部常量。
>
> 方法论（原则 / 流程 / 陷阱 / 负结果清单）：见 **`docs/nn-diagnosis-methodology.md`**。

---

## 0. 项目自带、**不要**复制这里的副本

| 文件 | 作用 | 注意 |
|---|---|---|
| `tools/sim/export-eval-game.ts` | 保真单局执行 + `--replay` 导出 | **在 `codehash-files.txt` 集内**，改动会让整个节点集群 stale |
| `tools/replay/verify-replay.ts` | 重放的权威写法 | 核心在 **第 93–145 行**，重放一律照抄它 |
| `src/replay/{file,ReplayInput}.ts`、`src/snapshot/WorldSerializer.ts` | replay 解析 / 输入源 / 世界恢复 | — |

## 1. 本文 §2 固化的工具

> 本文是**快照**：后续要改脚本，直接编辑本文的代码块即可（不必回头找 `tmp/`）。
> 文中出现的 `tmp/xxx` 是**运行时要放的位置**，不是源码存放处。

| # | 文件 | 作用 |
|---|---|---|
| 1 | `replay-dump.ts` | **核心**：重放 replay → 逐帧 CSV + `tickHashes` 自检（没有它其它分析都不可信） |
| 2 | `pick-10x2.py` | 从已有 eval 数据里挑 10 低分 + 10 高分样本 seed |
| 3 | `frame-compare-batch.py` | 10×2 逐帧聚合（移动率 / 活动范围 / 被命中 / 道具 / 停滞） |
| 4 | `slice-analyze.py` | 前 200 tick vs 全段（朝敌移动率 / 瞄准率 / 开火率） |
| 5 | `stall-detect.py` | 100-tick 窗口的「活动扩张停滞点」 |
| 6 | `win23-compare.py` | 指定窗口的态势对比（距离 / 同屏敌数 / 敌弹数 / 开火） |
| 7 | `dodge-quality.py` | 危险 tick 下的移动方向分类（垂直闪避 / 侧拉 / 顺弹道 / 未移动） |
| 8 | `nn-vs-god.py` | **同 seed 换策略配对**（替代"结果分组"，消幸存者偏差） |
| 9 | `cleanlog.py` | 日志净化（非 ASCII 降为 `.`，保留换行）—— 文件被 Read 判成 binary 时用 |
| 10 | `fixlog.py` | 日志中文反向还原（`*>` 重定向把 UTF-8 按 cp936 转码后的复原） |
| 11 | `trace-c20.ts` | ❌ **反面样本：不要用**（自建 NNInput 探针与 eval 差 4 倍，见方法论文档 §2 Step 1） |

**运行前置**（两条，缺一条就跑不起来）：

```bash
# ① 自定义关卡的 stageJson = 关卡文件 stages[i] 的原样 JSON（单行、去尾逗号）
python -c "import json,re;s=open('nn-training/levels/ladder-c20-lives1.jsonc',encoding='utf-8').read();d=json.loads(re.sub(r',(\s*[}\]])',r'\1',s));open('tmp/stage0.json','w',encoding='utf-8').write(json.dumps(d['stages'][0],ensure_ascii=False,separators=(',',':')))"

# ② PowerShell 里跑 bun 工具前，必须把 PortableGit 的 usr\bin 前置到 PATH
#    （否则内部 spawn bash 命中 WindowsApps 存根 ⇒ Access is denied / E_ACCESSDENIED）
$env:PATH = "C:\Users\ustch\.workbuddy\binaries\PortableGit\versions\1.2.0\usr\bin;C:\Users\ustch\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin;" + $env:PATH
```

典型全流程：

```powershell
# 1) 保真执行 + 导出 replay（每局 ~2s）
bun tools/sim/export-eval-game.ts --stage-json $sj --stage 0 --seed 415020 `
  --difficulty hard --weights <w.json> --policy nn --lives-override 1 --player-level 0 `
  --out tmp/eg-low-1 --replay tmp/rp-low-1 --node-label local

# 2) 重放 + 自检 + 逐帧 dump（hash 必须全对）
bun tmp/replay-dump.ts --replay tmp/rp-low-1/<file>.replay --out tmp/dump-low-1.csv

# 3) 分析
python tmp/frame-compare-batch.py    # 或 slice-analyze / stall-detect / win23-compare / dodge-quality
```

---

## 2. 源码

### replay-dump.ts —— 重放 + 逐帧 dump + hash 自检（核心）

```ts
#!/usr/bin/env bun
/**
 * replay-dump.ts — 重放 replay 并逐帧 dump 状态。
 *
 * 重放方式**逐字照抄** tools/replay/verify-replay.ts:93-145（权威实现）：
 *   new World → rng.reseed(replay.seed) → difficulty/rules → loadStageData →
 *   restoreWorld(initialSnapshot) → ReplayInput(frames) → sim.input(+input2) →
 *   state='playing' → while(!input.isFinished){ sim.tick(); input.advance(); tick++;
 *   consumeEvents(); hash@tick%interval==0 }
 *
 *   bun tmp/replay-dump.ts --replay <file.replay> --out tmp/dump.csv
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { restoreWorld } from '../src/snapshot/WorldSerializer'
import { ReplayInput } from '../src/replay/ReplayInput'
import { parseReplayFile } from '../src/replay/file'
import { worldTickHash } from '../src/replay/tickHash'

function arg(name: string, dflt?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : dflt
}

const replayPath = arg('replay', '')!
const outCsv = arg('out', 'tmp/dump.csv')!
const text = readFileSync(replayPath, 'utf8')
const parsed = parseReplayFile(text) as unknown as Record<string, any>
const replay = parsed.replay ?? parsed
if (!replay || !replay.initialSnapshot) {
  console.error('[replay-dump] parse failed; keys=', Object.keys(parsed))
  process.exit(2)
}
const meta = replay.metadata ?? {}

const world = new World()
world.rng.reseed(Number(replay.seed))
const dkey = String(meta.difficulty || 'hard')
world.difficultyKey = dkey
world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
world.rules = RULES[dkey] ?? DEFAULT_RULES
const stageIdx = Number(meta.stage ?? 0)
world.loadStageData(STAGES[stageIdx] ?? STAGES[0], 0)

restoreWorld(world, replay.initialSnapshot)
const input = new ReplayInput(replay.frames)
const sim = new Simulation(world, input)
sim.input = input
sim.input2 = (input as unknown as { input2?: unknown }).input2 ?? null
world.state = 'playing'

const recordedHashes: string[] = replay.tickHashes ?? []
const interval = Number(replay.hashInterval ?? 100)
const total = Number(replay.totalTicks ?? 0)
const anyW = world as unknown as Record<string, any>

const rows: string[] = ['t,px,py,pdir,pmoving,hp,kills,nEnemiesE,nBulletsE,minDistE,dmgTick,nPU,nBulletsP,ex,ey,edir,ebxy,pbxy']
let prevHp = anyW.player?.hp ?? 0
let hashOk = 0
let hashBad = 0
let tick = 0

while ((input as unknown as { isFinished?: boolean }).isFinished !== true && tick < total + 10) {
  sim.tick()
  ;(input as unknown as { advance?: () => void }).advance?.()
  tick++
  anyW.consumeEvents?.()

  const p = anyW.player
  if (p && p.alive !== false) {
    const tanks = (anyW.tanks ?? []) as any[]
    const enemies = tanks.filter((x) => x && x.alive !== false && x.allegiance === 'enemy')
    const bullets = (anyW.bullets ?? []) as any[]
    const eb = bullets.filter((b) => b && b.allegiance === 'enemy').length
    const pb = bullets.filter((b) => b && b.allegiance === 'player').length
    let minD = 9999
    let nx = -1
    let ny = -1
    let ndir = '-'
    for (const e of enemies) {
      const d = Math.abs(e.x - p.x) + Math.abs(e.y - p.y)
      if (d < minD) {
        minD = d
        nx = Math.round(e.x)
        ny = Math.round(e.y)
        ndir = String(e.dir ?? '-')
      }
    }
    // 敌弹 / 我方弹坐标串（x:y|x:y，无逗号 ⇒ CSV 安全）
    const ebxy = bullets
      .filter((b) => b && b.allegiance === 'enemy')
      .map((b) => `${Math.round(b.x)}:${Math.round(b.y)}`)
      .join('|')
    const pbxy = bullets
      .filter((b) => b && b.allegiance === 'player')
      .map((b) => `${Math.round(b.x)}:${Math.round(b.y)}`)
      .join('|')
    const dmgTick = prevHp - p.hp
    rows.push(
      [
        tick,
        Math.round(p.x),
        Math.round(p.y),
        p.dir,
        p.moving ? 1 : 0,
        Math.round(p.hp * 100) / 100,
        anyW.killCount ?? -1,
        enemies.length,
        eb,
        minD === 9999 ? -1 : Math.round(minD),
        dmgTick > 0 ? Math.round(dmgTick * 100) / 100 : 0,
        (anyW.powerUps ?? []).length,
        pb,
        nx,
        ny,
        ndir,
        ebxy,
        pbxy,
      ].join(','),
    )
    prevHp = p.hp
  }

  if (tick % interval === 0 && recordedHashes.length) {
    const idx = tick / interval - 1
    if (idx < recordedHashes.length) {
      const computed = worldTickHash(world)
      if (computed === recordedHashes[idx]) hashOk++
      else hashBad++
    }
  }
}

writeFileSync(outCsv, rows.join('\n') + '\n', 'utf8')
console.log(
  `[replay-dump] ${outCsv} ticks=${tick} total=${total} hashOk=${hashOk} hashBad=${hashBad} ` +
    `kills=${anyW.killCount ?? -1} state=${anyW.state} hp=${anyW.player?.hp}`,
)
```

### pick-10x2.py —— 挑 10 低分 + 10 高分样本 seed

```python
# -*- coding: utf-8 -*-
"""从 it30 的 800 局里挑 10 LOW + 10 HIGH（stage 2000，固定关以保可比）。"""
import json

SRC = r"D:\github\battle2\tmp\x20-powered-mig-it30.jsonl"
rows = []
with open(SRC, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

rs = [r for r in rows if r.get("stageId") == 2000]
lows = sorted((r for r in rs if r.get("kills", 0) <= 1), key=lambda r: r["seed"])[:10]
highs = sorted((r for r in rs if r.get("kills", 0) >= 16), key=lambda r: -r["kills"])[:10]

open(r"D:\github\battle2\tmp\seeds-low.txt", "w").write("\n".join(str(r["seed"]) for r in lows) + "\n")
open(r"D:\github\battle2\tmp\seeds-high.txt", "w").write(
    "\n".join(str(r["seed"]) for r in highs) + "\n"
)
print("LOW :", [(r["seed"], r["kills"], r["ticks"]) for r in lows])
print("HIGH:", [(r["seed"], r["kills"], r["ticks"]) for r in highs])
```

### frame-compare-batch.py —— 10×2 逐帧聚合

```python
# -*- coding: utf-8 -*-
"""10×2 逐帧聚合对照：把"挨打时不动"从单局观察变成统计量。"""
import csv
import glob
import os
import statistics

OUT = r"D:\github\battle2\tmp\frame-compare-batch.txt"


def load(p):
    rows = []
    with open(p, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            rows.append({
                "t": int(d["t"]), "x": int(d["px"]), "y": int(d["py"]),
                "dir": d["pdir"], "mv": int(d["pmoving"]), "hp": float(d["hp"]),
                "kills": int(d["kills"]), "ne": int(d["nEnemiesE"]),
                "nb": int(d["nBulletsE"]), "md": int(d["minDistE"]),
                "dmg": float(d["dmgTick"]), "npu": int(d["nPU"]),
            })
    return rows


def metrics(rows):
    n = len(rows)
    md = [r["md"] for r in rows if r["md"] >= 0]
    hits = [i for i, r in enumerate(rows) if r["dmg"] > 0]
    # 每次被命中「之前 10 tick」的位移总量（像素）：0 ⇒ 完全静止挨打
    pre_move = []
    for i in hits:
        j = max(0, i - 10)
        seg = rows[j:i + 1]
        pre_move.append(sum(abs(seg[k]["x"] - seg[k - 1]["x"]) + abs(seg[k]["y"] - seg[k - 1]["y"])
                            for k in range(1, len(seg))))
    # 命中时刻的最近敌距离
    hit_md = [rows[i]["md"] for i in hits if rows[i]["md"] >= 0]
    # 击杀间隔
    marks = []
    last = rows[0]["kills"]
    for r in rows:
        if r["kills"] > last:
            marks.append(r["t"])
            last = r["kills"]
    gaps = [marks[i] - marks[i - 1] for i in range(1, len(marks))]
    return {
        "n": n,
        "kills": rows[-1]["kills"],
        "move%": sum(r["mv"] for r in rows) / n * 100,
        "cells": len({(r["x"] // 16, r["y"] // 16) for r in rows}),
        "turns": sum(1 for i in range(1, n) if rows[i]["dir"] != rows[i - 1]["dir"]),
        "hits": len(hits),
        "hit_per_kt": len(hits) / n * 1000,
        "pre_move_med": statistics.median(pre_move) if pre_move else -1,
        "pre_move_mean": statistics.fmean(pre_move) if pre_move else -1,
        "hit_md_med": statistics.median(hit_md) if hit_md else -1,
        "pu_on%": sum(1 for r in rows if r["npu"] > 0) / n * 100,
        "close%": sum(1 for v in md if v <= 48) / max(len(md), 1) * 100,
        "md_p50": statistics.median(md) if md else -1,
        "gap_med": statistics.median(gaps) if gaps else -1,
        "ne_mean": statistics.fmean(r["ne"] for r in rows),
    }


def collect(tag):
    out = []
    for p in sorted(glob.glob(rf"D:\github\battle2\tmp\dump-{tag}-*.csv")):
        rows = load(p)
        if len(rows) < 20:
            continue
        m = metrics(rows)
        m["file"] = os.path.basename(p)
        out.append(m)
    return out


L = []
def w(s=""):
    L.append(s)


low = collect("low")
high = collect("high")

w("=" * 118)
w("A. 每局指标")
w("=" * 118)
w(" %-6s %-18s %7s %6s %7s %6s %6s %7s %9s %10s %7s %8s %7s %7s"
  % ("档", "file", "ticks", "kills", "move%", "cells", "turns", "hits", "hit/kt",
     "命中前10t位移", "命中距", "pu在场%", "近身%", "距p50"))
for tag, rs in (("LOW", low), ("HIGH", high)):
    for m in rs:
        w(" %-6s %-18s %7d %6d %7.1f %6d %6d %7d %9.2f %10.1f %7.0f %8.1f %7.1f %7.0f"
          % (tag, m["file"], m["n"], m["kills"], m["move%"], m["cells"], m["turns"],
             m["hits"], m["hit_per_kt"], m["pre_move_med"], m["hit_md_med"],
             m["pu_on%"], m["close%"], m["md_p50"]))

w("")
w("=" * 118)
w("B. 汇总（mean ± SE，n=局数）")
w("=" * 118)
keys = ["n", "kills", "move%", "cells", "turns", "hits", "hit_per_kt", "pre_move_med",
        "pre_move_mean", "hit_md_med", "pu_on%", "close%", "md_p50", "gap_med", "ne_mean"]
w(" %-16s %22s %22s %10s" % ("指标", "LOW(kills=1)", "HIGH(kills=20)", "差"))
for k in keys:
    a = [m[k] for m in low if m[k] >= 0]
    b = [m[k] for m in high if m[k] >= 0]
    if not a or not b:
        continue
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = statistics.stdev(a) / len(a) ** 0.5 if len(a) > 1 else 0
    sb = statistics.stdev(b) / len(b) ** 0.5 if len(b) > 1 else 0
    w(" %-16s %14.2f ± %-5.2f %14.2f ± %-5.2f %10.2f" % (k, ma, sa, mb, sb, mb - ma))

w("")
w("=" * 118)
w("C. 判定：「挨打时不动」是不是系统性")
w("=" * 118)
for tag, rs in (("LOW", low), ("HIGH", high)):
    v = [m["pre_move_med"] for m in rs if m["pre_move_med"] >= 0]
    zero = sum(1 for x in v if x <= 8)
    w(" %-6s n=%d  命中前10t位移中位数: p50=%.1f  mean=%.1f  min=%.1f max=%.1f"
      % (tag, len(v), statistics.median(v), statistics.fmean(v), min(v), max(v)))
    w("        「近静止」（≤8px/10t）的局占比 = %d/%d = %.0f%%" % (zero, len(v), zero / max(len(v), 1) * 100))

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok", len(low), len(high))
```

### slice-analyze.py —— 前 200 tick vs 全段

```python
# -*- coding: utf-8 -*-
"""切片分析：LOW 是「没去找敌人」，还是「敌人不来 / 被堵住」？

判据（只看玩家自己的位移方向，不受敌人移动干扰）：
  moveToEnemy% = 玩家位移向量与「玩家→最近敌」方向夹角 <60° 的 tick 占比
  aim%         = 玩家朝向与「玩家→最近敌」方向一致的占比
  同时分区：前 200 tick（早期，死亡前段） vs 全段
"""
import csv
import glob
import os
import statistics

OUT = r"D:\github\battle2\tmp\slice-analyze.txt"
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def load(p):
    rows = []
    with open(p, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            rows.append({
                "t": int(d["t"]), "x": int(d["px"]), "y": int(d["py"]),
                "dir": d["pdir"], "mv": int(d["pmoving"]), "hp": float(d["hp"]),
                "kills": int(d["kills"]), "ne": int(d["nEnemiesE"]),
                "nb": int(d["nBulletsE"]), "md": int(d["minDistE"]),
                "dmg": float(d["dmgTick"]), "npu": int(d["nPU"]),
                "pb": int(d["nBulletsP"]), "ex": int(d["ex"]), "ey": int(d["ey"]),
            })
    return rows


def seg_metrics(rows):
    n = len(rows)
    if n < 5:
        return None
    to_enemy = 0
    away = 0
    aim_ok = 0
    aimable = 0
    fire_t = 0
    ene_present = 0
    dist_sum = 0
    for i in range(1, n):
        r, p0 = rows[i], rows[i - 1]
        if r["md"] < 0 or r["ex"] < 0:
            continue
        ene_present += 1
        dist_sum += r["md"]
        vx, vy = r["ex"] - r["x"], r["ey"] - r["y"]
        norm = (vx * vx + vy * vy) ** 0.5 or 1
        ux, uy = vx / norm, vy / norm
        mx, my = r["x"] - p0["x"], r["y"] - p0["y"]
        mlen = (mx * mx + my * my) ** 0.5
        if mlen > 0.5:
            dot = (mx * ux + my * uy) / mlen
            if dot > 0.5:
                to_enemy += 1
            elif dot < -0.5:
                away += 1
        d = DIRS.get(r["dir"])
        if d:
            aimable += 1
            if d[0] * ux + d[1] * uy > 0.7:
                aim_ok += 1
        if r["pb"] > 0:
            fire_t += 1
    return {
        "n": n,
        "ene_present%": ene_present / (n - 1) * 100,
        "moveToEnemy%": to_enemy / max(ene_present, 1) * 100,
        "moveAway%": away / max(ene_present, 1) * 100,
        "aim%": aim_ok / max(aimable, 1) * 100,
        "fire%": fire_t / (n - 1) * 100,
        "cells": len({(r["x"] // 16, r["y"] // 16) for r in rows}),
        "md_mean": dist_sum / max(ene_present, 1),
        "kills": rows[-1]["kills"],
    }


L = []
def w(s=""):
    L.append(s)


def collect(tag):
    per = []
    for p in sorted(glob.glob(rf"D:\github\battle2\tmp\dump-{tag}-*.csv")):
        rows = load(p)
        per.append((os.path.basename(p), rows))
    return per


low = collect("low")
high = collect("high")

for label, per in (("LOW", low), ("HIGH", high)):
    w("=" * 122)
    w("%s —— 前 200 tick / 全段" % label)
    w("=" * 122)
    w(" %-16s %-9s %6s %9s %12s %10s %8s %8s %7s %8s"
      % ("file", "区段", "n", "遇敌%", "moveToEnemy%", "moveAway%", "aim%", "fire%", "cells", "距均"))
    for name, rows in per:
        for seg, tag in ((rows[:200], "head200"), (rows, "full")):
            m = seg_metrics(seg)
            if not m:
                continue
            w(" %-16s %-9s %6d %8.1f%% %11.1f%% %9.1f%% %7.1f%% %7.1f%% %7d %8.0f"
              % (name, tag, m["n"], m["ene_present%"], m["moveToEnemy%"], m["moveAway%"],
                 m["aim%"], m["fire%"], m["cells"], m["md_mean"]))
    w("")

w("=" * 122)
w("汇总（mean ± SE）")
w("=" * 122)
for seg_tag, sl in (("head200", slice(0, 200)), ("full", None)):
    w(" -- 区段 %s" % seg_tag)
    w(" %-16s %22s %22s %10s" % ("指标", "LOW", "HIGH", "差"))
    keys = ["ene_present%", "moveToEnemy%", "moveAway%", "aim%", "fire%", "cells", "md_mean"]
    acc = {"LOW": [], "HIGH": []}
    for label, per in (("LOW", low), ("HIGH", high)):
        ms = [seg_metrics(rows[sl] if sl else rows) for _n, rows in per]
        acc[label] = [m for m in ms if m]
    for k in keys:
        a = [m[k] for m in acc["LOW"]]
        b = [m[k] for m in acc["HIGH"]]
        if not a or not b:
            continue
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        sa = statistics.stdev(a) / len(a) ** 0.5 if len(a) > 1 else 0
        sb = statistics.stdev(b) / len(b) ** 0.5 if len(b) > 1 else 0
        w(" %-16s %14.2f ± %-5.2f %14.2f ± %-5.2f %10.2f" % (k, ma, sa, mb, sb, mb - ma))
    w("")

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok")
```

### stall-detect.py —— 活动扩张停滞点

```python
# -*- coding: utf-8 -*-
"""停滞点检测：LOW 的活动范围什么时候停止扩张？（100-tick 窗口的新增格数）"""
import csv
import glob
import os
import statistics

OUT = r"D:\github\battle2\tmp\stall-detect.txt"
WIN = 100


def load(p):
    rows = []
    with open(p, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            rows.append({"t": int(d["t"]), "x": int(d["px"]), "y": int(d["py"]),
                         "hp": float(d["hp"]), "ne": int(d["nEnemiesE"]),
                         "md": int(d["minDistE"]), "kills": int(d["kills"]),
                         "dmg": float(d["dmgTick"]),
                         "ex": int(d["ex"]), "ey": int(d["ey"])})
    return rows


def windows(rows):
    out = []
    seen = set()
    for s in range(0, len(rows), WIN):
        seg = rows[s:s + WIN]
        new = 0
        for r in seg:
            c = (r["x"] // 16, r["y"] // 16)
            if c not in seen:
                seen.add(c)
                new += 1
        md = [r["md"] for r in seg if r["md"] >= 0]
        dmg = sum(r["dmg"] for r in seg)
        out.append({
            "t0": seg[0]["t"], "new": new, "cells": len(seen),
            "hp": seg[-1]["hp"], "md": statistics.fmean(md) if md else -1,
            "ne": statistics.fmean(r["ne"] for r in seg),
            "dmg": dmg, "kills": seg[-1]["kills"],
        })
    return out


L = []
def w(s=""):
    L.append(s)


low = [(os.path.basename(p), windows(load(p))) for p in sorted(glob.glob(r"D:\github\battle2\tmp\dump-low-*.csv"))]
high = [(os.path.basename(p), windows(load(p))) for p in sorted(glob.glob(r"D:\github\battle2\tmp\dump-high-*.csv"))]

w("=" * 108)
w("A. LOW 各局逐窗口（每 %dtick）：新增格数 / 累计格 / 窗口末hp / 距离均 / 同屏敌 / 承伤" % WIN)
w("=" * 108)
for name, ws in low:
    w(" -- %s" % name)
    for k, x in enumerate(ws):
        w("    win%-2d t=%-5d new=%-3d cells=%-4d hp=%-7.1f md=%-6.0f ne=%.1f dmg=%.0f kills=%d"
          % (k, x["t0"], x["new"], x["cells"], x["hp"], x["md"], x["ne"], x["dmg"], x["kills"]))

w("")
w("=" * 108)
w("B. HIGH 前 8 个窗口（与 LOW 同期可比）")
w("=" * 108)
for name, ws in high:
    w(" -- %s" % name)
    for k, x in enumerate(ws[:8]):
        w("    win%-2d t=%-5d new=%-3d cells=%-4d hp=%-7.1f md=%-6.0f ne=%.1f dmg=%.0f kills=%d"
          % (k, x["t0"], x["new"], x["cells"], x["hp"], x["md"], x["ne"], x["dmg"], x["kills"]))

w("")
w("=" * 108)
w("C. 同期汇总：每个窗口序号的「新增格数」均值（LOW vs HIGH）")
w("=" * 108)
w(" %-6s %14s %14s %s" % ("win", "LOW new", "HIGH new", "LOW cells / HIGH cells"))
for k in range(6):
    la = [ws[k]["new"] for _n, ws in low if k < len(ws)]
    ha = [ws[k]["new"] for _n, ws in high if k < len(ws)]
    lc = [ws[k]["cells"] for _n, ws in low if k < len(ws)]
    hc = [ws[k]["cells"] for _n, ws in high if k < len(ws)]
    if not la:
        continue
    w(" %-6d %14s %14s   %.0f / %.0f" % (
        k, "%.1f±%.1f" % (statistics.fmean(la), statistics.stdev(la) / len(la) ** 0.5),
        "%.1f±%.1f" % (statistics.fmean(ha), statistics.stdev(ha) / len(ha) ** 0.5),
        statistics.fmean(lc), statistics.fmean(hc)))

w("")
w("=" * 108)
w("D. LOW 的「停滞窗口」（新增格数 <= 1）出现在第几个窗口、当时状态如何")
w("=" * 108)
for name, ws in low:
    stalls = [(k, x) for k, x in enumerate(ws) if x["new"] <= 1]
    if not stalls:
        w("  %-18s 无停滞窗口" % name)
        continue
    k, x = stalls[0]
    w("  %-18s 首次停滞 win%d (t=%d)  hp=%.0f md=%.0f ne=%.1f 之后剩 %d 窗口"
      % (name, k, x["t0"], x["hp"], x["md"], x["ne"], len(ws) - k - 1))

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok")
```

### win23-compare.py —— 指定窗口态势对比

```python
# -*- coding: utf-8 -*-
"""锁定 win2-win3（t=201-400）这一处：为什么同样的态势下 LOW 挨打而 HIGH 不挨打。"""
import csv
import glob
import os
import statistics

OUT = r"D:\github\battle2\tmp\win23-compare.txt"


def load(p):
    rows = []
    with open(p, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            rows.append({"t": int(d["t"]), "hp": float(d["hp"]), "ne": int(d["nEnemiesE"]),
                         "nb": int(d["nBulletsE"]), "pb": int(d["nBulletsP"]),
                         "md": int(d["minDistE"]), "dmg": float(d["dmgTick"]),
                         "mv": int(d["pmoving"]), "x": int(d["px"]), "y": int(d["py"]),
                         "dir": d["pdir"]})
    return rows


L = []
def w(s=""):
    L.append(s)


def stat(rows, t0, t1):
    seg = [r for r in rows if t0 <= r["t"] < t1]
    if len(seg) < 10:
        return None
    md = [r["md"] for r in seg if r["md"] >= 0]
    return {
        "n": len(seg),
        "hp_in": seg[0]["hp"], "hp_out": seg[-1]["hp"],
        "dmg": sum(r["dmg"] for r in seg),
        "hits": sum(1 for r in seg if r["dmg"] > 0),
        "md": statistics.fmean(md) if md else -1,
        "ne": statistics.fmean(r["ne"] for r in seg),
        "enemy_bullets": statistics.fmean(r["nb"] for r in seg),
        "our_bullets": statistics.fmean(r["pb"] for r in seg),
        "fire%": sum(1 for r in seg if r["pb"] > 0) / len(seg) * 100,
        "move%": statistics.fmean(r["mv"] for r in seg) * 100,
        "cells": len({(r["x"] // 16, r["y"] // 16) for r in seg}),
    }


for tag in ("low", "high"):
    w("=" * 116)
    w("%s —— 每局 win2(201-300) / win3(301-400)" % tag.upper())
    w("=" * 116)
    w(" %-16s %-6s %5s %7s %7s %6s %6s %6s %7s %8s %6s %7s %6s"
      % ("file", "区段", "n", "hp入", "hp出", "承伤", "被打", "距均", "同屏敌", "敌弹均", "我方弹", "开火%", "移动%"))
    for p in sorted(glob.glob(rf"D:\github\battle2\tmp\dump-{tag}-*.csv")):
        rows = load(p)
        for label, (t0, t1) in (("win2", (201, 301)), ("win3", (301, 401))):
            m = stat(rows, t0, t1)
            if not m:
                continue
            w(" %-16s %-6s %5d %7.0f %7.0f %6.0f %6d %6.0f %6.1f %7.2f %7.2f %6.1f %6.1f"
              % (os.path.basename(p), label, m["n"], m["hp_in"], m["hp_out"], m["dmg"], m["hits"],
                 m["md"], m["ne"], m["enemy_bullets"], m["our_bullets"], m["fire%"], m["move%"]))
    w("")

w("=" * 116)
w("汇总：win2+win3 合并（mean ± SE，n=局）")
w("=" * 116)
w(" %-16s %22s %22s %10s" % ("指标", "LOW", "HIGH", "差"))
acc = {}
for tag in ("low", "high"):
    ms = []
    for p in sorted(glob.glob(rf"D:\github\battle2\tmp\dump-{tag}-*.csv")):
        rows = load(p)
        a = stat(rows, 201, 301)
        b = stat(rows, 301, 401)
        if a and b:
            ms.append({k: (a[k] + b[k]) / 2 for k in a if k != "n"})
    acc[tag] = ms
for k in ("dmg", "hits", "md", "ne", "enemy_bullets", "our_bullets", "fire%", "move%", "cells", "hp_in"):
    a = [m[k] for m in acc["low"]]
    b = [m[k] for m in acc["high"]]
    if not a or not b:
        continue
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = statistics.stdev(a) / len(a) ** 0.5 if len(a) > 1 else 0
    sb = statistics.stdev(b) / len(b) ** 0.5 if len(b) > 1 else 0
    w(" %-16s %14.2f ± %-5.2f %14.2f ± %-5.2f %10.2f" % (k, ma, sa, mb, sb, mb - ma))
w(" (n: LOW=%d HIGH=%d)" % (len(acc["low"]), len(acc["high"])))

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok")
```

### dodge-quality.py —— 危险 tick 的移动方向分类

```python
# -*- coding: utf-8 -*-
"""躲弹质量：敌弹逼近时，玩家是「垂直闪避」还是「顺着弹道跑」。

判据（只用相邻两 tick，不做多帧匹配）：
  取 t-1 时刻距玩家最近的敌弹；其 t-1→t 位移 = 弹道方向 bv；
  玩家 t-1→t 位移 = pv；「弹→玩家」径向 rv。
  仅在「最近敌弹距离 < NEAR(40px)」的危险 tick 上统计：
    - approaching：bv·rv > 0（弹在逼近）
    - 夹角 = angle(pv, rv)：
        perp   (>60°)  垂直闪避 —— 好
        away   (45~60°) 侧向拉开
        along  (<45°)  顺着弹道跑 —— 坏
        still  未移动
"""
import csv
import glob
import math
import os
import statistics

OUT = r"D:\github\battle2\tmp\dodge-quality.txt"
NEAR = 40


def load(p):
    rows = []
    with open(p, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            rows.append({
                "t": int(d["t"]), "x": int(d["px"]), "y": int(d["py"]),
                "hp": float(d["hp"]), "dmg": float(d["dmgTick"]),
                "eb": int(d["nBulletsE"]), "ebxy": d.get("ebxy") or "",
                "md": int(d["minDistE"]), "ne": int(d["nEnemiesE"]),
            })
    return rows


def parse(s):
    out = []
    if not s:
        return out
    for part in s.split("|"):
        if ":" in part:
            a, b = part.split(":")
            try:
                out.append((float(a), float(b)))
            except ValueError:
                pass
    return out


def angle(u, v):
    nu = math.hypot(*u)
    nv = math.hypot(*v)
    if nu < 1e-6 or nv < 1e-6:
        return None
    c = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))
    return math.degrees(math.acos(c))


def analyze(rows):
    danger = 0
    appr = 0
    perp = away = along = still = 0
    near_min = 9999
    for i in range(1, len(rows)):
        r0, r1 = rows[i - 1], rows[i]
        b0 = parse(r0["ebxy"])
        if not b0:
            continue
        # t-1 时刻离玩家最近的敌弹
        px, py = r0["x"], r0["y"]
        bx, by = min(b0, key=lambda b: math.hypot(b[0] - px, b[1] - py))
        d0 = math.hypot(bx - px, by - py)
        if d0 >= NEAR:
            continue
        danger += 1
        near_min = min(near_min, d0)
        # 该弹在 t 时刻的位置（取最近的同颗近似）
        b1 = parse(r1["ebxy"])
        if not b1:
            continue
        bx1, by1 = min(b1, key=lambda b: math.hypot(b[0] - bx, b[1] - by))
        bv = (bx1 - bx, by1 - by)
        pv = (r1["x"] - px, r1["y"] - py)
        rv = (px - bx, py - by)  # 弹→玩家
        dot = bv[0] * rv[0] + bv[1] * rv[1]
        if dot > 0:
            appr += 1
        if math.hypot(*pv) < 0.5:
            still += 1
            continue
        a = angle(pv, rv)
        if a is None:
            continue
        if a > 60:
            perp += 1
        elif a >= 45:
            away += 1
        else:
            along += 1
    moved = perp + away + along
    return {
        "danger": danger, "appr%": appr / max(danger, 1) * 100,
        "perp%": perp / max(moved, 1) * 100, "away%": away / max(moved, 1) * 100,
        "along%": along / max(moved, 1) * 100, "still%": still / max(danger, 1) * 100,
        "near_min": near_min if near_min < 9999 else -1, "moved": moved,
    }


L = []
def w(s=""):
    L.append(s)


w("=" * 112)
w("A. 每局：危险 tick（最近敌弹 < %dpx）下的移动方向分类" % NEAR)
w("=" * 112)
w(" %-6s %-17s %7s %7s %7s %8s %8s %8s %9s"
  % ("档", "file", "危险t", "逼近%", "垂直闪", "侧拉", "顺弹道", "未移动", "最近距"))
res = {}
for tag in ("low", "high"):
    per = []
    for p in sorted(glob.glob(rf"D:\github\battle2\tmp\dump-{tag}-*.csv")):
        m = analyze(load(p))
        per.append(m)
        w(" %-6s %-17s %7d %6.1f%% %6.1f%% %7.1f%% %7.1f%% %7.1f%% %9.0f"
          % (tag.upper(), os.path.basename(p), m["danger"], m["appr%"], m["perp%"],
             m["away%"], m["along%"], m["still%"], m["near_min"]))
    res[tag] = per

w("")
w("=" * 112)
w("B. 汇总（mean ± SE，n=局）")
w("=" * 112)
w(" %-16s %22s %22s %10s" % ("指标", "LOW", "HIGH", "差"))
for k in ("danger", "appr%", "perp%", "away%", "along%", "still%", "near_min", "moved"):
    a = [m[k] for m in res["low"] if m[k] >= 0]
    b = [m[k] for m in res["high"] if m[k] >= 0]
    if not a or not b:
        continue
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = statistics.stdev(a) / len(a) ** 0.5 if len(a) > 1 else 0
    sb = statistics.stdev(b) / len(b) ** 0.5 if len(b) > 1 else 0
    w(" %-16s %14.2f ± %-5.2f %14.2f ± %-5.2f %10.2f" % (k, ma, sa, mb, sb, mb - ma))

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok")
```

### nn-vs-god.py —— 同 seed 换策略配对

```python
# -*- coding: utf-8 -*-
"""同 seed 配对：NN vs God（同关、同初始条件、同 RNG）。

⚠ 定位：God 只作**诊断参照**（它 §12.1 有三条执行器缺陷），不作为模仿目标。
本脚本只回答一件事：同一批 seed 上，NN 死的那几局 God 是不是也死。
"""
import re

OUT = r"D:\github\battle2\tmp\nn-vs-god.txt"
LOG_NN = r"D:\github\battle2\tmp\batch-run.log"
LOG_GOD = r"D:\github\battle2\tmp\god-run.log"
PAT = re.compile(
    r"seed(\d+) outcome=(\S+) ticks=(\d+) win=(\S+) score=([\d.]+) kills=(\d+)"
)


def parse(path):
    out = {}
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return out
    for m in PAT.finditer(txt):
        out[int(m.group(1))] = {
            "kills": int(m.group(6)), "ticks": int(m.group(3)),
            "outcome": m.group(2), "score": float(m.group(5)),
        }
    return out


nn = parse(LOG_NN)
god = parse(LOG_GOD)

L = []
def w(s=""):
    L.append(s)


w("解析到：NN %d 局 / God %d 局" % (len(nn), len(god)))
seeds = sorted(set(nn) & set(god))
low_seeds = [int(x) for x in open(r"D:\github\battle2\tmp\seeds-low.txt") if x.strip().isdigit()]
high_seeds = [int(x) for x in open(r"D:\github\battle2\tmp\seeds-high.txt") if x.strip().isdigit()]

w("")
w("=" * 100)
w("A. 逐 seed 配对（NN 分组：LOW 组 = NN 只杀 1 个的那 10 局）")
w("=" * 100)
w(" %-9s %-7s %26s %26s" % ("seed", "组", "NN (kills/ticks/outcome)", "God (kills/ticks/outcome)"))
for s in seeds:
    g = "LOW" if s in low_seeds else ("HIGH" if s in high_seeds else "?")
    a, b = nn[s], god[s]
    w(" %-9d %-7s %18d/%-6d/%-14s %18d/%-6d/%-14s"
      % (s, g, a["kills"], a["ticks"], a["outcome"], b["kills"], b["ticks"], b["outcome"]))


def summar(rs):
    n = len(rs)
    if not n:
        return None
    return {
        "n": n,
        "kills": sum(r["kills"] for r in rs) / n,
        "clear": sum(1 for r in rs if r["outcome"] == "stage_clear") / n * 100,
        "ticks": sum(r["ticks"] for r in rs) / n,
    }


w("")
w("=" * 100)
w("B. 分组汇总")
w("=" * 100)
for label, ss in (("LOW 组（NN 只杀 1）", low_seeds), ("HIGH 组（NN 通关）", high_seeds)):
    a = summar([nn[s] for s in ss if s in nn])
    b = summar([god[s] for s in ss if s in god])
    if not a or not b:
        continue
    w(" -- %s  (seed 数 %d)" % (label, a["n"]))
    w("     NN : kills=%.2f  clear=%.0f%%  ticks=%.0f" % (a["kills"], a["clear"], a["ticks"]))
    w("     God: kills=%.2f  clear=%.0f%%  ticks=%.0f" % (b["kills"], b["clear"], b["ticks"]))

w("")
w("=" * 100)
w("C. ★ 关键判定：LOW 组那 10 个 seed 上，God 是死还是活")
w("=" * 100)
same_die = same_live = nn_die_god_live = nn_live_god_die = 0
for s in low_seeds:
    if s not in nn or s not in god:
        continue
    a, b = nn[s], god[s]
    na = a["outcome"] == "stage_clear"
    nb = b["outcome"] == "stage_clear"
    if not na and nb:
        nn_die_god_live += 1
    elif na and not nb:
        nn_live_god_die += 1
    elif not na and not nb:
        same_die += 1
    else:
        same_live += 1
w("  两者都死        : %d / %d" % (same_die, len(low_seeds)))
w("  NN死 / God活    : %d / %d   ← 若是这一类占多数 ⇒ 同环境下 NN 做砸了（策略问题）"
  % (nn_die_god_live, len(low_seeds)))
w("  NN活 / God死    : %d / %d   ← 若这类出现 ⇒ 说明 God 也有做不到的局" % (nn_live_god_die, len(low_seeds)))
w("  两者都活        : %d / %d" % (same_live, len(low_seeds)))

# God 在 LOW 组的 kills 分布
gk = [god[s]["kills"] for s in low_seeds if s in god]
nk = [nn[s]["kills"] for s in low_seeds if s in nn]
if gk:
    w("")
    w("  LOW 组 kills：NN = %s" % nk)
    w("  LOW 组 kills：God= %s   (mean %.2f vs NN %.2f)" % (gk, sum(gk) / len(gk), sum(nk) / len(nk)))

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("ok nn=%d god=%d" % (len(nn), len(god)))
```

### cleanlog.py —— 日志净化

```python
# -*- coding: utf-8 -*-
"""通用日志净化：<src> <dst>（非 ASCII 降为 '.'，保留换行）。"""
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
b = open(src, "rb").read()
s = b.decode("utf-8", errors="replace")
s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
clean = "".join(
    "\n" if ch == "\n" else ("\t" if ch == "\t" else (ch if 32 <= ord(ch) < 127 else "."))
    for ch in s
)
open(dst, "w", encoding="utf-8", newline="\n").write(clean)
print(len(b), "->", len(clean))
```

### fixlog.py —— 日志中文反向还原

```python
# -*- coding: utf-8 -*-
"""还原被 PS 重定向双重编码的日志中文：<src> <dst>。

链：程序输出 UTF-8 字节 → PS 按 cp936 解码成乱码 str → 按 GBK-ish 写盘
⇒ 反向：utf-8 decode → gbk encode → utf-8 decode。
"""
import sys

src, dst = sys.argv[1], sys.argv[2]
raw = open(src, "rb").read()
s = raw.decode("utf-8", errors="replace")
best = s
try:
    fixed = s.encode("gbk", errors="replace").decode("utf-8", errors="replace")
    if fixed.count("\ufffd") < s.count("\ufffd"):
        best = fixed
except Exception:
    pass
open(dst, "w", encoding="utf-8", newline="\n").write(best)
print("ok", len(s), "->", len(best), "fffd", best.count("\ufffd"))
```

### trace-c20.ts —— ❌ 反面样本（自建探针不可信，仅作警示）

```ts
#!/usr/bin/env bun
/**
 * trace-c20.ts — x20 单局逐帧 / 批量对照（NN vs God，同 seed、同关、同初始条件）。
 *
 * 单局逐帧：
 *   bun tmp/trace-c20.ts --policy nn --seeds 415000-415019 --weights-dir tmp/w-it30 \
 *       --summary tmp/sum-nn.jsonl
 *   bun tmp/trace-c20.ts --policy nn --trace-seed 415020 --out tmp/trace-nn-415020.csv
 *   bun tmp/trace-c20.ts --policy god --trace-seed 415020 --out tmp/trace-god-415020.csv
 *
 * world/ai 的初始化顺序逐字照抄 tools/sim/export-eval-game.ts:341-421
 * （new World → reseed → difficulty/rules → new Simulation(world, ai) → loadStageData → ai.reset）。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { decodeStageGrid } from '../src/nn/config-stage'
import { NNInput } from '../src/nn/policy-input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import type { InputLike } from '../src/game/Input'

function arg(name: string, dflt?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : dflt
}

const LEVEL = 'nn-training/levels/ladder-c20-lives1.jsonc'
// jsonc：带尾逗号（oxfmt 格式化过）⇒ 去尾逗号后再 parse
const lvl = JSON.parse(readFileSync(LEVEL, 'utf8').replace(/,(\s*[}\]])/g, '$1')) as {
  stages: unknown[]
  player?: { lives?: number; level?: number }
}

const stageIdx = parseInt(arg('stage-idx', '0')!, 10)
const policy = arg('policy', 'nn')!
const weightsDir = arg('weights-dir', 'tmp/w-it30')!
const seedsSpec = arg('seeds', '')!
const traceSeed = arg('trace-seed')
const outCsv = arg('out', '')
const summaryPath = arg('summary', '')
const maxTicks = parseInt(arg('max-ticks', '20000')!, 10)

const seeds: number[] = seedsSpec
  ? (() => {
      const [a, b] = seedsSpec.split('-').map((x) => parseInt(x, 10))
      const out: number[] = []
      for (let s = a; s <= (Number.isNaN(b) ? a : b); s++) out.push(s)
      return out
    })()
  : traceSeed
    ? [parseInt(traceSeed, 10)]
    : []

const wantTrace = traceSeed !== undefined && outCsv !== ''

type Row = {
  seed: number
  policy: string
  kills: number
  ticks: number
  dmg: number
  shots: number
  outcome: string
  level: number
  pu: number
  killCount: number
  rem: number
  tot: number
  spw: number
}

function runOne(seed: number): Row {
  const stage = decodeStageGrid(JSON.stringify(lvl.stages[stageIdx]), 0, seed)
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = lvl.player?.level ?? 0
  world.lives = lvl.player?.lives ?? 1

  const ai: unknown =
    policy === 'god'
      ? new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, new RNG((seed ^ 0x9e3779b9) >>> 0))
      : new NNInput(world, { weightsDir })
  const sim = new Simulation(world, ai as InputLike)
  world.loadStageData(stage, 0)
  ;(ai as { reset: () => void }).reset()

  const anyW = world as unknown as Record<string, any>
  const rows: string[] = ['t,px,py,pdir,pmoving,hp,kills,nEnemies,nBulletsE,minDistE,dmgTick']
  let prevHp = anyW.player?.hp ?? 0
  let shots = 0
  let t = 0
  let dmg = 0
  for (; t < maxTicks; t++) {
    sim.tick()
    const p = anyW.player
    if (!p || p.alive === false) break
    dmg = anyW.playerDamageTaken ?? p.maxHp - p.hp ?? dmg
    if (wantTrace) {
      const tanks = (anyW.tanks ?? []) as any[]
      const enemies = tanks.filter((x) => x && x.alive !== false && x.allegiance === 'enemy')
      const bullets = (anyW.bullets ?? []) as any[]
      const eb = bullets.filter((b) => b && b.allegiance === 'enemy').length
      let minD = 9999
      for (const e of enemies) {
        const d2 = Math.abs(e.x - p.x) + Math.abs(e.y - p.y)
        if (d2 < minD) minD = d2
      }
      const dmgTick = prevHp - p.hp
      rows.push(
        [
          t,
          Math.round(p.x),
          Math.round(p.y),
          p.dir,
          p.moving ? 1 : 0,
          Math.round(p.hp * 100) / 100,
          anyW.killCount ?? -1,
          enemies.length,
          eb,
          minD === 9999 ? -1 : Math.round(minD),
          dmgTick > 0 ? Math.round(dmgTick * 100) / 100 : 0,
        ].join(','),
      )
      prevHp = p.hp
    }
  }
  const kills = anyW.killCount ?? 0
  const rem = anyW.enemiesRemaining ?? -1
  const tot = anyW.enemiesTotal ?? -1
  const spw = anyW.enemiesSpawned ?? -1
  const state = anyW.state ?? ''
  const outcome =
    kills >= (anyW.enemiesTotal ?? 20) ? 'stage_clear' : state === 'won' ? 'stage_clear' : 'gameover'
  if (wantTrace) writeFileSync(outCsv, rows.join('\n') + '\n', 'utf8')
  shots = anyW.playerShots ?? 0
  return {
    seed,
    policy,
    kills: kills || Math.max(0, tot - rem),
    ticks: t,
    dmg: Math.round(dmg),
    shots,
    outcome,
    level: world.playerLevel ?? 0,
    pu: anyW.powerUpsCollected ?? 0,
    killCount: kills,
    rem,
    tot,
    spw,
  }
}

const out: Row[] = []
for (const s of seeds) {
  const r = runOne(s)
  out.push(r)
  console.log(
    `[trace] ${r.policy} seed=${r.seed} kills=${r.kills} ticks=${r.ticks} dmg=${r.dmg} ${r.outcome}` +
      ` | killCount=${r.killCount} rem=${r.rem} tot=${r.tot} spw=${r.spw}`,
  )
}
if (summaryPath && out.length) {
  writeFileSync(summaryPath, out.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf8')
}
```

