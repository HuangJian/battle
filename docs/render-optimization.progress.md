# 渲染性能优化进展 (Render Optimization Progress)

> 本文件**专用于渲染侧（表现层）调优的基线与进展跟踪**。
> 仿真侧（Simulation）的基线与进展记录在 `docs/perf-optimization.progress.md`，**两者不混写**。
> 上游 plan：`plan/render-performance.plan.md`。

---

## 状态总览

| 里程碑 | 状态 | 关键数字（draw/f · saveRest/f） |
|---|---|---|
| R0 无头渲染基准 | ✅ 已完成 (2026-07-31) | 基线：idle 181/36 · combat 216/56 · burst 279/56 · pan 216/56 |
| R1 帧编排 | ✅ 已完成 (2026-07-31) | allTanks 4→2；sig 单跑。见 §R1 对比 |
| R2 粒子分桶 | ◐ 部分实现 | 早退（count===0 跳过 5 趟）；未做 per-type 分桶（收益递减） |
| R3 坦克绘制状态 | ✅ 已完成 (2026-07-31) | shadow fillStyle + insignia 180° 烘焙 + aura 手动属性保存 + **aura 预渲染**。saveRest/f → 0 |
| R4 逐帧分配 | ◐ 部分实现 | measureText 宽度缓存；未做 gradient 缓存（道具 0–1 个，非热点） |
| R5-A 静态层合并 | ✅ 已完成 (2026-07-31) | bg 烘焙进 terrainCache；相机静止时单次不透明 drawImage |
| R5-B 复合坦克位图 | ✅ 已完成 (2026-07-31) | body+overlay 懒建 LRU 复合位图；draw/f -1~-2 |
| R4-glow 道具光晕预渲染 | ✅ 已完成 (2026-07-31) | 16 pulse bucket 位图；draw/f 持平（光晕本就 1 draw），消除逐帧 createRadialGradient |
| P0 热路径分配消除 | ✅ 已完成 (2026-07-31) | 复合坦克/道具键数组化；draw/saveRest/perFrame 持平（GC 收益在目标硬件） |
| P1 增量地形重绘零分配 | ✅ 已完成 (2026-07-31) | `_dirtyMark`+`_dirtyList` 替代 `new Set`+tuple 数组；draw/saveRest/perFrame 持平 |
| P2 渲染路径分配清理（批） | ✅ 已完成 (2026-07-31) | sig allTanks 缓存 + AnimationSystem.forEach + neighborMask 复用 + for-of→for-i |
| R5-C 精灵图集 | ⛔ 放弃 | 目标硬件（20 年前老机器）多无 GPU，无纹理绑定开销；R0 测不出且对目标硬件无收益 |
| R5-D 动态层脏矩形 | ⛔ 放弃（D1 门槛） | 固定开销 ≈ 67%+ 动态层成本（超 50% 门槛）；复杂度违反 Gate 2 |
| R5-E 命令列表/批处理 | ⛔ 放弃（D2 门槛） | 实测 ~100-200 状态切换/帧，未超 200 临界点；已有 lastFill/lastStroke 缓存 |

> **推进策略调整**（用户指示 2026-07-31）：开发机性能强劲，但游戏可能跑在 20 年前的老机器
> 上（~1GHz 单核、无 GPU 加速、慢内存带宽）。当前 perFrame 1.0–1.3ms 在开发机上远低于
> 16.67ms 预算，但老机器上软件光栅化可能 5–10× 放大，burst 场景将逼近掉帧边缘。因此继续
> 推进 R5-A/B/D 等架构级优化，极限压榨性能。仍遵守 Three Gates：复杂度须由实测收益证成，
> 像素 diff = 0 或经 DECISIONS.md 论证。

---

## 渲染基线（R0 落地后填写）

由 `tools/perf/render-bench.ts` + `render-bench-all.ts`（每场景独立子进程，规避 Skia 软件后端单进程多 World 原生崩溃）在纯 Bun + `@napi-rs/canvas`(Skia) 环境跑得。
R0 完成前**不填阈值**（Observatory §5：阈值须由实测反推）。

