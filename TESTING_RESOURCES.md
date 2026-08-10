# 测试与资源说明

本文件说明 AIR 中**引文核查**、**PDF 全文摄入**、**跨模型审阅**、**证据账本**、**成本追踪**、**研究 wiki** 等功能的测试方法、所需资源及数据源说明。

---

## 一、新增功能总览

```
流水线（更新后，STORM 式两阶段）:
start → literature_review → pdf_ingestion → citation_precheck → paper_writing → citation_check → paper_review
                                                                                                        ↓ (评分低 或 有虚构引用)
                                                                                                   increment_revision → paper_writing
                                                                                                        ↓ (通过)
                                                                                                   finalize (成本报告)
```

| 节点 | 功能 | 关键文件 |
|------|------|---------|
| `pdf_ingestion` | 下载论文 PDF → 解析全文（GROBID 可选）→ 分块 → 向量入库 | `src/tools/pdf_fetcher.py`, `src/rag/paper_parser.py`, `src/rag/chunker.py` |
| `citation_precheck` | **STORM 式预写作**：写作前验证候选引用清单，只允许引用已验证文献 | `src/agents/citation_prechecker.py` |
| `paper_writing` | 撰写（abstention：无证据标注 [缺证据]，不编造引用） | `src/agents/paper_writer.py` |
| `citation_check` | 提取引用 → CrossRef/OpenAlex/arXiv 三源验证 → 生成核查报告 + **证据账本** | `src/tools/citation_verifier.py`, `src/agents/citation_checker.py` |
| `paper_review` | 审阅（**跨模型**：可用独立 reviewer LLM） | `src/agents/paper_reviewer.py` |
| `finalize` | 成本/用量报告 | `src/utils/cost_tracker.py` |

### 改进来源（高星项目借鉴）

| 改进 | 来源项目 | 实现方式 |
|------|---------|---------|
| 跨模型审阅 | ARIS (14.2k⭐) | `REVIEWER_MODEL` 环境变量独立配置审阅模型 |
| STORM 式预写作 | STORM (30.8k⭐) | `citation_precheck` 节点先验证引用清单再写作 |
| 证据账本 | research-proof | `build_evidence_ledger` 为每条引用生成证据链 |
| 成本追踪 | deer-flow (79.2k⭐) | `cost_tracker.py` tiktoken 用量 + 价格表 |
| abstention | vetresearch-workbench | 无素材/证据时拒绝写作或标注 [缺证据] |
| 研究 wiki | ARIS | `save_to_wiki` / `search_wiki` 跨会话笔记 |
| GROBID 结构化解析 | papercast / scipdf_parser | 可选，`GROBID_BASE_URL` 配置 |

审阅循环新增判定：`should_continue_review` 不仅看评分，**只要有虚构引用（NOT_FOUND）就触发修订**，且修订 prompt 中强制要求删除/替换虚构引用。

---

## 二、如何测试

### 1. 离线单元测试（无需 API Key、无需网络）

```bash
cd E:\SearchAgents\AIR

# 引文验证工具测试（8 项）
python tests/test_citation_verifier.py

# PDF 工具测试（8 项）
python tests/test_pdf_tools.py

# 新功能测试（9 项：成本追踪/证据账本/引用预验证）
python tests/test_new_features.py

# 或者用 pytest
pip install pytest
python -m pytest tests/ -v
```

覆盖范围：
- 标题相似度计算（`_title_similarity`）
- 参考文献条目提取（数字编号格式 + BibTeX 格式）
- 文中引用覆盖率检查（`check_inline_citation_coverage`）
- CrossRef 验证逻辑（mock 网络）
- 虚构引用识别（mock 网络）
- arXiv ID 提取（新/旧格式）
- 文本分块（短文本/长文本/空文本/重叠）

### 2. 集成测试（需要网络）

