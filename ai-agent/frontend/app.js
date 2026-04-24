const STORAGE_KEY = "dbass-auth";

const state = {
  auth: null,
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  sending: false,
  bootstrapping: false,
};

const elements = {
  loginModal: document.getElementById("login-modal"),
  loginForm: document.getElementById("login-form"),
  loginUserId: document.getElementById("login-user-id"),
  loginRole: document.getElementById("login-role"),
  identityCard: document.getElementById("identity-card"),
  sessionList: document.getElementById("session-list"),
  sessionTitle: document.getElementById("session-title"),
  sessionSubtitle: document.getElementById("session-subtitle"),
  sessionStatus: document.getElementById("session-status"),
  deleteButton: document.getElementById("delete-button"),
  newSessionButton: document.getElementById("new-session-button"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  messageInput: document.getElementById("message-input"),
  sendButton: document.getElementById("send-button"),
  flash: document.getElementById("flash"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function messageToHtml(value) {
  return escapeHtml(value).replaceAll("\n", "<br />");
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function truncatePreview(content) {
  return String(content || "").trim().replace(/\s+/g, " ").slice(0, 80);
}

function sortSessions(items) {
  return [...items].sort((left, right) => {
    const leftValue = left.last_message_at || left.updated_at || "";
    const rightValue = right.last_message_at || right.updated_at || "";
    return rightValue.localeCompare(leftValue);
  });
}

function buildLocalId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function showFlash(message, kind = "info") {
  elements.flash.classList.remove("hidden");
  elements.flash.dataset.kind = kind;
  elements.flash.textContent = message;
}

function clearFlash() {
  elements.flash.classList.add("hidden");
  elements.flash.dataset.kind = "";
  elements.flash.textContent = "";
}

function openLoginModal() {
  elements.loginModal.classList.remove("hidden");
}

function closeLoginModal() {
  elements.loginModal.classList.add("hidden");
}

function loadAuth() {
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (!saved) {
    return null;
  }

  try {
    return JSON.parse(saved);
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function saveAuth(auth) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
}

function clearAuth() {
  window.localStorage.removeItem(STORAGE_KEY);
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

function renderIdentity() {
  if (!state.auth) {
    elements.identityCard.innerHTML = `
      <div class="empty-state">请先登录。</div>
    `;
    return;
  }

  elements.identityCard.innerHTML = `
    <p class="eyebrow">当前身份</p>
    <h2>${escapeHtml(state.auth.user_id)}</h2>
    <div class="identity-row"><span>角色</span><strong>${escapeHtml(state.auth.role)}</strong></div>
    <div class="identity-row"><span>后端 user</span><strong>${escapeHtml(state.auth.user || "-")}</strong></div>
    <div class="session-actions">
      <button data-action="switch-user" class="ghost-button" type="button">切换用户</button>
    </div>
  `;
}

function renderSessions() {
  if (!state.auth) {
    elements.sessionList.innerHTML = `<div class="empty-state">请先登录后查看会话。</div>`;
    return;
  }

  if (!state.sessions.length) {
    elements.sessionList.innerHTML = `<div class="empty-state">当前用户还没有历史会话。</div>`;
    return;
  }

  elements.sessionList.innerHTML = state.sessions
    .map(
      (item) => `
        <article class="session-item ${item.session_id === state.currentSessionId ? "active" : ""}" data-session-id="${escapeHtml(item.session_id)}">
          <div class="session-title">${escapeHtml(item.title)}</div>
          <div class="session-preview">${escapeHtml(item.preview || "暂无预览")}</div>
          <div class="session-meta">
            <span>${escapeHtml(item.status)}</span>
            <span>${formatTime(item.last_message_at || item.updated_at)}</span>
          </div>
          <div class="session-actions">
            <button data-action="open" data-session-id="${escapeHtml(item.session_id)}" class="ghost-button" type="button">打开</button>
            <button data-action="delete" data-session-id="${escapeHtml(item.session_id)}" class="danger-button" type="button">删除</button>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderCurrentSession() {
  if (!state.currentSession) {
    elements.sessionTitle.textContent = "未选择会话";
    elements.sessionSubtitle.textContent = state.auth
      ? "请选择或创建一个会话。"
      : "请先登录。";
    elements.sessionStatus.textContent = "-";
    elements.messages.innerHTML = `<div class="empty-state">请选择或创建一个会话。</div>`;
    return;
  }

  const detail = state.currentSession;
  elements.sessionTitle.textContent = detail.meta.title;
  elements.sessionSubtitle.textContent = "当前页面只显示当前登录用户自己的会话。";
  elements.sessionStatus.textContent = detail.meta.status;

  if (!detail.messages.length) {
    elements.messages.innerHTML = `<div class="empty-state">当前会话还没有消息，开始提问吧。</div>`;
    return;
  }

  elements.messages.innerHTML = detail.messages
    .map(
      (message) => `
        <article class="message ${message.role} ${message.pending ? "pending" : ""}">
          <div class="message-meta">
            <span>${message.pending && message.role === "assistant" ? "助手思考中" : message.role === "assistant" ? "助手" : "用户"}</span>
            <span>${formatTime(message.created_at)}</span>
          </div>
          <div class="message-content ${message.typing ? "typing" : ""}">${messageToHtml(message.content)}</div>
        </article>
      `,
    )
    .join("");

  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function setComposerState() {
  const noSession = !state.currentSessionId;
  const disableActions = noSession || state.bootstrapping;

  elements.messageInput.disabled = disableActions;
  elements.sendButton.disabled = disableActions || state.sending;
  elements.sendButton.textContent = state.sending ? "发送中..." : "发送";
  elements.newSessionButton.disabled = state.bootstrapping || !state.auth;
  elements.deleteButton.disabled = noSession || state.bootstrapping;
}

function upsertSessionItem(meta, preview) {
  const nextItem = {
    session_id: meta.session_id,
    title: meta.title,
    status: meta.status,
    updated_at: meta.updated_at,
    last_message_at: meta.last_message_at,
    preview: truncatePreview(preview),
  };

  const index = state.sessions.findIndex((item) => item.session_id === meta.session_id);
  if (index === -1) {
    state.sessions = sortSessions([nextItem, ...state.sessions]);
  } else {
    const nextSessions = [...state.sessions];
    nextSessions[index] = nextItem;
    state.sessions = sortSessions(nextSessions);
  }
}

function removeSessionItem(sessionId) {
  state.sessions = state.sessions.filter((item) => item.session_id !== sessionId);
}

function appendOptimisticMessages(content) {
  if (!state.currentSession) {
    return null;
  }

  const now = new Date().toISOString();
  const optimisticUser = {
    message_id: buildLocalId("msg-user"),
    role: "user",
    content,
    created_at: now,
    pending: true,
  };
  const optimisticAssistant = {
    message_id: buildLocalId("msg-assistant"),
    role: "assistant",
    content: "助手正在思考...",
    created_at: now,
    pending: true,
    typing: true,
  };

  state.currentSession = {
    ...state.currentSession,
    meta: {
      ...state.currentSession.meta,
      updated_at: now,
      last_message_at: now,
    },
    messages: [...state.currentSession.messages, optimisticUser, optimisticAssistant],
  };

  upsertSessionItem(state.currentSession.meta, content);
  renderSessions();
  renderCurrentSession();

  return {
    optimisticUserId: optimisticUser.message_id,
    optimisticAssistantId: optimisticAssistant.message_id,
  };
}

function applyMessageResponse(payload, optimisticRefs) {
  if (!state.currentSession) {
    return;
  }

  const nextMessages = [];
  let replacedUser = false;
  let replacedAssistant = false;

  for (const message of state.currentSession.messages) {
    if (message.message_id === optimisticRefs.optimisticUserId) {
      nextMessages.push(payload.user_message);
      replacedUser = true;
      continue;
    }
    if (message.message_id === optimisticRefs.optimisticAssistantId) {
      nextMessages.push(payload.assistant_message);
      replacedAssistant = true;
      continue;
    }
    nextMessages.push(message);
  }

  if (!replacedUser) {
    nextMessages.push(payload.user_message);
  }
  if (!replacedAssistant) {
    nextMessages.push(payload.assistant_message);
  }

  state.currentSession = {
    ...state.currentSession,
    meta: payload.session,
    messages: nextMessages,
  };

  upsertSessionItem(payload.session, payload.assistant_message.content);
  renderSessions();
  renderCurrentSession();
}

async function fetchSessions() {
  const payload = await api("/api/v1/sessions");
  state.sessions = sortSessions(payload.items || []);
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
  await fetchSessions();
  await loadSession(payload.session.meta.session_id);
}

async function initializeAfterLogin() {
  state.bootstrapping = true;
  setComposerState();
  clearFlash();

  try {
    await fetchSessions();
    if (state.sessions.length) {
      await loadSession(state.sessions[0].session_id);
    } else {
      await createSession();
    }
  } finally {
    state.bootstrapping = false;
    setComposerState();
  }
}

async function reconcileCurrentSession() {
  await fetchSessions();
  if (!state.currentSessionId) {
    renderCurrentSession();
    return;
  }

  const stillExists = state.sessions.some((item) => item.session_id === state.currentSessionId);
  if (!stillExists) {
    state.currentSessionId = null;
    state.currentSession = null;
    renderCurrentSession();
    return;
  }

  await loadSession(state.currentSessionId);
}

function switchUser() {
  clearAuth();
  state.auth = null;
  state.sessions = [];
  state.currentSessionId = null;
  state.currentSession = null;
  state.sending = false;
  state.bootstrapping = false;
  renderIdentity();
  renderSessions();
  renderCurrentSession();
  setComposerState();
  clearFlash();
  openLoginModal();
}

async function handleDelete(sessionId) {
  const target = state.sessions.find((item) => item.session_id === sessionId);
  const title = target?.title || sessionId;
  const confirmed = window.confirm(`确认删除会话“${title}”吗？删除后不可恢复。`);
  if (!confirmed) {
    return;
  }

  await api(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
  removeSessionItem(sessionId);

  if (state.currentSessionId === sessionId) {
    state.currentSessionId = null;
    state.currentSession = null;
    if (state.sessions.length) {
      await loadSession(state.sessions[0].session_id);
    } else {
      renderSessions();
      renderCurrentSession();
      setComposerState();
    }
    return;
  }

  renderSessions();
}

async function handleSessionAction(action, sessionId) {
  clearFlash();

  if (action === "open") {
    await loadSession(sessionId);
    return;
  }

  if (action === "delete") {
    await handleDelete(sessionId);
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

  const optimisticRefs = appendOptimisticMessages(content);
  elements.messageInput.value = "";
  state.sending = true;
  setComposerState();
  clearFlash();

  try {
    const payload = await api(`/api/v1/sessions/${state.currentSessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });

    if (payload.warning === "mock-server-disabled") {
      showFlash("这是 DBAAS 相关请求，当前阶段后台尚未启用 mock-server 调用能力。");
    }

    if (optimisticRefs) {
      applyMessageResponse(payload, optimisticRefs);
    } else {
      await reconcileCurrentSession();
    }
  } catch (error) {
    if (!elements.messageInput.value.trim()) {
      elements.messageInput.value = content;
    }
    await reconcileCurrentSession();
    showFlash(error.message || "发送失败", "error");
  } finally {
    state.sending = false;
    setComposerState();
  }
}

async function bootstrap() {
  state.auth = loadAuth();
  renderIdentity();
  renderSessions();
  renderCurrentSession();
  setComposerState();

  if (!state.auth) {
    openLoginModal();
    return;
  }

  closeLoginModal();
  await initializeAfterLogin();
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
  closeLoginModal();
  renderIdentity();
  renderSessions();
  renderCurrentSession();
  await initializeAfterLogin();
});

elements.identityCard.addEventListener("click", (event) => {
  const target = event.target.closest("button[data-action='switch-user']");
  if (!target) {
    return;
  }
  switchUser();
});

elements.newSessionButton.addEventListener("click", async () => {
  clearFlash();
  await createSession();
});

elements.sessionList.addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-action]");
  if (actionButton) {
    event.stopPropagation();
    await handleSessionAction(actionButton.dataset.action, actionButton.dataset.sessionId);
    return;
  }

  const article = event.target.closest("[data-session-id]");
  if (!article) {
    return;
  }
  await handleSessionAction("open", article.dataset.sessionId);
});

elements.deleteButton.addEventListener("click", async () => {
  if (!state.currentSessionId) {
    return;
  }
  await handleDelete(state.currentSessionId);
});

elements.composer.addEventListener("submit", sendMessage);

bootstrap().catch((error) => {
  showFlash(error.message || "初始化失败", "error");
  openLoginModal();
});
