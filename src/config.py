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

# 部分模型限制 temperature 只能为特定值:
# - kimi-k2 系 (kimi-k2.6 等) 是推理模型, 开启 thinking 时只接受 temperature=1
#   但审阅/写作大文本时 thinking 会生成海量思维链, 单次远超 LLM_TIMEOUT 导致超时
# - 禁用 thinking (extra_body={"thinking": {"type": "disabled"}}) 后只接受 temperature=0.6
#   禁用后审阅从 >15min 降到 ~1-3min, 且对结构化评分任务质量影响很小
TEMPERATURE_RESTRICTED_MODELS = {
    "kimi": 1.0,       # kimi-k2.6 / moonshot 系: 开启 thinking 时只接受 temperature=1
    "kimi-k2": 1.0,
    "moonshot": 1.0,
}

# 推理模型 (kimi-k2 系) 默认禁用 thinking 提速; 设 KIMI_DISABLE_THINKING=0 恢复
KIMI_DISABLE_THINKING = os.getenv("KIMI_DISABLE_THINKING", "1") == "1"

# 禁用 thinking 后 kimi-k2 要求的温度
KIMI_NO_THINKING_TEMPERATURE = 0.6


def _is_kimi_reasoning_model(model: str) -> bool:
    """判断是否为 kimi-k2 系推理模型 (可用 thinking 参数)"""
    m = (model or "").lower()
    return m.startswith("kimi") or m.startswith("moonshot")


def _apply_temperature_restriction(model: str, temperature: float) -> float:
    """根据模型名修正 temperature（受限模型强制允许值）

    kimi-k2 系: 禁用 thinking 时用 0.6, 否则 1.0。
    """
    if _is_kimi_reasoning_model(model):
        if KIMI_DISABLE_THINKING:
            return KIMI_NO_THINKING_TEMPERATURE
        return 1.0
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


def _model_extra_body(model: str) -> dict | None:
    """为推理模型构造 extra_body: 禁用 thinking 提速"""
    if KIMI_DISABLE_THINKING and _is_kimi_reasoning_model(model):
        return {"thinking": {"type": "disabled"}}
    return None


def build_llm(config_key: str = "main"):
    """统一构建 ChatOpenAI 实例

    config_key: "main" (主模型) / "reviewer" (跨模型审阅) / "cheap" (廉价模型)
    """
    from langchain_openai import ChatOpenAI

    cfg = {"main": LLM_CONFIG, "reviewer": REVIEWER_CONFIG, "cheap": CHEAP_CONFIG}[config_key]
    extra = _model_extra_body(cfg["model"])
    # Moonshot (Kimi) 官方 API 要求请求头 Kimi-Api-Version,
    # 视觉/多模态请求缺失时会报 400 "missing required header Kimi-Api-Version"
    headers = None
    if _is_kimi_reasoning_model(cfg["model"]):
        headers = {"Kimi-Api-Version": os.getenv("KIMI_API_VERSION", "moonshot-v1-auto")}
    return ChatOpenAI(
        model=cfg["model"],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=cfg["temperature"],
        timeout=LLM_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
        default_headers=headers,
        **({"extra_body": extra} if extra else {}),
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

# 跳过向量化入库 (embedding 是可选 RAG 增强, 主流程不依赖):
# SKIP_EMBEDDING=1 时 pdf_ingestion 只下载 PDF、不解析不 embedding, 大幅提速
SKIP_EMBEDDING = os.getenv("SKIP_EMBEDDING", "0") == "1"

# 单篇论文全文摄入的最大字符数 (越小块越少、embedding 越快)
PDF_FULLTEXT_MAX_CHARS = int(os.getenv("PDF_FULLTEXT_MAX_CHARS", "20000"))

# PDF 下载上限 (pdf_ingestion 阶段最多下载多少篇全文)。
# 下载每篇间隔 3s 限流 + 解析/embedding 是流程最耗时环节之一,
# 调试全流程时调低此值 (如 5~10) 可显著缩短时间; 正式运行可调回 30。
PDF_DOWNLOAD_LIMIT = int(os.getenv("PDF_DOWNLOAD_LIMIT", "30"))

ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_SEARCH_MAX_RESULTS", "50"))
SEMANTIC_SCHOLAR_MAX_RESULTS = int(os.getenv("SEMANTIC_SCHOLAR_MAX_RESULTS", "50"))

MAX_REVISIONS = int(os.getenv("MAX_REVISIONS", "3"))
# 百分制阈值；80 分等价于 Reviewer 固定量表的 40/50，与审稿决策锚点一致。
REVIEW_ACCEPT_THRESHOLD = int(os.getenv("REVIEW_ACCEPT_THRESHOLD", "80"))
# 收敛检测: 连续 N 轮评分无提升则提前终止修订循环
STAGNATION_LIMIT = int(os.getenv("STAGNATION_LIMIT", "2"))

HTTP_CONNECT_TIMEOUT = int(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))
HTTP_READ_TIMEOUT = int(os.getenv("HTTP_READ_TIMEOUT", "30"))
HTTP_WRITE_TIMEOUT = int(os.getenv("HTTP_WRITE_TIMEOUT", "30"))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
HTTP_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("HTTP_CIRCUIT_BREAKER_THRESHOLD", "5"))
HTTP_CIRCUIT_BREAKER_COOLDOWN = int(os.getenv("HTTP_CIRCUIT_BREAKER_COOLDOWN", "60"))
HTTP_BACKOFF_BASE = float(os.getenv("HTTP_BACKOFF_BASE", "2"))
HTTP_429_BACKOFF_BASE = float(os.getenv("HTTP_429_BACKOFF_BASE", "4"))

# LLM 调用超时: 推理模型 (kimi-k2.6 等) 审稿/写作耗时长, 需要更宽容的超时
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "900"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
