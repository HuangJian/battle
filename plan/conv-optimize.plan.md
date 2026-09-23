# conv-optimize.plan.md —— features 内核优化：调研结论 + 实施简报

> **落地状态（2026-09-23）：Stage 1–4 全部落地**（内核四项 + 单源双目标 + 目录重组 + goal/intent 入池）——
> 本批提交 = `git log --oneline -- src/nn/conv` 的首条（标题含 `plan conv-optimize`；**本文不写死 hash**，
> 免得 amend 后自引用过期）。**§0.3 已全部勾完** —— arm64（Redmi K30 Pro · proot）实机计时 + 逐项归因也已跑完：
> 整批 **≈+5%**（wasm **1.28–1.30×**）、`pw8`/`nog3` ≈1.00 ⇒ **无一项为负、无需回落**，但 x64 的 +44% 不迁移（§11.4）。
> 本文下文提到的 `src/nn/native/**`、`src/nn/wasm/**` 是**重组前的路径**；最终布局全在 `src/nn/conv/**`
> （`conv.c` 单源 · `conv_native.h`/`conv_wasm.h` · `conv.ts`/`conv_native_adapter.ts`/`conv_wasm_adapter.ts`/
> `conv_ts.ts` · `native-prebuilt.ts` 分发矩阵 · `prebuilt/**`）。
>
> **落地结果一览**（本机 x64，真 FFI + 真权重）：native `2.31→1.64 ms`（**1.40–1.46×**）·
> wasm `6.03→3.59 ms`（**1.67–1.71×**）；linux-x64（WSL）native **1.55–1.59×** · wasm **1.67–1.71×**。
> ⚠ 上面是**内核**口径；**端到端同日补测**（真导出器，同 stage/seed/权重，唯一变量 = 内核二进制）：
> 生产形态 `--max-ticks 12900 --seeds 0-3` = `4275 → 2897 ms` = **1.48×**（扣启动 1.49–1.52×）；
> 定长 4×2500 = **1.40×** · 单局 494 决策 = **1.30×** —— 三档差异来自每进程固定成本的摊薄比例，详见 §11.2。
> 实测/门禁/偏差的完整记录在 §11；**本文档现址 = `plan/conv-optimize.plan.md`**
> （2026-09-23 从 `src/nn/conv/` 移出：`src/nn/**` 在 node codeHash 集内、F3 过滤不排除 `.md`
> ⇒ 每改一次本文都会触发一次节点升级波；`plan/**` 在排除清单里）。
> 同日流水账 = `docs/nn/runtime-opt.md` §9–§11（拆分前的旧号 §140–§142）；
> 内核 A/B 探针 = `bun tools/perf/conv-ab.ts`（`CONV_AB_OLD_LIB` 可指向历史 prebuilt 产物）；
> **逐项归因**（「这一项在我的机器上值多少」）= `bun tools/perf/kernel-variants.ts` 生成变体库再拿探针对拍（§11.4）。

> **本文档是交给实现方的交接件。** 读者没有参与过调研，因此每一处结论都附了证据与复跑方式；
> 每一处「已否决」都写明**为什么不要再试**（有实测数字）。
>
> **仓库纪律先读**：`AGENTS.md §0.1`（13 条会咬人的规则）与 `§5`（硬规则）。与本文相关性最高的：
> `pwsh`（不是 `powershell`）、禁止 `git stash`/`git push`、commit 用临时消息文件、
> 长任务输出写文件、**"绿" = 门禁真的跑过**、绝不 `git add` 未跟踪的 `*.md`。
>
> **范围**：只动 `src/nn/native/**` 与 `tools/agent/native-build.ts` 相关产物。
> 本文档记录的全部调研**未改任何生产源码**（`git status` 只多出未跟踪的 md）。

---

## 0. 交付摘要（TL;DR）

### 0.1 任务边界

| | 内容 |
|---|---|
| **要做的** | 把 §2 的四项循环重排落进 `src/nn/native/conv_feats_native.c`，重出 6 目标 prebuilt，跨平台实机确认 |
| **不要做的** | §3 的八条已否决路线（每条都有实测数字，重开 = 浪费一轮） |
| **不改变的** | 任何**数值**（逐位不变）、ABI、权重布局、任务定义（命数/敌数/关卡形态/终局标准） |
| **预期收益** | 端到端 **1.41×**（已在本机用真权重 + 真 FFI 实测）；内核 **1.37×** |

### 0.2 四项待落地改动（速览）

| # | 改动 | 位置 | 单层收益 | 跨平台风险 |
|---|---|---|---|---|
| 1 | **pw `4oc×4px` → `4oc×16px`**（单源时由 `CF_PW_PX` 选：native/arm64 16、wasm 8；**已拍板**见 §4.2） | `conv_feats_native.c::conv1x1_res` | **−32%（16px）** | 低（AVX1/AVX2 实测都更快）；arm64 待实测 |
| 2 | **conv3 `4oc 分组 + 权标量预取`** | `conv3` | **−2.7~−4.4%** | ⚠ **中**（arm64 栈引用 18→102）⇒ 必须实机测 |
| 3 | **dw 权预取 `kw[25]`** | `conv5dw` | **−3~5%** | 低（arm64 方向一致） |
| 4 | **pad5 只清边框** | `pad5` | pad5 的 **−19~−28%**（= features 的 ~0.5%） | 低（写指令数上升，字节大降） |
| **5** | **（非内核）eval 每 tick 白编码 → 移进决策守卫** | `tools/sim/export-eval-game.ts:482` | **−20 ms/局 = eval 单局游戏时间 −4.2%** | **零**（输出逐字段相同，§4.7 有真 A/B） |

> 第 5 项**独立于内核**，可单独做、单独验收（改动 = 1 行移动）。它是训练路径 §368 提速③ 的**同一处修复，eval 漏改了**。

**合并端到端（本机实测，真 FFI + 真权重）**：`2.178 → 1.542 ms/forward`（**1.41×**），
50 局批 `11.26s → 7.96s`。等价性四重验证全过（见 §2.5）。

### 0.3 验收清单（Definition of Done）

- [x] `bun tools/agent/native-build.ts --cross` 重出 6 目标，`--check-prebuilt` 通过 —— ✅ 6 native + **wasm32** 同源
- [x] `bun run check` 绿（含 `tests/native-parity.test.ts` 的 native↔wasm **逐字节**对拍）—— ✅ 2126 pass / 0 fail
- [x] `bun run build` 绿 —— ✅
- [x] 一局 rollout 的 **npy sha256 与现役库对比** —— ✅ native 路径 vs `NN_NATIVE=0`（wasm 路径）逐字节相同，仅 manifest 的 `feat` 字段不同
- [x] eval 输出**逐字段相同**，且单局墙钟下降 —— ✅ 8 轮交错 min/median `494/504 → 470/485` ⇒ **−19 ms/局**
- [x] 四方 memcmp（native 旧/新 × wasm 旧/新）—— ✅ 全等（`bun tools/perf/conv-ab.ts`）
- [x] **arm64 实机计时 + 逐项归因**（Redmi K30 Pro · proot/Ubuntu，2026-09-23）：整批 **≈+5%**（钉单核 best-of-N：1.017/1.054/1.057/1.061）、
  wasm **1.28–1.30×**；`pw8`≈1.00 · `nog3`≈1.01 ⇒ **无一项为负**（**不回退、不拆三档**），但 x64 的 +44% 不迁移到 NEON（§11.4）
- [x] 改动记进 `.workbuddy/memory/YYYY-MM-DD.md`；若出现"决策"级取舍再写 `DECISIONS.md` —— ✅ `DECISIONS.md §2026-09-23-goalnn-conv-single-source`

### 0.4 硬性前置 / 阻塞

| 前置 | 说明 |
|---|---|
| **clang 20 + lld** | `--cross` 依赖 `-fuse-ld=lld`（macho/coff 只能靠 lld）。**调研机上没有 lld**，所以本批无法出 prebuilt ⇒ 必须在有 lld 的机器上做（训练机 / Windows 开发机） |
| **必须是 clang，不是 cl.exe** | 6 目标 flags 里没有 `-march=native`；Windows 目标也是 clang 交叉编译。若改用真 MSVC，x64 的 inline asm 等路径会变（本文不依赖 asm，但要知道） |
| **arm64 实机** | attestation 只保证**正确**、不保证**不变慢**。先例：`8oc×8px` 在 AVX1 快 1.4×、在 AVX2 **慢 2.9×** |
| **复跑手册的 harness** | `tmp/perf/**` 全部在 **gitignore 内**（不会随仓库走）。若要实现方能复测，需先决定如何交付（§7 给了清单与每个文件的作用） |

### 0.5 红线（跨平台契约，别碰）

1. **禁止 FMA 收缩**：`-ffp-contract=off` / `-fno-fast-math` 是「逐位一致」的必要条件（`native-prebuilt.ts:147` 明写）。
2. **不得重排累加顺序**：本次四项全部保持「每元素：bias → ic 升序 → relu → +residual」。任何改动都要过 §2.5 的四重等价性验证。
3. **`CF_ABI` 变更必须 +1**：不符时 JS 侧**静默回落**（native → wasm → TS）。症状是**性能悬崖，不是崩溃**。

---

## 1. 背景：为什么只有内核可优化

### 1.1 使命（来自 `MANIFEST.md §1`）

> 打开浏览器，玩五分钟，笑着离开。

性能优化的目标不是"更快的代码"，而是**让训练侧 LAN 集群的 CPU 别再被 features 占满**。
训练侧跑 native，部署侧跑 wasm（见 §4.2）。

### 1.2 Profile：一步 rollout 里 95% 在一个函数

口径：`x20-noexplore.it165.20260922-080001.json` 权重 + `ladder-c20-lives1` stage 2000 abcd
+ `hard` + `lives=1` + `max-ticks 12900`，与 `runOne` 逐语句同构的探针：

| 阶段 | ms | 占比 |
|---|---|---|
| **`model.forward`**（in16 拷贝 + `cf_student_features` + 3 个 linear 头） | **2878.5** | **95.2%** |
| `sim.tick()`（整个仿真） | 62.3 | 2.1% |
| `encoder.encode`（obs 编码） | 44.3 | 1.5% |
| 其余（slice / masks / 采样 / pu 普查 / events） | 37.2 | 1.2% |

**⇒ 调度、仿真、编码全加起来不到 5%。想再快只有内核。**

### 1.3 内核内部逐阶段（生产实现）

| 阶段 | µs/次 | % of features | MAC | 单层效率 |
|---|---|---|---|---|
| `pw ×8`（1×1，8 个 ConvMixer 块） | 1313 | **54.2%** | 22.15M | 17.5–19.2 GMAC/s = **35% of 峰值** |
| `conv3`（stem 3×3，18→64） | 634 | 26.1% | 7.01M | 18.3–18.7 = **35%** |
| `dw ×8`（5×5 深度） | 511 | 21.1% | 8.65M | 16.9 = **32%** |
| `pad5 ×8` | 42 | 1.7% | — | — |
| `GAP` | 30 | 1.2% | — | — |
| `pad3` | 1.6 | 0.1% | — | — |

（自洽校验：各部分和 2533µs vs 整 features 2424µs —— 同口径内一致。）

**「单层效率」的分母是实测真上限，不是理论值**（§7.2 `c3-rf2.c::peak_reg`）：

| 独立累加链 | 2 | 4 | 6 |
|---|---|---|---|
| GMAC/s | 22.6–23.4（加法延迟链） | 42.7–43.1 | **51.0–54.4** |

⇒ **真上限 ≈ 52.4 GMAC/s**（≈86% of 理论 16 lane-MAC/cyc）。**注意 4 链只有 ~43** —— 平铺形状决定链数，链数决定上限。

### 1.4 成本模型（已闭环验证，可直接当健康检查用）

实测（4 局均值）：

| 量 | 值 |
|---|---|
| 一局 tick 数 | **1398** |
| 决策次数（K=10） | **~140** |
| features 稳态 | **2.15 ms** |
| ⇒ 预测每局工作量 | 140 × 2.15 = **301 ms** |
| 实测每局工作量（1 进程 4 局扣固定成本） | **309 ms** ✓ |

**训练墙钟 = 局数 ×（决策数 × features + 每进程固定成本）。** 固定成本 ≈ **89–107 ms/进程**
（其中首用设置 43.7–57.1ms、权重加载 7–10ms、bun 启动 30–45ms）—— 这一项**已经被长驻池摊掉**（§4.6）。

### 1.5 为什么「到顶了」：端口与取数算术，而不是编译器手艺

以 **pw 4oc×8px** 为例，每 32 MAC（= 1 个 ic）的 uop（AVX1，无 FMA）：

```
5 个 load uop（1 输入载入 + 4 个权广播）  +  8 个 FP uop（4 mul + 4 add）
```

Zen 3（本机 5800H）只有 **2 个 load 端口** ⇒ `5/2 = 2.5 cyc / 32 MAC`
⇒ **load-port 上限 ≈ 12.8 MAC/cyc ≈ 41 GMAC/s（≈ 实测峰值的 80%）**。

| | 硬上限 | 实测 | 距离硬上限 |
|---|---|---|---|
| 8px（load-bound） | ≈41 GMAC/s | 34.9–35.6 | **~85%** |
| 16px（每 64 MAC 只 6 个 load uop ⇒ 转 FP-bound） | = 峰值 52.4 | 38.9–40.3 | **~75%** |

