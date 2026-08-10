from __future__ import annotations

import logging
import os

from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

# 优先使用 langchain_chroma (新包, 无弃用警告), 回退 langchain_community
try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma

from src.config import LLM_CONFIG, CHROMA_CONFIG

logger = logging.getLogger(__name__)

# Embedding 是否可用的全局标记（避免重复探测日志刷屏）
_EMBEDDING_CHECKED = False
_EMBEDDING_OK = True


def get_embeddings() -> OpenAIEmbeddings:
    global _EMBEDDING_CHECKED, _EMBEDDING_OK

    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    base_url = os.getenv("EMBEDDING_BASE_URL", LLM_CONFIG["base_url"])
    api_key = os.getenv("EMBEDDING_API_KEY", LLM_CONFIG["api_key"])

    embeddings = OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=base_url,
        # 关键: 不传 dimensions 参数 (bge 等模型不支持, 硅基流动返回 400)
        dimensions=None,
        # 跳过 tiktoken 长度检查: 对中文/未知模型计算可能异常,
        # 且长度检查路径会引入非标准请求参数 (直接走标准 embedding 请求)
        check_embedding_ctx_length=False,
    )

    # 首次使用时探测 embedding 可用性（DeepSeek 等服务商无 embedding API）
    if not _EMBEDDING_CHECKED:
        _EMBEDDING_CHECKED = True
        try:
            embeddings.embed_query("connectivity test")
            _EMBEDDING_OK = True
        except Exception as e:
            _EMBEDDING_OK = False
            print(
                f"  [警告] Embedding API 不可用 ({e.__class__.__name__})。"
                f"RAG 向量检索/研究wiki将降级为不可用，但主流程仍可运行。\n"
                f"  若需启用 RAG：请配置支持 embedding 的服务商，例如在 .env 设置\n"
                f"  EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5\n"
                f"  EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1\n"
                f"  EMBEDDING_API_KEY=sk-xxx"
            )
    return embeddings


def embedding_available() -> bool:
    """Embedding 是否可用（用于调用方决定是否降级）"""
    global _EMBEDDING_CHECKED, _EMBEDDING_OK
    if not _EMBEDDING_CHECKED:
        _EMBEDDING_CHECKED = True
        try:
            embeddings = get_embeddings()
            embeddings.embed_query("connectivity test")
            _EMBEDDING_OK = True
        except Exception as e:
            _EMBEDDING_OK = False
            print(
                f"  [警告] Embedding API 不可用 ({e.__class__.__name__})。"
                f"RAG 向量检索/研究wiki将降级为不可用，但主流程仍可运行。\n"
                f"  若需启用 RAG：请配置支持 embedding 的服务商，例如在 .env 设置\n"
                f"  EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5\n"
                f"  EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1\n"
                f"  EMBEDDING_API_KEY=sk-xxx"
            )
    return _EMBEDDING_OK


def get_vector_store(collection_name: str = None) -> Chroma:
    embeddings = get_embeddings()
    name = collection_name or CHROMA_CONFIG["collection_name"]
    return Chroma(
        collection_name=name,
        embedding_function=embeddings,
        persist_directory=CHROMA_CONFIG["persist_directory"],
    )


def add_papers_to_store(papers: list[dict], collection_name: str = None) -> int:
    if not embedding_available():
        return 0
    store = get_vector_store(collection_name)
    docs = []
    for p in papers:
        content = f"Title: {p.get('title', '')}\nAuthors: {p.get('authors', '')}\n"
        content += f"Year: {p.get('year', '')}\nAbstract: {p.get('abstract', '')}"
        metadata = {
            "title": p.get("title", ""),
            "authors": p.get("authors", ""),
            "year": p.get("year", ""),
            "source": p.get("source", ""),
            "citations": p.get("citations", 0),
            "url": p.get("url", ""),
            "bibtex": p.get("bibtex", ""),
        }
        docs.append(Document(page_content=content, metadata=metadata))
    
    ids = store.add_documents(docs)

    return len(ids)


def similarity_search(
    query: str, k: int = 10, collection_name: str = None
) -> list[dict]:
    if not embedding_available():
        return []
    store = get_vector_store(collection_name)
    results = store.similarity_search(query, k=k)
    return [
        {
            "title": d.metadata.get("title", ""),
            "authors": d.metadata.get("authors", ""),
            "year": d.metadata.get("year", ""),
            "source": d.metadata.get("source", ""),
            "abstract": d.page_content.split("Abstract: ")[-1] if "Abstract:" in d.page_content else d.page_content,
            "citations": d.metadata.get("citations", 0),
            "url": d.metadata.get("url", ""),
            "bibtex": d.metadata.get("bibtex", ""),
        }
        for d in results
    ]


def clear_collection(collection_name: str = None) -> None:
    store = get_vector_store(collection_name)
    store.delete_collection()



def _add_documents_resilient(store, docs: list[Document]) -> tuple[int, int]:
    """递归降级入库: 批量失败 → 对半拆分重试; 单条失败 → 截断重试

    背景: 硅基流动 bge 对超 512 token 的文本返回 400 (code 20015),
    纯中文论文的块可能超限, 且一次批量提交中一个坏块会毁掉整篇论文。

    Returns: (成功条数, 失败条数)
    """
    if not docs:
        return 0, 0
    try:
        store.add_documents(docs)
        return len(docs), 0
    except Exception as e:
        if len(docs) == 1:
            # 单条失败: 先截断再试 (宁可丢尾部, 不可丢整篇)
            d = docs[0]
            text = d.page_content
            if len(text) > 300:
                truncated = Document(page_content=text[:300], metadata=dict(d.metadata))
                try:
                    store.add_documents([truncated])
                    logger.warning(
                        f"块超长截断后入库: {d.metadata.get('title', '')[:40]} "
                        f"({len(text)}→300 字符)"
                    )
                    return 1, 0
                except Exception as e2:
                    logger.warning(
                        f"块入库失败(截断后仍失败): {d.metadata.get('title', '')[:40]} | {e2}"
                    )
                    return 0, 1
            logger.warning(f"块入库失败: {d.metadata.get('title', '')[:40]} | {e}")
            return 0, 1
        # 批量失败: 对半拆, 定位坏块
        mid = len(docs) // 2
        ok_l, fail_l = _add_documents_resilient(store, docs[:mid])
        ok_r, fail_r = _add_documents_resilient(store, docs[mid:])
        return ok_l + ok_r, fail_l + fail_r


