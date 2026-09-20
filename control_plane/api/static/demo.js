/* Foreman 콘솔 (P9.2, D-52; UI 리뷰 2026-09-18). 빌드 없음, 같은 origin의 API만 호출한다.
 * 상태: project · goals 목록 · 선택 goal 상세(단계, plan, tasks) · WS 이벤트 로그(Goal별 필터).
 * WS 이벤트가 오면 500ms 디바운스로 goal/tasks를 다시 읽는다(읽기는 projection 반영 후). */
(() => {
  const $ = (id) => document.getElementById(id);
  // 등급 A(boundary.md): 공유 파일 없이 모듈당 Task 하나, 3개 이하 — 지금 구조가 안정적으로 완주하는 형태.
  // 사용자 지시 2026-09-20(P9.14): 2개만, 둘 다 README.md 작성까지 (문서 Task는 P9.13 매핑으로 코드 Task가 된다)
  const EXAMPLES = [
    "Add a slugify(text) utility module that lowercases and hyphenates, with tests, and document it in README.md",
    "Add a maths helpers module with add and mul functions and tests, and add a usage section to README.md",
  ];
  // 사용자 지시 2026-09-20(P9.14): 콘솔 선택지에서만 뺀다 — /llm 응답과 PROFILES(D-57)는 그대로
  const HIDDEN_LLM = ["anthropic"];
  const STATUS_LABEL = {
    draft: "준비 중", planning: "Plan 작성 중", awaiting_plan_approval: "승인 대기", active: "진행 중",
    done: "완료", cancelled: "취소됨", blocked: "막힘", failed: "실패", pending: "대기", ready: "배정 대기",
    assigned: "배정됨", running: "실행 중", in_review: "머지 대기",
  };
  const STEPS = ["생성", "Plan 작성", "Plan 승인", "Task 실행", "PR 머지", "완료"];
  const ENDED = ["done", "cancelled"];
  const state = {
    demo: { user_id: "judge" }, projects: [], project: null, goals: [], goalId: null, goal: null, tasks: [],
    taskLabel: new Map(), events: [], openEvents: new Set(), lastSeq: 0, ws: null, wsDownSince: null, timer: null, evFrame: 0,
  };

  // ---- helpers ---------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function safeGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
  function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 무시 */ } }
  let toastTimer = null;
  // 정보 알림은 6초 뒤 닫히고, 오류는 사용자가 닫을 때까지 남는다
  function toast(msg, bad = false) {
    const t = $("toast"); $("toast-text").textContent = msg; t.classList.toggle("bad", bad); t.hidden = false;
    clearTimeout(toastTimer); if (!bad) toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }
  $("toast-close").onclick = () => { $("toast").hidden = true; };
  // 조치가 필요한 오류는 해당 폼 아래에 남긴다 (box = 요소 id). 토스트로도 알린다
  function showError(box, e) {
    const el = $(box); el.textContent = e.message; el.hidden = false; toast(e.message, true);
  }
  const clearError = (box) => { $(box).hidden = true; };
  // 진행 중: 버튼을 잠그고 라벨을 바꿔 두 번 누르지 못하게 한다
  async function busy(btn, label, fn) {
    if (btn.disabled) return; const old = btn.textContent;
    btn.disabled = true; btn.textContent = label;
    try { return await fn(); } finally { btn.disabled = false; btn.textContent = old; }
  }
  async function api(path, opts = {}) {
    const headers = { "X-User-Id": state.demo.user_id, ...(opts.headers || {}) };
    if (opts.body) headers["Content-Type"] = "application/json";
    const r = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* 본문 없음 */ }
    if (!r.ok) {
      const d = data && data.detail;
      const detail = !d ? r.statusText : typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : JSON.stringify(d);
      const err = new Error(`요청이 거절되었습니다 (${r.status}) — ${detail}`); err.status = r.status; throw err;
    }
    return data;
  }
  // 아주 작은 markdown → HTML (제목/목록/코드/강조만). 입력은 전부 escape 한다.
  function md(src) {
    const lines = String(src || "").split("\n"); const out = []; let inList = false, inCode = false;
    const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    for (const raw of lines) {
      const line = raw.replace(/\r$/, "");
      if (line.startsWith("```")) { if (inList) { out.push("</ul>"); inList = false; } inCode = !inCode; out.push(inCode ? "<pre><code>" : "</code></pre>"); continue; }
      if (inCode) { out.push(esc(line) + "\n"); continue; }
      const h = /^(#{1,3})\s+(.*)$/.exec(line);
      const li = /^\s*[-*]\s+(.*)$/.exec(line);
      if (li) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
      if (inList) { out.push("</ul>"); inList = false; }
      if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
      if (line.trim() === "") continue;
      out.push(`<p>${inline(line)}</p>`);
    }
    if (inList) out.push("</ul>"); if (inCode) out.push("</code></pre>");
    return out.join("");
  }
  const badge = (s) => `<span class="badge ${esc(s)}" title="${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>`;
  const link = (url, text) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)} ↗</a>` : '<span class="muted">—</span>';
  const isShowcase = (g) => /^\[showcase\]/i.test(g.title || "") && g.status !== "cancelled"; // 취소된 showcase는 고정 안 함
  const ts = (iso) => { try { return new Date(iso).toLocaleTimeString("ko-KR", { hour12: false }); } catch (_) { return iso; } };
  function ago(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (!isFinite(s)) return ""; if (s < 60) return "방금";
    if (s < 3600) return `${Math.floor(s / 60)}분 전`; if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
    return `${Math.floor(s / 86400)}일 전`;
  }
  function setUrl(params) {
    const u = new URL(location.href);
    for (const [k, v] of Object.entries(params)) { if (v) u.searchParams.set(k, v); else u.searchParams.delete(k); }
    history.replaceState(null, "", u.toString());
  }

  // ---- load ------------------------------------------------------------
  async function loadDemo() {
    try { state.demo = await api("/demo"); } catch (_) { /* 데모 모드 아님 */ }
    const demo = !!state.demo.demo_mode;
    $("connect-token-label").hidden = !demo; $("demo-tag").hidden = !demo;
    if (state.demo.install_url) $("install-app").href = state.demo.install_url; // P9.11 공개 App
    // 사용자 지시 2026-09-20(P9.16): 데모 모드가 아니면 계정 안내를 띄우지 않는다
    $("demo-note").textContent = demo
      ? `데모 모드: 프로젝트당 동시에 진행되는 Goal ${state.demo.max_running_goals}개, 시간당 ${state.demo.goals_per_hour}개까지. 승인자 계정 "${state.demo.user_id}"로 동작합니다.`
      : "";
  }
  // D-57: LLM 프로파일 목록 (ollama/openai). 키 없는 것은 비활성
  async function loadLlm() {
    try {
      const info = await api("/llm");
      const sel = $("llm"); sel.innerHTML = "";
      for (const p of info.profiles.filter((x) => !HIDDEN_LLM.includes(x.name))) {
        const o = document.createElement("option");
        o.value = p.name; o.disabled = !p.available;
        o.textContent = `${p.name} — ${p.model}${p.available ? "" : " (키 없음)"}`;
        o.selected = p.name === info.default; sel.appendChild(o);
      }
      const remembered = safeGet("foreman.llm");
      if (remembered && [...sel.options].some((o) => o.value === remembered && !o.disabled)) sel.value = remembered;
      sel.onchange = () => safeSet("foreman.llm", sel.value);
    } catch (e) { console.warn(e); }
  }
  // 프로젝트 선택: ?project=<id> > 마지막 선택(localStorage) > 첫 항목. 둘 이상이면 <select> 표시
  async function loadProjects() {
    state.projects = (await api("/projects")).items;
    const has = state.projects.length > 0;
    $("settings").hidden = !has; $("goal-submit").disabled = !has;
    if (!has) { $("project-name").textContent = "— 프로젝트가 없습니다"; $("connect").open = true; return; }
    const wanted = new URLSearchParams(location.search).get("project") || safeGet("foreman.project");
    state.project = state.projects.find((p) => p.id === wanted) || state.projects[0];
    const sel = $("project"); sel.innerHTML = "";
    for (const p of state.projects) {
      const o = document.createElement("option"); o.value = p.id; o.textContent = `${p.name} — ${p.repo}`; sel.appendChild(o);
    }
    $("project-label").hidden = state.projects.length < 2;
    sel.onchange = () => switchProject(sel.value);
    renderProject();
  }
  function renderProject() {
    const p = state.project;
    $("project").value = p.id;
    $("project-name").textContent = `· ${p.name} (${p.repo})`;
    const rl = $("repo-link");
    if (p.repo_url) { rl.href = p.repo_url; rl.hidden = false; } else rl.hidden = true;
  }
  // 프로젝트 전환은 새로고침 없이 상태만 다시 읽는다
  async function switchProject(id, goalId = null) {
    const p = state.projects.find((x) => x.id === id); if (!p) return;
    state.project = p; safeSet("foreman.project", p.id); setUrl({ project: p.id, goal: goalId });
    Object.assign(state, { goals: [], goalId: null, goal: null, tasks: [], events: [], lastSeq: 0 });
    state.taskLabel.clear(); state.openEvents.clear();
    const old = state.ws; state.ws = null; if (old) old.close();
    renderProject(); $("detail").hidden = true; renderEvents();
    try {
      await loadGoals(); await loadEvents();
      const first = state.goals.find((g) => g.id === goalId) || visibleGoals()[0];
      if (first) await selectGoal(first.id);
    } catch (e) { toast(e.message, true); }
    connectWs();
  }
  async function loadGoals() {
    if (!state.project) return;
    const { items } = await api(`/projects/${state.project.id}/goals`);
    state.goals = [...items.filter(isShowcase), ...items.filter((g) => !isShowcase(g))];
    renderGoals();
  }
  async function loadGoal() {
    if (!state.goalId || !state.project) return;
    const pid = state.project.id, gid = state.goalId;
    const [goal, tasks] = await Promise.all([api(`/projects/${pid}/goals/${gid}`), api(`/projects/${pid}/tasks`)]);
    if (gid !== state.goalId || pid !== state.project.id) return; // 그 사이 다른 Goal을 골랐다
    for (const t of tasks.items) state.taskLabel.set(t.id, `${t.issue_number ? `#${t.issue_number} ` : ""}${t.title}`);
    const row = state.goals.find((g) => g.id === goal.id); // 상세 응답에 없는 값은 목록에서 (created_at, llm)
    state.goal = { created_at: row && row.created_at, llm: row && row.llm, ...goal };
    state.tasks = tasks.items.filter((t) => t.goal_id === goal.id);
    renderGoal(); scheduleEvents();
  }
  async function loadEvents() {
    if (!state.project) return;
    for (let i = 0; i < 10; i++) { // 최대 10페이지(5000건)까지 따라잡는다
      const page = await api(`/projects/${state.project.id}/events?since=${state.lastSeq}&limit=500`);
      for (const e of page.items) pushEvent(e);
      if (page.items.length < 500) break;
    }
  }
  const refreshSoon = () => { clearTimeout(state.timer); state.timer = setTimeout(async () => { try { await loadGoals(); await loadGoal(); } catch (e) { console.warn(e); } }, 500); };

  // ---- render ----------------------------------------------------------
  const hideEnded = () => $("goal-filter").checked;
  const visibleGoals = () => state.goals.filter((g) => !hideEnded() || !ENDED.includes(g.status) || g.id === state.goalId);
  function renderGoals() {
    const ul = $("goals"); ul.innerHTML = "";
    const waiting = state.goals.filter((g) => g.status === "awaiting_plan_approval").length;
    document.title = `${waiting ? `(승인 대기 ${waiting}) ` : ""}Foreman 콘솔`; // 다른 탭에 있어도 차례가 보인다
    const goals = visibleGoals();
    if (!goals.length) {
      ul.innerHTML = `<li class="muted small">${state.goals.length ? "진행 중인 Goal이 없습니다 (숨긴 Goal " + state.goals.length + "개)." : "아직 Goal이 없습니다 — 위에서 하나 만들어 보세요."}</li>`;
      return;
    }
    for (const g of goals) {
      const li = document.createElement("li");
      li.className = (g.id === state.goalId ? "active " : "") + (isShowcase(g) ? "showcase" : "");
      const btn = document.createElement("button"); btn.type = "button"; btn.className = "goal";
      if (g.id === state.goalId) btn.setAttribute("aria-current", "true");
      btn.innerHTML = `<span class="t"><b title="${esc(g.title)}">${esc(g.title)}</b></span>${g.total ? `<span class="muted small">${g.done}/${g.total}</span>` : ""}${badge(g.status)}`;
      btn.onclick = () => selectGoal(g.id, true);
      li.appendChild(btn); ul.appendChild(li);
    }
  }
  // P9.12: 콘솔 사용자가 이 프로젝트의 owner/approver인가. 아니면(외부 repo) 승인은 GitHub 댓글로
  const canApprove = () => ((state.project && state.project.approvers) || []).includes(state.demo.user_id);
  // 지금 어느 단계이고 누구 차례인가 (HITL의 핵심 메시지)
  function stage(goal, tasks) {
    const total = tasks.length, done = tasks.filter((t) => t.status === "done").length;
    const working = tasks.some((t) => ["ready", "assigned", "running", "pending"].includes(t.status));
    const review = tasks.filter((t) => t.status === "in_review");
    const stuck = tasks.filter((t) => ["blocked", "failed"].includes(t.status));
    const llm = goal.llm ? ` (${goal.llm})` : "";
    switch (goal.status) {
      case "draft": case "planning":
        return { at: 1, turn: `AI가 Plan을 작성하는 중입니다${llm} — ${ago(goal.created_at) === "방금" ? "방금 시작" : ago(goal.created_at).replace(" 전", " 경과")}` };
      case "awaiting_plan_approval": return { at: 2, turn: canApprove() ? "당신 차례입니다 — Plan을 읽고 승인하세요." : "당신 차례입니다 — GitHub의 Plan에 /approve 댓글을 다세요." };
      case "done": return { at: 6, turn: "완료된 Goal입니다." };
      case "cancelled": case "blocked":
        return { at: goal.plan_markdown ? (total ? 3 : 2) : 1, stopped: true, turn: goal.status === "cancelled" ? "취소된 Goal입니다." : "막힌 Goal입니다 — 이벤트 로그의 사유를 확인하세요." };
      default:
        if (total && done === total) return { at: 6, turn: "모든 Task의 PR이 머지되었습니다." };
        if (review.length && !working) return { at: 4, turn: `당신 차례입니다 — GitHub에서 PR ${review.length}개를 머지하세요.` };
        if (stuck.length && !working && !review.length) return { at: 3, stopped: true, turn: `Task ${stuck.length}개가 막혔습니다 — 이벤트 로그의 실패 사유를 확인하세요.` };
        return { at: 3, turn: review.length ? `AI 워커가 실행 중입니다. 머지를 기다리는 PR이 ${review.length}개 있습니다.` : "AI 워커가 Task를 실행 중입니다." };
    }
  }
  function renderGoal() {
    const goal = state.goal, tasks = state.tasks; if (!goal) return;
    $("detail").hidden = false;
    $("goal-title-view").textContent = goal.title;
    $("goal-status").outerHTML = badge(goal.status).replace("<span", '<span id="goal-status"');
    const st = stage(goal, tasks);
    $("steps").innerHTML = STEPS.map((name, i) => {
      const now = st.at < 6 && i === st.at;
      const cls = now ? (st.stopped ? "stopped" : "now") : i < st.at ? "past" : "";
      return `<li class="${cls}"${now ? ' aria-current="step"' : ""}>${i + 1}. ${name}</li>`;
    }).join("");
    $("turn").textContent = st.turn;
    const awaiting = goal.status === "awaiting_plan_approval";
    const approver = canApprove();
    $("approve-box").hidden = !(awaiting && approver); if (!(awaiting && approver)) $("reject-box").hidden = true;
    const ga = $("github-approve"); ga.hidden = !(awaiting && !approver);
    ga.innerHTML = awaiting && !approver ? `<b>GitHub에서 승인</b>${goal.plan_discussion_url ? `<a href="${esc(goal.plan_discussion_url)}" target="_blank" rel="noopener">Plan ↗</a>` : ""}<span class="small muted">${esc(((state.project && state.project.approvers) || []).join(", ") || "repo owner")} 계정으로 <code>/approve</code> 댓글 (반려: <code>/reject 사유</code>). 승인되면 이 화면이 자동으로 이어집니다.</span>` : "";
    $("cancel-goal").hidden = ENDED.includes(goal.status);
    const review = tasks.filter((t) => t.status === "in_review" && t.pr_url);
    const mb = $("merge-box"); mb.hidden = !review.length;
    mb.innerHTML = review.length ? `<b>GitHub에서 머지 대기</b>${review.map((t) => `<a href="${esc(t.pr_url)}" target="_blank" rel="noopener">PR #${esc(t.pr_number)} ↗</a>`).join("")}<span class="small muted">머지하면 Task가 완료되고 다음 Task가 배정됩니다.</span>` : "";
    const dl = $("discussion-link");
    if (goal.plan_discussion_url) {
      dl.href = goal.plan_discussion_url; dl.hidden = false;
      dl.textContent = goal.plan_discussion_url.includes("/issues/") ? "Issue ↗" : "Discussion ↗"; // P9.11
    } else dl.hidden = true;
    const plan = $("plan");
    if (goal.plan_markdown) { plan.className = "plan"; plan.innerHTML = md(goal.plan_markdown); }
    else { plan.className = "plan muted"; plan.textContent = ["draft", "planning"].includes(goal.status) ? "Plan을 만드는 중…" : "Plan 본문이 없습니다."; }
    // Plan은 읽어야 할 때(승인 대기)만 펼친다. 상태가 바뀔 때만 건드려 사용자의 접기/펼치기를 존중
    const planKey = `${goal.id}:${goal.status}`;
    if (planKey !== state.planKey) { state.planKey = planKey; $("plan-box").open = awaiting; }
    $("tasks-box").hidden = !tasks.length;
    $("task-count").textContent = tasks.length ? `${tasks.filter((t) => t.status === "done").length}/${tasks.length} 완료` : "";
    const tb = $("tasks").querySelector("tbody");
    const open = new Set([...tb.querySelectorAll("tr.spec-row.open")].map((r) => r.dataset.id)); // 다시 그려도 펼침 유지
    tb.innerHTML = "";
    tasks.sort((a, b) => (a.issue_number || 0) - (b.issue_number || 0));
    for (const t of tasks) {
      const tr = document.createElement("tr"); tr.className = "task-row";
      // 4열만: # (Issue 링크) · Task · 상태 · PR. 소유 파일·브랜치·시도 횟수는 Issue/PR에서 본다
      tr.innerHTML = `<td>${t.issue_url ? link(t.issue_url, `#${t.issue_number}`) : esc(t.issue_number ?? "")}</td><td><button type="button" class="task-toggle" aria-expanded="${open.has(t.id)}">${esc(t.title)}</button></td><td>${badge(t.status)}</td><td>${link(t.pr_url, `PR #${t.pr_number ?? ""}`)}</td>`;
      const sr = document.createElement("tr"); sr.className = `spec-row${open.has(t.id) ? " open" : ""}`; sr.dataset.id = t.id;
      sr.innerHTML = `<td></td><td class="spec" colspan="3">${esc(t.spec || "(spec 없음)")}</td>`;
      const btn = tr.querySelector(".task-toggle");
      btn.onclick = () => { btn.setAttribute("aria-expanded", String(sr.classList.toggle("open"))); };
      tb.appendChild(tr); tb.appendChild(sr);
    }
  }
  function pushEvent(e) {
    if (e.seq != null && e.seq <= state.lastSeq) return; // replay/live 중복
    if (e.seq != null) state.lastSeq = Math.max(state.lastSeq, e.seq);
    if (e.type === "run.tool_called") return; // 툴 호출은 너무 많다 (체인 밖, D-31)
    state.events.push(e); if (state.events.length > 2000) state.events.splice(0, state.events.length - 2000);
    scheduleEvents(); refreshSoon();
  }
  function scheduleEvents() { if (!state.evFrame) state.evFrame = requestAnimationFrame(() => { state.evFrame = 0; renderEvents(); }); }
  // 선택한 Goal의 이벤트만 (correlation_id = goal id). P9.17: "프로젝트 전체 보기"는 제거했다
  function renderEvents() {
    if (!$("events-box").open) return; // 접혀 있으면 그리지 않는다 (펼칠 때 그린다)
    const gid = state.goalId;
    const mine = (e) => !gid || e.correlation_id === gid || (e.payload && e.payload.goal_id === gid) || e.subject.id === gid;
    const rows = state.events.filter(mine).slice(-200).reverse();
    const ul = $("events"); ul.innerHTML = "";
    if (!rows.length) { ul.innerHTML = '<li class="muted">아직 이벤트가 없습니다.</li>'; return; }
    for (const e of rows) {
      const p = e.payload || {}, id = String(e.subject.id), key = e.id || `${e.seq}`;
      const who = e.subject.entity === "task" && state.taskLabel.has(id) ? state.taskLabel.get(id) : `${e.subject.entity}:${id.slice(-6)}`;
      const li = document.createElement("li");
      const head = `<span class="ts">${ts(e.ts)}</span><code>${esc(e.type)}</code> <span class="muted" title="${esc(id)}">${esc(who)}</span> `;
      if (e.type === "task.failed") { // 사유 한 줄 + 펼치면 테스트 출력 전체 (P9 버그 #5)
        li.className = "fail";
        const why = `${p.reason || ""}${p.attempt != null ? ` (run ${p.attempt}${p.edit_rounds ? `, ${p.edit_rounds} edits` : ""})` : ""}`;
        li.innerHTML = head + esc(why) + (p.test_output ? `<details${state.openEvents.has(key) ? " open" : ""}><summary>테스트 출력 보기</summary><pre>${esc(String(p.test_output).trim())}</pre></details>` : "");
        const d = li.querySelector("details");
        if (d) d.ontoggle = () => { if (d.open) state.openEvents.add(key); else state.openEvents.delete(key); };
      } else {
        li.innerHTML = head + esc(String(p.reason || p.branch || p.pr_number || p.title || p.summary || "").slice(0, 300));
      }
      ul.appendChild(li);
    }
  }

  // ---- ws --------------------------------------------------------------
  function wsState(kind, text) { $("ws-dot").className = `dot ${kind}`; $("ws-text").textContent = text; }
  function connectWs() {
    if (!state.project) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/projects/${state.project.id}/stream?since=${state.lastSeq}`);
    state.ws = ws;
    ws.onopen = () => { state.wsDownSince = null; $("ws-banner").hidden = true; wsState("on", "실시간"); };
    ws.onmessage = (m) => { if (state.ws !== ws) return; try { pushEvent(JSON.parse(m.data)); } catch (e) { console.warn(e); } };
    ws.onclose = () => {
      if (state.ws !== ws) return; // 프로젝트를 바꿔 닫은 옛 연결
      state.wsDownSince = state.wsDownSince || Date.now(); wsState("off", "재연결 중…"); setTimeout(() => { if (state.ws === ws) connectWs(); }, 3000);
    };
    ws.onerror = () => ws.close();
  }
  setInterval(() => {
    if (state.wsDownSince && Date.now() - state.wsDownSince > 30000) $("ws-banner").hidden = false; // 30초 넘게 끊기면 경고
    if (state.goal && ["draft", "planning"].includes(state.goal.status)) renderGoal(); // 경과 시간 갱신
  }, 10000);

  // ---- actions ---------------------------------------------------------
  // 선택한 Goal이 아직 projection에 없으면 1초 간격으로 최대 15번 다시 읽는다 (그 사이 다른 Goal을 고르면 멈춤)
  function retryGoalSoon(id, left = 15) {
    setTimeout(async () => {
      if (state.goalId !== id || state.goal) return;
      try { await loadGoals(); await loadGoal(); } catch (e) {
        if (e.status === 404 && left > 1) retryGoalSoon(id, left - 1);
        else toast(e.message, true);
      }
    }, 1000);
  }
  async function selectGoal(id, byUser = false) {
    state.goalId = id; state.goal = null; setUrl({ goal: id }); clearError("detail-error"); $("reject-box").hidden = true;
    renderGoals(); renderEvents();
    try { await loadGoal(); } catch (e) {
      // 생성 직후에는 projection이 아직 반영되지 않아 404가 난다 → 오류가 아니라 "준비 중". 알림 없이 재시도
      if (e.status === 404) retryGoalSoon(id); else toast(e.message, true);
    }
    // 1열 레이아웃(모바일)에서는 상세가 화면 아래에 생긴다 → 선택하면 그리로 이동
    if (byUser && window.matchMedia("(max-width: 900px)").matches) $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const goalUrl = (action) => `/projects/${state.project.id}/goals/${state.goalId}/${action}`;
  $("goal-form").onsubmit = (ev) => {
    ev.preventDefault();
    if (!state.project) return;
    const title = $("goal-title").value.trim(); if (!title) return;
    clearError("goal-error");
    busy($("goal-submit"), "생성 중… (LLM 확인)", async () => {
      try {
        const g = await api(`/projects/${state.project.id}/goals`, { method: "POST", body: { title, llm: $("llm").value || undefined } });
        $("goal-title").value = "";
        toast("Goal을 만들었습니다. Plan이 올라오면 승인 버튼이 보입니다.");
        await loadGoals(); await selectGoal(g.id, true);
      } catch (e) { showError("goal-error", e); }
    });
  };
  $("approve").onclick = () => busy($("approve"), "승인 중…", async () => {
    clearError("detail-error");
    try { await api(goalUrl("approve"), { method: "POST" }); toast("승인했습니다. Task → Issue → 워커 순으로 진행됩니다."); refreshSoon(); }
    catch (e) { showError("detail-error", e); }
  });
  // Reject는 사유 입력을 펼친 뒤 한 번 더 확인한다 (반려 = Goal 취소)
  $("reject").onclick = () => { $("reject-box").hidden = false; $("reject-reason").focus(); };
  $("reject-cancel").onclick = () => { $("reject-box").hidden = true; };
  $("reject-confirm").onclick = () => busy($("reject-confirm"), "반려 중…", async () => {
    clearError("detail-error");
    try {
      await api(goalUrl("reject"), { method: "POST", body: { reason: $("reject-reason").value } });
      $("reject-reason").value = ""; $("reject-box").hidden = true; toast("반려했습니다. Goal이 취소됩니다."); refreshSoon();
    } catch (e) { showError("detail-error", e); }
  });
  $("cancel-goal").onclick = () => {
    if (!state.goalId || !confirm("이 Goal과 남은 Task를 취소할까요? (GitHub Issue/PR은 그대로 남습니다)")) return;
    busy($("cancel-goal"), "취소 중…", async () => {
      clearError("detail-error");
      try { await api(goalUrl("cancel"), { method: "POST", body: { reason: "cancelled from console" } }); toast("취소했습니다."); refreshSoon(); }
      catch (e) { showError("detail-error", e); }
    });
  };
  // DELETE는 outbox까지다(D-46) — 바로 다시 읽으면 삭제한 프로젝트가 그대로 보인다.
  // 목록에서 빠질 때까지 기다렸다 화면을 다시 그린다 (최대 약 4.5초)
  async function untilDeleted(id, tries = 15) {
    for (let i = 0; i < tries; i++) {
      try {
        if (!(await api("/projects")).items.some((p) => p.id === id)) return;
      } catch (_) { return; } // 읽기 실패는 여기서 따지지 않는다 — 어차피 다시 그린다
      await new Promise((r) => setTimeout(r, 300));
    }
  }
  $("delete-project").onclick = () => {
    if (!state.project) return;
    const p = state.project;
    if (!confirm(`프로젝트 "${p.name}" (${p.repo})를 삭제할까요?\n남은 Goal·Task는 취소되고 목록에서 사라집니다. GitHub의 Issue/PR/Discussion과 이벤트 기록은 남습니다.`)) return;
    const headers = {};
    if (state.demo.demo_mode) { // 데모 모드에서는 삭제할 때 관리 토큰을 직접 묻는다
      const tok = $("connect-token").value.trim() || (prompt("관리 토큰을 입력하세요") || "").trim();
      if (!tok) return; headers["X-Admin-Token"] = tok;
    }
    busy($("delete-project"), "삭제 중…", async () => {
      try {
        await api(`/projects/${p.id}`, { method: "DELETE", headers });
        safeSet("foreman.project", ""); setUrl({ project: null, goal: null });
        await untilDeleted(p.id);
        location.reload();
      } catch (e) { toast(e.message, true); }
    });
  };
  $("goal-filter").onchange = () => { safeSet("foreman.hideEnded", $("goal-filter").checked ? "1" : ""); renderGoals(); };
  $("events-box").ontoggle = renderEvents;

  // ---- GitHub repo 연결 (POST /projects) + 사전 점검 (GET /projects/check) ------------------
  function renderCheck(body) {
    const ul = $("connect-check"); ul.innerHTML = "";
    for (const it of body.items) {
      const li = document.createElement("li");
      li.className = it.ok ? "ok" : it.required === false ? "warn" : "bad"; // 경고는 연결을 막지 않는다
      li.textContent = `${it.name}: ${it.detail}`; ul.appendChild(li);
    }
    if (body.install_url && body.installation_id == null) { // 미설치 → 설치 링크 (P9.11)
      const li = document.createElement("li"); li.className = "bad";
      const a = document.createElement("a"); a.href = body.install_url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = "이 repo에 GitHub App 설치 ↗"; li.appendChild(a); ul.appendChild(li);
    }
    if (body.canonical) $("connect-repo").value = body.canonical; // GitHub의 정식 대소문자로 연결
    return body.ok;
  }
  $("connect-check-btn").onclick = () => {
    const repo = $("connect-repo").value.trim(); if (!repo) return;
    clearError("connect-error");
    busy($("connect-check-btn"), "점검 중…", async () => {
      try { const ok = renderCheck(await api(`/projects/check?repo=${encodeURIComponent(repo)}`)); toast(ok ? "점검 통과 — 연결할 수 있습니다." : "점검 실패 항목이 있습니다.", !ok); }
      catch (e) { showError("connect-error", e); }
    });
  };
  $("connect-form").onsubmit = (ev) => {
    ev.preventDefault();
    const repo = $("connect-repo").value.trim(); if (!repo) return;
    const owner = repo.includes("/") ? repo.split("/")[0] : state.demo.user_id;
    const members = [{ user_id: owner, role: "owner" }];
    if (owner !== state.demo.user_id) members.push({ user_id: state.demo.user_id, role: "approver" });
    const headers = {}; const tok = $("connect-token").value.trim(); if (tok) headers["X-Admin-Token"] = tok;
    clearError("connect-error");
    busy($("connect-submit"), "연결 중…", async () => {
      try {
        const p = await api("/projects", { method: "POST", headers, body: {
          name: $("connect-name").value.trim() || repo.split("/").pop(),
          repo, default_branch: $("connect-branch").value.trim() || "main", members } });
        safeSet("foreman.project", p.id); setUrl({ project: p.id, goal: null }); location.reload();
      } catch (e) { showError("connect-error", e); }
    });
  };

  for (const ex of EXAMPLES) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = ex;
    b.onclick = () => { $("goal-title").value = ex; $("goal-title").focus(); };
    $("examples").appendChild(b);
  }

  // ---- boot ------------------------------------------------------------
  (async () => {
    $("goal-filter").checked = safeGet("foreman.hideEnded") === "1";
    $("howto").open = !safeGet("foreman.howto"); safeSet("foreman.howto", "seen"); // 첫 방문에만 펼친다
    try {
      await loadDemo(); await loadLlm(); await loadProjects();
      if (!state.project) return;
      await loadGoals(); await loadEvents();
      const wanted = new URLSearchParams(location.search).get("goal");
      const first = state.goals.find((g) => g.id === wanted) || visibleGoals()[0];
      if (first) await selectGoal(first.id);
      connectWs();
    } catch (e) { toast(`초기화 실패: ${e.message}`, true); }
  })();
})();
