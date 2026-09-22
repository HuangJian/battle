# rollout / eval 运行时优化 — 技术档案

> native 内核 / 并发口径 / 派发 / 单局看门狗。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §12 端到端实测：内核优化在一局 rollout 里的确切倍率（`conv-optimize.plan.md` §11.2 补测，2026-09-23）

> 补 §9–§11 缺的那一格：之前只量了**内核**（win32 native 1.40–1.46× / linux-x64 1.55–1.59×、wasm 1.67–1.71×），
> plan §11.3 ③ 当时明写「端到端没有单独量，按占比估 1.3–1.45×，要引用确切数字请自己量一次」。本次用**真导出器**量了。

**方法（唯一变量 = 内核二进制，其余全部相同）**

* 同一份代码 + 同一权重（`nn-training/weights/x20-noexplore/x20-noexplore.it181.20260922-083734.json`）
  + 同 `--stages/--seeds/--difficulty/--lives-override`；走的正是训练路径 `export-rl-rollout.ts`。
* **旧内核臂** = 把重组前的入库产物取出来再用 env 指进去：
  `git show <重组前提交>:src/nn/native/prebuilt/win32-x64/conv_feats_native.dll > tmp/old.dll`，
  然后 `NN_NATIVE_LIB=tmp/old.dll`（适配器按 env → prebuilt 顺序解析；日志里 `lib=` 行可确认实际加载的是哪一个，
  两臂的 `attest=3/3 逐字节 vs wasm` 都通过）。⇒ 不需要旧工作区、不需要重编，同一进程结构对拍。
* 交错 **A/B/A/B**（各 3 轮取最优）；启动基线 = 同命令 `--max-ticks 1`（同一 env，两臂各测）。

**结果（win32-x64 · bun 1.4.2 · 真 FFI + 真权重）**

| 配置 | 决策 / tick | 旧内核 | 新内核 | 提速（raw 墙钟） | 扣启动 |
|---|---|---|---|---|---|
| `--max-ticks 12900 --seeds 0-3`（生产形态，= plan §7.3 的命令） | 1433 / 14318 | 4275 ms | **2897 ms** | **1.48×** | 1.49–1.52× |
| `--max-ticks 2500 --seeds 0-3`（定长局） | 1000 / 10000 | 2914 ms | **2085 ms** | **1.40×** | 1.46× |
| `--max-ticks 12000 --seeds 0`（4940 tick 后 gameover） | 494 / 4940 | 1527 ms | **1178 ms** | **1.30×** | 1.34× |

* **每决策边际成本**（生产形态，扣启动）：旧 2.76–2.80 ms → 新 **1.84–1.85 ms**（1.49–1.52×）；4 局批 = **1069 → 724 ms/局**。
* 三轮的离散度很小（旧 4275/4284/4340 · 新 2897/2898/2946）⇒ 比值稳在 **1.45–1.50×**。
* 三档 raw 比不同不是噪声，是**每进程固定成本**（plan §1.4：89–107 ms + bun 启动）摊薄比例不同：
  局越短 / 进程数越多，raw 越低。**引用时必须带配置**；对外一律用生产形态的 **1.48×**。
* **等价性**：三个配置、两臂的 npy shard sha256 **全等**（含 `--max-ticks 1` 的 `18201299dc637d68`）
  ⇒ 提速是白拿的，语料血缘不变（与 §9 的四重验证同结论，这次是**端到端**通道）。

**一处偏离预估（记为观察，未深究）**：按内核占比 86.5–95.2%（plan §1.2 / §4.7.2）+ 内核 1.40–1.46× 反推，
端到端应 ≈1.35–1.46×；实测生产形态 1.48× 略超上沿，且**每决策省 0.92–0.96 ms 大于内核单独口径的 0.67–0.75 ms**
⇒ 真训练进程里内核的实际时间占比高于 profiler 抽样给的那一份（采样器对 JS/native 混合栈的归属偏差所致，未进一步归因）。

**复跑**：见 `conv-optimize.plan.md` §11.2 的「端到端」块（Windows/WSL 同一套 `NN_NATIVE_LIB` 覆盖；
把 `<重组前提交>` 换成 `git log --format=%H -1 <重组前>`，即本批提交的父提交）。

---

## §11 卷积代码全部收进 `src/nn/conv/**` + wasm 产物可重现（2026-09-23）

> 接 §9/§10：用户要求「infer.ts 里的纯 TS 卷积实现也拆出来、native-prebuilt.ts 也移进去」。
> 于是**内核（C）/ 两个加速后端 / TS 孪生 / 分发矩阵**现在都在一个目录里。

### 做了什么

* **TS 孪生实现独立成模块**：`StudentModel` 的 `conv3x3` / `conv5x5dw` / `conv1x1` / `reluInPlace`
  与 features() 里的 GAP 循环 ⇒ **`src/nn/conv/conv_ts.ts::runStudentConvTs(view)`**（含 `ConvTsView`）。
  infer.ts 的兜底变成一行调用；视图在**构造期建一次**（`tsConvView`）⇒ 每 tick 零分配（AGENTS §14 口径不变）。
  为什么值得：三个后端并排可见（改 conv.c 时能一眼看到 TS 侧对应实现），且 conv_ts 不 import 任何模型
  类型（入参只是 buffer 视图）⇒ 可单独当作参考实现对拍。
* **`src/nn/native-prebuilt.ts` → `src/nn/conv/native-prebuilt.ts`**（分发矩阵 / 构建 flags / ABI 与内核同目录；
  `tools/agent/native-build.ts` 仍在 tools/ —— 它是构建器不是卷积代码）。引用同步：两个适配器 ·
  native-build · tests/native-prebuilt · tools/perf/conv-ab · `nn-training/tests/test_ts_code_pack.py` 的
  打包清单。`src/nn/` 本就在 codeHash 目录集内 ⇒ 无需改 `codehash-files.txt`。
* **wasm 产物可重现（实测发现并修复）**：`conv.wasm` **每次重建 sha 都不同**（连编 3 次 3 个 sha）。
  定位：wasm-ld 把 `name` 自定义段的 **module name 写成输出文件名**，而构建为原子落盘写的是
  `${out}.tmp-<pid>` ⇒ 每次不同（直接 clang 对比确认差异就在文件尾 `name` 段，内容就是 `w-a.wasm` /
  `w-b.wasm`）。`-Wl,--name=` 参数 wasm-ld 不认 ⇒ 用 **`-Wl,--strip-all`** 去掉 `name` 段：实测
  「换输出名 → 字节相同」，连编 3 次同 sha，产物小 270B（7620→7350B），导出表
  （`cf_abi` / `cf_student_features` / `memory`）不受影响。代价：wasm 栈追踪少函数名。

### WSL / linux-x64 实测（2026-09-23 追加，`opencode` 发行版 · Ryzen 7 5800H · WSL2 6.18.33.2 · bun 1.4.2）

同一个探针在 Linux 上跑。WSL 里只有 gcc 没有 clang（现编旧内核那一步做不了），所以给探针加了
**`CONV_AB_OLD_LIB`** 覆盖：直接指向 git 里的旧 prebuilt 产物 —— 顺带让「没本地工具链的机器（含
arm64 节点）」也能测。取旧库：`git show <ref>:src/nn/native/prebuilt/linux-x64/conv_feats_native.so > old.so`。

| 后端（linux-x64） | 旧 | 新 | 提速 |
|---|---|---|---|
| native（入库 prebuilt，AVX1） | 2.37–2.47 ms | **1.53–1.55 ms** | **1.55–1.59×** |
| wasm（bun/JSC on Linux） | 4.34–4.50 ms | **2.59–2.63 ms** | **1.67–1.71×** |

* 四方（native 旧/新 × wasm 旧/新）**memcmp 全等**，`[verdict] bitexact=OK`；被计时的 native 新库
  sha256 就是 manifest 里 linux-x64 那一条（`35f32107…`）。
* **独立第二通道**（纯 python3 ctypes，无 bun-ffi 调用开销）：native 旧 2.31–2.40 → 新 **1.63** ms =
  **1.42–1.46×** —— 比值更低是因为去掉 ffi 开销后「内核占比」变大；两条通道结论一致（native **≥1.4×**）。
