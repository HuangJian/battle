# TPU / XLA 与 PPO 吞吐 — 技术档案

> 设备实测 / 编译缓存 / 单步耗诊断。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §8 TPU ragged tail 收尾：`target_transitions` 取成 mb 的倍数（2026-09-22）

承 §6/§7。用户口径（2026-09-22）：「**那就把 target_transitions 参数调成 1024 的倍数**」
——并裁决「**两件一起做**（既调倍数，也保留持久化编译缓存），`target_transitions` 取 **49152**，
**只改 x20-demo-mix**」。

### 机制（为什么倍数就够）

`chunk_episodes(episodes, mb)` 是**全局**切片（`shuffle=True`：展平全部 episode → 一次
permutation → `range(0, n, mb)`），所以 ragged tail = `n % mb`，与「哪一关」无关。而收进 PPO 的
总步数：

```
quota/关 = ceil(target_transitions / S)      # rl/volume_quota.target_per_stage
n = S × quota                                # 各关收满时
```

原 `T=48000, S=4, mb=1024` ⇒ `quota=12000` ⇒ `n=48000` ⇒ `48000 % 1024 = 896`（真机日志逐字
对上：`B=896:4步 / B=1024:184步`）。

**不变量：`S × ceil(T/S) ≡ 0 (mod mb)`。** 最省事的充分条件是「T 取 mb 的整数倍且 S | T」。

### 取值对比

| T | quota/关 | n | n/mb | 相对 48000 |
|---|---|---|---|---|
| 48000（原） | 12000 | 48000 | 46.875 ⇒ **尾巴 896** | — |
| 48128 | 12032 | 48128 | 47 整 | +0.27% |
| **49152（采纳）** | **12288** | **49152** | **48 整** | **+2.4%** |
| 45056 | 11264 | 45056 | 44 整 | −6.1% |

**为什么优于 Plan A（末 chunk 补到 mb + 权重掩码）**：这是**纯数据侧选择**——不补行、不掩码、
**零数学改动、无 ulp 差**；Plan A 会改归约树形状（末步 ulp 级差异）。两者都只动训练侧，不碰
任务定义（命数/敌数/关卡/终局）。

### 落地

* `curricula/x20-demo-mix.jsonc`：`target_transitions 48000 → 49152`（+1152 样本，+2.4%），
  并在文件里写下「不变量」与「TPU 步耗」注（含配对降级的诚实声明）。**只此一门课**。
* 持久化编译缓存（§7）保留 —— 两件一起做：形状从源头消掉 + 万一还有别的形状，被挤出时读盘
  而非重编。
* 与 AGENTS §15.5：采样量变更 = **新账本**（旧 48000 轮 it1–6 与新轮不同账本，混读以新轮为准）。

### 必须点明的两个边界（不假装是硬保证）

1. **软保证**：`n = S·quota` 只在**每关都收满**时成立。某关供给不足（日志 `SHORT (供给不足)`）
   ⇒ `n` 掉到非 mb 倍数 ⇒ 尾巴回来。当前 demo-mix 每轮恰 `kept 48000 / 4 stages`（收满），但这是
   数据供给的偶然，不是不变量。
2. **配对降级**：`paired_rotate_seed` 仍与 noexplore 同值，但每轮派局数变多 ⇒ 各关种子流消耗
   速率与历史臂不再逐轮相同 ⇒「同种子同 batch」不再逐字成立；归因按 **准配对** 看。

### 验证顺序

两条都需**点「训练」重打包**后才在任务包里生效。真机读数应看到：

* `job <id>: XLA 持久化编译缓存 已开启 → .../xla-compile-cache（缓存被挤出时读盘而非重编…）`
  （自描述：**日志里没有这行 = 旧包**）；
* `diag 累计汇总` 里 **只有 `B=1024`**（不再出现 `B=896`）。

### ⚠ 修正预期（2026-09-22，用户质询后翻 27 份账本重新定标）——「~15s」**不是本配置的数**

用户质询：「我印象中在线课程 PPO（48000×4ep）TPU 约 ~15s，重新评估你的 60s」；并给出下一手：
「翻 x 系列和 c 系列训练日志，ppo<20s 的轮次肯定是用 TPU 跑的，看对应课程设置」。

**先摆正量纲**（这一步之前就错了）：**云机自报的 PPO 时长 = `ppo_cloud_sec` = `wire.worker.grad_sec`**；
`ppo_sec` 是 **loop 侧墙钟**（含派发/等待/回传），**不是云机算的**。看 `ppo_sec` 会把两条腿比错。

#### 全库扫描（`tmp/*/training_log.jsonl`，27 份账本，`iteration` 事件）

**`ppo_cloud_sec < 20s` 的轮次只存在 4 门课，且全是同一个配置**：

| 课程 | mb | 采样/轮 | epochs | grad steps | `grad_sec` |
|---|---|---|---|---|---|
| x5-approach | 512 | 24000 | 6 | 47×6 = **282** | **16.2–17.3**（191/201 轮 <20s）|
| x7-rebirth | 512 | 24000 | 6 | 282 | **16.3–17.3**（170/211 轮）|
| x5-rebirth | 512 | 24000 | 6 | 282 | 16.4–18.0（27 轮）|
| x3-chip-k05 / k10 | 512 | ~23300 | 4 | ~184 | 10.8 / 10.7（各 1–2 轮）|

⇒ **「~15s」是 `mb=512 / 24000 samples / 6 epochs`（x5/x7 族），不是 48000×4**。用户确实记错了样本量
（他们自己也在怀疑）。x5/x7 的配方是 `lr 0.0005`（x20 是 3e-4）。

**而 x20 配置（`mb=1024 / 48000 / 4ep`）自带的干净基线是 ~44–50s**：

| 课程 | grad steps | `grad_sec`（min / 中位）|
|---|---|---|
| x20-noexplore | 188 | 49.5 / **50.2** |
| x20-clutch | 188 | 43.9 / 44.7 |
| x20-lambda | 188 | 49.7 / 50.3 |

关键：**`x20-noexplore` 没有 demo** ⇒ 不可能踩到 §5 的 host-index 每步重编译那条根因；它的 **50s
就是这条配置在 TPU 上的「干净上限」**。⇒ 我此前「目标 ~15s」是**第二次错**：**15s 从来不是 x20
工作量的数**。

#### 本修法修正后的正确预期

账（修后未消 ragged 的 it6 真机读数）：

