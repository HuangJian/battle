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

/** 任务包**旁挂**元数据的后缀/魔数（python：`remote/bundle.py::BUNDLE_META_SUFFIX` /
 *  `BUNDLE_META_MAGIC`）。
 *
 *  为什么需要它（2026-10-10，plan/cluster-code-snapshot §4.2）：控制台判「包是否过期」的
 *  代码维度从「源文件 mtime」换成「包内 `code.zip` 的 sha 是不是当前集群快照那一份」，
 *  而包内的 manifest 只有 python 能读（dashboard **零运行时依赖**，没有 zip 库）⇒
 *  python 导出时把该 sha 旁挂成 `<包路径>.meta.json`。两边写岔 ⇒ 判据静默退化成旧口径。 */
export const bundleMetaSuffix = '.meta.json'

/** 旁挂元数据的魔数（对不上就当它不存在——不猜、不迁移）。 */
export const bundleMetaMagic = 'battle2-task-bundle-meta'
