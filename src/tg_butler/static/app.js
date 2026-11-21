"use strict";

let csrf = "";
let state = null;
let currentPage = "overview";
let currentRunsFilter = "all";
let lastSyncAt = null;
let loginCountdownTimer = null;
let loginExpiresAt = 0;

const dirtyPages = { groups: false, checkins: false };
const aiModelsMem = new Map();
let currentLookupView = null;
let backupsLoaded = false;
let cacheStatsLoaded = false;

const $ = (id) => document.getElementById(id);

const VALID_PAGES = new Set([
  "overview",
  "telegram",
  "lookup",
  "groups",
  "checkins",
  "summary",
  "settings",
]);

const PAGE_TITLES = {
  overview: "运行概览",
  telegram: "Telegram 账号",
  lookup: "查找群组与 ID",
  groups: "监控群组",
  checkins: "定时签到",
  summary: "日报与 AI",
  settings: "设置与缓存",
};

const PAGE_SUMMARIES = {
  overview: "查看任务运行状态、配置就绪度与最近执行记录。",
  telegram: "管理多个 Telegram 账号会话、应用凭据与历史消息导入。",
  lookup: "带缓存读取群组、成员和实体 ID，支持一键添加到监控或签到。",
  groups: "配置监控群组、关注用户与双机器人筛选分流规则。",
  checkins: "管理每日定时签到命令、时间窗口与回复确认正则。",
  summary: "配置每日群聊摘要、AI 模型缓存与测试发送。",
  settings: "集中管理运行参数、管理员账户、通知机器人、备份与缓存。",
};

const RUN_KIND_LABELS = {
  checkin: "定时签到",
  summary: "每日日报",
};

const RUN_STATUS_META = {
  confirmed: { label: "已确认成功", tone: "ok" },
  ok: { label: "已完成", tone: "ok" },
  sent: { label: "已发送", tone: "info" },
  claimed: { label: "执行中", tone: "info" },
  timeout: { label: "等待超时", tone: "warn" },
  uncertain: { label: "结果未确认", tone: "warn" },
  rejected: { label: "收到失败回复", tone: "err" },
  error: { label: "执行失败", tone: "err" },
};

const STORAGE_KEYS = {
  theme: "tg_butler_theme",
  accounts: "tg_butler_selected_accounts",
  lookup: "tg_butler_lookup_cache_v1",
  aiModels: "tg_butler_ai_models_v1",
};

const SVG_NS = "http://www.w3.org/2000/svg";

function createSvg(paths, className = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  for (const spec of paths) {
    const el = document.createElementNS(SVG_NS, spec.tag || "path");
    for (const [k, v] of Object.entries(spec)) {
      if (k !== "tag") el.setAttribute(k, String(v));
    }
    svg.append(el);
  }
  return svg;
}

const ICONS = {
  sun: [
    { tag: "circle", cx: 12, cy: 12, r: 4 },
    { d: "M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" },
  ],
  moon: [{ d: "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" }],
  eye: [
    { d: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" },
    { tag: "circle", cx: 12, cy: 12, r: 3 },
  ],
  eyeOff: [
    { d: "M17.94 17.94A10.94 10.94 0 0 1 12 19c-6.5 0-10-7-10-7a21.8 21.8 0 0 1 5.06-5.94" },
    { d: "M9.9 4.24A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a21.8 21.8 0 0 1-3.17 4.35" },
    { d: "M1 1l22 22" },
  ],
  chevron: [{ d: "m6 9 6 6 6-6" }],
  checkCircle: [
    { tag: "circle", cx: 12, cy: 12, r: 9 },
    { d: "m9 12 2 2 4-4" },
  ],
  alertCircle: [
    { tag: "circle", cx: 12, cy: 12, r: 9 },
    { d: "M12 8v4M12 16h.01" },
  ],
  infoCircle: [
    { tag: "circle", cx: 12, cy: 12, r: 9 },
    { d: "M12 16v-4M12 8h.01" },
  ],
  close: [{ d: "m6 6 12 12M18 6 6 18" }],
};

/* ---------- Theme ---------- */
function getInitialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEYS.theme);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* ignore */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = $("theme-toggle");
  if (btn) {
    btn.replaceChildren(createSvg(theme === "dark" ? ICONS.sun : ICONS.moon));
    btn.title = theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
    btn.setAttribute("aria-label", btn.title);
  }
}

applyTheme(getInitialTheme());
$("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(STORAGE_KEYS.theme, next);
  } catch {
    /* ignore */
  }
});

/* ---------- Password Visibility ---------- */
function initPasswordToggles(root = document) {
  for (const btn of root.querySelectorAll("[data-password-toggle]")) {
    if (btn.dataset.bound === "1") continue;
    btn.dataset.bound = "1";
    const input = btn.parentElement.querySelector("input");
    const sync = () => {
      const visible = input.type === "text";
      btn.replaceChildren(createSvg(visible ? ICONS.eyeOff : ICONS.eye));
      btn.setAttribute("aria-label", visible ? "隐藏密码" : "显示密码");
      btn.title = visible ? "隐藏输入" : "显示输入";
    };
    btn.addEventListener("click", () => {
      input.type = input.type === "password" ? "text" : "password";
      sync();
      input.focus();
    });
    sync();
  }
}
initPasswordToggles();

/* ---------- Session Storage Cache Helpers ---------- */
function readSessionJson(key, fallback) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeSessionJson(key, value) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore quota errors */
  }
}

function clearBrowserCaches() {
  aiModelsMem.clear();
  currentLookupView = null;
  try {
    sessionStorage.removeItem(STORAGE_KEYS.lookup);
    sessionStorage.removeItem(STORAGE_KEYS.aiModels);
  } catch {
    /* ignore */
  }
  updateCachedDialogsDatalist([]);
  $("lookup-results-panel").hidden = true;
  $("lookup-cache-badge").hidden = true;
  $("ai-models-cache-badge").hidden = true;
}

function getLookupStore() {
  return readSessionJson(STORAGE_KEYS.lookup, {});
}

function saveLookupEntry(accountId, mode, entry) {
  const store = getLookupStore();
  store[accountId] = store[accountId] || {};
  store[accountId][mode] = entry;
  writeSessionJson(STORAGE_KEYS.lookup, store);
  if (mode === "dialogs" && Array.isArray(entry.items)) {
    updateCachedDialogsDatalist(entry.items);
  }
}

function getLookupEntry(accountId, mode) {
  const store = getLookupStore();
  return store[accountId]?.[mode] || null;
}

function updateCachedDialogsDatalist(items) {
  const datalist = $("cached-dialogs-list");
  if (!datalist) return;
  datalist.replaceChildren();
  const seen = new Set();
  for (const item of items || []) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    const opt = document.createElement("option");
    opt.value = String(item.id);
    opt.label = item.name ? `${item.name} (${item.id})` : String(item.id);
    datalist.append(opt);
  }
}

function restoreDatalistFromSession() {
  const store = getLookupStore();
  const merged = [];
  for (const acc of Object.values(store)) {
    if (acc?.dialogs?.items) merged.push(...acc.dialogs.items);
  }
  updateCachedDialogsDatalist(merged);
}

/* ---------- Toast ---------- */
function toast(message, variant = "info") {
  const type = variant === true ? "error" : variant === false ? "info" : variant;
  const stack = $("toast-stack");
  const item = document.createElement("div");
  item.className = `toast-item${type === "error" ? " is-error" : type === "success" ? " is-success" : ""}`;
  item.setAttribute("role", type === "error" ? "alert" : "status");

  const iconDef =
    type === "error" ? ICONS.alertCircle : type === "success" ? ICONS.checkCircle : ICONS.infoCircle;
  const text = document.createElement("div");
  text.className = "toast-text";
  text.textContent = message;

  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "关闭提示");
  close.append(createSvg(ICONS.close));

  const dismiss = () => {
    clearTimeout(timer);
    item.remove();
  };
  close.addEventListener("click", dismiss);

  item.append(createSvg(iconDef, "toast-icon"), text, close);
  stack.append(item);
  while (stack.children.length > 4) {
    stack.firstElementChild.remove();
  }
  const timer = window.setTimeout(dismiss, type === "error" ? 9000 : 5200);
}

/* ---------- Accessible Modal ---------- */
let activeModalResolver = null;
let previousFocus = null;

function closeModal(value) {
  const backdrop = $("modal-backdrop");
  if (backdrop.hidden) return;
  backdrop.hidden = true;
  $("modal-body").replaceChildren();
  $("modal-footer").replaceChildren();
  const resolver = activeModalResolver;
  activeModalResolver = null;
  if (previousFocus && typeof previousFocus.focus === "function") {
    previousFocus.focus();
  }
  previousFocus = null;
  if (resolver) resolver(value);
}

$("modal-close").addEventListener("click", () => closeModal(null));
$("modal-backdrop").addEventListener("click", (event) => {
  if (event.target === $("modal-backdrop")) closeModal(null);
});

function openModal({ title, bodyNodes, confirmText = "确认", cancelText = "取消", danger = false, onConfirm }) {
  previousFocus = document.activeElement;
  $("modal-title").textContent = title;
  const body = $("modal-body");
  body.replaceChildren(...bodyNodes);
  const footer = $("modal-footer");
  footer.replaceChildren();

  return new Promise((resolve) => {
    activeModalResolver = resolve;
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn-secondary";
    cancelBtn.textContent = cancelText;
    cancelBtn.addEventListener("click", () => closeModal(null));

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = danger ? "btn-danger" : "btn-primary";
    confirmBtn.textContent = confirmText;
    confirmBtn.addEventListener("click", () => {
      const value = onConfirm ? onConfirm() : true;
      if (value === undefined) return;
      closeModal(value);
    });

    footer.append(cancelBtn, confirmBtn);
    $("modal-backdrop").hidden = false;
    const focusable = body.querySelector("input, select") || confirmBtn;
    focusable.focus();
  });
}

