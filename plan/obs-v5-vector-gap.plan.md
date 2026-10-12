# plan: obs-v5 观测缺口一次补齐（POLICY_EXTRA 9→37 / SCALARS 30→40）

> 状态：**agent 侧已实施（2026-10-12）**——S1–S8 的代码/测试/课程文件全部落盘，`bun run check`
> 与 nn-training python 门禁绿；表 A 逐条见 §4（A6 为粗测，数字在 §3-S7）。**S9（开课→训练→判门）
> 由人执行**，本 plan 不开课。本文档是 obs-v5 的**唯一实施依据**：
> 第一部分（§1–§2）是冻结规格，实现时只许照抄不许发挥；第二部分（§3）是按序排列的实施步骤，
> 每步有输入文件、改动清单、测试与完成标准；第三部分（§4–§5）是验收与预注册门；附录是背景与评审记录。
> 本文档**不改任何代码、不开课、不重导语料**——动手的是按本文施工的 agent，**开课的是人**。
> 修订：r8（2026-10-12）重组为可实施体系（§0 阅读指南＋§3 实施步骤为新增主体；规格内容与 r7 一致，
> 另修复 r7 自审出的 4 处偏差：腿 B 头宽 188→189 及其参数构成、§2 缺症状映射表、课程 spec 模糊、
> s39 与 s14 同敌规则未冻结——见 §0 修订表）。

---

## 0. 阅读指南

### 0.1 谁读这份文档

| 角色 | 读什么 | 不读什么 |
|---|---|---|
| 实施 agent（改代码） | §1（目标）→ §2（规格，逐字）→ §3（按 S0–S9 顺序施工）→ §4（验收打勾） | §5（训练门，人执行）、附录（背景） |
| 开课的人 | §1.3（两阶段）＋ §3-S8（课程文件 spec）＋ §5（门） | 实现细节 |
| 评审 | 附录 C（评审对照）＋ §0.3（修订历史） | — |

### 0.2 术语（全文统一）

- **主腿 = 腿 A**（policy_extra 架构，头 165）：本次训练的架构。**腿 B**（空间塔，头 189）只做
  参考口径（参数/golden 照出），tower 重测另案，不在本腿训练。
- **intent/goal 档**（inject 137）与本 bump 无关，一律不动。
- **本腿**＝ ladder 第一阶段；**专场**＝ G0 过后的障碍/基地阶段（另起账本，本 plan 只冻结前置项）。

### 0.3 修订历史（r8 之前的一行结论，细则见附录 C）

| 版 | 改动 |
|---|---|
| r1 | 初稿：extra 9→37 / scalars 30→38（标题与正文维度打架，作废） |
| r2 | 评审 F1–F18：删 free/prev/hit/recentHit，定价口径对账，拆表 A/B，预注册门 |
| r3 | 评审 R1–R20：s31 阵营修正、三向基地、inject/extra 拆档、维度 40/39 |
| r4 | 评审 S1–S20：zero-init 收窄、坑 0 四张量迁移、A10/A4b、砍维序 |
| r5 | 评审 sb：曾拆 β→α→exp1′ 三腿（P0 地基：bump 对 RL 腿无重导成本） |
| r6 | 用户指令：回单腿一次补齐 37/39、value 全吃、ladder 先行＋G0 80%、0.8 降级线 |
| r7 | 补 s39（scalars 30→40，value 不动） |
| r8 | 重组为实施体系；修复：腿 B 189、症状映射表回场、课程 spec 具体化、s39 同敌规则 |
| r9 | 首轮代码评审（10 条）落定：**补 DECISIONS 条目**（§2026-10-12-goalnn-obs-v5-critic-superset）、
腿 B 迁移改**分段**映射（平铺拷 151 会静默错位）、`nearestPickupDist` **改为一并搬迁**、
`sampleBasePressure` 口径澄清（敌车过滤，非逐字照抄）、`alignedScan` 参数语义与侧判定冻结、
死路径 `nn-demo/…` 标注未核实、G0 阈值出处（用户指令）+ G5 降为次门、s39 分母说明、
`validate_export` 补 extra、权重 freeze 口径（gitignore）——F1–F10 全落 |
| r10 | 施工落盘（agent 侧）＋二轮自审补修 **7 条**：① python 侧 `mirror_x` 漏了 `EXTRA_MIRROR_NEG`
（相对向量 x 分量**不取负** ⇒ 镜像语料与 TS `mirrorSwap` 不同源，A4b 真红；已补 + 独立重实现用例）；
② `infer.ts` value 头判据收紧为**按档位严格**（leg A 165 不再可能被 137 宽的 v4 权重蒙混进 137 槽；
无标志的 legacy 档才兼容 128/137 两宽 —— goal 路径复用 137 槽）；③ 六处合成 shard 写入器仍写 9 维
`extra`（`smoke_loopback` / `e2e/test_run_rl` / `e2e/test_volume_e2e` / `tests/biz/test_metrics_shard` /
`tests/worker/test_bc_epoch_resume` / `tests/worker/test_shard_plan`）——A9 形状门实测把它们**响亮拒收**，
全部改 `POLICY_EXTRA_DIM`；④ `tests/worker/test_remote_iter_real_bun.py` 权重夹具从本机 `weights/*/`
（.gitignore ⇒ 换台机器整文件失败，且 v4 档位已被新判据拒收）改**仓内 golden**
（`tests/fixtures/student-golden-extra.json`）；⑤ `init_spatial_leg.py` 遗留 v4 文案（137/151）订正；
⑥ 课程/文档与本文的 `F1–F9` → `F1–F10`；⑦ `nn-training/e2e/test_run_rl.py` 的
`SCALAR_X_INDICES` 锁步行仍写 v4 的 `[15,18,29]`（**由全量 `nn-python-gate` 抓出** —— e2e/ 不在
平时的子集里，这正是「门禁要跑全量」的理由）→ 补 33=pickupRelX 并加一条翻转断言。 |

