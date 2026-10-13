/** 浏览器启动入口。 */

import { air } from './air-global';
// 样式拆到 src/styles/ 并按层引入 (基础 → 工作台 → 版面细节),
// 由 Vite 打包成哈希化 CSS 产物, 页面里不再有内联 <style>。
import './styles/base.css';
import './styles/workbench.css';
import './styles/layout.css';
// 团队状态条 / 角色卡片 / 任务表 (含窄屏响应式)
import './styles/team.css';
import './app';

export default air;
