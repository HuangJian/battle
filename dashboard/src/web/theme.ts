/** theme.ts — 训练控制台唯一 CSS 源的出口（§5 token 体系 + tc- 前缀 BEM）。
 *
 *  样式本体在 theme.css（真实 css 文件，编辑器高亮/diff 友好）；本文件只做文本
 *  import + pageCss() 出口——SSR render.tsx 内联 <style> 的契约不变，客户端
 *  bundle 不消费本模块（唯一消费者 = render.tsx）。
 *
 *  ★ 2026-10-04：改 theme.css **不必重启** —— 服务端每请求现读（`server/theme-css.ts`
 *  的 `livePageCss`，mtime/size 缓存），经 render 的 `css` 参数注入；本文件的文本
 *  导入退居**缺省兜底**（测试与未注入的调用方拿到构建期内容，行为逐字不变）。
 */

import css from './theme.css' with { type: 'text' }

export function pageCss(): string {
  return css
}