### 0.4 硬约束

- `OBS_SCHEMA_MAJOR` 4→5（常量在 `obs-encoder.ts:58`，`common/schema.py:46`）。RL 腿现产，无一次性重导成本；
  BC 存量本已 MAJOR=3 作废（`bc_corpus_identity_fp` 含 MAJOR ⇒ v4 起已被 D14 拒收）。
  ⚠ 源 replay 路径 **未核实**：本 plan 早稿写的 `nn-demo/c20-replays-20261001/replays/` 在本仓
  不存在（`nn-demo/` 在 `.gitignore`，工作树无此目录，全仓无第二处引用）——S0 inventory 必须
  先落地「BC 源在哪、还剩多少局」再谈重导，不许照抄死路径。
- `bun run check` 绿是交付定义；测试预算见 `nn-training/tests/test_source_text_assert_budget.py`
  双基线（FORM 1442/SRC 1091＋SANITY_FLOOR 防恒绿）——新用例用行为/返回判据，不写子串断言。

---

## 1. 目标、范围与两阶段

### 1.1 目标

给策略头补上"决策时不可从现有观测量推导"的 28＋10 维（extra 9→37，scalars 30→40），
并让 critic 与策略看同一份输入（value 吃 pf）。ladder 第一阶段通关率 **80% 毕业**，再转专场。

### 1.2 两阶段（丑话前置）

- **第一阶段 ladder**（与 x24 同课程、可比；课程 spec 见 §3-S8）：reward 照抄 x24 toy 公式，一字不动。
  空旷场上 corr/blk/wallDist 退化共线、基地 14 列恒常量——**G0 只证明"空旷场打到 80%"**，
  **不证明走廊/掩体/基地语义有效**，三者留专场重验。
- **第二阶段障碍/基地专场**（G0 过后另起账本）：obstruction（＋基地 tile (6,12)，lives 1）→
  经典 35 关（s4b 模板，lives 3）。转场转出按 §2.7 窄名单。专场门另起 plan 细化，
  本 plan 只冻结前置项（表 B）。

### 1.3 非目标（别再提）

记忆型时序维（`prev`/`hit`/`recentHit`，预算决定，门判决后另案，`prevMoveDir` 先例在
`types.ts:201-206`）；局时进度（缺归一化分母）；LSTM/帧堆叠；17ch 通道（备选 B）；
`near2`（实验二候选）。

### 1.4 设计原则（违反即返工）

- **P1** 只加不可推导的量。**P2** 每维声明 mirrorX，TS＋python 双侧实现；
  `mirrorSwap` 是本次新建（全仓 0 命中），落点见 §3-S3。
- **P3** 复用现有谓词，禁新建几何事实源；`alignedScan` 参数化＋零行为证明（§2.5）；
  跨层公式先搬迁进 `src/nn`（**两份都搬**：`sampleBasePressure` 4 处生产拷贝 + `nearestPickupDist`
  1 处；定义落 `src/nn/danger-metrics.ts`，工具侧 re-export 保持既有 import 面不变——
  `src/nn` 不得 import `tools/`，不搬就只能复制一份，那正是 P3 要禁的事）。
- **P4** 四向族进 extra，全局族进 scalars；value 全吃 pf（无用则网络自行闲置，不预剪）。
- **P5/P5b/P6** 见 §2.4（归一化/哨兵/cap/零分配）。

---

## 2. 冻结规格（实现唯一依据，照抄）

### 2.1 张量形状

`OBS_SCHEMA_MAJOR` 4→5。`OBS_CHANNELS=16`、`BOARD=26` 不动。
`POLICY_EXTRA_DIM` 9→**37**，`SCALAR_DIM` 30→**40**。
现有 extra 0–8、scalars 0–29 **序号与语义全不动**（只追加）。

### 2.2 POLICY_EXTRA 全表（0–36；F/B/L/R 均为**玩家相对**，前＝炮口朝向）