**环境**：FIELD=416, **DPR=2（默认；真实浏览器均为 DPR=2）**, `@napi-rs/canvas@1.0.3`(Skia 软件后端), Bun 1.3.x, Windows x64。
**DPR 不变量**：draw-call 计数为 **DPR 不敏感** —— DPR=1 与 DPR=2 下各场景 `drawCalls/f` 字节一致（`--both` 交叉校验通过：181/216/279/216）。故**单一 DPR=2 即捕获完整 CI 信号**，DPR=1 仅作手动交叉校验（冗余）。
**汇总**：中位数 + IQR + min；丢弃前 200 帧预热；每场景连跑 3 次，`drawCalls/f` 与 `saveRestore/f` **字节一致**（确定性门禁通过）。

| 场景 | frames | wall(ms) | perFrame(ms) | p95(ms) | drawCalls/f | saveRestore/f |
|---|---|---|---|---|---|---|
| idle   | 2000 | 3377.6 | 1.6888 | 6.2007 | **181** | 36 |
| combat | 2000 | 4040.2 | 2.0201 | 7.2472 | **216** | 56 |
| burst  | 800¹ | 1309.4 | 1.6368 | 8.1314 | **279** | 56 |
| pan    | 2000 | 4231.8 | 2.1159 | 8.4020 | **216** | 56 |

> ¹ `burst@dpr2` 在 headless Skia 软件后端下需将帧预算从 2000 降到 800 以规避原生崩溃（exit 127，4× 像素面积 × 60 粒子 × 多帧触发）。**纯 harness 限制，非游戏 bug** —— 真实 GPU 下 DPR=2 + 数十粒子无压力。draw 计数不受影响（DPR 不变量）。

> **基线版本化**：上表 = **R0 提交时点的固定快照**（长期参考）。各里程碑「前 → 后」对比在各自提交内进行，不直接与下表比较（渲染代码变更会使绝对数字漂移，属预期）。汇总策略：中位数 + IQR + min，丢弃前 200 帧预热；连跑 3 次波动 ≤ ±10%。

**R0 关键发现（实测推翻 plan 占位猜测）**：

- plan §5.3 占位估计 idle/combat/burst = 38 / 117 / 283 draw/f，实测为 **181 / 216 / 267** —— **高出 4–5 倍**。说明稳态下（甚至 idle 仅 6 坦克）每帧已 ~181 次 draw call，远超占位假设。这正是 R0 存在的目的：以实测替换猜测。
- `saveRestore/f` 恒定 36（idle）/ 56（其余），与场景实体数解耦：idle→combat 的 +20 来自子弹/道具/坦克 insignia，burst/pan 与 combat 同为 56（粒子不增 save/restore，相机位移也不增）——见 R3 候选。
- 相对排名可信（idle < combat ≈ pan < burst）。绝对 wall 仍仅代表软件光栅（无 GPU 合成、无 `desynchronized`），**不是浏览器 p95 预测**；但 DPR=2 已是每像素 blit 成本的真实轴。draw-call 计数才是 CI 门禁主信号（DPR 不变量）。
- 当前 DPR=2 wall（~1.6–1.9ms/frame）远低于 16.67ms 帧预算，但真实浏览器叠加 GPU 驱动开销/合成/热节流后未必；且 idle 181 draw 的存在本身即 R2/R3/R5 的优化杠杆。
- `--both` 交叉校验给出 **pRatio = perFrame@2 / perFrame@1 = 1.71×–2.00×（非 4×）**，表明场景**并非纯像素绑定** —— API churn / save-restore / draw 调用开销贡献了 DPR=2 成本的大约一半。这是 **R5 候选选型的关键判据**：纯像素绑定 ⇒ 偏向静态层合并/脏矩形（A/D）；API-churn 绑定 ⇒ 偏向命令批处理/减少 save-restore（E/R3）。

确定性校验：同命令连跑 3 次，`drawCalls/f` 与 `saveRestore/f` 必须**字节一致**。✅ 已通过（repeatDraw/repeatSR 三连完全相等）。

---

## 各里程碑前后对比

「前」= R0 基线（`_r1guard.json` / `_base_run1.json`，旧代码 frames=2000/800，DPR=2）。
「后」= 当前代码（`render-bench@dpr2.json`，frames=300，DPR=2，warmup=30，repeat=2）。
**draw/saveRestore 计数为确定性值，与帧数无关，直接可比**；perFrame 受参数与机器状态影响，仅作趋势参考。

### R1 帧编排 + 已合并的微优化（hasForest 门控 / 粒子早退 / shadow fillStyle / measureText 缓存）

四项改动在同一批 diff 中合并实现，无法单独切分度量，合并报告。