**⇒ 剩下的 15–25% 不是"代码没写好"，是端口数。** 这条结论直接否决了 §3.6（asm）——它加不出端口。

---

## 2. 已证实有提速的四项（逐项给确切代码）

> **通用约定**：改动只重排循环、**不动每元素的累加次序**（bias 起、ic 升序、relu 后加 residual）
> ⇒ 逐位不变 ⇒ **不是 new era**，由 `native-parity` + 一局 npy sha256 守护。
> 全部候选的源码在 `tmp/perf/xplat.c`（与生产同形，便于直接抄）。

### 2.1 pw：`conv1x1_res` 像素块 `4` → **`16`**（单源下 = `CF_PW_PX`：native/arm64 16、wasm 8，见 §4.2）

**机制**：1×1 没有 tap ⇒ 可以「K 像素 × G 输出通道」二维寄存器分块。块越宽，**每个权广播摊销到越多像素**
⇒ 取数:FP 比下降 ⇒ 从 load-port 受限推向 FP 受限。

| 形状 | 每 ic 取数 | 每 ic 向量-FP | 比值 | 寄存器 | 实测 GMAC/s |
|---|---|---|---|---|---|
| 生产 `4oc×4px` | 5 | 8 | 0.625 | 3 ymm | 17.5–19.2 |
| **`4oc×8px`（本次落地）** | 6 | 16 | 0.375 | 10 ymm | **31.8–33.0** |
| **`4oc×16px`（x64 最优，见 §4.2）** | 10 | 32 | 0.31 | 10 ymm | **35.1–37.4** |
| `4oc×32px` | — | — | — | 20 ymm ✗ 溢写 | 反向 |
| `8oc×8px` | — | — | — | — | AVX1 37.4 / **AVX2 3.8（崩 9×）** ✗ |

**落地代码**（`4oc×8px`；`CF_SP = 676`，`676 = 84×8 + 4` ⇒ **余数循环必须保留**）：

```c
/* 逐一对应 tmp/perf/xplat.c::pw_4x8 —— 注意：先 bias 初始化，再 ic 升序累加，最后 relu + residual */
static void conv1x1_res(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < CF_H; oc0 += 4) {              /* CF_H=64 ⇒ 必须整除 4（见 §4.3 脆弱点） */
    const f32 *w0 = w + (oc0 + 0) * CF_H, *w1 = w + (oc0 + 1) * CF_H;
    const f32 *w2 = w + (oc0 + 2) * CF_H, *w3 = w + (oc0 + 3) * CF_H;
    int p0 = 0;
    for (; p0 + 8 <= CF_SP; p0 += 8) {                    /* 主块：8 像素 */
      f32 a0[8], a1[8], a2[8], a3[8];
      for (int k = 0; k < 8; k++) {
        a0[k] = b[oc0 + 0]; a1[k] = b[oc0 + 1]; a2[k] = b[oc0 + 2]; a3[k] = b[oc0 + 3];
      }
      for (int ic = 0; ic < CF_H; ic++) {
        const f32* v = in + ic * CF_SP + p0;
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        for (int k = 0; k < 8; k++) {
          a0[k] += s0 * v[k]; a1[k] += s1 * v[k];
          a2[k] += s2 * v[k]; a3[k] += s3 * v[k];
        }
      }
      f32* o0 = resid + (oc0 + 0) * CF_SP + p0;
      f32* o1 = resid + (oc0 + 1) * CF_SP + p0;
      f32* o2 = resid + (oc0 + 2) * CF_SP + p0;
      f32* o3 = resid + (oc0 + 3) * CF_SP + p0;
      for (int k = 0; k < 8; k++) {                       /* relu 融合 + residual（顺序不变） */
        const f32 x0 = a0[k], x1 = a1[k], x2 = a2[k], x3 = a3[k];
        o0[k] += x0 > 0 ? x0 : 0;
        o1[k] += x1 > 0 ? x1 : 0;
        o2[k] += x2 > 0 ? x2 : 0;
        o3[k] += x3 > 0 ? x3 : 0;
      }
    }
    for (; p0 < CF_SP; p0++) {                            /* **余数 4 个像素**：CF_SP 不是 8 的倍数 */
      f32 a[4] = {b[oc0 + 0], b[oc0 + 1], b[oc0 + 2], b[oc0 + 3]};
      for (int ic = 0; ic < CF_H; ic++) {
        const f32 v = in[ic * CF_SP + p0];
        a[0] += w0[ic] * v; a[1] += w1[ic] * v; a[2] += w2[ic] * v; a[3] += w3[ic] * v;
      }
      for (int o = 0; o < 4; o++) {
        f32* op = resid + (oc0 + o) * CF_SP + p0;
        *op += a[o] > 0 ? a[o] : 0;
      }
    }
  }
}
```

**变 `16px`（= 本项目的落地形状）：把主块宽度 8→16（`a0..a3` 变 `f32 a0[16]`、内层 `k < 16`），余数循环不变。**
上面用 8px 写是为了让代码与 `xplat.c::pw_4x8` 逐行对得上；两个宽度**余数都是 4**（`676 = 84×8+4 = 42×16+4`）。

⚠ **证据强度不同，实现方要知道**：

| 形状 | 单层（iso） | 整管（in-pipeline） | **端到端 FFI** |
|---|---|---|---|
| `8px` | 31.8–33.0 GMAC/s | 73–77% of prod | ✅ **已实测 1.41×**（§2.5） |
| **`16px`** | **35.1–37.4** | **68.3% of prod** | ❌ **未实测**（预计 ~1.5×） |

⇒ Stage 1 完成后**自己量一次真 FFI 端到端**，不要直接引用 1.41× 当 16px 的数字。
x64 实测 16px 比 8px 再快 ~10–14%（§7.3 `pw-asm.c`：C/A = 110–115%）。

### 2.2 conv3（stem）：4 输出通道分组 + 权标量预取

**机制**：9 个输入值被 4 个 oc 共用 ⇒ 每组只需一次输入载入；4×9=36 个权标量进局部数组（预取）。

**收益小（−2.7~−4.4%）**，但代价是 **arm64 上栈引用 18→102、mem +14%（NEON 溢写）** ⇒ **必须实机测**。

```c
/* 逐一对应 tmp/perf/xplat.c::c3_oc4（只替换 conv3 的第二个循环，前置 bias 填充与尾部 relu 不变） */
for (int ic = 0; ic < inCh; ic++) {
  const f32* src = pad + ic * w3 * w3;
  for (int oc0 = 0; oc0 < outCh; oc0 += 4) {
    f32 wr[4][9];
    for (int o = 0; o < 4; o++)
      for (int k = 0; k < 9; k++) wr[o][k] = w[((oc0 + o) * inCh + ic) * 9 + k];
    for (int oh = 0; oh < CF_BOARD; oh++) {
      const f32 *r0 = src + oh * w3, *r1 = r0 + w3, *r2 = r1 + w3;
      for (int ow = 0; ow < CF_BOARD; ow++) {
        const f32 x00 = r0[ow], x01 = r0[ow+1], x02 = r0[ow+2];
        const f32 x10 = r1[ow], x11 = r1[ow+1], x12 = r1[ow+2];
        const f32 x20 = r2[ow], x21 = r2[ow+1], x22 = r2[ow+2];
        for (int o = 0; o < 4; o++) {
          const f32* wrr = wr[o];
          f32 acc = 0.f;
          acc += wrr[0]*x00 + wrr[1]*x01 + wrr[2]*x02;
          acc += wrr[3]*x10 + wrr[4]*x11 + wrr[5]*x12;
          acc += wrr[6]*x20 + wrr[7]*x21 + wrr[8]*x22;
          out[(oc0 + o) * CF_SP + oh * CF_BOARD + ow] += acc;   /* 累加序不变 */
        }
      }
    }
  }
}
```

### 2.3 dw（5×5 深度）：25 个权标量预取

**机制**：原实现每像素从 `wrow` 重新载 25 个权标量；预取进 `kw[25]` 后载入次数降，且各 ISA 方向一致。

```c
/* 对应 tmp/perf/xplat.c::dw_wpre：只加这两行，内层改用 kk[] */
f32 kw[25];
for (int k = 0; k < 25; k++) kw[k] = wrow[k];
/* 内层： */
const f32* kk = kw + kh * 5;
acc += kk[0]*r[ow+0] + kk[1]*r[ow+1] + kk[2]*r[ow+2] + kk[3]*r[ow+3] + kk[4]*r[ow+4];
```

跨平台佐证：arm64 上 `mem 77→63`、`stack 21→8`（与 x64 同方向）。

### 2.4 pad5：只清边框（内部只拷）

**机制**：现实现 `cf_zero(整张 30×30)` 再 `cf_copy(内 26×26)` —— **先清一遍内部再立刻覆盖**。
每通道写 1576 float 而最小只需 900；8 个块合计 3.2MB/forward。收益 = features 的 ~0.5%（12µs），但**零风险**。

```c
/* 对应 tmp/perf/xplat.c::pad5_border（更激进的 border_once+copy_only 实测 −28%，两种都逐字节验证过） */
static void pad5(const f32* in, f32* p) {
  const int w = W5;                                  /* 30 */
  for (int c = 0; c < CF_H; c++) {
    const f32* src = in + c * CF_SP;
    f32* dst = p + c * w * w;
    cf_zero(dst, 2 * w);                             /* 上 2 行 */
    cf_zero(dst + (2 + CF_BOARD) * w, 2 * w);        /* 下 2 行 */
    for (int r = 0; r < CF_BOARD; r++) {
      f32* d = dst + (r + 2) * w;
      d[0] = 0.f; d[1] = 0.f; d[w - 2] = 0.f; d[w - 1] = 0.f;   /* 左右各 2 列 */
      cf_copy(d + 2, src + r * CF_BOARD, CF_BOARD);
    }
  }
}
```

`pad3` 同理（`tmp/perf/phases-rf.c::pad3_border`），但量级无意义（0.1%），可做可不做。

### 2.5 合并收益与等价性验证（本批已完成的四重验证）

| 验证 | 结果 |
|---|---|
| 候选 vs 生产的内核输出 `memcmp` | **BITEXACT**（全部变体） |
| 首用 attestation（native ↔ wasm 逐字节 ×3） | **3/3 通过** |
| 一局 rollout 落盘 npy 的 sha256 | **40/40 相同** |
| eval 报告逐字段对比 | 除 `feat` / `elapsedSec` 外**完全相同** |

**端到端**：`2.178 → 1.542 ms/forward`（**1.41×**）；50 局批 `11.26s → 7.96s`。

> ⚠ **这组端到端数字对应的是 8px 候选**（pw 4oc×8px + conv3 4oc 分组 + dw 权预取）。
> 落地形状改成 **16px** 后只是在单层/整管测过（再快 10–14%，§2.1）⇒ **端到端需实现方自己量**。

---

## 3. 已否决的路线（**勿重开**，逐条附实测证据）

### 3.1 conv5dw 结构（三种，全部逐字节等价，全部更慢）

| 变体 | 整核组合（基线 68.8 / 74.5%） |
|---|---|
| `dw_wpre`（25 权预取） | **66.1% / 72.3%** ✅ ← 唯一采纳的 |
| `dw_khout`（kh 外提，零栈溢出） | 74.7% / 78.0% ❌ |
| `dw_khout16`（再放宽到 16px/块） | 89.7% / 96.5% ❌❌ |
| `dw_rows2`（1 输入行喂 2 输出行 —— **用户提的行分块**） | 73.3% / 79.1% ❌ |
| `dw_rows4`（1 输入行喂 4 输出行） | ❌ |

**为什么省的载入没变成收益**（读汇编，不是猜）：
1. **生产 `dw_ref` 的病根**：clang 一次做全 25 个广播权重，AVX1 只有 16 个 YMM ⇒ **13 个溢写到栈并在每个像素块重载**
   ⇒ 每 8 像素是「25 载入 + 25 广播 + 13 栈重载 = 63 条 load-op」对「50 条 FP-op」⇒ **load 端口先满**（48% 的来源）。
2. 行分块把输入载入从 50 降到 30，但 2 行输出要两套累加器 + 残段分支 ⇒ 产物里 **32 处栈访问、16 次 Reload**，
   省下的载入被溢写吃回去。
3. `khout` 零溢出却更慢：原版 25 个乘法彼此独立、ILP 足够喂饱 FP 端口；kh 外提后变成围绕**单一累加器的串行链** ⇒ 延迟成新瓶颈。

**⇒ 「按输入行分块」是实测负结果，不要再试。** 唯一稳定收益是 `kw[25]`（§2.3）。

### 3.2 conv3 结构（四种家族，全部收敛到同一条线）

| 家族 | 要点 | iso GMAC/s | pipe（% of prod） |
|---|---|---|---|
| ① 纯 C 4oc 分组（§2.2 采纳） | 权标量预取 | 22.2–22.5 | **96–99%** ✅ |
| ② intrinsics 输入常驻 | 9 输入向量跨 64 oc 复用 | 22.0–22.9 | 96–98% |
| ③ intrinsics 权爆炸 | 权预广播成内存操作数（不占寄存器） | 22.3–23.1 | 97% |
| ④ intrinsics ic 最内 + 输出寄存器累加 | 输出只写一次（生产是 18 遍读改写） | 21.4 | 95–97% |

三条附带负结果：**取数:FP 从 1.01→0.59 换来 0**；**输出访存 18 遍→1 遍换来 0**；**权缓冲 64B 对齐换来 0**。

