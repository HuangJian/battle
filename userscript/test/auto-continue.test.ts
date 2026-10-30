import { afterEach, describe, expect, it } from 'bun:test'
import { loadDom, type DomHandle } from '../happydom'
import { installAutoContinue } from '../src/notebook-companion/app/auto-continue'

const opened: DomHandle[] = []
const docOf = (w: DomHandle): Document => w.document as unknown as Document

function dom(): { w: DomHandle; doc: Document } {
  const w = loadDom('<html><body></body></html>')
  opened.push(w)
  return { w, doc: docOf(w) }
}
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

/** 用户贴的 Kaggle 弹窗形状（MUI paper + span 包文案）。 */
function dialog(
  doc: Document,
  labels: string[],
  id: string,
  bodyText = 'You have exceeded the number of hours allowed.',
): { root: HTMLElement; clicks: string[] } {
  const root = doc.createElement('div')
  root.setAttribute('role', 'dialog')
  root.setAttribute('aria-labelledby', ':ro:')
  root.id = id
  const body = doc.createElement('div')
  body.textContent = bodyText
  root.append(body)
  const row = doc.createElement('div')
  const clicks: string[] = []
  for (const label of labels) {
    const btn = doc.createElement('button')
    btn.setAttribute('role', 'button')
    const span = doc.createElement('span')
    span.textContent = label
    btn.append(span)
    btn.addEventListener('click', () => clicks.push(label))
    row.append(btn)
  }
  root.append(row)
  doc.body.append(root)
  return { root, clicks }
}

describe('Kaggle 弹窗自动确认（只点白名单）', () => {
  it('配额弹窗：点 Continue working，不碰 Turn off TPU', () => {
    const { doc } = dom()
    const confirmed: string[] = []
    const auto = installAutoContinue(doc, { onConfirm: (label) => confirmed.push(label) })
    const d = dialog(doc, ['Continue working', 'Turn off TPU'], 'quota')
    auto.scan()
    expect(d.clicks).toEqual(['Continue working'])
    expect(confirmed).toEqual(['continue working'])
    auto.dispose()
  })

  it('「是否仍需编辑」弹窗：点 Continue editing（span 里的文案也认）', () => {
    const { doc } = dom()
    const auto = installAutoContinue(doc)
    const d = dialog(doc, ['Cancel', 'Continue editing'], 'edit')
    auto.scan()
    expect(d.clicks).toEqual(['Continue editing'])
    auto.dispose()
  })

  it('保存冲突警告（notebook 在别处被改过）⇒ 只点 Cancel，不碰 Download', () => {
    const { doc } = dom()
    const auto = installAutoContinue(doc)
    const d = dialog(
      doc,
      ['Download', 'Cancel'],
      'conflict',
      "We're unable to save your notebook because it may have been modified in another " +
        'location. Refreshing the page may fix this issue.<br><br>We suggest downloading a ' +
        'copy of your notebook to avoid losing your work.',
    )
    auto.scan()
    expect(d.clicks).toEqual(['Cancel'])
    auto.dispose()
  })

  it('普通弹窗里的 Cancel 不点（避免误关别的对话框）', () => {
    const { doc } = dom()
    const auto = installAutoContinue(doc)
    const d = dialog(doc, ['Download', 'Cancel'], 'plain', 'Are you sure you want to leave?')
    auto.scan()
    expect(d.clicks).toEqual([])
    auto.dispose()
  })

  it('同一弹窗只点一次；无关文案一律不点', () => {
    const { doc } = dom()
    const auto = installAutoContinue(doc)
    const d = dialog(doc, ['Continue editing'], 'once')
    auto.scan()
    auto.scan()
    expect(d.clicks).toEqual(['Continue editing'])

    const other = dialog(doc, ['Turn off TPU', 'OK'], 'other')
    auto.scan()
    expect(other.clicks).toEqual([])
    auto.dispose()
  })

  it('冷却窗内不连点（MUI 重渲染同一文案的新 dialog）', () => {
    const { doc } = dom()
    let now = 1_000
    const auto = installAutoContinue(doc, { now: () => now })
    const a = dialog(doc, ['Continue editing'], 'a')
    auto.scan()
    expect(a.clicks).toHaveLength(1)

    const b = dialog(doc, ['Continue editing'], 'b')
    auto.scan()
    expect(b.clicks).toHaveLength(0) // 1.5s 内不再点
    now += 2_000
    auto.scan()
    expect(b.clicks).toHaveLength(1)
    auto.dispose()
  })

  it('dispose 之后新弹窗不再被点', () => {
    const { doc } = dom()
    const auto = installAutoContinue(doc)
    auto.dispose()
    const d = dialog(doc, ['Continue editing'], 'late')
    auto.scan()
    expect(d.clicks).toEqual([])
  })
})