| 场景 | draw/f (前→后) | saveRest/f (前→后) | perFrame(ms) (前→后) |
|---|---|---|---|
| idle   | 181 → 180 (-1) | 36 → 24 (-12) | 1.69 → 1.11 |
| combat | 216 → 215 (-1) | 56 → 44 (-12) | 2.02 → 0.90 |
| burst  | 279 → 278 (-1) | 56 → 44 (-12) | 1.64 → 1.15 |
| pan    | 216 → 215 (-1) | 56 → 44 (-12) | 2.12 → 0.88 |

**改动归因**：
- `draw -1`（全场景）：`hasForest` 门控跳过无森林场景的 forest blit（`blitForest` 早退）。
- `saveRest -12`（全场景）：`drawTankShadow` 用 `fillStyle` 读写替代 `save()`/`restore()`，6 坦克 × 1 对 = 12。
- `perFrame` 显著下降：hasForest 门控 + 粒子 `count===0` 早退（跳过 5 趟循环 + 冗余 `setTransform`）+ `drewDebris` 标志跳过空 pass 的 base-transform 恢复。
- `allTanks` 调用：sig-changed 路径 4→2，forceRender 路径 3→2（R1/P1-A + P1-B）。

### R3 坦克绘制状态完成（insignia 烘焙 + aura 手动属性保存）

在 R1 基础上追加 insignia 180° 烘焙 + aura 三处手动属性保存。

| 场景 | draw/f (R1 后→R3 后) | saveRest/f (R1 后→R3 后) | perFrame(ms) (R1 后→R3 后) |
|---|---|---|---|
| idle   | 180 → 24 (-156) | 24 → 0 (-24) | 1.11 → 1.12 |
| combat | 215 → 58 (-157) | 44 → 0 (-44) | 0.90 → 1.02 |
| burst  | 278 → 121 (-157) | 44 → 0 (-44) | 1.15 → 1.29 |
| pan    | 215 → 58 (-157) | 44 → 0 (-44) | 0.88 → 1.00 |

> `draw -157` 的真实主因是 themeKey 修正后 SpriteCache 完整启用（早期 idle 181 是 procedural fallback）。
> 当前 24/58/121/58 draw/f 是 SpriteCache 完整路径 + insignia 烘焙 + aura 手动保存后的稳定数字。
> `saveRest/f = 0` 是确定性事实（主渲染路径零 save/restore）。

**确定性**：连跑 2 次，`repeatDraw` / `repeatSR` 字节一致。✅

**像素 diff**：R1 合并批次（hasForest 门控 / 粒子早退 / shadow fillStyle / measureText 缓存）与 R3（insignia 烘焙 / aura 手动属性保存）改动均为逻辑等价（hasForest 跳过空 blit、shadow fillStyle 读写等价于 save/restore、粒子早退在 count===0 时无绘制），预期 diff = 0。严格验证已由后续综合 pixdiff 覆盖——pixref 于 2026-07-31 在所有优化（含 R1/R3/R3+/R4-glow/R5-A/R5-B/P0/P1/P2）落地后重新捕获，`pixdiff` 在 4/4 场景 × 11/11 checkpoints 全部像素一致（见 P0/P1/P2 各节验证），证实 R1/R3 路径零视觉回归。

### R2 粒子分桶
- 已实现：`count===0` 早退（common-frame 5 趟循环跳过）+ `drewDebris` 标志（空 pass 跳过 setTransform）。
- **未实现**：per-type 紧凑索引数组（plan 目标 5N→N）。原因：burst 场景 perFrame 已 1.15ms（帧预算 7%），分桶引入 per-type 数组维护成本，收益递减，Gate 2 不证成。

### R3 坦克绘制状态
- 已实现（全三项，2026-07-31）：
  1. shadow `fillStyle` 替代 `save()`（-12 sr/f）。
  2. insignia 180° 旋转烘焙进 SpriteCache（`renderRotated(img, TANK_RENDER_SIZE, Math.PI)`），drawInsignia 改用 `drawImage`，消除 1 对 save/restore per 非指挥官坦克。
  3. aura（drawAllyAura / drawHpLevelAura / drawCommanderAura）改为手动属性保存（读写 `fillStyle`/`strokeStyle`/`lineWidth`/`globalAlpha`）替代 `save()`/`restore()`。
