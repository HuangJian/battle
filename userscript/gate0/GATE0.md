# Gate 0 — 浏览器实证（人工闸，10 分钟）

> **这是什么**：`plan/notebook-userscript.plan.md` §5-Gate 0 的可勾选版本。整套方案唯一**没被存档证实**的
> 物理前提是「Tampermonkey 的脚本真的跑进了 Kaggle 的跨域子帧」。这一步只验物理前提，不搬日志、不画面板。
> **物料**：`userscript/gate0/probe.user.js`（探针）。探针只做三件事，每件打一行 `[gate0]` 日志：
> ① 角色/注入 · ② attach→ready 握手 · ③ 回环 GET。
> **安全**：探针打印的 URL 只到 `origin + pathname`。`?session=` 是 Kaggle 会话 JWT（含邮箱/uid），
> 第一版探针曾把它原样打进 Console —— 如果你手工复制地址栏，**先删掉 query** 再贴。
> **时间**：前置 1 min + 安装 1 min + 五条判据各 1–2 min + 判定 1 min。
> **通过后**：才允许开 W1（dashboard 接收端）；**任何一条失败 ⇒ 停**，先按 §末尾的失败处置定退化路径。

---

## 0. 前置（1 min）

- [ ] dashboard 起着：仓库根 `bun run dashboard`（:8900）。
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8900/api/state` 打印 `200`
      （不是 200 就先修控制台，别把「dashboard 没起」记成「回环失败」）。
- [ ] Tampermonkey 已装，且没有全局关掉「在 iframe 中运行用户脚本」；探针本身**没有** `@noframes`。
- [ ] 记下版本（填末尾记录表）：Chrome `chrome://version`、Tampermonkey 扩展详情页。

## 1. 安装探针（1 min）

- [ ] Tampermonkey → 新建脚本 → 整份粘贴 `userscript/gate0/probe.user.js` → 保存（Ctrl+S）。
- [ ] 脚本列表里能看到两条 `@match`：`https://www.kaggle.com/code/*` 与
      `https://*.jupyter-proxy.kaggle.net/*`（少一条就重装）。

## 2. 判据 ① 注入 + 角色唯一（2 min）

打开 `https://www.kaggle.com/code/<owner>/<slug>/edit/run/<id>`，F12 → Console，过滤 `[gate0]`。

- [ ] 至少出现**两行**摘要：`role=host notebook=false …` 与 `role=frame notebook=true …`（各一行）。
      frame 那行可能要等 1–5 s（JupyterLab 渲染完才打；探针最多等 30 s 再下结论）。
- [ ] `notebook` 没有反：host 读到 `.jp-Notebook` ⇒ 说明选择器/角色判据错了。
- [ ] frame 摘要只出现**一次**（两次 = 脚本被注入两份，或站点把 iframe 建了两回）。
- [ ] 看不到 frame 行？把 Console 左上角的 context 从 `top` 切到 `jupyter-proxy.kaggle.net` 再看；
      仍然没有 ⇒ **注入失败**（这是 Gate 0 的否决项，别再往下做功能）。

## 3. 判据 ② 握手（2 min）

- [ ] host：先出现 `host: iframe 还没出现（首次）——继续轮询` 是**正常**的（v0.1 就死在这一步），
      1 s 内应接着出现 `host: attach → https://<…>.jupyter-proxy.kaggle.net nonce=<N>（iframe 出现/更换）`。
- [ ] frame：`frame: attach ← nonce=<N>` 紧跟 `frame: ready → nonce=<N>`（**同一个 N**）。
- [ ] host：`host: ready ← nonce=<N> notebook=…`，且 5 s 内到（出现 `ready 超时 5s` = 失败）。
- [ ] 握手时 `notebook=false` 不一定是失败：`.jp-Notebook` 渲染晚于 attach。等 frame 打
      `frame: status → nonce=<N> notebook=true`、host 打 `host: status ← … notebook=true`，那才是正式证据。
- [ ] 若 host 打 `ready 但 nonce 不符`：frame 回了上一轮/别的标签的握手 ⇒ 记录下来（协议要按 nonce fence）。
- [ ] 拒绝路径也是证据：frame 打 `拒绝 origin=…` / `拒绝非 parent 的 attach` 说明 origin/source 校验在工作。

## 4. 判据 ③ 回环（1 min）

- [ ] host：`回环: GET /api/state → 200`。
- [ ] frame：同样一行 `→ 200`（两个角色都打；只有 host 200 说明 frame 出站被挡，W2 会瘸）。
- [ ] 非 200 / 网络错误：按顺序查 ① dashboard 是否在 8900；② `@connect 127.0.0.1` 是否被改；
      ③ 系统/公司代理是否把回环也劫持（决策 §2026-09-18-goalnn-loopback-http-no-proxy）。

## 5. 判据 ④⑤ 重挂（2 min）

