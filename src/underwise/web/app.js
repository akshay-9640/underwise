// Submission Review page. Every node is built with textContent, so text from a submission is never parsed as HTML.

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const LABELS = {
  business_name: "Business name", requested_effective_date: "Requested effective date", property_state: "Primary state",
  occupancy_description: "Occupancy", declared_location_count: "Declared locations",
  total_insured_value: "Total insured value", sprinklered: "Sprinklered", year_built: "Year built",
  loss_history_years: "Years of loss history", loss_runs_present: "Loss runs report attached",
  schedule_location_count: "Locations on schedule (CSV)",
};
const STATE_TEXT = { KNOWN: "Known", MISSING: "Missing", CONFLICT: "Conflict", INVALID: "Invalid", NOT_EXTRACTED: "Not extracted" };
const EMPTY_TEXT = {
  MISSING: "Not stated in the inspected sources", CONFLICT: "Unresolved: the sources disagree",
  INVALID: "Could not be used", NOT_EXTRACTED: "Not extracted",
};
const HINTS = {
  requested_effective_date: "YYYY-MM-DD", property_state: "OH", declared_location_count: "Whole number",
  total_insured_value: "Dollars, for example 8400000", year_built: "Four-digit year",
  loss_history_years: "Whole number of years", schedule_location_count: "Whole number",
};
const YES_NO = new Set(["sprinklered", "loss_runs_present"]);

let files = [];        // File objects chosen for the next packet
let current = null;    // the result on screen
let timer = null;
let chat = [];         // questions and answers for the packet on screen; cleared with the packet

function fmt(name, v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (name === "total_insured_value") return `$${Number(v).toLocaleString("en-US")}`;
  return String(v);
}

