/** log-index.tsx — 日志页浏览器入口：/log/<key> SSR payload + hydrate。 */

import { hydrate } from 'preact'
import { LogApp } from './log-app'
import type { LogPageOptions, LogPayload } from '../../ui/view'

declare const window: Window & { __INITIAL__?: { payload: LogPayload; options: LogPageOptions } }

const data = window.__INITIAL__
if (!data) throw new Error('missing window.__INITIAL__')

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')
hydrate(<LogApp initial={data.payload} options={data.options} />, root)