function modalConfirm(title, message, { confirmText = "确认继续", danger = false } = {}) {
  const p = document.createElement("p");
  p.textContent = message;
  return openModal({ title, bodyNodes: [p], confirmText, danger }).then((res) => Boolean(res));
}

function modalPrompt(title, labelText, { defaultValue = "", placeholder = "", confirmText = "确定" } = {}) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.value = defaultValue;
  input.placeholder = placeholder;
  label.append(input);

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const val = input.value.trim();
      if (val) closeModal(val);
    }
  });

  return openModal({
    title,
    bodyNodes: [label],
    confirmText,
    onConfirm: () => {
      const val = input.value.trim();
      if (!val) {
        toast("请填写内容", "error");
        input.focus();
        return undefined;
      }
      return val;
    },
  });
}

function modalPickGroup(userItem) {
  const groups = state?.config?.groups || [];
  if (!groups.length) {
    toast("还没有配置任何监控群组，请先添加监控群组。", "error");
    return Promise.resolve(null);
  }
  const desc = document.createElement("p");
  desc.textContent = `将用户「${userItem.name || userItem.id}」（ID: ${userItem.id}）添加到哪个监控群组的关注列表？`;
  const label = document.createElement("label");
  label.textContent = "选择监控群组";
  const select = document.createElement("select");
  for (const g of groups) {
    const opt = document.createElement("option");
    opt.value = String(g.id);
    opt.textContent = `${g.name || "未命名群组"} (${g.id})`;
    select.append(opt);
  }
  label.append(select);
  return openModal({
    title: "添加到群组关注用户",
    bodyNodes: [desc, label],
    confirmText: "添加关注",
    onConfirm: () => select.value,
  });
}

/* ---------- Network / API ---------- */
async function apiWithMeta(path, data) {
  const response = await fetch("/api/" + path, {
    method: data === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  let result = {};
  try {
    result = await response.json();
  } catch {
    result = { error: `服务器返回异常状态 (${response.status})` };
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "login") showLogin();
    throw Error(result.error || "请求失败");
  }
  const cacheHeader = response.headers.get("X-Cache");
  const cacheAtRaw = response.headers.get("X-Cache-At");
  return {
    data: result,
    cache: cacheHeader || null,
    cachedAt: cacheAtRaw ? Number(cacheAtRaw) * 1000 : null,
  };
}

async function api(path, data) {
  const { data: result } = await apiWithMeta(path, data);
  return result;
}

/* ---------- Helpers ---------- */
function formatTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return "";
  }
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

async function copyText(text, label = "内容") {
  try {
    await navigator.clipboard.writeText(String(text));
    toast(`已复制${label}：${text}`, "success");
  } catch {
    const area = document.createElement("textarea");
    area.value = String(text);
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast(`已复制${label}：${text}`, "success");
  }
}

function fields(form) {
  return Object.fromEntries(new FormData(form));
}

function syncStateToggle(input) {
  const key = input.name || input.dataset.key;
  const button = input.parentElement?.querySelector(`[data-toggle-for="${key}"]`);
  if (!button) return;
  const active = input.checked;
  button.setAttribute("aria-checked", String(active));
  button.classList.toggle("is-active", active);
  const labelNode = button.querySelector(".switch-label");
  if (labelNode) labelNode.textContent = active ? "已启用" : "已停用";
}

function addStateToggle(input) {
  if (input.dataset.enhanced === "1") {
    syncStateToggle(input);
    return;
  }
  input.dataset.enhanced = "1";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "state-toggle";
  button.dataset.toggleFor = input.name || input.dataset.key;
  button.setAttribute("role", "switch");
  button.setAttribute("aria-label", input.closest("label")?.textContent?.trim() || "切换状态");

  const track = document.createElement("span");
  track.className = "switch-track";
  const thumb = document.createElement("span");
  thumb.className = "switch-thumb";
  track.append(thumb);

  const labelSpan = document.createElement("span");
  labelSpan.className = "switch-label";

  button.append(track, labelSpan);
  input.insertAdjacentElement("afterend", button);
  input.classList.add("state-input");

  button.addEventListener("click", () => {
    input.checked = !input.checked;
    syncStateToggle(input);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  syncStateToggle(input);
  return button;
}

function fill(id, data) {
  const form = $(id);
  if (!form) return;
  for (const [key, value] of Object.entries(data)) {
    const field = form.elements.namedItem(key);
    if (!field) continue;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
      syncStateToggle(field);
    } else {
      field.value = value ?? "";
    }
  }
}

function bindForm(id, handler) {
  const form = $(id);
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const buttons = [...form.querySelectorAll('button:not([data-password-toggle])')];
    buttons.forEach((b) => (b.disabled = true));
    try {
      await handler(fields(form), form);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  });
}

function bindButton(id, handler) {
  const button = $(id);
  if (!button) return;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler(button);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}

function integer(value, label) {
  if (!/^-?\d+$/.test(String(value))) throw Error(`${label}需要填写数字`);
  const result = Number(value);
  if (!Number.isSafeInteger(result)) throw Error(`${label}超出支持范围`);
  return result;
}

/* ---------- Navigation & Mobile Drawer ---------- */
const mobileNavigation = window.matchMedia("(max-width:760px)");

function setMenuOpen(open, restoreFocus = true) {
  const sidebar = $("sidebar");
  const mobile = mobileNavigation.matches;
  open = mobile && open;
  const wasOpen = document.body.classList.contains("menu-open");
  document.body.classList.toggle("menu-open", open);
  $("menu-toggle").setAttribute("aria-expanded", String(open));
  $("nav-backdrop").hidden = !open;
  $("main-content").inert = open;
  sidebar.inert = mobile && !open;
  if (mobile) sidebar.setAttribute("aria-hidden", String(!open));
  else sidebar.removeAttribute("aria-hidden");
  if (open) {
    sidebar.setAttribute("role", "dialog");
    sidebar.setAttribute("aria-modal", "true");
    $("menu-close").focus();
  } else {
    sidebar.removeAttribute("role");
    sidebar.removeAttribute("aria-modal");
    if (wasOpen && restoreFocus && mobile) $("menu-toggle").focus();
  }
}

$("menu-toggle").addEventListener("click", () => setMenuOpen(true));
$("menu-close").addEventListener("click", () => setMenuOpen(false));
$("nav-backdrop").addEventListener("click", () => setMenuOpen(false));
mobileNavigation.addEventListener("change", () => setMenuOpen(false, false));
setMenuOpen(false, false);

function showLogin() {
  setMenuOpen(false, false);
  closeModal(null);
  $("dirty-bar").hidden = true;
  $("login").hidden = false;
  $("dashboard").hidden = true;
}

function syncDirtyBar() {
  const bar = $("dirty-bar");
  const isDirty = (currentPage === "groups" && dirtyPages.groups) || (currentPage === "checkins" && dirtyPages.checkins);
  bar.hidden = !isDirty;
  if (isDirty) {
    $("dirty-message").textContent =
      currentPage === "groups" ? "监控群组有未保存的更改" : "定时签到任务有未保存的更改";
  }
}

function markDirty(page, dirty = true) {
  if (page in dirtyPages) {
    dirtyPages[page] = dirty;
    syncDirtyBar();
  }
}

function navigate(page, { pushHash = true } = {}) {
  if (!VALID_PAGES.has(page)) page = "overview";
  currentPage = page;
  document.querySelectorAll("[data-section]").forEach((s) => {
    s.hidden = s.dataset.section !== page;
  });
  document.querySelectorAll("[data-page]").forEach((b) => {
    const selected = b.dataset.page === page;
    b.classList.toggle("selected", selected);
    if (b.closest("nav")) {
      if (selected) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    }
  });
  $("page-title").textContent = PAGE_TITLES[page];
  $("page-summary").textContent = PAGE_SUMMARIES[page] || "";
  $("global-notice").hidden = page !== "overview";
  setMenuOpen(false);
  syncDirtyBar();

  if (pushHash && location.hash !== `#${page}`) {
    history.replaceState(null, "", `#${page}`);
  }
  if (mobileNavigation.matches) window.scrollTo({ top: 0, behavior: "instant" });

  if (page === "settings" && state) {
    if (!backupsLoaded) loadBackups().catch(() => {});
    if (!cacheStatsLoaded) loadCacheStats().catch(() => {});
  }
  if (page === "lookup" && state) {
    restoreLookupForSelectedAccount();
  }
}

