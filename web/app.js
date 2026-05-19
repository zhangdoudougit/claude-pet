/* ============================================================
 * 泡沫桌面陪伴 — web 前端控制器
 * - QWebChannel 拿到 bridge → 转发 send/switch, 监听 chunk/finished
 * - 截图: 粘贴/拖入图片 → bridge.attach_image_b64 落盘 → 发送时拼路径
 * - 模型/权限模式: header 右上角两个 <select>, 每会话独立 model
 * - vanilla JS, 无构建
 * ============================================================ */

// ---------------- state ----------------
const state = {
  bridge: null,
  conversations: [],   // [{key, kind, name, short_code, color, sub, badge, last_active_ts}]
  currentKey: null,
  theme: "warm",       // warm | glass
  dark: false,
  // 每会话独立: 流式缓冲 + 状态. 切换会话时不丢, 切回来能恢复 "思考中" 的进度
  streamBufs: {},      // {[key]: 累积中的 assistant 文本}  完成后 delete
  statuses: {},        // {[key]: "idle" | "thinking"}     默认 idle
  // 截图: 待发送附件 [{path, dataUrl}] (path = bridge 落盘后的绝对路径; dataUrl 仅 web 端缩略图用)
  attachments: [],
  // 模型 / 权限模式
  modelOptions: [],    // [{key, label}]
  permModes: [],       // [{key, label}]
  currentModel: "",
  currentPermMode: "default",
};

// ---------------- DOM refs ----------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const els = {
  body: document.body,
  convList: $("#conv-list"),
  searchInput: $("#search-input"),
  addBtn: $("#add-btn"),
  settingsBtn: $("#settings-btn"),
  memoryBtn: $("#memory-btn"),
  headerAvatar: $("#header-avatar"),
  headerTitle: $("#header-title"),
  headerSubtitle: $("#header-subtitle"),
  headerMood: $("#header-mood"),
  modelDDEl: $("#model-dd"),
  permDDEl: $("#perm-dd"),
  statusPill: $("#status-pill"),
  chatBody: $("#chat-body"),
  composerInput: $("#composer-input"),
  composerCard: null,  // 待绑
  pasteStrip: $("#paste-strip"),
  attachBtn: $("#attach-btn"),
  attachFileInput: $("#attach-file-input"),
  sendBtn: $("#send-btn"),
  moreBtn: $("#more-btn"),
};

// 两个自定义 dropdown 实例 (setupDropdowns 里初始化)
let modelDD = null;
let permDD = null;

// ---------------- helpers ----------------
function fmtRelTime(ts) {
  if (!ts) return "尚无消息";
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}-${d.getDate()}`;
}

function fmtTimeHM(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// 极简 markdown: 段落 + inline code + 代码块 + 图片 + GFM 表格
function renderMarkdown(text) {
  let s = escapeHtml(text);
  // ```code blocks``` — 先抠出来, 避免内部 | 被误判成表格
  const codeBlocks = [];
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => {
    const html = `<pre><code>${code.replace(/^\n/, "").replace(/\n$/, "")}</code></pre>`;
    codeBlocks.push(html);
    return `\x00CB${codeBlocks.length - 1}\x00`;
  });
  // `inline code`
  s = s.replace(/`([^`\n]+)`/g, (_, code) => `<code>${code}</code>`);
  // GFM 表格 — 行级扫, 抠出来占位
  const tables = [];
  s = extractTables(s, tables);
  // 图片 ![alt](path) — 简单 file:// 渲染 (user 自己粘的图)
  s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, src) => {
    let url = src;
    if (/^[a-zA-Z]:[\\/]/.test(src)) {
      url = "file:///" + src.replace(/\\/g, "/");
    } else if (src.startsWith("/")) {
      url = "file://" + src;
    }
    return `<img class="msg-img" src="${url}" alt="${escapeHtml(alt)}" />`;
  });
  // 段落 (双换行 = 新段; 单换行 = <br>)
  const paras = s.split(/\n\n+/).map((p) => {
    if (p.startsWith("<pre>") || /^\x00TBL\d+\x00$/.test(p.trim())) return p;
    return `<p>${p.replace(/\n/g, "<br>")}</p>`;
  });
  let out = paras.join("");
  // 占位还原 (代码块 + 表格, 可能被 <p> 包了, 先剥)
  out = out.replace(/<p>\s*\x00(TBL|CB)(\d+)\x00\s*<\/p>/g, (_, kind, i) =>
    kind === "TBL" ? tables[Number(i)] : codeBlocks[Number(i)]);
  out = out.replace(/\x00TBL(\d+)\x00/g, (_, i) => tables[Number(i)]);
  out = out.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[Number(i)]);
  return out;
}