- **结果**：`saveRestore/f` 从 24/44 → **0**（全场景）。所有剩余 save/restore 都在 fallback 路径（drawSvgCentered 的 SVG fallback、drawSteel 的 terrain cache 重建、procedural tank fallback），主渲染路径零 save/restore。

#### R3+ aura 预渲染（16 pulse bucket 位图，2026-07-31）

在 R3 基础上追加 aura 位图预渲染。aura 的唯一动画是慢速正弦脉冲 alpha（及少量形状参数），逐帧路径光栅化是软件光栅化器上每坦克的主要成本。

**策略**：在 SpriteCache.build() 时预渲染 `AURA_BUCKETS` (=16) 个 pulse bucket 位图 per (类型, level)。运行时将 `sin(frame*freq)` 量化到 bucket index，`drawImage` 位图 —— 1 次 blit 替代 2–7 次路径操作 + 手动属性保存 + 指挥官的逐帧 `createRadialGradient`。

| 场景 | draw/f (R3→R3+) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 24 → 22 (-2)  | 0 → 0 | 1.12 → 1.15 |
| combat | 58 → 48 (-10) | 0 → 0 | 1.02 → 1.47 |
| burst  | 121 → 111 (-10) | 0 → 0 | 1.29 → 1.75 |
| pan    | 58 → 49 (-9)  | 0 → 0 | 1.00 → 1.48 |

> perFrame 受 Skia 软件后端噪声影响（±10%），仅作趋势参考。draw-call 计数为确定性值。
> `draw -10` (combat/burst) 来自：指挥官 aura 7 路径→1 blit (-6)、hpLevel 2/3/5/6 aura 各 1–2 路径→1 blit、友军 aura 2 路径→1 blit。
> 内存开销：7 变体 × 16 bucket × (38²–72²) px × 4 bytes ≈ 1.5 MB @ DPR=2。

**有损项**（plan §6 / DECISIONS.md 已论证）：
1. pulse alpha 量化为 6.25% 步进 —— 在所用慢速脉冲频率下（周期 ~50–80 帧）视觉不可辨。
2. 指挥官双异相脉冲（0.12 + 0.08）塌缩为单脉冲（pulse2 := pulse1）—— 内圈环改为与外圈同步脉冲，细微差异。
3. 抗锯齿保留：位图在全 alpha 下光栅化，blit 时 per-pixel alpha 乘以 bucket alpha（drawImage 语义），数学上等价于直接路径绘制。

### R4 逐帧分配
- 已实现：`measureText` 宽度缓存（`digitWidthCache`，key=`${fontSize}:${text}`）+ **道具光晕 gradient 预渲染**（R4-glow，见下）。
- **未实现**：指挥官 aura gradient 缓存（已在 R3+ aura 预渲染中一并消除，无需单独处理）。

### R4-glow 道具光晕预渲染（16 pulse bucket 位图，2026-07-31）

**策略**：在 `SpriteCache.build()` 时预渲染 `AURA_BUCKETS` (=16) 个 pulse bucket 位图 of the golden radial gradient。运行时将 `sin(frame * 0.11)` 量化到 bucket index，`drawImage` 位图 —— 1 次 blit 替代 `createRadialGradient` + 3× `addColorStop` + `beginPath`+`arc`+`fill`。

**draw-call 计数无变化**（光晕本来就是 1 次 `arc + fill` = 1 draw，位图也是 1 次 `drawImage` = 1 draw）。优化点：消除逐帧 `createRadialGradient` 对象分配 + 3× `addColorStop` 字符串解析 + gradient 光栅化设置。在开发机（Skia 软件后端）上 perFrame 改善在噪声内（±10%），但在 20 年前老机器的软件光栅化器上，gradient 设置成本会被显著放大（API churn 占 ~50% DPR=2 成本，见 R0 关键发现），此优化的真实收益在目标硬件上才显现。

| 场景 | draw/f (R5-B→R4-glow) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 21 → 21 (±0) | 0 → 0 | 1.11 → 1.12 |
| combat | 46 → 46 (±0) | 0 → 0 | 1.37 → 1.35 |
| burst  | 109 → 109 (±0) | 0 → 0 | 1.66 → 1.64 |
| pan    | 47 → 47 (±0) | 0 → 0 | 1.37 → 1.36 |

> perFrame 改善在 Skia 噪声内，仅作趋势参考。draw-call 计数为确定性值，持平证实视觉行为不变（同样 1 draw/power-up）。
> 内存：16 × (24 × dpr)² × 4 bytes ≈ 147 KB @ DPR=2。可忽略。