window.addEventListener("hashchange", () => {
  const hashPage = location.hash.replace(/^#/, "");
  if (VALID_PAGES.has(hashPage) && hashPage !== currentPage && !$("dashboard").hidden) {
    navigate(hashPage, { pushHash: false });
  }
});

document.addEventListener("keydown", (event) => {
  if (!$("modal-backdrop").hidden && event.key === "Escape") {
    event.preventDefault();
    closeModal(null);
    return;
  }
  if (document.body.classList.contains("menu-open")) {
    if (event.key === "Escape") {
      event.preventDefault();
      setMenuOpen(false);
      return;
    }
    if (event.key === "Tab") {
      const controls = [...$("sidebar").querySelectorAll("a[href],button:not(:disabled)")].filter(
        (el) => el.getClientRects().length
      );
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && !$("dashboard").hidden) {
    if (currentPage === "groups" && dirtyPages.groups) {
      event.preventDefault();
      $("save-groups").click();
    } else if (currentPage === "checkins" && dirtyPages.checkins) {
      event.preventDefault();
      $("save-checkins").click();
    } else if (currentPage === "summary") {
      event.preventDefault();
      $("summary-form").requestSubmit();
    }
  }
});

/* ---------- Overview Rendering ---------- */
function renderReadiness() {
  const strip = $("readiness-strip");
  if (!state) return;
  const hasAppCreds = Boolean(state.credentials?.TG_API_ID && state.configured?.TG_API_HASH);
  const hasAuthAccount = (state.accounts || []).some((a) => a.authorized);
  const hasBot = Boolean(
    (state.configured?.TG_MONITOR_BOT_TOKEN || state.configured?.TG_BOT_TOKEN) &&
      (state.credentials?.TG_MONITOR_NOTIFY_CHAT_ID || state.credentials?.TG_NOTIFY_CHAT_ID)
  );
  const hasGroups = (state.config?.groups || []).length > 0;

  const checks = [
    { label: "应用凭据", done: hasAppCreds, page: "telegram" },
    { label: "账号登录", done: hasAuthAccount, page: "telegram" },
    { label: "通知机器人", done: hasBot, page: "settings" },
    { label: "监控群组", done: hasGroups, page: "groups" },
  ];

  const allDone = checks.every((c) => c.done);
  strip.hidden = allDone;
  if (allDone) return;

  strip.replaceChildren();
  const title = document.createElement("span");
  title.className = "readiness-label";
  title.textContent = "配置进度：";
  strip.append(title);

  for (const check of checks) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `readiness-pill${check.done ? " is-done" : ""}`;
    const dot = document.createElement("span");
    dot.className = "dot";
    const text = document.createElement("span");
    text.textContent = `${check.label}${check.done ? " · 已就绪" : " · 待配置"}`;
    btn.append(dot, text);
    btn.addEventListener("click", () => navigate(check.page));
    strip.append(btn);
  }
}

function renderRuns() {
  const tbody = $("runs");
  tbody.replaceChildren();
  const rows = (state?.runs || []).filter(
    (row) => currentRunsFilter === "all" || row.kind === currentRunsFilter
  );
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty-state";
    td.textContent = currentRunsFilter === "all" ? "暂无执行记录，启动任务服务后会在此展示。" : "当前筛选条件下暂无记录。";
    tr.append(td);
    tbody.append(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");

    const tdDay = document.createElement("td");
    tdDay.textContent = row.day || "—";

    const tdKind = document.createElement("td");
    const kindChip = document.createElement("span");
    kindChip.className = "badge-chip neutral";
    kindChip.textContent = RUN_KIND_LABELS[row.kind] || row.kind;
    tdKind.append(kindChip);

    const tdName = document.createElement("td");
    tdName.textContent = row.name || "—";

    const tdStatus = document.createElement("td");
    const meta = RUN_STATUS_META[row.status] || { label: row.status || "未知", tone: "neutral" };
    const statusChip = document.createElement("span");
    statusChip.className = `badge-chip ${meta.tone}`;
    statusChip.textContent = meta.label;
    tdStatus.append(statusChip);

    const tdDetail = document.createElement("td");
    tdDetail.className = "muted";
    tdDetail.textContent = row.detail || "—";

    tr.append(tdDay, tdKind, tdName, tdStatus, tdDetail);
    tbody.append(tr);
  }
}

/* ---------- Accounts ---------- */
function saveSelectedAccounts() {
  const map = {};
  for (const id of ["login-account", "lookup-account", "history-account"]) {
    const el = $(id);
    if (el?.value) map[id] = el.value;
  }
  writeSessionJson(STORAGE_KEYS.accounts, map);
}

function renderAccounts() {
  const accounts = state.config.accounts || [{ id: "default", name: "默认账号" }];
  const list = $("account-list");
  list.replaceChildren();

  for (const account of accounts) {
    const authorized = Boolean(state.accounts?.find((item) => item.id === account.id)?.authorized);
    const row = document.createElement("div");
    row.className = "account-row";

    const avatar = document.createElement("div");
    avatar.className = "account-avatar";
    avatar.textContent = (account.name || "T").slice(0, 1).toUpperCase();

    const info = document.createElement("div");
    info.className = "account-info";

    const title = document.createElement("div");
    title.className = "account-title";
    const nameEl = document.createElement("b");
    nameEl.textContent = account.name;
    title.append(nameEl);

    if (account.id === "default") {
      const defTag = document.createElement("span");
      defTag.className = "badge-chip neutral";
      defTag.textContent = "系统默认";
      title.append(defTag);
    }

    const statusTag = document.createElement("span");
    statusTag.className = `badge-chip ${authorized ? "ok" : "warn"}`;
    statusTag.textContent = authorized ? "会话已登录" : "尚未登录";
    title.append(statusTag);

    const meta = document.createElement("small");
    meta.textContent = `账号 ID：${account.id}`;
    info.append(title, meta);

    const actions = document.createElement("div");
    actions.className = "account-actions";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn-secondary btn-sm";
    edit.textContent = "重命名";
    edit.addEventListener("click", async () => {
      const nextName = await modalPrompt("重命名 Telegram 账号", "账号备注名称", {
        defaultValue: account.name,
        placeholder: "例如 个人主力号",
        confirmText: "保存名称",
      });
      if (!nextName || nextName === account.name) return;
      try {
        await saveConfig((config) => {
          config.accounts = config.accounts.map((item) =>
            item.id === account.id ? { ...item, name: nextName } : item
          );
        });
      } catch (error) {
        toast(error.message, "error");
      }
    });
    actions.append(edit);

    if (account.id !== "default") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn-danger-soft btn-sm";
      remove.textContent = "删除";
      remove.addEventListener("click", async () => {
        if (
          (state.config.groups || []).some((x) => x.account_id === account.id) ||
          (state.config.checkins || []).some((x) => x.account_id === account.id)
        ) {
          toast("请先将该账号的群组和签到任务分配给其他账号。", "error");
          return;
        }
        const ok = await modalConfirm(
          "删除账号",
          `确认移除账号「${account.name}」吗？其本地登录会话文件会保留在服务器中。`,
          { confirmText: "确认删除", danger: true }
        );
        if (!ok) return;
        try {
          await saveConfig((config) => {
            config.accounts = config.accounts.filter((x) => x.id !== account.id);
          });
        } catch (error) {
          toast(error.message, "error");
        }
      });
      actions.append(remove);
    }

    row.append(avatar, info, actions);
    list.append(row);
  }

  const savedSelection = readSessionJson(STORAGE_KEYS.accounts, {});
  const validIds = new Set(accounts.map((a) => a.id));

  for (const id of ["login-account", "lookup-account", "history-account"]) {
    const select = $(id);
    const prev = select.value || savedSelection[id];
    select.replaceChildren();
    for (const account of accounts) {
      const option = document.createElement("option");
      option.value = account.id;
      option.textContent = account.name;
      select.append(option);
    }
    if (prev && validIds.has(prev)) {
      select.value = prev;
    }
  }
}

for (const id of ["login-account", "lookup-account", "history-account"]) {
  $(id).addEventListener("change", () => {
    saveSelectedAccounts();
    if (id === "lookup-account") restoreLookupForSelectedAccount();
  });
}

/* ---------- Login Countdown ---------- */
function startLoginCountdown() {
  clearInterval(loginCountdownTimer);
  loginExpiresAt = Date.now() + 5 * 60 * 1000;
  const pill = $("login-countdown");
  const tick = () => {
    const remaining = Math.max(0, Math.ceil((loginExpiresAt - Date.now()) / 1000));
    if (remaining <= 0) {
      clearInterval(loginCountdownTimer);
      pill.hidden = true;
      return;
    }
    const mins = Math.floor(remaining / 60);
    const secs = String(remaining % 60).padStart(2, "0");
    pill.textContent = `验证码有效 ${mins}:${secs}`;
    pill.classList.add("is-live");
    pill.hidden = false;
  };
  tick();
  loginCountdownTimer = window.setInterval(tick, 1000);
}

function stopLoginCountdown() {
  clearInterval(loginCountdownTimer);
  $("login-countdown").hidden = true;
}

/* ---------- Lookup Workbench ---------- */
function setCachePill(el, cacheStatus, cachedAt) {
  if (!el) return;
  if (!cacheStatus && !cachedAt) {
    el.hidden = true;
    return;
  }
  const timeText = cachedAt ? ` · ${formatTime(cachedAt)}` : "";
  if (cacheStatus === "HIT") {
    el.textContent = `来自缓存${timeText}`;
    el.classList.remove("is-live");
  } else {
    el.textContent = `实时拉取${timeText}`;
    el.classList.add("is-live");
  }
  el.hidden = false;
}