// GFM 表格识别: 第 1 行任意带 |, 第 2 行是 separator (|---|---|), 后续是 body
function extractTables(s, tablesOut) {
  const lines = s.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    if (
      i + 1 < lines.length &&
      lines[i].includes("|") &&
      isTableSeparator(lines[i + 1])
    ) {
      const header = lines[i];
      const sep = lines[i + 1];
      const body = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() && lines[j].includes("|")) {
        body.push(lines[j]);
        j++;
      }
      const html = renderGfmTable(header, sep, body);
      if (html) {
        tablesOut.push(html);
        out.push(`\x00TBL${tablesOut.length - 1}\x00`);
        i = j;
        continue;
      }
    }
    out.push(lines[i]);
    i++;
  }
  return out.join("\n");
}

function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{3,}:?(\s*\|\s*:?-{3,}:?)*\s*\|?\s*$/.test(line);
}

function splitRow(line) {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|")) t = t.slice(0, -1);
  return t.split("|").map((c) => c.trim());
}

function renderGfmTable(header, sep, body) {
  const headers = splitRow(header);
  const aligns = splitRow(sep).map((c) => {
    if (c.startsWith(":") && c.endsWith(":")) return "center";
    if (c.endsWith(":")) return "right";
    if (c.startsWith(":")) return "left";
    return "";
  });
  if (!headers.length) return "";
  const th = headers.map(
    (h, i) => `<th${aligns[i] ? ` style="text-align:${aligns[i]}"` : ""}>${h}</th>`
  ).join("");
  const trs = body.map((row) => {
    const cells = splitRow(row);
    return "<tr>" + cells.map(
      (c, i) => `<td${aligns[i] ? ` style="text-align:${aligns[i]}"` : ""}>${c}</td>`
    ).join("") + "</tr>";
  }).join("");
  return `<table class="md-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
}

// ---------------- sidebar 渲染 ----------------
function renderConvList() {
  const html = state.conversations.map((c) => {
    const isSelected = c.key === state.currentKey;
    const isProj = c.kind === "project";
    const avatarHtml = isProj
      ? `<div class="avatar project-badge" style="background:${c.color || "#7C8290"};">${(c.short_code || "").slice(0, 4)}</div>`
      : `<div class="avatar chat-avatar"></div>`;
    const badgeHtml = c.badge && c.badge !== "none"
      ? `<div class="avatar-badge ${c.badge}"></div>`
      : "";
    const sub = c.sub || (c.last_active_ts ? fmtRelTime(c.last_active_ts) : "·_·  尚无最近消息");
    return `
      <div class="conv-card ${isSelected ? "selected" : ""} ${isProj ? "proj" : "chat"}"
           data-key="${c.key}">
        ${avatarHtml}
        ${badgeHtml}
        <div class="conv-text">
          <div class="conv-name">${escapeHtml(c.name)}</div>
          <div class="conv-sub">${escapeHtml(sub)}</div>
        </div>
      </div>
    `;
  }).join("");
  els.convList.innerHTML = html;

  els.convList.querySelectorAll(".conv-card").forEach((el) => {
    el.addEventListener("click", () => {
      const key = el.dataset.key;
      switchConversation(key);
    });
    // 右键: 项目卡 = 编辑/删除; 闲聊卡 = 灰色提示
    el.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const key = el.dataset.key;
      const isProj = el.classList.contains("proj");
      showConvCardMenu(key, isProj, e.clientX, e.clientY);
    });
  });
}

