# 游戏画面性能优化 (Render Performance)

**版本 1.3** · 自包含专项 plan（经一轮自审修订：修正技术事实 / 内部矛盾 / 基准设计 / 候选预判 / 实现细节）

> 使命锚点：打开浏览器，玩五分钟，带着笑容离开。(MANIFEST §1)
> 本 plan 只服务于一件事——**让这五分钟在低端机器上也不掉帧**。任何不服务于此的改动不属于这里。

---

## 1. 为什么需要这个 plan

仿真侧已经做到 `perTick=0.0020ms`（帧预算的 0.012%），连续 9 轮优化把 wall time 压掉 13.4%。
**渲染侧从未被系统性测量过。**

`tools/perf/` 六个工具全部是仿真侧的。渲染侧唯一的测量手段是浏览器内的 `PerfOverlay`（Alt+D），
而 AGENTS §5 禁止 agent 启动 dev server —— 这意味着**当前没有任何一个 agent 能验证自己的渲染优化是否有效**。

`Performance-Observatory.md` §6 宪法第 1 条：**"永不盲目优化：每次优化以测量开始，以验证结束。"**
所以本 plan 的第一个里程碑不是优化，是**补上测量能力**。这是硬阻塞项。

**范围与边界**：本 plan 只覆盖**表现层的渲染开销**——`GameRenderer` / `SpriteArtist` / `ParticleSystem` /
`Camera` / `PresentationLayer` / `SpriteCache` / `SpriteLibrary` 的绘制编排与 Canvas2D 调用。
**不覆盖**：Simulation 仿真逻辑（已另有 9 轮优化，见上）、WebGL/WebGPU 渲染后端（MANIFEST §14 出界）、
玩法/数值调整。任何改动须通过 §11 的 Three Gates。

**跟踪分离**：渲染调优与仿真调优**完全分开**。本 plan 的进展与基线**单独**计入
`docs/render-optimization.progress.md`，与仿真侧 `docs/perf-optimization.progress.md` 互不混写；
两者不可合并报告。

---

## 2. 已有基线 —— 当前状态清单（非免检项）

以下为**当前 HEAD 已存在的表现层能力**，作为 R0 基准的待测对象列在此处。
**本 plan 不预设它们绝对正确，也不预设它们最优**——它们是现状快照，不是免优化清单；
R0 基准照常测量它们，任一基线项若被实测证实为热点，即按与热点项相同的流程优化。

| 能力 | 位置 |
|---|---|
| SVG → 位图预光栅化，坦克 4 方向预渲染 | `SpriteCache.ts` |
| terrain / forest / vignette 三层离屏缓存 | `GameRenderer.ts` |
| 地形增量重绘（脏格 + 邻居扩展，非全图 676 格） | `TileMap.dirtyCells` |
| 场景签名门控 `shouldRender` | `PresentationLayer.ts`（`shouldRender`） |
| 0-loop 静态屏 idle + `document.hidden` 停 rAF | `Game.ts` |
| `alpha:false` + `desynchronized:true` 上下文 | `GameRenderer`（上下文创建） |
| 相机用 `setTransform` 而非 `save/restore` | `GameRenderer`（相机 `setTransform`） |
| 粒子池化 + 按类型批绘 + debris `setTransform` | `GameRenderer.renderParticles` |
| Performance Mode（DPR 1 + 30 FPS 渲染） | `PresentationLayer`（Performance Mode） |
| 逐帧分配消除（事件双缓冲、Camera._offset 复用、帧戳标记清除） | DECISIONS §21–§26 |
| PerfOverlay（frame/sim/render/draws/GC p95 + breach 日志） | `PerfOverlay.ts` |

`Performance-Observatory.md` §2 曾断言："在其上再叠一套脏矩形系统是没有收益的复杂度（违反 Gate 2）。"
该断言针对**地形层**（地形缓存即脏矩形）。**本 plan 不把它当作既定真理**——
地形层脏矩形的实际开销同样由 R0 测量；若数据表明其重建路径或覆盖范围本身是高成本点，
则它进入优化候选，与 R5 其它项用同一方法决策。至于**动态实体层**（坦克/子弹/粒子）是否值得脏矩形，
作为 R5 候选 D，由 R0 实测决定（见 §7）。

**本 plan 不承接任何"预设排除"，也不承接"基线必正确"。** 任一基线项若被 R0 基准证实为热点，
即按与热点项相同的流程优化；最终取舍一律以实测数字 + Three Gates 为准。

---

## 3. 渲染管线实测核验（基线事实）

对当前 HEAD 的渲染管线逐条代码核验，以下为关键事实（均为本 plan 自测结论，不引用任何外部文档）：