* 跨平台对照：**wasm 的比值在 Windows 与 Linux 上相同（1.67–1.71×）**；native 比值 Linux 略高（1.55–1.59×
  vs win32 1.40–1.46×）。绝对耗时 Linux 两侧都更快（wasm 3.59→2.60、native 1.64→1.53）—— 纯执行侧差异，
  与包大小/磁盘无关（内核数据全在几百 KB，计时循环内无 I/O）。
* 旧库口径说明：win32 的探针在同样口径下用**旧 prebuilt dll** 作基线是 1.378×（vs 现编旧源码 1.40–1.46×），
  即基线取「历史产物」还是「现编同一份源码」本身就有 ~3% 差；两个数都应记为 native **≈1.4×**。

### 验证

* `bun run check` 绿（2126 pass / 0 fail）· `bun run build` 绿 · `--check-prebuilt` 通过（6 native + wasm32 同源）。
* 逐位：`tests/native-parity.test.ts` + `tests/native-prebuilt.test.ts` 29 例全绿（含 WSL 真 dlopen 新
  linux-x64 `.so` ↔ 新 wasm 逐字节）；`bun tools/perf/conv-ab.ts 30 3` 四方（native 旧/新 × wasm 旧/新）
  **memcmp 全等**（native 1.46× / wasm 1.77× vs 重组前内核）。
* TS 兜底路径由 `tests/nn/student-infer.test.ts` 等 golden 锚定（h16/d2 瘦身 fixture **必走 TS**）——
  4 个文件 25 例全绿 ⇒ 抽取没有动算术。
* 顺带把「重建可重现」台账做实：只改注释后重建，**linux/darwin 四个目标 sha 逐字节不变**，只有 win32
  两个变了（lld-link `/Brepro` 把输入临时路径哈希进 TimeDateStamp，头注已录）。
* **文档**：计划 `src/nn/conv/conv-optimize.plan.md` 随本批入库并更新到「已落地」状态（顶部状态块 +
  §0.3 DoD 勾选 + §9 现址表 + 新增 **§11 落地记录与偏差**：四个 Stage 的实际做法、两平台 × 两后端
  实测表、八条与计划的偏差、arm64 复跑命令、最终文件地图）。

---

## §10 goal/intent 导出器入池 + `--serve` 协议收敛（`tools/sim/serve-loop.ts`，conv-optimize.plan.md §4.6 Stage 4，2026-09-23）

### 做了什么

* **协议收敛为单一实现**：新增 `tools/sim/serve-loop.ts::runServe(main)` —— rl / eval / goal / intent
  四个导出器共用一份 `--serve` 长驻协议（`__SERVE_READY__` / `__SERVE_OK__` / `__SERVE_ERR__ <msg>`）。
  原先 rl 与 eval 各抄一份（两份逐行相同）—— 进池的导出器将来只会更多，协议留在四处就是下次漂移的温床。
  顺手硬化：**合法但非数组**的 JSON（`123` / `{}`）也判 `bad-json` —— 放进去 `main` 拿不到 argv，会静默
  按默认网格跑 16 局（报错比跑错便宜）。
* **goal/intent 入池**：`export-goal-rollout.ts` / `export-intent-rollout.ts` 加 `--serve`
  （`main(argv)` 可注入，`GOAL_SHARD_FILES` / `INTENT_SHARD_FILES` 导出供测试对拍），
  `sampler-agent.ts::PERSIST_SERVE_ENTRIES` 加这两条 —— 这两个模式**局数多、单局短**，是 spawn 成本
  占比最高的地方（计划预估该模式 +15–19%）。
* **静态同规门禁**：`tests/export-goal-intent-serve.test.ts` 读 `PERSIST_SERVE_ENTRIES` 字面量，断言每个
  条目文件都存在且都走 `runServe(main)` —— 堵「加了池条目但导出器没实现 `--serve`」（一进池就 spawn 一个
  跑默认网格的进程再退出，只在 agent 的熔断计数上留痕）。

### 实测（本机 x64，120 tick 瘦身局，真子进程）

| 导出器 | 一次性 spawn | persist worker | 每局固定开销差 |
|---|---|---|---|
| goal | 176 ms/局 | 22 ms/局 | **8.15×**（省 154 ms） |
| intent | 172 ms/局 | 19 ms/局 | **8.93×**（省 152 ms） |

口径提醒（AGENTS §16.1）：这张表量的是**每局固定开销**（瘦身局 120 tick，游戏本体只占很小一块）；生产局
~1200 tick 时长得多，收益占比落在计划预估的 **15–19%** 量级 —— 而 a95/Termux 上 spawn 那一跳 ~2.5s
（本文件 §5 实测），占比比本机更高。

### 等价性（`tests/export-goal-intent-serve.test.ts`）

每个导出器只起**一个**子进程，基线在进程内跑：① READY/OK 握手；② serve 与一次性调用的 **shard 树逐字节
相同** + 容器内 shard 条目逐字节相同（manifest 只除墙钟 `elapsedSec`）；③ 坏行（非法 JSON / 非数组 JSON）
响亮报错、worker 不倒；④ 同 worker 换 seed 的第二局按本局成包（不串局、worker 仍挺）。
rl/eval 改用共享实现后 `tests/export-eval-game-serve.test.ts` 仍绿（协议未变）。

### 门禁

`bun run check` 绿（2126 pass / 0 fail，+3 用例）· `bun run build` 绿。

---

## §9 卷积内核单源化 + 四项循环重排（`src/nn/conv/**`，conv-optimize.plan.md 落地，2026-09-23）

> 编号来历：本批四条在原 `docs/nn.progress.md` 里编 §140–§143（刻意从 140 起：2026-09-22 的 memory
> 把「TPU ragged tail 收尾」记作 §139，而那一条在文件里编 §131，当日的编号出现过漂移）；
> 2026-09-23 拆分后按本文件局部编号排为 §9–§12。

### 做了什么

* **目录重组**：`src/nn/native/**` + `src/nn/wasm/**` ⇒ **`src/nn/conv/**`** ——
  `conv.c`（唯一算法源）· `conv_native.h`（共享常量 / blob 顺序 / `CF_ABI`）· `conv_wasm.h`（wasm 导出层）·
  `conv_cli.c`（对拍 CLI）· `conv.ts`（生产咽喉 native→wasm→TS）· `conv_native_adapter.ts`（bun:ffi + 首用 attestation）·
  `conv_wasm_adapter.ts`（wasm 后端）· `prebuilt/{win32,linux,darwin}-{x64,arm64}/conv_native.{dll,so,dylib}` +
  `prebuilt/wasm/conv.wasm` + `manifest.json`。**`tools/agent/native-build.ts` 留在原址**
  （它是构建器，不是卷积计算；`native-prebuilt.ts` 于 §11 一并移入本目录）。
* **四项循环重排**（conv-optimize.plan.md §2，全部**不动每元素累加次序** ⇒ 逐位不变）：
  ① `pad5` 只清边框（原实现先清整张 30×30 再覆盖内部）② `conv3` 4oc 分组 + 权标量预取
  ③ `conv5dw` 权预取 `kw[25]` ④ `conv1x1_res` 像素块 4 → `CF_PW_PX`（**native 16 / wasm 8**）。
* **单源双目标**：wasm 侧不再手写 `wasm_simd128` intrinsics（DECISIONS §368 那一版 4oc×4px）——
  **同一份纯 C** 编两个目标，唯一差异是目标条件常量 `CF_PW_PX`（它只决定「哪些像素进同一条向量寄存器」，
  不改累加序 ⇒ 两侧逐位相同）；ABI 统一为 native 的 **单 blob + 4 参**
  `cf_student_features(wblob,in16,pooled,bufA)`（wasm 侧原来的 bufB/bufC 由内核静态区承担）。