| idx | 名 | 定义 | mirrorX |
|---|---|---|---|
| 0–3 | `threatFront/threatBack/threatLeft/threatRight` | 各向瞄我的威胁计数（既有） | `[2,3]` |
| 4–7 | `hitDistFront/hitDistBack/hitDistLeft/hitDistRight` | 各向首个可命中敌距（既有；打不到 1.5） | `[6,7]` |
| 8 | `pincer` | 包夹度（既有） | 不变 |
| 9–12 | `corrF/corrB/corrL/corrR` | 各向连续可走**地形**格数 /8（步进伪代码 §2.5；持船失水已知局限） | `[11,12]` |
| 13–16 | `ttiF/ttiB/ttiL/ttiR` | 各向最近逼近无遮挡敌弹 TTI /60；无弹 1.5 | `[15,16]` |
| 17–20 | `laneEF/laneEB/laneEL/laneER` | 各向同轴 19px＋无遮挡敌数（不要求瞄准），`min(n,4)/4` | `[19,20]` |
| 21–24 | `blkF/blkB/blkL/blkR` | 各向首阻弹地形距离 /416；无阻弹 1.5（坦克不挡；砖/钢不区分） | `[23,24]` |
| 25–27 | `baseThreatU/L/R` | 老家上/左/右逼近敌数，`min(n,4)/4`（**世界坐标**，转身不变；D 向几何不可达） | `[26,27]` |
| 28–30 | `baseRingU/L/R` | 各侧完好比例 U/4、L/R/2（brick/steel 完好） | `[29,30]` |
| 31 | `baseThreatTTI` | 与 32–33 同一**指定敌**：到基地 TTI /300；无 1.5（曼哈顿格距×CELL ÷ 该敌 live `tank.speed`） | 不变 |
| 32–33 | `baseThreatRelX/Y` | 该威胁敌相对玩家的单位向量 | X 取负（`POLICY_EXTRA_MIRROR_NEG=[32]`） |
| 34–36 | `baseEnemyDistU/L/R` | 各侧最近**敌车**曼哈顿格距 /12；无敌 1.5（锚点/侧判定见 §2.5，与 s38 同一份公式） | `[35,36]` |

### 2.3 SCALARS 全表（0–40；s33 取负，其余 X 分量见 `SCALAR_X_INDICES=[15,18,29,33]`）

0 `slack`（杀敌宽裕度）· 1 `baseDeadline`（基地威胁倒计时）· 2 `lives`（/3）· 3 `level`（星级）·
4 `fireProgress`（充能）· 5 `turnCooldownRemaining`（转向冷却）· 6 `ringCompleteness`（环墙比例）·
7 `enemiesOnField` · 8 `spawnQueueRemaining` ·
9–13 `tier_none/rookie/soldier/veteran/commander`（敌方构成）·
14 `nearestEnemyDist` · 15 `nearestEnemyRelX`（取负）· 16 `nearestEnemyRelY` ·
17 `nearestBaseDist` · 18 `nearestBaseRelX`（取负；y 分量缺失由 s37 补）·
19 `playerHp` · 20 `playerShield` · 21 `freeze` · 22 `stuck`（卡住计时）· 23 `boat` ·
24 `baseHp`（无基地恒 1.0；系 World 装载满血，与 `encodeBase` 无关）· 25 `fence` · 26 `score` ·
27 `emp` · 28 `iceVy`（=`vy/speed`，不取负）· 29 `iceVx`（=`vx/speed`，取负）·
30 `moving`（`player.moving?1:0`；顶墙推 vs STOP 的区分位，差集见附录 A）·
31 `myBullets`（玩家本人在飞弹数，`allegiance==='player'`，`min(n,3)/3`＋p99 规则）·
32 `pickupDist`（`!alive?0 : raw<0?1.5:raw/50`；玩家死亡取 0，跟随 extra"无玩家全 0"纪律）·
33 `pickupRelX`（取负；无/死亡=0）· 34 `pickupRelY` ·
35 `wallDist`（到边界格距 /13，B 类）· 36 `cornerDist`（到角锚点切比雪夫 /10）·
37 `baseRelY`（=`dy/d`，补 s18 缺 y；无基地=0）·
38 `basePressure`（=`sampleBasePressure` 同公式，无基地=0，显式 `allegiance==='enemy'` 过滤）·
39 `nearestEnemyApproach`（最近敌人**径向接近速度**：设玩家中心 P、最近敌中心 E
（**与 s14 同一敌**，同循环同 `hypot` 度量、`strict <` 首个最小者胜）、敌速度 V=`tank.vx/vy`、
u=(E−P)/|E−P|，`approach = −(V·u)/3` clamp [−1,1]（V 朝玩家为正；/3 覆盖各兵种速度，
fast 自然读高，无需分兵种归一）；无存活敌/玩家死亡/d=0 ⇒ 0；mirrorX **不变**。
**分母说明（勿当 bug）**：各兵种 live 速度 ≤1.25 px/tick（`config/speed.ts`：basic 3.75cps→1.00、
fast 4.50→1.20、玩家 3★ 4.6875→1.25），故 `/3` 的读数峰值 ≈0.4——`/3` 是**刻意留余量**
（跨难度/未来提速不越界），不是满量程归一；紧归一（÷1.25）会把 fast 顶到 1.0 而丢掉 headroom。
局限：只看最近一个；只看径向（纯绕侧切向读 0，罕见）。fallback"四向接近速度×4"记在案，不加）。

### 2.4 归一化 / 哨兵 / cap / mirror / 指纹

| 类 | 归一化 | 无目标 | 真值域 |
|---|---|---|---|
| 距离类 | 见各维分母 | **1.5** | clamp [0,1]，不许取到 1.5 |
| TTI 类 | 见各维分母 | **1.5** | clamp [0,1] |
| 计数类 | `min(n,cap)/cap`（敌 cap 4＝`MAX_ENEMIES_ALIVE`；`myBullets` cap 3，p99≤2 则实现时取 /2） | 0 | [0,1] |
| 单位向量类 | `dx/d` 欧氏（s32 曼哈顿不同度，各自有用） | (0,0)（d=0/无目标/玩家死亡） | [−1,1] |