**机制（两条独立证据）**：
- **消融**（故意破语义，仅归因）：去掉输出 RMW 只值 +8%；**去掉 9 个权取数 → +63%（37 GMAC/s）**。
- **骨架**（`mix.c`：同 uop 配比、去掉卷积语义）：tap=9 封顶 **~30 GMAC/s（57% of 峰值）**，**且与累加链数无关**
  ⇒ 不是 ILP 问题；真实 kernel 已吃到骨架上限的 75%。
- **不变量**：**9 输入 + 9 权 = 18 > 16 个 YMM** ⇒ AVX1 装不下两个操作数 tile；把权放寄存器只是把取数从「权」换到「输入」
  ⇒ **每 tap 一次取数是结构不变量**。

**⇒ conv3 结案（只保留 4oc 分组）。**

### 3.3 dw 结构：已到自己配比的上限

深度卷积**没有任何通道复用**：每个输出元素 25 个 tap，每个 tap 要**自己的权 + 自己的输入** ⇒ 取数:向量-FP = **1.0**。

把骨架折出 `nf=2` 档（每 tap 两次取数 = dw 的配比）：

| 骨架（tap=25） | GMAC/s | % of 52.4 |
|---|---|---|
| `nf=1`（输入常驻，conv3/pw 的配比） | 32.7–36.6 | 62–70% |
| **`nf=2`（= dw 配比）** | **12.9–17.2** | **25–33%** |

生产 dw = 16.9，**正落在 nf=2 骨架的上沿**；消融「权取数换常量」反而 **106%（更慢）**⇒ 不是取数侧卡住。
**⇒ dw 结案：只要输出通道之间不能互相复用输入，深度卷积注定停在峰值 1/3。**

### 3.4 ISA 升级：FMA / AVX2 —— 只值 ~5%，且破坏契约

| 标的 | 收益 | 判定 |
|---|---|---|
| 结构修好后的 FMA/AVX2（1.8987 → 1.8557 ms） | **~5%** | ❌ 动 `-ffp-contract=off` = new era + 语料血缘，代价 >> 收益 |
| 8oc×8px（AVX1 更快） | AVX1 37.4 GMAC/s | ❌ **AVX2 下崩 9×（3.84）** —— clang 寄存器分配行为 |

⇒ **内核不是 FP 吞吐受限，别为 ISA 升级去动逐位一致契约。**

### 3.5 寄存器预算类（同一个教训的三个实例）

| 变体 | 结果 | 原因 |
|---|---|---|
| `4oc×32px` | 反向（58%） | 20 ymm ⇒ 溢写 |
| pw 2D intrinsics 分块（`4×4`） | 29.8–33.6 iso，整管更差 | 破坏单源，且不如纯 C |
| conv3 intrinsics 三族 | 见 §3.2 | 寄存器装不下两个 tile |

**⇒ 「看起来更宽就更快」在这里不成立；寄存器预算是唯一裁判。**

### 3.6 inline asm —— 实测比编译器**慢** 15–20%（用户提问）

实验：同一段 pw MAC 内层（不带 epilogue）四种写法，**全部 `=A bit-exact`**：

| 变体 | GMAC/s（4 次重复） | vs A |
|---|---|---|
| **A** intrinsics 4oc×8px（生产候选配比） | **34.93 – 35.57** | 基准 |
| **B** inline asm 4oc×8px（手写 VEX） | 28.36 – 30.06 | **80.5 – 85.4%** ❌ |
| **D** asm + 软件流水（提前载下一 ic）+ 临时寄存器交编译器 | **27.94 – 29.28** | **79.3 – 83.8%** ❌❌ |
| **C** intrinsics 4oc×16px（加宽平铺，纯 C） | **38.89 – 40.95** | **110.4 – 115.3%** ✅ |

**为什么输**：① `"memory"` clobber 每 ic 一次 ⇒ 禁止跨迭代提升/重排；② 固定指令序剥夺全局调度
（4 条独立 MAC 链需交错喂两条 mul + 两条 add 通道）；③ 编译器会自己软件流水、并把权广播**融进内存操作数**；
④ D 比 B 更慢 ⇒ 「更认真的 asm」反而更差。

**更根本**：§1.5 的端口算术说明**那里已经没有空间**，asm 加不出 load 端口。

**跨平台（实测）**：

| | 结论 |
|---|---|
| 6 个 native 目标 | **都能编**（含两个 MSVC 三元组 —— 因为编的是 clang 而非 `cl.exe`）⚠️ 若改用真 MSVC，x64 无 inline asm |
| **wasm32** | **也能编，且操作数绑定可用**（`%0/%1` → `local.get/set n`，实测替换成功）；但 clang **不校验 asm 文本**（`nop`、甚至非 wasm 指令都原样透传）⇒ 错字只能到汇编期暴露，而本机无 wasm 汇编器 |
| intrinsics 对照 | `immintrin.h`/`arm_neon.h`/`wasm_simd128.h` **7 目标全编过**（clang 自带 resource 头，不需要 sysroot） |
| 源码份数 | asm = 逐 ISA 源码（x86 VEX / AArch64 / wasm 文本）⇒ **3 份实现 + 各自逐位证明** |

> **更正过一次的结论**：调研中曾误判「wasm 没有 inline asm」（当时是我自己加的 `#error` 守卫挡住了探针）。
> 正确说法：**它编得过**，不成立的是"值得这么干"。

**⇒ 不做 asm。**

### 3.7 fp16 / bf16 —— 本机群收益 **0×**（用户提问）

**硬件事实**（Zen 3 5800H，集群同型）：`avx2 f16c fma`；
**缺失**：`avx512f` / `avx512_fp16` / `avx512_bf16` / `avx512_vnni` / `amx_bf16` / `avx_vnni`。
`f16c` **只提供 fp16↔fp32 转换**；fp16 算术（`vaddph`）要 AVX512-FP16，bf16 点积（`vdpbf16ps`）要 AVX512-BF16/VNNI。

**代码生成实测**（`fp16-ceiling.c`，7 目标）：

| 目标 | fp16 向量算术 | 结果 |
|---|---|---|
| x86_64 | **0** | 被提升回 fp32 ⇒ 白折腾 |
| aarch64 | **0** + 83 条转换 | 一半**标量化** ⇒ **更差** |
| **wasm32** | — | **硬报错**：`_Float16 is not supported on this target` |
| （假如）`-mavx512fp16` | 2 | 只有这种机器才有真指令 |

**上限**：车道翻倍 = ≤2×，但只有 **FP-bound** 的 pw 吃得到；conv3 的墙是**寄存器个数**
（9 输入 + 9 权 = 18 > 16，fp16 下**依然是 18**）；dw 同理 ⇒ 现实估 **kernel 1.2–1.8×**，且本机测不了。

**代价（三层硬约束同时爆）**：
1. **跨目标逐字节契约不可满足**（`native-prebuilt.ts:41` 有成表「同一份源码两次 `--cross` 是否逐字节相同」；
   `native-parity` 是逐字节门）⇒ attestation 必失败 ⇒ **静默回落** ⇒ **native 快路径自毁，净效果可能比现在更慢**。
2. **语料血缘**：精度口径要进 `corpus_identity_fp`（D14 双指纹）；`DECISIONS.md:1750/1761` 明写
   「无条件加字段 ⇒ **所有**既有课程指纹一起漂移 ⇒ **全库血缘断裂、云端 job 全拒**」⇒ 新语料 + 重训。
3. **基线作废**：eval-suite v7、God-AI c4/c5/c6（68/47/50%）、所有 NN 胜率都是对 **fp32 定义者**量的 ⇒ 不可比。

**封口**：**fp16 存储 + fp32 计算**是**逐位不变**（fp16 值在 fp32 中精确可表示）的变体，不动血缘；
但只省带宽，而内核是**端口/FP 受限**（工作集 ~300KB 落 L2）⇒ **收益 ~0**。连这条也不做。

### 3.8 减少 forward 次数 / 缓存 / 跳 head / 跳 tick —— 已吃干净（用户提问）

| 方向 | 结论 | 证据 |
|---|---|---|
| **跨头 trunk 复用** | 代码上确有 **3 条独立重算路径**（`infer.ts:452/483/518`），但**生产路径一条都没命中** | 全部调用点 grep |
| └ per-tick（x20 活动路径） | 每决策 tick **只 `forward` 一次**（`export-rl-rollout.ts:648`） | — |
| └ goal / intent 模式 | 每 replan 只 `goalForward` / `intentForward` 一次 | — |
| └ eval 的 `nn-goal` | 另有 `GoalSteering`，但它是 **God/启发式**选目标（`goal-mask.ts:176`，**不跑 NN**）⇒ 无双跑 | — |
| **跨 tick 特征缓存** | **≈ 0** —— obs 每个决策 tick 都在变（子弹在动），而 features **只在**决策 tick 算（`t % K === 0`）⇒ 相邻两次永不共享 obs | `export-rl-rollout.ts:646` |
| **跳 head** | **≈ 0** —— 头是 64 MAC 量级，trunk 是 37.8M MAC（10⁻⁵） | — |
| **决策节奏 K** | **已吃**（默认 K=10）。改 K = 改控制率 = 改行为，**不是优化** | `policy-input.ts` |
| **每进程固定成本** | **实测 89–107 ms/进程**；批化 4 局/进程**省 15–19%** | §4.6 |

---

## 4. 六个关注点的结论（用户提问 → 结论 → 可行动项）

### 4.1 这些优化跨平台生效吗？—— **是，但要用代码生成普查证明，不能靠推理**

**真实机器形态**（`prebuilt/manifest.json`）：**只有 x64 带 `-mavx -msse4.2`**；
**arm64 目标的 flags 里没有任何向量选项** ⇒ arm64 = 默认 aarch64 + NEON（128-bit、32 个 q 寄存器）；
x64 = 256-bit、16 个 ymm。**cc = clang 20.1.0**（六个目标：x86_64/aarch64 × linux/darwin/win）。

**六目标普查结果**（`tmp/perf/census.py`）：

| 事实 | 结果 |
|---|---|
| 标量退化（灾难模式） | arm64 上 9 个函数 `scalar_fp = 0`（x64 只有余数尾巴） |
| **FMA 出现**（逐位一致性杀手） | **全部目标、全部函数 = 0**（`-ffp-contract=off` 在 NEON 上也生效） |
| 向量宽度 | x64 走 `ps`（256-bit）；arm64 走 `.4s`（128-bit）✓ |
| x64 三兄弟 | **逐条相同**（仅 win 的寻址方式让 mem 计数不同）⇒ x64 内部零风险 |
| arm64 linux vs win | 几乎相同；mac-arm64 指令选择不同（配对 load/store、循环展开） |

**逐项判定**（关键是收益来自**算术**还是某个 ISA 特性）：

| 改动 | 机制 | arm64 普查 | 风险 |
|---|---|---|---|
| pw 加宽 | 一个权广播喂 16 像素：**纯算术** | 6 目标均向量化、0 标量、0 FMA；NEON 需 16 acc + 4 in = **20/32 寄存器**（比 AVX1 的 10/16 更宽裕） | **低** |
| conv3 4oc 分组 | 9 输入被 4 oc 共用，纯算术 | ⚠ arm64 **栈引用 18 → 102**、mem +14% | **中 ⇒ 唯一必须实机判的** |
| dw 权预取 | 少一次权广播重取（纯搬运） | arm64 mem 77→63、stack 21→8，**方向一致** | 低 |
| pad5 只清边框 | 每通道少写 (900−224) float = **字节 −42%** | arm64 **写指令数 28→51**（更碎），字节大降 | 低 |

**不受影响的项**：四项全是**纯 C、无 intrinsics** ⇒ arm64 编得出来（`__m256` 那批一开始就没进候选，因为它们 arm64 编不过）；
**wasm 路径一字未改**（wasm 是产品字节的定义者，四项都不重排累加 ⇒ `native-parity` 契约成立）。

**诚实的边界**：静态计数不能代替计时（如 mac-arm64 的 `pad5` 出现 219 次 store 计数，而字节数与其它目标一致）
⇒ 普查只能证「结构没坏」，**不能证更快**。落地方案自带这一步：`--cross` + `native-parity` + **arm64 实机计时**。

### 4.2 能不能一份源码同时编出 native 与 wasm？—— **能，分歧只有 1 个函数**（用户核心诉求）

**现状量化**：

| | `src/nn/wasm/conv_feats.c`（169 行） | `src/nn/native/conv_feats_native.c`（195 行） |
|---|---|---|
| **pw `conv1x1_res`** | **手写 `wasm_simd128` intrinsics，4oc×4px** | **纯 C，4oc×4px（自动向量化）** |
| conv3/dw/pad3/pad5/GAP | 纯 C | 纯 C（**累加序逐字对得上**） |
| 入口 ABI | `features(in16, stemW, …, bufA, bufB, bufC, pooled)` —— **11 参、6 个权重指针** | `cf_student_features(wblob, in16, pooled, bufA)` —— **4 参、单 blob** |
| libc | 无（内联零填/拷贝） | 无（`cf_zero`/`cf_copy`，`-nostdlib` 硬要求） |

**⇒ 算法分歧 = 1 个函数。** 且两个权重布局**已经同序**
（`native-conv.ts::ensureBlob` 拼的顺序 == `conv-wasm.ts` 的六区偏移 == header 的 `CF_BLOB_FLOATS`）
⇒ JS 侧统一只是「上传 1 个 blob + 4 参调用」，**不是重写**。