* **wasm 进门禁**：`conv.wasm` 进 `prebuilt/manifest.json`（`--wasm` 重建 + `--check-prebuilt` 核对），
  连同 6 个 native 目标**一次抓全**「改了 conv.c 却漏重编某个目标」——此前 wasm 漏编只能靠人记得，
  是仓库记录过的最危险失败模式（静默回落 TS = 41ms/forward > 帧预算）。
* **eval 每 tick 白编码**（conv-optimize.plan.md §4.7）：`export-eval-game.ts` 的 `encoder.encode(world)`
  移进 `t % K === 0` 守卫（与 `export-rl-rollout.ts` 里那条 DECISIONS §368 同式）。

### 实测（本机 x64 / bun，真 FFI + 真权重；探针已入库 = `bun tools/perf/conv-ab.ts 30 3`）

| 口径 | 旧 | 新 | 比 |
|---|---|---|---|
| native ms/forward（x64 AVX1，16px） | 2.38 | **1.63** | **1.47×** |
| wasm ms/forward（bun/JSC，8px 纯 C vs 手写 intrinsics 4px） | 6.13 | **3.64** | **1.69×** |
| eval 单局墙钟（stage7/seed860001，1294 tick，8 轮交错 min/median） | unfixed 494/504 | fixed 470/485 | **−19 ms/局** |

**逐位等价（四重）**：① 四方等价矩阵（native 旧/新 × wasm 旧/新）同一权重+输入下 pooled+bufA **memcmp 全等**；
② `tests/native-parity.test.ts` native(16px) ↔ wasm(8px) 逐字节（8 次随机输入 + 参考 CLI）；
③ 一局 rollout（`export-rl-rollout --stages 0 --seeds 0`）native 路径 vs `NN_NATIVE=0`（wasm 路径）
`obs/scalars/a_move/a_fire/lp_*/mask/done/metrics/value.npy` **逐字节相同**，仅 manifest 的 `feat` 字段不同；
④ eval 报告逐字段相同（outcome/ticks/win/score/kills/hitRate/pickups）。
回滚粒度：`NN_NATIVE=0`（真 wasm）或按 commit revert；内核无运行期开关。

### 栅栏（跨平台）

* 6 目标 prebuilt 全量重出（`--cross`，clang 20.1.0 + lld）；`prebuilt/linux-x64` 在 WSL+ctypes 真加载，
  与 wasm 逐字节一致（`tests/native-prebuilt.test.ts` ④）。
* **⚠ 待办：arm64 实机计时**（a95/a96 Termux + mac）——本机只有 x64，静态普查只能证「结构没坏」。
  复测命令：`bun tools/perf/conv-ab.ts 30 3`（**在 arm64 机器上跑**；探针自带旧内核定位
  `git rev-list -1 HEAD -- src/nn/native/conv_feats_native.c`，故重组后仍能取到旧源码现编对拍，
  最后一行 `[verdict] bitexact=OK` 是判据）。
  风险集中在 **conv3 4oc 分组**（arm64 栈引用 18→102、mem +14%）：若 arm64 变慢，**只回退这一项**，
  其余三项保留（plan §2.2 / §5 Stage 2）。
* `--check-prebuilt` 同时守 6 native + wasm32；`bun run check` / `bun run build` 绿；
  nn-training `test_ts_code_pack.py` 绿（白名单已跟着换路径）。

### 决策

`DECISIONS.md §2026-09-23-goalnn-conv-single-source`（单源 + `CF_PW_PX`；被否决的「全局 8px」实测白吐 ~5pp）。

---
## §8 云机 rollout/eval 的单局看门狗：**>5s 即杀、原地重跑同一 argv**（2026-09-22）

用户口径（原话）：「或者超时重试！单局 >5s 肯定不正常。」起因是 it34 的一次现场：

```
[11:03:39] kind=iter rollout: 310/328 games settled (4s)
[11:14:25] kind=iter rollout: 320/328 games settled (651s)   ← 剩下几局卡住，日志只有计数
```

10 局卡死、日志里既没有「哪一局」也没有「卡多久」——旧口径 `timeout=off`（0 = 不限）是
**本机历史行为**，搬到云机上等于「一个卡住的 bun 子进程永远等下去」。

### 口径（唯一定义点 `remote/game_watch.py`，rollout 与 eval 共用一份）

| 常量 | 值 | 含义 |
|---|---|---|
| `SLOW_GAME_WARN_SEC` | 5.0 | 点名线：超过它打一行带局身份（`s3/d7`）的 WARN |
| `DEFAULT_GAME_TIMEOUT_SEC` | 5.0 | **首次尝试**硬顶（= 点名线）：超时 ⇒ kill + 原地重跑 |
| `RETRY_TIMEOUT_FACTOR` | 4.0 | **重试**尝试上限倍数（5s → 20s）；调用方显式配了就一字不改 |
| `GAME_MAX_ATTEMPTS` | 3 | 一局最多跑几次（种子在 argv 里 ⇒ 重跑是确定性的） |
| `GAME_POLL_SEC` | 0.5 | 轮询粒度（软告警与硬顶都靠它发现） |

为什么重试反而**放宽**上限：单局墙钟是重尾的（本机强并发实测 rollout `perGameSecs`
p50 1.8s / p90 3.4s / p99 16.8s；eval `wallSec` p50 1.2~1.6s / p90 3.2~4.2s / p99 7~8s，
`tmp/x20-*/eval_log.jsonl`）。首次 >5s 已属异常（用户口径）值得杀；但重试是**兜底**——
一次主机抖动把同一局连杀三次，代价是「整轮作废重发」或「评估少一局」（读数有偏），
比多等十几秒贵得多。显式配置（`--remote-iter-game-timeout` / `--eval-game-timeout-sec`）
对每次尝试一视同仁：配置说了算。

### 为什么重试而不是「竞速副本」（用户提的另一条路）

argv 不变 ⇒ out 目录不变 ⇒ 声明的 shard 集（`data_fp`）逐字节不变；副本会多产一个同
(stage,seed) 的 shard 目录，直接撞上「实产集 == 声明集」那道门。竞速在**多节点**在线路径上
成立是因为那里有 hub 侧候选表（`rl/queue_local.py::pick_race_target`）；云机离线只有一个节点，
重试是同一效果的最小实现。

### 落地

* `remote/game_watch.py`（新）：五个常量 + `attempt_timeout_sec` / `warn_is_redundant` /
  四行日志（`slow_warn_line` / `hard_cap_line` / `retry_line` / `game_time_summary`）。
  **调用点一律模块属性读**（`game_watch.X`）——`from ... import X` 会抄出第二份绑定，
  patch 了 game_watch 那份而调用点还在读旧绑定就是静默的错口径。
* `remote/iter_rollout.py`：`Popen` + 轮询代替一次 `wait(timeout)`（卡住期间就有告警）；
  `_run_one_game_with_retries` 逐尝试算上限、清理上一次的半截 shard 后重跑；整轮收尾打
  **单局耗时分布**（p50/p90/p99/max + ≥5s 计数 + 重试次数 + 最慢 3 局点名）。
* `remote/offline_eval.py` + `rl/eval_local.py`：同款看门狗（`run_eval_runner_capture` 的
  Popen 轮询版保留 `TimeoutExpired` 的 captured output —— 诊断不被超时吃掉）；单局失败
  原地重跑，且 `wall_sec` 与 in-loop 腿同字段（两腿逐条可比）。
* `remote/protocol.py`：`game_timeout_sec` 的注释口径改写（0 = 节点兜底，不是「不限」）；
  `remote/run_loop.py` 的 eval 侧**原样传 0**（不提前解析——「显式 vs 兜底」决定重试要不要
  放宽，解析一次就丢了这个信息）。
* 测试：`tests/test_game_watch.py`（7 条纯函数：默认值即用户口径 / 首试 vs 重试 / 显式优先 /
  告警冗余判定 / 四行日志带局身份 / 分布行 / 零局不崩）；`tests/test_remote_iter.py` 加
  「重试上限只在 plan 没给时放宽」与「轮末分布行」；`tests/test_offline_eval_cloud.py` 加
  「首次 5s、重试 20s；显式 120 ⇒ 两次都 120」。

### 未做（明确留白）

