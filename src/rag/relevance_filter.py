from __future__ import annotations

"""检索结果相关性过滤

问题背景: OpenAlex 模糊检索(尤其中文)会返回大量语义无关论文
(如检索"射频指纹"返回岩石油页岩/微生物/岩石学论文)。

两层过滤:
1. 规则过滤 (零成本): 标题必须包含主题关键词
2. LLM 打分过滤 (可选): 对候选做相关性打分 0-10, 过滤低分
"""

import logging
import re

logger = logging.getLogger(__name__)

RULE_THRESHOLD = 0.3       # 规则命中率低于该值剔除
LLM_SCORE_THRESHOLD = 4.5  # LLM 打分低于该值剔除 (0-10, 保留明确相关及以上)

# 同词异域混淆术语: 主题/关键词含左侧词时, 标题含右侧任一术语的论文剔除
# "website fingerprinting" (Tor 流量分析) ≠ "RF fingerprinting" (物理层射频指纹)
# 触发词覆盖中英混合写法 (rf fingerprint / rf指纹)
CONFUSABLE_TERMS = [
    ("rf fingerprint", ("website fingerprinting", "web fingerprint", "browser fingerprint", "tor traffic")),
    ("rf指纹", ("website fingerprinting", "web fingerprint", "browser fingerprint", "tor traffic")),
    ("射频指纹", ("网站指纹", "浏览器指纹", "website fingerprinting", "tor traffic")),
]

# 非技术性来源特征 (教学/社科期刊混入技术综述的论文)
NON_TECHNICAL_VENUE_HINTS = ("教学", "教育", "人文", "社会研究", "课程", "教改", "课堂")

# 无线/RF 领域特征词: 用于剔除"同词异域"论文——仅命中 "deep learning"/"transformer"
# 等泛方法词、但标题/摘要无任何无线通信领域信号的论文
# (如 "Scaling deep learning for materials discovery"、"Video Swin Transformer")。
# 注意 "rf" 需词边界匹配, 否则会误命中 "pe-rf-ormance" 里的 "rf"。
_DOMAIN_TOKENS = (
    "radio", "wireless", "emitter", "fingerprint", "spectrum", "transmitter",
    "receiver", "physical layer", "modulation", "drone", "uav", "iot",
    "bluetooth", "wifi", "wi-fi", "lte", "zigbee", "device identification",
    "cognitive radio", "radar", "specific emitter", "signal", "communication",
    "射频", "指纹", "辐射源", "发射机", "无线", "通信", "信号", "频谱", "电台", "电子对抗",
)
_DOMAIN_RF_RE = re.compile(r"\brf\b", re.IGNORECASE)

# 非 RF 领域负向信号: 标题命中这些词且无任何强 RF 信号时, 判定为跨域论文。
# 实测教训: "End-to-end Recording Device Identification" (音频录音设备) 借
# "device identification" 关键词通过过滤进入可信清单, 被 writer 引用后审稿人
# 连续 4 轮判 Critical 主题错配, 评分无法突破。
_OFF_DOMAIN_RE = re.compile(
    r"\b(audio|acoustic|speech|music|multimedia|video|image|face|website|malware)\b"
    r"|recording device",
    re.IGNORECASE,
)
# 强 RF 信号: 命中即视为本领域 (即使标题同时含 multimedia 等词,
# 如 "Wireless Multimedia Device Identification" 属边缘可接受)
_STRONG_RF_RE = re.compile(r"\brf\b|radio|wireless|emitter|射频|无线|辐射源|电台", re.IGNORECASE)


def has_off_domain_signal(title: str) -> bool:
    """判断标题是否属于音频/图像/多媒体等非 RF 领域 (且不含任何强 RF 信号)"""
    t = title or ""
    if _STRONG_RF_RE.search(t):
        return False
    return bool(_OFF_DOMAIN_RE.search(t))


def has_domain_signal(text: str) -> bool:
    """判断文本是否含无线/RF 领域特征词 (用于剔除"同词异域"论文)"""
    t = (text or "").lower()
    if _DOMAIN_RF_RE.search(t):
        return True
    return any(tok in t for tok in _DOMAIN_TOKENS)


