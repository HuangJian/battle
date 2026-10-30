// ==UserScript==
// @name         Battle City Notebook Companion — Gate 0 probe
// @namespace    battle-city-web
// @version      0.2.0
// @description  Gate 0 人工闸探针：注入 / 角色唯一 / attach-ready 握手 / 回环 / 重挂（日志前缀 [gate0]）
// @match        https://www.kaggle.com/code/*
// @match        https://*.jupyter-proxy.kaggle.net/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @run-at       document-start
// ==/UserScript==

/**
 * 三行探针 = 三行日志（每行一条 Gate 0 判据）：
 *   ① `[gate0] role=... notebook=...`        —— 注入 + 角色唯一（host 应 false，frame 应 true）
 *   ② `[gate0] host: ready ← ...`            —— 握手（frame 侧对应 `frame: ready →`，nonce 必须一致）
 *   ③ `[gate0] 回环: GET /api/state → 200`   —— GM_xmlhttpRequest 打到本机 dashboard
 * 重挂不额外要一行：iframe load / SPA 换 URL 都会再走一次 ②，数第二遍即可。
 *
 * 两个时序坑（2026-10-30 实测，v0.1 就是死在这里）：
 *   · host 的 iframe 在 DOMContentLoaded 时还不存在 ⇒ 必须**轮询**，不能只试一次；
 *   · JupyterLab 的 `.jp-Notebook` 比 DOMContentLoaded 晚渲染 ⇒ frame 必须**等它出现**再打摘要，
 *     握手时 notebook 还是 false 也正常，稍后补一条 `status` 才是正式证据。
 *
 * 安全：日志里的 URL 只到 origin + pathname。`?session=` 是 Kaggle 会话 JWT（含邮箱/uid），
 * 第一版把它原样打进了 Console —— 这里用 safeHref() 一律剥掉 query/hash。
 * 判据与处置见同目录 GATE0.md。
 */
