# obs schema v3 编码规格（obs-schema-v3.plan.md v4.0）

状态：**v4.0 — 实施规格**（体系化重构：内容 = Draft v3.4 经四轮评审全部定案的最终态，去除修订层；
实施者照 §3 表格直接编码，勿再考古）。上位总纲 = `plan/nn-ladder-roadmap.md` v2.0 §3-N1。
变更历史压缩见 §7，逐条证据：`plan/v3.review-{dsf,ms,hy}.md` + `plan/archive/obs-schema-v3-v3.4.md`。

---

## 0. 目的与机制依据

**NN 观测里没有玩家的血、敌人的剩余血量、冰冻/无敌/卡死状态**——人一眼能看到、NN 看不到。
当前 v2 = 14 通道 grid + 19 标量，无任何 hp 观测，导致「残血要躲 / 先补刀残血敌人 / 敌人冻住
了安全清场 / 盾期间压上去」全部无梯度可学。

**机制依据（hy R1）**：1 命课程玩家 `maxHp≈263 / damage≈105 ⇒ hits-to-kill≈3`——HP 是持续变化
的剩余生命隐藏态，当前 value 函数原理上无法预测「还能挨几下」；与已诊断瓶颈（value 欠拟合）
是同一件事的两面。观测补全同时服务策略与 value 头。本方案 = **一次 schema major bump 全部
加完**（阶梯中途零 bump，v4 唯一窗口 = 经典缺口复盘，见总纲 D8）。

## 1. v2 现状（基线）

| 项 | 值 | 来源 |
|---|---|---|
| OBS_CHANNELS / SCALAR_DIM / MAJOR / BOARD | 14 / 19 / 2 / 26 | `schema.py:14/22/31`、`obs-encoder.ts:42-45` |
| grid 通道含义 | 0-4 地形 / 5 base / 6 self / 7-10 敌 / 11 bullet / 12 powerup / 13 waveHeat | `schema.py:34-49` |
| 敌机值 / 子弹值 | `(tier<<3)\|(d+1)` 1-36；`d+1`（敌）/`d+1+4`（我，**加法**） | `obs-encoder.ts:221/231` |
| SCALAR_X_INDICES | [15, 18] | 两端同 |
| obs 缓冲类型 | **Uint8Array**（赋值 ToUint8 截断）；标量 Float32Array | `obs-encoder.ts:120-121` |
| 权重兼容 | major bump ⇒ 全旧权重失效重训 | `weights_io.py` 断言 |
| **现存缺陷** | waveHeat 硬编码 3 点（`counts[i%3]`/`i<3`），arena 实为 4 点 ⇒ 第 4 点热量丢失+轮转错位 | `obs-encoder.ts:261-267` vs `arena4.jsonc:48-53` |

## 2. 缺口清单（v3 全部补齐的定案依据）

**C 组（当前课程即缺）**：C1 玩家 HP（`Tank.hp/maxHp`）· C2 玩家盾（shieldTimer，道具盾会增至
20000 须先 min RESPAWN_SHIELD_MS=3000）· C3 敌冰冻（world.freezeTimer）· C4 卡死（World 无
字段，reward 由导出器自算 `export-rl-rollout.ts:653-659` ⇒ 须新增 World 字段）· C5 敌剩余命中
数（所有种类；pool：basic 250/105=3 发、fast 150=2、power 200=2、armor 350=4；instant
hp=hits×100 自洽）· C6 生成中敌人（encodeEnemies `continue` 跳过，`obs-encoder.ts:215`）·
C7 船（boatTimer）· C8 奖励车 `tank.bonus`（modern `bonusEnemyEveryNSpawns=4` 每局约 5 台，
打死掉道具 = wPickup 经济源头；classic 同 flag `fixedDropKillIndices [4,11,18]`）· C9 场上道具
剩余寿命（`POWERUP_TIMEOUT_MS=20000` despawn `SimulationPowerUps.ts:343`）· C10 弹速档
（projectileSpeed 40/45/50/70 按兵种，单帧不可推）。

**A 组（classic 预留）**：A1 base HP（`World.ts:246-247`；classic 1 血 `CLASSIC_BASE_MAX_HP`，
其余 120）· A2 fence（`fenceExpireFrame`）· A4 冰面动量 vx/vy（经典冰关 17/24/28/32；
`Tank.vx/vy` 冰面 glide 是单帧不可推隐藏态）。

