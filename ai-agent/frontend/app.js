const state = {
  auth: null,
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  sending: false,
};

const elements = {
  loginModal: document.getElementById("login-modal"),
  loginForm: document.getElementById("login-form"),
  loginUserId: document.getElementById("login-user-id"),
  loginRole: document.getElementById("login-role"),
  identityCard: document.getElementById("identity-card"),
  sessionList: document.getElementById("session-list"),
  sessionTitle: document.getElementById("session-title"),
  sessionStatus: document.getElementById("session-status"),
  archiveButton: document.getElementById("archive-button"),
  restoreButton: document.getElementById("restore-button"),
  deleteButton: document.getElementById("delete-button"),
  newSessionButton: document.getElementById("new-session-button"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  messageInput: document.getElementById("message-input"),
  sendButton: document.getElementById("send-button"),
  flash: document.getElementById("flash"),
};

function loadAuth() {
  const saved = window.localStorage.getItem("dbass-auth");
  if (!saved) {
    return null;
  }
  try {
    return JSON.parse(saved);
  } catch {
    window.localStorage.removeItem("dbass-auth");
    return null;
  }
}

function saveAuth(auth) {
  window.localStorage.setItem("dbass-auth", JSON.stringify(auth));
}

function authHeaders() {
  if (!state.auth) {
    return {};
  }
  return {
    "X-User-Id": state.auth.user_id,
    "X-User-Role": state.auth.role,
    "X-User": state.auth.role === "user" ? state.auth.user : "",
    "Content-Type": "application/json",
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "请求失败");
  }
  return payload;
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function showFlash(message, kind = "info") {
  elements.flash.classList.remove("hidden");
  elements.flash.textContent = message;
  elements.flash.dataset.kind = kind;
}

function clearFlash() {
  elements.flash.classList.add("hidden");
  elements.flash.textContent = "";
}

function renderIdentity() {
  if (!state.auth) {
    elements.identityCard.innerHTML = "";
    return;
  }
  elements.identityCard.innerHTML = `
    <p class="eyebrow">当前身份</p>
    <h2>${state.auth.user_id}</h2>
    <div class="identity-row"><span>角色</span><strong>${state.auth.role}</strong></div>
    <div class="identity-row"><span>后端 user</span><strong>${state.auth.user || "-"}</strong></div>
    <div class="session-item-actions">
      <button data-action="switch-user" class="ghost-button">切换用户</button>
    </div>
  `;
}

function renderSessions() {
  if (!state.sessions.length) {
    elements.sessionList.innerHTML = `<div class="empty-state">当前用户还没有历史会话。</div>`;
    return;
  }

  elements.sessionList.innerHTML = state.sessions
    .map(
      (item) => `
        <article class="session-item ${item.session_id === state.currentSessionId ? "active" : ""}" data-session-id="${item.session_id}">
          <div class="session-item-title">${escapeHtml(item.title)}</div>
          <div class="session-item-preview">${escapeHtml(item.preview || "暂无预览")}</div>
          <div class="session-item-footer">
            <span>${item.status}</span>
            <span>${formatTime(item.last_message_at || item.updated_at)}</span>
          </div>
          <div class="session-item-actions">
            <button data-action="open" data-session-id="${item.session_id}" class="ghost-button">打开</button>
            <button data-action="${item.status === "archived" ? "restore" : "archive"}" data-session-id="${item.session_id}" class="ghost-button">
              ${item.status === "archived" ? "恢复" : "归档"}
            </button>
            <button data-action="delete" data-session-id="${item.session_id}" class="danger-button">删除</button>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderCurrentSession() {
  const detail = state.currentSession;
  if (!detail) {
    elements.sessionTitle.textContent = "未选择会话";
    elements.sessionStatus.textContent = "-";
    elements.messages.innerHTML = `<div class="empty-state">请选择或创建一个会话。</div>`;
    return;
  }

  elements.sessionTitle.textContent = detail.meta.title;
  elements.sessionStatus.textContent = detail.meta.status;

  if (!detail.messages.length) {
    elements.messages.innerHTML = `<div class="empty-state">当前会话还没有消息，开始提问吧。</div>`;
    return;
  }

  elements.messages.innerHTML = detail.messages
    .map(
      (message) => `
        <article class="message ${message.role}">
          <div class="message-meta">
            <span>${message.role === "assistant" ? "助手" : "用户"}</span>
            <span>${formatTime(message.created_at)}</span>
          </div>
          <div>${escapeHtml(message.content).replaceAll("\n", "<br />")}</div>
        </article>
      `,
    )
    .join("");

  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function setComposerState() {
  const disabled = !state.currentSessionId || state.sending;
  elements.messageInput.disabled = disabled;
  elements.sendButton.disabled = disabled;
  elements.archiveButton.disabled = !state.currentSessionId;
  elements.restoreButton.disabled = !state.currentSessionId;
  elements.deleteButton.disabled = !state.currentSessionId;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function findSessionTitle(sessionId) {
  const matched = state.sessions.find((item) => item.session_id === sessionId);
  return matched?.title || sessionId;
}

async function refreshSessions() {
  const payload = await api("/api/v1/sessions");
  state.sessions = payload.items || [];
  renderSessions();
}

async function loadSession(sessionId) {
  const payload = await api(`/api/v1/sessions/${sessionId}`);
  state.currentSessionId = sessionId;
  state.currentSession = payload.session;
  renderSessions();
  renderCurrentSession();
  setComposerState();
}

async function createSession() {
  const payload = await api("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ title: null }),
  });
  await refreshSessions();
  await loadSession(payload.session.meta.session_id);
}

async function ensureInitialSession() {
  await refreshSessions();
  if (!state.sessions.length) {
    await createSession();
    return;
  }
  await loadSession(state.sessions[0].session_id);
}

async function handleAction(action, sessionId) {
  if (action === "open") {
    await loadSession(sessionId);
    return;
  }

  if (action === "archive") {
    await api(`/api/v1/sessions/${sessionId}/archive`, { method: "POST" });
  }

  if (action === "restore") {
    await api(`/api/v1/sessions/${sessionId}/restore`, { method: "POST" });
  }

  if (action === "delete") {
    const confirmed = window.confirm(
      `确认删除会话“${findSessionTitle(sessionId)}”吗？删除后将直接移除本地 Session 目录，且无法恢复。`,
    );
    if (!confirmed) {
      return;
    }
    await api(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
  }

  await refreshSessions();

  if (action === "delete" && sessionId === state.currentSessionId) {
    if (state.sessions.length) {
      await loadSession(state.sessions[0].session_id);
    } else {
      state.currentSessionId = null;
      state.currentSession = null;
      renderCurrentSession();
    }
  } else if (state.currentSessionId) {
    await loadSession(state.currentSessionId);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.currentSessionId || state.sending) {
    return;
  }

  const content = elements.messageInput.value.trim();
  if (!content) {
    return;
  }

  state.sending = true;
  setComposerState();
  clearFlash();

  try {
    const payload = await api(`/api/v1/sessions/${state.currentSessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    elements.messageInput.value = "";
    if (payload.warning === "mock-server-disabled") {
      showFlash("命中了 DBAAS 相关问题，当前阶段已明确提示 mock-server 后台尚未启用。");
    }
    await refreshSessions();
    await loadSession(payload.session.session_id);
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    state.sending = false;
    setComposerState();
  }
}

async function bootstrapApp() {
  state.auth = loadAuth();
  renderIdentity();
  setComposerState();

  if (!state.auth) {
    elements.loginModal.classList.remove("hidden");
    return;
  }

  elements.loginModal.classList.add("hidden");
  await ensureInitialSession();
}

function switchUser() {
  window.localStorage.removeItem("dbass-auth");
  state.auth = null;
  state.sessions = [];
  state.currentSessionId = null;
  state.currentSession = null;
  renderIdentity();
  renderSessions();
  renderCurrentSession();
  setComposerState();
  elements.loginModal.classList.remove("hidden");
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const userId = elements.loginUserId.value.trim();
  const role = elements.loginRole.value;
  if (!userId) {
    return;
  }
  state.auth = {
    user_id: userId,
    role,
    user: role === "user" ? userId : "",
  };
  saveAuth(state.auth);
  renderIdentity();
  elements.loginModal.classList.add("hidden");
  clearFlash();
  await ensureInitialSession();
});

elements.newSessionButton.addEventListener("click", async () => {
  clearFlash();
  await createSession();
});

elements.sessionList.addEventListener("click", async (event) => {
  const target = event.target.closest("button[data-action]");
  const article = event.target.closest("[data-session-id]");
  if (target) {
    event.stopPropagation();
    await handleAction(target.dataset.action, target.dataset.sessionId);
    return;
  }
  if (article) {
    await loadSession(article.dataset.sessionId);
  }
});

elements.identityCard.addEventListener("click", (event) => {
  const target = event.target.closest("button[data-action='switch-user']");
  if (!target) {
    return;
  }
  switchUser();
});

elements.archiveButton.addEventListener("click", async () => {
  if (state.currentSessionId) {
    await handleAction("archive", state.currentSessionId);
  }
});

elements.restoreButton.addEventListener("click", async () => {
  if (state.currentSessionId) {
    await handleAction("restore", state.currentSessionId);
  }
});

elements.deleteButton.addEventListener("click", async () => {
  if (state.currentSessionId) {
    await handleAction("delete", state.currentSessionId);
  }
});

elements.composer.addEventListener("submit", sendMessage);

bootstrapApp().catch((error) => {
  showFlash(error.message || "初始化失败", "error");
});
