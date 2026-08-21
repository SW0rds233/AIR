from __future__ import annotations

"""引用守门节点: 写作后强制校验引用编号与可信清单对应

借鉴:
- gpt-researcher: 引用只来自实际抓取过的内容 (visited_urls)
- HKUDS AI-Researcher: 引用只在已读论文中产生

作用:
1. 提取初稿中所有 [n] 引用编号
2. 与可信参考文献清单 (verified_references) 比对
3. **越界/未注册编号 → 用清单中真实论文自动替换, 无匹配则删除标记**
   （修复: 旧版只检测+触发修订，修订循环仍可能再越界，形成空转；
    新版守门直接修复，审计记录替换结果）
4. 确保 citation_check 阶段验证的是真实论文
"""

import re
import logging

from src.graph.state import PipelineState

logger = logging.getLogger(__name__)

# 上下文窗口: 取越界编号前后各多少字符用于相关性匹配
CTX_BEFORE = 60
CTX_AFTER = 120


def _cjk_bigrams(text: str) -> set[str]:
    """CJK 双字二元组: 用于中英跨语言的模糊匹配

    中文标题/摘要与英文正文互不共享单词，
    但中文语义以双字词为最小可比较单元（"深度学习"→{深度,学习}）。
    """
    bigrams = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        for i in range(len(run) - 1):
            bigrams.add(run[i:i + 2])
    return bigrams


def _tokens(text: str) -> set[str]:
    """分词: 英文单词 + CJK 双字二元组（跨语言匹配用）"""
    words = {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 1}
    words |= _cjk_bigrams(text)
    return words


def _ref_token_set(ref: dict) -> set[str]:
    """清单内论文的匹配特征: 标题 + 摘要（摘要提供跨语言线索）

    修复: 只匹配标题时, 中文正文 vs 英文标题无法命中；
    检索结果自带的摘要（可能为中文）大幅提升中英跨语言召回。
    """
    return _tokens(ref.get("title", "")) | _tokens(ref.get("abstract", ""))


def auto_fix_citations(draft: str, verified_refs: list[dict], invalid_nums: list[int]) -> dict:
    """越界编号自动修复

    策略（确定性，不依赖 LLM）:
    - 取越界编号所在句子的上下文 (前后 CTX_BEFORE/CTX_AFTER 字符)
    - 与清单内论文的标题+摘要做特征重叠打分（英文单词 + CJK 双字）
    - 重叠 ≥2 个特征 → 替换为清单内论文编号
    - 否则 → 删除该引用标记（宁可无引用，不可有虚假引用）

    Returns:
        {"repaired_draft": str, "replacements": [{from, to, method}], "removals": [...]}
    """
    if not invalid_nums:
        return {"repaired_draft": draft, "replacements": [], "removals": []}

    ref_titles = {}
    for e in verified_refs:
        n = e.get("ref_number")
        if n:
            ref_titles[n] = _ref_token_set(e)

    replacements = []
    removals = []
    repaired = draft

    # 句子边界（中文句号/叹号/问号/换行/英文句号）：上下文窗口不跨句
    _SENT_BOUND = re.compile(r"[。！？!?；;\n]")

    for n in sorted(set(invalid_nums), reverse=True):
        pattern = re.compile(rf"\[({n})\]")
        for m in pattern.finditer(repaired):
            # 向后: 至多 CTX_BEFORE 字符, 但止于上一句句末
            back_floor = max(0, m.start() - CTX_BEFORE)
            prev = repaired[back_floor:m.start()]
            bmatches = list(_SENT_BOUND.finditer(prev))
            if bmatches:
                back_floor += bmatches[-1].end()
            # 向前: 至多 CTX_AFTER 字符, 但止于下一句句首
            fwd_ceil = min(len(repaired), m.end() + CTX_AFTER)
            nxt = repaired[m.end():fwd_ceil]
            fmatches = list(_SENT_BOUND.finditer(nxt))
            if fmatches:
                fwd_ceil = m.end() + fmatches[0].start() + 1
            start, end = back_floor, fwd_ceil
            ctx_tokens = _tokens(repaired[start:end])
            if not ctx_tokens:
                continue

            best_num, best_score = None, 0
            for ref_num, title_tokens in ref_titles.items():
                overlap = len(ctx_tokens & title_tokens)
                if overlap > best_score:
                    best_score, best_num = overlap, ref_num

            if best_num is not None and best_score >= 2:
                replacements.append({"from": n, "to": best_num, "overlap": best_score})
                repaired = repaired[: m.start()] + f"[{best_num}]" + repaired[m.end():]
            elif best_num is not None and best_score == 1:
                # 单词重叠: 仅当该特征足够显著（长度≥5）才替换
                lone = ctx_tokens & ref_titles[best_num]
                if lone and len(next(iter(lone))) >= 5:
                    replacements.append({"from": n, "to": best_num, "overlap": 1})
                    repaired = repaired[: m.start()] + f"[{best_num}]" + repaired[m.end():]
                else:
                    removals.append(n)
                    repaired = repaired[: m.start()] + repaired[m.end():]
            else:
                removals.append(n)
                repaired = repaired[: m.start()] + repaired[m.end():]

    # 删除标记后折叠多余空格: "a [7] b" → "a  b" → "a b"
    repaired = re.sub(r" {2,}", " ", repaired)
    return {"repaired_draft": repaired, "replacements": replacements, "removals": removals}


