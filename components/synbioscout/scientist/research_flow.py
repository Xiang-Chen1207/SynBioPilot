"""Deterministic literature/claim receipts inside the existing bounded executor.

No skill interpreter, arbitrary URL/shell dispatch, new framework or implicit loop.
The caller's ToolScheduler, HTTP client and RunLedger remain the execution path.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .budgets import BudgetExceeded
from .flow_models import (
    ClaimRecord,
    ClaimReview,
    EvidenceLink,
    NoveltyReceipt,
    PublicationGroup,
    QueryReceipt,
    ResearchFlowState,
    RevisionReceipt,
    SourceSnapshot,
)
from .models import SourceRecord
from .research_public import public_literature_source
from .store import digest, utc_now
from .tool_public import _MISSING, _url
from .tools import ToolRequest


def norm(text):
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def source_observation_statement(source_id, quote):
    """v2's only licensed projection: a source reports this complete exact quote.

    This claims a recorded observation, not efficacy, equivalence, causality or
    individual prediction. This deterministic exception requires the exact source-observation form.
    """
    return f"Source {source_id} reports this observation: {quote}"


def observation_systems(value):
    """Finite conflict vocabulary, never positive proof of a study population.

    Human words can occur only in background. Approval additionally requires an
    affirmative current study-object passage via observation_study_objects().
    Generic/unsupported animal or model labels are rejection signals only.
    """
    text = norm(value)
    patterns = {
        "human": r"\b(?:humans?|homo sapiens|children|child|patients?|participants?|youths?|adolescents?|volunteers?|men|women|people)\b|人体|人群|儿童|患者|志愿者|青少年",
        "mouse": r"\b(?:mus musculus|mice|mouse|murine)\b|小鼠",
        "rat": r"\b(?:rattus norvegicus|rats?)\b|大鼠",
        "cell": r"\b(?:cells?|cellular|cell lines?|in vitro|ex vivo|organoids?|explants?|cultures?|hela|hek[- ]?293|sh[- ]?sy5y|ipscs?)\b|细胞|体外|离体|类器官|组织培养",
        "nonhuman": r"\b(?:non[- ]?humans?|animals?|rodents?|vertebrates?|invertebrates?|organisms?|"
                    r"wistar|sprague[- ]dawley|zebrafish|danio rerio|fish|fishes|medaka|"
                    r"hamsters?|gerbils?|rabbits?|guinea pigs?|macaques?|monkeys?|primates?|"
                    r"dogs?|canine|cats?|feline|pigs?|porcine|swine|sheep|ovine|goats?|bovine|cattle|"
                    r"horses?|equine|chickens?|avian|drosophila|caenorhabditis|c[.] elegans|xenopus|"
                    r"axolotls?|ambystoma|saccharomyces|yeast|escherichia|bacteria|bacterial)\b|"
                    r"动物|啮齿|斑马鱼|果蝇|线虫|非人灵长|兔|猪|酵母|细菌",
        "computational": r"\bin silico\b|\b(?:computer|computational|simulated) (?:models?|systems?)\b|计算机模型|计算模型|模拟系统",
    }
    return {key for key, pattern in patterns.items() if re.search(pattern, text)}


# This small recognizer accepts explicit recruitment/allocation or current trial
# population statements. It is intentionally not general species or NLI parsing.
_OBSERVATION_SUBJECT = (
    r"(?:human (?:subjects?|participants?|adults?)|patients?|children|child|youths?|"
    r"adolescents?|volunteers?|men|women|boys|girls|mice|mouse|rats?)"
)
_OBSERVATION_ACTION = r"(?:enrolled|recruited|randomi[sz]ed|allocated|assigned|included|treated|examined|studied)"
_DENIED_OBJECT = re.compile(
    rf"(?:\b(?:no|none of the)\s+(?:human\s+)?{_OBSERVATION_SUBJECT}\b[^.;]{{0,70}}?"
    rf"\b(?:{_OBSERVATION_ACTION}|participated)\b|"
    rf"\b{_OBSERVATION_SUBJECT}\b[^.;]{{0,70}}?\b(?:were|was|are|did|have|has)\s+not\s+"
    rf"(?:ever\s+)?(?:{_OBSERVATION_ACTION}|participate)\b)|"
    r"(?:未|没有|不)(?:纳入|招募|入组).{0,30}(?:患者|儿童|志愿者|青少年)"
)
_NONCURRENT_OBJECT = re.compile(
    r"\b(?:background|rationale|previous(?:ly)?|prior|earlier|historical|reported by|"
    r"hypothetical|planned|proposed|future|intended|will|would|could|might|may|"
    r"not|no|never|excluded|didn't|weren't|wasn't)\b|背景|既往|以往|先前|计划|拟|将|未|没有|不纳入"
)
_UNCERTAIN_OBJECT = re.compile(
    r"(?:\b(?:unknown|unreported|unspecified|uncharacterized|unclear|not reported|not specified)\b.{0,100}"
    r"\b(?:system|model|organism|subjects?|population|species|strain)\b|"
    r"\b(?:system|model|organism|subjects?|population|species|strain)\b.{0,100}"
    r"\b(?:unknown|unreported|unspecified|uncharacterized|unclear|not reported|not specified)\b)|"
    r"(?:物种|对象|系统|模型|人群|菌株).{0,30}(?:不明|未知|未报告)|"
    r"(?:不明|未知|未报告).{0,30}(?:物种|对象|系统|模型|人群|菌株)"
)


def observation_study_objects(value):
    """Return evidenced current subjects plus an unresolved-object flag.

    Whole source snapshots remain available for reproducibility. Mere human
    background, a target population, planned work and unknown experimental
    objects retain their grammatical role; participant evidence requires an affirmative participant relation.
    """
    objects, unresolved = set(), False
    section = "unstructured"
    for clause in re.split(r"[;；。!?！？]|(?<=[.])\s+|[\r\n]+", norm(value)):
        if not clause.strip():
            continue
        clause = clause.strip()
        heading = re.match(r"^(?:synthetic\s+)?(background|introduction|rationale|methods|materials and methods|results|discussion|conclusions?)\b", clause)
        if heading:
            section = heading[1]
        elif section == "unstructured" and re.search(r"\b(?:background|rationale)\b", clause):
            section = "background"
        if _UNCERTAIN_OBJECT.search(clause) or _DENIED_OBJECT.search(clause):
            unresolved = True
        # An explicit study/experiment object outside the recognizer is unknown,
        # rather than being silently treated as human from another sentence.
        object_clause = re.search(
            r"\b(?:this |the )?(?:experiment|study|assay)\s+(?:used|uses|using|was conducted in|was performed in)\s+(.+)", clause)
        if object_clause and not observation_systems(object_clause[1]):
            unresolved = True
        if _NONCURRENT_OBJECT.search(clause):
            continue
        # Participant numbers in a background/review passage are not this
        # source's cohort. Only an explicit current-study actor can override it.
        if section in {"background", "introduction", "rationale", "discussion", "conclusion", "conclusions"} and not re.search(
                r"\b(?:we|this study|this trial)\b|本(?:研究|试验)", clause):
            continue
        passive = re.search(rf"\b{_OBSERVATION_SUBJECT}\b[^.;]{{0,110}}?\b(?:were|was|are)\s+(?:randomly\s+)?{_OBSERVATION_ACTION}\b", clause)
        active = re.search(rf"\b(?:we|this study|the study|this trial|the trial)\s+{_OBSERVATION_ACTION}\b[^.;]{{0,100}}?\b{_OBSERVATION_SUBJECT}\b", clause)
        trial = re.search(rf"\b(?:this study|this trial)\s+(?:was|is)\b[^.;]{{0,150}}?\btrial\b[^.;]{{0,90}}?\bin\s+{_OBSERVATION_SUBJECT}\b", clause)
        chinese = re.search(r"本(?:研究|试验)(?:共)?(?:纳入|招募|入组)(?:了)?[0-9一二三四五六七八九十百零两名例位健康]*?(?:患者|儿童|志愿者|青少年)", clause)
        if passive or active or trial or chinese:
            systems = observation_systems(clause)
            if len(systems) == 1:
                objects.update(systems)
            else:
                unresolved = True
    return objects, unresolved


# Conservative vocabulary for dependency detection; assertion parsing has separate rules. Unknown, passive
# and conditional supply wording never exempts the designated-strain evidence
# requirement. Morphological families also cover producing/synthesizes, etc.
_PRODUCTION_LANGUAGE = (
    r"\b(?:produc\w*|biosynth\w*|synthes\w*|secret\w*|generat\w*|yield\w*|"
    r"manufactur\w*|ferment\w*|convert\w*|mak(?:e[rs]?|ing)|made)\b|"
    r"生产|产出|产量|产生|合成|分泌|生成|制造|发酵|转化|制备"
)
_SUPPLY_LANGUAGE = re.compile(_PRODUCTION_LANGUAGE + r"|\b(?:suppl\w*|provid\w*)\b|供给|供应|提供")
_PRODUCTION_ACTION = re.compile(_PRODUCTION_LANGUAGE)
_UNCONFIRMED_SUPPLY = re.compile(
    r"\b(?:unknown|unmeasured|unverified|uncertain|unconfirmed|hypothes\w*|"
    r"might|may|could|potential\w*|whether|requires?|needs?|pending|not|no|"
    r"cannot|unable|failed|lacks?|without|can|will|would|should)\b|\b(?:has|have|had) yet to\b|"
    r"未知|未验证|未证实|尚未|不明|不能|无法|未能|未检测|没有|缺乏|待|需|是否|可能|假设|推测|"
    r"(?:可以|能够|有望)(?:生产|合成|供给|提供|产生)"
)
_EXTERNAL_SUPPLY = re.compile(
    r"\b(?:supplement\w*|commercial\w*|chemical\w*|exogen\w*|external\w*|"
    r"fed|feed\w*)\b|medium containing|media containing|supplied (?:with|to)|"
    r"外加|外源|补加|添加|商业|化学合成|培养基(?:含|中含)|投喂"
)
_SUPPLY_VERB = (
    r"(?:produc(?:e|es|ed|ing)|(?:bio)?synthesi[sz](?:e|es|ed|ing)|"
    r"secret(?:e|es|ed|ing)|generat(?:e|es|ed|ing)|yield(?:s|ed|ing)?|"
    r"manufactur(?:e|es|ed|ing)|makes?|making|made)"
)
_SUPPLY_PASSIVE = r"(?:produced|(?:bio)?synthesi[sz]ed|secreted|generated|yielded|manufactured|made)"
_AUXILIARIES = r"(?:(?:is|are|was|were|has|have|had|been|being|directly|actively|naturally)\s+){0,4}"
_AMOUNT_WORDS = r"(?:(?:a|an|the|detectable|measurable|recorded|some|trace|substantial|small|amounts?|quantities|quantity|levels?|of)\s+){0,6}"
# Only complete, explicitly circumstantial tails are licensed. Unknown words
# after a molecule are part of the object, not guessed context. In particular,
# an `in...` character prefix is not the `in` preposition.
_PRODUCTION_CONTEXT = (
    r"(?:\s+(?:under(?:\s+the)?\s+(?:recorded|specified|tested|reported|experimental|culture|assay)\s+conditions|"
    r"in(?:\s+the)?\s+(?:culture|medium)))?"
)
_CHINESE_PRODUCTION_CONTEXT = r"(?:在(?:记录|指定|实验|培养)条件下)?"


def supply_dependency(text):
    return bool(_SUPPLY_LANGUAGE.search(norm(text)))


def affirmative_supply_span(h, evidence):
    """Require one affirmative original clause to identify strain, molecule and supply.

    Bind study_system to the strain named in the quotation. Other-strain evidence
    retains its original strain scope; ambiguous assignments remain unresolved.
    """
    names = [norm(n) for n in h.molecule_names if n.strip()]
    if not names or not norm(h.chassis_name) or norm(h.chassis_name) not in norm(evidence.study_system):
        return False
    strain = re.escape(norm(h.chassis_name))
    # Do not cut English periods/commas inside chemical names and accidentally
    # discard a prefix entity. Complex multi-sentence quotations abstain.
    for raw_clause in re.split(r"[;；。!?！？]", norm(evidence.quote)):
        clause = raw_clause.strip().removesuffix(".").strip()
        if (_UNCONFIRMED_SUPPLY.search(clause) or _EXTERNAL_SUPPLY.search(clause)
                or not _PRODUCTION_ACTION.search(clause)):
            continue
        def exact_object(raw, name, *, context=False):
            tail = _PRODUCTION_CONTEXT if context else ""
            return bool(re.fullmatch(_AMOUNT_WORDS + re.escape(name) + tail, raw.strip()))

        def bound(name, sentence=clause):
            molecule = re.escape(name)
            # Capture the entire product phrase/subject from a clause anchor.
            # Hyphens, suffix nouns, salts, receptors and derivatives retain their distinct chemical scope.
            active = re.fullmatch(rf"(?:the\s+)?{strain}\s+{_AUXILIARIES}{_SUPPLY_VERB}\s+(?P<object>.+)", sentence)
            if active and exact_object(active["object"], name, context=True):
                return True
            passive = re.fullmatch(rf"(?P<object>.+?)\s+{_AUXILIARIES}{_SUPPLY_PASSIVE}\s+by\s+(?:the\s+)?{strain}{_PRODUCTION_CONTEXT}", sentence)
            if passive and exact_object(passive["object"], name):
                return True
            nominal = re.fullmatch(rf"(?:the\s+)?{strain}(?:'s)?\s+(?:production|biosynthesis|synthesis|secretion|yield)\s+of\s+(?P<object>.+?)\s+{_AUXILIARIES}(?:measured|observed|quantified|detected){_PRODUCTION_CONTEXT}", sentence)
            if nominal and exact_object(nominal["object"], name):
                return True
            chinese_active = re.fullmatch(rf"{strain}(?:菌株)?(?:在[^，,;；。.]{{0,30}}下)?(?:生产|合成|产生|分泌|生成|制造|制备)(?:了|出)?(?:可测量的|可检测的|少量|大量)?(?P<object>.+)", sentence)
            if chinese_active and re.fullmatch(molecule + _CHINESE_PRODUCTION_CONTEXT, chinese_active["object"]):
                return True
            chinese_passive = re.fullmatch(rf"(?P<object>.+?)(?:由|是由){strain}(?:菌株)?(?:生产|合成|产生|分泌|生成|制造|制备){_CHINESE_PRODUCTION_CONTEXT}", sentence)
            return bool(chinese_passive and re.fullmatch(molecule, chinese_passive["object"]))
        if all(bound(name) for name in names):
            return True
    return False


def publication_aliases(source):
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", source.doi.strip(), flags=re.IGNORECASE).casefold()
    ids = ["doi:" + doi] if doi else []
    ids += ["pmid:" + source.pmid.strip()] if source.pmid.strip() else []
    ids += ["pmcid:" + source.pmcid.strip().upper()] if source.pmcid.strip() else []
    arxiv = str(source.metadata.get("arxiv_id", ""))
    if arxiv:
        ids.append("arxiv:" + re.sub(r"v\d+$", "", arxiv.casefold()))
    # Title is a fallback, never allowed to override contradictory stable IDs.
    title = re.sub(r"[^\w]+", " ", source.title.casefold()).strip()
    if title:
        ids.append("title:" + title)
    return ids


def public_source(source):
    parsed = urlsplit(source.url)
    return (_url(source.url) is not _MISSING and source.provider in {"europe_pmc", "pubmed", "arxiv", "semantic_scholar", "openalex"}
            and parsed.scheme == "https" and parsed.hostname in {
                "www.ebi.ac.uk", "europepmc.org", "pubmed.ncbi.nlm.nih.gov", "doi.org",
                "arxiv.org", "www.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
                "www.semanticscholar.org", "openalex.org"}
            and source.evidence_scope in {"abstract", "full_text"})


class ResearchFlow:
    def __init__(self, engine):
        self.engine = engine
        self.data = engine.state.research_flow = ResearchFlowState()
        total = engine.run.brief.budget.max_searches
        initial = 2 if total >= 4 else 1
        novelty = min(2, max(0, (total - initial) // 3))
        feedback = int(total - initial - novelty >= 2)
        self.data.stage_limits = {"landscape": initial, "novelty": novelty,
                                  "feedback": feedback, "supplement": total - initial - novelty - feedback,
                                  "full_text": min(2, engine.run.brief.budget.max_papers // 4)}
        self.data.stage_usage = dict.fromkeys(self.data.stage_limits, 0)
        self.stage = "landscape"
        self.active_query = None
        self.data.checklist = ["pending:source_inventory", "pending:landscape", "pending:claim_queries",
                               "pending:claim_review", "pending:revision_fresh_review", "pending:contract_validation"]

    def complete(self, item):
        self.data.checklist = [x.replace("pending:", "recorded:") if x.endswith(":" + item) else x
                               for x in self.data.checklist]

    def event(self, title, ids=(), *, status="completed", inputs=()):
        self.engine.event("supervisor" if self.stage == "landscape" else "reflection",
                          "research_artifact", status, title,
                          "有界执行产物；仅记录公开来源、主张和状态，不含内部推理。", ids,
                          input_ids=inputs)

    def ingest(self, sources, origin="retrieved"):
        """Group a study, retaining all provider/text/version snapshots and original IDs."""
        for source in sources:
            aliases = publication_aliases(source)
            kind = source.evidence_scope if source.evidence_scope in {"database", "local_document"} else "paper"
            if kind != "paper":
                aliases = [kind + ":" + source.source_id]
            strong = {a for a in aliases if not a.startswith("title:")}
            matches = []
            for group in self.data.publications:
                if group.kind != kind:
                    continue
                existing = {a for a in group.aliases if not a.startswith("title:")}
                strong_match = bool(strong.intersection(existing))
                title_match = bool(set(aliases).intersection(group.aliases))
                # No title-only merge when both snapshots carry disjoint stable IDs.
                if strong_match or (title_match and not (strong and existing)):
                    matches.append(group)
            if matches:
                group = matches[0]
                for other in matches[1:]:
                    group.aliases = list(dict.fromkeys(group.aliases + other.aliases))
                    group.snapshots.extend(other.snapshots)
                    self.data.publications.remove(other)
            else:
                group = PublicationGroup(paper_id="paper_" + digest(aliases or [source.source_id])[:16], kind=kind)
                self.data.publications.append(group)
            group.aliases = list(dict.fromkeys(group.aliases + aliases))
            snapshot_id = "snapshot_" + digest([source.source_id, source.provider, source.evidence_scope, sha(source.text)])[:16]
            duplicate = next((s for s in group.snapshots if s.snapshot_id == snapshot_id), None)
            if duplicate:
                if origin in {"saved", "library"}:
                    duplicate.origin = origin
                continue
            pub_types = str(source.metadata.get("publication_types", "")).casefold()
            version = str(source.metadata.get("version", source.metadata.get("arxiv_id", "")))[:80]
            group.snapshots.append(SourceSnapshot(
                snapshot_id=snapshot_id, source_id=source.source_id, provider=source.provider,
                title=source.title, url=source.url, retrieved_at=source.retrieved_at,
                scope=source.evidence_scope, doi=source.doi, pmid=source.pmid, pmcid=source.pmcid,
                version=version, year=str(source.metadata.get("year") or ""),
                authors=[a[:2000] for a in source.metadata.get("authors", [])[:100] if isinstance(a, str)]
                    if isinstance(source.metadata.get("authors"), list) else [],
                journal=source.metadata.get("journal", "")[:2000] if isinstance(source.metadata.get("journal", ""), str) else "",
                publication_status="preprint" if source.provider == "arxiv" or "preprint" in pub_types else "unknown",
                text_sha256=sha(source.text), origin=origin))

    def paper_for(self, source):
        for group in self.data.publications:
            for snapshot in group.snapshots:
                if snapshot.source_id == source.source_id and snapshot.text_sha256 == sha(source.text):
                    return group.paper_id, snapshot.snapshot_id
        return "", ""

    def inventory(self):
        """Only this repository's explicit libraries and this project's public sources."""
        from .background import BackgroundPackage, load_background_package
        from .intent_policy import is_disease_exploration, validate_intent_brief
        validate_intent_brief(self.engine.run.brief)
        if is_disease_exploration(self.engine.run.brief):
            self.engine.background_package = BackgroundPackage()
            self.data.library_status.append({"library": "task_intent_admission",
                "status": "disease_exploration_saved_context_excluded", "records": 0})
            self.data.limitations.append("疾病探索仅使用本轮公开检索；既有项目记忆、旧运行与候选专题库未载入。")
            self.complete("source_inventory")
            return
        root = Path(__file__).resolve().parents[1]
        self.engine.background_package = load_background_package(root, self.engine.run.brief)
        package = self.engine.background_package
        if package.receipt:
            added = self.engine.append_sources(package.sources)
            self.ingest(package.sources, "library")
            self.data.library_status.append({"library": "background:" + package.receipt["package_id"],
                "status": "verified_background_reuse_requires_review", "records": len(added),
                "observed_records": len(package.sources), "version": package.receipt["version"],
                "manifest_sha256": package.receipt["manifest_sha256"]})
            q = self.new_query("reuse", "background_package", "复用已启用的核对来源包，获取时间沿用原记录", provider="library")
            q.status, q.source_ids, q.reused_from = "reused", added, package.receipt["package_id"]
            q.stop_reason = "background_not_new_verification"
        for name in ("papers", "literature"):
            library = root / name
            if not library.is_dir() or library.is_symlink():
                self.data.library_status.append({"library": name, "status": "absent", "records": 0})
                continue
            loaded, skipped = 0, 0
            # Fixed file pattern, bounded reads; symlinks cannot escape the repository.
            for path in sorted(library.rglob("*.json"))[:20]:
                if path.is_symlink() or not path.resolve().is_relative_to(library.resolve()) or path.stat().st_size > 500000:
                    skipped += 1
                    continue
                try:
                    values = json.loads(path.read_text())
                    for value in (values if isinstance(values, list) else [values])[:20]:
                        source = SourceRecord.model_validate(value)
                        if public_source(source) and self.relevant(source):
                            source = self.reusable_source(source, origin="library_reuse")
                            added = self.engine.append_sources([source])
                            self.ingest([source], "library")
                            loaded += len(added)
                        else:
                            skipped += 1
                except (ValueError, OSError):
                    skipped += 1
            pdfs = sum(1 for p in library.rglob("*.pdf") if not p.is_symlink())
            self.data.library_status.append({"library": name, "status": "public_json_only_pdf_not_uploaded",
                                             "records": loaded, "skipped": skipped, "pdf_count": pdfs})
        count = 0
        for summary in self.engine.store.list_runs(self.engine.run.project_id)[:10]:
            if summary["run_id"] == self.engine.run.run_id or summary["status"] in {"running", "queued"}:
                continue
            previous = self.engine.store.load_run(self.engine.run.project_id, summary["run_id"])
            if previous.brief.disease != self.engine.run.brief.disease or previous.brief.phenotype != self.engine.run.brief.phenotype:
                continue
            reused = [self.reusable_source(s, origin="saved_reuse") for s in previous.sources if public_source(s) and self.relevant(s)]
            if reused:
                added = self.engine.append_sources(reused)
                self.ingest(reused, "saved")
                count += len(added)
                q = self.new_query("reuse", "saved_sources", "复用已存公开来源，获取时间沿用原检索记录", provider="saved")
                q.status, q.source_ids, q.reused_from = "reused", [s.source_id for s in reused], previous.run_id
                q.stop_reason = "saved_not_new_verification"
        self.data.library_status.append({"library": "current_project_public_sources", "status": "bounded_reuse", "records": count})
        self.complete("source_inventory")

    def reusable_source(self, source, *, origin="saved_reuse"):
        from .background import relevant_excerpt
        safe = SourceRecord.model_validate(public_literature_source(source))
        # Only a bounded excerpt is copied into new run context; a local full
        # paper is never uploaded by this inventory. Original snapshot remains
        # in the source run/library, identified by its original text hash.
        start, end = relevant_excerpt(source, [self.engine.run.brief.disease,
            getattr(self.engine, "disease_english", ""), *self.engine.run.brief.existing_candidates,
            self.engine.run.brief.phenotype])
        safe.text = safe.text[start:end]
        safe.metadata.update(original_text_sha256=sha(source.text), local_excerpt_only=True,
            original_evidence_scope=source.evidence_scope, acquisition_origin=origin,
            excerpt_start=start, excerpt_end=end, excerpt_sha256=sha(safe.text),
            excerpt_selection="bounded_continuous_result_window")
        if source.evidence_scope == "full_text":
            safe.evidence_scope = "abstract"
        return safe

    def relevant(self, source):
        # Only already-frozen task names/aliases; no translation or new lookup.
        aliases = getattr(self.engine, "disease_aliases", ())
        aliases = aliases if isinstance(aliases, (tuple, list)) else ()
        terms = [self.engine.run.brief.disease, getattr(self.engine, "disease_english", ""),
                 *aliases[:10], *self.engine.run.brief.existing_candidates]
        return any(norm(t) in norm(source.title + " " + source.text) for t in terms if t)

    def new_query(self, stage, purpose, query, **kwargs):
        q = QueryReceipt(query_id="query_" + str(len(self.data.queries) + 1).zfill(4),
                         stage=stage, purpose=purpose, query=query, **kwargs)
        self.data.queries.append(q)
        return q

    def allowed(self, stage):
        return self.data.stage_usage[stage] < self.data.stage_limits[stage]

    def before_search(self, query):
        q = self.active_query or self.new_query(self.stage, "landscape" if self.stage == "landscape" else "model_gap", query)
        q.retrieved_at = utc_now()
        if not self.allowed(self.stage):
            q.status, q.stop_reason = "skipped", "stage_quota_reserved_for_later_stages"
            raise BudgetExceeded("Research stage query quota exhausted")
        self.data.stage_usage[self.stage] += 1
        q.status = "running"
        return q

    def search_finished(self, q, sources, limit, *, error=False):
        if error:
            q.status, q.stop_reason = "unavailable", "provider_failure"
        else:
            self.ingest(sources)
            q.source_ids = list(dict.fromkeys(s.source_id for s in sources[:limit]))
            q.before_dedup = len(sources[:limit])
            q.after_dedup = len({self.paper_for(s)[0] for s in sources[:limit]})
            envelope = sources[0].metadata.get("search_scope", {}) if sources else getattr(self.engine.sources.source, "last_literature_scope", {})
            q.total_hits = envelope.get("total_hits")
            q.facet, q.sort = str(envelope.get("facet", "")), str(envelope.get("sort", ""))
            q.query_origin = str(envelope.get("query_origin", ""))
            q.pagination = str(envelope.get("pagination", ""))
            q.hit_range_start = envelope.get("hit_range_start") if type(envelope.get("hit_range_start")) is int else None
            q.hit_range_end = envelope.get("hit_range_end") if type(envelope.get("hit_range_end")) is int else None
            q.has_more = envelope.get("has_more") if type(envelope.get("has_more")) is bool else None
            truncated = q.total_hits > len(sources[:limit]) if isinstance(q.total_hits, int) else len(sources) >= limit
            q.status = "truncated" if truncated else ("ok" if sources else "empty")
            q.coverage = "bounded_subset" if truncated else ("returned_query_batch" if q.total_hits is not None else "total_unknown")
            q.stop_reason = "return_limit" if truncated else ("no_records" if not sources else "single_batch")
        self.engine.persist()

    def full_text(self, source):
        q = self.new_query("full_text", "critical_open_access_read", source.source_id,
                           scope="full_text", source_ids=[source.source_id],
                           selection_reason=source.metadata.get("selection_reason", ""),
                           coverage_gaps=source.metadata.get("coverage_gaps", []))
        if not source.pmcid or source.metadata.get("is_open_access") != "Y":
            q.status, q.stop_reason = "skipped", "not_recorded_open_access"
            return None
        if source.metadata.get("local_excerpt_only"):
            q.status, q.stop_reason = "reused", "saved_excerpt_not_new_full_text"
            return None
        if not self.allowed("full_text"):
            q.status, q.stop_reason = "skipped", "full_text_quota"
            return None
        self.data.stage_usage["full_text"] += 1
        self.ingest([source])  # abstract remains in the map even if the runtime source is upgraded
        q.retrieved_at = utc_now()
        http = getattr(self.engine.sources, "http", None)
        before, before_cache = getattr(http, "request_count", 0), getattr(http, "cache_hits", 0)
        try:
            value = self.engine.sources.source.full_text(source)
            q.status, q.stop_reason = ("ok", "open_access_body") if value else ("empty", "body_unavailable")
            if value:
                self.ingest([value])
            return value
        except BudgetExceeded:
            q.status, q.stop_reason = "budget_exhausted", "shared_budget"
            raise
        except TimeoutError:
            q.status, q.stop_reason = "budget_exhausted", "deadline"
            raise
        except Exception:
            q.status, q.stop_reason = "unavailable", "full_text_failure"
            raise
        finally:
            q.request_count = max(0, getattr(http, "request_count", 0) - before)
            q.cache_hits = max(0, getattr(http, "cache_hits", 0) - before_cache)
            self.engine.persist()

    def claims_for(self, h):
        return [c for c in self.data.claims if (c.hypothesis_id, c.version) == (h.hypothesis_id, h.version)]

    def canonical_claim_texts(self, h):
        anchor = self.engine.state.anchor
        return {
            "main": h.claim,
            "disease_phenotype": f"{anchor.disease} / {anchor.phenotype or '表型未指定'}：{h.endpoint}",
            "mechanism_direction": h.mechanism or "机制与方向未知",
            "intervention": f"{', '.join(h.molecule_names) or '分子未指定'} 在 {h.condition} 下的干预与 {h.endpoint}",
            "chassis_supply": f"{h.chassis_name} 在 {h.condition} 下对 {', '.join(h.molecule_names) or '分子未知'} 的供给待验证",
            "delivery_exposure": f"{self.engine.run.brief.delivery_context or '递送未指定'} / {h.endpoint} 的暴露待验证",
        }

    def register(self, h):
        if self.claims_for(h):
            return
        values = self.canonical_claim_texts(h)
        for kind, text in values.items():
            self.data.claims.append(ClaimRecord(claim_id=f"claim:{h.hypothesis_id}:v{h.version}:{kind}",
                hypothesis_id=h.hypothesis_id, version=h.version, kind=kind, text=text,
                condition=h.condition, endpoint=h.endpoint,
                gaps=["需要逐主张支持/反证与研究系统、条件、终点适用性审查"]))

    def compile_queries(self, cards):
        now = datetime.now(UTC).date()
        recent = (now - timedelta(days=730)).isoformat()
        disease = self.engine.disease_english
        for h in cards:
            self.register(h)
            for c in self.claims_for(h):
                if c.kind == "main":
                    continue  # main is adjudicated by the exact-version review, other layers get search plans
                molecule = " ".join(h.molecule_names[:2])
                target = " ".join(h.target_symbols[:2])
                seed = {"disease_phenotype": disease, "mechanism_direction": target or h.mechanism,
                        "intervention": molecule, "chassis_supply": h.chassis_name + " " + molecule,
                        "delivery_exposure": molecule + " exposure"}[c.kind]
                seed = seed.replace('"', ' ')[:140].strip()
                if not seed:
                    c.gaps.append("缺少可核查检索实体，未执行泛搜")
                    continue
                for purpose, suffix in (("support", '(mechanism OR intervention OR production)'),
                                        ("contrary", '("no effect" OR ineffective OR adverse OR alternative)')):
                    prefix = f'"{seed}"' if c.kind == "chassis_supply" else f'"{disease[:100]}" AND "{seed}"'
                    q = self.new_query("supplement", purpose, f'{prefix} AND {suffix}',
                        claim_ids=[c.claim_id], hypothesis_id=h.hypothesis_id, version=h.version)
                    c.query_ids.append(q.query_id)
            q = self.new_query("novelty", "recent_overlap", f'"{disease[:100]}" AND "{(h.molecule_names[0] if h.molecule_names else h.mechanism)[:100]}" AND FIRST_PDATE:[{recent} TO {now.isoformat()}]',
                claim_ids=[c.claim_id for c in self.claims_for(h)], hypothesis_id=h.hypothesis_id,
                version=h.version, date_from=recent, date_to=now.isoformat())
            for c in self.claims_for(h):
                c.query_ids.append(q.query_id)
            self.data.novelty.append(NoveltyReceipt(novelty_id=f"novelty:{h.hypothesis_id}:v{h.version}",
                hypothesis_id=h.hypothesis_id, version=h.version, query_ids=[q.query_id],
                unconfirmed_difference=h.novelty.difference,
                limitations=["近期两年查询；保留已有基础来源，未覆盖全部近期工作", "本次检索结果及其范围随查新记录保存"]))
        self.complete("claim_queries")

    def run_queries(self, stage):
        self.stage = stage
        plans = [q for q in self.data.queries if q.stage == stage and q.status == "planned"]
        # Round-robin across hypotheses AND claim layers, alternating purpose.
        # Three default slots cover mechanism/support, intervention/contrary,
        # chassis/support. Deferred plans remain explicit, never imply coverage.
        if stage == "supplement":
            kinds = ["mechanism_direction", "intervention", "chassis_supply", "delivery_exposure", "disease_phenotype"]
            ids = sorted({q.hypothesis_id for q in plans})
            ordered = []
            for purpose in ("support", "contrary"):
                for i in range(len(kinds) * max(1, len(ids))):
                    kind, hid = kinds[i % len(kinds)], ids[i % len(ids)]
                    target_purpose = purpose if i % 2 == 0 else ("contrary" if purpose == "support" else "support")
                    match = next((q for q in plans if q.hypothesis_id == hid and q.purpose == target_purpose
                        and any(c.kind == kind and c.claim_id in q.claim_ids for c in self.data.claims)
                        and q not in ordered), None)
                    if match:
                        ordered.append(match)
            plans = ordered + [q for q in plans if q not in ordered]
        for q in plans:
            if not self.allowed(stage):
                q.status, q.stop_reason = "skipped", "stage_quota"
                continue
            self.active_query = q
            before = {s.source_id for s in self.engine.run.sources}
            try:
                result, _ = self.engine.tool(ToolRequest(name="europepmc_search",
                    arguments={"query": q.query[:500], "limit": min(3, self.engine.run.brief.budget.max_papers)}))
                q.call_ids = [result.call_id] if result.call_id else []
                q.request_count, q.cache_hits = result.request_count, result.cache_hits
                # memo has no new retrieval, and its original source attribution remains explicit
                if q.status == "planned" or not q.retrieved_at:
                    q.status, q.stop_reason = "reused", "memo_not_new_evidence"
                    q.request_count, q.cache_hits = 0, 0
                    q.reused_from = result.call_id
                q.source_ids = [s.source_id for s in result.sources if s.source_id in {s.source_id for s in self.engine.run.sources}]
                if result.status in {"unavailable", "invalid", "budget_exhausted"}:
                    q.status, q.stop_reason = result.status, result.stop_reason
                if len(q.source_ids) < len(result.sources):
                    q.coverage, q.stop_reason = "paper_quota_subset", "paper_quota"
            except BudgetExceeded:
                q.status, q.stop_reason = "budget_exhausted", "shared_budget"
            finally:
                self.active_query = None
                self.engine.persist()
            self.event("保存逐主张检索状态", [q.query_id, *q.source_ids], inputs=q.claim_ids)
            if q.status == "budget_exhausted":
                for pending in plans:
                    if pending.status == "planned":
                        pending.status, pending.stop_reason = "skipped", "shared_budget"
                break
            if {s.source_id for s in self.engine.run.sources} - before:
                self.data.limitations.append("补检索新来源仅为观察；需要精确片段与逐主张语义审查后使用。")
        if stage == "novelty":
            self.update_novelty()
        self.engine.persist()

    def update_novelty(self):
        for n in self.data.novelty:
            queries = [q for q in self.data.queries if q.query_id in n.query_ids]
            n.closest_source_ids = list(dict.fromkeys(s for q in queries for s in q.source_ids))
            n.status = "overlap_candidates" if n.closest_source_ids else "insufficient_search"
            n.overlap = ["检索命中是最接近工作候选；机制/对象/菌株/条件/端点差异须逐主张确认"]
            for h in self.engine.state.hypotheses:
                if (h.hypothesis_id, h.version) != (n.hypothesis_id, n.version):
                    continue
                h.novelty.closest_source_ids = n.closest_source_ids
                h.novelty.status = "insufficient_search"  # novelty verdicts require search-scope and prior-work comparisons
                h.novelty.search_scope = "; ".join(f"{q.query} [{q.status}; {q.stop_reason}]" for q in queries)
            self.engine.state.novelty_searches = [q.model_dump() for q in self.data.queries if q.stage == "novelty"]

    def observation_relation_valid(self, h, claim, evidence, source, relation):
        """Validate the complete v2 relation against deterministic source-projection rules."""
        from .candidate_assessments import matching_candidates, names_in_quote
        if (relation is None or relation.schema_version != 2 or relation.relation != "supports"
                or claim.relation_schema_version != 2 or claim.assessment_scope != "source_observation"
                or claim.kind != "main" or relation.claim_id != claim.claim_id
                or (relation.hypothesis_id, relation.version) != (h.hypothesis_id, h.version)
                or (claim.hypothesis_id, claim.version) != (h.hypothesis_id, h.version)
                or (relation.evidence_id, relation.source_id) != (evidence.evidence_id, evidence.source_id)
                or source.source_id != evidence.source_id or not evidence.validated_span
                or source.evidence_scope not in {"abstract", "full_text"}
                or not evidence.location.strip() or not evidence.quote
                or source.text.count(evidence.quote) != 1
                or claim.text != source_observation_statement(source.source_id, evidence.quote)
                or h.claim != claim.text
                or (relation.study_system, relation.condition, relation.endpoint)
                   != (evidence.study_system, evidence.condition, evidence.endpoint)
                or (claim.condition, claim.endpoint) != (evidence.condition, evidence.endpoint)
                or (h.condition, h.endpoint) != (claim.condition, claim.endpoint)
                or any(norm(value) in {"", "unknown", "未知", "未指定培养与供给条件"}
                       for value in (evidence.study_system, evidence.condition, evidence.endpoint))):
            return False
        if (sum(s.source_id == source.source_id for s in self.engine.run.sources) != 1
                or sum(e.evidence_id == evidence.evidence_id for e in self.engine.run.evidence) > 1
                or sum((card.hypothesis_id, card.version) == (h.hypothesis_id, h.version)
                       for card in self.engine.state.hypotheses) != 1):
            return False
        # The source-report exception is unavailable when scope labels cross a
        # human/animal/cell boundary. A source containing mixed systems abstains.
        brief_species = self.engine.run.brief.species
        anchor_species = self.engine.state.anchor.species
        requested = observation_systems(brief_species)
        recorded = observation_systems(evidence.study_system)
        source_systems = observation_systems(source.text)
        quote_systems = observation_systems(evidence.quote)
        subjects, unresolved_subjects = observation_study_objects(source.text)
        if (norm(brief_species) != norm(anchor_species)
                or requested not in ({"human"}, {"mouse"}, {"rat"})
                or recorded != requested or source_systems != requested
                or subjects != requested or unresolved_subjects
                or _UNCERTAIN_OBJECT.search(evidence.study_system)
                or (quote_systems and quote_systems != requested)):
            return False
        matches = [c for c in self.engine.run.candidates
                   if relation.entity_id == (c.entity_id or c.candidate_id)]
        if len(matches) != 1 or matches[0].role == "compound_class" or not h.molecule_names:
            return False
        candidate = matches[0]
        if not names_in_quote(candidate, evidence.quote):
            return False
        for name in h.molecule_names:
            resolved = matching_candidates(self.engine.run.candidates, [name])
            if len(resolved) != 1 or resolved[0] is not candidate:
                return False
        return True

    def observation_supports(self, h, claim, refs):
        if claim.relation_schema_version != 2 or claim.assessment_scope != "source_observation":
            return False
        sources = {s.source_id: s for s in self.engine.run.sources}
        matching = []
        for e in refs:
            source = sources.get(e.source_id)
            for link in self.data.links:
                if ((link.claim_id, link.hypothesis_id, link.version, link.evidence_id, link.source_id)
                        == (claim.claim_id, h.hypothesis_id, h.version, e.evidence_id, e.source_id)
                        and source and self.observation_relation_valid(h, claim, e, source, link.claim_relation)):
                    matching.append(link)
        # One source-anchored report; cross-study synthesis uses a separate claim.
        return len(refs) == 1 and len(matching) == 1

    def adjudicate(self, cards, drafts, evidence_drafts=(), prior_work=()):
        from .evidence import validate_evidence
        if self.engine.run.status.value not in {"queued", "running"}:
            raise ValueError("Finalized research evidence is read-only")
        accepted, rejected = validate_evidence([e.model_dump() for e in evidence_drafts], self.engine.run.sources)
        original = {e.evidence_id: e for e in self.engine.run.evidence}
        collisions = [e.evidence_id for e in accepted if e.evidence_id in original and e.model_dump() != original[e.evidence_id].model_dump()]
        accepted = [e for e in accepted if e.evidence_id not in collisions]
        evidence = {**original, **{e.evidence_id: e for e in accepted}}
        sources = {s.source_id: s for s in self.engine.run.sources}
        known = {c.claim_id: c for h in cards for c in self.claims_for(h)}
        supplied = {}
        for draft in drafts:
            supplied[draft.claim_id] = None if draft.claim_id in supplied else draft
        used_new = set()
        for claim_id, c in known.items():
            h = next(h for h in cards if (h.hypothesis_id, h.version) == (c.hypothesis_id, c.version))
            draft = supplied.get(claim_id)
            if draft is None or (draft.hypothesis_id, draft.version) != (c.hypothesis_id, c.version):
                c.state = "unknown"
                c.gaps.append("缺少唯一的逐主张/同版本审查；不继承其他版本或主张结论")
                continue
            r = ClaimReview(**draft.model_dump(), review_id="claim_review:" + claim_id)
            c.relation_schema_version = draft.relation_schema_version
            c.assessment_scope = draft.claim_scope
            relations = {}
            for relation in draft.evidence_relations:
                relations[relation.evidence_id] = None if relation.evidence_id in relations else relation
            query_sources = {s for q in self.data.queries if claim_id in q.claim_ids for s in q.source_ids}
            old_refs = set(h.evidence_ids + h.opposing_evidence_ids)
            for eid in dict.fromkeys(draft.evidence_ids):
                e = evidence.get(eid)
                source = sources.get(e.source_id) if e else None
                # Both attribution and the original continuous span must pass. A source
                # returned for another claim is never silently attached here.
                if not e or not e.validated_span or not source or (eid not in old_refs and e.source_id not in query_sources):
                    r.rejected_evidence_ids.append(eid)
                    continue
                start = source.text.find(e.quote)
                if start < 0:  # normalized span matches are insufficient for positional receipts
                    r.rejected_evidence_ids.append(eid)
                    continue
                paper_id, snapshot_id = self.paper_for(source)
                if not paper_id:
                    self.ingest([source]); paper_id, snapshot_id = self.paper_for(source)
                qids = [q.query_id for q in self.data.queries if claim_id in q.claim_ids and e.source_id in q.source_ids]
                relation = relations.get(eid)
                verified_relation = relation.model_copy(deep=True) if self.observation_relation_valid(h, c, e, source, relation) else None
                if eid in relations and verified_relation is None:
                    r.rejected_relation_ids.append(eid)
                self.data.links.append(EvidenceLink(claim_id=claim_id, hypothesis_id=c.hypothesis_id,
                    version=c.version, evidence_id=eid, source_id=e.source_id, paper_id=paper_id,
                    snapshot_id=snapshot_id, query_ids=qids, polarity=e.polarity, scope=source.evidence_scope,
                    study_system=e.study_system, condition=e.condition, endpoint=e.endpoint,
                    span_start=start, span_end=start + len(e.quote), span_sha256=sha(e.quote), location=e.location,
                    claim_relation=verified_relation))
                r.valid_evidence_ids.append(eid)
                if eid not in original:
                    used_new.add(eid)
            c.evidence_ids = r.valid_evidence_ids
            if not r.valid_evidence_ids:
                r.verdict = "unknown"
                r.gaps.append("没有通过来源/片段/主张归属校验的引用")
            refs = [evidence[eid] for eid in r.valid_evidence_ids]
            r.rejected_relation_ids = list(dict.fromkeys(r.rejected_relation_ids +
                [eid for eid in relations if eid not in r.valid_evidence_ids]))
            observation = c.relation_schema_version == 2 and c.assessment_scope == "source_observation"
            if observation:
                if r.verdict == "supported" and (r.rejected_relation_ids or not self.observation_supports(h, c, refs)):
                    r.verdict = "context_only" if refs else "unknown"
                    r.gaps.append("原文观察关系核验失败：完整逐字投影、实体、物种、条件、终点及唯一版本均须匹配")
            else:
                if r.verdict == "supported" and not any(e.polarity == "supports" for e in refs):
                    r.verdict = "context_only"
                    r.gaps.append("该推断主张的方向支持待补充；mixed/negative 按各自来源范围记录")
                if any(e.polarity in {"opposes", "mixed"} for e in refs):
                    r.verdict = "contested" if r.verdict == "supported" else r.verdict
                if r.verdict == "supported" and not self.scope_supports(h, c, refs):
                    r.verdict = "context_only"
                    r.gaps.append("供给依据需匹配研究系统、条件、终点和任务菌株；unknown/待验证状态同样进入供给审查")
            c.state = r.verdict
            pending_note = "需要逐主张支持/反证与研究系统、条件、终点适用性审查"
            c.gaps = list(dict.fromkeys([gap for gap in c.gaps if gap != pending_note] + r.gaps))
            self.data.claim_reviews.append(r)
        for eid, record in evidence.items():
            if eid in used_new:
                self.engine.run.evidence.append(record)
        self.sync_candidate_evidence(cards)
        if rejected or collisions:
            self.data.limitations.append(f"本次逐主张审查拒绝 {len(rejected) + len(collisions)} 条缺失片段或冲突证据ID；已有证据未改写，未公开原始无效输入")
        for item in prior_work:
            n = next((n for n in self.data.novelty if (n.hypothesis_id, n.version) == (item.hypothesis_id, item.version)), None)
            if n and item.source_id in n.closest_source_ids and item.source_id in sources:
                n.prior_work.append(item)
                n.status = "scoped_overlap_reviewed"
        self.complete("claim_review")
        self.event("保存逐主张支持、反证与外推审查", [r.review_id for r in self.data.claim_reviews
                    if any((r.hypothesis_id, r.version) == (h.hypothesis_id, h.version) for h in cards)],
                   inputs=[c.claim_id for c in known.values()])

    def reviewed_evidence_ids(self, h):
        """Evidence considered for this exact version, separate from generator citations.

        A unique claim review, its canonical claim and its source/span receipt must
        agree. New evidence is not inherited by another version or another claim.
        Mixed findings remain visible both as context and as contrary evidence.
        """
        if self.integrity_errors():
            return {"evidence_ids": [], "opposing_evidence_ids": [], "associations": []}
        result = {"evidence_ids": [], "opposing_evidence_ids": [], "associations": []}
        evidence = {e.evidence_id: e for e in self.engine.run.evidence}
        query_lookup = {q.query_id: q for q in self.data.queries}
        original_refs = set(h.evidence_ids + h.opposing_evidence_ids)
        for claim in self.claims_for(h):
            reviews = [r for r in self.data.claim_reviews if r.claim_id == claim.claim_id
                       and (r.hypothesis_id, r.version) == (h.hypothesis_id, h.version)]
            if len(reviews) != 1:
                continue
            review = reviews[0]
            links = [link for link in self.data.links if link.claim_id == claim.claim_id
                     and (link.hypothesis_id, link.version) == (h.hypothesis_id, h.version)
                     and link.evidence_id in review.valid_evidence_ids and self.valid_link(link)]
            for link in links:
                queries = [query_lookup.get(qid) for qid in link.query_ids]
                attributed = all(q and claim.claim_id in q.claim_ids and link.source_id in q.source_ids
                    and (q.hypothesis_id, q.version) == (h.hypothesis_id, h.version) for q in queries)
                if not attributed or (link.evidence_id not in original_refs and not queries):
                    continue
                e = evidence[link.evidence_id]
                if e.polarity != "opposes":
                    result["evidence_ids"].append(e.evidence_id)
                if e.polarity in {"opposes", "mixed"}:
                    result["opposing_evidence_ids"].append(e.evidence_id)
                result["associations"].append({"claim_id": claim.claim_id, "claim_kind": claim.kind,
                    "hypothesis_id": h.hypothesis_id, "version": h.version,
                    "review_id": review.review_id, "verdict": review.verdict,
                    "evidence_id": e.evidence_id, "source_id": e.source_id,
                    "query_ids": list(link.query_ids), "polarity": e.polarity,
                    "relation_schema_version":claim.relation_schema_version,"assessment_scope":claim.assessment_scope,
                    "claim_relation":link.claim_relation.relation if link.claim_relation else "unassessed",
                    "relation_basis":"exact_source_report_only" if link.claim_relation else "legacy_global_polarity"})
        for key in ("evidence_ids", "opposing_evidence_ids"):
            result[key] = list(dict.fromkeys(result[key]))
        return result

    def sync_candidate_evidence(self, cards):
        """Associate current evidence without changing role, priority or old cards.

        Candidate attribution requires a unique exact recorded molecule alias and
        an intervention/main claim whose reviewed quote names that same entity.
        A hypothesis ID or general disease background alone is insufficient.
        """
        from .candidate_assessments import matching_candidates, names_in_quote
        if self.engine.run.status.value not in {"queued", "running"}:
            return
        evidence = {e.evidence_id: e for e in self.engine.run.evidence}
        latest = {}
        for card in self.engine.state.hypotheses:
            latest[card.hypothesis_id] = max(latest.get(card.hypothesis_id, 0), card.version)
        for h in cards:
            if h.version != latest.get(h.hypothesis_id):
                continue
            scope = self.reviewed_evidence_ids(h)
            for candidate in matching_candidates(self.engine.run.candidates, h.molecule_names):
                ids = [a["evidence_id"] for a in scope["associations"]
                       if a["claim_kind"] in {"main", "intervention"}
                       and names_in_quote(candidate, evidence[a["evidence_id"]].quote)]
                candidate.evidence_ids = list(dict.fromkeys(candidate.evidence_ids + ids))

    def main_review_state(self, h):
        if self.integrity_errors():
            return "invalid_receipts"
        main = next((c for c in self.claims_for(h) if c.kind == "main"), None)
        rs = [r for r in self.data.claim_reviews if main and r.claim_id == main.claim_id
              and (r.hypothesis_id, r.version) == (h.hypothesis_id, h.version)]
        if len(rs) != 1:
            return "unreviewed" if not rs else "ambiguous_review"
        if not rs[0].valid_evidence_ids:
            return "reviewed_evidence_missing"
        valid = {link.evidence_id for link in self.data.links if link.claim_id == main.claim_id
                 and (link.hypothesis_id, link.version) == (h.hypothesis_id, h.version) and self.valid_link(link)}
        if not set(rs[0].valid_evidence_ids).issubset(valid):
            return "invalid_receipts"
        evidence = {e.evidence_id: e for e in self.engine.run.evidence}
        refs = [evidence[eid] for eid in rs[0].valid_evidence_ids if eid in evidence]
        if rs[0].verdict != "supported" or not self.scope_supports(h, main, refs):
            return "reviewed_not_supported"
        if main.assessment_scope == "source_observation":
            return "source_observation_supported"
        return "supported"

    def main_review_gap(self, h):
        return {
            "unreviewed": "主主张尚无同版本逐主张审查；需要独立审查与可追溯引用",
            "ambiguous_review": "主主张存在重复同版本审查；需要消除歧义后再判断支持",
            "invalid_receipts": "主张审查收据或来源关联不一致；需要核对原文片段与版本归属",
            "reviewed_evidence_missing": "主主张已完成同版本审查，但缺少通过来源与片段校验的引用；需要补齐证据",
            "reviewed_not_supported": "主主张已完成同版本审查，现有证据未支持该主张；需要针对反证、适用条件与终点缺口补证",
            "source_observation_supported": "窄原文观察已完成同版本审查并获支持；预测、干预适用性与整卡疗效结论各自需要支持证据",
            "supported": "",
        }[self.main_review_state(h)]

    def main_reviewed(self, h):
        return self.main_review_state(h) == "supported"

    def scope_supports(self, h, claim, refs):
        """Shared mechanical applicability gate for adjudication and saved views."""
        if claim.assessment_scope == "source_observation":
            return self.observation_supports(h, claim, refs)
        sources = {s.source_id: s for s in self.engine.run.sources}
        refs = [e for e in refs if e.polarity == "supports"]
        text = norm(claim.text)
        names = [norm(name) for name in h.molecule_names if name.strip()]
        # Explicit strain+molecule dependence also triggers the conservative
        # gate, even when free text uses an unrecognised supply verb/nominal form.
        names_strain_and_molecule = bool(names and norm(h.chassis_name)
            and norm(h.chassis_name) in text and all(name in text for name in names))
        requires_supply = claim.kind == "chassis_supply" or supply_dependency(text) or names_strain_and_molecule
        if not requires_supply and claim.kind not in {"main", "mechanism_direction", "intervention", "chassis_supply", "delivery_exposure"}:
            return bool(refs)
        direct = [e for e in refs if e.source_id in sources
                  and sources[e.source_id].evidence_scope in {"abstract", "full_text", "local_document"}
                  and norm(e.condition) == norm(claim.condition) and norm(e.endpoint) == norm(claim.endpoint)
                  and norm(claim.condition) not in {"unknown", "未知", "未指定培养与供给条件"}]
        if requires_supply:
            direct = [e for e in direct if affirmative_supply_span(h, e)]
        return bool(direct)

    def valid_link(self, link):
        sources = {s.source_id: s for s in self.engine.run.sources}
        evidence = {e.evidence_id: e for e in self.engine.run.evidence}
        source, e = sources.get(link.source_id), evidence.get(link.evidence_id)
        if not source or not e or not e.validated_span or e.source_id != link.source_id:
            return False
        if link.claim_relation is not None:
            cards = [h for h in self.engine.state.hypotheses if (h.hypothesis_id,h.version) == (link.hypothesis_id,link.version)]
            claims = [c for c in self.data.claims if c.claim_id == link.claim_id]
            if (len(cards) != 1 or len(claims) != 1
                    or not self.observation_relation_valid(cards[0],claims[0],e,source,link.claim_relation)):
                return False
        span = source.text[link.span_start:link.span_end]
        group = next((p for p in self.data.publications if p.paper_id == link.paper_id), None)
        snapshot = next((s for s in group.snapshots if s.snapshot_id == link.snapshot_id), None) if group else None
        return bool(span and span == e.quote and sha(span) == link.span_sha256
            and (link.polarity, link.study_system, link.condition, link.endpoint, link.location, link.scope)
            == (e.polarity, e.study_system, e.condition, e.endpoint, e.location, source.evidence_scope)
            and snapshot and snapshot.source_id == source.source_id and snapshot.text_sha256 == sha(source.text))

    def feedback(self, cards):
        """One reserved batch driven by recorded objections, no repeat-until-pass loop."""
        self.stage = "feedback"
        for h in cards:
            reviews = [r for r in self.engine.state.reviews if (r.hypothesis_id, r.version) == (h.hypothesis_id, h.version)]
            if not reviews or not (reviews[0].objections or reviews[0].required_evidence):
                continue
            pending = [q for q in self.data.queries if q.hypothesis_id == h.hypothesis_id and q.version == h.version
                       and q.stage == "supplement" and q.status == "skipped"]
            if not pending:
                continue
            q = pending[0]
            f = self.new_query("feedback", "recorded_review_gap", q.query, claim_ids=q.claim_ids,
                               hypothesis_id=h.hypothesis_id, version=h.version)
            for c in self.claims_for(h):
                if c.claim_id in f.claim_ids:
                    c.query_ids.append(f.query_id)
        self.run_queries("feedback")

    def revision(self, old, new, reviews):
        criticized = [r for r in self.data.claim_reviews if (r.hypothesis_id, r.version) == (old.hypothesis_id, old.version)
                      and (r.gaps or r.verdict != "supported")]
        for field in ("claim", "condition", "endpoint", "prediction", "validation_question", "inference_gaps"):
            if getattr(old, field) == getattr(new, field):
                continue
            self.data.revisions.append(RevisionReceipt(revision_id=f"revision:{new.hypothesis_id}:v{new.version}:{field}",
                hypothesis_id=new.hypothesis_id, from_version=old.version, to_version=new.version, field=field,
                before_sha256=digest(getattr(old, field)), after_sha256=digest(getattr(new, field)),
                claim_ids=[r.claim_id for r in criticized],
                review_ids=[f"review:{r.hypothesis_id}:v{r.version}" for r in reviews] + [r.review_id for r in criticized],
                evidence_ids=list(dict.fromkeys(e for r in criticized for e in r.valid_evidence_ids)),
                query_ids=[q.query_id for q in self.data.queries if q.hypothesis_id == old.hypothesis_id and q.version == old.version],
                reason="根据已记录同版本批评修订；字段差异由程序核对，语义修改说明见 changes，旧结论不继承"))
        self.register(new)
        # Prior searches are explicitly reused leads for v2, never fresh novelty verification.
        prior = next((n for n in self.data.novelty if (n.hypothesis_id, n.version) == (old.hypothesis_id, old.version)), None)
        # Feedback sources remain scoped to their original claim layer. Make
        # their reuse explicit for the corresponding new-version layer, so the
        # fresh reviewer can assess them without borrowing another claim's search.
        for c in self.claims_for(new):
            previous = next((p for p in self.claims_for(old) if p.kind == c.kind), None)
            sources = list(dict.fromkeys(s for query in self.data.queries
                if previous and previous.claim_id in query.claim_ids and query.version == old.version
                for s in query.source_ids))
            if not sources:
                continue
            reused = self.new_query("reuse", "revision_claim_sources", "前版对应主张查询来源；新版需要fresh review",
                hypothesis_id=new.hypothesis_id, version=new.version, claim_ids=[c.claim_id],
                source_ids=sources, provider="saved", status="reused", reused_from=previous.claim_id,
                stop_reason="source_reuse_not_new_review")
            c.query_ids.append(reused.query_id)
        q = self.new_query("reuse", "revision_prior_search", "修订版复用前版检索线索，需重新审查",
            hypothesis_id=new.hypothesis_id, version=new.version,
            claim_ids=[c.claim_id for c in self.claims_for(new)], provider="saved", status="reused",
            source_ids=prior.closest_source_ids if prior else [], reused_from=f"{old.hypothesis_id}:v{old.version}",
            stop_reason="one_revision_no_new_search_budget")
        for c in self.claims_for(new):
            c.query_ids.append(q.query_id)
        self.data.novelty.append(NoveltyReceipt(novelty_id=f"novelty:{new.hypothesis_id}:v{new.version}",
            hypothesis_id=new.hypothesis_id, version=new.version, query_ids=[q.query_id],
            closest_source_ids=q.source_ids, status="insufficient_search",
            unconfirmed_difference=new.novelty.difference,
            limitations=["修订版复用前版来源，近期检索状态沿用原记录", "必须重新逐主张审查"] ))
        self.update_novelty()

    def integrity_errors(self):
        """Canonical fields, unique IDs and both directions of the receipt graph."""
        errors = []
        for items, field in ((self.data.claims, "claim_id"), (self.data.queries, "query_id"),
                             (self.data.claim_reviews, "review_id")):
            values = [getattr(item, field) for item in items]
            if len(values) != len(set(values)):
                errors.append("duplicate_" + field)
        versions = {(h.hypothesis_id, h.version): h for h in self.engine.state.hypotheses}
        if len(versions) != len(self.engine.state.hypotheses):
            errors.append("duplicate_hypothesis_version")
        claims = {c.claim_id: c for c in self.data.claims}
        queries = {q.query_id: q for q in self.data.queries}
        for c in self.data.claims:
            h = versions.get((c.hypothesis_id, c.version))
            if not h or c.claim_id != f"claim:{c.hypothesis_id}:v{c.version}:{c.kind}":
                errors.append("invalid_claim_version:" + c.claim_id)
            elif (c.text, c.condition, c.endpoint) != (self.canonical_claim_texts(h)[c.kind], h.condition, h.endpoint):
                errors.append("claim_field_drift:" + c.claim_id)
            if len(c.query_ids) != len(set(c.query_ids)):
                errors.append("duplicate_claim_query:" + c.claim_id)
            for qid in c.query_ids:
                if qid not in queries or c.claim_id not in queries[qid].claim_ids:
                    errors.append("claim_query_reverse_link:" + c.claim_id)
        for q in self.data.queries:
            if len(q.claim_ids) != len(set(q.claim_ids)):
                errors.append("duplicate_query_claim:" + q.query_id)
            for cid in q.claim_ids:
                c = claims.get(cid)
                if not c:
                    errors.append("unknown_query_claim:" + q.query_id)
                elif (c.hypothesis_id, c.version) != (q.hypothesis_id, q.version):
                    errors.append("query_version_mismatch:" + q.query_id)
                elif q.query_id not in c.query_ids:
                    errors.append("query_claim_reverse_link:" + q.query_id)
        for r in self.data.claim_reviews:
            c = claims.get(r.claim_id)
            if (not c or (r.hypothesis_id, r.version) != (c.hypothesis_id, c.version)
                    or r.review_id != "claim_review:" + r.claim_id):
                errors.append("invalid_claim_review:" + r.review_id)
                continue
            evidence = {e.evidence_id: e for e in self.engine.run.evidence}
            valid_links = {link.evidence_id for link in self.data.links if link.claim_id == r.claim_id
                           and (link.hypothesis_id, link.version) == (r.hypothesis_id, r.version) and self.valid_link(link)}
            if (len(r.valid_evidence_ids) != len(set(r.valid_evidence_ids))
                    or not set(r.valid_evidence_ids).issubset(valid_links)
                    or c.evidence_ids != r.valid_evidence_ids or c.state != r.verdict
                    or c.relation_schema_version != r.relation_schema_version or c.assessment_scope != r.claim_scope):
                errors.append("claim_review_record_mismatch:" + r.review_id)
            for link in self.data.links:
                if (link.claim_id == r.claim_id and link.claim_relation is not None
                        and (link.hypothesis_id,link.version) == (r.hypothesis_id,r.version)):
                    requests = [relation for relation in r.evidence_relations if relation.evidence_id == link.evidence_id]
                    if len(requests) != 1 or requests[0] != link.claim_relation:
                        errors.append("claim_relation_receipt_mismatch:" + r.review_id)
            if r.verdict == "supported" and r.claim_scope == "source_observation" and r.rejected_relation_ids:
                errors.append("supported_observation_with_rejected_relation:" + r.review_id)
            h = versions.get((r.hypothesis_id, r.version))
            refs = [evidence[eid] for eid in r.valid_evidence_ids if eid in evidence]
            if r.verdict == "supported" and (not h or not self.scope_supports(h, c, refs)):
                errors.append("unsupported_claim_scope:" + r.review_id)
        for c in self.data.claims:
            if c.state == "supported":
                reviews = [r for r in self.data.claim_reviews if r.claim_id == c.claim_id
                           and (r.hypothesis_id, r.version) == (c.hypothesis_id, c.version)]
                if len(reviews) != 1 or reviews[0].verdict != "supported" or not reviews[0].valid_evidence_ids:
                    errors.append("supported_claim_without_review:" + c.claim_id)
        return list(dict.fromkeys(errors))

    def verify(self):
        """Machine diagnostic over receipts; referenced by the external CLI verifier too."""
        errors = self.integrity_errors()
        claim_ids = {c.claim_id for c in self.data.claims}
        source_lookup = {s.source_id: s for s in self.engine.run.sources}
        query_lookup = {q.query_id: q for q in self.data.queries}
        evidence_lookup = {e.evidence_id: e for e in self.engine.run.evidence}
        versions = {(h.hypothesis_id, h.version) for h in self.engine.state.hypotheses}
        if len(claim_ids) != len(self.data.claims):
            errors.append("duplicate_claim_id")
        for c in self.data.claims:
            if (c.hypothesis_id, c.version) not in versions or c.claim_id != f"claim:{c.hypothesis_id}:v{c.version}:{c.kind}":
                errors.append("invalid_claim_version:" + c.claim_id)
        for q in self.data.queries:
            if any(cid not in claim_ids for cid in q.claim_ids):
                errors.append("unknown_query_claim:" + q.query_id)
            if any((c.hypothesis_id, c.version) != (q.hypothesis_id, q.version)
                   for c in self.data.claims if c.claim_id in q.claim_ids):
                errors.append("query_version_mismatch:" + q.query_id)
        for c in self.data.claims:
            if any(qid not in query_lookup or c.claim_id not in query_lookup[qid].claim_ids for qid in c.query_ids):
                errors.append("claim_query_reverse_link:" + c.claim_id)
        for link in self.data.links:
            source = source_lookup.get(link.source_id)
            span = source.text[link.span_start:link.span_end] if source else ""
            evidence = evidence_lookup.get(link.evidence_id)
            if (not self.valid_link(link) or link.claim_id not in claim_ids or not span or sha(span) != link.span_sha256
                    or not evidence or not evidence.validated_span or evidence.source_id != link.source_id
                    or evidence.quote != span or evidence.polarity != link.polarity):
                errors.append("invalid_claim_span:" + link.claim_id)
            if not any(c.claim_id == link.claim_id and (c.hypothesis_id, c.version) == (link.hypothesis_id, link.version)
                       for c in self.data.claims):
                errors.append("link_version_mismatch:" + link.claim_id)
            for qid in link.query_ids:
                q = query_lookup.get(qid)
                if not q or link.claim_id not in q.claim_ids or link.source_id not in q.source_ids:
                    errors.append("invalid_query_attribution:" + link.claim_id)
        for r in self.data.claim_reviews:
            c = next((c for c in self.data.claims if c.claim_id == r.claim_id), None)
            refs = {link.evidence_id for link in self.data.links if link.claim_id == r.claim_id
                    and (link.hypothesis_id, link.version) == (r.hypothesis_id, r.version) and self.valid_link(link)}
            if not c or (c.hypothesis_id, c.version) != (r.hypothesis_id, r.version) or not set(r.valid_evidence_ids).issubset(refs):
                errors.append("invalid_claim_review:" + r.review_id)
        for stage, used in self.data.stage_usage.items():
            if used > self.data.stage_limits[stage]:
                errors.append("stage_quota:" + stage)
        for contract in self.engine.state.contracts:
            hs = [h for h in self.engine.state.hypotheses if (h.hypothesis_id, h.version) == (contract.hypothesis_id, contract.version)]
            if (not hs or not self.main_reviewed(hs[0])) and contract.status != "needs_evidence":
                errors.append("unreviewed_contract")
        errors = list(dict.fromkeys(errors))
        return {"protocol_version": self.data.protocol_version, "valid": not errors, "errors": errors,
                "claim_count": len(claim_ids), "query_count": len(query_lookup), "link_count": len(self.data.links)}

