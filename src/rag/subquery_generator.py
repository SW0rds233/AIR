"""子查询生成器

借鉴: gpt-researcher 的 generate_sub_queries — LLM 把主主题拆成多个
中英文、多角度的搜索子查询，大幅提升检索覆盖（解决"只搜中文"问题）。
"""

from __future__ import annotations

import re
import logging

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.config import LLM_CONFIG

logger = logging.getLogger(__name__)

SUBQUERY_SYSTEM = """你是检索查询规划专家。根据研究主题和关键词，生成适合学术检索的子查询列表。

要求:
1. 生成 4-8 个子查询
2. **中英文都要有**（英文至少一半），覆盖不同角度
3. 每个子查询是独立的搜索词组合（不用引号、不用布尔操作符）
4. 涵盖: 核心概念、具体方法、应用场景、相关术语变体

只输出 JSON 数组, 如:
["射频指纹 设备识别", "RF fingerprinting device identification", "wireless device authentication", "physical layer fingerprint deep learning"]
"""


def generate_sub_queries(
    topic: str,
    user_keywords: str = "",
    llm=None,
    max_queries: int = 8,
) -> list[str]:
    """生成中英文子查询列表（失败时回退为主题+关键词）"""
    if llm is None:
        llm = ChatOpenAI(
            model=LLM_CONFIG["model"],
            api_key=LLM_CONFIG["api_key"],
            base_url=LLM_CONFIG["base_url"],
            temperature=LLM_CONFIG["temperature"],
        )

    try:
        prompt = (
            f"研究主题: {topic}\n"
            f"关键词: {user_keywords or '无'}\n\n"
            f"请生成 {max_queries} 个中英文子查询。"
        )
        result = llm.invoke(
            [SystemMessage(content=SUBQUERY_SYSTEM), HumanMessage(content=prompt)]
        )
        text = result.content if hasattr(result, "content") else str(result)

        # 提取 JSON 数组
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            import json

            queries = json.loads(m.group(0))
            queries = [str(q).strip() for q in queries if str(q).strip()]
            if queries:
                return queries[:max_queries]
    except Exception as e:
        logger.warning(f"子查询生成失败, 回退默认: {e}")

    # 回退: 主题 + 关键词拆分 (按逗号, 复合词保持原子)
    fallback = [topic]
    if user_keywords:
        for k in re.split(r"[,，;；]+", user_keywords):
            k = k.strip()
            if k and k not in fallback:
                fallback.append(k)
    return fallback[:max_queries]