;(() => {
  'use strict'

  const HOST_ORIGINS = ['https://www.kaggle.com', 'https://kaggle.com']
  const FRAME_HOST_RE = /(^|\.)jupyter-proxy\.kaggle\.net$/
  const STATE_URL = 'http://127.0.0.1:8900/api/state'
  const GATEWAY_RE = /^https:\/\/[a-z0-9-]+(\.[a-z0-9-]+)*\.jupyter-proxy\.kaggle\.net$/
  const NOTEBOOK_SEL = '.jp-Notebook'
  const FRAME_NAME_SEL = 'iframe[name="notebook-editor-cells"]'
  const FRAME_SRC_SEL = 'iframe[src*="jupyter-proxy.kaggle.net"]'

  const isFrame = FRAME_HOST_RE.test(location.hostname)
  const isHost =
    /(^|\.)kaggle\.com$/.test(location.hostname) && location.pathname.startsWith('/code/')
  if (!isFrame && !isHost) return

  const log = (msg) => console.log(`[gate0] ${msg}`)

  /** 只打 origin + pathname：`?session=` JWT 绝不许进 Console。 */
  const safeHref = () => `${location.origin}${location.pathname}`

  /** 先直查，再穿 open shadow root（节点在不在影子树都不影响判据）。 */
  const deepQuery = (selector, root = document) => {
    const direct = root.querySelector(selector)
    if (direct) return direct
    const stack = [root]
    while (stack.length > 0) {
      const scope = stack.pop()
      for (const el of scope.querySelectorAll('*')) {
        if (!el.shadowRoot) continue
        const hit = el.shadowRoot.querySelector(selector)
        if (hit) return hit
        stack.push(el.shadowRoot)
      }
    }
    return null
  }

  const hasNotebook = () => Boolean(deepQuery(NOTEBOOK_SEL))

  // ── ③ 回环：两个角色都打一次（frame 也必须能出站，不只 host）─────────────
  if (typeof GM_xmlhttpRequest !== 'function') {
    log('回环: GM_xmlhttpRequest 缺失（@grant 没生效 / 管理器设置）')
  } else {
    log(`回环: GET ${STATE_URL}`)
    GM_xmlhttpRequest({
      method: 'GET',
      url: STATE_URL,
      timeout: 5000,
      onload: (r) => log(`回环: GET /api/state → ${r.status}`),
      onerror: () => log('回环: GET /api/state → 网络错误（dashboard 没起 / @connect 缺失）'),
      ontimeout: () => log('回环: GET /api/state → 超时 5s'),
    })
  }

  // ── ② frame 侧：只认 parent 的 attach，回 ready；notebook 晚到时补 status ──
  let readyOrigin = ''
  let readyNonce = ''

  if (isFrame) {
    window.addEventListener('message', (e) => {
      const d = e.data
      if (!d || d.__gate0 !== 'attach') return
      if (window.parent === window) return log('frame: 顶层打开（不在 iframe 里）——不算注入证据')
      if (e.source !== window.parent) return log('frame: 拒绝非 parent 的 attach')
      if (!HOST_ORIGINS.includes(e.origin)) return log(`frame: 拒绝 origin=${e.origin}`)
      readyOrigin = e.origin
      readyNonce = d.nonce
      log(`frame: attach ← nonce=${d.nonce}`)
      window.parent.postMessage(
        { __gate0: 'ready', nonce: d.nonce, href: safeHref(), notebook: hasNotebook() },
        e.origin,
      )
      log(`frame: ready → nonce=${d.nonce} notebook=${hasNotebook()}`)
    })
  }

  // ── ① 注入 + 角色唯一：host 立刻打；frame 等 `.jp-Notebook` 出现再打 ──────
  const logSummary = () => {
    const ok = hasNotebook()
    log(`role=${isFrame ? 'frame' : 'host'} notebook=${ok} href=${safeHref()}`)
    if (isFrame && ok && readyNonce) {
      window.parent.postMessage(
        { __gate0: 'status', nonce: readyNonce, notebook: true, href: safeHref() },
        readyOrigin,
      )
      log(`frame: status → nonce=${readyNonce} notebook=true`)
    }
    if (isHost) wireHost()
  }

  const summarizeWhenReady = () => {
    if (hasNotebook()) return logSummary()
    const started = Date.now()
    const timer = setInterval(() => {
      if (hasNotebook()) {
        clearInterval(timer)
        logSummary()
      } else if (Date.now() - started > 30000) {
        clearInterval(timer)
        logSummary() // 30s 还 false 是**真信号**（选择器变了），不能永远等
      }
    }, 250)
  }

  // ── host 侧：轮询 iframe / attach / ready / iframe load / SPA 换 URL ─────
  function wireHost() {
    let nonce = ''
    let timer = 0
    let boundFrame = null
    let warned = false
    const started = Date.now()

    const findFrame = () => deepQuery(FRAME_NAME_SEL) || deepQuery(FRAME_SRC_SEL)

    const attach = (reason, loud) => {
      const frame = findFrame()
      if (!frame) {
        if (loud) log(`host: iframe 还没出现（${reason}）——继续轮询`)
        return false
      }
      if (frame !== boundFrame) {
        boundFrame = frame
        frame.addEventListener('load', () => attach('iframe load', true))
      }
      const target = frame.contentWindow
      let origin = ''
      try {
        origin = new URL(frame.src, location.href).origin
      } catch {
        /* src 缺失/非法 —— 下面按不可用拒绝 */
      }
      if (!target || !GATEWAY_RE.test(origin)) {
        if (loud) log(`host: iframe 不可用（${reason}）src=${frame.src}`)
        return false
      }
      nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
      clearTimeout(timer)
      log(`host: attach → ${origin} nonce=${nonce}（${reason}）`)
      target.postMessage({ __gate0: 'attach', nonce }, origin)
      timer = setTimeout(
        () => log(`host: ready 超时 5s（nonce=${nonce}）——frame 没跑脚本，或握手中断`),
        5000,
      )
      return true
    }

    window.addEventListener('message', (e) => {
      const d = e.data
      if (!d) return
      if (d.__gate0 === 'status') {
        if (d.nonce !== nonce) return
        return log(`host: status ← nonce=${d.nonce} notebook=${d.notebook}`)
      }
      if (d.__gate0 !== 'ready') return
      if (d.nonce !== nonce)
        return log(`host: ready 但 nonce 不符（拿到 ${d.nonce}，期待 ${nonce}）`)
      clearTimeout(timer)
      log(`host: ready ← nonce=${d.nonce} notebook=${d.notebook} href=${d.href}`)
    })

    attach('首次', true)
    let lastHref = location.href
    setInterval(() => {
      const frame = findFrame()
      if (frame !== boundFrame) {
        attach(frame ? 'iframe 出现/更换' : '轮询', true)
        if (!frame && !warned && Date.now() - started > 60000) {
          warned = true
          log('host: 60s 内没找到 iframe——选择器或页面结构变了？')
        }
      }
      if (location.href !== lastHref) {
        lastHref = location.href
        log(`host: URL 变化（SPA）→ ${safeHref()}`)
        attach('SPA 换 URL', true)
      }
    }, 1000)
  }

  if (isHost) logSummary()
  else summarizeWhenReady()
})()
