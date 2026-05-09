const STORAGE_KEY = "dbass-auth";

const state = {
  auth: null,
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  sending: false,
  bootstrapping: false,
  decidingApprovalIds: new Set(),
  config: {
    messageMaxChars: 20000,
  },
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

function timeValue(value) {
  if (!value) {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function truncatePreview(content) {
  return String(content || "").trim().replace(/\s+/g, " ").slice(0, 80);
}

function hasPendingApproval(detail = state.currentSession) {
  return Boolean(detail?.approvals?.some((approval) => approval.status === "pending"));
}

function formatValueWithUnit(value, unit) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return `${escapeHtml(value)}${unit ? escapeHtml(unit) : ""}`;
}

function formatRiskLevel(value) {
  const labels = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "高危",
  };
  return labels[value] || value || "-";
}

function formatExecutionMode(value) {
  const labels = {
    sync: "同步",
    async: "异步",
  };
  return labels[value] || value || "-";
}

function formatApprovalStatus(value) {
  const labels = {
    pending: "待确认",
    approved: "已批准",
    rejected: "已拒绝",
    expired: "已过期",
  };
  return labels[value] || value || "-";
}

function formatOperationStatus(value) {
  const labels = {
    started: "执行中",
    succeeded: "已成功",
    failed: "已失败",
    timeout: "已超时",
    unknown: "待核查",
    task_created: "任务已创建",
  };
  return labels[value] || value || "-";
}

function renderApprovalTarget(target) {
  const name = target.name || target.id || "-";
  const qualifiers = target.qualifiers || {};
  const qualifierText = Object.entries(qualifiers)
    .map(([key, value]) => `${key}: ${value}`)
    .join("，");
  return `
    <li>
      <strong>${escapeHtml(name)}</strong>
      ${qualifierText ? `<span>${escapeHtml(qualifierText)}</span>` : ""}
    </li>
  `;
}

function renderApprovalParameter(parameter) {
  const label = parameter.label || parameter.key || "-";
  const target = formatValueWithUnit(parameter.value, parameter.unit);
  const hasCurrent = parameter.current_value !== null && parameter.current_value !== undefined;
  const value = hasCurrent
    ? `${formatValueWithUnit(parameter.current_value, parameter.current_unit || parameter.unit)} → ${target}`
    : target;
  return `
    <li>
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
    </li>
  `;
}

function approvalPreview(approval) {
  return approval?.proposal?.summary || approval?.action || "等待人工确认";
}

function getApprovalHandledAt(approval) {
  return approval.decided_at || approval.expired_at || null;
}

function renderApprovalCard(approval) {
  const proposal = approval.proposal || {};
  const targets = proposal.targets || [];
  const parameters = proposal.parameters || [];
  const riskNotes = proposal.risk_notes || [];
  const isPending = approval.status === "pending";
  const deciding = isPending && state.decidingApprovalIds.has(approval.approval_id);

  return `
    <article class="approval-card ${escapeHtml(approval.status)}" data-approval-id="${escapeHtml(approval.approval_id)}">
      <div class="approval-header">
        <div>
          <p class="eyebrow">操作确认</p>
          <h3>${escapeHtml(proposal.summary || approval.action || "待确认操作")}</h3>
        </div>
        <div class="approval-badges">
          <span class="status-pill approval-status">${escapeHtml(formatApprovalStatus(approval.status))}</span>
          <span class="status-pill approval-risk">${escapeHtml(formatRiskLevel(proposal.risk_level))}</span>
        </div>
      </div>

      <div class="approval-grid">
        <div>
          <span>执行方式</span>
          <strong>${escapeHtml(formatExecutionMode(proposal.execution_mode))}</strong>
        </div>
        <div>
          <span>所需角色</span>
          <strong>${escapeHtml(proposal.required_role || "user")}</strong>
        </div>
        <div>
          <span>申请时间</span>
          <strong>${formatTime(approval.created_at)}</strong>
        </div>
        <div>
          <span>处理时间</span>
          <strong>${formatTime(getApprovalHandledAt(approval))}</strong>
        </div>
        <div>
          <span>过期时间</span>
          <strong>${formatTime(approval.expires_at)}</strong>
        </div>
      </div>

      <div class="approval-section">
        <span>目标资源</span>
        <ul class="approval-list">
          ${targets.length ? targets.map(renderApprovalTarget).join("") : "<li><strong>-</strong></li>"}
        </ul>
      </div>

      <div class="approval-section">
        <span>变更参数</span>
        <ul class="approval-param-list">
          ${parameters.length ? parameters.map(renderApprovalParameter).join("") : "<li><span>-</span><strong>-</strong></li>"}
        </ul>
      </div>

      ${
        riskNotes.length
          ? `<div class="approval-section"><span>风险提示</span><ul class="approval-list">${riskNotes
              .map((note) => `<li>${escapeHtml(note)}</li>`)
              .join("")}</ul></div>`
          : ""
      }

      ${
        approval.resume_failed
          ? `<div class="approval-section approval-warning"><span>恢复状态</span><strong>${escapeHtml(
              approval.resume_error || "审批恢复失败，请查看操作结果。",
            )}</strong></div>`
          : ""
      }

      ${
        isPending
          ? `<div class="approval-actions">
              <button
                class="primary-button"
                type="button"
                data-approval-decision="approved"
                data-approval-id="${escapeHtml(approval.approval_id)}"
                ${deciding ? "disabled" : ""}
              >${deciding ? "处理中..." : "批准"}</button>
              <button
                class="danger-button"
                type="button"
                data-approval-decision="rejected"
                data-approval-id="${escapeHtml(approval.approval_id)}"
                ${deciding ? "disabled" : ""}
              >拒绝</button>
            </div>`
          : ""
      }
    </article>
  `;
}

function renderOperationChange(change) {
  return `
    <li>
      <span>${escapeHtml(change.label || change.field || "-")}</span>
      <strong>${formatValueWithUnit(change.before, change.unit)} → ${formatValueWithUnit(
        change.after,
        change.unit,
      )}</strong>
    </li>
  `;
}

function renderOperationCard(operation) {
  const result = operation.result || {};
  const changes = result.changes || [];
  const summary = result.summary || operation.action || "操作结果";
  const status = result.status || operation.status;

  return `
    <article class="operation-card ${escapeHtml(status)}" data-operation-id="${escapeHtml(operation.operation_id)}">
      <div class="approval-header">
        <div>
          <p class="eyebrow">执行结果</p>
          <h3>${escapeHtml(summary)}</h3>
        </div>
        <span class="status-pill operation-status">${escapeHtml(formatOperationStatus(status))}</span>
      </div>

      <div class="approval-grid">
        <div>
          <span>执行方式</span>
          <strong>${escapeHtml(formatExecutionMode(operation.execution_mode))}</strong>
        </div>
        <div>
          <span>动作</span>
          <strong>${escapeHtml(operation.action || "-")}</strong>
        </div>
        <div>
          <span>完成时间</span>
          <strong>${formatTime(operation.completed_at || operation.started_at)}</strong>
        </div>
      </div>

      ${
        changes.length
          ? `<div class="approval-section"><span>变更明细</span><ul class="approval-param-list">${changes
              .map(renderOperationChange)
              .join("")}</ul></div>`
          : ""
      }

      ${
        result.error
          ? `<div class="approval-section approval-warning"><span>错误信息</span><strong>${escapeHtml(
              result.error.message || result.error.error_type,
            )}</strong></div>`
          : ""
      }
    </article>
  `;
}

function renderMessageCard(message) {
  return `
    <article class="message ${message.role} ${message.pending ? "pending" : ""} ${message.error ? "error" : ""}">
      <div class="message-meta">
        <span>${getMessageAuthorLabel(message)}</span>
        <span>${formatTime(message.created_at)}</span>
      </div>
      <div class="message-content ${message.typing ? "typing" : ""}">${messageToHtml(message.content)}</div>
    </article>
  `;
}

function getOperationTimelineTime(operation) {
  return operation.completed_at || operation.started_at || operation.created_at;
}

function buildSessionTimeline(detail) {
  const items = [];

  for (const [index, message] of (detail.messages || []).entries()) {
    items.push({
      type: "message",
      data: message,
      at: timeValue(message.created_at),
      priority: 0,
      index,
    });
  }

  for (const [index, approval] of (detail.approvals || []).entries()) {
    items.push({
      type: "approval",
      data: approval,
      at: timeValue(approval.created_at),
      priority: 1,
      index,
    });
  }

  for (const [index, operation] of (detail.operations || []).entries()) {
    items.push({
      type: "operation",
      data: operation,
      at: timeValue(getOperationTimelineTime(operation)),
      priority: 2,
      index,
    });
  }

  return items.sort((left, right) => {
    if (left.at !== right.at) {
      return left.at - right.at;
    }
    if (left.priority !== right.priority) {
      return left.priority - right.priority;
    }
    return left.index - right.index;
  });
}

function renderTimelineItem(item) {
  if (item.type === "message") {
    return renderMessageCard(item.data);
  }
  if (item.type === "approval") {
    return renderApprovalCard(item.data);
  }
  return renderOperationCard(item.data);
}

function formatApiDetail(detail) {
  if (!detail) {
    return "";
  }

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
        const message = item.msg || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      })
      .join("；");
  }

  if (typeof detail === "object") {
    return detail.detail || detail.msg || JSON.stringify(detail);
  }

  return String(detail);
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
    throw new Error(formatApiDetail(payload.detail) || "请求失败");
  }
  return payload;
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  let eventName = "message";
  const dataLines = [];

  for (const line of lines) {
    if (!line || line.startsWith(":")) {
      continue;
    }

    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const rawValue = separator === -1 ? "" : line.slice(separator + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

    if (field === "event") {
      eventName = value;
    }
    if (field === "data") {
      dataLines.push(value);
    }
  }

  if (!dataLines.length) {
    return null;
  }

  return {
    eventName,
    payload: JSON.parse(dataLines.join("\n")),
  };
}

