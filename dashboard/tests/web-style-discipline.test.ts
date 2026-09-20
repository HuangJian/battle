// P4 视觉精修的源文件级回归闸（docs/dashboard-redesign.md §7）。
//
// 为什么要有这一层：字号阶梯与内联样式收敛都是**靠人手一次性扫全量**完成的 ——
// 没有断言盯着「不许再出现第二个字号字面量 / 第二处内联布局 style」，
// 下一个改动顺手写 `style={{ marginTop: 8 }}` 或 `font-size: 11px` 就能把
// 「全站同一档字号、同一套间距」这条 DoD 悄悄退回去，而**任何渲染级断言都不会红**。
//
// 两条纪律（README 同款）：
//  ① 前提闸先行 —— 引用文件的闸若把路径写错（读到空串），断言会退化成永真；
//     所以每条扫描都先证明「扫到的量 > 0」。
//  ② 查**规则**不查字符串 —— theme.css 里留着解释改名史的注释，
//     而该文件会随 SSR 整体内联进 html（见 web-ssr-console.test.ts 头注），
//     所以「字符串出现次数为 0」这类断言会把自己的历史注也判红。
import { describe, it, expect } from 'bun:test'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const CSS_PATH = join(DASHBOARD_ROOT, 'src', 'web', 'theme.css')
const WEB_DIR = join(DASHBOARD_ROOT, 'src', 'web')

/** 递归收集 `src/web` 下的所有 `.tsx`（与 web-ssr-console 的扫描同意图）。 */
function webTsx(dir = WEB_DIR, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) webTsx(p, out)
    else if (e.name.endsWith('.tsx')) out.push(p)
  }
  return out
}

/** 按仓库相对路径读，报错信息里带上文件名便于定位。 */
const rel = (p: string) => p.slice(WEB_DIR.length + 1).replace(/\\/g, '/')
const read = (p: string) => readFileSync(p, 'utf8')

const css = read(CSS_PATH)

/** 阶梯的**唯一一份定义**。加档位要同时改 theme.css 与本数组。 */
const LADDER = ['--fs-xs', '--fs-sm', '--fs-base', '--fs-md', '--fs-lg', '--fs-xl', '--fs-2xl']

describe('视觉纪律：字号阶梯', () => {
  it('前提：theme.css 读到了内容，且真的带着阶梯定义', () => {
    expect(css.length).toBeGreaterThan(10_000)
    for (const t of LADDER) expect(css).toContain(`${t}:`)
  })

  it('每一条 font-size 都取自阶梯，没有字面量 px', () => {
    // 只匹配**声明**（`font-size:` 后到分号），不看注释里的举例。
    const decls = css.match(/font-size:\s*[^;]+;/g) ?? []
    expect(decls.length).toBeGreaterThan(50) // 前提：声明确实扫到了
    const literals = decls.filter((d) => !d.includes('var(--fs-'))
    expect(literals).toEqual([])
  })

  it('阶梯档位唯一且严格递增（顺序即语义：xs < sm < base < …）', () => {
    const px = LADDER.map((t) => {
      const m = css.match(new RegExp(`${t}:\\s*(\\d+(?:\\.\\d+)?)px`))
      return m ? Number(m[1]) : NaN
    })
    expect(px.every((n) => Number.isFinite(n))).toBe(true)
    for (let i = 1; i < px.length; i++) expect(px[i]).toBeGreaterThan(px[i - 1])
  })

  it('旧别名 --fs-1..--fs-5 不得复活（定义与消费者都不行）', () => {
    // 定义侧：theme.css 里不得再有 `--fs-<数字>:`。
    expect(css).not.toMatch(/--fs-[1-5]:/)
    // 消费侧：全量 tsx 不得再 `var(--fs-<数字>)`（P4 迁了 79 处）。
    const files = webTsx()
    expect(files.length).toBeGreaterThan(40) // 前提：扫描非空转
    const hits: string[] = []
    for (const f of files) {
      for (const m of read(f).matchAll(/var\(--fs-[1-5]\)/g)) hits.push(`${rel(f)}: ${m[0]}`)
    }
    expect(hits).toEqual([])
  })
})

