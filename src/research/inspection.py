from __future__ import annotations

"""研究对象库的**只读检视** (合并计划 §5.5 / G01 的前置)。

为什么单独一层
--------------
`server._research_state`（工作台只读端点）此前必须 `TheoryEngine(spec, store, …)` 才读得出
对象 —— 一个**只读**接口因此依赖了"能执行研究动作"的引擎。抽出来之后:

- 读路径只需要"存储 + 规格", 不需要任何执行能力, 也不可能顺手推进研究;
- 引擎退役时这一层**原样保留**, 工作台不用跟着重写;
- "读"与"判"的边界更清楚: 本模块不写任何对象, 也不产生状态。

实现口径与引擎的同类只读方法**逐字对齐**(`_load` 回填权威版本号、按问题过滤命题、
逐命题证据归属)。两者的一致性由 `tests/test_store_inspection.py` 的差异用例把守 ——
在引擎删除之前, 它们必须给出同样的结果; 删除之后这些用例改指本模块。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.research.schemas import (
    Assumption,
    Claim,
    EvidenceLink,
    ProofObligation,
    ResearchGap,
    ResearchModel,
    ResearchSpec,
    SourceEvidence,
    VerificationRecord,
)

__all__ = ["StoreInspection"]


@dataclass
class StoreInspection:
    """从存储读出工作台需要的一切 (不执行、不写入任何研究动作)。

    `retrieval_available` 需要两个外部事实 (知识底座是否可用、授权策略), 由调用方
    传入 —— 本层不探测资料库, 也不读环境变量: 那属于装配层的职责。
    """

    store: Any
    spec: ResearchSpec
    knowledge_available: bool = False
    #: 引擎在内存里维护的运行计数 (动作/工具调用/token/费用/墙钟); 调用方给出真实值,
    #: 本层不自己猜。缺省表示"没有运行记录", 计数按 0 呈现。
    runtime_counters: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 对象读取 (与引擎 `_load` 同一口径: 回填权威版本号)
    # ------------------------------------------------------------------
    def _load(self, kind: str, model) -> list:
        versions = self.store.version_index(kind)
        out = []
        for data in self.store.list_latest(kind):
            obj = model.model_validate(data)
            if "version" in model.model_fields:
                obj.version = versions.get(obj.id, obj.version)
            out.append(obj)
        return out

    def claims(self, problem_id: str = "") -> list[Claim]:
        """当前**研究问题**的命题 (R6: 同项目多问题按问题隔离)。

        归属判定按**两条来源**, 缺一不可 —— 只看载荷里的 `problem_id` 时, 团队写下的
        命题 (载荷里没有这个字段, 归属记在 `_scope`) 会被当成"归属不明", 于是同项目
        每个问题都看得到全部命题 (实测: 两个问题的结论集合完全相同, R6 形同虚设):

        1. 命题自身的 `problem_id`;
        2. 对象的 `_scope.problem_id` (提交时由唯一提交口写入的运行身份)。

        两条都读不到 (旧数据) 时才退回"本问题的" —— 前提是项目里**没有**归属别的
        问题的命题, 否则归属不明的一律不展示 (fail-closed)。
        """
        rows = self.store.list_latest("claim")
        pid = problem_id or self.spec.problem_id

        def owner(row: dict) -> str:
            return str(row.get("problem_id")
                       or (row.get("_scope") or {}).get("problem_id") or "")

        known_others = {owner(row) for row in rows
                        if owner(row) and owner(row) != pid}
        out: list[Claim] = []
        for row in rows:
            mine = owner(row)
            if mine != pid and not (not mine and not known_others):
                continue
            claim = Claim.model_validate({k: v for k, v in row.items()
                                          if not k.startswith("_")})
            claim.version = int(row.get("__version__", claim.version) or claim.version)
            out.append(claim)
        return out

    def obligations(self) -> list[ProofObligation]:
        return self._load("obligation", ProofObligation)

    def verifications(self) -> list[VerificationRecord]:
        return self._load("verification", VerificationRecord)

    def evidence(self) -> list[SourceEvidence]:
        return self._load("evidence", SourceEvidence)

    def evidence_link_records(self) -> list[EvidenceLink]:
        return self._load("evidence_link", EvidenceLink)

    def models(self) -> list[ResearchModel]:
        return self._load("model", ResearchModel)

    def routes(self) -> list[Any]:
        """研究路线。

        与引擎同一来源: 运行记录里的 `route_state`(引擎每次落盘都会写)。
        **读不出运行记录时返回空列表而不是去猜** —— 路线状态带分支身份, 从
        `KIND_ROUTE` 行反推会得到与引擎不同的分支归属。
        """
        from src.research.routes import RouteManager

        manager = RouteManager.load(self.runtime_record().get("route_state"))
        return list(manager.routes)

    def assumptions(self) -> list[Assumption]:
        return self._load("assumption", Assumption)

    def attempts(self) -> list[dict]:
        return list(self.store.list_latest("attempt"))

    # ------------------------------------------------------------------
    # 逐命题证据归属 (计划书 §3 R3)
    # ------------------------------------------------------------------
    def evidence_links_for(self, claim: Claim) -> list[EvidenceLink]:
        """绑定到该命题**当前版本**的证据关系 (旧版本关系视为过期)。"""
        return [link for link in self.evidence_link_records()
                if link.claim_ref.id == claim.id
                and (not link.claim_ref.version or link.claim_ref.version == claim.version)]

    def evidence_for_claim(self, claim: Claim) -> list[SourceEvidence]:
        """**属于该命题**的证据: 自身的 `claim_id` 或带版本的 `EvidenceLink`。"""
        linked = {link.source_ref.id for link in self.evidence_links_for(claim)}
        return [e for e in self.evidence() if e.claim_id == claim.id or e.id in linked]

    # ------------------------------------------------------------------
    # 运行与用量
    # ------------------------------------------------------------------
    @property
    def retrieval_available(self) -> bool:
        """能否开展检索: 有可用资料库, 或策略授权自主检索 (P0-1 场景 ②)。"""
        if self.knowledge_available:
            return True
        policy = getattr(self.spec.source_policy, "value", self.spec.source_policy)
        return str(policy) in ("autonomous", "both")

    def runtime_record(self) -> dict[str, Any]:
        """落盘的运行记录 (旧理论引擎的 `KIND_RUNTIME`); 没有则空字典。

        读不出来**不等于**没有运行过: 调用方据此如实呈现 0 计数, 不编造进度。
        """
        try:
            return dict(self.store.get("runtime", self.spec.problem_id) or {})
        except Exception:  # noqa: BLE001 - 巡检不得因为存储故障而崩
            return {}

    def identity(self) -> dict[str, str]:
        """统一身份四元组 (project / problem / run / branch, 计划书 R6)。

        运行身份有**两个**可能来源, 顺序不能反:
        1. 旧引擎的运行记录 (`KIND_RUNTIME`, 理论路径);
        2. 团队运行状态 (`KIND_TEAM_RUN`, 统一入口)。

        只读第一种时, 团队运行的 `run_id` 在工作台里恒为空 —— 而 HTTP 启动响应、任务、
        事件与交付包用的都是同一个值, "三处必须是同一个 id" 因此在团队路径上直接断了
        (实测: `wb['run_id'] == start['run_id']` 失败)。这里按"最近一次运行"取,
        与问题列表/交付清单看到的是同一次运行。
        """
        record = self.runtime_record()
        run_id = str(record.get("run_id", "") or "")
        branch_id = str(record.get("branch_id", "") or "")
        if not run_id:
            latest = self._latest_team_run()
            run_id = str(latest.get("run_id", "") or "")
            branch_id = branch_id or str(latest.get("branch_id", "") or "")
        return {
            "project_id": self.spec.project_id,
            "problem_id": self.spec.problem_id,
            "run_id": run_id,
            "branch_id": branch_id or "no-route",
        }

    def _latest_team_run(self) -> dict[str, Any]:
        """该问题最近一次团队运行状态 (`{}` 表示没有 —— 不猜)。"""
        try:
            rows = self.store.list_latest("team_run") or []
        except Exception:  # noqa: BLE001 - 读不出来如实当作"没有团队运行"
            return {}
        mine = [row for row in rows
                if str(row.get("problem_id", "")) == self.spec.problem_id]
        if not mine:
            return {}
        return max(mine, key=lambda row: str(row.get("saved_at", "")))

    def budget_payload(self) -> dict[str, Any]:
        """预算/用量投影 (只读计数, 不重算)。

        上限取**与执行侧同一个对象** (`research/budget.py`): `runtime_counters` 里给出
        的真实上限优先, 其次是运行记录里的, 最后是默认值 —— 工作台因此总是显示
        "动作 3/40" 而不是 "3/0"。
        """
        from src.research.budget import ResearchBudget

        defaults = ResearchBudget()
        counters = self.runtime_counters
        record = self.runtime_record()
        budget = record.get("budget") or {}
        return {
            "actions_used": int(counters.get("actions", record.get("actions", 0)) or 0),
            "tool_calls_used": int(counters.get("tool_calls",
                                                record.get("tool_calls", 0)) or 0),
            "max_actions": int(counters.get("max_actions")
                               or budget.get("max_actions")
                               or defaults.max_actions),
            "max_tool_calls": int(counters.get("max_tool_calls")
                                  or budget.get("max_tool_calls")
                                  or defaults.max_tool_calls),
            "done": bool(counters.get("done", record.get("done", False))),
        }

    def decisions(self) -> list[dict]:
        record = self.runtime_record()
        return list(record.get("decisions", []) or [])

    def usage_summary(self) -> dict[str, Any]:
        record = self.runtime_record()
        counters = self.runtime_counters
        return {
            "actions": int(counters.get("actions", record.get("actions", 0)) or 0),
            "max_actions": int(counters.get("max_actions", 0) or 0),
            "tool_calls": int(counters.get("tool_calls",
                                           record.get("tool_calls", 0)) or 0),
            "max_tool_calls": int(counters.get("max_tool_calls", 0) or 0),
            "tokens": int(record.get("tokens", counters.get("tokens", 0)) or 0),
            "max_tokens": int(counters.get("max_tokens", 0) or 0),
            "cost_usd": round(float(record.get("cost_usd", 0.0) or 0.0), 6),
            "max_cost_usd": counters.get("max_cost_usd"),
            "wall_seconds": round(float(record.get("elapsed_seconds", 0.0) or 0.0), 1),
            "max_wall_seconds": counters.get("max_wall_seconds"),
            "llm_calls": len(record.get("usage_events", []) or []),
        }

    def stopped_reason(self) -> str:
        record = self.runtime_record()
        return str(record.get("stopped_reason", "") or "")

    # ------------------------------------------------------------------
    # 摘要与指标
    # ------------------------------------------------------------------
    def event_digest(self, claims: list[Claim], *, problem_id: str = "",
                     limit: int = 40) -> list[dict]:
        """最近的研究事件摘要 (按问题过滤, 与引擎同一实现)。"""
        from src.research.logging_schema import SURFACE_EVENT_KINDS, belongs_to_problem
        from src.research.reporting import summarize_event

        pid = problem_id or self.spec.problem_id
        mine = {c.id for c in claims}
        out: list[dict] = []
        for event in self.store.events():
            kind = event.get("type", "")
            if kind not in SURFACE_EVENT_KINDS:
                continue
            payload = event.get("payload") or {}
            if not belongs_to_problem(payload, pid, claim_ids=mine):
                continue
            out.append({"seq": event.get("seq"), "kind": kind,
                        "at": event.get("created_at", ""),
                        "detail": summarize_event(kind, payload)})
        return out[-limit:]

    def metrics(self, claims: list[Claim], *, problem_id: str = "") -> dict[str, Any]:
        """统一日志键与可聚合指标 (只读日志/账本)。"""
        from src.research.logging_schema import metrics_from_store

        pid = problem_id or self.spec.problem_id
        data = metrics_from_store(self.store, pid, claim_ids={c.id for c in claims})
        data["usage"] = self.usage_summary()
        data["stopped_reason"] = self.stopped_reason()
        data["identity"] = self.identity()
        return data

    # ------------------------------------------------------------------
    # 模型选择 / 缺口 (工作台展示用)
    # ------------------------------------------------------------------
    def model_selection(self, claims: list[Claim],
                        available: dict[str, bool] | None = None) -> dict[str, Any]:
        """当前选中的模型与"需不需要模型"的判定。"""
        from src.research.capability import candidate_scheme, declare_capability
        from src.research.reasoning_kernel import _claim_category
        from src.research.schemas import ClaimType
        models = self.models()
        out: dict[str, Any] = {"models": [], "claims": {}}
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
                _claim_category(claim), available=available,
                has_data=bool(claim.study.data_ref or claim.study.rows),
                has_design=claim.study.design.value not in ("", "none"))
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

    def model_comparison(self, claim: Claim | None) -> dict[str, Any]:
        """最近一次候选模型比较 (候选与舍弃理由) —— 纯计算, 不写状态。"""
        if claim is None:
            return {}
        evidence = [e for e in self.evidence_for_claim(claim) if e.excerpt]
        if not evidence:
            return {}
        from src.research.modeling import compare_from_evidence

        return compare_from_evidence(claim, evidence, self.spec.contract).to_dict()

    def gaps(self, claims: list[Claim],
             obligations: list[ProofObligation]) -> list[ResearchGap]:
        """把"当前缺什么"显式化为缺口对象, 控制器据此推进。

        出场顺序即优先级: **证据缺口 > 未关闭义务 > 其他**。
        证据缺口优先是因为它的补全 (检索→原文→支持关系判定) 会改变后续义务的
        可判定性; 反之先跑工具只会在缺条件的情况下得到一堆 unknown。
        """
        from src.research.action_registry import ActionType
        from src.research.obligations import (  # noqa: F401  (排序口径的唯一来源)
            OBLIGATION_PRIORITY,
            obligation_claim as _claim_of,
            obligation_kind as _obligation_kind,
        )
        from src.research.schemas import (
            ClaimStatus,
            GapType,
            ObjectRef,
            ObligationStatus,
            SupportKindOfEvidence,
            ValidationStatus,
        )
        from src.research.store import KIND_ATTEMPT

        gaps: list[ResearchGap] = []
        evidence_gaps: list[ResearchGap] = []
        obligation_gaps: list[ResearchGap] = []
        models = self.models()
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
            # 研究一开始就应先掌握已有定义/条件, 不应等到缺条件才检索。
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





def _obligation_priority(obligation: ProofObligation) -> int:
    """义务排序优先级 (唯一口径在 `research/obligations.py`)。"""
    from src.research.obligations import priority_of

    return priority_of(obligation)


def owned_claims(claims: Iterable[Any], problem_id: str,
                 known_problem_ids: set[str]) -> list[Any]:
    """按问题归属过滤对象 (工作台的多问题隔离, 与 `server._owned` 同一判据)。

    放在这里是为了让"读工作台"的过滤规则只有一处实现; 服务端只做取数与 HTTP。
    """
    scoped = {str(getattr(c, "id", "")) for c in claims}
    multi = len([p for p in known_problem_ids if p]) > 1

    def _owned(claim_id: str) -> bool:
        if not claim_id:
            return not multi
        if claim_id in scoped:
            return True
        if claim_id in known_problem_ids:
            return False
        return not multi

    return [c for c in claims if _owned(str(getattr(c, "id", "")))]


# 兼容再导出: 调用方按需取用, 避免各自再写一份过滤
__all__ += ["owned_claims"]
