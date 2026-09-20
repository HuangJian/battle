/**
 * web-status-row.test.ts — P1 原子：行原语 / 区块头 / 空态 / 动作结果行
 *   （src/web/components/{StatusRow,StatusDot,SectionHeader,Empty,InlineNotice}.tsx）
 *
 * 分层：src/web/components/StatusRow.tsx（+ StatusDot / SectionHeader / Empty / InlineNotice）
 *
 * 依据 docs/dashboard-redesign.md §4.2（问题 C6/C9）：这三/四个原子是「同一语义只有一个实现」
 * 的落点，因此它们的**契约**（字段顺序、只读语义、展开语义、空态三态）必须被钉住——
 * 一旦某个调用方偷偷改回自绘行，这里不会红，但下一块面板就会重新长出第 5 套行样式。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { Empty } from '../src/web/components/Empty'
import { InlineNotice } from '../src/web/components/InlineNotice'
import { SectionHeader } from '../src/web/components/SectionHeader'
import { StatusDot, statusDotClass } from '../src/web/components/StatusDot'
import { StatusRow } from '../src/web/components/StatusRow'

describe('StatusDot：语义档 → 类名（四档，沿用既有 .tc-dot--* 词表）', () => {
  it('四档各自映射，且两两不同', () => {
    expect(statusDotClass('ok')).toBe('tc-dot tc-dot--on')
    expect(statusDotClass('warn')).toBe('tc-dot tc-dot--warn')
    expect(statusDotClass('err')).toBe('tc-dot tc-dot--dead')
    expect(statusDotClass('off')).toBe('tc-dot tc-dot--empty')
    const all = (['ok', 'warn', 'err', 'off'] as const).map(statusDotClass)
    expect(new Set(all).size).toBe(4)
  })

  it('title 是状态点的判据（颜色/形状不是唯一信息载体）', () => {
    const html = renderToString(h(StatusDot, { tone: 'err', title: 'hub 侧离线' }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('title="hub 侧离线"')
  })
})

describe('StatusRow：字段顺序与渲染契约', () => {
  const base = {
    tone: 'ok' as const,
    name: 'gpu1',
  }

  it('顺序固定：状态点 → 名称 → 值 → 徽章 → 元信息 → 动作', () => {
    const html = renderToString(
      h(StatusRow, {
        ...base,
        value: '✓4',
        badges: [{ text: '共享', tone: 'a', title: '一个进程服务所有课程' }],
        meta: [{ text: '上轮 12', title: '上一轮贡献数' }],
        actions: h('button', { type: 'button' }, '停止'),
      }),
    )
    const order = ['tc-dot', '>gpu1<', '>✓4<', '>共享<', '>上轮 12<', '>停止<']
    let prev = -1
    for (const frag of order) {
      const idx = html.indexOf(frag)
      expect(idx, frag).toBeGreaterThan(prev)
      prev = idx
    }
  })

  it('名称渲染为无类名的 <b>（既有断言 `<b>key</b>` 依赖它）', () => {
    const html = renderToString(h(StatusRow, base))
    expect(html).toContain('<b>gpu1</b>')
  })

  it('badge 可覆盖类名（面板自带徽章词表）或按 tone 取默认', () => {
    const custom = renderToString(
      h(StatusRow, {
        ...base,
        badges: [{ text: '共享', cls: 'tc-cc__scope tc-cc__scope--shared' }],
      }),
    )
    expect(custom).toContain('class="tc-cc__scope tc-cc__scope--shared"')
    const toned = renderToString(h(StatusRow, { ...base, badges: [{ text: '忙', tone: 'y' }] }))
    expect(toned).toContain('class="tc-badge tc-badge--y"')
    const plain = renderToString(h(StatusRow, { ...base, badges: [{ text: '忙' }] }))
    expect(plain).toContain('class="tc-badge tc-badge--gray"')
  })

  it('mono 元信息加等宽类', () => {
    const html = renderToString(
      h(StatusRow, { ...base, meta: [{ text: 'https://x', mono: true }] }),
    )
    expect(html).toContain('tc-row__meta tc-mono')
  })

  it('可点击行：role=button + tabIndex + aria-expanded（显式折叠语义）', () => {
    const html = renderToString(h(StatusRow, { ...base, onToggle: () => {}, expanded: true }))
    expect(html).toContain('role="button"')
    expect(html).toContain('tabindex="0"')
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('tc-row--act')
    expect(html).toContain('tc-row--open')
  })

  it('不可点击行不带交互角色，也不带 aria-expanded', () => {
    const html = renderToString(h(StatusRow, base))
    expect(html).not.toContain('role="button"')
    expect(html).not.toContain('tabindex')
    expect(html).not.toContain('aria-expanded')
  })

  it('只读：即使给了 onToggle 也不渲染交互角色（只读是动作边界，不是按钮状态）', () => {
    const html = renderToString(
      h(StatusRow, {
        ...base,
        onToggle: () => {},
        readOnly: true,
        roTitle: '只读模式：仅限本机',
        actions: h('button', { type: 'button' }, '启动'),
      }),
    )
    expect(html).not.toContain('role="button"')
    expect(html).not.toContain('tabindex')
    expect(html).toContain('title="只读模式：仅限本机"')
    // 行不因只读而灰败：动作区仍在
    expect(html).toContain('tc-row__acts')
  })

  it('详情只在展开时渲染，且是整行宽容器（flex-basis 100%）', () => {
    const collapsed = renderToString(
      h(StatusRow, { ...base, onToggle: () => {}, detail: h('em', {}, 'detail-text') }),
    )
    expect(collapsed).not.toContain('detail-text')
    const open = renderToString(
      h(StatusRow, {
        ...base,
        onToggle: () => {},
        expanded: true,
        detail: h('em', {}, 'detail-text'),
      }),
    )
    expect(open).toContain('tc-row__detail')
    expect(open).toContain('detail-text')
  })

  it('展开必带显式折叠头：收起是看得见的按钮，不是「点详情任意处」', () => {
    const open = renderToString(
      h(StatusRow, {
        ...base,
        onToggle: () => {},
        expanded: true,
        detailTitle: '日志详情',
        detail: h('pre', {}, 'tail'),
      }),
    )
    expect(open).toContain('tc-row__detailhd')
    expect(open).toContain('日志详情')
    expect(open).toContain('class="tc-row__collapse"')
    expect(open).toContain('aria-label="收起 gpu1 的详情"')
  })

  it('折叠头在详情之前，且详情原样透传（不被套壳）', () => {
    // 回归闸：旧 `tc-cc__detail` 是 `<pre onClick={close}>`——想选段复制，一按鼠标就收起。
    // 该缺陷**无法用 SSR 断言钉住**：preact-render-to-string 丢弃所有事件处理器
    // （实测 `h('pre', {onClick}, 'x')` → `<pre>x</pre>`），有没有点击区在 HTML 上都一样。
    // 能钉住的只有结构：折叠头必须出现，且详情标记原样相邻——被人套一层「点哪都收起」的
    // 包装元素时这里会红（多出的一层会落进这段子串里）。
    const html = renderToString(
      h(StatusRow, {
        ...base,
        onToggle: () => {},
        expanded: true,
        detail: h('pre', {}, 'tail'),
      }),
    )
    expect(html).toContain('tc-row__detailhd')
    expect(html.indexOf('tc-row__detailhd')).toBeLessThan(html.indexOf('<pre>tail</pre>'))
    expect(html).toContain('</div><pre>tail</pre>')
  })

  it('无 detailTitle 时折叠头只留右侧按钮（节点行那种紧凑详情不当标题用）', () => {
    const html = renderToString(
      h(StatusRow, { ...base, onToggle: () => {}, expanded: true, detail: h('em', {}, 'x') }),
    )
    expect(html).toContain('tc-row__detailhd')
    expect(html).not.toContain('tc-row__detailttl')
    expect(html).toContain('class="tc-row__collapse"')
  })

  it('只读行不渲染折叠头按钮（行本身已不可展开，按钮会是死键）', () => {
    const html = renderToString(
      h(StatusRow, {
        ...base,
        onToggle: () => {},
        expanded: true,
        readOnly: true,
        detail: h('em', {}, 'x'),
      }),
    )
    expect(html).not.toContain('tc-row__collapse')
  })

  it('动作区在行内且自带冒泡拦截标记（点按钮不顺带折叠整行）', () => {
    const html = renderToString(
      h(StatusRow, { ...base, onToggle: () => {}, actions: h('button', {}, '移除') }),
    )
    expect(html).toContain('class="tc-row__acts"')
  })

  it('className 透传为行 modifier（面板的局部语义，如本机/停用）', () => {
    const html = renderToString(h(StatusRow, { ...base, className: 'tc-row--local' }))
    expect(html).toContain('tc-row tc-row--local')
  })
})

describe('SectionHeader：标题 + 计数 + 注 + 动作', () => {
  it('计数为 0 也显示（「0」是有信息量的读数）', () => {
    const html = renderToString(h(SectionHeader, { title: 'push worker', count: 0 }))
    expect(html).toContain('>push worker<')
    expect(html).toContain('>0<')
  })

  it('note 是读数、actions 是控件，两者分开渲染', () => {
    const html = renderToString(
      h(SectionHeader, {
        title: '监控',
        hint: '标题悬停说明',
        note: h('span', { class: 'tc-wreg__mount' }, 'hub 已挂载派发'),
        actions: h('button', {}, '重载'),
      }),
    )
    expect(html).toContain('title="标题悬停说明"')
    expect(html).toContain('tc-wreg__mount')
    expect(html).toContain('tc-sechd__acts')
  })

  it('sub 层级的修饰类', () => {
    expect(renderToString(h(SectionHeader, { title: '服务面 · 单例', sub: true }))).toContain(
      'tc-sechd tc-sechd--sub',
    )
  })
})

describe('InlineNotice：面板内动作结果一行（C6 的第二个实例）', () => {
  it('默认灰字小号 + role=status，样式只此一处', () => {
    const html = renderToString(h(InlineNotice, {}, '已入队 ckpt.pt'))
    expect(html).toContain('class="tc-notice"')
    expect(html).toContain('role="status"')
    expect(html).toContain('已入队 ckpt.pt')
  })

  it('wrap 保留换行（任务包的多行尾注）', () => {
    const html = renderToString(h(InlineNotice, { wrap: true }, 'line1\nline2'))
    expect(html).toContain('tc-notice tc-notice--wrap')
  })

  it('不带任何内联 style —— 间距归 CSS（旧五处各写 marginBottom: 4|6）', () => {
    expect(renderToString(h(InlineNotice, {}, 'x'))).not.toContain('style=')
  })

  it('消息为空串也渲染（「本窗口无结果」这类空白结果读得出来）', () => {
    expect(renderToString(h(InlineNotice, {}, ''))).toContain('class="tc-notice"')
  })
})

describe('Empty：三态必须可区分（问题 C9）', () => {
  it('empty / loading / error 各自带语义类与角色', () => {
    const empty = renderToString(h(Empty, { kind: 'empty', reason: '该课程暂无迭代记录' }))
    expect(empty).toContain('tc-emptybox--empty')
    expect(empty).toContain('该课程暂无迭代记录')
    expect(empty).toContain('role="status"')

    const loading = renderToString(h(Empty, { kind: 'loading', reason: '正在读盘' }))
    expect(loading).toContain('tc-emptybox--loading')

    const error = renderToString(
      h(Empty, { kind: 'error', reason: 'hub 无应答', onRetry: () => {} }),
    )
    expect(error).toContain('tc-emptybox--error')
    expect(error).toContain('role="alert"')
    expect(error).toContain('>重试<')
  })

  it('错误态无 onRetry 就不渲染重试键（不画假按钮）', () => {
    expect(renderToString(h(Empty, { kind: 'error', reason: 'x' }))).not.toContain('>重试<')
  })

  it('附加说明作为子内容渲染（口径 / 命令提示）', () => {
    const html = renderToString(
      h(Empty, { kind: 'empty', reason: '还没有登记 push worker' }, h('code', {}, 'kick-once.py')),
    )
    expect(html).toContain('kick-once.py')
  })
})
