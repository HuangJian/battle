/** bundles/ — 控制台的**任务包导出 / 产物导入**（`task-<课程>.zip` / `deliver-<课程>.zip`）。
 *
 *  模块地图：
 *    export   任务包导出（run_rl --export-bundle 一次性进程 + 产出文件信息 + 下载响应）
 *    import   产物导入（上传落盘 → python remote.deliver_zip → 自动起 evalA）
 *    marks    与 python 共享的跨语言常量（改一边忘另一边会红）
 *
 *  两条腿的智能都在 python 一侧（导出 = trainer 自己的发布链；导入 = `remote/deliver_zip`
 *  的三道门），这里只做「起进程 / 对账 / 把结果交给浏览器」——控制台不重造包格式。
 */

export * from './marks'
export * from './export'
export * from './import'
