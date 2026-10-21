"""Bounded, real role collaboration over sources and immutable hypothesis versions.

Two Generation workers use separate stateless contexts concurrently. Other roles
consume saved artifacts by dependency; UI events describe work, not hidden thought.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations

from pydantic import Field

from .budgets import BudgetExceeded, RunLedger
from .co_models import (
    AgentEvent,
    AgentState,
    Contract,
    CoScientistState,
    HypothesisCard,
    HypothesisReview,
    PairwiseReview,
    ProblemAnchor,
    ResearchContract,
)
from .context import accepted_evidence_index, source_context, task_context
from .evidence import decide_candidates
from .evidence_digest import build_evidence_digest, hypothesis_groups, persist_evidence_digest
from .flow_models import ClaimReviewDraft, PriorWork
from .identity import CandidateIdentitySync
from .identity_prepare import prepare_core_identities
from .intent_policy import is_disease_exploration, validate_exploration_start
from .llm import serialized_request
from .models import EvidenceRecord, ExecutionStatus, PlanStep
from .protocols import role_system
from .provider_receipts import public_provider_failure
from .research import PlanDraft, RESEARCH_SYSTEM, ReviewDraft as EvidenceReviewDraft
from .research_flow import ResearchFlow
from .store import digest, utc_now, write_json
from .tool_plans import ToolScheduler
from .tool_public import INVALID_TOOL_PROPOSAL_WARNING, public_budget_failure
from .tools import TOOL_NAMES, ToolRegistry, ToolRequest

ROLES = {
    "supervisor": ("Supervisor · 任务与文献", "orchestrator"),
    "generation": ("Generation · 双视角生成", "llm"),
    "proximity": ("Proximity · 机械去重", "rule_based"),
    "reflection": ("Reflection · 证据与反证", "llm"),
    "ranking": ("Ranking · 成对比较", "llm"),
    "evolution": ("Evolution · 一轮修订", "llm"),
    "meta_review": ("Meta-review · 研究合同", "llm"),
}


class AnchorDraft(Contract):
    disease_english: str
    searches: list[str]
    questions: list[str]
    success_criterion: str


class GenerationDraft(Contract):
    hypotheses: list[HypothesisCard]
    tool_requests: list[ToolProposal] = Field(default_factory=list)


class ToolProposal(Contract):
    # Keep free-form dictionaries OUT of strict inference schemas. The JSON
    # string is parsed and separately validated against the tool's typed schema.
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    arguments_json: str = Field(max_length=2000)
    gap: str = Field(default="", max_length=300)
    hypothesis_ids: list[str] = Field(default_factory=list)


class ReflectionDraft(Contract):
    reviews: list[HypothesisReview]
    claim_reviews: list[ClaimReviewDraft] = Field(default_factory=list)
    new_evidence: list[EvidenceRecord] = Field(default_factory=list)
    prior_work: list[PriorWork] = Field(default_factory=list)


class EvolutionDraft(Contract):
    revisions: list[HypothesisCard]


class MetaDraft(Contract):
    contracts: list[ResearchContract]
    common_gaps: list[str]


def normalized(text):
    return re.sub(r"\s+", " ", text.casefold()).strip()


def latest_cards(state):
    latest = {}
    for h in state.hypotheses:
        if h.hypothesis_id not in latest or h.version > latest[h.hypothesis_id].version:
            latest[h.hypothesis_id] = h
    return list(latest.values())


class BoundedSources:
    """No new provider logic: count all searches, including tools/novelty, together."""

    def __init__(self, source, engine):
        self.source, self.engine = source, engine
        self.search_count = 0

    def __getattr__(self, name):
        return getattr(self.source, name)

    def search_literature(self, query, limit=8, **search_options):
        flow = getattr(self.engine, "flow", None)
        receipt = flow.before_search(query) if flow else None
        http = getattr(self.source, "http", None)
        before_http, before_cache = getattr(http, "request_count", 0), getattr(http, "cache_hits", 0)
        try:
            self.engine.ledger.check()
            if self.search_count >= self.engine.run.brief.budget.max_searches:
                raise BudgetExceeded("Literature search budget exhausted")
            self.search_count += 1  # failed queries also consume the search allowance
            value = self.source.search_literature(query, limit=limit, **search_options)
            if flow:
                flow.search_finished(receipt, value, limit)
            return value
        except BudgetExceeded:
            if receipt:
                receipt.status, receipt.stop_reason = "budget_exhausted", "shared_budget"
            raise
        except Exception:
            if flow:
                flow.search_finished(receipt, [], limit, error=True)
            raise
        finally:
            if receipt:
                receipt.request_count = max(0, getattr(http, "request_count", 0) - before_http)
                receipt.cache_hits = max(0, getattr(http, "cache_hits", 0) - before_cache)
                self.engine.persist()

    def full_text(self, source):
        flow = getattr(self.engine, "flow", None)
        return flow.full_text(source) if flow else self.source.full_text(source)

    def _identity(self, request):
        plan = self.engine.scheduler.create(request, origin="supervisor_identity",
                                            reason="固定协调器核对已保存实体身份；不由模型选择候选")
        result = self.engine.scheduler.run(plan)
        if result is None:  # reused plans retain references; lookup the actual saved result
            key = digest(request.model_dump())
            result = self.engine.scheduler.memo.get(key)
        if result is None or result.status != "ok" or result.identity.get("status") != "resolved":
            raise RuntimeError("Identity unresolved; inspect the saved tool plan/candidates")
        return result

    def resolve_target(self, gene_symbol, species="Homo sapiens"):
        result = self._identity(ToolRequest(name="uniprot", arguments={
            "gene_symbol": gene_symbol, "species": species}))
        return result.identity["selected_id"], result.sources[0]

    def resolve_molecule(self, name):
        result = self._identity(ToolRequest(name="pubchem", arguments={"name": name}))
        return {k: v for k, v in result.records[0].items() if k in
                {"candidate_id", "smiles", "inchikey", "pubchem_cid"}}, result.sources[0]


class EvidenceLLM:
    """Reuse exact-span + fresh-role semantic review from the established path."""

    def __init__(self, engine, *, optional=False):
        self.engine, self.optional = engine, optional

    @property
    def calls(self):
        return self.engine.actual_calls

    @property
    def cache_hits(self):
        return self.engine.llm_cache_hits

    def note_synthesis_expansion(self, receipt):
        self.engine.candidate_synthesis_growth = receipt["growth_bytes"]
        self.engine.synthesis_expansion_receipt = dict(receipt)

    def complete(self, system, user, schema):
        role = "reflection" if schema.get("title") == "ReviewDraft" else "supervisor"
        stage = "semantic_review" if role == "reflection" else "synthesis"
        candidate_names = ([c["name"] for c in json.loads(user).get("candidates", [])]
                           if stage == "semantic_review" else None)
        reserve = (self.engine.critical_reserve(growth=True) if self.optional else
                   self.engine.candidate_core_reserve(stage, candidate_names=candidate_names) if self.engine.candidate_assessment_mode() else
                   self.engine.minimum_chain_reserve(stage))
        return self.engine.invoke(role, "证据整理与独立语义复核", system, user, schema,
                                  reserve=reserve,
                                  optional=self.optional, output_cap=self.engine.output_allowance())


class CoScientistEngine:
    def __init__(self, legacy_engine):
        self.legacy = legacy_engine
        self.store, self.settings = legacy_engine.store, legacy_engine.settings
        self.lock = threading.RLock()
        self.active = {}
        self.observation_before = {}
        self.actual_calls = self.llm_cache_hits = self.serial = 0
        self.call_durations = []
        self.prior_timeout = False
        self.budget_serial = 0

    def persist(self):
        with self.lock:
            if hasattr(self, "identity_sync"):
                self.identity_sync.refresh_references()
            self.state.budget = {
                **self.ledger.snapshot(),
                "actual_llm_calls": self.actual_calls,
                "llm_cache_hits": self.llm_cache_hits,
                "literature_queries": self.sources.search_count,
                "budget_failures": self.state.budget_failures,
                "optional_skips": self.state.optional_skips,
            }
            self.store.save_run(self.run)

    def event(self, role, kind, status, title, summary="", ids=(), *,
              input_summary="", input_ids=(), operation_id="", operation_status="", provider_failure=None,
              budget_failure=None):
        with self.lock:
            stamp = utc_now()
            actor = next(a for a in self.state.agents if a.agent_id == role)
            actor.status, actor.task, actor.updated_at = status, title, stamp
            sequence = len(self.state.events) + 1
            self.state.events.append(
                AgentEvent(
                    event_id=f"event_{sequence:04d}",
                    sequence=sequence,
                    agent_id=role,
                    kind=kind,
                    status=status,
                    title=title,
                    summary=summary,
                    created_at=stamp,
                    artifact_ids=list(ids),
                    input_summary=input_summary,
                    input_artifact_ids=list(input_ids),
                    operation_id=operation_id,
                    operation_status=operation_status,
                    provider_failure=public_provider_failure(provider_failure),
                    budget_failure=public_budget_failure(budget_failure),
                )
            )
            self.persist()

    def phase(self, key, title, role):
        item = PlanStep(step_id="co_" + key, question=title, capability=role, status="running")
        self.run.steps.append(item)
        # These references describe available inputs at the phase boundary, not
        # model prompts. They do not affect execution or scientific decisions.
        inputs = self.observation_artifacts(key, inputs=True)
        self.observation_before[item.step_id] = set(self.observation_artifacts(key))
        self.event(role, "phase", "running", title,
                   input_summary=f"疾病：{self.run.brief.disease}；已保存输入引用 {len(inputs)} 项",
                   input_ids=inputs, operation_id=item.step_id, operation_status="running")
        return item

    def observation_artifacts(self, key, inputs=False):
        """Stable public artifact references for the UI; old events remain valid."""
        hypotheses = [f"{h.hypothesis_id}:v{h.version}" for h in latest_cards(self.state)]
        sources = [s.source_id for s in self.run.sources]
        evidence = [e.evidence_id for e in self.run.evidence]
        reviews = [f"review:{r.hypothesis_id}:v{r.version}" for r in self.state.reviews]
        comparisons = [f"comparison:{r.comparison_id}" for r in self.state.comparisons]
        evaluations = getattr(self.run, "candidate_evaluations", [])
        assessment_refs = [f"assessment:{a.assessment_id}:v{a.version}" for a in evaluations]
        assessment_reviews = ["assessment_review:" + r.review_id for a in evaluations for r in a.reviews]
        if inputs:
            return {
                "anchor": [], "literature": ["anchor"], "evidence": sources,
                "generation": ["anchor", *evidence, *(["research_flow"] if self.state.research_flow else [])], "proximity": hypotheses,
                "reflection": [*hypotheses, *evidence],
                "ranking": [*hypotheses, *reviews],
                "evolution": [*hypotheses, *reviews, *comparisons],
                "meta": [*hypotheses, *reviews, *comparisons],
                "candidate_assessment": [*assessment_refs, *evidence, *sources],
            }.get(key, [])
        return {
            "anchor": ["anchor"] if self.state.anchor else [], "literature": [*sources, *(["research_flow"] if self.state.research_flow else [])],
            "evidence": evidence, "generation": hypotheses,
            "proximity": [f"cluster:{i}" for i, _ in enumerate(self.state.clusters)],
            "reflection": reviews, "ranking": comparisons,
            "evolution": [f"{h.hypothesis_id}:v{h.version}" for h in self.state.hypotheses if h.parent_version is not None],
            "meta": [f"contract:{c.hypothesis_id}:v{c.version}" for c in self.state.contracts],
            "candidate_assessment": [*assessment_refs, *assessment_reviews],
        }.get(key, [])

    def finish(self, item, role, status="completed", summary=""):
        item.status, item.reason = status, summary
        outputs = [] if status == "skipped" else self.observation_artifacts(item.step_id.removeprefix("co_"))
        if item.step_id == "co_evolution":
            # A continuation can inherit v2 cards. Only this phase's new versions
            # are its output; inherited revisions remain inputs from the seed.
            outputs = [ref for ref in outputs if ref not in self.observation_before.get(item.step_id, set())]
        self.event(role, "phase", status, item.question, summary, outputs,
                   operation_id=item.step_id, operation_status=status)

    def record_budget_failure(self, exc, stage, *, optional=False):
        self.budget_serial += 1
        details = {**exc.details, "stage": stage, "reason": str(exc),
                   "prior_timeout": self.prior_timeout,
                   "receipt_id": f"budget_action_{self.budget_serial:04d}"}
        self.state.budget_failures.append(details)
        if optional:
            self.state.optional_skips.append(dict(details))
        self.event(role="supervisor", kind="budget", status="skipped" if optional else "partial",
                   title="资源预检跳过可选阶段" if optional else "请求在发送前被预算阻止",
                   summary=f"{stage}；{exc}；UTF-8字节预留，非token计量或费用。",
                   budget_failure=details)
        return details

    def invoke(self, role, title, system, user, schema, *, reserve=None, optional=False,
               output_cap=None):
        complete_system = role_system(role) + "\n" + system
        reservation_id = None
        llm = None
        started = None
        active_registered = settled = provider_entered = False
        phase = "admission"

        def settle():
            # A returned worker can still fail during event persistence. Account
            # for the worker and its active slot exactly once in either path.
            nonlocal settled, active_registered
            with self.lock:
                if settled:
                    return
                self.actual_calls += getattr(llm, "calls", 0)
                self.llm_cache_hits += getattr(llm, "cache_hits", 0)
                if active_registered:
                    self.active[role] -= 1
                    active_registered = False
                if provider_entered and started is not None:
                    self.call_durations.append(time.monotonic() - started)
                settled = True

        try:
            with self.lock:
                amount = len(serialized_request(self.settings.llm, complete_system, user, schema).encode())
                if optional and schema.get("title") == "SynthesisDraft":
                    # Also protect extraction's mandatory fresh semantic review.
                    reserve = dict(reserve or {})
                    reserve["calls"] = reserve.get("calls", 0) + 1
                    reserve["bytes"] = reserve.get("bytes", 0) + amount + 2 * self.output_allowance()
                    reserve["output_bytes"] = reserve.get("output_bytes", 0) + self.output_allowance()
                    reserve["seconds"] = reserve.get("seconds", 0) + self.latency_allowance()
                proposed_id = f"llm_{self.serial + 1:03d}"
                try:
                    self.ledger.reserve_request(amount, required_output_bytes=output_cap or self.output_allowance(),
                        required_seconds=self.latency_allowance() if optional or reserve else 0,
                        reserve=reserve, stage=title, prior_timeout=self.prior_timeout,
                        output_reservation_id=proposed_id)
                except BudgetExceeded as exc:
                    try:
                        self.record_budget_failure(exc, title, optional=optional)
                    except Exception:  # A secondary save failure must not replace the budget error.
                        pass
                    raise
                # From this point every exit, including event/save failure before
                # provider construction, is covered by the outer finally.
                reservation_id = proposed_id
                self.serial += 1
                serial = self.serial
                phase = "running_event"
                actor = next(a for a in self.state.agents if a.agent_id == role)
                actor.invocations += 1
                self.active[role] = self.active.get(role, 0) + 1
                active_registered = True
                self.event(role, "llm", "running", title,
                    "独立上下文调用；只返回结构化研究产物，不公开私密思维链。",
                    operation_id=reservation_id, operation_status="running")
            phase = "worker_setup"
            llm = self.legacy.llm_factory(self.settings.llm,
                self.directory / "inference" / role / f"call_{serial:03d}",
                max_calls=self.run.brief.budget.max_llm_calls)
            llm.deadline = self.ledger.deadline - (reserve or {}).get("seconds", 0)
            phase = "provider"
            started = time.monotonic()
            provider_entered = True
            result = llm.complete(complete_system, user, schema)
            phase = "output_admission"
            self.ledger.output(json.dumps(result, ensure_ascii=False),
                protected_output_bytes=(reserve or {}).get("output_bytes", 0),
                call_allowance=output_cap or (self.output_allowance() if optional else None),
                reservation_id=reservation_id)
            with self.lock:
                settle()
                phase = "completed_event"
                self.event(role, "llm", "running" if self.active[role] else "completed", title,
                    "结构化结果已返回，下一阶段仍需核对适用性。",
                    operation_id=reservation_id, operation_status="completed")
            return result
        except Exception as exc:
            if reservation_id is None:
                raise
            try:
                self.ledger.release_output(reservation_id)
            except Exception:
                pass  # Retry in finally; cleanup must not skip settlement or mask the cause.
            with self.lock:
                settle()
                # Local event/save/setup failure is not evidence of a provider
                # failure. Keep attempted reservations distinct from actual calls.
                receipt = public_provider_failure(getattr(exc, "failure_metadata", None))
                attempted = provider_entered and (getattr(llm, "calls", 0) > 0 or bool(receipt))
                local_failure = phase in {"running_event", "worker_setup", "completed_event"} or (phase == "provider" and not attempted)
                provider_failure = {} if local_failure else receipt
                timed_out = not local_failure and (isinstance(exc, TimeoutError) or provider_failure.get("category") == "timeout")
                self.prior_timeout = self.prior_timeout or timed_out
                failure = {"stage": f"{phase}: {title}" if local_failure else title,
                    "reason": "coordinator_failure" if local_failure else "provider_timeout" if timed_out else "provider_failure",
                    "blocked_before_provider": not attempted, "prior_timeout": self.prior_timeout,
                    "remaining_seconds": round(max(0, self.ledger.deadline - time.monotonic()), 3),
                    "operation_id": reservation_id}
                if provider_failure:
                    failure["provider_failure"] = provider_failure
                if isinstance(exc, BudgetExceeded):
                    failure.update(exc.details)
                    failure.update(reason=str(exc), blocked_before_provider=not attempted)
                self.budget_serial += 1
                failure["receipt_id"] = f"budget_action_{self.budget_serial:04d}"
                self.state.budget_failures.append(failure)
                try:
                    self.event(role, "orchestration" if local_failure else "llm",
                        "running" if self.active.get(role, 0) else "failed", title,
                        "本地调用准备或记录保存失败；实际模型调用另行计数。" if local_failure else
                        "调用失败或超出预算；没有补造完成结果。",
                        operation_id=reservation_id, operation_status="failed", provider_failure=provider_failure,
                        budget_failure=failure if local_failure or isinstance(exc, BudgetExceeded) else None)
                except Exception:  # Report best-effort; preserve the original exception.
                    pass
            raise
        finally:
            if reservation_id is not None:
                if sys.exc_info()[0] is None:
                    self.ledger.release_output(reservation_id)
                else:
                    try:
                        self.ledger.release_output(reservation_id)
                    except Exception:
                        pass  # Preserve an already-propagating primary error.

    def call(self, role, title, model, payload, **options):
        value = self.invoke(
            role,
            title,
            "Use the supplied output contract.",
            self.payload_json(role, payload),
            model.model_json_schema(),
            **options,
        )
        return model.model_validate(value)

    def optional_call(self, role, title, model, payload, *, reserve):
        try:
            return self.call(role, title, model, payload, reserve=reserve, optional=True)
        except Exception as exc:  # noqa: BLE001 -- preserve reviewed versions and attempt necessary closure
            self.state.optional_skips.append({"stage": title,
                "reason": "budget_preflight" if isinstance(exc, BudgetExceeded) else "optional_provider_failure",
                "prior_timeout": self.prior_timeout})
            self.event(role, "optional_skip", "skipped", title,
                       "可选调用没有可用结果；保留已审版本，继续尝试必要收尾。")
            return None

    def context(self, hypotheses=(), *, generation=False, closing=False):
        # Only the current requested versions and their support/opposition enter
        # model context. Query plans, snapshots and hashes remain local.
        keys = {(h.hypothesis_id, h.version) for h in hypotheses}
        refs = {i for h in hypotheses for i in h.evidence_ids + h.opposing_evidence_ids}
        associations = []
        if getattr(self, "flow", None):
            for h in hypotheses:
                scope = self.flow.reviewed_evidence_ids(h)
                refs.update(scope["evidence_ids"] + scope["opposing_evidence_ids"])
                associations.extend(scope["associations"])
        globally_accepted = [e for e in self.run.evidence if e.validated_span]
        evidence = [e for e in globally_accepted if closing or not keys or e.evidence_id in refs]
        source_ids = {e.source_id for e in evidence}
        # Admitted landscape papers can contain uncited contrary observations.
        # Retain their bounded original windows, marked unreviewed when uncited.
        source_ids.update(s.source_id for s in self.run.sources
                          if s.evidence_scope in {"abstract", "full_text", "local_document"})
        flow = self.state.research_flow
        claims, query_sources = [], []
        if flow:
            claims = [c for c in flow.claims if not keys or (c.hypothesis_id, c.version) in keys]
            claim_ids = {c.claim_id for c in claims}
            query_sources = [q for q in flow.queries if q.source_ids and
                (not keys or (q.hypothesis_id, q.version) in keys or claim_ids.intersection(q.claim_ids))]
            source_ids.update(s for q in query_sources for s in q.source_ids)
        novelty = [q for q in self.state.novelty_searches
                   if not keys or (q["hypothesis_id"], q.get("version")) in keys]
        source_ids.update(s for q in novelty for s in q["source_ids"])
        anchor = self.state.anchor.model_dump()
        task = task_context(self.run.brief)
        for key, anchor_key in (("conditions", "condition"), *((k, k) for k in
            ("disease", "phenotype", "species", "chassis_name", "population", "delivery_context",
             "research_goal", "success_criterion"))):
            if task.get(key) and task[key] == anchor.get(anchor_key):
                task.pop(key)
        cards_by_key = {(h.hypothesis_id, h.version): h for h in hypotheses}
        current_claims = []
        for claim in claims:
            row = claim.model_dump(exclude={"query_ids"})
            card = cards_by_key.get((claim.hypothesis_id, claim.version))
            if card:
                for key in ("condition", "endpoint"):
                    if row[key] == getattr(card, key):
                        row.pop(key)
            current_claims.append(row)
        result = {
            "anchor": anchor,
            "task": task,
            "accepted_evidence": [e.model_dump() for e in evidence],
            "global_accepted_evidence_index": accepted_evidence_index(globally_accepted,
                linked_evidence_ids=refs, claim_applicability=associations),
            "evidence_state_semantics": "Global span acceptance, exact-version linkage and claim applicability are separate. Not linked does not mean unaccepted. A globally accepted animal finding is not human intervention support.",
            "reviewed_evidence_associations": associations,
            "sources": [{**source_context(s, globally_accepted, 4500, include_prefix=True), "review_status":
                         "has_accepted_citations" if any(e.source_id == s.source_id for e in globally_accepted) else "unreviewed_source",
                         "global_accepted_evidence_ids": [e.evidence_id for e in globally_accepted if e.source_id == s.source_id],
                         "current_version_linked_evidence_ids": [e.evidence_id for e in globally_accepted if e.source_id == s.source_id and e.evidence_id in refs]}
                        for s in self.run.sources
                        if not keys or s.source_id in source_ids],
            "targets": [t.model_dump(include={"gene_symbol", "species", "uniprot_id",
                "desired_direction", "evidence_ids", "uncertainties"}) for t in self.run.targets],
            "candidates": [c.model_dump(include={"candidate_id", "name", "role", "role_status",
                "identity_status", "smiles", "inchikey", "pubchem_cid", "evidence_ids",
                "uncertainties", "aliases", "user_name_declarations", "identity_revision",
                "entity_id", "identity_source_ids", "identity_form", "original_ids"}) for c in self.run.candidates],
            "entity_references": getattr(self.state, "entity_references", []),
            "user_supplied_candidates": self.run.brief.existing_candidates,
            "hypotheses": [h.model_dump() for h in hypotheses],
            "literature_landscape": {"claims": current_claims,
                "applicability": "Claims inherit condition and endpoint from their exact hypothesis_id/version unless provided here.",
                "source_assignments": [{"query_id": q.query_id, "claim_ids": q.claim_ids,
                    "source_ids": q.source_ids, "purpose": q.purpose, "status": q.status}
                    for q in query_sources],
                "limitations": list(dict.fromkeys(flow.limitations))} if flow else None,
            "novelty_searches": [{k: q.get(k) for k in ("hypothesis_id", "version", "source_ids", "status")}
                                 for q in novelty],
        }
        if generation:
            result["evidence_digest"] = build_evidence_digest(self.run)
            # Scope/polarity live in the digest; the exact claim and continuous
            # quotation remain here once. Formal review still gets the original
            # bounded source windows, including unquoted contrary observations.
            result["accepted_evidence"] = [e.model_dump(include={"evidence_id", "source_id", "claim", "quote"})
                                           for e in evidence]
            cited_sources = {e.source_id for e in evidence}
            for source in result["sources"]:
                if source["source_id"] in cited_sources:
                    source["windows"] = []
                    source["context_coverage"] = "accepted quotations supplied separately; uncited text omitted during generation and retained for formal review"
                    source["text_truncated"] = True
        return result

    def output_allowance(self):
        # Scheduling allowance only; actual returned bytes are enforced before
        # interpretation. Codex has no provider-side output cap.
        return min(12000, self.run.brief.budget.max_output_tokens)

    def latency_allowance(self):
        return max([min(60, self.settings.llm.timeout_seconds), *self.call_durations])

    def request_bytes(self, role, model, payload):
        return len(serialized_request(self.settings.llm, role_system(role) + "\nUse the supplied output contract.",
            self.payload_json(role, payload), model.model_json_schema()).encode())

    @staticmethod
    def payload_json(role, payload):
        # Meta is a reference-based closing projection; whitespace carries no
        # evidence. Accounting and transmission must use the identical string.
        return json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":") if role == "meta_review" else None)

    def critical_reserve(self, *, growth=False, reflection=True):
        if getattr(self, "candidate_core_active", False):
            return self.candidate_core_reserve("identity")
        cards = latest_cards(self.state)
        requests = [("meta_review", MetaDraft, self.meta_payload(cards))]
        if reflection:
            requests.insert(0, ("reflection", ReflectionDraft, self.review_payload(cards)))
        allowance = self.output_allowance()
        return {"calls": len(requests),
                "bytes": sum(self.request_bytes(*r) for r in requests) + allowance * (2 if growth else 1),
                "output_bytes": allowance * len(requests),
                "seconds": self.latency_allowance() * len(requests) + 5}

    def candidate_assessment_mode(self):
        return self.run.brief.task_intent == "candidate_assessment" and not self.run.brief.continue_saved_hypotheses

    def candidate_core_reserve(self, stage, *, candidate_names=None):
        """Reserve only the actual remaining candidate-assessment obligations.

        Extraction and its independent semantic review precede identity and an
        independent conclusion review. Hypothesis/Meta work is not mandatory for
        this task. The same latency estimator and hard limits remain in force.
        """
        from .assessment_models import CandidateAssessmentReviewDraft
        from .assessment_review import assessment_review_payload
        from .research import evidence_review_input
        allowance = self.output_allowance()
        amount = self.request_bytes("reflection", CandidateAssessmentReviewDraft,
                                    assessment_review_payload(self.run))
        # The extraction's bounded assessment/evidence output is represented in
        # the later review payload alongside identity/scope bindings.
        amount += (3 if stage in {"synthesis", "semantic_review"} else 1) * allowance
        # Compact references can expand locally. Reserve their enforced maximum
        # growth before extraction, then the measured expansion at review.
        if stage == "synthesis":
            from .compact_synthesis import MAX_EXPANSION_FACTOR
            amount += (MAX_EXPANSION_FACTOR - 1) * allowance
        elif stage == "semantic_review":
            amount += getattr(self, "candidate_synthesis_growth", 0)
        calls = 1
        from .identity_prepare import core_identity_ready
        identity_done = core_identity_ready(self.run) and (
            not candidate_names or core_identity_ready(self.run, candidate_names))
        identity_seconds = 60 if stage in {"synthesis", "semantic_review"} and not identity_done else 0
        if stage == "synthesis":
            payload = evidence_review_input(self.run, [], [], [], {}, excerpt_chars=4500)
            amount += len(serialized_request(self.settings.llm,
                role_system("reflection") + "\n" + RESEARCH_SYSTEM,
                json.dumps(payload, ensure_ascii=False), EvidenceReviewDraft.model_json_schema()).encode()) + 2 * allowance
            calls += 1
        return {"calls": calls, "bytes": amount, "output_bytes": calls * allowance,
                "seconds": calls * self.latency_allowance() + 5 + identity_seconds}

    def review_candidate_evaluations(self):
        from .assessment_models import CandidateAssessmentReviewDraft
        from .assessment_review import (apply_assessment_review, assessment_review_payload,
            bind_assessment_identities, public_validated_evaluations)
        bind_assessment_identities(self.run)
        self.persist()
        ready = [a for a in self.run.candidate_evaluations if a.status == "ready_for_review"]
        if not ready:
            raise RuntimeError("No identity-bound candidate assessment draft with reviewed evidence")
        step = self.phase("candidate_assessment", "独立审查候选结论及适用范围", "reflection")
        draft = self.call("reflection", "独立候选结论审查", CandidateAssessmentReviewDraft,
            assessment_review_payload(self.run), reserve={"seconds": 5}, output_cap=self.output_allowance())
        apply_assessment_review(self.run, draft, operation_id=f"llm_{self.serial:03d}")
        valid = public_validated_evaluations(self.run)
        valid_ids = {a["candidate_id"] for a in valid}
        from .candidate_assessments import matching_candidates
        expected = matching_candidates(self.run.candidates, self.run.brief.existing_candidates)
        self.assessment_completed = bool(expected) and all(
            len(matching_candidates(self.run.candidates, [name])) == 1 for name in self.run.brief.existing_candidates
        ) and {c.candidate_id for c in expected}.issubset(valid_ids)
        self.finish(step, "reflection", "completed" if self.assessment_completed else "partial",
                    "候选结论独立审查已保存；科学支持程度与流程完成分别记录。")
        self.persist()  # Preserve the core result before attempting optional work.
        if not self.assessment_completed:
            raise RuntimeError("Candidate conclusion review missing, rejected or invalid; saved drafts remain unreviewed")
        self.candidate_core_active = False

    def admit_candidate_hypothesis_expansion(self):
        payload = self.generation_payloads()[0]
        try:
            self.ledger.preflight(self.request_bytes("generation", GenerationDraft, payload),
                required_calls=1, required_output_bytes=self.output_allowance(),
                required_seconds=self.latency_allowance(),
                reserve=self.minimum_chain_reserve("generation", generation_outputs=1),
                stage="candidate_hypothesis_expansion", prior_timeout=self.prior_timeout)
            return True
        except BudgetExceeded as exc:
            self.record_budget_failure(exc, "candidate_hypothesis_expansion", optional=True)
            self.state.limitations.append("独立候选评估已保存；假说生成、假说审查与Meta扩展未执行。")
            for stage in ("generation", "reflection", "meta"):
                self.run.steps.append(PlanStep(step_id="co_" + stage, question="候选评估后的可选假说扩展",
                    capability=stage, status="skipped", reason="candidate_core_completed_optional_chain_not_admitted"))
            return False

    def minimum_chain_reserve(self, stage, *, generation_outputs=1):
        """Serialize the known remaining chain, then reserve bounded output growth.

        Future model content and latency are not known. The plan is recomputed
        at every necessary boundary; large actual outputs/late calls fail closed.
        It does not manufacture hypotheses, reviews or a Meta contract.
        """
        cards = latest_cards(self.state)
        allowance = self.output_allowance()
        meta = self.request_bytes("meta_review", MetaDraft, self.meta_payload(cards))
        if stage == "reflection":
            calls, amount = 1, meta + 2 * allowance
        else:
            review = self.request_bytes("reflection", ReflectionDraft, self.review_payload(cards))
            # Generated cards may occur both in the version view and claim
            # definitions; the subsequent review adds scoped gaps/associations.
            calls, amount = 2, review + meta + (3 * generation_outputs + 2) * allowance
            if stage in {"semantic_review", "synthesis"}:
                first = self.generation_payloads()[0]
                amount += self.request_bytes("generation", GenerationDraft, first) + 2 * allowance
                calls += 1
            if stage == "synthesis":
                from .research import evidence_review_input
                payload = evidence_review_input(self.run, [], [], [], {}, excerpt_chars=4500)
                amount += len(serialized_request(self.settings.llm,
                    role_system("reflection") + "\n" + RESEARCH_SYSTEM,
                    json.dumps(payload, ensure_ascii=False), EvidenceReviewDraft.model_json_schema()).encode()) + 2 * allowance
                calls += 1
        return {"calls": calls, "bytes": amount, "output_bytes": allowance * calls,
                "seconds": self.latency_allowance() * calls + 5}

    def record_optional_skip(self, stage, reason):
        self.budget_serial += 1
        row = {"receipt_id": f"budget_action_{self.budget_serial:04d}", "stage": stage,
               "reason": reason, "blocked_before_provider": True, "accounting_unit": "utf8_bytes"}
        self.state.optional_skips.append(row)
        self.event("supervisor", "optional_skip", "skipped", stage,
                   "优先完成必要的同版本审查与模型收尾；此可选动作未执行。", budget_failure=row)

    def optional_preflight(self, stage, *, required_bytes=0, calls=0, reflection=True,
                           growth=False, seconds=10):
        try:
            return self.ledger.preflight(required_bytes, required_calls=calls,
                required_output_bytes=self.output_allowance() * calls,
                required_seconds=seconds, reserve=self.critical_reserve(growth=growth, reflection=reflection),
                stage=stage, prior_timeout=self.prior_timeout)
        except BudgetExceeded as exc:
            self.record_budget_failure(exc, stage, optional=True)
            return None

    def append_sources(self, sources):
        known = {s.source_id for s in self.run.sources}
        papers = sum(s.evidence_scope in ("abstract", "full_text") for s in self.run.sources)
        added = []
        for source in sources:
            if source.source_id in known:
                continue
            if source.evidence_scope in ("abstract", "full_text"):
                if papers >= self.run.brief.budget.max_papers:
                    continue
                papers += 1
            self.run.sources.append(source)
            known.add(source.source_id)
            added.append(source.source_id)
        if getattr(self, "flow", None):
            self.flow.ingest(sources)
        return added

    def tool(self, request, role="reflection", *, plan_id="", gap_id="", parent_call_id="", identity=None):
        if not plan_id:
            # Existing deterministic follow-up searches use the same tracing and
            # deduplication path; the research query/algorithm itself is unchanged.
            plan = self.scheduler.create(request, origin="deterministic_validation",
                                         reason="既有固定补证查询；经相同参数契约与共享预算执行")
            before = {s.source_id for s in self.run.sources}
            result = self.scheduler.run(plan) or self.scheduler.memo.get(digest(request.model_dump()))
            if result is None:
                raise RuntimeError("Tool plan remains unresolved; inspect saved identity candidates")
            if result.status == "budget_exhausted":
                raise BudgetExceeded("HTTP/tool budget exhausted")
            return result, [s.source_id for s in self.run.sources if s.source_id not in before]
        actor = next(a for a in self.state.agents if a.agent_id == role)
        operation_id = f"tool_{len(self.state.tool_calls):03d}"
        public_name = request.name if request.name in TOOL_NAMES else "未注册工具"
        self.event(
            role,
            "tool",
            "running",
            "查询 " + public_name,
            "工具观察记录查询范围、数据来源和返回内容。",
            input_summary=f"计划 {plan_id or '未记录'}；缺口 {gap_id or '未记录'}；参数经确定性契约核对",
            input_ids=[plan_id] if plan_id else [],
            operation_id=operation_id, operation_status="running",
        )
        from .tools import ToolResult
        try:
            self.ledger.tool()
            actor.tool_calls += 1
            result = self.registry.execute(request)
        except BudgetExceeded:
            result = ToolResult(tool_name=request.name, provider="none", parameters={},
                                status="budget_exhausted", retrieved_at=utc_now(),
                                warnings=["Shared tool or wall-time budget exhausted"], stop_reason="shared_budget")
        result.call_id, result.parent_call_id = operation_id, parent_call_id
        result.plan_id, result.gap_id = plan_id, gap_id
        result.requested_parameters = request.arguments
        if identity and not result.identity:
            result.identity = identity
        self.state.tool_calls.append(result.model_dump(mode="json"))
        added = self.append_sources(result.sources)
        self.event(
            role,
            "tool",
            "completed" if result.status in ("ok", "empty") else "partial",
            "查询 " + public_name,
            result.status + "; " + "; ".join(result.warnings),
            added,
            operation_id=operation_id, operation_status=result.status,
        )
        return result, added

    def planned_tool(self, request, **metadata):
        plan = next((p for p in self.state.tool_plans if p["plan_id"] == metadata["plan_id"]), {})
        role = "supervisor" if plan.get("origin") in {"supervisor_identity", "core_identity"} else "reflection"
        flow = getattr(self, "flow", None)
        cards = [h for h in latest_cards(self.state) if h.hypothesis_id in plan.get("hypothesis_ids", [])]
        receipt = None
        if flow and request.name == "europepmc_search" and flow.active_query is None and cards:
            h = cards[0]
            cid = f"claim:{h.hypothesis_id}:v{h.version}:main"
            receipt = flow.new_query("supplement", "model_gap_context", request.arguments.get("query", ""),
                hypothesis_id=h.hypothesis_id, version=h.version, claim_ids=[cid])
            next(c for c in flow.claims_for(h) if c.claim_id == cid).query_ids.append(receipt.query_id)
            flow.active_query = receipt
        try:
            result = self.tool(request, role, **metadata)[0]
            if receipt:
                receipt.source_ids = [s.source_id for s in result.sources if any(s.source_id == a.source_id for a in self.run.sources)]
                receipt.call_ids = [result.call_id]
                for h in cards[1:]:
                    cid = f"claim:{h.hypothesis_id}:v{h.version}:main"
                    q = flow.new_query("reuse", "model_gap_shared_context", receipt.query,
                        hypothesis_id=h.hypothesis_id, version=h.version, claim_ids=[cid],
                        status="reused", source_ids=receipt.source_ids, call_ids=receipt.call_ids,
                        reused_from=receipt.query_id, stop_reason="same_query_not_independent_evidence")
                    next(c for c in flow.claims_for(h) if c.claim_id == cid).query_ids.append(q.query_id)
            return result
        finally:
            if receipt:
                flow.active_query = None

    def normalize_card(self, h):
        h = h.model_copy(deep=True)
        valid = {e.evidence_id: e for e in self.run.evidence if e.validated_span}
        old = set(h.evidence_ids + h.opposing_evidence_ids)
        h.evidence_ids = list(
            dict.fromkeys(
                i for i in h.evidence_ids if i in valid and valid[i].polarity != "opposes"
            )
        )
        h.opposing_evidence_ids = list(
            dict.fromkeys(
                i
                for i in h.opposing_evidence_ids
                if i in valid and valid[i].polarity in ("opposes", "mixed")
            )
        )
        if old - set(h.evidence_ids + h.opposing_evidence_ids):
            h.inference_gaps.append("部分引用不存在、未通过审查或证据方向不符，已从可用证据中移除")
        if not h.evidence_ids:
            h.status = "needs_evidence"
            h.inference_gaps.append("没有已审查的支持证据，不能形成已支持的干预结论")
        if normalized(h.chassis_name) != normalized(self.state.anchor.chassis_name):
            h.chassis_name = self.state.anchor.chassis_name
            h.chassis = h.chassis.model_copy(
                update={
                    "status": "unknown",
                    "strain_match": "unknown",
                    "evidence_ids": [],
                    "gaps": ["原草稿菌株与任务不符；需对实际菌株重新补证"],
                }
            )
            h.inference_gaps.append("菌株错配已纠正，不沿用其他菌株生产结论")
        h.chassis.evidence_ids = [i for i in h.chassis.evidence_ids if i in valid]
        # A conservative mechanical gate supplements, never replaces, domain review.
        production_ids = []
        source_lookup = {s.source_id: s for s in self.run.sources}
        for i in h.chassis.evidence_ids:
            e = valid[i]
            source = source_lookup.get(e.source_id)
            if source is None or (
                source.evidence_scope == "database" and source.provider != "local_experiment"
            ):
                continue  # gene/pathway/binding annotations are never production measurements
            text = normalized(e.quote + " " + e.claim)
            production = any(
                word in text
                for word in ("produced", "production", "biosynth", "synthesis", "合成", "产生")
            )
            molecule = bool(h.molecule_names) and all(
                normalized(name) in text for name in h.molecule_names if name
            )
            if production and molecule:
                production_ids.append(i)
        if h.chassis.status != "unknown" and not production_ids:
            h.chassis.status, h.chassis.strain_match = "unknown", "unknown"
            h.chassis.gaps.append("底盘生产评价使用菌株和条件匹配的生产、机制及模型证据")
        if h.chassis.status == "direct_evidence":
            exact = [
                i
                for i in production_ids
                if normalized(h.chassis_name) and normalized(h.chassis_name)
                in normalized(valid[i].quote + " " + valid[i].study_system)
                and normalized(valid[i].condition) == normalized(h.condition)
                and normalized(h.condition) not in ("unknown", "未知", "未指定培养与供给条件")
            ]
            if not exact or h.chassis.strain_match != "exact":
                h.chassis.status = "indirect" if production_ids else "unknown"
                h.chassis.strain_match = "other_strain" if production_ids else "unknown"
                h.chassis.gaps.append("缺少 exact 菌株与条件匹配的生产依据，禁止直接外推")
        if not self.state.anchor.chassis_name:
            h.chassis.status, h.chassis.strain_match = "unknown", "unknown"
            h.chassis.evidence_ids = []
            h.chassis.gaps = ["本任务未指定生产菌株，底盘生产适用性尚未评估"]
        supplied = {normalized(n) for n in self.run.brief.existing_candidates}
        from .candidate_assessments import matching_candidates
        specified = matching_candidates(self.run.candidates, self.run.brief.existing_candidates)
        card_entities = matching_candidates(self.run.candidates, h.molecule_names)
        if any(normalized(n) in supplied for n in h.molecule_names) or any(
            candidate is selected for candidate in card_entities for selected in specified
        ):
            h.origin = "user_supplied"
        source_ids = {s.source_id for s in self.run.sources}
        h.novelty.closest_source_ids = [s for s in h.novelty.closest_source_ids if s in source_ids]
        searches = [s for s in self.state.novelty_searches if (s["hypothesis_id"], s.get("version")) == (h.hypothesis_id, h.version)]
        h.novelty.search_scope = "; ".join(s["query"] + " [" + s["status"] + "]" for s in searches)
        if not searches or not h.novelty.closest_source_ids:
            h.novelty.status = "insufficient_search"
        h.inference_gaps = list(dict.fromkeys(h.inference_gaps))
        h.chassis.gaps = list(dict.fromkeys(h.chassis.gaps))
        return h

    def generation_payloads(self):
        context = self.context(generation=True)
        count = 1
        narrow = ("Use the evidence_digest strata to keep population, intervention, endpoint and study design separate. "
                  "Each hypothesis must address one narrow, falsifiable question with its own supporting and contrary citation IDs. "
                  "Do not merge clinical effects, animal mechanisms and engineered supply into one claim. "
                  "Do not fill the quota with paraphrases of an evidence gap; fewer distinct hypotheses are allowed. "
                  "A narrow source-observation card may use claim exactly 'Source {source_id} reports this observation: {full accepted quote}', "
                  "copying its accepted evidence condition and endpoint exactly and naming only the matching current entity and study species. "
                  "This reports a source observation only; keep inference, efficacy, equivalence and individual prediction outside that claim and explicitly unresolved. "
                  "Do not paraphrase or append an inference to this exact observation form. ")
        exploration = is_disease_exploration(self.run.brief)
        return [
            {
                **context,
                "view": "机制/干预证据；区分相关性、作用方向与反证",
                "instructions": narrow + f"Propose at most {count} distinct hypotheses with falsifiable predictions. Temporary hypothesis_id; version=1,parent_version=null. State inference gaps. At most 2 gap-driven typed tool requests. No quality scores. Tools: {self.registry.catalog()}",
            },
            {
                **context,
                "view": "人群/终点适用性与相反结果；提出能区分替代解释的问题" if exploration else "指定菌株/供给条件/可测暴露；寻找最便宜的区分性验证问题",
                "instructions": narrow + f"Propose at most {count} hypotheses focused on a contrary or alternative explanation, not a second wording of the main intervention claim. Production unknown unless specific evidence. Temporary hypothesis_id; version=1,parent_version=null. At most 2 gap-driven tool requests. Tools: {self.registry.catalog()}",
            },
        ]

    def generate(self):
        item = self.phase("generation", "形成最少问题卡；有收尾余量才加入第二视角", "generation")
        count = 1
        payloads = self.generation_payloads()
        views = 1
        if self.run.brief.budget.max_hypotheses > 1:
            try:
                self.ledger.preflight(sum(self.request_bytes("generation", GenerationDraft, p) for p in payloads),
                    required_calls=2, required_output_bytes=2 * self.output_allowance(),
                    required_seconds=self.latency_allowance(),
                    reserve=self.minimum_chain_reserve("generation", generation_outputs=2),
                    stage="second_generation_view_plan", prior_timeout=self.prior_timeout)
                views = 2
            except BudgetExceeded as exc:
                self.record_budget_failure(exc, "second_generation_view_plan", optional=True)
        else:
            self.record_optional_skip("second_generation_view_plan", "hypothesis_limit")
        self.minimum_chain_mode = views == 1
        payloads = payloads[:views]
        reserve = self.minimum_chain_reserve("generation", generation_outputs=views)
        outputs, failures = [], []
        with ThreadPoolExecutor(max_workers=views, thread_name_prefix="co-generation") as pool:
            futures = {
                pool.submit(
                    self.call, "generation", "视角 " + str(i + 1), GenerationDraft, payload,
                    reserve=reserve, output_cap=self.output_allowance(), optional=i > 0
                ): i
                for i, payload in enumerate(payloads)
            }
            for future in as_completed(futures):
                try:
                    outputs.append((futures[future], future.result()))
                except Exception as exc:  # noqa: BLE001 -- keep the other real worker's output
                    failures.append(str(exc))
        requests = []
        for view, draft in sorted(outputs):
            for h in draft.hypotheses[:count]:
                if len(self.state.hypotheses) >= self.run.brief.budget.max_hypotheses:
                    break
                h.hypothesis_id = "h_" + digest([view, h.title, h.claim])[:12]
                h.version, h.parent_version = 1, None
                h.status = "proposed"  # generation cannot approve its own draft
                h.changes = []
                self.state.hypotheses.append(self.normalize_card(h))
            for proposal in draft.tool_requests[:2]:
                try:
                    arguments = json.loads(proposal.arguments_json)
                    request = ToolRequest(name=proposal.name, arguments=arguments)
                    # Preserve the actual generation view and saved cards even when
                    # an older model response omits explicit gap/hypothesis fields.
                    ids = [h.hypothesis_id for h in self.state.hypotheses
                           if h.hypothesis_id in proposal.hypothesis_ids]
                    if not ids:
                        ids = ["h_" + digest([view, h.title, h.claim])[:12] for h in draft.hypotheses[:count]]
                    plan = self.scheduler.create(request, reason=proposal.gap or
                                  "模型提出补证；旧提案未记录具体缺口文字", hypothesis_ids=ids)
                    requests.append(plan)
                    self.event("generation", "tool_plan", "completed", "保存有界补证计划",
                               "模型提出缺口；执行器先校验参数和唯一身份。", [plan["plan_id"]],
                               input_ids=[f"{i}:v1" for i in ids])
                except (ValueError, TypeError):
                    self.run.warnings.append(INVALID_TOOL_PROPOSAL_WARNING)
                    # Persist deterministic rejection without retaining raw JSON
                    # or pretending that an HTTP lookup occurred.
                    rejected = ToolRequest(name=proposal.name, arguments={"invalid_proposal": True})
                    requests.append(self.scheduler.create(rejected, reason=
                                    "模型补证 JSON 未通过确定性解析；仅记录参数拒绝，不执行 HTTP"))
        self.finish(
            item,
            "generation",
            "partial" if failures else "completed",
            f"{len(self.state.hypotheses)} 个实际返回假说；{len(failures)} 个视角失败",
        )
        self.run.warnings.extend("Generation worker: " + f for f in failures)
        if not self.state.hypotheses:
            raise RuntimeError("No hypotheses returned; do not fabricate a research contract")
        return requests

    def proximity(self):
        item = self.phase("proximity", "按实体、机制、菌株、条件和终点机械去重", "proximity")
        self.state.clusters = hypothesis_groups(latest_cards(self.state), self.run.candidates)
        self.finish(
            item, "proximity", summary=f"规则模块得到 {len(self.state.clusters)} 组；保留全部版本与不同反证"
        )

    def novelty(self, cards):
        if getattr(self, "flow", None):
            self.flow.run_queries("novelty")
            return
        for h in cards[:3]:
            seed = (
                h.molecule_names[0]
                if h.molecule_names
                else (h.target_symbols[0] if h.target_symbols else h.mechanism[:60])
            )
            if not seed:
                continue
            query = f'"{self.disease_english}" AND "{seed}" AND (mechanism OR intervention OR production)'
            try:
                result, _ = self.tool(
                    ToolRequest(
                        name="europepmc_search",
                        arguments={
                            "query": query,
                            "limit": min(3, self.run.brief.budget.max_papers),
                        },
                    )
                )
            except BudgetExceeded:
                self.state.novelty_searches.append(
                    {
                        "hypothesis_id": h.hypothesis_id,
                        "query": query,
                        "status": "budget_exhausted",
                        "source_ids": [],
                    }
                )
                break
            ids = [
                s.source_id
                for s in result.sources
                if any(s.source_id == a.source_id for a in self.run.sources)
            ]
            self.state.novelty_searches.append(
                {
                    "hypothesis_id": h.hypothesis_id,
                    "query": query,
                    "status": result.status,
                    "source_ids": ids,
                    "scope": "bounded Europe PMC query, not an exhaustive novelty audit",
                }
            )
            h.novelty.closest_source_ids = ids
            h.novelty.search_scope = query
        self.persist()

    def review_payload(self, cards):
        return {
            **self.context(cards),
            "claim_relation_contract": {
                "version": 2,
                "scope": "source_observation only; default version 1 remains the inference path",
                "rule": "A main claim may be supported as a source observation only when its entire text is exactly 'Source {source_id} reports this observation: {full accepted quote}'. Set relation_schema_version=2 and claim_scope=source_observation. Include one evidence_relations entry with schema_version=2, exact claim_id/hypothesis_id/version, evidence_id/source_id, the unique current entity_id, relation=supports, and study_system/condition/endpoint copied exactly from that evidence. The card condition/endpoint and requested species must match. A label alone cannot validate a relationship.",
                "limits": "Global mixed/opposes describes the study result, separately from support of this literal observation. No paraphrase, partial quote, appended inference, cross-species projection, efficacy, equivalence or individual prediction is supported by this exception. A supported observation does not make the complete card or therapeutic candidate supported. Review all other claims independently; preserve contrary and unknown findings.",
            },
            "instructions": "Review each exact hypothesis_id/version AND every supplied claim_id in claim_reviews. Never see generator self-scores. Retain/revise/withhold with specific objections and needed evidence. valid_evidence_ids may select that card's existing accepted IDs or new_evidence IDs that you also attach to that exact version's claim_reviews with valid continuous source quotations. New sources may be extracted into new_evidence with exact continuous quotation and location; claim_reviews must reference that evidence_id and only that claim's query-returned sources. State supported/contested/context_only/unknown with study-system, condition and endpoint gaps. Distinguish completed review without support from missing review. For recent closest sources, fill prior_work with mechanism/object/strain/condition/endpoint overlaps and unconfirmed_difference; hits alone do not prove novelty. Unknown production, ambiguous intervention identities, species/endpoint extrapolation remain gaps. Research priority is not efficacy. Source-claim review is not experimental validation.",
        }

    def review(self, cards, title):
        if getattr(self, "flow", None):
            for h in cards:
                self.flow.register(h)
        draft = self.call("reflection", title, ReflectionDraft, self.review_payload(cards),
                          reserve=self.minimum_chain_reserve("reflection"), output_cap=self.output_allowance())
        if getattr(self, "flow", None):
            self.flow.adjudicate(cards, draft.claim_reviews, draft.new_evidence, draft.prior_work)
        known = {(h.hypothesis_id, h.version): h for h in cards}
        supplied = {}
        for r in draft.reviews:
            key = (r.hypothesis_id, r.version)
            if key in supplied:  # ambiguous repeated review fails closed
                supplied[key] = None
            else:
                supplied[key] = r
        for key, h in known.items():
            r = supplied.get(key)
            if r is None:
                r = HypothesisReview(
                    hypothesis_id=h.hypothesis_id,
                    version=h.version,
                    verdict="revise",
                    reason="审查缺失或重复，保留待复核状态",
                    required_evidence=["完整版本特异审查"],
                    valid_evidence_ids=[],
                )
            refs = set(h.evidence_ids + h.opposing_evidence_ids)
            scope = self.flow.reviewed_evidence_ids(h) if getattr(self, "flow", None) else {"evidence_ids": [], "opposing_evidence_ids": []}
            refs.update(scope["evidence_ids"] + scope["opposing_evidence_ids"])
            r.valid_evidence_ids = [i for i in r.valid_evidence_ids if i in refs]
            support_ids = set(h.evidence_ids + scope["evidence_ids"])
            observation_supported = bool(getattr(self, "flow", None) and
                self.flow.main_review_state(h) == "source_observation_supported")
            if not observation_supported and (not support_ids or not set(r.valid_evidence_ids).intersection(support_ids)):
                r.verdict = "revise" if r.verdict != "withhold" else "withhold"
                r.required_evidence.append("该假说缺少其自身已审查的支持证据")
            if getattr(self, "flow", None) and not self.flow.main_reviewed(h):
                r.verdict = "withhold" if r.verdict == "withhold" else "revise"
                r.required_evidence.append(self.flow.main_review_gap(h))
            self.state.reviews.append(r)
            h.status = (
                "withheld"
                if r.verdict == "withhold"
                else ("reviewed" if r.verdict == "retain" else "needs_evidence")
            )
            h.inference_gaps = list(dict.fromkeys(h.inference_gaps + r.required_evidence))
            if getattr(self, "flow", None):
                h.inference_gaps = list(dict.fromkeys(h.inference_gaps + [gap for c in self.flow.claims_for(h) if c.state != "supported" for gap in c.gaps]))
        persist_evidence_digest(self.run)
        self.persist()

    def compare(self):
        item = self.phase("ranking", "在同任务约束下比较最多三个方案", "ranking")
        representatives = {c["representative"] for c in self.state.clusters}
        eligible = [
            h
            for h in latest_cards(self.state)
            if h.hypothesis_id in representatives and h.status == "reviewed" and
            (h.evidence_ids or (getattr(self, "flow", None) and self.flow.reviewed_evidence_ids(h)["evidence_ids"]))
        ][:3]
        for left, right in combinations(eligible, 2):
            cid = (
                "cmp_"
                + digest([left.hypothesis_id, left.version, right.hypothesis_id, right.version])[
                    :12
                ]
            )
            criterion = "研究优先级依据证据适用性、底盘证据与缺口、可检验性及资源匹配；新颖性另行记录"
            same = all(
                normalized(getattr(left, key)) == normalized(getattr(right, key))
                for key in ("chassis_name", "condition", "endpoint")
            )
            if not same:
                review = PairwiseReview(
                    comparison_id=cid,
                    left_id=left.hypothesis_id,
                    left_version=left.version,
                    right_id=right.hypothesis_id,
                    right_version=right.version,
                    outcome="incomparable",
                    reason="条件或终点不同；规则模块弃权，不混排实验体系",
                    criterion=criterion,
                )
                self.event("ranking", "rule", "running", "条件不匹配：不进行 LLM 胜负判断")
            else:
                payload = {**self.context([left, right]), "comparison_id": cid, "criterion": criterion,
                    "instructions": "Compare only these two exact versions under the given criterion. Outcomes include tie/insufficient/incomparable. IDs/versions must match. Cite only their accepted evidence IDs."}
                if not self.optional_preflight("pairwise_comparison", required_bytes=self.request_bytes(
                    "ranking", PairwiseReview, payload), calls=1, reflection=False,
                    seconds=self.latency_allowance()):
                    self.state.comparisons.append(PairwiseReview(comparison_id=cid,
                        left_id=left.hypothesis_id, left_version=left.version,
                        right_id=right.hypothesis_id, right_version=right.version,
                        outcome="insufficient", reason="共享调用、时间与字节额度预留必要收尾；比较未执行", criterion=criterion))
                    self.persist()
                    continue
                draft = self.optional_call("ranking", "成对评审 " + cid, PairwiseReview, payload,
                                           reserve=self.critical_reserve(reflection=False))
                if draft is None:
                    continue
                if (draft.left_id, draft.left_version, draft.right_id, draft.right_version) != (
                    left.hypothesis_id,
                    left.version,
                    right.hypothesis_id,
                    right.version,
                ):
                    raise ValueError("Pairwise output refers to a different hypothesis/version")
                review = draft.model_copy(update={"comparison_id": cid, "criterion": criterion})
                refs = set(
                    left.evidence_ids
                    + left.opposing_evidence_ids
                    + right.evidence_ids
                    + right.opposing_evidence_ids
                )
                if getattr(self, "flow", None):
                    for card in (left, right):
                        scope = self.flow.reviewed_evidence_ids(card)
                        refs.update(scope["evidence_ids"] + scope["opposing_evidence_ids"])
                review.evidence_ids = [i for i in review.evidence_ids if i in refs]
                if review.outcome in ("left", "right") and not review.evidence_ids:
                    review.outcome, review.reason = (
                        "insufficient",
                        "缺少本比较的有效证据引用，不能宣称胜出",
                    )
            self.state.comparisons.append(review)
            self.persist()
        self.finish(
            item,
            "ranking",
            "completed" if self.state.comparisons else "skipped",
            f"{len(self.state.comparisons)} 场实际比较；不足两个合格方案时不生成比赛",
        )

    def evolve(self):
        item = self.phase("evolution", "按证据与批评最多修订一轮", "evolution")
        cards = [h for h in latest_cards(self.state) if h.status != "withheld"][:3]
        reviews = [
            r
            for r in self.state.reviews
            if any((r.hypothesis_id, r.version) == (h.hypothesis_id, h.version) for h in cards)
        ]
        if not cards or not any(r.objections or r.required_evidence for r in reviews):
            self.finish(item, "evolution", "skipped", "没有具体修订依据，不伪造版本变化")
            return
        payload = {**self.context(cards), "reviews": [r.model_dump() for r in reviews],
            "instructions": "At most one revision per supplied card. Preserve ID; version=old+1,parent_version=old. Every change names actual review/source observations. Narrow unsupported claims rather than invent support. Stay anchored. Empty revisions allowed."}
        if not self.optional_preflight("hypothesis_revision", required_bytes=self.request_bytes(
            "evolution", EvolutionDraft, payload), calls=1, growth=True,
            seconds=self.latency_allowance()):
            self.finish(item, "evolution", "skipped", "预算预检不足；保留已审版本，不生成未复核修订")
            return
        draft = self.optional_call("evolution", "据批评修订，不继承旧胜场", EvolutionDraft, payload,
                                   reserve=self.critical_reserve(growth=True))
        if draft is None:
            self.finish(item, "evolution", "skipped", "修订调用未取得可用结果；保留已审版本")
            return
        known = {h.hypothesis_id: h for h in cards}
        revised, seen = [], set()
        for h in draft.revisions:
            old = known.get(h.hypothesis_id)
            if old is None or h.hypothesis_id in seen or not h.changes:
                continue
            if all(
                getattr(h, k) == getattr(old, k)
                for k in (
                    "claim",
                    "condition",
                    "endpoint",
                    "prediction",
                    "validation_question",
                    "inference_gaps",
                )
            ):
                continue  # cosmetic rewording alone is not a scientific revision
            seen.add(h.hypothesis_id)
            h.version, h.parent_version, h.status = old.version + 1, old.version, "revised"
            h = self.normalize_card(h)
            h.inference_gaps.append("新版按本轮复核结果进入后续研究评估，比较名次按版本记录")
            revised.append(h)
            if getattr(self, "flow", None):
                self.flow.revision(old, h, [r for r in reviews if r.hypothesis_id == old.hypothesis_id])
        self.state.hypotheses.extend(revised)
        self.finish(
            item,
            "evolution",
            "completed" if revised else "skipped",
            f"{len(revised)} 个有依据的新版，旧版仍保留",
        )
        if revised:
            self.review(revised, "修订版本重新审查")
            if getattr(self, "flow", None):
                self.flow.complete("revision_fresh_review")

    def meta_payload(self, cards):
        keys = {(h.hypothesis_id, h.version) for h in cards}
        payload = {
            **self.context(cards, closing=True),
            "reviews": [r.model_dump() for r in self.state.reviews if (r.hypothesis_id, r.version) in keys],
            "comparisons": [c.model_dump() for c in self.state.comparisons
                            if (c.left_id, c.left_version) in keys and (c.right_id, c.right_version) in keys],
            "instructions": "Select at most 3 supplied latest versions as research questions. Select evidence IDs from the card's original citations and its exact-version reviewed_evidence_associations; original card citations are a historical generation snapshot, not the full updated evidence set. Consider mixed/opposing findings and scope. Include only references actually relevant to the contract question, never all new IDs mechanically. Unknowns stay unknown. Not a therapy list. Name minimal validation and resource requirements. Parameter needs have value=null, measurement_required, explicit units and reason; no inferred numerical PK/PD/production parameters. Revised versions have not inherited old rankings. Missing review/support means needs_evidence; completed review without support is distinct from absent review.",
        }
        from .closing_context import compact_closing_context
        return compact_closing_context(payload)

    def meta(self):
        item = self.phase("meta", "形成研究合同与 NasoTwin 测量需求", "meta_review")
        cards = [h for h in latest_cards(self.state) if h.status != "withheld"]
        draft = self.call("meta_review", "总审与最多三个研究合同", MetaDraft, self.meta_payload(cards),
                          output_cap=self.output_allowance())
        known = {(h.hypothesis_id, h.version): h for h in cards}
        seen = set()
        for contract in draft.contracts:
            key = (contract.hypothesis_id, contract.version)
            if key not in known or key in seen or len(self.state.contracts) >= 3:
                continue
            h = known[key]
            seen.add(key)
            scope = self.flow.reviewed_evidence_ids(h) if getattr(self, "flow", None) else {"evidence_ids": [], "opposing_evidence_ids": []}
            allowed = set(h.evidence_ids + scope["evidence_ids"])
            # A globally negative result may support a literal source report.
            # It is selectable as a question basis only after the v2 proof; it
            # remains negative evidence and never upgrades the contract status.
            allowed.update(a["evidence_id"] for a in scope.get("associations", [])
                if a.get("verdict") == "supported" and a.get("relation_schema_version") == 2
                and a.get("assessment_scope") == "source_observation"
                and a.get("claim_relation") == "supports" and a.get("relation_basis") == "exact_source_report_only")
            contract.evidence_ids = [i for i in contract.evidence_ids if i in allowed]
            contract.opposing_evidence_ids = list(dict.fromkeys(h.opposing_evidence_ids + scope["opposing_evidence_ids"]))
            if h.status != "reviewed" or not contract.evidence_ids:
                contract.status = "needs_evidence"
            if getattr(self, "flow", None) and not self.flow.main_reviewed(h):
                contract.status = "needs_evidence"
            contract.remaining_unknowns = list(
                dict.fromkeys(contract.remaining_unknowns + h.inference_gaps + h.chassis.gaps)
            )
            # Numerical values are forbidden by the parameter schema, not only the prompt.
            source_ids = {s.source_id for s in self.run.sources}
            for need in contract.parameter_needs:
                need.source_ids = [s for s in need.source_ids if s in source_ids]
            self.state.contracts.append(contract)
            self.state.selected_versions.append(
                {
                    "hypothesis_id": h.hypothesis_id,
                    "version": h.version,
                    "selection_basis": "meta_review_latest_version_not_inherited_wins",
                }
            )
        self.state.limitations = list(dict.fromkeys(self.state.limitations + draft.common_gaps))
        if getattr(self, "flow", None):
            self.flow.complete("contract_validation")
        self.finish(
            item,
            "meta_review",
            "completed" if self.state.contracts else "partial",
            f"{len(self.state.contracts)} 份供人工评审的研究合同",
        )

    def restore_seed(self):
        from .evidence import validate_evidence
        from .models import ResearchRun

        if not self.run.parent_run_id:
            raise ValueError("A saved-hypothesis continuation requires a parent run")
        path = self.store.run_dir(self.run.project_id, self.run.parent_run_id) / "run.json"
        content = path.read_bytes()
        parent = ResearchRun.model_validate_json(content)
        if (
            parent.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
            or not parent.co_scientist
            or not parent.co_scientist.hypotheses
        ):
            raise ValueError("Parent must be terminal and contain saved hypotheses")
        for field in (
            "disease",
            "phenotype",
            "species",
            "chassis_name",
            "conditions",
            "success_criterion",
            "engineering_allowed",
            "allowed_precursors",
            "population",
            "delivery_context",
            "existing_candidates",
        ):
            if getattr(parent.brief, field) != getattr(self.run.brief, field):
                raise ValueError(
                    "Continuation anchor changed; start a fresh research instead: " + field
                )
        accepted, rejected = validate_evidence(
            [e.model_dump() for e in parent.evidence], parent.sources
        )
        if rejected or not accepted:
            raise ValueError("Stored evidence does not match its source snapshots")
        self.run.sources = [s.model_copy(deep=True) for s in parent.sources]
        self.run.evidence = accepted
        self.run.targets = [t.model_copy(deep=True) for t in parent.targets]
        self.run.candidates = [c.model_copy(deep=True) for c in parent.candidates]
        self.state.anchor = parent.co_scientist.anchor.model_copy(deep=True)
        self.state.seed = {
            "parent_run_id": parent.run_id,
            "original_record_sha256": hashlib.sha256(content).hexdigest(),
            "mode": "saved_sources_and_drafts_rechecked_not_new_independent_evidence",
        }
        self.state.novelty_searches = [
            {**q, "reused_from_run": parent.run_id} for q in parent.co_scientist.novelty_searches
        ]
        if getattr(self, "flow", None):
            self.flow.ingest(self.run.sources, "saved")
        for old in parent.co_scientist.hypotheses:
            h = old.model_copy(deep=True)
            h.status = "proposed"  # never inherit the prior run's self-approval/review
            self.state.hypotheses.append(self.normalize_card(h))
            if getattr(self, "flow", None):
                self.flow.register(h)
                q = self.flow.new_query("reuse", "saved_version_context", "已存版本与来源重新审查；没有本轮新检索",
                    hypothesis_id=h.hypothesis_id, version=h.version, status="reused", provider="saved",
                    claim_ids=[c.claim_id for c in self.flow.claims_for(h)],
                    reused_from=parent.run_id, source_ids=[s.source_id for s in self.run.sources],
                    stop_reason="continuation_not_new_verification")
                for c in self.flow.claims_for(h):
                    c.query_ids.append(q.query_id)
        self.state.limitations.append(
            "本轮继续已保存的来源与草稿，重新执行审查和比较；来源保留原检索记录。"
        )
        self.event(
            "supervisor",
            "saved_input",
            "completed",
            "复用已保存来源与草稿，逐引用重新核对",
            parent.run_id,
        )
        self.proximity()

    def initial_research(self):
        project_id = self.run.project_id
        step = self.phase("anchor", "冻结问题、菌株和研究边界", "supervisor")
        if self.settings.llm.backend == "none":
            plan = PlanDraft(
                disease_english=self.run.brief.disease,
                phenotype_english="",
                searches=[
                    f'"{self.run.brief.disease}" AND (mechanism OR intervention)',
                    f'"{self.run.brief.disease}" AND (adverse OR ineffective)',
                ],
                research_questions=["模型禁用；只有检索观察，没有已判读假说"],
                assumptions=[],
            )
        else:
            draft = self.call(
                "supervisor",
                "bio-problem-anchor",
                AnchorDraft,
                {
                    "brief": task_context(self.run.brief, include_documents=True),
                    "memory": [] if is_disease_exploration(self.run.brief) else self.store.search_memory(project_id, self.run.brief.disease, limit=3),
                    "instructions": "Freeze user species/strain/constraints; do not rewrite them. Plan English Europe PMC queries for mechanism, intervention, and contrary evidence; no scientific assertions. Use max 3 initial queries, reserve searches for claim-level novelty. Unspecified endpoint remains a clarification question. Memory is a lead, not new evidence.",
                },
                output_cap=self.output_allowance(),
            )
            plan = PlanDraft(
                disease_english=draft.disease_english,
                phenotype_english="",
                searches=draft.searches[:3],
                research_questions=draft.questions,
                assumptions=[],
            )
            if not self.run.brief.success_criterion:
                self.state.anchor.success_criterion = draft.success_criterion
        self.disease_english = plan.disease_english
        self.flow.inventory()
        plan.searches = plan.searches[:self.flow.data.stage_limits["landscape"]]
        self.run.questions = plan.research_questions
        self.finish(step, "supervisor")
        step = self.phase("literature", "建立文献地图，保留相反发现", "supervisor")
        retrieval = self.run.model_copy(deep=True)
        # Reserve half the paper quota for actual claim-level followup searches.
        retrieval.brief.budget.max_papers = max(1, self.run.brief.budget.max_papers // 2)
        provided = [s for s in self.run.sources if s.text.strip() and
                    s.evidence_scope in {"abstract", "full_text", "local_document"}]
        if self.candidate_assessment_mode() and provided:
            # Availability is not acceptance or sufficient evidence. Review the
            # supplied bounded material first; online coverage remains unknown.
            self.flow.new_query("landscape", "provided_materials_first",
                "候选评估优先审查已有材料；本阶段未执行在线扩展", status="skipped",
                source_ids=[s.source_id for s in provided], stop_reason="candidate_core_before_online_extension")
            self.run.warnings.append("Candidate assessment first uses supplied material; online literature coverage has not been extended or verified in this stage.")
        else:
            try:
                self.legacy._retrieve(retrieval, plan)
            finally:
                self.run.sources = retrieval.sources
        self.flow.ingest(self.run.sources)
        self.flow.complete("landscape")
        self.run.warnings.extend(retrieval.warnings)
        self.finish(
            step,
            "supervisor",
            "completed" if self.run.sources else "partial",
            f"{len(self.run.sources)} 个来源；摘要/全文/数据库范围分别保留",
        )
        if self.settings.llm.backend == "none":
            raise RuntimeError(
                "LLM disabled; source retrieval is not a completed Co-Scientist result"
            )
        step = self.phase("evidence", "核验原文与独立语义审查；解析靶点和分子身份", "supervisor")
        if self.candidate_assessment_mode():
            prepare_core_identities(self, getattr(getattr(self, "background_package", None), "identities", []), local_only=True)
        self.legacy._synthesize(self.run, EvidenceLLM(self), self.directory)
        prepare_core_identities(self, getattr(getattr(self, "background_package", None), "identities", []))
        self.legacy._resolve_identities(self.run)
        persist_evidence_digest(self.run, getattr(getattr(self, "background_package", None), "sources", []))
        if any(t["status"] == "budget_exhausted" for t in self.state.tool_calls):
            raise BudgetExceeded("Identity/tool budget exhausted; partial identities saved")
        self.finish(
            step,
            "supervisor",
            "completed" if self.run.evidence else "partial",
            f"{len(self.run.evidence)} 个通过自动原文与语义审查的证据",
        )
        if not self.run.evidence:
            raise RuntimeError("No reviewed evidence; do not synthesize supported hypotheses")
        if self.candidate_assessment_mode():
            self.review_candidate_evaluations()
            self.hypothesis_expansion_started = self.admit_candidate_hypothesis_expansion()
            if not self.hypothesis_expansion_started:
                return
        self.start_hypothesis_research()

    def start_hypothesis_research(self):
        requests = self.generate()
        self.proximity()
        self.flow.compile_queries(latest_cards(self.state))
        self.flow.stage = "supplement"
        if getattr(self, "minimum_chain_mode", False):
            for plan in requests:
                plan.update(status="skipped", stop_reason="necessary_chain_first")
            for stage in ("supplemental_queries", "claim_novelty_queries"):
                self.record_optional_skip(stage, "necessary_chain_first")
            return
        new_sources = []
        for plan in requests:
            if not self.optional_preflight("supplemental_tool"):
                plan.update(status="skipped", stop_reason="critical_review_reservation")
                continue
            if plan["request"]["name"] == "europepmc_search" and not self.flow.allowed("supplement"):
                plan.update(status="skipped", stop_reason="stage_quota_reserved_for_novelty")
                continue
            before_plan = {s.source_id for s in self.run.sources}
            self.scheduler.run(plan)
            new_sources.extend(s.source_id for s in self.run.sources if s.source_id not in before_plan)
            if plan["status"] == "budget_exhausted":
                self.state.limitations.append("工具预算不足，保存已完成解析和部分补证；未执行后续请求")
                for pending in requests:
                    if pending["status"] == "planned":
                        pending.update(status="skipped", stop_reason="shared_budget")
                raise BudgetExceeded("Supplemental tool budget exhausted; partial evidence saved")
        before = {s.source_id for s in self.run.sources}
        if self.optional_preflight("supplemental_queries"):
            self.flow.run_queries("supplement")
        if self.optional_preflight("claim_novelty_queries"):
            self.novelty(latest_cards(self.state))
        if any(t["status"] == "budget_exhausted" for t in self.state.tool_calls):
            raise BudgetExceeded("Follow-up tool budget exhausted; saved proposals remain partial")
        new_sources.extend(s.source_id for s in self.run.sources if s.source_id not in before)
        if new_sources:
            self.supplement_evidence(new_sources)

    def supplement_evidence(self, new_sources):
        supplemental = self.run.model_copy(deep=True)
        supplemental.sources = [s for s in supplemental.sources if s.source_id in new_sources]
        incomplete = False
        try:
            self.legacy._synthesize(supplemental, EvidenceLLM(self, optional=True),
                                    self.directory / "supplemental")
            existing = {e.evidence_id: e for e in self.run.evidence}
            existing.update({e.evidence_id: e for e in supplemental.evidence})
            self.run.evidence = list(existing.values())
            self.run.warnings.extend(supplemental.warnings)
        except Exception as exc:  # noqa: BLE001 -- optional enrichment cannot erase reviewed artifacts
            incomplete = True
            self.state.optional_skips.append({"stage": "supplemental_evidence", "reason":
                "budget_preflight" if isinstance(exc, BudgetExceeded) else "optional_provider_failure",
                "prior_timeout": self.prior_timeout})
        if incomplete or not supplemental.evidence or any("review incomplete" in w for w in supplemental.warnings):
            self.state.limitations.append("补抽未取得新增已审证据；已有证据保留。新增来源须逐主张Reflection核查或保持未决。")
        persist_evidence_digest(self.run)
        self.persist()

    def execute(self, project_id, run_id):
        self.run = self.store.load_run(project_id, run_id)
        if self.run.co_scientist and self.run.co_scientist.events:
            return self.run  # explicit followup creates a new budgeted run; no reset/replay
        validate_exploration_start(self.run)
        if not self.settings.allow_remote_llm_data and self.settings.llm.backend != "none":
            raise ValueError("Remote LLM data sharing is disabled")
        self.candidate_core_active = self.candidate_assessment_mode()
        self.assessment_completed = False
        self.hypothesis_expansion_started = False
        start = time.monotonic()
        self.directory = self.store.run_dir(project_id, run_id)
        self.ledger = RunLedger(
            self.run.brief.budget, start + self.run.brief.budget.timeout_seconds
        )
        self.sources = BoundedSources(self.legacy.sources, self)
        self.legacy.sources = self.sources
        self.legacy.deadline = self.ledger.deadline
        # Identity connectors inside the registry must use the underlying source,
        # otherwise the supervisor wrapper would recursively create another plan.
        self.registry = ToolRegistry(self.sources, identity_sources=self.sources.source)
        self.state = self.run.co_scientist = CoScientistState(
            agents=[
                AgentState(agent_id=k, role=label, execution=execution)
                for k, (label, execution) in ROLES.items()
            ]
        )
        self.state.anchor = ProblemAnchor(
            disease=self.run.brief.disease,
            phenotype=self.run.brief.phenotype,
            species=self.run.brief.species,
            chassis_name="" if is_disease_exploration(self.run.brief) else self.run.brief.chassis_name,
            population=self.run.brief.population,
            delivery_context=self.run.brief.delivery_context,
            research_goal=self.run.brief.research_goal,
            condition=("未指定干预条件" if is_disease_exploration(self.run.brief)
                       else self.run.brief.conditions or "未指定培养与供给条件"),
            success_criterion=self.run.brief.success_criterion or "待团队明确可测量研究终点",
            non_goals=["临床处方/疗效确认", "四模型筛药、对接、ADMET、完整Gate、MD或湿实验执行"],
            constraints=[] if is_disease_exploration(self.run.brief) else [
                f"engineering_allowed={self.run.brief.engineering_allowed}",
                "allowed_precursors=" + ", ".join(self.run.brief.allowed_precursors),
                "resources=" + ", ".join(self.run.brief.experimental_capabilities),
            ],
        )
        self.flow = ResearchFlow(self)
        self.identity_sync = CandidateIdentitySync(self.run, self.state)
        self.scheduler = ToolScheduler(self.planned_tool, self.state.tool_plans, self.persist,
            bind_candidate=self.identity_sync.bind, apply_identity=self.identity_sync.apply)
        http = getattr(self.sources, "http", None)
        if http is not None:
            http.deadline = self.ledger.deadline
            http.before_request, http.on_cache_hit = self.ledger.request, self.ledger.cache
        self.run.status = ExecutionStatus.RUNNING
        self.persist()
        try:
            if self.run.brief.continue_saved_hypotheses:
                self.restore_seed()
            else:
                self.initial_research()
            if not self.candidate_assessment_mode() or self.hypothesis_expansion_started:
                step = self.phase("reflection", "逐假说审查支持、反证与底盘缺口", "reflection")
                self.review(latest_cards(self.state), "bio-research-review：完整初始版本审查")
                self.finish(step, "reflection")
                if getattr(self, "minimum_chain_mode", False):
                    for optional_stage in ("feedback_queries", "comparison", "evolution"):
                        self.record_optional_skip(optional_stage, "necessary_chain_first")
                else:
                    if not self.run.brief.continue_saved_hypotheses and self.optional_preflight(
                        "feedback_queries", reflection=False):
                        self.flow.feedback(latest_cards(self.state))
                    self.compare()
                    self.evolve()
                self.meta()
            validation = self.flow.verify()
            if not validation["valid"]:
                raise ValueError("Research receipt validation failed; contracts remain pending")
            self.run.status = (
                ExecutionStatus.COMPLETED
                if self.state.contracts and not self.run.errors
                else ExecutionStatus.PARTIAL
            )
        except Exception as exc:  # noqa: BLE001 -- durable boundary, never fake completion
            self.run.errors.append(f"{type(exc).__name__}: {exc}")
            self.run.status = (
                ExecutionStatus.PARTIAL if self.run.sources else ExecutionStatus.FAILED
            )
            for step in self.run.steps:
                if step.status == "running":
                    step.status, step.reason = "partial", str(exc)
            self.state.limitations.append("本轮未完成全部研究阶段：" + str(exc))
            self.event("supervisor", "stop", "partial", "停止并保存已有证据与假说", str(exc))
        for actor in self.state.agents:
            if actor.status == "queued":
                actor.status, actor.task = "skipped", "未执行，不显示虚构成果"
            elif actor.status == "running":
                actor.status = "partial"
        decide_candidates(self.run.candidates, self.run.evidence)
        self.run.usage = {
            **self.ledger.snapshot(),
            "llm_calls": self.actual_calls,
            "llm_cache_hits": self.llm_cache_hits,
            "duration_seconds": round(time.monotonic() - start, 3),
            "sources": len(self.run.sources),
            "validated_evidence": len(self.run.evidence),
            "research_mode": "co_scientist",
            "parallel_generation_workers": 2,
            **({"synthesis_expansion": self.synthesis_expansion_receipt}
               if hasattr(self, "synthesis_expansion_receipt") else {}),
            **({"candidate_assessment_status": "completed_review" if self.assessment_completed else "needs_review",
                "hypothesis_expansion_status": "started" if self.hypothesis_expansion_started else "not_run"}
               if self.candidate_assessment_mode() else {}),
        }
        self.persist()
        self.write_outputs()
        self.store.save_run(self.run)
        self.store.add_memory(
            project_id,
            kind="co_scientist_summary",
            source_run_id=run_id,
            text=f"{self.run.brief.disease}: {len(self.state.hypotheses)} hypothesis versions, {len(self.state.contracts)} contracts; {self.run.status.value}",
            verified=False,
            metadata={"scope": "research_proposals_not_experimental_facts"},
        )
        return self.run

    def write_outputs(self):
        from .reports import render_report

        for name, value in (
            ("problem_anchor.json", self.state.anchor.model_dump()),
            ("hypotheses.json", [h.model_dump() for h in self.state.hypotheses]),
            ("reviews.json", [r.model_dump() for r in self.state.reviews]),
            ("comparisons.json", [c.model_dump() for c in self.state.comparisons]),
            ("events.json", [e.model_dump() for e in self.state.events]),
            ("tool_calls.json", self.state.tool_calls),
            ("tool_plans.json", self.state.tool_plans),
            ("budget.json", self.state.budget),
            ("co_scientist.json", self.state.model_dump()),
            ("research_flow.json", self.state.research_flow.model_dump()),
            ("research_flow_validation.json", self.flow.verify()),
            ("sources.json", [s.model_dump() for s in self.run.sources]),
            ("candidate_evaluations.json", [a.model_dump() for a in self.run.candidate_evaluations]),
        ):
            write_json(self.directory / name, value)
        report = self.directory / "report.md"
        self.run.report_path = str(report.relative_to(self.store.project_dir(self.run.project_id)))
        report.write_text(render_report(self.run), encoding="utf-8")
        (self.directory / "research_contract.md").write_text(
            render_contracts(self.run), encoding="utf-8"
        )


def render_contracts(run):
    state = run.co_scientist
    if state is None:
        return ""
    from .reports import cell

    lines = ["# Co-Scientist 研究合同", "", "本合同列出研究优先级、证据依据及参数测量需求。", ""]
    for c in state.contracts:
        lines += [
            f"## {cell(c.hypothesis_id)} v{c.version}",
            "",
            c.question,
            "",
            f"状态：{c.status}。{c.judgment}",
            "",
            "最低验证问题：" + c.minimal_validation,
            "",
            "证据：" + ", ".join(c.evidence_ids),
            "",
            "反证：" + ", ".join(c.opposing_evidence_ids),
            "",
            "资源：" + "; ".join(c.resource_requirements),
            "",
            "未知：" + "; ".join(c.remaining_unknowns),
            "",
        ]
        for need in c.parameter_needs:
            lines += [
                f"- 测量需求 `{cell(need.variable)}` ({cell(need.unit)})：{need.why_needed}；值未提供。"
            ]
    if not state.contracts:
        lines += ["本轮未生成研究合同；已保存假说、预算和停止原因供继续研究使用。", ""]
    lines += ["## 局限", ""] + ["- " + x for x in state.limitations]
    return "\n".join(lines) + "\n"

