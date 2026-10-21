/* Read-only demo workspace. Repository reports and imported scores keep separate provenance. */
"use strict";

(function (root) {
  const DEMO_ID = "hdac2_h20";
  const MAX_BYTES = 500000;
  const MAX_DISPLAY = 100;
  const ROLES = {model: "demoColumnModel", compound_id: "demoColumnCompound", score: "demoColumnScore", status: "demoColumnStatus", error: "demoColumnError"};
  const SYNTHETIC_CSV = "model,compound_id,score,status,error\nDrugCLIP,SYNTHETIC_A,0.82,ok,\nDrugCLIP,SYNTHETIC_B,0.61,ok,\nSPRINT,SYNTHETIC_A,0.35,ok,\nPSICHIC,SYNTHETIC_C,,failed,SYNTHETIC test failure\nPharmacoNet,SYNTHETIC_D,0.47,ok,\n";
  const state = {initialized: false, catalogAttempted: false, catalog: null, catalogPromise: null,
    scenario: "common50", analysis: null, inputEpoch: 0, requestEpoch: 0, fileEpoch: 0,
    busy: false, reading: false, synthetic: false, filename: ""};
  const $ = id => root.document.getElementById(id);
  const items = value => Array.isArray(value) ? value : [];
  const str = value => value === null || value === undefined ? "Not reported" : String(value);
  // Display-only compatibility for fixed backend labels. Never apply to CSV cells or source records.
  const CATALOG_TEXT = Object.freeze({
    "用于展示四模型评分流程的 50 分子界面示例。": "A 50-molecule interface example for the four-model scoring workflow.",
    "列名映射示例；实际列名以导入 CSV 为准。": "Column mapping example; use the actual column names in the imported CSV.",
    "用于展示四模型评分流程的 50 个精选分子。": "A 50-molecule interface example for the four-model scoring workflow.",
    "HDAC2 / 4LXZ 固定靶点演示；更换靶点需要重建蛋白与口袋缓存，兼容的分子 embedding 可复用。": "Fixed-target HDAC2 / 4LXZ demo. A new target requires matching protein and pocket caches; compatible molecular embeddings may be reused.",
    "展示来源仓库记录的运行结果；本地执行状态见运行信息。": "Displays reported results from the source repository; local execution status is shown separately.",
    "计时分为准备阶段与评分阶段；模型加载、编码、构象生成和预热属于准备范围，准备阶段部分复用缓存。": "Timing separates preparation and scoring. Model loading, encoding, conformer generation and warmup are preparation tasks; preparation partly reuses caches.",
    "演示分数用于模型内排序；根入口另以完整有效评分池的百分位构建跨模型共识。": "Demo scores describe within-model ranking. The root workflow separately builds cross-model consensus from full valid score-pool percentiles.",
    "分数比较使用相同模型、候选池和预处理版本；DrugCLIP 的 median/MAD 标度分别对应 common50 和 capacity1000 候选池。": "Compare scores within the same model, candidate pool and preprocessing version. DrugCLIP median/MAD scales are specific to the common50 and capacity1000 pools.",
    "用户导入评分：显示本次 CSV 的数值排序，靶点、候选池、预处理版本与模型来源待核验。": "User-imported scores: numeric rankings from this CSV; target, candidate pool, preprocessing version and model provenance await verification.",
    "本演示展示固定 HDAC2 输入下的四模型评分及 CSV 排序检查。": "This demo presents four-model scoring and CSV ranking checks for fixed HDAC2 inputs.",
    "用户导入评分独立保存在本次内存检查中；研究证据、运行历史与模型执行各自记录。": "User-imported scores remain in this in-memory inspection; research evidence, run history and model execution are recorded separately.",
    "共同 50 分子": "Common 50 molecules",
    "常见 50 分子": "Common 50 molecules",
    "容量 1000 分子": "Capacity: 1,000 molecules",
    "人工挑选的 50 分子演示集，不代表随机采样的科学基准。": "A 50-molecule interface example for the four-model scoring workflow.",
    "共同 50 分子加 950 分子；四模型共 4000 次评分尝试。": "The common 50 molecules plus 950 additional molecules; 4,000 scoring attempts across four models.",
    "常见 50 分子加 950 个容量测试分子；四模型共 4000 次评分尝试。": "The common 50 molecules plus 950 capacity-test molecules; 4,000 scoring attempts across four models.",
    "来源 README": "Source README",
    "来源中文使用说明": "Source usage guide (Chinese)",
    "来源共同 50 分子表": "Source common-50 molecule table",
    "来源常见 50 分子表": "Source common-50 molecule table",
    "完整 HDAC2 H20 演示包": "Complete HDAC2 H20 demo archive",
    "PSICHIC 权重": "PSICHIC weights",
    "模型源码": "Model source code",
    "分子构象": "Molecular conformers",
    "HDAC2 固定靶点缓存": "Fixed-target HDAC2 caches",
    "来源实测 CSV": "Scoring CSV files reported by the source",
    "Python / CUDA 环境": "Python / CUDA environments",
    "DrugCLIP 原始六折大权重": "Original DrugCLIP six-fold weights",
    "SaProt 原始大权重": "Original SaProt weights",
    "已编码缓存的真实向量匹配；原始六折大权重未包含。": "Vector matching with precomputed embeddings; original six-fold weights are not included.",
    "已编码缓存的真实向量匹配；原始 SaProt 大权重未包含。": "Vector matching with precomputed embeddings; original SaProt weights are not included.",
    "模型 forward 推理；演示包包含 PSICHIC 权重。": "Model forward inference; the demo archive includes PSICHIC weights.",
    "基于已准备口袋药效团和构象的 CPU matching。": "CPU matching with a prepared pocket pharmacophore and molecular conformers.",
    "HDAC2 / 4LXZ 固定靶点演示；更换靶点需要重建对应蛋白与口袋缓存，兼容的分子 embedding 可复用，不能只改名称。": "Fixed-target HDAC2 / 4LXZ demo. A new target requires matching protein and pocket caches; compatible molecular embeddings may be reused.",
    "仓库历史 benchmark 是来源报告，未在本机复现；本机状态为 not_run。": "Displays reported results from the source repository; local execution status is shown separately.",
    "在线计时不含模型加载、编码、构象生成和预热；准备阶段部分复用缓存。": "Timing separates preparation and scoring. Model loading, encoding, conformer generation and warmup are preparation tasks; preparation partly reuses caches.",
    "分数只用于各模型内部排序，不跨模型归一化，也不是药效、疾病疗效或结合实验证据。": "Demo scores describe within-model ranking. The root workflow separately builds cross-model consensus from full valid score-pool percentiles.",
    "科学比较需同模型、同候选池及同预处理版本；DrugCLIP common50 与 capacity1000 的 median/MAD 分数不能直接比较。": "Compare scores within the same model, candidate pool and preprocessing version. DrugCLIP median/MAD scales are specific to the common50 and capacity1000 pools.",
    "导入只派生本次 CSV 的数值排序；其靶点关联、候选池、预处理版本与真实模型输出来源均未核验。": "User-imported scores: numeric rankings from this CSV; target, candidate pool, preprocessing version and model provenance await verification.",
    "演示不构成 ADMET、ChassisGate 生产能力、PK/PD、MD 或湿实验验证。": "This demo presents four-model scoring and CSV ranking checks for fixed HDAC2 inputs.",
    "用户导入数据未核验，不加入研究证据或运行历史，也不自动触发下载或模型执行。": "User-imported scores remain in this in-memory inspection; research evidence, run history and model execution are recorded separately."
  });
  const catalogText = value => Object.prototype.hasOwnProperty.call(CATALOG_TEXT, value) ? CATALOG_TEXT[value] : str(value);
  const VALIDATION_TEXT = Object.freeze({
    invalid_input_type: "CSV input must be text.",
    byte_limit: "CSV exceeds the 500,000 UTF-8 byte limit.",
    invalid_encoding: "CSV contains invalid Unicode characters.",
    invalid_control_character: "CSV must not contain NUL characters.",
    invalid_mapping: "Provide an explicit column-mapping object.",
    unknown_mapping_role: "The column mapping contains an unknown role.",
    missing_mapping_role: "Map model, compound_id, score, and status.",
    invalid_mapping_header: "Mapped column names must contain 1–128 characters.",
    ambiguous_mapping: "Map each role to a different column.",
    empty_input: "No CSV header or data was provided.",
    malformed_csv: "CSV has an invalid header, quoting, or record structure; no rows were accepted.",
    column_limit: "CSV requires a header and no more than 64 columns.",
    invalid_header: "CSV headers must be nonempty names of 1–128 characters.",
    duplicate_header: "CSV contains duplicate headers or headers that differ only by surrounding spaces.",
    missing_mapped_column: "A mapped column is missing from the CSV header.",
    row_limit: "CSV exceeds 5,000 data rows; no rows were accepted.",
    row_width: "The row has a different number of columns from the header.",
    cell_limit: "A data cell exceeds 1,024 characters.",
    unknown_model: "Model must be DrugCLIP, SPRINT, PSICHIC, or PharmacoNet.",
    missing_compound_id: "Compound ID is required.",
    unsafe_compound_id: "Compound ID must be a safe identifier of at most 128 characters.",
    invalid_status: "Status must be ok, success (an alias for ok), or failed.",
    failed_score_not_empty: "A failed row must have an empty score.",
    invalid_score: "An ok row requires a valid finite numeric score.",
    non_finite_score: "Scores must not be NaN or infinite.",
    duplicate_model_compound: "Duplicate model/compound IDs are rejected in every occurrence.",
    no_data_rows: "CSV has a header but no data rows."
  });
  function apiErrorText(value) {
    return value === "演示导入请求不符合格式或长度限制；未回显输入。" ?
      "The demo request has invalid fields or exceeds a length limit. Input was not echoed." : str(value);
  }
  const node = (tag, className = "", content) => {
    const element = root.document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = str(content);
    return element;
  };
  function link(label, href) {
    try {
      const url = new URL(href);
      if (!["https:", "http:"].includes(url.protocol)) return node("span", "muted", label);
      const element = node("a", "", label);
      element.href = url.href; element.target = "_blank"; element.rel = "noopener noreferrer";
      return element;
    } catch (_) {return node("span", "muted", label);}
  }
  function byteLength(value) {return new TextEncoder().encode(value).length;}
  function inputIssue(value, maxBytes = MAX_BYTES) {
    if (!String(value).trim()) return "Choose a CSV file or paste its contents first.";
    if (byteLength(value) > maxBytes) return "CSV exceeds the " + maxBytes.toLocaleString("en-US") + " byte limit. Use a smaller file and retry.";
    return "";
  }
  // Read just the header for mapping suggestions; the API validates the entire CSV.
  function csvHeaders(value) {
    const headers = []; let cell = "", quoted = false;
    const text = String(value).replace(/^\uFEFF/, "");
    for (let index = 0; index < Math.min(text.length, 65536); index++) {
      const char = text[index];
      if (char === '"') {
        if (quoted && text[index + 1] === '"') {cell += '"'; index++;}
        else quoted = !quoted;
      } else if (!quoted && (char === "," || char === "\n" || char === "\r")) {
        headers.push(cell); cell = "";
        if (char !== "," || headers.length >= 64) break;
      } else {cell += char;}
      if (index === text.length - 1) headers.push(cell);
    }
    return [...new Set(headers.filter(header => header && header.length <= 128))].slice(0, 64);
  }
  function visibleRows(group, filter = "all") {
    return items(group?.rows).filter(row => filter === "all" || row.status === filter).slice(0, MAX_DISPLAY);
  }
  function currentRequest(epoch, inputEpoch) {return epoch === state.requestEpoch && inputEpoch === state.inputEpoch;}
  function reportForDownload(analysis, synthetic) {
    return {kind: "hdac2_score_inspection", demo_id: DEMO_ID, synthetic: Boolean(synthetic),
      interpretation: "computational_prediction_only", payload_scope: "bounded_api_preview",
      provenance: {kind: "user_imported_not_verified", verified: false, target_association: "not_verified"},
      export_note: "JSON contains summaries, validation issues and a preview of at most 100 rows per model. Use the original CSV for the complete score table. Target, candidate-pool and preprocessing verification status: pending.",
      local_execution: {status: "not_run", executed: false}, analysis};
  }
  async function request(path, body) {
    if (root.location.protocol === "file:") throw new Error("This local file preview is disconnected. Start AI Scientist and open the HDAC2 workbench at http://127.0.0.1:8765/.");
    let response;
    try {response = await root.fetch(path, {method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body)});
    } catch (_) {throw new Error("Cannot connect to the local demo API. Confirm AI Scientist is running, then retry.");}
    let data;
    try {data = await response.json();} catch (_) {throw new Error("The demo API returned an unreadable response (HTTP " + response.status + ").");}
    if (!response.ok) {
      let message = data.detail || data.message;
      if (Array.isArray(message)) message = message.map(item => apiErrorText(item.msg || item.message || "Invalid parameter")).join("; ");
      throw new Error(typeof message === "string" ? apiErrorText(message) : "The demo API request failed (HTTP " + response.status + ").");
    }
    return data;
  }
  function error(message = "") {$("demoImportError").textContent = message; $("demoImportError").hidden = !message;}
  function setBusy(value) {
    state.busy = value;
    $("demoImportForm").setAttribute("aria-busy", String(value || state.reading));
    $("demoAnalyze").disabled = value || state.reading;
    $("demoAnalyze").textContent = value ? "Validating CSV…" : "Analyze CSV";
  }
  function emptyResults(message = "Choose a CSV or load the clearly labeled SYNTHETIC example, then select Analyze CSV.") {
    const empty = node("div", "demo-empty");
    empty.append(node("span", "", "◈"), node("h3", "", "No analysis for the current input"), node("p", "", message));
    $("demoResults").replaceChildren(empty);
    $("demoResultSummary").replaceChildren(); $("demoValidationIssues").replaceChildren();
    $("demoResultsControls").hidden = true; $("demoDownload").disabled = true;
    $("demoResultCaption").textContent = "Analyze a CSV to inspect successful rows, failures, and format issues.";
  }
  function invalidate() {
    state.inputEpoch++; state.requestEpoch++; state.fileEpoch++;
    state.analysis = null; state.reading = false; setBusy(false); error();
    emptyResults("The input or column mapping changed. Analyze the current CSV again.");
  }
  function updateInput() {
    const value = $("demoCsvText").value;
    $("demoInputStatus").textContent = value ? byteLength(value).toLocaleString("en-US") + " bytes" +
      (state.synthetic ? " · SYNTHETIC test data" : state.filename ? " · File: " + state.filename : " · Pasted input") : "No file or pasted input selected.";
    const options = csvHeaders(value).map(header => {const option = node("option"); option.value = header; return option;});
    $("demoColumnOptions").replaceChildren(...options);
  }
  async function readFile() {
    invalidate(); state.synthetic = false; state.filename = "";
    const file = $("demoFile").files?.[0];
    if (!file) {updateInput(); return;}
    $("demoCsvText").value = ""; updateInput();
    if (file.size > MAX_BYTES) {error("The file exceeds the 500,000 byte (500 KB) limit. Choose a smaller CSV."); return;}
    const epoch = state.fileEpoch;
    state.reading = true; setBusy(false); $("demoInputStatus").textContent = "Reading CSV file…";
    try {
      const value = await file.text();
      if (epoch !== state.fileEpoch) return;
      const issue = inputIssue(value);
      if (issue) {error(issue); $("demoInputStatus").textContent = "File not imported. Check the CSV contents."; return;}
      $("demoCsvText").value = value; state.filename = file.name; updateInput();
    } catch (_) {
      if (epoch === state.fileEpoch) {error("Cannot read this file. Choose the CSV again or paste its contents."); $("demoInputStatus").textContent = "File reading failed.";}
    } finally {if (epoch === state.fileEpoch) {state.reading = false; setBusy(false);}}
  }
  function synthetic() {
    invalidate(); state.synthetic = true; state.filename = ""; $("demoFile").value = "";
    $("demoCsvText").value = SYNTHETIC_CSV;
    for (const [role, id] of Object.entries(ROLES)) $(id).value = role;
    updateInput();
  }
  function renderCatalog(catalog) {
    $("demoSourceLinks").replaceChildren(link("GitHub source →", catalog.source?.readme_url), link("Usage and timing notes →", catalog.source?.usage_url));
    const sourceMeta = node("small", "muted", str(catalog.source?.repository) + " · commit " + str(catalog.source?.commit));
    $("demoSourceLinks").append(sourceMeta);
    const scenarios = items(catalog.scenarios);
    if (!scenarios.some(scenario => scenario.id === state.scenario)) state.scenario = scenarios[0]?.id || "";
    const tabs = scenarios.map(scenario => {
      const button = node("button", "button secondary small", catalogText(scenario.label || scenario.id));
      button.type = "button"; button.dataset.scenario = scenario.id;
      button.addEventListener("click", () => {state.scenario = scenario.id; renderBenchmark();});
      return button;
    });
    $("demoScenarioTabs").replaceChildren(...tabs);
    const notes = node("div", "demo-source-notes");
    notes.append(node("p", "", "Preparation and scoring are reported separately. Resource entries describe the source package; this view lists reference metadata."));
    const resources = node("ul", "demo-resource-list");
    for (const resource of items(catalog.resources)) {
      const item = node("li"); item.append(link(catalogText(resource.label || resource.id), resource.url));
      item.append(node("span", "muted", (resource.size_mib ? " · " + resource.size_mib + " MiB" : "") + " · Source-package reference"));
      if (items(resource.included).length) item.append(node("p", "muted", "Includes: " + resource.included.map(catalogText).join(", ")));
      if (items(resource.excluded).length) item.append(node("p", "muted", "Excludes: " + resource.excluded.map(catalogText).join(", ")));
      resources.append(item);
    }
    notes.append(resources);
    for (const limitation of items(catalog.limitations)) notes.append(node("p", "muted", catalogText(limitation)));
    $("demoBenchmarkNotes").replaceChildren(notes); renderBenchmark();
  }
  function metric(label, value, note) {
    const card = node("div", "demo-metric");
    card.append(node("span", "demo-metric-label", label), node("strong", "", value), node("small", "muted", note));
    return card;
  }
  function renderBenchmark() {
    const catalog = state.catalog;
    if (!catalog) return;
    const scenario = items(catalog.scenarios).find(item => item.id === state.scenario);
    if (!scenario) return;
    for (const button of $("demoScenarioTabs").children) {
      const active = button.dataset.scenario === state.scenario;
      button.setAttribute("aria-pressed", String(active)); button.classList.toggle("active", active);
    }
    const report = scenario.reported_benchmark || {};
    $("demoScenarioNote").textContent = str(scenario.molecule_count) + " molecules · " + catalogText(scenario.selection_note);
    const content = node("div");
    const metrics = node("div", "demo-benchmark-metrics");
    metrics.append(metric("Four-model successes / attempts", str(report.successful) + " / " + str(report.attempted), "Source-reported four-model aggregate"),
      metric("PREPARATION", str(report.preparation_seconds) + " s", report.partial_cache_reuse ? "The source partly reused caches" : "Timing reported by the source"),
      metric("SCORING TIME", str(report.online_seconds) + " s", "Four-model scoring stage"));
    content.append(metrics);
    const excluded = items(report.online_timing_excludes);
    const names = {model_loading: "model loading", encoding: "encoding", conformer_generation: "conformer generation", warmup: "warmup"};
    content.append(node("p", "demo-timing-note", "Scoring-time exclusions: " + (excluded.length ? excluded.map(value => names[value] || value).join(", ") : "not reported by the source") + ". Timing scope: source-reported aggregate; local and per-model timing fields: unrecorded."));
    const models = node("div", "demo-model-grid");
    for (const model of items(catalog.models)) {
      const card = node("article", "demo-model-card");
      card.append(node("h3", "", model.label || model.id), node("p", "", catalogText(model.execution_mode)), node("small", "muted", "Interpret within the same model, candidate pool, and preprocessing · Local: not_run")); models.append(card);
    }
    content.append(models); $("demoBenchmark").replaceChildren(content);
  }
  function connection(message, failed = false) {
    const container = $("demoConnection");
    container.className = "callout" + (failed ? " amber" : "");
    container.setAttribute("role", failed ? "alert" : "status"); container.replaceChildren(node("p", "", message));
    if (failed && root.location.protocol !== "file:") {
      const retry = node("button", "button secondary small", "Retry connection"); retry.type = "button";
      retry.addEventListener("click", () => fetchCatalog(true)); container.append(retry);
    }
  }
  async function fetchCatalog(force = false) {
    if (state.catalogPromise) return state.catalogPromise;
    if (state.catalog || state.catalogAttempted && !force) return;
    state.catalogAttempted = true;
    connection("Loading the demo source report…");
    state.catalogPromise = (async () => {
      try {
        const catalog = await request("/api/demos/" + DEMO_ID);
        if (catalog.id !== DEMO_ID || catalog.schema_version !== 1 || !Array.isArray(catalog.scenarios)) throw new Error("The demo API format is incompatible. The source report has not been loaded.");
        state.catalog = catalog; renderCatalog(catalog);
        connection("Connected to the local demo API. Benchmarks retain source-repository provenance; CSV inspection stays in memory. Local execution status is shown above.");
      } catch (problem) {connection(problem.message + " Source statistics have not been loaded.", true);}
      finally {state.catalogPromise = null;}
    })();
    return state.catalogPromise;
  }
  function renderIssues(analysis) {
    const list = items(analysis.validation_issues);
    if (!list.length) {$("demoValidationIssues").replaceChildren(); return;}
    const details = node("details", "demo-details demo-validation");
    details.open = !analysis.accepted;
    details.append(node("summary", "", "Format and validation issues · " + str(analysis.validation_issue_count) + (analysis.issues_truncated ? " (truncated)" : "")));
    const issues = node("ul");
    for (const issue of list.slice(0, MAX_DISPLAY)) {
      const message = Object.prototype.hasOwnProperty.call(VALIDATION_TEXT, issue.code) ? VALIDATION_TEXT[issue.code] : "Validation issue (" + str(issue.code) + ").";
      issues.append(node("li", "", (issue.row_number ? "CSV row " + issue.row_number + " · " : "") + (issue.field ? issue.field + " · " : "") + message));
    }
    details.append(issues);
    if (list.length > MAX_DISPLAY) details.append(node("p", "muted", "Showing the first 100 issues. Download JSON to inspect the remaining issues returned by the API."));
    $("demoValidationIssues").replaceChildren(details);
  }
  function renderAnalysis() {
    const analysis = state.analysis;
    if (!analysis) return;
    $("demoResultCaption").textContent = (state.synthetic ? "SYNTHETIC test data · " : "User import · ") + "user_imported_not_verified · Computational predictions only; association with HDAC2 or local execution is unverified.";
    const summary = node("div", "demo-analysis-summary");
    summary.append(metric("Accepted / rejected", str(analysis.accepted) + " / " + str(analysis.rejected), "CSV formatting checks"),
      metric("Successful / failed", str(analysis.summary?.successful) + " / " + str(analysis.summary?.failed), "Validated against the input status"),
      metric("Models observed", str(analysis.summary?.models_observed), "Candidate pool and preprocessing are unverified"));
    $("demoResultSummary").replaceChildren(summary);
    const select = $("demoResultModel"), previous = select.value;
    const groups = items(analysis.by_model);
    select.replaceChildren(...groups.map(group => {const option = node("option", "", group.model); option.value = group.model; return option;}));
    if (groups.some(group => group.model === previous)) select.value = previous;
    else select.value = groups.find(group => group.observed > 0)?.model || groups[0]?.model || "";
    $("demoResultsControls").hidden = !groups.length;
    $("demoDownload").disabled = false;
    renderResultRows(); renderIssues(analysis);
  }
  function renderResultRows() {
    const analysis = state.analysis;
    if (!analysis) return;
    const group = items(analysis.by_model).find(item => item.model === $("demoResultModel").value);
    const filter = $("demoResultFilter").value;
    const rows = visibleRows(group, filter);
    const container = node("div");
    if (!group) {container.append(node("p", "muted", "No model rows are available for this input. Check the format and validation issues.")); $("demoResults").replaceChildren(container); return;}
    container.append(node("p", "demo-model-summary", str(group.model) + " · Accepted " + str(group.accepted) + " / rejected " + str(group.rejected) + " · Successful " + str(group.successful) + " / failed " + str(group.failed)));
    if (!rows.length) container.append(node("p", "muted", "No rows in the current preview match this status."));
    else {
      const wrap = node("div", "demo-table-wrap"), table = node("table", "demo-table"), head = node("thead"), headings = node("tr");
      const labels = ["Rank in this import", "Compound ID", "Score", "Status", "Error / note"];
      for (const label of labels) {const th = node("th", "", label); th.scope = "col"; headings.append(th);}
      head.append(headings); table.append(head);
      const body = node("tbody");
      for (const row of rows) {
        const tr = node("tr");
        const safeError = row.error_redacted && row.error === "[错误详情包含敏感或不安全内容，已隐藏]" ? "[Sensitive or unsafe error details hidden]" : row.error;
        const values = [row.rank ?? "—", row.compound_id, row.score ?? "—", row.status === "ok" ? "Successful · ok" : "Failed · failed", safeError || (row.error_redacted ? "Error details sanitized" : "—")];
        values.forEach((value, index) => {const cell = node("td", "", value); cell.dataset.label = labels[index]; tr.append(cell);});
        body.append(tr);
      }
      table.append(body); wrap.append(table); container.append(wrap);
    }
    container.append(node("p", "demo-display-note muted", "Showing " + rows.length + " rows. Each model preview contains at most 100 successful and failed rows, with successful scores in descending order followed by failures." +
      (group.omitted ? " The API omitted " + str(group.omitted) + " rows." : "") + " JSON contains the bounded API preview; the original CSV retains the complete table. Ranks describe valid rows in this import. Candidate-pool and preprocessing compatibility status: pending."));
    $("demoResults").replaceChildren(container);
  }
  async function analyze(event) {
    event?.preventDefault(); if (state.busy || state.reading) return;
    const csv = $("demoCsvText").value, issue = inputIssue(csv);
    if (issue) {error(issue); return;}
    const columns = {};
    for (const [role, id] of Object.entries(ROLES)) {
      const value = $(id).value;
      if (role !== "error" && !value) {error("Specify every required column name."); return;}
      if (value) columns[role] = value;
    }
    const epoch = ++state.requestEpoch, inputEpoch = state.inputEpoch;
    error(); setBusy(true);
    try {
      const analysis = await request("/api/demos/" + DEMO_ID + "/inspect-scores", {csv_text: csv, columns});
      if (!currentRequest(epoch, inputEpoch)) return;
      if (analysis.demo_id !== DEMO_ID || analysis.schema_version !== 1 || !Array.isArray(analysis.by_model)) throw new Error("The analysis API format is incompatible. Results were not displayed.");
      state.analysis = analysis; renderAnalysis();
    } catch (problem) {if (currentRequest(epoch, inputEpoch)) error(problem.message);}
    finally {if (currentRequest(epoch, inputEpoch)) setBusy(false);}
  }
  function download() {
    if (!state.analysis) return;
    const data = reportForDownload(state.analysis, state.synthetic);
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json;charset=utf-8"});
    const url = URL.createObjectURL(blob), anchor = node("a");
    anchor.href = url; anchor.download = DEMO_ID + (state.synthetic ? "-SYNTHETIC" : "") + "-inspection.json"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function initialize() {
    if (state.initialized || !$("view-demo")) return;
    state.initialized = true;
    $("demoImportForm").addEventListener("submit", analyze);
    $("demoFile").addEventListener("change", readFile);
    $("demoCsvText").addEventListener("input", () => {invalidate(); if (!$("demoCsvText").value.trim()) state.synthetic = false; state.filename = ""; $("demoFile").value = ""; updateInput();});
    for (const id of Object.values(ROLES)) $(id).addEventListener("input", invalidate);
    $("demoSynthetic").addEventListener("click", synthetic);
    $("demoResultModel").addEventListener("change", renderResultRows);
    $("demoResultFilter").addEventListener("change", renderResultRows);
    $("demoDownload").addEventListener("click", download);
  }
  async function load() {initialize(); if (state.initialized) return fetchCatalog();}
  root.Hdac2Demo = Object.freeze({load});
  if (typeof module !== "undefined" && module.exports) module.exports = {byteLength, inputIssue, csvHeaders, visibleRows, reportForDownload, SYNTHETIC_CSV, MAX_BYTES, MAX_DISPLAY};
})(typeof window !== "undefined" ? window : globalThis);