mirror：swap 9 对（`[2,3][6,7]`＋`[11,12][15,16][19,20][23,24]`＋`[26,27][29,30][35,36]`）；
取负 extra `[32]`、scalars `SCALAR_X_INDICES=[15,18,29,33]`。
指纹末段冻结为 `dim|names|swaps|neg` 四字段。
热路径：零分配；`world.allTanks` 每 encode 取一次传引用，玩家族与基地族共用。

### 2.5 谓词与公式（A2/A3/A5 的独立重实现以此为准）

- **`alignedScan(cx,cy,colLow,colHigh,rowLow,rowHigh)`**（`alignedEnemyScan` 参数化后）：
  sameCol 优先、单计数 ⇒ **Σ桶 ≡ `alignedEnemyCount`**；"逼近"纯位置语义，不加速判断。
  基地分桶：`col<12⇒L、col>13⇒R、跨度内且row<24⇒U`（2×2 跨度 colLow=12/colHigh=13/rowLow=24/rowHigh=25；
  mirror 下 L↔R 互换、U 自守，规则对称）。
  **参数语义（实现前必须对齐，防两套读法）**：`(cx,cy)` = 被瞄目标的**像素中心**，对齐判据仍是
  「中心距 < 19px」的固定带（**不是**格跨度包含）；`colLow..rowHigh` = 遮挡 raycast 的**目标体跨格**
  （玩家＝自身身体格，基地＝2×2 足印）。**侧判定**（`baseSideOf` / laneE 桶）统一为世界方位：
  若竖直对齐 ⇒ `srcCy<cy ? U : D`，横向对齐 ⇒ `srcCx<cx ? L : R`；`Σ四桶 ≡ 总计数` 是恒等式
  （每源恰落一桶），A2/A3 直接钉它。基部 2 格宽 ⇒ 在 19px 带内的源其格号必落 U/L/R 三桶之一
  （`col<12`/`col>13`/`row<24` 只是该规则的格坐标表述），`D` 向被网格边界封死（基地底行＝25）
  ——**不存在不可归属的源**。
- **`blk` 外向 raycast**：起点＝玩家中心格沿该向下一格，步进 1 格，`blocksBullet`（brick/steel/base 挡，
  水/草/冰过），首阻弹格距离 px/416 或出界 1.5；坦克一律忽略。
- **`corr` 步进**：起点按 `canStep` 同规 snap；沿该向步进 1 格（CELL），查左上 TANK 矩形；
  首命中（出界即 true）停；`corr=(k−1)/8`。
- **`pickupDist` 换算**：`!alive?0 : raw<0?1.5:raw/50`（raw＝`nearestPickupDist` 格距，哨兵 −1，
  定义**搬进** `src/nn/danger-metrics.ts`，`export-rl-rollout.ts` re-export）。s33/s34 的选取规则与它
  同源（中心格曼哈顿、`strict <` 首最小者胜、哨兵 −1），向量本身按像素中心欧氏（与 s15/16 同规）。
- **`basePressure`**：`sampleBasePressure(world, enemiesOnly=true)`——**公式唯一实现**落
  `src/nn/danger-metrics.ts`，工具侧 `enemiesOnly=false`（默认）保持 telemetry 逐字节不变。
  ⚠ 口径澄清（**不是**「逐字照抄」）：原函数**无阵营过滤**（对含玩家在内的 `world.tanks` 取 max），
  观测侧必须**只认敌车**（`allegiance==='enemy'`），否则玩家自己贴家会把 s38 顶满、把基地威胁
  读成「我在守家」。`baseEnemyDist×3` 用**同一份敌车过滤 + 同一锚点**（`BASE_POS` 左上格↔坦克中心格
  floor），故 `min(三侧格距) = (1−s38)×12` 恒等式成立（A5 钉它）。

### 2.6 网络结构（主腿腿 A；腿 B 参考；intent/goal 不动）

| 档 | policy 输入 `pf` | value 输入 | 旧 value 宽 |
|---|---|---|---|
| legacy | hidden 128 | 128（不动） | 128 |
| 腿 A | hidden 128＋extra 37 ＝ **165** | 同 pf，**165** | 128 |
| 腿 B | tower 112＋scalars 40＋extra 37 ＝ **189** | 同 pf，**189** | 128 |
| intent/goal | hidden 128＋inject 9 ＝ 137（不动） | 同 pf，137 | — |