**一次性原则**：即使当前课程恒 0/恒值也一并预留——schema bump 幂等，一次加满是唯一避免
「进 classic 又重训」的办法。

## 3. v3 目标布局（定稿）

### 3.1 总维度与常量

`OBS_CHANNELS = 16`、`SCALAR_DIM = 30`、`OBS_SCHEMA_MAJOR = 3`、`BOARD = 26`、
`SCALAR_X_INDICES = [15, 18, 29]`。

### 3.2 grid 通道全表（ch0-ch15）

| ch | 内容 | 编码（定稿） | 值域 | mirror |
|---|---|---|---|---|
| 0-4 | 地形 brick/steel/water/forest/ice | 存在=1（steel=边界钢环，阶梯恒非零常量） | 0/1 | 无关 |
| 5 | base 鹰(2)+护圈(1) | `hasBase` 守卫保留（幻影基地修复：无基地全 0） | 0/1/2 | 无关 |
| 6 | self | `(star<<3) \| (d+1)`，star=min(level,3) | 1-28 | DIRECTION_CHANNELS |
| 7-10 | 敌 basic/fast/power/armor | **`(bonus<<6) + (tier<<3) + (d+1)`**（v3.2 A1：bonus 在 bit6，不得用 bit4——与 tier 字段逐位碰撞） | 1-**100** | DIRECTION_CHANNELS（低 3 位翻转，高位保留） |
| 11 | bullet | **`(speedBucket<<3) + (owner<<2) + (d+1)`，全程加法禁字面 OR**（d+1=4 占 bit2 与 owner 重叠）；**混合基记法（位权 8/4+加法），不是位域**。弹速（v4.0 实施勘误）：真源 = `bulletSpeedCps` 表（config/speed.ts），**非** profile.projectileSpeed（{40,45,50,70} 是能力维度、与实际弹速脱钩——dsf B5 前提修正）；桶按 live px/tick 序数划分，边界 [3.7,3.9,4.1]（modern armor 3.60/power 3.80/basic 4.00/fast+player 4.20-4.60；classic 重弹 8.0 落最高档）。**解码注意**：rest∈1..8 溢出 bit3 ⇒ sb=(v-1)>>3（hy E6 伪码同错，镜像单测先于实现抓到） | 1-**32** | DIRECTION_CHANNELS——**32 项静态 LUT**（见 §3.4） |
| 12 | powerup | `(lifeBucket<<4) \| (typeIdx+1)`，`lifeBucket = min(3, floor(剩余ms/5000))`（dsf 复核无冲突：typeIdx+1≤15 占位 0-3） | 1-**63** | 无关 |
| 13 | waveHeat | **N 点通用**：`counts` 按 `world.enemySpawnPoints.length` 轮转分配（修 v2 三点硬编码缺陷）；`counts` 提**模块级复用缓冲**（§14.1：现为每次 encode 分配） | 0..proj | 无关 |
| 14 | 敌剩余命中数 | `min(9, ceil(enemy.hp / player.damage))`——**每帧从 live player.damage 现算、禁缓存**（伤害随星级变，`combat.ts:334-355`） | 0-9 | 无关 |
| 15 | 生成中敌人 | **`Math.round(255 * clamp01(spawnTimer / 1000))`**（v3.4 E1：Uint8 截断，浮点直写恒 0） | 0-255 | 无关 |

### 3.3 标量全表（s0-s29）

