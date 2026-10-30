# Review — `plan/notebook-userscript.plan.md`

> **评审日期**：2026-10-29  
> **范围**：完整阅读原计划（815 行）与 `plan/notebook.review-ds.md`，并静态抽查 dashboard 的 `server.ts`、`route-table.ts`、`paths.ts`、日志相关实现。  
> **限制**：未启动 dashboard、未开浏览器、未在 Tampermonkey/Kaggle 实测；因此本文评估的是设计与仓库接线，不把静态检查说成端到端验证。

## 0. 结论

**方向合理，工程上有条件可行，但当前计划还不宜直接按 W0→W6 全量开工。** 原计划正确抓住了两个根约束：Kaggle notebook 在跨域 iframe 内，且日志只能从浏览器当前可见的 DOM 搬运；userscript 与 dashboard 通过窄 HTTP 契约解耦，也比修改训练 cell 或引入云端回连路径更符合低侵入目标。`review-ds` 的主要发现大多已被 v2 吸收。

剩余最大风险不是 TypeScript 或命令选择器，而是：**脚本能否真的注入目标 iframe、日志写入与 meta 的崩溃一致性、隐藏标签的租约/定时语义，以及探测/日志的凭据泄漏。** 用户已明确：运行中的 cell 保存后清空不需要保证并发产生的输出无损，因此该竞态是需如实说明的 best-effort 边界，不再视为阻断项。

| 维度 | 判断 | 说明 |
|---|---|---|
| 需求与方案匹配 | 基本合理 | 浏览器作为本机 dashboard 的传输桥，避免改云端训练代码；Kaggle 优先也正确。 |
| 技术可行性 | 有条件可行 | DOM 采集、GM 请求、双帧桥和 dashboard 虚拟日志页都有可行实现路径；真实站点注入与权限仍未证实。 |
| 数据可靠性 | 设计未闭合 | 当前“meta 原子替换”不能单独保证文件 append、clear、seq 去重在进程中断后的事务一致性。 |
| 交付范围 | 偏大 | 新独立 Bun 项目、dashboard 新 API、桥、UI、自动清空、定时 run/stop 同时推进，验证面远超一个小脚本。 |
| 建议决策 | 先过物理可行性闸，再分批 | 先验证 iframe 注入与 localhost 请求；首个可用版本包含日志与默认启用的阈值自动清空；定时动作最后加入。 |

## 1. 计划中合理且应保留的设计

1. **不改训练代码、不要求云机回连本机**：对第三方 Kaggle notebook 是现实的边界；浏览器扩展的网络权限是方案成立的前提，而不是可省略的实现细节。
2. **Host / frame 分工**：外层会话按钮与内层 JupyterLab DOM 分开处理是正确建模；用带 origin 校验的 `postMessage` 桥传命令，而日志由 frame 直接发本机，减少不必要的数据转发。
3. **把 notebook 日志当日志，不当受管进程**：复用 dashboard 日志视图、但不登记为 `Component`，生命周期判断正确。
4. **失败可见、失败不清空、drain 必须排空**：这些安全语义值得保留。DOM 命令也应继续要求可观测后置条件，避免“点过即成功”。
5. **先支持 Kaggle，不对 Colab/AI Studio 假装可用**：未有真实结构前不加 `@match`、不写猜测选择器，范围控制合理。
6. **happy-dom 只测结构与逻辑**：滚动、可见性、虚拟化无法由无布局 DOM 测试证明；计划把这些条件注入测试是对的。但必须另有明确的真实浏览器验收门。

## 2. 开工前必须处理的阻断项

### P0-1：把 iframe 注入实测移到 W1 之前，而不是仅列在最终人工验收

原计划 §7 已承认“脚本是否进 `notebook-editor-cells` iframe”是唯一未证实的物理前提，且要求人工验收排第一；但 §5 的实施顺序仍是先做 W0，再做完整 dashboard 接收端 W1，之后才到 frame 采集 W2。若 userscript 管理器不在这个跨域子帧执行，W1 的 API、虚拟日志页和多数协议代码都没有实际用户价值。

**建议**：在任何 dashboard 功能实现之前增加一个很小的 **W0a go/no-go**：用目标 Tampermonkey 版本、目标浏览器和与成品相同的 metadata（`@match`、`@grant`、`@connect`、`@run-at`、子帧设置）验证：

- 脚本分别在 Kaggle 外层与 `jupyter-proxy` 子帧启动，且 role 判定唯一、不会把 host 逻辑跑进 frame；
- frame 能读取 `.jp-Notebook`，host 能定位 iframe 并收到握手；
- `GM_xmlhttpRequest` 能对实际的 `127.0.0.1:8900` 发请求；
- iframe reload / URL 改变后能重新 attach。