def _normalize_keyword(kw: str) -> str:
    """规范化关键词: 小写 + 去下划线/连字符 + 去空格

    借鉴 gpt-researcher 的查询规范化:
    "RF_fingerprinting" / "RF-Fingerprinting" / "rf fingerprinting"
    统一为 "rffingerprinting", 避免下划线/连字符/空格导致的匹配失败
    """
    return re.sub(r"[\s_\-]+", "", kw.lower())


def extract_keywords(topic: str, user_keywords: str = "") -> list[str]:
    """提取检索关键词（按逗号/分号切分, **空格不拆散复合词**）

    "device identification" 保持为一个原子关键词 (deviceidentification),
    避免拆成 device/identification 后误匹配无关论文。
    """
    kws = []
    for k in [topic, user_keywords]:
        if not k:
            continue
        for part in re.split(r"[,，;；]+", k):
            part = part.strip()
            if part and len(part) >= 2 and part not in kws:
                kws.append(part)
    return kws


def rank_papers_by_priority(
    papers: list[dict], topic: str, user_keywords: str = ""
) -> list[dict]:
    """权威性优先级排序（不修改原列表）

    排序键 (按用户要求: 主关键词多且已发表最优先):
    1. 是否已发表 (已发表论文整体优先于预印本)
    2. 主关键词命中数 (标题+摘要)
    3. 被引量 (权威度指标)
    """
    kws = extract_keywords(topic, user_keywords)
    norm_kws = [k for k in (_normalize_keyword(kw) for kw in kws) if k]

    def _key(p: dict) -> tuple:
        text = _normalize_keyword(
            f"{p.get('title', '') or ''} {p.get('abstract', '') or ''}"
        )
        hits = sum(1 for k in norm_kws if k in text)
        published = 1 if (p.get("published") or (p.get("venue") or "").strip()) else 0
        citations = int(p.get("citations", 0) or 0)
        # 人工导入文献 (人已确认, 价值最高) 排最前, 其余按 已发表→命中→被引 排序
        manual = 1 if (p.get("api_source") == "人工导入") else 0
        return (-manual, -published, -hits, -citations)

    return sorted(papers, key=_key)


def rule_filter(papers: list[dict], topic: str, user_keywords: str = "") -> list[dict]:
    """规则过滤: 标题/摘要必须命中主题关键词 (归一化匹配)

    - 命中率 0 的论文剔除
    - 未来年份 (当前+1 之后) 剔除
    - 同词异域论文剔除 (如 website fingerprinting ≠ RF fingerprinting)
    - 非技术性期刊论文剔除 (教学/人文类)
    """
    import datetime

    kws = extract_keywords(topic, user_keywords)
    if not kws:
        return papers
    norm_kws = [_normalize_keyword(kw) for kw in kws]
    norm_kws = list(dict.fromkeys(norm_kws))

    # 同词异域检查: 主题或关键词含左侧触发词时, 标题含右侧术语 → 剔除
    topic_lower = (topic or "").lower()
    kw_lower = (user_keywords or "").lower()
    confusable_context = f"{topic_lower} {kw_lower}"
    # 主题本身含无线/RF 领域词时, 要求候选论文标题/摘要也必须含领域信号,
    # 否则 "deep learning" 这类泛方法词会放过材料发现/视频理解等完全无关论文
    topic_is_wireless = has_domain_signal(f"{topic} {user_keywords}")
    current_year = datetime.date.today().year

    kept = []
    for p in papers:
        # 人工导入文献: 人已确认相关, 豁免规则过滤。
        # 否则中文标题的人工文献遇英文主题 (如 "RF fingerprinting") 会因
        # 0 关键词命中被误删, 白白浪费用户精心挑选的高价值文献。
        if (p.get("api_source") or "") == "人工导入":
            kept.append(p)
            continue

        title = (p.get("title", "") or "").lower()
        abstract = (p.get("abstract", "") or "").lower()
        text = _normalize_keyword(f"{title} {abstract}")

        # 未来年份: 发表于今年之后的论文不可能存在
        try:
            year = int(p.get("year") or 0)
            if year > current_year + 1:
                logger.debug(f"规则过滤剔除 (未来年份 {year}): {p.get('title', '')[:60]}")
                continue
        except (ValueError, TypeError):
            pass

        # 同词异域: 主题或关键词含左侧触发词时, 标题含右侧术语 → 剔除
        confusable = False
        for trigger, bad_terms in CONFUSABLE_TERMS:
            if trigger in confusable_context and any(t in title for t in bad_terms):
                confusable = True
                break
        if confusable:
            logger.debug(f"规则过滤剔除 (同词异域): {p.get('title', '')[:60]}")
            continue

        # 领域信号校验: 无线/RF 主题下, 标题+摘要无任何领域词的论文剔除
        # (命中 "deep learning" 等泛词但实为材料/视频等异域论文)
        if topic_is_wireless and not has_domain_signal(f"{title} {abstract}"):
            logger.debug(f"规则过滤剔除 (无领域信号): {p.get('title', '')[:60]}")
            continue

        # 跨域负向信号: 音频/图像/多媒体等领域论文 (借 "device identification"
        # 等共享关键词混入, 被引用后审稿人判主题错配 Critical)
        if topic_is_wireless and has_off_domain_signal(p.get("title", "") or ""):
            logger.debug(f"规则过滤剔除 (跨域论文): {p.get('title', '')[:60]}")
            continue

        # 非技术性来源
        venue = (p.get("venue") or p.get("source") or "").lower()
        if any(h in venue for h in NON_TECHNICAL_VENUE_HINTS):
            logger.debug(f"规则过滤剔除 (非技术来源): {p.get('title', '')[:60]} ({venue[:40]})")
            continue

        hits = sum(1 for kw in norm_kws if kw in text)
        if hits > 0:
            kept.append(p)
        else:
            logger.debug(f"规则过滤剔除 (0 关键词命中): {p.get('title', '')[:60]}")

    logger.info(f"规则过滤: {len(papers)} -> {len(kept)} (关键词: {kws[:5]})")
    return kept


