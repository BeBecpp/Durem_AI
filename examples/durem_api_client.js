export class DuremClient {
  constructor(baseUrl = 'http://127.0.0.1:8080') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.token = '';
  }

  async login(username, password, deviceName = 'DUREM JS Client') {
    const response = await fetch(`${this.baseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password, device_name: deviceName}),
    });
    if (!response.ok) throw new Error((await response.json()).detail || 'Login failed');
    const data = await response.json();
    this.token = data.access_token;
    return data;
  }

  async request(path, {method = 'GET', body} = {}) {
    if (!this.token) throw new Error('Call login() first');
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        'Authorization': `Bearer ${this.token}`,
        ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    return response.status === 204 ? null : response.json();
  }

  ask(question, {mode = 'auto', conversationId = null} = {}) {
    return this.request('/api/v1/assistant/ask', {
      method: 'POST', body: {question, mode, conversation_id: conversationId},
    });
  }

  route(question, {mode = 'auto', conversationId = null} = {}) {
    return this.request('/api/v1/assistant/route', {
      method: 'POST', body: {question, mode, conversation_id: conversationId},
    });
  }

  conversations() { return this.request('/api/v1/conversations'); }
  memory() { return this.request('/api/v1/memory'); }
  sourcePreview(documentId) { return this.request(`/api/v1/documents/${encodeURIComponent(documentId)}/preview`); }

  async logout() {
    try { if (this.token) await this.request('/api/v1/auth/session', {method: 'DELETE'}); }
    finally { this.token = ''; }
  }
}