_SENT_RE = re.compile(r"[^。！？!?；;\n]+")
_AUTHOR_CITE_RE = re.compile(r"([A-Z][a-z]{1,20})(?:\s*等人|\s+et al\.?)")
# 裸「X等」(不带"人"): 仅当其后紧跟归属动词时才认定为作者引用,
# 避免把「Transformer等」「LoRa等」这类技术名词枚举误判为作者。
# 触发案例: 正文「Zeng等则较早探索了...[51]」而 [51] 作者实为 Yu 等 → 引用错配。
_AUTHOR_BARE_DENG_RE = re.compile(r"([A-Z][A-Za-z]{1,20})\s*等")
_ATTRIB_TAIL_RE = re.compile(
    r"人?\s*(?:\[\d+(?:\s*,\s*\d+)*\])?\s*"
    r"(?:则|也|都|又|首次|较早|随后|进一步|分别|先后|最近|近年|后来|较|曾|已|率先)*\s*"
    r"(?:提出|探索|设计|采用|研究|证明|发现|验证|分析|指出|表明|报道|开发|引入|利用|将|基于|通过|针对|构建|给出|揭示|开展)"
)
# 常见技术名词: 这些词 + 「等」是事物枚举而非作者引用
_TECH_TERM_BLOCKLIST = {
    "transformer", "lora", "wifi", "bluetooth", "zigbee", "gan", "gans", "cnn", "rnn",
    "lstm", "gru", "svm", "rf", "rfid", "nfc", "iot", "ai", "ml", "dl", "dnn", "gnn",
    "bert", "resnet", "vgg", "gpu", "cpu", "fpga", "asic", "adc", "dac", "mimo",
    "ofdm", "csi", "rssi", "sei", "dctf", "uav", "gnss", "sdn", "mec", "noma",
    "autoencoder", "vae", "rl", "drl", "ppo", "awgn", "snr", "svm", "knn", "pso",
}


def _find_author_surnames(sentence: str) -> set[str]:
    """提取句中的作者姓氏引用: 「X等人」「X et al.」无条件识别;
    裸「X等」需后接归属动词 (提出/探索/设计...) 才识别, 防技术名词误判。"""
    surnames = set(_AUTHOR_CITE_RE.findall(sentence))
    for m in _AUTHOR_BARE_DENG_RE.finditer(sentence):
        name = m.group(1)
        if name.lower() in _TECH_TERM_BLOCKLIST:
            continue
        tail = sentence[m.end(): m.end() + 24]
        if _ATTRIB_TAIL_RE.match(tail):
            surnames.add(name)
    return surnames


def check_citation_semantics(draft: str, verified_refs: list[dict]) -> dict:
    """检测并移除正文「引用-语义错配」的引用标记 (确定性, 不依赖 LLM)。

    保守策略: 仅处理**单个引用**的句子 (多引用句子跳过, 避免误删)。
    若句中出现 "Xxx等人 / Xxx et al." 的作者名模式, 且该作者名不在 ref[n]
    的作者列表中, 说明 [n] 指向了错误的文献 (如正文称 "Sankhe ORACLE" 却引用
    CVPR 视频论文), 删除该 [n] 标记 (只删标记, 不改写正文, 宁可少删不可错删)。

    Returns: {"repaired_draft": str, "mismatches": [{"num", "surname", "context"}]}
    """
    ref_authors: dict[int, str] = {}
    for e in verified_refs:
        n = e.get("ref_number")
        if n is not None:
            ref_authors[int(n)] = (e.get("authors", "") or "").lower()

    mismatches = []
    removals: list[tuple[int, int]] = []

    for sm in _SENT_RE.finditer(draft):
        sentence = sm.group(0)
        nums = {int(x) for x in re.findall(r"\[(\d+)\]", sentence)}
        if len(nums) != 1:
            continue
        n = next(iter(nums))
        if n not in ref_authors:
            continue
        surnames = _find_author_surnames(sentence)
        if not surnames:
            continue
        for surname in surnames:
            if surname.lower() in ref_authors[n]:
                continue
            mismatches.append({
                "num": n,
                "surname": surname,
                "context": re.sub(r"\s+", " ", sentence).strip()[:120],
            })
            # 删除本句中的所有 [n] 标记
            for cm in re.finditer(rf"\[{n}\]", sentence):
                removals.append((sm.start() + cm.start(), sm.start() + cm.end()))
            break

    if not removals:
        return {"repaired_draft": draft, "mismatches": mismatches}

    repaired = draft
    for start, end in sorted(set(removals), reverse=True):
        repaired = repaired[:start] + repaired[end:]
    repaired = re.sub(r" {2,}", " ", repaired)
    return {"repaired_draft": repaired, "mismatches": mismatches}