| 项 | 值 | 说明 |
|---|---|---|
| PPO 单轮 | 85.0s | diag 总墙钟 84s |
| 累计编译 | **51s**（新编译 8 次） | 其中 B=1024 窗内 ~37s + ragged 4 步 ~14s |
| 188 grad steps × 0.174s/step | **~33s** | 稳态（`新=0, 命中=160`，编译=0） |
| 51 + 33 | 84 ✓ | 自洽 |

⇒ **编译全消后的地板 = 188 × 0.174 ≈ 33s**；参照 **x20 干净基线 ~50s**（noexplore，无 demo）⇒
本腿修完预计 **~33–50s**。**不是 60s，也不是 15s。**

旁证：我们的稳态 **174ms/步** 其实**比 x20 在线腿的 266ms/步（=50s/188）还快** ⇒「修完进 50s 以内」
是有底的（同一工作量，不比在线腿差）。

#### 两个必须挂上的不确定性（不假装是结论）

1. **同配置不同速**：x3-rebirth 与 x5-rebirth 是**逐字相同的配置**（mb512/24000/6），`grad_sec` 却是
   **33.8 vs 17.2**，差 2×。⇒ `grad_sec` **依 worker/设备而变**，而账本**不记 device**（全库搜过：
   无 `device`/`tpu`/`cuda` 字段）。所以**跨课程比 `grad_sec` 不可靠**，唯一可信的比较是 **x20 系列内部**。
2. x5-approach 是**单关**（`stages=2000`），x7-rebirth 是 4 关 —— 两者都 17s ⇒「关数」不是快慢主因；
   真正的主因（设备？模型大小？宿主竞争？）**账本回答不了**，不要拿它下结论。

**正确口径（以后引用）**：本修法目标 = **85s → ~33–50s**（对齐 x20 自身基线），**不是对着 15s 定**。

#### ⚠⚠ 第三次修正（2026-09-22 又一轮用户质询）——x20 的 ~50s 是 **GPU** 数，作废

用户补充两条事实：① **x20 系列课程都是 GPU 机器跑的**（⇒ 上面那张 44–50s 表**不能当 TPU 标尺**）；
② 同一课程从 **GPU 换 TPU：~40s → ~15s**（~2.7×，用户口径，带问号）。

⇒ 上一条「对齐 ~50s」**作废**。重定 TPU 标尺——用**真正 TPU 的那些轮次**：

| 课程 | 配置 | grad steps | `grad_sec` | 每步（@B=512）|
|---|---|---|---|---|
| x5-approach | mb512/24000/6ep | 282 | 16.2–17.3 | **~60ms** |
| x7-rebirth | mb512/24000/6ep | 282 | 16.3–17.3 | **~60ms** |
| x3-chip-k05/k10（单轮）| mb512/~23300/4ep | ~184 | 10.7–10.8 | **~59ms** |

三者每步耗时**彼此一致（~60ms/步）**，且与用户 A/B（2.7×）量级相洽 ⇒ **TPU 上 B=512 ≈ 60ms/步**。

**这顺手解掉了上一条的「不确定性①」**：x3-rebirth(33.8) vs x5-rebirth(17.2) 同配置差 2× = **GPU vs TPU**
（池子混装，`grad_sec` 依领取的 worker 而变）。按此读：x3/x4/x6 在 ~32–36 簇（GPU），x5/x7 在 ~16 簇（TPU）。

**折算到本腿（mb=1024，188 步）**：TPU 上 B=512 ≈60ms/步 ⇒ B=1024 若近似线性 ≈**120ms/步** ⇒ 188 步 ≈ **~23s**。
我们实测稳态 **174ms/步（33s）** ⇒ 距 TPU 标称差 **~45%**，**不是 2×**。

差额的候选解释（**假设，待真机验证，不是结论**）：**demo 路径的额外工作**——多一遍 demo 前向/反向 + BC loss，
且 `_demo_masked_ce` 的 `keep.any()` 是**设备→主机同步**，会把 XLA 图切开；it6 实录 **10 次 ExecuteComputation/步**、
**d2h ~126ms/步**。这台机器上 x5/x7 没有 demo。⇒ **不是编译问题**（编译已单独计过 51s）。

思索：我们的 174ms/步是在**同节点 220 个 rollout 工人并发**（`workers=220`）下量的，与在线腿同样有宿主竞争。

**三次修正后的口径（以后引用）**：**85s → ~23–33s**（TPU 标称参考），**不是 50s，也不是 15s**。
若需坐实 15–20s，得先把「demo 开销之外的残余」与设备级常态分开——需真机一轮 diag 对比同配置无 demo 腿。

**待用户一句话**：那次 GPU→TPU 的 A/B 是**哪门课**？若是 x20 系列（mb1024/48000/4ep），则
188 步 / 15s ⇒ **80ms/步**，我方 174ms/步就是 **2.2× 差**，那时 demo 开销之外还得再查；若是别的课，按步数折算。

#### ⚠⚠⚠ 第四次修正：A/B **不用回想，账本里就有**（同课双簇）

用户答「记不清了」⇒ 改从数据取证：**同一门课的 `grad_sec` 若中途换设备，会留下双峰**。扫 27 份账本
（按时间序压缩成高低簇），**找到了两例同课切换**：

| 课程 | 早段（疑似 GPU） | 晚段（疑似 TPU） | 倍数 |
|---|---|---|---|
| **x7-rebirth**（mb512/24000/6ep）| it1–27：**42.9s**（38.5–53.3）⇒ **159 ms/步** | it28–211：**16.7s** ⇒ **59 ms/步** | **2.6×** |
| x5-rebirth（同配置）| it1–28：**73.6s** ⇒ 261 ms/步 | it29–63：**16.9s** ⇒ 60 ms/步 | 4.4× |

⇒ 用户记忆的「同课 GPU ~40s → TPU ~15s」几乎逐字对上 **x7-rebirth 的 it28 切换（42.9→16.7s，2.6×）**，
只是那次 A/B 的课是 **x7 的配置（24000/6ep），不是 x20 的（48000/4ep）**。

**TPU 标尺现已四处自洽（每步 59–60ms @ B=512）**：x7-rebirth、x5-rebirth、x5-approach、x3-chip-k05/k10。
GPU 侧则散（159 / 261 ms/步），符合「池子里的 GPU 型号不一」。

