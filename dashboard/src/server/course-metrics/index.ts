/** index.ts — 课程指标备份库的公开面（dashboard 各目录只 import 桶，AGENTS §3）。
 *
 *  纯函数判定（engine）、写侧 IO（mirror）、冷存打包（compact）、冷存读（archive）与
 *  存储契约（meta）都从这里出；`api/course-source.ts` 只消费它，不直接摸 fs 细节。
 */

export * from './meta'
export * from './engine'
export * from './mirror'
export * from './compact'
export * from './archive'
