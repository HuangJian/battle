/** eval-index.tsx — /eval 评估页浏览器入口：SSR payload + hydrate。 */

import { hydrate } from 'preact'
import { EvalApp } from './eval-app'
import type { EvalPageOptions, EvalPagePayload } from '../../ui/view'

declare const window: Window & {
  __INITIAL__?: { views: EvalPagePayload['views']; options: EvalPageOptions }
}

const data = window.__INITIAL__
if (!data) throw new Error('missing window.__INITIAL__')

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')
hydrate(
  <EvalApp initial={{ views: data.views, options: data.options }} options={data.options} />,
  root,
)
