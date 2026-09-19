# NN 诊断方法论 —— 如何找到「策略卡住了」的真实瓶颈

> **适用场景**：策略（NN）表现不达预期，但**不知道该改什么**——奖励？观测？位移？还是根本不知道。
> **来源**：2026-09-19 对 x20 线（20 敌 1 命）"均杀卡在 ~7"的一次完整排查。
> 那次排查**连续否掉 9 个假设**后才收敛到结论，本文件是把过程中的纪律与工具提炼成可复用体系。
>
> **读者**：任何接手"训练不涨"类问题的 agent。**请按 §2 的流程走，不要跳步。**
>
> ⚠ 本文只讲**诊断**，不涉及"该改哪个超参"。诊断的目标是**把搜索空间砍到能下手的大小**。

> **⚠ 关于文中出现的 `tmp/*.py`、`tmp/*.ts`**
> 它们是**运行时的落位要求**（脚本放 `tmp/` 下才能照抄本文的命令跑），但 `tmp/` 会被清理。
> **所有脚本的完整源码已固化在 `docs/nn-diagnosis-toolkit.md`**：
> 文中凡标 **（toolkit）** 的脚本，取用时先从那篇文档里把源码取出、放回 `tmp/`（或任意目录），
> 命令里的 `tmp/xxx` 路径随之调整即可。
> **项目自带**的工具（`tools/sim/export-eval-game.ts`、`tools/replay/verify-replay.ts`、
> `src/replay/*`）不在其中，它们不会丢。

---

## 0. 一句话总纲

> **不要用聚合指标猜机制。用逐帧对照，在下任何结论之前先设法证伪自己。**

本次排查最贵的三次错误全部同源：**拿一个字段名，凭直觉解释它的数字**。

---

## 1. 七条原则（每条都对应一次踩坑）

### 1.1 ★★★ 先查定义，再解释数字

`eval_log.jsonl` / metrics 里的每个字段，**先读它的定义再解释**，位置在
`src/game/World.ts`（状态字段）、`src/game/Simulation*.ts`（计算与事件）、`src/config/*.ts`（常量/伤害/血量）。

本次三次误读（全部因此作废）：

| 我当时的解释 | 定义实际是 | 后果 |
|---|---|---|
| 单发伤害 = `dmg / playerHits` ≈ 223 | 两字段**不同源**（`playerHits=0` 的局 dmg 却有 191.5；`playerDeaths` 恒 0）；真值是 `round(firepower × DAMAGE_SCALE)`，basic 100 / power 128，玩家 hp 263 | "一发就死"结论错 |
| `dmg/千tick` = 躲避质量 | 玩家 hp=263 ⇒ **每局都以挨满 ~3 发告终** ⇒ 它 ≈ `263 / ticks`，是**寿命倒数** | "承伤率是杠杆"是循环论证 |
| `stuckTicks` = 卡墙 | `World.ts`：**中心 cell 不变 且 本 tick 未命中敌车 ⇒ +1** | "减少无效时间"建议错；且 c20 是玩具关，内部无墙 |

**做法**：解释任何指标前，先在代码里 `grep` 出它的写入点，把注释读一遍。30 秒的事。

### 1.2 单局结论必须扩容

"LOW 挨打时站着不动"——n=1 时看着极其漂亮（命中后 5 tick 距离一字不变 `[63,63,63,63,63]`）。
扩到 10 局后：**LOW 9.75±0.68 vs HIGH 10.06±0.71 ⇒ 无差异**，而两组都普遍"粘着走"。

**规则**：任何"我在某一局看到 X"都必须先标 `n=1，待验证`，扩到 ≥10 局再进结论。

### 1.3 ★★★ 用「结果」分组 = 幸存者偏差

**本次最贵的教训。** 我用 `kills≤1`（LOW）vs `kills≥16`（HIGH）分组，连否 9 个因子（主动性、瞄准、
开火率、距离、同屏敌数、移动率、活动范围、宏观位移、闪避方向）**全部无显著差异**。

原因不是"没找到"，而是**分组本身选错了**：LOW 组是"被挑出来的失败局"，它们的共同点可能只是
**这些 seed 本来就难**。事后用 God 在同一批 seed 上验证 ⇒ **LOW 组 10 局里 8 局 God 也死**。

