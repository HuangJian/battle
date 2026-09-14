/** theme.d.ts — bun 的 css 文本 import 类型声明。
 *
 *  bun 运行时对 `import css from './x.css' with { type: 'text' }` 的 default export =
 *  文件文本（不带 with 属性时 bun 返回 CSSStyleSheet 对象，不能内联；theme.ts 依赖
 *  文本契约做 SSR <style> 注入）。bun-types 未内置该声明，这里补齐给 tsc。
 */

declare module '*.css' {
  const css: string
  export default css
}