只要这一步不成立，就先解决注入/权限路径或缩小范围，不要先投入 W1。此项是人工浏览器实测，不能用 mhtml 或 happy-dom 代替。

### 已确认边界（非阻断）：运行中的 cell 保存后清空不保证无损

§3.2 的两轮重读只能缩小“最后一次读取”与点击 clear 之间的窗口，不能把 kernel 输出与 DOM clear 做成原子操作。cell 仍在运行时，最后一次读取之后、clear 命令之前到达的输出可能不会被保存；`MutationObserver` 也不能提供与 Jupyter kernel 的同步屏障。

**用户已明确：运行中的 cell 保存后清空，不需要保证运行期间新产生的输出无损。** 因此按 best-effort 实现即可，不增加 idle 闸或阻断条件；两轮重读可保留为缩小竞态窗口的尽力措施。DoD 与 UI/README 不承诺运行中新增输出无损，但已知 POST/drain 失败仍应中止清空，避免把明确传输失败伪装成成功。

### P0-2：补齐 dashboard 请求体的真实大小上限，不能假定 Bun 自动给 256 KiB 上限

静态检查当前 `dashboard/src/server/server.ts` 的 `Bun.serve()` 未配置 `maxRequestBodySize`。原计划把 256 KiB 写作“Bun 层防御性天花板”，但尚未指定怎样在新路由实施。若直接 `req.json()`，超大 body 可能在解析前占用内存；若简单把 `Bun.serve` 的全局上限设为 256 KiB，又可能影响既有 `/api/deliverUpload` multipart 上传端点。

**建议**：只对 `/api/notebookLog` 做有界读取（例如校验声明长度并在读取流时强制上限，不能只信 `Content-Length`），在 JSON parse 前限制整个请求体；`text` 字段再单独限制 48 KiB，`probe` 字段另行限制。超限返回 413 且不得写盘/续租/标记 console activity。不要改动现有大文件上传端点的体积语义。

### P0-3：把 meta 去重从“原子 JSON”提升为可恢复的写入协议

§4.6 的临时文件 + rename 只保证 **meta 文件自身**替换原子，不能令日志 append 与 meta 更新成为同一事务。典型崩溃窗口：日志文本已 append，meta 尚未更新；客户端重试时 dashboard 重启后看不到该 seq，会把同一块再写一次。clear 同样存在“截断日志已完成、epoch 尚未落盘”的不一致窗口。故当前“dashboard 重启后 seq 去重仍生效”的 DoD 不能由 meta 原子替换单独推出。

**建议**：选一个明确的恢复模型，并让 meta 成为可重建缓存而非唯一事实：例如每个落盘块在同一 append-only 日志中带可识别的 `(epoch, writer, cell, seq)` 记录头，启动/恢复时扫描并恢复已接收游标；clear 用可恢复的 generation/clear marker 定义新代际。至少为“写数据后、写 meta 前中断”和“clear 后、写 meta 前中断”各加故障注入测试。若暂时不实现恢复，就把 DoD 降为明确的 at-least-once，并说明重启可能重复，不能承诺去重。

## 3. 重要风险与建议

### P1-1：30 秒 writer lease 与浏览器后台节流自相矛盾

§4.5 租约是 30 秒，但 §3.1/§3.3 同时承认后台页面定时器可能被节流到约一分钟、系统睡眠后也会延迟。隐藏中的正常写者会先失租；第二标签取得 lease 后以完整 DOM 发 `reset`，会把已落盘历史再写一次。现有 `epoch` 是 clear 代际，不是 writer fencing token，不能表达每次 owner 交接。

**建议**：租约时长应按实测的后台最坏正常调度间隔设置并允许抖动；同时给每次租约授予单调 fencing generation，所有写入校验该 generation。明确 takeover 的数据语义：新 writer 是从持久游标续写，还是允许重复 segment；不能让完整 reset 默默复制数 MiB 历史。分别测试两个标签、一个后台超过 lease、恢复前后交错请求。

### P1-2：日志协议的 append/clear/reset 需定义单会话串行化与故障次序

frame 可串行发自己的请求，但 dashboard 同时可接收两个浏览器标签。计划没有明确 per-session 的串行写锁/事务边界；若 handler 在读 meta、append 文件、更新 meta 之间有 `await`，两个请求可能同时依据同一旧 seq 做决定。即使当前选择同步文件 API，也应把这当成协议保证而不是偶然实现细节。