async function api(method, url, body) {
  const opts = { method };
  if (body instanceof FormData) opts.body = body;
  else if (body) { opts.body = JSON.stringify(body); opts.headers = { "Content-Type": "application/json" }; }
  let r;
  try {
    r = await fetch(url, opts);
  } catch {
    throw new Error("Could not reach the server. Check that it is still running.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `The request failed (HTTP ${r.status}).`);
  return data;
}

// ---------- Start screen ----------

function kindOf(name) {
  const ext = name.toLowerCase().split(".").pop();
  return { txt: "Email", eml: "Email", pdf: "PDF", csv: "CSV" }[ext] || "Unsupported";
}

function addFiles(list) {
  for (const f of list) {
    files = files.filter((x) => x.name.toLowerCase() !== f.name.toLowerCase());
    files.push(f);
  }
  renderFiles();
}

function renderFiles() {
  const ul = $("file-list");
  ul.replaceChildren();
  for (const f of files) {
    const li = el("li");
    const rm = el("button", "remove", "Remove");
    rm.type = "button";
    rm.setAttribute("aria-label", `Remove ${f.name}`);
    rm.addEventListener("click", () => { files = files.filter((x) => x !== f); renderFiles(); });
    li.append(el("span", "kind", kindOf(f.name)), el("span", "fname", f.name), rm);
    ul.append(li);
  }
  updateProcessButton();
}

function updateProcessButton() {
  const empty = !files.length && !$("email-text").value.trim();
  $("process-btn").disabled = empty;
  $("process-hint").hidden = !empty;
}

// "claude-code: claude-opus-5-5" -> "claude-opus-5-5 via the Claude Code CLI"
function modelName(label) {
  if (!label) return "none configured";
  const [provider, model] = label.split(": ");
  const via = { "claude-code": "the Claude Code CLI", "openai-compatible": "an OpenAI-compatible endpoint" }[provider];
  return via ? `${model} via ${via}` : label;
}

function showError(msg) {
  const e = $("upload-error");
  e.textContent = msg;
  e.hidden = !msg;
}

function busy(title, detail) {
  $("busy-title").textContent = title;
  $("busy-detail").textContent = detail;
  const started = Date.now();
  $("elapsed").textContent = "";
  clearInterval(timer);
  timer = setInterval(() => { $("elapsed").textContent = `${Math.round((Date.now() - started) / 1000)} s`; }, 1000);
  $("start").hidden = true;
  $("result").hidden = true;
  $("busy").hidden = false;
}

function idle() {
  clearInterval(timer);
  $("busy").hidden = true;
}

async function submit(e) {
  e.preventDefault();
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.append("email_text", $("email-text").value);
  showError("");
  busy("Processing packet", "Reading documents, extracting fields with the model, then checking the rules. This usually takes 10 to 30 seconds.");
  try {
    showResult(await api("POST", "/api/packets", form));
  } catch (err) {
    idle();
    $("start").hidden = false;
    showError(err.message);
  }
}

function newPacket() {
  // Nothing from the previous applicant survives: selections, pasted text and the rendered result are all cleared.
  current = null;
  chat = [];
  files = [];
  $("files").value = "";
  $("email-text").value = "";
  $("paste").open = false;
  renderFiles();
  showError("");
  $("result").replaceChildren();
  $("result").hidden = true;
  $("start").hidden = false;
  window.scrollTo(0, 0);
  $("files").focus();
}

// ---------- Result ----------

function showResult(r, keepScroll = false) {
  if (!current || current.packet_id !== r.packet_id) chat = [];
  current = r;
  idle();
  const y = window.scrollY;
  const root = $("result");
  root.replaceChildren(header(r), ...banners(r), el("p", "disclaimer", r.disclaimer), body(r));
  root.hidden = false;
  $("start").hidden = true;
  window.scrollTo(0, keepScroll ? y : 0);
}

function header(r) {
  const name = r.fields.business_name;
  const title = name.override ? fmt("business_name", name.override.value) : name.state === "KNOWN" ? name.value : "Applicant name not found";
  const head = el("div", "res-head");
  const left = el("div");
  left.append(el("h1", null, title),
    el("p", "res-meta", `Packet ${r.packet_id}. ${r.attachments.length} file${r.attachments.length === 1 ? "" : "s"}. Extraction model: ${modelName(r.model)}.`));
  const actions = el("div", "res-actions");
  const ready = r.overall_status === "READY_FOR_REVIEW";
  const overall = el("span", `overall ${ready ? "ready" : "needs"}`, ready ? "Ready for review" : "Needs review");
  overall.title = r.overall_status;
  const json = el("button", "btn ghost small", "Download JSON");
  json.type = "button";
  json.addEventListener("click", () => {
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: "application/json" }));
    a.download = `submission-${current.packet_id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  const report = el("a", "btn ghost small", "Report");
  report.href = `/api/packets/${r.packet_id}/report`;
  report.target = "_blank";
  report.rel = "noopener";
  const again = el("button", "btn small", "New packet");
  again.type = "button";
  again.addEventListener("click", newPacket);
  actions.append(overall, json, report, again);
  head.append(left, actions);
  return head;
}

function banners(r) {
  const out = [];
  if (r.extraction.status !== "OK") {
    const b = el("div", "banner");
    b.append(el("p", null, r.extraction.detail));
    if (r.extraction.status === "FAILED" || r.model) {
      const retry = el("button", "btn small", "Retry extraction");
      retry.type = "button";
      retry.addEventListener("click", async () => {
        busy("Retrying extraction", "Reading the same packet again with the model. Corrections made so far are cleared.");
        try { showResult(await api("POST", `/api/packets/${r.packet_id}/process`)); }
        catch (err) { showResult(current); alert(err.message); }
      });
      b.append(retry);
    }
    out.push(b);
  }
  return out;
}

function body(r) {
  const grid = el("div", "grid");
  const left = el("div", "col");
  const right = el("div", "col");
  left.append(checksCard(r), fieldsCard(r));
  right.append(summaryCard(r), askCard(r), draftCard(r), attachmentsCard(r));
  grid.append(left, right);
  return grid;
}

function card(title, ...children) {
  const c = el("section", "card");
  const h = el("h2", null, title);
  c.append(h, ...children);
  return c;
}

function evidenceList(items) {
  const ul = el("ul", "evidence");
  for (const e of items) {
    const li = el("li");
    li.append(el("span", "src", `${e.source_file}, ${e.locator}`));
    li.append(e.kind === "excerpt" ? el("q", "quote", e.excerpt_or_observation) : el("span", "observation", e.excerpt_or_observation));
    ul.append(li);
  }
  return ul;
}

function checksCard(r) {
  const ul = el("ul", "rules");
  for (const rule of r.rules) {
    const li = el("li", "rule");
    li.append(el("span", `pill ${rule.status}`, rule.status));
    const main = el("div");
    const name = el("div", "rule-name");
    name.append(el("span", "rule-id", rule.id), document.createTextNode(rule.name));
    main.append(name, el("p", "rule-reason", rule.reason));
    li.append(main);
    const used = el("div", "used");
    for (const [field, v] of Object.entries(rule.values)) {
      const chip = el("span", "chip");
      chip.append(document.createTextNode(`${LABELS[field]}: `), el("b", null, fmt(field, v.value) ?? STATE_TEXT[v.state]));
      if (v.human_override) chip.append(document.createTextNode(" (human override)"));
      used.append(chip);
    }
    if (rule.evidence.length) {
      const d = el("details");
      d.append(el("summary", "link", `Evidence (${rule.evidence.length})`), evidenceList(rule.evidence));
      used.append(d);
    }
    li.append(used);
    ul.append(li);
  }
  return card("Checks", ul);
}

function fieldsCard(r) {
  const list = el("div");
  for (const [name, f] of Object.entries(r.fields)) list.append(fieldRow(r, name, f));
  return card("Extracted fields and evidence", list);
}

function fieldRow(r, name, f) {
  const row = el("div", "field");
  row.id = `field-${name}`;
  const head = el("div", "field-head");
  head.append(el("span", "field-label", LABELS[name]));
  if (f.override) head.append(el("span", "pill OVERRIDE", "Human override"));
  head.append(el("span", `pill ${f.state}`, STATE_TEXT[f.state]));
  const fix = el("button", "link", f.override ? "Change correction" : "Correct");
  fix.type = "button";
  head.append(fix);
  row.append(head);

  const value = f.override ? f.override.value : f.value;
  const shown = fmt(name, value);
  row.append(el("p", `field-value${shown === null ? " none" : ""}`, shown ?? EMPTY_TEXT[f.state]));

  if (f.override) {
    const box = el("div", "override-box");
    const was = f.override.original_value === null ? STATE_TEXT[f.override.original_state].toLowerCase() : fmt(name, f.override.original_value);
    box.append(el("p", null, `Corrected by a reviewer: ${f.override.reason}. The extracted value was ${was}; its evidence is kept below.`));
    const undo = el("button", "link", "Remove correction");
    undo.type = "button";
    undo.addEventListener("click", () => saveCorrection(r, name, null, undo));
    box.append(undo);
    row.append(box);
  }
  if (f.alternatives.length) {
    const ul = el("ul", "evidence");
    for (const a of f.alternatives) {
      const li = el("li");
      li.append(el("span", "src", `${a.evidence.source_file}, ${a.evidence.locator}`), el("span", "alt-value", `Stated: ${a.raw}`));
      li.append(a.evidence.kind === "excerpt" ? el("q", "quote", a.evidence.excerpt_or_observation) : el("span", "observation", a.evidence.excerpt_or_observation));
      ul.append(li);
    }
    row.append(ul);
  }
  if (f.evidence.length) row.append(evidenceList(f.evidence));
  if (f.note && f.state !== "KNOWN") row.append(el("p", "note", f.note));

  fix.addEventListener("click", () => {
    if (row.querySelector(".correct-form")) return;
    row.append(correctionForm(r, name, f));
    row.querySelector(".correct-form input, .correct-form select").focus();
  });
  return row;
}

function correctionForm(r, name, f) {
  const form = el("form", "correct-form");
  let input;
  if (YES_NO.has(name)) {
    input = el("select");
    for (const [v, t] of [["", "Choose"], ["Yes", "Yes"], ["No", "No"]]) {
      const o = el("option", null, t);
      o.value = v;
      input.append(o);
    }
  } else {
    input = el("input");
    input.type = "text";
    input.placeholder = HINTS[name] || "Corrected value";
  }
  input.setAttribute("aria-label", `Corrected ${LABELS[name]}`);
  if (f.override) input.value = YES_NO.has(name) ? (f.override.value ? "Yes" : "No") : String(f.override.value);
  const reason = el("input");
  reason.type = "text";
  reason.placeholder = "Reason, for example: confirmed with broker by phone";
  reason.setAttribute("aria-label", "Reason for the correction");
  if (f.override) reason.value = f.override.reason;
  const save = el("button", "btn small", "Save and rerun");
  save.type = "submit";
  const cancel = el("button", "link", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => form.remove());
  const err = el("p", "error");
  err.hidden = true;
  form.append(input, reason, save, cancel, err);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!input.value.trim() || !reason.value.trim()) {
      err.textContent = "Enter a value and a reason.";
      err.hidden = false;
      return;
    }
    saveCorrection(r, name, { value: input.value.trim(), reason: reason.value.trim() }, save, err);
  });
  return form;
}

async function saveCorrection(r, name, body, button, err) {
  button.disabled = true;
  button.textContent = "Rerunning checks";
  try {
    const url = `/api/packets/${r.packet_id}/fields/${name}/override`;
    const next = body ? await api("PUT", url, body) : await api("DELETE", url);
    showResult(next, true);
    document.getElementById(`field-${name}`)?.querySelector(".link")?.focus({ preventScroll: true });
  } catch (e) {
    button.disabled = false;
    button.textContent = body ? "Save and rerun" : "Remove correction";
    if (err) { err.textContent = e.message; err.hidden = false; } else alert(e.message);
  }
}

function summaryCard(r) {
  const c = el("section", "card");
  const h = el("h2", null, "Underwriter summary");
  h.append(el("span", "ai", "AI"));
  c.append(h);
  if (r.summary.status === "OK") c.append(el("p", "summary-text", r.summary.detail));
  else c.append(el("p", "muted", r.summary.detail));
  return c;
}

const ASK_SUGGESTIONS = ["What needs my attention in this packet?", "What does the email say about loss runs?", "Where does the total insured value come from?"];

function askCard(r) {
  const c = el("section", "card ask");
  const h = el("h2", null, "Ask about this packet");
  h.append(el("span", "ai", "AI"));
  c.append(h, el("p", "muted small-note", "Answers come only from this packet's files and check results. Every quote is checked against its source."));
  const log = el("div", "chat");
  log.setAttribute("aria-live", "polite");
  for (const turn of chat) log.append(turnView(turn));
  const form = el("form", "ask-form");
  const input = el("input");
  input.type = "text";
  input.maxLength = 500;
  input.placeholder = "Ask a question about the documents or checks";
  input.setAttribute("aria-label", "Question about this packet");
  const go = el("button", "btn small", "Ask");
  go.type = "submit";
  form.append(input, go);
  const chips = el("div", "chips");
  if (!chat.length) {
    for (const q of ASK_SUGGESTIONS) {
      const b = el("button", "chip-btn", q);
      b.type = "button";
      b.addEventListener("click", () => { input.value = q; form.requestSubmit(); });
      chips.append(b);
    }
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (question.length < 3 || go.disabled) return;
    const turn = { question, pending: true };
    chat.push(turn);
    chips.replaceChildren();
    const view = turnView(turn);
    log.append(view);
    log.scrollTop = log.scrollHeight;
    input.value = "";
    go.disabled = true;
    try {
      const history = chat.filter((t) => t.answer).slice(-4).map((t) => ({ question: t.question, answer: t.answer.statements.map((s) => s.text).join(" ") }));
      turn.answer = await api("POST", `/api/packets/${r.packet_id}/ask`, { question, history });
    } catch (err) {
      turn.error = err.message;
    }
    turn.pending = false;
    const done = turnView(turn);
    view.replaceWith(done);
    log.scrollTop = done.offsetTop - log.offsetTop;  // show the start of the newest answer
    go.disabled = false;
    input.focus();
  });
  c.append(log, form, chips);
  return c;
}

function turnView(turn) {
  const box = el("div", "turn");
  box.append(el("p", "q", turn.question));
  if (turn.pending) { box.append(el("p", "muted", "Answering from the packet's sources")); return box; }
  if (turn.error) { box.append(el("p", "error", turn.error)); return box; }
  const ul = el("ul", "answer");
  for (const st of turn.answer.statements) {
    const li = el("li");
    li.append(el("span", st.supported ? null : "unsupported", st.text));
    if (!st.supported) li.append(el("span", "pill FLAG", "No verified source"));
    for (const c of st.citations) {
      const cite = el("div", "cite");
      cite.append(el("span", "src", c.verified ? `${c.source}, ${c.locator}` : `${c.source}: quote not found in this source`));
      cite.append(el("q", "quote", c.quote));
      if (!c.verified) cite.classList.add("bad");
      li.append(cite);
    }
    ul.append(li);
  }
  box.append(ul);
  return box;
}

function draftCard(r) {
  const c = el("section", "card draft");
  c.append(el("h2", null, "Draft follow-up email"));
  const fields = [["To", "to", "input"], ["Subject", "subject", "input"], ["Message", "body", "textarea"]];
  const inputs = {};
  for (const [label, key, tag] of fields) {
    const id = `draft-${key}`;
    const l = el("label", null, label);
    l.htmlFor = id;
    const i = el(tag);
    if (tag === "input") i.type = "text";
    i.id = id;
    i.value = r.email_draft[key];
    inputs[key] = i;
    c.append(l, i);
  }
  const copy = el("button", "btn ghost small", "Copy draft");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    const text = `To: ${inputs.to.value}\nSubject: ${inputs.subject.value}\n\n${inputs.body.value}`;
    try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; }
    catch { copy.textContent = "Copy failed, select the text instead"; }
    setTimeout(() => { copy.textContent = "Copy draft"; }, 1800);
  });
  const row = el("div", "row-end");
  row.append(copy);
  c.append(row, el("p", "draft-note", "Draft only. This app never sends email. Edits here are not saved, and the draft is rebuilt when checks rerun."));
  return c;
}

function attachmentsCard(r) {
  const ul = el("ul", "attachments");
  const types = { application: "Application", loss_runs: "Loss runs report", other: "Other document" };
  for (const a of r.attachments) {
    const li = el("li");
    li.append(el("span", "fname", a.file), el("span", "kind", a.kind.toUpperCase()));
    if (!a.readable) li.append(el("span", "pill FLAG", "Unreadable"));
    if (a.kind === "pdf" && a.readable) li.append(el("span", "chip", a.doc_type ? types[a.doc_type] : "Type not determined"));
    if (a.pages) li.append(el("span", "chip", `${a.pages} page${a.pages === 1 ? "" : "s"}`));
    if (a.error) li.append(el("span", "note", a.error));
    ul.append(li);
  }
  return card("Attachment inventory", ul);
}

// ---------- Wiring ----------

async function init() {
  const pill = $("model");
  try {
    const h = await api("GET", "/api/health");
    pill.hidden = !!h.model;
    if (h.model) $("model-foot").textContent = `Extraction and summary model: ${modelName(h.model)}.`;
    else {
      pill.textContent = h.model_error ? "Model setup error: extraction will be skipped" : "No model configured: extraction will be skipped";
      pill.title = h.model_error || "Set UNDERWISE_LLM_PROVIDER, see .env.example";
      pill.classList.add("warn");
    }
  } catch {
    pill.textContent = "Server unreachable";
    pill.classList.add("warn");
  }
  $("files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  $("email-text").addEventListener("input", updateProcessButton);
  $("upload").addEventListener("submit", submit);
  const drop = $("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles(e.dataTransfer.files); });
}

init();