**另：x20 系列全账本无双峰**（标度扫描里一个 x20 都没出现）⇒ **x20 全程单设备**，独立印证用户的
「x20 都是 GPU 跑的」。

#### 最终定标（本腿 = mb1024/188 步）

| 折算方式 | 结果 |
|---|---|
| 按每步线性折（2×59ms ⇒ 118ms/步）| 188 × 0.118 ≈ **22s** |
| 按样本量折（282步×512=144k 用 16.7s ⇒ 本腿 188×1024=192k = 1.33×）| 16.7 × 1.33 ≈ **22s** |
| 我方实测稳态 | 174 ms/步 = **33s** |

⇒ **TPU 标称 ≈ 22s，我方 33s，残余 ~1.5×**（不是 2×，也不是 4×）。残余的候选仍然是
**demo 路径额外工作**（多一遍 demo 前/反向 + `keep.any()` host sync 切图），**不是编译**（已单计 51s）。

**唯一能一锤定音的对照实验（待做）**：把 **x20-noexplore 放到离线 TPU 上跑**（与 demo-mix 同配置、
无 demo）⇒ 直接分离「设备/配置基线」与「demo 开销」：
* 若 noexplore-离线-TPU ≈ 22s ⇒ 那 11s 残余计入 demo，可立项优化 demo 路径；
* 若 ≈ 33s ⇒ 残余在配置/设备侧，demo 是「免费」的，本腿已经到位，无需再动。



测试：本轮只改课程配置（无代码路径变更）；配置经 jsonc 解析 + 不变量核算（`quota=12288`,
`n=49152=48×1024`, `ragged=0`）。

---
## §7 程序缓存容量旋钮：找不到 ⇒ 改走**持久化编译缓存**（2026-09-22）

§6 的遗留问题：ragged 形状（一轮只用一次）被挤出程序缓存 ⇒ 重编 ~14s/次。本轮按用户口径
「不动数值，先找旋钮」先做调研，结论与处置如下。

### 调研结果（公开文档）

* **torch_xla 官方文档**几乎逐字支持 §5/§6 的修法：编译产物会被缓存复用；`torch_xla.sync()`
  会**切开 IR 图**，「更小的图只编一次、后续复用」；并直言「shape 变了就会重编，可以
  **把输入 pad 成固定 shape** 来避免动态 shape」。另有一个与「每步变数据」直接相关的旋钮：
  **`XLA_NO_SPECIAL_SCALARS=1`**（XLA 把 0/1 当常量 ⇒ 值变就会重编，可关掉）。
* **没有程序缓存容量旋钮**：`openxla` 全量 XLA flag 表里 cache 相关的只有 GPU 侧
  （`xla_gpu_enable_command_buffer` / `xla_cmd_buffer_trace_cache_size`），TPU/Libtpu 侧无
  「program cache size」公开 flag；`LIBTPU_INIT_ARGS` 能透传 XLA flag 但不改变这个缺失。
  故「调大缓存」这条路**不成立**，不拿彩票当方案。
* 真正可用且**零数值改动**的是 **`torch_xla.runtime.initialize_cache(path)`**（torch_xla 2.5+，
  Kaggle 镜像 2.8.0 有）：编译产物**落盘**，缓存被挤出后再用到时是「读盘同一份可执行」而
  不是「重编」——同 HLO 哈希 ⇒ 同一程序，**不改变任何数值**。

### 落地

* `ppo/common.py`：`xla_enable_compile_cache(path, enabled=None) -> str`（绝不抛；未装
  torch_xla / 老版无此 API / 初始化失败 ⇒ 各自返回一行状态）。硬约束一并照顾：必须在**任何
  计算之前**调、同进程重复调会抛 ⇒ 内部记账做幂等；`XLA_PERSISTENT_CACHE=0` 关。
* `remote/worker.py`：tpu 分支在 `xla_device()`/指纹/速度自检**之前**开启（那些自检已会产生
  第一张图），目录 = `work_dir / "xla-compile-cache"`，并把状态打进日志（自描述：日志里没有
  这一行就是旧包）。
* 未采用 `XLA_NO_SPECIAL_SCALARS=1`：它改的是**编译器常量折叠**，而本轮已证明真正病根是
  host 索引内联（已从源头修掉），且折叠方式变了原则上可能影响 fp 结合顺序——与用户
  「不动数值」的口径不一致；等有独立证据再说。

预期：ragged 那一步从 ~14s 降到亚秒（读盘），且**新进程/新 it 的冷启动编译**也一并变便宜。

---

---

## §6 真机验证：修复后 PPO 单轮 1200s+ → **88.9s**（2026-09-22）

承 §5：`demo_index` 复用缓冲进包后（ipynb 重打包），真机 `x20-demo-mix` it2：

| 量 | 修前（it1） | 修后（it2） |
|---|---|---|
| PPO 单轮 | ~1200s+（eta 一度 2193s） | **88.9s**（it wall 94.5s） |
| 单步（B=1024） | 11~12s（`新=2`/步） | **0.17s**（`新=0, 命中=10`） |
| 累计新编译 | epoch1 就 41 次 | **全程 8 次**（`累计编译=54s` 里大半是指标重置假读） |

前 12 步逐步读数全是 `窗口=1步/0.17s 编译=0.00s(新=0,命中=10)`——**编译只付一次，之后纯执行**。

### 剩下的 64%：ragged tail（下一个杠杆）

修后 89s 里有 **~57s 是每个 epoch 一次的那一步**：

```
B=896:4步 累计墙钟 43s   ← 每 epoch 只出现一次的末 chunk（48000 % 1024 = 896）
B=1024:184步 累计墙钟 45s ← 0.17s/步
```

每个 epoch 含 ragged 那一步的窗口都是 14~17s 且 `新=2`（两张图各一次全新编译，~7s/张）——
**同一个形状、同一批张量、每 epoch 重编一次**。与 B=1024 的唯一结构差异：它一个 epoch 只用一次。
⇒ 最可能的是**编译缓存容量/淘汰**：每步一用的 10 张程序常驻，而「一轮只用一次」的那两张在
下一个 epoch 前被挤出（真机节律 4/4 次全中）。

候选处置（按侵入性排序，未实施）：
1. **把末 chunk 补到 mb + 权重掩码** ⇒ 全程单一形状。数学上等价（去零后按合法行数取均值），
   但归约树形状变了 ⇒ 末步会有 ulp 级差异（需 DECISIONS 记录）。