// 项目卡右键菜单 — 对齐老 PyQt sidebar._on_right_click 行为
function showConvCardMenu(key, isProj, x, y) {
  document.querySelectorAll(".header-dd-menu.conv-menu").forEach((m) => m.remove());

  const menu = document.createElement("div");
  menu.className = "header-dd-menu conv-menu";
  if (!isProj) {
    menu.innerHTML = `<div class="header-dd-item" style="opacity:.5;pointer-events:none">闲聊不能删除</div>`;
  } else {
    menu.innerHTML = `
      <div class="header-dd-item" data-action="edit">编辑项目</div>
      <div class="header-dd-item" data-action="delete">删除项目</div>
    `;
  }
  document.body.appendChild(menu);

  // 定位 + 防越界
  menu.style.minWidth = "160px";
  menu.style.top = y + "px";
  menu.style.left = x + "px";
  const r = menu.getBoundingClientRect();
  if (r.right > window.innerWidth - 8) {
    menu.style.left = (window.innerWidth - r.width - 8) + "px";
  }
  if (r.bottom > window.innerHeight - 8) {
    menu.style.top = (window.innerHeight - r.height - 8) + "px";
  }

  const close = () => {
    menu.remove();
    document.removeEventListener("click", onOut, true);
    document.removeEventListener("contextmenu", onOut, true);
    window.removeEventListener("resize", close);
  };
  const onOut = (e) => {
    if (menu.contains(e.target)) return;
    close();
  };
  menu.addEventListener("click", (e) => {
    const it = e.target.closest(".header-dd-item");
    if (!it) return;
    const action = it.dataset.action;
    close();
    if (!state.bridge) return;
    if (action === "edit") state.bridge.request_edit_project(key);
    else if (action === "delete") state.bridge.request_delete_project(key);
  });
  setTimeout(() => {
    document.addEventListener("click", onOut, true);
    document.addEventListener("contextmenu", onOut, true);
    window.addEventListener("resize", close);
  }, 0);
}

// ---------------- chat header 渲染 ----------------
function renderHeader() {
  const conv = state.conversations.find((c) => c.key === state.currentKey);
  if (!conv) return;
  if (conv.kind === "project") {
    els.headerAvatar.className = "header-avatar project-badge";
    els.headerAvatar.style.background = conv.color || "#7C8290";
    els.headerAvatar.textContent = (conv.short_code || "").slice(0, 4);
    els.headerTitle.textContent = conv.name;
    els.headerSubtitle.textContent = "· 项目";
  } else {
    els.headerAvatar.className = "header-avatar";
    els.headerAvatar.style.background = "";
    els.headerAvatar.textContent = "";
    els.headerTitle.textContent = "泡沫";
    els.headerSubtitle.textContent = "· 桌面伙伴";
  }
}

function setStatus(state_name) {
  const pill = els.statusPill;
  pill.className = `status-pill status-${state_name}`;
  const text = { idle: "待机", thinking: "思考中…", online: "在线" }[state_name] || state_name;
  pill.querySelector(".status-text").textContent = text;
}

// ---------------- chat body 渲染 ----------------
function addTimeDivider(label) {
  const div = document.createElement("div");
  div.className = "time-divider";
  div.innerHTML = `<span>${escapeHtml(label)}</span>`;
  els.chatBody.appendChild(div);
}

// 跨日跟踪 (B4): 渲染消息前调 maybeAddDivider(ts), 同日不重复插
let _lastMessageDay = null;

function _dayKey(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

function _dayLabel(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const today = `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
  const yesterday = new Date(now.getTime() - 86400 * 1000);
  const yKey = `${yesterday.getFullYear()}-${yesterday.getMonth() + 1}-${yesterday.getDate()}`;
  const dk = _dayKey(ts);
  let prefix;
  if (dk === today) prefix = "今天";
  else if (dk === yKey) prefix = "昨天";
  else prefix = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `${prefix} · ${fmtTimeHM(ts)}`;
}

function maybeAddDivider(ts) {
  if (!ts) ts = Date.now() / 1000;
  const dk = _dayKey(ts);
  if (_lastMessageDay !== dk) {
    addTimeDivider(_dayLabel(ts));
    _lastMessageDay = dk;
  }
}

function makeMessageRow(role, html, conv) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  let avatarHtml;
  if (role === "user") {
    avatarHtml = `<div class="msg-avatar"></div>`;
  } else {
    if (conv && conv.kind === "project") {
      avatarHtml = `<div class="msg-avatar project-badge" style="background:${conv.color || "#7C8290"};">${(conv.short_code || "").slice(0, 4)}</div>`;
    } else {
      avatarHtml = `<div class="msg-avatar"></div>`;
    }
  }
  if (role === "user") {
    row.innerHTML = `<div class="msg-bubble">${html}</div>${avatarHtml}`;
  } else {
    row.innerHTML = `${avatarHtml}<div class="msg-bubble">${html}</div>`;
  }
  return row;
}

