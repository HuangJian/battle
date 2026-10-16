/** storage.ts — 浏览器 localStorage 的两行薄封装（控制台**唯一**的本地状态通道）。
 *
 *  为什么单独成模块（2026-10-16）：`/errors` 页（`errors-app.tsx`）与首页（`app.tsx`）**必须**
 *  读写同一份 `TC_ALERT_ACKS`（ack 是浏览器级事实：同一份登记表在两个页签里看到的「未处理数」
 *  必须一致）。此前这两个函数是 `app.tsx` 的私有函数 —— 复制一份到新页 = 两处各写一遍
 *  「读不到就算空、写失败就吞」（这一对语义一旦漂开，症状是「ack 了但红条还在」）。
 *
 *  吞异常是**有意的**：Safari 无痕模式 / 存储配额满时 `localStorage` 会抛，而「记不住已读」
 *  不该把控制台整页打崩。
 */

export function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeLocal(key: string, v: string): void {
  try {
    localStorage.setItem(key, v)
  } catch {
    /* 读不到/写不进 = 本次会话不记忆（不抛） */
  }
}
