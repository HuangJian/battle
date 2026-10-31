// ==UserScript==
// @name         Battle City Notebook Companion
// @namespace    battle-city-web
// @version      0.1.0
// @description  把 Kaggle / Colab notebook 的 cell 输出定期保存到本机 dashboard
// @match        https://www.kaggle.com/code/*
// @match        https://*.jupyter-proxy.kaggle.net/*
// @match        https://colab.research.google.com/*
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

boot(globalThis as unknown as Record<string, unknown>, location.href)
