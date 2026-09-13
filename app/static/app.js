/**
 * PROJECT AURA — CLIENT INTERFACE JAVASCRIPT
 * Handles real-time API communication, state management, and user interaction.
 */

(() => {
  'use strict';

  // Client State
  const state = {
    serverUrl: localStorage.getItem('aura_server_url') || '',
    authToken: sessionStorage.getItem('aura_auth_token') || '',
    isProcessing: false,
    isConnected: false,
  };

  // DOM Elements
  const messagesContainer = document.getElementById('messages-container');
  const welcomeCard = document.getElementById('welcome-card');
  const loadingIndicator = document.getElementById('loading-indicator');
  const chatForm = document.getElementById('chat-form');
  const userInput = document.getElementById('user-input');
  const sendBtn = document.getElementById('send-btn');
  const clearBtn = document.getElementById('clear-btn');
  const statusPill = document.getElementById('connection-status');
  const statusText = document.getElementById('status-text');
  
  // Settings Modal Elements
  const settingsBtn = document.getElementById('settings-btn');
  const settingsModal = document.getElementById('settings-modal');
  const closeSettingsBtn = document.getElementById('close-settings-btn');
  const saveSettingsBtn = document.getElementById('save-settings-btn');
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
      setConnectionStatus('checking', 'Checking...');
      const resp = await fetch(`${baseUrl}/ready`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
      });

      if (resp.ok) {
        const data = await resp.json();
        if (data.ready) {
          setConnectionStatus('ready', 'AURA Online');
          return;
        }
      }
      
      // Fallback to /health if /ready returned non-200
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

  // Auto-resize Textarea
  function adjustTextareaHeight() {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
  }

  // Scroll Chat Stream to Bottom
  function scrollToBottom() {
    const workspace = document.querySelector('.chat-workspace');
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

    // 1. Render user message
    appendMessage('user', text, { timestamp: Date.now() / 1000 });
    userInput.value = '';
    adjustTextareaHeight();

    // 2. Set processing state
    state.isProcessing = true;
    sendBtn.disabled = true;
    loadingIndicator.classList.remove('hidden');
    scrollToBottom();

    const baseUrl = getBaseUrl();
    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };

    if (state.authToken) {
      headers['Authorization'] = `Bearer ${state.authToken}`;
    }

    try {
      const resp = await fetch(`${baseUrl}/v1/run`, {
        method: 'POST',
        headers: headers,
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
        appendMessage('assistant', '⚠️ Authentication required: Please click the Settings gear icon (⚙️) above and enter your Bearer API token.', {
          isError: true,
        });
      } else {
        const errMsg = (data.error && data.error.message) ? data.error.message : `Server returned status ${resp.status}`;
        appendMessage('assistant', `⚠️ Execution Error: ${errMsg}`, {
          isError: true,
        });
      }
    } catch (networkErr) {
      appendMessage('assistant', `⚠️ Network Error: Unable to reach AURA server at ${baseUrl}. Ensure the server is running on http://127.0.0.1:8000.`, {
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

  // Event Listeners
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
        <p>Your autonomous, policy-governed personal intelligence assistant.</p>
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