def add_fulltext_chunks(
    chunks: list[dict],
    collection_name: str = "papers_fulltext",
    paper_meta: dict = None,
) -> int:
    """将论文全文分块入库（向量检索到原文段落）

    chunks: [{title, chunk_index, text}, ...]
    paper_meta: 论文元数据 (title/authors/year/url/bibtex)

    单块超长/批量失败时自动递归降级, 不影响整篇入库。
    """
    if not embedding_available():
        return 0
    store = get_vector_store(collection_name)
    docs = []
    meta = paper_meta or {}
    for c in chunks:
        content = (
            f"Title: {c['title']}\n"
            f"Chunk {c['chunk_index']}:\n{c['text']}"
        )
        metadata = {
            "title": c["title"],
            "chunk_index": c["chunk_index"],
            "authors": meta.get("authors", ""),
            "year": meta.get("year", ""),
            "source": meta.get("source", ""),
            "url": meta.get("url", ""),
            "bibtex": meta.get("bibtex", ""),
        }
        docs.append(Document(page_content=content, metadata=metadata))

    ok_count, fail_count = _add_documents_resilient(store, docs)
    if fail_count:
        logger.warning(f"add_fulltext_chunks: {ok_count} 成功, {fail_count} 失败")
    return ok_count


def search_fulltext(
    query: str,
    k: int = 8,
    collection_name: str = "papers_fulltext",
) -> list[dict]:
    """从论文全文向量库检索相关段落"""
    if not embedding_available():
        return []
    store = get_vector_store(collection_name)
    results = store.similarity_search(query, k=k)
    return [
        {
            "title": d.metadata.get("title", ""),
            "chunk_index": d.metadata.get("chunk_index", 0),
            "authors": d.metadata.get("authors", ""),
            "year": d.metadata.get("year", ""),
            "source": d.metadata.get("source", ""),
            "url": d.metadata.get("url", ""),
            "text": d.page_content,
        }
        for d in results
    ]


def search_fulltext_by_title(
    title: str,
    k: int = 2,
    collection_name: str = "papers_fulltext",
) -> list[dict]:
    """按论文标题检索其自己的全文段落（写作证据绑定用）

    优先 metadata 精确过滤；若无命中回退到向量检索 + 归一化标题相似度过滤。
    返回段落带 title/url 元数据，供 paper_writer 组装「编号→论文→原文段落」证据映射。
    """
    import re as _re

    if not embedding_available():
        return []
    store = get_vector_store(collection_name)

    docs = []
    try:
        docs = store.similarity_search(
            title, k=max(k * 3, 6), filter={"title": title}
        )
    except Exception:
        docs = []
    if not docs:
        try:
            docs = store.similarity_search(title, k=max(k * 3, 6))
        except Exception:
            return []

    def _norm(s: str) -> str:
        return _re.sub(r"[^a-z0-9]", "", (s or "").lower())

    target = _norm(title)
    ranked = []
    for d in docs:
        dtitle = _norm(d.metadata.get("title", ""))
        if target and dtitle and target in dtitle or (dtitle and dtitle in target):
            ranked.append(d)
        elif target and dtitle:
            # 模糊兜底: 公共前缀覆盖超过 60%
            common = len(set(target) & set(dtitle))
            ratio = common / max(len(set(target) | set(dtitle)), 1)
            if ratio >= 0.6:
                ranked.append(d)

    results = ranked[:k] if ranked else docs[:1]
    return [
        {
            "title": d.metadata.get("title", ""),
            "chunk_index": d.metadata.get("chunk_index", 0),
            "authors": d.metadata.get("authors", ""),
            "year": d.metadata.get("year", ""),
            "source": d.metadata.get("source", ""),
            "url": d.metadata.get("url", ""),
            "text": d.page_content,
        }
        for d in results
    ]


def save_to_wiki(topic: str, notes: str, collection_name: str = None) -> int:
    """研究 wiki 记忆：按主题保存跨会话的研究笔记

    参考: ARIS 的 research-wiki 设计 — 跨会话维护研究知识，避免长任务记忆丢失
    """
    if not embedding_available():
        return 0
    name = collection_name or CHROMA_CONFIG["wiki_collection"]
    store = get_vector_store(name)
    doc = Document(
        page_content=f"Topic: {topic}\n{notes}",
        metadata={"topic": topic, "type": "research_note"},
    )
    ids = store.add_documents([doc])

    return len(ids)


def search_wiki(query: str, k: int = 3, collection_name: str = None) -> list[dict]:
    """从研究 wiki 检索历史笔记"""
    if not embedding_available():
        return []
    name = collection_name or CHROMA_CONFIG["wiki_collection"]
    try:
        store = get_vector_store(name)
        results = store.similarity_search(query, k=k)
        return [
            {
                "topic": d.metadata.get("topic", ""),
                "text": d.page_content,
            }
            for d in results
        ]
    except Exception:
        return []