**建议**：同一 session 的校验、epoch/lease/seq 决策、日志写入、游标提交必须串行；定义操作失败后的恢复语义。用并发测试覆盖同 seq 重放、相邻 seq、两个 writer claim 与 clear/append 同时到达，验证既不丢块也不误确认。

### P1-3：定时任务缺少跨标签“只执行一次”的所有权与任务目标保护

GM 配置是共享的，而两个 Kaggle 标签都可能启动 scheduler。两标签在同一分钟读到同一个 `enabled` job 时，都可能通过各自倒计时并执行 `run-cell` / `stop-session`；日志 writer lease 不保护这类页面副作用。`lastFiredAt` 没有定义何时写入，刷新/崩溃后也可能重复执行。

此外，`target.index` 在 notebook 插入/删除 cell 后可能指向另一段代码；定时运行一个“看起来仍是 index 3”的 cell 有执行错代码的风险。

**建议**：每个 job occurrence（如 job ID + 本地日期/时刻）设置持久 claim/结果状态，动作前先取得唯一执行权；不得仅靠每标签的 `setTimeout` 去重。任务应绑定明确的 session/notebook 身份；cell target 优先用唯一 marker 或 UUID，并可选 source fingerprint，索引只作为用户明确接受的易变定位。多重命中、身份变化或版本变化都 fail closed，不运行。

### P1-4：自动清空是核心需求；焦点闸不能让它长期失效，也不能抢走用户输入

自动清空是用户明确要求且计划已设为默认启用的核心能力，不应降级成仅手动或默认关闭。当前焦点闸（`document.hasFocus()` / 最近输入事件）能降低抢选区风险，但焦点持续留在 notebook 时可能一拍接一拍跳过；后台节流也会延迟清理。因此闸门若没有恢复策略，虽名义上开启自动清空，实际却不能控制长期 DOM 积压。

**建议**：自动清空保留为默认启用的核心功能；先在真实 Kaggle 页面验证按 cell 定向清空是否能保留用户当前选区/焦点。焦点或近期输入时可以短暂延期，但需定义最大延期/重试与可见积压状态，不能无限跳过；有未排空积压或明确传输失败仍必须拒绝清空。明确这是减少已保存输出在 DOM 中滞留的措施，不保证浏览器总内存有硬上限；并按用户已确认的边界，不承诺清空竞态中运行中新产生的输出无损。

### P1-5：采集/哈希的网络预算不限制 DOM 读取成本

计划以全局 1 MiB/拍限制 POST 量，但每次处理 dirty cell 仍要读出完整 `outputText()`，并重新对 `[0, offset)` 做 hash。累计输出很大时，5 秒一次的整段复制和 O(历史长度) 扫描会造成明显 CPU 与临时字符串开销；编辑器输入、折叠等 subtree 变动若也标 dirty，会让扫描频率进一步提高。所谓“其余 cell 零成本”并不覆盖同一个大输出 cell。

**建议**：observer 回调只把 `.jp-OutputArea` 下真正相关的 mutation 映射成 dirty cell，避免 source 编辑器每次键入都触发日志读取；明确单 cell 扫描/临时分配上限与超限的可见降级策略。至少用代表性的大输出（数十 MiB、tqdm `\r` 更新、多个 dirty cell）测量扫描耗时与峰值内存，再定阈值。若每拍仍必须重扫全前缀，应在 DoD 中诚实记录其复杂度与上限。

### P1-6：探测 JSON 本身可能泄露 session JWT，脱敏不能宣称覆盖所有凭据

§2.1 明确 iframe URL 含 `?session=<JWT>`；§4.8 的探测快照又收集 `iframe.src`、`href`、`referrer`，并同时复制到剪贴板和 POST 到 `probe.jsonl`。现有日志脱敏规则覆盖若干 token 形状，但没有明确通用 JWT/query 参数过滤；用户将探测结果贴到 issue 时可能直接泄露凭据。运行期 stdout 的任意秘密也不可能靠有限正则保证全部识别。

**建议**：构造探测数据时就删除 URL 的 query/hash，必要时遮掉 owner、slug、runId；剪贴板与本地 probe 文件都使用同一净化后对象。探测不应默认 POST，或至少在 UI 明示并让用户选择。把脱敏定义为“已知格式的尽力过滤”，不要以“运行期凭据都已脱敏”作硬保证；提供关闭采集/清理日志方式，并用 JWT、`TS_AUTHKEY`、JSON 字符串、跨 chunk token 等测试验证已知规则。

### P1-7：schedule 的时区、迟到、幂等与页面身份仍未定义到可验收程度

