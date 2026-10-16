/** errors-index.tsx — 错误详情页浏览器入口：/errors SSR payload + hydrate（照 log-index.tsx）。 */

import { hydrate } from 'preact'
import { ErrorsApp } from './errors-app'
import type { ErrorsPageOptions, ErrorsPagePayload } from '../view'

declare const window: Window & {
  __INITIAL__?: { payload: ErrorsPagePayload; options: ErrorsPageOptions }
}

const data = window.__INITIAL__
if (!data) throw new Error('missing window.__INITIAL__')

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')
hydrate(<ErrorsApp initial={data.payload} options={data.options} />, root)
