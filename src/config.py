from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"

LLM_CONFIG = {
    "model": os.getenv("OPENAI_MODEL", "deepseek-chat"),
    "api_key": os.getenv("OPENAI_API_KEY", "sk-placeholder"),
    "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "temperature": 0.1,
}

# 跨模型审阅：审阅节点使用独立的 reviewer LLM（默认与撰写模型相同）
# 参考: ARIS 的 cross-model review loops —— 审阅者与撰写者不同模型可避免共享认知框架盲区
REVIEWER_CONFIG = {
    "enabled": bool(os.getenv("REVIEWER_MODEL", "")),
    "model": os.getenv("REVIEWER_MODEL", LLM_CONFIG["model"]),
    "api_key": os.getenv("REVIEWER_API_KEY", LLM_CONFIG["api_key"]),
    "base_url": os.getenv("REVIEWER_BASE_URL", LLM_CONFIG["base_url"]),
    "temperature": float(os.getenv("REVIEWER_TEMPERATURE", "0.1")),
}

# 廉价模型分层（借鉴 gpt-researcher 的 FAST/STRATEGIC 降级链 + HKUDS 的 CHEEP_MODEL）:
# 子查询生成/相关性打分/引文 LLM 意见等轻量任务用廉价模型摊薄成本，
# 写作/大纲/审阅等质量敏感环节仍用主模型（或 REVIEWER_MODEL）。
CHEAP_CONFIG = {
    "enabled": bool(os.getenv("CHEAP_MODEL", "")),
    "model": os.getenv("CHEAP_MODEL", LLM_CONFIG["model"]),
    "api_key": os.getenv("CHEAP_API_KEY", LLM_CONFIG["api_key"]),
    "base_url": os.getenv("CHEAP_BASE_URL", LLM_CONFIG["base_url"]),
    "temperature": 0.1,
}

# 部分模型限制 temperature 只能为特定值（如 kimi 系只接受 1）
# 若审阅模型在受限列表中, 强制使用其允许的温度
TEMPERATURE_RESTRICTED_MODELS = {
    "kimi": 1.0,       # kimi-k2.6 / moonshot 系只接受 temperature=1
    "kimi-k2": 1.0,
    "moonshot": 1.0,
}


def _apply_temperature_restriction(model: str, temperature: float) -> float:
    """根据模型名修正 temperature（受限模型强制允许值）"""
    model_lower = model.lower()
    for prefix, allowed in TEMPERATURE_RESTRICTED_MODELS.items():
        if model_lower.startswith(prefix):
            return allowed
    return temperature


REVIEWER_CONFIG["temperature"] = _apply_temperature_restriction(
    REVIEWER_CONFIG["model"], REVIEWER_CONFIG["temperature"]
)
LLM_CONFIG["temperature"] = _apply_temperature_restriction(
    LLM_CONFIG["model"], LLM_CONFIG["temperature"]
)
CHEAP_CONFIG["temperature"] = _apply_temperature_restriction(
    CHEAP_CONFIG["model"], CHEAP_CONFIG["temperature"]
)


def build_llm(config_key: str = "main"):
    """统一构建 ChatOpenAI 实例

    config_key: "main" (主模型) / "reviewer" (跨模型审阅) / "cheap" (廉价模型)
    参考: gpt-researcher 的 FAST/SMART/STRATEGIC 三层模型工厂。
    """
    from langchain_openai import ChatOpenAI

    cfg = {"main": LLM_CONFIG, "reviewer": REVIEWER_CONFIG, "cheap": CHEAP_CONFIG}[config_key]
    return ChatOpenAI(
        model=cfg["model"],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=cfg["temperature"],
    )

# 每百万 token 价格 (USD) - 用于成本估算，可按服务商修改
MODEL_PRICES = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-5": {"input": 1.25, "output": 10.0},
    "gpt-5-mini": {"input": 0.15, "output": 2.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-opus-4-1": {"input": 15.0, "output": 75.0},
    "qwen-plus": {"input": 0.4, "output": 1.2},
    "glm-4-plus": {"input": 0.5, "output": 1.5},
}
DEFAULT_MODEL_PRICE = {"input": 0.5, "output": 1.5}

# GROBID 结构化解析（可选，需要本地/远程 GROBID 服务）
# 去掉尾部斜杠: scipdf 拼接 /api/processFulltextDocument 时双斜杠会导致 400
GROBID_BASE_URL = os.getenv("GROBID_BASE_URL", "").rstrip("/")

# Langfuse 追踪（可选）
LANGFUSE_CONFIG = {
    "public_key": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
    "secret_key": os.getenv("LANGFUSE_SECRET_KEY", ""),
    "host": os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
}

CHROMA_CONFIG = {
    "persist_directory": str(DATA_DIR / os.getenv("CHROMA_PERSIST_REL", "chroma")),
    "collection_name": "papers",
    "fulltext_collection": "papers_fulltext",
    "wiki_collection": "research_wiki",
}

ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_SEARCH_MAX_RESULTS", "50"))
SEMANTIC_SCHOLAR_MAX_RESULTS = int(os.getenv("SEMANTIC_SCHOLAR_MAX_RESULTS", "50"))

MAX_REVISIONS = int(os.getenv("MAX_REVISIONS", "3"))
REVIEW_ACCEPT_THRESHOLD = int(os.getenv("REVIEW_ACCEPT_THRESHOLD", "75"))
