"use strict";

const state = {
  documents: [], filtered: [], active: null, query: "", showLabels: true, sidebarKind: "all",
  visibleKinds: new Set(["session", "people", "topic", "daily"]),
};
const kindLabels = { all: "전체", session: "세션", people: "사람", topic: "주제", daily: "일별" };
const folderLabels = { session: "sessions", people: "people", topic: "topics", daily: "daily" };
const kindColors = { session: "var(--session)", people: "var(--people)", topic: "var(--topic)", daily: "var(--daily)" };
const $ = (selector) => document.querySelector(selector);

function normalized(value) {
  return value.toLocaleLowerCase("ko").replaceAll("_", " ").trim();
}

function stem(path) {
  return path.split("/").pop().replace(/\.md$/i, "");
}

function resolveLink(name) {
  const target = normalized(name);
  return state.documents.find((doc) => normalized(doc.title) === target || normalized(stem(doc.path)) === target);
}

function setMobileView(view) {
  document.body.dataset.mobileView = view;
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
}

function setMainView(view) {
  document.body.dataset.mainView = view;
  document.querySelectorAll("[data-main-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mainView === view);
  });
}

function setSidebarKind(kind) {
  state.sidebarKind = kind;
  document.querySelectorAll("[data-sidebar-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.sidebarKind === kind);
  });
  applyFilters();
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.classList.toggle("active", button.dataset.themeChoice === theme);
  });
  localStorage.setItem("mem2life-wiki-theme", theme);
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const toggle = $("#sidebar-toggle");
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "사이드바 열기" : "사이드바 접기");
  localStorage.setItem("mem2life-wiki-sidebar", collapsed ? "collapsed" : "open");
  window.setTimeout(renderGraph, 220);
}

function setGraphSettingsCollapsed(collapsed) {
  const settings = $("#graph-settings");
  const toggle = $("#graph-settings-toggle");
  settings.classList.toggle("collapsed", collapsed);
  toggle.textContent = collapsed ? "+" : "−";
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.title = collapsed ? "그래프 설정 열기" : "그래프 설정 접기";
  localStorage.setItem("mem2life-wiki-graph-settings", collapsed ? "collapsed" : "open");
}

function applyFilters() {
  const query = normalized(state.query);
  state.filtered = state.documents.filter((doc) => {
    const kindMatch = state.visibleKinds.has(doc.kind);
    const sidebarMatch = state.sidebarKind === "all" || doc.kind === state.sidebarKind;
    const textMatch = !query || normalized(`${doc.title} ${doc.body} ${doc.links.join(" ")}`).includes(query);
    return kindMatch && sidebarMatch && textMatch;
  });
  renderDocumentList();
  renderGraph();
  $("#document-count").textContent = state.filtered.length;
}

function renderDocumentList() {
  const list = $("#document-list");
  list.replaceChildren();
  if (!state.filtered.length) {
    const empty = document.createElement("p");
    empty.className = "no-results";
    empty.textContent = "조건에 맞는 기록이 없습니다.";
    list.append(empty);
    return;
  }
  Object.keys(folderLabels).forEach((kind) => {
    const documents = state.filtered.filter((doc) => doc.kind === kind);
    if (!documents.length) return;
    const folder = document.createElement("details");
    folder.className = "vault-folder";
    folder.open = true;
    const summary = document.createElement("summary");
    summary.append(document.createTextNode(folderLabels[kind]));
    const count = document.createElement("span");
    count.className = "folder-count";
    count.textContent = documents.length;
    summary.append(count);
    const items = document.createElement("div");
    items.className = "folder-items";
    documents.forEach((doc) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `document-item${state.active?.path === doc.path ? " active" : ""}`;
      const title = document.createElement("span");
      title.className = "document-item-title";
      title.textContent = doc.title;
      button.append(title);
      button.addEventListener("click", () => openDocument(doc));
      items.append(button);
    });
    folder.append(summary, items);
    list.append(folder);
  });
}