* **在线多节点腿**（`rl/dispatch.py`）不变：它有 `taskTimeoutSec`（缺省 900s）+ 竞速候选表 +
  超时冷却黑名单，是另一套成熟机制；本节只治云机离线这条腿。
* **本机 in-loop eval**（`rl/eval_dispatch` / `rl/batch_eval`）的上限仍由调用方显式给：本机实测
  eval p90 已 3~8s（机器慢、并发高），拿 5s 当硬顶会频繁误杀；云机 96 核上的 p50 亚秒，
  两者不是同一档。

---

---

## §7 并发口径统一：rollout 与 eval 共用 `max(cores−4, cores×0.8)`（2026-09-22）

用户口径：**「rollout 和 eval 是交替进行的，所以不应该为 eval 保留 CPU 核数，两者都使用
`max(cores − 4, cores × 0.8)`；只要留两三个核给数据回传任务就够了。」**

* **唯一口径落地**：`platform_utils.cpu_worker_slots(cores=None)`（新）= `max(1, min(n, max(n−4,
  floor(n×0.8))))`。96 核 → 92；40 → 36；16 → 12；8 → 6（留 2）；1 → 1。
* **eval 侧**：`remote/offline_eval.default_slots()` 直接跟着它（**删掉**「扣 `plan.workers` 再卡
  64」的老口径——96 核上那是白掉整三成；也删掉 `DEFAULT_SLOTS_CAP/RESERVE` 两个常量）。
* **rollout 侧（云机）**：`remote/run_loop` 新增 `--rollout-workers`（0 = 同口径自动），装配时解析一次
  写进 `RunContext.rollout_workers`，每轮用 `with_rollout_workers(spec, n)` 覆盖计划里钉着的
  `plan.workers`——**那是导出机的规模**（常在 8~16 核的本机导出，却要在 96 vCPU 云机整段跑）。
  `workers` 不进 `data_fp`（`iter_declared_entries` 只取 argv 的 stage/seed）⇒ 换并行度不动摇
  任何指纹；每段开跑时把「本机值 / 计划值」一并日志报出（两者不同时能当场归因「为什么不见快」）。
* **notebook**：`CFG.rollout_workers`（0 = 自动）+ 启动回显；配置表两行的口径文案同步（说明与代码
  说的必须是同一个公式）。

为什么不再互相预留：云机离线段是 rollout → PPO → eval **交替**推进的，给 eval 扣掉 rollout 的
并行度等于两笔账扣同一份钱；真正需要一直活着的只有补传/日志/守护线程，几个核足够。

测试：`test_offline_eval_cloud`（口径=同一公式，逐核数对账）、`test_offline_eval_wiring`
（缺省不吃 `plan.workers`；`with_rollout_workers` 只换 workers、`data_fp` 不变、0/同值不动原对象）、
`test_offline_notebook`（说明面写了公式与旋钮）。

---

---

## §6 手动 evalA 慢 6.7 倍的根因：绕开了节点池（339s → ~18s，改走 in-loop 同一派发器）（2026-09-22）

现象：控制台指标表里手动点的 evalA 耗时 **339s**（x20-noexplore it177），而 in-loop eval ~60s。
读数（同一份 `tmp/<课>/eval_log.jsonl` 的 `eval_summary`）：

| 轮 | sec | nodes |
|---|---|---|
| it165/170/175/180（in-loop） | 50.8 / 57.8 / 50.8 / 63.7 | local 100–118 · mac 64–133 · self 167–218 |
| **it177（手动 evalA）** | **339.2** | **`{"local-evalA": 400}`** |

根因：**不是评估变慢，是手动触发绕开了节点池**。in-loop 的 ~60s 靠三台节点分摊 400 局；
`rl/eval_a_once.py`（控制台 evalA 按钮 + 导入后自动评估的**唯一**启动点）自带一个
`for stage, seed in todo:` **本机串行**循环 —— 339.2 / 400 = 0.848 s/局，与串行假设逐位吻合
（`local-evalA` 这个 node 标签本身就是「自己跑的」指纹；同时刻并跑的 judge-414000 只贡献次要噪声）。

修复（**用户 2026-09-22 指令：evalA 不要只在 local 跑，和 in-loop 等同对待**）：删掉自带的串行
循环，改为**薄包装 `rl/eval_dispatch.py::dispatch_eval_round`**（= in-loop 的同一个
`EvalDispatcher`），于是语料（`a_eval_seed_list` 双轨）、去重（`eval_done_keys(min_iter=1)`）、
**节点门**（evalSupport / stageJsonSupport / bun 版本 / codeHash）、并行 ping + 并行权重下发、
本机份额（`policy.evalLocalSlots`，缺省 4）、账本 schema（`node=<节点 id>` / `wallSec`）、
summary 落账全部与 in-loop 同一份实现 —— 手动行与 in-loop 行从此可比。

- 脚本自己要管的只剩一件：`EvalDispatcher` 在「同 wver 语料已评完」时**早退不写 summary**
  （in-loop 有 `_drain` 覆盖判定兜底，手动触发没有）⇒ 保留按同 wver 回填本 iter 读数的
  `_write_summary_for_wver`（控制台按 iter 挂 evalData，缺本 iter 的 summary 表上永远空）。
- 布局与 in-loop 对齐：冻结权重快照 + 本机局目录都落 `traj/it<N>/`（`eval_replays_once` 的
  权重解析按 `traj/it*/_eval_frozen_weights*.json` 找，同址才有得找）；本机局目录收工即清。
- 本机 gate **立即置位**：手动评估不在训练的 PPO 窗口里，没有要让位的对象
  （不置位 = 本机槽位一路空等到 deadline）。
- ⚠ 一个必须先摘掉的坑：`rl/queue.py` 遮蔽 stdlib `queue`（脚本目录被插进 `sys.path[0]`），
  而节点派发要走 `dist_common.ping_nodes_parallel` / `post_weights_parallel` 的
  `concurrent.futures`（内部 `import queue`）⇒ **导入派发器之前必须先 scrub 脚本目录**，
  否则一 ping 就炸（旧实现从不派发，所以这个坑从没暴露过；同 `eval_replays_once.py`）。

验证（scratch 课程 `tmp/evala-smoke/smoke.jsonc`，1 关 × 双轨 100 局，**不碰任何真实账本**）：

```
[evalA] it105 … → 派发（与 in-loop 同路：节点池 ['self','mac','a97','a96'] + 本机份额）
[eval] it105: dispatch 100 greedy games (corpus=100, done=0) [dual-track anchor+rotor]
       -> [('self',8), ('mac',5), ('a97',7), ('a96',7)] [local tail-reserved ×4]
[evalA] DONE it105 sec=17.8 games=100 winRate=0.11 nodes={'local': 63, 'mac': 37} elapsed=18s
```

节点确实参与（mac 37 局；self 背压停派、a97/a96 未结算 = 派发器既有的节点门/软失败熔断行为，
in-loop 同样如此），100 局 18s 完成；it177 折算 339s → **~50s**（400 局，与 in-loop 50–64s 同量级）。
回归测试 `tests/test_eval_a_once.py`（派发器接线 + summary 读回契约）。

半路被否的方案（留档）：先只做了「本机并发」——`--workers`（min(8,CPU)）+ ThreadPoolExecutor，
实测 7.3×（scratch 100 局 144.3s → 19.9s）。用户口径：手动 evalA 不是「另一套本机评估」，
而是**同一次评估的手动触发**，必须与 in-loop 等同（节点池也不例外）；本机并发只是把单机吃满，
仍然拿不到集群那 5–8 倍并发。**别再往「本机并行度」方向调**——那不是这条路径的杠杆。

---

---

## §5 真机验收：mac(darwin-arm64) / a95(linux-arm64·Termux) 上 native 全绿（plan/rollout-eval-opt.plan.md T2/T3/T4，2026-09-21）

**起因（用户指令）**：「mac 和 a95 节点都已经更新到最新代码，请做一轮真机 rollout/eval 测试」——
补上 T2 DoD 唯一剩项（本机无法代跑的那一条）。

