/** component-groups.ts — 组件卡分组（R3-3：**服务面**（单例角色）vs **课程面**（按课程））。
 *
 *  为什么分组，而不是继续排一行：
 *
 *   - 单例角色（selfNode / hub / 隧道 / **trainer** / **本机 worker**）与当前查看的课程**无关**
 *     ——0 门课也在、100 门课也是一份。混在按课的行里，操作员会去「给这门课再起一个 hub /
 *     trainer / worker」（而它们是共享的：第二个实例抢同一个端口、两套调度器抢同一批 traj、
 *     多份 worker 抢同一份 hub 队列），或者把「共享 hub 没起」读成「这门课自己的 hub 没起」；
 *   - 课程面恰好相反：卡片上的对象**就是当前查看的那门课**。换课程 = 换对象，这一点必须在视觉上
 *     说出来（否则「启动」按下去，起的可能是另一门课）。**2026-09-19 起这一族没有成员**
 *     （账本键收敛的终点：进程面全在服务面），但机制保留——它是 `scope` 的函数，不是名单。
 *
 *  ★ **成员资格只由 `ComponentView.scope` 决定**（服务端按 `core/registry.componentScope` 填）。
 *  这里**不写**「哪些 key 属于哪一族」的名单——名单一旦与账本槽位规则漂开，症状是某个组件从
 *  UI 上**消失**（而它还在被启动、被监督、被冒烟），且读代码的人只看到一行 filter、不知所为何来。
 *  本模块只声明：**族 → 标题/说明** 与 **族内化妆顺序**。
 *
 *  （旧的 `NODE_FACE_COMPONENTS = ['workerServe']` 例外名单已随「本机伪节点退出受管组件」
 *  在 2026-09-19 删除：`worker_server` 此后只是**节点登记表里的数据**（`nodes[]` 的
 *  gpu_push / local_push 条目）与节点行，不再是账本里的受管组件 ⇒ 不需要第二份名单。）
 */

import type { ComponentScope, ComponentView } from './console-types'

/** 族标识。★ 本文件在**客户端** bundle 里：别写出未加引号的 `node:` 键（`{ node: … }`）——
 *  构建期的禁词门禁把 `node:` 当「引入了 node 内置模块」（`server/build.ts` 的 FORBIDDEN）。
 *  2026-09-19 实际踩到：`Record<ComponentFamilyId, …>` 里那个 `node: []` 就让三份 bundle 全挂。 */
export type ComponentFamilyId = 'service' | 'course'

/** 族元数据（标题上屏；hint 是组标题的悬停/副标题——它回答「这一族的键是什么」）。 */
export const FAMILY_META: Record<'service' | 'course', { title: string; hint: string }> = {
  service: {
    title: '服务面 · 单例',
    hint:
      '与课程数量无关：一个进程服务所有并行课程（0 门课也在，100 门课也只有一份）。' +
      '「共享」= hub / 隧道 / trainer / 本机 worker（各一个进程服务所有课程）；' +
      '「单例」= 本机 agent（全机一份）。',
  },
  course: {
    title: '课程面 · 按课程',
    hint: '卡片上的对象是**当前查看的那门课**——换课程 = 换对象（启动前先看清顶栏选的是哪门课）。',
  },
}

/** 组内展示顺序（**纯化妆**：未列出的 key 落到组尾，绝不丢弃——成员资格只由 scope 决定）。 */
const ORDER: Record<'service' | 'course', readonly string[]> = {
  // 服务面按「谁依赖谁」排：agent（一切动作的落点）→ hub（调度中枢）→ 隧道（入站通道）
  // → trainer（消费 hub 的作业队列、服务所有课程）→ 本机 worker（消费同一份队列里的活）。
  service: ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop', 'localWorker'],
  // 课程面：**当前空**（2026-09-19 收敛完成：selfNode 单例，hub / 隧道 / trainer / 本机 worker
  // 四条共享，本机伪节点退出受管组件 —— 于是 `cardFamilies` 只渲染服务面一组，这不是坏了）。
  // `course` 族本身保留：它是 scope 的函数，日后真出现按课程的卡片会自然落进去，不必改代码。
  // 注：trainer（R3-5）与本机 worker（2026-09-19）都已收敛为共享进程 ⇒ 属于**服务面**，
  // 不在此列（ORDER 只是化妆顺序：真成员资格由 `scope` 决定，写错这里不会让组件错族）。
  course: [],
}

const FAMILY_OF_SCOPE: Record<ComponentScope, 'service' | 'course'> = {
  singleton: 'service',
  shared: 'service',
  course: 'course',
}

export interface ComponentFamilyView {
  id: ComponentFamilyId
  title: string
  hint: string
  rows: ComponentView[]
}

function sortByOrder(rows: ComponentView[], order: readonly string[]): ComponentView[] {
  const rank = (k: string): number => {
    const i = order.indexOf(k)
    return i < 0 ? order.length : i
  }
  return [...rows].sort((a, b) => rank(a.key) - rank(b.key))
}

/** 组件卡分组：`[服务面, 课程面]`（空组不渲染——没东西可说时不留空壳）。
 *
 *  `scope` 缺省/未知 ⇒ 课程面（单侧保守，与 `ComponentView.scope` 的注释同规）。
 */
export function cardFamilies(components: readonly ComponentView[]): ComponentFamilyView[] {
  const buckets: Record<'service' | 'course', ComponentView[]> = { service: [], course: [] }
  for (const c of components) buckets[FAMILY_OF_SCOPE[c.scope ?? 'course']].push(c)
  const out: ComponentFamilyView[] = []
  for (const id of ['service', 'course'] as const) {
    if (buckets[id].length === 0) continue
    out.push({ id, ...FAMILY_META[id], rows: sortByOrder(buckets[id], ORDER[id]) })
  }
  return out
}

/** 作用域徽章：**只说 scope 说不出来的那件事**。
 *
 *  - `shared` ⇒ 「共享」（一个进程服务所有课程）——不说，操作员会以为「这门课自己的 hub 停了」
 *    而去重复启动（第二个 hub 抢同一端口）；
 *  - `singleton` ⇒ 「单例」（全机一份）；
 *  - `course` ⇒ **null**：按课程是这张卡片的**默认语义**（组标题已说「课程面 · 按课程」），
 *    每行再挂一个「本课」只是噪声——徽章的密度决定了它还读不读得出来。
 */
export function scopeBadge(c: ComponentView): { text: string; cls: string; title: string } | null {
  if (c.scope === 'shared') {
    return {
      text: '共享',
      cls: 'tc-cc__scope--shared',
      title:
        '共享实例：一个进程服务所有并行课程（启动/停止/冒烟的操作对象是同一个），' +
        '不属于任何单门课；重复启动会与现有实例抢同一端口。',
    }
  }
  if (c.scope === 'singleton') {
    return {
      text: '单例',
      cls: 'tc-cc__scope--singleton',
      title: '单例：全机一份（所有课程共用同一个 agent——它服务任意课程的 rollout/eval 请求）。',
    }
  }
  return null
}