| s | 内容 | 编码（定稿） | 备注 |
|---|---|---|---|
| 0 | slack | min killSlack/600，无敌=1 | 现存 |
| 1 | baseDeadline | min deadline/600；无基地=0 | 现存 |
| 2 | lives | clamp01(lives/3) | 现存；**跨 tier OOD**（1 命恒 0.333、2 命起手 0.667——c08 零样本清单项） |
| 3 | level | min(level,3)/3 | 现存 |
| 4 | fireProgress | (now−lastFire)/nextFireInterval | 现存 |
| 5 | turnCooldownRemaining | (cd−elapsed)/cd | 现存 |
| 6 | ringCompleteness | intact/8；无基地=0 | 现存 |
| 7 | enemiesOnField | /4（生成中敌**不算**） | 现存 |
| 8 | spawnQueueRemaining | /enemiesTotal | 现存 |
| 9-13 | tier 占比 ×5 | 生成中敌不算（filter 排除，定案） | 现存 |
| 14-16 | nearestEnemy dist/relX/relY | /FIELD_DIAG；rel ∈[-1,1] | 现存；15 翻 mirror |
| 17-18 | nearestBase dist/relX | 无基地=0 | 现存；18 翻 mirror |
| 19 | **玩家 HP** | `clamp01(hp/maxHp)` —— **ratio 定案**（raw≈263 量纲离群；maxHp 由 immutable profile 生成时导出、阶段内恒定 ⇒ 与 raw 信息等价；跨模型量纲稳定；未来星级改 maxHp 时 ratio 语义仍正确） | 新增 |
| 20 | **玩家盾** | `clamp01(min(shieldTimer, RESPAWN_SHIELD_MS)/RESPAWN_SHIELD_MS)`（min 防道具盾 20000 饱和） | 新增 |
| 21 | **敌冰冻** | `clamp01(freezeTimer / 20000)` | 新增 |
| 22 | **卡死** | `clamp01(stuckTicks / 900)`（分母 = reward 封顶 `c4-margin.jsonc:53`）；**World 新增字段，判定共享纯函数**（见 §3.5） | 新增 |
| 23 | **boat** | `boatTimer > 0 ? 1 : 0` | 新增 |
| 24 | **base HP** | **`clamp01(baseHp/baseMaxHp)` 无条件编码**（代码核实无基地关恒置满 1.0 不变化，`World.ts:484-485`；恒 0 会与「基地被毁=0」碰撞） | 新增 |
| 25 | **fence** | `fenceExpireFrame !== undefined ? 1 : 0` | 新增 |
| 26 | **score** | `clamp01(score / 20000)` | 新增 |
| 27 | **EMP** | `clamp01(empTimer / 8000)` | 新增 |
| 28 | **vy** | `p ? p.vy / p.speed : 0`（[-1,1] 不 clamp01，同 s15/16 惯例） | 新增 |
| 29 | **vx** | `p ? p.vx / p.speed : 0` | 新增；**x 分量 ⇒ X_INDICES** |

### 3.4 全局编码规则（写进 schema.py 顶层注释）

1. **uint8 规则**：所有 grid 通道是 uint8——任何浮点幅值编码必须 `Math.round(255*clamp01(x))`
   （或量化成档）后写入；标量缓冲 Float32 不受此限。
2. **加法记法**：ch11 低段 = `(owner<<2) + (d+1)` 全程加法（owner 判定 `rest = col-(sb<<3);
   owner = rest>=5`，**不可 `rest>>2`**——rest=4 是敌 right 会误判）；禁止照抄位域直觉用 OR。
3. **mirror 规则**：`DIRECTION_CHANNELS` = {6,7,8,9,10,11} **不变**；翻转实现用**显式静态 LUT**
   （bullet 32 项；敌机 self 101 项或公式 `(v & ~0b111) | flip3[v & 0b111]`——现 `(col>>3)&0x1F`
   沿用会侥幸保住 bonus 位，是巧合不是设计，一并换 LUT）；`SCALAR_X_INDICES=[15,18,29]` 取负；
   ch12/13/14/15 与打包位高位 mirror 无关。
4. **waveHeat N 点**：按 `world.enemySpawnPoints.length` 轮转，3 点（classic）/4 点（arena）皆正确。
5. **ch14 随玩家星级重算**：同一敌人不同星级通道值不同，禁缓存。
6. **生成中敌人不进标量集合**：s0/s1/s7/s9-13/nearest 的 filter 保持排除（grid 可见即可，
   标量语义 =「已激活敌」）——写进 SCALAR_LAYOUT 注释。
7. **SCHEMA_FINGERPRINT**：对 CH 表 / SCALAR_LAYOUT / 打包值域 / X_INDICES / 常量做稳定哈希，
   schema.py 与 obs-encoder.ts 双端导出同名常量，单测断言相等并写进 npy shard manifest。

### 3.5 同步链（13+ 处，漏一处 = 事故）

1. `schema.py`：三常量 + SCALAR_LAYOUT 加 11 行（含 s2 OOD 注释、生成中敌排除注释）+
   X_INDICES + CH 表 2 行 + ch7-10/11/12 值域注释 + 弹速静态映射（敌 4 档 + 玩家 L0..L3）+
   加法/uint8 全局注释 + FINGERPRINT。