| 常见误判 / 旧认知 | 实测事实 | 本 plan 处置 |
|---|---|---|
| `allTanks` getter 每帧重建 3 次且分配 `_allTanksBuf` | 每渲染帧 **sig-changed 路径 4 次 / forceRender 路径 3 次**（`computeSceneSig` 在 `shouldRender` 与 `recordRendered` 各一次，外加 `renderTanks` 与 `updateVisualState` 各一次）。getter **不分配** —— `World.allTanks` getter（`buf.length = i` 复用缓冲区）。路径分解见 §4 P1-A/P1-B | 列 P1（冗余 O(n) 拷贝，非 GC 压力） |
| 每帧调用 `setTheme` 是性能隐患 | `GameRenderer.setTheme` 引用比较早退，成本 ≈ 0 | 维持现状，列入 §4 待 R0 实测确认项（非排除） |
| 每坦克约 3 对 `save/restore` | 实际 **5–6 对**：`drawSvgCentered` 自带 1 对，starbuf/hit 叠加再 1 对 | 列 P1 |
| `computeSceneSig` 只算一次 | **仅 sig-changed 路径**每渲染帧跑 **2 次**全量 O(tanks+bullets+powerups) 扫描；`forceRender`/`_needRender` 路径只跑 1 次（burst 场景走 forceRender，不受益）。路径分解见 §4 | 列 P1（原分析遗漏项；burst 不受益） |

**选型关键事实**：`@napi-rs/canvas` 是 **Skia** 后端（与 Chromium 同一光栅化引擎），详见 §5.1。

**附带文档修正（E2，已落地）**：`PerfOverlay` 快捷键实际是 **Alt+D**（F6 现绑 frenzy）。早期注释在 `Game.ts`、
`PerfOverlay.ts`、`GameRenderer.ts`、`UIManager.ts`、`ControlCenter.ts`、`main.css` 多处误写 F6；已统一更正为 Alt+D。
此为低风险文档修正，与 R0 的基准工具交付物无关。

**工具现状**：仿真侧无独立的 `analyze-profile.ts`（已并入 `profile-and-analyze.ts`）；渲染基准从零新建（见 R0），其输出风格对齐 `bench-all-stages.ts`。

---

## 4. 已验证的瓶颈清单

按实测严重度排序。所有行号对应当前 HEAD。

### P1-A · `computeSceneSig` 在 sig-changed 路径双跑（burst 不受益）

`shouldRender` 的 sig-changed 分支（`computeSceneSig` 调用点）调一次，`recordRendered` 内部（`computeSceneSig` 调用点）再调一次 ——
**仅当走 sig-changed 路径时成立**。`forceRender` / `_needRender` 路径只走 `recordRendered`，只算 1 次。

影响域（实测 `shouldRender` 控制流）：

- `idle` / `combat`（子弹移动 → sig 必变）：sig-changed 路径 → **2 次** ✗ 冗余
- `burst`（3 爆炸 + 60 粒子）：`forceRender` 路径 → 仅 **1 次**，本优化**不受益**

每次扫描遍历全部 tank（含 `allTanks` getter 拷贝）+ bullets + powerups。

**修法（零新字段，review E1）**：给 `recordRendered` 加可选参数 `sig?: number`，
sig-changed 分支在 `:517` 算一次后传入，`:538` 直接用；`forceRender` / `_needRender` 不传（仍自算）。
不引入新字段，符合"少一个字段更简单"。

### P1-B · `allTanks` getter 每渲染帧：sig-changed 4 次 / forceRender 3 次

调用点（每渲染帧）：`GameRenderer.renderTanks`（`:567`）1 次、`computeSceneSig`（`:568`，随 shouldRender 路径
1 或 2 次）、`updateVisualState`（`:615`）1 次。

| 路径 | 修复前 | P1-A 后 | P1-A + P1-B 后 |
|---|---|---|---|
| sig-changed | 4 | 3 | 2 |
| forceRender / _needRender | 3 | 3 | 2 |

（P1-A 只消除 sig-changed 路径的重复扫描；P1-B 透传后渲染入口取一次、renderer 与 updateVisualState 共享，
两条路径都降到 2 次。）

**修法**：`PresentationLayer.render` 入口取一次 `world.allTanks`，透传给 `renderTanks` 与 `updateVisualState`，
`computeSceneSig` 也接收该数组而非再调 getter。AGENTS §14.6 已列 `allTanks` 反复调用为反模式。

### P1-C · 粒子 5 趟全池扫描

`GameRenderer.renderParticles`（5 趟循环），每趟 × `activeCount`，每趟 `if (!p.active || p.type !== X) continue`。
`ParticleSystem.ts` 只有 `pool` / `activeCount` / `freeList`，**无 per-type 分桶**。
爆炸爆发期 `activeCount` 顶到 ~30+，迭代量 5N。