2. 找 XLA/TPU 的程序缓存容量旋钮（`LIBTPU_INIT_ARGS`/`XLA_FLAGS`）把「一轮一次」的形状留住。
3. 每 N 步用杂形状「保温」（hack，不推荐）。

无论哪种都不该动任务定义（命数/敌数/关卡/终局标准），也不该用教师表现反推阈值（AGENTS §0.2）。

### 附带洞见（写进代码注释）

* **XLA 的 metrics delta 会被重置放毒**：真机实录 `编译=73.93s`、`追踪=-52.57s`（新形状出现/
  缓存事件会重置计数器）。判定行已加护栏：只在 `编译 ∈ (50%, 100%]×窗口墙钟` 时给百分比结论，
  超出时改写「读数不可信，只看新编译次数与墙钟」。
* 真机 `XLA 预物化`与`demo 索引缓冲已启用` 两行自描述日志：**日志里没有它，就说明包是旧的**——
  别拿旧包的结果判修法（本回就差点误判）。

---

---

## §5 根因定案：demo 混 batch 的 host 索引让 XLA **每步重编译**（2026-09-22）

承 §4 的观测（诊断行上真机即定案）。真机读数（`x20-demo-mix` it1，B=1024 恒定、kl1/demo1 恒定）：

| 步 | 窗口墙钟 | XLA 账本 |
|---|---|---|
| s=1 | 11.76s | 编译 **10.42s（新=6，命中=4）** + 追踪 7.3s |
| s=2 / s=3 / s=5 | 11.5s / 11.5s / 11.5s | 编译 **11.1~11.2s（新=2，命中=8）** |
| **s=4** | **0.31s** | 编译 0.00s（**新=0，命中=10**） |

同一批参数下只差在一次缓存命中：**命中的那一步 0.31s**（也就 188 步本该 ~1 分钟）。
形状与分支（B/demo/kl/ref）全程恒定 ⇒ 不是形状漂移。

**微探针（设备上同一张 bank，四组对照）**：

| 组 | 每步索引的来路 | 结果 |
|---|---|---|
| A | `bank[np.random.randint(...)]`（**现状**） | #0 `新=2`、#1 `新=2` ⇒ 每步新图 |
| C | 固定 index 张量 | 只有 #0 编译，#1/#2 `新=0` |
| D | 完全不索引 | 同 C |
| **E** | index 先 `to(dev)` + `mark_step` 物化 | **`新=0`** |
| **B** | 复用同一设备张量 + `copy_` 就地改值 | **`新=0`** |

**根因**：XLA 下把 **host numpy 数组直接交给高级索引**，索引数据进不了「图输入」那条路——
同一批形状下每步仍是一张**新图**；demo 混 batch 的两张图（demo 前向 + BC loss/backward，
中间被 `_demo_masked_ce` 的 `keep.any()` 同步切成两段）各重编一次 ⇒ **每步 2 次全图重编译**，
而一次这样的编译就是秒级（§4 实测 7.7s）。这就是「离线 8~10s/步」的全部。

**修法（纯训练侧实现修复，不碰任务定义/不降难度）**：

* `ppo/common.py`：新增 `demo_index(buf, didx)` —— 有缓冲就 `copy_` 就地改值并**返回同一张量**
 （图输入恒定 ⇒ 签名恒定），`buf=None` 时原样返回 numpy 索引（非 demo 路径逐字节不变）。
* `ppo/engine.py`：循环外建一次 `demo_idx_dev = torch.zeros(per_mb, int64, device)`，
 每步仍用 `np.random.randint` 抽样本（**抽哪些样本、RNG 顺序一字未改** ⇒ 数值逐位相同），
 只是索引先落到复用缓冲再 `demo_t[...][_didx_t]`。

**预期**：188 步从 ~35min 降到 ~1~3min（编译只在前几步付一次）；`PPO_XLA_DIAG` 行应从
第二个 chunk 起就 `新=0, 命中>0`。**这条修法也让「step 4 那一步的 0.31s」成为常态而不是例外。**

测试：`tests/test_xla_step_diag.py`（复用同张量/数值与朴素 numpy 索引逐位相同/None 路径不变/
engine 源码守线「不得再 `demo_t[...][_didx]`」）。

已知噪声（记录备查）：XLA 的 metrics 计数器会被重置，所以 `编译=−0.56s`、`追踪=−0.825s`
这类**负 delta** 会偶发出现（探针 A#1 实录）——判读只看 `新=` 与墙钟，量级上不影响结论。

---

---

## §4 TPU 步耗诊断进日志：把「8~10s/步」从猜测变成读数（2026-09-22）

承 `docs/nn/runtime-opt.md` §7 与 `test_tpu_backend_guard.py`：Kaggle v5lite-8 离线课程 PPO 单步 8~10s，而
**同引擎在线课程此前是 ~15s 跑完 4 epoch**（用户口径）。真机探针把范围收到了两条：

| 探针读数 | 值 | 含义 |
|---|---|---|
| `ExecuteTime`（编译命中后） | 20~90ms/次，`LazyTracing` 1.7µs 中位 | 执行链路健康、不是主机侧 op 风暴 |
| `CompileTime`（一次未缓存编译） | **7.7s** | 与「8~10s/步」同量级 ⇒ 嫌疑＝**每步都在重新编译** |
| CPU 对照同一步 | 0.54s/步（96 线程） | 主机 CPU 都比它快 15× |

但探针只有 2 个 chunk，无法区分「每**调用**编一次」与「每**步**编一次」。于是本轮**不动算法、只加观测**：

* `ppo/common.py`：`xla_metrics_snapshot()`（读 `torch_xla.debug.metrics` 的 CompileTime /
  ExecuteTime / LazyTracing / 拷贝耗时 + `UncachedCompile`/`CachedCompile`/`ExecuteComputation`
  计数）/ `xla_metrics_delta()` / `xla_delta_str()`。非 XLA 或未装 torch_xla ⇒ `{}`，尽管调用点
  整体跳过（**零开销**）；解析只读 `Accumulator`/`Value` 行（第一版 `(.*)$` + `re.S` 会把报告里
  剩下所有指标错加进 CompileTime——测试用真机报告原文钉住）。