2. `src/nn/obs-encoder.ts`：30 标量 + 16 通道 + 3 打包位 + waveHeat N 点 + counts 模块级 +
   ch15 ×255 + 常量 + FINGERPRINT。
3. `src/nn/npy.ts`：`writeShard` 硬编码 `[N,14,26,26]`/`[N,19]`（`:82-83`）→ 参数化 `[N,16,26,26]`/`[N,30]`。
4. `src/nn/infer.ts`：StudentModel stem 16→18（`in_ch+2`，`student.py:107`）+ coord 偏移 14→16 +
   in16 缓冲与拷贝长度（`:423/553-554`）；BC 参考模型 `NNModel.conv.0` 14→16。
5. `nn-training/data/dataset.py`：`_flip_direction` bullet 分支换 32 项 LUT（保 speedBucket、
   owner=rest>=5）；敌机分支换显式 LUT（不依赖 hi 位巧合）。
6. **`src/nn/wasm/conv_feats.c` + 重编**：`#define PAD3 (28*28*16)`（:20）、`pad3(in16,16,…)`（:153）
   → 18；clang 重编提交新 `conv_feats.wasm`；`conv-wasm.ts` 契约校验同步。**漏编后果：run()
   静默回退 TS 路径 41ms/次 > 16.7ms 帧预算 → 60 FPS 门红**。验证必须跑 **h64/d8 wasm 路径
   golden**（2026-09-10 bufA 事故：h16/d2 TS golden 测不到生产 wasm 路径）。
7. `World.stuckTicks` 新字段：**判定抽共享纯函数**（Simulation 维护与导出器消费同一实现，
   导出器改读字段不自算）；约束——只读 (prevWorld, curWorld, player) 纯量、**不消费
   `world.rng`**（§2.3）、`Simulation.updatePlaying()` 固定调用点；`WorldSerializer` 快照 +
   `tickHash` 签名；旧快照加载默认 0 过 snapshot 全字段断言（AGENTS §8）；tickHash 签名变化
   在提交信息声明。
8. BC 语料全量重导出（`export-godai-labels`，保留 `--wins 1`）。
9. 全部旧权重失效 ⇒ 重训全链路（weights_io 断言强制）。

### 3.6 容量 / 成本 / 字节数

- fc 输入 128×(64+19)=10624 → 128×(64+30)=**12032**，Δ=+1408（+2.1%）；stem 16→18
  Δ=64×2×9=**+1152**（+1.7%）；合计 **+3.8%**（67.5K 模型上 +2560）。打包位零形状变化。
- 推理墙钟：决策 tick 驱动（~6 次/秒，`policy-input.ts:33`）；features wasm ~6.9ms/次，
  +2.1% MAdds ≈ +0.15ms 量不出——**前提 wasm 重编落地**（否则 TS 回退 41ms，见 §3.5-6）。
- 编码器热路径（§14）：新增读取全落既有循环（ch14 一次除法/敌 ≤4 敌）；零新增分配
  （counts 反而修掉一处现存分配）；obs 缓冲 9,464→10,816B、标量 76→**120**B（一次性分配）。
- 训练侧：npy shard `[N,16,26,26]` 比 v2 **+14%**（mirror 增广同比例）。
- 字节数两笔：宿主 in16 拷贝 16ch 43,264B → 18ch **48,672B（≈47.5KB）**；wasm 内部 PAD3
  scratch 16ch 50,176B → 18ch **56,448B（≈55KB）**（`#define` 改后自动扩，线性内存预留同步核）。

### 3.7 掩码（mask）语义与信息量（2026-09-14 补记）

mask 不在 obs 字节里，而是随 shard 单独落盘（`masks.npy`，7 位 = move[5] + fire[2]），
由 `src/nn/obs-encoder.ts::computeMasks` 产出。bc-c4-v3 it1 语料的实测分布：

| 位 | 语义 | 实测均值 |
|---|---|---|
| move[0..4] | 5 个移动方向是否合法 | 恒 1（当前动作空间没有非法移动） |
| fire[0] | fire 动作是否合法 | 恒 1 |
| fire[1] | **fire-ready**（开火冷却是否结束） | 0.374 |

⇒ **7 位里 6 位是死信息**，唯一携带语义的是 fire-ready（37.4% 的帧可开火；而教师只在
7.3% 的帧真的开火 ⇒ 能开火时 19.5% 才开）。