function appendInline(parent, text) {
  const tokenPattern = /(\[\[[^\]]+\]\]|\[\d{1,2}:\d{2}(?::\d{2})?\])/g;
  let cursor = 0;
  for (const match of text.matchAll(tokenPattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("[[")) {
      const rawTarget = token.slice(2, -2).split("|")[0].split("#")[0];
      const linked = resolveLink(rawTarget);
      if (linked) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "wiki-link";
        button.textContent = token.slice(2, -2).split("|").pop();
        button.addEventListener("click", () => openDocument(linked));
        parent.append(button);
      } else {
        parent.append(document.createTextNode(token));
      }
    } else {
      const timestamp = document.createElement("span");
      timestamp.className = "timestamp";
      timestamp.textContent = token;
      parent.append(timestamp);
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function renderMarkdown(body) {
  const container = $("#markdown-body");
  container.replaceChildren();
  let list = null;
  let code = null;
  const finishList = () => { list = null; };
  for (const line of body.split("\n")) {
    if (line.startsWith("```")) {
      finishList();
      if (code) { container.append(code); code = null; } else { code = document.createElement("pre"); code.append(document.createElement("code")); }
      continue;
    }
    if (code) { code.firstChild.append(document.createTextNode(`${line}\n`)); continue; }
    if (!line.trim()) { finishList(); continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      finishList();
      const element = document.createElement(heading[1].length >= 3 ? "h3" : "h2");
      appendInline(element, heading[2]);
      container.append(element);
      continue;
    }
    if (/^---+$/.test(line.trim())) { finishList(); container.append(document.createElement("hr")); continue; }
    if (line.startsWith("- ")) {
      if (!list) { list = document.createElement("ul"); container.append(list); }
      const item = document.createElement("li");
      appendInline(item, line.slice(2));
      list.append(item);
      continue;
    }
    finishList();
    const element = document.createElement(line.startsWith("> ") ? "blockquote" : "p");
    appendInline(element, line.startsWith("> ") ? line.slice(2) : line);
    container.append(element);
  }
  if (code) container.append(code);
}

function openDocument(doc, showReader = true) {
  state.active = doc;
  $("#reader-empty").hidden = true;
  $("#document-view").hidden = false;
  $("#document-title").textContent = doc.title;
  $("#document-path").textContent = doc.path;
  $("#document-meta").textContent = `${kindLabels[doc.kind]}${doc.date ? ` · ${doc.date}` : ""}`;
  const connections = $("#connections");
  connections.replaceChildren();
  doc.links.map(resolveLink).filter(Boolean).forEach((linked) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "connection-button";
    button.textContent = `↗ ${linked.title}`;
    button.addEventListener("click", () => openDocument(linked));
    connections.append(button);
  });
  renderMarkdown(doc.body);
  renderDocumentList();
  renderGraph();
  $("#reader").scrollTo({ top: 0, behavior: "smooth" });
  if (showReader) {
    $("#reader-tab-title").textContent = doc.title;
    $("#reader-tab-close").setAttribute("aria-label", `${doc.title} 닫기`);
    $("#reader-tab").hidden = false;
    setMainView("reader");
    setMobileView("reader");
  }
}

function closeDocument() {
  state.active = null;
  $("#reader-tab").hidden = true;
  $("#document-view").hidden = true;
  $("#reader-empty").hidden = false;
  renderDocumentList();
  setMainView("graph");
  setMobileView("graph");
  renderGraph();
}

function graphEdges(documents) {
  const edges = [];
  const visiblePaths = new Set(documents.map((doc) => doc.path));
  documents.forEach((source) => source.links.forEach((name) => {
    const target = resolveLink(name);
    if (target && visiblePaths.has(target.path) && target.path !== source.path) edges.push({ source, target });
  }));
  return edges;
}