def llm_score_filter(
    papers: list[dict],
    topic: str,
    llm,
    threshold: float = LLM_SCORE_THRESHOLD,
    max_batch: int = 30,
) -> list[dict]:
    """LLM 相关性打分过滤（分批处理，不限总数）

    对候选论文分批打分 (0-10), 剔除低于阈值的。
    返回带 relevance_score 字段的论文列表。
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    if not papers:
        return []

    system = """你是文献相关性评估专家。对每篇候选论文判断其与给定研究主题的相关性，
输出 0-10 的分数（0=完全不相关, 10=高度相关）。只输出分数列表，每行一个数字。"""

    scored = []
    for batch_start in range(0, len(papers), max_batch):
        candidates = papers[batch_start : batch_start + max_batch]

        # 人工导入文献: 人已确认相关, 直接给高分通过, 不送 LLM 打分
        # (避免中文标题被 LLM 误判低分, 浪费高价值人工文献)
        manual = [p for p in candidates if (p.get("api_source") or "") == "人工导入"]
        to_score = [p for p in candidates if (p.get("api_source") or "") != "人工导入"]
        for p in manual:
            p["relevance_score"] = 10.0
            scored.append(p)
        if not to_score:
            continue

        candidates = to_score
        items_text = "\n".join(
            f"{i+1}. {p.get('title', '')} ({p.get('year', '')}) [{p.get('source', '')}]"
            for i, p in enumerate(candidates)
        )

        prompt = (
            f"研究主题: {topic}\n"
            f"请评估以下 {len(candidates)} 篇论文与该主题的相关性，输出每篇的分数（0-10）：\n\n"
            f"{items_text}\n\n"
            f"输出格式: 每行一个数字，如:\n8\n3\n6\n..."
        )

        try:
            result = llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=prompt)]
            )
            text = result.content if hasattr(result, "content") else str(result)
            scores = [float(s.strip()) for s in re.findall(r"\d+(?:\.\d+)?", text)]
            scores = scores[: len(candidates)]
        except Exception as e:
            logger.warning(f"LLM 打分失败 (batch {batch_start}), 保留全部: {e}")
            for p in candidates:
                p["relevance_score"] = 5.0
            scored.extend(candidates)
            continue

        for i, p in enumerate(candidates):
            score = scores[i] if i < len(scores) else 5.0
            p["relevance_score"] = score
            if score >= threshold:
                scored.append(p)

    logger.info(f"LLM 打分过滤: {len(papers)} -> {len(scored)} (阈值 {threshold}, {len(papers) // max_batch + 1} 批)")
    return scored
