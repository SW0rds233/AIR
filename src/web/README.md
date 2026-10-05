# AIR 前端

前端源码在 `src/`，使用 Vite + TypeScript。`index.html` 是开发用模板，不能直接作为生产页面提供；生产页面使用 `dist/index.html` 与 `assets/` 中的构建资源。

## 构建

在本目录执行：

```bash
npm ci
npm run typecheck
npm run build
```

`vite.config.ts` 会把带内容哈希的 JS/CSS 发布到 `assets/`。后端 `src/server.py` 提供 `dist/index.html` 和 `/assets/*`；缺少 `dist/index.html` 时首页返回构建提示。

传输包中附带依据当前已构建 JS/CSS 生成的 `dist/index.html`，可直接启动。修改 `src/`、`index.html` 或构建配置后，必须重新运行 `npm run build`。`start.bat` 会检查是否需要重建。

本次精简移除了 `tests/`、`node_modules/` 和临时构建文件；测试代码可从清理前的 Git 历史恢复，依赖可通过 `npm ci` 重新安装。