推论（勿再议）：① 别把 mask 当"帮助策略避开非法动作"的通道——没有非法动作；
② 别用 mask 的位数评估观测信息量，学习信号只在 obs/scalars；
③ mask 与 obs 分开落盘，改 mask **不影响** `SCHEMA_FINGERPRINT`（指纹只钉 §3.1/§3.2/§3.3
的常量表），但改 mask 语义 = 改「一个样本是什么」⇒ 同样必须走 schema bump + 新语料轮次。

## 4. 测试清单（全部）

| 测试 | 断言 |
|---|---|
| 编码唯一性枚举 | (bonus, tier, d) 全组合通道值唯一（ch7-10）；ch11 全 32 值唯一，`mirror(mirror(v))==v` 且 speedBucket 翻转前后相等 |
| ch15 三值 | spawnTimer = 1000/500/0 ⇒ 通道值 255/128/0 |
| 弹速映射 | combat.ts → speedBucket 全落档（敌 4 档 + 玩家 L0..L3） |
| mirror 值域 | `test_dataset_mirror.py`：敌机 100 / 道具 63 / 子弹 32，翻转后双向唯一 |
| waveHeat N 点 | 3 点（classic）与 4 点（arena 四角）双断言，含第 4 点热量与轮转分配一致性 |
| stuckTicks golden | 同一局轨迹 obs s22 序列 ≡ reward 消费序列；同 seed 双跑逐字节一致；旧快照默认 0 |
| wasm | `tests/conv-wasm.test.ts` 18 通道断言 + **h64/d8 wasm 路径 golden** |
| 指纹 | SCHEMA_FINGERPRINT 双端相等 + shard manifest 含指纹 |
| 既有 | SCALAR_LAYOUT 索引连续断言、POWERUP_ORDER=15、schema 校验断言（`bun run check`） |

## 5. 不做（防再议定案）

- 不做「加一维废一次权重」的增量升级；**不推翻 14ch+19sc 布局重新设计**（证据：缺信息而非
  编码坏；重排信息增益为零却重开同步面；reward 不引用 obs 索引——METRICS 是独立 31 维遥测，
  `reward_library.py:820`）。
- World 全状态扫描后明确不加：**allies / guardStock / frenzyStock / rewindStock / mines**
  （全在主动使用道具路径，AI 无主动道具，局内从不出现；sacrifice 是死亡被动 AoE 无实体）·
  **敌方拾取道具**（采集仅 `by:'player'`）· **pickupWindowTimer**（结算期）· **flashTimer /
  hitCount**（表现层，ch14 更准）· **playTimeMs / kills / 关卡进度**（s0/s1 承担紧迫性、s8 显
  末局；满压加成是 reward 遥测）· **commanderQuota**（tier 位已覆盖）。
- 不降难度、不改任务定义（总纲 D7）。

## 6. 已决问题记录（勿再议）

sN1 **ratio** 非 raw（量纲/等价性/跨模型/未来兼容四理由）· sN6 **无条件编码**（无基地恒 1.0，
消除与「被毁=0」碰撞）· sN9 EMP 并入（19→30 的一部分）· sN10/sN11 vx/vy 并入（经典 4 冰关
坐实）· ch15 倒计时幅值 ×255（E1）· 生成中敌不进标量集合 · ch14 跨模型自洽（instant
hp=hits×100 整除；pool 105 步长界限不漂移）· hits-to-kill 所有种类编码（非 armor 专利）·
s8 分数基准 20000 档（避开 5000 倍频混淆）· mirrorX 不变量：X_INDICES=[15,18,29]、
DIRECTION_CHANNELS 不动。

## 7. 变更记录

- **v4.0（2026-09-13）**：体系化重构——Draft v3.4 全部定案重排为实施规格（全通道/全标量定稿
  表 + 全局规则 + 同步链 + 测试清单）；删除已完成的时序/配方/审核征召章节（归总纲 v2.0 §5/§6/N3）。
  v3.4 原文存档 `plan/archive/obs-schema-v3-v3.4.md`。
- v3→v3.4：第二轮审核回填（C8-C10 打包位、EMP、wasm）→ v3.1（vx/vy、sN1 ratio、sN6 无条件）
  → v3.2（dsf：A1 bit6、A2 加法、A4 同源、B9-2 驳正）→ v3.3（ms：F1 waveHeat N 点、F3 约束）
  → v3.4（hy：E1 uint8 截断、E6 mirror 丢位、X4 指纹）。