* `ppo/engine.py`：`ppo_update` 每 chunk 迭代在 `xla_mark_step` 之后取一次快照，打印
  「窗口步数/墙钟/图签名(B/demo/kl/ref)/编译·执行·追踪·拷贝增量」，编译占窗口 >50% 时额外打一行
  **判定**。采样节奏有预算：前 `PPO_XLA_DIAG_FIRST`（12）步逐步、之后每 `PPO_XLA_DIAG_EVERY`
  （16）步一次，**未取样的步不推进基线**（delta 累积，期间的编译会落进下一次读数）；
  `PPO_XLA_DIAG=0` 全关。健康的 TPU 单步 ~40ms，不能让诊断吃掉几个百分点。
  epoch 末再打一行累计汇总（按 chunk 形状分组 + 累计编译/新编译次数/总墙钟）。

判断口径（写进日志，避免下次再靠面板猜）：**每步「新编译=1」+ 编译秒级 ⇒ 图签名每步都在变
（真正的病）；「新编译=0、命中>0」而墙钟仍秒级 ⇒ 病不在编译**（看执行/主机侧）。

测试：`tests/test_xla_step_diag.py`（真机 metrics 原文解析、单位混排、delta/格式化、
engine 接线与「基线只在取样分支里推进」的源码守线）；`tests/test_tpu_backend_guard.py` 同步。

顺带修掉一个间歇红的门禁噪声（与本改动无关、但会挡绿）：
`tests/test_measure_checkpoint_rss.py::test_build_stack_is_cheap_and_keepalive_holds_it`
把 ΔRSS 的**符号**当不变量（`adam_mb >= 0`），而同机 `xdist -n 12` 下分配器回收会让它为负
（实录 `-0.03`/`-0.05`，单文件跑恒正）。改为「必须是 MB 量级的数」（`abs < 100`）+ 原有的
确定性 `theory_mb` 结论，符号/绝对值一律不钉（与该模块 docstring 的既有口径一致）。

---

---

## §3 云端多卡确认：PPO/BC 双卡默认真用上（2026-09-13，用户指令「双/多卡时训练跑在双/多卡上」）

**缺口**：PPO 的 DP 能力在（worker run_job 对 --device cuda-dp 包 DataParallel，
T4x2 实测 1.92×，§1）但 cell 默认 `use_multi_gpu: False`（多卡机器第二张卡闲置）；
BC 更是完全单卡（worker `_bc_device` 把 cuda-dp 砍成 cuda，bc.py 无 DP）。

**补齐（默认反转）**：

- **cell 默认 `use_multi_gpu: True`**：auto + 2+ 卡 → cuda-dp（响亮日志）。opt-out
  保留（要逐位可比改 False）——多卡机器重跑 cell 后新会话即生效；在跑腿中途换 DP
  = 数值不可逐位比，操作员须知（markdown/日志双提示）。
- **bc.py 补 DP**：`_resolve_bc_device`（cuda-dp：2+ 卡真 DP；单卡/无卡**响亮退化**
  cuda/cpu——与 worker 同语义）+ `_bc_raw`（DP state_dict 的 "module." 前缀绝不进
  weights.json——TS 运行时格式防线）；train() 经 raw_model 落盘/恢复/计数（DP 与 raw
  共享参数对象，训练原地更新终态即 raw 终态）。
- **worker `_bc_device` 改透传**（不再代砍单卡）；tpu/xla 拒收不变。
- **单卡机器零变化**：resolve 尊重 1 卡现实（cuda），退化路径响亮。

**验证**：`test_bc_dp.py`（12 例：cuda-dp 多卡/单卡退化/无卡退化/透传、_bc_raw 解包
+ "module." 前缀真实存在性断言 + 参数对象同一性、worker 透传/tpu 拒收）+
`test_notebook_runtime.py`（24 例既有）全绿；nn-python-gate 全量绿。
真 2 卡行为由 worker 既有 DP 分支 + §1 T4x2 实测背书（本机无 2 卡，DP 转发无法本地跑）。

---

## §2 TPU PPO「单步递增爆炸」根因定案 + 修复（2026-09-11，c6b-margin 首个 TPU job 45~52s/step / eta 5.9h）

**用户动作链**：贴远程 log（job 6c0a0424488e5b70）→ 探针 E 段复现（Colab）→ Kaggle TPU 三重否决 → `--step-mark` 收敛 44ms → engine 生产修复。

### 2.1 现象

- 真实 job：142 chunks × 4 ep = 568 步，单步 **24→45→52s 线性递增**（eta~5.9h），hub wait_job(1800s) 必超时。
- 探针 E 段复现（Colab/Kaggle TPU）：E0/E1 均 1.7→13~24s 递增；**E2 干净基准（bench_case 已 warmup）亦 ~10s/step**；`--tail 0` 无尾块、3 chunks 固定 shape 仍递增 → 与 engine 结构 / ref / 尾块 / perm 全部无关。

### 2.2 根因（mechanism）

torch_xla 惰性模式下，`.tolist()` materialize 只断言其依赖子图；`backward()`/`optimizer_step()` 的在途节点不 drain，跨步骤累积 → **编译/执行的图线性变大 → 单步耗时 ∝ 已走步数**。CPU/CUDA eager 即时回收，本地永远看不见（所有"本地 OK、云端 TPU 爆炸"的旧困惑都源于此）。

**连带修正**：`plan/ppo-optimization.plan.md` §0.5「TPU 快 GPU 4.7× / 44 ms/step」是**测量假象**——那次 `chunks=1 epochs=1` 总共 ≤3 个梯度步，递增尚未展开；44ms 真实吞吐需「每步有 mark」的形态（探针 E1+mark 实测 1803→109→71→44→41→42ms）。以往所有 TPU 端 ppo_sec 读数需按此重读。

### 2.3 修复（生产落地）

| 位置 | 改动 |
|---|---|
| `ppo/engine.py::ppo_update` | 每步 stats append 后 `xla_mark_step(device)`（图执行边界；非 XLA no-op，CPU/CUDA 逐位不变） |
| `ppo/engine.py` | ref 预计算后一次性 mark（防首步巨图编译） |
| `ppo/common.py` | `xla_world_size()` helper（新版 `torch_xla.runtime.world_size()` / 旧版 `xm.xrt_world_size()` 双探） |
| `remote/worker.py` | TPU 分支日志：device + world_size（H2 诊断不再静默） |
| `tools/tpu-probe.py` | E 段全套：`--engine/--per-step/--skip-e0/--tail 0/--step-mark` + XLA metrics 累计 + world_size 诊断 |