function renderChatBody() {
  els.chatBody.innerHTML = "";
  _lastMessageDay = null;   // 重置跨日跟踪 (B4)
  // 不再无脑插 "今天 · 当前时间" — 等真有消息时按消息 ts 插
}

function appendUserMessage(text) {
  const conv = state.conversations.find((c) => c.key === state.currentKey);
  const row = makeMessageRow("user", renderMarkdown(text), conv);
  els.chatBody.appendChild(row);
  scrollToBottom();
}

function appendAssistantPlaceholder() {
  const conv = state.conversations.find((c) => c.key === state.currentKey);
  const row = makeMessageRow("pet",
    `<div class="thinking-dots"><span></span><span></span><span></span></div>`, conv);
  row.dataset.streaming = "1";
  els.chatBody.appendChild(row);
  scrollToBottom();
  return row;
}

function appendAssistantMessage(text) {
  const conv = state.conversations.find((c) => c.key === state.currentKey);
  const row = makeMessageRow("pet", renderMarkdown(text), conv);
  els.chatBody.appendChild(row);
  scrollToBottom();
}

// 流式渲染策略 (B2 — 避免 O(n²) 卡死):
// - RAF 节流: 多个 chunk 合并到下一帧渲一次, 不每个 chunk 都全量重渲
// - 长文阈值: buf 超过 STREAM_FULL_RENDER_THRESHOLD 切到纯文本 <pre> 预览
//   (renderMarkdown 跑表格/代码块/段落扫描在长文上是 O(n) ~ O(n²), 累积上千 chunk 就崩)
// - finalize 时 (message_finished) 强制一次完整 renderMarkdown, 修复格式
const STREAM_FULL_RENDER_THRESHOLD = 8 * 1024;
let _streamRafPending = false;

function updateStreamingBubble(_text) {
  // _text 这一帧不再用 — 总是从 streamBufs[currentKey] 读最新累积值, RAF 内合并
  if (_streamRafPending) return;
  _streamRafPending = true;
  requestAnimationFrame(() => {
    _streamRafPending = false;
    const row = $$(".msg-row.pet[data-streaming]").slice(-1)[0];
    if (!row) return;
    const buf = state.streamBufs[state.currentKey] || "";
    const bubble = row.querySelector(".msg-bubble");
    if (buf.length <= STREAM_FULL_RENDER_THRESHOLD) {
      bubble.innerHTML = renderMarkdown(buf);
    } else {
      // 长文预览: 只 escape + 保留换行, 不跑 markdown 解析
      bubble.innerHTML = `<pre class="stream-preview">${escapeHtml(buf)}</pre>`;
    }
    scrollToBottom();
  });
}

function finalizeStreamingBubble(fullBuf) {
  const row = $$(".msg-row.pet[data-streaming]").slice(-1)[0];
  if (!row) return;
  delete row.dataset.streaming;
  // 完成时强制一次完整 markdown 渲染 (覆盖可能存在的纯文本预览)
  if (fullBuf) {
    const bubble = row.querySelector(".msg-bubble");
    bubble.innerHTML = renderMarkdown(fullBuf);
  }
}

function scrollToBottom() {
  // 双 hop, 等 layout 完
  requestAnimationFrame(() => {
    els.chatBody.scrollTop = els.chatBody.scrollHeight;
    requestAnimationFrame(() => {
      els.chatBody.scrollTop = els.chatBody.scrollHeight;
    });
  });
}

// ---------------- 附件: 粘贴/拖入 ----------------
function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

async function attachImageFile(file) {
  if (!state.bridge || !file) return;
  if (!file.type || !file.type.startsWith("image/")) return;
  try {
    const dataUrl = await blobToDataUrl(file);
    // bridge slot 同步 return — JS 端走 callback
    state.bridge.attach_image_b64(dataUrl, (path) => {
      if (!path) {
        console.warn("attach_image_b64 返回空", file.name);
        return;
      }
      state.attachments.push({ path, dataUrl });
      renderPasteStrip();
    });
  } catch (e) {
    console.error("attach 失败", e);
  }
}