参数（执行验算；基线 70,216／70,279／85,345）：legacy 71,496（＋1280，＋1.82%）／
腿 A **71,792**（fc 1280＋move 140＋fire 56＋value 37＝＋1513，＋2.15%）／
腿 B 86,952（fc 1280＋move 190＋fire 76＋value 61＝＋1607，＋1.88%）。
初始化：新列 `normal_(0,0.01)`（经验值；`kaiming_uniform_(nonlinearity='relu')` 默认 gain √2，
student 无 gain 规范；zero-init 只留给专场基地列）。
golden：165/**189** 两档（TS 逐字节对；137 不动）。
`infer.ts`：判据改**档位**（`intentHeads`→137／`spatialTower`→189／`policyExtra`→165／legacy→128），
长度不符 throw；extra 硬编码 9 共 6 处（`:413/:495/:498/:500/:550/:559`，`:495` inject 不动）；
`:570` 静默置零只许留给无 value 头 BC 权重且须计数/warn。

### 2.7 迁移与转场

本腿转出（x24 峰 = **burst-it220**，sha16 `caf9e8749fdad70d`，DECISIONS
`§2026-10-12-goalnn-burst220-crown`；`nn-training/worker/scripts/init_spatial_leg.py` 列迁移；
`load_state_into` 整块跳过语义不动）：fc [128,94]→[128,104] 拷 94＋init 10；
move/fire [137]→[165] 拷 137（含 extra9 旧语义）＋init 28；
value [1,128]→[1,165] 拷首 128 列（h 同语义）＋init 37；单测（旧列逐位等、新列尺度≤0.01）。
腿 B（如训）：spatial 主干拷；move/fire **分段**搬迁（**不是**平铺拷 151！旧
`[tower 0–111 | scalars 112–141 | extra 142–150]` → 新 `[tower 0–111 | scalars 112–151 | extra 152–188]`：
拷 [0..111] + 拷 [112..141]（scalars 同位）+ 旧 extra [142..150]→新 [152..160]，其余 init
＝ scalars s30–39 [142..151] + extra 新 [161..188]）；平铺拷 151 会把旧 extra 灌进 s30–s38 = 静默错位。
value [1,128]→[1,189] **全重 init**（旧吃 h，新吃 tower 起始的 pf，无对应前缀）。
专场转出（G0 过后）：zero-init **extra 25–36＋s37/s38**（腿 B 追加 pf 两列），其余拷；
旧 5 维（s1/s6/s17/s18/s24）接受分布突变（新 out/traj、opt fresh、不续跑）。
单测两条：基地列贡献 0；非基地列逐位等于 ladder 终点。
**闲置语义**：value 新列无用则训练中学小（37 参数），不预剪。

---

## 3. 实施步骤（按序；上一步绿了才进下一步）

> 施工状态（2026-10-12，agent 侧）：**S1–S6、S8 已落盘**（S7 见本节注）；**S0 的 inventory 与
> 现役腿冻结、S9 的开课/训练/判门由人执行**。各步的产物：`src/nn/{policy-extra,obs-encoder,infer,danger-metrics}.ts`、
> `nn-training/common/schema.py`、`nn-training/worker/{models/student.py,ppo/np_core.py,data/{mirror.py,npyio.py},scripts/{init_spatial_leg.py,validate_export.py}}`、
> 双侧测试与 `nn-training/curricula/x25-obs-v5.jsonc`；双端门禁绿（`bun run check` + `nn-python-gate`）。

### S0 前置：inventory＋冻结现役腿

- 输出 inventory（shard 总数/GB/墙钟；BC／demo bank／rollout 去向；确认本腿现产、无存量依赖）。
- 现役腿（x24-stack7-dist/burst）跑完并冻结权重。换代顺序：停 worker → 升 hub → 升 worker。

### S1 schema 常量双端（预期双端指纹测试**先红**）

- 改 `src/nn/obs-encoder.ts`（`OBS_SCHEMA_MAJOR` 4→5、`SCALAR_DIM` 30→40、`SCALAR_NAMES`＋s30–s39、
  `SCALAR_X_INDICES`＋33）与 `src/nn/policy-extra.ts`（`POLICY_EXTRA_DIM` 9→37、NAMES、SWAPS＋7对）。
- 改 `nn-training/common/schema.py`（同值＋`EXTRA_MIRROR_NEG=[32]`）。
- 完成标准：双端指纹测试同红（其他测试不动）。

### S2 谓词重构＋公式搬迁（A3）

- `alignedEnemyScan` → `alignedScan(cx,cy,…)`（§2.5 参数语义），旧函数变薄封装（行为逐位不变）。
- 搬 `sampleBasePressure`（4 处生产拷贝：`rl-reward.ts` 定义 + `simulation-runner` + `export-eval-game`
  + `export-rl-rollout`）与 `nearestPickupDist`（`export-rl-rollout` 1 处）进 `src/nn/danger-metrics.ts`；
  `rl-reward.ts` / `export-rl-rollout.ts` 改 re-export，既有调用点 import 面不变。
- 完成标准：重构/搬迁前后随机 200 局面逐 tick 逐位相等；Σ四桶≡总数。

### S3 编码器＋extra＋mirrorSwap 新建（A1/A2/A4/A5/A8）

- `computePolicyExtra` 加 idx 9–36、`encodeScalars` 加 s30–s39（含 s39 公式与同敌规则 §2.3）。
- 新建 `mirrorSwap`（`policy-extra.ts`：extra swap＋neg；`obs-encoder.ts`：scalars 编排）。
  **消费方声明**（防「只在测试里存在」）：TS 侧镜像变换只服务 A4/A4b 的双端对账与
  `spatial-probe-*` 类探针；生产增广仍在 python（`worker/data/mirror.py`）。它必须**与 python 侧同表**
  （共用 `POLICY_EXTRA_MIRROR_SWAPS` / `EXTRA_MIRROR_NEG` / `SCALAR_X_INDICES`），故不能留在测试文件里。
- 单测：A2（corr 独立重实现 200×4）／A4（ladder＋obstruction＋骑线合成局；ladder 基地族只做恒常量形状检查）／
  A5（pickup/s6/s38 逐行相等）／A8（tti/laneE/blk/pickup/base/s39 golden，先红后绿；
  两个 1.5 语义各一条；s39 逼近正/远离负/无敌零＋同敌断言）。
- 完成标准：A1 双端同绿。

### S4 推理＋模型＋golden

- `infer.ts` 档位判据＋6 处硬编码（§2.6）；`student.py` value 按档显宽＋`_init_value_head`；
  golden 出 165/**189**；intent/goal 137 不动。
- 改 `policy-extra.ts:4` "critic 不动"（已变假）、student 头/docstring、`infer.ts:550/559` 消息。
- 完成标准：双端 golden 逐字节对。

### S5 转出脚本＋A10

- `init_spatial_leg.py` 列迁移（§2.7 映射）＋单测；转出产物命名如
  `nn-training/weights/x25-obs-v5/x25-obs-v5.it0.json`（`arch` 带 `policyExtra:true`，单一来源）。
  ⚠ `nn-training/weights/**/*.json` 在 `.gitignore`（大件不入库）⇒「冻结现役腿权重」= 拷进
  `backup_dir` + 记 sha16 进本 plan/课程注释，**不是** commit 一个 json。
- 完成标准：A10（固定局面与 x24 终点逐位比对，非新增列路径一致）。

### S6 防护与校验（A9）

- `np_core.load_shard_fields` 补形状判据＋单测；`validate_export.py` 的 `EXPECT` 表补 `extra.npy`
  （现状**只列** obs/scalars/actions/masks/conditions 五件，`scalars` 那行在 :36）并核一遍形状来源；
  确认主闸 `corpus_fp`＋D14、BC 侧 `verify_shard_schema` 响亮拒收旧 MAJOR（形状 + 指纹双判）。
- 完成标准：A9（9 维旧 shard 在 RL 路径被拒，不是静默错位）。

### S7 开销探针（A6；超线砍维，不重议）

> 施工状态：② **粗测已跑**——每局墙钟中位数 732 ms（v4 基线树）→ 741 ms（本树）＝ **+1.2%**
> （线 +5%），未触发砍维；①③④ 由人开课前补（真语料量法）。

- ① encode 端到端＋分维计时＋myBullets p99（≤2 则实现取 /2）；② 每局 rollout 墙钟（≤+5%）；
  ③ BC 切片计时；④ 三组新维 ladder 方差/相关（|r|>0.8 ⇒ G3 降级，预注册）。
- **砍维序**：禁砍（corr/blk/pickup×3/cornerDist/基地族/s38/s39）；
  可砍 myBullets→wallDist→laneE×4→tti×4。顺序：探针→砍维→实现→重导。

### S8 课程文件＋启动（**人执行**；agent 只给文件）

> 施工状态：文件已落 `nn-training/curricula/x25-obs-v5.jsonc`（真 loader 可开：`course_openable=True`；
> reward 块与 `x24-stack7-burst` 逐字相同；`paired_rotate_seed=20261020` 新流）。
> **开课仍需人**：先跑 S5 转出 `nn-training/weights/x25-obs-v5/x25-obs-v5.it0.json`（命令见课程头注）。

新课程文件（如 `x25-obs-v5.jsonc`）：`"level": "ladder-c20-lives1"`（环境键全在 level 内，
课程**不得**重复 stages/difficulty/max_ticks/player）＋ `"stages"/"eval_stages": "2000-2003"` ＋
`"seed_rotate": 150`（与 x24 同值）＋ `"paired_rotate_seed"` 新 V（开腿前冻结）＋
`"reward"` 照抄 x24 toy 公式＋ `"bc"` 指 S5 转出产物＋新 `out`/`traj`
（如 `x25-obs-v5` 系新路径＋`backup_dir`，与现役腿账本隔离）。
起点纪律：起点播种／`manifest.it == plan.start_it + 1`／rotateSeed 核对／禁 `bc=` 非 resume
（`model_build.py:146-179` 不传 extra 必崩）。

### S9 训练→门→专场（人执行）

ladder 训练 → §5 门 → G0 过 ⇒ 专场（课程 obstruction＋基地，B1–B4；转出按 §2.7 窄名单）。

---

## 4. 验收

### 表 A（本腿 DoD，ladder）

（状态：**agent 侧已落盘**；[x] = 已按需求证，证据在行内；A6 为**粗测**；S9 由人。）

- [x] A1 双端指纹同红后同绿（dim 37/40、NAMES、9 对 SWAPS＋`EXTRA_MIRROR_NEG=[32]`）。
      ⇒ `tests/nn/schema-fingerprint.test.ts`（TS 锚：四字段末段）＋
      `nn-training/tests/common/test_schema_fingerprint.py`（python 锚：同指纹 + 布局名逐条）。
- [x] A2 走廊自洽（§2.5 独立重实现，200 局面×4 向）＋地形版 canStep 对照。
      ⇒ `tests/nn/obs-v5-vector-gap.test.ts` A2 组（corr 独立重实现 + 32px 整步 ⟺ `canStep`）。
- [x] A3 重构＋搬迁 parity（逐 tick；Σ桶≡总数）。
      ⇒ 搬迁为 re-export（`src/nn/danger-metrics.ts` 单一实现，工具侧 import 面不变），
      parity 由既有门承担：`tests/sim/eval-game-parity.test.ts`（零事件局逐字段相同）、
      `tests/sim/metrics-v11-hotlane.test.ts`、godai-score 门——`bun run check` 全绿；
      恒等式「Σ四桶 ≡ 总数」钉在 `tests/nn/policy-extra.test.ts`（laneE ⑷）。
      ⚠ 跨版本逐 tick 字节对账在 schema bump 后**不可做**（obs/scalars 宽度已变，两侧权重不同源），
      故 A3 的判据 = 「搬迁零行为 + 既有 parity 门全绿」。
- [x] A4 mirrorX（新建 `mirrorSwap`）：逐维相等；fixture 见 §3-S3。
      ⇒ TS：`policy-extra.ts::mirrorSwap`（9 对 swap + NEG 取负）＋ `obs-encoder.ts::mirrorScalars`（X 索引）；
      消费方 = A4 双端对账与 `spatial-probe-*`（生产增广仍在 python）。
- [x] A4b python 侧：`mirror_x` vs TS 新建变换逐维相等（含 scalars 33；测试手改 15/18/29＋33）。
      ⇒ **施工时抓到真缺陷**：`mirror_x` 只走 swaps、漏 `EXTRA_MIRROR_NEG`（extra 32 符号错）——
      已补 + `tests/worker/test_dataset_mirror.py` 加独立重实现用例（未改代码前先红）；两表同源由指纹钉住。
- [x] A5 口径对账（**逐行**）：pickupDist（`!alive?0:raw<0?1.5:raw/50`）；s6（满环前提）；
      s38（无基地=0，显式过滤；与 `baseEnemyDist` 同锚点算法）。
      ⇒ `tests/nn/obs-v5-vector-gap.test.ts` A5 组（四条，含 `(1−s38)×12 = min(三侧格距)` 恒等式）。
- [~] A6 开销探针（§3-S7 四项＋砍维序）。**只跑了 ② 且为粗测**：同场景（stage 0 / seeds 1–3 /
      1200 ticks / lives 1 / 同一个 h64-d8 夹具模型）在 v4 基线树与本树各 3 遍，每局墙钟中位数
      732 ms → 741 ms（**+1.2%**，线 = +5%）⇒ 未触发砍维；①③④ 与真语料长跑**未做**（人开课前补，
      真语料量法见 §16.1）。
- [x] A7 reward 公式一字不动。⇒ TS 侧 `rl-reward.ts` 只把 `sampleBasePressure`/半径改为 re-export
      （公式与 weights 未动，diff 可核）；x25 课程文件 reward 块与 `x24-stack7-burst` **逐字相同**
      （用真 loader 对账 model_dump 相等）。
- [x] A8 逐维 golden（含 s39 三条＋同敌断言；双 1.5 各一条）。
      ⇒ `tests/nn/obs-v5-vector-gap.test.ts` A8 组（s30/s31/s35+s36/s39）＋
      `tests/fixtures/student-golden{,-extra,-tower,-wasm}.json` 重出为 128/165/189（双端逐字节对）。
- [x] A9 shard 门（BC 响亮拒收；RL 补判据；主闸 corpus_fp＋D14）。
      ⇒ `np_core._RL_SHARD_SHAPES` + 形状判据（`load_shard_fields`，不符即 ValueError）、
      `validate_export.EXPECT` 补 `extra.npy`、BC 侧 `npyio.verify_shard_schema` 原有 extra 形状判据；
      `nn-training/tests/worker/test_shard_schema_shape.py`（新旧 schema 均验，批以行为判）。
      实测：本轮把 6 处仍写 9 维 extra 的合成 shard 写入器**红着抓出来**（见 r10③）。
- [x] A10 迁移探针（非新增列路径一致＋列迁移单测）。
      ⇒ `nn-training/tests/worker/test_init_spatial_leg.py`（legacy 全量过、腿 A 旧列逐位、
      腿 B 分段映射、形状不符拒绝、三档互斥/无档拒绝）。

### 表 B（专场前置，G0 过后细化）

B1 RING 对齐（`hasBase()=true`；U 墙逐格相同已复核）／B2 terminal `'base'`／
B3 ticks（12000 vs 12900）／B4 zero-init 窄名单＋level-key（含 `2000+i` 复用）。

---

## 5. 预注册门

### 维类三行表

| 类 | 维 | 先验 |
|---|---|---|
| **A 打通已定价量** | `pickup×3`、`cornerDist`、s38、`baseRing×3`、`moving`(wBlock) | 无 |
| **B 动作可执行性** | `corr×4`、`wallDist` | 无 |
| **C 感知增维** | `tti×4`、`laneE×4`、`blk×4`、`myBullets`、s39（切向/多敌为 fallback） | 三次阴性＋残余 |

D08"不定价"域=奖励公式，G1 探索读数。起点纪律见 §3-S8。

- **G0 毕业（主）**：ladder 通关率点估计 ≥**80%** 且 CI 下界 ≥75%（800 局起，池外段；地板内加 1600）。
  过 ⇒ 专场；不过 ⇒ 冻结，教训节。**G0 只证明空旷场 80%，三语义留专场重验**。
  **阈值出处**（必须有，防被当成教师天花板）：80%/75% = **用户指令**（§0.3 r6：「ladder 先行＋G0 80%」）
  ＝产品侧 DoD，不是从教师/NN 表现反推（AGENTS §0.2 四条禁令只管「用教师表现定 DoD」）。
  起点参照（**只作 Δ 的锚，不判门**）= burst-it220 日常 ~0.51 / 拼池 0.5550。
- **G5 总账（次）**：通关率/杀敌任一显著正且另一不显著负（vs 零训练对照，同段同 seed）。
  ⚠ 名为「总账」但判据是**非劣**（允许一项非显著变差）⇒ 只作「训练有没有主动打坏」的护栏，
  **不得**用它替代 G0 的毕业读数；过门以 G0 为准（三门全过才转专场）。
- **G3 撞墙（主，B 类）**：W07 −30%（CI 上界<0）；A6 |r|>0.8 则降级为导航能力判据（预注册）。
- 探索 G1/G2/G4（C 类）：类内归因，不计过门。
- **过门规则**：G0＋G5＋G3 全过 ⇒ 专场；任一 miss ⇒ 冻结；归因顺序：A10/零训练（创伤？）→
  残余（wHurt/wNearSq，见附录 A）→ 类无效。

---

## 附录 A. 背景：奖励对账与症状映射

x24 公式 17 项：9 项已审（`wApproach`/`wPickup`/`wCorner`/`wEncl`/`wThreat`/`wHurt`/`wNearSq`/
`wDist`(出局)/`wBlock`，覆盖度与残余见下）＋8 项有对应物未列（战果 `wKill`/`wHit`/`wDmg`/`wMiss`／
命中率 `wAcc`／拾取分型 `wFrFreeze`/`wFrBomb`/`wFrStar`；9 个已列权重逐字一致）。
wBI/wBS（s4b `:36-44`，0.25＋0.25＝50%）是专场目标：s6 同源精确＋`baseRing×3` 方位；
s38 精确＋方向族转向。

| 症状 | 人 | NN 早崩51 | 全局160 | 靠哪个新维 |
|---|---|---|---|---|
| D08 必死弹躲开率 | 0.648 | 0.417 | 0.509 | `tti×4`（观测杠杆；不定价域=奖励公式，G1 探索） |
| D05 挨打后还贴脸 | 26.3 | 128.7 | 80.1 | `tti×4`+`laneE×4`+s39（逼近读数）＋`stuck`；记忆面另案 |
| D04 挨打后脱离延迟 | 59.5 | 89.4 | 97.7 | `corr×4` |
| D03 危险格时间 | 87.0 | 143.4 | 98.9 | `tti×4`+`laneE×4`（wHurt 多弹残余） |
| W07 撞墙/W08 顶住/W09 | 45.4/3.0/0.063 | 111.6/16.7/0.126 | 168.4/20.5/0.178 | `corr×4` |
| W10 原地打转 | 0.183 | 0.589 | 1.055 | `stuck`+`corr×4`部分覆盖；`prev`已删，缺口待门判决 |
| A07 远射占比 | 0.346 | 0.237 | 0.228 | `blk×4`（砖/钢不区分；空旷场退化，掩体语义留专场） |
| wApproach/wPickup/wCorner | — | — | — | `pickup×3`/`cornerDist`/`wallDist` |
| wBS（专场） | — | — | — | s38＋方向族 |

信息丢弃根因（S14 钉死）：守卫弹 `:331` 跳过，owner=1 玩家弹独占，GAP 才是根因（s31 谓词 `'player'`）；
顶墙推 vs STOP 在 s28(`vy`)/s29(`vx`) 逐位相同，冰面滑行反例 ⇒ s30 保留。

## 附录 B. 治理

`docs/nn/engineering.md` 否决"改 SCALAR_DIM…违反 critic 输入集不动"，自本 plan 起**推翻**
（训练侧属 AGENTS §0.2 允许域；根因正在于 critic 盲区）。DECISIONS
`§2026-10-12-goalnn-obs-v5-critic-superset`（**本 plan 施工同批落条**，正文见该条目）；
engineering.md 原文不动，由此引用覆盖。
⚠ 评审发现（2026-10-12）：早稿写的 `§2026-10-12-obs-v5-critic-superset` 从未落条（全仓 0 命中）
⇒「推翻既有否决」当时没有授权链；本次施工第一件事就是补条目，再动代码。

## 附录 C. 评审处理对照（过程记录，冻结）

- ms F1–F18：r2 落（删 free/prev/hit/recentHit；对账；拆表 A/B；预注册门）→ r6 注（标题 37/39→r7 改 37/40；单腿全吃表；窄名单转专场；G0/G5/G3）。
- ds R1–R20：r3 落（s31 阵营；三向基地；inject/extra 拆档；维度数）→ r6 注（单腿档位表，value 165；基地回 bump；A6＋0.8；搬迁 5 份；NEG 本 bump）。
- s5 S1–S20：r4 落（zero-init 收窄；坑 0；mirror neg＋A4b；砍维序）→ r6 注（S1 名单转专场；S2 单腿映射；S6β 腿取消记 fallback；S10 保留）。
- sb 全条：r5 落（P0 地基→拆腿；P1 mirrorSwap 新建／参数账／标题／A9 静默；P2 全部订正）→
  r6 注（单腿回场系**指令选择**；基地回 bump；参数账重算）→ **r7 注（s39；value 不动；腿 B 189）** →
  **r8 注（重组实施体系；修复腿 B 188→189 及参数构成 double-count、症状映射表回场、课程 spec 具体化、s39 同敌规则）**。

## 附录 D. s39 修正案（r7 问答纪要）

位置（ch7-10＋s14-16）与朝向（通道值个位）躯干可见；GAP 后头只剩方向级；
extra 六组四向维＝方向盘（laneE−threat 差集即绑定）；塔＝区域（未启用）；
向量不再加（越权）；缺的是敌方速度 ⇒ s39 单维（径向）。兵种速度差含在绝对单位里，
无需分归一。局限：只看最近一个、只看径向；fallback 四向接近速度×4。