有 `HH:MM`、`days`、DST 测试计划，但没有明确 `1,3,5` 的星期编号、时区来源、DST 春季不存在时间/秋季重复时间的行为、同一 occurrence 的最多执行次数、页面恢复后对多条过期任务的次序，也没有说明任务只对创建时的 run 生效还是跟随 SPA 新 run。倒计时在隐藏页不能给用户可见的取消机会，按预授权执行 stop 必须是非常明确的 opt-in。

**建议**：任务创建页明确本地时区与星期编号；每个 occurrence 用唯一键持久记录 `pending → claimed → succeeded/failed/skipped`，定义刷新恢复与迟到时最多执行一次；要求 stop 任务创建时二次确认并显示绑定 session。用户没明确授权的任务不能在后台自动 stop。把可取消倒计时定义为“页面可见才显示”，隐藏页执行必须只由已显式授权的策略决定。

## 4. 范围与实施顺序

当前 W0–W6 加上完整 §3 规格，把基础传输、数据保护、跨帧通信、DOM 操作与 scheduler 一次纳入，测试矩阵也较大。按现在顺序会在物理前提尚未验证前先做接收端。自动清空是核心需求，应与日志保存链路一并交付；适合后置的是跨标签定时 run/stop 等另一类高副作用自动化。

**建议保持全部需求为最终方向，但拆成可独立验收的阶段：**

1. **Gate 0：浏览器实证**——真实 Kaggle iframe 注入、GM localhost POST、页面 reload 重握手；失败即暂停，不先做 W1。
2. **MVP A：日志搬运与默认启用的阈值自动清空**——先做 bounded POST、session 文件、可恢复去重、stderr/CR 处理、dashboard 日志页；自动清空需包含排空/重读/清空链路、焦点延期但不无限延期、积压与传输失败保护。运行中新增输出按 best-effort 说明，不承诺无损。
3. **MVP B：低副作用页面操作**——折叠、滚动、CFG 定位；每个命令保留可观测结果与真实页面验收。
4. **MVP C：定时 run/stop**——在 occurrence 唯一性、cell 身份绑定、后台节流和失败恢复语义明确后再加入；stop 必须额外确认且有独立验收。
5. **其他站点**——接口类型可以先留在抽象层，但不发布空适配器 UI、不加 `@match`；有真实 probe 与夹具后再逐站实现。

这不是删需求，而是把“最核心、低副作用、可先验证的日志价值”与“定时执行/停止等高风险自动化”分开，使风险失败不会拖累基本功能。

## 5. 建议补充到计划的验收条款

- 用真实浏览器执行 Gate 0，并记录浏览器、userscript 管理器版本、子帧设置、权限、实际 iframe origin；此项必须在 W1 前通过。
- `/api/notebookLog` 在 body 上限外立即 413；限制只作用于该端点，不破坏 `/api/deliverUpload`；恶意大 JSON 不会先被完整读入内存。
- 注入故障测试：append 成功但 meta 未提交、clear 已开始但进程退出、同一 session 并发请求；重启恢复后的数据/epoch/seq 结果符合所选语义。
- 后台标签实际节流情况下 writer lease 不因正常暂停频繁迁移；迁移后无静默重复/丢失，面板能说明交接状态。
- 同一个 job 在两个标签同时到点只执行一次；刷新、睡眠恢复、DST 边界均按已写明策略工作；任务 target 变化时不会错跑。
- 大输出基准不仅测 1 MiB payload 是否完整，还测 DOM 扫描耗时、峰值内存、速率高于 1 MiB/拍时的 backlog、drop/clear 行为。
- probe JSON 在复制与落盘前都不含 URL query token；脱敏测试包含敏感值跨 chunk 边界的情形。
- 默认启用的阈值自动清空在超过阈值后能实际触发；焦点/输入保护只延期且不会无限期阻塞，延期与积压可观测；有未排空积压或已知 POST/drain 失败时拒绝清空。
- 自动清空明确为 best-effort：不保证清空竞态中、仍在运行的 cell 新产生的输出无损；但不得把已知 POST/drain 失败伪装成成功。

## 6. 最终判断

**可行，但需先过 Gate 0，并闭合请求体上限与日志恢复一致性。** 自动清空是用户明确需要的核心能力，应默认启用并与日志保存闭环一起交付；焦点保护不能造成无限期跳过，且明确 POST/drain 失败仍需阻止清空。运行中的 cell 在清空竞态里新产生的输出按 best-effort 处理，不承诺无损。其余主要设计方向（双帧、DOM 采集、dashboard 虚拟日志页、Kaggle-only）成立；应与自动清空分开后置的是定时 stop/run 等自动化风险。先过真实浏览器 Gate 0，再交付日志与自动清空，之后逐步加入定时动作，更容易验证，也更符合小步、可回退的工程方式。
