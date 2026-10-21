/* Buildless local application. All user/source text is inserted as text, never HTML. */
"use strict";

const $ = id => document.getElementById(id);
const state = {project: "", run: "", selected: null, projects: [], runs: [], jobs: [],
  experiments: [], view: "research", epoch: 0, selectionEpoch: 0, poll: null, toast: null, submitting: false,
  catalogs: {tools: null, protocols: null}, catalogErrors: {}, catalogPromise: null,
  memory: [], agent: "", collaborationRun: "", eventLimit: 8, rendered: {}, renderContext: {}};
const VIEWS = {research: "New research", overview: "Overview", targets: "Protein targets",
  candidates: "Research candidates", context: "Context molecules", evidence: "Evidence & sources", files: "Target files",
  compute: "Compute handoff", experiments: "Experimental feedback", memory: "Project memory", settings: "Environment",
  collaboration: "Agent activity", hypotheses: "Hypotheses", contracts: "Research plans", tools: "Tools & protocols", demo: "HDAC2 workbench"};
const LABELS = {queued: "Queued", running: "Research in progress", completed: "Execution completed", partial: "Partial · gaps retained",
  failed: "Run failed", cancelled: "Cancelled", ready: "Inputs ready", needs_review: "Needs review", blocked: "Not ready",
  not_prepared: "Not prepared", prioritize: "Prioritize validation", exploratory: "Exploratory", hold: "On hold",
  inhibit: "Inhibit", activate: "Activate", modulate: "Modulate", unknown: "Unknown",
  supports: "Supports", opposes: "Opposes", mixed: "Mixed", context: "Context",
  abstract: "Abstract only", full_text: "Full text", database: "Database", local_document: "Local record",
  direct: "Direct-action hypothesis", indirect: "Indirect action", parallel: "Parallel mechanism",
  therapeutic_candidate: "Therapeutic intervention lead", symptom_management: "Symptom-management lead", biomarker: "Biomarker",
  risk_factor: "Risk-associated molecule", metabolic_context: "Metabolic context", compound_class: "Compound class",
  literature: "Literature route", literature_efficacy: "Literature route", target_screening: "Target-screening route",
  measured: "Measurement recorded", invalid_data: "Invalid data", qc_failed: "QC failed",
  insufficient_data: "Insufficient data", non_detection: "Not detected", interrupted: "Interrupted",
  skipped: "Skipped", proposed: "Proposed · awaiting review", reviewed: "Automatically reviewed", revised: "Revised · awaiting validation",
  needs_evidence: "Needs evidence", withheld: "Withheld", human_review_needed: "Human review required",
  completed_review: "Independent assessment reviewed", unreviewed: "Independent conclusion unreviewed",
  limited_source_findings: "Scoped source findings · awaiting validation", mixed_evidence: "Mixed evidence · scope retained",
  no_supported_benefit: "Benefit not supported within current scope",
  source_scope_only: "Source-reported scope only", population_uncertain: "Population applicability unconfirmed", nonhuman_or_unknown: "Nonhuman or unspecified system",
  source_claim_supported_pending_validation: "Specific claim supported by sources · validation pending",
  awaiting_measurement: "Awaiting measurement", user_supplied: "User-supplied candidate", synthesis: "Cross-source research inference",
  direct_evidence: "Direct evidence", adverse: "Adverse evidence", exact: "Exact strain match",
  other_strain: "Other strain", native_synthesis: "Native synthesis hypothesis", precursor_conversion: "Precursor-conversion hypothesis",
  exogenous: "Exogenous supply hypothesis", known_application: "Known application", potential_gap: "Potential research gap",
  insufficient_search: "Search insufficient · novelty unconfirmed", retain: "Retain for validation", revise: "Revise", withhold: "Withheld",
  left: "Left option prioritized", right: "Right option prioritized", tie: "Tie", insufficient: "Insufficient evidence",
  incomparable: "Different conditions · incomparable", success: "Query succeeded", empty: "Successful query · no results",
  unavailable: "Service unavailable", truncated: "Results truncated", error: "Call error", cached: "Cached result",
  rule_based: "Rule-based grouping", standard: "Classic dual route", co_scientist: "Co-Scientist collaborative research",
  ok: "Query succeeded", invalid: "Invalid parameters", budget_exhausted: "Budget exhausted",
  planned: "Plan recorded", unresolved: "Identity unresolved", ambiguous: "Multiple identity candidates", resolved: "Unique identity checked",
  mismatch: "Identity mismatch", not_run: "Not run", not_recorded: "Not recorded", pending: "Pending"};
const AGENT_ROLES = {supervisor: ["Supervisor", "Coordination & budget"], generation: ["Generation", "Multi-perspective generation"],
  proximity: ["Proximity", "Rule-based grouping"], reflection: ["Reflection", "Evidence reflection"],
  ranking: ["Ranking", "Pairwise ranking"], evolution: ["Evolution", "Evidence-guided revision"],
  meta_review: ["Meta-review", "Meta-review & contracts"], "meta-review": ["Meta-review", "Meta-review & contracts"]};
