# 决策正文归档 — repo-governance

> 2026-09-23 从 `DECISIONS.md` 搬出的决策正文（索引行与编号仍在 `DECISIONS.md`）。

## §2026-09-08-decisions-governance（2026-09-08，用户拍板执行 plan/decisions-governance.md）

- **背景**：DECISIONS.md 膨胀至 3734 行、编号至 §384，并行分支撞号（§293/§354/§355/§361 各 2 条同号）、
  历次瘦身必输（写入成本 0、清理成本 100，三 agent 并行每月 +30 条）。实测定案为准入问题，非整理问题。
- **备选与否决**：更勤瘦身 —— 否，写入门槛为零时任何频率瘦身都是负收益；换编号格式（全局计数器加锁）—— 否，
  顺序整数是全局共享可变计数器，并发必然撞号；ADR 分片（一决策一文件，P4）—— 暂缓，瓶颈在准入而非文件组织，
  收紧准入后写入量掉 80%，单文件 append 够用。
- **决定**：收紧准入（三问闸门 + 三类路由，禁双写）+ 防冲突命名（日期 ID `§YYYY-MM-DD-<branch>-<slug>`，
  旧编号 §1–§384 冻结契约不动）+ union 合并（`.gitattributes`）+ 只追加（新条目只写末尾）+
  模板外置（`docs/decisions/HOW-TO-ADD.md`）+ 自动校验（`tools/check-decisions.ts` 接进 `bun run check`，
  基线 `tools/decisions-baseline.json`）+ 一次性瘦身（编号集合不变式：只删正文不删编号；
  实验/调优 → progress 指针，bugfix/UI → commit 承载）。本次迁移 3734 → 约 980 行，编号集合逐条核对未变。
- **违反后果**：撞号与膨胀回归（目标 ≤5 条/月 vs 现状 ~30）、合并冲突复现、外部引用断链。
- **留存**：旧全文在 ccf49ff^（瘦身前全文版，git 历史为准，不另存本机备份）。


---

## §2026-09-15-sandbox-precommit-immunity（2026-09-15，用户指令：杜绝编码 agent 删除保护沙箱反复拦截 pre-commit / 反复弹删除审批；hook 进程树级免疫 + 零删除纪律）

- **背景**：WorkBuddy / Mimo 类编码 agent 自带 python 运行时 + 删除保护沙箱，经两个载体注入：
  **bash 函数**（BASH_ENV 指向 safe-delete-bash-env.sh → 每个**子** bash 启动时把 rm/unlink/rmdir
  重新包装成函数，带每回合删除配额 ~50）与 **python sitecustomize**（守卫 os.remove/shutil.rmtree）。
  后果：mypy 自清理 / pytest 临时目录被塞 SystemExit → INTERNAL ERROR 假红；hook EXIT trap 的
  rm 撞配额 FAIL_CLOSED → 门禁全绿提交却被 git 静默丢弃（2026-09-14 实测 count=181/182；
  2026-09-15 Mimo 再犯：反复重试 commit + 反复弹删除审批）。旧防御只在本 shell `unset -f rm`——
  子进程经 BASH_ENV 重新注入后照拦不误；python 侧开关只写进注释让「调用方设」= 等于不设。
- **决定（三管齐下，只对本 hook 子树生效，不碰宿主环境）**：检测到注入载体（名字含
  CODEBUDDY/SAFE_/BUDDY/SANDBOX 的环境变量，或 BASH_ENV/ENV 含 safe/delete/buddy/runtime）即拉闸：
  ① BASH_ENV/ENV 指向 /dev/null + unset 全部注入变量 → 子 bash 拿不到包装函数与配额配置，
  删除走真 `rm`（配额彻底失效）；② CODEBUDDY_SAFE_DELETE_ENABLED=0 + PYTHONNOUSERSITE=1 →
  python 侧 sitecustomize 守卫对整棵子树（mypy/pytest/ruff 及其自清理）整体关闭；
  ③ PATH 滤掉命中 safe-delete/safe-bin/codebuddy/shell-runtime 的目录 → 直接可执行 shim 也无处可寻。
  **铁律追加（hook 零删除纪律）**：hook 自带临时文件清理一律「截断不删除」（`: > file`），
  删除 syscall 归零——截断是写不是删，任何删除拦截都对其失效；残留 0 字节文件在 gitignored
  tmp/ 由 periodic 清理兜底。权威文档 = `tools/githook/pre-commit` 顶部注释。
