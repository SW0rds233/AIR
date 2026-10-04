/**
 * 浏览器启动入口 (计划书 §2 F4)。
 *
 * Vite 会把本文件与它 import 的模块一起打进 `assets/*.js`; `index.html` 通过
 * `/src/main.ts` 引入。这里只做两件事:
 * 1. 建立/复用 `window.AIR` 契约 (见 `air-global.ts`);
 * 2. 引入页面逻辑 `app.ts` —— **必须真的被 import**, 否则会出现"页面能加载、
 *    但所有按钮都没有反应"的产物 (计划书 §2 F4 的构建组织问题)。
 */

import { air } from './air-global';
// 计划书 §4: 样式拆到 src/styles/ 并按层引入 (基础 → 工作台 → 版面细节),
// 由 Vite 打包成哈希化 CSS 产物, 页面里不再有内联 <style>。
import './styles/base.css';
import './styles/workbench.css';
import './styles/layout.css';
// 合并计划 §9.2: 团队状态条 / 角色卡片 / 任务表 (含窄屏响应式)
import './styles/team.css';
import './app';

export default air;