**方法**（生产同源，不绕协议）：读 `rl-config.json` 的 mac/a95 → `dist_common.node_ping`（含
`check_code_hash`）→ `post_weights`（rollout/eval 两个 kind）→ `fetch_task` 真派任务（
per-tick rollout `kind=rollout` + 干净评估 `mode=eval,kind=eval`）→ 解容器读 manifest。
环境与课程逐字同源：`x20-clutch`（stage 2000、stageJson 577B、lives 1、level 0、hard、12900 ticks），
权重 = `it176`（wver `b414e7d7…`），3 个 seed（4242/4243/4244）。临时探针在 `tmp/probe-realnode-native.py`。

**结果**：

| 项 | mac (192.168.0.88) | a95 (192.168.0.95) |
|---|---|---|
| codeHash | `e96d12b8…` == 训练机 | 同 |
| agent / 引擎 | `6ed7f11` / bun | 同 |
| rollout ×3 · eval ×3 | **`feat=native` 6/6** | **`feat=native` 6/6** |
| `validate_result` / `validate_eval_result` | 全 ok | 全 ok（无 409/超时/校验拒收） |
| 服务时长（manifest `elapsedSec`） | rollout 0.3–0.6s · eval 0.3–1.1s | rollout 0.5–1.2s · eval 0.9–2.4s（手机 CPU，量级合理） |

- **跨平台读数一致（最硬的一条）**：以本机 win32-x64 native 为基准，mac(darwin-arm64) 与
a95(linux-arm64) 在 3 seed ×（rollout, eval）= **12 组上 outcome/ticks/score/kills/enemyHits 逐值相同**
（例 s4242 rollout：`lives_exhausted` / 1062 ticks / score `0.23758612136100288` / kills 1 / enemyHits 6，三平台同）。
`codeHash` 相等已保证三边拿的是**同一份 prebuilt 库字节**（`src/nn/**` 在 codeHash 集内）⇒ 这是
「跨平台逐位一致」在真机上的证明，而不是单机声明。
- **本机 A/B 对照臂**（`tmp/probe-local-ab.py`，win32-x64，同 stage/seed/权重，`NN_NATIVE=0` 关 native）：
native 与 wasm **除 `feat` 外逐字段相同**，native 快 **1.38–1.75×（rollout）/ 1.54–1.67×（eval）**。

**顺带发现（已查清，与 native 正交）**：a95 上 eval 局的客户端往返明显大于 manifest 服务时长
（3.4–7.0s vs 0.8–1.1s）。两个实验定位到**两个独立问题**（探针 `tmp/probe-a95-eval-root-cause.py`、
`tmp/probe-a95-spawn-timeline.py`）：

**① 真因 = 每局一个新子进程的手机冷启动（~2.5s），且它会拖慢同节点其它请求。** 证据链：
· eval / bc（都不在 `PERSIST_SERVE_ENTRIES` ⇒ 每局 `spawn` 一个新 bun）的 `提交→202` = 2.5s / 3.0s；
  同节点 rollout（走长驻 persist worker）= 0.07s；mac 的同一个 eval = 0.03s（同一段代码 ⇒ 差异只可能在
  `spawn()` 这一处）。
· 该 2.5s **不是异步的**：任务进行中每 0.1s 打一次 `/v1/status`，第一次轮询的**应答被拖到 2.59s**，
  随后的轮询都在 0.02–0.45s 内回 ⇒ 事件循环在那段被占满（子进程冷启动把手机 CPU 吃满/占着调度）。
  实践含义：**手机上每局 eval 都会把 agent 卡死 ~2.5s**，并发吞吐被吃。
· 量级对得上：同局生命周期 3.2s − 子进程内口径 0.7s = 2.5s（见②的两份读数）。

**② 为什么它看起来像「接单前」且一直看不到真相：同步路径回给客户端的是**未盖章**的容器（真 bug）。**
`tools/agent/sampler-agent.ts:1575-1582`：`lruPut(key, stampServiceSec(key, buf))` 把**盖章副本**放进缓存，
但紧接着 `controller.enqueue(new Uint8Array(buf))` 发的是**原 buf**（子进程内口径）。⇒ 客户端第一次拿到
0.7s，同一局再请求一次（结果缓存命中，走 `cached.buf`=已盖章）拿到 **3.2s**：同一局两个 elapsedSec。
影响：生产 `fetch_task` 默认走同步路径 ⇒ **节点上报的「服务耗时」系统性漏掉子进程冷启动**（手机上漏 2.5s/局），
控制台的节点耗时会被低估；异步路径（1191 行）无此问题。

**两个修法已落地并真机验证（2026-09-21，commit `4a14987`，已 push）**：
· ② 结构上修：新增唯一出口 `serveResult(key, buf)`（盖章一次），同步/异步两处都只发它的返回值
  —— 「盖章值 == 对外值」由代码结构保证。判例 `tests/agent/result-stamp.test.ts`（纯函数，~2ms；含一条
  结构钉子：源码里不得再出现 `enqueue(new Uint8Array(buf))`）。
· ① `export-eval-game.ts` 加 `--serve`（协议与 `export-rl-rollout` 一字不差）+ 入 `PERSIST_SERVE_ENTRIES`
  ⇒ eval 走长驻 worker，手机上每局省下 ~2.5s 与那次事件循环卡顿。判例
  `tests/export-eval-game-serve.test.ts`（握手 / 与一次性调用等价（除 `elapsedSec`）/ 坏行不致命 / 不串局；
  自带 ~0.3s，另一次 agent over-HTTP 的端到端判例实测在 `bun test` 里起不来（30s 超时）已弃用）。

**真机复验（mac/a95 拉到 `4a14987` 后，同 stage/seed/权重）**：

| 指标（a95） | 修前 | 修后 |
|---|---|---|
| 同步 eval 一局（含首次冷启动那轮） | wall 3.43s，manifest `elapsedSec` 0.7（真值 3.2） | wall **0.45s**，`elapsedSec` **0.4** |
| 同局第二次（缓存命中） | 0.83s / 3.2（与第一次不等） | 0.06s / **0.4（与第一次相同）** |
| 任务期间 `/v1/status` 首轮应答 | 被拖 **2.59s**（事件循环卡住） | **0.04s**（无卡顿） |
| eval `feat` / 读数 | native / 同 | native / 同（`lives_exhausted` 与 ticks/score 逐值不变） |

全节点复验：mac + a95 各 3 seed ×（rollout, eval）仍 **`feat=native` 12/12**，且与本机 win32-x64 native
在 12 组上逐值一致（跨平台逐位一致未受影响）。仍存的一次性成本：**节点重启后的第一局**要付一次长驻
worker 冷启动（a95 实测服务侧 6.6s/首局，之后回到 0.6–1.2s）——这是新口径（真实生命周期）下应看到的数。

**未做**：云机（`ts_code.zip` 通道）仍只有 real-bun 哨兵 + 打包门禁的间接证据，没在真云机上确认
一行 `feat=native`——那条通道的验收要等下一次离线训练任务时看 shard 的 `feat`。

---

---

## §4 prebuilt 交叉编译分发：节点不再需要 clang（plan/rollout-eval-opt.plan.md §2.5/T2，2026-09-21）

**起因（用户提问）**：「rollout 节点机器上可能没有 clang，能本机编译出所有平台 windows/macos/linux/
android termux 的 native 库直接给它们使用吗？」——查节点池：self(win x64) / mac / a95·a96·a97·a98
(Android-Termux arm64) / lite / gcs，**异质且多数没有 clang**。原 T2「节点上自己编一次」等于 native 臂
在远端永远开不起来（只会打一行「编译器不可用」）。答：能，且这正是 T2 的正解。

**做了什么**：

1. 训练机交叉编译 6 目标 → `src/nn/native/prebuilt/<id>/conv_feats_native.{dll,so,dylib}` + `manifest.json`
   （源码 sha + flags + cc 版本 + 每目标 sha；共 ~78 KB），随既有 `git pull` 升级通道分发。
   目标 = win32/linux/darwin × x64/arm64；**Termux 用 linux-arm64 那份**（platform 报 linux、arch 报 arm64）。