function renderLookupView(view) {
  currentLookupView = view;
  const panel = $("lookup-results-panel");
  if (!view || !Array.isArray(view.items)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("lookup-results-title").textContent = view.title || "查询结果";
  setCachePill($("lookup-cache-badge"), view.cache, view.cachedAt);

  const query = ($("lookup-filter").value || "").trim().toLowerCase();
  const filtered = view.items.filter((item) => {
    if (!query) return true;
    const hay = `${item.id} ${item.name || ""} ${item.username || ""}`.toLowerCase();
    return hay.includes(query);
  });

  $("lookup-results-count").textContent =
    query && filtered.length !== view.items.length
      ? `${filtered.length} / ${view.items.length}`
      : String(view.items.length);

  const container = $("lookup-list");
  container.replaceChildren();

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = query ? "没有匹配筛选条件的条目。" : "没有可显示的记录。";
    container.append(empty);
    return;
  }

  for (const item of filtered) {
    const row = document.createElement("div");
    row.className = "lookup-row";

    const main = document.createElement("div");
    main.className = "lookup-row-main";

    const title = document.createElement("div");
    title.className = "lookup-row-title";
    const nameEl = document.createElement("b");
    nameEl.textContent = item.name || "（无名称）";
    title.append(nameEl);

    if (item.username) {
      const handle = document.createElement("span");
      handle.className = "badge-chip neutral";
      handle.textContent = `@${item.username}`;
      title.append(handle);
    }

    const meta = document.createElement("div");
    meta.className = "lookup-row-meta";
    const idChip = document.createElement("span");
    idChip.className = "mono-id";
    idChip.textContent = String(item.id);
    meta.append(idChip);

    main.append(title, meta);

    const actions = document.createElement("div");
    actions.className = "lookup-row-actions";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn-secondary btn-sm";
    copyBtn.textContent = "复制 ID";
    copyBtn.addEventListener("click", () => copyText(item.id, " ID "));
    actions.append(copyBtn);

    if (view.itemKind === "group") {
      const membersBtn = document.createElement("button");
      membersBtn.type = "button";
      membersBtn.className = "btn-ghost btn-sm";
      membersBtn.textContent = "查成员";
      membersBtn.addEventListener("click", () => {
        $("members-form").elements.chat_id.value = String(item.id);
        fetchMembers(item.id, false);
      });
      actions.append(membersBtn);

      const addGroupBtn = document.createElement("button");
      addGroupBtn.type = "button";
      addGroupBtn.className = "btn-secondary btn-sm";
      addGroupBtn.textContent = "+ 监控";
      addGroupBtn.addEventListener("click", () => quickAddGroupFromLookup(item, view.accountId));
      actions.append(addGroupBtn);

      const addCheckinBtn = document.createElement("button");
      addCheckinBtn.type = "button";
      addCheckinBtn.className = "btn-ghost btn-sm";
      addCheckinBtn.textContent = "+ 签到";
      addCheckinBtn.addEventListener("click", () => quickAddCheckinFromLookup(item, view.accountId));
      actions.append(addCheckinBtn);
    } else if (view.itemKind === "user") {
      const watchBtn = document.createElement("button");
      watchBtn.type = "button";
      watchBtn.className = "btn-secondary btn-sm";
      watchBtn.textContent = "+ 关注";
      watchBtn.addEventListener("click", () => quickWatchUserFromLookup(item));
      actions.append(watchBtn);
    }

    row.append(main, actions);
    container.append(row);
  }
}

function restoreLookupForSelectedAccount() {
  const accountId = $("lookup-account").value || "default";
  const cached = getLookupEntry(accountId, "dialogs");
  if (cached) {
    renderLookupView(cached);
  } else if (currentLookupView && currentLookupView.accountId !== accountId) {
    $("lookup-results-panel").hidden = true;
    $("lookup-cache-badge").hidden = true;
  }
}

async function fetchDialogs(refresh = false) {
  const accountId = $("lookup-account").value || "default";
  const { data, cache, cachedAt } = await apiWithMeta("telegram/dialogs", {
    account_id: accountId,
    refresh,
  });
  const view = {
    mode: "dialogs",
    itemKind: "group",
    accountId,
    title: "账号已加入的群组 / 频道",
    items: data.items || [],
    cache,
    cachedAt: cachedAt || Date.now(),
  };
  saveLookupEntry(accountId, "dialogs", view);
  $("lookup-filter").value = "";
  renderLookupView(view);
  toast(
    cache === "HIT"
      ? `已从缓存加载 ${view.items.length} 个群组（未中断任务服务）`
      : `已实时读取 ${view.items.length} 个群组并写入缓存`,
    "success"
  );
}

async function fetchMembers(chatId, refresh = false) {
  const accountId = $("lookup-account").value || "default";
  const { data, cache, cachedAt } = await apiWithMeta("telegram/members", {
    account_id: accountId,
    chat_id: chatId,
    refresh,
  });
  const view = {
    mode: "members",
    itemKind: "user",
    accountId,
    chatId,
    title: `群组 ${chatId} 的成员列表（前 200 位）`,
    items: data.items || [],
    cache,
    cachedAt: cachedAt || Date.now(),
  };
  saveLookupEntry(accountId, `members:${chatId}`, view);
  $("lookup-filter").value = "";
  renderLookupView(view);
  toast(
    cache === "HIT"
      ? `已从缓存加载 ${view.items.length} 位成员（未中断任务服务）`
      : `已实时读取 ${view.items.length} 位成员并写入缓存`,
    "success"
  );
}

function quickAddGroupFromLookup(item, accountId) {
  const existing = [...$("group-list").querySelectorAll(".record")].find(
    (box) => box.querySelector('[data-key="id"]')?.value.trim() === String(item.id)
  );
  navigate("groups");
  if (existing) {
    existing.classList.remove("is-collapsed");
    existing.scrollIntoView({ behavior: "smooth", block: "center" });
    toast(`群组 ${item.name || item.id} 已在监控列表中，已为你定位。`, "info");
    return;
  }
  const box = addGroup(
    {
      id: item.id,
      name: item.name || "",
      account_id: accountId || "default",
      enabled: true,
    },
    true
  );
  markDirty("groups", true);
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  toast(`已预填群组「${item.name || item.id}」，核对后请点击保存。`, "success");
}

function quickAddCheckinFromLookup(item, accountId) {
  navigate("checkins");
  const box = addCheckin(
    {
      name: item.name ? `${item.name}签到` : "",
      chat_id: item.id,
      account_id: accountId || "default",
      enabled: true,
    },
    true
  );
  markDirty("checkins", true);
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  toast(`已预填签到群组「${item.name || item.id}」，补充命令后请点击保存。`, "success");
}

async function quickWatchUserFromLookup(userItem) {
  const targetGroupId = await modalPickGroup(userItem);
  if (!targetGroupId) return;
  const targetBox = [...$("group-list").querySelectorAll(".record")].find(
    (box) => box.querySelector('[data-key="id"]')?.value.trim() === String(targetGroupId)
  );
  if (!targetBox) {
    toast("未找到对应群组卡片", "error");
    return;
  }
  const input = targetBox.querySelector('[data-key="watch_users"]');
  const existing = input.value
    .replaceAll("，", ",")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  if (!existing.includes(String(userItem.id))) {
    existing.push(String(userItem.id));
    input.value = existing.join(", ");
    markDirty("groups", true);
  }
  navigate("groups");
  targetBox.classList.remove("is-collapsed");
  targetBox.scrollIntoView({ behavior: "smooth", block: "center" });
  toast(`已将 ${userItem.name || userItem.id} 加入关注用户，请点击保存生效。`, "success");
}

/* ---------- Dynamic Record Cards (Groups & Check-ins) ---------- */
function inputField(container, key, label, value, help, type = "text", options = {}) {
  const wrapper = document.createElement("label");
  if (options.span2) wrapper.classList.add("span-2");
  const titleSpan = document.createElement("span");
  titleSpan.textContent = label;
  wrapper.append(titleSpan);

  let control;
  if (type === "select") {
    control = document.createElement("select");
    control.dataset.key = key;
    for (const opt of options.choices || []) {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.label;
      control.append(o);
    }
    control.value = value ?? (options.choices?.[0]?.value || "");
  } else {
    control = document.createElement("input");
    control.dataset.key = key;
    control.type = type;
    if (options.placeholder) control.placeholder = options.placeholder;
    if (options.list) control.setAttribute("list", options.list);
    if (type === "checkbox") {
      wrapper.classList.add("check");
      control.checked = Boolean(value);
    } else {
      control.value = value ?? "";
    }
  }
  wrapper.append(control);
  if (help) {
    const small = document.createElement("small");
    small.textContent = help;
    wrapper.append(small);
  }
  container.append(wrapper);

  if (type === "checkbox") addStateToggle(control);
  if (["id", "chat_id"].includes(key)) addIdentityCheck(control, "group");
  if (["watch_users", "responder_id"].includes(key)) addIdentityCheck(control, "user");
  return control;
}

