// ==UserScript==
// @name         Battle City Notebook Companion
// @namespace    battle-city-web
// @version      0.1.0
// @description  把 Kaggle / Colab / AI Studio notebook 的 cell 输出定期保存到本机 dashboard（魔搭：面板可用，输出在跨域 webview 里读不到）
// @match        https://www.kaggle.com/code/*
// @match        https://*.jupyter-proxy.kaggle.net/*
// @match        https://colab.research.google.com/*
// @match        https://aistudio.baidu.com/*
// @match        https://modelscope.cn/notebook/*
// @match        https://www.modelscope.cn/notebook/*
// @match        https://modelscope.cn/code/*
// @match        https://www.modelscope.cn/code/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @grant        GM_setClipboard
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-start
// ==/UserScript==

import { boot } from './app/boot'
import { installModelscopeIdleDismiss } from './app/modelscope-idle'

const scope = globalThis as unknown as Record<string, unknown>

// 魔搭「静默提醒」弹窗出现在外层工作区页（`/code/workspace`）——那一页没有 notebook 角色，
// `boot` 会直接返回。所以它与角色分流**无关**，在 modelscope 任意匹配页上独立安装
//（2026-11-10 用户要求：静默提醒弹窗要随机等几十秒后自动点确定）。
if (/^https?:\/\/(?:www\.)?modelscope\.cn\//i.test(location.href)) {
  const doc = scope.document as Document | undefined
  const install = (): void => {
    if (doc) installModelscopeIdleDismiss(doc)
  }
  // `@run-at document-start`：documentElement 还没有时等 DOM 就绪再装观察器。
  if (doc?.documentElement) install()
  else doc?.addEventListener('DOMContentLoaded', install, { once: true })
}

boot(scope, location.href)