**修法**：`ParticleSystem` 维护 per-type 紧凑索引数组，emit 时入桶、update 时压缩。迭代量 5N → N。

### P1-D · 每坦克 5–6 对 `save/restore`

| 调用点 | 行号 | 条件 |
|---|---|---|
| `drawTankShadow` | 504/509 | 恒定 |
| `drawSvgCentered` | 137/142 | 每次 SVG 绘制；starbuf/hit 叠加时再一对 |
| `drawInsignia` | 880/884 | 非指挥官且 level≠none，**旋转恒为 180°** |
| `drawAllyAura` | 832/852 | 友军 |
| `drawHpLevelAura` | 1306/1416 | hpLevel 2–6 |
| `drawCommanderAura` | 1431/1483 | 指挥官 |

Canvas2D 的 `save()` 分配 graphics state 并压栈，V8 无法消除。6 坦克 × 5.5 对 ≈ 33 次/帧。

**修法**：insignia 的 180° 旋转烘焙进 `SpriteCache`；shadow 改 `setTransform` + scale；
aura 静态路径预渲染成位图，逐帧只改 `globalAlpha`。

> ⚠️ **有损项（按 §6 有损流程论证，review B1）**：aura 预渲染位图 + `globalAlpha` 合成与原逐帧 path 绘制，
> 在抗锯齿 / alpha 混合精度上可能产生 1-bit 差异，会使「像素 diff = 0」硬约束不成立。实现时若发现差异，
> 必须按 §6 的"有损项单独论证"流程处理（DECISIONS.md 论证 + 视觉对比），不得静默放过。

### P2-A · 逐帧 gradient / measureText 分配

- `SpriteArtist.drawPowerUp` —— 每道具每帧 `createRadialGradient` + 3× `addColorStop`
- `SpriteArtist.drawCommanderAura` —— 同上
- `SpriteArtist.drawPowerUpCountdown` —— 每帧 `ctx.font` 写入（字体重解析）+ `measureText`

绝对量小（道具 0–1 个、指挥官 0–1 个），但是热路径短命对象 → minor GC。

**修法**：gradient 按 `(theme, pulse bucket)` 缓存；数字宽度按 digit 预量一次缓存。

### 待 R0 实测确认的项（非预设排除）

以下项不在 R1–R4 的必改清单里，但**不作为既定排除**——最终处置以 R0 基准数字为准：

- `setTheme` 每帧调用：`GameRenderer.setTheme` 现为引用比较早退，成本 ≈ 0。**若 R0 显示其实际占比显著，则纳入修复**；否则维持。
- `drawSteel` / `drawBrick` 极重但只在 terrain cache 重建时跑，增量更新已覆盖。仅当基准显示重建频率异常高时才重新评估。
- `Camera.getOffset` 的 2× `Math.random()` —— 纯展示（AGENTS §2.3 允许）；shake 期间强制重绘是设计意图，维持。

---

## 5. 里程碑 R0 —— 无头渲染基准（阻塞项）

**目标**：让 agent 能在不启浏览器的前提下，确定性地测量 `GameRenderer.render()` 的真实绘制耗时。

### 5.1 技术选型：`@napi-rs/canvas`

**已实测通过**（本机 Bun 1.3.14 / Windows x64 / `@napi-rs/canvas@1.0.3`）：

```
OK  getContext(alpha:false)   OK  setTransform            OK  save/restore
OK  globalAlpha               OK  createRadialGradient    OK  measureText
OK  ellipse + arc             OK  roundRect               OK  drawImage(canvas→canvas)
OK  translate/rotate          OK  createPattern           OK  getImageData/putImageData
OK  shadowBlur + GCO          OK  setLineDash             OK  Image + SVG buffer 解码
```
16/16 通过。1000 帧合成负载 26.4ms（0.026 ms/帧）—— 跑数千帧基准毫无压力。

**为什么是它而不是 mock canvas**：它是 **Skia** 后端，与 Chromium 同一光栅化引擎（常被误记为 Cairo）。
mock canvas 只能测 JS 侧编排开销，测不到 `drawImage` blit、gradient 光栅化、`save/restore` 的真实成本
—— 而 §4 的 P1-D / P2-A 恰恰是原生调用成本。绝对值仍不能外推到浏览器（无 GPU 合成、无
`desynchronized` 路径），但**相对排名与优化增量可信**，这正是 CI 门禁需要的。

> **新增依赖需人类批准（AGENTS §6.5）—— 已获批准（2026-07-31）。**
> 列为 `devDependencies`，不进 `bun run build` 产物。需在 DECISIONS.md 记录。