**解锁点（本机实测）**：同一份纯 C 在 **wasm32** 上的向量化：

| pw 形状（纯 C） | wasm32 `-msimd128` `vecFP` | x64 `-mavx` `vec_fp` |
|---|---|---|
| `4oc×4px`（生产） | **0**（156 条标量）—— **一条 SIMD 都不出** | 4 |
| `4oc×8px` | **56** | 28 |
| `4oc×16px` | **88** | 40 |

**这就是 §368 当初必须手写 intrinsics 的同一事实**（4 个 float 恰好一个 `v128`，clang 在 wasm 后端把它 SROA 成标量）
⇒ **「一份纯 C 两边都用」加宽像素块之后才成立**。

**硬约束：wasm 的寄存器文件只有 AVX1 的一半**

| | 向量寄存器 | float 容量 | 4oc×8px | 4oc×16px |
|---|---|---|---|---|
| x64 AVX1 | 16 ymm | 128 | 10/16 ✓ | 10/16 ✓（最优） |
| **wasm** | **16 v128** | **64** | 10/16 ✓ | **20/16 ✗ 溢出**（locals 373→455） |
| arm64 NEON | 32 q | 128 | ✓ 宽裕 | ✓ |

叠加已知反例「`8oc×8px` 在 AVX2 崩 9×」⇒ **交集唯一安全形状 = `4oc×8px`**。

### 已拍板（2026-09-22）：方案 **(b)** —— 单文件 + **一个目标条件常量** `CF_PW_PX`

两种解法都是一份算法源码：

| | 方案 | native | wasm | 代价 |
|---|---|---|---|---|
| (a) | **全局 8px**（单一常量） | 吃 −23~27% | −23% | native **白吐 ~5pp**（实测 §2.1） |
| **(b) ✅ 采纳** | **一个目标条件常量** `CF_PW_PX`（native 16 / wasm 8） | **−32%**（最优） | −23% | 无 —— 仍是单文件、单算法、单累加序 |

**为什么 (b) 不破坏单源的实质**：`CF_PW_PX` 只决定「哪些像素被放进同一条向量寄存器」，
**不改变任何元素的累加次序**（每元素仍是 bias → ic 升序 → relu → +residual）
⇒ native(16px) 与 wasm(8px) 的输出**逐位相同** ⇒ `native-parity` 契约成立（§6.4 第 2 项）。
这与「x64 才有 `-mavx -msse4.2`、arm64 没有任何向量 flag」是**同一性质的既有事实**，不是新引入的特例。

**落地写法（直接抄）**：

```c
/* 平铺宽度 —— **不要再"简化"成单一常量**：
 *   wasm 只有 16×v128 = 64 个 float 的寄存器容量 ⇒ 16px 需 20/16，**溢出**（实测 locals 373→455）；
 *   x64 AVX1（16 ymm / 128 float）与 arm64 NEON（32 q / 128 float）都装得下 16px。
 *   实测：pw 单层 8px = 31.8–33.0 GMAC/s、16px = 35.1–37.4（AVX1）；纯 C 在 wasm32 上
 *   8px 出 56 条向量 FP、16px 出 88 条，但 16px 在 wasm 上会溢写。
 *   改成单一常量会让 native 白吐 ~5pp；把 wasm 也设成 16px 会溢写。 */
#if defined(__wasm__) || defined(__wasm32__)
#define CF_PW_PX 8
#else
#define CF_PW_PX 16
#endif
_Static_assert(CF_PW_PX == 8 || CF_PW_PX == 16, "CF_PW_PX 只允许 8 / 16（见上方注释）");
```

⚠ **唯一的待实测项**：`CF_PW_PX` 的 `#else` 分支同时覆盖 **arm64**。本机测不了 arm64 计时
（普查只证「结构没坏」）。若 Stage 2 发现 arm64 上 16px 反而慢，**把常量拆成三档即可**
（仍是单文件，例如给 arm64 独立一个 `#elif defined(__aarch64__)`）——
**不要**为了"统一"去回退 native。先例：`8oc×8px` 在 AVX1 快 1.4×、在 AVX2 慢 2.9×。

**余数路径对两档都要保留**：`CF_SP = 676` 对 8 与 16 的余数**都是 4**（`676 = 84×8+4 = 42×16+4`）。

**落地时的记录义务**：Stage 3 合入时，把本决策（含被否决的方案 (a) 与实测 5pp）写进
`DECISIONS.md`（按 §6.3 的入口闸门：被否决的备选 ✓ + 未来重犯风险 ✓ ⇒ 值得一条）。
现在**不写** —— 尚未实施，写在 plan 里就够了（同 §6.3「就近可表达的不另立条目」）。

**统一设计草案**：

```
src/nn/kernel/conv_feats.c   ← 唯一内核源（两目标共用）
src/nn/kernel/conv_feats.h   ← CF_* 常量 + ABI（loader / native-build / CLI 共用）
   cf_zero / cf_copy           无 libc
   pad3 / pad5                 只清边框（§2.4）
   conv3                       4oc 分组（§2.2）
   conv5dw                     权预取 kw[25]（§2.3）
   conv1x1_res                 纯 C，4oc×CF_PW_PX（wasm 8 / 其他 16，见上方拍板块）
   cf_student_features(wblob, in16, pooled, bufA)   ← 唯一导出（采用 native 的 ABI）
   内部 static scratch / bufB_store（wasm 侧不再需要 JS 管 bufB/bufC）
   #if _WIN32 → _fltused（仅 native 需要）
```

- **wasm 编译**：`clang --target=wasm32 -msimd128 -O3 -ffp-contract=off -fno-fast-math -fno-builtin
  -fno-stack-protector -nostdlib -Wl,--no-entry -Wl,--export=cf_student_features -Wl,--export-memory`
  （**必须带 `-ffp-contract=off`**：wasm 本身无 FMA 指令，但这条让「同源 = 同字节」成为**结构性事实**而非巧合）。
- **JS 侧**：`conv-wasm.ts` 的六区上传 + 11 参调用 → 单 blob + 4 参；越界检查与 bufB/bufC 记账一并消失。
- **保留 attestation**：同源后它从「跨实现一致性」变成「**构建 flag 漂移的守卫**」（例如谁把 `-ffp-contract=off` 删了），仍值得留。

**两阶段落地（带便宜的 kill switch）**：

| 阶段 | 动作 | 判定 |
|---|---|---|
| **Stage 1（低风险、可杀）** | 只把 wasm 的 `conv1x1_res` 换成**纯 C 4oc×8px**，ABI/其余不动 → 训练机重编 `.wasm` → `bun tools/sim/perf-cmp-rollout.ts` A/B（逐字节 + 性能） | **若不如现有手写 intrinsics ⇒ 只让 pw 保留双实现**，统一范围缩到 conv3/dw/pad |
| **Stage 2** | ABI 统一 + 单文件（`native-build.ts` 指向同一份，删掉 native 实现） | — |

**闸门**（两阶段都要）：新旧 wasm **语料级逐字节** + 一局 40 个 npy sha256 + `native-parity` + `bun run check`。
**注意**：`src/nn/wasm/conv_feats.wasm` 在 `tools/agent/codehash-files.txt` 内 ⇒ 换它 = 触发一次**正常**的节点升级波
（`DECISIONS.md:5272` 同先例：受跟踪的构建产物）。**不是 new era**（不重排累加 ⇒ 输出字节不变）。

**本机无法验证的那一环**：wasm 计时（无 `wasm-ld`/`lld`/`emcc`，仓内无 wasm 构建脚本，
`tools/agent/agent-setup.md:306` 规定「从训练机复制覆盖，勿在节点本地重编」）⇒ 普查只能证「结构没坏」。

### 4.3 以后扩容 / 改网络定义，还能继续生效吗？—— **算法层有效（实测对层宽免疫），真正的成本在同步链**

**实测：内核结构对层宽免疫**。把 `CF_H` 64→128（最现实的扩容轴）后重跑普查，**每个计数位完全相同**：

| 函数 | H=64 wasm32 | H=128 |
|---|---|---|
| `pw_prod` | 467 / **vecFP=0** / 156 / 216 local | **逐项相同** |
| `pw_4x8` | 815 / 56 / 112 / 42 / 373 | **逐项相同** |
| x64 三档 | 4/32/47 ; 28/24/29 ; 40/24/38 | **逐项相同** |

`CF_H` **只出现在循环上界**，平铺形状与指令混合只由「tile 大小 + 寄存器文件」决定
⇒ 加宽不会把任一优化变成错的结构，**不需要为「大模型 / 小模型」各维护一份**。

**扩容反而把杠杆推向 pw**（MAC ∝ H²(pw) / H(dw) / H(conv3)）：

| | pw | dw | conv3 | pw 占比 |
|---|---|---|---|---|
| H=64（当前） | 22.15M | 8.65M | 7.01M | **58.6%** |
| H=128 | 88.60M | 17.31M | 14.02M | **73.9%** |

**负结果同样继承**：dw 的 1/3 上限**与通道数无关**（每元素 25 个 tap 的取数比是 1.0）；
conv3 的墙取决于**核大小**（9 = 3×3），与宽度无关。D 8→12 三类同比例缩放 ⇒ 份额不变。

**改网络定义时会真的坏掉的地方（硬编码清单）**：

| 依赖 | 位置 | 动作 |
|---|---|---|
| `pw` oc 分组 `oc0 += 4` **无余数路径** | `conv1x1_res` | **H 必须是 4 的倍数**（当前 64 ✓）；否则补余数循环 |
| `pw` 假定 **H×H 方阵** | 同上 | 非方阵（64→96）需泛化 kernel |
| `dw` 假定 **25 个 tap** | `wrow = w + oc*25`、`kh < 5` | 换核大小改常量（结构不变） |
| `conv3` 假定 **9 个 tap** + `inCh` | `wrow[0..8]`、`CF_IN_CH` | 先例：obs-schema v3 的 IN_CH 16→18 |
| `CF_ABI` | `conv_feats_native.h` | 布局变更**必须 +1**；不符即**静默回落** |
| prebuilt 源码 sha | `prebuilt/manifest.json` | 改内核 ⇒ `--cross` 重出 + `--check-prebuilt` |
| `codehash-files.txt` | 节点升级波 | 换 `.wasm`/内核 ⇒ 一次**正常**升级波 |
| 形状守卫 | `native-conv.ts::shapeOk`、`conv-wasm.ts` 契约 | 同步改 |
| 生产形状 golden | `tests/conv-wasm.test.ts` + `goal-infer` | **必须补新形状 golden** |

**最危险的失败模式（仓库已记录）**：改了 `conv_feats.c` 但**漏重编 wasm** ⇒ `run()` 静默回退 TS 路径
= 41ms/次 > 16.7ms 帧预算 ⇒ **60 FPS 门红**（`plan/obs-schema-v3.plan.md §3.5-6`）。
**先例**：`plan/obs-schema-v3.plan.md §3.5`「同步链（13+ 处，漏一处 = 事故）」，其第 6 项就是 `conv_feats.c` + 重编 wasm。

**唯一要主动维持的约定：op 词汇表要小且可融合**——batchnorm / scale / 逐通道仿射应在**导出期摺进 bias**（零内核成本）；
真加新算子才扩内核，但方法不变（逐阶段归因 → roofline → 消融 → 六目标普查，harness 已宏参数化）。

### 4.4 inline asm → 见 §3.6（**否决**：实测慢 15–20%）

### 4.5 fp16 / bf16 → 见 §3.7（**否决**：本机群 0×，且契约不可满足）

### 4.6 减少 forward 次数能省多少训练吞吐？—— **当前课程 0%（已经拿到）；goal/intent 模式还有 15–19%**

**逐条盘点**：见 §3.8（forward 恰 1 次/决策、非决策不算、缓存≈0、跳 head≈0、K 已吃）。

**每进程固定成本拆解**（`bun tmp/perf/measure-load.ts <weights>`，三轮）：

| 阶段 | 耗时 |
|---|---|
| `readFileSync`（379 KB JSON） | 0.3–0.6 ms |
| `JSON.parse` | 0.24–0.41 ms |
| `buildModelFromJson` | **6.2–9.1 ms** |
| **首次 features**（wasm 实例化 + native dlopen + **3 次 attestation**） | **43.7–57.1 ms** ← 主项 |
| 第二次 features（稳态） | 2.0–3.1 ms |
| 合计（+ bun 启动 ≈ 30–45 ms） | **89–107 ms** |

**批化实测**（同机、同权重、串行）：1 进程 4 局 **1342/1433/1426 ms** vs 4 个单局进程 **1664/1704/1694 ms**
⇒ **省 15–19%**（每多一个进程 ≈ 89–107 ms）。

**这笔收益已经拿到**：`tools/agent/sampler-agent.ts:94` `let persistEnabled = true`（**长驻池默认开**）；
`PERSIST_SERVE_ENTRIES` = **`export-rl-rollout.ts` + `export-eval-game.ts`**
（eval 是 2026-09-21 **真机归因**后入池的：a95/Termux 每局 `spawn` 一下 ~2.5s，连 `/v1/status` 都被拖到 2.59s）。

**两处真实缺口**：

| 缺口 | 影响 | 代价 |
|---|---|---|
| **`export-goal-rollout.ts` / `export-intent-rollout.ts` 不在池里** | 这两个 RL 模式每局仍付 90–107 ms ⇒ **还有 15–19%** | 实现 `--serve` 协议（stdin 一行一局 + `__SERVE_OK__`/`__SERVE_ERR__`），加进 `PERSIST_SERVE_ENTRIES` |
| **过时注释** | `sampler-agent.ts:758` 写「长驻 worker 池（--persist，**默认关**）」，与 `:94` 的 `persistEnabled = true` 矛盾 ⇒ 会让人误判 | 1 行注释修正 |