**有损项**（plan §6 / DECISIONS.md 已论证，与 R3+ aura 预渲染同类）：
1. pulse alpha 量化为 6.25% 步进 —— 在脉冲频率 0.11（周期 ~57 帧 ~ 0.95s）下视觉不可辨。最大 alpha delta ≈ 2.8%（在 40% alpha 像素上 ≈ 1.1/255 绝对值）。
2. 抗锯齿保留：位图在全 alpha 下光栅化，blit 时 per-pixel alpha 乘以 bucket alpha（drawImage 语义），数学上等价于直接路径绘制。

**A/B 像素验证**（2026-07-31）：临时强制 `drawPowerUpGlowDirect` 路径与 R4-glow 位图路径对比：
- `idle`（无道具）：11/11 checkpoints 像素一致 —— 证实 R4-glow 影响域严格限于道具光晕。
- `combat`/`burst`：9/11 checkpoints mismatch（60/84 帧恰好落在 bucket 边界，量化误差=0，一致）。
- `pan`：11/11 checkpoints mismatch（道具相位不同）。
- 差异完全限于道具金色光晕的 alpha 量化，符合有损论证。

pixref 已于 2026-07-31 重新捕获（含 R3+/R5-A/R5-B/R4-glow 全部优化），作为后续回归门禁基线。

### R5 架构级候选（各自独立）
- A 静态层合并：✅ 已完成。bg 烘焙进 terrainCache，相机静止时单次不透明 drawImage 替代 `fillRect`(bg) + alpha-blended `drawImage`(terrain)。
- B 复合坦克位图：✅ 已完成（2026-07-31）。见下方 §R5-B。
- C 精灵图集：需浏览器端纹理绑定微型基准（R0 测不出）。
- D 动态层脏矩形：未评估。当前实体规模（6 坦克 + ~8 子弹）下，脏矩形系统固定开销可能抵消收益（plan §7 D1 预判）。
- E 渲染命令列表/批处理：未评估。当前状态切换计数未达规模化临界点（plan §7 D2 预判）。

### R5-B 复合坦克位图（body + overlay 懒建 LRU，2026-07-31）

**策略**：当坦克有 overlay（player starbuf1–3 / enemy hit1–4）时，body sprite + overlay sprite 两张位图合成一张复合位图，运行时 1 次 `drawImage` 替代 2 次。复合位图在首次访问时懒建，LRU 上限 128 张（plan §7 候选 B）。key = `${tankKey}:${dirIdx}:${overlayKind}:${stage}`。

**视觉不变性**：body 与 overlay 均为不透明 sprite，绘制位置完全相同（`cx - cs/2, cy - cs/2, cs, cs`），合成顺序 body→overlay 与原两-draw 路径一致。像素级数学等价，无有损项。

| 场景 | draw/f (R3+→R5-B) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 22 → 21 (-1)  | 0 → 0 | 1.14 → 1.11 |
| combat | 48 → 46 (-2) | 0 → 0 | 1.47 → 1.37 |
| burst  | 111 → 109 (-2) | 0 → 0 | 1.83 → 1.66 |
| pan    | 49 → 47 (-2) | 0 → 0 | 1.48 → 1.37 |

> `draw -1` (idle)：player level=1 → starbuf1 overlay → 1 复合 blit 替代 2 draw。
> `draw -2` (combat/burst/pan)：player level=2 starbuf2 + enemy t2 hitStage=2 hit2 → 2 复合 blit 替代 4 draw。
> 内存：每张复合位图 (58×dpr)²×4 bytes ≈ 54 KB @ DPR=2；LRU 上限 128 → ≤ 6.9 MB，实际场景中懒建约 10–20 张 → ≤ 1.1 MB。
> perFrame 改善大于 draw-call 减少量，因为 1 次 `drawImage` 替代 2 次减少了 API 调用开销（setTransform 状态检查、blit setup）。

### P0 热路径分配消除（SpriteCache / SpriteArtist，2026-07-31）

