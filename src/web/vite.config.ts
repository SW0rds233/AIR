import { defineConfig } from 'vite';
import { existsSync, readdirSync, copyFileSync, mkdirSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { basename, dirname, join, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * 计划书 §2 F4: 用 Vite + TypeScript 组织前端, 构建产物由 FastAPI 提供。
 *
 * 产物布局 (源码目录保持"输入"语义, 不写入构建产物):
 * - `src/web/dist/`      构建输出 (构建后复制 assets, 再删除; 已被 .gitignore 忽略)
 * - `src/web/assets/`    构建后的 JS/CSS (提交进版本库, server.py 直接服务)
 * - `src/web/dist/index.html` 已构建页面, 由 server.py 优先提供
 *
 * 为什么不把构建结果写回 `src/web/index.html`: 那会让"源码"与"产物"混为一谈,
 * 第二次构建会把自己注入的 `/assets/...` 标签当成源码 import 而失败。
 */
export default defineConfig({
  root: here,
  base: '/',
  // 开发模式 (`start.bat dev`): vite 提供源码页面 (5173), /api 反向代理到后端 (8000)。
  // 没有它时源码模板里的 `/api/...` 会打到 vite 自己身上, 页面打开却读不到任何数据。
  server: {
    // 显式绑定 IPv4: 默认只监听 localhost, 在部分 Windows 上解析到 ::1 时
    // 浏览器打开 http://127.0.0.1:5173 会被拒绝 (与后端 127.0.0.1:8000 也不一致)。
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: {
    outDir: resolve(here, 'dist'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: resolve(here, 'index.html'),
    },
  },
  plugins: [
    {
      name: 'air-publish-assets',
      writeBundle() {
        const assets = resolve(here, 'dist', 'assets');
        const target = resolve(here, 'assets');
        if (!existsSync(assets)) {
          throw new Error('构建未生成 dist/assets');
        }
        mkdirSync(target, { recursive: true });
        // 复制**全部**产物 (JS 与 CSS): 只复制 .js 会让样式产物留在 dist/ 里,
        // 而服务端只提交/提供 src/web/assets/ —— 拆分样式后页面会突然没有样式。
        const stale = existsSync(target) ? readdirSync(target) : [];
        stale.forEach((name) => {
          if (name.endsWith('.js') || name.endsWith('.css')) {
            // 旧哈希产物不再被引用, 先清掉避免仓库里堆死文件
            try { unlinkSync(join(target, name)); } catch { /* 忽略 */ }
          }
        });
        readdirSync(assets).forEach((name) => {
          copyFileSync(join(assets, name), join(target, basename(name)));
        });
      },
    },
  ],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
  },
});