---

### 4.7 卷积之外还有优化点位吗？—— **eval 一处真 bug；训练侧已无杠杆**（profiler 实测）

#### 4.7.1 方法：用 `bun --cpu-prof` 直接归因，**不要用减法**

我第一版用「God 局墙钟 − 探针里的 `sim.tick`」做减法，得出"遥测 ~90 ms"——**错的**。profiler 显示遥测只占 **0.4%**；
差额其实是 **God AI 自身**（62.5%），因为探针里的 `sim.tick()` **包含 God 输入的计算**（God 是 `InputLike`，在 tick 内被调用）。
归因脚本：`python3 tmp/perf/prof-top.py <file.cpuprofile>`（self time 排名 + 分类汇总）。

#### 4.7.2 训练 rollout 归因（4 局、真权重、x20 课程路径：`export-rl-rollout.ts --stages 0 --seeds 0-3`）

| 项 | 占比（采样） | 说明 |
|---|---|---|
| **NN 内核**（`run @ native-conv.ts:205` 1052 ms + `cf_student_features` 234 ms） | **86.5%** | 1286 / 1488 ms |
| 敌 AI（`perception` / `TacticalIntelligence`） | 2.0% | 敌人战术层 |
| 仿真系统（Simulation*） | 2.0% | |
| **启动**（`i18n loadInitial` 18.6 ms + `config/theme` 模块 16 ms + attestation/wasm ~18 ms） | ~3% | **常驻池已摊掉**（§4.6） |
| npy / 采样写出（`writeFileSync` + `slice`/`concat`/`grow`） | 1.7% | 每局 ~6 ms |
| 其余零头（`computeMasks`、`attest`、`b64ToF32`…） | <2% | |

⇒ **训练侧没有卷积之外的杠杆**：与 §4.6 的固定成本结论一致，唯一还没拿的是 goal/intent 入池的 15–19%。

#### 4.7.3 eval 侧的真 bug：每 tick 编码（守卫位置不一致）

`export-eval-game.ts:482` 的 `encoder.encode(world)` 在 `t % K === 0` 守卫**外**（每 tick 都编码），
而 obs **只**被守卫内的 `model.forward` 消费 —— 全文件 grep：`encoder` 只出现在 **429 / 482 / 484** 三行，
且**没有**传给 `IntentExecutor`/`GoalExecutor`（那两个在别的 `policy` 分支里，走自己的 encoder）。
训练路径同一处早在 §368 提速③ 修好（`export-rl-rollout.ts:644-647`，带注释）⇒ **eval 漏了**。

**为什么不能当"编码很便宜"忽略**：编码器自身 **14–18 µs/次**，且大头是 **God-AI 的 `killAssessment`**
（调用链实测：`encodeScalars` → `killAssessmentSlack @ obs-encoder.ts:526` → `ThreatBudget.ts` 的
`killAssessment` + `enemyDeadline` + `firePower`）—— 即 obs 规格要求的一段重计算，不是平凡的数组填充。

#### 4.7.4 真 A/B（同脚本复制到 tmp、只移动这一行；输出逐字段相同 ⇒ 行为中性）

| | 单局墙钟（10 次中位） | 扣掉启动后（`--max-ticks 1` = 0.085 s） |
|---|---|---|
| 原（每 tick 编码） | 0.56 s | **0.475 s** |
| 修复（只在决策 tick） | 0.54 s | **0.455 s** |

⇒ **省 ~20 ms/局 = 单局游戏时间 4.2%**（profiler 归因 23–29 ms，同向）。
输出验证：两者同为 `outcome=gameover ticks=1772 win=false score=0.193 kills=6 hitRate=0.382 pickups=0`。

**这条修复会随内核优化变得更值钱**（浪费 ∝ 总 tick 数，有效工作 ∝ 决策数，K=10）：

```
浪费占单局比 ≈ 9 × 编码µs / featuresµs
现在：    9 × 14 / 2150 ≈ 5.9%
内核落地后： 9 × 14 / 1540 ≈ 8.2%      ← features 1.54 ms（§2.5）
```

#### 4.7.5 改动（1 行移动，无 API 变化）

```diff
     if (policy === 'nn' || policy === 'nn-goal') {
-      encoder.encode(world)
       if (t % K === 0) {
+        encoder.encode(world)
         model!.forward(encoder.obs, encoder.scalars)
```

#### 4.7.6 ops 层复核：`workers = os.cpus().length`（**别改**）

SMT 实测（2 进程 × pw `4oc×16px`，`taskset`）：

| 配置 | 每进程 iso | 相对 solo |
|---|---|---|
| solo（1 进程独占物理核） | 0.565 ms | 1.00 |
| **2 进程挤同一物理核的兄弟线程**（`-c 2,3`） | 0.607 ms | **1.07** |
| 2 进程分到两个物理核（`-c 2,4`） | 0.632 ms | 1.11 |

⇒ 兄弟线程只慢 7%，比跨核（慢 11%）更划算 ⇒ **16 worker 跑在 8 物理核上是对的选择**；
也说明"集群 CPU 已满"的性质是**包级功耗/内存**（增核不线性），不是单核端口不足。

#### 4.7.7 本轮清掉的两个错误口径（**本文其余数字不受影响，但方法要记住**）

1. **减法归因不可信** —— 见 §4.7.1；用 profiler。
2. **`taskset -c N` 会给出 3× 的假数字** —— 同一探针 pin 到单核时 encode 28–35 µs、`sim.tick` 45–60 µs
   （不 pin 为 10–13 / 22 µs），而**同一进程**的 `fill(0)` 基线不变；纯 JS 忙循环 pin vs 不 pin 只差 **12%**
   ⇒ 那是**环境噪声**（本机同时跑着 agent harness），不是 pin 的效应。
   **纪律：绝对时间用多轮中位 + profiler；只信同一次会话内的 A/B 比值。**

---

## 5. 实施路线（分阶段，含精确文件与验收）

### Stage 0 —— 前置检查（在任何改动之前）

```bash
# 1) 工具链：clang 20 + lld（--cross 依赖 -fuse-ld=lld）
clang --version && (wasm-ld --version || ld.lld --version)
# 2) 仓库就绪
bun run setup          # git config core.hooksPath tools/githook
git status --short     # 确认工作区干净
# 3) 基线（改前必须先跑一次，留档）
bun run check > tmp/check-before.log 2>&1 ; echo "EXIT=$?"
bun tools/agent/native-build.ts --check-prebuilt
```

**若 `lld` 缺失** ⇒ Stage 1/2 做不了（`-fuse-ld=lld` 是 `nativeLinkArgs` 的固定组成），必须先解决工具链。

### Stage 1 —— 内核四项（改 `src/nn/native/conv_feats_native.c`）

1. 按 §2.1 改 `conv1x1_res` —— **本阶段只改 native 文件，平铺宽度直接写 16**
   （`CF_PW_PX` 常量是 Stage 3 单源时才引入）。**记得保留余数循环**：`CF_SP=676` 不是 16 的倍数（余 4）。
2. 按 §2.2 改 `conv3`（若 arm64 实机显示更慢，**这一项单独回退**，其余三项保留）。
3. 按 §2.3 改 `conv5dw`。
4. 按 §2.4 改 `pad5`。
5. **不要**改 `CF_ABI`（签名与布局未变）。

**验证**：
```bash
bun tools/agent/native-build.ts            # 本机构建（开发用）
bun run check > tmp/check-after.log 2>&1 ; echo "EXIT=$?"
# 一局 rollout 的 npy sha256 与改前对比（本机已验 40/40 相同）
```

**DoD**：`bun run check` 绿；单局 npy sha256 与改前一致；`memcmp` 级等价（若自己加了 harness）。

### Stage 2 —— 重出 6 目标 prebuilt + 跨平台确认

```bash
bun tools/agent/native-build.ts --cross        # 6 目标（x86_64/aarch64 × linux/darwin/win）
bun tools/agent/native-build.ts --check-prebuilt
bun run check                                  # 含 tests/native-prebuilt.test.ts（源码 sha 不再过期）
bun run build                                  # 这是要发布的东西
```

**arm64 实测（不可跳过）**：在 a95/a96（Termux）与 mac 上各跑一次
`bun tools/agent/perf-cmp-rollout.ts` 或等价的稳态 features 计时，与现役库对比。
**重点关注 conv3 4oc 分组**（arm64 栈引用 18→102）；若变慢 ⇒ 只回退这一项。
**先例警告**：attestation 只保证**正确**、不保证**不变慢**（`8oc×8px` 在 AVX2 上慢 2.9×）。

**DoD**：`--check-prebuilt` 通过；`bun run check` + `bun run build` 绿；arm64 计时不慢。

### Stage 3（可选，优先级中）—— 单源双目标（方案已拍板；详见 §4.2）

**已定：单文件 + 一个目标条件常量 `CF_PW_PX`（native/arm64 = 16、wasm = 8）**，不进单一常量。

实施顺序：**先做只换 wasm 的 pw 为纯 C 4oc×8px**（ABI 不动）→ 训练机重编 `.wasm` →
`bun tools/sim/perf-cmp-rollout.ts` 出 A/B（逐字节 + 性能）→ 赢了再做 ABI 统一 + 单文件。
**输了就只让 pw 保留双实现**（其余三处本来就同源）。

验证时注意：Stage 3 完成后要在 **native(16px) 与 wasm(8px)** 之间重跑 §6.4 第 2 项。

### Stage 4（可选，独立于内核）—— goal/intent 导出器入池（详见 §4.6）

给 `export-goal-rollout.ts` / `export-intent-rollout.ts` 实现 `--serve` 协议，
加进 `sampler-agent.ts::PERSIST_SERVE_ENTRIES`。预期 **15–19%**（这两个模式）。

---

## 6. 验证与门禁（Windows 口径，可直接复制）

### 6.1 命令清单

```bash
bun run check        # 定义「绿」：tsc --noEmit --incremental && bun test --parallel --timeout=50000 ...
bun run build        # oxlint && tsc && vite build（这是要 ship 的）
bun run test         # 省 token 的跑法（按改动范围挑测试）
cd dashboard && bun run typecheck && bun run test    # 只有动 dashboard/** 才需要
```

**训练/pytest 相关**（若实现方要跑 python 侧）：
```bash
bash tools/githook/nn-py-safe.sh -m pytest ...      # 严禁裸 python -m pytest（沙箱会静默挂死）
```

### 6.2 日志与记录纪律

- 长任务输出**一律写文件**：`> run.log 2>&1`，**绝不** `| tail`（会缓冲到进程退出）。
- 诊断把日志**捕获一次**；绿了就在同一步删掉（除非日志本身就是证据）。
  现成工具：`tools/githook/run-logged.sh <log> -- <cmd>`（红则保留并回显尾部，绿则删除）。
- 改动记进 `.workbuddy/memory/YYYY-MM-DD.md`（append-only，永不删）；耐久约定进 `MEMORY.md`。

### 6.3 commit 纪律（**Windows 上尤其重要**）

```bash
# 用临时消息文件，不要用 -m（heredoc 与非 ASCII 的 -m 会静默失败）
git commit -F tmp/<ascii-name>
git log -1 --pretty=fuller          # 每次都验证提交真的落下了
```
- **绝不** `git stash`（任何子命令）、**绝不** `git push`（推送是人的事）。
- 重命名/删除用文件系统 `mv`/`rm`，**不要** `git mv`/`git rm`。
- **绝不** `git add` 未跟踪的 `*.md`（本文档已入库：用户明确要求 ⇒ §5.8 的例外；2026-09-23 起住 `plan/`）。

### 6.4 本项目特有的等价性验证（**必做**）

四项改动**全部要求逐位不变**。四重验证（本批已完成，实现方必须重跑）：

| # | 验证 | 方式 |
|---|---|---|
| 1 | 内核输出 `memcmp` | 候选 vs 生产，同输入 |
| 2 | `tests/native-parity.test.ts` | native ↔ wasm **逐字节**（8 次随机输入，pooled + bufA） |
| 3 | 一局 rollout 的 npy sha256 | 与改前逐文件对比（本机已验 40/40） |
| 4 | eval 报告字段 | 除 `feat` / `elapsedSec` 外逐字段相同 |

**任一不过 ⇒ 不进 Stage 2。**

> ⚠ **进入 Stage 3（单源）后，第 2 项多出一条硬断言**：native 走 `CF_PW_PX=16`、wasm 走 `8`
> —— **平铺宽度不同，输出必须逐位相同**。这是方案 (b) 成立的前提（因为宽度只改「哪些像素同处一条寄存器」，
> 不改每元素的累加序）。`native-parity` 就是它的守卫；**若这一项红了，说明有人动了累加顺序，而不是宽度选错**。

---

## 7. 附录 A：复跑手册（harness 清单）

> ⚠ **`tmp/perf/**` 全部在 `.gitignore` 内**，不会随仓库交给实现方。
> 若要实现方能复测，需先决定交付方式（例如搬进 `tools/perf/` 并提交 —— `.c/.py/.ts` 不受 §5.8 的 md 禁令约束）。
> 下表是清单与每个文件的作用，最坏情况下可按 §7.4 的口径重建。