### 5.2 接缝：不改生产代码

已勘查出三处需要适配，**全部可在 harness 侧用全局 shim 解决**：

| 接缝 | 现状 | harness 对策 |
|---|---|---|
| `GameRenderer` 构造函数调 `canvas.getContext('2d', {alpha,desynchronized})` | napi 的第二参是 boolean | 薄适配器包一层，转译参数 |
| `utils/canvas.ts`（OffscreenCanvas / document 运行时检查处） | Bun 中均不存在 → 抛错 | 导入渲染器**之前**装 `globalThis.OffscreenCanvas` shim（napi `createCanvas` 支撑） |
| `SpriteLibrary.load()` 走 `new Image()` + Vite `?url` | `src/assets/sprites/index.ts` 是 `?url` 导入，Bun 无法解析 | harness 直接从磁盘读 `src/assets/sprites/*.svg` 喂给 napi `Image`，绕开 index.ts |

第三条需要一个注入点：`SpriteLibrary` 的 `images` 是私有的且无 setter。
**唯一允许的生产代码改动**：新增 `loadFromSources(map: Record<string, CanvasImageSource>): void`（约 4 行，
纯展示层，与既有 `loadFromUrls` 同构）。

**shim 作用域隔离（review B4）**：`utils/canvas.ts` 若含 `typeof OffscreenCanvas !== 'undefined'` 之类运行时检查，
直接挂 `globalThis` 会让生产代码在 Bun 下误走 shim 分支，产生难以察觉的行为分叉。对策：
- shim 以 `globalThis.__RENDER_BENCH__ = true` 标志位守护，harness 启动首行设置；
- 在 `utils/canvas.ts` 等生产路径加 dev-only 守卫 `if (globalThis.__RENDER_BENCH__) throw new Error('shim 泄漏到生产')`；
- shim 仅存在于 `tools/perf/`，生产 `bun run build` 产物零引用（由体积守卫确认）。

> 实际落地：`utils/canvas.ts` 等生产路径**未加** dev-only `assert(!__RENDER_BENCH__)` 守卫。原因：shim 仅由 harness 安装、只存在于 `tools/perf/`，生产 `bun run build` 产物零引用（体积守卫确认），不存在"泄漏到生产"的路径；若在生产路径加该断言，反而会在 harness 合法地于 `__RENDER_BENCH__` 下运行生产渲染代码时误伤。隔离已由"shim 仅存于 tools/perf + 体积守卫"保证，不依赖生产侧断言。

### 5.3 交付物

```
tools/perf/render-bench.ts        # 主基准：确定性 World fixture 驱动 N 帧，输出分阶段耗时 + draw-call 数
tools/perf/headless-canvas.ts     # napi canvas 全局 shim + SpriteLibrary 磁盘加载
tools/perf/fixtures/render-*.ts   # 四个确定性场景（见下）
```

**四个固定场景**（RNG 固定种子，帧序确定）：

| 场景 | 构成 | 压什么 |
|---|---|---|
| `idle` | 关卡开局，6 坦克、0 子弹、0 粒子 | 稳态基线 |
| `combat` | 6 坦克（含 1 指挥官 / 1 友军 / 各 hpLevel）、8 子弹、2 道具 | P1-A/P1-B（sig-changed 路径）、P1-D / P2-A |
| `burst` | combat + 3 处爆炸同帧、粒子 activeCount ≈ 60 | P1-C（forceRender 路径，P1-A 不受益） |
| `pan` | 固定相机持续位移 2 秒（recovery 滑动 / 关卡切换动画） | 整屏重绘 + terrain cache 重建频率 + 全屏 blit 成本（review C1） |

**输出格式**（对齐 `bench-all-stages.ts` 的风格）：

```
scene      frames   wall(ms)   perFrame(ms)   p95(ms)   drawCalls/f   saveRestore/f
idle         2000      412.3        0.206       0.241          38            12
combat       2000      938.7        0.469       0.612         117            41
burst        2000     1604.2        0.802       1.133         283            41
pan          2000        —            —            —            —            —   (R0 实测填入)
```

`drawCalls` / `saveRestore` 由 harness 侧的计数代理统计（复用 `GameRenderer.setDrawCallCounting`
的思路，扩展到全方法），**不依赖 Skia 时序**，因此是最稳定的回归信号。

### 5.4 R0 完成标准

> **R0 已于 2026-07-31 完成。** 见下方「R0 落地附注」。基线数字见 `docs/render-optimization.progress.md`。