**验证**：探针 Kaggle 闭环 44ms；58 项相关 python 测试全绿（kickstart_cache/scalar_sync/common/numerics/no_torch_on_import/remote_hotswap/run_rl_m1）。

**待实证**：下一轮真实 TPU job（首步几十秒编译一次性，后续稳定 ~50ms，epoch 时长均匀）；hub 需重打包 code.zip 触发 worker sha 热替换。

---

---

## §1 PPO 吞吐：三设备实测 + ref 缓存 / 传输压缩 / 多卡落地 + TPU 设备层（2026-09-10）

> ⚠ **2026-09-11 作废「TPU 快 GPU 4.7×」结论**：44ms/step 是 ≤3 步微基准的测量假象，详见 §2（根因 = torch_xla 惰性图不 drain → 单步线性递增；修复 = 每步 mark）。本节其余（ref 缓存/传输/多卡）结论不受影响。

同一会话四条线：**度量**（三设备实测矩阵）、**优化**（ref 缓存 / 传输压缩 / 多卡）、
**扩容**（TPU 设备层）、**基建**（门禁两项修复）。数字来自自包含探针
`nn-training/tools/tpu-probe.py`（CPU/CUDA/TPU 通用）与一次真实 job 日志
（`826081bcb840f76d`）。台账：`plan/ppo-optimization.plan.md`。

### 1.1 性能画像，以及我三次被实测推翻的假设

一轮 = 37 chunks x 4 epochs = **148 个梯度步**。真实日志逐段（GPU 单轮 47 s）：

```
claim 08:28:02 -> payload 下行 3.83 MB (2.3 s) -> code 缓存命中 + model/opt 恢复
  + ref 加载 + 150 shards load + GAE（全部 <1 s）-> kickstart ref 预计算 3 s
  -> PPO 4 epochs / 148 梯度步 33 s -> result 上行 1.63 MB (7.4 s)
```

**探针被这份日志验证**：真实梯度 33 s / 148 步 = **223 ms/step**，探针测 206 ms（差 8%，
落在 epoch 级统计与心跳开销上）⇒ 以后不必每次上真机验证。

**三次错误假设（留档，勿重走）**：

| # | 假设 | 证伪证据 |
|---|---|---|
| 1 | 「GPU 利用率仅 2.9%，首要嫌疑是 `.item()` 同步串行化」 | A 段（**完全无同步**）就已 191 ms/step；同步税 B_old−A 仅 **+17 ms**；8 次合 1 次只省 **+2 ms**。按 A 段口径利用率 ≈ **7.2%**（旧 2.9% 用了含传输的 70 s 口径）⇒ **D2 结案**。 |
| 2 | 「~40 s 差额是 `load_episodes` + GAE + 统计」 | 日志 `shard IO + GAE done for 150 episodes (0s)`，整段不到 1 s。真差额在**传输**。 |
| 3 | 「探针 A 段在 CUDA 上是算力下限」 | `sync_mode="none"` 走 `Backend.mark()`，CUDA 上它是 **no-op** ⇒ 计时只含 CPU 入队（A=64 ms 而 B_new=192 ms）。已加真正的 `sync()`（`torch.cuda.synchronize()`）。 |

### 1.2 已落地四项

| 项 | 改动 | 实测 | 数值 |
|---|---|---|---|
| **ref 前向缓存** | `ppo/engine.py`：kickstart 的 ref 输出只依赖 (obs,scalars,mask)，逐 chunk 固定且 ref 冻结（BN-free）⇒ 跨 epoch 不变；原「每梯度步重算」改为「按 chunk 预计算一次 + 索引复用」 | CPU +18.6% / **GPU +22.7%** / TPU +31.5%（比例随设备变快而升）；真实日志确认省 **~9 s/轮（20%）** | ✅ 逐位不变 |
| **标量同步批量化** | `ppo/common.py::sync_scalars` + 三后端：每步 6-8 处 `.item()`/`float()` 合为 1 次 `stack().tolist()` | GPU **+2 ms（0.8%）⇒ 收益≈0**（见 21.1 #1） | ✅ 逐位不变 |
| **传输压缩** | `remote/protocol.py`：① 4 个编解码函数改 gzip(level 6)+base64（魔数自动判别，旧格式可解）；② **方案B v2 体**——result 上行改 `BRV2` 魔数 + JSON 头 + gzip **裸二进制段**，省掉 base64 的 33% | ① 上行 1,634,596 → 1,150,292 B（−29.6%）；② **再 → 863,023 B（合计 −47.2%）**，上行 7.4 s → **~3.9 s**；`opt_init` 下行 −31.3% | ✅ 无损；v2 往返逐字段一致 |
| **多卡** | `remote/worker.py` opt-in `--device cuda-dp`（`nn.DataParallel`，单卡自动退化）；产物落盘一律用未包装的 `raw_model` | **B_new 192 → 100 ms/step = 1.92×**（接近线性） | ⚠ 归约顺序变 ⇒ ulp 变，属新开实验臂 |

**三设备实测矩阵**（s/轮 = s/step x 148）：本机 CPU 3.607 s / Kaggle GPU T4x2 191 ms /
**Kaggle+Colab TPU v5e-8 39 ms**（A 段）⇒ **TPU 快 GPU 4.7×**，不是「配额接力」的量级。

**被否掉的两条路**（勿重走）：① 去掉重复权重（`result` 里 `weights_json` 与 `tar/model.pt`
是同一份）—— **hub 刻意免 torch**，无法把 model.pt 转回 `init_weights.json`，去重会破坏
`init_weights_fp` 对账；② 传输「方案B」（gzip 后传裸二进制）能再省到 47%，但要改 POST 体格式
+ hub 端 parser，增量仅 ~1.3 s。

### 1.3 TPU 设备层（扩容，非提速）

`ppo/common.py` 增 `is_xla / xla_device / optimizer_step / xla_mark_step / _to_cpu_state`；
三后端 `opt.step()` → `optimizer_step(opt, device)`；`_ppo_save` 落盘前把 XLA 张量物化到 CPU；
`remote/worker.py` 支持 `--device tpu|xla`；`ipynb/battle-rl.ipynb` cell3/4 改为
CUDA→TPU→CPU 探测。**踩坑四条**（全部来自真机）：

1. **API 改名**：`xm.xla_device()` → `torch_xla.device()`；`xm.mark_step()` → **`torch_xla.sync()`**
   （按 `sync` → `mark_step` 顺序探测，兼容旧版）。