### 7.1 文件清单

| 文件 | 作用 |
|---|---|
| `native-phases.c` | 内核逐阶段计时（pad3/conv3/pad5/dw/pw/GAP） |
| `kernel-ab.c` | 循环重排 A/B 主台架（函数指针隔离，避免内联/DCE 污染） |
| `c3-asm.c` / `c3-phases.c` / `c3-rf2.c` / `c3-roofline.c` | conv3：代码生成、阶段、`#include` 生产源的同源台架、真上限测量 |
| `mix.c` | uop 配比骨架（`chains/tap/nf` 参数化）—— 判「指令混合本身是不是墙」 |
| `pw-rf.c` / `pw-bench.c` / `pw-asm.c` | pw：roofline、单层、**C vs inline asm 对照** |
| `phases-rf.c` | dw + pad3/pad5/GAP 归因（`#include` 生产源） |
| `xplat.c` + `census.py` / `census-wasm.py` / `opmix-wasm.py` | 六目标 + wasm32 代码生成普查 |
| `asm-probe.c` / `asm-probe-wasm.c` / `asm-bind-wasm.c` | inline asm 可用性探针 |
| `fp16-ceiling.c` | fp16 代码生成普查 |
| `measure-load.ts` | 每进程固定成本拆解 |
| `measure-encode.ts` | **`ObsEncoder.encode` 每 tick 成本**（世界设置复刻 eval）× 白编码总量 |
| `prof-top.py` | **`bun --cpu-prof` profile 解析**（self time 排名 + 分类汇总）—— 归因用这个，别用减法 |
| `jsbusy.ts` | 纯 JS 忙循环（判定 pin / 机器的计时噪声） |
| `gen-stage.ts` → `stage2000.json` | 关卡 fixture（stage 2000 abcd） |
| `probe-split.ts` / `probe-features.ts` | 端到端拆分探针 |

### 7.2 编译与运行口径（与生产 flags 同规）

```bash
CC=/usr/lib/llvm-20/bin/clang
FLAGS="-O3 -ffp-contract=off -fno-fast-math -fno-stack-protector -ffreestanding -fno-builtin"
# 注意：-mavx -msse4.2 **只有 x64** 有；arm64 目标没有任何向量 flag
$CC --target=x86_64-unknown-linux-gnu -mavx -msse4.2 $FLAGS -S <file>.c -o out.s
$CC --target=aarch64-unknown-linux-gnu               $FLAGS -S <file>.c -o out.s
$CC --target=wasm32 -msimd128                        $FLAGS -S <file>.c -o out.s
python3 census.py out.s ; python3 census-wasm.py out.s
```

**计时纪律（踩过的坑）**：
1. 多变体**必须用位掩码分开跑** —— 同进程互污染缓存（实测同一变体在 1.63 ↔ 2.94 ms 摇摆）。
2. **pin 核 + 交错 A/B 多轮**（`taskset -c 3`，best-of-5 × 2–4 轮）。
3. 单层必须走**函数指针**（直接调用 + 字面常量会被内联 + 常量折叠，口径差 1.48×）。
4. 对拍参照**必须在同一次输入上现算**（拿陈旧 scratch 当参照会误判 MISMATCH）。
5. **G 必须整除通道数**（`G=12`/`G=6` 越界写会搞坏堆，并污染邻接缓冲区让**别的**变体假 MISMATCH）。
6. **JS 侧的计时别用 `process.hrtime.bigint()`** —— 它每次调用分配 BigInt（每 tick 2 次 ⇒ 整局 ~28k 次），
   单核受限时 bun 的 GC 辅助线程无法并行，实测把 encode 从 10 µs 虚增到 30 µs。用 `performance.now()`（零分配）。
7. **不要信单核 `taskset` 的绝对值**（§4.7.7）：用多轮中位；JS 与原生混测时尤其小心。

### 7.3 关键复跑命令

```bash
# 端到端拆分（真 FFI + 真权重）
SJ=$(cat tmp/perf/stage2000.json)
bun tmp/perf/probe-split.ts nn-training/weights/x20-noexplore.it165.20260922-080001.json
# 一局 4 局批 vs 4 个单局进程（每进程固定成本）
bun tools/sim/export-rl-rollout.ts --weights <w> --out tmp/ro-a --stages 0 --seeds 0-3 \
  --stage-json "$SJ" --difficulty hard --lives-override 1 --max-ticks 12900
# wasm 内核 A/B（逐字节一致 + 性能）—— Stage 3 用
bun tools/sim/perf-cmp-rollout.ts

# 归因（§4.7 的权威口径）——训练 rollout
bun --cpu-prof --cpu-prof-dir tmp/perf/prof2 tools/sim/export-rl-rollout.ts --weights <w> \
  --out tmp/ro-prof --stages 0 --seeds 0-3 --stage-json "$SJ" --difficulty hard \
  --lives-override 1 --max-ticks 12900
python3 tmp/perf/prof-top.py tmp/perf/prof2/*.cpuprofile 26

# 编码器每 tick 成本（复刻 eval 世界设置）
bun tmp/perf/measure-encode.ts 7 860001 12000 10

# eval 每 tick 编码 bug 的真 A/B：把 export-eval-game.ts 复制到 tmp/perf/eval-encfix.ts，
# 改两处 import 路径（'../eval/godai-score' → '../../tools/eval/godai-score'；'./pack-container'
# → '../../tools/sim/pack-container'），再按 §4.7.5 移动那一行。两侧输出必须逐字段相同。
for i in 1 2 3; do
  /usr/bin/time -f "A wall=%e" bun tools/sim/export-eval-game.ts --stage 7 --seed 860001 \
    --difficulty hard --max-ticks 12000 --policy nn --weights <w> | tail -1
  /usr/bin/time -f "B wall=%e" bun tmp/perf/eval-encfix.ts --stage 7 --seed 860001 \
    --difficulty hard --max-ticks 12000 --policy nn --weights <w> | tail -1
done
# 启动基线（扣掉进程启动，§4.7.4）：同命令加 --max-ticks 1
```

### 7.4 交付 harness 时不要触发节点升级波

`tools/agent/codehash-files.txt` 是 codeHash 的单一事实来源（python 与 TS 两侧都读它；任一侧漏同步会把节点
**永久误判 stale、反复重启**）。它的纳入规则对本任务很关键：

| 路径 | 是否入 codeHash | 后果 |
|---|---|---|
| **`src/nn/`（目录级）** | **入** | 放任何文件（含 `*.c`）到这里 = 改 codeHash = **触发节点升级波** |
| `tools/sim/export-rl-rollout.ts` 等 4 个显式文件 | 入 | 同上 |
| **`tools/perf/`、`tools/probe/`（未列出）** | **不入** | ✅ 把 harness 放这里提交起来，**不会**触发重启波 |
| `tests/**` / `docs/**` / `plan/**` / `nn-training/**` | 显式排除 | 不必担心 |

⇒ 建议：**把 `tmp/perf/**` 搬到 `tools/perf/kernel/**` 并提交**（`.c/.py/.ts` 不受 §5.8 的 md 禁令约束），
这样实现方能复测，且不惊动节点。反过来，**永远不要**把 harness 或临时产物放进 `src/nn/`。

### 7.5 口径（写清楚，否则数字不可比）

- **「% of 峰值」的分母 = 实测 52.4 GMAC/s**（6 链、操作数常驻寄存器），**不是**理论 16 lane-MAC/cyc。
- **relu 在 pw/dw 尾部融合**（写回时做），GAP 是逐元素**串行求和链**（与 wasm 的逐位契约，**不能重排**）。
- `pad3`/`pad5` 的字节数：pad5 每通道最小 900 float，现实现写 1576。

---

## 8. 附录 B：原始数据速查

### 8.1 三种形状 × 两档 ISA（pw，单层 isolated）

| 变体 | AVX1 GMAC/s | %peak | pipe %prod | AVX2 |
|---|---|---|---|---|
| `prod 4oc×4px` | 17.5–19.2 | 33–37% | 100% | 18.9 |
| `4oc×8px`（落地） | 31.8–33.0 | 57–67% | 73–77% | 33.6 |
| **`4oc×16px`**（x64 最优） | **35.1–37.4** | **72–74%** | **68.3%** | **39.7** |
| `8oc×8px` | 37.4 | 71% | 70.4% | **3.8（崩 9×）** |

### 8.2 内核占比（生产，µs/次）

`pw ×8` 1313 · `conv3` 634 · `dw ×8` 511 · `pad5 ×8` 42 · `GAP` 30 · `pad3` 1.6

### 8.3 真上限（实测，6 条独立链最优）

| 链数 | 2 | 4 | 6 |
|---|---|---|---|
| GMAC/s | 22.6–23.4 | 42.7–43.1 | **51.0–54.4** |

### 8.4 每进程固定成本

首用 43.7–57.1 · buildModel 6.2–9.1 · JSON.parse 0.24–0.41 · readFile 0.3–0.6 · bun 启动 30–45（ms）

### 8.5 归因（`bun --cpu-prof` + `prof-top.py`，真权重）

| 场景 | 内核 | 非内核明细 |
|---|---|---|
| **训练 rollout**（4 局） | **86.5%** | 敌 AI 2.0 · 仿真 2.0 · 启动 ~3（池已摊） · npy/拷贝 1.7 · 其余 <2 |
| **eval nn 局**（stage7/seed860001，1772 tick） | **~77%** | **编码器 ~5%（其中 9/10 是浪费）** · sim ~7 · 设备/模块启动 ~6 · God 链（nn-goal 才跑） |
| eval god 局（对照，无 NN） | 0% | **God AI 62.5** · 仿真 16 · 遥测 0.4 |

单局时间账（eval nn，0.58 s 墙钟）：内核 0.38 · 启动 0.085 · 编码 0.026（浪费 0.023） · sim 0.04 · 其余 ~0.05

---

## 9. 附录 C：常量与位置速查

```
CF_BOARD 26 · CF_SP 676 · CF_H 64 · CF_D 8 · CF_IN_CH 18 · CF_ABI 1
W3 = 28 · W5 = 30 · STEM_W = 18×576 · DW_W = D×H×25 · PW_W = D×H×H
CF_SP = 676 = 84×8 + 4  ⇒ pw 的 8px 主块有 4 像素余数（**必须保留余数路径**）
```

> 下表已是**重组后的现址**（2026-09-23）；重组前的路径见顶部说明与 §5 各 Stage。

| 位置 | 说明 |
|---|---|
| `src/nn/conv/conv.c` | **唯一算法源**（native 与 wasm32 两目标共用；本次四项改动都在这里） |
| `src/nn/conv/conv_native.h` | native ABI + `CF_*` 常量 + 权重 blob 顺序 + `CF_PW_PX`（16） |
| `src/nn/conv/conv_wasm.h` | wasm 导出层（`CF_PW_PX` = 8 + `export_name`） |
| `src/nn/conv/conv_ts.ts` | **TS 孪生实现**（`runStudentConvTs`；原在 `infer.ts` 的私有方法里） |
| `src/nn/conv/prebuilt/wasm/conv.wasm` | wasm 产物（**产品字节的定义者**；进 manifest，漏编由 `--check-prebuilt` 抓） |
| `src/nn/conv/conv_native_adapter.ts` | native 加载 + attestation + `ensureBlob` |
| `src/nn/conv/conv_wasm_adapter.ts` | wasm 后端（单 blob + 4 参 ABI） |
| `src/nn/conv/conv.ts` | `runStudentFeatures` 生产咽喉（native → wasm → TS） |
| `src/nn/conv/native-prebuilt.ts` | 6+1 目标矩阵 / flags / ABI（**分发物的单一事实来源**） |
| `src/nn/infer.ts` | `StudentModel`：`features`（兜底调 `runStudentConvTs`）/ `forward` / `intentForward` / `goalForward` |
| `tools/agent/native-build.ts` | 6 目标构建 + `--cross` + `--wasm` + `--check-prebuilt` |
| `src/nn/conv/prebuilt/manifest.json` | 逐目标 flags + 源码 sha（改内核后必须重出） |
| `tests/native-parity.test.ts` | native ↔ wasm **逐字节**门 |
| `tools/agent/sampler-agent.ts` | 长驻 worker 池（`persistEnabled`、`PERSIST_SERVE_ENTRIES`） |
| `tools/sim/export-rl-rollout.ts` | per-tick rollout（x20 课程走这条） |
| `tools/sim/export-goal-rollout.ts` / `export-intent-rollout.ts` | goal / intent 模式（**已入池**，见 §4.6 / §11.1） |
| `tools/sim/serve-loop.ts` | `--serve` 长驻协议的**唯一实现**（四个导出器共用） |
| `tools/perf/conv-ab.ts` | 内核 A/B 探针（旧 vs 新四方 memcmp + **逐轮交错**稳态计时；`CONV_AB_OLD_LIB` 指定旧库） |
| `tools/perf/kernel-variants.ts` | **逐项归因**：从现役单源派生 `ctl`/`pw8`/`nog3` 变体库（配合上面的探针；见 §11.4） |
| `tools/perf/kernel-phases.ts` | **逐阶段归因**：插桩 `cntvct_el0`/`rdtsc` 量 pad3/conv3/pad5/dw/pw/GAP 的 µs 与 GMAC/s（见 §11.6） |
| `tools/sim/perf-cmp-rollout.ts` | wasm 内核 A/B（逐字节 + 性能） |
| `plan/obs-schema-v3.plan.md §3.5` | 「同步链（13+ 处）」先例 —— **改内核前必读** |