```bash
# 测试 PDF 下载 + 解析 + 分块（真实 arXiv）
python -c "
import sys; sys.path.insert(0, '.')
from src.tools.pdf_fetcher import download_arxiv_pdf
from src.rag.paper_parser import extract_text_from_pdf
from src.rag.chunker import chunk_paper_fulltext

path = download_arxiv_pdf('https://arxiv.org/abs/1706.03762')  # Attention Is All You Need
print('PDF:', path)
text = extract_text_from_pdf(path, max_chars=30000)
chunks = chunk_paper_fulltext('Attention Is All You Need', text)
print(f'解析 {len(text)} 字符, {len(chunks)} 个分块')
"
```

```bash
# 测试引文验证（真实 CrossRef/OpenAlex/arXiv API）
python -c "
import sys; sys.path.insert(0, '.')
from src.tools.citation_verifier import verify_draft_citations

sample = '''
# 测试
Transformer [1] 和 BERT [2]。

## 参考文献
[1] Attention Is All You Need (2017)
[2] BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (2019)
'''
report = verify_draft_citations(sample)
print(report['report_md'])
"
```

预期结果：
- `Attention Is All You Need` → ✅ VERIFIED（相似度 1.0）
- `BERT: Pre-training...` → ✅ VERIFIED（通过 arXiv 源匹配）
- 虚构标题 → ⚠️ AMBIGUOUS 或 ❌ NOT_FOUND（不会误报为 VERIFIED）

### 3. 端到端流水线测试（需要 LLM API Key）

```bash
python -m src.main "基于大语言模型的对话系统综述" --keywords "LLM,对话系统" --subtopics "意图识别,响应生成"
```

观察输出：
- `[citation_precheck] 引用预验证: 可信 N, 未通过 M`（写作前先验证）
- `[pdf_ingestion] 全文入库: N 篇论文`
- `[citation_check] 引用核查: N 条, 已验证 X, 未找到 Y`
- `[paper_review] 审稿评分: N/50`（若配置了 `REVIEWER_MODEL` 则用独立模型审阅）
- `[finalize] 成本统计: $X.XX`
- 若 Y > 0，流水线自动进入修订循环

### 4. 跨模型审阅测试

```bash
# .env 中配置审阅模型（例如用 gpt-4o-mini 审阅 deepseek 写的论文）
REVIEWER_MODEL=gpt-4o-mini
REVIEWER_API_KEY=sk-xxx
REVIEWER_BASE_URL=https://api.openai.com/v1

# 然后运行流水线，观察 paper_review 阶段是否使用 reviewer 模型
```

### 5. GROBID 结构化解析测试（可选）

```bash
# 启动 GROBID 服务
docker run -t -p 8070:8070 lfoppiano/grobid:0.8.0

# .env 配置
GROBID_BASE_URL=http://localhost:8070

# 安装 scipdf-parser
pip install scipdf-parser

# 测试解析
python -c "from src.rag.paper_parser import extract_text_from_pdf; print(extract_text_from_pdf('data/pdfs/1706.03762.pdf', 1000))"
```

---

## 三、需要获取的资源

### 必须

| 资源 | 用途 | 获取方式 | 费用 |
|------|------|---------|------|
| OpenAI 兼容 LLM API Key | 撰写/审阅/核查的 LLM | DeepSeek / OpenAI / 硅基流动 / 智谱 等 | 按量付费 |
| 网络连接 | 访问 arXiv / CrossRef / OpenAlex API | — | 免费 |

### 建议（可选但推荐）

| 资源 | 用途 | 获取方式 | 费用 |
|------|------|---------|------|
| 第二个 LLM API Key（`REVIEWER_*`） | **跨模型审阅**（不同 provider 的模型做审阅，避免认知盲区） | 任一其他服务商 | 按量付费 |
| Embedding API（如 `text-embedding-3-small` 或硅基流动 bge 模型） | 向量检索（ChromaDB） | 同上，与 LLM 同一服务商或单独 | 按量付费 |
| `CROSSREF_EMAIL` 环境变量 | 进入 CrossRef polite pool，提高速率限制 | 任意邮箱即可 | 免费 |