function addIdentityCheck(input, kind) {
  const control = document.createElement("div");
  control.className = "identity-control";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn-secondary btn-sm";
  button.textContent = "核对 ID";
  const result = document.createElement("small");

  button.addEventListener("click", async () => {
    const ids = input.value
      .replaceAll("，", ",")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!ids.length) {
      toast("请先填写 ID", "error");
      return;
    }
    button.disabled = true;
    result.textContent = "正在查询…";
    try {
      const accountId =
        input.closest(".record")?.querySelector('[data-key="account_id"]')?.value ||
        $("lookup-account").value ||
        "default";
      const { data, cache } = await apiWithMeta("telegram/identify", {
        account_id: accountId,
        kind,
        ids,
      });
      const text = data.items
        .map((item) =>
          item.error
            ? `${item.id}：${item.error}`
            : `${item.id}：${item.name || "（无名称）"}${item.username ? ` · @${item.username}` : ""}`
        )
        .join("；");
      result.textContent = cache === "HIT" ? `${text}（来自缓存）` : text;
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  input.replaceWith(control);
  control.append(input, button, result);
}

function accountSelect(container, key, label, selected) {
  const choices = (state.config.accounts || [{ id: "default", name: "默认账号" }]).map((a) => ({
    value: a.id,
    label: a.name,
  }));
  return inputField(
    container,
    key,
    label,
    selected || "default",
    "使用此账号访问目标群组或执行任务。",
    "select",
    { choices }
  );
}

function createRecordCard({ containerId, pageKey, title, subtitleId, enabled, accountId, original, isNew = false }) {
  const box = document.createElement("div");
  box.className = `record${isNew ? " is-new" : ""}`;
  box._original = structuredClone(original);

  const heading = document.createElement("div");
  heading.className = "record-heading";

  const titleWrap = document.createElement("div");
  titleWrap.className = "record-title-wrap";

  const chevron = createSvg(ICONS.chevron, "record-chevron");
  const titleEl = document.createElement("span");
  titleEl.className = "record-title";
  titleEl.textContent = title;

  const idPill = document.createElement("span");
  idPill.className = "mono-id";
  idPill.textContent = subtitleId ? String(subtitleId) : "未填 ID";

  const stateChip = document.createElement("span");
  stateChip.className = `badge-chip ${enabled ? "ok" : "neutral"}`;
  stateChip.textContent = enabled ? "已启用" : "已停用";

  const accName =
    (state.config.accounts || []).find((a) => a.id === (accountId || "default"))?.name || "默认账号";
  const accChip = document.createElement("span");
  accChip.className = "badge-chip info";
  accChip.textContent = accName;

  titleWrap.append(chevron, titleEl, idPill, stateChip, accChip);

  const headActions = document.createElement("div");
  headActions.className = "record-head-actions";

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn-danger-soft btn-sm";
  remove.textContent = "删除";
  remove.addEventListener("click", async (event) => {
    event.stopPropagation();
    const ok = await modalConfirm("删除配置项", `确认移除「${titleEl.textContent}」吗？点击保存后生效。`, {
      confirmText: "移除",
      danger: true,
    });
    if (!ok) return;
    box.remove();
    markDirty(pageKey, true);
    renderEmptyListHint(containerId, pageKey);
  });

  headActions.append(remove);
  heading.append(titleWrap, headActions);

  heading.addEventListener("click", () => {
    box.classList.toggle("is-collapsed");
  });

  const body = document.createElement("div");
  body.className = "record-body";

  const grid = document.createElement("div");
  grid.className = "record-grid";
  body.append(grid);
  box.append(heading, body);

  // Remove empty state placeholder if present
  const container = $(containerId);
  container.querySelector(".empty-state")?.remove();
  container.append(box);

  box.addEventListener("input", () => markDirty(pageKey, true));
  box.addEventListener("change", () => markDirty(pageKey, true));

  return { box, body, grid, titleEl, idPill, stateChip, accChip };
}

function renderEmptyListHint(containerId, pageKey) {
  const container = $(containerId);
  if (container.querySelector(".record")) return;
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent =
    pageKey === "groups"
      ? "还没有配置监控群组。可点击右上角「＋ 添加群组」，或在「查找群组与 ID」页一键添加。"
      : "还没有配置定时签到任务。可点击右上角「＋ 添加签到」，或在「查找群组与 ID」页一键创建。";
  container.replaceChildren(empty);
}

function addGroup(group = {}, isNew = false) {
  const enabledVal = group.enabled !== false;
  const { box, body, grid, titleEl, idPill, stateChip, accChip } = createRecordCard({
    containerId: "group-list",
    pageKey: "groups",
    title: group.name || "未命名群组",
    subtitleId: group.id || "",
    enabled: enabledVal,
    accountId: group.account_id,
    original: group,
    isNew,
  });

  const legacyReportFilter =
    group.report_filter_enabled === undefined &&
    Boolean((group.report_keywords || []).length || group.report_images || group.report_links);

  const accSel = accountSelect(grid, "account_id", "负责账号", group.account_id);
  const enabledInput = inputField(
    grid,
    "enabled",
    "启用监控",
    enabledVal,
    "启用后才会读取该群消息并参与日报。",
    "checkbox"
  );
  const idInput = inputField(
    grid,
    "id",
    "群组 ID",
    group.id,
    "支持从缓存群组下拉选择，请保留 -100 等负号。",
    "text",
    { placeholder: "-1001234567890", list: "cached-dialogs-list" }
  );
  const nameInput = inputField(grid, "name", "备注名称", group.name, "用于提醒消息中区分来源群组。", "text", {
    placeholder: "例如 核心交流群",
  });
  inputField(
    grid,
    "watch_users",
    "关注用户 ID",
    (group.watch_users || []).join(", "),
    "多个数字 ID 用英文或中文逗号分隔，留空不指定关注用户。",
    "text",
    { span2: true, placeholder: "例如 12345678, 87654321" }
  );
  inputField(grid, "notify_all", "全群消息提醒", group.notify_all, "打开后该群收到的每条新消息都会实时推送。", "checkbox");
  inputField(
    grid,
    "daily_summary",
    "参与每日日报",
    group.daily_summary !== false,
    "汇总前一日消息，由「关注-日报机器人」发送。",
    "checkbox"
  );

  // Collapsible custom filter subpanel
  const subpanel = document.createElement("div");
  const filterEnabled = group.report_filter_enabled === true || legacyReportFilter;
  subpanel.className = `record-subpanel${filterEnabled ? "" : " is-inactive"}`;

  const filterToggle = inputField(
    subpanel,
    "report_filter_enabled",
    "启用自定义机器人筛选分流",
    filterEnabled,
    "关闭时关注用户的每条消息由「关注-日报机器人」推送；开启后仅命中下方规则的消息由「自定义机器人」推送并附带原消息。",
    "checkbox"
  );

  const subFields = document.createElement("div");
  subFields.className = "subpanel-fields";
  inputField(
    subFields,
    "report_keywords",
    "筛选关键词",
    (group.report_keywords || []).join(", "),
    "多个关键词用英文或中文逗号分隔。",
    "text",
    { span2: true, placeholder: "例如 公告, 上线, 抽奖" }
  );
  inputField(
    subFields,
    "report_match_mode",
    "关键词匹配方式",
    group.report_match_mode || "any",
    "决定多个关键词是命中任一即可，还是必须同时包含。",
    "select",
    {
      choices: [
        { value: "any", label: "命中任一关键词 (any)" },
        { value: "all", label: "同时包含全部关键词 (all)" },
      ],
    }
  );
  inputField(
    subFields,
    "report_images",
    "命中图片消息",
    group.report_images,
    "关注用户发送图片时触发自定义机器人提醒。",
    "checkbox"
  );
  inputField(
    subFields,
    "report_links",
    "命中链接消息",
    group.report_links,
    "关注用户发送包含链接的消息时触发自定义机器人提醒。",
    "checkbox"
  );
  subpanel.append(subFields);
  body.append(subpanel);

  filterToggle.addEventListener("change", () => {
    subpanel.classList.toggle("is-inactive", !filterToggle.checked);
  });

  // Live header sync
  nameInput.addEventListener("input", () => {
    titleEl.textContent = nameInput.value.trim() || "未命名群组";
  });
  idInput.addEventListener("input", () => {
    idPill.textContent = idInput.value.trim() || "未填 ID";
  });
  enabledInput.addEventListener("change", () => {
    stateChip.className = `badge-chip ${enabledInput.checked ? "ok" : "neutral"}`;
    stateChip.textContent = enabledInput.checked ? "已启用" : "已停用";
  });
  accSel.addEventListener("change", () => {
    const label = accSel.selectedOptions[0]?.textContent || "默认账号";
    accChip.textContent = label;
  });

  // Footer with test button
  const footer = document.createElement("div");
  footer.className = "record-footer";
  const test = document.createElement("button");
  test.type = "button";
  test.className = "btn-secondary btn-sm group-test";
  test.textContent = "测试通知与模板";
  test.addEventListener("click", async () => {
    let payload;
    try {
      payload = groupValues(box);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    const ok = await modalConfirm(
      "发送群组通知测试",
      `将向已配置的通知机器人发送「${payload.name || payload.id}」的模拟消息，用于检查两种通知模板效果。继续吗？`,
      { confirmText: "立即发送" }
    );
    if (!ok) return;
    test.disabled = true;
    try {
      toast((await api("group-test", payload)).message, "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      test.disabled = false;
    }
  });
  footer.append(test);
  body.append(footer);
  return box;
}

function addCheckin(job = {}, isNew = false) {
  const enabledVal = job.enabled !== false;
  const { box, body, grid, titleEl, idPill, stateChip, accChip } = createRecordCard({
    containerId: "checkin-list",
    pageKey: "checkins",
    title: job.name || "未命名签到任务",
    subtitleId: job.at ? `${job.at} · ${job.chat_id || "未填群 ID"}` : job.chat_id || "",
    enabled: enabledVal,
    accountId: job.account_id,
    original: job,
    isNew,
  });

  const accSel = accountSelect(grid, "account_id", "负责账号", job.account_id);
  const enabledInput = inputField(
    grid,
    "enabled",
    "启用签到",
    enabledVal,
    "启用后才会按设定时间自动发送签到命令。",
    "checkbox"
  );
  const nameInput = inputField(
    grid,
    "name",
    "任务唯一名称",
    job.name,
    "同一名称每天只尝试一次；修改名称视为新任务。",
    "text",
    { placeholder: "例如 某站每日签到" }
  );
  const chatInput = inputField(
    grid,
    "chat_id",
    "签到群组 ID",
    job.chat_id,
    "支持从缓存群组下拉选择，也可点击核对 ID。",
    "text",
    { placeholder: "-1001234567890", list: "cached-dialogs-list" }
  );
  const atInput = inputField(grid, "at", "每日执行时间", job.at || "09:00", "按运行设置中的时区触发。", "time");
  inputField(
    grid,
    "command",
    "签到命令",
    job.command || "/checkin",
    "请以目标群内签到机器人的帮助说明为准。",
    "text"
  );
  inputField(
    grid,
    "responder_id",
    "回复机器人 ID（可选）",
    job.responder_id,
    "留空时仅通知命令已发送，不等待机器人回复。",
    "text",
    { placeholder: "数字用户/机器人 ID" }
  );
  inputField(
    grid,
    "timeout_seconds",
    "等待回复秒数",
    job.timeout_seconds || 60,
    "范围 1–600；超时会标记为等待超时。",
    "number"
  );
  inputField(
    grid,
    "success_pattern",
    "成功回复正则（可选）",
    job.success_pattern,
    "例如：签到成功|已签到；留空不校验成功文本。",
    "text"
  );
  inputField(
    grid,
    "failure_pattern",
    "失败回复正则（可选）",
    job.failure_pattern,
    "例如：签到失败|无权限；留空不校验失败文本。",
    "text"
  );
  inputField(
    grid,
    "require_reply",
    "仅匹配回复本次命令的消息",
    job.require_reply !== false,
    "推荐开启，避免把群内其他人的成功回复误判为你的签到结果。",
    "checkbox",
    { span2: true }
  );

  const syncHeaderMeta = () => {
    const atVal = atInput.value || "09:00";
    const cid = chatInput.value.trim() || "未填群 ID";
    idPill.textContent = `${atVal} · ${cid}`;
  };
  nameInput.addEventListener("input", () => {
    titleEl.textContent = nameInput.value.trim() || "未命名签到任务";
  });
  chatInput.addEventListener("input", syncHeaderMeta);
  atInput.addEventListener("input", syncHeaderMeta);
  enabledInput.addEventListener("change", () => {
    stateChip.className = `badge-chip ${enabledInput.checked ? "ok" : "neutral"}`;
    stateChip.textContent = enabledInput.checked ? "已启用" : "已停用";
  });
  accSel.addEventListener("change", () => {
    accChip.textContent = accSel.selectedOptions[0]?.textContent || "默认账号";
  });

  const footer = document.createElement("div");
  footer.className = "record-footer";
  const test = document.createElement("button");
  test.type = "button";
  test.className = "btn-secondary btn-sm";
  test.textContent = "测试签到";
  test.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    if (!name) {
      toast("请先填写任务名称", "error");
      return;
    }
    const unsavedNotice = dirtyPages.checkins ? "（提示：测试将使用已保存的配置，如有修改请先保存。）" : "";
    const ok = await modalConfirm(
      "真实发送签到测试",
      `将使用已保存的任务「${name}」向目标群真实发送签到命令并等待回复，不会占用当天正式签到次数。${unsavedNotice}继续吗？`,
      { confirmText: "发送测试签到" }
    );
    if (!ok) return;
    test.disabled = true;
    try {
      toast(
        (await api("checkin-test", { name, account_id: accSel.value })).message,
        "success"
      );
    } catch (error) {
      toast(error.message, "error");
    } finally {
      test.disabled = false;
    }
  });
  footer.append(test);
  body.append(footer);
  return box;
}

function groupValues(box) {
  const row = { ...box._original };
  box.querySelectorAll("[data-key]").forEach((input) => {
    row[input.dataset.key] = input.type === "checkbox" ? input.checked : input.value.trim();
  });
  return {
    ...row,
    id: integer(row.id, "群组 ID"),
    watch_users: row.watch_users
      .replaceAll("，", ",")
      .split(",")
      .filter((x) => x.trim())
      .map((x) => integer(x.trim(), "关注用户 ID")),
    report_keywords: row.report_keywords
      .replaceAll("，", ",")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean),
    report_match_mode: (row.report_match_mode || "any").trim().toLowerCase() || "any",
    report_images: Boolean(row.report_images),
    report_links: Boolean(row.report_links),
  };
}

function records(id) {
  return [...$(id).querySelectorAll(".record")].map((box) => {
    const values = { ...box._original };
    box.querySelectorAll("[data-key]").forEach((input) => {
      values[input.dataset.key] = input.type === "checkbox" ? input.checked : input.value.trim();
    });
    return values;
  });
}

function bindListSearchAndToggle(searchId, toggleId, listId) {
  const search = $(searchId);
  const toggle = $(toggleId);
  let collapsedAll = false;

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    for (const card of $(listId).querySelectorAll(".record")) {
      const text = card.querySelector(".record-heading")?.textContent?.toLowerCase() || "";
      card.hidden = Boolean(q && !text.includes(q));
    }
  });

  toggle.addEventListener("click", () => {
    collapsedAll = !collapsedAll;
    for (const card of $(listId).querySelectorAll(".record")) {
      card.classList.toggle("is-collapsed", collapsedAll);
    }
    toggle.textContent = collapsedAll ? "全部展开" : "全部折叠";
  });
}
bindListSearchAndToggle("group-search", "toggle-all-groups", "group-list");
bindListSearchAndToggle("checkin-search", "toggle-all-checkins", "checkin-list");