---

## 10. 一页结论

| 问题 | 答案 |
|---|---|
| 优化空间在哪 | 内核为主（features 占一步的 95.2%）；**卷积之外只剩 §0.2 第 5 项那一处真 bug**（profiler 归因见 §4.7） |
| 卷积之外还有吗 | **训练侧：无**（rollout 内核 86.5%，其余全是零头；只剩 goal/intent 入池 15–19%）。**eval：1 处真 bug** —— `export-eval-game.ts:482` 每 tick 白编码（≈ 单局 4.2%，真 A/B 已验证，输出逐字段相同） |
| 常见误区 | "编码很便宜"——不，14–18 µs/次，大头是 God-AI 的 `killAssessment`；也别用减法归因（会错 60 倍） |
| 已经拿到多少 | **1.41× 端到端实测**（pw **4oc×8px** + conv3 4oc 分组 + dw 权预取；pad5 边框是白捡的 0.5%）。落地形状用 **16px** ⇒ 预计 ~1.5×，**待实现方实量**（单层/整管已证再快 10–14%） |
| 还能拿多少 | pw 再 +5pp（16px，但这是单源的取舍）；goal/intent 入池 15–19%；eval 每 tick 编码 4.2%；**其余全部到顶** |
| 到顶的证据 | 端口算术（5 load uop / 32 MAC，2 个 load 端口）：8px 已到硬上限的 ~85%；16px 到峰值的 ~75% |
| 哪些路已否决 | conv5dw 行分块 · conv3 四种结构 · dw 结构 · FMA/AVX2（~5%） · 8oc×8px/4oc×32px · **inline asm（慢 15–20%）** · **fp16/bf16（本机群 0×）** · 减少 forward（已吃干净） · 跳 tick / 跨 tick 缓存（≈0） · **worker 数改物理核（SMT 实测更差）** |
| 跨平台 | 四项均为**纯 C、机制与 ISA 无关**；六目标普查全过；**只有 conv3 4oc 分组需 arm64 实机确认** |
| 单源双目标 | **可行**（分歧只有 pw 一个函数 + 调用签名）；交集安全形状 = `4oc×8px`；代价 = native 吐回 ~5pp |
| 扩容后还有效吗 | **算法层有效**（实测对层宽免疫）；常量层只在换寄存器文件/元素类型时重推；**真成本在同步链** |
| 第一步做什么 | Stage 0 前置检查（clang + **lld**）→ Stage 1 改四项 → Stage 2 `--cross` + arm64 实测 |

---

## 11. 落地记录与偏差（2026-09-23，实现方回报）

> 本节是**结果**；上面 §0–§10 是调研与方案（**勿据本文正文的旧路径找文件** —— 现址见 §9 表与 §11.5）。
> 同日流水账：`docs/nn/runtime-opt.md` §9（内核 + 重组）· §10（goal/intent 入池）· §11（TS 孪生/矩阵移入 + wasm 可重现 + WSL 实测）
> （= 拆分前的旧号 §140–§142）。
> 决策：`DECISIONS.md §2026-09-23-goalnn-conv-single-source`。提交：见 `git log --oneline -- src/nn/conv`
> （整批 51 files，+3544 / −955）。

### 11.1 四个 Stage 的落地情况

| Stage | 计划 | 实际 |
|---|---|---|
| 0 | 前置检查（clang 20 + lld） | ✅ clang 20.1.0 + lld，`--cross` 全程可用 |
| 1 | 四项循环重排（先只改 native） | ✅ 四项全做，但**与 Stage 3 同批**（见偏差 ①） |
| 2 | 重出 6 目标 prebuilt + 跨平台确认 | ✅ 6 native + **wasm32 一并进门禁**；**arm64 实机计时 + 逐项归因已做** = 整批 **≈+5%**、wasm **1.28–1.30×**、`pw8`/`nog3`≈1.00（§11.4） |
| 3 | 单源双目标（`CF_PW_PX` 两档） | ✅ 按拍板方案 (b)：`conv.c` 单源、`CF_PW_PX` = wasm 8 / 其余 16；wasm 侧**不再手写 intrinsics** |
| 4 | goal/intent 入池 | ✅ 两个导出器加 `--serve` 并入 `PERSIST_SERVE_ENTRIES`；协议收敛到 `tools/sim/serve-loop.ts` |
| 额外 | —— | ① `infer.ts` 的 TS 卷积实现抽成 `conv_ts.ts`；② `native-prebuilt.ts` 移入 `src/nn/conv/`；③ **wasm 产物可重现**（`-Wl,--strip-all`，见偏差 ④） |

### 11.2 实测（真 FFI + 真权重；best-of-N，同进程交替计时，`bun tools/perf/conv-ab.ts`）

| 平台 | 后端 | 旧 | 新 | 提速 |
|---|---|---|---|---|
| win32-x64（bun 1.4.2） | native（prebuilt AVX1） | 2.31–2.39 ms | **1.64–1.73 ms** | **1.38–1.46×** |
| win32-x64 | wasm（JSC） | 6.03–6.22 ms | **3.59–3.63 ms** | **1.67–1.71×** |
| linux-x64（WSL2，Ryzen 7 5800H） | native | 2.37–2.47 ms | **1.53–1.55 ms** | **1.55–1.59×** |
| **darwin-x64**（macOS） | native | ——（只记了倍率） | —— | **1.408×** |
| linux-x64（WSL2） | wasm（JSC） | 4.34–4.50 ms | **2.59–2.63 ms** | **1.67–1.71×** |
| **linux-arm64**（K30 Pro · proot/Ubuntu，bun 1.4.2） | native（prebuilt，NEON） | 6.70–6.87 ms | **6.38–6.59 ms** | **≈1.05×**（钉单核 best-of-N，n=4；不钉核时 0.99–1.10，见 §11.4） |
| linux-arm64（同上） | wasm（JSC on Android） | 9.44–9.53 ms | **7.29–7.42 ms** | **1.28–1.30×**（n=13，±0.5%） |

* 复跑：仓根 `bun tools/perf/conv-ab.ts 80 5`。**没有本地编译器**的机器（WSL 只有 gcc、arm64 节点）
  先取历史旧库再用 `CONV_AB_OLD_LIB` 指进去：
  ```bash
  git show 6ed7f11f:src/nn/native/prebuilt/linux-x64/conv_feats_native.so > /tmp/old.so
  CONV_AB_OLD_LIB=/tmp/old.so bun tools/perf/conv-ab.ts 100 4
  ```
* 口径提醒：基线取「历史 prebuilt 产物」还是「现编同一份源码」本身有 ~3% 差（win32：1.378× vs 1.40–1.46×）；
  另有一条独立通道（python3 ctypes，无 ffi 调用开销）给出 linux native **1.42–1.46×**。
  **结论取：native ≈1.4×、wasm ≈1.7×。**
* **darwin-x64 与 x64 同族同带**（实机 1.408×，补齐上表最后一格；流水账见 `docs/nn/runtime-opt.md` §16）
  ⇒ **决定倍率的是 ISA 家族，不是 OS/工具链** ⇒ 后续按 x64 / arm64 两类评估即可。
* **wasm 的 1.7× 是本批最大的意外收获**：`4oc×8px` 纯 C 在 wasm32 上**快过**原手写 `wasm_simd128`
  intrinsics ⇒ §4.2 担心的「单源可能要牺牲 wasm」不成立，**单源是纯赚**。
* **arm64 与 x64 差一个数量级**（≈1.05× vs 1.40–1.59×）——已逐项量完：**不是某一项拖后腿**（`pw8`≈1.00、
  `nog3`≈1.01，见 §11.4），而是这四项的机制（取数端口/寄存器平铺）在 NEON + 这台机器的**微架构上就不值钱**。
  绝对耗时 6.4–6.9 ms/forward（x64 1.53–1.64）。⇒ 有 arm64 节点的课程调优**不要把 x64 的提速当成可用产能**。
  （早先 1.078/1.106 那两轮是未钉核的噪声带，已归入 §11.4。）

**端到端（同日补测）—— 真导出器 `export-rl-rollout.ts`，唯一变量 = 内核二进制**

方法：同一份代码 + 同一权重（`nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json`）
+ 同 `--stages/--seeds/--difficulty`；**旧内核臂** = 把重组前的入库产物取到临时路径再指进去
（`git show <重组前提交>:src/nn/native/prebuilt/win32-x64/conv_feats_native.dll > tmp/old.dll`，
再 `NN_NATIVE_LIB=tmp/old.dll`；适配器按 env → prebuilt 顺序解析，日志里的 `lib=` 行可确认实际加载的是哪一个）。
交错 A/B/A/B 三轮取最优；启动基线 = 同命令 `--max-ticks 1`。

| 配置（stage/seed/权重同） | 决策 / tick | 旧内核 | 新内核 | 提速（raw 墙钟） | 扣启动 |
|---|---|---|---|---|---|
| `--max-ticks 12900 --seeds 0-3`（= §7.3 的生产形态命令） | 1433 / 14318 | 4275 ms | **2897 ms** | **1.48×** | 1.49–1.52× |
| `--max-ticks 2500 --seeds 0-3`（定长局） | 1000 / 10000 | 2914 ms | **2085 ms** | **1.40×** | 1.46× |
| `--max-ticks 12000 --seeds 0`（4940 tick 后 gameover） | 494 / 4940 | 1527 ms | **1178 ms** | **1.30×** | 1.34× |

* **每决策边际成本**（生产形态，扣启动）：旧 2.76–2.80 ms → 新 1.84–1.85 ms；4 局批 = **1069 → 724 ms/局**。
* 三档 raw 比不同的原因是**每进程固定成本**（§1.4 的 89–107 ms + bun 启动）摊到的比例不同 —— 局越短 / 进程数越多越低。
  引用时必须带配置：**生产形态（12900 × 4 局）取 1.48×**。
* **npy shard 逐字节相同**（三个配置、两臂、含 `--max-ticks 1`，sha256 全等）⇒ 提速是白拿的，语料血缘不变。
* 与预估的一处偏离（记为观察，未深究）：按内核占比 86.5–95.2% + 内核 1.40–1.46× 反推端到端应 ≈1.35–1.46×；
  实测每决策省 **0.92–0.96 ms**，**大于**内核单独口径的 0.67–0.75 ms ⇒ 真进程里内核的实际占比高于 profiler 抽样给的那一份。

### 11.3 与计划的偏差（含两条计划里没有的发现）

| # | 计划怎么说 | 实际 |
|---|---|---|
| ① | Stage 1 只改 native，Stage 3 再单源 | 用户拍板**同批做**（`CF_PW_PX` 一并引入）；等价性从「四重」变成「**四方 memcmp（native 旧/新 × wasm 旧/新）+ 四重**」，结论不变 |
| ② | Stage 3「先只换 wasm 的 pw，输了就保留双实现」 | 实测**赢了**（1.67–1.71×，且与 x64 比值一致）⇒ 直接完成 ABI 统一 + 单源，**没有保留双实现** |
| ③ | 端到端预计 **~1.5×**（16px 单层/整管再快 10–14%） | **已补测**（同日，真导出器，唯一变量 = 内核二进制）：生产形态 `12900×4 局` = `4275→2897 ms` = **1.48×**（扣启动 1.49–1.52×）· 定长 `2500×4 局` = **1.40×** · 单局 494 决策 = **1.30×**；每决策 2.76–2.80 → 1.84–1.85 ms。**npy shard 逐字节相同**。方法与三档差异的解释见 §11.2 的「端到端」块 |
| ④ | 计划没提 | **wasm 产物重建不可重现**（连编 3 次 3 个 sha）：wasm-ld 把 `name` 段的 module name 写成输出文件名，而构建为原子落盘用的是 `${out}.tmp-<pid>`。修法 = `wasmBuildFlags()` 加 **`-Wl,--strip-all`**（`-Wl,--name=` wasm-ld 不认）。实测：换输出名 → 字节相同、连编 3 次同 sha、产物 7620→7350B、导出表不变。**删掉这个 flag = 每次重建都产生二进制 diff** |
| ⑤ | 计划没提 | 顺带把「重建可重现」台账做实：只改注释后重建，**linux/darwin 四个目标 sha 逐字节不变**，只有 win32 两个变（lld-link `/Brepro` 哈希输入临时路径 —— 与 §0.4 的记录一致） |
| ⑥ | §4.6 预估 goal/intent 入池 15–19% | 每局固定开销实测 **spawn 176/172 ms → worker 22/19 ms**（8.15×/8.93×，120 tick 瘦身局）；生产局 ~1200 tick，占比落在计划的 15–19% 量级 |
| ⑦ | §7.4 建议把 harness 放进 `tools/perf/` 提交（避开节点升级波） | 已做：只提交 A/B 探针 `tools/perf/conv-ab.ts`（`tools/perf/` 不在 codehash 集内）；其余 `tmp/perf/**` 调研台架仍留本地，按 §7.1 清单可重建 |
| ⑧ | §4.6 的「过时注释」缺口（`sampler-agent.ts` 写 --persist 默认关） | 未单独改（不在本批范围）；`PERSIST_SERVE_ENTRIES` 现为 4 条（rl / eval / goal / intent），并有静态同规测试 `tests/export-goal-intent-serve.test.ts` 钉住「池里每个条目都真的走 `runServe`」 |
| ⑨ | §2.1 记 pw `8px→16px` 单层再快 10–14%（整管 68.3%）；§2.2 记 conv3 4oc 分组 −2.7~−4.4%（整管 96–99%） | **在真内核里重测**（交错计时 + 变体库，x64，见 §11.4）：`pw8` = **1.07×**（16px 值 ~6%）、`nog3` = **1.14×** —— **conv3 分组的实际价值远大于 §2.2 的台架口径**。两组均同向，但都在**内联进 `cf_student_features` 的真产物**里量的 ⇒ 以后引用**以 `tools/perf/kernel-variants.ts` 的读数为准**，§2.1/§2.2 的旧数字视为「tmp 台架口径」 |
| ⑩ | 计划没提 | **探针自身两处读数缺陷**（arm64 第一次真机运行暴露）：① 旧侧定位在重组提交进历史后命中**删除提交** ⇒ wasm 对比被静默跳过（`[wasm-old] … ⇒ 跳过`）；② 计时不分轮交错 ⇒ 同一二进制跨相位 ±4%。两者已修（`tools/perf/conv-ab.ts`） |

