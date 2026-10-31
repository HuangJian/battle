import { describe, expect, it } from 'bun:test'
import { detectRole } from '../src/notebook-companion/selectors'

describe('detectRole', () => {
  it('Kaggle 编辑页 = host', () => {
    expect(detectRole('https://www.kaggle.com/code/owner/slug/edit/run/123')).toBe('host')
    expect(detectRole('https://kaggle.com/code/owner/slug')).toBe('host')
  })

  it('jupyter-proxy 子帧 = frame（含 kkb-production 前缀）', () => {
    expect(
      detectRole(
        'https://kkb-production.jupyter-proxy.kaggle.net/static/assets/jupyterlab-v4/x.html?session=REDACTED',
      ),
    ).toBe('frame')
    expect(detectRole('https://jupyter-proxy.kaggle.net/static/x.html')).toBe('frame')
  })

  it('Colab = single（cell 与面板同文档，无跨域帧）', () => {
    expect(
      detectRole('https://colab.research.google.com/drive/1VUDX_hyHVvzc2kQxrAOSgsPvzwcuXD7n'),
    ).toBe('single')
    expect(detectRole('https://colab.research.google.com/github/u/r/blob/main/nb.ipynb')).toBe(
      'single',
    )
    expect(detectRole('https://www.colab.research.google.com/drive/x')).toBeNull()
  })

  it('其他页面 / 非 https / 伪域名一律 null', () => {
    expect(detectRole('https://www.kaggle.com/datasets/x')).toBeNull()
    expect(detectRole('http://www.kaggle.com/code/x')).toBeNull()
    expect(detectRole('https://notjupyter-proxy.kaggle.net/x')).toBeNull()
    expect(detectRole('https://evil.com/?u=https://www.kaggle.com/code/x')).toBeNull()
    expect(detectRole('not a url')).toBeNull()
  })
})
