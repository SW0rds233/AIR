from __future__ import annotations

"""主题抽取 (供交付物标题/日志/产物名使用)。

触发本测试的真实缺陷: CLI/Web 的 `--request` 是整段问题描述 (带 Markdown 标题、
多段落、公式) 时, 主题被整段拿去当标题, 交付物第一行变成
`# 关于「# Problem 2：…\n\n某大型实验有 211 个节点。…」的理论研究`, 可读性为零。
"""

from pathlib import Path

import pytest

from src.utils.file_utils import TOPIC_MAX_CHARS, derive_topic

CASE = (Path(__file__).resolve().parents[1] / "evals" / "cases"
        / "combinatorial-design" / "case.md")


def test_takes_first_heading_as_topic():
    text = "# Problem 2：211 个实验节点的无重复配对计划\n\n某大型实验有 211 个节点。\n"
    assert derive_topic(text) == "Problem 2：211 个实验节点的无重复配对计划"


@pytest.mark.skipif(not CASE.is_file(), reason="缺少组合设计用例")
def test_real_case_request_yields_single_line_topic():
    topic = derive_topic(CASE.read_text(encoding="utf-8"))
    assert topic, "整段请求也必须能取到主题"
    assert "\n" not in topic and len(topic) <= TOPIC_MAX_CHARS, topic
    assert topic.startswith("Problem 2"), topic


def test_long_first_line_is_cut_at_sentence_or_limit():
    long_line = "这是一段没有任何换行的超长问题描述" * 5
    topic = derive_topic(long_line)
    assert len(topic) <= TOPIC_MAX_CHARS
    # 超长时按句末标点截断, 保留完整语义 (未超长则原样保留)
    sentence = "第一句是问题。" + "第二句是补充要求与更多细节说明文字, 用于把整体长度拉过阈值上限, 并且继续加长。"
    assert len(sentence) > TOPIC_MAX_CHARS
    assert derive_topic(sentence) == "第一句是问题"
    assert derive_topic("第一句是问题。第二句是补充要求。") == "第一句是问题。第二句是补充要求"
    assert derive_topic("") == ""
    assert derive_topic("   \n  \n") == ""


def test_topic_is_not_the_project_id_helper():
    """主题与文件名安全化是两件事: 主题要可读, 不能把空格/冒号换成下划线。"""
    from src.utils.file_utils import sanitize_filename

    text = "分析信道变化对可分性的影响: 一个综述"
    assert derive_topic(text) == text
    assert sanitize_filename(text) != derive_topic(text)