- [x] `bun tools/perf/render-bench.ts` 在无浏览器环境下跑通**四个场景**（idle / combat / burst / pan）
- [x] **确定性信号**：同一命令连跑 3 次，`drawCalls` / `saveRestore` **字节一致**；预热前 200 帧丢弃 ✅（repeatDraw/repeatSR 三连相等）
- [x] **波动容差（review C2）**：`perFrame` 采用 **中位数 + IQR + min** 汇总，min 为主信号；连跑 3 次波动在 ±10% 内 ✅
- [x] **生产代码改动仅限 `SpriteLibrary.loadFromSources`**；F6→Alt+D 注释修正已落地（review E2）✅（`loadFromSources` 已加；注释已统一更正为 Alt+D）
- [x] `@napi-rs/canvas` 进 `devDependencies`，`bun run build` 产物体积不变 ✅（devDep only）
- [x] 基线数字写入 `docs/render-optimization.progress.md`（新建，渲染侧专用）；**R0 基线 = R0 提交时点的固定快照**（见 C4）✅
- [x] **tsconfig 覆盖确认（review C5）**：`tools/perf/*.ts` 在 `tsconfig.json` include 范围内 ✅（`include: ["src","tests","tools",...]`，无需 `tsconfig.tools.json`）
- [x] `bun run check` 绿 ✅（tsc 0 / oxlint 0 error / oxfmt / bun test 644 pass 0 fail）

**R0 未完成前，不得开始 R1–R5。**

#### R0 落地附注（实现期发现，补充 §5.3）

- **每场景独立子进程驱动**：`render-bench.ts` 单进程连跑四场景时，Skia 软件后端在渲染多个不同 World 于同一 surface 上 ~12000 帧后**原生崩溃（exit 127，无 JS 栈）**。根因是 harness/环境的 Skia 限制，**不是游戏 bug**。对策：`render-bench-all.ts` 用 `spawnSync` 为 `[idle, combat, burst, pan]` 各起一个独立 `bun` 子进程跑 `--only=<scene>`，各自写 `results/<scene>.json`，父进程聚合打印。单进程内反复 `--repeat` 同一场景不触发该崩溃（已验证稳定）。
- **`loadFromSources` 实际用法**：harness 的 `buildLib` 现在构造**真实 `SpriteLibrary`** 并通过 `loadFromSources` 注入磁盘解码的 SVG，使基准走的是与生产相同的精灵加载代码路径（非 duck-typed 克隆）；实测 draw-count 与早期 duck-typed 版完全一致（181/216/267），证明早期探测亦可信。
- **`drawCalls/f` 实测远超 plan §5.3 占位（38/117/283 → 181/216/267）**：占位被推翻，R0 即为此存在。idle 仅 6 坦克已达 181 draw/frame，是 R2/R3/R5 的优化杠杆。

---

## 6. 里程碑 R1–R4 —— 已定位热点

每个里程碑独立可提交，**各自附 R0 基准的前后数字**。

| 里程碑 | 内容 | 目标信号 |
|---|---|---|
| **R1** 帧编排 | P1-A sig 透传（可选参数，零新字段）+ P1-B allTanks 透传 | `combat`（sig-changed 路径）perFrame ↓；`allTanks` 调用 sig-changed 4→2 / forceRender 3→2 |
| **R2** 粒子分桶 | P1-C `ParticleSystem` per-type 索引数组 | `burst` perFrame ↓；粒子迭代 5N→N |
| **R3** 坦克绘制状态 | P1-D insignia 烘焙 / shadow setTransform / aura 预渲染 | `combat` `saveRestore/f` 显著下降 |
| **R4** 逐帧分配 | P2-A gradient + 数字宽度缓存 | `combat` perFrame ↓；GC 计数下降 |

> **实际落地偏差（详见 `docs/render-optimization.progress.md`）**：R2 仅实现 `count===0` 早退 + `drewDebris` 标志，未做 per-type 分桶（5N→N）；burst perFrame 已 ~1.15ms（占帧预算 7%），分桶维护成本收益递减，Gate 2 不证成。R4 仅实现 `measureText` 宽度缓存，未做 gradient 缓存；改为 R4-glow 道具光晕位图预渲染（16 pulse bucket），更彻底地消除逐帧 `createRadialGradient` 分配（见 progress R4-glow）。

