/**
 * PROJECT AURA — CLIENT INTERFACE JAVASCRIPT (M46)
 * Handles real-time API communication, multi-view navigation, memory controls,
 * RAG interaction, tool execution state, and safe session lifecycle.
 */

(() => {
  'use strict';

  // Client State
  const state = {
    serverUrl: localStorage.getItem('aura_server_url') || '',
    authToken: sessionStorage.getItem('aura_auth_token') || '',
    isProcessing: false,
    isConnected: false,
    currentTab: 'chat-view',
  };

  // DOM Elements - Navigation & Global
  const navTabs = document.querySelectorAll('.nav-tab');
  const viewPanels = document.querySelectorAll('.view-panel');
  const statusPill = document.getElementById('connection-status');
  const statusText = document.getElementById('status-text');

  // DOM Elements - Chat
  const messagesContainer = document.getElementById('messages-container');
  const welcomeCard = document.getElementById('welcome-card');
  const loadingIndicator = document.getElementById('loading-indicator');
  const chatForm = document.getElementById('chat-form');
  const userInput = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  const clearBtn = document.getElementById('clear-btn');

  // DOM Elements - Memory / Preferences
  const preferencesForm = document.getElementById('preferences-form');
  const prefName = document.getElementById('pref-name');
  const prefStyle = document.getElementById('pref-style');
  const prefPrivacy = document.getElementById('pref-privacy');
  const prefProactivity = document.getElementById('pref-proactivity');
  const prefInstructions = document.getElementById('pref-instructions');
  const prefsStatusMsg = document.getElementById('prefs-status-msg');

  // DOM Elements - RAG / Knowledge
  const ragSearchForm = document.getElementById('rag-search-form');
  const ragQueryInput = document.getElementById('rag-query-input');
  const ragResultsContainer = document.getElementById('rag-results-container');

  // DOM Elements - Tools
  const toolsCatalogContainer = document.getElementById('tools-catalog-container');

  // DOM Elements - Settings & Auth
  const settingsBtn = document.getElementById('settings-btn');
  const settingsModal = document.getElementById('settings-modal');
  const closeSettingsBtn = document.getElementById('close-settings-btn');
  const saveSettingsBtn = document.getElementById('save-settings-btn');
  const logoutBtn = document.getElementById('logout-btn');
  const serverUrlInput = document.getElementById('server-url-input');
  const authTokenInput = document.getElementById('auth-token-input');

  // Utility: HTML Escaper for XSS Prevention
  function escapeHtml(str) {
    if (!str || typeof str !== 'string') return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Get Effective Base API URL
  function getBaseUrl() {
    if (state.serverUrl && state.serverUrl.trim()) {
      return state.serverUrl.trim().replace(/\/$/, '');
    }
    return window.location.origin;
  }

  // Get Auth Headers
  function getAuthHeaders() {
    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    if (state.authToken) {
      headers['Authorization'] = `Bearer ${state.authToken}`;
    }
    return headers;
  }

  // Update Connection Status Pill
  function setConnectionStatus(status, text) {
    statusPill.className = `status-pill status-${status}`;
    statusText.textContent = text;
    state.isConnected = (status === 'ready');
  }

  // Check Backend Health & Readiness
  async function checkServerHealth() {
    const baseUrl = getBaseUrl();
    try {
      setConnectionStatus('checking', 'Connecting...');
      const resp = await fetch(`${baseUrl}/ready`, {
        method: 'GET',
        headers: getAuthHeaders(),
      });

      if (resp.ok) {
        const data = await resp.json();
        if (data.ready) {
          setConnectionStatus('ready', 'AURA Online');
          return;
        }
      }
      
      const healthResp = await fetch(`${baseUrl}/health`);
      if (healthResp.ok) {
        setConnectionStatus('ready', 'AURA Active');
      } else {
        setConnectionStatus('offline', 'Degraded');
      }
    } catch (err) {
      setConnectionStatus('offline', 'Offline');
    }
  }

  // Tab Navigation Handling
  navTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const targetId = tab.getAttribute('data-tab');
      navTabs.forEach((t) => t.classList.remove('active'));
      viewPanels.forEach((p) => p.classList.add('hidden'));

      tab.classList.add('active');
      const targetPanel = document.getElementById(targetId);
      if (targetPanel) {
        targetPanel.classList.remove('hidden');
        state.currentTab = targetId;
      }

      if (targetId === 'memory-view') loadPreferences();
      if (targetId === 'tools-view') loadToolsCatalog();
    });
  });

  // Auto-resize Textarea
  function adjustTextareaHeight() {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
  }

  // Scroll Chat Stream to Bottom
  function scrollToBottom() {
    const workspace = document.getElementById('chat-view');
    if (workspace) {
      workspace.scrollTop = workspace.scrollHeight;
    }
  }

  // Render a User or Assistant Message
  function appendMessage(role, text, metadata = {}) {
    if (welcomeCard && welcomeCard.parentNode) {
      welcomeCard.remove();
    }

    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    const avatar = document.createElement('div');
    avatar.className = `message-avatar ${role}-avatar`;
    avatar.textContent = (role === 'user') ? '👤' : '✨';

    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${metadata.isError ? 'message-error' : ''}`;

    const content = document.createElement('div');
    content.className = 'message-content';
    content.textContent = text;

    bubble.appendChild(content);

    // Optional metadata badge (e.g. Request ID)
    if (metadata.requestId || metadata.timestamp) {
      const metaDiv = document.createElement('div');
      metaDiv.className = 'message-meta';
      if (metadata.requestId) {
        const badge = document.createElement('span');
        badge.className = 'meta-badge';
        badge.textContent = metadata.requestId.substring(0, 12);
        badge.title = `Request ID: ${metadata.requestId}`;
        metaDiv.appendChild(badge);
      }
      if (metadata.timestamp) {
        const timeSpan = document.createElement('span');
        timeSpan.textContent = new Date(metadata.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        metaDiv.appendChild(timeSpan);
      }
      bubble.appendChild(metaDiv);
    }

    if (role === 'user') {
      row.appendChild(bubble);
      row.appendChild(avatar);
    } else {
      row.appendChild(avatar);
      row.appendChild(bubble);
    }

    messagesContainer.appendChild(row);
    scrollToBottom();
  }

  // Send Message to AURA Backend
  async function sendMessage(messageText) {
    const text = messageText.trim();
    if (!text || state.isProcessing) return;

    appendMessage('user', text, { timestamp: Date.now() / 1000 });
    userInput.value = '';
    adjustTextareaHeight();

    state.isProcessing = true;
    sendBtn.disabled = true;
    loadingIndicator.classList.remove('hidden');
    scrollToBottom();

    const baseUrl = getBaseUrl();
    try {
      const resp = await fetch(`${baseUrl}/v1/run`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          user_input: text,
          metadata: { client: 'aura-web-ui', version: '0.28.0' },
        }),
      });

      const data = await resp.json();

      if (resp.ok) {
        appendMessage('assistant', data.content || '(No response content)', {
          requestId: data.request_id,
          timestamp: data.timestamp,
        });
        setConnectionStatus('ready', 'AURA Online');
      } else if (resp.status === 401) {
        appendMessage('assistant', '⚠️ Authentication required: Please open Settings (⚙️) and enter your Bearer API token.', {
          isError: true,
        });
      } else {
        const errMsg = (data.error && data.error.message) ? data.error.message : `Server returned status ${resp.status}`;
        appendMessage('assistant', `⚠️ Execution Error: ${errMsg}`, {
          isError: true,
        });
      }
    } catch (networkErr) {
      appendMessage('assistant', `⚠️ Network Error: Unable to reach AURA server at ${baseUrl}. Ensure the server is online.`, {
        isError: true,
      });
      setConnectionStatus('offline', 'Offline');
    } finally {
      state.isProcessing = false;
      sendBtn.disabled = false;
      loadingIndicator.classList.add('hidden');
      userInput.focus();
    }
  }

  // Memory / Preferences Management
  async function loadPreferences() {
    const baseUrl = getBaseUrl();
    try {
      const resp = await fetch(`${baseUrl}/v1/preferences`, {
        method: 'GET',
        headers: getAuthHeaders(),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (prefName) prefName.value = data.preferred_name || '';
        if (prefStyle) prefStyle.value = data.communication_style || 'concise';
        if (prefPrivacy) prefPrivacy.value = data.privacy_mode || 'standard';
        if (prefProactivity) prefProactivity.value = data.proactivity_level || 'balanced';
        if (prefInstructions) prefInstructions.value = data.custom_instructions || '';
      }
    } catch (err) {
      console.error('Failed to load preferences:', err);
    }
  }

  if (preferencesForm) {
    preferencesForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const baseUrl = getBaseUrl();
      const payload = {
        preferred_name: prefName ? prefName.value.trim() : null,
        communication_style: prefStyle ? prefStyle.value : 'concise',
        privacy_mode: prefPrivacy ? prefPrivacy.value : 'standard',
        proactivity_level: prefProactivity ? prefProactivity.value : 'balanced',
        custom_instructions: prefInstructions ? prefInstructions.value.trim() : '',
      };

      try {
        const resp = await fetch(`${baseUrl}/v1/preferences`, {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify(payload),
        });

        if (resp.ok) {
          if (prefsStatusMsg) {
            prefsStatusMsg.textContent = '✓ Preferences saved successfully!';
            setTimeout(() => { prefsStatusMsg.textContent = ''; }, 3000);
          }
        } else {
          if (prefsStatusMsg) {
            prefsStatusMsg.textContent = '⚠️ Failed to save preferences.';
            prefsStatusMsg.style.color = 'var(--status-offline)';
          }
        }
      } catch (err) {
        if (prefsStatusMsg) {
          prefsStatusMsg.textContent = '⚠️ Network error while saving preferences.';
          prefsStatusMsg.style.color = 'var(--status-offline)';
        }
      }
    });
  }

  // RAG / Knowledge Search
  if (ragSearchForm) {
    ragSearchForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const query = ragQueryInput.value.trim();
      if (!query) return;

      ragResultsContainer.innerHTML = '<div class="empty-state">Searching knowledge base...</div>';
      const baseUrl = getBaseUrl();

      try {
        const resp = await fetch(`${baseUrl}/v1/rag`, {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({ query: query, max_chars: 4000 }),
        });

        if (resp.ok) {
          const data = await resp.json();
          ragResultsContainer.innerHTML = '';
          const items = data.items || data.chunks || (data.formatted_context ? [{ title: 'Retrieved Context', snippet: data.formatted_context }] : []);

          if (items.length === 0) {
            ragResultsContainer.innerHTML = '<div class="empty-state">No matching knowledge documents found.</div>';
            return;
          }

          items.forEach((item) => {
            const card = document.createElement('div');
            card.className = 'result-card';

            const title = document.createElement('h4');
            title.textContent = item.title || item.source_id || 'Knowledge Document';

            const snippet = document.createElement('p');
            snippet.textContent = item.snippet || item.content || JSON.stringify(item);

            card.appendChild(title);
            card.appendChild(snippet);
            ragResultsContainer.appendChild(card);
          });
        } else {
          ragResultsContainer.innerHTML = `<div class="empty-state">⚠️ Query failed with status ${resp.status}</div>`;
        }
      } catch (err) {
        ragResultsContainer.innerHTML = '<div class="empty-state">⚠️ Network error connecting to RAG service.</div>';
      }
    });
  }

  // Tools Catalog Loader
  async function loadToolsCatalog() {
    const baseUrl = getBaseUrl();
    toolsCatalogContainer.innerHTML = '<div class="empty-state">Loading tool catalog...</div>';

    try {
      const resp = await fetch(`${baseUrl}/v1/tools`, {
        method: 'GET',
        headers: getAuthHeaders(),
      });

      if (resp.ok) {
        const data = await resp.json();
        const tools = data.tools || [];
        toolsCatalogContainer.innerHTML = '';

        if (tools.length === 0) {
          toolsCatalogContainer.innerHTML = '<div class="empty-state">No tools registered.</div>';
          return;
        }

        tools.forEach((tool) => {
          const card = document.createElement('div');
          card.className = 'tool-card';

          const title = document.createElement('h4');
          title.textContent = `🛠️ ${tool.name || tool.tool_name}`;

          const desc = document.createElement('p');
          desc.textContent = tool.description || 'No description provided.';

          card.appendChild(title);
          card.appendChild(desc);
          toolsCatalogContainer.appendChild(card);
        });
      } else {
        toolsCatalogContainer.innerHTML = `<div class="empty-state">⚠️ Failed to load tools (status ${resp.status})</div>`;
      }
    } catch (err) {
      toolsCatalogContainer.innerHTML = '<div class="empty-state">⚠️ Network error loading tool catalog.</div>';
    }
  }

  // Event Listeners for Chat Form
  chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(userInput.value);
  });

  userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event('submit'));
    }
  });

  userInput.addEventListener('input', adjustTextareaHeight);

  // Suggestion Chips
  document.addEventListener('click', (e) => {
    const chip = e.target.closest('.suggestion-chip');
    if (chip) {
      const prompt = chip.getAttribute('data-prompt');
      if (prompt) {
        userInput.value = prompt;
        adjustTextareaHeight();
        userInput.focus();
      }
    }
  });

  // Clear Chat Button
  clearBtn.addEventListener('click', () => {
    messagesContainer.innerHTML = `
      <div class="welcome-card" id="welcome-card">
        <div class="welcome-icon">🔮</div>
        <h2>Welcome to Project AURA</h2>
        <p>Your policy-governed, autonomous personal intelligence system.</p>
        <div class="suggestions-grid">
          <button class="suggestion-chip" data-prompt="What is Project AURA and what capabilities do you have?">
            💡 What is Project AURA?
          </button>
          <button class="suggestion-chip" data-prompt="Calculate 256 * 1024 / 8">
            🧮 Calculate 256 * 1024 / 8
          </button>
          <button class="suggestion-chip" data-prompt="Hello AURA. Reply with: AURA CLIENT LIVE TEST">
            ⚡ Test Connection
          </button>
        </div>
      </div>
    `;
  });

  // Settings Modal Controls
  settingsBtn.addEventListener('click', () => {
    serverUrlInput.value = state.serverUrl;
    authTokenInput.value = state.authToken;
    settingsModal.classList.remove('hidden');
    serverUrlInput.focus();
  });

  closeSettingsBtn.addEventListener('click', () => {
    settingsModal.classList.add('hidden');
  });

  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
      settingsModal.classList.add('hidden');
    }
  });

  logoutBtn.addEventListener('click', () => {
    sessionStorage.removeItem('aura_auth_token');
    state.authToken = '';
    authTokenInput.value = '';
    settingsModal.classList.add('hidden');
    checkServerHealth();
  });

  saveSettingsBtn.addEventListener('click', () => {
    const newUrl = serverUrlInput.value.trim();
    const newTok = authTokenInput.value.trim();

    state.serverUrl = newUrl;
    state.authToken = newTok;

    if (newUrl) {
      localStorage.setItem('aura_server_url', newUrl);
    } else {
      localStorage.removeItem('aura_server_url');
    }

    if (newTok) {
      sessionStorage.setItem('aura_auth_token', newTok);
    } else {
      sessionStorage.removeItem('aura_auth_token');
    }

    settingsModal.classList.add('hidden');
    checkServerHealth();
  });

  // Initial Health Check and Periodic Status Polling
  checkServerHealth();
  setInterval(checkServerHealth, 15000);
})();
