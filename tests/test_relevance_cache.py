"""相关性过滤与缓存兜底测试：跨域论文拦截 / 缓存清洗 / 台账重编号提示（离线）

运行:
    python tests/test_relevance_cache.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.relevance_filter import has_off_domain_signal, rule_filter


def test_off_domain_detection():
    """实测案例: 录音设备识别论文借 'device identification' 关键词混入,
    被引用后审稿人连续 4 轮判 Critical 主题错配, 评分卡在 36 无法突破"""
    # 音频/多媒体等跨域论文 → 拦截
    assert has_off_domain_signal("End-to-end Recording Device Identification Based on Deep Representation Learning")
    assert has_off_domain_signal("Audio Fingerprint Recognition Using Neural Networks")
    assert has_off_domain_signal("Video-based Device Identification System")
    assert has_off_domain_signal("Image Fingerprint for Multimedia Content")
    # 含强 RF 信号的边缘论文 → 保留 (如 Wireless Multimedia Device Identification)
    assert not has_off_domain_signal("An Ensemble Learning Method for Wireless Multimedia Device Identification")
    # 正常 RF 指纹论文 → 保留
    assert not has_off_domain_signal("A Robust RF Fingerprinting Approach Using Multisampling CNN")
    assert not has_off_domain_signal("射频指纹识别的研究现状及趋势")
    assert not has_off_domain_signal("Specific Emitter Identification via Convolutional Neural Networks")


def test_rule_filter_excludes_off_domain_papers():
    papers = [
        {"title": "End-to-end Recording Device Identification Based on Deep Representation Learning",
         "abstract": "device identification with deep learning", "year": "2016"},
        {"title": "A Robust RF Fingerprinting Approach Using Multisampling CNN",
         "abstract": "rf fingerprinting for wireless devices", "year": "2019"},
        {"title": "射频指纹识别中的特征提取方法",
         "abstract": "射频 指纹 特征", "year": "2020"},
    ]
    kept = rule_filter(papers, "RF fingerprinting", "device identification, 射频指纹")
    titles = [p["title"] for p in kept]
    assert not any("Recording Device" in t for t in titles)
    assert any("RF Fingerprinting" in t for t in titles)
    assert any("射频指纹" in t for t in titles)


def test_rule_filter_matches_mixed_language_keywords():
    """中英混写关键词 (如 "射频指纹（RF fingerprinting / RFFI）") 应能命中英文论文,
    而不是因关键词含中文/括号而把英文论文全部误删 (实测 200→1)"""
    papers = [
        {"title": "Deep learning based RF fingerprinting for device identification and wireless security",
         "abstract": "", "year": "2018"},
        {"title": "Wireless security through RF fingerprinting",
         "abstract": "", "year": "2007"},
        {"title": "岩石力学与工程学报某篇无关论文",
         "abstract": "岩石 应力 应变", "year": "2020"},
        {"title": "Website fingerprinting in Tor traffic analysis",
         "abstract": "", "year": "2019"},
    ]
    kws = "射频指纹（RF fingerprint）, 射频指纹识别（RF fingerprinting / RFFI / radio frequency fingerprint identification）"
    kept = rule_filter(papers, "射频指纹识别技术", kws)
    titles = [p["title"] for p in kept]
    assert any("RF fingerprinting" in t for t in titles), f"英文射频指纹论文应保留, 实际 {titles}"
    assert not any("岩石" in t for t in titles), "岩石学论文应剔除"
    assert not any("Website fingerprinting" in t for t in titles), "Tor 网站指纹论文应剔除"


def test_load_cache_filters_off_domain_refs():
    """历史缓存中的跨域论文在加载时兜底剔除 (避免重跑 --skip-retrieval 时复发)"""
    import src.utils.pipeline_cache as pc

    refs = [
        {"ref_number": i, "title": f"RF Fingerprinting Paper {i}", "doi": f"10.1000/rf{i}",
         "year": "2020", "venue": "IEEE TIFS"}
        for i in range(1, 21)
    ]
    refs.append({"ref_number": 21, "doi": "10.1000/rec",
                 "title": "End-to-end Recording Device Identification Based on Deep Representation Learning",
                 "year": "2016", "venue": "ICSIP"})
    payload = {
        "topic": "测试主题",
        "literature_review_notes": "素材" * 100,
        "verified_references": refs,
    }
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = pc.CACHE_DIR
        pc.CACHE_DIR = Path(tmp)
        try:
            pc.save_retrieval_cache("测试主题", payload["literature_review_notes"], refs)
            loaded = pc.load_retrieval_cache("测试主题")
            assert loaded is not None
            titles = [r.get("title", "") for r in loaded["verified_references"]]
            assert not any("Recording Device" in t for t in titles)
            assert len(titles) == 20  # 21 篇中跨域 1 篇被剔除
        finally:
            pc.CACHE_DIR = orig_dir


def test_review_anchor_warns_about_renumbered_citations():
    """台账含 [n] 时, 复审锚点必须提醒编号每轮重排 (实测: 审稿人沿用旧台账
    对 [34] 的主题描述, 连续 4 轮误判 Critical 未解决)"""
    from src.agents.paper_reviewer import _build_review_anchor

    state = {
        "review_dimensions": {},
        "review_issue_ledger": [
            {"id": "R-REF-01", "status": "未解决", "priority": "Critical",
             "problem": "[34]为音频录音设备识别论文", "evidence": "3.3.5节引用[34]"},
        ],
    }
    anchor = _build_review_anchor(state)
    assert "引用编号每轮重排" in anchor
    assert "已解决" in anchor
    # 无 [n] 的台账不产生该警告
    state2 = {"review_dimensions": {}, "review_issue_ledger": [
        {"id": "R-ABS-01", "status": "未解决", "priority": "高",
         "problem": "摘要超长", "evidence": "摘要章节"},
    ]}
    assert "引用编号每轮重排" not in _build_review_anchor(state2)


def test_reviewer_prompt_has_renumber_rule():
    from src.agents.paper_reviewer import PAPER_REVIEWER_SYSTEM

    assert "引用编号每轮重新排序" in PAPER_REVIEWER_SYSTEM
    assert "严禁沿用旧台账里对 [n] 的主题描述" in PAPER_REVIEWER_SYSTEM


def test_writer_prompt_has_off_domain_replacement_rule():
    from src.agents.paper_writer import PAPER_REVISION_SYSTEM

    assert "引用主题错配" in PAPER_REVISION_SYSTEM
    assert "recording/audio/multimedia" in PAPER_REVISION_SYSTEM


if __name__ == "__main__":
    tests = [
        test_off_domain_detection,
        test_rule_filter_excludes_off_domain_papers,
        test_rule_filter_matches_mixed_language_keywords,
        test_load_cache_filters_off_domain_refs,
        test_review_anchor_warns_about_renumbered_citations,
        test_reviewer_prompt_has_renumber_rule,
        test_writer_prompt_has_off_domain_replacement_rule,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