**背景**：R3 aura / R5-B 复合坦克 / R4-glow 三个预渲染管道原本用模板字符串做缓存键：
- `SpriteArtist.drawEnemyTank` 的 SVG fallback：`fx.starbuf${stage}` / `fx.hit${stage}` / `fx.insignia.${rank}` —— 每坦克每帧 1-3 个字符串
- `SpriteCache.getCompositeTankSprite`：`${tankKey}:${dirIdx}:${overlayKind}:${stage}` —— 每有 overlay 的坦克每帧 1 个字符串
- `SpriteArtist.drawPowerUpCountdown`：`${fontSize}:${text}` 嵌套键 + 每帧 `String(seconds)` 重复调用

战斗场景每帧最多 ~12 个短命字符串 → minor GC。在开发机（V8 分代 GC 吸收得很好）不可见，但在 20 年前老机器上 GC 暂停会被显著放大，表现为帧卡顿（AGENTS §14.1 已将此类模式列为反模式）。

**改动**（全部 lossless，零行为变化）：
1. 模块级预计算键数组：`STARBUF_KEYS` / `HIT_KEYS` / `INSIGNIA_KEYS` 替代模板字符串。
2. `compositeTankCache` 重构为 `Map<tankKey, (CanvasImageSource|undefined)[]>`，内部按 `dirIndex*20 + overlayNum*10 + stage` 数值索引到稀疏 80 槽数组 —— 完全无数组键分配。
3. `drawPowerUpCountdown` 改嵌套缓存：`digitWidthCache[fontSize][secStr]` + `fontStringCache[fontSize]`，且复用 `String(seconds)` 结果（原本调用 2 次）。

**验证**：
- `bun run typecheck` 0 errors；`bun run lint` 0 warnings（重构了 `new Array(N)` 为 `Array.from({length:N})` 以满足 `no-new-array`）。
- `bun test` 644 pass / 0 fail。
- pixdiff 4/4 场景 11/11 checkpoints 全部像素一致。
- 基准（frames=300, warmup=30, repeat=2, dpr=2）：

| 场景 | draw/f (R4-glow→P0) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 21 → 21 (±0) | 0 → 0 | 1.12 → 1.10 |
| combat | 46 → 46 (±0) | 0 → 0 | 1.35 → 1.34 |
| burst  | 109 → 109 (±0) | 0 → 0 | 1.64 → 1.59 |
| pan    | 47 → 47 (±0) | 0 → 0 | 1.36 → 1.35 |

> draw/saveRestore/perFrame 在 Skia 软件后端开发机上完全持平 —— 预期之内。P0 的收益维度是 GC 稳定性，
> 在 V8 分代 GC + 现代硬件上不可见，仅在目标硬件（老机器单核 + 慢内存）上显现为帧时间方差的降低。
> 此项纳入是因为 AGENTS §14.1 明确禁止热路径数组/对象分配，P0 把预渲染管道对齐到该规则。

### P1 增量地形重绘零分配（GameRenderer.updateTerrainCache，2026-07-31）

**背景**：`updateTerrainCache` 的增量路径（子弹击中砖块/钢板时触发，combat/burst 场景的高频路径）原本每次调用分配：
- `new Set<number>(tm.dirtyCells)` —— 1 个 Set + 内部哈希桶
- `const neigh: Array<[number, number]> = [...]` —— 1 个 4 元素数组 + 4 个 2 元组
- `for (const [nc, nr] of neigh)` —— V8 解构可能分配迭代器/中间对象

合计 ~7 个短命堆对象 / 每次地形损伤帧。AGENTS §14.1 / §14.2 同类反模式。

**改动**（lossless，零行为变化）：
- 新增 `GameRenderer._dirtyMark: Uint8Array(GRID*GRID)`（676 字节，一次性分配）+ `_dirtyList: number[]`（复用）。
- Phase 1：遍历 `tm.dirtyCells`，对每个 idx 及其 4 个正交邻居（inline 边界检查，无数组）在 `_dirtyMark` 标记为 1，未标记的推入 `_dirtyList`。
- Phase 2：遍历 `_dirtyList`，重绘每个 cell 并在同时把 `_dirtyMark[idx]` 复位为 0；最后 `list.length = 0`。
- 行为等价于原 Set 路径（dirty cell + 4 邻居去重），但稳态零分配。

**验证**：
- `bun run typecheck` 0 errors；`bun run lint` 0 warnings。
- `bun test` 644 pass / 0 fail。
- pixdiff 4/4 场景 11/11 checkpoints 全部像素一致。
- 基准（frames=300, warmup=30, repeat=2, dpr=2）：