> **实现期追加项（非本 plan 原里程碑列表）**：优化推进中额外交付了下列工作，已记入 `docs/render-optimization.progress.md`，但不在 §6 的 R1–R4 表中：
> - **R3 → R3+ 扩展**：aura 位图预渲染（16 pulse bucket），在 R3 手写属性保存基础上进一步把逐帧 path 光栅化换成 1 次 blit（见 progress R3+）。
> - **R4 → R4-glow 扩展**：道具光晕 `createRadialGradient` 位图预渲染（16 pulse bucket），比单纯缓存 gradient 更彻底（见 progress R4-glow）。
> - **P0 / P1 / P2 热路径分配清理（批）**：源自 AGENTS §14 的"禁止热路径数组/对象分配"硬规则，而非本 plan 的瓶颈分析。包括 SpriteCache/SpriteArtist 缓存键数组化、增量地形重绘零分配（`_dirtyMark`+`_dirtyList`）、`computeSceneSig` allTanks 缓存 + `AnimationSystem.forEach` + `neighborMask` 复用 + `for-of`→`for-i`。均为 lossless，draw/saveRest 计数不变，收益维度是目标硬件（老机器）GC 稳定性，开发机不可见（见 progress P0/P1/P2）。
> - R5-A（静态层合并）/ R5-B（复合坦克位图）属 §7 候选池，已在候选 A/B 规划内，非追加项。

**每个里程碑的硬约束**：

- 像素输出不变。R0 harness 增加 `--snapshot` 模式导出 PNG，优化前后逐像素 diff 必须为 0
  （aura 预渲染等有损项若必然产生差异，需在 DECISIONS.md 单独论证并附视觉对比）。
- **`--snapshot` / `encode()` 运行策略（review B2）**：像素 diff 必须 `encode()` 成 PNG。但 `@napi-rs/canvas` 的
  `encode()` 历史上有内存泄漏，长基准跑里调用会污染 wall time。对策：**`--snapshot` 仅在 CI 的独立 step /
  隔离子进程中跑一次**（不在数千帧的主基准循环内调用），导出单帧 PNG 做 diff；主基准循环**绝不**调用 `encode()`。
- 不得往 `World` 里加任何字段（AGENTS §2.2 / §2.5，表现层状态可抛弃）。
- 不得在 Simulation 路径引入 `Math.random()`（AGENTS §2.3）。

---

## 7. 里程碑 R5 —— 架构级候选（实测驱动，无预设排除）

**原则**：R0 基准落地后，凡可能进一步降耗的架构级改动，一律先建微型基准、再决定取舍。
**本 plan 不预设任何排除项**——此前被判定"排除"或"降级"的方案，与从未讨论过的方案，
站在同一起跑线上，用同一种方法测量。

每个候选的**统一接受准则**：

1. R0 基准（或必要时的浏览器微型基准）显示**真实、可复现**的改善（draw-call 计数或 perFrame 下降，且非噪声）。
2. 通过 The Three Gates（§11）。
3. 像素输出受控（diff = 0 或已在 DECISIONS.md 论证）。
4. 引入的复杂度被实测收益证成（Gate 2 的"简单"以实测收益为证，不以直觉为证）。

### 候选池（全部开放；下表按预期风险列示，**非优先级、非排除**）

| 候选 | 预期风险 | 测量方法 | 备注 |
|---|---|---|---|
| A 静态层合并 | 低 | R0 分阶段计时 + draw-call 计数 | 背景+地形+暗角合成单张，fill+2blit→1blit；森林留坦克之上 |
| B 复合坦克位图 | 中 | R0 draw-call/saveRestore 计数 + `cacheEntries` 计数 | 缓存键惰性构建 + LRU 上限（128）；脉冲 alpha 不烘焙 |
| C 精灵图集 | 中 | **R0 测不出**（软件光栅无纹理绑定）→ **需浏览器端纹理绑定微型基准（人工项，见 B3）** | 配 PerfOverlay 探针；由 agent 提探针方案、人类跑浏览器基准（AGENTS §5 禁 agent 启浏览器），否则无法判定 |
| D 动态层脏矩形 | 中高 | 前置门槛（见下 D1）+ R0 动态层重绘面积统计 + 浏览器 p95 交叉 | 此前被排除；现以实测重审（见 §2 说明） |
| E 渲染命令列表 / 状态排序批处理 | 高 | R0 draw-call 计数 + 状态切换计数 | 此前被排除；**预期放弃，除非状态切换 >200 switches/frame（见 D2 门槛）** |

**候选前置门槛（避免为注定无收益项投入开发，review D）**：
- **D 动态层脏矩形（D1）**：先**估算脏矩形系统的最小固定开销**（每帧矩形合并 + 裁剪设置 + 跨层协调），
  若该开销 > 当前动态层绘制成本的 **50%**，直接放弃，不进入微型基准阶段（Gate 2 要求净简化）。
- **E 渲染命令列表（D2）**：预期放弃，除非 R0 显示**状态切换计数异常高（>200 switches/frame）**；否则不投入微型基准。