**正确对照 = 同环境、换策略**（同 seed / 同 RNG / 同关卡，只变策略）。这样两组是"同一局的两个玩家"，
不存在选择偏差。见 §2 Step 4。

### 1.4 区分「结果」与「原因」

看起来最像杠杆的量往往是被结果决定的：

- `pu_on%`（场上有道具的时间）：LOW 0.0% vs HIGH 49.7% —— 但**道具是杀出来的**（杀 power 敌才掉）。
- `cells`（活动范围）：LOW 27 vs HIGH 183 —— 可能是"清完一片再挪"的**结果**。
- 两者都不能直接当"要提升的目标"。

**自检问法**：这个量，是"我做了什么"还是"发生了什么"？后者一律先当结果。

### 1.5 用「率」消共线

kills 与 ticks 相关系数 **+0.944**——几乎共线。此时任何"总量"指标的对比都会被寿命污染
（活得久当然什么都多）。

**做法**：全部换算成 `/千tick` 的率，或者**分区段切片**（前 200 tick / 首次交火窗口）。
本次正是靠这一步，发现"前 200 tick 两组几乎逐项相同"，从而把注意力压到 t=200–400。

### 1.6 自检优先：没有 hash 校验的逐帧数据不要用

项目里 replay 自带 `tickHashes`（每 100 tick 一个）。**重放后必须逐点对账**：

```
[replay-dump] ticks=482 total=482 hashOk=4 hashBad=0   ← 可用
[replay-dump] ticks=487 total=482 hashOk=0 hashBad=4   ← 数据作废，先修重放
```

本次第二版重放器 `hashBad=4/6`（漏了 `rng.reseed`）——如果不看 hash，我会拿一堆分叉的数据
做完整轮分析。

### 1.7 教师（God AI）只作**参照**，不作**目标**

God 是**有缺陷的规则系统**（`plan/Intent-Policy-NN-Plan.md:437` §12.1：冰冻期目标摇摆 /
不清墙开路 / 追击不并线）。本次配对恰好给出反证：
**NN 通关的那 10 个 seed 上，God 平均只杀 5.70、1/10 通关** ⇒ God 不是天花板。

**允许**：用 God 做"同环境基线"，看差异落在哪。
**禁止**：把"像 God 那样"当训练目标；用 God 读数定阈值；用 God 的表现判关卡难度。

---

## 2. 五步流程

### Step 1　保真执行：用项目自己的执行器，**不要自建**

自建探针（直接 `new NNInput(world)` 驱动 `new Simulation`）**结果不可信**：
本次自建得到 `kills=0 / 577 ticks`，而 eval_log 同 seed 是 `kills=9 / 2312 ticks`——
差 4 倍。原因是项目里存在多套 NN 驱动路径，观察时点/相位不同。

**正确**：`tools/sim/export-eval-game.ts`（节点侧参考实现），它**逐字复现** eval：

```bash
# 自定义关卡的 stageJson = 关卡文件 stages[i] 的「原样」JSON（单行、去尾逗号）
python -c "import json,re;s=open('nn-training/levels/ladder-c20-lives1.jsonc',encoding='utf-8').read();d=json.loads(re.sub(r',(\s*[}\]])',r'\1',s));open('tmp/stage0.json','w',encoding='utf-8').write(json.dumps(d['stages'][0],ensure_ascii=False,separators=(',',':')))"

bun tools/sim/export-eval-game.ts \
  --stage-json "$(cat tmp/stage0.json)" --stage 0 --seed 415020 \
  --difficulty hard --weights nn-training/weights/<w>.json --policy nn \
  --lives-override 1 --player-level 0 \
  --out tmp/eg-xxx --replay tmp/rp-xxx --node-label local
```

输出行即对照锚点（务必先与 `eval_log.jsonl` 对一次）：
```
[eval-game] s0 seed415020 outcome=gameover ticks=482 win=false score=0.112 kills=1 hitRate=0.500
```

### Step 2　重放 + 自检

replay 是 `bc-replay` v1 JSON：`{initialSnapshot, framesBase64, totalTicks, metadata, seed, tickHashes, hashInterval}`。
**注意 `initialSnapshot` 里没有 RNG 状态。**

重放**必须照抄** `tools/replay/verify-replay.ts:93-145`，三处漏一处必分叉：

