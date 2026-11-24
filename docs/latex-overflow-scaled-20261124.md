# 2026-11-24 长展示公式溢出：按行宽缩放

检查对象：`outputs/research/proj-mwu2z139gl5h/` 的
`研究中国星网系统的建设可能给中国现代空战_20261124_103558_a479`，快照 `snap-3ececb4f`。
该轮 `delivery_level=条件性研究报告`，四个 gate 全 False，审阅 70/80（`major_revision`），
交付件是 `manuscript.failed.pdf`（249,872 B）。

## 根因与修复

1. **失败不是语法错误。** `manuscript.log` 没有一条以 `!` 开头的错误，PDF 已经生成；
   卡的是排版闸门：`manuscript.log:974`
   `Overfull \hbox (50.85374pt too wide) detected at line 128`，
   而 `src/publication/compiler.py` 的 `OVERFULL_BLOCKING_PT = 24.0` 判定交付失败，
   产物改名 `manuscript.failed.pdf`。
2. **溢出的是一条断不了行的单行公式。** `manuscript.tex` 第 128 行是一条 222 字符的
   `\tau_{\max}` 公式，顶层断点只有 `\quad`（位置 113）与 `=`（132），
   两者都在 `_wrap_math_row` 的 `[start+25, start+limit]` 窗口（`[25, 100]`）之外，
   于是 `candidates` 为空、函数原样返回一行。整篇满足“≥160 字符且单行”的公式只有这一条。
3. **现在不能安全断行时按行宽缩放。** 新增 `_MATH_ROW_LIMIT = 100`；`_display_math`
   在断行之后仍存在超限行时改走 `_scaled_display`：

   ```latex
   \resizebox{\ifdim\width>\linewidth\linewidth\else\width\fi}{!}{$\displaystyle <body>$}
   ```

   用 `\width` 判断自然宽度，只在真的过宽时收缩：实测窄公式 46.49802pt → 46.49942pt
   （不动），这条公式 474.91408pt → `\linewidth` 424.06033pt，差额 50.85375pt 正是
   日志里的 50.85374pt。`\tag` 属于公式层，提到盒子外面，避免 amsmath 在盒内报错。
   没有提高 `OVERFULL_BLOCKING_PT`，验收阈值不变。

## 验证

- 快照原 `manuscript.tex` 直接编译：`ok=False`、`50.85374pt`、`severity=error`；
  只把第 128 行那一条换成缩放写法后编译：`ok=True`、0 处溢出、`severity=ok`、
  PDF 249,913 B —— 同一文件内只改一行即可过闸，因果关系确定。
- 用同一份 `progress/manuscript.json` 第 4 版重新渲染：2 处 `\resizebox`
  （另一处是香农容量公式，原本未溢出，靠 `\width` 判断保持原尺寸）、5 处 `aligned`；
  `compile_latex` 返回 `ok=True`、`max_overfull_pt=0.0`、0 条 `!`、PDF 243,515 B。
- 回归 `proj-mwalpf145gq3` 的手稿：`ok=True`、0 处溢出（PR #10/#11 的断行修复未被破坏）。
- `tests/test_pdf_compilation.py`：30 passed，含新增 3 条 —— 不可断行长公式走缩放、
  短公式不套盒子、`\tag` 留在盒外。
- 全量（排除 `tests/test_browser_web_flow.py`）：525 passed、4 skipped、5 subtests。
  `tests/test_browser_web_flow.py` 整篇运行时会 `page.goto(networkidle)` 超时，
  进程随后以 `0x80000003` 中止；单独运行该文件 5.14s 通过，且把本次改动 stash 掉
  整篇运行同样中止 —— 与本改动无关，属本机浏览器用例的老问题。

## 边界

- 模型自己写的 `aligned`/`cases` 等环境仍不缩放（`\resizebox` 不能包住环境体），
  其中单行过宽时仍会触发溢出闸门；这条留给后续按行处理。
- 盒子按比例缩字号（本例约 0.89），不改变行距、外框与编号；只在自然宽度超过
  `\linewidth` 时生效，正常公式的排版不受影响。