**人工执行项（review B3）**：R5-C 的浏览器微型基准违反 AGENTS §5（agent 不得启浏览器 / dev server），列为人工执行项——
agent 提出探针方案与预期信号，交人类在浏览器内跑 PerfOverlay 采集，结果回填 `docs/render-optimization.progress.md`。
R5 任一候选的浏览器交叉验证同理走人工通道。

**WebGL / WebGPU**：属 MANIFEST §14 硬性出界，是项目 creed 约束而非本 plan 的实证排除，单列于此，不进入候选池。

### 决策流程（每个候选独立，无全局一票否决）

```
候选 → 写微型基准 / 探针 → 跑 R0（或浏览器交叉）→ 有真实改善且过 Gates? → 是 → 并入并记 DECISIONS / 否 → 放弃并记结论
```

不存在"全局预算阈值触发才做"的开关；任一候选只要自证有效即可采纳，自证无效即放弃，结论均入档。

---

## 8. Definition of Done

### 测量（R0）

- [ ] `bun tools/perf/render-bench.ts` 可在纯 Bun 环境跑通，三场景基线入档
- [ ] `drawCalls` / `saveRestore` 计数确定性可复现
- [ ] `--snapshot` 像素 diff 模式可用

### 优化（R1–R4，逐项）

- [ ] 每个里程碑提交时附「前 → 后」基准数字与 draw-call 差值
- [ ] 像素 diff = 0（或差异已在 DECISIONS.md 论证）
- [ ] `bun run check` 绿，`bun run build` 成功
- [ ] `World` 无新增字段；Simulation 路径无新增 `Math.random()`
- [ ] 无新 UI 画到 game canvas（AGENTS §2.5）

### 验收门禁（CI，agent 自验，分层，review C3）

- [ ] **drawCalls / saveRestore 计数（确定性）**：相对 R0 基线 **0 回退**，硬门禁，超阈值 exit 非 0；
- [ ] **perFrame（wall time，含 Skia 噪声）**：设 **±X% 容差**（X 由 R0 实测波动反推，预计 ≤10%），或仅作**报告项**不作门禁——
  避免 >5% 噪声导致随机 CI 失败（对齐 `sim-bench.ts` 的既有做法，但只对确定性信号设硬门）

> 原"浏览器校准（人工）"门禁已移除：无头 `@napi-rs/canvas`(Skia) 基准的 draw-call 计数是 DPR 不敏感的确定性主信号，足以作为 CI 门禁；人工浏览器 p95 验证改为按需、非阻塞项。

### 目标数字

**R0 完成前不定阈值。** `Performance-Observatory.md` §5 明确批评过早期方案「18ms/12ms 阈值无测量依据」
的做法。R0 三场景基线落地后，用实测反推合理目标，写进本节并同步 DECISIONS.md。

**基线版本化（review C4）**：R0 基线 = **R0 提交时点的固定快照**，作为长期参考长期保留；R1–R4 各自的「前 → 后」
对比在**各自提交内**进行，不直接与 R0 基线比较（渲染代码变更会使 R0 绝对数字漂移，属预期）。
进度文档 `docs/render-optimization.progress.md` 的基线表锁定 R0 值，各里程碑对比表独立记录。

---

## 9. 风险

| 风险 | 缓解 |
|---|---|
| Skia 时序与 Chromium 不一致，优化方向被误导 | 以 `drawCalls`/`saveRestore` 计数为主信号（与后端无关），wall time 为辅；每里程碑后人工 PerfOverlay 校准 |
| `@napi-rs/canvas` `encode()` 内存泄漏污染 wall time（review B2） | `--snapshot`/encode 仅在 CI 隔离 step / 子进程跑**一次**，主基准循环绝不调用；已实测 16/16 API 通过 |
| `tools/perf/` 逃过类型检查（review C5） | R0 启动前确认 tsconfig include 覆盖；否则加 `tsconfig.tools.json` 并纳入 `bun run check` |
| R5-B 缓存键爆炸 | 惰性构建 + 128 张上限 + LRU + harness `cacheEntries` 计数验证 |
| R5-C 精灵图集 R0 测不出，易凭直觉误判 | 不靠 R0 决定；必须配浏览器端纹理绑定微型基准（人工项，见 §7 B3）才采纳 |
| R5-D 动态层脏矩形此前被断言"无收益"，需实测证伪/证实 | 先过 D1 固定开销前置门槛（>50% 直接放弃），再走 R0 重绘面积统计 + 浏览器 p95 交叉，结论入档 |
| 优化引入视觉回归 | 像素 diff 强制为 0；有损项（如 R3 aura 预渲染）须单独论证 |
| harness shim 误入生产代码分支（review B4） | shim 仅存于 `tools/perf/` 且只由 harness 安装；生产 `bun run build` 产物零引用（体积守卫确认）；生产路径未加 dev-only 断言（会误伤 harness，见 §5.2） |