2. **内核改免 libc**：交叉编译没有目标平台 sysroot，碰 `string.h`/`memset`/`memcpy` 就链不出来。
   内核的零填与整行拷贝换成 `cf_zero/cf_copy`（float 按值赋值，结果逐字节不变 ⇒ 本机 parity 仍 8/8 绿）。
   收益 = 产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / PE 导入表）⇒ glibc / musl /
   **bionic(Termux)** 都能 dlopen；代价 = padding 从 libc 向量化实现降级为自己写，实测 ~0–2%（2.52 → 2.53ms）。
3. **x64 只到 AVX1**（`-mavx -msse4.2`）而不是 `-mavx2`：实测 SSE2/SSE4.2 = 3.46–3.53ms、
   AVX1 = 2.60–2.64ms、AVX2 = 2.51–2.59ms ⇒ AVX1 已拿到全部收益（~27%），AVX2 只多 ~2%，
   而 prebuilt 是发给别人用的，`-mavx2` 的代价是「2013 年前的 x64 CPU SIGILL」。依旧禁 `-march=native`。
4. **解析顺序**：`NN_NATIVE_LIB` → **prebuilt** → `tmp/native` 本机构建 →（有编译器时）编一次。
   节点走第 2 条 ⇒ **零工具链**。两处实现同序（`src/nn/native-conv.ts` 与 tools 侧 `resolveNativeLib`，
   因 src/ 不许依赖 tools/）。
5. **新鲜度只在仓库侧**（`--check-prebuilt` + `tests/native-prebuilt.test.ts`，跑在常规套件里 ⇒ 提交必然过闸）；
   **不做运行期源码 sha 校验**——运行期真闸门是首用 attestation（native↔wasm 逐字节，不过即关 native）。

**证据（本机可复跑）**：

| 项 | 结果 |
|---|---|
| 6 目标产物 | linux/darwin 两份**逐字节可重现**；win32 差 9–13 字节（见坑 ① ） |
| 零依赖（机械核对） | 逐目标断言 ELF ET_DYN+EM_X86_64/AARCH64、Mach-O MH_DYLIB+cputype、PE machine+`IMAGE_FILE_DLL`；且产物里不出现 `libc.so`/`ld-linux`/`libSystem`/`KERNEL32`/`ucrtbase`/`VCRUNTIME`；`llvm-nm -u` 空、ELF `NeededLibraries []` |
| **真执行** | **WSL + python3 ctypes 加载入库 linux-x64 `.so`**：pooled+bufA 与 wasm **逐字节相同**（本机唯一能真跑非本平台产物的通道） |
| win32-x64 | 生产入口 bun:ffi 真加载入库 DLL，attestation **3/3 逐字节 vs wasm**（`tests/native-parity.test.ts`，现加载的就是 prebuilt） |
| 门禁 | `tests/native-prebuilt.test.ts` 16 例 + `bun run check` + `bun run build` 绿 |

**踩到的三个坑（都写进注释/DECISIONS 免得重踩）**：

① **Windows 免 CRT 的两件事**：任何浮点使用都会引用 `_fltused`（否则 `lld-link: undefined symbol: _fltused`）
⇒ 内核在 `CF_FREESTANDING` 下自带该符号；免 CRT 的 DLL 没有 `_DllMainCRTStartup` ⇒ 链接期 `-Wl,-noentry`
（PE 规范允许入口点 0，加载器跳过 init）。实测 bun:ffi 能正常加载。
② **Mach-O 的可重现性**：不显式给 `-no_uuid` + `-install_name` 时，lld 会把**临时输出文件名**（带 pid）
写进 LC_ID_DYLIB/UUID ⇒ 两次构建差 6 字节。给了这两个 flag 后 linux/darwin 均字节相同。
③ **COFF 不追可重现**：lld-link 的 `/Brepro` 把 TimeDateStamp 换成含临时 .o 路径的哈希，`-fno-temp-file`
也压不住（实测仍差 9–13 字节）。正确性靠 attestation、新鲜度靠 manifest sha256，都不依赖「重建字节相同」。

**仍未做**：真节点上确认一行日志（`native 资产就绪 source=prebuilt …`）与一局 shard 的 `feat=native`
—— 本机无法代跑；其余（格式/零依赖/本机加载/linux-x64 真执行）已全部进门禁。

**补（同日，用户点名）：云机通道也得带库。** 离线训练任务的 rollout 是在 **PPO 云机**上执行的
（`kind=iter`：训练侧把 TS 运行时打成 `ts_code.zip` 下发，worker 解到 `ts_cache/<sha>` 再
`bun tools/sim/export-rl-rollout.ts`）。云机既没 clang 也不持仓库，那条白名单原本只收
`.ts/.jsonc/.wasm` ⇒ 库根本不进包 ⇒ `native-conv` 找不到库 ⇒ 首用 attestation 直接跳过 ⇒
**静默回落 wasm**（不报错，只是每局回到 1338ms）—— 正是最难查的那种。修法：新增
`TS_CODE_BINARY_DIRS=("src/nn/native/prebuilt",)` + `TS_CODE_BINARY_SUFFIXES=(".dll",".so",".dylib")`
（只对该目录生效，不给 `.so` 开全局口子），6 目标全带、写 0755，目录缺失就 `HubClientError`
+ 提示重建命令。门禁两道：`tests/test_ts_code_pack.py`（4 例）+ real-bun 哨兵里「把 zip 解到临时树、
以它为 cwd 跑 rollout，断言 shard manifest `feat == native`」（探针验过断言是活的）。

---

---

## §3 rollout/eval 优化落地：native features 内核进生产（plan/rollout-eval-opt.plan.md，2026-09-21）

**做了什么**：Student `features`（rollout/eval 的绝对瓶颈，本机一局 1338ms 里九成）接入 native 内核，
与 wasm 逐位一致、且**门禁化**；选择的进程模型是**共享库 + `bun:ffi`**，不是起进程。

**为什么不是「CLI + 常驻子进程」**（这一条是本轮最贵的判断）：features 是**逐决策顺序依赖**的
（动作 → 下一状态），跨决策无法批量；每局 ~236 次调用，而本机进程启动实测 **42–54ms/次**
（`cmd.exe` 42.1 / `hostname` 54.4，n=30）—— 起进程比整局 sim 还贵，等于负优化。
FFI 每次 ~0.5µs，且 `in16/pooled/bufA` 直接传 JS 数组地址，连 wasm 的「拷进 48KB + 回拷 169KB」都省了。
代价是 native 只在 bun 引擎可用（node 无 FFI）⇒ 引擎选择必须把 bun+native 当独立臂与 node+wasm 比。

**实测（本机，可复跑）**：

| 臂 | ms/次 forward | 出处 |
|---|---|---|
| TS（兜底） | 52.38 | `bun tools/sim/perf-conv-wasm.ts --iters 400` |
| wasm | 6.04（对 TS 8.68×） | 同上 |
| **native** | **2.62（对 wasm 2.31×、对 TS 20.0×）** | 同上 |

单局端到端（同 seed 同权重，x20-clutch.it99，2400 ticks）：**1447ms → 853ms（1.70×）**，
且 outcome/score/kills/ticks 逐字段一致。引擎选择实测：
`bun 2.66ms[native] vs node 3.52ms[wasm] → bun` —— 在 native 之前这里会选 node（3.5 vs bun-wasm 6.2）。

**四条纪律**（都写进了 `DECISIONS.md §2026-09-21-goalnn-native-features-engine`）：

1. **flags 钉死 + 禁 `-march=native`**：它会开 FMA/AVX-512，而 clang 默认 `-ffp-contract=fast`
   ⇒ 乘加融合 ⇒ 与 wasm 不再逐位 ⇒ 异质节点 shard 字节抖动。
2. **首用 attestation**（本机真实权重 + 3 次 native↔wasm 逐字节）：节点是异质的，
   「本机 8/8 逐位」不能外推，只能在**本机**验；不过就关 native + 一行 warning。
   单测直接用「编一个输 0 的 poison 库」钉住这条守卫。
