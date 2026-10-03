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
│  ├─ current-research.ts# 当前项目/问题/会话/模式/状态 (纯函数, 可直接测试)
│  ├─ research-api.ts    # 会话/工作台/反馈/派生/产物请求 (URL 与重试只有一份实现)
│  ├─ markdown.ts        # 安全渲染: 只用 DOM API, 链接协议白名单
│  └─ app.ts             # 页面逻辑 (从原内联脚本迁出; 尚未类型化)
├─ assets/               # 构建产物 (提交入库, server.py 从这里提供)
├─ dist/                 # Vite 临时输出目录 (git 忽略)
└─ tests/                # Vitest 单元测试 (jsdom)
```

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