describe('视觉纪律：内联布局样式', () => {
  // 唯一豁免：TrendChart 的 3 处 `style=` 都是**计算值**（图形高度、提示框按
  // x 比例定位），无法写成静态类名；SVG 坐标系是运行时才知道的。
  // 键 = 仓库相对路径，值 = 允许的 `style=` 出现次数（写死次数，免得这个桶越积越大）。
  const ALLOWED: Record<string, number> = {
    'components/TrendChart.tsx': 3,
  }

  it('前提：扫到的 tsx 数量合理（防止路径写错导致扫描空转）', () => {
    const files = webTsx()
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => rel(f) === 'components/TrendChart.tsx')).toBe(true)
  })

  it('除 TrendChart 的 3 处计算值外，tsx 里没有 style=', () => {
    const offenders: string[] = []
    for (const f of webTsx()) {
      const n = (read(f).match(/style=/g) ?? []).length
      if (n === 0) continue
      const allowed = ALLOWED[rel(f)]
      // 既报「新的违规文件」，也报「豁免文件里多出来的第 4 处」。
      if (allowed === undefined || n > allowed) offenders.push(`${rel(f)}: ${n} 处`)
    }
    expect(offenders).toEqual([])
  })

  it('没有字符串式内联（`style="…"`）——这是 P4 清掉的那批', () => {
    const offenders = webTsx()
      .filter((f) => /style="/.test(read(f)))
      .map(rel)
    expect(offenders).toEqual([])
  })

  it('TrendChart 的豁免确实是计算值（不是趁机塞进来的静态布局）', () => {
    // 豁免不是「这个文件随便写」：三处都必须是 JSX 表达式，且不得出现硬编码的
    // 长度/字号字面量 —— `style={{ height: '12px' }}` 这种能写成类的写法要直接红。
    const src = read(join(WEB_DIR, 'components', 'TrendChart.tsx'))
    const heads = [...src.matchAll(/style=\{([^\n]*)/g)].map((m) => m[1])
    expect(heads.length).toBe(ALLOWED['components/TrendChart.tsx'])
    for (const h of heads) {
      // 表达式（对象/标识符）或对象字面量的内部（`{ height }}` 的尾巴）都算合法起点，
      // 关键是否定项在下一条。
      expect(h.trim().length).toBeGreaterThan(0)
    }
    // 硬编码长度值（`'12px'` / `"40%"` 之类）必须走 CSS 类。
    expect(src).not.toMatch(/style=\{\{[^}\n]*:\s*['"][\d.]+(px|rem|em)/)
    // 前提：豁免的确实是「高度/定位」这类几何量（值里引用了变量）。
    expect(src).toMatch(/style=\{\{ height \}\}/)
  })
})

describe('视觉纪律：色值只能来自语义 token', () => {
  // 两处豁免都是「画店标 / 画 SVG 标记」，不是 UI 语义色：
  //  · render.tsx —— 品牌图标的内联 SVG（配色跟着 logo 走，不跟主题走）；
  //  · TrendChart —— SVG 标记点的白描边 + 一条 token 的 fallback。
  // 值 = 该文件允许的十六进制字面量个数，写死次数，新的第三处必须是一次显式决定。
  const ALLOWED: Record<string, number> = {
    'render.tsx': 8,
    'components/TrendChart.tsx': 2,
  }

  it('前提：tsx 扫描非空转', () => {
    expect(webTsx().length).toBeGreaterThan(40)
  })

  it('除两处 SVG 豁免外，tsx 里没有十六进制色值字面量', () => {
    const offenders: string[] = []
    for (const f of webTsx()) {
      const src = read(f)
      // 六位与三位都算（`#fff` 这种短路写法同样是绕过 token）。
      // `(?<!&)` 排掉 HTML 数字实体（`it&#123;N&#125;` 里的 `&#123` 长得像三位色值）。
      const n = (src.match(/(?<!&)#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/g) ?? []).length
      if (n === 0) continue
      const allowed = ALLOWED[rel(f)]
      if (allowed === undefined || n > allowed) offenders.push(`${rel(f)}: ${n} 处`)
    }
    expect(offenders).toEqual([])
  })

  it('豁免只豁免 SVG 呈现属性 —— 字符串里的 CSS 颜色仍不许（`color: #…` / `rgba(…)`）', () => {
    // 这条是上一条的补丁：`fill="#1c2333"` 是 SVG 属性（合法），
    // 而 `style={{ color: '#333' }}` 或 `css = 'background: rgba(0,0,0,.5)'` 不是。
    expect(
      webTsx().filter((f) => /(?:color|background)\s*:\s*(?:#|rgba?\()/.test(read(f))),
    ).toEqual([])
  })
})