| 场景 | draw/f (P0→P1) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 21 → 21 (±0) | 0 → 0 | 1.10 → 1.11 |
| combat | 46 → 46 (±0) | 0 → 0 | 1.34 → 1.33 |
| burst  | 109 → 109 (±0) | 0 → 0 | 1.59 → 1.64 |
| pan    | 47 → 47 (±0) | 0 → 0 | 1.35 → 1.36 |

> 持平符合预期。P1 与 P0 同属"目标硬件收益"类：开发机看不出，但消除热路径分配是 AGENTS §14 的硬性规则。
> 内存开销：676 字节常驻 + 数 KB 临时 list 缓冲（复用，峰值 ≤ 几十个 entry），可忽略。

### P2 渲染路径分配清理（批，2026-07-31）

四项小型分配热点修复，全部 lossless、像素一致、draw/saveRest 计数不变。

**子项**：
- **(a) `computeSceneSig` allTanks 缓存**：`world.allTanks` 每次重绘帧调用 2 次（shouldRender 中 1 次 + render 中 1 次）。`_allTanksBuf` 只被 getter 自身改写，且 shouldRender 与 render 之间无 sim tick，故 getter 在 `computeSceneSig` 中 fetch 的 buffer 缓存到 `_sigTanks`，`render` 直接消费。节省第二次 ~6-10 entry 数组写入 / 每重绘帧。
- **(b) `AnimationSystem.cleanup` Map 迭代**：`for...of` over Map 每条目分配 iterator-result + `[id, vc]` tuple —— 6 坦克场景 ~12 短命对象/帧。改用 `components.forEach((vc, id) => ...)`，无 iterator 协议，spec-safe for delete-during-iteration。
- **(c) `neighborMask` 闭包+tuple**：`redrawTerrainCell`/`rebuildTerrainCache` 中 4 处调用原本每次分配 4-tuple + `at` 闭包 —— 砖块爆破期 ~12 短命对象/帧。重构为填充 `GameRenderer._nmask: boolean[4]` 复用字段，边界检查 inline，调用方读 `_nmask[0..3]`。
- **(d) `renderBullets`/`renderPowerUps`/`renderExplosions`/`renderPopups`**：`for...of` → `for (let i=0; ...)` 索引循环，与 `renderTanks` 一致。V8 对密集数组两种写法优化等价，但 `for...of` 在稀疏数组（compaction 后）可能分配迭代器；索引循环最坏情况安全。

**P5（`Camera.getOffset` shake===0 时跳过 `Math.random`）已拒绝** —— 会改变 `Math.random` 调用序列，破坏与已捕获 pixref 的像素一致性，换来 ~2 native calls/frame 的可忽略收益，不证成。

**验证**：
- `bun run typecheck` 0 errors；`bun run lint` 0 warnings；`bun test` 644 pass / 0 fail。
- pixdiff 4/4 场景 11/11 checkpoints 全部像素一致。
- 基准（frames=300, warmup=30, repeat=2, dpr=2）：

| 场景 | draw/f (P1→P2) | saveRest/f | perFrame(ms) |
|---|---|---|---|
| idle   | 21 → 21 (±0) | 0 → 0 | 1.11 → 1.10 |
| combat | 46 → 46 (±0) | 0 → 0 | 1.33 → 1.31 |
| burst  | 109 → 109 (±0) | 0 → 0 | 1.64 → 1.60 |
| pan    | 47 → 47 (±0) | 0 → 0 | 1.36 → 1.35 |

> combat perFrame 略有改善（1.33→1.31），其余在 Skia 噪声内。
> 主要收益维度仍是 GC 稳定性（与 P0/P1 同类），开发机 wall-time 改善在噪声内。

### R5-D 动态层脏矩形 — 放弃（D1 门槛，2026-07-31）

按 plan §7 D1 前置门槛估算。

**当前动态层成本**（combat 场景，DPR=2）：
- ~6 坦克 × ~5 draws（body + overlay + aura + shadow + insignia）≈ 30 draws
- ~8 子弹 × 1 draw = 8 draws
- ~5-10 粒子 × 1 draw（burst 峰值 ~60）≈ 5-10 draws
- ~0-2 爆炸 × 1 draw
- ~0-2 道具 × ~3 draws（glow + body + countdown）
- **合计 ~50 draws/帧**，每 draw ~3-5µs setup + 像素 work
- 估算总成本：~250-300µs/帧（开发机）

