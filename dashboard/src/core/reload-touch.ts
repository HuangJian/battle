/** reload-touch.ts — 监控触点（sentinels.ts 的语义别名出口）。

 *  hub.ts / push.ts 等以 `monitorTouch` 名字使用触点；实现集中在 sentinels.ts，
 *  此处仅 re-export，避免两处定义。
 */

export { monitorTouch, lastMonitorChange } from './sentinels'