/* ---------- AI Models Cache ---------- */
function getCachedAiModels(apiUrl) {
  if (!apiUrl) return null;
  if (aiModelsMem.has(apiUrl)) return aiModelsMem.get(apiUrl);
  const stored = readSessionJson(STORAGE_KEYS.aiModels, {});
  if (stored[apiUrl]) {
    aiModelsMem.set(apiUrl, stored[apiUrl]);
    return stored[apiUrl];
  }
  return null;
}

function saveCachedAiModels(apiUrl, entry) {
  if (!apiUrl) return;
  aiModelsMem.set(apiUrl, entry);
  const stored = readSessionJson(STORAGE_KEYS.aiModels, {});
  stored[apiUrl] = entry;
  writeSessionJson(STORAGE_KEYS.aiModels, stored);
}

function setModelOptions(models, selected = "") {
  const select = $("summary-model-select");
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = models.length ? `请选择模型（共 ${models.length} 个）` : "请先点击“读取模型”";
  select.append(placeholder);

  for (const model of models) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.append(option);
  }
  if (selected && !models.includes(selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = `${selected}（当前配置）`;
    select.append(option);
  }
  select.value = selected || "";
}

function aiFormValues(refresh = false) {
  return {
    api_url: $("summary-form").elements.api_url.value.trim(),
    model: $("summary-model-manual").value.trim() || $("summary-model-select").value,
    api_key: $("ai-form").elements.SUMMARY_API_KEY.value.trim(),
    refresh,
  };
}

async function loadAiModels(refresh = false) {
  const payload = aiFormValues(refresh);
  const { data, cache, cachedAt } = await apiWithMeta("ai/models", payload);
  const models = data.models || [];
  const entry = { models, cache, cachedAt: cachedAt || Date.now() };
  saveCachedAiModels(payload.api_url, entry);
  setModelOptions(models, $("summary-model-manual").value.trim());
  setCachePill($("ai-models-cache-badge"), cache, entry.cachedAt);
  if (!models.length) {
    toast("接口返回的模型列表为空", "error");
  } else {
    toast(
      cache === "HIT"
        ? `已从缓存加载 ${models.length} 个模型`
        : `已实时拉取 ${models.length} 个模型并缓存`,
      "success"
    );
  }
}

/* ---------- Backups & Cache Stats ---------- */
async function loadBackups() {
  const data = await api("backups");
  backupsLoaded = true;
  const form = $("backup-schedule-form");
  form.elements.enabled.checked = Boolean(data.schedule.enabled);
  syncStateToggle(form.elements.enabled);
  form.elements.at.value = data.schedule.at;

  const tz = state?.config?.timezone || "Asia/Shanghai";
  const keepDays = data.retention_days || 7;
  $("backup-next").textContent = data.next
    ? `已启用，每日 ${data.schedule.at}（${tz}，保留 ${keepDays} 天）；下次执行：${new Date(data.next).toLocaleString(undefined, { timeZone: tz })}`
    : `自动备份已关闭（手动备份同样仅保留最近 ${keepDays} 天）。`;

  const summary = $("backup-summary");
  if (summary) {
    const totalSize = data.total_size ?? data.items.reduce((acc, item) => acc + (item.size || 0), 0);
    summary.textContent = `共 ${data.items.length} 份 · 占用 ${formatBytes(totalSize)}`;
    summary.hidden = false;
  }

  const list = $("backup-list");
  list.replaceChildren();
  if (!data.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无历史备份文件。";
    list.append(empty);
    return;
  }
  for (const item of data.items) {
    const row = document.createElement("div");
    row.className = `backup-item${item.size === 0 ? " is-corrupt" : ""}`;

    const link = document.createElement("a");
    link.href = `/api/backups/${encodeURIComponent(item.name)}`;
    link.className = "backup-item-link";
    link.title = `点击下载 · 创建时间：${new Date(item.created_at).toLocaleString()}`;

    const name = document.createElement("span");
    name.className = "backup-item-name";
    name.textContent = item.name;

    const size = document.createElement("span");
    size.className = "backup-item-size";
    size.textContent = item.size === 0 ? "0 B · 异常" : formatBytes(item.size);

    link.append(name, size);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "icon-btn backup-delete-btn";
    delBtn.title = "删除此备份";
    delBtn.setAttribute("aria-label", `删除备份 ${item.name}`);
    delBtn.append(createSvg(ICONS.close));
    delBtn.addEventListener("click", async () => {
      const ok = await modalConfirm(
        "删除备份文件",
        `确认从服务器删除备份「${item.name}」（${formatBytes(item.size)}）吗？此操作不可恢复。`,
        { confirmText: "确认删除", danger: true }
      );
      if (!ok) return;
      delBtn.disabled = true;
      try {
        const res = await api("backups/delete", { name: item.name });
        await loadBackups();
        toast(res.message, "success");
      } catch (error) {
        toast(error.message, "error");
        delBtn.disabled = false;
      }
    });

    row.append(link, delBtn);
    list.append(row);
  }
}

async function loadCacheStats() {
  const data = await api("cache");
  cacheStatsLoaded = true;
  $("cache-stat-dialogs").textContent = String(data.telegram?.dialogs ?? 0);
  $("cache-stat-members").textContent = String(data.telegram?.members ?? 0);
  $("cache-stat-entities").textContent = String(
    (data.telegram?.entities ?? 0) + (data.telegram?.resolve ?? 0)
  );
  $("cache-stat-ai").textContent = String(data.ai_models ?? 0);
}

