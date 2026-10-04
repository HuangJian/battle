/** theme-css.ts — SSR 内联样式（theme.css）的**现读**口：改样式不必重启 dashboard。
 *
 *  病根（2026-10-04 用户报）：`web/theme.ts` 走 `import css from './theme.css' with
 *  { type: 'text' }` —— Bun 在**模块加载时**读一次并缓存，SSR 进程不重启就不重读；
 *  改一条 CSS 必须重启控制台才生效（改成多少次刷新页面都是旧样式）。
 *
 *  判据与 `serveBundle` 的 `bundleMemory` 同款：每请求一次 `statSync`（微秒级），
 *  `mtimeMs` **或** `size` 变了才重读，没变零读盘（请求路径预算纪律见
 *  plan/dashboard-reload-perf：这里只有 1 次 stat，不碰聚合、不读大文件）。
 *  读不了（stat/read 抛）⇒ 上次好值优先（样式宁旧勿空）；从未读到 ⇒ 构建期内联。
 *
 *  分层红线（architecture-layering.test.ts）：读盘只能住服务端——`web/**` 禁
 *  `fs`/`node:`；CSS 出口经 render 函数的 `css` 参数注入，缺省仍是 `pageCss()`
 *  （测试与旧调用方拿到构建期内容，行为逐字不变）。**新增页面渲染口时别忘了注入**
 *  ——dashboard/tests/server-theme-hot-css.test.ts 的接线闸钉住 server.ts 的全部调用点。
 */
import { readFileSync, statSync } from 'fs'
import { join } from 'path'
import { DASHBOARD_ROOT } from '../core/paths'
import { pageCss } from '../web/theme'

export const THEME_CSS_PATH = join(DASHBOARD_ROOT, 'src', 'web', 'theme.css')

/** 变更指纹（只取判据需要的两列；`Stats` 结构兼容）。 */
export interface CssFingerprint {
  mtimeMs: number
  size: number
}

export interface CssHotReaderDeps {
  readFile?: (p: string) => string
  stat?: (p: string) => CssFingerprint
  /** 从未读到任何内容时的兜底（缺省 = 构建期文本导入 `pageCss()`）。 */
  fallback?: () => string
}

/** 依赖注入版（测试用 temp 文件 + 计数 stub 对拍）；生产单例见 `livePageCss`。 */
export function makeCssHotReader(cssPath: string, deps: CssHotReaderDeps = {}): () => string {
  const readFile = deps.readFile ?? ((p: string) => readFileSync(p, 'utf8'))
  const stat =
    deps.stat ??
    ((p: string) => {
      const s = statSync(p)
      return { mtimeMs: s.mtimeMs, size: s.size }
    })
  const fallback = deps.fallback ?? pageCss
  let cached: { fp: CssFingerprint; css: string } | null = null
  return () => {
    try {
      const fp = stat(cssPath)
      // size 是免费的第二判据：粗粒度文件系统（mtime 秒级）或同毫秒连写下 mtime 可能不动，
      // 只看 mtime 会静默发旧样式——「窗口比数据源长」同类，错数比崩溃贵。
      if (!cached || cached.fp.mtimeMs !== fp.mtimeMs || cached.fp.size !== fp.size) {
        cached = { fp, css: readFile(cssPath) }
      }
      return cached.css
    } catch {
      return cached?.css ?? fallback()
    }
  }
}

/** SSR 每拍现读（每请求一次 statSync；文件没变零读盘）。 */
export const livePageCss = makeCssHotReader(THEME_CSS_PATH)