// Exact display aliases for known software-authored text only. Original records,
// scientific statements, quotes and user inputs remain untouched.
const SYSTEM_UI_TEXT = Object.freeze({
  "任务锚定": "Problem anchoring",
  "疾病、具体表型、物种、菌株、工程/资源边界、已有候选": "Disease, specific phenotype, species, strain, engineering/resource boundaries and existing candidates",
  "无法确定终点则保留澄清问题；不擅自改疾病或菌株": "Retain clarification questions when the endpoint is unclear; do not change the disease or strain",
  "文献地图与证据": "Literature map & evidence",
  "任务锚、已有文献线索、相反发现检索式": "Problem anchor, existing literature leads and counterevidence queries",
  "原总预算不变；默认8检索按地图2/逐主张3/近期2/反馈1分配，最多2开放全文": "Keep the original total budget: default eight searches allocated to landscape 2 / claim-level 3 / recent work 2 / feedback 1, with at most two open-access full texts",
  "无原文则不生成有来源结论": "Do not create source-supported conclusions without retrieved source text",
  "摘要不是完整论文": "Source reading scope is labelled as abstract or full text",
  "同研究跨源去重": "Deduplicate the same study across sources",
  "双视角假说生成": "Two-perspective hypothesis generation",
  "已审查证据、文献地图、用户给定候选": "Reviewed evidence, literature map and user-supplied candidates",
  "先保留1个必要视角；正式审查及Meta预算充足才加入第二独立视角，合计受配置假说上限约束": "Preserve one required perspective; add a second separate perspective only when formal-review and meta-review budgets permit, within the configured hypothesis limit",
  "仅机械去重，不由生成者做质量淘汰": "Use mechanical deduplication; the generator does not eliminate ideas by self-assessed quality",
  "用户给定候选不算自主发现": "Track candidate origin: user input or identification during the current research workflow",
  "保留候选来源：用户输入或当前研究流程识别": "Track candidate origin: user input or identification during the current research workflow",
  "逐主张查新": "Claim-level novelty search",
  "核心主张、实际分子/机制与研究条件": "Core claims, actual molecule/mechanism and study conditions",
  "默认预留2个近期查询；所有假说记录查询计划与未执行原因，共享原检索/HTTP/论文预算": "Reserve two recent-work queries by default; record query plans and skip reasons for all hypotheses within the original shared search/HTTP/paper budget",
  "未检出不是原创证据": "Novelty conclusions link to query scope and related-work comparisons",
  "无检索记录不得宣称有查新结论": "Novelty conclusions use recorded search results",
  "独立上下文反思": "Separate-context reflection",
  "完整假说、支持/反证、研究条件，不含生成者自我评分": "Full hypotheses, support/counterevidence and conditions, excluding generator self-scores",
  "1初始审查、最多3场同约束比较、1修订后复核": "One initial review, up to three same-constraint comparisons and one post-revision review",
  "证据不足/不可比较可弃权": "Abstain when evidence is insufficient or options are incomparable",
  "数据库注释不证明因果或生产": "Database annotations, causal evidence and production evidence are recorded separately",
  "一轮修订与研究合同": "One revision round & research plan",
  "任务锚、可追溯批评、证据缺口与实际检索观察": "Problem anchor, traceable critiques, evidence gaps and actual search observations",
  "最多1轮修订；新版不继承旧比较胜场": "At most one revision round; new versions do not inherit old comparison wins",
  "缺少修订依据就保留原版": "Retain the original version without a basis for revision",
  "新主张重新审查": "Re-review new claims",
  "无测量时参数值保持null": "Keep parameter values null when measurements are unavailable",
  "检索文献摘要和 DOI/PMID；不自动读取全文，不把摘要当作完整研究。": "Search abstracts and DOI/PMID identifiers; do not automatically retrieve full text or treat abstracts as complete studies.",
  "解析疾病或靶点名称；同名结果是待核对的身份线索。": "Resolve disease or target names; same-name matches are identity leads requiring review.",
  "检索疾病—靶点关联；关联分数不是干预疗效或因果概率。": "Retrieve disease-target associations and their sources; association scores follow the database definitions.",
  "按基因符号及物种核对唯一蛋白身份，保留原始 UniProt 数据。": "Check unique protein identity by gene symbol and species, retaining original UniProt data.",
  "核对唯一化学身份与 SMILES/InChIKey；不预测活性或生产能力。": "Check unique chemical identity and SMILES/InChIKey; do not predict activity or production capacity.",
  "有界查询分子、靶点、assay、bioactivity 或 mechanism；保留单位、关系符号和物种。": "Run bounded molecule, target, assay, bioactivity or mechanism queries, retaining units, relation operators and species.",
  "查已核对物种的 UniProt—通路归属；本地最多两页，不做富集或通路效应预测。": "Retrieve species-checked UniProt–pathway relationships, with at most two pages; no enrichment or pathway-effect prediction.",
  "按蛋白与 taxon 查询 GO 注释，保留 qualifier、ECO、GO evidence 和参考来源。": "Query GO annotations by protein and taxon, retaining qualifiers, ECO, GO evidence and references.",
  "Supervisor · 任务与文献": "Supervisor · task & literature",
  "Generation · 双视角生成": "Generation · two perspectives",
  "Proximity · 机械去重": "Proximity · rule-based deduplication",
  "Reflection · 证据与反证": "Reflection · evidence & counterevidence",
  "Ranking · 成对比较": "Ranking · pairwise comparisons",
  "Evolution · 一轮修订": "Evolution · one revision round",
  "Meta-review · 研究合同": "Meta-review · research plan",
  "证据整理与独立语义复核": "Evidence synthesis & independent semantic review",
  "资源预检跳过可选阶段": "Resource preflight skipped an optional stage",
  "请求在发送前被预算阻止": "Budget blocked the request before dispatch",
  "独立上下文调用；只返回结构化研究产物，不公开私密思维链。": "Separate-context call; returns structured research artifacts only, without private chain-of-thought.",
  "结构化结果已返回，下一阶段仍需核对适用性。": "Structured results returned; applicability still requires review in the next stage.",
  "本地调用准备或记录保存失败；实际模型调用另行计数。": "Local call preparation or record persistence failed. Actual model calls are counted separately.",
  "调用失败或超出预算；没有补造完成结果。": "The call failed or exceeded its budget. Review its saved status and recovery options.",
  "可选调用没有可用结果；保留已审版本，继续尝试必要收尾。": "The optional call produced no usable result. Retain reviewed versions and attempt required closing steps.",
  "独立审查候选结论及适用范围": "Independently review the candidate conclusion & applicability",
  "独立候选结论审查": "Independent candidate conclusion review",
  "候选结论独立审查已保存；科学支持程度与流程完成分别记录。": "Independent conclusion review saved. Scientific support and workflow completion are recorded separately.",
  "独立候选评估已保存；假说生成、假说审查与Meta扩展未执行。": "Independent candidate assessment saved. Hypothesis generation, hypothesis review and meta-review expansion were not run.",
  "候选评估后的可选假说扩展": "Optional hypothesis expansion after candidate assessment",
  "优先完成必要的同版本审查与模型收尾；此可选动作未执行。": "Prioritize required same-version review and model closing steps; this optional action was not run.",
  "固定协调器核对已保存实体身份；不由模型选择候选": "The coordinator checks the identity of saved candidates",
  "既有固定补证查询；经相同参数契约与共享预算执行": "Existing fixed evidence query, executed under the same parameter contract and shared budget",
  "未注册工具": "Unregistered tool",
  "使用有界只读工具；观察结果不是疗效判断。": "Tool observations record query scope, data sources and returned content.",
  "模型提出补证；旧提案未记录具体缺口文字": "The model requested additional evidence; the legacy proposal did not record a specific gap",
  "形成最少问题卡；有收尾余量才加入第二视角": "Create the minimum question cards; add a second perspective only if closing budget remains",
  "保存有界补证计划": "Save a bounded evidence-gathering plan",
  "模型提出缺口；执行器先校验参数和唯一身份。": "The model identifies a gap; the executor first validates parameters and unique identity.",
  "模型补证 JSON 未通过确定性解析；仅记录参数拒绝，不执行 HTTP": "Evidence-request JSON failed deterministic parsing. Record parameter rejection only; do not perform HTTP requests.",
  "按实体、机制、菌株、条件和终点机械去重": "Deduplicate by entity, mechanism, strain, conditions and endpoint",
  "在同任务约束下比较最多三个方案": "Compare up to three options under the same task constraints",
  "条件不匹配：不进行 LLM 胜负判断": "Conditions differ: skip LLM winner selection",
  "按证据与批评最多修订一轮": "Revise once at most, guided by evidence and critique",
  "没有具体修订依据，不伪造版本变化": "No specific basis for revision; do not fabricate version changes",
  "预算预检不足；保留已审版本，不生成未复核修订": "Budget preflight failed; retain reviewed versions and do not create unreviewed revisions",
  "据批评修订，不继承旧胜场": "Revise from critique without inheriting previous wins",
  "修订调用未取得可用结果；保留已审版本": "Revision call produced no usable result; retain reviewed versions",
  "修订版本重新审查": "Re-review revised versions",
  "形成研究合同与 NasoTwin 测量需求": "Create research plans & NasoTwin measurement needs",
  "总审与最多三个研究合同": "Meta-review & up to three research plans",
  "已存版本与来源重新审查；没有本轮新检索": "Re-review saved versions and sources; no new search this run",
  "复用已保存来源与草稿，逐引用重新核对": "Reuse saved sources and drafts; recheck each reference",
  "本轮复用已存来源和草稿，不把它们算作新检索或独立科学验证；审查和比较重新执行。": "This run continues saved sources and drafts, repeats reviews and comparisons, and retains the original retrieval records.",
  "冻结问题、菌株和研究边界": "Freeze the question, strain & research boundaries",
  "模型禁用；只有检索观察，没有已判读假说": "Model disabled; retrieval observations only, with no interpreted hypotheses",
  "建立文献地图，保留相反发现": "Build a literature map, retaining opposing findings",
  "候选评估优先审查已有材料；本阶段未执行在线扩展": "Candidate assessment prioritizes existing material; online expansion was not run at this stage",
  "核验原文与独立语义审查；解析靶点和分子身份": "Verify source text and independent semantics; resolve target and molecule identities",
  "工具预算不足，保存已完成解析和部分补证；未执行后续请求": "Tool budget insufficient. Save completed identity resolution and partial evidence gathering; later requests were not run",
  "补抽未取得新增已审证据；已有证据保留。新增来源须逐主张Reflection核查或保持未决。": "Additional extraction yielded no new reviewed evidence. Existing evidence is retained; new sources require claim-level Reflection checks or remain unresolved.",
  "未指定干预条件": "Intervention conditions not specified",
  "未指定培养与供给条件": "Culture and supply conditions not specified",
  "待团队明确可测量研究终点": "Team must specify a measurable research endpoint",
  "临床处方/疗效确认": "Clinical prescribing / efficacy confirmation",
  "四模型筛药、对接、ADMET、完整Gate、MD或湿实验执行": "Four-model screening, docking, ADMET, full Gate, MD or wet-lab execution",
  "逐假说审查支持、反证与底盘缺口": "Review support, counterevidence & chassis gaps for each hypothesis",
  "bio-research-review：完整初始版本审查": "bio-research-review: complete initial-version review",
  "停止并保存已有证据与假说": "Stop and save existing evidence & hypotheses",
  "未执行，不显示虚构成果": "Not run",
  "软件流程完成不等于候选有效；引文审查不等于独立实验或临床验证。": "Candidate conclusions include assessment status, evidence sources and applicable conditions.",
  "候选结论包含评估状态、证据来源和适用条件。": "Candidate conclusions include assessment status, evidence sources and applicable conditions.",
  "指定名称尚未唯一关联本轮候选；未猜测身份或关联证据": "The candidate name awaits a unique chemical-identity link",
  "核对指定候选的唯一化学身份并形成可追溯评估记录": "Check the specified candidate's unique chemical identity and create a traceable assessment",
  "化学身份仍需核对；不能由名称推定化学形式或结构": "Chemical identity still requires checking; names do not establish chemical form or structure",
  "未指定": "Not specified",
  "未知": "Unknown",
  "明确疾病、表型和研究问题": "Define the disease, phenotype & research question",
  "检索疾病、干预与相反结果": "Search disease, intervention & opposing findings",
  "整理靶点假说和独立文献分子": "Synthesize target hypotheses & independent literature molecules",
  "核对蛋白及化学实体身份": "Check protein & chemical entity identities",
  "自动准备四模型输入包": "Automatically prepare four-model input packages",
  "部分引用不存在、未通过审查或证据方向不符，已从可用证据中移除": "Some references were missing, unreviewed or directionally inapplicable and were removed from usable evidence",
  "没有已审查的支持证据，不能形成已支持的干预结论": "No reviewed supporting evidence is available; a supported intervention conclusion cannot be formed",
  "原草稿菌株与任务不符；需对实际菌株重新补证": "The draft strain differs from the task; gather evidence for the actual strain",
  "菌株错配已纠正，不沿用其他菌株生产结论": "Strain mismatch corrected; production conclusions from other strains are not inherited",
  "宿主通路、GO或药理注释不是底盘生产证据": "Chassis-production assessment uses strain-and-condition-matched production, mechanism and model evidence",
  "缺少 exact 菌株与条件匹配的生产依据，禁止直接外推": "Exact strain-and-condition production evidence is missing; do not extrapolate directly",
  "本任务未指定生产菌株，底盘生产适用性尚未评估": "No production strain was specified; chassis-production applicability is not assessed",
  "审查缺失或重复，保留待复核状态": "Review is missing or duplicated; retain a pending-review state",
  "完整版本特异审查": "Complete version-specific review",
  "该假说缺少其自身已审查的支持证据": "This hypothesis lacks its own reviewed supporting evidence",
  "证据适用性、底盘证据/缺口、可检验性与资源匹配；不把新意混成可信度概率": "Research priority follows evidence applicability, chassis evidence and gaps, testability and resource fit; novelty is recorded separately",
  "条件或终点不同；规则模块弃权，不混排实验体系": "Conditions or endpoints differ; the rule-based module abstains rather than ranks different study systems together",
  "共享调用、时间与字节额度预留必要收尾；比较未执行": "Shared call, time and byte budgets reserve required closing steps; comparison was not run",
  "缺少本比较的有效证据引用，不能宣称胜出": "Valid evidence references are missing for this comparison; a winner cannot be claimed",
  "新版未继承旧版比较名次；仅在本轮复核后入选待人工审查": "The revised version enters follow-up research assessment after this run's review; comparison ranks are version-specific",
  "为本次口袋重新编码；分子embedding须与权重、预处理和ID映射一致。": "Encode this pocket anew; molecule embeddings must match weights, preprocessing and ID mapping.",
  "为本次链重新生成Foldseek/SaProt特征；不能沿用其它靶点向量。": "Regenerate Foldseek/SaProt features for this chain; do not reuse vectors from another target.",
  "为本次序列重新生成蛋白图，并核对分子图与模型环境。": "Regenerate the protein graph for this sequence and check molecule graphs and model environment.",
  "为本次口袋生成药效团；分子构象失败保持失败，不补造得分。": "Generate pharmacophores for this pocket. Conformer failures remain failures; scores are not fabricated.",
  "演示导入请求不符合格式或长度限制；未回显输入。": "The demo import request violates format or length limits; input is not echoed.",
  "准备好的蛋白与同坐标系参考配体，配体周围6 Å定义口袋。": "Prepared protein and same-frame reference ligand; the pocket is defined within 6 Å of the ligand.",
  "明确选择的蛋白链，SaProt输入不超过1022残基；不自动截断。": "An explicitly selected protein chain. SaProt input must not exceed 1,022 residues; no automatic truncation.",
  "与研究对象一致的一条蛋白序列，完整蛋白或明确选择的结构域。": "One protein sequence matching the study subject: the full protein or an explicitly selected domain.",
  "准备好的蛋白与同坐标系参考配体，用参考配体定位口袋。": "Prepared protein and same-frame reference ligand, using the reference ligand to locate the pocket.",
  "输入状态不表示模型环境、权重或缓存已就绪，也不表示评分已运行。": "Input readiness, model environment, weights, caches and scoring status are recorded separately.",
  "本检查未重新验证文件内容；修改过的文件须重新准备与审查。": "File contents are not revalidated by this check. Modified files require preparation and review again.",
  "HDAC2演示缓存仅对应其固定构建体、链、序列、口袋和版本，不能只改靶点名复用。": "The HDAC2 demo cache applies only to its fixed construct, chain, sequence, pocket and version. Renaming the target does not make the cache reusable.",
  "ADMET、ChassisGate、对接和MD保持独立阶段，未因本检查而执行。": "ADMET, ChassisGate, docking and MD remain separate stages and are not executed by this check.",
  "文献路线的候选分子不必强制作用于这个靶点。": "Literature-route candidate molecules are not required to act on this target.",
  "多角色通过共享后端的分离上下文完成生成、反思与审查，后端配置随运行保存。": "Roles use separate contexts on the shared backend for generation, reflection and review; backend configuration is saved with the run.",
  "研究优先级依据证据适用性、可检验性和资源匹配进行比较。": "Research priorities compare evidence applicability, testability and resource fit.",
  "底盘快照汇总研究线索；正式分类、FBA 结果与 PKPD 参数来源分别记录。": "Chassis snapshots summarize research leads; formal classification, FBA results and PKPD parameter sources are recorded separately.",
  "检索范围为 Europe PMC 中本次记录的查询、时间窗口和分页。": "Retrieval covers the recorded Europe PMC queries, date windows and pages.",
  "独立研究按原始研究标识去重，缓存访问沿用原来源记录。": "Independent studies are deduplicated by study identity; cache access retains the original source record.",
  "查新报告列出实际检索窗口、相关工作、待确认差异和执行状态。": "Novelty reports list actual search windows, related work, differences to check and execution status.",
  "主张审查由共享后端的分离上下文执行，保存逐条主张与来源的审查记录。": "Claim review uses separate contexts on the shared backend and saves claim-to-source review records.",
  "本次检索结果及其范围随查新记录保存": "Search results and their scope are saved with the novelty record",
  "工具观察记录查询范围、数据来源和返回内容。": "Tool observations record query scope, data sources and returned content.",
  "底盘生产评价使用菌株和条件匹配的生产、机制及模型证据": "Chassis-production assessment uses strain-and-condition-matched production, mechanism and model evidence",
  "研究优先级依据证据适用性、底盘证据与缺口、可检验性及资源匹配；新颖性另行记录": "Research priority follows evidence applicability, chassis evidence and gaps, testability and resource fit; novelty is recorded separately",
  "新版按本轮复核结果进入后续研究评估，比较名次按版本记录": "The revised version enters follow-up research assessment after this run's review; comparison ranks are version-specific",
  "本合同列出研究优先级、证据依据及参数测量需求。": "This plan lists research priorities, evidence and parameter measurement needs.",
  "本轮未生成研究合同；已保存假说、预算和停止原因供继续研究使用。": "No research plan was generated this run; saved hypotheses, budgets and stop reasons support continuation.",
  "本轮继续已保存的来源与草稿，重新执行审查和比较；来源保留原检索记录。": "This run continues saved sources and drafts, repeats reviews and comparisons, and retains the original retrieval records.",
  "来源读取范围标记为摘要或全文": "Source reading scope is labelled as abstract or full text",
  "查新结论关联查询范围和相关工作对照": "Novelty conclusions link to query scope and related-work comparisons",
  "查新结论使用已记录的检索结果": "Novelty conclusions use recorded search results",
  "数据库注释、因果证据与生产证据分别归档": "Database annotations, causal evidence and production evidence are recorded separately",
  "历史角色标签来源为原记录的明确文本线索；当前审查状态为待复核。": "Historical role labels derive from explicit text in the original record; current review status: pending.",
  "部分靶点的靶细胞信息待补充。": "Target-cell information remains to be added for some targets.",
  "部分干预线索的单一化学实体待解析；小分子筛选入口使用已解析的单一结构。": "Some intervention leads await single-entity resolution; small-molecule screening uses resolved individual structures.",
  "论文分组保留跨数据库和版本的原快照；独立研究按研究标识去重，证据强度按设计、条件与审查结果评价。": "Paper groups retain source snapshots across databases and versions; independent studies are deduplicated by identity and evidence strength follows study design, conditions and review.",
  "候选名称的唯一化学身份关联待确定": "The candidate name awaits a unique chemical-identity link",
  "展示标签来源为历史候选说明与关联证据中的明确角色词；角色审查待完成": "Display labels use explicit role terms in historical candidate descriptions and linked evidence; role review is pending",
  "科研用途。导出内容包含当前研究与证据；分享前检查私人研究资料。": "For research use. Exports contain the current research and evidence; check private research information before sharing.",
  "科研用途：识别靶点、候选分子及关联证据": "For research use: identify targets, candidate molecules and linked evidence",
  "检索疾病—靶点关联及其数据来源，关联分数按数据库定义记录。": "Retrieve disease-target associations and their sources; association scores follow the database definitions.",
  "列名映射示例；实际列名以导入 CSV 为准。": "Column mapping example; use the actual column names in the imported CSV.",
  "用于展示四模型评分流程的 50 分子界面示例。": "A 50-molecule interface example for the four-model scoring workflow."
});
function systemText(value) {
  if (typeof value !== "string") return value;
  if (Object.hasOwn(SYSTEM_UI_TEXT, value)) return SYSTEM_UI_TEXT[value];
  let match;
  if ((match = /^查询 (europepmc_search|opentargets_search|disease_targets|uniprot|pubchem|chembl|reactome_pathways|quickgo_annotations)$/.exec(value))) return "Query " + match[1];
  if ((match = /^视角 ([12])$/.exec(value))) return "Perspective " + match[1];
  if ((match = /^(\d+) 个实际返回假说；(\d+) 个视角失败$/.exec(value))) return match[1] + " hypotheses returned; " + match[2] + " perspectives failed";
  if ((match = /^规则模块得到 (\d+) 组；保留全部版本与不同反证$/.exec(value))) return "Rule-based module produced " + match[1] + " groups; all versions and distinct counterevidence retained";
  if ((match = /^(\d+) 场实际比较；不足两个合格方案时不生成比赛$/.exec(value))) return match[1] + " comparisons recorded; no tournament is fabricated with fewer than two eligible options";
  if ((match = /^(\d+) 个有依据的新版，旧版仍保留$/.exec(value))) return match[1] + " evidence-guided new versions; earlier versions retained";
  if ((match = /^(\d+) 份供人工评审的研究合同$/.exec(value))) return match[1] + " research plans for human review";
  if ((match = /^(\d+) 个来源；摘要\/全文\/数据库范围分别保留$/.exec(value))) return match[1] + " sources; abstract/full-text/database scope retained separately";
  if ((match = /^(\d+) 个通过自动原文与语义审查的证据$/.exec(value))) return match[1] + " evidence records passed automated span and semantic checks";
  if ((match = /^疾病：([\s\S]*)；已保存输入引用 (\d+) 项$/.exec(value))) return "Disease: " + match[1] + "; " + match[2] + " saved input references";
  if ((match = /^计划 ([A-Za-z0-9_:-]+|未记录)；缺口 ([A-Za-z0-9_:-]+|未记录)；参数经确定性契约核对$/.exec(value))) return "Plan " + (match[1] === "未记录" ? "not recorded" : match[1]) + "; gap " + (match[2] === "未记录" ? "not recorded" : match[2]) + "; parameters checked against deterministic contracts";
  if ((match = /^([A-Za-z0-9_:-]+)；(Wall-time scheduling allowance exhausted|Input reservation budget exhausted|Output reservation budget exhausted|Model call budget exhausted)；UTF-8字节预留，非token计量或费用。$/.exec(value))) return match[1] + "; " + match[2] + "; UTF-8 byte reservation, not token usage or cost.";
  if (value.startsWith("本轮未完成全部研究阶段：")) return "Not all research stages completed this run: " + value.slice("本轮未完成全部研究阶段：".length);
  return value;
}
function displayActionSummary(event, maximum = 1200) {return systemText(safeEventSummary(event, maximum));}
const THERAPEUTIC = new Set(["therapeutic_candidate", "symptom_management"]);
const enc = encodeURIComponent;
const label = value => LABELS[value] || systemText(value) || "Unknown";
const text = value => value === null || value === undefined || value === "" ? "Not recorded" : String(value);
const list = value => Array.isArray(value) ? value : [];
const el = (tag, className = "", content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content);
  if (tag === "summary" && content !== undefined) node.dataset.focusKey = "summary:" + content;
  return node;
};
function badge(value, style = "") {
  const polarity = {supports:"support", opposes:"counter", mixed:"mixed", context:"context"}[value] || "";
  return el("span", "badge " + style + (polarity ? " " + polarity : ""), label(value));
}
function status(value) {return el("span", "status-badge " + String(value).replace(/[^a-z_]/g, ""), label(value));}
function runStateLabel(value) {return value === "completed" ? "Run ended" : label(value);}
function runOptionLabel(run) {return runStateLabel(run.status) + " · " + localDate(run.created_at) + " · " + run.brief.disease;}
function runStateBadge(value) {
  const node = status(value); node.textContent = runStateLabel(value);
  if (value === "completed") node.className = "status-badge historical";
  return node;
}
function button(content, action, className = "button secondary small") {
  const node = el("button", className, content); node.type = "button";
  node.dataset.focusKey = "button:" + className + ":" + content;
  node.addEventListener("click", async () => {
    if (node.disabled) return;
    node.disabled = true;
    try {await action();} catch (error) {notify(error.message, true);}
    finally {node.disabled = false;}
  });
  return node;
}
function safeLink(content, href, className = "") {
  try {
    const url = new URL(href, location.href);
    if (!["http:", "https:"].includes(url.protocol)) return el("span", className, content);
    const node = el("a", className, content); node.href = url.href;
    node.dataset.focusKey = "link:" + url.href + ":" + content;
    node.target = "_blank"; node.rel = "noopener noreferrer"; return node;
  } catch (_) {return el("span", className, content);}
}
function detail(title, value) {
  const node = el("details", "entity-details");
  node.append(el("summary", "", title), el("pre", "", JSON.stringify(value, null, 2)));
  node.querySelector("summary").dataset.focusKey = "summary:" + title;
  return node;
}
function facts(entries) {
  const node = el("dl", "entity-facts");
  for (const [name, value] of entries) {
    const entry = el("dd");
    if (value instanceof Node) entry.append(value); else entry.textContent = text(value);
    node.append(el("dt", "", name), entry);
  }
  return node;
}
function empty(title, description, action = true) {
  const node = el("div", "empty-state");
  node.append(el("div", "empty-state-symbol", "◎"), el("h2", "", title), el("p", "", description));
  if (action) node.append(button("Start new research", () => navigate("research")));
  return node;
}
function callout(content, style = "") {
  const node = el("div", "callout " + style); node.append(el("span", "", "ⓘ"), el("p", "", content));
  return node;
}
function notifications(items, title = "Uncertainty / validation gaps") {
  if (!list(items).length) return null;
  const node = el("div", "uncertainties"), ul = el("ul");
  node.append(el("strong", "", title)); for (const item of items) ul.append(el("li", "", item));
  node.append(ul); return node;
}
function add(node, child) {if (child) node.append(child);}
function notify(message, error = false) {
  clearTimeout(state.toast); $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : ""); $("toast").hidden = false;
  state.toast = setTimeout(() => {$("toast").hidden = true;}, 7500);
}
function base(project = state.project) {
  if (!project) throw new Error("Select or create a project first.");
  return "/api/projects/" + enc(project);
}
function runBase(project = state.project, run = state.run) {
  if (!run) throw new Error("Select a research run first.");
  return base(project) + "/runs/" + enc(run);
}
function artifact(path, content, project = state.project) {
  return safeLink(content, base(project) + "/artifacts/" + String(path).split("/").map(enc).join("/"));
}
function exportLink(format, content, className = "button secondary small") {
  return safeLink(content, runBase() + "/export?format=" + enc(format), className);
}
async function api(path, method = "GET", body) {
  if (location.protocol === "file:") throw new Error("This is an unconnected local-file preview. Start AI Scientist and open http://127.0.0.1:8765/ to use the workbench.");
  let response;
  try {response = await fetch(path, {method, headers: body === undefined ? {} : {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body)});} catch (_) {
    throw new Error("Cannot connect to the local service. Start AI Scientist and refresh.");
  }
  let data;
  try {data = await response.json();} catch (_) {throw new Error("The local service returned an unreadable response (HTTP " + response.status + ").");}
  if (!response.ok) {
    let message = data.detail;
    if (Array.isArray(message)) message = message.map(item => item.loc.join(".") + ": " + systemText(item.msg)).join("; ");
    if (typeof message !== "string") message = data.errors?.map(systemText).join("; ") || data.message || JSON.stringify(data);
    const error = new Error(systemText(message) || "Request failed (HTTP " + response.status + ")");
    error.status = response.status; error.code = data.code; throw error;
  }
  return data;
}
function optionList(select, values, getId, getLabel, current = "", placeholder = "") {
  select.replaceChildren();
  if (placeholder) {const node = el("option", "", placeholder); node.value = ""; select.append(node);}
  for (const value of values) {const node = el("option", "", getLabel(value)); node.value = getId(value); select.append(node);}
  if (values.some(value => getId(value) === current)) select.value = current;
  else if (!placeholder && values.length) select.value = getId(values[0]);
}
function navigate(view) {
  if (!VIEWS[view]) return;
  const fromMobileNav = document.activeElement?.closest?.("#navigation") && window.matchMedia?.("(max-width: 680px)").matches;
  state.view = view;
  document.querySelectorAll("[data-section]").forEach(node => {node.hidden = node.dataset.section !== view;});
  document.querySelectorAll("[data-view]").forEach(node => {
    node.classList.toggle("active", node.dataset.view === view);
    if (node.dataset.view === view) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current");
    if (node.dataset.view === view && node.closest("details.nav-group")) node.closest("details.nav-group").open = true;
  });
  $("viewLabel").textContent = VIEWS[view];
  const next = new URL(location.href); next.hash = view;
  if (state.project) next.searchParams.set("project", state.project); else next.searchParams.delete("project");
  if (state.run) next.searchParams.set("run", state.run); else next.searchParams.delete("run");
  history.replaceState(null, "", next);
  renderActiveView();
  $("navToggle")?.setAttribute("aria-expanded", "false");
  document.querySelector(".sidebar")?.classList.remove("navigation-open");
  if (fromMobileNav) $("main").focus({preventScroll:true});
  if (location.protocol !== "file:" && view === "settings") loadDoctor().catch(error => notify(error.message, true));
  if (location.protocol !== "file:" && view === "tools") loadResearchCatalogs().catch(error => notify(error.message, true));
  window.scrollTo({top: 0, behavior: "instant"});
}
function selectedEpoch(epoch, project, run) {return epoch === state.epoch && project === state.project && run === state.run;}
function selectionCurrent(epoch, project, run) {return epoch === state.selectionEpoch && project === state.project && run === state.run;}
function selectionNotice(message = "", error = false, preview = false) {
  let notice = $("selectionNotice");
  if (!notice) {
    notice = el("div", "callout"); notice.id = "selectionNotice";
    $("main").insertBefore(notice, $("main").firstChild);
  }
  notice.replaceChildren(); notice.hidden = !message;
  notice.className = "callout selection-notice" + (error ? " amber" : "");
  notice.setAttribute("role", error ? "alert" : "status");
  notice.setAttribute("aria-live", "polite");
  $("main").setAttribute("aria-busy", String(Boolean(message && !error && !preview)));
  if (!message) return;
  notice.append(el("p", "", message));
  if (preview) notice.append(safeLink("Open local workbench →", "http://127.0.0.1:8765/", "button secondary small"));
  else if (error) notice.append(button("Retry loading", () => state.run ? refresh() : loadRuns()));
}
function selectRun(run, summary = null) {
  if (state.run !== run) {
    state.selectionEpoch++; state.rendered = {}; state.renderContext = {}; state.agent = ""; state.collaborationRun = ""; state.eventLimit = 8;
    $("agentEventFilter").value = "";
    if ($("agentActivityKind")) $("agentActivityKind").value = "all";
  }
  clearTimeout(state.poll); state.poll = null; state.run = run;
  if (state.selected?.run_id !== run) {
    // The raw history endpoint has not applied /results' conservative status validation.
    // Only expose task metadata while scientific results are still loading.
    state.selected = summary && ["queued", "running"].includes(summary.status) ? {
      run_id: summary.run_id, status: summary.status, brief: summary.brief,
      created_at: summary.created_at, updated_at: summary.updated_at, parent_run_id: summary.parent_run_id
    } : null;
    renderRun(state.selected);
  }
  selectionNotice(run ? "Loading saved records for the selected research…" : "");
}
function scheduleRefresh(epoch, project, run) {
  if (!selectedEpoch(epoch, project, run) || state.poll) return;
  const selected = state.selected?.run_id === run ? state.selected : state.runs.find(item => item.run_id === run);
  if (["queued", "running"].includes(selected?.status) || state.jobs.some(job => ["queued", "running"].includes(job.status))) {
    state.poll = setTimeout(() => {
      state.poll = null;
      refresh().catch(error => notify(error.message, true));
    }, 4000);
  }
}
function clearSelected() {
  state.selected = null; state.jobs = []; state.experiments = []; state.memory = []; state.rendered = {}; state.renderContext = {};
  state.runs = [];
  optionList($("run"), [], r => r.run_id, r => r.run_id, "", "No research records");
  state.agent = ""; state.collaborationRun = ""; clearTimeout(state.poll); state.poll = null;
  if ($("evidenceDialog").open) $("evidenceDialog").close();
  renderRun(null); $("computeJobs").replaceChildren(); $("experimentList").replaceChildren(); $("memoryList").replaceChildren();
}
async function loadProjects(preferred = state.project) {
  const project = state.project, run = state.run, epoch = state.selectionEpoch;
  const projects = await api("/api/projects");
  if (!selectionCurrent(epoch, project, run)) return;
  state.projects = projects;
  optionList($("project"), projects, p => p.project_id, p => p.name, preferred, "New project (created automatically)");
  // Initial visit has an immediately usable blank form. No historical result is misrepresented as a new output.
  state.project = $("project").value;
  await loadRuns();
}
async function loadRuns(preferred = state.run) {
  const project = state.project, epoch = ++state.epoch;
  clearTimeout(state.poll); state.poll = null;
  if (!project) {
    clearSelected(); state.run = ""; state.runs = [];
    optionList($("run"), [], r => r.run_id, r => r.run_id, "", "No research records");
    selectionNotice(); return;
  }
  selectionNotice("Syncing research records…");
  let runs;
  try {runs = await api(base(project) + "/runs");}
  catch (error) {
    if (epoch === state.epoch && project === state.project) selectionNotice(error.message, true);
    throw error;
  }
  if (epoch !== state.epoch || project !== state.project) return;
  state.runs = runs;
  optionList($("run"), runs, r => r.run_id,
    runOptionLabel, preferred,
    runs.length ? "Select a saved research run" : "No research records");
  selectRun($("run").value, runs.find(item => item.run_id === $("run").value));
  await refresh();
}
async function refresh() {
  clearTimeout(state.poll); state.poll = null;
  const project = state.project, run = state.run, epoch = ++state.epoch;
  if (!project) {renderRun(null); selectionNotice(); return;}
  if (run && state.selected?.run_id !== run) selectionNotice("Loading saved records for the selected research…");
  let coreFinished = !run;
  // Ancillary project data must not delay the selected research result.
  const ancillary = Promise.allSettled([api(base(project) + "/compute"), api(base(project) + "/experiments"), api(base(project) + "/memory")]);
  const updateAncillary = ancillary.then(results => {
    if (!selectedEpoch(epoch, project, run)) return;
    if (results[0].status === "fulfilled") state.jobs = results[0].value;
    if (results[1].status === "fulfilled") state.experiments = results[1].value;
    if (results[2].status === "fulfilled") state.memory = results[2].value;
    renderActiveView();
    const failure = results.find(item => item.status === "rejected");
    if (failure) notify("Could not load project resources: " + failure.reason.message, true);
    if (!run) selectionNotice(failure ? failure.reason.message : "", Boolean(failure));
    if (coreFinished) scheduleRefresh(epoch, project, run);
  }).catch(error => {
    if (selectedEpoch(epoch, project, run)) notify(error.message, true);
  });
  if (!run) {renderRun(null); await updateAncillary; return;}
  try {
    const result = await api(runBase(project, run) + "/results");
    if (!selectedEpoch(epoch, project, run)) return;
    state.selected = result; renderRun(result); selectionNotice();
  } catch (error) {
    if (selectedEpoch(epoch, project, run)) selectionNotice("Research records could not be loaded: " + error.message, true);
  } finally {coreFinished = true; scheduleRefresh(epoch, project, run);}
}
function localDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false
  }).format(date);
}
function roleOf(candidate) {return candidate.role || "unknown";}
function roleStatus(candidate) {return candidate.role_status || "unclassified";}
function evidenceLinks(ids) {
  const node = el("div", "evidence-links"); node.append(el("span", "", "Evidence"));
  if (!list(ids).length) node.append(el("span", "muted", "No citable evidence recorded"));
  for (const id of list(ids)) node.append(button(id, () => openEvidence(id), "evidence-chip"));
  return node;
}
function entityHeading(name, id, value) {
  const node = el("div", "entity-top"), title = el("div");
  title.append(el("h2", "", name), el("div", "entity-id", id));
  node.append(title); if (value) node.append(status(value)); return node;
}
function packageFor(target) {return list(state.selected?.target_packages).find(p => p.package_id === target.package_id);}
function targetCard(target) {
  const card = el("article", "entity-card");
  card.append(entityHeading(target.gene_symbol, target.target_id, target.structure_status));
  card.append(el("p", "entity-description", target.rationale || "No mechanism evidence is available to display; this remains a hypothesis awaiting review."));
  const id = target.uniprot_id ? safeLink(target.uniprot_id, "https://www.uniprot.org/uniprotkb/" + enc(target.uniprot_id)) : "Identity unresolved";
  card.append(facts([["Protein identity", id], ["Proposed direction", label(target.desired_direction)],
    ["Related cells", list(target.related_cells).join("; ") || "Not specified"],
    ["Related pathways", list(target.pathways).join("; ") || "Not specified"], ["Research species", target.species || "Not recorded"]]));
  card.append(evidenceLinks(target.evidence_ids)); add(card, notifications(target.uncertainties));
  const pkg = packageFor(target);
  if (pkg) {const actions = el("div", "export-actions"); actions.style.marginTop = "16px";
    actions.append(button("View input package & download files →", () => {navigate("files"); document.getElementById("package-" + pkg.package_id)?.scrollIntoView({block: "center"});})); card.append(actions);}
  if (Object.keys(target.assessment || {}).length) card.append(detail("Detailed target assessment", target.assessment));
  return card;
}
function candidateAssessment(candidate, run = state.selected) {
  return list(run?.candidate_assessments).find(row => row.candidate_id && row.candidate_id === candidate.candidate_id);
}
function candidateVisible(candidate, run = state.selected) {
  return THERAPEUTIC.has(roleOf(candidate)) || candidateAssessment(candidate, run)?.origin === "user_supplied";
}
function sourceScopeView(source) {
  const scopes = ["abstract","full_text","database","local_document"], metadata = source?.metadata || {};
  const reused = ["background_reuse","saved_reuse","library_reuse","background_identity_reuse","cache_reuse","exact_query_http_cache","http_cache"];
  const fresh = ["current_run_retrieval","current_retrieval","live_response","current_run_full_text","live_full_text_response"], fullFresh = ["current_run_full_text","live_full_text_response"];
  const stored = scopes.includes(source?.evidence_scope) ? source.evidence_scope : "unknown";
  const rawOriginal = metadata.original_evidence_scope === undefined ? stored : metadata.original_evidence_scope;
  const original = scopes.includes(rawOriginal) ? rawOriginal : "unknown", excerpt = metadata.local_excerpt_only === true;
  const start = metadata.excerpt_start, end = metadata.excerpt_end;
  // Match Python's Unicode code-point offsets, not JS UTF-16 code units. Only
  // the actual nonempty text can verify a window; metadata cannot certify it.
  const window = excerpt && Number.isSafeInteger(start) && Number.isSafeInteger(end) && start >= 0 && start < end && end <= 1e12 &&
    typeof source?.text === "string" && source.text.length > 0 && end - start === Array.from(source.text).length ? {start,end} : null;
  let origin = [metadata.acquisition_origin,metadata.acquisition,metadata.origin].find(x => [...reused,...fresh].includes(x)) || "unknown";
  const receipt = metadata.query_receipt || {}, fullReceipt = metadata.full_text_query_receipt && typeof metadata.full_text_query_receipt === "object" && !Array.isArray(metadata.full_text_query_receipt) ? metadata.full_text_query_receipt : ["full_text","full_text_retrieval","full_text_download"].includes(receipt.operation) ? receipt : {};
  let acquisition = "unknown";
  if (reused.includes(origin)) acquisition = "reused";
  else if (fullReceipt.cache_origin === "live_response") {acquisition = "newly_acquired"; origin = "live_full_text_response";}
  else if (fullReceipt.cache_origin === "exact_query_http_cache") {acquisition = "reused"; origin = "exact_query_http_cache";}
  else if (receipt.cache_origin === "exact_query_http_cache" && !fullFresh.includes(origin)) {acquisition = "reused"; origin = "exact_query_http_cache";}
  else if (fresh.includes(origin)) acquisition = "newly_acquired";
  else if (receipt.cache_origin === "live_response") {acquisition = "newly_acquired"; origin = "live_response";}
  const current = excerpt ? "local_excerpt" : stored;
  return {original_scope:original,current_scope:current,local_excerpt:excerpt,original_window:window,
    acquisition,acquisition_origin:origin,fresh_fulltext:current === "full_text" && acquisition === "newly_acquired" && fullFresh.includes(origin)};
}
function sourceScopeLabel(source) {
  const scope = sourceScopeView(source), names = {abstract:"Abstract",full_text:"Full text",database:"Database record",local_document:"Local record",unknown:"Material with unrecorded scope"};
  if (scope.local_excerpt) return "Original " + names[scope.original_scope] + " · saved contiguous excerpt" +
    (scope.original_window ? ` · original character window [${scope.original_window.start}, ${scope.original_window.end})` : " · original text window unverified") + " · " +
    ({reused:"Reused saved excerpt",newly_acquired:"New excerpt this run · excerpt scope",unknown:"Acquisition not recorded · excerpt scope"}[scope.acquisition]);
  const title = scope.current_scope === "abstract" ? "Abstract only" : names[scope.current_scope];
  if (scope.fresh_fulltext) return title + " · full text newly retrieved this run";
  if (scope.acquisition === "reused") return title + " · reused saved material";
  if (scope.acquisition === "newly_acquired") return title + (scope.current_scope === "full_text" ? " · retrieved this run; full-text completeness requires review" : " · newly retrieved this run");
  return title + (scope.current_scope === "full_text" ? " · fresh full-text retrieval unconfirmed" : "");
}
function assessmentDetails(assessment) {
  const root = el("section", "dialog-section");
  if (!assessment) return root;
  assessment = publicCandidateAssessment(assessment);
  root.append(el("h3", "", assessment.origin === "user_supplied" ? "User-specified candidate assessment" : "Current candidate assessment"),
    facts([["Requested population / endpoint", text(systemText(assessment.population_requested)) + " / " + text(systemText(assessment.endpoint_requested))],
      ["Delivery context", systemText(assessment.delivery_context)], ["Independent conclusion review", label(assessment.assessment_status || "unreviewed")],
      ["Scientific assessment", label(assessment.scientific_status)], ["Overall workflow", label(assessment.workflow_status)],
      ["Hypothesis expansion", assessment.hypothesis_expansion_status === "not_run" ? "Not run" : assessment.hypothesis_expansion_status === "started" ? "Started · check actual review records" : "Not recorded"]]),
    artifactLinks(assessment.evidence_ids, "Linked support / context"),
    artifactLinks(assessment.opposing_evidence_ids, "Counterevidence / mixed results"));
  const evaluation = publicCandidateEvaluation(assessment.validated_evaluation);
  if (evaluation) root.append(evaluationDetails(evaluation));
  else root.append(el("p", "confirmation-note", "Independent conclusion review is pending for the current record. Role reviews, accepted citations and drafts are available separately."));
  const observations = list(publicCandidateAssessment(assessment).observation_support);
  if (observations.length) {
    root.append(el("h4", "", "Reviewed source observation"),
      el("p", "confirmation-note", "Support applies to the cited source observation and its recorded conditions. Whole-hypothesis assessment and clinical applicability are evaluated separately; mixed/opposing evidence retains its polarity."));
    for (const item of observations) root.append(artifactLinks([item.claim_id,item.evidence_id], "Source observation & citation"),
      el("p", "muted", "Original evidence polarity: " + text(item.polarity) + " · narrow observation relation: " + text(item.claim_relation) + " · v" + text(item.version)));
  }
  for (const row of list(assessment.evidence_scope)) root.append(el("p", "muted",
    row.evidence_id + " · " + row.study_system + " · " + row.condition + " · " + row.endpoint));
  add(root, notifications(assessment.unknowns.map(systemText), "Unknowns / applicability"));
  add(root, notifications(assessment.next_questions.map(systemText), "Next questions"));
  root.append(el("p", "confirmation-note", systemText(assessment.status_note)));
  return root;
}
function evaluationDetails(value) {
  const row = publicCandidateEvaluation(value), root = el("section", "dialog-section");
  if (!row) return root;
  root.append(el("h3", "", "Independently reviewed scoped conclusion"), el("p", "entity-description", row.conclusion),
    facts([["Assessment / identity version", row.assessment_id + " v" + row.version + " / v" + row.identity_revision],
      ["Review completion / scientific status", label(row.status) + " / " + label(row.scientific_status)]]),
    artifactLinks(["assessment:" + row.assessment_id + ":v" + row.version, "assessment_review:" + row.review_id], "Conclusion & independent review"));
  const findings = el("details", "evaluation-findings"); findings.dataset.uiKey = "findings-" + row.assessment_id + "-" + row.version;
  findings.append(el("summary", "", "Source findings & applicability · " + row.findings.length + " items"));
  for (const item of row.findings) {
    const finding = el("section", "finding-record");
    finding.append(el("p", "", item.statement),
      el("p", "muted", [item.scope.study_system,item.scope.condition,item.scope.endpoint].map(text).join(" / ")),
      el("p", "confirmation-note", label(item.applicability) + " · " + item.scope_limitations.join("; ")),
      artifactLinks(item.evidence_ids, "Citations for this finding")); findings.append(finding);
  }
  root.append(findings);
  root.append(artifactLinks(row.supporting_evidence_ids, "Assessment support / context"), artifactLinks(row.opposing_evidence_ids, "Assessment opposing / mixed evidence"));
  add(root, notifications(row.unknowns, "Remaining conclusion unknowns")); add(root, notifications(row.next_questions, "Assessment next steps"));
  root.append(el("p", "confirmation-note", "Review status and evidence strength are reported separately. Optional stages retain their execution status."));
  return root;
}
function assessmentExecutionLabel(value, kind) {
  if (!value || typeof value !== "object" || !Object.keys(value).length || value.status === "not_run") return "Not run";
  if (["unknown", "unavailable", "failed", "queued", "running"].includes(value.status)) return label(value.status);
  if (kind === "gate") return value.class || value.classification || "Record available · expand to review";
  return "Prediction recorded · expand to review";
}
function candidateCard(candidate) {
  const card = el("article", "entity-card");
  card.append(entityHeading(candidate.name, candidate.candidate_id, candidate.decision));
  const tags = el("div", "badge-row"); tags.append(badge(roleOf(candidate), THERAPEUTIC.has(roleOf(candidate)) ? "teal" : ""));
  for (const route of list(candidate.routes)) tags.append(badge(route, "outline"));
  if (roleStatus(candidate) === "legacy_inferred") tags.append(badge("Classification inferred from historical records", "amber"));
  else if (["unknown", "unclassified", "needs_review"].includes(roleStatus(candidate))) tags.append(badge("Role classification awaiting review", "amber"));
  else if (roleStatus(candidate)) tags.append(badge("Automated research classification", "outline"));
  const evaluated = publicCandidateEvaluation(candidateAssessment(candidate)?.validated_evaluation);
  card.append(tags, el("p", "entity-description", evaluated ? evaluated.conclusion :
    candidate.rationale ? "Draft rationale (independent conclusion unreviewed): " + candidate.rationale : "See the assessment below for evidence, mixed findings and applicability. Conclusions remain unreviewed without a valid review receipt."));
  const pubchem = candidate.pubchem_cid ? safeLink("PubChem CID " + candidate.pubchem_cid,
    "https://pubchem.ncbi.nlm.nih.gov/compound/" + enc(candidate.pubchem_cid)) : label(candidate.identity_status === "resolved" ? "Resolved" : "Identity unresolved");
  const relations = Object.entries(candidate.target_relations || {}).map(([name, relation]) => name + ": " + label(relation)).join("; ");
  card.append(facts([["Chemical identity", pubchem], ["Target relationships", relations || "Relationship not recorded"],
    ["ADMET", assessmentExecutionLabel(candidate.admet, "admet")],
    ["ChassisGate", assessmentExecutionLabel(candidate.gate, "gate")]]));
  card.append(evidenceLinks(candidate.evidence_ids), assessmentDetails(candidateAssessment(candidate)));
  if (candidate.role_reason) add(card, notifications([candidate.role_reason], "Role-classification basis & boundaries"));
  if (candidate.role_evidence_ids?.length) {
    const roleLinks = evidenceLinks(candidate.role_evidence_ids);
    roleLinks.querySelector("span").textContent = "Classification evidence"; card.append(roleLinks);
  }
  add(card, notifications(list(candidate.decision_reasons), "Research-priority rationale"));
  add(card, notifications(list(candidate.uncertainties)));
  card.append(detail("Chemical structure, model scores & compute records", {identity_status: candidate.identity_status, smiles: candidate.smiles,
    inchikey: candidate.inchikey, original_ids: candidate.original_ids, entity_id:candidate.entity_id,
    identity_revision:candidate.identity_revision, identity_assessment_pending:candidate.identity_assessment_pending,
    user_name_declarations:publicUserNameDeclarations(candidate.user_name_declarations),
    identity_priority_before_update:candidate.identity_priority_before_update, identity_history:candidate.identity_history, identity_form:candidate.identity_form, aliases:list(candidate.aliases), identity_source_ids:list(candidate.identity_source_ids), admet: candidate.admet,
    gate: candidate.gate, scores: candidate.scores}));
  if (candidate.identity_assessment_pending) card.append(el("p", "confirmation-note", "Chemical identity updated. Research priority is retained pending evidence reassessment; scientific and ChassisGate status are recorded separately."));
  else if (candidate.identity_revision > 0) card.append(el("p", "confirmation-note", "Chemical identity updated to v" + candidate.identity_revision + ". Scientific assessment, chassis feasibility and research priority are recorded separately."));
  return card;
}
function miniEntity(entity, target = false) {
  const row = el("div", "mini-item"), info = el("div");
  row.append(el("div", "mini-item-icon", target ? "◎" : "⬡"));
  info.append(el("h3", "", target ? entity.gene_symbol : entity.name), el("p", "",
    target ? "Direction: " + label(entity.desired_direction) + " · " + (entity.uniprot_id || "Identity awaiting review") : label(roleOf(entity)) + " · " + label(entity.decision)));
  row.append(info); return row;
}
function metric(name, value, note) {
  const node = el("div", "metric-card"); node.append(el("div", "metric-label", name), el("div", "metric-value", value), el("div", "metric-note", note)); return node;
}
function renderOverview(run) {
  const root = $("overviewContent"); root.replaceChildren(); $("runStatus").replaceChildren();
  if (!run) {$("resultTitle").textContent = "Overview"; $("resultSubtitle").textContent = "Select a saved research run or start a new question.";
    root.append(empty("No research selected", "Select a saved run or start research to view targets, candidates and evidence.")); return;}
  const candidates = list(run.candidates), therapeutic = candidates.filter(c => THERAPEUTIC.has(roleOf(c)));
  $("resultTitle").textContent = run.brief.disease;
  $("resultSubtitle").textContent = [run.brief.phenotype || "No specific phenotype provided", run.brief.population,
    run.brief.delivery_context].filter(Boolean).join(" · ");
  $("runStatus").append(runStateBadge(run.status));
  if (syntheticRun(run)) root.append(coModeMarker(run));
  const independent = list(run.candidate_evaluations).map(publicCandidateEvaluation).filter(Boolean);
  if (run.co_scientist) {
    const assessment = el("section", "assessment-summary"), heading = el("div");
    heading.append(el("span", "eyebrow", "Candidate conclusion review"), el("h2", "", independent.length ?
      independent.length + " candidate conclusions independently reviewed" : "No independently reviewed candidate conclusions"));
    const description = independent.length ? "See each scoped conclusion for scientific support, counterevidence and applicability." :
      "No valid reviewed conclusion for the current evidence. Open the review status for details.";
    heading.append(el("p", "", description));
    assessment.append(heading, button("View candidate assessment →", () => navigate("candidates"), "button secondary"));
    root.append(assessment);
  }
  const selectedOption = [...$("run").options].find(option => option.value === run.run_id);
  if (selectedOption) selectedOption.textContent = runOptionLabel(run);
  const metrics = el("div", "metrics"); metrics.append(metric("Protein targets", list(run.targets).length, "Targets linked to the current research question and evidence"),
    metric("Research candidates", therapeutic.length, "Including " + therapeutic.filter(c => c.identity_status === "resolved").length + " with checked chemical identity"),
    metric("Traceable evidence", list(run.evidence).length, "Source-span checks & automated semantic review"),
    metric("Ready input packages", list(run.target_packages).filter(p => p.status === "ready").length, "Prepared target files with readiness details")); root.append(metrics);
  if (["queued", "running"].includes(run.status)) root.append(callout("Research is in progress. Results refresh every 4 seconds; outputs and issues are saved, so this page can be closed."));
  else if (run.status === "partial") root.append(callout("This run is partial. Gaps and stop reasons are retained. Read results alongside independent-review status, evidence applicability and run details.", "amber"));
  else if (run.status === "failed") root.append(callout("This run did not finish. Available records and errors are saved; review the run details before starting again.", "red"));
  else root.append(callout("This report presents research candidates, literature leads, predictions and measurements. Candidate–target relationships are recorded with their supporting evidence."));
  if (run.co_scientist) {
    const co = run.co_scientist, panel = el("div", "panel co-overview");
    panel.append(el("h2", "", "Agent activity & research outputs"));
    const agentCount = collaborationModel(run)?.working.length || 0;
    panel.append(el("p", "muted", list(co.hypotheses).length + " recorded hypothesis versions · " + list(co.contracts).length +
      " research plans · " + list(co.events).length + " recorded decision events" + (agentCount ? " · currently " + agentCount + " active roles" : "")));
    const actions = el("div", "export-actions"); actions.append(button("Open Agent workspace →", () => navigate("collaboration"), "button primary"),
      button("Evidence, critique & version changes →", () => navigate("hypotheses")), button("Next validation question →", () => navigate("contracts")));
    panel.append(actions); root.append(panel);
  }
  if (candidates.some(c => roleStatus(c) === "legacy_inferred")) root.append(callout("Historical molecule roles are inferred from saved source text and records. Their original evidence and review status are available in the details.", "amber"));
  if (therapeutic.some(c => c.identity_status !== "resolved")) root.append(callout("Some intervention leads await single-entity resolution. Small-molecule screening uses resolved individual chemical structures.", "amber"));
  const columns = el("div", "overview-columns");
  const targetPanel = el("div", "panel"), candidatePanel = el("div", "panel");
  targetPanel.append(el("h2", "", "Protein-target hypotheses"));
  if (!list(run.targets).length) targetPanel.append(el("p", "muted", "No protein targets with citable evidence are available."));
  for (const t of list(run.targets).slice(0, 4)) targetPanel.append(miniEntity(t, true));
  targetPanel.append(button("View targets & files →", () => navigate("targets")));
  const visibleCandidates = candidates.filter(c => candidateVisible(c, run));
  candidatePanel.append(el("h2", "", "Therapeutic leads & specified-candidate assessments"));
  if (!visibleCandidates.length) candidatePanel.append(el("p", "muted", list(run.candidate_assessments).some(row => !row.candidate_id) ? "The specified candidate has no unique entity link yet. Assessment cards retain the unresolved questions." : "No therapeutic or symptom-management leads are available this run. Other leads remain under Context molecules."));
  for (const c of visibleCandidates.slice(0, 4)) candidatePanel.append(miniEntity(c));
  candidatePanel.append(button("View research candidates →", () => navigate("candidates")));
  columns.append(targetPanel, candidatePanel); root.append(columns);
  const process = el("div", "panel"); process.append(el("h2", "", "Research progress"));
  const steps = el("ul", "process-list");
  for (const step of list(run.steps)) {const row = el("li"), description = el("div");
    description.append(el("strong", "", systemText(step.question)), el("small", "", systemText(step.reason || step.capability)));
    row.append(description, status(step.status)); steps.append(row);}
  if (!run.steps?.length) steps.append(el("li", "muted", ["queued", "running"].includes(run.status) ?
    "Task submitted; waiting for research stages to be recorded." : "No stage list was saved for this run. Recorded public activity is available in the Agent workspace."));
  process.append(steps); root.append(process);
  if (list(run.questions).length) {const panel = el("div", "panel"), ul = el("ul", "text-list");
    panel.append(el("h2", "", "Open questions")); for (const q of run.questions) ul.append(el("li", "", q)); panel.append(ul); root.append(panel);}
  const exports = el("div", "panel"); exports.append(el("h2", "", "Share & export results"));
  exports.append(el("p", "muted", "Export saved records, evidence and existing target files, preserving original identities, versions and research scope."));
  const actions = el("div", "export-actions"); actions.append(exportLink("markdown", "Research report .md ↓"),
    exportLink("json", "Structured results .json ↓"), exportLink("csv", "All molecules .csv ↓"), exportLink("zip", "Research package .zip ↓"));
  exports.append(actions, el("p", "run-caption", "Run " + run.run_id + " · updated " + localDate(run.updated_at) + " (Asia/Shanghai)"));
  if (run.task_intent_receipt) exports.append(detail("Task intent & context admission", publicTaskIntent(run.task_intent_receipt)));
  if (run.evidence_digest) exports.append(detail("Evidence layers & reference coverage", publicEvidenceDigest(run.evidence_digest)));
  exports.append(detail("Warnings, errors & budget", {warnings: run.warnings, errors: run.errors, usage: run.usage,
    scope_notes: run.summary?.scope_notes || [], parent_run_id: run.parent_run_id, brief: run.brief})); root.append(exports);
}

