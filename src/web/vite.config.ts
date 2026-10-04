import { defineConfig } from 'vite';
import {
  existsSync, readdirSync, readFileSync, copyFileSync, mkdirSync, renameSync, unlinkSync,
} from 'node:fs';
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
      /**
       * 产物发布必须是**原子**的 (合并计划 §9.5 "构建发布")。
       *
       * 为什么不能"先删旧的再拷新的": 服务端在发布过程中随时可能响应用户 ——
       * 旧哈希文件一旦被删、新文件还没拷全, 页面拿到的 `index.html` 会引用一个
       * 不存在的脚本/CSS (表现为"样式没了/按钮全不响应")。
       *
       * 这里的做法利用一个事实: 产物名带内容哈希, **新旧文件名必然不同**, 因此
       * 可以让两代产物同时存在 —— 先把新文件全部拷进去 (此时旧文件仍在, 页面
       * 无论引用哪一代都能加载), 再删掉本代不再被引用的旧文件。任何时刻磁盘上
       * 都有一份完整可用的产物。
       */
      writeBundle() {
        const assets = resolve(here, 'dist', 'assets');
        const target = resolve(here, 'assets');
        if (!existsSync(assets)) {
          throw new Error('构建未生成 dist/assets');
        }
        mkdirSync(target, { recursive: true });
        // 1) 先写入本代全部产物 (不删任何东西; 同名文件是同一份内容)
        const fresh = readdirSync(assets).filter((name) => !name.startsWith('.'));
        fresh.forEach((name) => {
          // 先写临时名再改名: 对**同名**文件也保证读者不会看到半个文件
          const staging = join(target, `.${basename(name)}.tmp`);
          copyFileSync(join(assets, name), staging);
          renameSync(staging, join(target, basename(name)));
        });
        // 2) 再清理不再被本代页面引用的旧产物 (此时代替品已经就位)
        const freshSet = new Set(fresh.map((name) => basename(name)));
        const builtIndex = resolve(here, 'dist', 'index.html');
        const referenced = existsSync(builtIndex)
          ? readFileSync(builtIndex, 'utf-8')
          : '';
        readdirSync(target).forEach((name) => {
          if (!/\.(js|css)$/.test(name)) return;
          const stillReferenced = referenced.includes(basename(name));
          if (!freshSet.has(name) && !stillReferenced) {
            try { unlinkSync(join(target, name)); } catch { /* 忽略 */ }
          }
        });
      },
    },
  ],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
  },
});
