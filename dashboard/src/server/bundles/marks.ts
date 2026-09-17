/** marks.ts — 与 python 一侧共享的**跨语言常量**（控制台 ↔ `remote/deliver_zip.py`）。
 *
 *  为什么单独一个文件：这些串两边都要写，而两边写岔了是**静默**的（导入器打的那行没人
 *  认得出 → 控制台报「导入失败」，真因却不在这边）。放在这里 + 配套测试
 *  （`tests/server-api-task-bundle.test.ts` 直接读 python 源码对账）⇒ 改一边忘另一边
 *  会红。
 */

/** 导入器 stdout 里那行机器可读结果的标记（python：`deliver_zip.IMPORT_JSON_MARK`）。 */
export const deliverImportJsonMark = 'DELIVER_IMPORT_JSON='

/** 产物 zip 的习惯文件名前缀（`deliver-<课程>.zip`；python：`deliver_zip.DELIVER_PREFIX`）。 */
export const deliverFileNamePrefix = 'deliver-'

/** 任务包 zip 的习惯文件名前缀（`task-<课程>.zip`；python：`bundle` 的 README 里同名）。 */
export const taskFileNamePrefix = 'task-'