/* ---------- Main State Load ---------- */
function renderGroupsList() {
  const groups = state.config.groups || [];
  $("group-list").replaceChildren();
  if (!groups.length) {
    renderEmptyListHint("group-list", "groups");
  } else {
    groups.forEach((group) => addGroup(group, false));
  }
  markDirty("groups", false);
}

function renderCheckinsList() {
  const checkins = state.config.checkins || [];
  $("checkin-list").replaceChildren();
  if (!checkins.length) {
    renderEmptyListHint("checkin-list", "checkins");
  } else {
    checkins.forEach((job) => addCheckin(job, false));
  }
  markDirty("checkins", false);
}

async function load({ preserveDirty = false } = {}) {
  state = await api("state");
  lastSyncAt = Date.now();
  $("login").hidden = true;
  $("dashboard").hidden = false;

  const syncMeta = $("sync-meta");
  syncMeta.textContent = `同步于 ${formatTime(lastSyncAt)}`;
  syncMeta.hidden = false;

  const config = state.config;
  const summary = config.summary || {};
  const serviceActive = state.service === "active";

  const setStatusPill = (id, active, onText, offText) => {
    const el = $(id);
    el.className = `status-pill${active ? "" : " off"}`;
    const spans = el.querySelectorAll("span");
    if (spans[1]) spans[1].textContent = active ? onText : offText;
  };
  setStatusPill("service-badge", serviceActive, "任务运行中", "任务未运行");
  setStatusPill("service-panel-dot", serviceActive, "在线", "离线");
  $("service-state-card").textContent = serviceActive ? "运行中" : "未运行";

  const groups = config.groups || [];
  const checkins = config.checkins || [];
  const enabledGroups = groups.filter((g) => g.enabled !== false).length;
  const enabledCheckins = checkins.filter((c) => c.enabled !== false).length;

  $("count-groups").textContent = String(groups.length);
  $("count-groups-sub").textContent = `已启用 ${enabledGroups} 个监控群组`;
  $("nav-count-groups").textContent = String(groups.length);

  $("count-checkins").textContent = String(checkins.length);
  $("count-checkins-sub").textContent = `已启用 ${enabledCheckins} 个签到任务`;
  $("nav-count-checkins").textContent = String(checkins.length);

  $("summary-time").textContent = summary.enabled === false ? "已关闭" : summary.at || "00:05";
  $("timezone-label").textContent = config.timezone || "Asia/Shanghai";

  fill("application-form", { TG_API_ID: state.credentials.TG_API_ID || "", TG_API_HASH: "" });
  fill("monitor-notification-form", {
    TG_MONITOR_NOTIFY_CHAT_ID:
      state.credentials.TG_MONITOR_NOTIFY_CHAT_ID || state.credentials.TG_NOTIFY_CHAT_ID || "",
    TG_MONITOR_BOT_TOKEN: "",
  });
  fill("report-notification-form", {
    TG_REPORT_NOTIFY_CHAT_ID:
      state.credentials.TG_REPORT_NOTIFY_CHAT_ID || state.credentials.TG_NOTIFY_CHAT_ID || "",
    TG_REPORT_BOT_TOKEN: "",
  });

  for (const [formId, key] of [
    ["application-form", "TG_API_HASH"],
    ["monitor-notification-form", "TG_MONITOR_BOT_TOKEN"],
    ["report-notification-form", "TG_REPORT_BOT_TOKEN"],
    ["ai-form", "SUMMARY_API_KEY"],
  ]) {
    $(formId).elements.namedItem(key).placeholder = state.configured[key]
      ? "已保存 · 留空保留原值"
      : "尚未设置";
  }

  fill("summary-form", {
    enabled: true,
    at: "00:05",
    max_messages: 500,
    max_chars: 30000,
    ...summary,
  });

  // Preserve cached AI model list instead of wiping it!
  const cachedModels = getCachedAiModels(summary.api_url || "");
  if (cachedModels) {
    setModelOptions(cachedModels.models, summary.model || "");
    setCachePill($("ai-models-cache-badge"), cachedModels.cache, cachedModels.cachedAt);
  } else {
    setModelOptions([], summary.model || "");
  }

  fill("system-form", {
    timezone: config.timezone || "Asia/Shanghai",
    retention_days: config.retention_days || 30,
    queue_size: (config.notifications || {}).queue_size || 500,
  });

  renderAccounts();
  renderReadiness();
  renderRuns();

  if (!preserveDirty || !dirtyPages.groups) renderGroupsList();
  if (!preserveDirty || !dirtyPages.checkins) renderCheckinsList();

  restoreDatalistFromSession();

  if (currentPage === "settings") {
    await Promise.all([loadBackups().catch(() => {}), loadCacheStats().catch(() => {})]);
  }
}

async function saveConfig(change, { clearDirtyPage = null } = {}) {
  const config = structuredClone(state.config);
  change(config);
  const result = await api("config", { config, revision: state.revision });
  if (clearDirtyPage) markDirty(clearDirtyPage, false);
  await load({ preserveDirty: true });
  toast(result.message, "success");
}

/* ---------- Wire Static Switches & Controls ---------- */
for (const input of document.querySelectorAll(
  '#summary-form input[type="checkbox"], #ai-form input[type="checkbox"], #backup-schedule-form input[type="checkbox"]'
)) {
  addStateToggle(input);
}

for (const button of document.querySelectorAll("[data-page]")) {
  button.addEventListener("click", () => navigate(button.dataset.page));
}

