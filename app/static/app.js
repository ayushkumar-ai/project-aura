/**
 * PROJECT AURA — MODERN WEB PRODUCT CLIENT (P1)
 * Full interactive controller connecting the unified AURA engine to the browser.
 * Supports Chat, M59 Agent Runs, M48/M59 Approvals, M52/M53 Tasks, M56 Memory,
 * M57 Multimodal Files, M58 Devices, Settings, Real-time Events, and XSS Sanitization.
 */

(() => {
  'use strict';

  // ---------------------------------------------------------------------------
  // 1. Application State & Storage
  // ---------------------------------------------------------------------------
  const state = {
    serverUrl: localStorage.getItem('aura_server_url') || '',
    authToken: sessionStorage.getItem('aura_auth_token') || '',
    activeTab: 'chat-view',
    activeSubtabs: {
      memory: 'memories-subtab',
      tasks: 'tasks-subtab',
    },
    isConnected: false,
    isProcessing: false,
    selectedRunId: null,
    selectedTaskId: null,
    selectedArtifactId: null,
    selectedDeviceId: null,
    activePollers: {},
    principal: {
      userId: 'default',
      username: 'Guest User',
      roles: ['user'],
      scopes: ['aura:run', 'aura:state:read'],
    },
  };

  // ---------------------------------------------------------------------------
  // 2. DOM Elements Cache
  // ---------------------------------------------------------------------------
  const dom = {
    // Navigation
    navTabs: document.querySelectorAll('.nav-tab'),
    viewPanels: document.querySelectorAll('.view-panel'),
    subtabBtns: document.querySelectorAll('.subtab-btn'),
    statusPill: document.getElementById('connection-status'),
    statusText: document.getElementById('status-text'),
    runsBadge: document.getElementById('runs-badge'),
    approvalsBadge: document.getElementById('approvals-badge'),
    contradictionsBadge: document.getElementById('contradictions-badge'),
    headerSettingsBtn: document.getElementById('header-settings-btn'),

    // Chat
    chatForm: document.getElementById('chat-form'),
    userInput: document.getElementById('user-input'),
    messagesContainer: document.getElementById('messages-container'),
    welcomeCard: document.getElementById('welcome-card'),
    loadingIndicator: document.getElementById('loading-indicator'),
    chatStatusBar: document.getElementById('chat-status-bar'),
    chatStatusMsg: document.getElementById('chat-status-msg'),
    chatPhaseTag: document.getElementById('chat-phase-tag'),
    clearBtn: document.getElementById('clear-btn'),
    launchRunBtn: document.getElementById('launch-run-btn'),

    // Runs
    runsListContainer: document.getElementById('runs-list-container'),
    runDetailContainer: document.getElementById('run-detail-container'),
    refreshRunsBtn: document.getElementById('refresh-runs-btn'),
    newRunModalBtn: document.getElementById('new-run-modal-btn'),
    runsSearch: document.getElementById('runs-search'),
    runsStatusFilter: document.getElementById('runs-status-filter'),

    // Approvals
    approvalsContainer: document.getElementById('approvals-container'),
    refreshApprovalsBtn: document.getElementById('refresh-approvals-btn'),

    // Tasks
    tasksListContainer: document.getElementById('tasks-list-container'),
    taskDetailContainer: document.getElementById('task_detail_container'),
    refreshTasksBtn: document.getElementById('refresh-tasks-btn'),
    newTaskModalBtn: document.getElementById('new_task_modal_btn'),

    // Memory
    memoriesListContainer: document.getElementById('memories-list-container'),
    contradictionsListContainer: document.getElementById('contradictions-list-container'),
    memorySearchInput: document.getElementById('memory-search-input'),
    memoryTypeFilter: document.getElementById('memory-type-filter'),
    memoryStateFilter: document.getElementById('memory-state-filter'),
    refreshMemoryBtn: document.getElementById('refresh-memory-btn'),
    consolidateMemoryBtn: document.getElementById('consolidate-memory-btn'),
    newMemoryModalBtn: document.getElementById('new-memory-modal-btn'),
    profileForm: document.getElementById('profile-form'),
    profCommStyle: document.getElementById('prof-comm-style'),
    profProactivity: document.getElementById('prof-proactivity'),
    profInstructions: document.getElementById('prof-instructions'),
    profileStatusMsg: document.getElementById('profile-status-msg'),

    // Files
    fileDropzone: document.getElementById('file-dropzone'),
    fileInput: document.getElementById('file-input'),
    artifactsListContainer: document.getElementById('artifacts-list-container'),
    artifactDetailContainer: document.getElementById('artifact-detail-container'),
    refreshFilesBtn: document.getElementById('refresh-files-btn'),

    // Devices
    devicesListContainer: document.getElementById('devices-list-container'),
    deviceDetailContainer: document.getElementById('device-detail-container'),
    refreshDevicesBtn: document.getElementById('refresh-devices-btn'),
    registerDeviceModalBtn: document.getElementById('register-device-modal-btn'),

    // Settings & Preferences
    settingsForm: document.getElementById('auth-config-form') || document.getElementById('settings-form'),
    settingsServerUrl: document.getElementById('settings-server-url'),
    settingsAuthToken: document.getElementById('auth-token-input-main') || document.getElementById('auth-token-input') || document.getElementById('settings-auth-token'),
    settingsLogoutBtn: document.getElementById('clear-auth-btn') || document.getElementById('settings-logout-btn'),
    refreshTelemetryBtn: document.getElementById('refresh-telemetry-btn'),
    savePrefBtn: document.getElementById('save-pref-btn'),
    prefName: document.getElementById('pref-name'),
    prefInstructions: document.getElementById('pref-instructions'),
    identUserId: document.getElementById('ident-user-id'),
    identUsername: document.getElementById('ident-username'),
    identRoles: document.getElementById('ident-roles'),
    identScopes: document.getElementById('ident-scopes'),
    diagStatus: document.getElementById('diag-status'),
    diagEnv: document.getElementById('diag-env'),
    diagUptime: document.getElementById('diag-uptime'),
    diagDb: document.getElementById('diag-db'),
    purgeTenantModalBtn: document.getElementById('purge-tenant-modal-btn'),

    // Modals
    modalCloseBtns: document.querySelectorAll('.modal-close-btn, [data-modal]'),
    newRunModal: document.getElementById('new-run-modal'),
    newRunForm: document.getElementById('new-run-form'),
    runIntentInput: document.getElementById('run-intent-input'),
    runRoleSelect: document.getElementById('run-role-select'),
    runTimeoutInput: document.getElementById('run-timeout-input'),

    registerDeviceModal: document.getElementById('register-device-modal'),
    registerDeviceForm: document.getElementById('register-device-form'),
    devNameInput: document.getElementById('dev-name-input'),
    devTypeSelect: document.getElementById('dev-type-select'),
    devPlatformSelect: document.getElementById('dev-platform-select'),

    recordMemoryModal: document.getElementById('record-memory-modal'),
    recordMemoryForm: document.getElementById('record-memory-form'),
    memContentInput: document.getElementById('mem-content-input'),
    memTypeSelect: document.getElementById('mem-type-select'),
    memCategoryInput: document.getElementById('mem-category-input'),

    purgeModal: document.getElementById('purge-modal'),
    purgeConfirmInput: document.getElementById('purge-confirm-input'),
    confirmPurgeBtn: document.getElementById('confirm-purge-btn'),

    // Toasts
    toastContainer: document.getElementById('toast-container'),
  };

  // ---------------------------------------------------------------------------
  // 3. Security & Utility Functions
  // ---------------------------------------------------------------------------
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const s = String(str);
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatTimestamp(epochSeconds) {
    if (!epochSeconds) return '-';
    const date = new Date(epochSeconds * 1000);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    dom.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  function getBaseUrl() {
    if (state.serverUrl && state.serverUrl.trim()) {
      return state.serverUrl.trim().replace(/\/$/, '');
    }
    return window.location.origin;
  }

  function getAuthHeaders() {
    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    if (state.authToken && state.authToken.trim()) {
      headers['Authorization'] = `Bearer ${state.authToken.trim()}`;
    }
    return headers;
  }

  async function apiRequest(path, options = {}) {
    const url = `${getBaseUrl()}${path}`;
    const opts = {
      ...options,
      headers: {
        ...getAuthHeaders(),
        ...(options.headers || {}),
      },
    };

    try {
      const response = await fetch(url, opts);
      if (response.status === 401) {
        setConnectionStatus('offline', 'Unauthorized (401)');
        showToast('Authentication failed. Please check your Bearer token in Settings.', 'error');
        throw new Error('Unauthorized');
      }
      if (response.status === 403) {
        showToast('Forbidden (403): You do not have permission for this action.', 'error');
        throw new Error('Forbidden');
      }

      const isJson = response.headers.get('content-type')?.includes('application/json');
      const data = isJson ? await response.json() : await response.text();

      if (!response.ok) {
        const errMsg = (isJson && data?.error?.message) ? data.error.message : `HTTP ${response.status}`;
        throw new Error(errMsg);
      }
      return data;
    } catch (err) {
      if (err.name === 'TypeError' && err.message.includes('fetch')) {
        setConnectionStatus('offline', 'Offline');
        throw new Error('Network error: Unable to reach AURA server.');
      }
      throw err;
    }
  }

  function setConnectionStatus(status, text) {
    dom.statusPill.className = `status-pill status-${status}`;
    dom.statusText.textContent = text;
    state.isConnected = (status === 'ready');
  }

  function openModal(modalEl) {
    if (modalEl) modalEl.classList.remove('hidden');
  }

  function closeModal(modalEl) {
    if (modalEl) modalEl.classList.add('hidden');
  }

  // ---------------------------------------------------------------------------
  // 4. Navigation & View Routing
  // ---------------------------------------------------------------------------
  function switchTab(targetTabId) {
    state.activeTab = targetTabId;
    dom.navTabs.forEach(tab => {
      const isTarget = (tab.dataset.tab === targetTabId);
      tab.classList.toggle('active', isTarget);
      tab.setAttribute('aria-selected', isTarget ? 'true' : 'false');
    });

    dom.viewPanels.forEach(panel => {
      panel.classList.toggle('hidden', panel.id !== targetTabId);
      panel.classList.toggle('active', panel.id === targetTabId);
    });

    // Refresh data on view activation
    if (targetTabId === 'runs-view') loadRuns();
    if (targetTabId === 'approvals-view') loadApprovals();
    if (targetTabId === 'tasks-view') loadTasks();
    if (targetTabId === 'memory-view') loadMemories();
    if (targetTabId === 'files-view') loadArtifacts();
    if (targetTabId === 'devices-view') loadDevices();
    if (targetTabId === 'settings-view') checkSystemHealth();
  }

  function setupNavigation() {
    dom.navTabs.forEach(tab => {
      tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    dom.subtabBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const subtabId = btn.dataset.subtab;
        const parentContainer = btn.closest('.two-column-layout, #memory-view');
        if (!parentContainer) return;

        parentContainer.querySelectorAll('.subtab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        parentContainer.querySelectorAll('.subtab-panel').forEach(p => {
          p.classList.toggle('hidden', p.id !== subtabId);
          p.classList.toggle('active', p.id === subtabId);
        });

        if (subtabId === 'contradictions-subtab') loadContradictions();
        if (subtabId === 'profile-subtab') loadProfile();
        if (subtabId === 'automations-subtab') loadAutomations();
      });
    });

    dom.headerSettingsBtn.addEventListener('click', () => switchTab('settings-view'));
  }

  // ---------------------------------------------------------------------------
  // 5. Chat Controller (Primary Assistant)
  // ---------------------------------------------------------------------------
  function appendMessage(role, content, timestamp = null) {
    dom.welcomeCard?.classList.add('hidden');

    const wrapper = document.createElement('div');
    wrapper.className = `message-wrapper ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    
    // Render code blocks safely
    const formatted = formatMessageContent(content);
    bubble.innerHTML = formatted;

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.textContent = `${role === 'user' ? 'You' : 'AURA'} • ${formatTimestamp(timestamp || Date.now() / 1000)}`;

    wrapper.appendChild(bubble);
    wrapper.appendChild(meta);
    dom.messagesContainer.appendChild(wrapper);
    dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
  }

  function formatMessageContent(rawText) {
    if (!rawText) return '';
    const escaped = escapeHtml(rawText);
    // Format ```code``` blocks
    return escaped.replace(/```([\s\S]*?)```/g, '<pre class="font-mono text-sm"><code>$1</code></pre>');
  }

  async function sendMessage(text) {
    if (!text || state.isProcessing) return null;
    appendMessage('user', text);
    state.isProcessing = true;
    if (dom.loadingIndicator) dom.loadingIndicator.classList.remove('hidden');
    if (dom.chatStatusBar) {
      dom.chatStatusBar.classList.remove('hidden');
      if (dom.chatStatusMsg) dom.chatStatusMsg.textContent = 'AURA is reasoning...';
      if (dom.chatPhaseTag) dom.chatPhaseTag.textContent = 'REASON';
    }

    try {
      const response = await apiRequest('/v1/run', {
        method: 'POST',
        body: JSON.stringify({ prompt: text, user_input: text }),
      });

      const reply = response.content || response.response || response.text || 'Action acknowledged.';
      appendMessage('assistant', reply);
      return response;
    } catch (err) {
      appendMessage('assistant', `⚠️ Execution Error: ${err.message}`);
      showToast(err.message, 'error');
      throw err;
    } finally {
      state.isProcessing = false;
      if (dom.loadingIndicator) dom.loadingIndicator.classList.add('hidden');
      if (dom.chatStatusBar) dom.chatStatusBar.classList.add('hidden');
    }
  }

  async function checkServerHealth() {
    return await checkSystemHealth();
  }

  async function loadPreferences() {
    try {
      const savedName = localStorage.getItem('aura_pref_name') || '';
      const savedInst = localStorage.getItem('aura_pref_instructions') || '';
      if (dom.prefName) dom.prefName.value = savedName;
      if (dom.prefInstructions) dom.prefInstructions.value = savedInst;
    } catch (err) {
      // ignore
    }
  }

  async function loadToolsCatalog() {
    const toolsContainer = document.getElementById('tools-list-container');
    if (!toolsContainer) return;
    try {
      toolsContainer.innerHTML = '<div class="empty-state">Loading registered tools catalog...</div>';
      const data = await apiRequest('/v1/meta/tools').catch(() => ({ tools: [] }));
      const tools = data.tools || [];
      if (tools.length === 0) {
        toolsContainer.innerHTML = '<div class="empty-state">No custom tools registered in catalog.</div>';
        return;
      }
      toolsContainer.innerHTML = tools.map(t => `
        <div class="item-card">
          <div class="card-title-row">
            <strong>${escapeHtml(t.name || t.id)}</strong>
            <span class="badge risk_low">${escapeHtml(t.category || 'tool')}</span>
          </div>
          <div class="card-desc">${escapeHtml(t.description || 'Tool capability')}</div>
        </div>
      `).join('');
    } catch (err) {
      toolsContainer.innerHTML = `<div class="empty-state text-danger">Failed to load tools: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function handleChatSubmit(e) {
    e.preventDefault();
    const text = dom.userInput.value.trim();
    if (!text || state.isProcessing) return;
    dom.userInput.value = '';
    await sendMessage(text);
  }

  // ---------------------------------------------------------------------------
  // 6. M59 Autonomous Agent Runs Controller
  // ---------------------------------------------------------------------------
  async function loadRuns() {
    dom.runsListContainer.innerHTML = '<div class="empty-state">Loading runs...</div>';
    try {
      const data = await apiRequest('/v1/agent/runs?limit=50');
      const runs = data.runs || [];
      renderRunsList(runs);

      // Update badge for active runs
      const activeCount = runs.filter(r => ['running', 'waiting_approval', 'paused'].includes(r.status)).length;
      if (activeCount > 0) {
        dom.runsBadge.textContent = activeCount;
        dom.runsBadge.classList.remove('hidden');
      } else {
        dom.runsBadge.classList.add('hidden');
      }
    } catch (err) {
      dom.runsListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load agent runs: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderRunsList(runs) {
    if (!runs || runs.length === 0) {
      dom.runsListContainer.innerHTML = '<div class="empty-state">No agent runs recorded. Click "New Agent Run" to start.</div>';
      return;
    }

    const search = dom.runsSearch.value.toLowerCase();
    const statusFilter = dom.runsStatusFilter.value;

    const filtered = runs.filter(r => {
      const matchesSearch = !search || (r.intent && r.intent.toLowerCase().includes(search));
      const matchesStatus = !statusFilter || (r.status === statusFilter);
      return matchesSearch && matchesStatus;
    });

    if (filtered.length === 0) {
      dom.runsListContainer.innerHTML = '<div class="empty-state">No matching agent runs found.</div>';
      return;
    }

    dom.runsListContainer.innerHTML = '';
    filtered.forEach(run => {
      const card = document.createElement('div');
      card.className = `item-card ${state.selectedRunId === run.run_id ? 'active' : ''}`;
      card.innerHTML = `
        <div class="card-title-row">
          <span class="card-title">${escapeHtml(run.intent || 'Autonomous Run')}</span>
          <span class="badge status-${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
        </div>
        <div class="card-desc">Phase: <strong>${escapeHtml(run.current_phase || 'receive')}</strong> • Depth: ${run.depth || 0}</div>
        <div class="card-meta-row">
          <span>ID: <code class="font-mono text-sm">${escapeHtml(run.run_id.substring(0, 12))}</code></span>
          <span>${formatTimestamp(run.created_at)}</span>
        </div>
      `;
      card.addEventListener('click', () => selectRun(run.run_id));
      dom.runsListContainer.appendChild(card);
    });
  }

  async function selectRun(runId) {
    state.selectedRunId = runId;
    renderRunsList(await apiRequest('/v1/agent/runs').then(d => d.runs || []).catch(() => []));
    dom.runDetailContainer.innerHTML = '<div class="empty-state">Loading run details & execution steps...</div>';

    try {
      const [run, eventsData] = await Promise.all([
        apiRequest(`/v1/agent/runs/${runId}`),
        apiRequest(`/v1/agent/runs/${runId}/events`).catch(() => ({ events: [] })),
      ]);

      renderRunDetail(run, eventsData.events || []);
    } catch (err) {
      dom.runDetailContainer.innerHTML = `<div class="empty-state text-danger">Error: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderRunDetail(run, events) {
    const stepsHtml = (run.steps && run.steps.length > 0)
      ? run.steps.map(s => `
        <div class="item-card" style="margin-bottom: 8px;">
          <div class="card-title-row">
            <span><strong>Step ${s.step_number}:</strong> ${escapeHtml(s.action_name)} (<code>${escapeHtml(s.action_type)}</code>)</span>
            <span class="badge risk_${escapeHtml(s.risk_level)}">${escapeHtml(s.risk_level)}</span>
          </div>
          <div class="card-desc">${escapeHtml(s.description || 'Executing plan step')}</div>
          <div class="card-meta-row">
            <span>Status: <strong class="badge status-${escapeHtml(s.status)}">${escapeHtml(s.status)}</strong></span>
            <span>Verification: <strong>${escapeHtml(s.verification_status || 'unverified')}</strong></span>
            <span>Duration: ${s.duration_ms ? s.duration_ms.toFixed(1) + 'ms' : '-'}</span>
          </div>
        </div>
      `).join('')
      : '<p class="text-muted">No steps generated yet.</p>';

    const eventsHtml = (events && events.length > 0)
      ? events.map(e => `
        <li style="margin-bottom: 6px; font-size: 0.8rem;">
          <span class="font-mono text-muted">[${formatTimestamp(e.created_at)}]</span>
          <strong>${escapeHtml(e.event_type)}</strong> (${escapeHtml(e.phase)})
        </li>
      `).join('')
      : '<p class="text-muted">No timeline events recorded.</p>';

    const canPause = run.status === 'running';
    const canResume = ['paused', 'waiting_approval'].includes(run.status);
    const canCancel = ['running', 'pending', 'paused', 'waiting_approval'].includes(run.status);

    dom.runDetailContainer.innerHTML = `
      <div class="panel-header" style="padding: 0 0 16px 0; background: transparent;">
        <div>
          <h3>${escapeHtml(run.intent)}</h3>
          <p class="font-mono text-sm text-muted">Run ID: ${escapeHtml(run.run_id)} • Tenant: ${escapeHtml(run.tenant_id)}</p>
        </div>
        <div class="panel-actions">
          ${canPause ? `<button id="run-pause-btn" class="action-btn secondary small">⏸️ Pause</button>` : ''}
          ${canResume ? `<button id="run-resume-btn" class="action-btn secondary small">▶️ Resume</button>` : ''}
          ${canCancel ? `<button id="run-cancel-btn" class="action-btn danger small">🛑 Cancel</button>` : ''}
        </div>
      </div>

      <div class="telemetry-grid" style="margin-bottom: 20px;">
        <div class="telemetry-box">
          <div class="telemetry-metric">${escapeHtml(run.status)}</div>
          <div class="telemetry-label">Lifecycle Status</div>
        </div>
        <div class="telemetry-box">
          <div class="telemetry-metric">${escapeHtml(run.current_phase)}</div>
          <div class="telemetry-label">Current Phase</div>
        </div>
        <div class="telemetry-box">
          <div class="telemetry-metric">${run.iteration_count || 0} / ${run.budget?.max_iterations || 25}</div>
          <div class="telemetry-label">Iterations</div>
        </div>
        <div class="telemetry-box">
          <div class="telemetry-metric">${run.tool_call_count || 0} / ${run.budget?.max_tool_calls || 50}</div>
          <div class="telemetry-label">Tool Calls</div>
        </div>
      </div>

      <h4 style="margin-bottom: 10px;">Execution Plan & Steps</h4>
      <div style="margin-bottom: 20px;">${stepsHtml}</div>

      <h4 style="margin-bottom: 10px;">Execution Event Timeline</h4>
      <ul style="list-style: none; padding-left: 0;">${eventsHtml}</ul>
    `;

    document.getElementById('run-pause-btn')?.addEventListener('click', async () => {
      await apiRequest(`/v1/agent/runs/${run.run_id}/pause`, { method: 'POST', body: '{}' });
      showToast('Run paused', 'info');
      selectRun(run.run_id);
    });

    document.getElementById('run-resume-btn')?.addEventListener('click', async () => {
      await apiRequest(`/v1/agent/runs/${run.run_id}/resume`, { method: 'POST', body: '{}' });
      showToast('Run resumed', 'info');
      selectRun(run.run_id);
    });

    document.getElementById('run-cancel-btn')?.addEventListener('click', async () => {
      await apiRequest(`/v1/agent/runs/${run.run_id}/cancel`, { method: 'POST', body: '{}' });
      showToast('Run cancelled', 'info');
      selectRun(run.run_id);
    });
  }

  // ---------------------------------------------------------------------------
  // 7. M48 / M59 Approvals Controller
  // ---------------------------------------------------------------------------
  async function loadApprovals() {
    dom.approvalsContainer.innerHTML = '<div class="empty-state">Loading pending approvals...</div>';
    try {
      const data = await apiRequest('/v1/approvals');
      const approvals = data.approvals || [];
      renderApprovals(approvals);

      if (approvals.length > 0) {
        dom.approvalsBadge.textContent = approvals.length;
        dom.approvalsBadge.classList.remove('hidden');
      } else {
        dom.approvalsBadge.classList.add('hidden');
      }
    } catch (err) {
      dom.approvalsContainer.innerHTML = `<div class="empty-state text-danger">Failed to load approvals: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderApprovals(approvals) {
    if (!approvals || approvals.length === 0) {
      dom.approvalsContainer.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1;">
          <div style="font-size: 2rem; margin-bottom: 10px;">✅</div>
          <h3>All clear!</h3>
          <p>No high-risk actions currently awaiting human authorization.</p>
        </div>
      `;
      return;
    }

    dom.approvalsContainer.innerHTML = '';
    approvals.forEach(appr => {
      const card = document.createElement('div');
      const isCritical = (appr.risk_level === 'critical');
      card.className = `approval-card ${isCritical ? 'critical-risk' : 'high-risk'}`;
      card.innerHTML = `
        <div class="approval-header">
          <span class="approval-action-title">⚠️ ${escapeHtml(appr.action || 'High-Risk Action')}</span>
          <span class="badge risk_${escapeHtml(appr.risk_level || 'high')}">${escapeHtml(appr.risk_level || 'HIGH')}</span>
        </div>
        <div class="approval-target">Target: ${escapeHtml(JSON.stringify(appr.parameters || {}))}</div>
        <div class="approval-explanation">${escapeHtml(appr.reason || 'Operation requires explicit confirmation before execution.')}</div>
        <div class="card-meta-row">
          <span>Nonce: <code class="font-mono text-sm">${escapeHtml(appr.nonce ? appr.nonce.substring(0, 10) : 'N/A')}</code></span>
          <span>Expires: ${formatTimestamp(appr.expires_at)}</span>
        </div>
        <div class="approval-actions">
          <button class="action-btn primary approve-btn" data-id="${escapeHtml(appr.id || appr.approval_id)}">✅ Approve Action</button>
          <button class="action-btn danger reject-btn" data-id="${escapeHtml(appr.id || appr.approval_id)}">❌ Reject</button>
        </div>
      `;

      card.querySelector('.approve-btn').addEventListener('click', () => decideApproval(appr.id || appr.approval_id, 'approved'));
      card.querySelector('.reject-btn').addEventListener('click', () => decideApproval(appr.id || appr.approval_id, 'rejected'));

      dom.approvalsContainer.appendChild(card);
    });
  }

  async function decideApproval(approvalId, decision) {
    try {
      await apiRequest(`/v1/approvals/${approvalId}/decide`, {
        method: 'POST',
        body: JSON.stringify({
          decision: decision,
          reason: `Decided '${decision}' via AURA Web Product UI.`,
        }),
      });
      showToast(`Action ${decision} successfully.`, decision === 'approved' ? 'success' : 'info');
      loadApprovals();
    } catch (err) {
      showToast(`Approval decision failed: ${err.message}`, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // 8. Tasks & Automations Controller
  // ---------------------------------------------------------------------------
  async function loadTasks() {
    dom.tasksListContainer.innerHTML = '<div class="empty-state">Loading tasks...</div>';
    try {
      const data = await apiRequest('/v1/tasks?limit=50').catch(() => ({ tasks: [] }));
      const tasks = data.tasks || [];
      renderTasksList(tasks);
    } catch (err) {
      dom.tasksListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load tasks: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderTasksList(tasks) {
    if (!tasks || tasks.length === 0) {
      dom.tasksListContainer.innerHTML = '<div class="empty-state">No background tasks found.</div>';
      return;
    }

    dom.tasksListContainer.innerHTML = '';
    tasks.forEach(t => {
      const card = document.createElement('div');
      card.className = 'item-card';
      card.innerHTML = `
        <div class="card-title-row">
          <span class="card-title">${escapeHtml(t.title || t.task || 'Background Task')}</span>
          <span class="badge status-${escapeHtml(t.status)}">${escapeHtml(t.status)}</span>
        </div>
        <div class="card-meta-row">
          <span>ID: <code class="font-mono text-sm">${escapeHtml(t.id ? t.id.substring(0, 10) : '-')}</code></span>
          <span>${formatTimestamp(t.created_at)}</span>
        </div>
      `;
      card.addEventListener('click', () => selectTask(t.id));
      dom.tasksListContainer.appendChild(card);
    });
  }

  async function selectTask(taskId) {
    dom.taskDetailContainer.innerHTML = '<div class="empty-state">Loading task detail...</div>';
    try {
      const task = await apiRequest(`/v1/tasks/${taskId}`);
      dom.taskDetailContainer.innerHTML = `
        <div class="panel-header" style="padding: 0 0 16px 0; background: transparent;">
          <div>
            <h3>${escapeHtml(task.title || 'Task Details')}</h3>
            <p class="font-mono text-sm text-muted">ID: ${escapeHtml(task.id)}</p>
          </div>
          <div class="panel-actions">
            ${['pending', 'running'].includes(task.status) ? `<button id="task_cancel_btn" class="action-btn danger small">Cancel Task</button>` : ''}
          </div>
        </div>
        <div class="telemetry-grid" style="margin-bottom: 20px;">
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(task.status)}</div>
            <div class="telemetry-label">Status</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${task.steps ? task.steps.length : 0}</div>
            <div class="telemetry-label">Steps Completed</div>
          </div>
        </div>
        <h4>Execution Steps</h4>
        <div style="margin-top: 10px;">
          ${(task.steps && task.steps.length > 0) 
            ? task.steps.map(s => `<div class="item-card" style="margin-bottom: 8px;"><strong>Step ${s.step_number}:</strong> ${escapeHtml(s.action_name || s.name)} - <span class="badge status-${escapeHtml(s.status)}">${escapeHtml(s.status)}</span></div>`).join('') 
            : '<p class="text-muted">No step details available.</p>'}
        </div>
      `;

      document.getElementById('task_cancel_btn')?.addEventListener('click', async () => {
        await apiRequest(`/v1/tasks/${task.id}/cancel`, { method: 'POST' });
        showToast('Task cancelled', 'info');
        selectTask(task.id);
      });
    } catch (err) {
      dom.taskDetailContainer.innerHTML = `<div class="empty-state text-danger">Failed to load task: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function loadAutomations() {
    dom.tasksListContainer.innerHTML = '<div class="empty-state">Loading automations...</div>';
    try {
      const data = await apiRequest('/v1/automations');
      const autos = data.automations || [];
      if (autos.length === 0) {
        dom.tasksListContainer.innerHTML = '<div class="empty-state">No scheduled automations found.</div>';
        return;
      }
      dom.tasksListContainer.innerHTML = '';
      autos.forEach(a => {
        const card = document.createElement('div');
        card.className = 'item-card';
        card.innerHTML = `
          <div class="card-title-row">
            <span class="card-title">${escapeHtml(a.name)}</span>
            <span class="badge status-${escapeHtml(a.status)}">${escapeHtml(a.status)}</span>
          </div>
          <div class="card-desc">Trigger: <code>${escapeHtml(a.trigger_type)}</code> • Max Runs: ${a.max_runs || '∞'}</div>
          <div class="card-meta-row">
            <span>Next: ${formatTimestamp(a.next_fire_at)}</span>
            <span>Runs: ${a.total_runs || 0}</span>
          </div>
        `;
        dom.tasksListContainer.appendChild(card);
      });
    } catch (err) {
      dom.tasksListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load automations: ${escapeHtml(err.message)}</div>`;
    }
  }

  // ---------------------------------------------------------------------------
  // 9. M56 Cognitive Memory Controller
  // ---------------------------------------------------------------------------
  async function loadMemories() {
    dom.memoriesListContainer.innerHTML = '<div class="empty-state">Loading memories...</div>';
    const type = dom.memoryTypeFilter.value;
    const stateVal = dom.memoryStateFilter.value;
    const query = dom.memorySearchInput.value.trim();

    let q = `/v1/cognitive-memory/query?lifecycle_state=${encodeURIComponent(stateVal)}`;
    if (type) q += `&memory_type=${encodeURIComponent(type)}`;
    if (query) q += `&query=${encodeURIComponent(query)}`;

    try {
      const data = await apiRequest(q);
      const mems = data.memories || [];
      renderMemoriesList(mems);
    } catch (err) {
      dom.memoriesListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load cognitive memory: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderMemoriesList(mems) {
    if (!mems || mems.length === 0) {
      dom.memoriesListContainer.innerHTML = '<div class="empty-state" style="grid-column: 1 / -1;">No cognitive memories match criteria.</div>';
      return;
    }

    dom.memoriesListContainer.innerHTML = '';
    mems.forEach(mem => {
      const card = document.createElement('div');
      card.className = 'memory-card';
      card.innerHTML = `
        <div class="card-title-row">
          <span class="memory-provenance">${escapeHtml(mem.provenance_type || 'USER_EXPLICIT')}</span>
          <span class="badge risk_low">${escapeHtml(mem.memory_type || 'semantic')}</span>
        </div>
        <div class="memory-content">${escapeHtml(mem.content)}</div>
        <div class="card-meta-row">
          <span>Category: <strong>${escapeHtml(mem.category || 'general')}</strong></span>
          <span>Conf: <strong>${(mem.confidence * 100).toFixed(0)}%</strong></span>
        </div>
        <div class="card-meta-row" style="margin-top: 6px; border-top: 1px solid var(--border-subtle); padding-top: 6px;">
          <button class="action-btn secondary small mem-feedback-btn" data-id="${escapeHtml(mem.memory_id)}" title="Confirm accuracy">👍</button>
          <button class="action-btn danger small mem-delete-btn" data-id="${escapeHtml(mem.memory_id)}" title="Delete memory permanently">🗑️ Delete</button>
        </div>
      `;

      card.querySelector('.mem-delete-btn').addEventListener('click', async () => {
        if (!confirm('Are you sure you want to permanently delete this memory?')) return;
        try {
          await apiRequest(`/v1/cognitive-memory/memories/${mem.memory_id}`, { method: 'DELETE' });
          showToast('Memory deleted and wiped from persistence.', 'success');
          loadMemories();
        } catch (e) {
          showToast(`Delete failed: ${e.message}`, 'error');
        }
      });

      card.querySelector('.mem-feedback-btn').addEventListener('click', async () => {
        try {
          await apiRequest('/v1/cognitive-memory/feedback', {
            method: 'POST',
            body: JSON.stringify({
              target_memory_id: mem.memory_id,
              feedback_type: 'confirmation',
            }),
          });
          showToast('Feedback recorded.', 'success');
          loadMemories();
        } catch (e) {
          showToast(`Feedback failed: ${e.message}`, 'error');
        }
      });

      dom.memoriesListContainer.appendChild(card);
    });
  }

  async function loadContradictions() {
    dom.contradictionsListContainer.innerHTML = '<div class="empty-state">Loading contradictions...</div>';
    try {
      const data = await apiRequest('/v1/cognitive-memory/contradictions');
      const contras = data.contradictions || [];
      if (contras.length === 0) {
        dom.contradictionsListContainer.innerHTML = '<div class="empty-state">No active contradictions found.</div>';
        dom.contradictionsBadge.classList.add('hidden');
        return;
      }
      dom.contradictionsBadge.textContent = contras.length;
      dom.contradictionsBadge.classList.remove('hidden');

      dom.contradictionsListContainer.innerHTML = '';
      contras.forEach(c => {
        const card = document.createElement('div');
        card.className = 'item-card';
        card.innerHTML = `
          <div class="card-title-row">
            <span class="card-title text-danger">⚠️ Contradiction on: ${escapeHtml(c.key || 'key')}</span>
            <span class="badge status-failed">${escapeHtml(c.status || 'detected')}</span>
          </div>
          <div class="card-desc">Existing: "${escapeHtml(c.existing_content)}" vs Incoming: "${escapeHtml(c.incoming_content)}"</div>
          <div class="card-meta-row" style="margin-top: 10px;">
            <button class="action-btn primary small resolve-btn" data-id="${escapeHtml(c.contradiction_id)}" data-strategy="keep_new">Keep New</button>
            <button class="action-btn secondary small resolve-btn" data-id="${escapeHtml(c.contradiction_id)}" data-strategy="keep_existing">Keep Existing</button>
          </div>
        `;
        card.querySelectorAll('.resolve-btn').forEach(btn => {
          btn.addEventListener('click', async () => {
            await apiRequest(`/v1/cognitive-memory/contradictions/${c.contradiction_id}/resolve`, {
              method: 'POST',
              body: JSON.stringify({ resolution_strategy: btn.dataset.strategy }),
            });
            showToast('Contradiction resolved.', 'success');
            loadContradictions();
          });
        });
        dom.contradictionsListContainer.appendChild(card);
      });
    } catch (err) {
      dom.contradictionsListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function loadProfile() {
    try {
      const profile = await apiRequest('/v1/cognitive-memory/profile');
      if (profile) {
        if (profile.communication_style) dom.profCommStyle.value = profile.communication_style;
        if (profile.proactivity_level) dom.profProactivity.value = profile.proactivity_level;
        if (profile.custom_directives) dom.profInstructions.value = profile.custom_directives.join('\n');
      }
    } catch (err) {
      console.warn('Profile load warning:', err);
    }
  }

  // ---------------------------------------------------------------------------
  // 10. M57 Multimodal / Files Controller
  // ---------------------------------------------------------------------------
  async function loadArtifacts() {
    dom.artifactsListContainer.innerHTML = '<div class="empty-state">Loading artifacts...</div>';
    try {
      const data = await apiRequest('/v1/multimodal/artifacts?limit=50');
      const artifacts = data.artifacts || [];
      renderArtifactsList(artifacts);
    } catch (err) {
      dom.artifactsListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load files: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderArtifactsList(artifacts) {
    if (!artifacts || artifacts.length === 0) {
      dom.artifactsListContainer.innerHTML = '<div class="empty-state">No files uploaded. Drag & drop files above.</div>';
      return;
    }

    dom.artifactsListContainer.innerHTML = '';
    artifacts.forEach(art => {
      const card = document.createElement('div');
      card.className = `item-card ${state.selectedArtifactId === art.artifact_id ? 'active' : ''}`;
      card.innerHTML = `
        <div class="card-title-row">
          <span class="card-title">📄 ${escapeHtml(art.filename || art.artifact_id)}</span>
          <span class="badge risk_low">${escapeHtml(art.media_type || art.format || 'file')}</span>
        </div>
        <div class="card-meta-row">
          <span>Size: ${(art.size_bytes / 1024).toFixed(1)} KB</span>
          <span>${formatTimestamp(art.created_at)}</span>
        </div>
      `;
      card.addEventListener('click', () => selectArtifact(art.artifact_id));
      dom.artifactsListContainer.appendChild(card);
    });
  }

  async function selectArtifact(artifactId) {
    state.selectedArtifactId = artifactId;
    dom.artifactDetailContainer.innerHTML = '<div class="empty-state">Loading artifact detail...</div>';

    try {
      const art = await apiRequest(`/v1/multimodal/artifacts/${artifactId}`);
      dom.artifactDetailContainer.innerHTML = `
        <div class="panel-header" style="padding: 0 0 16px 0; background: transparent;">
          <div>
            <h3>${escapeHtml(art.filename)}</h3>
            <p class="font-mono text-sm text-muted">ID: ${escapeHtml(art.artifact_id)}</p>
          </div>
          <div class="panel-actions">
            <button id="art-ocr-btn" class="action-btn secondary small">🔍 OCR Extract</button>
            <button id="art-del-btn" class="action-btn danger small">🗑️ Delete</button>
          </div>
        </div>

        <div class="telemetry-grid" style="margin-bottom: 20px;">
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(art.format || '-')}</div>
            <div class="telemetry-label">Format</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${(art.size_bytes / 1024).toFixed(1)} KB</div>
            <div class="telemetry-label">File Size</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(art.provenance || 'USER_UPLOADED')}</div>
            <div class="telemetry-label">Provenance</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(art.lifecycle_state || 'active')}</div>
            <div class="telemetry-label">Lifecycle</div>
          </div>
        </div>

        <h4>Extracted Text / OCR Content</h4>
        <div id="ocr-result-box" class="item-card" style="margin-top: 10px; min-height: 80px; font-family: var(--font-mono); font-size: 0.85rem;">
          ${escapeHtml(art.metadata?.extracted_text || 'Click "OCR Extract" to process document/image text.')}
        </div>
      `;

      document.getElementById('art-ocr-btn')?.addEventListener('click', async () => {
        showToast('Running OCR processing pipeline...', 'info');
        try {
          const res = await apiRequest('/v1/multimodal/process', {
            method: 'POST',
            body: JSON.stringify({
              artifact_id: art.artifact_id,
              operation: 'ocr',
            }),
          });
          const txt = res.result?.extracted_text || res.result?.summary || 'OCR completed.';
          document.getElementById('ocr-result-box').textContent = txt;
          showToast('OCR complete.', 'success');
        } catch (e) {
          showToast(`OCR failed: ${e.message}`, 'error');
        }
      });

      document.getElementById('art-del-btn')?.addEventListener('click', async () => {
        if (!confirm('Permanently delete this artifact?')) return;
        try {
          await apiRequest(`/v1/multimodal/artifacts/${art.artifact_id}`, { method: 'DELETE' });
          showToast('Artifact deleted.', 'success');
          loadArtifacts();
          dom.artifactDetailContainer.innerHTML = '<div class="empty-state">Select an artifact to view details.</div>';
        } catch (e) {
          showToast(`Delete failed: ${e.message}`, 'error');
        }
      });
    } catch (err) {
      dom.artifactDetailContainer.innerHTML = `<div class="empty-state text-danger">Failed to load: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function handleFileUpload(file) {
    if (file.size > 10 * 1024 * 1024) {
      showToast(`File '${file.name}' exceeds maximum limit of 10MB`, 'error');
      return;
    }

    showToast(`Uploading '${file.name}'...`, 'info');
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const base64Data = reader.result.split(',')[1] || '';
        await apiRequest('/v1/multimodal/artifacts', {
          method: 'POST',
          body: JSON.stringify({
            filename: file.name,
            content_base64: base64Data,
            format: file.name.split('.').pop()?.toLowerCase() || 'bin',
            provenance: 'USER_UPLOADED',
          }),
        });
        showToast(`'${file.name}' uploaded successfully.`, 'success');
        loadArtifacts();
      } catch (e) {
        showToast(`Upload failed: ${e.message}`, 'error');
      }
    };
    reader.readAsDataURL(file);
  }

  // ---------------------------------------------------------------------------
  // 11. M58 Devices Controller
  // ---------------------------------------------------------------------------
  async function loadDevices() {
    dom.devicesListContainer.innerHTML = '<div class="empty-state">Loading registered devices...</div>';
    try {
      const data = await apiRequest('/v1/devices');
      const devices = data.devices || [];
      renderDevicesList(devices);
    } catch (err) {
      dom.devicesListContainer.innerHTML = `<div class="empty-state text-danger">Failed to load devices: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderDevicesList(devices) {
    if (!devices || devices.length === 0) {
      dom.devicesListContainer.innerHTML = '<div class="empty-state">No devices registered. Click "Register Device" to connect.</div>';
      return;
    }

    dom.devicesListContainer.innerHTML = '';
    devices.forEach(dev => {
      const card = document.createElement('div');
      card.className = `item-card ${state.selectedDeviceId === dev.device_id ? 'active' : ''}`;
      card.innerHTML = `
        <div class="card-title-row">
          <span class="card-title">💻 ${escapeHtml(dev.name)}</span>
          <span class="badge status-${escapeHtml(dev.trust_state || 'verified')}">${escapeHtml(dev.trust_state || 'verified')}</span>
        </div>
        <div class="card-desc">Platform: <strong>${escapeHtml(dev.platform)}</strong> • Type: ${escapeHtml(dev.device_type)}</div>
        <div class="card-meta-row">
          <span>ID: <code class="font-mono text-sm">${escapeHtml(dev.device_id.substring(0, 10))}</code></span>
          <span>${formatTimestamp(dev.created_at)}</span>
        </div>
      `;
      card.addEventListener('click', () => selectDevice(dev.device_id));
      dom.devicesListContainer.appendChild(card);
    });
  }

  async function selectDevice(deviceId) {
    state.selectedDeviceId = deviceId;
    dom.deviceDetailContainer.innerHTML = '<div class="empty-state">Loading device detail...</div>';

    try {
      const [dev, capsData, execsData] = await Promise.all([
        apiRequest(`/v1/devices/${deviceId}`),
        apiRequest(`/v1/devices/${deviceId}/capabilities`).catch(() => ({ capabilities: [] })),
        apiRequest(`/v1/devices/${deviceId}/executions`).catch(() => ({ executions: [] })),
      ]);

      const capsHtml = (capsData.capabilities && capsData.capabilities.length > 0)
        ? capsData.capabilities.map(c => `
          <div class="item-card" style="margin-bottom: 6px;">
            <div class="card-title-row">
              <span><code>${escapeHtml(c.capability_name)}</code></span>
              <span class="badge status-${escapeHtml(c.auth_status)}">${escapeHtml(c.auth_status)}</span>
            </div>
            <div class="card-meta-row">Risk: <strong class="risk_${escapeHtml(c.risk_level)}">${escapeHtml(c.risk_level)}</strong></div>
          </div>
        `).join('')
        : '<p class="text-muted">No capability records registered.</p>';

      dom.deviceDetailContainer.innerHTML = `
        <div class="panel-header" style="padding: 0 0 16px 0; background: transparent;">
          <div>
            <h3>${escapeHtml(dev.name)}</h3>
            <p class="font-mono text-sm text-muted">ID: ${escapeHtml(dev.device_id)} • OS: ${escapeHtml(dev.platform)}</p>
          </div>
          <div class="panel-actions">
            <button id="dev-auth-btn" class="action-btn secondary small">🛡️ Authorize</button>
            <button id="dev-suspend-btn" class="action-btn secondary small">⏸️ Suspend</button>
            <button id="dev-revoke-btn" class="action-btn danger small">🚫 Revoke</button>
          </div>
        </div>

        <div class="telemetry-grid" style="margin-bottom: 20px;">
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(dev.trust_state)}</div>
            <div class="telemetry-label">Trust State</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${escapeHtml(dev.platform)}</div>
            <div class="telemetry-label">Platform</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${capsData.capabilities ? capsData.capabilities.length : 0}</div>
            <div class="telemetry-label">Capabilities</div>
          </div>
          <div class="telemetry-box">
            <div class="telemetry-metric">${execsData.executions ? execsData.executions.length : 0}</div>
            <div class="telemetry-label">Executions</div>
          </div>
        </div>

        <h4>Authorized Capabilities</h4>
        <div style="margin-top: 10px; margin-bottom: 20px;">${capsHtml}</div>
      `;

      document.getElementById('dev-auth-btn')?.addEventListener('click', async () => {
        await apiRequest(`/v1/devices/${dev.device_id}/trust`, { method: 'PUT', body: JSON.stringify({ trust_state: 'authorized' }) });
        showToast('Device state updated to Authorized', 'success');
        selectDevice(dev.device_id);
      });

      document.getElementById('dev-suspend-btn')?.addEventListener('click', async () => {
        await apiRequest(`/v1/devices/${dev.device_id}/trust`, { method: 'PUT', body: JSON.stringify({ trust_state: 'suspended' }) });
        showToast('Device suspended', 'info');
        selectDevice(dev.device_id);
      });

      document.getElementById('dev-revoke-btn')?.addEventListener('click', async () => {
        if (!confirm('Revoke this device? All capability execution will be immediately blocked.')) return;
        await apiRequest(`/v1/devices/${dev.device_id}/trust`, { method: 'PUT', body: JSON.stringify({ trust_state: 'revoked' }) });
        showToast('Device revoked', 'error');
        selectDevice(dev.device_id);
      });
    } catch (err) {
      dom.deviceDetailContainer.innerHTML = `<div class="empty-state text-danger">Failed to load: ${escapeHtml(err.message)}</div>`;
    }
  }

  // ---------------------------------------------------------------------------
  // 12. Settings & Health Diagnostics Controller
  // ---------------------------------------------------------------------------
  async function checkSystemHealth() {
    dom.diagStatus.textContent = 'Checking...';
    try {
      const [health, ready] = await Promise.all([
        apiRequest('/health').catch(() => ({ status: 'unreachable' })),
        apiRequest('/ready').catch(() => ({ database: 'unknown' })),
      ]);

      setConnectionStatus(health.status === 'healthy' ? 'ready' : 'offline', health.status === 'healthy' ? 'Connected' : 'Degraded');
      dom.diagStatus.textContent = health.status || 'unknown';
      dom.diagEnv.textContent = health.environment || 'production';
      dom.diagUptime.textContent = health.uptime_seconds ? `${health.uptime_seconds}s` : '-';
      dom.diagDb.textContent = ready.database || 'in-memory';
    } catch (err) {
      setConnectionStatus('offline', 'Disconnected');
      dom.diagStatus.textContent = 'Offline';
    }
  }

  // ---------------------------------------------------------------------------
  // 13. Initialization & Event Bindings
  // ---------------------------------------------------------------------------
  function setupEventListeners() {
    setupNavigation();

    // Chat
    dom.chatForm.addEventListener('submit', handleChatSubmit);
    dom.clearBtn.addEventListener('click', () => {
      dom.messagesContainer.innerHTML = '';
      dom.welcomeCard?.classList.remove('hidden');
    });
    dom.launchRunBtn.addEventListener('click', () => openModal(dom.newRunModal));

    document.querySelectorAll('.suggestion-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        dom.userInput.value = chip.dataset.prompt;
        dom.chatForm.dispatchEvent(new Event('submit'));
      });
    });

    // Modals
    dom.newRunModalBtn.addEventListener('click', () => openModal(dom.newRunModal));
    dom.registerDeviceModalBtn.addEventListener('click', () => openModal(dom.registerDeviceModal));
    dom.newMemoryModalBtn.addEventListener('click', () => openModal(dom.recordMemoryModal));
    dom.purgeTenantModalBtn.addEventListener('click', () => {
      dom.purgeConfirmInput.value = '';
      dom.confirmPurgeBtn.disabled = true;
      openModal(dom.purgeModal);
    });

    dom.modalCloseBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const modalId = btn.dataset.modal || btn.closest('.modal-backdrop')?.id;
        if (modalId) closeModal(document.getElementById(modalId));
      });
    });

    // Form Submissions
    dom.newRunForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const intent = dom.runIntentInput.value.trim();
      if (!intent) return;
      closeModal(dom.newRunModal);
      showToast('Launching autonomous agent run...', 'info');

      try {
        const res = await apiRequest('/v1/agent/runs', {
          method: 'POST',
          body: JSON.stringify({
            intent: intent,
            auto_execute: true,
            budget: {
              timeout_seconds: parseFloat(dom.runTimeoutInput.value) || 300,
            },
          }),
        });
        showToast('Run launched successfully.', 'success');
        switchTab('runs-view');
        selectRun(res.run_id);
      } catch (err) {
        showToast(`Failed to launch run: ${err.message}`, 'error');
      }
    });

    dom.registerDeviceForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = dom.devNameInput.value.trim();
      if (!name) return;
      closeModal(dom.registerDeviceModal);
      try {
        await apiRequest('/v1/devices', {
          method: 'POST',
          body: JSON.stringify({
            name: name,
            device_type: dom.devTypeSelect.value,
            platform: dom.devPlatformSelect.value,
          }),
        });
        showToast('Device registered.', 'success');
        loadDevices();
      } catch (err) {
        showToast(`Failed to register device: ${err.message}`, 'error');
      }
    });

    dom.recordMemoryForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const content = dom.memContentInput.value.trim();
      if (!content) return;
      closeModal(dom.recordMemoryModal);
      try {
        await apiRequest('/v1/cognitive-memory/memories', {
          method: 'POST',
          body: JSON.stringify({
            content: content,
            memory_type: dom.memTypeSelect.value,
            category: dom.memCategoryInput.value.trim() || 'general',
          }),
        });
        showToast('Memory saved.', 'success');
        loadMemories();
      } catch (err) {
        showToast(`Failed to record memory: ${err.message}`, 'error');
      }
    });

    dom.purgeConfirmInput.addEventListener('input', () => {
      dom.confirmPurgeBtn.disabled = (dom.purgeConfirmInput.value.trim() !== 'PURGE');
    });

    dom.confirmPurgeBtn.addEventListener('click', async () => {
      closeModal(dom.purgeModal);
      showToast('Purging all tenant data...', 'info');
      try {
        await apiRequest(`/v1/agent/tenants/${state.principal.userId}/purge`, { method: 'DELETE' });
        showToast('Tenant data permanently wiped.', 'success');
        loadMemories();
        loadRuns();
      } catch (err) {
        showToast(`Purge error: ${err.message}`, 'error');
      }
    });

    // File Drag & Drop
    dom.fileDropzone.addEventListener('click', () => dom.fileInput.click());
    dom.fileInput.addEventListener('change', () => {
      if (dom.fileInput.files) {
        Array.from(dom.fileInput.files).forEach(handleFileUpload);
      }
    });
    dom.fileDropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.fileDropzone.classList.add('dragover');
    });
    dom.fileDropzone.addEventListener('dragleave', () => dom.fileDropzone.classList.remove('dragover'));
    dom.fileDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dom.fileDropzone.classList.remove('dragover');
      if (e.dataTransfer?.files) {
        Array.from(e.dataTransfer.files).forEach(handleFileUpload);
      }
    });

    // Settings
    dom.settingsForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const url = dom.settingsServerUrl.value.trim();
      const token = dom.settingsAuthToken.value.trim();
      if (url) localStorage.setItem('aura_server_url', url);
      else localStorage.removeItem('aura_server_url');

      if (token) sessionStorage.setItem('aura_auth_token', token);
      else sessionStorage.removeItem('aura_auth_token');

      state.serverUrl = url;
      state.authToken = token;
      showToast('Credentials updated.', 'success');
      checkSystemHealth();
    });

    dom.settingsLogoutBtn.addEventListener('click', () => {
      sessionStorage.removeItem('aura_auth_token');
      state.authToken = '';
      dom.settingsAuthToken.value = '';
      showToast('Logged out.', 'info');
      checkSystemHealth();
    });

    dom.refreshTelemetryBtn.addEventListener('click', checkSystemHealth);
    dom.refreshRunsBtn.addEventListener('click', loadRuns);
    dom.refreshApprovalsBtn.addEventListener('click', loadApprovals);
    dom.refreshTasksBtn.addEventListener('click', loadTasks);
    dom.refreshMemoryBtn.addEventListener('click', loadMemories);
    dom.refreshFilesBtn.addEventListener('click', loadArtifacts);
    dom.refreshDevicesBtn.addEventListener('click', loadDevices);

    dom.runsSearch.addEventListener('input', () => loadRuns());
    dom.runsStatusFilter.addEventListener('change', () => loadRuns());
    dom.memorySearchInput.addEventListener('input', () => loadMemories());
    dom.memoryTypeFilter.addEventListener('change', () => loadMemories());
    dom.memoryStateFilter.addEventListener('change', () => loadMemories());
  }

  // Boot Application
  function init() {
    dom.settingsServerUrl.value = state.serverUrl;
    dom.settingsAuthToken.value = state.authToken;
    setupEventListeners();
    checkSystemHealth();
    loadApprovals(); // check for pending approvals badge
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
