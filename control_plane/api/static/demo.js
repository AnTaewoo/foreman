/* Foreman 데모 콘솔 (P9.2, D-52). 빌드 없음, 같은 origin의 API만 호출한다.
 * 상태: project(첫 프로젝트) · goals 목록 · 선택 goal 상세(plan, tasks) · WS 이벤트 로그.
 * WS 이벤트가 오면 500ms 디바운스로 goal/tasks를 다시 읽는다(읽기는 projection 반영 후). */
(() => {
  const $ = (id) => document.getElementById(id);
  // 등급 A(boundary.md): 공유 파일 없이 모듈당 Task 하나, 3개 이하 — 지금 구조가 안정적으로 완주하는 형태
  const EXAMPLES = [
    "Add a maths helpers module with add and mul functions and tests",
    "Add a slugify(text) utility module that lowercases and hyphenates, with tests",
    "Add a greet(name) helper module returning 'Hello, <name>!' with tests",
  ];
  const state = { demo: { user_id: "judge" }, project: null, goals: [], goalId: null, lastSeq: 0, ws: null, timer: null };

  // ---- helpers ---------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let toastTimer = null;
  function toast(msg, ms = 6000) {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
  }
  async function api(path, opts = {}) {
    const headers = { "X-User-Id": state.demo.user_id, ...(opts.headers || {}) };
    if (opts.body) headers["Content-Type"] = "application/json";
    const r = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* 본문 없음 */ }
    if (!r.ok) {
      const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText;
      throw new Error(`${r.status}: ${detail}`);
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
  const badge = (s) => `<span class="badge ${esc(s)}">${esc(s)}</span>`;
  const link = (url, text) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)} ↗</a>` : '<span class="muted">—</span>';
  const isShowcase = (g) => /^\[showcase\]/i.test(g.title || "") && g.status !== "cancelled"; // 취소된 showcase는 고정 안 함
  const ts = (iso) => { try { return new Date(iso).toLocaleTimeString("ko-KR", { hour12: false }); } catch (_) { return iso; } };

  // ---- load ------------------------------------------------------------
  async function loadDemo() {
    try { state.demo = await api("/demo"); } catch (_) { /* 데모 모드 아님 */ }
    $("connect-token").hidden = !state.demo.demo_mode;
    const n = $("demo-note");
    n.textContent = state.demo.demo_mode
      ? `데모 모드: 프로젝트당 동시에 진행되는 Goal ${state.demo.max_running_goals}개, 시간당 ${state.demo.goals_per_hour}개까지. 승인자 계정 "${state.demo.user_id}"로 동작합니다.`
      : `승인자 계정 "${state.demo.user_id}"로 동작합니다.`;
  }
  // 프로젝트 선택: ?project=<id> > 마지막 선택(localStorage) > 첫 항목. 둘 이상이면 <select> 표시
  async function loadProject() {
    const { items } = await api("/projects");
    if (!items.length) {
      $("project-name").textContent = "— 프로젝트가 없습니다. 아래 \"GitHub repo 연결\"로 만드세요";
      $("goal-submit").disabled = true;
      $("delete-project").hidden = true;
      return;
    }
    $("delete-project").hidden = false;
    const wanted = new URLSearchParams(location.search).get("project") || safeGet("foreman.project");
    state.project = items.find((p) => p.id === wanted) || items[0];
    $("project-name").textContent = `· ${state.project.name} (${state.project.repo})`;
    const rl = $("repo-link");
    if (state.project.repo_url) { rl.href = state.project.repo_url; rl.hidden = false; } else rl.hidden = true;
    const sel = $("project"); sel.innerHTML = "";
    for (const p of items) {
      const o = document.createElement("option"); o.value = p.id; o.textContent = `${p.name} — ${p.repo}`;
      o.selected = p.id === state.project.id; sel.appendChild(o);
    }
    $("project-label").hidden = items.length < 2;
    sel.onchange = () => {
      safeSet("foreman.project", sel.value);
      const u = new URL(location.href); u.searchParams.set("project", sel.value); location.href = u.toString();
    };
  }
  function safeGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
  function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 무시 */ } }
  async function loadGoals() {
    if (!state.project) return;
    const { items } = await api(`/projects/${state.project.id}/goals`);
    state.goals = [...items.filter(isShowcase), ...items.filter((g) => !isShowcase(g))];
    renderGoals();
  }
  async function loadGoal() {
    if (!state.goalId) return;
    const pid = state.project.id;
    const [goal, tasks] = await Promise.all([
      api(`/projects/${pid}/goals/${state.goalId}`),
      api(`/projects/${pid}/tasks`),
    ]);
    renderGoal(goal, tasks.items.filter((t) => t.goal_id === goal.id));
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
  function renderGoals() {
    const ul = $("goals"); ul.innerHTML = "";
    if (!state.goals.length) { ul.innerHTML = '<li class="muted">아직 Goal이 없습니다 — 위에서 하나 만들어 보세요.</li>'; return; }
    for (const g of state.goals) {
      const li = document.createElement("li");
      li.className = (g.id === state.goalId ? "active " : "") + (isShowcase(g) ? "showcase" : "");
      li.innerHTML = `<span class="t" title="${esc(g.title)}">${esc(g.title)}</span>${badge(g.status)}<span class="muted small">${g.done}/${g.total}</span>`;
      li.onclick = () => selectGoal(g.id);
      ul.appendChild(li);
    }
  }
  function renderGoal(goal, tasks) {
    $("detail").hidden = false;
    $("goal-title-view").textContent = goal.title;
    $("goal-status").outerHTML = badge(goal.status).replace("<span", '<span id="goal-status"');
    $("approve-box").hidden = goal.status !== "awaiting_plan_approval";
    $("cancel-goal").hidden = ["done", "cancelled"].includes(goal.status);
    const dl = $("discussion-link");
    if (goal.plan_discussion_url) { dl.href = goal.plan_discussion_url; dl.hidden = false; } else dl.hidden = true;
    const plan = $("plan");
    if (goal.plan_markdown) { plan.className = "plan"; plan.innerHTML = md(goal.plan_markdown); }
    else { plan.className = "plan muted"; plan.textContent = ["draft", "planning"].includes(goal.status) ? "Plan을 만드는 중… (로컬 LLM, 1~3분)" : "Plan 본문이 없습니다."; }
    $("task-count").textContent = tasks.length ? `${goal.done}/${tasks.length} done` : "";
    const tb = $("tasks").querySelector("tbody"); tb.innerHTML = "";
    tasks.sort((a, b) => (a.issue_number || 0) - (b.issue_number || 0));
    for (const t of tasks) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${t.issue_number ?? ""}</td><td>${esc(t.title)}<br><span class="muted small">${esc(t.owned_paths.join(", "))}</span></td><td>${badge(t.status)}</td><td>${t.attempt_count}</td><td>${link(t.issue_url, `#${t.issue_number ?? ""}`)}</td><td>${link(t.pr_url, `PR #${t.pr_number ?? ""}`)}${t.branch_name ? `<br><code class="small">${esc(t.branch_name)}</code>` : ""}</td>`;
      const sr = document.createElement("tr"); sr.className = "spec-row";
      sr.innerHTML = `<td></td><td class="spec" colspan="5">${esc(t.spec || "")}</td>`;
      tr.onclick = () => sr.classList.toggle("open");
      tb.appendChild(tr); tb.appendChild(sr);
    }
  }
  function pushEvent(e) {
    if (e.seq != null && e.seq <= state.lastSeq) return; // replay/live 중복
    if (e.seq != null) state.lastSeq = Math.max(state.lastSeq, e.seq);
    if (e.type === "run.tool_called") return; // 툴 호출은 너무 많다 (체인 밖, D-31)
    const ul = $("events");
    const li = document.createElement("li");
    const p = e.payload || {};
    // task.failed면 사유 + 테스트 출력 꼬리(P9 버그 #5), 그 외는 대표 필드 하나
    const extra = e.type === "task.failed"
      ? `${p.reason || ""}${p.attempt != null ? ` (run ${p.attempt}${p.edit_rounds ? `, ${p.edit_rounds} edits` : ""})` : ""}${p.test_output ? ` — ${String(p.test_output).trim().split("\n").slice(-3).join(" | ")}` : ""}`
      : (p.reason || p.branch || p.pr_number || p.title || p.summary || "");
    li.innerHTML = `<span class="ts">${ts(e.ts)}</span><code>${esc(e.type)}</code> <span class="muted">${esc(e.subject.entity)}:${esc(String(e.subject.id).slice(-6))}</span> ${esc(String(extra).slice(0, 300))}`;
    ul.prepend(li);
    while (ul.children.length > 200) ul.removeChild(ul.lastChild);
    refreshSoon();
  }

  // ---- ws --------------------------------------------------------------
  function connectWs() {
    if (!state.project) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/projects/${state.project.id}/stream?since=${state.lastSeq}`);
    state.ws = ws;
    ws.onopen = () => { $("ws-state").textContent = "실시간"; };
    ws.onmessage = (m) => { try { pushEvent(JSON.parse(m.data)); } catch (e) { console.warn(e); } };
    ws.onclose = () => { $("ws-state").textContent = "재연결 중…"; setTimeout(connectWs, 3000); };
    ws.onerror = () => ws.close();
  }

  // ---- actions ---------------------------------------------------------
  async function selectGoal(id) {
    state.goalId = id; renderGoals();
    try { await loadGoal(); } catch (e) { toast(e.message); }
  }
  $("goal-form").onsubmit = async (ev) => {
    ev.preventDefault();
    if (!state.project) return;
    const title = $("goal-title").value.trim(); if (!title) return;
    $("goal-submit").disabled = true;
    try {
      const g = await api(`/projects/${state.project.id}/goals`, { method: "POST", body: { title } });
      $("goal-title").value = "";
      toast("Goal을 만들었습니다. Plan이 올라오면 Approve 버튼이 보입니다.");
      await loadGoals(); await selectGoal(g.id);
    } catch (e) { toast(e.message); } finally { $("goal-submit").disabled = false; }
  };
  $("approve").onclick = async () => {
    try { await api(`/projects/${state.project.id}/goals/${state.goalId}/approve`, { method: "POST" }); toast("승인했습니다. Task → Issue → 워커 순으로 진행됩니다."); refreshSoon(); }
    catch (e) { toast(e.message); }
  };
  $("delete-project").onclick = async () => {
    if (!state.project) return;
    const p = state.project;
    if (!confirm(`프로젝트 "${p.name}" (${p.repo})를 삭제(보관)할까요?\n남은 Goal·Task는 취소되고 목록에서 사라집니다. GitHub의 Issue/PR/Discussion과 이벤트 기록은 남습니다.`)) return;
    const headers = {}; const tok = $("connect-token").value.trim(); if (tok) headers["X-Admin-Token"] = tok;
    try {
      await api(`/projects/${p.id}`, { method: "DELETE", headers });
      safeSet("foreman.project", "");
      const u = new URL(location.href); u.searchParams.delete("project"); location.href = u.toString();
    } catch (e) { toast(e.message); }
  };
  $("cancel-goal").onclick = async () => {
    if (!state.goalId || !confirm("이 Goal과 남은 Task를 취소할까요? (GitHub Issue/PR은 그대로 남습니다)")) return;
    try {
      await api(`/projects/${state.project.id}/goals/${state.goalId}/cancel`, { method: "POST", body: { reason: "cancelled from console" } });
      toast("취소했습니다."); refreshSoon();
    } catch (e) { toast(e.message); }
  };
  $("reject").onclick = async () => {
    try { await api(`/projects/${state.project.id}/goals/${state.goalId}/reject`, { method: "POST", body: { reason: $("reject-reason").value } }); toast("거절했습니다."); refreshSoon(); }
    catch (e) { toast(e.message); }
  };
  // ---- GitHub repo 연결 (POST /projects) + 사전 점검 (GET /projects/check) ------------------
  function renderCheck(body) {
    const ul = $("connect-check"); ul.innerHTML = "";
    for (const it of body.items) {
      const li = document.createElement("li"); li.className = it.ok ? "ok" : "bad";
      li.textContent = `${it.name}: ${it.detail}`; ul.appendChild(li);
    }
    return body.ok;
  }
  $("connect-check-btn").onclick = async () => {
    const repo = $("connect-repo").value.trim(); if (!repo) return;
    $("connect-check-btn").disabled = true;
    try { const ok = renderCheck(await api(`/projects/check?repo=${encodeURIComponent(repo)}`)); toast(ok ? "점검 통과 — 연결할 수 있습니다." : "점검 실패 항목이 있습니다."); }
    catch (e) { toast(e.message); } finally { $("connect-check-btn").disabled = false; }
  };
  $("connect-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const repo = $("connect-repo").value.trim(); if (!repo) return;
    const owner = repo.includes("/") ? repo.split("/")[0] : state.demo.user_id;
    const members = [{ user_id: owner, role: "owner" }];
    if (owner !== state.demo.user_id) members.push({ user_id: state.demo.user_id, role: "approver" });
    const headers = {}; const tok = $("connect-token").value.trim(); if (tok) headers["X-Admin-Token"] = tok;
    $("connect-submit").disabled = true;
    try {
      const p = await api("/projects", { method: "POST", headers, body: {
        name: $("connect-name").value.trim() || repo.split("/").pop(),
        repo, default_branch: $("connect-branch").value.trim() || "main", members } });
      safeSet("foreman.project", p.id);
      const u = new URL(location.href); u.searchParams.set("project", p.id); location.href = u.toString();
    } catch (e) { toast(e.message); } finally { $("connect-submit").disabled = false; }
  };

  for (const ex of EXAMPLES) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = ex;
    b.onclick = () => { $("goal-title").value = ex; };
    $("examples").appendChild(b);
  }

  // ---- boot ------------------------------------------------------------
  (async () => {
    try {
      await loadDemo(); await loadProject(); await loadGoals(); await loadEvents();
      if (state.goals.length) await selectGoal(state.goals[0].id);
      connectWs();
    } catch (e) { toast(`초기화 실패: ${e.message}`); }
  })();
})();