function renderPasteStrip() {
  if (!els.pasteStrip) return;
  if (!state.attachments.length) {
    els.pasteStrip.hidden = true;
    els.pasteStrip.innerHTML = "";
    return;
  }
  els.pasteStrip.hidden = false;
  els.pasteStrip.innerHTML = state.attachments.map((a, i) => `
    <div class="paste-chip" data-idx="${i}" title="${escapeHtml(a.path)}">
      <img src="${a.dataUrl}" alt="" />
      <button class="paste-chip-x" data-idx="${i}" title="移除">×</button>
    </div>
  `).join("");
  els.pasteStrip.querySelectorAll(".paste-chip-x").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(b.dataset.idx);
      state.attachments.splice(idx, 1);
      renderPasteStrip();
    });
  });
}

function bindAttachmentEvents() {
  // composer-input 上的 paste — 抓剪贴板里的图
  els.composerInput.addEventListener("paste", (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    let handled = false;
    for (const it of items) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f && f.type.startsWith("image/")) {
          attachImageFile(f);
          handled = true;
        }
      }
    }
    if (handled) e.preventDefault();
  });

  // composer-card 整体接 drag/drop
  const card = els.composerInput.closest(".composer-card");
  els.composerCard = card;
  if (card) {
    ["dragenter", "dragover"].forEach((ev) => {
      card.addEventListener(ev, (e) => {
        if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) {
          e.preventDefault();
          card.classList.add("dragover");
        }
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      card.addEventListener(ev, (e) => {
        if (ev === "drop") e.preventDefault();
        card.classList.remove("dragover");
      });
    });
    card.addEventListener("drop", (e) => {
      const files = e.dataTransfer && e.dataTransfer.files;
      if (!files) return;
      for (const f of files) attachImageFile(f);
    });
  }

  // 回形针按钮 = 触发文件选择
  if (els.attachBtn && els.attachFileInput) {
    els.attachBtn.addEventListener("click", () => els.attachFileInput.click());
    els.attachFileInput.addEventListener("change", (e) => {
      const files = e.target.files;
      if (!files) return;
      for (const f of files) attachImageFile(f);
      els.attachFileInput.value = "";  // 重置, 下次选同一文件也能触发
    });
  }
}

// ---------------- 模型 / 权限模式 dropdown (自定义, 不用 native select) ----------------
class Dropdown {
  constructor(rootEl, opts) {
    this.root = rootEl;
    this.valueEl = rootEl.querySelector(".header-dd-value");
    this.options = opts.options || [];
    this.value = opts.value || "";
    this.onChange = opts.onChange || (() => {});
    this.menu = null;
    this._onOutside = null;

    this.root.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggle();
    });
    this.root.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        this.toggle();
      } else if (e.key === "Escape") {
        this.close();
      }
    });
    this._renderValue();
  }
  setOptions(options) {
    this.options = options;
    this._renderValue();
  }
  setValue(v) {
    this.value = v || "";
    this._renderValue();
  }
  _renderValue() {
    const opt = this.options.find((o) => o.key === this.value);
    this.valueEl.textContent = opt ? opt.label : (this.value || "");
  }
  toggle() {
    if (this.menu) this.close(); else this.open();
  }
  open() {
    if (this.menu) return;
    this.root.classList.add("open");
    this.menu = document.createElement("div");
    this.menu.className = "header-dd-menu";
    this.menu.innerHTML = this.options.map((o) =>
      `<div class="header-dd-item${o.key === this.value ? " selected" : ""}" data-key="${escapeHtml(o.key)}">${escapeHtml(o.label)}</div>`
    ).join("");
    document.body.appendChild(this.menu);

    // 定位: dropdown 下方, 右对齐
    const r = this.root.getBoundingClientRect();
    this.menu.style.top = (r.bottom + 4) + "px";
    this.menu.style.right = (window.innerWidth - r.right) + "px";
    this.menu.style.minWidth = r.width + "px";

    this.menu.addEventListener("click", (e) => {
      const it = e.target.closest(".header-dd-item");
      if (!it) return;
      const k = it.dataset.key;
      this.close();
      this.onChange(k);
    });

    // 下一帧再挂 outside-click, 避免本次 click 立即关闭
    setTimeout(() => {
      this._onOutside = (e) => {
        if (!this.menu) return;
        if (this.menu.contains(e.target) || this.root.contains(e.target)) return;
        this.close();
      };
      document.addEventListener("click", this._onOutside);
      window.addEventListener("resize", this._onOutside);
    }, 0);
  }
  close() {
    this.root.classList.remove("open");
    if (this.menu) {
      this.menu.remove();
      this.menu = null;
    }
    if (this._onOutside) {
      document.removeEventListener("click", this._onOutside);
      window.removeEventListener("resize", this._onOutside);
      this._onOutside = null;
    }
  }
}