---

## 10. 需要写入 DECISIONS.md 的条目

按 AGENTS §6.3 格式，**执行前**写入：

1. **引入 `@napi-rs/canvas` 作为渲染基准的 devDependency** —— 记录 Skia vs mock canvas 的取舍、
   为何绝对值不外推、为何不进构建产物。
2. **`SpriteLibrary.loadFromSources` 注入点** —— 记录为何这是唯一必要的生产代码改动。
3. **渲染回归门禁以 draw-call 计数为主信号** —— 记录为何不用 wall time 做 CI 阈值。
4. （采纳任一架构级候选时）**该候选的取舍结论 + 关键参数**（如复合坦克位图缓存上限/淘汰、精灵图集浏览器实测数字、动态层脏矩形重审结论等）。无预设排除，每个候选的"做/不做"结论均入档。
5. （若出现）**任何像素 diff ≠ 0 的有损优化的视觉论证**。

---

## 11. The Three Gates (MANIFEST §13)

- **Gate 1（更好玩）**：低端机上稳定 60 FPS 直接服务于"五分钟"。R0 本身是开发工具，
  但它是让后续每一项优化可验证的前提。**通过**。
- **Gate 2（架构更简单）**：R1–R4 全是局部改动，净效果是减少代码路径（少一趟扫描、少一次遍历、
  少一层 save/restore）。R5 改为**实测驱动候选池、无预设排除**——每个候选须自证实测收益后才过 Gate 2，
  复杂度由数据证成而非直觉否定。**通过**。
- **Gate 3（尊重原作）**：像素 diff 强制为 0，视觉零变化。**通过**。

3/3。

---

## 12. 执行顺序

```
R0 ──→ R1 ──→ R2 ──→ R3 ──→ R4 ──→ R5 候选池（A/B/C/D/E 各自独立建微型基准 → 实测决策 → 入档）
(阻塞)
```

每个里程碑独立提交。R0 之后的任何一个里程碑都可以单独中止而不留半成品。
R5 各候选互不阻塞：某候选实测无效即放弃并记结论，有效即并入，不拖住其余候选。

---

## 13. 修订记录（自审追溯）

本 plan 在一轮自审中修正了下列技术事实 / 内部矛盾 / 实现细节，映射如下（修订项编号仅作内部追溯）：

| 修订项 | 处置 | 落点 |
|---|---|---|
| A1 `computeSceneSig` 仅 sig-changed 双跑 | 改写 P1-A，标注 burst(forceRender) 仅 1 次不受益；目标信号改为 combat perFrame | §3 表 / §4 P1-A |
| A2 allTanks 调用数路径分拆 | 给出 sig-changed 4→2 / forceRender 3→2 路径表 | §3 表 / §4 P1-B / §6 R1 |
| B1 像素 diff 0 vs aura 预渲染有损 | R3 aura 预渲染显式标注为「有损项」，走 §6 有损流程 | §4 P1-D |
| B2 encode 泄漏 vs --snapshot | 定义 --snapshot 仅 CI 隔离 step/子进程跑一次，主循环禁 encode | §6 硬约束 / §9 风险 |
| B3 R5-C 浏览器禁令冲突 | R5-C 标人工执行项；agent 提探针、人类跑浏览器 | §7 候选 C / 人工执行项 |
| B4 shim 作用域 | `__RENDER_BENCH__` 守卫 + 生产 dev-only assert | §5.2 |
| C1 缺相机移动场景 | 新增第 4 场景 `pan` | §5.3 |
| C2 ±5% 过严 | 放宽 ±10% + 中位数/IQR/min + 丢弃前 200 帧预热 | §5.4 |
| C3 CI 门禁噪声敏感 | 分层：计数 0 回退硬门；perFrame ±X% 容差或仅报告 | §8 DoD |
| C4 基线版本化 | R0 基线 = 固定快照；R1-R4 各自提交内对比 | §5.4 / §8 目标数字 |
| C5 tsconfig 覆盖 | R0 启动前确认 include，否则加 tsconfig.tools.json | §5.4 / §9 风险 |
| D1 R5-D 复杂度 | 加 D1 固定开销前置门槛（>50% 直接放弃） | §7 候选 D / 前置门槛 |
| D2 R5-E 临界点 | 标预期放弃，>200 switches/frame 才投入 | §7 候选 E / 前置门槛 |
| E1 P1-A 更简修法 | recordRendered 加可选 sig 参数，零新字段 | §4 P1-A |
| E2 F6→Alt+D 注释修正 | 已落地，全局注释统一为 Alt+D | §3 / §5.4 |