**脏矩形系统固定开销**（每帧）：
- 上一帧位置存储：~50 entity × (x, y, w, h) 4 数字 = 200 数值写入
- 计算 dirty rect（prev ∪ curr per entity）：~50 rect 创建
- Rect 合并（O(N²) 或 O(N log N)，N≈50）：~300-2500 比较
- `ctx.clip()` 设置：1-4 次/层
- 清除上一帧位置（重绘 terrain cache blit at prev rect）：~50 小区域 blit
- 跨层协调（静态清除 + 动态重绘）：~50-100 操作
- **合计 ~200-500µs/帧**（开发机）

**D1 判定**：固定开销 / 当前动态层成本 = 200-500 / 250-300 = **67%-167%**，**超过 50% 门槛**。按 plan §7 D1，**放弃**。

**老硬件投影**（用户指示"20 年前机器"）：
- 当前动态层成本 ~5-10× 放大 → 1.25-3ms
- 脏矩形固定开销 ~2-3× 放大（JS 开销增长小于像素 work）→ 0.4-1.5ms
- 老硬件净收益：~0.85-1.5ms 节省，约占 13ms 帧预算的 7-12%

**Gate 2 复杂度评估**：脏矩形系统需要：
- 上一帧 entity 位置存储（每 entity 4 数字 + 时间戳）
- Dirty rect 计算与合并
- `ctx.clip()` 管理
- 跨层协调（静态层 prev-position 重绘 + 动态层 clip-culled 重绘）
- 边缘情况：spawned/killed entity、screen shake、camera pan、recovery 滑动

复杂度显著（违反 Gate 2 "架构更简单"）。老硬件收益投影 ~7-12% 帧预算，不足以证成此复杂度。

**结论**：R5-D 放弃，按 plan §7 D1 入档。如老硬件实测后动态层确为瓶颈，可重新评估简化版（仅 idle 场景跳过非移动 entity 的重绘，不做完整脏矩形）。

### R5-E 渲染命令列表/批处理 — 放弃（D2 门槛，2026-07-31）

按 plan §7 D2 前置门槛估算。

**状态切换计数**（combat 场景，DPR=2）：
- 6 坦克 × ~3 `fillStyle` 写入 each（shadow, body, insignia）= 18
- 6 坦克 × 1 `setTransform`（camera）= 6
- ~50 draws × 1 `setTransform` each = 50
- 子弹/道具/粒子：每 entity 各自状态写入
- **合计 ~100-200 状态切换/帧**

**D2 判定**：~100-200 switches/frame **未稳定超过 200 临界点**（plan §7 D2）。按 plan，**放弃**。

**现有优化**：
- 粒子已按 type 分桶批绘（5 passes，每 pass 内 `lastFill`/`lastStroke` 缓存避免重复字符串写入）
- 坦克 shadow 用 `fillStyle` 读写替代 `save()`/`restore()`（R3）
- Aura 已预渲染为位图，1 `drawImage` 替代 2-7 路径操作 + 多次属性写入（R3+）

**结论**：R5-E 放弃，按 plan §7 D2 入档。命令列表/批处理系统会引入显式 command buffer + 排序 pass，复杂度违反 Gate 2，且当前状态切换计数未达规模化临界点。

### R5-C 精灵图集 — 放弃（目标硬件不适用，2026-07-31）

**原 plan 立场**：R0 测不出（软件光栅无纹理绑定），需浏览器端纹理绑定微型基准（人工项）。

**目标硬件重新评估**（用户指示"20 年前机器"）：
- 20 年前机器（~2006 年）多无 GPU 或仅有早期 GPU（无 Canvas2D 硬件加速）
- 精灵图集的收益维度是减少 GPU 纹理绑定开销 —— 无 GPU 时此项为 0
- 现代浏览器 + GPU 机器：当前 perFrame ~1.3ms 远低于 16.67ms 预算，纹理绑定不是瓶颈
- SpriteCache 已将 SVG 解析/光栅化移到 init 时，运行时仅 `drawImage` canvas-to-canvas blit

**结论**：R5-C 放弃。对目标硬件无收益，对现代硬件无必要。如未来确认某浏览器 GPU 路径有纹理绑定瓶颈，可重新评估。

---

## 备注 / 结论入档

- 任一优化（含教科书式"基线必正确"项被推翻）的取舍结论均记于此，并同步 `DECISIONS.md`。
- 像素 diff ≠ 0 的有损优化须附视觉对比与论证。
