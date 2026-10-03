from __future__ import annotations

"""检索式规划契约 (P0-1 场景 ②/§1 契约第 2 行): 自设检索式而非单一查询词。"""

from src.research.query_planner import Query, plan_queries
from src.research.question_planner import formulate
from src.research.schemas import ProblemContract, SourceSummary, TaskKind

REQUEST = "分析信道变化对射频指纹可分性的影响, 并给出理论结论与仿真建议"
TERMS = {"信道": ["channel", "channel variation"], "射频指纹": ["RF fingerprint", "RFF"]}


def _contract(kind: TaskKind) -> ProblemContract:
    summary = {
        TaskKind.mechanism: SourceSummary(has_theory_model=True),
        TaskKind.empirical_causal: SourceSummary(has_observational_data=True),
        TaskKind.scenario: SourceSummary(source_set_id="rf-A", documents=3),
    }[kind]
    return formulate(REQUEST, source_summary=summary)


def test_angles_follow_task_kind():
    mechanism = plan_queries(_contract(TaskKind.mechanism))
    causal = plan_queries(_contract(TaskKind.empirical_causal))
    scenario = plan_queries(_contract(TaskKind.scenario))
    assert mechanism[0].angle == "mechanism"
    assert causal[0].angle == "result"
    assert scenario[0].angle == "boundary"
    # 每个角度都带"为什么这样问", 覆盖记录才能自证检索意图
    assert all(q.text and q.why for q in mechanism + causal + scenario)


def test_counterexample_angle_is_always_available():
    angles = {q.angle for q in plan_queries(_contract(TaskKind.mechanism), limit=6)}
    assert "counterexample" in angles


def test_terminology_variants_are_used_and_deduped():
    queries = plan_queries(_contract(TaskKind.mechanism), terminology=TERMS)
    terminology_queries = [q for q in queries if q.angle == "terminology"]
    assert len(terminology_queries) == 1
    assert "channel" in terminology_queries[0].text
    texts = [q.text for q in queries]
    assert len(texts) == len(set(texts)), texts


def test_limit_is_respected_and_queries_never_empty():
    queries = plan_queries(_contract(TaskKind.mechanism), limit=2)
    assert len(queries) == 2
    assert all(isinstance(q, Query) and q.text.strip() for q in queries)
    assert plan_queries(_contract(TaskKind.mechanism), limit=0)


def test_without_objects_falls_back_to_goal_query():
    contract = formulate("对所有实数 x: x**2 >= 0")
    queries = plan_queries(contract)
    assert len(queries) == 1
    assert queries[0].angle == "goal"
    assert "x**2" in queries[0].text


# ----------------------------------------------------------------------
# 中文题的跨语言检索式: 术语表缺省时必须自动补英文核心词
# ----------------------------------------------------------------------

def _design_contract(goal: str, objects: list[str]) -> ProblemContract:
    return ProblemContract(goal=goal, task_kind=TaskKind.formal_proof, objects=objects)


def test_chinese_goal_without_terminology_gets_english_core_terms():
    """术语表为空的中文题也必须产出**纯英文**跨语言检索式。

    必要性来自实测: OpenAlex 的 `search` 对中文关键词几乎不做语义匹配 ——
    查"射影平面 不存在性"返回"非平行光束对窄带滤光片"、"地表伽玛辐射";
    查"组合设计 存在性"返回土木的"路面结构组合设计"。
    """
    contract = _design_contract("射影平面 不存在性 与 Bruck-Ryser-Chowla 定理",
                                ["射影平面 不存在", "必要条件"])
    queries = plan_queries(contract, terminology=None)
    terminology = [q for q in queries if q.angle == "terminology"]
    assert len(terminology) == 1, [q.text for q in queries]
    text = terminology[0].text
    assert "projective plane" in text, text
    # 不得把中文词混进英文检索式 (会稀释英文匹配)
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in text), text


def test_english_query_pairs_intent_with_domain_and_dedupes_synonyms():
    """意图词与领域名词短语**配对**, 且同义意图词只留一个。

    实测 (OpenAlex 前 3 条相关性):
      `projective plane` 单独查        -> 教科书条目 ("Projective planes")
      `nonexistence non-existence does not exist` -> 液滴模型等无关结果
      `nonexistence projective plane`  -> "The Nonexistence of Certain Finite
                                          Projective Planes" 等 3/3 命中
    """
    contract = _design_contract("射影平面 不存在性 (计数 必要条件)",
                                ["射影平面 不存在", "必要条件"])
    text = next(q.text for q in plan_queries(contract, terminology=None)
                if q.angle == "terminology")
    assert text.startswith("nonexistence"), text
    assert "projective plane" in text, text
    # 同义变体不得同时出现
    assert "non-existence" not in text and "does not exist" not in text, text


def test_generic_terms_never_dilute_the_english_query():
    """泛词 (existence/count/design/necessary condition) 不得进入英文检索式。"""
    contract = _design_contract("判断 2-设计是否存在 (射影平面 计数 必要条件)",
                                ["射影平面 存在性", "计数 必要条件"])
    text = next(q.text for q in plan_queries(contract, terminology=None)
                if q.angle == "terminology")
    for generic in ("count", "necessary condition", "design theorem"):
        assert generic not in text, f"泛词 {generic!r} 混入: {text}"


def test_non_chinese_goal_does_not_get_fallback_terms():
    """本来就是英文的题目不触发中文回退 (避免无意义地改写检索式)。"""
    contract = ProblemContract(goal="projective plane nonexistence of order 10",
                               task_kind=TaskKind.formal_proof,
                               objects=["projective plane", "nonexistence"])
    queries = plan_queries(contract, terminology=None)
    assert not [q for q in queries if q.angle == "terminology"]