3. **算法单源**：共享库与对拍 CLI 都链接 `src/nn/native/conv_feats_native.c`（CLI 只剩编解码）。
   wasm 侧那份动不得（`wasm_simd128` intrinsics = 产品字节），所以两侧一致性交给
   `tests/native-parity.test.ts` 逐字节断言，而不是「同序」的口头约定。
4. **记账**：shard manifest 加 `feat`（native/wasm/ts），但**不进 `data_fp`**
   （protocol.py 只对 dir/wver/stage/seed 求 sha）——「以为开了 native 其实回落了」事后能查。

**顺手修掉的两个历史坑**：

* `tools/agent/engine-bench.ts` 旧版**自绘 wasm 内存布局**，按 v2 的 16 通道算而 v3 已是 18 通道 ⇒
  `offIn` 只留 16×SP，越界写进 bufA 区（dummy 数据所以没炸，但基准与生产不同构）。现已改为
  调用生产入口，顺便让基准能测到 native 臂。
* 旧的「8/8 逐位一致」只活在一个手工脚本里（硬编码 `tmp/conv_features_cli.exe`、脚本还不负责构建），
  tmp 一清即失效。现由 `tools/agent/native-build.ts`（钉死 flags + 指纹 `native-build.json`）
  + 门禁用例取代。

**eval 侧端到端验收（T4，2026-09-21）**：`export-eval-game.ts` 与 rollout 共用 `infer.features`，
它的 features 调用次数与 rollout 同量级（贪心逐步 argmax），所以收益同理：
同权重（x20-clutch.it99）/同关（s0）/同 max-ticks（2400），**5 个 seed 逐个跑两臂**——
单局 **1846ms → 1013ms（1.82×）**（5 局总计 9228ms → 5064ms）；
报告 `_eval_report.json` 的**全部字段逐字段一致（唯一不同的字段是 `feat`：native vs wasm）**，
即「换后端不改评估读数」（也再次印证逐位一致的工程价值）。
同时给 eval 报告补上 `feat` 记账（与 rollout 的 shard manifest 同口径）——
greedy eval 是节点侧跑的量最大的一类任务，这里看不见后端就等于「以为开了 native 其实回落」。
注：`export-eval-game.ts` 在 dist 哈希集内 ⇒ 改它意味着节点需随新代码同步（既有惯例）。

**补上的门禁（§3 遗留项）**：`tests/pack-memory-parity.test.ts`（3 例，~2.3s）把 `--pack-memory`
与写盘路径的字节等价从「一次手工冒烟」变成门禁：① `shardNpyEntries` ≡ `writeRlShard` 落盘文件
（逐字节 + 名字集合/顺序 == 导出的 `RL_SHARD_FILES`）；② `buildPack` 两份 entries 字节相同；
③ 真跑两次 exporter：内存模式不写 npy 目录，且用消费端读者 `unpackContainer` 解回后
10 个 npy **逐字节同一**、manifest 除 `elapsedSec` 外逐字段同一。

**③ 这个用例本身抓到一个真事实**（第一次进全量套件时红了）：**pack 不是字节确定的** ——
`packManifest` 带墙钟 `elapsedSec`（0.1s 粒度），并行负载下两条路径分别 0.3s / 0.2s ⇒ 包字节不等，
而失败现场的 10 个 npy 与 manifest 其余字段**逐字节相同**（已从失败产物直接验证）。
所以原稿「同 seed 冒烟两包字节相等（5561B）」是**舍入巧合**，已改成上面那条准确边界。
教训：把「手工冒烟偶然成立」写成结论，会在门禁里以「看起来是 flaky」的形式还回来。

**未做（诚实标注）**：真节点上的 T2（预编译库上传 / 本机编译）未验——代码路径在
（`ensureNativeAssets` 三态 + bundle 复制 + `NN_NATIVE_LIB`），需在节点上跑一次
`bun tools/agent/native-build.ts` 看启动日志与 shard 的 `feat=native`；
`--pack-memory` 的两路径字节等价仍只有手工冒烟、未进门禁；P1 实验内核（`tools/agent/native/conv_feats_opt.c`）不接生产。

**门禁**：`bun run check` 绿（tsc + 全量根套件，27s）；`bun run build` 绿；
新增 `tests/native-parity.test.ts`（8，含 eval 侧 `feat` 记账用例）与 `tests/pack-memory-parity.test.ts`（3），
`tests/agent/rollout-runner.test.ts` 扩到 32（含 native 臂反超 node、`nativeSha` 缓存失效、`parseBench`）。

---

## §2 x20-rebirth it19 rollout 208s 复盘：权重并行下发 + kept 短路径 + tail-join grace 默认 0（2026-09-19）


**一句话**：it19 `rollout_sec=208.4s` 不是仿真慢（真采集 ~26s / 12%），而是 volume 补波
每波重跑「串行权重 POST + tail-join grace 30s」叠出来的墙钟。三处收紧 + GBK 门禁修复。

### 根因（training-loop.log it19 时间线）

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 权重 POST w0 | 50s | 串行 6 节点，单节点 7–12s（purged 整包上传） |
| 真仿真 w0 | 21s | 124/124 局 |
| tail-join grace | 30s | 结果已齐仍等僵尸 worker |
| 补波 w1/w2 同构 | ×2 | kept 仍整包 POST + 又各 30s grace |
| **合计** | **~209s** | **仿真仅 ~26s** |

- 权重 POST 原为裸 `for nd in nodes` 串行；ping 早已 ThreadPool 并行（v4.0）。
- 补波时 agent 返回 `kept`（同 sha 幂等），但 HTTP body 仍整包上传。
- `tailGraceJoinSec` 默认 30s：all_settled 后在飞副本只剩竞速输家，结果注定被 dedup
  丢弃——等待无数据价值；旧实现的问题是「无上界」（join window+taskTimeout≈2700s），
  不是「必须等 30s」。

### 改动

1. **`dist_common.post_weights_parallel`** + dispatch/eval_dispatch/batch_eval 接线：
   ThreadPool 并行 POST，日志按配置顺序回放。`pure_collect_sec` 锚点**不变**
   （仍 = 全部节点权重就绪时刻）。
2. **kept 短路径**：`GET /v1/weights/cached`（头 X-Weights-Sha256 / X-Kind）→
   命中则不传 body 直接 kept。旧 agent 404 → 回退完整 POST。trainer
   `post_weights` 内先探针；agent `sampler-agent.ts` 新增该 GET + 纯函数
   `weightsCachedInBucket`。
3. **`resolve_tail_join_sec`**（`rl/dispatch.py`）：all_settled/halt 默认 **0**；
   窗口到期未齐默认 5s（`tailGraceJoinSecDeadline`）。policy 可覆写（e2e 用 2s）。
4. **同 it 波次权重复用**（本条追加）：`dist_common` 进程内 `_WEIGHTS_PUSHED[wver]→nodes`。
   `partition_weights_nodes` 拆 reuse/need；补波只对 need POST。ping/codeHash/bun
   exclude 时 `forget_weights_node`。`post_weights_parallel` 成功后自动 note。
5. **边分发边开采 + rollout 口径**（用户 2026-09-19，DECISIONS §2026-09-19-rollout-pipeline-metric）：
   `pure_collect_sec` = **权重开始分发 → 样本齐可交 PPO**（`last_settle − t_dist_start`，
   含与采集重叠的分发墙钟）。`post_weights_parallel(..., on_alive=)` 每节点成功即 spawn
   采样线程；local/reuse 先开采。旧口径「末局 − 全节点 ready」作废。
   **多波聚合**：`rl/reports.aggregate_rollout_collect`——volume 各波带
   `weights_dist_start_ts`/`collect_end_ts`，`combine_reports` 压成 it 级
   `min(start)→max(end)`（首波分发→全部样本齐）；iteration 事件写
   `rollout_collect_aggregated` / `rollout_collect_waves`。