- **被否决的备选**：① v1「本 shell unset -f」—— 已被实测证伪（子进程重注入）；②「exec 重执行 +
  env -u 剥离」—— 引号/递归复杂度不值，BASH_ENV=/dev/null 更简单且等效；③「让调用方自己设
  CODEBUDDY_SAFE_DELETE_ENABLED=0」—— 调用方正是肇事 agent，指望它们设等于不设。
- **违反后果**：hook 再出现删除 syscall（trap/selftest 里的 rm 等）或依赖 agent 自设开关 ⇒
  沙箱弹审批/配额重演、agent 反复重试 commit（本次要消灭的循环）；child 进程再被 BASH_ENV
  注入 ⇒ mypy/pytest 假红回归。
- **验证**：hook `--selftest` 绿；伪造 BASH_ENV 注入文件（真定义 rm() 包装函数并 export -f）+
  CODEBUDDY 标记变量跑 selftest → 拉闸行出现且三层子进程（self/child/grandchild）rm 均为真身、
  载体变量已清、真实删除可用（无配额/无审批）；根 `bun run check` **1825 pass / 3 skip / 0 fail**。
- **追加（2026-09-15，用户提问「coding agent 直接跑 python.exe -m pytest xxx 也会被拦截吗」
  ——**会**）——把 python 入口也一起处理**：删除拦截发生在 **python 进程内部**
  （os.remove / shutil.rmtree / pathlib.unlink 层，由启动时加载的 sitecustomize 守卫实现），
  bash 的 rm() 包装覆盖不到——`python -m pytest` 的 tmp_path 清理、测试内删除一删一个准
  （本机实测：两个解释器 site-packages 都**没有**烘焙 sitecustomize，注入是**环境变量驱动**的，
  沙箱 env 随进程树带下来才激活，所以整树净化可治）。免疫做成三件套：① 共享实现
  `tools/githook/_sandbox-sanitize.sh`（**唯一事实源**，pre-commit 改为 source 它，禁止复制粘贴漂移；
  幂等 SANDBOX_SANITIZED 守卫）；② 新增 python 启动器 `tools/githook/nn-py-safe.sh` ——
  **先拉闸、再 exec 指定解释器**（默认 nn-training/.venv，NN_PY 可覆盖），语义与裸
  `python.exe …` 逐字节一致，agent 跑 pytest/任意 python 一律经它；
  ③ `CODEBUDDY_SAFE_DELETE_ENABLED=0` + `PYTHONNOUSERSITE=1` 改**无条件设**（惰性变量，
  普通机器无害）——不再依赖「调用方自觉设」。**验证口径**：bash 层三层免疫在注入模拟下
  逐层为真（self/child/grandchild rm 均为真身、变量已清）；python 内部 env 在本验证宿主
  （bash→Windows exe 边界剥环境）无法直视，靠「沙箱 env 能传进去就能传开关出来」+ 既有
  `CODEBUDDY_SAFE_DELETE_ENABLED` 文档语义成立。**违反后果**：agent 再裸跑 `python -m pytest`
  ⇒ pytest 假红/弹审批重演；有人在别处复制粘贴拉闸逻辑 ⇒ 二处漂移，任一失效用户都得重推一次。


---


---

## 第五轮归档（2026-11-11）