function setupDropdowns() {
  modelDD = new Dropdown(els.modelDDEl, {
    options: [],
    value: "",
    onChange: (key) => {
      if (key === "__custom__") {
        const text = window.prompt(
          "输入模型 ID (如 claude-opus-4-7 / 别名 / 第三方 endpoint id):",
          state.currentModel || ""
        );
        if (text && text.trim()) {
          state.currentModel = text.trim();
        }
        // 取消或确认都重新渲染 (取消时回到原 currentModel)
        updateModelDD();
      } else {
        state.currentModel = key;
        updateModelDD();
      }
      if (state.bridge && state.currentKey) {
        state.bridge.set_model(state.currentKey, state.currentModel);
      }
    },
  });
  permDD = new Dropdown(els.permDDEl, {
    options: [],
    value: "default",
    onChange: (key) => {
      state.currentPermMode = key;
      updatePermDD();
      if (state.bridge) state.bridge.set_perm_mode(key);
    },
  });
}

function updateModelDD() {
  if (!modelDD) return;
  const presetKeys = state.modelOptions.map((o) => o.key);
  const opts = state.modelOptions.slice();
  if (state.currentModel && !presetKeys.includes(state.currentModel)) {
    opts.push({ key: state.currentModel, label: `[自定义] ${state.currentModel}` });
  }
  opts.push({ key: "__custom__", label: "自定义…" });
  modelDD.setOptions(opts);
  modelDD.setValue(state.currentModel);
}

function updatePermDD() {
  if (!permDD) return;
  permDD.setOptions(state.permModes);
  permDD.setValue(state.currentPermMode || "default");
}

// ---------------- "更多"按钮菜单 (chat-header 三个点) ----------------
function showMoreMenu() {
  // 关掉旧 menu (如果有)
  document.querySelectorAll(".header-dd-menu.more-menu").forEach((m) => m.remove());

  const menu = document.createElement("div");
  menu.className = "header-dd-menu more-menu";
  menu.innerHTML = `
    <div class="header-dd-item" data-action="clear">🆕 开始新对话 (清空当前)</div>
  `;
  document.body.appendChild(menu);

  const r = els.moreBtn.getBoundingClientRect();
  menu.style.top = (r.bottom + 4) + "px";
  menu.style.right = (window.innerWidth - r.right) + "px";
  menu.style.minWidth = "200px";

  const close = () => {
    menu.remove();
    document.removeEventListener("click", onOut);
    window.removeEventListener("resize", onOut);
  };
  const onOut = (e) => {
    if (!e) return close();
    if (menu.contains(e.target) || els.moreBtn.contains(e.target)) return;
    close();
  };
  menu.addEventListener("click", (e) => {
    const it = e.target.closest(".header-dd-item");
    if (!it) return;
    const action = it.dataset.action;
    close();
    if (action === "clear") onClearHistory();
  });
  setTimeout(() => {
    document.addEventListener("click", onOut);
    window.addEventListener("resize", () => close());
  }, 0);
}

function onClearHistory() {
  if (!state.bridge || !state.currentKey) return;
  const conv = state.conversations.find((c) => c.key === state.currentKey);
  const name = conv ? conv.name : "当前对话";
  if (!window.confirm(`清空 [${name}] 的历史, 开始新对话?`)) return;
  const key = state.currentKey;
  // B7: clear 期间锁 send, 等 history_loaded("[]") 回来解锁, 避免中间窗口发消息
  _pendingHistoryKey = key;
  els.sendBtn.disabled = true;
  state.bridge.clear_history(key);
  // 本地缓存也清, 防止切回时残留思考态
  delete state.streamBufs[key];
  state.statuses[key] = "idle";
  // bridge 会 emit history_loaded("[]") → onHistoryLoaded 渲染空 chat-body 并解锁 send
  // 这里再兜底立刻清 UI
  renderChatBody();
  setStatus("idle");
}