- [ ] **iframe reload**：在 frame 的 Console 执行 `location.reload()`（或 DevTools 对 iframe 右键 Reload frame）
      ⇒ host 重新打 `attach（iframe load）` + **新 nonce** + 新 `ready ←`。
- [ ] **SPA 换 run**：站内点到另一个 run（地址栏变 `/edit/run/<另一 id>`，页面不整刷）
      ⇒ host 先打 `host: URL 变化（SPA）→ …`，再打 `attach（SPA 换 URL）` + 新 `ready ←`。
- [ ] 两个标签同时开同一个 notebook（可选，30 s）：两边 nonce 各自成套、互不串 ready。

## 5.1 日志急救表（看到这行就按右栏做）

| 日志 | 含义 / 下一步 |
|---|---|
| `host: iframe 还没出现（首次）——继续轮询` | 正常。iframe 晚于 DOMContentLoaded；等 1 s 看有没有 attach。 |
| `host: 60s 内没找到 iframe` | 页面结构变了（选择器失效）⇒ Gate 0 失败，先改探针选择器再测。 |
| `host: ready 超时 5s` | frame 没跑脚本，或 `@match`/子帧权限被挡 ⇒ 回到 §2 确认 frame 摘要行。 |
| 只有 host 行，没有 `role=frame` | Tampermonkey 没注入子帧（站点权限 / Chrome「允许用户脚本」）⇒ Gate 0 失败。 |
| `frame: notebook=false` 持续 30 s | 选择器不对（不是时序问题）⇒ 记录后改探针。 |
| `host: ready 但 nonce 不符` | frame 回了别的握手（旧标签/旧帧）⇒ 记录下来，协议必须按 nonce 隔离。 |
| `回环: … 网络错误` | dashboard 没起、端口不是 8900、或代理劫持回环。 |

## 6. 判定（1 min）

- [ ] 判据 ①–④ 勾上 ⇒ **Gate 0 通过**，可以开 W1。
      （2026-10-30 判定：⑤ 是自研的 1 s URL 轮询，不是物理前提 ⇒ 转 W3 集成验收；①–③ 已实测通过。）
- [ ] 任一失败 ⇒ **不宣称端到端可用**；按 plan §5-Gate 0 的失败处置：停在这里先定退化路径
      （例如用户手动在 frame 控制台粘贴同一份脚本仍走同一协议，但不进 v1 交付）。
      已写好的纯函数与单测可以留（与注入无关、失败也不浪费）。

## 7. 记录表（把证据留在提交/备忘里）

| 字段 | 值 |
|---|---|
| 日期 | 2026-10-30 |
| Chrome 版本 | （待补） |
| Tampermonkey 版本 | （待补） |
| Windows / 扩展组合备注 | 探针 v0.2；host 轮询到 iframe 后握手成功 |
| ① 注入 + 角色唯一 | ✅ host `notebook=false` / frame `notebook=true`，各一行 |
| ② attach → ready（nonce 一致） | ✅ nonce `mvu9kzyw-xrnk` 贯通 attach/ready/status；`status ← notebook=true` |
| ③ 回环 `GET /api/state` 200（host / frame） | ✅ / ✅ |
| ④ iframe reload 重挂 | ✅ 同一 `load` 监听器已实测触发（`attach（iframe load）`）；独立 reload 未单跑 |
| ⑤ SPA 换 run 重挂 | ⏳ 待测 → 转 W3 集成验收（自研 1 s 轮询，不是物理前提，不阻塞 W1） |
| 结论（通过 / 退化路径） | 物理前提通过（注入 / 握手 / 回环）⇒ W1 放行 |

> **2026-10-30 实测（探针 v0.2）**：host 先报 `iframe 还没出现——继续轮询`，1 s 内 attach 到 iframe；
> frame 回 `ready`，`.jp-Notebook` 渲染后补 `status`（同一 nonce）。host 与 frame 的回环均 `200`，
> 日志里的 URL 已无 `?session=`（`safeHref()` 生效）。转发日志前仍请手工确认没有 query。

> 证据留法：Console 里右键 **Save as…**（或选中 `[gate0]` 行复制），把关键几行贴进上表下方。
> 日志里的 URL 若含 `?session=`，贴之前删掉 query（探针不打印 token，但浏览器地址栏会带）。

---

## 附：与 plan 的对应关系

| plan §5-Gate 0 判据 | 本清单 | 探针日志 |
|---|---|---|
| 注入：内层帧真的跑了脚本 | §2 | `role=frame …` |
| 角色唯一：host 逻辑不进 frame | §2 | `role=` 摘要各一行 |
| 握手：host 定向 attach，frame 回 ready | §3 | `attach →` / `ready ←` |
| 回环：`GM_xmlhttpRequest` GET `:8900/api/state` | §4 | `回环: … → 200` |
| 重挂：iframe reload / SPA 换 run 后重新 attach | §5 | 第二次 `attach（iframe load / SPA 换 URL）` |
