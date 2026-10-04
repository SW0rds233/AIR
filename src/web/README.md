# AIR 前端 (计划书 §2 F3/F4)

单页应用, 保留原生浏览器界面, 用 **Vite + TypeScript** 组织。服务端
(`src/server.py`) 提供构建产物, 不再从 CDN 加载任何脚本。

## 目录

```text
src/web/
├─ index.html            # 源码模板 (Vite 入口, 含 DOM 结构与样式)
├─ package.json          # 前端依赖与脚本 (vite / typescript / vitest / jsdom)
├─ tsconfig.json         # 严格模式 + 仅类型检查 (noEmit)
├─ vite.config.ts        # 构建配置: 输出 dist/ 并把 assets/ 发布到源码目录
├─ src/
│  ├─ main.ts            # 组合模块, 暴露 window.AIR (状态 + HTTP 契约 + 渲染)
│  ├─ current-research.ts# 旧状态 (项目/问题/会话/模式/状态); 迁移期**只被单向读取**
│  ├─ research-api.ts    # 会话/工作台/反馈/派生/产物请求 (URL 与重试只有一份实现)
│  ├─ markdown.ts        # 安全渲染: 只用 DOM API, 链接协议白名单
│  ├─ app.ts             # 页面逻辑 (从原内联脚本迁出; 仍在写路径上, 拆分见下)
│  ├─ team-controller.ts # 团队视图接入: 只读拉取 /api/team/*, 迟到响应丢弃
│  ├─ state/
│  │  └─ research-store.ts  # **唯一状态入口**: selection/entities/transport/ui + reducer + selectors
│  ├─ events/
│  │  └─ session-events.ts  # 会话级游标、seq 去重、缺口重同步、有上限退避重连
│  ├─ views/
│  │  └─ team-board.ts   # 团队状态条 / 任务表 / 角色真实能力 / 审阅面板 (纯函数)
│  └─ styles/            # base / workbench / layout / team (按层引入)
├─ assets/               # 构建产物 (提交入库, server.py 从这里提供)
├─ dist/                 # Vite 临时输出目录 (git 忽略)
└─ tests/                # Vitest 单元测试 (jsdom) + e2e/ (Playwright, 真实 Chromium)
```

## 状态与事件归属 (合并计划 §9.3)

`state/research-store.ts` 是**唯一**状态入口, 按归属切成四片:

| 片 | 内容 | 约束 |
|---|---|---|
| `selection` | 当前 project/problem/session/run 与所选对象、当前标签 | 切换会话时**整体替换**; 不保留旧 run 的对象 |
| `entities` | 任务、角色能力、决策序列、对象计数、问题账本 (**按 run 分区**) | 不同类型 run 不互相覆盖 |
| `transport` | 连接状态、**每会话**事件游标、错误、重连次数、是否需要重同步 | 与后台运行状态**分开**: 离线 ≠ 已停止 |
| `ui` | 展开项、筛选、未发送草稿、加载代号、提示 | **不能回写命题状态** |

关键不变量（都有单测）:

- **迟到响应丢弃**: 每次加载带 `loadToken`, 只有 token 与身份都匹配的结果才被应用;
- **事件去重与缺口**: 按 `seq` 推进游标, 收到重复直接丢弃; 出现缺口或**未知事件类型**时
  置 `needsResync` 并要求重新加载投影 —— 不允许把缺口当"完成";
- **重连有界**: 指数退避 + 抖动 + 次数上限; 运行终止或切换会话后取消重连。

## 构建与测试

```bash
cd src/web
npm install          # 首次
npm run build        # 产出 dist/ 并把 assets/ 发布到 src/web/assets/
npm test             # vitest (jsdom)
npm run typecheck    # tsc --noEmit
```

构建后, `src/server.py` 会：

- `GET /` → 优先提供 `dist/index.html` (未构建时回退源码模板, 仅供 `vite dev`);
- `GET /assets/*` → 只从 `src/web/assets/` 提供, 并做路径越界检查。

CSP (`script-src 'self'`) 不允许内联脚本, 因此页面逻辑必须留在 `app.ts` 等模块里 ——
这也是把原内联 `<script>` 迁出的原因。

## 产物级校验

`tests/js/bundle_security.test.js` 直接加载构建后的 bundle, 用最小 DOM 桩验证它
暴露的 `AIRMarkdown` 仍只生成白名单标签与安全协议链接（源码测试通过不等于打包后
行为不变）。由 `tests/test_web_frontend.py` 在 Python 测试里调用。

## 迁移期边界 (不要当成已完成)

- `app.ts` **尚未**按合并计划 §9.5 拆成 `session-controller.ts` / `features/intake/` /
  `views/*`, 仍在写路径上;
- `current-research.ts` 的旧状态仍在用, 团队层只做了**单向**映射到 `selection`
  (禁止双向同步两份副本);
- `contracts.ts` 与 `current-research.ts` 的 `RunMode/RunStatus` 重复定义未去重;
- `@ts-nocheck` 未移除; 论文/公式/图表追溯视图 (F3–F5) 未做;
- `src/web/dist` 与 `src/web/assets` 的**原子发布**未做（构建时直接覆盖, 运行中的页面
  可能加载到不匹配的 HTML/资源）。