6. **连续配额采集 VOLUME_RULE_V2**（用户 2026-09-19，DECISIONS
   §2026-09-19-volume-continuous-quota）：**退役离散补波**——串行 volume 路径改为
   `loop_core._volume_collect_continuous`：读账本 → 按分关差额+软停
   （`collected+inflight*est_s≥quota` 不再派）→ 小批派发 → 直到达标/game_cap/
   batch 安全阀。种子 `(it,stage,k)`；`resume.trailing_stage_samples_per_game`
   提供 est_s。wave 纯函数保留（旧 e2e）；生产不再走 `_volume_topup`。
7. **GBK 门禁**（`docs/nn/engineering.md` §3 同源）：`test_remote_iter_real_bun` / `test_tpu_probe_notebook`
   改 `tests.subproc_util.run_utf8`；`bun_version` 三处显式 `encoding=utf-8`。
   real_bun 另补 `lives_override=1`（exporter 2026-09-19 起无 flag 即响亮失败）。

### 未做（有意）

- **边分发边开采**：会改 `pure_collect_sec` 用户口径（起点 ≠ 全部权重就绪）——属决策项，
  不在本次执行范围。
- volume 多波跨波聚合 `pure_collect_sec`：dashboard 在无 dist 字段时仍回退
  `rollout_sec`（整段墙钟）；观测口径问题，非调度路径。

### 回归

- `nn-training/tests/test_dist_weights.py`（探针命中/404 回退/并行顺序）
- `nn-training/tests/test_tail_grace.py`（grace 默认 0 / deadline 5s）
- `e2e/test_run_rl.py::test_it_tail_join_grace_v317`（policy 覆写仍有界）
- `tests/dist-agent.test.ts` weightsCachedInBucket
- `bash tools/githook/nn-python-gate.sh` 绿（1243+ 用例）
- 根 `bun run test` 绿

### 预期效果（量级，非承诺）

- w0 权重：串行 ~50s → 最慢节点 ~10s（并行）+ 探针。
- 补波 kept：~16–20s/波 → ~2s/波（只探针）。
- tail grace：30s×3 波 → 0s（all_settled）。
- it19 同类轮次 rollout 墙钟有望从 ~200s 压到 ~40–60s（仿真 + 少量调度）。

---

---

## §1 in-loop eval 墙钟：软等可配（180s→30s）+ 本机份额提前放行（2026-09-17）

**为什么记这一笔**：用户检查训练流程后确认「eval 已藏进下一轮 PPO」——`_dispatch_delayed_eval(it)`
排在 `_serial_ppo(it)` **之前**、读归档 W(it-1)、事后只软等（`select_delayed_eval_it(6)==5`）。
但仍有**两段墙钟暴露在 PPO 之后**（都在 `rl/loop_steps.py`）：

```
改前： it6 collect(W5) ─┬─ 派发 eval(W5) ─┬─ PPO(6)  ◄── 节点侧 eval 藏在这里 ✅
                        │                 └─ _join_eval：软等 ≤180s（硬编码）❌①
                        └─ 本机份额 gate 只在 _join_eval 置位 ⇒ 本机 eval 局
                           在 PPO 收尾后才开跑（local_slots ≈ 5% 语料）❌②

改后： ① **不站等固定秒数**：尾巴交下一轮 rollout 收官这个自然边界收拢（_sweep_eval_tail，非阻塞）
          应急旋钮 policy.evalJoinSoftSec **缺省 0**（>0 = 回到 “PPO 后最多站等 N 秒”）
       ② 本机份额按「本轮 PPO 是否占本机核心」分档放行（policy.evalLocalEarlyEpochs，默认 1）
          · 远端 PPO（--ppo remote）/ 整轮上云（rollout=node）/ stream 轮
              → immediate：本机核心此刻空闲 ⇒ 派发即开闸（节点 hold_for_local 预留同步解除）
          · 本机 PPO → last_epoch：末 early 个 epoch 完成即开闸（复用 ppo_update 的
              on_epoch_done 钩子，判据 `ep_done >= epochs - early`，与吞吐 T4 预采同口径）
          · early=0 → on_join：维持 R6 原语义（纯让位）
          · 远端降级本机（_serial_ppo 落到本地）⇒ _regate_local_eval 收回我们提前放的 gate
```

**为什么是“边界收拢”而不是“站等 N 秒”（2026-09-17 同日修正）**：固定秒数两个方向都错——
尾巴早落地就白站（最常见），尾巴更晚就照样丢。尾巴在**下一轮整段采集**（分钟级）里自己能跑完、
自己写 summary（wver 键控、续跑幂等），所以到下一个自然同步点（下一轮 rollout 收官，即
`_dispatch_delayed_eval` 入口；另加收官 `_drain_pending_eval`）只需零成本观测/清账：已收官的
打一行 `tail settled during rollout`，还在跑的（异常：节点慢/挂）打 WARN 后交后台——时间基准用它
**自己的** `eval_window_sec`（不引入新魔数），它自己的 deadline 会结束它。intent/goal 模式**不动**：
止损判门要吃同轮 summary，仍走全预算 join（`window+60`）。

**验证**：`nn-training/tests/test_eval_timing.py` 新增/更新为 16 例（含「缺省零 join」「边界收拢」）——旋钮读数与坏值回落、放行档三分支、
`early_epoch_reached` 边界（early=0 / early>epochs）、派发即放行 + 降级收回、本机 PPO 未放行 /
epoch 钩子到点放行 / `evalLocalEarlyEpochs=0` 不加钩子、缺省零 join + 边界收拢（已收官/在跑/应急旋钮>0/无尾巴）、`evalJoinSoftSec` 应急值超预算夹回。
门禁：`e2e/test_run_rl.py -k "eval_deferred|eval_post_ppo_weights|eval_local_gate|tail_join_grace|early_race"
5 passed`；nn python 全量 **绿**（**1200 passed / 3 skipped，第一次无 any deselect/跳过**——
`docs/nn/engineering.md` §10 记的那条平台性存量红已在 §1.1 修掉）；ruff + mypy 干净。

### §1.1 顺带清掉存量红：盘符/UNC 路径在任何平台都被拒（2026-09-17）

**红点**：`test_remote_iter::test_spec_argv_weights_must_be_relative` 在 Linux 上恒失败——
`_iter_rel_path`（`remote/protocol.py`）只挡 `..` / `~` / POSIX 绝对路径，`os.path.isabs` 与
`Path.is_absolute` 都只看**当前内核**规则 ⇒ `C:/weights.json` 在 Linux 上静默过门。
这不只是“测试红”：Windows 盘符路径真能过 hub 的门，到节点上却指向**宿主盘**（或直接跑挂）——
节点的 cwd 契约不随 hub 内核变。

**修**：`_iter_rel_path` 增两条跨平台判定（只多拒、不放松）：盘符前缀 `^[A-Za-z]:`（绝对值与
**drive-relative `c:x`** 都算）与 UNC（`\\host\share`）。回归用例扩为参数化 5 例（`C:/`、`C:\`、
`c:weights.json`、两种 UNC）+ 一条反向断言（`weights.json`、`sub/dir/w.json`、`./w.json`、
`w-1.2.json`、`a_b/c.json` 照旧放行，防收紧误伤正常相对路径）。修前 4/5 参数化用例红（`//host/…`
本就命中 `os.path.isabs`），修后全绿。

**门禁**：`nn-python-gate.sh` **1200 passed / 3 skipped，rc=0**（首个无 deselect、无跳过的全绿）；
ruff + mypy 干净。

**仍暴露的墙钟（有意保留）**：① 收官 drain（`_drain_pending_eval`，无 PPO 可藏，
≤min(window+60,600)s）；② intent/goal 全预算 join。per-tick 主链现在**不为 eval 站等一秒**。
另：stream 路径「标签超前一轮 + 同 iter 双点」的缺陷**未动**（`--ppo remote` 下不可达，本地
stream 腿才可见），已在上一轮的流检查中记录，待单独处置。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §66→§1 · 旧 §95→§2 · 旧 §124→§3 · 旧 §125→§4 · 旧 §126→§5 · 旧 §127→§6 · 旧 §131→§7 · 旧 §136→§8 · 旧 §140→§9 · 旧 §141→§10 · 旧 §142→§11 · 旧 §143→§12