```ts
const world = new World()
world.rng.reseed(replay.seed)                       // ← ① 最要命：不重播种必然分叉
world.difficultyKey = meta.difficulty
world.difficulty = DIFFICULTIES[meta.difficulty] ?? DIFFICULTIES['classic']
world.rules = RULES[meta.difficulty] ?? DEFAULT_RULES
world.loadStageData(STAGES[meta.stage] ?? STAGES[0], 0)   // ← ② 先 loadStageData
restoreWorld(world, replay.initialSnapshot)               // ← ③ 再 restore
const input = new ReplayInput(replay.frames)
const sim = new Simulation(world, input); sim.input = input; sim.input2 = input.input2 ?? null
world.state = 'playing'
while (!input.isFinished && tick < replay.totalTicks + 10) {
  sim.tick(); input.advance(); tick++        // ← ④ advance() 不能漏
  world.consumeEvents?.()
  if (tick % replay.hashInterval === 0) { /* worldTickHash(world) 对 tickHashes */ }
}
```

现成脚本：**`replay-dump.ts`（toolkit）** —— 重放 + 逐帧 CSV + `tickHashes` 自检。

### Step 3　批量 + 分区段

- 每档 **≥10 局**（单局只配当线索）。
- 输出**单位时间的率**，并**切片**：`head200 / 全段 / 关键窗口`。

本次的分层结构（也是推荐模板）：
```
① 总览（ticks / kills / move% / cells / turns / hits）
② 每局逐窗口（100-tick）：新增格数 / hp / 距离 / 同屏敌 / 承伤   ← 找"何时分化"
③ 关键窗口细分                                            ← 找"为什么分化"
```

### Step 4　★ 同环境换策略对照（替代"结果分组"）

```bash
# 同一批 seed、同一关卡、同一 RNG，只把 --policy 换掉
bun tools/sim/export-eval-game.ts ... --policy god --seed $S ...
```

配对后回答三个问题：
1. **两者都死 / 都活 / 一死一活**各多少（判断"是局难还是策略差"）
2. 谁在"自己能打的局"上更强（上限对比）
3. 谁更稳（方差对比）

本次结论：**NN 是高方差策略（10 局 20 杀 + 10 局 1 杀），God 是低方差（两类局都 5.7–6.7）**
⇒ "均杀卡在 7"的实质是**两极化**，不是水平不足 ⇒ **读数必须换成 p10 kills / 低分局占比**。

### Step 5　归一化读数

结论落地时，把 pooled 均值换成能暴露分布的统计量：**p10 / p50 / p90、低分档占比**。
本次 pooled 6.34 ≈ (10×20 + 10×1)/20，把最要命的信息（一半的局崩了）平均掉了。

---

## 3. 工具清单

> ⚠ 下表脚本的**完整源码**固化在 **`docs/nn-diagnosis-toolkit.md`**（11 个文件 + 运行前置 + 全流程命令）。
> 本表只登记"脚本名 + 用途"：标 **（toolkit）** 的，用前先从那篇文档把源码取出放回 `tmp/`；
> 标 **（项目自带）** 的不会丢。

| 脚本 | 用途 |
|---|---|
| `replay-dump.ts`（toolkit） | **重放 + 逐帧 dump + hash 自检**（核心工具） |
| `export-eval-game.ts`（项目自带 `tools/sim/`） | 保真单局执行 + `--replay` 导出 |
| `verify-replay.ts`（项目自带 `tools/replay/`） | 重放的权威写法（照抄 93–145 行） |
| `frame-compare-batch.py`（toolkit） | 10×2 逐帧聚合（移动/活动范围/被命中/道具） |
| `slice-analyze.py`（toolkit） | 前 200 tick vs 全段（朝敌移动 / 瞄准 / 开火率） |
| `stall-detect.py`（toolkit） | 100-tick 窗口的"活动扩张停滞点" |
| `win23-compare.py`（toolkit） | 指定窗口的态势对比（距离/敌数/敌弹/开火） |
| `dodge-quality.py`（toolkit） | 危险 tick 下的移动方向分类（垂直闪避 vs 顺弹道） |
| `nn-vs-god.py`（toolkit） | 同 seed 策略配对 |
| `cleanlog.py` / `fixlog.py`（toolkit） | 日志净化 / 中文反向还原（见 §4） |

**环境坑（必须知道）**