async function readSseResponse(response, onEvent) {
  if (!response.body) {
    throw new Error("当前浏览器不支持流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";

    for (const block of blocks) {
      const parsed = parseSseBlock(block);
      if (parsed) {
        onEvent(parsed.eventName, parsed.payload);
      }
    }

    if (done) {
      break;
    }
  }

  const tail = buffer.trim();
  if (tail) {
    const parsed = parseSseBlock(tail);
    if (parsed) {
      onEvent(parsed.eventName, parsed.payload);
    }
  }
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

  const timelineHtml = buildSessionTimeline(detail).map(renderTimelineItem).join("");

  if (!timelineHtml) {
    elements.messages.innerHTML = `<div class="empty-state">当前会话还没有消息，开始提问吧。</div>`;
    return;
  }

  elements.messages.innerHTML = timelineHtml;

  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function getMessageAuthorLabel(message) {
  if (message.error) {
    return "发送失败";
  }
  if (message.role === "ai-agent") {
    return "AI Agent";
  }
  if (message.pending && message.role === "assistant") {
    return "助手思考中";
  }
  if (message.role === "assistant") {
    return "助手";
  }
  if (message.role === "system") {
    return "系统";
  }
  return "用户";
}

function setComposerState() {
  const noSession = !state.currentSessionId;
  const pendingApproval = hasPendingApproval();
  const decidingApproval = state.decidingApprovalIds.size > 0;
  const disableActions = noSession || state.bootstrapping || pendingApproval || decidingApproval;

  elements.messageInput.disabled = disableActions;
  elements.sendButton.disabled = disableActions || state.sending;
  elements.sendButton.textContent = state.sending ? "发送中..." : pendingApproval ? "待确认" : "发送";
  elements.newSessionButton.disabled = state.bootstrapping || !state.auth;
  elements.deleteButton.disabled = noSession || state.bootstrapping || pendingApproval || decidingApproval;
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

function upsertApproval(approvals, approval) {
  if (!approval) {
    return approvals || [];
  }
  const current = approvals || [];
  const index = current.findIndex((item) => item.approval_id === approval.approval_id);
  if (index === -1) {
    return [...current, approval];
  }
  const next = [...current];
  next[index] = approval;
  return next;
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
    content: "",
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

  if (!payload.assistant_message) {
    applyPausedApprovalResponse(payload, optimisticRefs, state.currentSessionId);
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

  if (
    !replacedUser &&
    !nextMessages.some((message) => message.message_id === payload.user_message.message_id)
  ) {
    nextMessages.push(payload.user_message);
  }
  if (
    !replacedAssistant &&
    !nextMessages.some((message) => message.message_id === payload.assistant_message.message_id)
  ) {
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

function applyStreamUserMessage(payload, optimisticRefs, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId || !optimisticRefs) {
    return;
  }

  state.currentSession = {
    ...state.currentSession,
    messages: state.currentSession.messages.map((message) =>
      message.message_id === optimisticRefs.optimisticUserId ? payload.user_message : message,
    ),
  };
  renderCurrentSession();
}

function applyStreamToken(payload, optimisticRefs, sessionId) {
  const delta = payload.delta || "";
  if (!delta || !state.currentSession || state.currentSessionId !== sessionId || !optimisticRefs) {
    return;
  }

  state.currentSession = {
    ...state.currentSession,
    messages: state.currentSession.messages.map((message) => {
      if (message.message_id !== optimisticRefs.optimisticAssistantId) {
        return message;
      }
      return {
        ...message,
        content: `${message.content || ""}${delta}`,
        pending: true,
        typing: true,
      };
    }),
  };
  renderCurrentSession();
}

function applyStreamSystemMessage(message, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId || !message) {
    return false;
  }

  if (state.currentSession.messages.some((item) => item.message_id === message.message_id)) {
    return true;
  }

  state.currentSession = {
    ...state.currentSession,
    messages: [...state.currentSession.messages, message],
  };
  upsertSessionItem(state.currentSession.meta, message.content);
  renderSessions();
  renderCurrentSession();
  return true;
}

function applyStreamApprovalRequired(approval, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId || !approval) {
    return false;
  }

  state.currentSession = {
    ...state.currentSession,
    approvals: upsertApproval(state.currentSession.approvals, approval),
  };
  upsertSessionItem(state.currentSession.meta, approvalPreview(approval));
  renderSessions();
  renderCurrentSession();
  setComposerState();
  return true;
}

function applyPausedApprovalResponse(payload, optimisticRefs, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId) {
    return false;
  }

  const nextMessages = [];
  let replacedUser = false;

  for (const message of state.currentSession.messages || []) {
    if (optimisticRefs && message.message_id === optimisticRefs.optimisticUserId) {
      if (payload.user_message) {
        nextMessages.push(payload.user_message);
        replacedUser = true;
      }
      continue;
    }
    if (optimisticRefs && message.message_id === optimisticRefs.optimisticAssistantId) {
      continue;
    }
    nextMessages.push(message);
  }

  if (
    payload.user_message &&
    !replacedUser &&
    !nextMessages.some((message) => message.message_id === payload.user_message.message_id)
  ) {
    nextMessages.push(payload.user_message);
  }

  const nextMeta = payload.session || state.currentSession.meta;
  const nextApprovals = upsertApproval(state.currentSession.approvals, payload.approval);
  state.currentSession = {
    ...state.currentSession,
    meta: nextMeta,
    messages: nextMessages,
    approvals: nextApprovals,
  };

  upsertSessionItem(nextMeta, approvalPreview(payload.approval));
  renderSessions();
  renderCurrentSession();
  setComposerState();
  return true;
}

function applyStreamError(message, optimisticRefs, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId || !optimisticRefs) {
    return false;
  }

  let updated = false;
  state.currentSession = {
    ...state.currentSession,
    messages: state.currentSession.messages.map((item) => {
      if (item.message_id !== optimisticRefs.optimisticAssistantId) {
        return item;
      }
      updated = true;
      return {
        ...item,
        content: `本轮回复失败：${message}`,
        pending: false,
        typing: false,
        error: true,
      };
    }),
  };

  if (updated) {
    renderCurrentSession();
  }
  return updated;
}

function applyStreamAiAgentMessage(message, optimisticRefs, sessionId) {
  if (!state.currentSession || state.currentSessionId !== sessionId || !message) {
    return false;
  }

  let replaced = false;
  const nextMessages = state.currentSession.messages.map((item) => {
    if (optimisticRefs && item.message_id === optimisticRefs.optimisticAssistantId) {
      replaced = true;
      return message;
    }
    return item;
  });

  if (!replaced && !nextMessages.some((item) => item.message_id === message.message_id)) {
    nextMessages.push(message);
  }

  state.currentSession = {
    ...state.currentSession,
    messages: nextMessages,
  };
  upsertSessionItem(state.currentSession.meta, message.content);
  renderSessions();
  renderCurrentSession();
  return true;
}

async function streamMessageResponse(sessionId, content, optimisticRefs) {
  const response = await fetch(`/api/v1/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: {
      ...authHeaders(),
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ content }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatApiDetail(payload.detail) || "请求失败");
  }

  let completed = false;

  await readSseResponse(response, (eventName, payload) => {
    if (eventName === "user_message") {
      applyStreamUserMessage(payload, optimisticRefs, sessionId);
      return;
    }

    if (eventName === "token") {
      applyStreamToken(payload, optimisticRefs, sessionId);
      return;
    }

    if (eventName === "compression_started" || eventName === "compression_completed") {
      if (payload.system_message) {
        applyStreamSystemMessage(payload.system_message, sessionId);
      }
      return;
    }

    if (eventName === "approval.required") {
      applyStreamApprovalRequired(payload.approval, sessionId);
      return;
    }

    if (eventName === "run.paused") {
      return;
    }

    if (eventName === "error") {
      const message = formatApiDetail(payload.detail) || "流式响应失败";
      const stage = payload.stage ? ` (${payload.stage})` : "";
      if (payload.ai_agent_message) {
        completed = true;
        applyStreamAiAgentMessage(payload.ai_agent_message, optimisticRefs, sessionId);
        showFlash(`${message}${stage}`, "error");
        return;
      }
      const error = new Error(`${message}${stage}`);
      error.streamError = true;
      throw error;
    }

    if (eventName === "done") {
      completed = true;
      if (payload.paused || payload.approval) {
        applyPausedApprovalResponse(payload, optimisticRefs, sessionId);
        return;
      }
      if (optimisticRefs && state.currentSessionId === sessionId) {
        applyMessageResponse(payload, optimisticRefs);
      } else if (payload.session && payload.assistant_message) {
        upsertSessionItem(payload.session, payload.assistant_message.content);
        renderSessions();
      }
    }
  });

  if (!completed) {
    throw new Error("流式响应提前结束。");
  }
}

async function fetchSessions() {
  const payload = await api("/api/v1/sessions");
  state.sessions = sortSessions(payload.items || []);
  renderSessions();
}

async function fetchAppConfig() {
  const payload = await api("/api/v1/config");
  if (Number.isInteger(payload.message_max_chars) && payload.message_max_chars > 0) {
    state.config.messageMaxChars = payload.message_max_chars;
  }
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

async function handleApprovalDecision(approvalId, decision) {
  const sessionId = state.currentSessionId;
  if (!sessionId || !approvalId || !decision) {
    return;
  }
  if (state.decidingApprovalIds.has(approvalId)) {
    return;
  }

  state.decidingApprovalIds.add(approvalId);
  renderCurrentSession();
  setComposerState();
  clearFlash();

  try {
    const payload = await api(`/api/v1/sessions/${sessionId}/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    if (state.currentSessionId === sessionId) {
      await reconcileCurrentSession();
    } else {
      await fetchSessions();
    }
    if (payload.next_approval) {
      showFlash("当前操作已处理，后续操作等待确认。");
    } else {
      showFlash(decision === "approved" ? "操作已批准，执行结果已更新。" : "操作已拒绝。");
    }
  } catch (error) {
    showFlash(error.message || "审批处理失败", "error");
    if (state.currentSessionId === sessionId) {
      await reconcileCurrentSession().catch(() => {});
    } else {
      await fetchSessions().catch(() => {});
    }
  } finally {
    state.decidingApprovalIds.delete(approvalId);
    renderCurrentSession();
    setComposerState();
  }
}

async function sendMessage(event) {
  event.preventDefault();

  if (!state.currentSessionId || state.sending) {
    return;
  }

  const content = elements.messageInput.value.trim();
  if (!content) {
    showFlash("消息内容不能为空。", "error");
    elements.messageInput.focus();
    return;
  }
  if (content.length > state.config.messageMaxChars) {
    showFlash(`消息长度不能超过 ${state.config.messageMaxChars} 字符。`, "error");
    elements.messageInput.focus();
    return;
  }

  const sessionId = state.currentSessionId;
  const optimisticRefs = appendOptimisticMessages(content);
  elements.messageInput.value = "";
  state.sending = true;
  setComposerState();
  clearFlash();

  try {
    await streamMessageResponse(sessionId, content, optimisticRefs);
    if (!optimisticRefs) {
      await reconcileCurrentSession();
    }
  } catch (error) {
    if (!elements.messageInput.value.trim()) {
      elements.messageInput.value = content;
    }
    const handledLocally = applyStreamError(
      error.message || "发送失败",
      optimisticRefs,
      sessionId,
    );
    if (handledLocally) {
      await fetchSessions();
    } else {
      await reconcileCurrentSession();
    }
    showFlash(error.message || "发送失败", "error");
  } finally {
    state.sending = false;
    setComposerState();
  }
}

async function bootstrap() {
  await fetchAppConfig();
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

elements.messages.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-approval-decision]");
  if (!button) {
    return;
  }
  await handleApprovalDecision(button.dataset.approvalId, button.dataset.approvalDecision);
});

elements.composer.addEventListener("submit", sendMessage);

bootstrap().catch((error) => {
  showFlash(error.message || "初始化失败", "error");
  openLoginModal();
});