> 2026-11-11 第五轮瘦身：`DECISIONS.md` 中仍带成篇正文、正文 >6 非空行的 **1** 条决策全文按领域落到本节（索引行与编号仍在 `DECISIONS.md`）。锚点 = `## §<id>`。

## §2026-10-20-eol-guard-commit-time-lf-normalization — 「commit 时把文件改为 LF」= 把**工作树**拉回索引字节（不是让 git 提交时归一）

- **用户指令**（2026-10-20）：配置本机 commit 时自动把文件改为 LF，问是否能避免 mac 节点门事故。
- **先纠正前提**（实测）：**git 的提交时归一早就在做，且早就是对的**——`.gitattributes` 的
  `* text=auto eol=lf` ＋ 本机 `core.autocrlf=input` ⇒ 索引里永远是 LF（事故当天
  `src/game/Input.ts` 的索引 blob 就是 13641 字节 LF，而工作区是 14027 字节 CRLF）。
  实测 `eol=lf` **压过** `core.autocrlf=true`（临时仓库实验：checkout 写出 CR=0）⇒ git 本身
  不是污染源。**坏的是工作树**，而 codeHash 算的正是**工作区原始字节** ⇒ 「提交时归一」治不了
  它；能治它的是「**把工作树拉回索引字节**」。
- **决定**：① **本机 user 级 git 配置**：`core.autocrlf=input`（已有）+ `core.eol=lf`（新）+
  `core.safecrlf=warn`（新）——`safecrlf` 实测在 `git add` 时喊「CRLF will be replaced by LF」，
  是**早信号**；不用 `=true`（实测 `git add` 直接 fatal，会把提交卡死在看不懂的报错上）。
  ② **pre-commit 钩子新增一步「eol 归一」**（`tools/githook/normalize-lf.ts`）：commit 时把
  **codehash 集 + staged 文件**里**纯行尾污染**的工作树文件重写为**索引字节**（LF）。
  ③ 判据锁死（`classifyDrift`）：工作区字节按 CRLF→LF 归一后必须**逐字节等于索引 blob**
  ⇒ 只动行尾、不动内容 ⇒ 他人的在途编辑（真实内容差异）**一字不碰**、只报告。
  ④ 二进制（集内 `prebuilt/**` 的 .wasm/.dll/.dylib/.so）永不参与。
- **与钩子铁律的关系**：钩子原有铁律是「除 oxfmt 精确格式化外对工作树**零写操作**」——本次
  新增的写入面被**前置条件**（归一后 == 索引 blob）锁死，与 oxfmt 那条例外同性质（只动自己
  确认安全的内容），已在钩子头注释里显式改写铁律并说明理由。
- **为什么这能覆盖事故**：事故当天 09:42 污染、11:16 有一次提交（ddbda71）⇒ 有这道闸，
  集内文件会在那次提交时被拉回 LF，**mac 不会被判 stale**。闸放在 dirty 清单**之前**：
  归一后「仅因行尾而不一致」的文件不再算未暂存改动。
- **备选与否决**：① 只设 `core.autocrlf` / `core.safecrlf` 就收工 —— 否（索引早就是 LF，
  坏的是工作树，配置层动不到它）；② 提交时把**全部**跟踪文件重写成 LF —— 否（与 oxfmt 同样
  的风险面：会吃掉未暂存的真实编辑；本条只修「归一后 == 索引」的那一类）；
  ③ 在**训练侧**（dispatch）自动修复集内污染 —— 未实施（训练链路的写工作树行为变更，另议；
  运行时的 `dirty_hash_files()` WARN 仍在，且已被本条的前置闸大幅缩短窗口）。
- **落点**：`tools/githook/normalize-lf.ts`（新）· `tools/githook/pre-commit`（新增一步 ＋
  铁律改写 ＋ selftest 接线）· `tests/normalize-lf.test.ts`（新，9 例）· 本机 user 级 git 配置。