2. **TPU 是独占 PCI 直通设备**（`/dev/vfio/*`）：notebook 探测 cell **禁止 import torch_xla**
   （2.6+ 导入即自动探测 PJRT device、会占住设备），改用 `importlib.util.find_spec`；
   探针必须在**内核进程内**跑（子进程抢不到设备 → `open(/dev/vfio/0): Device or resource busy`）。
   占用者是**别的进程**可 kill 释放；是**本内核**则 Python 无 release API，只能重启 runtime。
3. **`!python -u` 必须带 `-u`**：子进程 stdout 块缓冲 ⇒ 输出要等进程结束才吐，看起来像卡死。
4. **慢段要能跳**：TPU 上 `.item()` 每步 = 整图 materialize（分钟级），C 段每个新尾块 shape
   一次 XLA 编译（数十秒级）。探针新增 `--skip-sync-tax / --warmup / --repeat`，
   notebook 拆成三段各自可完成。

### 1.4 门禁两项修复

- **`platform_utils.rmtree_best_effort`**：沙箱删除保护抛 `SystemExit`（BaseException），
  `shutil.rmtree(..., ignore_errors=True)` **挡不住** ⇒ 直接打死调用线程（实证：`rl/dispatch.py`
  派发线程被打死后不再派发，竞态日志缺失导致 `test_it_early_race_v314` 假红）。
  全部清理路径改走该助手；`rl/workdir_sweep.py` 的计数改挂到返回值上。
- **`test_it_early_race_v314` 假红的真根因（结构性）**：单节点配置下
  `pick_race_target` 的 `nd_id not in inflight_nodes[task]` **恒假** ⇒ v3.10 race lane
  **永不触发**（失败日志里一条 `— race lane` 都没有），断言只能靠 v3.7 fan-out 的
  `next(iter(inflight))`（dict 插入序，取决于线程抢锁）偶然命中 seed111。
  改 `cfg["nodes"]` **追加第二节点**（慢任务挂 A、B 空闲 ⇒ race lane 按 `sorted(inflight)`
  确定性选中 `(0,111)`，全表最小键）；`took9` 由 12~15 s 降至 **2.0 s**，门禁连跑两次全绿。
  修完还**提高**了真实覆盖 —— 原先那条路径在该配置下根本没被执行过。

### 1.5 kickstart 系数阈值：退火到 ~0 后不再白付（2026-09-10 追加；用户确认课程不会回抬）

**发现（来自 21.1 的真实日志）**：`kickstart ref 已加载（kl=1.4551915228366852e-11）` ——
系数已是 **2^-36 ≈ 0**，但判据是 `> 0` ⇒ 照付：ref 权重进 payload（~0.36 MB）+ worker 每轮
预计算 **3 s** + engine 每轮算 ref 前向。而它的数学贡献 `1.46e-11 x 0.126 ≈ 1.8e-12`，相对
`policy=0.0046` 完全可忽略。

**根因**：系数按 `kickstart_kl * kickstart_decay ** N` **几何衰减**，永远到不了精确 0
（2^-36 正是 0.5^36）。

**修法（四处；源头单点归零 + 消费端兜底）**：

| 位置 | 改动 |
|---|---|
| `remote/protocol.py` | 新增 `NEGLIGIBLE_COEF = 1e-9` + `coef_active()`（顶层免 torch，hub 侧也可 import） |
| `run_rl.py::update_kwargs` | **唯一的衰减源**归零：低于阈值直接置 0.0。`rl/loop_steps.kickstart_coef` 只是它的薄包装 ⇒ 单点归零即贯通全链 |
| `rl/loop_steps.py` | 附 ref 字节的条件由 `kick_on` 改为 `kick_on and coef_active(kick_kl)` —— 原先"缰绳早已松开、ref 权重还在每轮空运" |
| `remote/worker.py` | 判据换 `coef_active` 并打日志，兜住"旧 hub 产出的、仍带微小系数的在途 manifest" |

**行为**（decay=0.5）：`it=30` → 1.86e-9 仍活跃；**`it=31` 起精确 0.0**；实测踩到的 `it=37`
现在精确为 0.0。回归测试 `tests/test_run_rl_m1.py::test_kickstart_coef_anneals_to_exact_zero`
（含 1e-9 上下边界）。

**兼容性**：新 hub + 旧 worker、旧 hub + 新 worker 两条组合都安全（精确 0.0 两侧都判"关"；
微系数由 worker 侧阈值兜住）。

### 1.6 payload 容器 zip/deflate → tar.xz（2026-09-10；「下行改 v2」不成立后的实测替代）

**先更正一个误判**：我原以为 payload 下行也背着 base64（"可再省 ~0.6 s"）—— **错**。
pull 模式下 `hub_server._get_payload()` 是 `self._bytes(p.read_bytes())`，**本来就是裸二进制**；
`payload_b64` 只存在于 **push 模式**（`push_client.py` → `worker_server.py`）。所以"改 v2"无事可做。

**顺路量出的真选项**（20 个真实 c5-margin shard，裸 22.3 MB；×7.5 折算 150 份）：

| 方案 | 体积 | 相对 deflate | 打包耗时(150 份) |
|---|---|---|---|
| ZIP_DEFLATED(6)（原状） | 241,382 | — | 1.8 s |
| ZIP_LZMA（只改一个参数） | 187,443 | −22.3% | **9.0 s**（慢 5×，净亏，否决） |
| 单流 lzma(3) | 119,280 | −50.6% | 1.1 s |
| **tar.xz(preset=3)（采纳）** | **123,788** | **−48.7%** | **1.9 s**（与现状持平） |

折算真实 payload **3.83 MB → ~1.96 MB，下载 2.3 s → ~1.2 s（−1.1 s/轮）**；
`tarfile`+`lzma` 都是 **stdlib，无新依赖**；打包 CPU 与现状持平（1.9 s vs 1.8 s）。
`ZIP_LZMA` 被否是因为每个条目独立字典（只省 22%）且慢 5×。

**实现（含新旧互通）**：

- `PAYLOAD_NAME = "payload.tar.xz"` + `PAYLOAD_LEGACY_NAMES = ("payload.zip",)` +
  `find_payload(job_dir)`（**优先新名、回退旧名**）；
