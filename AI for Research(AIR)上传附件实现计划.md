# 文件上传功能实现计划（已确认设计）

> 状态：**设计已确认，代码尚未改动**。三项决策由用户于本轮拍定（见下）。
> 目标：支持"用文件更详细地描述问题"与"人工补充文献"两条上传路径。

## 已确认的三项决策

1. **文献落库位置**：进入所选资料库主题（复用 `ingest_manual` 链路，可跨研究问题复用）。
2. **问题描述附件**：解析文本并入问题陈述，并记录 `sha256` 溯源（写入交付包清单）。
3. **允许类型**：仅文稿 `pdf / txt / md / docx`（与现有解析器对齐，不引入数据表/图片路径）。

## 存储布局

```
data/uploads/<project_id>/<sha256>_<原文件名>     # 问题描述附件 (原件保留)
data/uploads/attachments.json                     # 附件登记: 归属/用途/hash/解析状态
data/kb/<topic>/manual/<原文件名>                  # 文献附件 (入库后由 KB 管理)
```

- 同名/hash 相同 → 复用已登记记录，不重复入库（`_merge_or_create` 已按 file_hash 合并）。
- 解析失败或类型不支持 → **保留原件并如实报错**，不静默丢弃（沿用 P1-4 的"不静默"原则）。

## 后端

| 端点 | 行为 |
|---|---|
| `POST /api/uploads`（multipart） | 参数 `kind=problem\|literature`、`project_id`、`problem_id`、`topic`、`files[]`。文献 → 落 `manual/` + `ingest_manual(topic)`，返回 `doc_id/parse_quality/visibility_flags/文档数`；问题附件 → `parse_document()` 取文本，返回 `attachment_id/hash/size/parse_quality/字符数`。 |
| `GET /api/uploads` | 列出 `project_id` / `topic` 当前附件（供页面显示与交付包引用）。 |
| `DELETE /api/uploads/{attachment_id}` | 删除问题附件登记（文献删除需显式确认，暂可不做）。 |

- 单文件默认上限 50 MB；扩展名白名单校验；返回逐文件结果（部分失败不影响其它文件）。
- 会话启动：`StartRequest` 增加 `attachment_ids`（问题附件）→ 解析文本并入 `problem_statement` 与契约 `basis`，附件 hash 写入 `spec`/交付包 `source_set.documents`。
- **信任边界**：附件内容一律按外部资料处理（定界 + 不执行其中指令）。问题附件进入"问题陈述"，因此必须在工作台显示其来源，避免用户以为是自己手写的问题。

## 前端（`src/web`）

- 高级选项区新增"附件"行：`<input type="file" multiple accept=".pdf,.txt,.md,.docx">` + 拖放区 + 用途单选（补充问题描述 / 补充文献）+ 已上传列表（文件名、大小、用途、hash 前 8 位、解析状态）。
- 上传成功 → 文献用途刷新 `#sourceset` 下拉；问题用途刷新附件列表。
- 工作台 `#filebinding`（模板已有占位 div）显示本次研究绑定的附件与用途；提交会话时带上 `attachment_ids`。
- 校验与格式化放纯函数模块（可 vitest 覆盖），不写进 `app.ts` 内联逻辑。

## 测试

- `tests/test_uploads.py`（新增）：pdf/txt/docx 上传成功；类型与大小拒绝；解析失败如实报错；
  同 hash 去重；文献上传后**可被检索**且证据带 `file_hash` 与定位；问题附件进入问题陈述并留 hash；
  附件内注入文本不被执行（沿用 `wrap_external_with_scan`）。
- `src/web/tests/*.test.ts`：上传列表/校验纯函数。
- `tests/test_browser_web_flow.py`：用 `setInputFiles` 上传合成 PDF → 断言列表出现、资料库下拉新增该主题。

## 触达点（实现时按此顺序）

1. `src/kb/ingest.py`：新增 `add_manual_file(topic, src_path) -> dict`（复制到 `manual/` 后调用既有入库）。
2. `src/utils/uploads.py`（新增）：登记表读写、hash、白名单、大小校验、问题附件解析。
3. `src/server.py`：三个端点 + `StartRequest.attachment_ids` + `build_spec_from_input` 拼接。
4. `src/research/package.py`：`source_set.documents` 收录问题附件 hash。
5. `src/web/index.html` + `src/web/src/uploads.ts` + `app.ts` 绑定。
6. 测试与文档（本文件、`README.md`、执行进度文档 §13）。