- **回归**：① 归一器 `--selftest`（分类三分支 ＋ 端到端修复 ＋「真实改动不动」）；
  ② 钩子 `--selftest`（含归一器自检）绿；③ `tests/normalize-lf.test.ts` 9 例（分类 ＋ 修复
  ＋ 覆盖面接线 ＋「钩子调它且在 dirty 清单之前」）；④ **事故级端到端**：把集内文件
  `src/utils/RNG.ts` 写成 CRLF ⇒ `dirty_hash_files` 报它、期望 codeHash 由 `e64efa38…` 变
  `f0f774df…` ⇒ 归一后工作树回到索引字节、dirty 清空、期望值复原 `e64efa38…`；真实内容差异
  用例确认一字未动。
- **门禁**：`bun run check` 绿（tsc ＋ check-decisions ＋ agents-budget ＋ 根套件）；
  `tools/githook/pre-commit --selftest` 绿。

---

## §2026-11-11-decisions-slim-round5（2026-11-11，用户指令：给 DECISIONS.md 瘦身 —— 第五轮：正文下沉到 details/）

- **背景**：第四轮后（4 479 行）一个月新增 408 条，文件回到 11 839 行 / 1.2MB；其中 256 条仍带成篇正文（>6 非空行、无 `—— 全文` 指针），合计 5 698 行。
- **备选与否决**：① 再按 >12 行切 —— 否（第四轮已证明阈值过高，正文继续留在索引里）；② 逐条搬进 7 个主题档 —— 否（这些条目跨 NN 工程 / 远端 / 控制台，逐条分档成本高，且 `details/<领域>` 已是 2026-11-08 起的既有落点）；③ 删除正文 —— 否（编号与缘由是外部引用契约，只搬不删）。
- **决定**：256 条全文按领域搬进 `docs/decisions/details/{goalnn,godai,repo-governance}.md`（锚 `## §<id>`），索引行压成「标题 + 原文节录（≤6 行）+ 指针」；编号集合不变（`bun tools/check-decisions.ts` 绿）。
- **违反后果**：文件按每月 ~+7K 行膨胀，注入截断与检索成本回归。

---

## 第六轮归档（2026-11-11）

> 2026-11-11 第六轮瘦身：索引节录压缩到 ≤2 行；全文位置不变。本节只归档本轮记录条正文。

## §2026-11-11-decisions-slim-round6（2026-11-11，用户指令：索引节录压到 1–2 行）

- **背景**：第五轮后 `DECISIONS.md` 仍有 647 条带指针的索引条目，其中 629 条节录 >2 行（合计 3 989 行），文件 8 067 行 / 754KB；全文其实已在 `docs/decisions/details/*` 或主题档。
- **备选与否决**：① 只压第五轮搬走的 256 条 —— 否（其余 373 条同形，留下长短不一）；② 删除节录只留标题 + 指针 —— 否（标题之外没有决策内容，grep 时无法快速判断是否相关）；③ 压到 1 行 —— 否（`- **决定**：` 标签单独占一行时一行放不下有效内容，2 行是「标签 + 内容」的最小完整形态）。
- **决定**：629 条已带 `—— 全文` 指针的索引节录压到 ≤2 行（优先保留 `**决定**` 起始行；截断加 `……`）；全文位置与编号不变，`bun tools/check-decisions.ts` 绿。
- **违反后果**：文件重新向 8K/1MB 膨胀，注入截断与检索成本回归。

---

## §2026-11-12-engine-bench-shape-drift（2026-11-12，采样节点启动日志 `BENCH-ARM unknown` 排查）