// ---------------- 用户动作 ----------------
// B5/B7: history_loaded 到达前 send 按钮锁住, 避免竞态期间发消息丢
let _pendingHistoryKey = null;

function switchConversation(key) {
  if (key === state.currentKey) return;
  state.currentKey = key;
  state.attachments = [];     // 切会话清空待发送图 (per-conv 待加, 暂不持久化)
  renderPasteStrip();
  renderConvList();
  renderHeader();
  renderChatBody();
  // B5: 锁 send 直到 history_loaded 到达
  _pendingHistoryKey = key;
  const st = state.statuses[key] || "idle";
  setStatus(st);
  els.sendBtn.disabled = true;
  if (state.bridge) {
    state.bridge.switch_conversation(key);
    state.bridge.get_history(key);  // history 回来后 onHistoryLoaded 会接着恢复流式
    state.bridge.get_model(key, (m) => {
      state.currentModel = m || "";
      updateModelDD();
    });
  }
}

function sendMessage() {
  const text = els.composerInput.value.trim();
  const hasImages = state.attachments.length > 0;
  if ((!text && !hasImages) || !state.bridge || !state.currentKey) return;

  const key = state.currentKey;

  // 本地气泡显示: text + 图片 markdown (复用 renderMarkdown 的图片渲染)
  const imgTail = state.attachments.map((a) => `![pasted](${a.path})`).join("\n");
  const displayText = text
    ? (hasImages ? text + "\n\n" + imgTail : text)
    : imgTail;
  maybeAddDivider(Date.now() / 1000);   // B4: 跨日插 divider
  appendUserMessage(displayText);

  els.composerInput.value = "";
  els.composerInput.style.height = "auto";
  els.sendBtn.disabled = true;
  state.streamBufs[key] = "";
  state.statuses[key] = "thinking";
  appendAssistantPlaceholder();
  setStatus("thinking");

  const pathsJson = JSON.stringify(state.attachments.map((a) => a.path));
  state.bridge.send_message(key, text, pathsJson);

  state.attachments = [];
  renderPasteStrip();
}

// ---------------- bridge signal handlers ----------------
function onBootstrap(payload) {
  try {
    const data = typeof payload === "string" ? JSON.parse(payload) : payload;
    state.conversations = data.conversations || [];
    state.currentKey = data.current_key || (state.conversations[0]?.key);
    state.theme = data.theme || "warm";
    state.dark = !!data.dark;
    state.modelOptions = data.model_options || [];
    state.permModes = data.perm_modes || [];
    state.currentModel = data.current_model || "";
    state.currentPermMode = data.current_perm_mode || "default";
    applyTheme();
    updateModelDD();
    updatePermDD();
    renderConvList();
    renderHeader();
    renderChatBody();
    setStatus("idle");
    if (state.bridge) state.bridge.get_history(state.currentKey);
  } catch (e) {
    console.error("bootstrap parse failed", e);
  }
}

function onConversationsChanged(payload) {
  try {
    const data = typeof payload === "string" ? JSON.parse(payload) : payload;
    state.conversations = data.conversations || [];
    if (!state.conversations.find((c) => c.key === state.currentKey)) {
      state.currentKey = state.conversations[0]?.key;
    }
    renderConvList();
    renderHeader();
  } catch (e) { console.error(e); }
}