function graphPositions(documents, edges, width, height) {
  const targetDistance = documents.length < 20 ? Math.min(height * .48, 260) : 145;
  const positions = new Map();
  documents.forEach((doc, index) => {
    let seed = 2166136261;
    for (const char of doc.path) seed = Math.imul(seed ^ char.charCodeAt(0), 16777619);
    const angle = ((seed >>> 0) / 4294967296) * Math.PI * 2;
    const ring = 100 + (index % 5) * 62;
    positions.set(doc.path, { x: width / 2 + Math.cos(angle) * ring, y: height / 2 + Math.sin(angle) * ring });
  });
  for (let step = 0; step < 160; step += 1) {
    const force = new Map(documents.map((doc) => [doc.path, { x: 0, y: 0 }]));
    for (let i = 0; i < documents.length; i += 1) {
      for (let j = i + 1; j < documents.length; j += 1) {
        const a = positions.get(documents[i].path); const b = positions.get(documents[j].path);
        const dx = a.x - b.x; const dy = a.y - b.y; const distance2 = Math.max(dx * dx + dy * dy, 64);
        const strength = 22000 / distance2; const distance = Math.sqrt(distance2);
        const fx = (dx / distance) * strength; const fy = (dy / distance) * strength;
        force.get(documents[i].path).x += fx; force.get(documents[i].path).y += fy;
        force.get(documents[j].path).x -= fx; force.get(documents[j].path).y -= fy;
      }
    }
    edges.forEach(({ source, target }) => {
      const a = positions.get(source.path); const b = positions.get(target.path);
      const dx = b.x - a.x; const dy = b.y - a.y; const distance = Math.max(Math.hypot(dx, dy), 1);
      const strength = (distance - targetDistance) * .012;
      const fx = (dx / distance) * strength; const fy = (dy / distance) * strength;
      force.get(source.path).x += fx; force.get(source.path).y += fy;
      force.get(target.path).x -= fx; force.get(target.path).y -= fy;
    });
    documents.forEach((doc) => {
      const point = positions.get(doc.path); const pull = force.get(doc.path);
      pull.x += (width / 2 - point.x) * .0015; pull.y += (height / 2 - point.y) * .0015;
      point.x = Math.max(35, Math.min(width - 35, point.x + pull.x));
      point.y = Math.max(35, Math.min(height - 35, point.y + pull.y));
    });
  }
  return positions;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderGraph() {
  const svg = $("#graph");
  svg.replaceChildren();
  const docs = state.filtered;
  const edges = graphEdges(docs);
  const bounds = svg.getBoundingClientRect();
  const ratio = bounds.width > 0 && bounds.height > 0 ? bounds.width / bounds.height : 1.6;
  const width = 1000; const height = Math.max(420, Math.min(760, width / ratio));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const positions = graphPositions(docs, edges, width, height);
  const degree = new Map(docs.map((doc) => [doc.path, 0]));
  edges.forEach(({ source, target }) => {
    degree.set(source.path, degree.get(source.path) + 1);
    degree.set(target.path, degree.get(target.path) + 1);
  });
  edges.forEach(({ source, target }) => {
    const a = positions.get(source.path); const b = positions.get(target.path);
    const active = state.active && (source.path === state.active.path || target.path === state.active.path);
    svg.append(svgElement("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `graph-edge${active ? " active" : ""}` }));
  });
  docs.forEach((doc) => {
    const point = positions.get(doc.path);
    const connected = !state.active || doc.path === state.active.path || edges.some((edge) => (edge.source.path === state.active.path && edge.target.path === doc.path) || (edge.target.path === state.active.path && edge.source.path === doc.path));
    const radius = doc.path === state.active?.path ? 20 : Math.min(8 + degree.get(doc.path) * 2, 18);
    const circle = svgElement("circle", { cx: point.x, cy: point.y, r: radius, fill: kindColors[doc.kind], class: `graph-node${connected ? "" : " dim"}`, role: "button", tabindex: "0" });
    circle.append(svgElement("title"));
    circle.firstChild.textContent = doc.title;
    circle.addEventListener("click", () => openDocument(doc));
    circle.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") openDocument(doc); });
    svg.append(circle);
    if (state.showLabels && (doc.path === state.active?.path || docs.length <= 30)) {
      const label = svgElement("text", { x: point.x, y: point.y + radius + 14, class: "graph-label" });
      label.textContent = doc.title.length > 12 ? `${doc.title.slice(0, 12)}…` : doc.title;
      svg.append(label);
    }
  });
  $("#graph-count").textContent = `${docs.length} · ${edges.length}`;
}

function showError() {
  const reader = $("#reader");
  reader.replaceChildren($("#error-template").content.cloneNode(true));
  reader.querySelector("button").addEventListener("click", () => window.location.reload());
  setMainView("reader");
  setMobileView("reader");
}

async function loadDocuments() {
  try {
    const response = await fetch("/api/documents", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.documents = await response.json();
    state.documents.sort((a, b) => (b.date || "").localeCompare(a.date || "") || a.title.localeCompare(b.title, "ko"));
    applyFilters();
    if (state.documents.length) {
      openDocument(state.documents[0], false);
      state.active = null;
      renderDocumentList();
      renderGraph();
    }
    else { $("#reader-empty h1").textContent = "아직 기록이 없습니다"; $("#reader-empty p").textContent = "볼트에 Markdown 문서가 생기면 여기에 표시됩니다."; }
    setMainView("graph");
    setMobileView("graph");
  } catch (error) {
    console.error(error);
    showError();
  }
}

$("#search-input").addEventListener("input", (event) => { state.query = event.target.value; applyFilters(); setMobileView("list"); });
$("#refresh-button").addEventListener("click", loadDocuments);
$("#reader-tab-close").addEventListener("click", closeDocument);
$("#sidebar-toggle").addEventListener("click", () => {
  if (window.innerWidth <= 720) setMobileView("list");
  else setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
});
$("#graph-settings-toggle").addEventListener("click", () => {
  setGraphSettingsCollapsed(!$("#graph-settings").classList.contains("collapsed"));
});
document.querySelectorAll("[data-main-view]").forEach((button) => button.addEventListener("click", () => setMainView(button.dataset.mainView)));
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setMobileView(button.dataset.view)));
document.querySelectorAll("[data-sidebar-kind]").forEach((button) => button.addEventListener("click", () => setSidebarKind(button.dataset.sidebarKind)));
document.querySelectorAll("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => setTheme(button.dataset.themeChoice)));
document.querySelectorAll("[data-kind]").forEach((input) => input.addEventListener("change", () => {
  if (input.checked) state.visibleKinds.add(input.dataset.kind); else state.visibleKinds.delete(input.dataset.kind);
  applyFilters();
}));
$("#labels-toggle").addEventListener("change", (event) => { state.showLabels = event.target.checked; renderGraph(); });
document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey)) return;
  if (event.key.toLowerCase() === "k") { event.preventDefault(); $("#search-input").focus(); }
  if (event.key.toLowerCase() === "b") { event.preventDefault(); setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed")); }
});
window.addEventListener("resize", () => window.requestAnimationFrame(renderGraph));

const viewParameters = new URLSearchParams(window.location.search);
const requestedTheme = viewParameters.get("theme");
setTheme(["light", "dark"].includes(requestedTheme) ? requestedTheme : (localStorage.getItem("mem2life-wiki-theme") || "light"));
const requestedSidebar = viewParameters.get("sidebar");
setSidebarCollapsed(requestedSidebar === "collapsed" || (requestedSidebar !== "open" && localStorage.getItem("mem2life-wiki-sidebar") === "collapsed"));
setGraphSettingsCollapsed(localStorage.getItem("mem2life-wiki-graph-settings") === "collapsed");
window.requestAnimationFrame(() => document.body.classList.add("sidebar-ready"));
loadDocuments();