- **背景**：`bun ./tools/agent/sampler-agent.ts --port 8443 --workers 6` 启动日志出现
  `engine=bun — 微基准缓存（winner=bun，bun 0.00ms[unknown] / node 0.00ms[unknown]）`；
  `tmp/dist-agent/node-bundle/engine-choice.json` 实存
  `bunMs=0.002 / nodeMs=0 / bunArm=nodeArm=unknown`。根因链：v7（2026-11-01）把 stem
  输入通道 18→19（`src/nn/conv/conv_native.h:21` 的 `CF_IN_CH`；两个 adapter 的形状守卫
  同步到 19），**只有 `tools/agent/engine-bench.ts` 的 dummy 模型仍写 `IN_CH = 18`**
  ⇒ `runStudentFeatures` 的两个后端都在形状守卫处 `return false`
  （`conv_wasm_adapter.ts` 的 `m.in16.length !== IN_CH * SP`、`conv_native_adapter.ts`
  的 `shapeOk`）⇒ 计时的是空转函数、`featuresEngine()` 保持 `unknown` ⇒
  输出 `BENCH 0.00x` + `BENCH-ARM unknown`。`chooseByBench` 对 `nodeMs<=0` 的兜底
  恰好把这个假结果判成 bun，`choiceValid` 只比版本 / wasm sha / native sha，
  版本与资产都没变 ⇒ 坏缓存每次启动复用、日志永远 unknown。这是 2026-09-21
  「改为调生产入口」评审只修一半留下的复发口（入口统一了，模型形状仍自绘）。
- **备选与否决**：① 只把 18 改 19 —— 否（第二次漂移只是时间问题，已漂过一次）；
  ② 在 `choiceValid` 里按绝对耗时阈值判空转 —— 否（跨平台量级不同，会误杀）；
  ③ 只靠 `bunArm !== 'unknown'` —— 否（还有打包产物陈旧这个独立坑：
  `engine-bench.ts` 不在 `tools/agent/codehash-files.txt` 白名单，`runEngineBench`
  原先只在 mjs 不存在时才打包，改 TS 源不会重建，node 臂会继续跑旧形状）。
- **决定**：① 新建 `src/nn/conv/conv-shape.ts` 作为 TS 侧形状**唯一来源**，两个 adapter
  与 `engine-bench.ts` 共用；`tests/agent/engine-bench.test.ts` 机械断言
  `IN_CH === conv_native.h::CF_IN_CH`（跨语言契约）。② `engine-bench.ts` 自检：
  非 `native|wasm` 或耗时非正 ⇒ **非零退出**，绝不打印 BENCH 数字。
  ③ `rollout-runner.ts` 加 `benchResultValid()`（真跑结果必须进了加速后端且 ms>0）、
  `choiceValid` 拒绝 `unknown` / 非正耗时的旧缓存并新增 `benchSha`（基准实现指纹，
  改实现即重测）、`createRolloutRunner` 对注入结果同样校验（坏结果不落盘）、
  `engine-bench.mjs` 按 TS 源指纹 stamp 重打包。④ `sampler-agent.ts` 启动日志把
  `rolloutEngine=bun (v22.23.2)` 改成显式 `node v22.23.2`（括号里原本就是 node
  版本，看着像 bun 版本）。
- **证据**：修复前两侧均 `BENCH 0.003 / BENCH-ARM unknown`；修复后 bun 裸跑
  `BENCH 2.921 / BENCH-ARM native`。带坏缓存启动的端到端复现：
  `微基准：bun 3.05ms[native] vs node 5.43ms[wasm] → bun（node 未快过 3% 迟滞）`，
  新缓存写回合法值且带 `benchSha`。结论 winner 仍是 bun，但这次是**有依据**的
  （node 慢 78%），不是 `nodeMs=0` 兜底碰巧撞对。
- **回归**：`tests/agent/engine-bench.test.ts`（端到端 spawn 断言 arm/ms + C 头对齐）、
  `tests/agent/rollout-runner.test.ts`（坏缓存、`benchResultValid`、`benchSha`、
  无效注入不落盘）；`bun run check` 绿。
- **违反后果**：形状再漂时引擎选择会静默退化成「恒选 bun 且日志显示 unknown」，
  在 V8 更快的 x64 Linux/Windows 节点上白丢 14–30% 吞吐，且因缓存命中永不自愈。