for (const btn of document.querySelectorAll("[data-runs-filter]")) {
  btn.addEventListener("click", () => {
    currentRunsFilter = btn.dataset.runsFilter;
    document.querySelectorAll("[data-runs-filter]").forEach((tab) => {
      const active = tab === btn;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    renderRuns();
  });
}

/* ---------- Bind Forms & Buttons ---------- */
bindForm("login-form", async (data, form) => {
  const result = await api("login", data);
  csrf = result.csrf;
  $("logged-user").textContent = result.username;
  $("user-avatar").textContent = (result.username || "A").slice(0, 1).toUpperCase();
  fill("admin-form", { username: result.username });
  form.reset();
  const initialHash = location.hash.replace(/^#/, "");
  navigate(VALID_PAGES.has(initialHash) ? initialHash : "overview", { pushHash: true });
  await load();
});

bindButton("logout", async () => {
  await api("logout", {});
  clearBrowserCaches();
  location.reload();
});

bindButton("refresh", async (btn) => {
  const icon = btn.querySelector(".btn-icon");
  icon?.classList.add("is-spinning");
  try {
    await load({ preserveDirty: true });
    if (currentPage === "settings") {
      await Promise.all([loadBackups(), loadCacheStats()]);
    }
    toast("最新状态已同步", "success");
  } finally {
    icon?.classList.remove("is-spinning");
  }
});

for (const [id, section] of [
  ["application-form", "application"],
  ["monitor-notification-form", "monitor"],
  ["report-notification-form", "report"],
  ["ai-form", "ai"],
]) {
  bindForm(id, async (data, form) => {
    if (section === "ai") data.clear_key = form.elements.clear_key.checked;
    const result = await api("credentials/" + section, data);
    form.querySelectorAll("input[type=password]").forEach((input) => (input.value = ""));
    if (section === "ai") {
      form.elements.clear_key.checked = false;
      syncStateToggle(form.elements.clear_key);
      aiModelsMem.clear();
    }
    await load({ preserveDirty: true });
    toast(result.message, "success");
  });
}

for (const [id, action] of [
  ["send-code-form", "send-code"],
  ["verify-code-form", "verify-code"],
  ["password-form", "password"],
  ["members-form", "members"],
  ["history-form", "history"],
  ["group-resolve-form", "resolve"],
  ["user-resolve-form", "resolve"],
]) {
  bindForm(id, async (data, form) => {
    if (action === "members") {
      await fetchMembers(data.chat_id, false);
      return;
    }
    data.account_id =
      action === "history"
        ? $("history-account").value
        : action === "resolve"
          ? $("lookup-account").value
          : $("login-account").value;

    const { data: result, cache, cachedAt } = await apiWithMeta("telegram/" + action, data);
    if (action === "resolve") {
      const view = {
        mode: "resolve",
        itemKind: result.kind,
        accountId: data.account_id,
        reference: data.reference,
        kind: data.kind,
        title: result.kind === "group" ? "群组解析结果" : "用户解析结果",
        items: [{ id: result.id, name: result.name, username: result.username }],
        cache,
        cachedAt: cachedAt || Date.now(),
      };
      $("lookup-filter").value = "";
      renderLookupView(view);
      toast(
        `${result.kind === "group" ? "群组" : "用户"} ID：${result.id}${cache === "HIT" ? "（来自缓存）" : ""}`,
        "success"
      );
    } else if (action === "history") {
      $("history-status").textContent = result.message;
      toast(result.message, "success");
    } else {
      $("telegram-status").textContent = result.message;
      $("password-form").hidden = result.status !== "password";
      if (action === "send-code" && result.status === "code") startLoginCountdown();
      if (result.status === "authorized") {
        stopLoginCountdown();
        form.reset();
        await load({ preserveDirty: true });
      } else if (action !== "send-code") {
        form.reset();
      }
      toast(result.message, "success");
    }
  });
}

bindButton("dialogs", () => fetchDialogs(false));
bindButton("dialogs-refresh", () => fetchDialogs(true));

bindButton("lookup-refresh-current", async () => {
  if (!currentLookupView) return;
  if (currentLookupView.mode === "dialogs") {
    await fetchDialogs(true);
  } else if (currentLookupView.mode === "members") {
    await fetchMembers(currentLookupView.chatId, true);
  } else if (currentLookupView.mode === "resolve") {
    const { data: result, cache, cachedAt } = await apiWithMeta("telegram/resolve", {
      account_id: currentLookupView.accountId,
      kind: currentLookupView.kind,
      reference: currentLookupView.reference,
      refresh: true,
    });
    renderLookupView({
      ...currentLookupView,
      items: [{ id: result.id, name: result.name, username: result.username }],
      cache,
      cachedAt: cachedAt || Date.now(),
    });
    toast("已强制刷新解析结果", "success");
  }
});

$("lookup-clear-view").addEventListener("click", () => {
  currentLookupView = null;
  $("lookup-results-panel").hidden = true;
  $("lookup-cache-badge").hidden = true;
});

$("lookup-filter").addEventListener("input", () => {
  if (currentLookupView) renderLookupView(currentLookupView);
});

bindButton("monitor-notify-test", async () =>
  toast((await api("notify-test", { role: "monitor" })).message, "success")
);
bindButton("report-notify-test", async () =>
  toast((await api("notify-test", { role: "report" })).message, "success")
);
bindButton("fetch-models", () => loadAiModels(false));
bindButton("refresh-models", () => loadAiModels(true));
bindButton("test-ai", async () => toast((await api("ai/test", aiFormValues())).message, "success"));

bindButton("test-summary", async () => {
  const ok = await modalConfirm(
    "发送测试日报",
    "将发送前一自然日的测试日报到通知机器人，且不会记录为正式日报。继续吗？",
    { confirmText: "立即发送" }
  );
  if (!ok) return;
  toast((await api("summary-test", {})).message, "success");
});

bindButton("update-project", async (button) => {
  if (button.dataset.docker === "true") {
    const message =
      "请在宿主机项目目录执行：docker compose up -d --build；或输入 bu，选择 6. 更新项目。";
    $("service-output-wrap").hidden = false;
    $("service-output").textContent = message;
    toast(message, "info");
    return;
  }
  const ok = await modalConfirm(
    "更新 TG-Butler",
    "将拉取最新代码并重启服务，现有配置、凭据与数据会保留。提交后页面将在 10 秒后自动刷新，继续吗？",
    { confirmText: "开始更新" }
  );
  if (!ok) return;
  const result = await api("update", {});
  $("service-output-wrap").hidden = false;
  $("service-output").textContent = result.message;
  toast("更新已开始，页面将在 10 秒后自动刷新。", "info");
  window.setTimeout(() => window.location.reload(), 10000);
});

$("copy-service-output").addEventListener("click", () =>
  copyText($("service-output").textContent || "", "日志输出")
);
$("close-service-output").addEventListener("click", () => {
  $("service-output-wrap").hidden = true;
});

bindButton("backup-create", async () => {
  const result = await api("backups/create", {});
  await loadBackups();
  toast(result.message, "success");
});

bindButton("backup-prune", async () => {
  const result = await api("backups/prune", {});
  await loadBackups();
  toast(result.message, result.removed ? "success" : "info");
});

bindButton("backup-download", async () => {
  const created = await api("backups/create", {});
  await loadBackups();
  const response = await fetch(`/api/backups/${encodeURIComponent(created.name)}`, {
    headers: { "X-CSRF-Token": csrf },
  });
  if (!response.ok) throw Error("下载备份失败");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = created.name;
  link.click();
  URL.revokeObjectURL(url);
  toast("最新备份已导出到本地。", "success");
});

bindForm("backup-schedule-form", async (_data, form) => {
  await api("backups/schedule", {
    enabled: form.elements.enabled.checked,
    at: form.elements.at.value,
  });
  await loadBackups();
  toast("备份计划已保存。", "success");
});

$("backup-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    toast("备份文件超过 20 MB 限制。", "error");
    event.target.value = "";
    return;
  }
  const ok = await modalConfirm(
    "导入并恢复备份",
    `将导入「${file.name}」并恢复其中的配置、凭据、Telegram 会话和消息记录。导入前会自动备份当前数据，管理员账号与访问设置会保留。继续吗？`,
    { confirmText: "确认恢复", danger: true }
  );
  if (!ok) {
    event.target.value = "";
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/backups/import", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: form,
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || "导入失败");
    clearBrowserCaches();
    toast(result.message, "success");
    await load();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    event.target.value = "";
  }
});

bindButton("cache-refresh-stats", async () => {
  await loadCacheStats();
  toast("缓存统计已更新", "info");
});

bindButton("cache-clear-telegram", async () => {
  const res = await api("cache/clear", { scope: "telegram" });
  try {
    sessionStorage.removeItem(STORAGE_KEYS.lookup);
  } catch {
    /* ignore */
  }
  updateCachedDialogsDatalist([]);
  currentLookupView = null;
  $("lookup-results-panel").hidden = true;
  $("lookup-cache-badge").hidden = true;
  await loadCacheStats();
  toast(res.message, "success");
});

bindButton("cache-clear-all", async () => {
  const res = await api("cache/clear", { scope: "all" });
  clearBrowserCaches();
  await loadCacheStats();
  toast(res.message, "success");
});

$("summary-model-select").addEventListener("change", (event) => {
  if (event.target.value) $("summary-model-manual").value = event.target.value;
});

bindButton("add-group", () => {
  const box = addGroup({ enabled: true }, true);
  markDirty("groups", true);
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  box.querySelector('[data-key="id"]')?.focus();
});

bindButton("add-checkin", () => {
  const box = addCheckin({ enabled: true }, true);
  markDirty("checkins", true);
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  box.querySelector('[data-key="name"]')?.focus();
});

bindButton("add-account", async () => {
  const name = await modalPrompt("添加 Telegram 账号", "账号备注名称", {
    placeholder: "例如 工作小号",
    confirmText: "添加账号",
  });
  if (!name) return;
  const accounts = [...(state.config.accounts || [])];
  let id;
  do {
    id = "acc_" + crypto.randomUUID().replaceAll("-", "");
  } while (accounts.some((account) => account.id === id));
  accounts.push({ id, name });
  await saveConfig((config) => (config.accounts = accounts));
});

bindButton("save-groups", () =>
  saveConfig(
    (config) => {
      config.groups = [...$("group-list").querySelectorAll(".record")].map(groupValues);
    },
    { clearDirtyPage: "groups" }
  )
);

bindButton("save-checkins", () =>
  saveConfig(
    (config) => {
      config.checkins = records("checkin-list").map((row) => ({
        ...row,
        chat_id: integer(row.chat_id, "群组 ID"),
        responder_id: row.responder_id ? integer(row.responder_id, "机器人 ID") : null,
        success_pattern: row.success_pattern || null,
        failure_pattern: row.failure_pattern || null,
        timeout_seconds: integer(row.timeout_seconds, "超时秒数"),
      }));
    },
    { clearDirtyPage: "checkins" }
  )
);

$("dirty-save").addEventListener("click", () => {
  if (currentPage === "groups") $("save-groups").click();
  else if (currentPage === "checkins") $("save-checkins").click();
});

$("dirty-discard").addEventListener("click", async () => {
  const ok = await modalConfirm("放弃未保存更改", "将还原为上次保存的配置，当前未保存的修改会丢失。继续吗？", {
    confirmText: "放弃更改",
    danger: true,
  });
  if (!ok) return;
  if (currentPage === "groups") renderGroupsList();
  else if (currentPage === "checkins") renderCheckinsList();
  toast("已还原为已保存的配置", "info");
});

bindForm("summary-form", (data, form) =>
  saveConfig((config) => {
    config.summary = {
      ...config.summary,
      ...data,
      enabled: form.elements.enabled.checked,
      api_url: data.api_url || null,
      model: data.model || null,
      max_messages: integer(data.max_messages, "消息数"),
      max_chars: integer(data.max_chars, "字符数"),
    };
  })
);

bindForm("system-form", (data) =>
  saveConfig((config) => {
    config.timezone = data.timezone;
    config.retention_days = integer(data.retention_days, "保留天数");
    config.notifications = {
      ...config.notifications,
      queue_size: integer(data.queue_size, "队列容量"),
    };
  })
);

bindForm("admin-form", async (data, form) => {
  if (data.password !== data.confirm_password) throw Error("两次新密码不一致");
  const result = await api("admin", data);
  form.reset();
  clearBrowserCaches();
  toast(result.message, "success");
  location.reload();
});

for (const button of document.querySelectorAll("[data-service]")) {
  button.addEventListener("click", async () => {
    const action = button.dataset.service;
    if (action === "stop") {
      const ok = await modalConfirm(
        "停止任务服务",
        "确认停止群消息监控和定时任务吗？网页管理后台将继续正常运行。",
        { confirmText: "确认停止", danger: true }
      );
      if (!ok) return;
    }
    button.disabled = true;
    try {
      const result = await api("service/" + action, {});
      $("service-output-wrap").hidden = false;
      $("service-output").textContent = result.message;
      if (action !== "logs") {
        await load({ preserveDirty: true });
        toast("服务操作已完成", "success");
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}

/* ---------- Boot ---------- */
(async () => {
  try {
    const info = await api("session");
    csrf = info.csrf;
    if (info.authenticated) {
      $("logged-user").textContent = info.username;
      $("user-avatar").textContent = (info.username || "A").slice(0, 1).toUpperCase();
      fill("admin-form", { username: info.username });
      const initialHash = location.hash.replace(/^#/, "");
      navigate(VALID_PAGES.has(initialHash) ? initialHash : "overview", { pushHash: true });
      await load();
    } else {
      showLogin();
    }
  } catch (error) {
    toast(error.message, "error");
  }
})();