### 可选（高级功能）

| 资源 | 用途 | 获取方式 | 费用 |
|------|------|---------|------|
| GROBID 服务 | PDF 结构化解析（标题/摘要/正文分离） | `docker run -p 8070:8070 lfoppiano/grobid:0.8.0` | 免费 |
| Langfuse 账号 | LLM 调用追踪/可视化 | https://cloud.langfuse.com 注册 | 免费额度 |

### 无需获取（完全免费，无需 Key）

- **arXiv API**: `https://export.arxiv.org/api/query` — 论文元数据 + PDF 下载
- **CrossRef API**: `https://api.crossref.org/works` — DOI/题录验证
- **OpenAlex API**: `https://api.openalex.org/works` — 备选验证源

### 依赖安装

```bash
cd E:\SearchAgents\AIR
pip install -r requirements.txt   # 全部核心依赖
pip install pymupdf               # PDF 解析（重要）
cp .env.example .env              # 然后填入 OPENAI_API_KEY
```

---

## 四、参考开源项目

| 项目 | 借鉴点 |
|------|--------|
| [opendraft](https://github.com/federicodeponte/opendraft) | 引用必须经 CrossRef/OpenAlex/arXiv 验证后才进参考文献 |
| [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) | **跨模型审阅循环**、研究 wiki 记忆、完整性取证 |
| [STORM](https://github.com/stanford-oval/storm) | **预写作阶段先收集验证引用再写正文**、多视角提问 |
| [deer-flow](https://github.com/bytedance/deer-flow) | **成本估算**、checkpoint 断点续跑、上下文工程 |
| [research-proof](https://github.com/tonyblu331/research-proof) | **证据账本**：每个 claim 有可复核的证据链 |
| [vetresearch-workbench](https://github.com/Funluned/vetresearch-workbench) | **abstention 机制**：无证据时拒绝编造 |
| [papercast](https://github.com/papercast-dev/papercast) | arXiv 下载 → GROBID/LangChain 解析流水线 |
| [scipdf_parser](https://github.com/titipata/scipdf_parser) | 科学文献 PDF 结构解析（GROBID 客户端） |

---

## 五、已知限制

1. **GROBID 为可选**：未配置 `GROBID_BASE_URL` 时用 PyMuPDF 纯文本解析，无章节结构分离。配置后自动升级为结构化解析。
2. **阈值权衡**：相似度 ≥ 0.7 才判定 VERIFIED，≥ 0.4 为 AMBIGUOUS。真实论文若标题表达差异大可能被标为 AMBIGUOUS（需人工确认），这是有意为之（宁可存疑，不可误放虚构引用）。
3. **CrossRef 覆盖局限**：CrossRef 对 NeurIPS/ICLR 等会议论文集收录不全（ACM DOI 不在此库），因此设计了三源验证（CrossRef → OpenAlex → arXiv）兜底。
4. **扫描 PDF 无法解析**：纯图片扫描的旧论文 PDF 提取不到文本，会被跳过（在 ingestion 报告中记录）。
5. **限流**：Semantic Scholar 匿名调用限 1 QPS；arXiv/CrossRef/OpenAlex 一般够用，大规模检索建议在 `.env` 设置 `CROSSREF_EMAIL` 进入 polite pool。
6. **跨模型审阅需第二个 API Key**：未配置 `REVIEWER_MODEL` 时回退为主模型审阅（失去认知多样性优势）。
7. **Langfuse 集成未接入**：`LANGFUSE_*` 配置已定义但代码未挂钩（下一步可接 langchain 的 LangfuseCallbackHandler）。
8. **成本为估算**：`MODEL_PRICES` 为每百万 token 价格表，实际价格可能随服务商变动，需定期更新。