// Only persisted data drives this workbench. No simulated agent states or invented event streams.
function coData() {
  const value = state.selected?.co_scientist;
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}
function coEmpty(root, title) {
  const run = state.selected;
  if (syntheticRun(run)) root.append(coModeMarker(run));
  if (!run) root.append(empty(title, "Select a Co-Scientist run or start a multi-role hypothesis study. Collaboration states come only from saved records."));
  else if (run.brief?.research_mode === "co_scientist" && ["queued", "running"].includes(run.status)) {
    root.append(callout("Task submitted. Waiting for the first activity record; updates refresh every 4 seconds."));
  } else root.append(empty("No collaboration records for this run", "This historical run used the fixed workflow. Its targets, molecules, evidence and exports are available.", false));
}
function structured(value, className = "entity-description") {
  return typeof value === "object" && value !== null ? el("pre", "raw-json", JSON.stringify(value, null, 2)) : el("p", className, text(value));
}
function referenceLinks(ids, title = "Evidence") {
  const node = el("div", "evidence-links"); node.append(el("span", "", title));
  if (!list(ids).length) node.append(el("span", "muted", "No references recorded"));
  for (const raw of list(ids)) {
    const id = String(raw), evidence = list(state.selected?.evidence).some(record => record.evidence_id === id);
    const source = list(state.selected?.sources).some(record => record.source_id === id);
    if (evidence || source) node.append(button(id, () => evidence ? openEvidence(id) : openSource(id), "evidence-chip"));
    else {const chip = el("span", "reference-unavailable", id); chip.title = "This ID is referenced in the record but its content is absent from the current source/evidence response. Check the original source."; node.append(chip);}
  }
  return node;
}
function coModeMarker(run) {
  const active = ["queued", "running"].includes(run.status);
  const node = el("div", "co-run-marker");
  node.dataset.updatedAt = run.updated_at || "";
  node.append(badge(syntheticRun(run) ? "SYNTHETIC · UI test data" : active ? "Live run · recorded state" : "Saved-run playback", syntheticRun(run) ? "amber" : active ? "teal" : "outline"),
    el("span", "", "Updated " + localDate(run.updated_at) + " (Asia/Shanghai)"), el("span", "entity-id", run.run_id));
  return node;
}
function syntheticRun(run) {return Boolean(run?.fixture?.synthetic || run?.presentation?.fixture?.synthetic);}
function recordedStatus(event) {
  const value = event.operation_status || event.status, node = status(value);
  if (value === "running" && !["queued", "running"].includes(state.selected?.status)) {
    node.textContent = "Running at that time"; node.className = "status-badge historical";
  }
  else if (event.operation_status && ["llm", "tool"].includes(event.kind)) {
    if (value === "running") node.textContent = "Call in progress";
    if (value === "completed") node.textContent = "Returned";
  }
  else if (value === "completed") node.textContent = "Role completed";
  return node;
}
function roleRecordStatus(agent, run) {
  const active = ["queued", "running"].includes(run?.status);
  const labels = {completed:"Role completed", running:active ? "Role running" : "Last recorded: running",
    queued:active ? "Awaiting execution" : "Last recorded: queued", failed:"Role failed", skipped:"Last recorded: skipped",
    not_recorded:"Execution not recorded", interrupted:"Role interrupted"};
  const node = status(agent.status);
  node.textContent = labels[agent.status] || label(agent.status);
  if (!active && ["queued", "running"].includes(agent.status)) node.className = "status-badge historical";
  return node;
}
function agentRole(agent) {
  const key = String(agent.agent_id || agent.role || "").toLowerCase().replace(/\s+/g, "_");
  return AGENT_ROLES[key] || [text(agent.role || agent.agent_id), "Research operation"];
}
function safeEventSummary(event, maximum = 1200) {
  // Events are public action summaries. Never expand arbitrary objects into
  // prompts, provider payloads or local logs, even for legacy records.
  const value = event.summary;
  if (typeof value !== "string" || !value.trim()) return "No public action summary recorded.";
  if (/(?:\/Users\/|\/home\/|\/private\/|\/tmp\/|\/var\/|\/etc\/|(?:file|vscode):\/\/|[A-Z]:\\|Traceback|\b(?:stderr|stdout)\b|authorization\s*[:=]|bearer\s+|\bsk-[a-z0-9]{12}|\bgh[pousr]_[a-z0-9_]{8,}|\bgithub_pat_[a-z0-9_]{8,}|\bAKIA[A-Z0-9]{16}\b|(?:api[_-]?key|secret|token|password|credentials?|(?:raw|system)[_-]?prompt)\s*[:=])/i.test(value)) return "Action details contain local/provider diagnostic information and are not public in this workbench. Review the recorded status.";
  return value.length > maximum ? value.slice(0, maximum) + "… (public summary truncated)" : value;
}
function collaborationModel(run) {
  const co = run?.co_scientist;
  if (!co || typeof co !== "object" || Array.isArray(co)) return null;
  const events = list(co.events).slice().sort((a, b) => Number(a.sequence || 0) - Number(b.sequence || 0));
  const active = ["queued", "running"].includes(run.status);
  const operations = new Map(), producers = new Map(), handoffs = new Map();
  for (const event of events) {
    if (event.operation_id) operations.set(event.operation_id, event);
    for (const ref of list(event.input_artifact_ids)) {
      const from = producers.get(ref);
      if (from && from.agent_id !== event.agent_id) {
        const key = from.agent_id + ">" + event.agent_id;
        if (!handoffs.has(key)) handoffs.set(key, {from:from.agent_id, to:event.agent_id, refs:[]});
        if (!handoffs.get(key).refs.includes(ref)) handoffs.get(key).refs.push(ref);
      }
    }
    for (const ref of list(event.artifact_ids)) producers.set(ref, event);
  }
  return {co, events, active, agents: list(co.agents),
    working: active ? list(co.agents).filter(a => a.status === "running") : [],
    latest: events.at(-1), operations: [...operations.values()], handoffs:[...handoffs.values()]};
}
function agentArtifacts(run, role) {
  const events = list(run?.co_scientist?.events).filter(e => e.agent_id === role);
  return {inputs: [...new Set(events.flatMap(e => list(e.input_artifact_ids)))],
    outputs: [...new Set(events.flatMap(e => list(e.artifact_ids)))],
    inputSummary: events.slice().reverse().find(e => e.input_summary)?.input_summary || "No stage-specific input summary recorded for this role."};
}
function openHypothesis(id, version) {
  $("hypothesisVersion").value = "all"; $("hypothesisSearch").value = id; $("hypothesisOrigin").value = "all";
  delete state.rendered.hypotheses; navigate("hypotheses");
  const card = $("hypothesis-" + id + "-v" + version);
  if (card) {const details = card.querySelector("details.hypothesis-detail"); if (details) details.open = true; card.scrollIntoView({block: "start"});}
}
function artifactTarget(ref, run = state.selected) {
  const co = run?.co_scientist || {};
  const evaluationRecords = list(run?.candidate_evaluation_records).map(publicCandidateEvaluationRecord);
  const assessmentMatches = evaluationRecords.filter(item => ref === "assessment:" + item.assessment_id + ":v" + item.version);
  if (assessmentMatches.length) return assessmentMatches.length === 1 ? {kind:"candidate_evaluation",item:assessmentMatches[0]} : null;
  const reviewMatches = evaluationRecords.flatMap(item => item.reviews.filter(row => ref === "assessment_review:" + row.review_id)
    .map(review => ({...review,assessment_status:item.status})));
  if (reviewMatches.length) return reviewMatches.length === 1 ? {kind:"candidate_evaluation_review",item:reviewMatches[0]} : null;
  for (const raw of list(run?.candidate_evaluations)) {
    const item = publicCandidateEvaluation(raw);
    if (item && ref === "assessment:" + item.assessment_id + ":v" + item.version) return {kind:"candidate_evaluation",item};
  }
  const failure = publicFailureRecords(co).find(row => row.receipt_id && row.receipt_id === ref);
  if (failure) return {kind:"failure_receipt",item:failure};
  const flow = co.research_flow;
  if (ref === "research_flow" && flow) return {kind:"research_flow", item:flow};
  for (const [field, idKey] of [["claims","claim_id"], ["queries","query_id"], ["claim_reviews","review_id"], ["novelty","novelty_id"], ["revisions","revision_id"], ["publications","paper_id"]]) {
    const receipt = list(flow?.[field]).find(item => item[idKey] === ref);
    if (receipt) return {kind:"research_receipt", item:receipt};
  }
  const plan = list(co.tool_plans).find(p => p.plan_id === ref);
  if (plan) return {kind:"tool_plan", item:plan};
  if (ref === "anchor" && co.anchor) return {kind: "anchor", item: co.anchor};
  const source = list(run?.sources).find(s => s.source_id === ref);
  if (source) return {kind: "source", item: source};
  const evidence = list(run?.evidence).find(e => e.evidence_id === ref);
  if (evidence) return {kind: "evidence", item: evidence};
  const hypothesis = list(co.hypotheses).find(h => ref === h.hypothesis_id + ":v" + h.version || ref === h.hypothesis_id);
  if (hypothesis) return {kind: "hypothesis", item: hypothesis};
  const review = list(co.reviews).find(r => ref === "review:" + r.hypothesis_id + ":v" + r.version);
  if (review) return {kind: list(co.hypotheses).some(h => h.hypothesis_id === review.hypothesis_id && h.version === review.version) ? "hypothesis" : "review", item: review};
  const comparison = list(co.comparisons).find(c => ref === "comparison:" + c.comparison_id || ref === c.comparison_id);
  if (comparison) return {kind: "comparison", item: comparison};
  const contract = list(co.contracts).find(c => ref === "contract:" + c.hypothesis_id + ":v" + c.version);
  if (contract) return {kind: "contract", item: contract};
  if (/^cluster:\d+$/.test(ref) && list(co.clusters)[Number(ref.split(":")[1])]) return {kind: "cluster", item: co.clusters[Number(ref.split(":")[1])]};
  return null;
}
function openArtifact(ref) {
  const target = artifactTarget(ref); if (!target) return;
  const {kind, item} = target;
  if (["hypothesis", "comparison", "contract"].includes(kind) && $("evidenceDialog").open) $("evidenceDialog").close();
  if (kind === "research_flow" || kind === "research_receipt") {openResearchReceipt(ref, item); return;}
  if (kind === "tool_plan") {openToolPlan(item); return;}
  if (kind === "source") {openSource(ref); return;}
  if (kind === "evidence") {openEvidence(ref); return;}
  if (kind === "hypothesis") {openHypothesis(item.hypothesis_id, item.version); return;}
  if (kind === "comparison") {navigate("hypotheses"); $("comparison-" + item.comparison_id)?.scrollIntoView({block: "start"}); return;}
  if (kind === "contract") {navigate("contracts"); $("contract-" + item.hypothesis_id + "-v" + item.version)?.scrollIntoView({block: "start"}); return;}
  if (["candidate_evaluation","candidate_evaluation_review"].includes(kind)) {
    const root = $("dialogContent"); root.replaceChildren(el("h2", "", kind === "candidate_evaluation_review" ? "Independent candidate conclusion-review record" : "Candidate-assessment version record"));
    if (item.final || item.conclusion) root.append(evaluationDetails(item.final || item));
    root.append(el("p", "confirmation-note", item.status === "unreviewed" || item.assessment_status === "unreviewed" ?
      "Conclusion binding validation is pending; inspect the current review status." : "Review completion and strength of evidence are recorded separately."), detail("Safe public record", item));
    showDialog("ASSESSMENT · " + ref); return;
  }
  const root = $("dialogContent"); root.replaceChildren(el("h2", "", kind === "failure_receipt" ? failureDetailTitle(item) : kind === "anchor" ? "Saved research boundaries" : kind === "review" ? "Saved review · hypothesis-version content not recorded" : "Saved rule-based grouping"), detail("Public structured artifact", item));
  root.querySelector("details").open = true; showDialog("ARTIFACT · " + ref);
}
function openResearchReceipt(ref, item) {
  const root = $("dialogContent"); root.replaceChildren(el("h2", "", "Literature / claim / version research record"));
  root.append(el("p", "confirmation-note", "Saved sources retain acquisition status and publication identity. Abstracts, full text, annotations and synthetic test material are labelled separately; records of the same paper are grouped."));
  if (ref === "research_flow") {
    const flow = coData()?.research_flow || {};
    root.append(facts(Object.entries(flow.stage_limits || {}).map(([stage, limit]) => [stage, text(flow.stage_usage?.[stage] || 0) + " / " + text(limit)])));
    for (const [field, key, label] of [["publications","paper_id","Canonical publications & original snapshots"], ["claims","claim_id","Claim-level applicability"], ["queries","query_id","Actual queries & stop states"], ["novelty","novelty_id","Scoped novelty searches"], ["revisions","revision_id","Revision evidence"]])
      root.append(artifactLinks(list(flow[field]).map(row => row[key]), label));
    add(root, notifications(flow.limitations, "Scope & limitations"));
  } else {
    if (item.query_id) root.append(facts([["Actual query", item.query], ["Status / stop reason", text(item.status) + " / " + text(item.stop_reason)],
      ["Date window", (item.date_from || "Foundational work not date-limited") + " → " + (item.date_to || "Not restricted")], ["Read scope", item.scope],
      ["Total hits / coverage", text(item.total_hits) + " / " + text(item.coverage)], ["Sort / pagination", text(item.sort) + " / " + text(item.pagination)],
      ["Retrieved hit range", text(item.hit_range_start) + " → " + text(item.hit_range_end)], ["Further hits", item.has_more == null ? "Not recorded" : item.has_more ? "Additional hits remain unread" : "No further hits within this scope"],
      ["Selection rationale", item.selection_reason],
      ["HTTP / cache this query", text(item.request_count || 0) + " / " + text(item.cache_hits || 0)], ["Before / after cross-source deduplication", text(item.before_dedup || 0) + " / " + text(item.after_dedup || 0)]]));
    if (list(item.coverage_gaps).length) add(root, notifications(item.coverage_gaps, "Search-coverage gaps"));
    if (item.claim_id) root.append(badge(item.state || item.verdict || "unknown", "outline"), structured(item.text || item.reason), notifications(item.gaps, "Applicability gaps"));
    if (item.paper_id) {
      root.append(facts([["Source-group type", item.kind || "paper"], ["Saved snapshots", list(item.snapshots).length]]));
      for (const snapshot of list(item.snapshots)) root.append(facts([["Source title", snapshot.title], ["Returned authors", list(snapshot.authors).join("; ") || "Not recorded"],
        ["Journal", snapshot.journal], ["Year / read scope", text(snapshot.year) + " / " + text(snapshot.scope)], ["Original snapshot", snapshot.snapshot_id]]));
    }
    const refs = [...list(item.query_ids), ...list(item.source_ids), ...list(item.closest_source_ids), ...list(item.evidence_ids), ...list(item.valid_evidence_ids), ...list(item.claim_ids), ...list(item.review_ids)];
    if (item.hypothesis_id && item.version) refs.push(item.hypothesis_id + ":v" + item.version);
    if (item.hypothesis_id && item.to_version) refs.push(item.hypothesis_id + ":v" + item.to_version, item.hypothesis_id + ":v" + item.from_version);
    if (list(item.snapshots).length) refs.push(...item.snapshots.map(s => s.source_id));
    root.append(artifactLinks([...new Set(refs)], "Open query / source / span / claim version"));
  }
  root.append(detail("Public structured artifact (no private reasoning or full text)", item));
  showDialog("RESEARCH · " + ref);
}
function publicClaimRelation(value) {
  if (!value || value.schema_version !== 2 || !["supports","opposes","context"].includes(value.relation)) return null;
  return publicFields(value,["schema_version","claim_id","hypothesis_id","version","evidence_id","source_id","entity_id","relation","study_system","condition","endpoint"]);
}
function publicResearchFlow(value) {
  if (!value || typeof value !== "object") return null;
  const record = (row, fields) => Object.fromEntries(fields.filter(k => row?.[k] !== undefined).map(k => [k,
    Array.isArray(row[k]) ? list(row[k]).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x) :
      typeof row[k] === "number" && Number.isFinite(row[k]) || typeof row[k] === "boolean" || row[k] == null ? row[k] : safeEventSummary({summary:row[k]})]));
  const specs = {claims:["claim_id","hypothesis_id","version","kind","text","condition","endpoint","state","evidence_ids","query_ids","gaps","relation_schema_version","assessment_scope"],
    queries:["query_id","stage","purpose","query","provider","date_from","date_to","hypothesis_id","version","status","stop_reason","claim_ids","source_ids","request_count","cache_hits","call_ids","reused_from","retrieved_at","scope","total_hits","coverage","before_dedup","after_dedup","sort","pagination","facet","query_origin","hit_range_start","hit_range_end","has_more","selection_reason","coverage_gaps"],
    claim_reviews:["review_id","claim_id","hypothesis_id","version","verdict","reason","valid_evidence_ids","gaps","relation_schema_version","claim_scope","rejected_relation_ids"],
    novelty:["novelty_id","hypothesis_id","version","status","query_ids","closest_source_ids","overlap","unconfirmed_difference","limitations"],
    revisions:["revision_id","hypothesis_id","from_version","to_version","field","before_sha256","after_sha256","claim_ids","review_ids","query_ids","evidence_ids","reason"],
    reference_coverage:["reference_id","source_id","identifiers","title","url","package_id","package_version","package_manifest_sha256","declared_evidence_level","available_in_reference","source_scope","original_source_scope","local_excerpt_only","selection_status","access_status","citation_status","selected_source_ids","accepted_evidence_ids","scope_note"]};
  const result = record(value, ["protocol_version","activation","executor","limitations","checklist"]);
  for (const [field, fields] of Object.entries(specs)) result[field] = list(value[field]).slice(0,300).map(row => record(row, fields));
  result.claim_reviews = result.claim_reviews.map((row,index) => ({...row,
    evidence_relations:list(value.claim_reviews?.[index]?.evidence_relations).slice(0,100).map(publicClaimRelation).filter(Boolean)}));
  for (const field of ["stage_limits", "stage_usage"]) result[field] = Object.fromEntries(Object.entries(value[field] || {}).filter(([key, n]) => ["landscape","supplement","novelty","feedback","full_text"].includes(key) && Number.isInteger(n) && n >= 0 && n <= 100));
  result.library_status = list(value.library_status).slice(0,5).map(row => record(row, ["library","status","records","skipped","pdf_count","reused_records","newly_acquired_records","newly_selected_records","selected_records","observed_records","version","manifest_sha256"]));
  result.publications = list(value.publications).slice(0,300).map(row => ({...record(row, ["paper_id","kind","aliases","warnings"]),
    snapshots:list(row.snapshots).slice(0,100).map(snapshot => record(snapshot, ["snapshot_id","source_id","provider","title","url","retrieved_at","scope","doi","pmid","pmcid","version","year","publication_status","text_sha256","origin","authors","journal"]))}));
  result.links = list(value.links).slice(0,300).map(row => ({...record(row, ["claim_id","hypothesis_id","version","evidence_id","source_id","paper_id","snapshot_id","query_ids","polarity","scope","study_system","condition","endpoint","strength","span_start","span_end","span_sha256","location"]),
    ...(row.claim_relation === undefined ? {} : {claim_relation:publicClaimRelation(row.claim_relation)})}));
  return result;
}
function researchObservationCounts(run) {
  const co = run?.co_scientist || {}, flow = co.research_flow || {}, groups = list(flow.publications), sources = list(run?.sources);
  const ingested = new Set(sources.map(s => s.source_id)), toolIds = new Set(list(co.tool_calls).flatMap(t => list(t.sources).map(s => s.source_id)));
  const papers = groups.filter(g => (g.kind || "paper") === "paper"), inRun = g => list(g.snapshots).some(s => ingested.has(s.source_id));
  const accepted = list(run?.evidence).filter(e => e.validated_span === true);
  const counts = {canonical_paper_count:papers.length, database_group_count:groups.filter(g => g.kind === "database").length,
    local_document_group_count:groups.filter(g => g.kind === "local_document").length,
    ingested_paper_group_count:papers.filter(inRun).length, tool_observation_only_paper_group_count:papers.filter(g => !inRun(g) && list(g.snapshots).some(s => toolIds.has(s.source_id))).length,
    paper_count:sources.filter(s => ["abstract","full_text"].includes(s.evidence_scope)).length,
    database_source_count:sources.filter(s => s.evidence_scope === "database").length,
    accepted_citation_count:accepted.length, accepted_citation_source_count:new Set(accepted.map(e => e.source_id)).size,
    formal_hypothesis_review_count:list(co.reviews).length, query_plan_count:list(flow.queries).length,
    executed_literature_query_count:0, executed_full_text_count:0, reused_query_count:0, unexecuted_query_count:0, unknown_execution_query_count:0};
  for (const q of list(flow.queries)) {
    if (q.status === "reused" || q.stage === "reuse") counts.reused_query_count++;
    else if (q.request_count > 0 || q.cache_hits > 0) counts[q.stage === "full_text" ? "executed_full_text_count" : "executed_literature_query_count"]++;
    else if (["planned","pending","skipped","budget_exhausted","cancelled"].includes(q.status)) counts.unexecuted_query_count++;
    else counts.unknown_execution_query_count++;
  }
  return counts;
}
const FAILURE_NUMBER_KEYS = ["required_bytes","remaining_bytes","remaining_seconds","required_output_bytes","remaining_output_bytes","required_calls","remaining_calls","reserve_calls","reserve_input_bytes","reserve_output_bytes","reserve_seconds","required_seconds","reserved_bytes","reserved_calls","reserved_output_bytes","reserved_seconds","inflight_output_bytes"];
function publicProviderFailure(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== 1 ||
      !["launch_error","process_nonzero","answer_missing","answer_empty","timeout","json_invalid","schema_invalid","read_error"].includes(value.category) ||
      !["executable_lookup","prepare","process","output_stat","output_read","json_parse","schema_validation"].includes(value.stage) ||
      value.origin !== "provider_call" || !["codex","responses","compatible"].includes(value.backend)) return {};
  const result = {version:1,category:value.category,stage:value.stage,origin:value.origin,backend:value.backend};
  if (typeof value.elapsed_seconds === "number" && Number.isFinite(value.elapsed_seconds) && value.elapsed_seconds >= 0 && value.elapsed_seconds <= 1e9) result.elapsed_seconds = value.elapsed_seconds;
  if (value.returncode === null || Number.isInteger(value.returncode) && value.returncode >= -2147483648 && value.returncode <= 2147483647) result.returncode = value.returncode;
  if (value.signal === null || Number.isInteger(value.signal) && value.signal >= 1 && value.signal <= 127 && result.returncode === -value.signal) result.signal = value.signal;
  for (const key of ["output_exists","output_is_file"]) if (value[key] === null || typeof value[key] === "boolean") result[key] = value[key];
  if (value.output_size_bytes === null || Number.isInteger(value.output_size_bytes) && value.output_size_bytes >= 0 && value.output_size_bytes < 1e18) result.output_size_bytes = value.output_size_bytes;
  if (["not_checked","observed","missing","error"].includes(value.output_stat_status)) result.output_stat_status = value.output_stat_status;
  return result;
}
function providerFailureFields(value) {
  const receipt = publicProviderFailure(value?.provider_failure);
  return Object.keys(receipt).length ? {provider_failure:receipt} : {};
}
function failureDetailTitle(value) {
  const failure = publicBudgetFailure(value);
  const provider = Object.keys(providerFailureFields(failure)).length || ["provider_failure","provider_timeout","optional_provider_failure"].includes(failure.reason);
  return (provider ? "Model call failed" : list(value?.record_types).includes("optional_skip") ? "Optional stage skipped" : "Budget stop") + " · " + [failure.receipt_id, failure.operation_id, failure.stage].filter(Boolean).join(" · ");
}
function publicBudgetFailure(value) {
  const result = publicFields(value, ["reason","stage","receipt_id","action_id","operation_id","status","skip_reason","accounting_unit"]);
  for (const key of ["blocked_before_provider","prior_timeout"]) if (typeof value?.[key] === "boolean") result[key] = value[key];
  for (const key of FAILURE_NUMBER_KEYS) if (typeof value?.[key] === "number" && Number.isFinite(value[key]) && value[key] >= 0 && value[key] < 1e18) result[key] = value[key];
  for (const key of ["call_allowance","effective_available_output_bytes"]) {
    if (Number.isInteger(value?.[key]) && value[key] >= 0 && value[key] < 1e18) result[key] = value[key];
    else if (key === "call_allowance" && value?.[key] === null) result[key] = null;
  }
  if (Array.isArray(value?.output_limit_categories)) result.output_limit_categories = [...new Set(value.output_limit_categories.filter(item => ["single_call","global","future_reserved","parallel"].includes(item)))];
  Object.assign(result, providerFailureFields(value));
  return result;
}
function publicFailureRecords(co) {
  co = co && typeof co === "object" && !Array.isArray(co) ? co : {};
  const budget = co.budget || {}, records = [], identified = new Map(), mirrored = new Map();
  const canonical = value => JSON.stringify(value, function(key,item) {
    return item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(k => [k,item[k]])) : item;
  });
  function append(row, kind) {
    const identity = row.receipt_id || row.action_id || row.operation_id, marker = identity ? canonical([identity,row]) : null;
    if (marker && identified.has(marker)) {const old = identified.get(marker); if (!old.record_types.includes(kind)) old.record_types.push(kind); return;}
    const record = {...row,record_types:[kind]}; records.push(record); if (marker) identified.set(marker,record);
  }
  for (const [kind,rows] of [["budget_failure",co.budget_failures],["budget_failure",budget.budget_failures],
      ["budget_failure",budget.budget_failure ? [budget.budget_failure] : []],["optional_skip",co.optional_skips],["optional_skip",budget.optional_skips]]) {
    const previous = mirrored.get(kind) || new Map(), occurrences = new Map();
    for (const raw of list(rows).slice(0,100)) {
      const row = publicBudgetFailure(raw); if (!Object.keys(row).length) continue;
      const payload = canonical(row), count = (occurrences.get(payload) || 0) + 1; occurrences.set(payload,count);
      if (!(row.receipt_id || row.action_id || row.operation_id) && count <= (previous.get(payload) || 0)) continue;
      append(row,kind);
    }
    for (const [payload,count] of occurrences) previous.set(payload,Math.max(previous.get(payload) || 0,count));
    mirrored.set(kind,previous);
  }
  for (const event of list(co.events).slice(0,300)) {
    if (event?.budget_failure && typeof event.budget_failure === "object") {const row = publicBudgetFailure(event.budget_failure); if (Object.keys(row).length) append(row,"budget_event");}
    const receipt = publicProviderFailure(event?.provider_failure); if (!Object.keys(receipt).length) continue;
    const safe = publicFields(event,["operation_id","title"]), operation = safe.operation_id || "", stage = safe.title || "";
    const matches = records.filter(row => operation && row.operation_id === operation && canonical(row.provider_failure) === canonical(receipt) && (row.stage || "") === stage);
    if (matches.length === 1) {if (!matches[0].record_types.includes("provider_event")) matches[0].record_types.push("provider_event"); continue;}
    append({...operation ? {operation_id:operation} : {}, ...stage ? {stage} : {},provider_failure:receipt},"provider_event");
  }
  return records;
}
function publicBudget(value) {
  const result = publicFields(value, ["token_accounting","provider_billed_tokens","provider_cost","provider_output_cap_enforced"]);
  for (const key of ["llm_calls","http_attempts","tool_calls","input_tokens_reserved","output_tokens_reserved","input_bytes_reserved","output_bytes_reserved","output_bytes_committed","output_bytes_received","output_bytes_inflight","cache_hits","actual_llm_calls","llm_cache_hits","literature_queries","remaining_seconds","elapsed_seconds"]) if (typeof value?.[key] === "number" && Number.isFinite(value[key]) && value[key] >= 0 && value[key] < 1e18) result[key] = value[key];
  result.limits = publicFields(value?.limits, ["max_targets","literature_max_candidates","max_papers","max_searches","max_llm_calls","max_md_jobs_total","timeout_seconds","max_hypotheses","max_http_requests","max_tool_calls","max_input_tokens","max_output_tokens"]);
  if (value?.budget_failure && typeof value.budget_failure === "object") result.budget_failure = publicBudgetFailure(value.budget_failure);
  for (const key of ["budget_failures","optional_skips"]) result[key] = list(value?.[key]).slice(0,100).map(publicBudgetFailure);
  return result;
}
function publicUserNameDeclarations(value) {
  return list(value).filter(row => row?.source === "brief.existing_candidates" && Number.isInteger(row.source_index) && row.source_index >= 0 && row.source_index < 10).slice(0,10)
    .map(row => publicFields(row, ["label","full_name","abbreviation","source","source_index","run_id"]));
}
function publicEntityReference(value) {
  return {...publicFields(value, ["entity_id","candidate_id","name","identity_status","identity_revision","scientific_assessment","identity_assessment_pending","identity_priority_before_update"]),
    aliases:list(value?.aliases).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,50),
    user_name_declarations:publicUserNameDeclarations(value?.user_name_declarations),
    source_ids:list(value?.source_ids).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,50),
    hypothesis_versions:list(value?.hypothesis_versions).slice(0,50).map(row => ({...publicFields(row, ["hypothesis_id","version"]),
      historical_identity_gaps:list(row.historical_identity_gaps).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,50)}))};
}
function artifactLinks(ids, title) {
  const node = el("div", "evidence-links"); node.append(el("span", "", title));
  if (!list(ids).length) node.append(el("span", "muted", "No artifact references recorded"));
  for (const ref of list(ids)) {
    const target = artifactTarget(ref);
    if (target) node.append(button(ref, () => openArtifact(ref), "evidence-chip artifact-link"));
    else {const missing = el("span", "reference-unavailable", ref + " · content not recorded"); node.append(missing);}
  }
  return node;
}
function observationExport(run) {
  const co = run.co_scientist || {};
  const document = {kind: "public_collaboration_observation", run_id: run.run_id, status: run.status,
    synthetic: syntheticRun(run), updated_at: run.updated_at, task_intent_receipt:publicTaskIntent(run.task_intent_receipt), evidence_digest:publicEvidenceDigest(run.evidence_digest),
    agents: list(co.agents).map(a => ({agent_id:a.agent_id, role:a.role, execution:a.execution, status:a.status, task:safeEventSummary({summary:a.task})})),
    events: list(co.events).map(e => ({event_id:e.event_id, sequence:e.sequence, agent_id:e.agent_id, kind:e.kind,
      status:e.status, operation_id:e.operation_id || "", operation_status:e.operation_status || "", title:safeEventSummary({summary:e.title}),
      summary:safeEventSummary(e), input_summary:e.input_summary ? safeEventSummary({summary:e.input_summary}) : "", input_artifact_ids:list(e.input_artifact_ids).filter(id => typeof id === "string" && safeEventSummary({summary:id}) === id),
      artifact_ids:list(e.artifact_ids).filter(id => typeof id === "string" && safeEventSummary({summary:id}) === id), created_at:e.created_at,
      ...(e.budget_failure ? {budget_failure:publicBudgetFailure(e.budget_failure)} : {}), ...providerFailureFields(e)})),
    tool_plans:list(co.tool_plans).map(publicToolPlan), tool_calls:list(co.tool_calls).map(publicToolObservation),
    budget:publicBudget(co.budget), budget_failures:list(co.budget_failures).slice(0,100).map(publicBudgetFailure), optional_skips:list(co.optional_skips).slice(0,100).map(publicBudgetFailure), failure_records:publicFailureRecords(co),
    candidate_assessments:list(run.candidate_assessments).slice(0,100).map(publicCandidateAssessment),
    candidate_evaluations:list(run.candidate_evaluations).slice(0,100).map(publicCandidateEvaluation).filter(Boolean),
    candidate_evaluation_records:list(run.candidate_evaluation_records).slice(0,100).map(publicCandidateEvaluationRecord),
    entity_references:list(co.entity_references).slice(0,100).map(publicEntityReference), summary:researchObservationCounts(run), research_flow:publicResearchFlow(co.research_flow)};
  const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], {type:"application/json"}));
  const link = el("a"); link.href = url; link.download = run.run_id + "-collaboration.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function publicEvidenceAssociation(row) {
  return {...publicFields(row,["claim_id","claim_kind","hypothesis_id","version","review_id","verdict","evidence_id","source_id","polarity","claim_relation","relation_basis","assessment_scope","relation_schema_version"]),query_ids:publicStrings(row?.query_ids)};
}
function publicCandidateAssessment(value) {
  const result = publicFields(value, ["candidate_id","entity_id","name","origin","identity_status","identity_revision","decision","population_requested","endpoint_requested","delivery_context","workflow_status","scientific_status","status_note","assessment_status"]);
  result.hypothesis_expansion_status = ["started","not_run"].includes(value?.hypothesis_expansion_status) ? value.hypothesis_expansion_status : "not_recorded";
  result.validated_evaluation = publicCandidateEvaluation(value?.validated_evaluation);
  if (result.validated_evaluation && ["candidate_id","entity_id","identity_revision"].some(key =>
    result.validated_evaluation[key] !== result[key])) result.validated_evaluation = null;
  result.assessment_status = result.validated_evaluation ? "completed_review" : "unreviewed";
  for (const key of ["evidence_ids","opposing_evidence_ids","unknowns","next_questions"]) result[key] = list(value?.[key]).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,100);
  result.evidence_scope = list(value?.evidence_scope).slice(0,100).map(row => ({...publicFields(row, ["evidence_id","source_id","polarity","study_system","condition","endpoint","source_scope","title","association"]),
    limitations:list(row.limitations).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,50)}));
  result.hypothesis_versions = list(value?.hypothesis_versions).slice(0,50).map(row => publicFields(row, ["hypothesis_id","version","review_state"]));
  result.review_associations = list(value?.review_associations).slice(0,100).map(publicEvidenceAssociation);
  result.observation_support = list(value?.observation_support).slice(0,100).filter(row => row?.relation_schema_version === 2 &&
    row.assessment_scope === "source_observation" && row.claim_relation === "supports" && row.relation_basis === "exact_source_report_only" && row.verdict === "supported").map(publicEvidenceAssociation);
  result.scoped_claim_gaps = list(value?.scoped_claim_gaps).slice(0,100).map(row => ({...publicFields(row, ["claim_id","hypothesis_id","version","kind","state"]), evidence_ids:publicStrings(row.evidence_ids), gaps:publicStrings(row.gaps)}));
  result.recorded_gap_notes = list(value?.recorded_gap_notes).slice(0,100).map(row => ({...publicFields(row, ["origin","hypothesis_id","version","text","scope_status"]), claim_ids:publicStrings(row.claim_ids)}));
  return result;
}
function publicEvaluationFields(value, keys, maximum = 1200) {
  const row = publicFields(value, keys);
  for (const key of keys) if (typeof value?.[key] === "string" && value[key].length <= maximum &&
    (value[key] === "" || safeEventSummary({summary:value[key]}, maximum) === value[key])) row[key] = value[key];
  return row;
}
function publicEvaluationStrings(value) {return list(value).slice(0,100).filter(x => typeof x === "string" && x.length <= 1200 && safeEventSummary({summary:x}) === x);}
const EVALUATION_KEYS = ["schema_version","assessment_id","version","candidate_id","entity_id","identity_revision","identity_fingerprint","task_fingerprint","conclusion_fingerprint","evidence_fingerprint","status","review_id","review_operation_id","conclusion","scientific_status"];
function publicCandidateEvaluation(value) {
  if (!value || value.schema_version !== 1 || value.status !== "completed_review" ||
    !["limited_source_findings","mixed_evidence","no_supported_benefit","needs_evidence"].includes(value.scientific_status)) return null;
  const row = publicEvaluationFields(value, EVALUATION_KEYS);
  if (!["assessment_id","candidate_id","entity_id","review_id","review_operation_id","conclusion","task_fingerprint","identity_fingerprint","conclusion_fingerprint","evidence_fingerprint"].every(key => typeof row[key] === "string" && row[key]) ||
    !Number.isSafeInteger(row.version) || row.version < 1 || !Number.isSafeInteger(row.identity_revision)) return null;
  for (const key of ["supporting_evidence_ids","opposing_evidence_ids","unknowns","next_questions"]) row[key] = publicEvaluationStrings(value[key]);
  row.findings = list(value.findings).slice(0,100).map(item => ({...publicEvaluationFields(item,["finding_id","statement","applicability"]),
    evidence_ids:publicEvaluationStrings(item.evidence_ids),scope_limitations:publicEvaluationStrings(item.scope_limitations),scope:publicEvaluationFields(item.scope,["study_system","condition","endpoint"],2000)}));
  return row;
}
function publicCandidateEvaluationRecord(value) {
  const row = publicEvaluationFields(value, EVALUATION_KEYS.filter(key => !["review_id","conclusion","scientific_status"].includes(key)).concat("recorded_status"));
  row.final = publicCandidateEvaluation(value?.final); row.status = row.final ? "completed_review" : "unreviewed";
  row.validation_errors = publicEvaluationStrings(value?.validation_errors);
  if (row.final && ["assessment_id","version","candidate_id","entity_id","identity_revision","identity_fingerprint","task_fingerprint","conclusion_fingerprint","evidence_fingerprint"].some(key => row[key] !== row.final[key])) {
    row.final = null; row.status = "unreviewed";
  }
  row.reviews = list(value?.reviews).slice(0,100).map(review => ({...publicEvaluationFields(review,EVALUATION_KEYS.filter(key => !["status","conclusion","scientific_status"].includes(key)).concat("verdict","reason")),
    supporting_evidence_ids:publicEvaluationStrings(review.supporting_evidence_ids),opposing_evidence_ids:publicEvaluationStrings(review.opposing_evidence_ids),
    item_reviews:list(review.item_reviews).slice(0,100).map(item => ({...publicEvaluationFields(item,["finding_id","verdict","reason","applicability"]),evidence_ids:publicEvaluationStrings(item.evidence_ids)}))}));
  return row;
}
function publicStrings(value) {return list(value).filter(x => typeof x === "string" && safeEventSummary({summary:x}) === x).slice(0,100);}
function publicTaskIntent(value) {
  return publicFields(value, ["declared_intent","effective_intent","intent_basis","context_admission","historical_memory_allowed","saved_source_reuse_allowed","candidate_background_allowed","discovery_claim"]);
}
function publicEvidenceDigest(value) {
  if (!value || typeof value !== "object") return null;
  return {...publicFields(value, ["version"]), scope_notes:publicStrings(value.scope_notes),
    counts:{...publicFields(value.counts, ["selected_publications","accepted_citations","accepted_publications","randomized_trial_publications","review_publications"]), polarity_citations:publicFields(value.counts?.polarity_citations, ["supports","opposes","mixed","context"])},
    reference_coverage:publicResearchFlow({reference_coverage:value.reference_coverage}).reference_coverage,
    publications:list(value.publications).slice(0,100).map(row => ({...publicFields(row, ["publication_id","kind","source_design","design_basis","design_does_not_classify_each_reported_experiment"]), identifiers:publicStrings(row.identifiers), source_ids:publicStrings(row.source_ids),
      accepted_citations:list(row.accepted_citations).slice(0,100).map(c => ({...publicFields(c, ["evidence_id","source_id","polarity","study_system","system_layer","condition","endpoint","intervention_mode","candidate_mention_is_not_intervention_support","location"]), explicit_candidate_mentions:publicStrings(c.explicit_candidate_mentions), limitations:publicStrings(c.limitations)}))}))};
}
function publicFields(value, keys) {
  const result = {}; if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  for (const key of keys) {
    const raw = value[key];
    if (typeof raw === "string" && raw.length <= 1200 && (raw === "" || safeEventSummary({summary:raw}) === raw)) result[key] = raw;
    else if (typeof raw === "number" && Number.isFinite(raw) || typeof raw === "boolean" || raw === null) result[key] = raw;
  }
  return result;
}
const TOOL_PARAM_KEYS = ["resource", "chembl_id", "molecule_chembl_id", "target_chembl_id", "query", "entity", "entity_type", "disease_id",
  "molecule_name", "target_symbol", "species", "gene_symbol", "name", "inchikey", "target_accession", "uniprot_id", "taxon_id", "go_id", "evidence_code", "aspect", "limit", "page"];