- 产侧：`protocol.pack_payload` 与 `hub_client.pack_payload_zip` 改写 tar.xz
  （`PAYLOAD_XZ_PRESET: Literal[3]`——typeshed 的 `w:xz` 重载要求 `Literal[0..9]`）；
- 消费侧**双读**：`_extract_archive()` 用 `zipfile.is_zipfile` 判别，zip 与 tar.xz 都能解
  ⇒ 旧 hub 产的 `payload.zip` 对新 worker、新 hub 产的 `payload.tar.xz` 对旧 worker 都能工作；
- 触点：`protocol.py`（容器+find_payload）/ `hub_client.py`（打包+落盘名）/
  `hub_server.py`（存在性检查、落盘、服务三处）/ `worker.py`（落盘名）/ `rl/loop_steps.py`（push 读字节）。

**验证**：tar.xz 往返逐文件一致；**双读**（旧 zip 仍可解）；`find_payload` 两名并存时优先新名；
同素材体积 **−44.2%**（6 个真实 shard）。回归测试 3 项：容器魔数+体积、旧 zip 双读、find_payload 优先级。

### 1.7 待做

按实测收益排序：**`channels_last`**（同步被排除后升为第一优先；已验证 `cat(obs_cl, coords_nchw)` 会把 layout
静默退回 NCHW，不是传个参数就行，且与 TF32 耦合）、**尾块固定 shape**（仅 TPU 有收益）。
~~payload 下行改 v2~~ → 不成立（pull 模式本就是裸二进制），已由 §1.6 的 tar.xz 替代。
**DECISIONS 条目待补**（建议 `§2026-09-10-ppo-perf`）。

---


## §8 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-11-ppo-tpu-step-mark（2026-09-11，c6b-margin 首个 TPU job 单步 45~52s / eta 5.9h 根因定案）

- **背景**：Colab worker PPO 每梯度步 24→45→52s **线性递增**（142 chunks × 4 ep ≈ 7h，hub
  wait_job 1800s 必超时）；探针 E 段真机复现（Colab/Kaggle TPU）：E0/E1/E2（含已 warmup 的
  干净基准）全部 ~10s+/step 递增，`--tail 0` 固定 shape 亦不例外。
- **备选与否决**：ref 预计算执行时机 —— 否，E1≈E0 同速；尾块 shape 编译税 —— 否，
  tail=0 仍慢；多 chunk/perm 结构 —— 否，E2（bench_case warmup 后）亦 10s+；归因设备
  （PJRT 回退）—— 否，E1 + 每步 mark **收敛回 44ms**，正是旧微基准数字。
- **决定**：根因 = torch_xla 惰性模式下 `.tolist()` materialize 只断言依赖子图，backward/
  optimizer 在途节点不 drain、跨步骤累积 → 图线性变大 → 单步耗时 ∝ 步数。修复 =
  `ppo/engine.py::ppo_update` 每步 stats 后 `xla_mark_step(device)`（图执行边界；非 XLA
  no-op，CPU/CUDA 数值逐位不变）+ ref 预计算后一次 mark（防首步巨图）+ worker 日志打印
  device/world_size。**修正旧结论**：`plan/ppo-optimization.plan.md` §0.5「TPU 快 GPU 4.7× /
  44ms」是 ≤3 步微基准测量假象；44ms 只属于「每步有 mark」形态。
- **违反后果**：任何 torch_xla 持续训练循环若每步只有 materialize 而无显式 mark，都会再现
  「单步递增、多步后爆炸」；TPU 吞吐评估必须以 ≥6 步持续循环口径，且读「引擎即有 44ms」前
  必须先确认每步有图边界。后续 TPU 端 ppo_sec 读数均须按此修正解读。

---

### §2026-09-22-demo-mix-bc-aux（2026-09-22，demo 混 batch 接线：BC 辅 loss，不是 kickstart）

**否决的备选**：
- kickstart 锚到人类 BC-ref——否：ref 在 held-out 挂零（§120），KL 朝常量坍缩策略锚定 = 投毒。
- demo 内联进 payload（base64，旧 slim-off 口径）——否：3MB 进 manifest 不可接受；blob 内容寻址
  首轮一传后缓存命中，与 opt/ref 同规（post() 内非 slim 带 bank 直接响亮拒绝）。
- rollout 侧掺 demo——否：BC 梯度必须进 PPO update，采样侧掺只会污染 advantage 血缘。

**落点**（单变量纯度：loss 侧加项，corpus 不动）：
- `ppo/engine.ppo_update` 新三参（`demo_bank/demo_bc_coef/demo_per_mb`，缺省全关、数学逐字节不变）；
  每 minibatch 步 np RNG 抽样（ckpt 精确复现）、合法类掩码 CE 与 `train.bc._masked_ce` 同数学。
- worker 经 manifest 取 blob（`BLOB_DEMO`，缺 bank 而 coef>0 即拒收，mirror kickstart 安全阀）；
  pack（hub_client.post）、训练侧（loop_steps 发布 + `_remote_forward_agg` + iteration 行
  `demo_bc`）、课程 schema（CourseConfig 三键 + flat_overrides）全链打通。
- `corpus_identity_fp` **刻意排除** demo 键（loss 语义 ≠ 「样本是什么」，与 kickstart_init 同待遇；
  course_fp 照常覆盖整文件）；配对腿（demo-mix vs B）共享 `paired_rotate_seed`。
- 污染预登记：demo 种子 ∈ 414xxx 与 414000 段重叠 ⇒ verdict 主段 415000 + 416000 纯回测，
  414000 只作次段（剔除 demo 种子局）；背题指纹 = demo 内外 pass gap >15pp 即降 coef。
- 系数锚：`demo_bc_coef=0.02`（CE~1.0 vs value 项~0.25 ⇒ ~8% 梯度占比起步，按 demo_bc 遥测调）。
- 离线（2026-09-22 追记）：demo 腿支持 cloud rollout＋PPO 离线——bundle 自动带 `demo.npz`
  （OPTIONAL_PARTS，导入即 sha 对账），节点 `open_run_context` 启动期种子 blob_cache
  （缺件启动期响亮拒绝）；push 经 BLOB_NAMES 自动带 blob；pull 零改动。控制台不开离线入口。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §21→§1 · 旧 §25→§2 · 旧 §41→§3 · 旧 §132→§4 · 旧 §133→§5 · 旧 §134→§6 · 旧 §135→§7 · 旧 §131→§8（重号 §131 的第二条）
