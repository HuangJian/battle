/** BundleRowActions.tsx — 课程**行内**的离线任务包操作（导出 / 取回 / 导入训练结果）。
 *
 *  ★ 2026-09-22 改版（用户指令）：首页独立「任务包」面板下线——每个离线课程的
 *  「文件导出/导入」能力挪到课程矩阵所在行的「操作」列，与「切离线/切换成在线」「暂停」
 *  并列。
 *
 *  ★ 2026-09-23 改版（用户指令：「歧义太大」）：**「下载」链接删掉**，导出键本身兼取回。
 *
 *  为什么删：导出（生成 zip）与下载（取走 zip）是两个源上的两件事（前者是控制台后台的
 *  一次性快照，后者是浏览器导航到 `/api/taskBundle`），但对操作员是**同一个意图**
 *  ——「我要拿到这个包」。摆成两个控件时，他的动作取决于一个他看不见的内部状态：
 *  包还没生成时「下载」**不在页面上**（只剩一句「未导出」），于是「点了导出却没反应」
 *  成了最常见的误判。现在只有一个键，**点一下的结局恒定 = 拿到包**：
 *    · 已有包 ⇒ 直接下载；
 *    · 尚未生成 ⇒ 先起导出，生成后（轮询看到）**自动下载**。
 *  轮询仍走 `/api/taskBundleInfo`（5s；不另起通道），它同时负责「生成后自动取回」。
 *
 *  导入改为**真按键 + 真文件名**（「导入训练结果」）：旧形状是一个 `<label>` 包着隐藏
 *  input，视觉像键、语义不是键（键盘/SR 读出来是文件控件），而「导入产物」这个说法也没
 *  说清导入的是**云机跑出来的训练结果**（权重/指标），不是一份配置。
 *
 *  只读（LAN）语义沿用矩阵：按钮照常渲染可点，被拒由服务端 403 说明（§7 O1）。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  fetchTaskBundleInfo,
  postAction,
  taskBundleDownloadUrl,
  uploadDeliverZip,
  type TaskBundleInfo,
} from '../lib/api-client'
import { fmtBytes } from '../../view'

export interface BundleRowActionsProps {
  course: string
}

/** 行内小条：`导出任务包 · 导入训练结果`（+ 一条当前状态 flash）。
 *  轮询同时负责「生成完成后自动取回」——状态不另起拉取通道。 */
export function BundleRowActions({ course }: BundleRowActionsProps) {
  const [info, setInfo] = useState<TaskBundleInfo | null>(null)
  const [busy, setBusy] = useState<'export' | 'import' | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  /** 本轮点了「未生成的导出」⇒ 等它出现就自动取回（一次点击 = 拿到包）。 */
  const [awaitBundle, setAwaitBundle] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let alive = true
    const load = (): void => {
      void fetchTaskBundleInfo(course)
        .then((i) => alive && setInfo(i))
        .catch(() => alive && setInfo(null))
    }
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [course])

  /** 浏览器导航到下载端点（服务端带 `attachment` 文件名）。SSR 期无 window（不动作）。 */
  const startDownload = (): void => {
    if (typeof window === 'undefined') return
    window.location.href = taskBundleDownloadUrl(course)
  }

  // 导出完成后自动取回（见文件头注：一个键的结局必须是恒定的）。
  useEffect(() => {
    if (awaitBundle && info?.exists) {
      setAwaitBundle(false)
      startDownload()
    }
    // startDownload 是每次渲染重建的纯函数（无状态），依赖它只会白跑 —— 只认这两个状态。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitBundle, info?.exists])

  const finish = (msg: string | null): void => setFlash(msg)

  const onExport = async (): Promise<void> => {
    // 已有包 ⇒ 这一下就是取回；没有 ⇒ 起导出，生成后自动取回（两条路的结局相同）。
    if (info?.exists) {
      finish(null)
      startDownload()
      return
    }
    setBusy('export')
    try {
      const r = await postAction('exportTaskBundle', { course })
      if (r.ok) {
        setAwaitBundle(true)
        finish('导出已启动…生成完成后自动下载')
      } else {
        finish(r.message ?? '导出启动失败')
      }
    } catch (e) {
      finish(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const onImport = async (file: File): Promise<void> => {
    setBusy('import')
    try {
      const r = await uploadDeliverZip(course, file)
      finish(r.ok ? (r.message ?? '导入完成') : (r.message ?? '导入失败'))
    } catch (e) {
      finish(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <span className="tc-mx__opgroup" role="group" aria-label={`任务包：${course}`}>
      <span className="tc-mx__opsep" aria-hidden="true" />
      <button
        type="button"
        className="tc-btn tc-btn--sm"
        disabled={busy === 'export'}
        aria-label={`导出任务包 ${course}`}
        title={
          '点一下即拿到任务包：已有包就直接下载，尚未生成则先导出、生成后自动下载。' +
          '包内容 = 整段剩余（当前代码 + 课程配置 + 起点权重）；hub 的 /offline/task-pack ' +
          '也按课上架它，Kaggle/Colab 离线 worker 就绪后会自动拉取执行；' +
          '离线开课（重打包）会把旧包挪进 stale-packs/ 作废。'
        }
        onClick={() => void onExport()}
      >
        {busy === 'export' ? '导出中…' : '导出任务包'}
      </button>
      {info?.exists ? (
        <span className="tc-mx__bundleinfo" title={`${fmtBytes(info.bytes)} · ${info.log ?? ''}`}>
          <span className="tc-caption">{fmtBytes(info.bytes)}</span>
        </span>
      ) : (
        <span className="tc-caption" title="还没有导出过任务包（点「导出任务包」生成并自动下载）">
          未导出
        </span>
      )}
      <button
        type="button"
        className="tc-btn tc-btn--sm"
        disabled={busy !== null}
        aria-label={`导入训练结果 ${course}`}
        title={
          '导入云机的训练结果 deliver-<课>.zip（权重 / opt / 指标）——先落成课程账本与评估数据，' +
          '再自动评估（与实时回传同一个落地路径）。'
        }
        onClick={() => fileRef.current?.click()}
      >
        导入训练结果
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".zip"
        hidden
        disabled={busy !== null}
        aria-label={`选择训练结果 zip ${course}`}
        onChange={(e: JSX.TargetedEvent<HTMLInputElement>) => {
          const f = e.currentTarget.files?.[0]
          if (!f) return
          void onImport(f)
          e.currentTarget.value = ''
        }}
      />
      {flash ? (
        <span className="tc-caption" title={flash}>
          {flash.length > 60 ? `${flash.slice(0, 60)}…` : flash}
        </span>
      ) : null}
    </span>
  )
}