const HTTP_PARAM_KEYS = ["query", "q", "format", "size", "limit", "offset", "page", "pageSize", "resultType", "sort_date",
  "molecule_chembl_id", "target_chembl_id", "assay_chembl_id", "organism", "target_organism", "assay_organism", "species", "molecule_structures__standard_inchi_key",
  "target_components__accession", "geneProductId", "taxonId", "taxonUsage", "goId", "goUsage", "evidenceCode", "aspect"];
function publicIdentity(value) {
  const result = publicFields(value, ["status", "entity_type", "query", "species", "selected_id", "reason", "inchikey", "molecule_chembl_id", "target_chembl_id"]);
  result.candidates = list(value?.candidates).slice(0, 10).map(row => ({...publicFields(row, ["id", "cid", "accession", "name", "pref_name", "species", "target_type", "inchikey", "parent_id"]),
    accessions:list(row.accessions).filter(id => typeof id === "string" && /^[A-Z0-9-]+$/.test(id)).slice(0, 10)}));
  return result;
}
function publicToolObservation(value) {
  return {...publicFields(value, ["tool_name", "provider", "status", "retrieved_at", "call_id", "parent_call_id", "plan_id", "gap_id", "request_count", "cache_hits", "stop_reason"]),
    parameters:publicFields(value?.parameters, TOOL_PARAM_KEYS), identity:publicIdentity(value?.identity),
    record_count:list(value?.records).length,
    source_ids:list(value?.sources).map(row => row.source_id).filter(id => typeof id === "string" && safeEventSummary({summary:id}) === id),
    http_trace:list(value?.http_trace).slice(0, 100).map(row => ({...publicFields(row, ["endpoint", "method", "cache_key", "attempt", "retry_index", "cache_hit", "outcome", "http_status"]), parameters:publicFields(row.parameters, HTTP_PARAM_KEYS)})),
    warnings:list(value?.warnings).filter(w => typeof w === "string" && safeEventSummary({summary:w}) === w)};
}
function publicToolPlan(value) {
  return {...publicFields(value, ["plan_id", "gap_id", "origin", "reason", "status", "stop_reason", "duplicate_of"]),
    request:{...publicFields(value?.request, ["name"]), arguments:publicFields(value?.request?.arguments, TOOL_PARAM_KEYS)},
    identity:publicIdentity(value?.identity), candidate_link:publicFields(value?.candidate_link, ["status","query","reason","candidate_id","entity_id","verification"]),
    identity_application:publicFields(value?.identity_application, ["status","entity_id","candidate_id","identity_revision","scientific_assessment","reason"]), call_ids:list(value?.call_ids).filter(id => /^tool_\d+$/.test(id)),
    hypothesis_ids:list(value?.hypothesis_ids).filter(id => /^h_[a-z0-9_]+$/i.test(id))};
}
function renderCollaboration() {
  const co = coData(), run = state.selected, root = $("collaborationContent"); root.replaceChildren();
  $("agentEventToolbar").hidden = !co;
  if (!co) {coEmpty(root, "No Agent collaboration records"); $("agentTimeline").replaceChildren(); return;}
  const model = collaborationModel(run);
  if (state.collaborationRun !== run.run_id) {
    state.collaborationRun = run.run_id; state.agent = ""; state.eventLimit = 8; $("agentEventFilter").value = "all";
    if ($("agentActivityKind")) $("agentActivityKind").value = "all";
  }
  const defaultAgent = model.working[0] || model.agents.find(a => a.agent_id === model.latest?.agent_id) || model.agents[0];
  if (!model.agents.some(a => a.agent_id === state.agent)) state.agent = defaultAgent?.agent_id || "";
  root.append(coModeMarker(run));
  root.dataset.active = String(model.active);
  const current = el("section", "current-action " + String(run.status).replace(/[^a-z_]/g, "")), title = model.active ? model.working.length ? "Working now · " + model.working.map(a => agentRole(a)[0]).join(" + ") : "Waiting for the next execution record" : "Run ended · viewing saved records";
  current.append(runStateBadge(run.status), el("h2", "", title), el("p", "", model.active && model.working.length ? model.working.map(a => systemText(a.task) || "Task summary not recorded").join("; ") : systemText(model.latest?.title) || "No saved actions"));
  if (model.latest) current.append(el("p", "confirmation-note", "Latest record #" + model.latest.sequence + " · " + localDate(model.latest.created_at) + " · " + displayActionSummary(model.latest)));
  if (!model.active && list(run.errors).length) current.append(el("p", "confirmation-note", "Saved stop / gap summary: " + list(run.errors).slice(0, 2).map(summary => displayActionSummary({summary})).join("; ")));
  root.append(current);
  const summary = el("div", "workspace-summary");
  const counts = researchObservationCounts(run);
  for (const [name, count, view] of [["Hypotheses", latestHypotheses(list(co.hypotheses)).length, "hypotheses"], ["Accepted citations", counts.accepted_citation_count, "evidence"], ["Formal hypothesis reviews", counts.formal_hypothesis_review_count, "hypotheses"], ["Tool attempts", list(co.tool_calls).length, "tools"], ["Plans", list(co.contracts).length, "contracts"]]) summary.append(button(count + " " + name + " →", () => navigate(view), "button secondary small"));
  summary.append(button("Export collaboration record ↓", () => observationExport(run), "button secondary small"));
  root.append(summary);
  const recent = el("div", "recent-actions"); recent.setAttribute("aria-label", "Three most recent saved activities");
  for (const event of model.events.slice(-3).reverse()) {
    const row = el("div", "recent-action"), action = button("#" + event.sequence + " · " + systemText(event.title), () => {
      if (model.agents.some(a => a.agent_id === event.agent_id)) {state.agent = event.agent_id; renderCollaborationSelection();}
    }, "text-button"); action.dataset.focusKey = "recent-" + event.event_id;
    row.append(el("small", "", localDate(event.created_at)), action, recordedStatus(event)); recent.append(row);
  }
  const workbench = el("div", "workbench-grid"), flowPanel = el("section", "panel flow-panel");
  flowPanel.append(el("h2", "", "Research roles"), el("p", "confirmation-note", "Select a role to view its complete public record. States come from saved data for this run."));
  const picker = el("div", "agent-picker"), pickerLabel = el("label", "", "View role record"), select = el("select");
  pickerLabel.htmlFor = "agentPicker"; select.id = "agentPicker";
  optionList(select, model.agents, agent => agent.agent_id,
    agent => agentRole(agent)[0] + " · " + roleRecordStatus(agent, run).textContent, state.agent);
  select.addEventListener("change", () => {state.agent = select.value; renderCollaborationSelection();});
  picker.append(pickerLabel, select); flowPanel.append(picker);
  const board = el("div", "agent-flow"); board.setAttribute("aria-label", "Select a role to view recorded inputs, tools and artifacts");
  const roleOrder = Object.keys(AGENT_ROLES);
  for (const agent of model.agents.slice().sort((a,b) => roleOrder.indexOf(a.agent_id) - roleOrder.indexOf(b.agent_id))) {
    const [name, subtitle] = agentRole(agent), node = el("button", "flow-node " + String(agent.status || "").replace(/[^a-z_]/g, ""));
    node.type = "button"; node.dataset.focusKey = "agent-" + agent.agent_id; node.dataset.agentId = agent.agent_id;
    node.setAttribute("aria-pressed", String(state.agent === agent.agent_id)); node.setAttribute("aria-controls", "agentNodeDetail");
    node.append(el("strong", "", name), roleRecordStatus(agent, run), el("span", "", subtitle), el("small", "", agent.execution === "rule_based" ? "Rule-based module" : agent.execution === "orchestrator" ? "Orchestrator" : "Model role"));
    node.addEventListener("click", () => {state.agent = agent.agent_id; renderCollaborationSelection();}); board.append(node);
  }
  if (!model.agents.length) board.append(el("p", "muted", "Role execution states are awaiting recorded events."));
  flowPanel.append(board, el("p", "confirmation-note", "Research roles use separate contexts with a shared model backend. Nodes display saved public actions; review status is linked to each result."));
  const handoffs = el("details", "recorded-handoffs"); handoffs.dataset.uiKey = "recorded-handoffs";
  handoffs.append(el("summary", "", "Recorded artifact handoffs · " + model.handoffs.length));
  if (!model.handoffs.length) handoffs.append(el("p", "confirmation-note", "The diagram shows workflow dependencies. Stage handoff records are awaiting saved links for this run."));
  for (const handoff of model.handoffs) {
    const from = model.agents.find(a => a.agent_id === handoff.from), to = model.agents.find(a => a.agent_id === handoff.to), row = el("details", "flow-connector");
    row.dataset.uiKey = "handoff-" + handoff.from + "-" + handoff.to;
    row.append(el("summary", "", (from ? agentRole(from)[0] : handoff.from) + " → " + (to ? agentRole(to)[0] : handoff.to) + " · " + handoff.refs.length + " references"), artifactLinks(handoff.refs, "Handoff artifacts"));
    handoffs.append(row);
  }
  flowPanel.append(handoffs);
  const nodeDetail = el("section", "panel node-detail"); nodeDetail.id = "agentNodeDetail";
  workbench.append(flowPanel, nodeDetail); root.append(workbench, recent);
  renderCollaborationSelection();
  const anchor = co.anchor || {}, anchorPanel = el("details", "panel anchor-panel"); anchorPanel.dataset.uiKey = "anchor";
  anchorPanel.append(el("summary", "", "Research boundaries & input summary"), facts([["Disease / phenotype", [anchor.disease, anchor.phenotype].filter(Boolean).join(" · ")],
    ["Research species", anchor.species], ["Specified chassis", anchor.chassis_name], ["Conditions", systemText(anchor.condition)], ["Success criterion", systemText(anchor.success_criterion)]]));
  add(anchorPanel, notifications(list(anchor.constraints).map(systemText), "Constraints & resources")); add(anchorPanel, notifications(list(anchor.non_goals).map(systemText), "Explicit non-goals"));
  const budget = publicBudget(co.budget), panel = el("details", "panel budget-summary"); panel.dataset.uiKey = "budget";
  panel.append(el("summary", "", "Budget & limitations · model requests reserved " + text(budget.llm_calls) + " / HTTP " + text(budget.http_attempts) + " / tools " + text(budget.tool_calls)),
    facts([["Model requests reserved", budget.llm_calls], ["Actual model calls", budget.actual_llm_calls ?? "Not distinguished in legacy records"], ["HTTP request attempts", budget.http_attempts], ["Cache hits", budget.cache_hits], ["Conservative input reservation (UTF-8 bytes)", budget.input_bytes_reserved ?? budget.input_tokens_reserved], ["Conservative output reservation (UTF-8 bytes)", budget.output_bytes_reserved ?? budget.output_tokens_reserved]]),
    el("p", "confirmation-note", "Input/output quotas reserve UTF-8 bytes; provider token usage and billing are separate measures. Failures, pagination and retries consume HTTP budget."));
  if (budget.output_bytes_received != null) panel.append(facts([["Structured-result bytes (UTF-8, including budget-discarded results)", budget.output_bytes_received]]));
  if (budget.output_bytes_inflight != null) panel.append(facts([["In-flight output reservation (UTF-8 bytes)", budget.output_bytes_inflight]]));
  const failures = publicFailureRecords(co);
  for (const failure of failures) panel.append(detail(failureDetailTitle(failure), failure));
  if (failures.length) panel.append(el("p", "confirmation-note", "Input/output are recorded in UTF-8 bytes. Action IDs link the same stop or skip; legacy records with missing IDs retain an unlinked status."));
  add(panel, notifications(list(co.limitations).map(summary => displayActionSummary({summary})), "Limitations of this run"));
  const supporting = el("div", "collaboration-supporting"); supporting.append(anchorPanel, panel); root.append(supporting);
  if (co.research_flow) {
    const researchPanel = el("details", "panel"); researchPanel.dataset.uiKey = "research-flow";
    researchPanel.append(el("summary", "", "Literature map & claim-level search · " + counts.canonical_paper_count + " paper groups / " + counts.database_group_count + " database groups / " + counts.query_plan_count + " query plans"),
      facts([["Ingested paper groups / tool-observation-only groups", counts.ingested_paper_group_count + " / " + counts.tool_observation_only_paper_group_count], ["Executed searches / full-text retrievals", counts.executed_literature_query_count + " / " + counts.executed_full_text_count], ["Reused / unexecuted / execution unrecorded", counts.reused_query_count + " / " + counts.unexecuted_query_count + " / " + counts.unknown_execution_query_count], ["Accepted citations / citation sources / formal hypothesis reviews", counts.accepted_citation_count + " / " + counts.accepted_citation_source_count + " / " + counts.formal_hypothesis_review_count]]),
      el("p", "confirmation-note", "Query plans show reused, executed and pending queries. Search counts use saved HTTP/cache receipts. Tool observations and accepted evidence are listed separately, with publication identity, search scope and novelty-review status. Missing legacy execution fields remain unrecorded."),
      artifactLinks(["research_flow"], "Sources, claims, novelty & revision chain")); root.append(researchPanel);
  }
  if (co.seed?.parent_run_id) root.append(callout("This run continues " + co.seed.parent_run_id + "'s saved sources and drafts, retaining their original retrieval provenance."));
  if (!model.active && list(co.hypotheses).length && !syntheticRun(run)) root.append(button("Review existing hypotheses · start budgeted continuation", async () => {
    const project = state.project, parentRun = run.run_id, epoch = state.selectionEpoch;
    const next = await api(base(project) + "/runs/" + enc(parentRun) + "/continue-hypotheses", "POST", {max_llm_calls:6, timeout_seconds:900});
    if (!selectionCurrent(epoch, project, parentRun)) {notify("Continuation created. Find it in the original project's saved runs."); return;}
    selectRun(next.run_id, next); navigate("collaboration"); await loadRuns(next.run_id);
    notify("Continuation created with up to six model calls, using the saved retrieval and generation records for renewed review.");
  }));
  optionList($("agentEventFilter"), list(co.agents), agent => agent.agent_id,
    agent => agentRole(agent)[0] + " · " + agent.agent_id, $("agentEventFilter").value, "All roles");
  if (!$("agentActivityKind")) {
    const select = el("select"); select.id = "agentActivityKind"; select.setAttribute("aria-label", "Filter by activity type");
    for (const [value, title] of [["all", "All activity"], ["tool", "Tool calls"], ["llm", "Model calls"], ["problems", "Failed / interrupted / skipped"]]) {const option = el("option", "", title); option.value = value; select.append(option);}
    select.addEventListener("change", () => {state.eventLimit = 8; renderTimeline();}); $("agentEventToolbar").append(select);
  }
  // Placeholder has an empty value; renderTimeline treats it as all, including coordinator events.
  renderTimeline();
}
function renderCollaborationSelection() {
  const co = coData(), root = $("agentNodeDetail"); if (!root || !co) return;
  root.replaceChildren(); const agent = list(co.agents).find(a => a.agent_id === state.agent);
  document.querySelectorAll(".flow-node").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.agentId === state.agent)));
  if ($("agentPicker")) $("agentPicker").value = state.agent;
  if (!agent) {root.append(el("p", "muted", "Select a recorded role to view details.")); return;}
  const records = agentArtifacts(state.selected, agent.agent_id), events = collaborationModel(state.selected).events.filter(e => e.agent_id === agent.agent_id), latest = events.at(-1);
  const heading = el("div", "entity-top"), name = el("div");
  name.append(el("h2", "", agentRole(agent)[0]), el("span", "entity-id", agentRole(agent)[1] + " · " + agent.agent_id));
  heading.append(name, roleRecordStatus(agent, state.selected));
  root.append(heading, el("p", "", displayActionSummary({summary:agent.task || "Task not recorded"})));
  const historyHeading = el("div", "role-history-heading");
  historyHeading.append(el("h3", "", "Public activity record"), el("span", "confirmation-note", "From start to present · " + events.length + " records"));
  root.append(historyHeading);
  root.append(el("p", "confirmation-note", "Event states describe the recorded moment. Current role states and call states are shown separately."));
  if (!events.length) root.append(el("p", "muted", "Public execution events for this role are awaiting saved records."));
  else {
    const history = el("ol", "role-history"); history.id = "agentHistory"; history.tabIndex = 0;
    history.setAttribute("aria-label", agentRole(agent)[0] + " public activity from start to present");
    for (const event of events) {
      const row = el("li", "role-history-item"), head = el("div", "timeline-event-top");
      head.append(el("strong", "", "#" + text(event.sequence) + " · " + displayActionSummary({summary:event.title || event.kind})), recordedStatus(event));
      row.append(el("time", "", localDate(event.created_at)), head, el("p", "", displayActionSummary(event)));
      const context = el("details", "role-event-context"); context.dataset.uiKey = "role-event-" + event.event_id;
      context.append(el("summary", "", "Stage inputs, calls & artifacts"), el("p", "confirmation-note", displayActionSummary({summary:event.input_summary || "No input summary recorded for this stage."})),
        facts([["Role record", label(event.status)], ["Call ID", event.operation_id || "Not recorded"], ["Call status", event.operation_status ? label(event.operation_status) : "Separate call status not recorded"]]),
        artifactLinks(list(event.input_artifact_ids), "Input references"), artifactLinks(list(event.artifact_ids), "Artifact references"));
      row.append(context); history.append(row);
    }
    root.append(history);
  }
  root.append(el("h3", "", "Inputs & handoffs"), el("p", "confirmation-note", displayActionSummary({summary:records.inputSummary})), artifactLinks(records.inputs, "Stage inputs"),
    el("h3", "", "Outputs & artifacts"), artifactLinks(records.outputs, "Event-linked artifacts"));
  if (!records.outputs.length) root.append(el("p", "confirmation-note", ["queued", "running"].includes(agent.status) && ["queued", "running"].includes(state.selected?.status) ? "This role has not saved output references yet; waiting for actual results." : "Summary counts open this run's saved results. Role and call associations are unrecorded for these artifacts."));
  const operations = collaborationModel(state.selected).operations.filter(e => e.agent_id === agent.agent_id && ["llm", "tool"].includes(e.kind));
  const calls = el("details", "tool-mini"); calls.dataset.uiKey = "role-calls-" + agent.agent_id;
  calls.append(el("summary", "", "Model / tool calls · " + operations.length + " linkable records"));
  if (!operations.length) {
    const actions = events.filter(e => ["llm", "tool"].includes(e.kind)).slice(-2);
    if (!actions.length) calls.append(el("p", "muted", "No calls recorded for this role."));
    for (const event of actions) {
      const row = el("div", "tool-mini-row"); row.append(el("span", "", systemText(event.title)), recordedStatus(event));
      calls.append(row, el("small", "", displayActionSummary(event)));
    }
    if (actions.length) calls.append(el("p", "confirmation-note", "Legacy events lack per-call IDs/separate states. Original role-event states are shown."));
  }
  for (const event of operations) {
    const row = el("div", "tool-mini-row"); row.append(el("span", "", systemText(event.title)), recordedStatus(event));
    if (event.kind === "tool" && /^tool_\d+$/.test(event.operation_id)) {
      const record = list(co.tool_calls)[Number(event.operation_id.slice(5))];
      if (record) {const action = button("View returned record →", () => openToolRecord(record), "text-button"); action.dataset.focusKey = "tool-" + event.operation_id; row.append(action);}
    }
    row.append(el("small", "entity-id", event.operation_id)); calls.append(row);
  }
  root.append(calls);
  root.append(el("p", "confirmation-note", "Role states and individual call states are separate. Public summaries do not include private model reasoning."));
  if (events.some(e => e.kind === "tool") && list(co.tool_calls).length) {
    const returns = el("details", "tool-mini"); returns.dataset.uiKey = "tool-returns-" + agent.agent_id;
    returns.append(el("summary", "", "Saved tool returns this run · " + co.tool_calls.length + " calls"),
      el("p", "confirmation-note", "Records follow saved order; per-call associations in this history remain unrecorded."));
    for (const [index, record] of list(co.tool_calls).slice(-5).entries()) {
      const row = el("div", "tool-mini-row"); row.append(button(record.tool_name, () => openToolRecord(record), "text-button"), status(record.status),
        el("small", "", list(record.records).length + " records / HTTP " + text(record.request_count) + " / cache " + text(record.cache_hits)));
      row.querySelector("button").dataset.focusKey = "tool-return-" + (Math.max(0, co.tool_calls.length - 5) + index); returns.append(row);
    }
    root.append(returns);
  }
  if (latest) root.append(el("p", "confirmation-note", "Latest action: " + systemText(latest.title) + " · " + displayActionSummary(latest)));
  root.append(facts([["Context-call attempts", agent.invocations], ["Tool calls", agent.tool_calls], ["Last updated", agent.updated_at ? localDate(agent.updated_at) : "Not recorded"]]));
}
function openToolRecord(record) {
  const root = $("dialogContent"); root.replaceChildren(el("h2", "", record.tool_name || "Tool return record"), status(record.status),
    facts([["Provider", record.provider], ["Returned records", list(record.records).length], ["HTTP attempts", record.request_count], ["Cache hits", record.cache_hits]]),
    detail("Identity & execution provenance", {call_id:record.call_id || "Not recorded in history", parent_call_id:record.parent_call_id || "No parent call recorded", plan_id:record.plan_id || "Not recorded in history", gap_id:record.gap_id || "Not recorded in history",
      identity:record.identity || {}, parameters:record.parameters, http_trace:record.http_trace || [], pagination:record.pagination || {}, stop_reason:record.stop_reason || "Not recorded"}),
    detail("Saved scientific records & sources", {records:record.records, sources:record.sources}));
  const plan = list(coData()?.tool_plans).find(p => p.plan_id === record.plan_id);
  if (plan) root.append(button("View evidence-gathering plan →", () => openToolPlan(plan), "text-button"));
  add(root, notifications(list(record.warnings).map(systemText), "Call limitations")); showDialog("TOOL · " + text(record.tool_name));
}
function openToolPlan(plan) {
  const root = $("dialogContent"); root.replaceChildren(el("h2", "", "Saved tool plan · " + text(plan.plan_id)), status(plan.status),
    structured(systemText(plan.reason) || "Specific gap not recorded"), facts([["Source", plan.origin], ["Gap", plan.gap_id], ["Stop reason", systemText(plan.stop_reason) || "None"]]),
    detail("Plan inputs & identity decisions", publicToolPlan(plan)));
  for (const call_id of list(plan.call_ids)) {
    const record = list(coData()?.tool_calls).find(r => r.call_id === call_id);
    if (record) root.append(button(call_id + " · " + text(record.tool_name) + " →", () => openToolRecord(record), "text-button"));
    else root.append(el("p", "muted", call_id + " · call content not recorded in history"));
  }
  showDialog("PLAN · " + text(plan.plan_id));
}
function renderTimeline() {
  const co = coData(), root = $("agentTimeline"); root.replaceChildren(); if (!co) return;
  const filter = $("agentEventFilter").value, kind = $("agentActivityKind")?.value || "all", events = list(co.events).filter(event => (!filter || filter === "all" || event.agent_id === filter) &&
    (kind === "all" || event.kind === kind || kind === "problems" && [event.operation_status, event.status].some(value => ["failed", "partial", "skipped", "interrupted", "invalid", "unavailable", "budget_exhausted"].includes(value))))
    .slice().sort((left, right) => (right.sequence ?? 0) - (left.sequence ?? 0));
  root.append(el("h2", "section-heading", "Latest activity · " + events.length + " saved events"));
  if (!events.length) {root.append(empty("No matching events", "Only saved action and decision summaries are shown. Wait for records or change the role filter.", false)); return;}
  const timeline = el("ol", "activity-feed");
  for (const event of events.slice(0, state.eventLimit)) {
    const row = el("li", "activity-item"), head = el("div", "timeline-event-top"), meta = el("div", "badge-row");
    row.id = "activity-" + event.event_id;
    const agent = list(co.agents).find(item => item.agent_id === event.agent_id);
    const action = button(systemText(event.title) || event.kind || "Recorded research event", () => {
      if (agent) {state.agent = agent.agent_id; renderCollaborationSelection(); $("agentNodeDetail").scrollIntoView({block:"nearest"});}
    }, "text-button activity-title"); action.dataset.focusKey = "event-" + event.event_id;
    head.append(action, recordedStatus(event));
    meta.append(badge(agent ? agentRole(agent)[0] : event.agent_id || "Coordination record", "outline"),
      el("span", "entity-id", "#" + text(event.sequence)), el("span", "", localDate(event.created_at)));
    row.append(meta, head, el("p", "confirmation-note", displayActionSummary(event)));
    if (list(event.artifact_ids).length) row.append(artifactLinks(event.artifact_ids, "Artifacts"));
    const metadata = detail("Handoff & public event fields", {event_id: event.event_id, kind: event.kind, role_status:event.status,
      operation_id:event.operation_id || "Not recorded", operation_status:event.operation_status || "Not recorded", input_summary:event.input_summary || "Not recorded",
      input_artifact_ids:list(event.input_artifact_ids), artifact_ids:list(event.artifact_ids),
      ...(event.budget_failure ? {budget_failure:publicBudgetFailure(event.budget_failure)} : {}), ...providerFailureFields(event)});
    metadata.dataset.uiKey = "event-" + event.event_id; row.append(metadata); timeline.append(row);
  }
  root.append(timeline);
  if (events.length > state.eventLimit) root.append(button("Show " + (events.length - state.eventLimit) + " earlier records", () => {state.eventLimit = events.length; renderTimeline();}));
}
function latestHypotheses(hypotheses) {
  const byId = new Map();
  for (const hypothesis of hypotheses) {
    const previous = byId.get(hypothesis.hypothesis_id);
    if (!previous || Number(hypothesis.version) > Number(previous.version)) byId.set(hypothesis.hypothesis_id, hypothesis);
  }
  return [...byId.values()];
}
function hypothesisIdentity(hypothesis) {return text(hypothesis.hypothesis_id) + " · v" + text(hypothesis.version);}
function chassisSnapshot(snapshot, chassisName) {
  const chassis = snapshot || {}, node = el("section", "chassis-snapshot"), top = el("div", "entity-top");
  top.append(el("h3", "", "Chassis Snapshot · " + (chassisName || "Strain not specified")), badge(chassis.status || "unknown", chassis.status === "direct_evidence" && chassis.strain_match === "exact" ? "teal" : "amber"));
  node.append(top, facts([["Strain match", label(chassis.strain_match || "unknown")], ["Supply mode", label(chassis.mode || "unknown")]]),
    referenceLinks(chassis.evidence_ids, "Chassis evidence"));
  add(node, notifications(list(chassis.gaps).map(systemText), "Chassis production / exposure gaps"));
  if (chassis.strain_match === "other_strain") node.append(el("p", "confirmation-note", "Evidence comes from another strain. Extrapolation to the specified strain still requires validation."));
  node.append(el("p", "confirmation-note", "This snapshot summarizes strain-specific evidence and production gaps. Yield measurements and ChassisGate results are recorded separately; missing evidence retains unknown status.")); return node;
}
function hypothesisCard(hypothesis, co) {
  const card = el("article", "entity-card hypothesis-card"); card.id = "hypothesis-" + String(hypothesis.hypothesis_id) + "-v" + String(hypothesis.version);
  card.append(entityHeading(hypothesis.title || "Untitled research hypothesis", hypothesisIdentity(hypothesis), hypothesis.status));
  const tags = el("div", "badge-row"); tags.append(badge(hypothesis.origin || "unknown", "outline"));
  if (hypothesis.origin === "user_supplied") tags.append(badge("Origin: supplied assessment input", "amber"));
  if (hypothesis.parent_version != null) tags.append(badge("Revised from v" + hypothesis.parent_version + "", "outline"));
  const reviews = list(co.reviews).filter(review => review.hypothesis_id === hypothesis.hypothesis_id && Number(review.version) === Number(hypothesis.version));
  card.append(tags, structured(hypothesis.claim));
  const counters = el("div", "badge-row");
  counters.append(badge(list(hypothesis.evidence_ids).length + " supporting references", "teal"), badge(list(hypothesis.opposing_evidence_ids).length + " counterevidence references", "amber"),
    badge(reviews.length + " same-version reviews", "outline"), badge(list(hypothesis.changes).length + " revision notes", "outline"));
  card.append(counters, el("p", "confirmation-note", "Next validation question: " + text(hypothesis.validation_question)));
  const body = el("details", "hypothesis-detail"); body.dataset.uiKey = card.id + "-detail";
  body.append(el("summary", "", "View support, counterevidence, reviews & versions"), facts([["Molecules", list(hypothesis.molecule_names).join("; ")], ["Targets", list(hypothesis.target_symbols).join("; ") || "No target required"],
    ["Mechanism", hypothesis.mechanism], ["Conditions", hypothesis.condition], ["Measured endpoint", hypothesis.endpoint]]),
    referenceLinks(hypothesis.evidence_ids, "Supporting evidence"), referenceLinks(hypothesis.opposing_evidence_ids, "Counterevidence / alternative explanation"));
  add(body, notifications(list(hypothesis.inference_gaps).map(systemText), "Inference gaps"));
  const entityRefs = list(coData()?.entity_references).map(publicEntityReference).filter(ref => list(ref.hypothesis_versions).some(v => v.hypothesis_id === hypothesis.hypothesis_id && Number(v.version) === Number(hypothesis.version)));
  if (entityRefs.length) body.append(detail("Current entity identity & historical gaps", entityRefs), el("p", "confirmation-note", "Current identity is linked here. Saved hypothesis text retains its version; identity, candidate evidence, chassis feasibility and priority have separate assessment records."));
  body.append(chassisSnapshot(hypothesis.chassis, hypothesis.chassis_name));
  const novelty = hypothesis.novelty || {}, noveltyNode = el("section", "hypothesis-section");
  noveltyNode.append(el("h3", "", "Closest prior work & unconfirmed differences"), badge(novelty.status || "insufficient_search", "outline"),
    structured(novelty.difference || "Differences from prior work are not specified."), referenceLinks(novelty.closest_source_ids, "Closest related sources"),
    el("p", "confirmation-note", "Search scope: " + text(novelty.search_scope) + ". Novelty assessment uses this recorded search scope and its gaps.")); body.append(noveltyNode);
  const prediction = el("section", "hypothesis-section"); prediction.append(el("h3", "", "Falsifiable prediction & next question"),
    structured(hypothesis.prediction), structured(hypothesis.validation_question)); body.append(prediction);
  if (!reviews.length) body.append(el("p", "confirmation-note", "This version is awaiting a saved review."));
  for (const review of reviews) {
    const node = el("section", "hypothesis-review"); node.append(el("h3", "", "Reflection · review of this version"), badge(review.verdict, "amber"), structured(review.reason));
    add(node, notifications(review.objections, "Specific objections")); add(node, notifications(review.required_evidence, "Required additional evidence"));
    node.append(referenceLinks(review.valid_evidence_ids, "References accepted by review"), el("p", "confirmation-note", "Automated research review linked to the cited sources and current version.")); body.append(node);
  }
  if (list(hypothesis.changes).length) {
    const node = el("section", "version-changes"); node.append(el("h3", "", "Evolution · version changes"));
    const items = el("ul"); for (const change of hypothesis.changes) items.append(el("li", "", change)); node.append(items,
      el("p", "confirmation-note", "New versions do not inherit earlier comparison wins. Revised versions require fresh review.")); body.append(node);
  }
  const priorVersions = list(co.hypotheses).filter(item => item.hypothesis_id === hypothesis.hypothesis_id);
  if (priorVersions.length > 1) body.append(button("View all versions of this hypothesis (" + priorVersions.length + ")", () => {
    $("hypothesisVersion").value = "all"; $("hypothesisOrigin").value = "all"; $("hypothesisSearch").value = hypothesis.hypothesis_id; renderHypotheses();
  }));
  const claimRefs = list(co.research_flow?.claims).filter(c => c.hypothesis_id === hypothesis.hypothesis_id && Number(c.version) === Number(hypothesis.version)).map(c => c.claim_id);
  if (claimRefs.length) body.append(artifactLinks(claimRefs, "Claim-level reviews & queries for this version"));
  body.append(detail("Structured hypothesis record", hypothesis)); card.append(body); return card;
}
function renderHypotheses() {
  const co = coData(), root = $("hypothesisCards"), comparisonRoot = $("comparisonContent"); root.replaceChildren(); comparisonRoot.replaceChildren();
  if (!co) {coEmpty(root, "No research hypotheses"); return;}
  const all = list(co.hypotheses), query = $("hypothesisSearch").value.trim().toLowerCase(), origin = $("hypothesisOrigin").value;
  const visible = ($("hypothesisVersion").value === "latest" ? latestHypotheses(all) : all).filter(hypothesis =>
    (!query || JSON.stringify(hypothesis).toLowerCase().includes(query)) && (origin === "all" || hypothesis.origin === origin));
  if (!visible.length) root.append(empty("No matching saved hypotheses", all.length ? "Adjust keywords, origin or version filters." : "No hypotheses were saved. Check the activity log for generation status and remaining budget.", false));
  for (const hypothesis of visible) root.append(hypothesisCard(hypothesis, co));
  comparisonRoot.append(el("h2", "section-heading", "Ranking · pairwise comparisons"), callout("Pairwise comparisons record research priority by version and criterion, allowing ties, insufficient evidence and incomparable options."));
  if (!list(co.comparisons).length) {comparisonRoot.append(el("p", "muted", "No pairwise comparisons are recorded this run. Check candidate availability, condition compatibility and budget details for the recorded reason.")); return;}
  const grid = el("div", "comparison-grid");
  for (const comparison of list(co.comparisons)) {
    const card = el("article", "panel comparison-card"), pair = el("div", "comparison-pair"); card.id = "comparison-" + comparison.comparison_id;
    pair.append(el("div", "", text(comparison.left_id) + " · v" + text(comparison.left_version)), el("span", "", "vs"),
      el("div", "", text(comparison.right_id) + " · v" + text(comparison.right_version)));
    card.append(pair, badge(comparison.outcome || "insufficient", "outline"), structured(comparison.reason),
      facts([["Comparison criteria", typeof comparison.criterion === "object" ? JSON.stringify(comparison.criterion) : comparison.criterion]]),
      referenceLinks(comparison.evidence_ids, "Comparison evidence"), el("p", "entity-id", comparison.comparison_id)); grid.append(card);
  }
  comparisonRoot.append(grid);
}
function renderContracts() {
  const co = coData(), root = $("contractCards"); root.replaceChildren(); if (!co) {coEmpty(root, "No research plans"); return;}
  if (!list(co.contracts).length) {root.append(empty("No research plans created", "No plan was saved. Review the existing hypotheses and Meta-review activity.", false)); return;}
  for (const contract of list(co.contracts)) {
    const hypothesis = list(co.hypotheses).find(item => item.hypothesis_id === contract.hypothesis_id && Number(item.version) === Number(contract.version));
    const card = el("article", "panel contract-card"); card.id = "contract-" + contract.hypothesis_id + "-v" + contract.version;
    card.append(entityHeading(hypothesis?.title || "Research plans", text(contract.hypothesis_id) + " · v" + text(contract.version), contract.status));
    card.append(dialogSection("Research question", structured(contract.question)), dialogSection("Evidence-based assessment", structured(contract.judgment)),
      referenceLinks(contract.evidence_ids, "Current evidence"), referenceLinks(contract.opposing_evidence_ids, "Counterevidence / disagreement"));
    if (hypothesis) card.append(chassisSnapshot(hypothesis.chassis, hypothesis.chassis_name), button("View this hypothesis version & review →", () => {
      $("hypothesisVersion").value = "all"; $("hypothesisSearch").value = contract.hypothesis_id; $("hypothesisOrigin").value = "all";
      renderHypotheses(); navigate("hypotheses"); $("hypothesis-" + contract.hypothesis_id + "-v" + contract.version)?.scrollIntoView({block: "start"});
    }));
    else card.append(callout("The hypothesis version referenced by this plan is absent from the current record. Check artifact consistency.", "amber"));
    card.append(dialogSection("Minimal validation question / plan", structured(contract.minimal_validation)));
    add(card, notifications(contract.resource_requirements, "Resource requirements")); add(card, notifications(contract.remaining_unknowns, "Remaining unknowns"));
    const parameters = el("section", "parameter-needs"); parameters.append(el("h3", "", "NasoTwin parameter needs · awaiting measurement"));
    if (!list(contract.parameter_needs).length) parameters.append(el("p", "muted", "Parameter needs are awaiting records in this plan. Each parameter lists its units, source, measurement status and model input."));
    for (const parameter of list(contract.parameter_needs)) {
      const row = el("article", "parameter-card"); row.append(el("h4", "", parameter.variable || "Parameter name not recorded"),
        facts([["Units", parameter.unit], ["Why needed", parameter.why_needed], ["Value", parameter.value == null ? "Measurement required · no value" : "Unverified input value: " + text(parameter.value)],
          ["Data class", parameter.data_class === "measurement_required" ? "Measurement required" : label(parameter.data_class)]]), referenceLinks(parameter.source_ids, "Parameter evidence"));
      if (parameter.value != null) row.append(callout("Parameter source: measurement unconfirmed. Calibration requires a traceable measurement record.", "amber")); parameters.append(row);
    }
    parameters.append(el("p", "confirmation-note", "Production flux, nasal PK and response parameters each link to their corresponding measurements or model inputs.")); card.append(parameters, detail("Structured research plan", contract)); root.append(card);
  }
}
async function loadResearchCatalogs(force = false) {
  if (state.catalogPromise) return state.catalogPromise;
  if (!force && state.catalogs.tools !== null && state.catalogs.protocols !== null) {renderCatalogs(); return;}
  state.catalogPromise = (async () => {
    const results = await Promise.allSettled([api("/api/research-tools"), api("/api/research-protocols")]);
    for (const [index, key] of ["tools", "protocols"].entries()) {
      const result = results[index];
      if (result.status === "fulfilled") {state.catalogs[key] = list(result.value[key]); delete state.catalogErrors[key];}
      else state.catalogErrors[key] = result.reason.message;
    }
    renderCatalogs();
  })();
  try {await state.catalogPromise;} finally {state.catalogPromise = null;}
}
function renderCatalogs() {
  const toolRoot = $("toolCatalog"), protocolRoot = $("protocolCatalog"); toolRoot.replaceChildren(); protocolRoot.replaceChildren();
  toolRoot.append(el("h2", "section-heading", "Available lightweight tools"));
  if (state.catalogErrors.tools) toolRoot.append(callout("Tool catalog unavailable: " + state.catalogErrors.tools + ". Saved research results remain accessible.", "amber"), button("Reload catalogs", () => loadResearchCatalogs(true)));
  else if (state.catalogs.tools === null) toolRoot.append(el("p", "muted", "The actual tool catalog is loaded from the service when this page opens."));
  else if (!state.catalogs.tools.length) toolRoot.append(el("p", "muted", "The service returned no registered tools."));
  const tools = el("div", "tool-grid");
  for (const tool of list(state.catalogs.tools)) {
    const card = el("article", "tool-card"); card.append(el("h3", "", tool.name), badge(tool.provider || "Provider not recorded", "outline"),
      structured(systemText(tool.description)), detail("Server-validated parameter contract", tool.parameters_schema || {})); tools.append(card);
  }
  toolRoot.append(tools);
  protocolRoot.append(el("h2", "section-heading", "Research protocol contracts"), el("p", "muted", "Project execution contracts organize the staged research workflow, inspired by ARIS."));
  if (state.catalogErrors.protocols) protocolRoot.append(callout("Research protocol catalog unavailable: " + state.catalogErrors.protocols, "amber"));
  else if (state.catalogs.protocols === null) protocolRoot.append(el("p", "muted", "Waiting for the service's actual research protocols."));
  const protocols = el("div", "protocol-grid");
  for (const protocol of list(state.catalogs.protocols)) {
    const card = el("article", "tool-card"); card.append(el("h3", "", systemText(protocol.title) || protocol.name), el("div", "entity-id", text(protocol.name) + " · v" + text(protocol.version)),
      facts([["Input", systemText(protocol.input)], ["Outputs", list(protocol.outputs).join(", ")], ["Tools", list(protocol.tools).join(", ") || "None"], ["Budget", systemText(protocol.budget)]]));
    add(card, notifications(list(protocol.stop_conditions).map(systemText), "Stop conditions"));
    card.append(detail("Original protocol record (unmodified)", {input: protocol.input, outputs: protocol.outputs, tools: protocol.tools, budget: protocol.budget, stop_conditions: protocol.stop_conditions})); protocols.append(card);
  }
  protocolRoot.append(protocols); renderToolCalls();
}
function renderToolCalls() {
  const root = $("toolCallRecords"), co = coData(); root.replaceChildren(); root.append(el("h2", "section-heading", "Actual tool calls this run"));
  if (!co) {root.append(el("p", "muted", "This catalog describes available tools. The selected run's tool-call history is currently empty.")); return;}
  if (list(co.tool_plans).length) {
    root.append(el("h3", "", "Gap-driven tool plans"), el("p", "muted", "The model identifies evidence gaps; the fixed coordinator checks existing identity. A deterministic executor validates parameters, identity and shared budget. Unresolved identities retain candidates but stop downstream queries."));
    for (const plan of list(co.tool_plans)) {
      const card = el("article", "panel tool-result"); card.append(button(plan.plan_id + " · " + text(plan.request?.name), () => openToolPlan(plan), "text-button"), status(plan.status),
        structured(systemText(plan.reason) || "Specific gap not recorded"), facts([["Source", plan.origin], ["Gap", plan.gap_id], ["Actual steps", list(plan.call_ids).length], ["Stop reason", systemText(plan.stop_reason) || "None"]])); root.append(card);
    }
  } else root.append(el("p", "muted", "Original tool returns retain their saved order; tool-plan and identity-resolution links are unrecorded in this history."));
  if (!list(co.tool_calls).length) {root.append(el("p", "muted", "Tool calls are created for the current evidence gaps; the call history is currently empty.")); return;}
  for (const record of list(co.tool_calls)) {
    const card = el("article", "panel tool-result"), top = el("div", "entity-top"); top.append(el("h3", "", record.tool_name || "Recorded tool call"), status(record.status));
    card.append(top, facts([["Provider", record.provider], ["Retrieved at", localDate(record.retrieved_at)],
      ["HTTP attempts", record.request_count ?? 0], ["Cache hits", record.cache_hits ?? 0], ["Returned records", list(record.records).length]]));
    add(card, notifications(list(record.warnings).map(systemText), "Call limitations & warnings"));
    for (const source of list(record.sources)) {
      if (typeof source === "string") {if (/^https?:\/\//i.test(source)) card.append(safeLink(source, source)); else card.append(el("p", "entity-id", source));}
      else if (source && typeof source === "object") card.append(safeLink(source.title || source.source_id || source.url || "Tool source", source.url || ""));
    }
    card.append(button("View identity / query / error provenance →", () => openToolRecord(record), "text-button"),
      detail("Actual request parameters & returned records", {parameters: record.parameters, records: record.records, sources: record.sources})); root.append(card);
  }
}
function preserveView(root, render) {
  const detailsKey = (node, index) => node.dataset.uiKey || (node.closest("article[id]")?.id || root.id + ":" + index) + ":" + node.querySelector("summary")?.textContent;
  const opened = new Set([...root.querySelectorAll("details")].map((node, index) => node.open ? detailsKey(node, index) : null).filter(Boolean));
  const active = document.activeElement, focus = root.contains(active) ? {id:active.id, key:active.dataset.focusKey,
    scope:active.parentElement?.closest("[id]")?.id,
    summaryKey:active.tagName === "SUMMARY" ? active.parentElement?.dataset.uiKey : null,
    start:active.selectionStart, end:active.selectionEnd} : null;
  const scrollX = window.scrollX, scrollY = window.scrollY;
  const scrolls = [...root.querySelectorAll("[id]")].filter(node => node.scrollTop || node.scrollLeft).map(node => [node.id, node.scrollTop, node.scrollLeft]);
  const controls = [...root.querySelectorAll("input[id], select[id], textarea[id]")].filter(node => node.type !== "file").map(node => ({node, id:node.id, value:node.value, checked:node.checked}));
  render();
  [...root.querySelectorAll("details")].forEach((node, index) => {if (opened.has(detailsKey(node, index))) node.open = true;});
  for (const control of controls) {
    const node = $(control.id); if (!node || node === control.node) continue;
    if (node.tagName !== "SELECT" || [...node.options].some(option => option.value === control.value)) node.value = control.value;
    if (node.type === "checkbox") node.checked = control.checked;
  }
  const nextFocus = focus?.summaryKey ? [...root.querySelectorAll("details")].find(n => n.dataset.uiKey === focus.summaryKey)?.querySelector("summary") : focus?.id ? $(focus.id) : focus?.key ? [...root.querySelectorAll("[data-focus-key]")].find(n => n.dataset.focusKey === focus.key && n.parentElement?.closest("[id]")?.id === focus.scope) : null;
  if (nextFocus) {nextFocus.focus({preventScroll:true}); if (typeof nextFocus.setSelectionRange === "function" && focus.start != null) nextFocus.setSelectionRange(focus.start, focus.end);}
  for (const [id, top, left] of scrolls) {const node = $(id); if (node) {node.scrollTop = top; node.scrollLeft = left;}}
  window.scrollTo({left:scrollX, top:scrollY, behavior:"instant"});
}
function renderActiveView() {
  const view = state.view, root = $("view-" + view); if (!root || ["research", "settings"].includes(view)) return;
  if (view === "demo") {window.Hdac2Demo?.load(); return;}
  const signature = JSON.stringify({project:state.project, run:view === "compute" || view === "experiments" || view === "memory" ? {id:state.run, status:state.selected?.status} : state.selected, jobs:view === "compute" ? state.jobs : null,
    experiments:view === "experiments" ? state.experiments : null, memory:view === "memory" ? state.memory : null});
  if (state.rendered[view] === signature) return;
  const render = () => {
    if (view === "overview") renderOverview(state.selected);
    if (view === "collaboration") renderCollaboration();
    if (view === "hypotheses") renderHypotheses();
    if (view === "contracts") renderContracts();
    if (view === "candidates" || view === "context") renderCandidates();
    if (view === "evidence") renderEvidence();
    if (view === "files") renderPackages();
    if (view === "compute") renderCompute();
    if (view === "experiments") renderExperiments();
    if (view === "memory") renderMemory(state.memory);
    if (view === "tools") renderToolCalls();
    if (view === "targets") {
      $("targetCards").replaceChildren();
      if (!state.selected?.targets?.length) $("targetCards").append(empty("No protein targets", state.selected ? "No targets were saved this run. Check scope, evidence and warnings." : "Select a run or submit a disease question."));
      for (const target of list(state.selected?.targets)) $("targetCards").append(targetCard(target));
    }
  };
  const context = state.project + "/" + state.run;
  if (state.renderContext[view] === context) preserveView(root, render); else render();
  state.rendered[view] = signature;
  state.renderContext[view] = context;
}
function renderRun(run) {
  $("targetsNavCount").textContent = run ? String(list(run.targets).length) : "";
  $("candidatesNavCount").textContent = run ? String(list(run.candidates).filter(c => candidateVisible(c, run)).length + list(run.candidate_assessments).filter(row => !row.candidate_id).length) : "";
  $("evidenceNavCount").textContent = run ? String(list(run.evidence).length) : "";
  $("agentsNavCount").textContent = run?.co_scientist ? String(collaborationModel(run).working.length) : "";
  $("hypothesesNavCount").textContent = run?.co_scientist ? String(latestHypotheses(list(run.co_scientist.hypotheses)).length) : "";
  $("contractsNavCount").textContent = run?.co_scientist ? String(list(run.co_scientist.contracts).length) : "";
  const selectedOption = [...$("run").options].find(option => option.value === run?.run_id);
  if (selectedOption && run?.brief) selectedOption.textContent = runOptionLabel(run);
  renderActiveView();
  const chosenCandidate = $("experimentCandidate").value;
  optionList($("experimentCandidate"), list(run?.candidates), c => c.candidate_id, c => c.name, chosenCandidate, "No specific molecule");
  const packages = list(run?.target_packages);
  optionList($("packageChoice"), packages.filter(p => p.status === "ready"), p => p.package_id,
    p => (list(run?.targets).find(t => t.package_id === p.package_id)?.gene_symbol || "Targets") + " · " + p.package_id,
    $("packageChoice").value, "Select a ready input package");
  $("createHandoff").disabled = !state.run || !packages.some(p => p.status === "ready") || ["queued", "running"].includes(run?.status);
  $("candidateCSV").disabled = !run; $("prepareTarget").disabled = !run || ["running", "queued"].includes(run.status);
  $("uploadExperiment").disabled = !state.project || ["running", "queued"].includes(run?.status);
}
function renderCandidates() {
  const candidates = list(state.selected?.candidates), therapeutic = candidates.filter(c => candidateVisible(c));
  const query = $("candidateSearch").value.trim().toLowerCase(), decision = $("candidateDecision").value;
  const filtered = therapeutic.filter(c => (!query || JSON.stringify(c).toLowerCase().includes(query)) && (decision === "all" || c.decision === decision));
  const unresolved = list(state.selected?.candidate_assessments).filter(a => !a.candidate_id && (!query || a.name.toLowerCase().includes(query)) && (decision === "all" || a.decision === decision));
  $("candidateCards").replaceChildren();
  if (!filtered.length && !unresolved.length) $("candidateCards").append(empty(therapeutic.length ? "No matching candidates" : "No therapeutic research candidates",
    therapeutic.length ? "Try another keyword or research priority." : "Therapeutic candidates, biomarkers and risk signals are shown by research role. See Context molecules for other findings.", false));
  for (const candidate of filtered) $("candidateCards").append(candidateCard(candidate));
  for (const row of unresolved) {
    const card = el("article", "entity-card"); card.append(entityHeading(row.name, "Specified candidate · no unique entity link yet", row.decision), assessmentDetails(row)); $("candidateCards").append(card);
  }
  const root = $("contextCards"); root.replaceChildren();
  const roles = ["biomarker", "risk_factor", "metabolic_context", "compound_class", "unknown"];
  let total = 0;
  for (const role of roles) {const group = candidates.filter(c => !candidateVisible(c) && (roleOf(c) === role || role === "unknown" && !roles.includes(roleOf(c))));
    if (!group.length) continue; total += group.length; root.append(el("h2", "group-heading", label(role) + " · " + group.length));
    for (const candidate of group) root.append(candidateCard(candidate));}
  if (!total) root.append(empty("No context molecules", "No additional biomarkers, risk-associated molecules, metabolic context or unclassified molecules were recorded.", false));
}
function renderEvidence() {
  const run = state.selected, query = $("evidenceSearch").value.trim().toLowerCase(), polarity = $("evidencePolarity").value;
  const sources = Object.fromEntries(list(run?.sources).map(source => [source.source_id, source]));
  const records = list(run?.evidence).filter(e => (!query || JSON.stringify(e).toLowerCase().includes(query)) && (polarity === "all" || e.polarity === polarity));
  const root = $("evidenceCards"); root.replaceChildren();
  if (!records.length) root.append(empty("No matching evidence", "Evidence links to retrieved literature and database records. Check retrieval and review records for the current selection.", false));
  for (const record of records) {
    const card = el("article", "evidence-card"), top = el("div", "evidence-card-top"), meta = el("div", "evidence-meta"), source = sources[record.source_id];
    top.append(el("h2", "", record.claim), badge(record.polarity, record.polarity === "opposes" ? "red" : record.polarity === "supports" ? "teal" : ""));
    meta.append(el("span", "entity-id", record.evidence_id), badge(sourceScopeLabel(source)), el("span", "", record.study_system));
    card.append(top, meta, el("blockquote", "", record.quote), button("View source text, study scope & limitations →", () => openEvidence(record.evidence_id)));
    root.append(card);
  }
  $("sourceCount").textContent = list(run?.sources).length + " retrieved sources";
  $("sourceCards").replaceChildren();
  for (const source of list(run?.sources)) {
    const row = el("article", "source-row"), meta = el("div", "evidence-meta");
    row.append(safeLink(source.title, source.url));
    meta.append(badge(sourceScopeLabel(source)), el("span", "", source.provider), el("span", "entity-id", source.source_id),
      el("span", "", "Retrieved " + localDate(source.retrieved_at)), button("View retrieved text", () => openSource(source.source_id)));
    row.append(meta); $("sourceCards").append(row);
  }
}
function dialogSection(title, content) {
  const node = el("section", "dialog-section"); node.append(el("h3", "", title));
  if (content instanceof Node) node.append(content); else node.append(el("p", "", content)); return node;
}
function showDialog(title) {$("dialogEyebrow").textContent = title; if (!$("evidenceDialog").open) $("evidenceDialog").showModal();}
function openEvidence(id) {
  const run = state.selected, record = list(run?.evidence).find(e => e.evidence_id === id);
  if (!record) throw new Error("Evidence not found in the selected run: " + id);
  const source = list(run.sources).find(s => s.source_id === record.source_id), root = $("dialogContent"); root.replaceChildren();
  root.append(el("h2", "", record.claim), badge(record.polarity),
    dialogSection("Retrieved source excerpt", el("blockquote", "dialog-quote", record.quote)),
    facts([["Source location", record.location], ["Study system", record.study_system], ["Study conditions", record.condition],
      ["Measured endpoint", record.endpoint], ["Span verification", record.validated_span ? "Span found in retrieved source" : "Unconfirmed"],
      ["Evidence scope", sourceScopeLabel(source)]]));
  if (record.limitations?.length) {const ul = el("ul"); for (const value of record.limitations) ul.append(el("li", "", value)); root.append(dialogSection("Limitations & applicability", ul));}
  if (source) {const sourceNode = el("div"); sourceNode.append(safeLink(source.title, source.url), el("p", "muted", source.provider + " · " + source.source_id), button("View retrieved source text", () => openSource(source.source_id))); root.append(dialogSection("Source", sourceNode));}
  root.append(callout("Automated review checks alignment between source text and conclusions. Evidence details retain the study system, conditions and endpoint.", "amber"));
  showDialog("EVIDENCE · " + id);
}
function openSource(id) {
  const source = list(state.selected?.sources).find(s => s.source_id === id);
  if (!source) throw new Error("Source not found in the selected run: " + id);
  const root = $("dialogContent"); root.replaceChildren();
  root.append(el("h2", "", source.title), badge(sourceScopeLabel(source)), dialogSection("Original source", safeLink(source.url, source.url)),
    facts([["Source provider", source.provider], ["Retrieved at", localDate(source.retrieved_at)], ["PMID", source.pmid], ["DOI", source.doi]]),
    detail("Original acquisition scope, actual window & reuse", sourceScopeView(source)),
    dialogSection(source.metadata?.local_excerpt_only ? "Saved contiguous excerpt (see metadata for original scope & location)" : "Retrieved text (abstract / full text / database record)", el("pre", "raw-json", source.text || "No source text was saved.")),
    detail("Source metadata", source.metadata || {}));
  if (!$("evidenceDialog").open) showDialog("SOURCE · " + id); else $("dialogEyebrow").textContent = "SOURCE · " + id;
}
function fileLinks(pkg, className = "file-actions") {
  const node = el("div", className);
  for (const [kind, title, note] of [["protein_pdb", "PDB", "Protein structure"], ["sequence_fasta", "FASTA", "Protein sequence"], ["reference_ligand_sdf", "SDF", "Reference ligand in the same coordinate frame"]]) {
    const available = Boolean(pkg[kind]);
    const row = available ? safeLink("", runBase() + "/target-packages/" + enc(pkg.package_id) + "/" + kind, "file-download") : el("span", "file-download disabled");
    const description = el("span"); description.append(document.createTextNode((available ? "Download " : "Missing ") + title), el("small", "", note));
    row.append(el("span", "", title), description);
    if (!available) {row.setAttribute("aria-disabled", "true"); row.title = "This file was not generated in the actual input package. Check the selected structure and ligand.";}
    node.append(row);
  }
  return node;
}
function renderPackages() {
  const packages = list(state.selected?.target_packages), root = $("packageCards"); root.replaceChildren();
  if (!packages.length) root.append(empty("No target input packages", "Input preparation checks protein identity, structure, chain and a reference ligand in the same coordinate system.", false));
  for (const pkg of packages) {
    const card = el("article", "file-package"); card.id = "package-" + pkg.package_id;
    const target = list(state.selected?.targets).find(t => t.package_id === pkg.package_id);
    card.append(entityHeading(target?.gene_symbol || pkg.metadata?.uniprot_id || "Target input package", pkg.package_id, pkg.status));
    card.append(facts([["Protein chain", pkg.chain || "Not established"], ["Pocket center", list(pkg.pocket_center).join(", ") || "Unconfirmed"]]), fileLinks(pkg));
    const models = el("div", "model-readiness");
    for (const [model, readiness] of Object.entries(pkg.model_readiness || {})) {
      const ready = readiness.ready === true || readiness.status === "ready";
      const value = badge(model + " · " + (ready ? "Ready" : "Awaiting review"), ready ? "teal" : "amber"); value.title = JSON.stringify(readiness); models.append(value);
    }
    card.append(models);
    const checklist = el("div");
    card.append(button("View four-model input handoff checklist", async () => {
      const project = state.project, run = state.run, epoch = state.selectionEpoch;
      checklist.replaceChildren(el("p", "loading-line", "Checking recorded states and whether project files exist…"));
      const data = await api(runBase(project, run) + "/target-packages/" + enc(pkg.package_id) + "/screening-checklist");
      if (!selectionCurrent(epoch, project, run)) return;
      checklist.replaceChildren(callout("This check covers saved input states and file presence. Structure review, cache preparation and model execution have separate readiness states.", "amber"));
      for (const model of list(data.models)) {
        const entry = el("div", "panel");
        entry.append(el("h3", "", model.label), facts([
          ["Inputs", list(model.inputs).join(" + ")], ["Requirements", systemText(model.contract)],
          ["Recorded inputs", model.input_state === "recorded_ready_files_present" ? "Recorded ready; files present" : "Needs review / files missing"],
          ["Next step", systemText(model.next_step)], ["Cache & execution", "Cache not checked · models not run by this checklist"],
        ]));
        checklist.append(entry);
      }
      add(checklist, notifications(list(data.limitations).map(systemText), "Handoff boundaries"));
    }), checklist);
    if (pkg.status !== "ready") card.append(callout("This input package is not admitted to full four-model computation. Existing files remain downloadable; review construct, sequence-scope and ligand checks below.", "amber"));
    card.append(detail("Source, construct, pocket & model-readiness checks", {checks: pkg.checks, model_readiness: pkg.model_readiness, metadata: pkg.metadata}));
    root.append(card);
  }
  optionList($("packageChoice"), packages.filter(p => p.status === "ready"), p => p.package_id,
    p => (list(state.selected?.targets).find(t => t.package_id === p.package_id)?.gene_symbol || "Targets") + " · " + p.package_id,
    $("packageChoice").value, "Select a ready input package");
  $("createHandoff").disabled = !state.run || !packages.some(p => p.status === "ready") || ["queued", "running"].includes(state.selected?.status);
}
function renderCompute() {
  const root = $("computeJobs"); root.replaceChildren();
  root.append(callout("The HDAC2 demo illustrates four-model inputs and scoring outputs using target-specific caches. Configuration, model preloading and scoring have separate states.", "amber"),
    button("Open HDAC2 workbench →", () => navigate("demo")));
  if (!state.jobs.length) {root.append(empty("No compute jobs", "Once target inputs are ready, create a Target2Lead configuration. Actual computation cannot start before dependency checks pass.", false)); return;}
  const project = state.project;
  for (const job of state.jobs) {
    const card = el("article", "panel"), heading = el("div", "compute-heading"), results = el("div");
    card.id = "compute-" + job.compute_id;
    heading.append(el("h2", "", job.compute_id), status(job.status));
    card.append(heading, facts([["Target input package", job.target_package_id], ["Reserved MD budget", job.max_md_jobs], ["Source research run", job.research_run_id]]));
    if (job.error || job.note) card.append(callout(job.error || job.note, job.error ? "red" : "amber"));
    const controls = el("div", "compute-controls"), stage = el("select"); stage.setAttribute("aria-label", "Final compute stage");
    stage.id = "compute-" + job.compute_id + "-stage";
    for (const value of ["prepare", "drugclip", "sprint", "psichic", "pharmaconet", "top100", "merge_candidates", "admet", "gate", "select", "md"]) {const node = el("option", "", "Run through " + value); node.value = value; stage.append(node);} stage.value = "md";
    const resumeLabel = el("label", "checkbox-label"), resume = el("input"); resume.type = "checkbox"; resume.id = "compute-" + job.compute_id + "-resume"; resumeLabel.append(resume, document.createTextNode("Resume execution"));
    const jobBase = base(project) + "/compute/" + enc(job.compute_id);
    controls.append(stage, resumeLabel, button("Check dependencies", async () => {
      results.replaceChildren(el("p", "loading-line", "Checking actual configuration, dependencies and compute resources…"));
      const data = await api(jobBase + "/preflight?until=" + enc(stage.value));
      if (project !== state.project) return;
      results.replaceChildren(callout(data.ready ? "Preflight passed. The configuration is ready for an explicit compute submission." : "Execution requirements are not met. Supply the missing resources below.", data.ready ? "" : "amber"));
      const checks = el("ul", "check-list"); for (const check of data.checks || []) {const row = el("li"); row.append(el("span", check.ready ? "ok" : "missing", check.ready ? "✓" : "×"), el("span", "", check.name + " · " + (check.ready ? "Available" : "Missing") + (check.reason ? " · " + check.reason : ""))); checks.append(row);}
      results.append(checks, detail("Full preflight record", data));
    }), button("Execute job", async () => {
      if (!confirm("This will use the configured resources to execute through " + stage.value + ". Computation may take a long time. Continue?")) return;
      await api(jobBase + "/run", "POST", {until: stage.value, resume: resume.checked});
      if (project === state.project) {notify("Compute job submitted. Progress and outputs appear in the run status."); await refresh();}
    }), button("View actual outputs", async () => {
      const data = await api(jobBase); if (project === state.project) results.replaceChildren(detail("Actual job & existing results", data));
    }), button("Collect results into report", async () => {
      await api(jobBase + "/collect", "POST", {});
      if (project === state.project) {notify("Actual compute outputs were collected into the research record."); await refresh();}
    }));
    controls.querySelectorAll("button").forEach(node => {if (["queued", "running"].includes(job.status) && ["Execute job", "Collect results into report"].includes(node.textContent)) node.disabled = true;});
    card.append(controls, results); root.append(card);
  }
}
function renderExperiments() {
  const root = $("experimentList"), checked = new Set([...root.querySelectorAll("input:checked")].map(node => node.value)); root.replaceChildren();
  if (!state.experiments.length) root.append(el("p", "muted", "No measurements have been submitted. Uploads retain the original CSV, analysis and QC status."));
  for (const experiment of state.experiments) {
    const row = el("label", "experiment-record"), checkbox = el("input"), info = el("div"); checkbox.type = "checkbox"; checkbox.value = experiment.experiment_id; checkbox.checked = checked.has(experiment.experiment_id);
    info.append(el("strong", "", experiment.spec?.name || experiment.name || experiment.experiment_id),
      el("p", "", label(experiment.status) + " · " + experiment.experiment_id + " · " + localDate(experiment.created_at)),
      artifact("experiments/" + experiment.experiment_id + "/report.md", "Download experiment analysis report")); row.append(checkbox, info); root.append(row);
  }
  $("followup").disabled = !state.run || ["queued", "running"].includes(state.selected?.status);
}
function renderMemory(records) {
  const root = $("memoryList"); root.replaceChildren();
  if (!records.length) {root.append(empty("Project memory is empty", "Saved research summaries and experiment records will appear here with their original review status.", false)); return;}
  for (const record of records) {
    const card = el("article", "memory-record"), meta = el("div", "badge-row");
    meta.append(badge(record.kind), badge(record.verified ? "Independent validation recorded" : "Independent validation pending", record.verified ? "teal" : "amber"), el("span", "entity-id", localDate(record.created_at)));
    card.append(meta, el("p", "", record.text)); if (record.artifact) card.append(artifact(record.artifact, "View original artifact →")); root.append(card);
  }
}
async function loadDoctor() {
  const data = await api("/api/doctor"), root = $("doctorPanel"); root.replaceChildren();
  root.append(el("h2", "", "Local service & model interface"), facts([["Python", data.version], ["Model backend", data.llm?.backend],
    ["Model", data.llm?.model], ["Per-call model timeout (seconds)", data.llm?.timeout_seconds],
    ["Authentication", data.llm?.login], ["Remote model data", data.llm?.remote_data_allowed ? "Allowed (server configuration)" : "Not allowed"]]));
  const grid = el("div", "environment-grid");
  for (const [name, ready] of Object.entries(data.dependencies || {})) {const row = el("div", "environment-row"); row.append(el("span", "environment-value", name), badge(ready ? "Installed" : "Not installed", ready ? "teal" : "amber")); grid.append(row);}
  root.append(grid, callout(systemText(data.note) || "Check the status of installed components and optional frameworks below."), detail("Full environment diagnostics & optional frameworks", data));
}
async function ensureProject(name, epoch = state.selectionEpoch) {
  if (state.project) return state.project;
  const project = await api("/api/projects", "POST", {name: name + " · Research project"});
  if (epoch === state.selectionEpoch && !state.project) {
    state.project = project.project_id; await loadProjects(project.project_id);
  }
  return project.project_id;
}
function selectedBackgroundPackage(mode, value) {
  if (mode !== "co_scientist") return "";
  const id = String(value || "").trim();
  if (id && !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(id)) throw new Error("Background-package IDs may contain only letters, digits, underscores or hyphens, up to 80 characters.");
  return id;
}
function applyTaskIntent(brief, intent) {
  if (!["legacy", "disease_exploration", "candidate_assessment"].includes(intent)) throw new Error("Choose a task intent.");
  const result = {...brief, task_intent:intent};
  if (intent === "candidate_assessment" && !list(result.existing_candidates).length) throw new Error("Enter a candidate name for a specified-candidate assessment.");
  if (intent !== "disease_exploration") return result;
  const fields = ["existing_candidates", "background_package_id", "target_hints", "delivery_context", "conditions", "engineering_allowed", "assumptions", "experiment_ids", "target_packages", "allowed_precursors", "experimental_capabilities", "continue_saved_hypotheses"];
  const present = value => Array.isArray(value) ? value.length > 0 : Boolean(value);
  if (fields.some(key => present(result[key])) || (result.chassis_name && result.chassis_name !== "Lactiplantibacillus plantarum WCFS1")) {
    throw new Error("Disease exploration cannot include candidates, background packages, target hints or engineering context. Clear those fields or choose research with existing context.");
  }
  return {...result, chassis_name:"", chassis_id:"", reference_chassis:""};
}
function configureResearchMode(resetDefaults = true) {
  const co = $("researchMode").value === "co_scientist";
  $("modeHelp").textContent = co ? "An orchestrator and six research roles share a base model with separate contexts." :
    "Standard mode follows the target and literature routes with a stage-by-stage activity log.";
  $("backgroundPackageId").disabled = !co; if (!co) $("backgroundPackageId").value = "";
  $("coBudgetNote").hidden = !co; $("downloadTargets").disabled = co;
  if (resetDefaults) $("downloadTargets").checked = !co; else if (co) $("downloadTargets").checked = false;
  $("downloadTargets").closest("label").title = co ? "After research in Co mode, retrieve structures from Target files." : "Prepares target inputs for the separately launched screening and molecular-analysis modules.";
  if (!resetDefaults) return;
  $("maxCalls").value = co ? 16 : 10; $("maxPapers").value = 12; $("maxSearches").value = co ? 6 : 8;
  // Leave time for independent review and closure after slow model responses.
  $("maxMinutes").value = co ? 60 : 30;
}
function buildResearchPayload() {
    const disease = $("disease").value.trim();
    if (!disease) throw new Error("Enter the disease or pathology question for this run.");
    const researchMode = $("researchMode").value;
    const brief = applyTaskIntent({disease, research_mode: researchMode,
      background_package_id: selectedBackgroundPackage(researchMode, $("backgroundPackageId").value),
      existing_candidates: $("existingCandidates").value.split(/[,，\n;]/).map(value => value.trim()).filter(Boolean),
      phenotype: $("phenotype").value.trim(), population: $("population").value.trim(),
      delivery_context: $("deliveryContext").value.trim(), research_goal: $("researchGoal").value.trim(),
      conditions: $("researchConditions").value.trim(), success_criterion: $("successCriterion").value.trim(),
      species: $("species").value.trim(), chassis_name: $("chassis").value.trim(),
      chassis_id: "", reference_chassis: $("referenceChassis").value.trim(), engineering_allowed: $("engineeringAllowed").checked,
      target_hints: $("hints").value.split(/[,，]/).map(value => value.trim()).filter(Boolean),
      budget: {max_targets: Number($("maxTargets").value), literature_max_candidates: Number($("maxCandidates").value),
        max_papers: Number($("maxPapers").value), max_searches: Number($("maxSearches").value),
        max_llm_calls: Number($("maxCalls").value), max_md_jobs_total: 20,
        max_hypotheses: 6, max_http_requests: 32, max_tool_calls: 12,
        max_input_tokens: 300000, max_output_tokens: 96000,
        timeout_seconds: Math.round(Number($("maxMinutes").value) * 60)}}, $("taskIntent").value);
    return {brief, prepare_targets:brief.task_intent !== "disease_exploration" && researchMode === "standard" && $("downloadTargets").checked};
}
async function submitResearch(event) {
  event.preventDefault();
  if (!window.ResearchIntake) {notify("The request interface did not load. Refresh the page; no research was submitted.", true); return;}
  await window.ResearchIntake.submit();
}
async function prepareTarget(event) {
  event.preventDefault(); const node = $("prepareTarget"), project = state.project, run = state.run;
  node.disabled = true;
  try {
    notify("Retrieving actual structure, sequence and bound reference ligand. Input packages that fail checks will not be admitted automatically.");
    const request = {uniprot_id: $("targetUniProt").value.trim(), pdb_id: $("targetPDB").value.trim(),
      protein_chain: $("targetChain").value.trim(), ligand_id: $("targetLigand").value.trim(),
      ligand_chain: $("ligandChain").value.trim(), ligand_residue: $("ligandResidue").value.trim(),
      sequence_scope: $("targetSequenceScope").value, model_number: Number($("targetModelNumber").value),
      species: state.selected?.brief.species || "Homo sapiens", allow_mutations: $("allowMutations").checked};
    const pkg = await api(base(project) + "/targets", "POST", {run_id: run, request});
    if (project === state.project && run === state.run) {await refresh(); notify("Actual input package recorded: " + label(pkg.status));}
  } finally {node.disabled = false;}
}
async function uploadExperiment(event) {
  event.preventDefault(); const file = $("experimentFile").files[0], node = $("uploadExperiment");
  if (!file) throw new Error("Choose a CSV file."); if (file.size > 2_000_000) throw new Error("CSV files must not exceed 2 MB.");
  const project = state.project, run = state.run; node.disabled = true;
  try {
    const spec = {name: $("experimentName").value.trim(), kind: $("experimentKind").value,
      endpoint: $("experimentEndpoint").value.trim(), unit: $("experimentUnit").value.trim(),
      candidate_id: $("experimentCandidate").value, research_run_id: run,
      control_group: $("controlGroup").value.trim(), chassis_name: state.selected?.brief.chassis_name || $("chassis").value,
      condition: $("experimentCondition").value.trim(), time_unit: $("experimentTimeUnit").value.trim(),
      dose_unit: $("experimentDoseUnit").value.trim(), qc_passed: $("qcPassed").checked,
      qc_notes: $("qcNotes").value.trim(), detection_limit: $("detectionLimit").value === "" ? null : Number($("detectionLimit").value)};
    const result = await api(base(project) + "/experiments", "POST", {csv_text: await file.text(), spec});
    if (project !== state.project) return;
    $("experimentResult").replaceChildren(callout("Measurement record saved: " + label(result.status) + ". Linked candidate and chassis assessments retain their own review records.", result.status === "measured" ? "" : "amber"), detail("Actual experiment analysis", result));
    await refresh(); notify("Experiment analysis saved: " + label(result.status));
  } finally {node.disabled = false;}
}
function bind() {
  window.addEventListener("hashchange", () => {
    const view = location.hash.slice(1);
    if (VIEWS[view] && view !== state.view) navigate(view);
  });
  $("navToggle").addEventListener("click", () => {
    const open = $("navToggle").getAttribute("aria-expanded") !== "true";
    $("navToggle").setAttribute("aria-expanded", String(open));
    document.querySelector(".sidebar").classList.toggle("navigation-open", open);
  });
  for (const node of document.querySelectorAll("[data-view]")) node.addEventListener("click", () => navigate(node.dataset.view));
  $("newResearch").addEventListener("click", () => {window.ResearchIntake?.startNew();navigate("research"); $("requirementText").focus();});
  $("researchForm").addEventListener("submit", submitResearch);
  $("researchMode").addEventListener("change", configureResearchMode);
  $("taskIntent").addEventListener("change", () => {
    if ($("taskIntent").value === "candidate_assessment") document.querySelector(".research-options").open = true;
  });
  $("agentEventFilter").addEventListener("change", renderTimeline);
  $("hypothesisSearch").addEventListener("input", renderHypotheses);
  $("hypothesisVersion").addEventListener("change", renderHypotheses);
  $("hypothesisOrigin").addEventListener("change", renderHypotheses);
  $("targetForm").addEventListener("submit", event => prepareTarget(event).catch(error => notify(error.message, true)));
  $("experimentForm").addEventListener("submit", event => uploadExperiment(event).catch(error => notify(error.message, true)));
  $("closeDialog").addEventListener("click", () => $("evidenceDialog").close());
  $("evidenceDialog").addEventListener("click", event => {if (event.target === $("evidenceDialog")) {const r = $("evidenceDialog").getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) $("evidenceDialog").close();}});
  $("candidateSearch").addEventListener("input", renderCandidates); $("candidateDecision").addEventListener("change", renderCandidates);
  $("evidenceSearch").addEventListener("input", renderEvidence); $("evidencePolarity").addEventListener("change", renderEvidence);
  $("project").addEventListener("change", () => {state.selectionEpoch++; state.project = $("project").value; state.run = ""; clearSelected(); navigate(state.view); loadRuns().catch(error => notify(error.message, true));});
  $("run").addEventListener("change", () => {selectRun($("run").value, state.runs.find(item => item.run_id === $("run").value)); refresh().catch(error => notify(error.message, true)); if (state.run) navigate("overview");});
  $("openHistory").addEventListener("click", () => {if (!state.run) notify("Select a project and a saved research run first."); else navigate("overview");});
  $("refresh").addEventListener("click", () => refresh().catch(error => notify(error.message, true)));
  $("candidateCSV").addEventListener("click", () => {if (state.run) window.open(runBase() + "/export?format=csv", "_blank", "noopener");});
  $("newProject").addEventListener("click", async () => {
    const name = prompt("Name the research project"); if (!name?.trim()) return;
    const previousProject = state.project, previousRun = state.run, epoch = state.selectionEpoch;
    try {const project = await api("/api/projects", "POST", {name: name.trim()});
      if (!selectionCurrent(epoch, previousProject, previousRun)) {notify("Project created. Refresh and select it from the project list."); return;}
      state.selectionEpoch++; state.project = project.project_id; state.run = ""; clearSelected(); await loadProjects(project.project_id); navigate("research"); notify("Project created.");
    } catch (error) {notify(error.message, true);}
  });
  $("createHandoff").addEventListener("click", async () => {
    const project = state.project, run = state.run, node = $("createHandoff"); node.disabled = true;
    try {if (!$("packageChoice").value) throw new Error("Select a ready target input package.");
      const job = await api(base(project) + "/compute", "POST", {run_id: run, package_id: $("packageChoice").value, max_md_jobs: Number($("mdBudget").value)});
      if (project === state.project) {await refresh(); notify("Compute configuration created; submission pending: " + job.compute_id);}
    } catch (error) {notify(error.message, true);}
    finally {renderPackages();}
  });
  $("followup").addEventListener("click", async () => {
    const project = state.project, parentRun = state.run, epoch = state.selectionEpoch;
    try {const ids = [...$("experimentList").querySelectorAll("input:checked")].map(node => node.value);
      if (!ids.length) throw new Error("Select at least one submitted experiment record.");
      const run = await api(runBase(project, parentRun) + "/followup", "POST", {experiment_ids: ids, question: $("followupQuestion").value.trim(), prepare_targets: false});
      if (!selectionCurrent(epoch, project, parentRun)) {notify("Follow-up research created. Find it in the original project's saved runs."); return;}
      selectRun(run.run_id, run); navigate("overview"); await loadRuns(run.run_id); notify("New research version created. The parent run and original measurements are preserved.");
    } catch (error) {notify(error.message, true);}
  });
}
async function init() {
  window.ResearchIntake?.init({api, buildPayload:buildResearchPayload, syncMode:()=>configureResearchMode(false), getSelection:()=>state.selectionEpoch,
    onSubmitted:async (project, run, epoch) => {
      if (epoch !== state.selectionEpoch) {notify("Research submitted. Find it in the new project's saved runs."); return;}
      state.selectionEpoch++; state.project = project; state.run = run.run_id;
      clearSelected(); await loadProjects(project); navigate(run.brief.research_mode === "co_scientist" ? "collaboration" : "overview");
      notify("One research run was created from the confirmed draft. Review its actual records and gaps.");
    }});
  if (location.protocol === "file:") {
    bind(); configureResearchMode(); renderRun(null); renderCatalogs(); navigate("research");
    $("connection").textContent = "Disconnected · local-file preview";
    $("connectionDot").className = "connection-dot error";
    for (const id of ["startResearch", "newProject", "project", "run", "refresh", "prepareTarget", "uploadExperiment", "followup", "createHandoff"]) $(id).disabled = true;
    selectionNotice("This is an unconnected local-file preview. Double-click start_ai_scientist.command in the repository, then open the local workbench. Research records and operations require the local HTTP service.", false, true);
    return;
  }
  const incoming = new URL(location.href);
  state.project = incoming.searchParams.get("project") || "";
  state.run = incoming.searchParams.get("run") || "";
  bind(); configureResearchMode(); renderRun(null); renderCatalogs(); navigate(location.hash.slice(1) || "research");
  try {
    const health = await api("/api/health"); $("connection").textContent = "Local service online"; $("connectionDot").className = "connection-dot connected";
    if (health.version) document.querySelector(".sidebar-version>span:last-child").textContent = "v" + health.version;
    await loadProjects();
  } catch (error) {$("connection").textContent = "Service connection failed"; $("connectionDot").className = "connection-dot error"; notify(error.message, true);}
}
init();