### 11.4 arm64 实机计时 + 逐项归因（**已做**，2026-09-23）：**没有负项，但 +44% 不迁移**

> 流水账（含测量纪律与探针两处缺陷）：`docs/nn/runtime-opt.md` **§14**。

**环境**：Redmi K30 Pro（M2002J9E · Snapdragon 865：4×1.8G 小核 + 大核 2.4G）· Android + **proot/Ubuntu** · bun 1.4.2。
对比的库：old = git 里那份历史 prebuilt（`/tmp/old.so`，sha256 `d3f18b2e…`，7664B，**已当场核对**）；
new = 仓库里的 `prebuilt/linux-arm64/conv_native.so`。

**结论（一句话）**：四项**没有任何一项在 arm64 上是负的** ⇒ **不回退、也不拆三档**；
但这台机器上整批只值 **≈+5%**，x64 的 **+44%**（wasm 1.7×）**不迁移**到 NEON（wasm 侧 1.28–1.30×）。

| 对比（钉单核 `taskset -c 7` + best-of-N，100×6 除注明外） | 旧 | 新 | 比值 | n |
|---|---|---|---|---|
| **整批**（历史 prebuilt vs 现役） | 6.70–6.87 ms | 6.38–6.59 ms | **1.017 / 1.054 / 1.057 / 1.061** ⇒ **≈1.05×** | 4 |
| `pw8` 变体（16px vs 8px） | 6.43–6.48 | 6.28–6.48 | **1.003 / 0.993 / 1.008** ⇒ **≈1.00** | 3 |
| `nog3` 变体（conv3 分组 vs 展开） | 6.48–6.53 | 6.39–6.64 | **1.014 / 0.983 / 1.045** ⇒ **≈1.01** | 3 |
| `ctl`（**与现役逐字节相同**的库） | 6.38 | 6.27 | **1.017** | 1 |
| **wasm**（同一份源码的 wasm32 目标） | 9.44–9.53 | **7.29–7.42** | **1.275–1.295**（±0.5%） | 13 |

* **判读**：`ctl`（**逐字节相同**的两份库）都能量出 1.017 ⇒ 这台机器 native 侧的**单次噪声 ≈ ±2%**；
  `pw8`/`nog3` 与 1.00 不可区分 ⇒ 16px 在 NEON 上既不亏也不赚（**不要拆三档**），conv3 分组在这台机上约中性
  （x64 是 1.14× ⇒ **保留**：arm64 不亏而 x64 大赚）。唯一稳定可见的是 wasm 的 **1.28–1.30×**。
* **方法教训（引用 arm64 数字必须带）**：同一台机器**不钉核**时逐次比值能从 **0.988 冲到 1.096**（±5%，
  与要读的效应同阶）；钉在**单个大核**（内核本来就单线程）+ best-of-N 后收敛到 ±2%。
* 早先那一轮（未钉核）报出的 1.078/1.106 落在这条噪声带内 —— 当时「只有 x64 的一半」**方向对、数值不可引用**。
* **「6.4 ms 到底花在哪」+「为什么这四项不迁移」已单独量完 ⇒ §11.6**（逐阶段归因 + FMA 上限实验）。

**顺带修掉探针的两个读数缺陷（都影响结论，2026-09-23）**

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| ① | 重组提交进历史后**静默跳过 wasm 对比** | 旧侧定位用 `git rev-list -1 HEAD -- <旧路径>`，而 **路径限制的历史把「删除该路径的提交」也算作一次改动** ⇒ 命中的正是删除提交（其上文件已不存在） | 换成 `git log --diff-filter=AM -1 -- <路径>`，且 native 源码与 wasm 产物**各自定位**（实测两者最后一次增改不同提交：`6ed7f11f` vs `7664673e`） |
| ② | 同一份二进制跨相位差 **±4%**（与要读的差异同阶 ⇒ 会把结论读反） | 原计时「先跑完 A 的所有轮次、再跑 B」—— 频率漂移/后台负载整段偏到一侧 | 改为**逐轮交错 A/B**（`benchPair`），每侧取各轮最优。修后 x64 复测重复性 **≤1.5%** |

**逐项归因的手段（已入库；下面是同一套手段在 x64 上的读数，作对照）**

`bun tools/perf/kernel-variants.ts` 从**现役单源**派生「单项关闭/单档」的变体库（断言过的文本替换；
conv3 的优化前实现从 git 取，不手抄）。变体**不需要探针加接口** —— old 侧本来就能指任意库：

```bash
bun tools/perf/kernel-variants.ts     # → tmp/kernel-variants/{ctl,pw8,nog3}/conv_native-<target>.*
# 把目标平台的库拷到机器上（arm64 取 -linux-arm64.so），每份跑一次探针：
CONV_AB_OLD_LIB=<变体库> bun tools/perf/conv-ab.ts 100 4
```

| 变体 | 改动 | x64 实测（交错 60×4 ×2 轮） | 读法 |
|---|---|---|---|
| `ctl` | **一字不改** | 1.016 / 0.992 ⇒ **≈1.00** | 偏置对照 = 零；arm64 上它与入库产物**逐字节相同**（`0909fd03…`，9288B）⇒ 变体只在想改的那一处不同 |
| `pw8` | `CF_PW_PX 16 → 8` | 1.084 / 1.059 ⇒ **≈1.07** | 16px 在 x64 上比 8px 真快 ~6%（与 §2.1 预估同向，见偏差 ⑨） |
| `nog3` | `conv3` 4oc 分组 → 展开的逐 oc | 1.148 / 1.142 ⇒ **≈1.14** | **去掉分组要慢 14%**（远大于 §2.2 记的 2.7–4.4%，见偏差 ⑨） |

**arm64 上的读法**（已按此跑完）：`ctl ≈ 1.00` ✓（1.017）· `pw8` = 1.00 ⇒ **16px 保留，不拆三档** ·
`nog3` = 1.01 ⇒ 4oc 分组**保留**（x64 上它是 1.14×）。⇒ **本项 DoD 结案：无回落、无分档**。
在 arm64 上复跑（库在 `tmp/kernel-variants/*/conv_native-linux-arm64.so`，**务必钉核**）：

```bash
taskset -c 7 bun tools/perf/conv-ab.ts 100 6        # 100 iters × 6 rounds（best-of-6）
```

### 11.5 最终文件地图（= §9 现址表的目录视图）

```
src/nn/conv/
  conv.c                 唯一算法源（native + wasm32 两目标；四项重排在此）
  conv_native.h          ABI / CF_* 常量 / blob 顺序 / CF_PW_PX=16
  conv_wasm.h            wasm 导出层 + CF_PW_PX=8
  conv_cli.c             参考 CLI（字节对拍 / 离线基准）
  conv.ts                生产咽喉 runStudentFeatures（native → wasm → TS）
  conv_native_adapter.ts bun:ffi + 首用 attestation
  conv_wasm_adapter.ts   wasm32 后端（单 blob + 4 参 ABI）
  conv_ts.ts             TS 孪生实现（runStudentConvTs）
  native-prebuilt.ts     6+1 目标矩阵 / flags / ABI（分发物的单一事实来源）
  prebuilt/{win32,linux,darwin}-{x64,arm64}/conv_native.*
  prebuilt/wasm/conv.wasm + prebuilt/manifest.json

本文档：plan/conv-optimize.plan.md —— **刻意不放 `src/nn/` 下**（避开 codehash / 节点升级波）

tools/agent/native-build.ts   --cross / --wasm / --check-prebuilt
tools/perf/conv-ab.ts         内核 A/B 探针（旧 vs 新，四方 memcmp + 计时）
tools/sim/serve-loop.ts       --serve 长驻协议唯一实现（rl / eval / goal / intent 共用）
```

---

*本节之后的任何内核改动都要重跑 §6.4 的等价性验证与 §11.2 的探针；**若动了累加次序，那是一次 new era**（新语料 + 重训），不在本文范围内。*

### 11.6 arm64 逐阶段归因：那 6.4 ms 花在哪，以及**为什么四项不迁移**（`tools/perf/kernel-phases.ts`）

> 流水账（相位表 + FMA 上限实验 + 未决事项）：`docs/nn/runtime-opt.md` **§15**。

**方法**：把计时插桩**注入 `conv.c` 的一份副本**（断言过的文本替换；生产源码一字未改），
各阶段用 aarch64 `cntvct_el0`（19.2 MHz）/ x86 `rdtsc` 取时；插桩库与生产库**逐字节同输出**（台架默认对拍，x64 上已验）。

| 阶段 | x64 µs | x64 占比 | x64 GMAC/s | arm64 µs | arm64 占比 | arm64 GMAC/s | 两边倍差 |
|---|---|---|---|---|---|---|---|
| total | 1605 | 100% | 23.6 | **6463** | 100% | 5.85 | 4.0× |
| pad3 | 2.1 | 0.1% | — | 6.1 | 0.1% | — | 2.9× |
| conv3 | 343 | 21.4% | 20.4 | 1378 | 21.3% | **5.1** | 4.0× |
| pad5×8 | 36 | 2.2% | — | 146 | 2.3% | — | 4.0× |
| dw×8 | 594 | 37.0% | 14.6 | 1516 | 23.5% | **5.7** | 2.6× |
| pw×8 | 599 | 37.3% | **37.0** | 3377 | **52.3%** | **6.6** | **5.6×** |
| GAP | 30 | 1.9% | — | 36 | 0.6% | — | 1.2× |

（两侧都是**优化后**的内核；x64 = win32-x64 AVX1 本机，arm64 = K30 Pro/proot `taskset -c 7`。
对照 §1.3 的**优化前** x64 口径：pw 54.2% (17.5–19.2 GMAC/s) · conv3 26.1% (18.3) · dw 21.1% (16.9) —— pw 的 GMAC/s 翻了一倍，这正是 x64 +44% 的来源。）

**① arm64 已经贴近自己的 FP 吞吐上限。** A77 只有 2 条 128-bit FP 管线（4 lane/条）且 mul 与 add **共用**它们；
`-ffp-contract=off` 下 1 个 MAC = 2 个 FP op ⇒ 非 FMA 上限 ≈ 4 MAC/cycle × 2.4 GHz ≈ **9.6 GMAC/s**。
实测整体 **5.85**、pw **6.6** GMAC/s ⇒ **61–69% 上限**。而 x64（Zen3）有 4 条 FP 管线且 mul/add 分开
（非 FMA 也能源源 16 MAC/cycle；实测上限 52.4 GMAC/s，§1.3），它的短板是**取数端口**（2 个，§1.5）——
所以「少取数」的四项在 x64 值 **+44%**、在 arm64 只剩 **+5%**。**不是某项拖后腿，是这台机器没有那个瓶颈可治。**

**② FMA 是 arm64 唯一的大杠杆，但被逐位契约挡着。** 同机量上限（`KP_EXTRA_FLAGS=-ffp-contract=fast`，
**故意破契约、只量上限、不是可用配置**）：

| | total | conv3 | dw | pw |
|---|---|---|---|---|
| arm64 无 FMA | 6463 µs | 5.1 | 5.7 | 6.6 |
| **arm64 +FMA** | **4893 µs（+32%）** | 6.8 | 7.0 | **9.1** |
| x64 无 FMA | 1605 µs | 20.4 | 14.6 | 37.0 |
| x64 +AVX2/FMA | **1414 µs（+14%）** | 20.5 | 17.7 | 43.0 |

⇒ **同一个杠杆在 arm64 上值 +32%、在 x64 上只值 +14%**（§3.4 当年记的 ~5% 是**优化前**代码的口径）。
但开 FMA = 改数值 = **new era**（权重/语料/基线全重做，§0.5 红线 ①）——**不是本批能动的**。
记在这里是给下一个 agent 的判据：**arm64 节点想再上一个台阶，只剩「减少 MAC 数」或「接受 FMA 新纪」两条路**；
继续抠循环重排没有空间（61–69% 已贴着上限）。相反对 x64，取数侧的重排仍是有效手段。

**复跑**：

```bash
bun tools/perf/kernel-phases.ts 300                       # 本机（x64）：应与上表一致
bun tools/perf/kernel-phases.ts --target linux-arm64      # 交叉构建 → 拷到 arm64 机器
KP_EXTRA_FLAGS=-ffp-contract=fast bun tools/perf/kernel-phases.ts --target linux-arm64   # FMA 上限实验
# 目标机器上（**必须钉核**）：taskset -c 7 bun tools/perf/kernel-phases.ts --lib /tmp/kernel-phases.so 300
```
