/** theme.ts — 训练控制台唯一 CSS 源的出口（§5 token 体系 + tc- 前缀 BEM）。
 *
 *  样式本体在 theme.css（真实 css 文件，编辑器高亮/diff 友好）；本文件只做文本
 *  import + pageCss() 出口——SSR render.tsx 内联 <style> 的契约不变，客户端
 *  bundle 不消费本模块（唯一消费者 = render.tsx）。
 */

import css from './theme.css' with { type: 'text' }

export function pageCss(): string {
  return css
}
