from __future__ import annotations

"""理论研究循环引擎 (P1-P3)。
流程: formulate → decide → act → verify → update → decide, 以交付门槛结束。

硬约束 (计划书 §4.3 状态不变量):
1. 模型/假设/形式化编码/变量域任一变化 → 相关验证过期 (verification_closure);
2. 局部验证通过只关闭它明确覆盖的义务; 全部必要义务关闭后才升级目标结论;
3. 反驳必须来自适用域内的反例或可审查的反证, 运行错误不算反驳;
4. 已知条件下的结论不得写成无条件;
5. 假设/证据修订后依赖它的结论与报告失效, 旧版本保留不覆盖;
6. 只检查本次快照实际引用的有效记录是否 stale;
7. 程序负责事实状态、版本、预算与证据校验; LLM 只提交候选提议;
8. `unknown / timeout / unsupported / unavailable` 一律保持未决。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.kb.service import KnowledgeService
from src.research import coordinator
from src.research import novelty as novelty_mod
from src.research.acceptance import GateResult, theory_validity_gate
from src.research.action_registry import available_actions
from src.research.argument import WritingGap, writing_gaps
from src.research.context import build_context_pack
from src.research.dependency_graph import CyclicDependencyError, DependencyGraph
from src.research.design_feasibility import is_design_claim
from src.research.logging_schema import SURFACE_EVENT_KINDS
from src.research.problem_formulator import formulate
from src.research.question_planner import generate_candidates
from src.research.routes import RouteManager
from src.research.schemas import (
    ActionExecution,
    ActionType,
    Assumption,
    Assurance,
    Claim,
    ClaimStatus,
    ClaimType,
    Coverage,
    Definition,
    EvidenceGrade,
    EvidenceLink,
    FailureKind,
    GapType,
    NoveltyComparisonRow,
    NoveltyStatus,
    ObjectRef,
    ObligationStatus,
    Origin,
    ProofAttempt,
    ProofObligation,
    ProofStep,
    Relation,
    ResearchAction,
    ResearchGap,
    ResearchModel,
    ResearchSnapshot,
    ResearchSpec,
    RouteStatus,
    SourceEvidence,
    SourcePolicy,
    StudyDesign,
    SupportKind,
    SupportKindOfEvidence,
    ValidationStatus,
    VerificationRecord,
    VerificationScope,
    coverage_for_tool,
    hash_payload,
    new_id,
    support_kind_for_tool,
    utcnow,
)
from src.research.store import (
    KIND_ASSUMPTION,
    KIND_ATTEMPT,
    KIND_CLAIM,
    KIND_DEFINITION,
    KIND_EVIDENCE,
    KIND_EVIDENCE_LINK,
    KIND_GAP,
    KIND_MODEL,
    KIND_NOVELTY,
    KIND_OBLIGATION,
    KIND_ROUTE,
    KIND_RUNTIME,
    KIND_SPEC,
    KIND_VERIFICATION,
    ResearchStore,
    StepAlreadyApplied,
)
from src.research.theorist import Plan, _check_for, plan_proof
from src.verification.runner import VerificationRunner, available_tools

# 义务种类 → 该义务关闭后命题可提升到的覆盖范围
_OBLIGATION_COVERAGE = {
    "prove_inequality": Coverage.target,
    "prove_identity": Coverage.target,
    "prove_monotonicity": Coverage.target,
    "estimate_effect": Coverage.target,
    "equality_condition": Coverage.step,   # 等号条件是对局部子目标的补充
    "check_implication": Coverage.target,
    "control_confound": Coverage.target,
    "scope_check": Coverage.target,
    "evidence_support": Coverage.target,
    "design_necessity": Coverage.target,
}

# 写作缺口种类 → 能真正核查它的验收方法 (决定协调者把义务派给哪个动作)。
# `informal_review` 对应 check_step, `rule`/`manual` 对应需要重新推导或人工确认,
# 因此缺口的回流义务不会落到"没有动作可用"的死角。
_GAP_ACCEPTANCE = {
    "missing_argument_chain": "informal_review",
    "missing_verification_input": "rule",
    "scope_mismatch": "rule",
    "dangling_evidence_ref": "informal_review",
    "missing_uncertainty": "rule",
    "suggestion_without_rule": "informal_review",
    "execution_without_artifact": "informal_review",
}

# 义务处理顺序: 先做效应估计/核心推导, 再关闭依赖其结果的规则型义务。
# (缺口构造与决策排序必须使用同一顺序, 否则控制器会一直选中依赖项。)
OBLIGATION_PRIORITY = {
    "estimate_effect": 0, "prove_inequality": 0, "prove_monotonicity": 0,
    "prove_identity": 0, "check_implication": 0, "equality_condition": 1,
    "control_confound": 2, "scope_check": 3, "evidence_support": 4,
    # 设计/计数类存在性判定: 与计数关系同层, 先于一般规则型义务
    "design_necessity": 2,
    # 因果识别类声明必须在效应估计之后再评估 (计划书 §7.4)
    "identification_assumptions": 4, "design_feasibility": 4,
    "measurement_and_missing": 4, "error_structure": 4,
}


def _obligation_kind(obligations: list[ProofObligation], obligation_id: str) -> str:
    for obligation in obligations:
        if obligation.id == obligation_id:
            return obligation.kind
    return ""


def _claim_of(obligations: list[ProofObligation], obligation_id: str) -> str:
    for obligation in obligations:
        if obligation.id == obligation_id:
            return obligation.claim_id
    return ""


def _feedback_candidates(context: dict) -> list[dict]:
    """把反馈可选对象整理成前端可点选的清单 (计划书 F1-5)。

    每项含对象类型、ID 与简短文本, 让用户在"无法唯一定位"时直接选择,
    而不是只收到一条错误消息。
    """
    out: list[dict] = []
    for assumption_id, text in (context.get("assumptions") or {}).items():
        out.append({"kind": "assumption", "id": assumption_id, "text": text})
    for claim_id, text in (context.get("claims") or {}).items():
        out.append({"kind": "claim", "id": claim_id, "text": text})
    for step_key, text in (context.get("steps") or {}).items():
        out.append({"kind": "step", "id": step_key, "text": text})
    return out


def _claim_category(claim: Claim) -> str:
    """命题在能力矩阵里的问题类型 (计划书 §7.2)。

    先看命题类型本身; 定义性命题再区分单调性/恒等/不等式, 以便能力声明
    不会把"需要数据的描述性命题"当成"可符号核验的定义性命题"。
    """
    if claim.claim_type != ClaimType.definitional:
        return claim.claim_type.value
    if claim.expr and claim.wrt:
        return "monotonicity"
    if claim.relation == Relation.eq:
        return "identity"
    if claim.relation in (Relation.ge, Relation.le, Relation.gt, Relation.lt):
        return "inequality"
    return "definitional"


@dataclass
class ResearchBudget:
    max_actions: int = 40
    max_tool_calls: int = 60
    no_progress_limit: int = 3
    max_routes: int = 3
    # ---- 资源预算 (计划书 §9.3): 0 表示不限制 ----
    max_tokens: int = 0
    max_cost_usd: float = 0.0
    max_wall_seconds: float = 0.0

    def exhausted_reason(self, *, actions: int, tool_calls: int, tokens: int,
                         cost_usd: float, elapsed: float) -> str:
        """返回触顶的原因; 未触顶返回空串。"""
        if actions >= self.max_actions:
            return f"动作预算耗尽 ({actions}/{self.max_actions})"
        if tool_calls >= self.max_tool_calls:
            return f"工具调用预算耗尽 ({tool_calls}/{self.max_tool_calls})"
        if self.max_tokens and tokens >= self.max_tokens:
            return f"token 预算耗尽 ({tokens}/{self.max_tokens})"
        if self.max_cost_usd and cost_usd >= self.max_cost_usd:
            return f"费用预算耗尽 (${cost_usd:.4f}/${self.max_cost_usd})"
        if self.max_wall_seconds and elapsed >= self.max_wall_seconds:
            return f"墙钟预算耗尽 ({elapsed:.0f}s/{self.max_wall_seconds:.0f}s)"
        return ""


@dataclass
class EngineResult:
    snapshot: ResearchSnapshot
    decisions: list[dict] = field(default_factory=list)
    gate: GateResult | None = None
    needs_clarification: bool = False
    needs_confirmation: bool = False
    candidates: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tool_calls: int = 0
    gaps: list[dict] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    # 实际花费与停止原因 (计划书 §9.3: 实际花费与预估分开, 超限能保存部分报告)
    usage: dict = field(default_factory=dict)
    stopped_reason: str = ""
    # R4: 收尾时发现阻塞性写作缺口并已回流为研究义务 → 研究应当继续, 而不是交付
    feedback_reopened: bool = False


class TheoryEngine:
    def __init__(
        self,
        spec: ResearchSpec,
        store: ResearchStore | None = None,
        runner: VerificationRunner | None = None,
        budget: ResearchBudget | None = None,
        novelty_lookup: Callable[[Claim], list[NoveltyComparisonRow]] | None = None,
        llm=None,
        available: dict[str, bool] | None = None,
        knowledge: KnowledgeService | None = None,
        proposer: Callable[[dict], dict | None] | None = None,
        usage_hook: Callable[..., None] | None = None,
        run_id: str = "",
        search_fn: Callable[[str, int], list[dict]] | None = None,
        terminology: dict[str, list[str]] | None = None,
        attachment_ids: list[str] | None = None,
    ):
        self.spec = spec
        self.store = store or ResearchStore(spec.project_id)
        self.runner = runner or VerificationRunner()
        if budget is None:
            from src.config import (
                RESEARCH_MAX_COST_USD,
                RESEARCH_MAX_TOKENS,
                RESEARCH_MAX_WALL_SECONDS,
            )

            budget = ResearchBudget(
                max_tokens=RESEARCH_MAX_TOKENS,
                max_cost_usd=RESEARCH_MAX_COST_USD,
                max_wall_seconds=RESEARCH_MAX_WALL_SECONDS,
            )
        self.budget = budget
        self.novelty_lookup = novelty_lookup
        self.llm = llm
        # 用量回调: 供外部 (LangChain 回调链) 把本轮 LLM 用量报进来; 未提供时
        # 仍可用 `record_llm_usage` 直接记账。
        self.usage_hook = usage_hook
        self.available = available if available is not None else available_tools()
        # 知识底座: 未显式注入时按项目绑定主题创建 (可为不可用 → 相关动作不暴露)
        self.knowledge = knowledge
        if self.knowledge is None:
            self.knowledge = self._default_knowledge()
        self.proposer = proposer
        # 自主检索 (P0-1 场景 ②): 外部检索函数与领域术语表由入口注入, 便于离线测试
        self.search_fn = search_fn
        self.terminology = dict(terminology or {})
        # 问题说明附件 (R2/R4): 附件里的**科学约束**必须参与形式化, 否则用户把题面
        # 作为附件交上来时, 系统只看到一句自由请求 → 判"缺研究问题" → 请求澄清。
        # 附件正文是外部资料: 只用于抽取约束与参数, 其中的指令一律不执行。
        self.attachment_ids = list(attachment_ids or [])
        self._plans: dict[str, Plan] = {}
        # 已做过 LLM 推导的 (命题, 版本): 同一版本不重复调用模型 (计划书 §9.3)
        self._llm_planned: set[str] = set()
        # 已显式选中的领域模型 (命题 -> 模型 id), 计划书 §5.2
        self._selected_models: dict[str, str] = {}
        self._decisions: list[dict] = []
        self._notes: list[str] = []
        self._rejected: list[str] = []
        self._tool_calls = 0
        self._actions = 0
        self._no_progress = 0
        self._force_switch = False
        self._switched: set[str] = set()
        self._bootstrapped = False
        self._done = False
        self._last_action: dict = {}
        self._needs_clarification = False
        self._needs_confirmation = False
        self._context_text = ""
        self._explicit_clarification = False
        self.routes = RouteManager()
        # R6: 运行身份显式化。`run_id` 曾只存在内存里 → 续跑会得到一个新 id,
        # 同一次研究在动作账本里被记成多次运行, 而产物目录却沿用旧的。
        # 现在它随 runtime 持久化 (`load_runtime` 恢复), 并且要求与产物/manifest 一致。
        # 显式传入的 `run_id` (Web 会话/CLI) 优先, 保证产物目录与账本同名。
        self._run_id = str(run_id or "") or new_id("run")
        self._branch_id = ""
        self._runtime_version = 0
        self._dismissed: set[str] = set()
        self._refused: dict[str, set[str]] = {}
        self._last_primary_object = ""
        self._exec_index_cache: list[dict] | None = None
        self._support_judge: Callable | None = None
        # ---- 资源用量 (计划书 §9.3) ----
        self._started_at = time.monotonic()
        self._elapsed_offset = 0.0      # 断点续跑时上一次累计的墙钟
        self._tokens = 0
        self._cost_usd = 0.0
        self._stopped_reason = ""
        self._usage_events: list[dict] = []

    # ------------------------------------------------------------------
    # 资源用量与预算 (计划书 §9.3)
    # ------------------------------------------------------------------
    def record_llm_usage(self, model: str = "", usage: dict | None = None,
                         stage: str = "research") -> None:
        """登记一次 LLM 调用的用量 (token/费用)。无用量信息时静默忽略。"""
        if not usage:
            return
        in_tokens = int(usage.get("input_tokens", 0) or 0)
        out_tokens = int(usage.get("output_tokens", 0) or 0)
        total = int(usage.get("total_tokens", 0) or 0) or (in_tokens + out_tokens)
        if total <= 0:
            return
        from src.utils.cost_tracker import estimate_cost

        self._tokens += total
        self._cost_usd += estimate_cost(model or self._model_name(), in_tokens, out_tokens)
        self._usage_events.append({"stage": stage, "model": model or self._model_name(),
                                   "input_tokens": in_tokens, "output_tokens": out_tokens,
                                   "total_tokens": total})
        if self.usage_hook is not None:
            try:
                self.usage_hook(model=model, usage=usage, stage=stage)
            except Exception:  # noqa: BLE001 - 外部记账失败不得影响研究
                pass

    def metered_llm(self, stage: str = "research"):
        """返回会**自动记账**的 LLM 包装; 无 LLM 时返回 None。

        研究循环里的模型调用 (子查询/证据判定/意图解析) 都走这里,
        否则 token/费用预算会永远为 0 而形同虚设。
        """
        if self.llm is None:
            return None
        from src.utils.cost_tracker import MeteredLLM

        return MeteredLLM(self.llm, self.record_llm_usage, stage=stage)

    def _model_name(self) -> str:
        name = getattr(self.llm, "model_name", "") or getattr(self.llm, "model", "")
        if not name:
            try:
                from src.config import LLM_CONFIG

                name = LLM_CONFIG.get("model", "")
            except Exception:  # noqa: BLE001
                name = ""
        return str(name or "")

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_offset + (time.monotonic() - self._started_at)

    def usage_summary(self) -> dict:
        """实际花费 (与预估分开, 计划书 §9.3)。"""
        return {
            "actions": self._actions,
            "max_actions": self.budget.max_actions,
            "tool_calls": self._tool_calls,
            "max_tool_calls": self.budget.max_tool_calls,
            "tokens": self._tokens,
            "max_tokens": self.budget.max_tokens,
            "cost_usd": round(self._cost_usd, 6),
            "max_cost_usd": self.budget.max_cost_usd,
            "wall_seconds": round(self.elapsed_seconds, 1),
            "max_wall_seconds": self.budget.max_wall_seconds,
            "llm_calls": len(self._usage_events),
        }

    def _budget_exhausted(self) -> str:
        return self.budget.exhausted_reason(
            actions=self._actions, tool_calls=self._tool_calls, tokens=self._tokens,
            cost_usd=self._cost_usd, elapsed=self.elapsed_seconds)

    def _default_knowledge(self) -> KnowledgeService | None:
        topic = self.spec.domain or self.spec.original_request or ""
        if not topic:
            return None
        try:
            # 只探测已存在的知识底座: 为每条命题构造服务不应创建空库文件
            service = KnowledgeService(topic, create_if_missing=False)
            return service if service.available else None
        except Exception:  # noqa: BLE001
            return None

    @property
    def knowledge_available(self) -> bool:
        """知识底座是否可用于研究动作 (非空); 空底座不暴露检索/读取动作。"""
        return bool(self.knowledge and self.knowledge.usable)

    @property
    def retrieval_available(self) -> bool:
        """能否开展检索类动作: 有可用资料库, 或策略授权自主检索 (P0-1 场景 ②)。"""
        if self.knowledge_available:
            return True
        return self.spec.source_policy in (SourcePolicy.autonomous, SourcePolicy.both)

    def _retrieval_topic(self) -> str:
        """自主检索时的资料主题 (无绑定资料库时按项目建一个可读的库)。"""
        return (self.spec.source_set_id or self.spec.domain
                or f"auto-{self.spec.project_id}")

    def _record_coverage(self, coverage) -> None:
        """把本轮检索的覆盖记录累加到问题规格上 (多轮检索不覆盖历史)。"""
        if self.spec.coverage is None:
            self.spec.coverage = coverage
        else:
            self.spec.coverage.absorb(coverage)
        self.append_step(
            writes=[(KIND_SPEC, self.spec.problem_id, self.spec.model_dump(mode="json"))],
            events=[("retrieval_coverage", {
                "problem_id": self.spec.problem_id,
                "policy": coverage.policy.value,
                "queries": len(coverage.queries),
                "hits": coverage.hits,
                "ingested": coverage.ingested,
                "fulltext_available": coverage.fulltext_available,
                "abstract_only": coverage.abstract_only,
                "executed": coverage.executed,
                "uncovered": coverage.uncovered,
            })],
            idempotency_key=f"coverage:{self.spec.problem_id}:{len(coverage.queries)}"
                            f":{coverage.hits}:{coverage.ingested}")

    def _failure_next_actions(self) -> list[str]:
        """未决命题各自最近一次失败所允许的下一动作 (去重)。

        已经换过路的命题不再优先 `switch_strategy`: 新分支上它又变成"没试过"的动作,
        会被反复挑中而把预算烧在换路上 (实测让代数验收题偶发因未关闭义务而门槛不过)。
        换路本身仍然可用, 只是不再抢在其他能真正推进的动作前面。
        """
        out: list[str] = []
        for item in self._unresolved_claim_ids():
            for action in self.routes.next_actions_for(item):
                if action == ActionType.switch_strategy.value and item in self._switched:
                    continue
                if action not in out:
                    out.append(action)
        return out

    def _failure_judgement(self) -> str:
        """最近一次失败的可读"下一判断" (写进动作理由, 便于日志回答"为什么这样做")。"""
        claims = self._unresolved_claim_ids()
        for item in reversed(claims):
            failures = self.routes.failures_for(item)
            if failures:
                kind = failures[-1].failure_kind
                hint = failures[-1].recovery_condition or ""
                label = kind.value if kind else failures[-1].kind
                return f"{label}: {hint}" if hint else str(label)
        return ""

    def _unresolved_claim_ids(self) -> list[str]:
        return [c.id for c in self._claims()
                if c.status not in (ClaimStatus.supported, ClaimStatus.refuted)]

    def _refresh_knowledge(self, topic: str) -> None:
        """自主检索入库后重新装配知识服务, 让后续 read_source 能读到全文。"""
        if self.knowledge_available:
            return
        try:
            service = KnowledgeService(topic, create_if_missing=False)
        except Exception:  # noqa: BLE001 - 装配失败时按"无可用资料"处理
            return
        if service.available:
            self.knowledge = service

    # ------------------------------------------------------------------
    # 入口 (整体运行)
    # ------------------------------------------------------------------
    def run(self) -> EngineResult:
        self.load_runtime()
        if (not self._done and not self._needs_confirmation
                and not self.bootstrap()):
            return self.finalize()
        # 澄清是终态: 一旦控制器判定需要澄清, 循环与收尾重开都必须停 (现场 #1–#32)
        while (not self._done and not self._needs_confirmation
               and not self._needs_clarification):
            self.step()
        result = self.finalize()
        # R4: 收尾时若发现**能由研究循环消解**的阻塞性写作缺口, 它已回流为新的义务
        # → 重开研究循环。缺口回流是幂等的, 因此重开不会重复制造同一批缺口;
        # 预算/墙钟仍是最外层护栏 (`_budget_exhausted`)。
        while (result.feedback_reopened and not self._needs_confirmation
               and not self._needs_clarification
               and not self._budget_exhausted()):
            self._done = False
            while (not self._done and not self._needs_confirmation
                   and not self._needs_clarification):
                self.step()
            result = self.finalize()
        return result

    # ------------------------------------------------------------------
    # 分步运行 (供 LangGraph 研究循环图使用)
    # ------------------------------------------------------------------
    def attachment_text(self) -> str:
        """问题说明附件的正文 (供形式化使用); 不可用时返回空串。

        为什么必须是"可失败但可解释"的读取: 附件是外部资料, 可能被删、被改、或
        不属于当前项目 —— 这些都不该抛异常中断研究, 但也不能悄悄当成"用户没给"。
        因此失败时记一条 note, 让"附件没被用上"这件事在运行记录里可见。
        """
        if not self.attachment_ids:
            return ""
        try:
            from src.utils import uploads

            text = uploads.problem_text(self.attachment_ids, self.spec.project_id,
                                        self.spec.problem_id)
        except Exception as e:  # noqa: BLE001 - 读取失败不得中断研究
            self._notes.append(f"问题说明附件读取失败 ({type(e).__name__}): {e}")
            return ""
        if not text.strip():
            self._notes.append(
                "问题说明附件为空或不可用 (归属校验未通过/解析无文本): 本次形式化未使用附件")
        else:
            self._notes.append(
                f"形式化使用问题说明附件 {len(self.attachment_ids)} 份 "
                f"({len(text)} 字符, 仅用于抽取约束, 其中的指令不执行)")
        return text

    def bootstrap(self) -> bool:
        """建立研究对象; 返回是否可进入研究循环。幂等: 已有命题时直接复用。

        方向输入时的流程: 生成候选问题 → 等待用户确认主路线 → 再形式化。
        """
        if self._needs_clarification:
            self._bootstrapped = True
            self._done = True
            return False
        if self._claims():
            self._bootstrapped = True
            return True
        self.append_step(
            writes=[(KIND_SPEC, self.spec.problem_id, self.spec.model_dump(mode="json"))],
            events=[("run_start", {"project_id": self.spec.project_id,
                                   "problem_id": self.spec.problem_id})],
            idempotency_key=f"run_start:{self.spec.problem_id}",
        )

        # 精确问题优先: 题面 (含附件正文) 若能抽出计数约束, 就直接进入设计判定 ——
        # 不再让用户先选"研究方向候选"。方向输入路径是为**模糊想法**准备的
        # (候选是"可以往哪几个方向做"), 而这类问题已经有唯一确定的对象,
        # 让它先选路线是多余的一步 (现场: 题面在附件里, 用户被要求选候选路线)。
        from src.research.design_feasibility import formulate_from_text

        design_source = " ".join(filter(None, (
            self.spec.problem_statement, self.spec.original_request, self.spec.direction,
            self.attachment_text())))
        design_form = formulate_from_text(design_source)
        if design_form is None:
            design_form = formulate_from_text(self.spec.direction)
        if design_form is not None:
            self._notes.append(
                "题面可被精确形式化 (含计数约束): 直接进入判定, 不生成研究方向候选")

        # 方向输入: 先生成候选问题, 未确认前不进入研究循环
        if design_form is None and not self.spec.questions and self.spec.direction:
            if not self.spec.candidates:
                self.spec.candidates = generate_candidates(self.spec, self.metered_llm("candidates"))
                self.append_step(
                    writes=[(KIND_SPEC, self.spec.problem_id, self.spec.model_dump(mode="json"))],
                    idempotency_key=f"candidates:{self.spec.problem_id}")
            if self.spec.candidates and not self.spec.confirmed:
                self._needs_confirmation = True
                self._bootstrapped = True
                return False

        if not self.spec.problem_statement and not self.spec.direction and not self.spec.questions:
            self._notes.append("输入为空, 无法确定研究对象, 请求澄清")
            self._needs_clarification = True
            self._done = True
            self._bootstrapped = True
            return False

        form = formulate(self.spec, self.available)
        # 设计/计数类存在性问题: 用通用必要条件判定给出确切数学结论。
        # S1: 判定不再只写进笔记 —— 它必须变成**命题 + 必要性义务**, 由规则验收
        # 关闭后才允许进入交付门槛 (`design_necessity` 验收方式)。
        # 抽不出计数约束时保持原行为 (逐个命题走普通形式化; 无命题才请求澄清)。
        #
        # 为什么**先于** `form.claims` 判断: 方向输入会先让用户确认候选路线,
        # `confirm_candidate` 把候选择填入 `spec.questions`; 此后 `formulate` 返回的
        # 命题来自候选陈述, **题面里的精确约束就再也参与不上了** —— 现场表现是
        # "粘贴了完整题面 (含 v/k/λ 与 b/r), 系统却把它当研究方向, 最后请求澄清"。
        # 只要题面本身能被精确形式化, 就以它为准; 用户已确认的候选取向不丢弃,
        # 作为来源记录在案。
        if design_form is not None and form.claims:
            # 题面能精确形式化, 而当前命题来自用户确认的候选路线: **以题面为准**。
            # 只加一条命题会让两条针对同一问题的结论并存 (候选那条把设计命题挤到
            # 后面, 甚至两条都被当成本次交付的结论) —— 现场表现就是"粘贴了完整题面
            # 却请求澄清"。候选路线不静默丢弃, 作为来源记在笔记里。
            confirmed = self.spec.selected_candidate_id or (
                self.spec.questions[0].statement[:80] if self.spec.questions else "")
            self._notes.append(
                "题面可被精确形式化: 以计数约束为准生成命题, 覆盖由候选路线 "
                f"({confirmed or '未记录'}) 形式化出的 {len(form.claims)} 条命题; "
                "候选路线仅作来源记录")
            candidate_claim_ids = {c.id for c in form.claims}
            form.claims = list(design_form.claims)
            form.obligations = [o for o in form.obligations
                                if o.claim_id not in candidate_claim_ids]
            for note in design_form.notes:
                if note not in self._notes:
                    self._notes.append(note)
            form.obligations.extend(design_form.obligations)
        elif design_form is not None and not form.claims:
            # 判定结论进入运行笔记 (过程可见), 命题/义务本身随 form 落盘
            for note in design_form.notes:
                if note not in self._notes:
                    self._notes.append(note)
            form.claims.extend(design_form.claims)
            form.obligations.extend(design_form.obligations)
        if not form.claims:
            self._notes.append(
                "无法可靠形式化问题 (缺少量词/变量域/关系/显式表达式), 请求澄清")
            if form.unknown_fields:
                self._notes.append("unknown_fields: " + ", ".join(form.unknown_fields))
            if form.notes:
                self._notes.extend(form.notes)
            self._needs_clarification = True
            self._done = True
            self._bootstrapped = True
            return False
        self._persist_formulation(form)
        # 为每条命题建立初始路线 (可换路的基础)
        for claim in form.claims:
            self.routes.ensure_route(claim.id, goal=claim.statement,
                                     strategy=self._plan_strategy(claim))
        self._save_routes()
        # 计划书 §7.2: 声明该问题类型的前置条件; 缺数据/缺后端/未登记类型必须显式说出来,
        # 而不是把"需要数据的问题"当成"可符号核验的命题"直接去证。
        self._declare_capabilities(form.claims)
        self._needs_confirmation = False
        self._bootstrapped = True
        return True

    def _declare_capabilities(self, claims: list[Claim]) -> list[dict]:
        """为每条命题登记能力声明, 并把缺前置条件的部分转成澄清请求。"""
        from src.research.capability import declare_capability

        out: list[dict] = []
        for claim in claims:
            decl = declare_capability(
                _claim_category(claim), available=self.available,
                has_data=bool(claim.study.data_ref or claim.study.rows),
                has_design=claim.study.design.value not in ("", "none"))
            record = {"claim_id": claim.id, "problem_id": self.spec.problem_id,
                      "category": decl.category, "action": decl.action,
                      "questions": list(decl.questions),
                      "declaration": decl.describe()}
            out.append(record)
            # 声明只登记在事件流里: 不写命题状态、不关闭义务
            self.append_step(
                writes=[],
                events=[("capability_declared", record)],
                idempotency_key=f"capability:{claim.id}:{decl.category}:{decl.action}",
            )
            if decl.blocked:
                for question in decl.questions:
                    note = f"{claim.id}: {question}"
                    if note not in self._notes:
                        self._notes.append(note)
        return out

    def plan_candidates(self) -> list:
        """生成 (或返回) 候选研究问题, 供用户确认。"""
        if not self.spec.candidates:
            self.spec.candidates = generate_candidates(self.spec, self.metered_llm("candidates"))
            self.append_step(
                writes=[(KIND_SPEC, self.spec.problem_id, self.spec.model_dump(mode="json"))],
                idempotency_key=f"candidates:{self.spec.problem_id}")
        return list(self.spec.candidates)

    def confirm_candidate(self, index: int = 0, candidate_id: str = "") -> bool:
        """确认主路线: 把候选转为待研究问题, 允许进入研究循环。

        候选一旦确认即绑定稳定 ID, 后续不得"重新生成候选再按旧序号选择"。
        `candidate_id` 优先于 `index`: 历史恢复后候选顺序可能变化, 按序号确认
        会选到另一条路线 (计划书 F1-4)。
        """
        candidates = self.spec.candidates or self.plan_candidates()
        if not candidates:
            return False
        if candidate_id:
            match = next((i for i, c in enumerate(candidates)
                          if c.candidate_id == candidate_id), None)
            if match is None:
                self._notes.append(
                    f"候选 ID {candidate_id} 不在当前候选列表内, 拒绝按旧序号猜测")
                return False
            index = match
        index = max(0, min(int(index), len(candidates) - 1))
        chosen = candidates[index]
        if not chosen.candidate_id:
            chosen.candidate_id = new_id("cand")
        chosen.confirmed_version = self.spec.version
        self.spec.questions = [chosen]
        self.spec.confirmed = True
        self.spec.research_type = chosen.category
        self.spec.selected_candidate_id = chosen.candidate_id
        # P0-2: 用户确认候选即冻结问题契约; 之后改题必须新版本或新问题
        if self.spec.contract is not None:
            self.spec.contract.frozen_version = self.spec.version
        self._needs_confirmation = False
        self.append_step(
            writes=[(KIND_SPEC, self.spec.problem_id, self.spec.model_dump(mode="json"))],
            events=[("candidate_confirmed",
                     {"index": index, "candidate_id": chosen.candidate_id,
                      "statement": chosen.statement,
                      "contract_kind": (self.spec.contract.task_kind.value
                                        if self.spec.contract else "")})],
            idempotency_key=f"confirm:{self.spec.problem_id}:{chosen.candidate_id}",
        )
        self.save_runtime()
        return True

    def resume_after_reflow(self) -> None:
        """回到研究循环继续处理**回流出的阻塞义务** (图/Web 路径专用)。

        背景: 收尾时写作缺口会回流成新的研究义务 (计划书 §3 R4)。`run()` 有内层循环
        能继续处理, 但图路径是逐步驱动: 收尾节点结束时引擎的 `done` 已被置 True,
        而它是**持久化**的 —— 图回到 `step` 后 `step()` 会立刻返回, 回流出的义务
        永远无人处理, 运行以"有一条未关闭义务"收尾而无法成文 (现场 #11)。
        本方法只解除完成态, 不改动任何结论、义务或验证记录。
        """
        self._done = False
        self._stopped_reason = ""
        self._notes.append("写作缺口回流: 重新进入研究循环消解该义务")
        self.save_runtime()

    def step(self) -> dict:
        """执行一个 决策+动作+验证 循环体; 返回本次动作摘要。"""
        if self._done:
            return self._last_action
        if not self._bootstrapped and not self.bootstrap():
            return {"action": "clarify_problem", "object_id": "",
                    "reason": "无法形式化, 请求澄清"}
        # 资源预算: 动作 / 工具调用 / token / 费用 / 墙钟任一触顶都停止并导出部分报告
        exhausted = self._budget_exhausted()
        if exhausted:
            self._stopped_reason = exhausted
            self._notes.append(f"研究因预算停止: {exhausted} (以下为部分结果, 不代表研究已完成)")
            self._done = True
            return {"action": "stop_with_report", "object_id": "", "reason": exhausted}
        # 每步重新计算决策状态 (含精确避让集合)
        state = self._compute_state()
        # 缺口落盘: 冻结快照从存储读 KIND_GAP, 不落盘就会得到空的 gaps.json (实测)
        self._persist_gaps(state.get("gaps") or [])
        action = coordinator.decide(state, proposer=self._propose)
        self._record_proposal_audit(state)
        self._actions += 1
        info = {
            "action": action.action_type.value, "object_id": action.object_id,
            "reason": action.reason or (action.preconditions[0] if action.preconditions else ""),
            "target_gap": action.target_gap,
            "available_actions": list(state.get("available_actions", [])),
        }
        self._decisions.append(info)
        self._last_action = info
        # 澄清是**终态判断**, 不是一个普通动作 (现场 #1–#32: 控制器连续 16 轮提议
        # clarify_problem, 每轮都被判"有进展", 于是预算耗尽而研究没有任何推进)。
        # 一旦决定需要澄清, 就必须停下来把问题交给用户, 而不是继续循环。
        if action.action_type == ActionType.clarify_problem:
            self._needs_clarification = True
            self._done = True
            if not any("澄清" in n for n in self._notes):
                self._notes.append(
                    "结论: 当前输入不足以形成可检验的研究对象, 请求澄清"
                    + (f" (依据: {action.reason})" if action.reason else ""))
            self.save_runtime()
            return info
        progressed = self._dispatch(action)
        self._track_progress(progressed)
        if action.action_type in (ActionType.synthesize_results, ActionType.stop_with_report,
                                 ActionType.deliver_partial):
            self._done = True
        self.save_runtime()
        return info

    def _propose(self, state: dict) -> dict | None:
        """语义提议 (计划书 §5.3-3): 有 proposer 时用模型提议, 否则走确定性排序。"""
        if self.proposer is None:
            return None
        pack = state.get("context_text", "")
        try:
            return self.proposer({"context": pack, "state": state})
        except Exception as e:  # noqa: BLE001
            self._rejected.append(f"提议失败: {e}")
            return None

    def _record_proposal_audit(self, state: dict) -> None:
        """把本轮"提议与拒绝理由"写进研究事件流 (计划书 §3 R1)。

        只写一次: 记的是最后一次 decide 的提议审计, 不是累积列表。
        """
        audit = state.get("proposal_audit") or []
        if not audit:
            return
        accepted = next((a for a in audit if a.get("accepted")), None)
        rejected = [a for a in audit if not a.get("accepted")]
        if accepted is None and not rejected:
            return
        self.append_step(
            writes=[],
            events=[("proposal", {"accepted": accepted, "rejected": rejected})],
            idempotency_key=("proposal:"
                             + hash_payload({"a": accepted, "r": rejected,
                                             "step": self._actions})),
        )

    @property
    def done(self) -> bool:
        return self._done

    @property
    def needs_clarification(self) -> bool:
        return self._needs_clarification

    @property
    def needs_confirmation(self) -> bool:
        return self._needs_confirmation

    @property
    def decisions(self) -> list[dict]:
        return list(self._decisions)

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def run_id(self) -> str:
        """本次研究的运行身份 (与产物目录/manifest/动作账本同一个值)。"""
        return self._run_id

    @property
    def branch_id(self) -> str:
        """问题内当前分支身份 (由 `branch_for` 决定, 与动作账本同一个值)。"""
        return self._branch_id or "no-route"

    def identity(self) -> dict:
        """统一的身份四元组 (计划书 R6): project / problem / run / branch。"""
        return {"project_id": self.spec.project_id, "problem_id": self.spec.problem_id,
                "run_id": self._run_id, "branch_id": self.branch_id}

    def finalize(self) -> EngineResult:
        return self._finalize(self._needs_clarification)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def append_step(self, writes, events=None, idempotency_key: str = "") -> dict:
        """原子研究步骤提交; 幂等键命中时返回空 dict (调用方不得重复执行)。"""
        try:
            return self.store.submit_step(writes, events or [], idempotency_key)
        except StepAlreadyApplied:
            return {}

    def load_runtime(self) -> None:
        data = self.store.get(KIND_RUNTIME, self.spec.problem_id, strip_meta=False)
        if not data:
            return
        self._runtime_version = int(data.get("__version__", 1) or 1)
        # R6: 恢复运行身份 (续跑必须沿用同一个 run_id, 否则账本/产物/快照对不上)
        persisted_run = str(data.get("run_id", "") or "")
        if persisted_run:
            self._run_id = persisted_run
        self._branch_id = str(data.get("branch_id", "") or "")
        self._actions = int(data.get("actions", 0))
        self._tool_calls = int(data.get("tool_calls", 0))
        self._no_progress = int(data.get("no_progress", 0))
        self._done = bool(data.get("done", False))
        self._needs_clarification = bool(data.get("needs_clarification", False))
        self._needs_confirmation = bool(data.get("needs_confirmation", False))
        self._bootstrapped = bool(data.get("bootstrapped", False))
        self._decisions = list(data.get("decisions", []))
        self._notes = list(data.get("notes", []))
        self._rejected = list(data.get("rejected_proposals", []))
        self.routes = RouteManager.load(data.get("route_state"))
        # 恢复已花费资源: 续跑不得重置 token/费用/墙钟预算 (§9.3)
        self._tokens = int(data.get("tokens", 0) or 0)
        self._cost_usd = float(data.get("cost_usd", 0.0) or 0.0)
        self._elapsed_offset = float(data.get("elapsed_seconds", 0.0) or 0.0)
        self._usage_events = list(data.get("usage_events", []) or [])
        self._stopped_reason = str(data.get("stopped_reason", "") or "")
        # 进程中断后仍有 running 的工具调用 → 外部状态未知, 显式记录
        unresolved = self.store.unresolved_tool_runs()
        if unresolved:
            self._notes.append(
                f"有 {len(unresolved)} 次工具调用在中断前未登记结果, 状态未知, 需对账或重跑")

    def save_runtime(self) -> None:
        payload = {
            # R6: 身份随运行状态持久化 —— 断点续跑沿用同一个 run/branch
            "run_id": self._run_id,
            "branch_id": self.branch_id,
            "actions": self._actions,
            "tool_calls": self._tool_calls,
            "no_progress": self._no_progress,
            "done": self._done,
            "needs_clarification": self._needs_clarification,
            "needs_confirmation": self._needs_confirmation,
            "bootstrapped": self._bootstrapped,
            "decisions": self._decisions,
            "notes": self._notes,
            "rejected_proposals": self._rejected,
            "route_state": self.routes.dump(),
            # 资源用量随运行状态持久化: 断点续跑不得重置已花费的 token/费用/墙钟
            "tokens": self._tokens,
            "cost_usd": self._cost_usd,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "usage_events": self._usage_events[-200:],
            "stopped_reason": self._stopped_reason,
        }
        version = self.store.put(KIND_RUNTIME, self.spec.problem_id, payload,
                                 version=self._runtime_version + 1)
        self._runtime_version = version

    def _save_routes(self) -> None:
        """路线与失败档案持久化 (版本只增不减)。"""
        for route in self.routes.routes:
            self.store.put(KIND_ROUTE, route.id, route.model_dump(mode="json"))

    def _save_bundle(self, objects: list[tuple[str, Any]], event: tuple[str, dict] | None = None,
                     key: str = "") -> None:
        """把一个研究对象集合 + 事件作为一个原子步骤提交。"""
        writes = [(kind, obj.id, obj.model_dump(mode="json")) for kind, obj in objects]
        events = [event] if event else []
        self.append_step(writes, events, idempotency_key=key)

    def _persist_formulation(self, form) -> None:
        # R6: 命题必须记录所属研究问题 —— 同一项目可有多个问题, 工作台与
        # 问题级查询都靠这个归属来隔离对象 (否则 A 问题的结论会显示在 B 问题上)。
        for claim in form.claims:
            if not claim.problem_id:
                claim.problem_id = self.spec.problem_id
        objects = ([(KIND_ASSUMPTION, a) for a in form.assumptions]
                   + [(KIND_DEFINITION, d) for d in form.definitions]
                   + [(KIND_CLAIM, c) for c in form.claims]
                   + [(KIND_OBLIGATION, o) for o in form.obligations])
        writes = [(kind, obj.id, obj.model_dump(mode="json")) for kind, obj in objects]
        self.append_step(writes, [("formulated", {"claims": len(form.claims),
                                                  "obligations": len(form.obligations),
                                                  "problem_id": self.spec.problem_id})],
                         idempotency_key=f"formulate:{self.spec.problem_id}")

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def _load(self, kind: str, model) -> list:
        """读取某类对象的最新版本, 并把权威版本号写回对象的 version 字段。"""
        versions = self.store.version_index(kind)
        out = []
        for data in self.store.list_latest(kind):
            obj = model.model_validate(data)
            if "version" in model.model_fields:
                obj.version = versions.get(obj.id, obj.version)
            out.append(obj)
        return out

    def _claims(self) -> list[Claim]:
        """当前**研究问题**的命题 (R6: 同项目多问题必须按问题隔离)。

        归属判定:
        - 命题记录了 `problem_id` → 必须与本问题一致;
        - 命题未记录 (旧库遗留) → 仅当它不是别的已知问题的命题时才算本问题的。
        早期实现返回项目下全部命题, 导致第二个问题直接复用第一个问题的命题而
        不再形式化 (新问题"看起来已经有结论")。
        """
        claims = self._load(KIND_CLAIM, Claim)
        pid = self.spec.problem_id
        known = {str(c.problem_id) for c in claims if c.problem_id}
        selected = [c for c in claims
                    if (c.problem_id or pid) == pid or (not c.problem_id and pid not in known)]
        return selected

    def _claim_belongs_to_problem(self, claim_id: str) -> bool:
        return any(c.id == claim_id for c in self._claims())

    def _obligations(self) -> list[ProofObligation]:
        return self._load(KIND_OBLIGATION, ProofObligation)

    def _verifications(self) -> list[VerificationRecord]:
        return self._load(KIND_VERIFICATION, VerificationRecord)

    def _evidence(self) -> list[SourceEvidence]:
        return self._load(KIND_EVIDENCE, SourceEvidence)

    # ------------------------------------------------------------------
    # 逐命题证据归属 (计划书 §3 R3)
    # ------------------------------------------------------------------
    def _evidence_links(self) -> list[EvidenceLink]:
        return self._load(KIND_EVIDENCE_LINK, EvidenceLink)

    def _claim_of_evidence(self, evidence_id: str) -> str:
        """反查一条证据归属的命题 (无归属返回空)。"""
        item = next((e for e in self._evidence() if e.id == evidence_id), None)
        if item is not None and item.claim_id:
            return item.claim_id
        link = next((lk for lk in self._evidence_links() if lk.source_ref.id == evidence_id),
                    None)
        return link.claim_ref.id if link else ""

    def evidence_links_for(self, claim: Claim) -> list[EvidenceLink]:
        """该命题的 `(claim_id, claim_version, evidence_id, evidence_version)` 关系。

        只返回绑定到**当前版本**的关系: 命题换代后旧关系视为过期 (与"验证结果
        绑定具体版本"的规则一致), 否则旧命题的证据会继续为新一代命题记账。
        """
        return [link for link in self._evidence_links()
                if link.claim_ref.id == claim.id
                and (not link.claim_ref.version or link.claim_ref.version == claim.version)]

    def evidence_for_claim(self, claim: Claim) -> list[SourceEvidence]:
        """**属于该命题**的证据 (逐命题归属的唯一入口)。

        证据归属按两条确定来源判定, 不从"项目下所有证据"里推断:
        1. 证据自身的 `claim_id` (检索/挂接时就写入);
        2. 该命题的 `EvidenceLink.claim_ref` (带版本的关系记录)。
        没有任何归属的证据属于"尚未归属", 不得让别的命题的缺口消失。
        """
        linked = {link.source_ref.id for link in self.evidence_links_for(claim)}
        return [e for e in self._evidence()
                if e.claim_id == claim.id or e.id in linked]

    @property
    def _unattributed_evidence(self) -> list[SourceEvidence]:
        return [e for e in self._evidence() if not e.claim_id]

    def link_evidence(self, claim: Claim, item: SourceEvidence,
                      relation=None, *, note: str = "") -> EvidenceLink:
        """为一条证据建立/更新到该命题的归属关系 (幂等: 同命题同证据只留一条)。"""
        relation = relation if relation is not None else item.support
        existing = next((link for link in self.evidence_links_for(claim)
                         if link.source_ref.id == item.id), None)
        link = existing or EvidenceLink(
            claim_ref=ObjectRef(id=claim.id, version=claim.version),
            source_ref=ObjectRef(id=item.id, version=item.version))
        link.relation = relation
        link.excerpt = item.support_evidence or (item.excerpt or "")[:300]
        link.locator = item.location
        link.condition_match = relation in (SupportKindOfEvidence.supports,
                                            SupportKindOfEvidence.partially_supports)
        link.condition_notes = note or item.support_reason
        link.review_status = (ValidationStatus.verified
                              if relation != SupportKindOfEvidence.insufficient
                              else ValidationStatus.unchecked)
        link.reviewer = item.reviewer or "rule"
        self.store.put(KIND_EVIDENCE_LINK, link.id, link.model_dump(mode="json"))
        return link

    def _models(self) -> list[ResearchModel]:
        return self._load(KIND_MODEL, ResearchModel)

    def _assumptions(self) -> list[Assumption]:
        return self._load(KIND_ASSUMPTION, Assumption)

    # ------------------------------------------------------------------
    # 状态与缺口
    # ------------------------------------------------------------------
    def _gaps(self, claims: list[Claim], obligations: list[ProofObligation]) -> list[ResearchGap]:
        """把"当前缺什么"显式化为缺口对象 (计划书 §4.1), 控制器据此推进。

        出场顺序即优先级: **证据缺口 > 未关闭义务 > 其他**。
        证据缺口优先是因为它的补全 (检索→原文→支持关系判定) 会改变后续义务的
        可判定性; 反之先跑工具只会在缺条件的情况下得到一堆 unknown。
        """
        gaps: list[ResearchGap] = []
        evidence_gaps: list[ResearchGap] = []
        obligation_gaps: list[ResearchGap] = []
        models = self._models()
        attempts = self.store.list_latest(KIND_ATTEMPT)
        # 与决策排序使用同一优先级: 否则规则型义务会先于其依赖的估计被选中
        obligations = sorted(obligations,
                             key=lambda o: OBLIGATION_PRIORITY.get(o.kind, 5))

        for claim in claims:
            # R3: 证据归属必须逐命题 —— 用项目下全部证据统计会让 A 命题的证据
            # 把 B 命题的证据缺口"填掉"。这里只取属于本命题的证据。
            own_evidence = self.evidence_for_claim(claim)
            supported_evidence = [e for e in own_evidence
                                  if e.support in (SupportKindOfEvidence.supports,
                                                   SupportKindOfEvidence.partially_supports)]
            # 已定论的命题只跳过**已有文献依据**的那些: "机器验证通过"不等于"有文献
            # 依据"。实测 (真实运行 liveauto): 命题 status=supported 且
            # validation_status=verified, 但证据条数为 **0**; 早期实现按 status 直接
            # `continue`, 于是它永不产生证据缺口、也不触发检索 —— 那次运行的全部外部
            # 检索都发生在 publication 层 (研究冻结之后), 对研究本身毫无作用
            # (计划书 P0-1/P0-2)。
            concluded = claim.status in (ClaimStatus.supported, ClaimStatus.refuted)
            needs_literature = (self.retrieval_available and not own_evidence)
            if concluded and not needs_literature:
                continue
            claim_obligations = [o for o in obligations if o.claim_id == claim.id]
            unjudged = [e for e in own_evidence
                        if e.support == SupportKindOfEvidence.insufficient]
            # 计划书 §6.1: 研究一开始就应先掌握已有定义/条件, 不应等到缺条件才检索。
            # 因此只要**检索能力可用**且尚无"已判定支持"的证据, 就存在证据缺口。
            # 这里同样不能用 `knowledge_available`: 本地库为空但授权自主检索时, 外部
            # 检索仍可用 —— 否则缺口不产生, 检索请求也就无从生成 (计划书 P0-1 的第二处
            # 一票否决, 与 `_retrieval_requests` 的那处配合才构成"自主检索被整个跳过")。
            if self.retrieval_available and not supported_evidence:
                reason = ("尚有候选证据未判定支持关系" if unjudged
                          else "尚未检索已有定义/模型/结论, 无法判断当前问题的新颖性与适用条件")
                evidence_gaps.append(ResearchGap(
                    gap_type=GapType.missing_evidence,
                    target_ref=ObjectRef(id=claim.id, version=claim.version),
                    statement=reason,
                    resolving_actions=[ActionType.retrieve_targeted.value,
                                       ActionType.read_source.value,
                                       ActionType.extract_result.value,
                                       ActionType.interpret_evidence.value]
                    if unjudged else
                    [ActionType.retrieve_targeted.value, ActionType.propose_model.value],
                    resolution_criteria="取得可定位原文并显式判定支持/反对关系",
                ))
            if not claim_obligations:
                obligation_gaps.append(ResearchGap(
                    gap_type=GapType.missing_model if not models else GapType.missing_definition,
                    target_ref=ObjectRef(id=claim.id, version=claim.version),
                    statement=f"{claim.id} 尚未拆解出证明义务 (目标: {claim.statement[:80]})",
                    resolving_actions=[ActionType.plan_proof.value,
                                       ActionType.propose_model.value],
                    resolution_criteria="生成至少一条可核验义务",
                ))
                continue
            open_required = [o for o in claim_obligations
                             if o.status == ObligationStatus.open and o.required]
            if open_required:
                # 该命题尚无任何推导尝试时, 先拆解出可审查步骤 (含反方审查),
                # 否则一上来就调用验证工具只会在缺条件时反复得到"无法判定"。
                has_attempt = any(a.get("target_claim_id") == claim.id for a in attempts)
                resolving = [ActionType.check_step.value, ActionType.plan_proof.value]
                if not has_attempt:
                    resolving.insert(0, ActionType.derive_step.value)
                obligation_gaps.append(ResearchGap(
                    gap_type=GapType.open_obligation,
                    target_ref=ObjectRef(id=open_required[0].id, version=open_required[0].version),
                    statement=f"未关闭义务: {open_required[0].statement[:100]}",
                    blocking=[claim.id],
                    resolving_actions=resolving,
                    resolution_criteria="义务获得有效验证记录或被明确标为不可判定",
                ))

        # 未判定的候选证据 → 需要判定支持关系。
        # 注意: 缺口的目标对象是**命题**(动作作用对象), 不是证据条目 —— 否则
        # 协调者会把证据 id 交给动作, 路线分支也对不上, 导致动作被反复派发。
        # R3: 逐命题统计 —— 每条命题只数**自己**的未判定证据, 并各自形成缺口,
        # 否则 A 命题的证据会顶替 B 命题的缺口, B 永远不会被推进。
        for claim in claims:
            if claim.status in (ClaimStatus.supported, ClaimStatus.refuted):
                continue
            pending_own = [e for e in self.evidence_for_claim(claim)
                           if e.support == SupportKindOfEvidence.insufficient and e.source_id]
            if not pending_own:
                continue
            evidence_gaps.append(ResearchGap(
                gap_type=GapType.missing_evidence,
                target_ref=ObjectRef(id=claim.id, version=claim.version),
                statement=f"有 {len(pending_own)} 条候选证据尚未判定支持关系",
                resolving_actions=[ActionType.interpret_evidence.value,
                                   ActionType.read_source.value],
                resolution_criteria="每条候选证据显式判定为 支持/反对/部分/背景/不足",
            ))

        # 缺口出场顺序 = 优先级: 证据缺口 > 未关闭义务 > 编码不一致
        # 义务之间按义务种类优先级 + 命题出现顺序排序, 保证先做核心推导再关规则型义务
        claim_order = {c.id: i for i, c in enumerate(claims)}
        obligation_gaps.sort(key=lambda g: (
            OBLIGATION_PRIORITY.get(_obligation_kind(obligations, g.target_ref.id), 5),
            claim_order.get(_claim_of(obligations, g.target_ref.id), 0),
            g.target_ref.id,
        ))
        gaps.extend(evidence_gaps)
        gaps.extend(obligation_gaps)

        # 编码不一致 / 不可判定 → 需要换路或补条件
        for obligation in obligations:
            if obligation.validation_status in (ValidationStatus.encoding_mismatch,
                                                ValidationStatus.invalid_input):
                gaps.append(ResearchGap(
                    gap_type=GapType.encoding_mismatch,
                    target_ref=ObjectRef(id=obligation.id, version=obligation.version),
                    statement=f"验证编码与命题不一致或输入非法: {obligation.detail[:100]}",
                    blocking=[obligation.claim_id],
                    resolving_actions=[ActionType.switch_strategy.value,
                                       ActionType.revise_hypothesis.value],
                    resolution_criteria="修正编码或调整命题范围后重新验证",
                ))
        return gaps

    def _compute_state(self) -> dict:
        claims = self._claims()
        obligations = self._obligations()
        self._claim_versions = {c.id: c.version for c in claims}
        self._obligation_versions = {o.id: o.version for o in obligations}
        attempts = self.store.list_latest(KIND_ATTEMPT)
        novelty_records = self.store.list_latest(KIND_NOVELTY)
        planned_ids = {a.get("target_claim_id") for a in attempts} | set(self._plans.keys())
        novelty_ids = {n.get("claim_id") for n in novelty_records}
        evidence = self._evidence()
        gaps = self._gaps(claims, obligations)

        # 证明计划只针对"尚无义务"的命题: 已有开放义务的命题应直接执行义务核验,
        # 反复规划只会消耗预算 (原实现会把已有关闭义务的命题也当成未规划)。
        claim_ids_with_obligations = {o.claim_id for o in obligations}
        unplanned = [c for c in claims
                     if c.id not in claim_ids_with_obligations
                     and c.id not in planned_ids
                     and c.status == ClaimStatus.proposed]
        open_obligations = [o for o in obligations if o.status == ObligationStatus.open]
        open_obligations.sort(key=lambda o: OBLIGATION_PRIORITY.get(o.kind, 5))
        pending_novelty = [c for c in claims
                           if c.status in (ClaimStatus.supported, ClaimStatus.refuted)
                           and c.novelty_status == NoveltyStatus.unchecked
                           and c.id not in novelty_ids]
        unresolved = [c for c in claims
                      if c.status in (ClaimStatus.proposed, ClaimStatus.in_progress)]
        # 可重试 / 不可判定的命题: 允许否定性结果与换路
        undetermined = [c for c in claims
                        if c.status in (ClaimStatus.blocked, ClaimStatus.in_progress,
                                        ClaimStatus.proposed)]
        retryable = [c for c in undetermined if self.routes.retryable(c.id)]
        failed_routes = [r.model_dump(mode="json") for r in self.routes.routes
                         if r.status == RouteStatus.failed]
        # 无进展时换路: 只要存在"已遇到非科学性困难且尚未用尽路线"的命题, 就应换路
        # 而不是直接输出部分结果。缺少新鲜方法本身就是一个缺口。
        stalls = [c for c in undetermined
                  if self.routes.failures_for(c.id)
                  and not self.routes.scientific_failure(c.id)]
        if self._force_switch:
            gaps.insert(0, ResearchGap(
                gap_type=GapType.route_exhausted,
                target_ref=ObjectRef(
                    id=(stalls[0].id if stalls else (undetermined[0].id if undetermined else "")),
                    version=(stalls[0].version if stalls else 1)),
                statement="当前路线连续无进展, 需要更换研究方法",
                blocking=[c.id for c in (stalls or undetermined)[:1]],
                resolving_actions=[ActionType.switch_strategy.value,
                                   ActionType.design_experiment.value],
                resolution_criteria="产生新路线并保留失败原因, 或把困难部分转为实验规格",
            ))
        remaining = min(self.budget.max_actions - self._actions,
                        self.budget.max_tool_calls - self._tool_calls)

        retrieval_requests = self._retrieval_requests(claims, gaps)
        experimented = {str(d.get("claim_id", "")) for d in self.store.list_latest(KIND_GAP)
                        if str(d.get("id", "")).startswith("exp-")}

        state = {
            "open_obligations": [o.model_dump() for o in open_obligations],
            "unplanned_claims": [c.model_dump() for c in unplanned],
            # 已存在推导尝试 (即已跑过反方审查) 的命题: 未审查时优先审查再验证。
            # 必须按"是否有 attempt 落盘"判断, 不能按动作是否被选中 ——
            # 动作可能因去重被跳过 (同一对象同一版本不重复), 那时并没有真的审查过。
            "reviewed_claims": sorted({str(a.get("target_claim_id", "")) for a in attempts
                                       if a.get("target_claim_id")
                                       and (a.get("steps") or [])}),
            "pending_novelty": [c.model_dump() for c in pending_novelty],
            "unresolved_claims": [c.model_dump() for c in unresolved],
            "undetermined_claims": [c.model_dump() for c in undetermined],
            "retryable_claims": [c.model_dump() for c in retryable],
            "claims_without_route": [c.model_dump() for c in
                                     [c for c in undetermined
                                      if self.routes.active_route(c.id) is None]],
            "failed_routes": failed_routes,
            # 只有"全部必要义务都已关闭 (closed)"才允许冻结快照
            "all_obligations_closed": bool(obligations) and all(
                o.status == ObligationStatus.closed for o in obligations),
            "questions": [q.model_dump() for q in self.spec.questions],
            "budget_remaining": remaining,
            "budget_actions": remaining,
            "budget_tool_calls": self.budget.max_tool_calls - self._tool_calls,
            "force_switch": self._force_switch,
            "knowledge_available": self.knowledge_available,
            # 自主检索授权 (场景 ②) 下没有资料库也能开展检索类动作
            "retrieval_available": self.retrieval_available,
            # P1-2: 最近一次失败的**类型**决定下一动作候选, 并给出可读的下一判断
            "failure_next_actions": self._failure_next_actions(),
            "failure_judgement": self._failure_judgement(),
            "novelty_lookup_available": bool(self.novelty_lookup) or self.knowledge_available,
            "retrieval_requests": retrieval_requests,
            # 模型/实验规格缺口: 已提议过就不再重复 (避免动作空转)
            "model_gaps": [c.id for c in claims if c.status != ClaimStatus.refuted
                           and c.model_ref is None],
            "experiment_gaps": [c.id for c in undetermined if c.id not in experimented],
            "pending_evidence": [e.model_dump() for e in self._evidence()
                                 if e.support == SupportKindOfEvidence.insufficient],
            # 证据缺口 = 确实有待判定/待检索的证据工作
            "evidence_gaps": [g.model_dump(mode="json") for g in gaps
                              if g.gap_type == GapType.missing_evidence],
            "gaps": [g.model_dump(mode="json") for g in gaps],
        }
        state["available_actions"] = [a.value for a in available_actions(state)]
        state["primary_gap"] = self._primary_gap(gaps, state)
        # 精确避让: 只避开"对当前主缺口对象、同一版本、同一路线已执行"的动作
        self._last_primary_object = (state["primary_gap"] or {}).get("object_id", "")
        self._dismissed = self._dismissed_actions()
        state["dismissed_actions"] = sorted(self._dismissed)
        state["context_text"] = self._build_context(claims, obligations, evidence, gaps).render()
        state["no_action_reason"] = self._no_action_reason(state)
        return state

    def _primary_gap(self, gaps: list[ResearchGap], state: dict) -> dict:
        """选出当前最紧迫的缺口并给出**正确的目标对象 id**。

        不同缺口类型的动作作用对象不同: 未关闭义务作用于 obligation,
        其余作用于 claim。把 claim id 当成 obligation id 派发会导致动作永远失败。
        """
        if not gaps:
            return {}
        if self._force_switch and state.get("retryable_claims"):
            claim = state["retryable_claims"][0]
            return {"gap_type": GapType.route_exhausted.value, "object_id": claim["id"],
                    "statement": "当前路线连续无进展, 需要更换研究方法",
                    "resolving_actions": [ActionType.switch_strategy.value,
                                          ActionType.design_experiment.value]}
        top = gaps[0]
        target_id = top.target_ref.id

        if top.gap_type == GapType.open_obligation:
            obligation = next((o for o in state.get("open_obligations", [])
                               if o["id"] == target_id), None)
            if obligation is None:
                # 目标义务已被关闭: 退回该命题的其他未关闭义务
                claim_id = next((o.get("claim_id") for o in state.get("open_obligations", [])), "")
                obligation = next((o for o in state.get("open_obligations", [])
                                   if o.get("claim_id") == claim_id), None)
            object_id = obligation["id"] if obligation else target_id
            return {"gap_type": top.gap_type.value, "object_id": object_id,
                    # 所属命题: 协调者据此判断"该命题是否已做过推导审查"
                    "claim_id": (obligation or {}).get("claim_id", ""),
                    "statement": top.statement, "resolving_actions": list(top.resolving_actions)}

        # 其他缺口: 目标可能是 claim / evidence / model, 用对象自身 id
        object_id = target_id
        for claim in state.get("unresolved_claims", []) + state.get("undetermined_claims", []):
            if claim["id"] == target_id:
                object_id = claim["id"]
                break
        return {"gap_type": top.gap_type.value, "object_id": object_id,
                "statement": top.statement, "resolving_actions": list(top.resolving_actions)}

    def _no_action_reason(self, state: dict) -> str:
        if not state.get("budget_remaining", 0):
            return "预算耗尽"
        if not self.knowledge_available and not state.get("unresolved_claims"):
            return "知识底座不可用且无可推进的命题"
        return "所有前置条件均不满足"

    def _retrieval_requests(self, claims: list[Claim], gaps: list[ResearchGap]) -> list[dict]:
        """把缺口转成定向检索请求 (计划书 §6.4): 缺口 → 检索 → 原文 → 证据。

        **不能用 `knowledge_available` 一票否决**: 本地知识库为空 ≠ 没有检索能力。
        策略授权自主检索时 `retrieval_available` 为真, 外部检索 (arXiv/OpenAlex) 仍然
        可用; 早期实现在这里直接 `if not self.knowledge_available: return []`, 于是
        "自主检索"被整个研究循环跳过 —— 实测无本地库时得到 0 个检索请求, 只有存在本地
        库时才得到 1 个 (计划书 P0-1)。
        """
        if not self.retrieval_available:
            return []
        requests: list[dict] = []
        for gap in gaps:
            if gap.gap_type != GapType.missing_evidence:
                continue
            claim = next((c for c in claims if c.id == gap.target_ref.id), None)
            if claim is None:
                continue
            category = "theorem"
            if "反例" in gap.statement:
                category = "counterexample"
            requests.append({
                "claim_id": claim.id, "gap_type": gap.gap_type.value,
                "category": category, "statement": gap.statement,
            })
        return requests

    def _build_context(self, claims, obligations, evidence, gaps):
        return build_context_pack(
            goal=self.spec.problem_statement or self.spec.direction or "",
            claims=claims, obligations=obligations, evidence=evidence, gaps=gaps,
            routes=self.routes.routes, failures=[f.to_dict() for f in self.routes.failures],
            budget={"actions": self.budget.max_actions - self._actions,
                    "tool_calls": self.budget.max_tool_calls - self._tool_calls},
            permissions={"scope": self.spec.scope_change_policy,
                         "data": "仅允许授权资料范围"},
        )

    # ------------------------------------------------------------------
    # 动作派发 (注册表只暴露有执行器的动作; 这里必须全部有 handler)
    # ------------------------------------------------------------------
    def _dispatch(self, action: ResearchAction) -> bool:
        if self._already_executed(action):
            self._notes.append(
                f"{action.action_type.value} 对 {action.object_id or '(全局)'} 在本路线内已执行过, "
                f"不重复同一动作 (hash={self._action_hash(action)[:8]}, "
                f"versions={self._object_versions(action.object_id)})")
            return False
        handlers = {
            ActionType.plan_proof: self._act_plan_proof,
            ActionType.check_step: self._act_check_step,
            ActionType.seek_counterexample: self._act_seek_counterexample,
            ActionType.compare_prior_work: self._act_compare_novelty,
            ActionType.switch_strategy: self._act_switch_strategy,
            ActionType.retrieve_targeted: self._act_retrieve_targeted,
            ActionType.read_source: self._act_read_source,
            ActionType.interpret_evidence: self._act_interpret_evidence,
            ActionType.extract_result: self._act_extract_result,
            ActionType.propose_model: self._act_propose_model,
            ActionType.design_experiment: self._act_design_experiment,
            ActionType.derive_step: self._act_derive_step,
            ActionType.revise_hypothesis: self._act_revise_hypothesis,
            ActionType.synthesize_results: lambda a: True,
            ActionType.deliver_partial: lambda a: True,
            ActionType.stop_with_report: lambda a: True,
            ActionType.clarify_problem: lambda a: True,
        }
        handler = handlers.get(action.action_type)
        if handler is None:
            self._notes.append(f"动作 {action.action_type.value} 无执行器, 拒绝派发")
            return False
        # 账本记录本次动作**实际作用的对象**: 回填与引擎解析一致的 object_id,
        # 否则后续无法按对象精确避让 (会出现"记录了却避不开"的空转)。
        resolved_object = action.object_id or self._last_primary_object
        # R6: 分支身份取自同一次解析结果, 并与 runtime 一起持久化 ——
        # 账本、runtime、快照三处必须是同一个 branch_id。
        self._branch_id = self._current_branch(resolved_object)
        execution = ActionExecution(
            action_id=action.id, run_id=self._run_id, project_id=self.spec.project_id,
            problem_id=self.spec.problem_id,
            branch_id=self._branch_id,
            object_id=resolved_object,
            action_type=action.action_type.value, status="running",
            request_hash=self._action_signature(action),
            input_versions=self._action_inputs(resolved_object),
        )
        self.store.record_action(execution)
        try:
            progressed = bool(handler(action))
        except Exception as e:  # noqa: BLE001 - 动作失败不得中断整个研究
            # 必须显式记录: 早期实现把状态写入异常静默吞掉, 导致动作反复重试却不进展
            self._notes.append(
                f"动作 {action.action_type.value} 执行失败 ({type(e).__name__}): {e}")
            progressed = False
            execution.detail = f"{type(e).__name__}: {e}"
            execution.status = "failed"
        else:
            execution.status = "succeeded" if progressed else "failed"
        execution.finished_at = utcnow()
        self.store.record_action(execution)
        self._exec_index_cache = None   # 账本已变化, 索引缓存失效
        # 研究阶段检索的**回写**: 产生/判定证据的动作完成后, 若出现相反来源, 命题与
        # 依赖的验证必须失效重验 (计划书 P0-2)。放在这里而不是 `_compute_state`,
        # 是因为只有动作产生新证据后才有必要检查, 且不会让每步决策都做一次全表扫描。
        if progressed and action.action_type in (
                ActionType.retrieve_targeted, ActionType.read_source,
                ActionType.extract_result, ActionType.interpret_evidence,
                ActionType.seek_counterexample, ActionType.compare_prior_work):
            try:
                self._literature_impact_recheck()
            except Exception as e:  # noqa: BLE001 - 回写失败不得中断研究
                self._notes.append(f"文献回写检查失败 ({type(e).__name__}): {e}")
        return progressed

    def _plan_strategy(self, claim: Claim) -> str:
        plan = plan_proof(claim, [], self.available)
        return plan.strategy

    def _already_executed(self, action: ResearchAction) -> bool:
        """执行账本去重 (计划书 §9.3 重复动作, §9.2-4)。

        同一路线内, 相同 (动作, **目标对象**, 输入版本) 已执行过就不再重复:
        重复同一输入不会产生新信息, 只会消耗预算。
        换路或对象/输入换代后签名变化, 允许重试。
        """
        if action.action_type in (ActionType.synthesize_results,
                                  ActionType.deliver_partial,
                                  ActionType.stop_with_report,
                                  ActionType.clarify_problem):
            return False
        signature = self._action_signature(action)
        return any(entry["signature"] == signature for entry in self._executed_index())

    def _current_branch(self, object_id: str) -> str:
        route = self.routes.active_route(object_id)
        return route.id if route else "no-route"

    def branch_for(self, object_id: str) -> str:
        """问题内分支身份: 该对象当前生效的研究路线 (`no-route` 表示尚无路线)。

        R6: 分支身份要和动作账本里的 `branch_id` 一致, 而不是另起一套命名。
        """
        return self._current_branch(object_id or "")

    def _executed_index(self) -> list[dict]:
        """已执行动作索引: {action, object, branch, signature}。"""
        if getattr(self, "_exec_index_cache", None) is None:
            index = []
            for entry in self.store.list_actions(self.spec.problem_id):
                if entry.get("status") not in ("succeeded", "failed"):
                    continue
                index.append({
                    "action": entry.get("action_type"),
                    "object": entry.get("object_id", ""),
                    "branch": entry.get("branch_id") or "no-route",
                    "versions": entry.get("input_versions") or {},
                    "gap": entry.get("target_gap", ""),
                    "signature": entry.get("request_hash", ""),
                })
            self._exec_index_cache = index
        return self._exec_index_cache

    def _dismissed_actions(self) -> set[str]:
        """当前待处理对象上**签名相同**的动作 (供协调者精确避开)。

        只避开"对同一对象、同一版本、同一路线已经跑过"的动作;
        同一动作作用于其他未处理对象时仍然可用。
        """
        gap_object = (self._last_primary_object or "")
        dismissed: set[str] = set()
        branch = self._current_branch(gap_object)
        versions = self._object_versions(gap_object)
        for entry in self._executed_index():
            if entry["branch"] != branch:
                continue
            if entry["object"] != gap_object:
                continue
            if not self._same_versions(entry.get("versions"), versions):
                continue
            dismissed.add(entry["action"])
        # 明确拒绝过的动作同样要避开, 否则会在同一预算里反复空转 (P1-3/P1-2)
        dismissed |= self._refused.get(f"{gap_object}|{branch}", set())
        return dismissed

    def _refuse_action(self, action: ResearchAction, reason: str) -> None:
        """拒绝一个动作并记住: 同一对象+路线下不再重复挑选它。"""
        gap_object = action.object_id or (self._last_primary_object or "")
        branch = self._current_branch(gap_object)
        self._refused.setdefault(f"{gap_object}|{branch}", set()).add(
            action.action_type.value)
        self._notes.append(f"{action.action_type.value} 未执行: {reason}")

    def _same_versions(self, recorded: dict | None, current: dict) -> bool:
        """比较对象版本闭包 (只用真实对象键, 忽略指纹用的合成键)。"""
        current_real = {k: int(v) for k, v in (current or {}).items() if not k.startswith("__")}
        if not current_real:
            return True
        recorded = recorded or {}
        if not recorded:
            return False
        recorded_real = {k: int(v) for k, v in recorded.items() if not k.startswith("__")}
        return all(recorded_real.get(k, 0) == v for k, v in current_real.items())

    def _action_signature(self, action: ResearchAction) -> str:
        """动作去重签名: 动作 + 目标对象 + 对象版本 + 路线 + 缺口。

        只按动作类型哈希会把"同一动作作用于不同义务/命题"误判为重复执行,
        从而永久跳过第二个对象的核验。
        """
        return hash_payload({
            "action": action.action_type.value,
            "object": action.object_id,
            "branch": self._current_branch(action.object_id),
            "versions": self._object_versions(action.object_id),
            "gap": action.target_gap,
        })

    # 兼容旧名
    def _action_hash(self, action: ResearchAction) -> str:
        return self._action_signature(action)

    def _object_versions(self, object_id: str) -> dict[str, int]:
        """动作去重用的输入版本指纹。

        只包含**输入对象**的版本: 动作自身产生的证据/记录会推高自己的版本,
        若把它们计入指纹, 动作就会被自己"发现是新输入"而无限重跑。
        """
        versions: dict[str, int] = {}
        if object_id:
            for kind in (KIND_CLAIM, KIND_OBLIGATION, KIND_ATTEMPT, KIND_MODEL, KIND_ROUTE):
                version = self.store.latest_version(kind, object_id)
                if version:
                    versions[kind] = version
            if not versions:
                versions["unknown"] = 1
            return versions
        # 全局动作 (如按缺口检索): 用结论版本合计做指纹
        index = self.store.version_index(KIND_CLAIM)
        versions["__claims__"] = sum(index.values())
        return versions

    def _action_inputs(self, object_id: str) -> dict[str, int]:
        """动作输入的对象版本闭包 (不含指纹用的合成键)。"""
        return {k: int(v) for k, v in self._object_versions(object_id).items()
                if not k.startswith("__")}

    # ---- 工具执行 (统一走账本, 结果不可变落盘) ----
    def _run_tool(self, claim: Claim, obligation: ProofObligation | None, check,
                  record_id: str, scope: VerificationScope) -> tuple[Any, VerificationRecord | None]:
        idem = f"tool:{claim.id}:{obligation.id if obligation else 'ce'}:{hash_payload(check.arguments)}"
        run_id = f"run-{hash_payload([claim.id, check.tool, check.operation, check.arguments])}"
        started = self.store.begin_tool_run(
            run_id, check.tool, check.operation, check.arguments,
            problem_id=self.spec.problem_id,
            input_versions=self._verification_closure(claim), idempotency_key=idem)
        if not started:
            existing = self.store.get_tool_run(run_id)
            self._notes.append(
                f"工具调用 {check.tool}/{check.operation} 已执行过 "
                f"(状态 {existing.get('status') if existing else 'unknown'}), 不重复计费")
        result = self.runner.run(check.tool, check.operation, check.arguments)
        self._tool_calls += 1
        self.store.finish_tool_run(run_id, result.status.value,
                                   result.model_dump(mode="json")
                                   if hasattr(result, "model_dump") else {},
                                   tool_version=result.tool_version,
                                   detail=result.detail)
        record = VerificationRecord(
            id=record_id, tool=check.tool, tool_version=result.tool_version,
            input_hash=hash_payload({"tool": check.tool, "op": check.operation,
                                     "args": check.arguments}),
            claim_id=claim.id, claim_version=claim.version,
            assumption_ids=list(claim.assumption_ids),
            scope=scope, arguments=check.arguments, raw_output=result.raw_output,
            status=result.status.value, certificate=result.certificate,
            counterexample=result.counterexample,
            verification_closure=self._verification_closure(claim),
            validation_status=self._to_validation_status(result),
            support_kind=support_kind_for_tool(check.tool),
        )
        return result, record

    def _save_verification(self, record: VerificationRecord) -> None:
        self.store.put(KIND_VERIFICATION, record.id, record.model_dump(mode="json"))
        self.store.append_event(
            "verification_recorded",
            {"claim_id": record.claim_id, "record_id": record.id, "status": record.status},
            idempotency_key=f"rec:{record.id}",
        )

    @staticmethod
    def _to_validation_status(result) -> ValidationStatus:
        mapping = {
            "passed": ValidationStatus.verified,
            "failed": ValidationStatus.counterexample_found,
            "unknown": ValidationStatus.unknown,
            "unsupported": ValidationStatus.unsupported,
            "timeout": ValidationStatus.timeout,
            "unavailable": ValidationStatus.unavailable,
            "error": ValidationStatus.execution_error,
        }
        return mapping.get(result.status.value, ValidationStatus.execution_error)

    def _verification_closure(self, claim: Claim) -> dict[str, int]:
        """验证绑定的是具体陈述与依赖版本, 不只是 claim id。"""
        closure: dict[str, int] = {claim.id: claim.version}
        spec_version = self.store.latest_version(KIND_SPEC, self.spec.problem_id)
        if spec_version:
            closure[self.spec.problem_id] = spec_version
        for assumption_id in claim.assumption_ids:
            version = self.store.latest_version(KIND_ASSUMPTION, assumption_id)
            if version:
                closure[assumption_id] = version
        if claim.model_ref:
            version = self.store.latest_version(KIND_MODEL, claim.model_ref.id)
            closure[claim.model_ref.id] = version or claim.model_ref.version
        return closure

    # ---- 具体动作 ----
    def _llm_plan_key(self, claim: Claim) -> str:
        """LLM 推导的记账键: 同一命题同一版本只调用一次模型。

        版本变化必须允许重新推导 (旧推导已随版本失效), 但同一版本内重复动作
        既无新信息又烧预算 —— 由这里拦住, 而不是靠模型自觉。
        """
        return f"{claim.id}@v{claim.version}"

    def plan_for_claim(self, claim: Claim,
                       obligations: list[ProofObligation] | None = None) -> Plan:
        """为命题制定证明计划 (计划书 §7.1)。

        有 LLM 时走 `plan_proof_deep` (规则 + 结构化推导), 否则纯规则路径;
        同一版本只调用一次模型, 之后的调用直接复用已算出的计划。
        """
        from src.research.theorist import plan_proof_deep

        cached = self._plans.get(claim.id)
        if cached is not None and cached.attempt.target_version == claim.version:
            return cached
        if obligations is None:
            obligations = [o for o in self._obligations()
                           if o.claim_id == claim.id and o.status == ObligationStatus.open]
        key = self._llm_plan_key(claim)
        if self.llm is None or key in self._llm_planned:
            plan = plan_proof(claim, obligations, self.available)
            if self.llm is not None:
                plan.source = "rules"
                plan.notes = (plan.notes + " 同一命题版本已做过 LLM 推导, 本次不重复调用"
                              ).strip()
            self._plans[claim.id] = plan
            return plan
        self._llm_planned.add(key)
        plan = plan_proof_deep(claim, obligations, self.available,
                               llm=self.metered_llm("theorist_derivation"),
                               budget_exhausted=self._budget_exhausted)
        self._plans[claim.id] = plan
        return plan

    def _act_plan_proof(self, action: ResearchAction) -> bool:
        claim = self._get_claim(action.object_id)
        if claim is None:
            return False
        obligations = [o for o in self._obligations()
                       if o.claim_id == claim.id and o.status == ObligationStatus.open]
        plan = self.plan_for_claim(claim, obligations)
        attempt = plan.attempt
        attempt.id = f"pf-{claim.id}-v{claim.version}"
        self._plans[claim.id] = plan
        route = self.routes.ensure_route(claim.id, goal=claim.statement, strategy=plan.strategy)
        route.attempts += 1
        writes = [(KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json")),
                  (KIND_ROUTE, route.id, route.model_dump(mode="json"))]
        writes += [(KIND_OBLIGATION, o.id, o.model_dump(mode="json")) for o in plan.proposed]
        self.append_step(writes,
                         [("plan_proof", {"claim_id": claim.id, "strategy": plan.strategy,
                                          "source": plan.source,
                                          "steps": len(attempt.steps),
                                          "subgoals": len(plan.proposed),
                                          "route_id": route.id})],
                         idempotency_key=f"plan:{claim.id}:{plan.strategy}")
        if plan.notes:
            self._notes.append(f"{claim.id}: {plan.notes}")
        return True

    def _derive_design_steps(self, claim: Claim) -> bool:
        """设计/计数存在性命题的推导步骤 = 证书判定链 (S1)。

        步骤内容为**确定性重算**的判定链, 不含自由生成的数学步骤; 因此不产生新的
        "反方审查"义务 (审查清单针对的是陈述强度与前提缺失, 而参数的适用性已由
        `design_necessity` 义务的证书逐条覆盖)。
        """
        from src.research import design_feasibility as df

        params = df.DesignParams(v=claim.design_v or 0, k=claim.design_k or 0,
                                 lam=claim.design_lambda or 1,
                                 b=claim.design_b, r=claim.design_r)
        report = df.check(params)
        certificate = report.certificate_dict()
        certificate["sha256"] = report.certificate_digest()
        own_obligations = [o for o in self._obligations()
                           if o.claim_id == claim.id and o.kind == df.OBLIGATION_KIND]
        steps: list[ProofStep] = []
        for index, (text, rule) in enumerate(
                df.theorem_application_steps(certificate), start=1):
            obligation = own_obligations[0] if own_obligations else None
            steps.append(ProofStep(
                index=index, statement=text, justification="由证书重算, 可反查", rule=rule,
                obligation_ref=(ObjectRef(id=obligation.id, version=obligation.version)
                                if obligation is not None else None)))
        attempt = ProofAttempt(
            id=f"pf-{claim.id}-v{claim.version}", target_claim_id=claim.id,
            target_version=claim.version, strategy="design_necessity_certificate",
            steps=steps, subgoals=[o.statement for o in self._obligations()
                                   if o.claim_id == claim.id],
            status="complete",
        )
        self.append_step(
            writes=[(KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))],
            events=[("derive_step", {"claim_id": claim.id, "steps": len(steps),
                                     "formal_gap": False, "review_obligations": 0,
                                     "adversarial_checked": 0, "adversarial_hits": 0,
                                     "adversarial": "证书判定链 (无需自由推导)"})],
            idempotency_key=f"derive:{claim.id}:{report.certificate_digest()[:16]}",
        )
        return True

    def _act_derive_step(self, action: ResearchAction) -> bool:
        """生成可审查的推导步骤 (计划书 §7.1), 并做一次反方审查。

        审查意见不直接改命题状态, 而是转为**新的证明义务** (§5.5), 仍需核验才能关闭。

        应用/数据类命题 (因果/描述/预测/情景/规范) 没有可符号推导的形式化片段,
        但**反方审查仍然适用** —— 早期实现因为"没有结构化形式"直接返回失败,
        导致这八项检查在实践中从未对应用类命题执行过。现在这类命题会补一条
        显式的"尚无形式化片段"步骤, 使审查与审计记录都能进行。
        """
        claim = self._get_claim(action.object_id)
        if claim is None:
            # 协调者把"未关闭义务"缺口的对象设为义务 id (check_step 需要义务),
            # 但推导步骤作用在命题上: 必须按义务回查所属命题, 否则该动作
            # 每次都因"找不到命题"静默失败, 反方审查永远不会执行。
            obligation = self._get_obligation(action.object_id)
            if obligation is not None:
                claim = self._get_claim(obligation.claim_id)
        if claim is None:
            self._notes.append(
                f"derive_step: 无法从对象 {action.object_id} 定位命题 (既不是命题也不是义务)")
            return False
        # S1: 具名定理应用类命题 (设计/计数存在性) 的"推导步骤"就是证书判定链本身。
        # 既不能编造通用代数步骤 (会与结论无关), 也不需要再走反方审查清单 ——
        # 证书已经逐条记录了定理、适用条件、输入与结论。
        if is_design_claim(claim):
            return self._derive_design_steps(claim)
        plan = self._plans.get(claim.id) or self.plan_for_claim(claim)
        self._plans[claim.id] = plan
        attempt = plan.attempt
        formal_gap = False
        if not attempt.steps:
            formal_gap = True
            attempt.steps = [ProofStep(
                index=1,
                statement=f"该命题尚无形式化片段, 无法给出符号推导 (陈述: {claim.statement[:80]})",
                justification="应用类命题的结论强度取决于证据与设计, 不是推导",
                rule="adversarial_review_only",
            )]

        from src.research.adversarial import review_claim_with_obligations
        from src.research.critic import issues_to_obligations

        existing = {o.statement for o in self._obligations() if o.claim_id == claim.id}
        rule_obligations = issues_to_obligations(attempt, claim, existing_statements=existing)
        existing |= {o.statement for o in rule_obligations}
        # 计划书 §7.2: 反方审查清单八项必须都跑过, 并在审计里留下每项结论
        review, review_obligations = review_claim_with_obligations(
            claim, evidence=[e for e in self._evidence() if e.claim_id == claim.id],
            existing_statements=existing)
        new_obligations = list(rule_obligations) + list(review_obligations)
        # 一次推导尝试到此已完整记录 (步骤 + 审查意见 + 新增义务), 因此标记为
        # complete: 这只是"记录完整", 不代表结论成立 —— 结论仍由中央规则依据
        # 验证记录计算。缺此标记时交付门槛会认为"正文引用了证明却没有任何
        # 已完成的证明尝试记录"。
        attempt.status = "complete"
        writes = [(KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))]
        writes += [(KIND_OBLIGATION, o.id, o.model_dump(mode="json")) for o in new_obligations]
        self.append_step(
            writes=writes,
            events=[("derive_step", {"claim_id": claim.id, "steps": len(attempt.steps),
                                     "formal_gap": formal_gap,
                                     "review_obligations": len(new_obligations),
                                     "adversarial_checked": review.checked,
                                     "adversarial_hits": len(review.hits),
                                     "adversarial": review.digest()})],
            idempotency_key=f"derive:{claim.id}:{hash_payload([s.statement for s in attempt.steps])}",
        )
        if formal_gap:
            self._notes.append(
                f"{claim.id}: 无可形式化片段, 已改为以反方审查清单产出待核验义务")
        if new_obligations:
            self._notes.append(
                f"{claim.id}: 独立审查提出 {len(new_obligations)} 条待核验义务 "
                f"({review.summary()})")
        return True

    def _act_check_step(self, action: ResearchAction) -> bool:
        obligation = self._get_obligation(action.object_id)
        if obligation is None:
            return False
        claim = self._get_claim(obligation.claim_id)
        if claim is None:
            return False
        # 义务绑定的是具体命题版本: 命题换代后必须重新拆解义务
        if obligation.claim_version != claim.version:
            obligation.status = ObligationStatus.blocked
            obligation.detail = (
                f"命题已更新至 v{claim.version} (义务针对 v{obligation.claim_version}), 需重新拆解"
            )
            obligation.validation_status = ValidationStatus.encoding_mismatch
            self._save_obligation(obligation)
            self.routes.record_failure(claim.id, "encoding_mismatch", obligation.detail,
                                       scientific=False,
                                       recovery_condition="对当前命题版本重新拆解义务")
            self._save_routes()
            self._reconcile_claim(claim)
            return False
        if obligation.acceptance_method == "rule":
            return self._act_rule_obligation(obligation, claim)
        if obligation.acceptance_method == "design_necessity":
            # S1: 设计/计数类存在性判定由确定性规则 + 可复核证书验收
            return self._act_rule_obligation(obligation, claim)
        if obligation.acceptance_method == "informal_review":
            # 计划书 §5.5/§7.2: 独立论证审查没有工具可核验, 只能由人工或
            # 独立审查者确认。缺确认时保持 blocked, 不得自动关闭。
            obligation.status = ObligationStatus.blocked
            obligation.validation_status = ValidationStatus.unknown
            if not obligation.detail.endswith("[待人工/独立审查确认]"):
                obligation.detail = (obligation.detail + " [待人工/独立审查确认]").strip()
            self._save_obligation(obligation)
            self.routes.record_failure(
                claim.id, "informal_review_pending", obligation.statement,
                scientific=False,
                recovery_condition="由人工或独立审查者确认该意见已被处理")
            self._save_routes()
            self._reconcile_claim(claim)
            return False
        # 按**当前**义务与当前命题版本即时构造验证请求。
        # (不能依赖规划阶段的缓存: 计划可能是在只有部分义务时生成的, 之后新增的
        #  义务会找不到 check 而被误判为"无可用验证适配器"。)
        method = obligation.acceptance_method or ""
        available = dict(self.available)
        if method in ("sympy", "z3", "lean", "stats"):
            for tool in ("sympy", "z3", "lean", "stats"):
                available[tool] = bool(self.available.get(tool)) and tool == method
        check = _check_for(obligation, claim, available)
        if check is None:
            check = _check_for(obligation, claim, self.available)
        if check is None:
            obligation.status = ObligationStatus.blocked
            obligation.detail = "无可用验证适配器"
            obligation.validation_status = ValidationStatus.unsupported
            self._save_obligation(obligation)
            self.routes.record_failure(
                claim.id, "unsupported", obligation.detail, scientific=False,
                recovery_condition="安装可用后端或改用可审查的自然语言推导")
            self._save_routes()
            self._reconcile_claim(claim)
            return False

        result, record = self._run_tool(claim, obligation, check,
                                       record_id=f"ver-{claim.id}-{obligation.id}",
                                       scope=VerificationScope.target)
        status = self._to_validation_status(result)
        obligation.validation_status = status
        obligation.support_kind = support_kind_for_tool(check.tool)
        obligation.coverage = coverage_for_tool(check.tool)
        # 原始工具结果先不可变落盘 (stale=True 表示"尚未确认可用"), 再由中央状态规则
        # 依据记录计算命题状态 —— 顺序不能颠倒, 否则裁决时看不到这条证据。
        record.stale = True
        self._save_verification(record)

        if status == ValidationStatus.verified:
            obligation.status = ObligationStatus.closed
            obligation.detail = result.detail or "验证通过"
            if obligation.kind == "equality_condition":
                self._add_equality_claim(claim, result.certificate, check.tool)
            elif obligation.kind == "estimate_effect":
                # 估计写入内存态, 由随后的 _reconcile_claim 作为**单次**版本写回,
                # 避免"先写估计再被弱状态覆盖"的多写竞争。
                claim = self._apply_estimate(claim, result)
            self._save_obligation(obligation)
            record.stale = False   # 确认可用后才解除 stale
            self._save_verification(record)
            self._reconcile_claim(claim)
            self.append_step(
                writes=[(KIND_OBLIGATION, obligation.id, obligation.model_dump(mode="json"))],
                events=[("obligation_closed", {"claim_id": claim.id, "obligation_id": obligation.id,
                                               "tool": check.tool})],
                idempotency_key=f"closed:{obligation.id}:{record.id}",
            )
            return True

        if status == ValidationStatus.counterexample_found:
            # 只有满足前提的反例才算数学反驳; 统计估计失败单独处理
            if not result.counterexample:
                obligation.status = ObligationStatus.blocked
                obligation.detail = f"未给出可回代的反例: {result.detail}"
                self._save_obligation(obligation)
                self.routes.record_failure(claim.id, "no_counterexample", obligation.detail,
                                           scientific=False, tool=check.tool,
                                           recovery_condition="补出满足前提的反例后再判定为假")
                self._save_routes()
                self._reconcile_claim(claim)
                return False
            if obligation.kind == "estimate_effect":
                obligation.status = ObligationStatus.blocked
                obligation.detail = "效应估计未支持结论 (区间跨零不等于效应不存在)"
                self._save_obligation(obligation)
                self._reconcile_claim(claim)
                return False
            obligation.status = ObligationStatus.refuted
            obligation.counterexample = dict(result.counterexample)
            obligation.detail = result.detail or "找到满足前提的反例"
            self._save_obligation(obligation)
            self.routes.record_failure(
                claim.id, "counterexample",
                f"找到反例 {result.counterexample}", scientific=True, tool=check.tool,
                detail=result.detail,
                recovery_condition="修改命题条件或改为弱化版本后另建命题")
            self._save_routes()
            self._reconcile_claim(claim)
            record.stale = False
            self._save_verification(record)
            self.append_step(
                writes=[(KIND_OBLIGATION, obligation.id, obligation.model_dump(mode="json"))],
                events=[("claim_refuted", {"claim_id": claim.id,
                                           "witness": result.counterexample})],
                idempotency_key=f"refuted:{obligation.id}",
            )
            return True

        # 其余 (unknown/timeout/unsupported/error/invalid_input/encoding_mismatch):
        # 一律保持未决, 不映射为通过或数学反驳。
        # 该义务退出 open 集合 (标记 blocked), 使循环到达不动点而非无限重试同一输入。
        obligation.status = ObligationStatus.blocked
        obligation.detail = result.detail or result.status.value
        self._save_obligation(obligation)
        kind = "unknown" if status == ValidationStatus.unknown else status.value
        self.routes.record_failure(
            claim.id, kind, obligation.detail, scientific=False, tool=check.tool,
            recovery_condition="出现新证据/条件后重试, 或改用其他后端")
        self._save_routes()
        self._reconcile_claim(claim)
        self._notes.append(f"{claim.id} 未决({check.tool}/{result.status.value}): {obligation.detail}")
        return False

    def _act_seek_counterexample(self, action: ResearchAction) -> bool:
        from src.research.critic import counterexample_check

        claim = self._get_claim(action.object_id)
        if claim is None:
            return False
        if self.routes.scientific_failure(claim.id, "counterexample"):
            self._notes.append(f"{claim.id}: 已有科学反例, 不重复搜索")
            return False
        # 同一输入下重复搜索不会得到不同结果: 已试过的失败不再重跑 (省预算, 不空转)
        repeats = {"unsupported", "unknown", "no_counterexample_found", "read_failed"}
        if self.routes.failures_for(claim.id) and all(
                f.kind in repeats for f in self.routes.failures_for(claim.id)):
            self._notes.append(
                f"{claim.id}: 反例搜索已试过且均为运行/能力问题, 不重复同一输入; 需换路或补条件")
            self._force_switch = bool(self._no_progress >= 1)
            return False
        check = counterexample_check(claim, self.available)
        if check is None:
            self.routes.record_failure(
                claim.id, "unsupported", "当前命题形式无法编码反例搜索", scientific=False,
                recovery_condition="先补出可核验的结构化形式")
            self._save_routes()
            self._reconcile_claim(claim)
            return False
        result, record = self._run_tool(claim, None, check, record_id=f"ce-{claim.id}",
                                       scope=VerificationScope.target)
        status = self._to_validation_status(result)
        if status == ValidationStatus.counterexample_found and result.counterexample:
            updated = self._with_status(claim, status=ClaimStatus.refuted,
                                        support_kind=support_kind_for_tool(check.tool),
                                        note=f"反例: {result.counterexample}")
            self._save_claim(updated)
            record.stale = False
            self._save_verification(record)
            self.routes.record_failure(claim.id, "counterexample",
                                       f"找到反例 {result.counterexample}", scientific=True,
                                       tool=check.tool,
                                       recovery_condition="改为弱化命题或增加条件")
            self._save_routes()
            self.store.append_event("counterexample",
                                    {"claim_id": claim.id, "witness": result.counterexample},
                                    idempotency_key=f"ce:{claim.id}:{result.counterexample}")
            return True
        if status == ValidationStatus.verified:
            # 未找到反例 ≠ 普遍成立: 只记录一次有限测试证据
            note = "反例搜索未发现反例 (有限测试证据, 不构成证明)"
            self._notes.append(f"{claim.id}: {note}")
            self._save_verification(record)
            self.routes.record_failure(claim.id, "no_counterexample_found", note,
                                       scientific=False, tool=check.tool)
            self._save_routes()
            return False
        self._save_verification(record)
        self.routes.record_failure(claim.id, status.value, result.detail or status.value,
                                   scientific=False, tool=check.tool,
                                   recovery_condition="改用其他后端或缩小问题范围")
        self._save_routes()
        return False

    def _act_rule_obligation(self, obligation: ProofObligation, claim: Claim) -> bool:
        """规则型义务: 由确定性条件关闭 (混淆处理/范围/证据分级)。"""
        ok, certificate = False, ""
        if obligation.kind == "control_confound":
            if claim.study.confounders or claim.study.confounder_handling:
                ok = True
                certificate = claim.study.confounder_handling or ", ".join(claim.study.confounders)
            else:
                obligation.detail = "未列出混淆因素或识别策略 (不得默认无混淆)"
        elif obligation.kind == "scope_check":
            if claim.scope_population and claim.scope_region and claim.scope_period:
                ok = True
                certificate = f"{claim.scope_population} / {claim.scope_region} / {claim.scope_period}"
            else:
                obligation.detail = "缺少人群/地区/时期范围"
        elif obligation.kind == "evidence_support":
            # 支持来源有二: (a) 已判定支持关系的文献证据; (b) 本研究在声明设计下
            # 得到的估计 (有真实数据/日志产物时才存在)。两者都必须带可信度分级。
            supporters = [e for e in self._evidence()
                          if e.support in (SupportKindOfEvidence.supports,
                                           SupportKindOfEvidence.partially_supports)]
            design = claim.study.design
            has_own_estimate = bool(claim.effect_estimate) and design in (
                StudyDesign.rct, StudyDesign.did, StudyDesign.iv, StudyDesign.rdd,
                StudyDesign.matching, StudyDesign.observational)
            if has_own_estimate:
                grade = (EvidenceGrade.identification_based
                         if design in (StudyDesign.rct, StudyDesign.did, StudyDesign.iv,
                                       StudyDesign.rdd, StudyDesign.matching)
                         else EvidenceGrade.single_source)
                ok = True
                certificate = f"研究自身估计 ({design.value}) → {grade.value}"
                claim = claim.model_copy(update={"evidence_grade": grade})
            elif supporters:
                grade = self._grade_evidence(supporters)
                if grade != EvidenceGrade.unsupported:
                    ok = True
                    certificate = grade.value
                else:
                    obligation.detail = "支持关系已判定但证据等级仍不足"
            else:
                obligation.detail = "尚无已判定支持关系的证据 (召回不等于支持)"
        elif obligation.kind in ("identification_assumptions", "design_feasibility",
                                 "measurement_and_missing", "error_structure"):
            # 计划书 §7.4: 因果结论的识别假设/设计可行性/测量与缺失/误差结构
            # 必须各自显式声明, 不得默认成立。
            ok, certificate = self._evaluate_causal_declaration(obligation, claim)
        elif obligation.kind == "design_necessity":
            # S1: 设计可行性判定 → 可复核证书; 证书同时写进验证记录
            return self._evaluate_design_necessity(obligation, claim)
        else:
            obligation.detail = f"未知规则型义务 {obligation.kind}"
        obligation.validation_status = (ValidationStatus.verified if ok
                                        else ValidationStatus.unknown)
        obligation.status = ObligationStatus.closed if ok else ObligationStatus.blocked
        # 注意: 不要在这里用空证据列表重算分级 —— 那会把"研究自身估计"的
        # identification_based 错误降回 unsupported。
        self.append_step(
            writes=[(KIND_OBLIGATION, obligation.id, obligation.model_dump(mode="json"))]
            + ([(KIND_CLAIM, claim.id, claim.model_dump(mode="json"))]
               if obligation.kind == "evidence_support" else []),
            events=[("rule_obligation_evaluated",
                     {"claim_id": claim.id, "obligation_id": obligation.id,
                      "certificate": certificate, "closed": ok})],
            idempotency_key=f"rule:{obligation.id}:{obligation.status.value}",
        )
        if not ok:
            self.routes.record_failure(
                claim.id, "missing_condition", obligation.detail, scientific=False,
                recovery_condition="补齐范围/混淆处理/证据后重试")
            self._save_routes()
        self._reconcile_claim(claim)
        return ok

    def _evaluate_design_necessity(self, obligation: ProofObligation,
                                   claim: Claim) -> bool:
        """S1: 设计/计数类存在性判定义务的规则验收 (证书可复核)。

        判定**只用命题记录里的参数重算**, 不读义务文本里的数字, 因此"改参数留旧
        证书"的偷换会在对齐检查里失败。三态语义:

        - `nonexistent` (存在一条被违反的必要条件) → 关闭义务, 写验证记录;
        - `necessary_met` (必要条件全过, 存在性未定) → **保持未关闭**: 必要条件满足
          不等于存在, 由调用方按未决处理, 交付等级不得因此升级;
        - 其他/异常 → 保持 open 并如实记录原因, 不冒充已判定。
        """
        import json

        from src.research import design_feasibility as df

        if claim.design_v is None or claim.design_k is None:
            obligation.detail = "该义务需要命题记录设计参数 (v,k,λ), 当前缺失"
            self._save_obligation(obligation)
            return False
        params = df.DesignParams(v=claim.design_v, k=claim.design_k,
                                 lam=claim.design_lambda or 1,
                                 b=claim.design_b, r=claim.design_r)
        try:
            report = df.check(params)
        except Exception as e:  # noqa: BLE001 - 判定失败必须保持未决, 不得冒充结论
            obligation.status = ObligationStatus.blocked
            obligation.validation_status = ValidationStatus.execution_error
            obligation.detail = f"可行性判定执行失败: {type(e).__name__}: {e}"
            self._save_obligation(obligation)
            self._reconcile_claim(claim)
            return False

        certificate = report.certificate_dict()
        certificate["sha256"] = report.certificate_digest()
        premises = [f"{item.theorem_cn}: {item.statement}" for item in report.checks]
        premises.append("引用定理: " + ("、".join(report.sources()) or "-"))
        premises += [f"未判定: {item.condition} ({item.reason})" for item in report.unchecked]
        arguments = {
            "design_v": params.v, "design_k": params.k, "design_lambda": params.lam,
            "design_b": params.b, "design_r": params.r,
            "design_verdict": report.verdict,
            # 判定链的参数副本: 对齐检查与规则验收使用同一份数值
            "design_report": certificate,
        }
        # 判定记录先落盘 (stale=True 表示尚未确认可用), 再由中央规则裁决命题状态
        record = VerificationRecord(
            tool="design_necessity", tool_version="1",
            input_hash=hash_payload({"design": certificate.get("design"),
                                     "counts": certificate.get("counts")}),
            claim_id=claim.id, claim_version=claim.version,
            obligation_ref=ObjectRef(id=obligation.id, version=obligation.version),
            link_status="confirmed",
            assumption_ids=list(claim.assumption_ids),
            scope=VerificationScope.target,
            arguments=arguments,
            checked_formula=(f"r=λ(v-1)/(k-1)={params.lam}·({params.v}-1)/({params.k}-1)"
                             f"={report.r}; b=vr/k={params.v}·{report.r}/{params.k}={report.b}"),
            premises=premises,
            output_digest=report.certificate_digest(),
            raw_output=report.describe(),
            validation_status=(ValidationStatus.verified if report.verdict == "nonexistent"
                               else ValidationStatus.unknown),
            # 结论靠**具名经典定理 + 机器重算的参数**支持: 既不是原文引用, 也不是
            # 符号求解器的输出, 因此单独记一种支持方式 (citation 即定理本身)。
            support_kind=SupportKind.theorem_application,
            stale=report.verdict != "nonexistent",
        )
        self._save_verification(record)

        if report.verdict == "nonexistent":
            obligation.status = ObligationStatus.closed
            obligation.validation_status = ValidationStatus.verified
            obligation.support_kind = SupportKind.theorem_application
            obligation.coverage = Coverage.target
            obligation.detail = (
                "证书: 存在被违反的必要条件 → 不存在。判定链: "
                + " | ".join(f"{item.claim_cn}: {item.conclusion}" for item in report.checks
                             if not item.result)
                + f"; 引用定理: {'、'.join(report.sources()) or '-'}"
                + f"; 证书 sha256={report.certificate_digest()[:16]}")
            self._save_obligation(obligation)
            record.stale = False    # 结论已采纳后才解除 stale
            self._save_verification(record)
            self.append_step(
                writes=[(KIND_OBLIGATION, obligation.id, obligation.model_dump(mode="json"))],
                events=[("design_necessity_evaluated",
                         {"claim_id": claim.id, "obligation_id": obligation.id,
                          "verdict": report.verdict, "closed": True,
                          "theorem_sources": report.sources(),
                          "certificate": report.certificate_digest()})],
                idempotency_key=f"design:{obligation.id}:{report.certificate_digest()[:16]}",
            )
            self._reconcile_claim(claim)
            return True

        if report.verdict == "necessary_met":
            # 必要条件满足 ≠ 存在: 义务必须保持未关闭, 只如实记录还缺哪一步
            obligation.status = ObligationStatus.blocked
            obligation.validation_status = ValidationStatus.unknown
            obligation.detail = (
                "必要条件全部满足, 但存在性未定 (未关闭): 需要显式构造或更强的排除定理。"
                + "已通过: "
                + "; ".join(f"{item.claim_cn} ({item.conclusion})" for item in report.checks
                            if item.result)
                + (f"; 未判定: {'; '.join(item.reason for item in report.unchecked)}"
                   if report.unchecked else ""))
            self._save_obligation(obligation)
            self.routes.record_failure(
                claim.id, "necessary_met_not_exists",
                "必要条件满足不等于存在, 该义务不得关闭", scientific=True,
                recovery_condition="给出显式构造, 或找到能排除该参数的更强定理")
            self._save_routes()
            self.append_step(
                writes=[],
                events=[("design_necessity_evaluated",
                         {"claim_id": claim.id, "obligation_id": obligation.id,
                          "verdict": report.verdict, "closed": False,
                          "theorem_sources": report.sources(),
                          "certificate": report.certificate_digest()})],
                idempotency_key=f"design:{obligation.id}:necessary_met",
            )
            self._reconcile_claim(claim)
            return False

        obligation.validation_status = ValidationStatus.unknown
        obligation.detail = "判定信息不足, 需要补充计数约束: " + json.dumps(
            certificate.get("design"), ensure_ascii=False)
        self._save_obligation(obligation)
        self._reconcile_claim(claim)
        return False

    def _grade_evidence(self, evidence: list[SourceEvidence]) -> EvidenceGrade:
        from src.research.evidence import grade_evidence

        return grade_evidence(evidence)

    @staticmethod
    def _evaluate_causal_declaration(obligation: ProofObligation,
                                     claim: Claim) -> tuple[bool, str]:
        """评估因果研究设计类义务 (计划书 §7.4)。

        这些义务只能由**显式声明**关闭: 缺声明一律保持 blocked, 不允许用
        "填了别的字段"代替。返回 (是否关闭, 证书/说明)。
        """
        study = claim.study
        kind = obligation.kind
        if kind == "identification_assumptions":
            if study.identification_assumptions:
                return True, "; ".join(study.identification_assumptions)
            obligation.detail = (
                "未列出识别假设; 声明研究设计不等于识别成立 (如平行趋势/排他性/可忽略性等)"
            )
        elif kind == "design_feasibility":
            if study.design_feasibility.strip():
                return True, study.design_feasibility.strip()
            obligation.detail = "未说明该设计在现有数据上为何可行 (分组/前后期/工具/断点是否存在)"
        elif kind == "measurement_and_missing":
            if study.measurement_notes.strip() and study.missing_data_handling.strip():
                return True, f"测量: {study.measurement_notes.strip()}; 缺失: {study.missing_data_handling.strip()}"
            missing = []
            if not study.measurement_notes.strip():
                missing.append("测量方案")
            if not study.missing_data_handling.strip():
                missing.append("缺失数据机制与处理")
            obligation.detail = "未说明" + "、".join(missing) + " (不得默认测量无误、无缺失)"
        elif kind == "error_structure":
            if study.error_structure.strip():
                return True, study.error_structure.strip()
            obligation.detail = "未说明误差结构 (聚类/异方差/自相关), 区间估计可能不可靠"
        return False, ""

    def _apply_estimate(self, claim: Claim, result) -> Claim:
        """把统计估计写入命题 (内存态): 仅当 CI 排除 0 才支持效应存在。

        实际落盘由调用方的单次 `_reconcile_claim` 完成。
        """
        values = dict(result.values or {})
        closure = self._verification_closure(claim)
        updated = claim.model_copy(update={
            "effect_estimate": values,
            "assurance": Assurance.empirical_estimated,
            "support_kind": SupportKind.statistical_estimate,
            "verification_scope": VerificationScope.target,
            "coverage": Coverage.target,
            "validation_status": ValidationStatus.verified,
            "verification_closure": closure,
            "notes": (claim.notes + " 效应估计完成; 设计成立与否由独立义务判定").strip(),
        })
        self.append_step(
            writes=[(KIND_CLAIM, updated.id, updated.model_dump(mode="json"))],
            events=[("effect_estimated", {"claim_id": claim.id, "estimate": values})],
            idempotency_key=f"est:{claim.id}:{hash_payload(values)}",
        )
        return updated

    def _act_propose_model(self, action: ResearchAction) -> bool:
        """从已有证据提出**候选模型并比较**, 再显式选中一个 (计划书 §3 R2 / §5.2)。

        R2: 早期实现按命题类型套一个通用模板就结束。现在至少构造两个互相竞争的
        候选机制 (条件化机制 vs 简化替代解释), 比较来源/忠实度/可验证性/成本,
        给出选中理由, 并在候选预测冲突时产出**可区分检验**建议。

        计划书 §5.2: 提出候选之后必须显式选中一个具体版本, 否则
        `ResearchModel.selected` 永远为假, 结论依据哪个模型无从审计。
        """
        from src.research.capability import declare_capability
        from src.research.modeling import compare_from_evidence

        claim = self._get_claim(action.object_id)
        if claim is None:
            claim = (self._claims() or [None])[0]
        if claim is None:
            return False
        evidence = [e for e in self.evidence_for_claim(claim) if e.excerpt]
        if not evidence:
            self._notes.append("缺少可用原文, 无法提出有来源的模型 (先做定向检索)")
            self.routes.record_failure(claim.id, "missing_model_evidence",
                                       "无已读取原文", scientific=False,
                                       recovery_condition="先 retrieve_targeted + read_source")
            self._save_routes()
            return False

        comparison = compare_from_evidence(claim, evidence, self.spec.contract)
        declaration = declare_capability(
            _claim_category(claim), available=self.available,
            has_data=bool(claim.study.data_ref or claim.study.rows),
            has_design=claim.study.design.value not in ("", "none"))
        models: list[ResearchModel] = []
        for mechanism in comparison.mechanisms:
            models.append(ResearchModel(
                name=f"{mechanism.name}-{claim.id}",
                natural_language=(f"基于 {len(evidence)} 条原文的候选机制 "
                                  f"({mechanism.name}), 待核验"),
                formal_encoding=mechanism.formal_encoding,
                variables=list(claim.variables),
                variable_domains=dict(claim.variable_domains),
                mechanism=mechanism.relation or mechanism.name,
                source_refs=list(mechanism.source_refs),
                origin=Origin.proposed,
                assumptions=list(claim.assumption_ids) + list(mechanism.assumptions),
                approximations=[mechanism.noise] if mechanism.noise else [],
                boundaries=mechanism.boundaries,
                fidelity=mechanism.fidelity,
                verified_scope="未验证: 该模型下的结论需重新推导与核验",
            ))
        chosen_id = ""
        for model in models:
            if model.name.startswith(next((m.name for m in comparison.mechanisms
                                           if m.id == comparison.selected), "")):
                chosen_id = model.id
        chosen = next((m for m in models if m.id == chosen_id), models[0])
        chosen.selected = True
        updated = claim.model_copy(update={"model_ref": ObjectRef(id=chosen.id,
                                                                 version=chosen.version)})
        self._selected_models[claim.id] = chosen.id

        writes = [(KIND_MODEL, m.id, m.model_dump(mode="json")) for m in models]
        writes.append((KIND_CLAIM, updated.id, updated.model_dump(mode="json")))
        # 可区分检验 → 实验规格 (仍需人工/后续授权才能执行, 不产生"已执行结果")
        if comparison.distinguishing:
            from src.experiments.planner import design_experiment
            from src.experiments.schemas import ExperimentPurpose

            test = comparison.distinguishing[0]
            spec = design_experiment(claim, gaps=[], evidence=evidence,
                                     distinguishing=test,
                                     model=chosen.model_dump(mode="json"))
            spec.purpose = ExperimentPurpose.compare_mechanisms
            writes.append((KIND_GAP, f"exp-{spec.id}", spec.model_dump(mode="json")))
            self.append_step(
                writes=[],
                events=[("distinguishing_test_proposed",
                         {"claim_id": claim.id, "spec_id": spec.id,
                          "kind": test.kind, "statement": test.statement,
                          "discriminates": test.discriminates})],
                idempotency_key=f"distinguish:{claim.id}:{hash_payload(test.to_dict())}",
            )

        self.append_step(
            writes=writes,
            events=[("model_proposed", {"claim_id": claim.id, "model_id": chosen.id,
                                        "version": chosen.version,
                                        "selected": True,
                                        "candidates": [m.id for m in models],
                                        "sources": chosen.source_refs,
                                        "why_selected": comparison.why_selected,
                                        "weak": [m.id for m in comparison.weak],
                                        "terms": len(comparison.terms),
                                        "conflicts": comparison.conflicts,
                                        "capability": declaration.describe()})],
            idempotency_key=f"model:{claim.id}:{hash_payload([m.source_refs for m in models])}",
        )
        if comparison.weak:
            self._notes.append(
                f"{claim.id}: 候选模型 {len(models)} 个, 其中 {len(comparison.weak)} 个为弱候选 "
                f"(缺来源或无可观测预测)")
        return True

    def model_comparison(self, claim_id: str = "") -> dict:
        """最近一次候选模型比较结果 (供工作台展示"候选与舍弃理由")。"""
        claim = self._get_claim(claim_id) if claim_id else (self._claims() or [None])[0]
        if claim is None:
            return {}
        evidence = [e for e in self.evidence_for_claim(claim) if e.excerpt]
        if not evidence:
            return {}
        from src.research.modeling import compare_from_evidence

        return compare_from_evidence(claim, evidence, self.spec.contract).to_dict()

    def model_selection(self, claim_id: str = "") -> dict:
        """当前选中的模型 (供工作台与交付物展示, 计划书 §9.5)。"""
        from src.research.capability import candidate_scheme, declare_capability
        from src.research.store import KIND_MODEL

        models = [ResearchModel.model_validate(m) for m in self.store.list_latest(KIND_MODEL)]
        claims = self._claims()
        if claim_id:
            claims = [c for c in claims if c.id == claim_id]
        out: dict = {"models": [], "claims": {}}
        for model in models:
            out["models"].append({
                "id": model.id, "version": model.version, "name": model.name,
                "selected": bool(model.selected),
                "sources": len(model.source_refs),
                "fidelity": model.fidelity,
                "verified_scope": model.verified_scope,
            })
        for claim in claims:
            ref = claim.model_ref
            cap = declare_capability(
                _claim_category(claim), available=self.available,
                has_data=bool(claim.study.data_ref or claim.study.rows),
                has_design=claim.study.design.value not in ("", "none"))
            # 只有"确实需要领域模型"的命题 (应用/数据类) 才把缺模型当问题;
            # 纯形式化命题 (不等式/恒等/单调性) 不需要领域模型, 不能虚报缺失。
            model_required = claim.claim_type != ClaimType.definitional
            if ref:
                state = "selected"
            elif model_required:
                state = "missing_selection"
            else:
                state = "not_required"
            out["claims"][claim.id] = {
                "claim_type": claim.claim_type.value,
                "capability": cap.describe(),
                "capability_action": cap.action,
                "declared_scheme": candidate_scheme(claim)[0],
                "model_required": model_required,
                "model_ref": ({"id": ref.id, "version": ref.version} if ref else None),
                "selected_state": state,
            }
        return out

    def select_model_for_claim(self, claim_id: str, model_id: str = "") -> dict:
        """显式选中领域模型 (计划书 §5.2), 并把选择落盘为一次可审计的步骤。"""
        from src.research.capability import select_model
        from src.research.store import KIND_MODEL

        claim = self._get_claim(claim_id)
        if claim is None:
            return {"ok": False, "reason": f"命题不存在: {claim_id}"}
        models = [ResearchModel.model_validate(m) for m in self.store.list_latest(KIND_MODEL)]
        outcome = select_model(self.store, claim, models, prefer=model_id,
                               reason="用户/系统显式选择" if model_id else "自动选择候选")
        if not outcome.get("ok"):
            return outcome
        updates = outcome.pop("updates")
        updated_claim = outcome.pop("claim")
        writes = [(KIND_MODEL, m.id, m.model_dump(mode="json")) for m in updates]
        writes.append((KIND_CLAIM, updated_claim.id, updated_claim.model_dump(mode="json")))
        self.append_step(
            writes=writes,
            events=[("model_selected", {"claim_id": claim.id,
                                        "model_id": outcome["selected"],
                                        "version": outcome.get("version", 1),
                                        "reason": outcome.get("reason", "")})],
            idempotency_key=f"select-model:{claim.id}:{outcome['selected']}",
        )
        self._selected_models[claim.id] = outcome["selected"]
        return outcome

    def _selected_mechanism(self, claim_id: str) -> dict:
        """当前为某命题选中的候选机制 (P1-3: 建议必须由它导出)。"""
        try:
            comparison = self.model_comparison(claim_id) or {}
        except Exception:  # noqa: BLE001 - 模型比较不可用时不阻断其余流程
            return {}
        for mechanism in comparison.get("mechanisms", []) or []:
            if mechanism.get("selected"):
                return dict(mechanism)
        return {}

    def _distinguishing_for(self, claim_id: str):
        """把已记录的冲突预测转成可区分检验对象 (没有则 None)。"""
        try:
            comparison = self.model_comparison(claim_id) or {}
        except Exception:  # noqa: BLE001
            return None
        items = comparison.get("distinguishing") or []
        if not items:
            return None
        from src.research.modeling import DistinguishingTest

        first = dict(items[0])
        return DistinguishingTest(
            kind=str(first.get("kind", "derivation") or "derivation"),
            statement=str(first.get("statement", "")),
            discriminates=list(first.get("discriminates") or []),
            expected_if_a=str(first.get("expected_if_a", "")),
            expected_if_b=str(first.get("expected_if_b", "")),
            cost=int(first.get("cost", 1) or 1))

    def _has_open_obligation(self, claim_id: str) -> bool:
        return any(o.claim_id == claim_id and o.status != ObligationStatus.closed
                   for o in self._obligations())

    def _act_design_experiment(self, action: ResearchAction) -> bool:
        """为具体未决问题生成实验/仿真规格 (计划书 §8); 绝不产生"已执行结果"。"""
        from src.experiments.planner import design_experiment

        claim = self._get_claim(action.object_id) or (self._claims() or [None])[0]
        if claim is None:
            return False
        distinguishing = self._distinguishing_for(claim.id)
        # P1-3: 只对有明确竞争预测或未闭义务的对象生成建议; 否则不套模板充数
        if distinguishing is None and not self._has_open_obligation(claim.id):
            self._refuse_action(action, "既没有竞争预测也没有未闭义务, 不套模板生成建议")
            return False
        spec = design_experiment(claim, gaps=[g.model_dump(mode="json") for g in
                                              self._gaps(self._claims(), self._obligations())],
                                 evidence=self._evidence(),
                                 distinguishing=distinguishing,
                                 model=self._selected_mechanism(claim.id))
        self.append_step(
            writes=[(KIND_GAP, f"exp-{spec.id}", spec.model_dump(mode="json"))],
            events=[("experiment_spec_proposed", {"claim_id": claim.id, "spec_id": spec.id,
                                                  "status": spec.execution_status})],
            idempotency_key=f"exp:{claim.id}",
        )
        return True

    def _act_revise_hypothesis(self, action: ResearchAction) -> bool:
        """按反馈修订假设/限定范围, 生成新版本并传播失效。"""
        if action.object_id.startswith("asm-"):
            affected = self.revise_assumption(action.object_id, action.reason)
            return bool(affected) or True
        claim = self._get_claim(action.object_id)
        if claim is None:
            return False
        # 弱化版本: 保留原命题 (不覆盖), 新建更弱命题并建立依赖
        weaker = self._weaken_claim(claim, action.reason)
        if weaker is None:
            return False
        self.append_step(
            writes=[(KIND_CLAIM, weaker.id, weaker.model_dump(mode="json")),
                    (KIND_ROUTE, self.routes.ensure_route(
                        weaker.id, goal=weaker.statement, strategy="weaker_conclusion").id,
                     self.routes.ensure_route(
                         weaker.id, goal=weaker.statement,
                         strategy="weaker_conclusion").model_dump(mode="json"))],
            events=[("hypothesis_revised", {"original": claim.id, "weaker": weaker.id,
                                            "reason": action.reason})],
            idempotency_key=f"weaken:{claim.id}:{weaker.relation.value}",
        )
        return True

    def _weaken_claim(self, claim: Claim, reason: str) -> Claim | None:
        """把严格不等式弱化为非严格, 或把无条件结论限定到声明域。"""
        from src.research.schemas import Relation

        mapping = {Relation.gt: Relation.ge, Relation.lt: Relation.le}
        new_relation = mapping.get(claim.relation, claim.relation)
        if new_relation == claim.relation and not claim.variable_domains:
            self._notes.append(f"{claim.id}: 无法自动弱化 (缺少可放宽的条件), 需人工定义")
            return None
        statement = claim.statement
        if new_relation != claim.relation:
            statement = statement.replace(claim.relation.value, new_relation.value, 1)
        else:
            conds = ", ".join(f"{v} ∈ {d}" for v, d in claim.variable_domains.items())
            statement = f"在 {conds} 条件下: {claim.statement}"
        weaker = claim.model_copy(update={
            "id": new_id("clm"), "version": 1, "statement": statement,
            "relation": new_relation, "dependencies": [claim.id],
            "origin": Origin.derived, "status": ClaimStatus.proposed,
            "assurance": Assurance.unverified, "verification_scope": None,
            "support_kind": SupportKind.none, "coverage": Coverage.step,
            "validation_status": ValidationStatus.unchecked,
            "novelty_status": NoveltyStatus.unchecked, "obligations": [],
            "notes": f"由 {claim.id} 弱化而来 ({reason or '用户/内核要求放宽条件'})",
        })
        return weaker

    def _act_retrieve_targeted(self, action: ResearchAction) -> bool:
        """定向检索: 缺口 → 自设检索式 → 带定位的候选证据 + 覆盖记录。"""
        if not self.retrieval_available:
            self._notes.append("未授权检索且无可用资料库, 跳过定向检索")
            return False
        from src.kb.bridge import gather_sources

        claim = self._get_claim(action.object_id)
        if claim is None:
            claims = self._claims()
            claim = claims[0] if claims else None
        if claim is None:
            return False
        gap_type = action.target_gap or GapType.missing_evidence.value
        topic = self._retrieval_topic()
        items, coverage = gather_sources(
            self.knowledge if self.knowledge_available else None,
            self.spec.contract, topic=topic, policy=self.spec.source_policy,
            claim=claim, gap_type=gap_type, terminology=self.terminology,
            search_fn=self.search_fn)
        self._record_coverage(coverage)
        if coverage.ingested:
            self._refresh_knowledge(topic)
        self._notes.append(
            f"定向检索 {len(coverage.queries)} 条检索式: 命中 {coverage.hits} 条, "
            f"入库 {coverage.ingested}, 去重后证据 {len(items)} 条"
            + (f", 失败 {len(coverage.failures)}" if coverage.failures else ""))
        if not coverage.executed:
            self.routes.record_failure(
                claim.id, "retrieval_failed",
                "; ".join(coverage.failures) or "检索能力不可用", scientific=False,
                recovery_condition="恢复检索能力后重试")
            self._save_routes()
            return False
        if not items:
            # 无命中 ≠ 不存在: 记录检索边界
            self.routes.record_failure(claim.id, "no_retrieval_hit", "在所检索范围内无命中",
                                       scientific=False, recovery_condition="扩大术语/范围后重试")
            self._save_routes()
            return False
        # R3: 检索结果在写库时就绑定到本命题 —— 证据归属不能等到缺口统计时再猜
        items = [e.model_copy(update={"claim_id": e.claim_id or claim.id}) for e in items]
        writes = [(KIND_EVIDENCE, e.id, e.model_dump(mode="json")) for e in items]
        self.append_step(
            writes=writes,
            events=[("evidence_retrieved", {"claim_id": claim.id, "count": len(items),
                                            "query": coverage.queries[0] if coverage.queries else "",
                                            "failures": coverage.failures,
                                            "channels": [],
                                            "dropped_out_of_scope": 0,
                                            "coverage": coverage.scope_note})],
            idempotency_key=f"retrieve:{claim.id}:{hash_payload(coverage.queries)}",
        )
        return True

    def _act_read_source(self, action: ResearchAction) -> bool:
        """回到原文读取完整上下文 (计划书 §6.1-4)。"""
        if not self.knowledge_available:
            return False
        evidence = self._evidence()
        target = next((e for e in evidence if e.id == action.object_id), None)
        if target is None:
            target = next((e for e in evidence
                           if e.support == SupportKindOfEvidence.insufficient), None)
        if target is None:
            return False
        from src.kb.service import SourceRef

        ref = SourceRef(source_id=target.source_id, chunk_id=target.chunk_id,
                        title=target.title, excerpt=target.excerpt,
                        locator=target.location, page=target.page,
                        char_start=target.char_start, char_end=target.char_end)
        read = self.knowledge.read(ref)
        if read.get("failure") and not read.get("text"):
            self.routes.record_failure(target.id, "read_failed", str(read["failure"]),
                                       scientific=False, recovery_condition="补齐全文后重试")
            self._save_routes()
            return False
        updated = target.model_copy(update={
            "excerpt": (read.get("text") or target.excerpt)[:2000],
            "location": read.get("locator") or target.location,
            "page": read.get("page") or target.page,
            "char_start": read.get("char_start", target.char_start),
            "notes": (target.notes + f" {read.get('failure', '')}").strip(),
        })
        self.append_step(
            writes=[(KIND_EVIDENCE, updated.id, updated.model_dump(mode="json"))],
            events=[("source_read", {"evidence_id": updated.id, "locator": updated.location,
                                     "truncated": read.get("truncated", False)})],
            idempotency_key=f"read:{updated.id}:{hash_payload(read.get('text', '')[:200])}",
        )
        return True

    def _act_extract_result(self, action: ResearchAction) -> bool:
        """从已读取的原文中抽取定理卡/适用条件 (规则抽取, 只作候选)。"""
        from src.rag.theorem_extractor import extract_source_evidence

        evidence = self._evidence()
        target = next((e for e in evidence if e.id == action.object_id), None) or \
            next((e for e in evidence if e.support == SupportKindOfEvidence.insufficient), None)
        if target is None or not target.excerpt:
            return False
        cards = extract_source_evidence(target.excerpt, literature_id=target.source_id,
                                        title=target.title, doi=target.doi,
                                        source_kind=target.source_kind.value)
        if not cards:
            self.routes.record_failure(target.id, "no_card_extracted",
                                       "原文中未识别到定理/定义结构", scientific=False,
                                       recovery_condition="换用其他来源或人工标注")
            self._save_routes()
            return False
        writes = [(KIND_EVIDENCE, c.id, c.model_dump(mode="json")) for c in cards]
        self.append_step(
            writes=writes,
            events=[("cards_extracted", {"from": target.id, "count": len(cards)})],
            idempotency_key=f"extract:{target.id}:{len(cards)}",
        )
        return True

    def _act_interpret_evidence(self, action: ResearchAction) -> bool:
        """判定候选证据与命题的支持关系, 写 EvidenceLink (计划书 §6.2 第二项检查)。

        有模型可用时走带条件的语义判定; 否则退化为规则判定 (两者都只写证据对象,
        命题状态一律由中央规则计算)。

        R3: 只判定**本命题**的候选证据 —— 早期实现一次判定全部待判定证据, 并可能
        回退到第一条命题, 于是 A 命题的证据会被记成 B 命题的支持。
        """
        from src.research.evidence import assess_support, build_support_judge, detect_contradictions

        claim = self._get_claim(action.object_id)
        if claim is None:
            # 不允许悄悄换一条命题: 对象不明确时宁可不动
            self._notes.append(
                f"interpret_evidence: 对象 {action.object_id or '(空)'} 不是本问题的命题, 已跳过")
            return False
        pending = [e for e in self.evidence_for_claim(claim)
                   if e.support == SupportKindOfEvidence.insufficient]
        if not pending:
            return False
        judge = self._support_judge
        if judge is None and self.llm is not None:
            judge = build_support_judge(self.metered_llm("evidence_judge"))
            self._support_judge = judge
        judged = [(judge or assess_support)(claim, e) for e in pending]
        # 逐条写回命题归属: 判定结果既落在证据对象上, 也形成带版本的关系记录
        judged = [e.model_copy(update={"claim_id": e.claim_id or claim.id}) for e in judged]
        judged = detect_contradictions(judged)
        writes = [(KIND_EVIDENCE, e.id, e.model_dump(mode="json")) for e in judged]
        links = []
        for item in judged:
            link = self.link_evidence(claim, item)
            links.append(link)
            writes.append((KIND_EVIDENCE_LINK, link.id, link.model_dump(mode="json")))
        self.append_step(
            writes=writes,
            events=[("evidence_interpreted",
                     {"claim_id": claim.id, "judged": len(judged),
                      "supports": sum(1 for e in judged
                                      if e.support == SupportKindOfEvidence.supports)})],
            idempotency_key=f"interpret:{claim.id}:{hash_payload([e.id for e in judged])}",
        )
        return True

    def _resolve_novelty_lookup(self):
        """新颖性对照查询: 注入的 lookup → 本地知识底座 → 外部文献检索。

        三层都不能用时返回 None, `novelty.assess` 会保持 unchecked 并给出有界表述。
        """
        if self.novelty_lookup is not None:
            return self.novelty_lookup
        if self.knowledge_available:
            from src.kb.bridge import novelty_lookup

            return novelty_lookup(self.knowledge)
        try:
            from src.research.retrieval import build_lookup

            return build_lookup()
        except Exception:  # noqa: BLE001 - 检索工具不可用则保持未决
            return None

    def _act_compare_novelty(self, action: ResearchAction) -> bool:
        claim = self._get_claim(action.object_id)
        if claim is None:
            return False
        lookup = self._resolve_novelty_lookup()
        # P1-1: 授权证据库 (用户选定/绑定的资料源) 与本次新颖性检索范围分开记录
        coverage = self.spec.coverage
        retrieval_scope = ({"policy": coverage.policy.value,
                            "queries": list(coverage.queries),
                            "uncovered": list(coverage.uncovered)}
                           if coverage is not None else {})
        record = novelty_mod.assess(
            claim, lookup,
            evidence=self.evidence_for_claim(claim),
            evidence_scope=[x for x in (self.spec.source_set_id, self.spec.domain) if x],
            retrieval_scope=retrieval_scope,
        )
        self.append_step(
            writes=[(KIND_NOVELTY, record.id, record.model_dump(mode="json"))],
            events=[("novelty_assessed", {"claim_id": claim.id, "status": record.status.value})],
            idempotency_key=f"novelty:{claim.id}:{hash_payload(record.rows and len(record.rows))}",
        )
        updated = claim.model_copy(update={"novelty_status": record.status})
        self.append_step(
            writes=[(KIND_CLAIM, updated.id, updated.model_dump(mode="json"))],
            idempotency_key=f"novelty-claim:{claim.id}:{record.status.value}",
        )
        # 已经做过有界检索即视为"已检索待比较"; 未接入检索能力时保持 unchecked,
        # 不再重复派发同一动作。
        return record.status != NoveltyStatus.unchecked or self.knowledge_available

    def _switch_change(self, claim: Claim) -> dict:
        """换路时必须给出的**实质变化** (P1-2): 新机制 > 新条件 > 新子命题 > 换后端。

        只换工具名而研究问题不变会被 `RouteManager.revise` 拒绝, 并给出停止理由。
        """
        change: dict[str, str] = {}
        try:
            comparison = self.model_comparison(claim.id)
        except Exception:  # noqa: BLE001 - 模型比较不可用不影响换路
            comparison = {}
        for mechanism in comparison.get("mechanisms", []) or []:
            if not mechanism.get("selected") and mechanism.get("name"):
                change["new_mechanism"] = str(mechanism["name"])[:120]
                break
        if not change:
            boundaries = [e.location for e in self.evidence_for_claim(claim)
                          if e.location and e.applicability_conditions]
            if boundaries:
                change["new_condition"] = f"限定到已读原文的适用域 ({boundaries[0]})"
        if not change and claim.claim_type not in (ClaimType.definitional,):
            change["new_subclaim"] = f"弱化/限定版本: {claim.statement[:80]}"
        if not change:
            tried_tools = {f.tool for f in self.routes.failures_for(claim.id) if f.tool}
            change["tool_change"] = next(
                (t for t in ("z3", "sympy", "lean", "stats") if t not in tried_tools), "")
        return {k: v for k, v in change.items() if v}

    def _act_switch_strategy(self, action: ResearchAction) -> bool:
        """真实换路 (计划书 §5.4): 新建路线并保留失败原因, 不修改命题真假状态。"""
        claim = self._get_claim(action.object_id)
        if claim is None:
            return False
        self._switched.add(claim.id)
        self._force_switch = False
        failures = self.routes.failures_for(claim.id)
        failure = failures[-1] if failures else None
        reason = failure.reason if failure else (action.reason or "连续无进展")
        failure_kind = (failure.failure_kind if failure is not None
                        else FailureKind.budget_exhausted)
        route, message = self.routes.revise(
            claim.id, failure_kind, reason=reason, max_routes=self.budget.max_routes,
            **self._switch_change(claim))
        self._save_routes()
        if route is None:
            self._notes.append(f"{claim.id}: {message}")
            self._stopped_reason = self._stopped_reason or message
            return False
        self.append_step(
            writes=[(KIND_ROUTE, route.id, route.model_dump(mode="json"))],
            events=[("route_switched", {"claim_id": claim.id, "route_id": route.id,
                                        "strategy": route.strategy, "reason": reason,
                                        "failure_kind": failure_kind.value,
                                        "substantive_change": route.substantive_change})],
            idempotency_key=f"route:{route.id}",
        )
        self._notes.append(f"{claim.id}: 换路 → 策略 {route.strategy} ({message})")
        # 换路后允许同一义务换后端重试 —— 但只针对**工具型**义务: 规则型义务
        # (范围/混淆/证据) 依赖缺失的条件或资料, 换证明策略无法解决, 重开只会空转。
        tool_methods = ("sympy", "z3", "lean", "stats")
        for obligation in self._obligations():
            if obligation.claim_id != claim.id or obligation.status != ObligationStatus.blocked:
                continue
            if obligation.acceptance_method not in tool_methods:
                continue
            obligation.status = ObligationStatus.open
            obligation.detail = (obligation.detail + " [换路后重试]").strip()
            self._save_obligation(obligation)
        self._reconcile_claim(claim)
        return True

    # ------------------------------------------------------------------
    # 中央状态规则 (计划书 §2 P0-A06/A07)
    # ------------------------------------------------------------------
    def _with_status(self, claim: Claim, *, status: ClaimStatus, support_kind: SupportKind,
                     note: str = "", coverage: Coverage = Coverage.target,
                     validation: ValidationStatus = ValidationStatus.verified) -> Claim:
        return claim.model_copy(update={
            "status": status, "support_kind": support_kind, "coverage": coverage,
            "validation_status": validation, "verification_scope": VerificationScope.target,
            "assurance": self._assurance_for(support_kind) or claim.assurance,
            "verification_closure": self._verification_closure(claim),
            "notes": (claim.notes + " " + note).strip() if note else claim.notes,
        })

    @staticmethod
    def _assurance_for(support_kind: SupportKind) -> Assurance | None:
        return {
            SupportKind.theorem_application: Assurance.symbolic_checked,
            SupportKind.symbolic_check: Assurance.symbolic_checked,
            SupportKind.constraint_solve: Assurance.solver_checked,
            SupportKind.formal_proof: Assurance.formally_checked,
            SupportKind.statistical_estimate: Assurance.empirical_estimated,
            SupportKind.informal_argument: Assurance.informal_reviewed,
        }.get(support_kind)

    def _reconcile_claim(self, claim: Claim) -> Claim:
        """唯一的命题状态写入点: 由义务聚合与有效验证记录推导。

        只有**全部必要义务关闭**才升级为 supported; 任何义务被反例否决即 refuted;
        未关闭/受阻/未决一律保持 in_progress 或 blocked。
        """
        obligations = [o for o in self._obligations() if o.claim_id == claim.id]
        records = [v for v in self._verifications() if v.claim_id == claim.id and not v.stale]
        required = [o for o in obligations if o.required]
        open_required = [o for o in required if o.status == ObligationStatus.open]
        refuted = [o for o in required if o.status == ObligationStatus.refuted]
        blocked = [o for o in required if o.status == ObligationStatus.blocked]
        closed = [o for o in required if o.status == ObligationStatus.closed]

        updates: dict[str, Any] = {"verification_closure": self._verification_closure(claim)}

        if refuted:
            witness = next((o.counterexample for o in refuted if o.counterexample), {})
            updates.update({
                "status": ClaimStatus.refuted,
                "coverage": Coverage.target,
                "validation_status": ValidationStatus.counterexample_found,
                "support_kind": refuted[0].support_kind or claim.support_kind,
                "assurance": self._assurance_for(refuted[0].support_kind) or claim.assurance,
                "verification_scope": VerificationScope.target,
                "notes": (claim.notes + f" 被反例否决: {witness}").strip(),
            })
        elif required and not open_required and not blocked and len(closed) == len(required):
            # 全部必要义务关闭 → 检查有效验证记录与编码对齐
            aligned = [v for v in records if self._record_aligned(v, claim)]
            estimate = claim.effect_estimate or {}
            if estimate and not estimate.get("ci_excludes_zero", True):
                # 统计估计的区间跨零: 不能作为"效应存在"的支持
                updates.update({
                    "status": ClaimStatus.blocked,
                    "coverage": Coverage.step,
                    "validation_status": ValidationStatus.unknown,
                    "notes": (claim.notes + " 效应95%CI包含0, 不足以支持该结论").strip(),
                })
            elif not aligned:
                updates.update({
                    "status": ClaimStatus.in_progress,
                    "coverage": Coverage.step,
                    "validation_status": ValidationStatus.unknown,
                    "notes": (claim.notes + " 义务已关闭但缺少与原命题对齐的有效验证记录").strip(),
                })
            else:
                kinds = [o.support_kind for o in closed if o.support_kind != SupportKind.none]
                support_kind = kinds[0] if kinds else SupportKind.informal_argument
                updates.update({
                    "status": ClaimStatus.supported,
                    "coverage": Coverage.target,
                    "validation_status": ValidationStatus.verified,
                    "support_kind": support_kind,
                    "assurance": self._assurance_for(support_kind) or claim.assurance,
                    "verification_scope": VerificationScope.target,
                })
        elif blocked and not open_required:
            updates.update({
                "status": ClaimStatus.blocked,
                "validation_status": ValidationStatus.unknown,
                "notes": (claim.notes + " 存在受阻义务, 保持未决").strip(),
            })
        else:
            updates.update({
                "status": ClaimStatus.in_progress if closed or records else claim.status,
                "validation_status": claim.validation_status
                if claim.status == ClaimStatus.supported else ValidationStatus.unknown,
            })

        updated = claim.model_copy(update=updates)
        if updated.model_dump() == claim.model_dump():
            return updated
        version = self.store.put(KIND_CLAIM, updated.id, updated.model_dump(mode="json"))
        updated = updated.model_copy(update={"version": version})
        self.store.append_event(
            "claim_state_reconciled",
            {"claim_id": updated.id, "status": updated.status.value,
             "coverage": updated.coverage.value, "support_kind": updated.support_kind.value},
            idempotency_key=f"state:{updated.id}:{version}",
        )
        return updated

    @staticmethod
    def _record_aligned(record: VerificationRecord, claim: Claim) -> bool:
        from src.research.acceptance import _aligned

        return _aligned(record, claim)

    # ------------------------------------------------------------------
    # 证据挂接 (外部调用入口)
    # ------------------------------------------------------------------
    def attach_evidence(self, claim_id: str, items: list[SourceEvidence],
                        judge: bool = False) -> list[str]:
        """挂接候选证据到**指定命题**。默认不升级证据分级 (支持关系未判定)。

        R3: 挂接时必须写入命题归属 (证据对象的 `claim_id` + 版本化的
        `EvidenceLink`), 否则同一批证据会被所有命题共享。
        """
        from src.research.evidence import assess_support

        claim = self._get_claim(claim_id)
        if claim is None:
            return []
        items = [item.model_copy(update={"claim_id": claim_id}) for item in items]
        if judge:
            items = [assess_support(claim, item) for item in items]
        writes = [(KIND_EVIDENCE, item.id, item.model_dump(mode="json")) for item in items]
        for item in items:
            link = self.link_evidence(claim, item)
            writes.append((KIND_EVIDENCE_LINK, link.id, link.model_dump(mode="json")))
        self.append_step(
            writes=writes,
            events=[("evidence_attached", {"claim_id": claim_id, "count": len(items),
                                           "judged": judge})],
            idempotency_key=f"ev:{claim_id}:{hash_payload([i.id for i in items])}",
        )
        if judge:
            grade = self._grade_evidence(items)
            if grade != claim.evidence_grade:
                updated = claim.model_copy(update={"evidence_grade": grade})
                self.append_step(
                    writes=[(KIND_CLAIM, updated.id, updated.model_dump(mode="json"))],
                    idempotency_key=f"grade:{claim_id}:{grade.value}",
                )
        return [item.id for item in items]

    # ------------------------------------------------------------------
    # 分支 (fork): 从既有快照派生新的研究问题
    # ------------------------------------------------------------------
    def fork_from_snapshot(self, source_snapshot_id: str = "", claim_ids: list[str] | None = None,
                           problem_id: str = "") -> dict:
        """从既有冻结快照派生一个新问题 (计划书 §9.1 的第三种操作)。

        明确记录: 哪些结论作为**前提导入**(需重新检查条件), 哪些必须重算。
        不复制验证记录 —— 旧验证绑定的是旧命题版本, 直接复用等于静默继承结论。
        """
        source = self.store.load_snapshot(source_snapshot_id or None)
        if source is None:
            return {"ok": False, "reason": "未找到可派生的快照"}
        selected = [c for c in source.claims
                    if (not claim_ids or c.id in claim_ids)]
        if not selected:
            return {"ok": False, "reason": "快照中没有可导入的结论"}

        new_problem = problem_id or new_id("prob")
        imported: list[str] = []
        writes: list[tuple[str, str, dict]] = []
        for claim in selected:
            forked = claim.model_copy(update={
                "id": new_id("clm"), "version": 1,
                "problem_id": new_problem,   # R6: 派生命题归属**新**问题
                "status": ClaimStatus.proposed,
                "assurance": Assurance.unverified,
                "support_kind": SupportKind.none,
                "coverage": Coverage.step,
                "validation_status": ValidationStatus.unchecked,
                "novelty_status": NoveltyStatus.unchecked,
                "verification_scope": None,
                "verification_closure": {},
                "obligations": [],
                "dependencies": list(claim.dependencies),
                "notes": (claim.notes + f" 由快照 {source.snapshot_id} 的 {claim.id} 派生导入, "
                                        "其前提与条件需在本问题下重新检查").strip(),
            })
            writes.append((KIND_CLAIM, forked.id, forked.model_dump(mode="json")))
            imported.append(forked.id)
            route = self.routes.ensure_route(forked.id, goal=forked.statement,
                                             strategy=self._plan_strategy(forked))
            writes.append((KIND_ROUTE, route.id, route.model_dump(mode="json")))

        # F0-5: 派生必须同时创建**新问题的规格**。只导入命题而不建规格时,
        # 新问题在存储里没有身份, 工作台按 problem_id 查不到 (404), 也无法续研。
        forked_spec = self.spec.model_copy(update={
            "problem_id": new_problem,
            "problem_statement": self.spec.problem_statement,
            "original_request": (self.spec.original_request
                                 or self.spec.problem_statement or self.spec.direction),
            "questions": [],          # 新问题尚未确认研究路线: 由用户/研究者重新确认
            "candidates": [],
            "confirmed": True,        # 命题已从快照导入, 可直接进入研究循环
            "selected_candidate_id": "",
            "unknown_fields": [],
        })
        writes.append((KIND_SPEC, forked_spec.problem_id,
                       forked_spec.model_dump(mode="json")))

        lineage = {
            "source_project_id": source.project_id,
            "source_snapshot_id": source.snapshot_id,
            "source_problem_id": self.spec.problem_id,
            "new_problem_id": new_problem,
            "imported_claims": imported,
            "reused_verifications": [],   # 明确不复用: 必须重算
            "must_recompute": imported,
            "created_at": utcnow(),
        }
        self.append_step(
            writes=writes,
            events=[("forked_from_snapshot", lineage)],
            idempotency_key=f"fork:{source.snapshot_id}:{new_problem}",
        )
        self._notes.append(
            f"已从快照 {source.snapshot_id} 派生 {len(imported)} 条结论作为待重验命题 "
            f"(新问题 {new_problem})")
        return {"ok": True, "lineage": lineage, "imported_claims": imported,
                "new_problem_id": new_problem, "problem_id": new_problem,
                "spec_created": True}

    # ------------------------------------------------------------------
    # 用户反馈: 解析为对象级动作并施加 (计划书 §5.1 / §6.1)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 研究过程摘要 (供 Web 工作台)
    # ------------------------------------------------------------------
    # 值得展示给用户的研究事件 (其余为内部记账)。
    # 唯一权威是 `logging_schema.SURFACE_EVENT_KINDS` (事件契约目录); 这里只做
    # 引用, 避免"目录里登记了、展示列表里漏了"两处各写一遍。
    RESEARCH_EVENTS = SURFACE_EVENT_KINDS

    def event_digest(self, limit: int = 40) -> list[dict]:
        """把事件日志压成"当前研究在做什么"的简短列表 (最近 limit 条)。

        R6: 按当前问题过滤 —— 同项目多问题时, A 的影子不能出现在 B 的"研究过程"里。
        事件表只按 project 记录, 因此归属判定用 payload 里的 `problem_id` (生命周期
        事件都带), 没有归属字段的逐命题事件再用命题归属判断; 两者都判不出来时保留
        (旧数据不能凭缺失就丢弃)。
        """
        from src.research.logging_schema import belongs_to_problem

        mine = {c.id for c in self._claims()}
        out: list[dict] = []
        for event in self.store.events():
            kind = event.get("type", "")
            if kind not in self.RESEARCH_EVENTS:
                continue
            payload = event.get("payload") or {}
            if not belongs_to_problem(payload, self.spec.problem_id, claim_ids=mine):
                continue
            out.append({"seq": event.get("seq"), "kind": kind,
                        "at": event.get("created_at", ""),
                        "detail": self._summarize_event(kind, payload)})
        return out[-limit:]

    def metrics(self) -> dict:
        """统一日志键与可聚合指标 (只读日志/账本, 不改动任何研究状态)。"""
        from src.research.logging_schema import metrics_from_store

        data = metrics_from_store(self.store, self.spec.problem_id,
                                  claim_ids={c.id for c in self._claims()})
        data["usage"] = self.usage_summary()
        data["stopped_reason"] = self._stopped_reason or self._budget_exhausted()
        data["identity"] = self.identity()
        return data

    @staticmethod
    def _summarize_event(kind: str, payload: dict) -> str:
        if kind == "evidence_retrieved":
            extra = f" (失败: {'; '.join(payload.get('failures') or [])})" \
                if payload.get("failures") else ""
            blocked = payload.get("dropped_out_of_scope") or 0
            scope_note = f" (范围外剔除 {blocked} 条)" if blocked else ""
            channels = payload.get("channels") or []
            chan_note = f" [通道 {'/'.join(channels)}]" if channels else ""
            return (f"定向检索「{payload.get('query', '')}」命中 "
                    f"{payload.get('count', 0)} 条{chan_note}{extra}{scope_note}")
        if kind == "source_read":
            return f"回到原文: {payload.get('locator', '')}" + \
                ("(片段被截断)" if payload.get("truncated") else "")
        if kind == "cards_extracted":
            return f"抽取定理/定义卡 {payload.get('count', 0)} 张"
        if kind == "evidence_interpreted":
            return (f"判定证据关系: 共 {payload.get('judged', 0)} 条, "
                    f"支持 {payload.get('supports', 0)} 条")
        if kind == "obligation_closed":
            return f"义务关闭: {payload.get('obligation_id', '')} (工具 {payload.get('tool', '')})"
        if kind == "claim_refuted":
            return f"找到反例, 命题被否定: {payload.get('witness')}"
        if kind == "rule_obligation_evaluated":
            state = "已关闭" if payload.get("closed") else "未满足"
            return (f"规则型义务 {payload.get('obligation_id', '')} {state}"
                    + (f" — {payload.get('certificate')}" if payload.get("certificate") else ""))
        if kind == "route_switched":
            return (f"换路: 策略 {payload.get('strategy', '')} "
                    f"(原因: {payload.get('reason', '')})")
        if kind == "novelty_assessed":
            return f"新颖性判定: {payload.get('status', '')}"
        if kind == "experiment_spec_proposed":
            return f"生成实验/仿真规格 {payload.get('spec_id', '')} (状态 {payload.get('status', '')})"
        if kind == "model_proposed":
            return f"提出候选模型 {payload.get('model_id', '')} (来源 {len(payload.get('sources') or [])} 条)"
        if kind == "model_selected":
            return (f"选中模型 {payload.get('model_id', '')} v{payload.get('version', 1)} "
                    f"(原因: {payload.get('reason', '')})")
        if kind == "capability_declared":
            return f"问题类型能力声明: {payload.get('declaration', '')}"
        if kind == "plan_proof":
            source = payload.get("source") or "rules"
            label = {"rules": "规则模板", "llm": "LLM 推导+子目标",
                     "llm_steps": "LLM 推导步骤",
                     "llm_subgoals": "LLM 子目标"}.get(source, source)
            extra = f", 新增待核验子目标 {payload.get('subgoals')} 条" \
                if payload.get("subgoals") else ""
            return f"制定证明路线: {payload.get('strategy', '')} (来源: {label}{extra})"
        if kind in ("proposal", "proposal_used", "proposal_rejected", "proposal_failed",
                    "proposal_skipped"):
            if kind == "proposal":
                accepted = payload.get("accepted") or {}
                rejected = payload.get("rejected") or []
                if accepted:
                    return (f"采纳模型提议: {accepted.get('action_type', '')} → "
                            f"{accepted.get('object_id', '')} (减少不确定性: "
                            f"{accepted.get('uncertainty_reduced', '')})")
                if rejected:
                    return f"拒绝模型提议 {len(rejected)} 条: " + \
                        "; ".join(str(r.get("reason", ""))[:80] for r in rejected)
                return "模型提议: 无"
            if kind == "proposal_used":
                return (f"模型提议 {payload.get('action_type', '')} → "
                        f"{payload.get('object_id', '')}")
            if kind == "proposal_rejected":
                return "提议被拒: " + "; ".join(str(r)[:80]
                                                for r in (payload.get("reasons") or []))
            return f"提议不可用: {payload.get('reason', '')}"
        if kind == "derive_step":
            checked = payload.get("adversarial_checked")
            hits = payload.get("adversarial_hits")
            extra = f", 反方审查 {checked} 项 (提出 {hits} 项)" if checked else ""
            return f"生成推导步骤 {payload.get('steps', 0)} 步 (待核验){extra}"
        if kind == "effect_estimated":
            return f"效应估计完成: {payload.get('estimate')}"
        if kind == "equality_condition":
            return f"等号条件: {payload.get('condition', '')}"
        if kind == "assumption_revised":
            return (f"假设 {payload.get('assumption_id', '')} 已修订 "
                    f"(原因: {payload.get('reason', '')}; 失效 {len(payload.get('affected') or [])} 条结论)")
        if kind == "hypothesis_revised":
            return f"命题弱化: {payload.get('original', '')} → {payload.get('weaker', '')}"
        if kind == "user_feedback":
            return f"用户反馈已施加: {len(payload.get('applied') or [])} 项"
        if kind == "formulated":
            return f"形式化完成: {payload.get('claims', 0)} 条命题 / {payload.get('obligations', 0)} 条义务"
        if kind == "candidate_confirmed":
            return f"确认研究路线: {payload.get('statement', '')}"
        if kind == "evidence_attached":
            return f"挂接证据 {payload.get('count', 0)} 条"
        if kind == "run_start":
            return f"开始研究问题 {payload.get('problem_id') or payload.get('project_id', '')}"
        if kind == "verification_recorded":
            return (f"登记验证记录 {payload.get('record_id', '')} "
                    f"(状态 {payload.get('status', '')})")
        if kind == "claim_state_reconciled":
            return (f"重算结论状态: {payload.get('claim_id', '')} → "
                    f"{payload.get('status', '')} (支持方式 {payload.get('support_kind', '')}, "
                    f"覆盖 {payload.get('coverage', '')})")
        if kind == "counterexample":
            return f"找到反例: {payload.get('witness')}"
        if kind == "distinguishing_test_proposed":
            return (f"提出可区分检验 ({payload.get('kind', '')}): "
                    f"{payload.get('statement', '')}")
        if kind == "writing_gaps_fed_back":
            return (f"写作缺口回流: {payload.get('count', 0)} 条新义务 "
                    "(必须继续研究, 不得只在正文里说明)")
        if kind == "forked_from_snapshot":
            return (f"从快照 {payload.get('source_snapshot_id', '')} 派生新问题 "
                    f"{payload.get('new_problem_id', '')} "
                    f"(待重算 {len(payload.get('imported_claims') or [])} 条)")
        return kind

    def feedback_context(self) -> dict:
        assumptions = {a.id: a.statement for a in self._assumptions()}
        claims = {c.id: c.statement for c in self._claims()}
        steps: dict[str, str] = {}
        for attempt in self.store.list_latest(KIND_ATTEMPT):
            for step in attempt.get("steps", []) or []:
                index = step.get("index")
                if index is None:
                    continue
                steps[f"{attempt.get('target_claim_id', '')}:{index}"] = \
                    f"{index}:{step.get('statement', '')}"
        return {"assumptions": assumptions, "claims": claims, "steps": steps}

    def submit_feedback(self, feedback: str, llm=None,
                        target_object_id: str = "") -> dict:
        """把自然语言反馈转成对象级动作并施加。

        硬约束 (计划书 §5.1 / §6.1):
        - 反馈必须落到具体 `assumption_id / claim_id / step_id` 上;
        - 无法唯一确定对象时**不猜**: 返回 `needs_clarification=True` 与澄清问题,
          研究状态保持不变;
        - 控制指令 (停止/暂停/导出) 与普通修订分开处理, 不触发重新研究。

        `target_object_id` (计划书 F1-5): 前端对象选择器已明确指定作用对象时,
        以它为准 —— 用户先选对象再写意见, 不应再靠语义解析去猜。
        """
        from src.research.intent import parse_intent

        feedback = (feedback or "").strip()
        if not feedback:
            return {"ok": False, "reason": "空反馈"}

        context = self.feedback_context()
        target = (target_object_id or "").strip()
        if target:
            known = set(context.get("assumptions", {})) | set(context.get("claims", {}))
            known |= {str(k).split(":", 1)[0] for k in (context.get("steps") or {})}
            if target not in known:
                return {
                    "ok": False, "needs_clarification": True,
                    "clarify": f"所选对象 {target} 不在当前研究问题内, 请重新选择",
                    "actions": [], "objects": context,
                }
            # 用显式对象绑定解析结果, 避免"说法里没提对象"导致解析失败
            feedback = f"[对象 {target}] {feedback}"

        actions = parse_intent(feedback, context, llm=llm)
        if target:
            bound = [a for a in actions if a.get("object_id") == target]
            if not bound:
                actions = [{**a, "object_id": target, "ambiguous": False}
                           for a in actions] or [{
                               "action_type": "revise_hypothesis" if target.startswith("asm-")
                               else "retrieve_targeted",
                               "object_id": target, "reason": feedback,
                               "ambiguous": False}]
        if not actions:
            return {
                "ok": False, "needs_clarification": True,
                "clarify": "无法把该意见解析为研究对象上的动作; 请指明它针对哪条假设/结论, "
                           "以及希望如何修改",
                "actions": [], "objects": context,
            }

        ambiguous = [a for a in actions if a.get("ambiguous")]
        if ambiguous and not any(not a.get("ambiguous") for a in actions):
            # 全部动作都无法确定对象 → 请求澄清, 不施加任何变更。
            # 同时回传候选对象清单, 让前端直接点选 (F1-5) 而不是只看到一条错误。
            return {
                "ok": False, "needs_clarification": True,
                "clarify": " / ".join(a.get("clarify", "") for a in ambiguous),
                "actions": actions, "objects": context,
                "candidates": _feedback_candidates(context),
            }

        applied: list[dict] = []
        for action in actions:
            if action.get("ambiguous"):
                continue
            kind = action["action_type"]
            object_id = action.get("object_id", "")
            if kind == "revise_hypothesis" and object_id.startswith("asm-"):
                affected = self.revise_assumption(object_id, action.get("reason", ""))
                applied.append({"action": kind, "object_id": object_id,
                                "affected_claims": affected})
            elif kind == "revise_hypothesis":
                claim = self._get_claim(object_id)
                if claim is None:
                    continue
                weakened = self._weaken_claim(claim, action.get("reason", ""))
                if weakened is not None:
                    self.store.put(KIND_CLAIM, weakened.id, weakened.model_dump(mode="json"))
                    route = self.routes.ensure_route(
                        weakened.id, goal=weakened.statement, strategy="weaker_conclusion")
                    self._save_routes()
                    applied.append({"action": kind, "object_id": weakened.id,
                                    "derived_from": claim.id, "route_id": route.id})
            elif kind in ("seek_counterexample", "check_step"):
                # 记录用户的质疑: 清掉同路线的执行记录, 让该动作可以重新执行
                target = object_id or (self._claims() or [None])[0]
                claim_id = target.id if hasattr(target, "id") else str(target)
                self._reopen_for_recheck(claim_id, reason=action.get("reason", ""))
                applied.append({"action": kind, "object_id": claim_id})
            elif kind == "stop_with_report":
                self._done = True
                applied.append({"action": kind, "object_id": ""})
            elif kind == "retrieve_targeted":
                self._reopen_for_recheck(object_id, reason="用户要求定向检索")
                applied.append({"action": kind, "object_id": object_id})
            else:
                applied.append({"action": kind, "object_id": object_id, "note": "未施加具体变更"})

        self.append_step(
            writes=[],
            events=[("user_feedback", {"feedback": feedback, "actions": actions,
                                       "applied": applied})],
            idempotency_key=f"feedback:{hash_payload({'f': feedback, 'a': applied})}",
        )
        self._exec_index_cache = None
        self.save_runtime()
        return {"ok": True, "actions": actions, "applied": applied,
                "needs_clarification": False}

    def _persist_gaps(self, gaps: list) -> None:
        """把本轮缺口落盘, 使它们出现在冻结快照与交付包里 (可事后复核)。

        必要性来自实测: `_gaps()` 只做**内存**计算, 而 `_freeze_snapshot()` 从存储读
        `KIND_GAP` —— 于是决策日志里明明有 `missing_evidence` 缺口, 交付包的
        `gaps.json` 却是 0 条, 事后无法复核"当时缺什么、为什么这么走"。

        接受 `ResearchGap` 或已序列化的 dict (`_compute_state()` 里是后者)。
        不覆盖实验规格: 它们同用 `KIND_GAP` 但 id 以 `exp-` 开头, 由设计实验的动作
        单独写入 (见 `_act_design_experiment`)。
        """
        for gap in gaps or []:
            data = gap if isinstance(gap, dict) else gap.model_dump(mode="json")
            gap_id = str(data.get("id", "") or "")
            if not gap_id or gap_id.startswith("exp-"):
                continue
            try:
                self.store.put(KIND_GAP, gap_id, data)
            except Exception as e:  # noqa: BLE001 - 落盘失败不得中断研究
                self._notes.append(f"缺口落盘失败 ({gap_id}): {e}")

    def _literature_impact_recheck(self) -> list[str]:
        """检索到的**相反来源**使命题失效重验, 返回受影响 claim id (计划书 P0-2)。

        为什么需要: 检索现在发生在研究循环内 (P0-1), 但判定层把一条来源判成
        `contradicts` 之后, 冻结的命题与验证记录**不受影响** —— 于是"检索到了相反结果"
        只留在证据表里, 结论照样成立。这就是计划书说的"发表前检索无法纠正先前推导"。

        处置与 `revise_assumption` 保持一致 (不另造机制):
        1. 命题写回 `in_progress`/`unknown` 并**落盘新版本** (版本号由存储递增);
        2. 现有验证记录**保留但标记 stale** (不删除、不改写, 保住审计线索);
        3. 相关义务重开, 由中央状态规则重新裁决 (`_reconcile_claim`);
        4. 记录"哪条来源改变了哪条结论", 供决策日志与人审复核。

        幂等: 同一来源只触发一次 (命题 notes 里已记该来源则跳过), 否则每步都会加版本。
        """
        affected: list[str] = []
        for claim in self._claims():
            contradictions = [
                item for item in self.evidence_for_claim(claim)
                if item.support == SupportKindOfEvidence.contradicts
                and item.id not in (claim.notes or "")]
            if not contradictions:
                continue
            reason = "检索到相反来源: " + "; ".join(
                f"{item.id}({str(item.title or '')[:40]})" for item in contradictions)
            # 1. 命题: 回到进行中并注明依据
            claim.status = ClaimStatus.in_progress
            claim.coverage = Coverage.step
            claim.validation_status = ValidationStatus.unknown
            claim.assurance = Assurance.unverified
            claim.verification_scope = None
            claim.notes = (claim.notes + f" [{reason}] 结论需重验").strip()
            self._save_claim(claim)
            # 2. 验证记录: 标记过期
            for record in self._verifications():
                if record.claim_id != claim.id or record.stale:
                    continue
                flagged = record.model_copy(update={
                    "stale": True,
                    "raw_output": (record.raw_output or "") + f"\n[{reason}]",
                })
                self.store.put(KIND_VERIFICATION, flagged.id, flagged.model_dump(mode="json"))
            # 3. 义务重开 (与用户要求复核同一口径: 只重开可机器核验的那些)
            for obligation in self._obligations():
                if obligation.claim_id != claim.id:
                    continue
                if obligation.status not in (ObligationStatus.open, ObligationStatus.blocked,
                                            ObligationStatus.closed):
                    continue
                if obligation.acceptance_method not in ("sympy", "z3", "lean", "stats"):
                    continue
                obligation.status = ObligationStatus.open
                obligation.validation_status = ValidationStatus.unchecked
                obligation.detail = (obligation.detail + f" [{reason}]").strip()
                self._save_obligation(obligation)
            # 4. 由中央规则重新裁决, 并留痕
            self._reconcile_claim(claim)
            affected.append(claim.id)
            self._notes.append(
                f"文献回写: {claim.id} 因相反来源失效重验 (新版本 v{claim.version}); "
                "旧验证记录保留但标记过期")
        return affected

    def _reopen_for_recheck(self, claim_id: str, reason: str = "") -> None:
        """按用户质疑重开受阻义务, 允许重新核验。

        旧验证记录**保留但标记 stale** (不删除、不改写), 由中央状态规则重新计算结论;
        否则会失去"曾被验证过/为何失效"的审计线索。
        """
        claim = self._get_claim(claim_id)
        if claim is None:
            return
        for record in self._verifications():
            if record.claim_id == claim_id and not record.stale:
                flagged = record.model_copy(update={
                    "stale": True,
                    "raw_output": (record.raw_output or "") + f"\n[用户要求复核: {reason}]",
                })
                self.store.put(KIND_VERIFICATION, flagged.id, flagged.model_dump(mode="json"))
        for obligation in self._obligations():
            if obligation.claim_id != claim_id:
                continue
            if obligation.status not in (ObligationStatus.open, ObligationStatus.blocked,
                                        ObligationStatus.closed):
                continue
            if obligation.acceptance_method not in ("sympy", "z3", "lean", "stats"):
                continue
            obligation.status = ObligationStatus.open
            obligation.validation_status = ValidationStatus.unchecked
            obligation.detail = (obligation.detail + f" [用户要求复核: {reason}]").strip()
            self._save_obligation(obligation)
        self._reconcile_claim(claim)

    # ------------------------------------------------------------------
    # 进度与预算
    # ------------------------------------------------------------------
    def _track_progress(self, progressed: bool) -> None:
        """进展跟踪: 连续无有效进展 → 置换路标志。

        标志**只能由真正的换路动作清除** (`_act_switch_strategy`), 否则一次"部分交付"
        失败就会把它清掉, 换路永远不会发生。
        """
        if progressed:
            self._no_progress = 0
            return
        self._no_progress += 1
        if self._no_progress >= self.budget.no_progress_limit:
            self._force_switch = True

    # ------------------------------------------------------------------
    # 快照与报告
    # ------------------------------------------------------------------
    def _freeze_snapshot(self) -> ResearchSnapshot:
        claims = self._claims()
        obligations = self._obligations()
        verifications = self._verifications()
        edges = []
        for c in claims:
            for dep in c.dependencies:
                edges.append((dep, c.id))
            for aid in c.assumption_ids:
                edges.append((aid, c.id))
            if c.model_ref:
                edges.append((c.model_ref.id, c.id))
        index: dict[str, list[str]] = {}
        for claim in claims:
            index[claim.id] = [v.id for v in verifications
                               if v.claim_id == claim.id and not v.stale]
        models = self._models()
        experiment_specs = []
        for data in self.store.list_latest(KIND_GAP):
            if str(data.get("id", "")).startswith("exp-"):
                experiment_specs.append(data)
        return ResearchSnapshot(
            project_id=self.spec.project_id,
            # R6: 快照自带问题/运行/分支身份 —— 产物目录、manifest、工作台查询
            # 都读取同一个身份, 而不是各自"取第一个规格"再拼对象。
            problem_id=self.spec.problem_id,
            run_id=self._run_id,
            branch_id=self.branch_id,
            spec_version=self.store.latest_version(KIND_SPEC, self.spec.problem_id) or self.spec.version,
            assumptions=self._assumptions(),
            definitions=[Definition.model_validate(d)
                         for d in self.store.list_latest(KIND_DEFINITION)],
            models=models,
            claims=claims,
            obligations=obligations,
            attempts=[ProofAttempt.model_validate(d) for d in self.store.list_latest(KIND_ATTEMPT)],
            evidence=self._evidence(),
            evidence_links=[EvidenceLink.model_validate(d)
                            for d in self.store.list_latest(KIND_EVIDENCE_LINK)],
            verifications=verifications,
            routes=self.routes.routes,
            gaps=[ResearchGap.model_validate(d) for d in self.store.list_latest(KIND_GAP)
                  if not str(d.get("id", "")).startswith("exp-")],
            dependency_edges=edges,
            novelty=[novelty_mod.NoveltyRecord.model_validate(d)
                     for d in self.store.list_latest(KIND_NOVELTY)],
            experiment_specs=experiment_specs,
            verification_index=index,
        )

    def _finalize(self, needs_clarification: bool = False) -> EngineResult:
        # R4: 本轮结束前把写作阶段发现的缺口回流成新的研究义务 (幂等, 不会重复创建)。
        # 放在冻结快照之前, 缺口与由此产生的义务必须出现在同一份快照里。
        reopened = False
        try:
            # 收尾前的缺口也要落盘: 冻结快照读存储, 否则最后一步的缺口会缺失
            self._persist_gaps((self._compute_state().get("gaps")) or [])
            snapshot = self._freeze_snapshot()
            reopened = self.writing_gap_feedback(snapshot)
        except Exception as e:  # noqa: BLE001 - 回流失败不得吞掉结论
            self._notes.append(f"写作缺口回流失败: {e}")
        snapshot = self._freeze_snapshot()
        gate = theory_validity_gate(snapshot.claims, snapshot.obligations,
                                    snapshot.verifications, snapshot.dependency_edges,
                                    snapshot=snapshot)
        usage = self.usage_summary()
        # 预算触顶时必须留下可导出的部分报告 (计划书 §9.3)
        stopped_reason = self._stopped_reason or self._budget_exhausted()
        if stopped_reason and stopped_reason not in " ".join(self._notes):
            self._notes.append(
                f"研究因预算停止: {stopped_reason} (以下为部分结果, 不代表研究已完成)")
        try:
            self.store.save_snapshot(snapshot)
        except Exception as e:  # noqa: BLE001 - 快照冲突不得吞掉结论
            self._notes.append(f"快照保存失败: {e}")
        self.save_runtime()
        return EngineResult(
            snapshot=snapshot, decisions=list(self._decisions), gate=gate,
            needs_clarification=needs_clarification,
            needs_confirmation=self._needs_confirmation,
            candidates=[c.model_dump(mode="json") for c in self.spec.candidates],
            notes=list(self._notes) + list(self._rejected), tool_calls=self._tool_calls,
            gaps=[g.model_dump(mode="json") for g in
                  self._gaps(self._claims(), self._obligations())],
            routes=[r.model_dump(mode="json") for r in self.routes.routes],
            failures=[f.to_dict() for f in self.routes.failures],
            usage=usage, stopped_reason=stopped_reason,
            feedback_reopened=reopened,
        )

    # ------------------------------------------------------------------
    # 用户反馈: 假设修订与失效传播
    # ------------------------------------------------------------------
    def revise_assumption(self, assumption_id: str, reason: str = "") -> list[str]:
        """放弃某假设, 生成新版本并使相关下游证明失效。返回失效的 claim id。"""
        data = self.store.get(KIND_ASSUMPTION, assumption_id)
        if not data:
            self._notes.append(f"未找到假设 {assumption_id}")
            return []
        assumption = Assumption.model_validate(data)
        assumption.accepted = False
        assumption.notes = (assumption.notes + f" 修订: {reason}").strip()
        assumption.version = assumption.version + 1
        graph = self._dependency_graph()
        stale = graph.stale_closure(assumption_id)
        affected = [c.id for c in self._claims() if c.id in stale]

        writes = [(KIND_ASSUMPTION, assumption.id, assumption.model_dump(mode="json"))]
        for claim in self._claims():
            if claim.id not in stale:
                continue
            updated = claim.model_copy(update={
                "status": ClaimStatus.in_progress,
                "coverage": Coverage.step,
                "validation_status": ValidationStatus.unknown,
                "assurance": Assurance.unverified,
                "verification_scope": None,
                "notes": (claim.notes + f" 依赖假设 {assumption_id} 已修订, 结论失效").strip(),
            })
            writes.append((KIND_CLAIM, updated.id, updated.model_dump(mode="json")))
            for obligation in self._obligations():
                if obligation.claim_id == claim.id:
                    reopened = obligation.model_copy(update={
                        "status": ObligationStatus.open,
                        "validation_status": ValidationStatus.unchecked,
                        "detail": (obligation.detail + " [假设修订后重新开启]").strip(),
                    })
                    writes.append((KIND_OBLIGATION, reopened.id,
                                   reopened.model_dump(mode="json")))
        for record in self._verifications():
            if record.claim_id in stale:
                flagged = record.model_copy(update={"stale": True})
                writes.append((KIND_VERIFICATION, flagged.id, flagged.model_dump(mode="json")))
        self.append_step(
            writes=writes,
            events=[("assumption_revised", {"assumption_id": assumption_id,
                                            "affected": affected, "reason": reason})],
            idempotency_key=f"revise:{assumption_id}:v{assumption.version}",
        )
        self._notes.append(f"假设 {assumption_id} 已修订, 下游失效: {affected}")
        return affected

    # ------------------------------------------------------------------
    # R4 后半段: 写作缺口回流研究循环
    # ------------------------------------------------------------------
    def writing_gap_feedback(self, snapshot: ResearchSnapshot | None = None) -> bool:
        """把写作阶段发现的缺口转成**新的研究义务** (计划书 §3 R4)。

        "回流"是要求缺口变成可执行对象, 而不是在正文里写一句"略":

        - 每条缺口转成一条 `ProofObligation`, 且**指向具体命题与命题版本**;
        - `acceptance_method` 取"能真正核查这条缺口"的方法, 于是协调者能把它
          派给 `derive_step` / `check_step` / `read_source`, 而不是只能人工读正文;
        - 严重度为 `blocking` 的缺口是 `required=True` —— 它重新阻塞该命题, 且
          **只有当前研究能力真能消解**才会是 blocking (补推导步骤/补验证输入),
          因此回流后重开研究循环是有意义的;
        - `advisory` 缺口是 `required=False` —— 它们要人确认或补资料才能消除,
          标成阻塞只会让结论永远无法交付; 这类缺口由验收门槛独立拦下并随交付物呈现
          (与反方审查义务同一原则);
        - **幂等**: 已存在同一 `claim_id + kind + statement` 的义务不再重复创建。
          因此本方法每轮结束时调用都安全, 缺口不会被反复"发现"成新义务而空转。

        返回**是否新增了会阻塞结论的缺口**: 调用方据此决定是否重开研究循环
        (`run()` 只在有新增阻塞缺口时继续), 否则本方法就是一个纯记账动作。

        本方法只新增义务, **不改写任何命题状态**, 也不声称缺口已被解决。
        """
        if snapshot is None:
            snapshot = self._freeze_snapshot()
        gaps = writing_gaps(snapshot)
        if not gaps:
            return False
        existing = {(o.claim_id, self._gap_kind_of(o), o.statement.strip())
                    for o in self._obligations()}
        writes = []
        created: list[WritingGap] = []
        for gap in gaps:
            if (gap.claim_id, gap.kind, gap.statement.strip()) in existing:
                continue
            if not self._claim_belongs_to_problem(gap.claim_id):
                # 缺口指向别的研究问题的命题 → 不得写进本问题的义务集 (R6)
                self._notes.append(
                    f"写作缺口 {gap.kind} 指向非本问题命题 {gap.claim_id}, 已拒绝回流")
                continue
            claim = self._get_claim(gap.claim_id)
            obligation = ProofObligation(
                statement=gap.statement,
                kind=f"writing_{gap.kind}",
                acceptance_method=_GAP_ACCEPTANCE.get(gap.kind, "manual"),
                claim_id=gap.claim_id,
                claim_version=claim.version if claim else 1,
                detail=gap.detail,
                coverage=Coverage.subgoal,
                required=gap.severity == "blocking",
            )
            data = obligation.model_dump(mode="json")
            data["gap_kind"] = gap.kind
            writes.append((KIND_OBLIGATION, obligation.id, data))
            existing.add((gap.claim_id, gap.kind, gap.statement.strip()))
            created.append(gap)
        if not writes:
            return False
        self.append_step(
            writes=writes,
            events=[("writing_gaps_fed_back",
                     {"count": len(created), "problem_id": self.spec.problem_id,
                      "gaps": [g.to_dict() for g in created]})],
            idempotency_key=("writing_gaps:" + hash_payload(
                {"p": self.spec.problem_id,
                 "g": sorted((g.claim_id, g.kind, g.statement) for g in created)})),
        )
        blocking = [g for g in created if g.severity == "blocking"]
        self._notes.append(
            f"写作阶段缺口已回流为 {len(created)} 条研究义务"
            + (f" (其中 {len(blocking)} 条阻塞结论, 需继续研究)" if blocking else ""))
        return bool(blocking)

    def _gap_kind_of(self, obligation: ProofObligation) -> str:
        """义务记录的写作缺口种类 (非写作义务返回空串)。"""
        data = self.store.get(KIND_OBLIGATION, obligation.id) or {}
        kind = str(data.get("gap_kind", ""))
        if kind:
            return kind
        return obligation.kind[len("writing_"):] if obligation.kind.startswith("writing_") else ""

    def _dependency_graph(self) -> DependencyGraph:
        graph = DependencyGraph()
        for c in self._claims():
            edges = list(c.dependencies) + list(c.assumption_ids)
            if c.model_ref:
                edges.append(c.model_ref.id)
            for dep in edges:
                try:
                    graph.add_edge(dep, c.id)
                except CyclicDependencyError:
                    self._notes.append(f"拒绝循环依赖: {dep} -> {c.id}")
        return graph

    def _add_equality_claim(self, parent: Claim, condition: str, tool: str) -> None:
        support_kind = support_kind_for_tool(tool)
        claim = Claim(
            statement=f"{parent.statement} 的等号条件: {condition}",
            lhs=parent.lhs, rhs=parent.rhs, relation=parent.relation,
            variables=parent.variables, variable_domains=parent.variable_domains,
            dependencies=[parent.id], origin=Origin.derived,
            status=ClaimStatus.supported,
            support_kind=support_kind or SupportKind.symbolic_check,
            coverage=Coverage.target,
            validation_status=ValidationStatus.verified,
            assurance=self._assurance_for(support_kind) or parent.assurance,
            verification_scope=VerificationScope.target,
            assumption_ids=parent.assumption_ids,
            evidence_grade=parent.evidence_grade,
            notes="由等号条件求解派生",
        )
        claim.id = f"eq-{parent.id}"
        record = VerificationRecord(
            id=f"ver-{claim.id}", tool=tool, input_hash=hash_payload({"eq": condition}),
            claim_id=claim.id, claim_version=1, assumption_ids=list(parent.assumption_ids),
            scope=VerificationScope.target, status="passed", certificate=condition,
            validation_status=ValidationStatus.verified, support_kind=support_kind,
        )
        # R4: 派生命题同样要有**可逐步复核**的推导步骤 —— 只写"supported + 证书"
        # 会让写作阶段报出 `missing_argument_chain` (正文无法复核), 而那确实是缺口。
        # 这里落盘的是真实发生过的两步: 条件由求解得到 → 由工具核验。
        attempt = ProofAttempt(
            id=f"pf-{claim.id}", target_claim_id=claim.id, target_version=1,
            strategy="equality_condition",
            steps=[
                ProofStep(index=0, rule="parent_claim",
                          statement=f"由 {parent.id} 的等号条件求解得到: {condition}",
                          justification=f"父命题 {parent.id}@{parent.version} 在闭合条件下取等号",
                          requires_conditions=[f"{v} ∈ {d}" for v, d
                                               in (parent.variable_domains or {}).items()]),
                ProofStep(index=1, rule="tool_check",
                          statement=f"用 {tool or '工具'} 核验等号条件成立",
                          justification=condition,
                          requires_conditions=list(parent.assumption_ids)),
            ],
            evidence_ids=[record.id], status="complete",
        )
        self.append_step(
            writes=[(KIND_CLAIM, claim.id, claim.model_dump(mode="json")),
                    (KIND_VERIFICATION, record.id, record.model_dump(mode="json")),
                    (KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))],
            events=[("equality_condition", {"claim_id": claim.id, "condition": condition,
                                            "attempt_id": attempt.id})],
            idempotency_key=f"eq:{claim.id}",
        )

    def _save_claim(self, claim: Claim) -> None:
        version = self.store.put(KIND_CLAIM, claim.id, claim.model_dump(mode="json"))
        claim.version = version

    def _save_obligation(self, obligation: ProofObligation) -> None:
        version = self.store.put(KIND_OBLIGATION, obligation.id,
                                 obligation.model_dump(mode="json"))
        obligation.version = version

    def _get_claim(self, claim_id: str) -> Claim | None:
        data = self.store.get(KIND_CLAIM, claim_id)
        return Claim.model_validate(data) if data else None

    def _get_obligation(self, obligation_id: str) -> ProofObligation | None:
        data = self.store.get(KIND_OBLIGATION, obligation_id)
        return ProofObligation.model_validate(data) if data else None