def validate_draft_citations(draft: str, verified_refs: list[dict]) -> dict:
    """校验初稿引用与可信清单的对应关系

    Returns:
        {
            "valid_citations": [...],  # 清单内的编号
            "invalid_citations": [...],  # 越界编号
            "fix": {...},  # 自动修复结果
            "report_md": "...",
        }
    """
    from src.rag.reference_formatter import find_citation_numbers

    inline_nums = find_citation_numbers(draft)
    valid_nums = set()
    ref_map = {}
    for e in verified_refs:
        n = e.get("ref_number")
        if n:
            valid_nums.add(n)
            ref_map[n] = e

    valid = [n for n in inline_nums if n in valid_nums]
    invalid = sorted(set(n for n in inline_nums if n not in valid_nums))

    # 自动修复: 越界编号 → 清单内论文 (或删除标记)
    fix = auto_fix_citations(draft, verified_refs, invalid)

    lines = [
        "# 引用守门报告 (Citation Guard)",
        "",
        f"- 文中引用标记总数: {len(inline_nums)}",
        f"- 可信清单论文数: {len(verified_refs)}",
        f"- 清单内有效引用: {len(set(valid))} 个编号",
        f"- **越界/虚构引用编号: {invalid if invalid else '无'}**",
        "",
        "## 说明",
        "",
        "可信清单中的论文均来自 arXiv/Semantic Scholar/OpenAlex 官方 API，",
        "真实存在。初稿中越界的引用编号 [n] 无法对应清单，说明 LLM 未遵守纪律。",
        "守门节点已尝试自动修复：有上下文关联的替换为清单内论文，",
        "无关联的删除该引用标记（宁缺毋假）。",
    ]

    if invalid:
        lines.append("")
        lines.append("## ⚠️ 需要修复的引用编号")
        for n in invalid:
            lines.append(f"- [{n}]: 不在可信清单内 (清单编号范围 1-{max(valid_nums) if valid_nums else 0})")
        if fix["replacements"]:
            lines.append("")
            lines.append("### ✅ 自动替换")
            for r in fix["replacements"]:
                lines.append(f"- [{r['from']}] → [{r['to']}] (上下文关键词重叠 {r['overlap']})")
        if fix["removals"]:
            lines.append("")
            lines.append("### 🗑️ 自动删除（无清单内关联，删除标记避免虚假引用）")
            for n in sorted(set(fix["removals"])):
                lines.append(f"- [{n}] 引用标记已删除")

    return {
        "valid_citations": sorted(set(valid)),
        "invalid_citations": invalid,
        "fix": fix,
        "report_md": "\n".join(lines),
    }


def run_citation_guard(state: PipelineState) -> dict:
    """流水线节点: 写作后校验引用，并自动修复越界编号 + 语义错配引用"""
    draft = state.get("paper_draft", "")
    verified_refs = state.get("verified_references", [])

    if not draft:
        return {"current_phase": "citation_guard", "guard_report": {}}

    result = validate_draft_citations(draft, verified_refs)

    # 语义一致性校验: 检测 "Xxx等人" 作者名与 ref[n] 不匹配的引用并删除标记。
    # 在越界自动修复的基础上运行 (base_draft 已替换/删除越界编号)。
    fix = result.get("fix", {})
    base_draft = fix.get("repaired_draft") or draft
    sem = check_citation_semantics(base_draft, verified_refs)
    if sem["mismatches"]:
        result["semantic_mismatches"] = sem["mismatches"]
        result["report_md"] += (
            "\n\n## ⚠️ 引用语义错配（已删除错误引用标记）\n\n"
            + "\n".join(
                f"- [{m['num']}] 正文提到作者「{m['surname']}」但该编号文献作者不含此名"
                f" → 已删除标记: {m['context']}"
                for m in sem["mismatches"]
            )
        )
        logger.info(f"引用语义错配已删除 {len(sem['mismatches'])} 处")

    updates: dict = {
        "current_phase": "citation_guard",
        "guard_report": result,
        "guard_invalid_count": len(result["invalid_citations"]) + len(sem["mismatches"]),
    }

    # sem["repaired_draft"] 已同时包含越界修复与语义错配删除的结果
    if sem["repaired_draft"] != draft:
        updates["paper_draft"] = sem["repaired_draft"]
        logger.info(
            f"引用守门自动修复: {len(fix.get('replacements', []))} 处替换, "
            f"{len(set(fix.get('removals', [])))} 处删除, "
            f"{len(sem['mismatches'])} 处语义错配删除"
        )

    return updates