1. **PowerShell 里跑 `bun tools/sim/*.ts` 必须前置 PortableGit 的 `usr\bin`**，否则内部 spawn bash
   会命中 `WindowsApps\bash.exe`（WSL 存根）⇒ `Access is denied. E_ACCESSDENIED` + 秒退。
2. **`*>` 重定向会把子进程 UTF-8 中文按 cp936 转码**（"失败"→"澶辫触"）；要读中文原文用
   `fixlog.py`（toolkit）反向还原。日志被 Read 工具判成 binary 时，先用 `cleanlog.py`（toolkit）净化。
3. 自定义关卡 jsonc **带尾逗号**（oxfmt 格式化过）⇒ 用正则去尾逗号再 `JSON.parse`。

---

## 4. 负结果清单（这是资产，不是失败）

**被证伪 / 被排除的假设**（别再重复走）：

| # | 假设 | 否证方式 |
|---|---|---|
| 1 | NN 不主动打 power 敌 | power 击杀占各 kills 桶恒定 23–30%；四敌种 kills 占比 23.6/26.7/26.7/23.0 |
| 2 | NN 不捡道具 | `got/spawn` 各桶恒定 1.62–1.90（不拒绝捡） |
| 3 | 卡墙 / 无效时间多 | `stuckTicks` 定义是"没换格且没打中"；且玩具关无墙 |
| 4 | 承伤率是可控杠杆 | 它是寿命倒数（hp 固定 ⇒ 每局都挨满） —— 循环论证 |
| 5 | 加 `wDmg` 能压承伤 | 惩罚剂量改变不了"怎么躲"；且死亡代价由整局承担 |
| 6 | 定位在 lvl 0 存活 | lvl 由**随机稀缺**掉落决定，不能当稳定杠杆 |
| 7 | 活跃度不足（前 200 tick） | 前 200 tick 两组逐项相同（moveToEnemy/aim/cells/距离） |
| 8 | 首次交火时"挨打不规避" | n=10 后 `pre_move` 无差异（9.75 vs 10.06） |
| 9 | 顺弹道跑 vs 垂直闪避 | `perp%`/`along%` 均无显著差异（HIGH 的 along% 反而更高） |

**其中 7–9 的否证本身暴露了 §1.3 的方法缺陷** —— 这一条比任何一个负结果都值钱。

---

## 5. 适用边界与代价

**适用**
- "表现不达预期但不知从何下手"——把搜索空间从"十几个候选因子"砍到"一个可验证的问题"
- 验证 / 否证具体假设（本次否掉 9 个，都是净收益）
- 建立可复现的失败样本（同一失败模式 10 局模板化 ⇒ 这是个确定性缺陷，不是随机）

**不适用 / 局限**
- **只能找相关与排除，不能证明因果**。要证因果需要干预实验（改一处、重跑、看差异）。
- **危险窗口类统计容易样本不足**（LOW 组 10 局里 danger 只有 0–38 个 tick，SE 高达 12）——
  这类指标要么放宽窗口，要么放弃。
- **对"观测里没有的信息"无能为力**（例：想看"是否在敌人炮口线上"，就需要 `edir` 这一列；
  没有就先补列，别用别的量代偿）。

**代价**（要有心理准备）
- 单轮完整跑（20 局 export + dump + 多层分析）约 **10–20 分钟**，其中 export 占大头。
- 需要三层：**保真执行 → 重放自检 → 分析**。任何一层偷懒，结论都不可信。
- 最容易失控的地方：**在错误的字段上反复建模型**（§1.1）。查定义只要 30 秒，忽略它能烧掉两小时。

---

## 6. 最小行动清单（照着做）

1. `grep` 出你要用的每个指标在 `World.ts` / `Simulation*.ts` 里的定义 —— 记下来。
2. 用 `export-eval-game.ts` 跑 **1 局**，与 `eval_log.jsonl` 对账（kills/ticks 必须逐字一致）。
3. 跑 `--replay`，用 `replay-dump.ts`（toolkit）重放，**hash 必须全对**才继续。
4. 挑 **10 局/档**，批量 dump。
5. 先做**总览 + 逐窗口切片**，找"何时分化"。
6. 再对分化窗口做细分，找"为什么"。
7. **每一步都要问：这是原因还是结果？这个分组有没有选择偏差？**
8. 结论落地时，读数换成能暴露分布的统计量（p10 / 低分档占比）。
9. 把**被否掉的假设**也记下来 —— 它们是下一轮的地基。