function onHistoryLoaded(convKey, payload) {
  if (convKey !== state.currentKey) return;
  try {
    const rows = typeof payload === "string" ? JSON.parse(payload) : payload;
    renderChatBody();  // 清空 + 重置 _lastMessageDay
    rows.forEach((row) => {
      // B4: 按消息真实 ts 跨日插 divider, 不再统一标 "今天"
      maybeAddDivider(row.ts || (Date.now() / 1000));
      if (row.role === "user") appendUserMessage(row.text);
      else if (row.role === "assistant") appendAssistantMessage(row.text);
    });
    // 切回正在思考的会话 → 把暂存的流式 buf 重新挂回 UI
    const buf = state.streamBufs[convKey];
    if (buf !== undefined) {
      maybeAddDivider(Date.now() / 1000);
      appendAssistantPlaceholder();
      if (buf) updateStreamingBubble(buf);
    }
    // B5/B7: history 到达, 解锁 send (除非当前正在 thinking)
    if (_pendingHistoryKey === convKey) {
      _pendingHistoryKey = null;
      const st = state.statuses[convKey] || "idle";
      els.sendBtn.disabled = (st === "thinking");
    }
  } catch (e) { console.error(e); }
}

function onMessageChunk(convKey, text) {
  // 总是累积到 per-key buf, 即使不是当前会话也要存住 (切回时能恢复)
  state.streamBufs[convKey] = (state.streamBufs[convKey] || "") + text;
  if (convKey !== state.currentKey) return;
  updateStreamingBubble(state.streamBufs[convKey]);
}

function onMessageFinished(convKey) {
  // 后台完成的会话: 清 buf+status, 但不动 UI
  const fullBuf = state.streamBufs[convKey] || "";   // 先存, 后面 delete
  delete state.streamBufs[convKey];
  state.statuses[convKey] = "idle";
  if (convKey !== state.currentKey) return;
  finalizeStreamingBubble(fullBuf);   // 传 buf 让 finalize 跑一次完整渲染
  setStatus("idle");
  els.sendBtn.disabled = false;
}

function onStatusChanged(convKey, st) {
  state.statuses[convKey] = st;
  if (convKey !== state.currentKey) return;
  setStatus(st);
  els.sendBtn.disabled = st === "thinking";
}

function onError(convKey, msg) {
  delete state.streamBufs[convKey];
  state.statuses[convKey] = "idle";
  if (convKey !== state.currentKey) return;
  finalizeStreamingBubble();
  appendAssistantMessage(`⚠️ ${msg}`);
  setStatus("idle");
  els.sendBtn.disabled = false;
}

function onModelChanged(convKey, model) {
  if (convKey !== state.currentKey) return;
  state.currentModel = model || "";
  updateModelDD();
}

// ---------------- theme ----------------
function applyTheme() {
  els.body.className = `theme-${state.theme}${state.dark ? " dark" : ""}`;
}

// ---------------- bindings ----------------
function bindUi() {
  els.sendBtn.addEventListener("click", sendMessage);

  els.composerInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  els.composerInput.addEventListener("input", () => {
    els.composerInput.style.height = "auto";
    els.composerInput.style.height =
      Math.min(els.composerInput.scrollHeight, 200) + "px";
  });

  els.addBtn.addEventListener("click", () => {
    if (state.bridge) state.bridge.request_add_project();
  });
  els.settingsBtn.addEventListener("click", () => {
    if (state.bridge) state.bridge.request_settings();
  });

  setupDropdowns();
  bindAttachmentEvents();

  if (els.moreBtn) {
    els.moreBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      showMoreMenu();
    });
  }
}

// ---------------- bootstrap ----------------
function initChannel() {
  if (typeof QWebChannel === "undefined") {
    console.error("QWebChannel 未注入 — bridge 不可用");
    return;
  }
  new QWebChannel(qt.webChannelTransport, (channel) => {
    state.bridge = channel.objects.bridge;
    // hook signals
    state.bridge.bootstrap.connect(onBootstrap);
    state.bridge.conversations_changed.connect(onConversationsChanged);
    state.bridge.history_loaded.connect(onHistoryLoaded);
    state.bridge.message_chunk.connect(onMessageChunk);
    state.bridge.message_finished.connect(onMessageFinished);
    state.bridge.status_changed.connect(onStatusChanged);
    state.bridge.error_occurred.connect(onError);
    state.bridge.model_changed.connect(onModelChanged);
    state.bridge.theme_changed.connect((theme, dark) => {
      state.theme = theme || "warm";
      state.dark = !!dark;
      applyTheme();
    });
    // 拉一次 bootstrap
    state.bridge.request_bootstrap();
  });
}

window.addEventListener("DOMContentLoaded", () => {
  bindUi();
  initChannel();
});
