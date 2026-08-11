const $ = (id) => document.getElementById(id);
let csrf = sessionStorage.getItem('durem_csrf') || '';
let currentMode = 'auto';
let currentConversation = null;
let currentUser = null;
let configState = null;
let sending = false;

const icons = {
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M7 10v10H4V10zM7 18h9a3 3 0 003-2.4l1-5A3 3 0 0017 7h-4l1-4-1-1-6 8"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M7 14V4H4v10zM7 6h9a3 3 0 013 2.4l1 5A3 3 0 0117 17h-4l1 4-1 1-6-8"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg>'
};

function esc(value='') {
  return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function richText(value='') {
  const parts = String(value).split('```');
  return parts.map((part, index) => {
    if (index % 2) {
      const cleaned = part.replace(/^\s*[A-Za-z0-9_+-]+\s*\n/, '');
      return `<pre class="chat-code"><code>${esc(cleaned.trim())}</code></pre>`;
    }
    return esc(part)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
      .replace(/\n/g, '<br>');
  }).join('');
}

async function api(url, options={}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || 'GET').toUpperCase();
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  if (['POST','PUT','PATCH','DELETE'].includes(method) && csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(url, {...options, headers, credentials:'same-origin'});
  if (response.status === 401) {
    sessionStorage.removeItem('durem_csrf');
    location.href = '/login';
    throw new Error('Нэвтрэх шаардлагатай.');
  }
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || body || `HTTP ${response.status}`);
  return body;
}

function initials(name) {
  return (name || 'U').split(/\s+/).filter(Boolean).slice(0,2).map(x => x[0]).join('').toUpperCase();
}

function modeMeta(mode) {
  if (mode === 'policy') return {label:'Source-backed policy', placeholder:'Компанийн дүрэм, зөвшөөрөл, процессоо асуугаарай…'};
  if (mode === 'chat') return {label:'Natural local chat', placeholder:'Ярилц, brainstorm хий, бичих эсвэл тайлбар авах…'};
  return {label:'Auto routing', placeholder:'Дүрэм асуу, brainstorm хий, эсвэл зүгээр ярилц…'};
}

function setMode(mode) {
  currentMode = ['auto','policy','chat'].includes(mode) ? mode : 'auto';
  document.querySelectorAll('#modeRow [data-mode]').forEach(x => x.classList.toggle('active', x.dataset.mode === currentMode));
  const meta = modeMeta(currentMode);
  $('modeHint').textContent = meta.label;
  $('question').placeholder = meta.placeholder;
}

function showSheet(id) {
  const el = $(id); if (!el) return;
  el.hidden = false; document.body.classList.add('sheet-open');
}

function hideSheet(id) {
  const el = $(id); if (!el) return;
  el.hidden = true;
  if (![...document.querySelectorAll('.sheet-backdrop')].some(x => !x.hidden)) document.body.classList.remove('sheet-open');
}

function scrollBottom(smooth=true) {
  const box = $('chatScroll');
  requestAnimationFrame(() => box.scrollTo({top: box.scrollHeight, behavior: smooth ? 'smooth' : 'auto'}));
}

function autoGrow() {
  const textarea = $('question');
  textarea.style.height = 'auto';
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

async function bootstrap() {
  const me = await api('/api/auth/me');
  csrf = me.csrf_token || csrf;
  sessionStorage.setItem('durem_csrf', csrf);
  currentUser = me.user;
  const userInitials = initials(currentUser.name);
  $('userName').textContent = currentUser.name;
  $('userRole').textContent = `${currentUser.role || 'Ажилтан'}${currentUser.department ? ' · ' + currentUser.department : ''}`;
  $('avatar').textContent = userInitials; $('profileAvatar').textContent = userInitials;
  $('profileName').textContent = currentUser.name;
  $('profileRole').textContent = `${currentUser.role || 'Ажилтан'}${currentUser.department ? ' · ' + currentUser.department : ''}`;
  $('adminLink').hidden = !currentUser.is_admin; $('profileAdminLink').hidden = !currentUser.is_admin;

  configState = await api('/api/config');
  $('companyName').textContent = configState.company;
  $('modelPill').textContent = configState.model;
  $('memoryStatus').textContent = configState.personal_memory_enabled ? 'Local · зөвхөн таны account' : 'Админаар унтраалттай';
  $('clearMemoryBtn').disabled = !configState.personal_memory_enabled;
  $('showMemoryBtn').disabled = !configState.personal_memory_enabled;
  if (!configState.personal_memory_enabled) $('welcomeMemoryTrust').classList.add('muted-trust');
  const chatModeButton = document.querySelector('#modeRow [data-mode="chat"]');
  if (chatModeButton) { chatModeButton.disabled = !configState.general_chat_enabled; chatModeButton.title = configState.general_chat_enabled ? '' : 'Админ ердийн чат mode-ийг унтраасан'; }
  document.querySelectorAll('.starter-card[data-mode="chat"]').forEach(button => { button.disabled = !configState.general_chat_enabled; });
  if (!configState.general_chat_enabled && currentMode === 'chat') setMode('auto');
  await Promise.all([loadHealth(), loadConversations()]);
}

async function loadHealth() {
  try {
    const h = await api('/api/health');
    const ok = h.status === 'ok' && h.llm_reachable;
    $('healthDot').classList.toggle('ok', ok);
    $('healthText').textContent = h.llm_reachable ? 'Local AI бэлэн' : 'AI offline';
  } catch {
    $('healthText').textContent = 'Backend алдаа';
  }
}

async function loadConversations() {
  const items = await api('/api/conversations');
  const list = $('conversationList');
  if (!items.length) {
    list.innerHTML = '<div class="sidebar-empty">Одоогоор яриа алга.</div>';
    return;
  }
  list.innerHTML = items.map(item => `
    <div class="conversation-row ${item.id===currentConversation?'active':''}">
      <button class="conversation-open" data-conv="${esc(item.id)}" title="${esc(item.title)}">
        <svg viewBox="0 0 24 24"><path d="M5 5h14v11H9l-4 4z"/></svg><span>${esc(item.title)}</span>
      </button>
      <button class="conversation-delete" data-delete-conv="${esc(item.id)}" aria-label="Яриа устгах">${icons.trash}</button>
    </div>`).join('');
  list.querySelectorAll('[data-conv]').forEach(button => button.addEventListener('click', () => openConversation(button.dataset.conv)));
  list.querySelectorAll('[data-delete-conv]').forEach(button => button.addEventListener('click', event => {
    event.stopPropagation(); deleteConversation(button.dataset.deleteConv);
  }));
}

async function deleteConversation(id) {
  if (!confirm('Энэ яриаг устгах уу?')) return;
  await api(`/api/conversations/${encodeURIComponent(id)}`, {method:'DELETE'});
  if (currentConversation === id) newChat(false);
  await loadConversations();
}

async function openConversation(id) {
  const data = await api(`/api/conversations/${encodeURIComponent(id)}`);
  currentConversation = id;
  $('messages').innerHTML = ''; $('welcome').hidden = true;
  for (const message of data.messages) {
    if (message.role === 'user') addUserMessage(message.content, false);
    else if (message.response) renderAnswer(message.response, false);
    else addPlainAssistant(message.content, false);
  }
  setMode('auto');
  await loadConversations();
  document.body.classList.remove('sidebar-open');
  scrollBottom(false);
}

function newChat(focus=true) {
  currentConversation = null; $('messages').innerHTML = ''; $('welcome').hidden = false;
  $('question').value = ''; autoGrow(); setMode('auto'); loadConversations();
  if (focus) $('question').focus();
  $('chatScroll').scrollTo({top:0, behavior:'smooth'});
}

function addUserMessage(text, scroll=true) {
  $('welcome').hidden = true;
  const row = document.createElement('div'); row.className = 'message-row user-row';
  row.innerHTML = `<div class="user-message">${esc(text).replace(/\n/g,'<br>')}</div>`;
  $('messages').appendChild(row); if (scroll) scrollBottom();
}

function addLoading() {
  const row = document.createElement('div'); row.className = 'message-row assistant-row loading-row'; row.id = 'loadingAnswer';
  const copy = currentMode === 'policy'
    ? ['эх сурвалж шалгаж байна','Дүрэм, эрх, эх сурвалжийг тулгаж байна…']
    : currentMode === 'chat'
      ? ['бодож байна','Local AI хариултаа боловсруулж байна…']
      : ['чиглүүлж байна','Асуултад тохирох замыг сонгож байна…'];
  row.innerHTML = `<div class="assistant-avatar"><img src="/static/mascot.svg" alt="" /></div><div class="assistant-content"><div class="assistant-name">Дүрмээ <span>${copy[0]}</span></div><div class="thinking-card"><span></span><span></span><span></span><p>${copy[1]}</p></div></div>`;
  $('messages').appendChild(row); scrollBottom();
}

function answerTone(data) {
  if (data.answer_type !== 'DECISION') return data.answer_type === 'NOT_FOUND' ? 'unknown' : 'info';
  return {ALLOWED:'allowed', DENIED:'denied', APPROVAL_REQUIRED:'approval', NOT_FOUND:'unknown'}[data.decision] || 'unknown';
}

function typeLabel(data) {
  if (data.answer_type === 'DECISION') return {ALLOWED:'БОЛНО',DENIED:'БОЛОХГҮЙ',APPROVAL_REQUIRED:'ЗӨВШӨӨРӨЛ ШААРДЛАГАТАЙ',NOT_FOUND:'ОЛДСОНГҮЙ'}[data.decision] || 'ШИЙДВЭР';
  return {GUIDANCE:'ЗААВАР',ROUTING:'ХАРИУЦАХ ХҮН',POLICY:'ДҮРЭМ',NOT_FOUND:'АЛБАН ЁСНЫ МЭДЭЭЛЭЛ ОЛДСОНГҮЙ'}[data.answer_type] || data.answer_type;
}

function confidenceLabel(value) {
  return value === 'confirmed' ? 'Баталгаажсан' : value === 'partial' ? 'Хэсэгчилсэн' : 'Тодорхойгүй';
}

function sourceIcon(kind) { return kind === 'responsibility' ? '↗' : kind === 'rule' ? '§' : '▤'; }
function runtimeLabel(data) {
  if (data.method === 'rule_engine') return 'Rule Engine';
  if (data.method === 'memory') return 'Personal memory';
  return 'Local AI';
}

function answerTools(data) {
  return `<div class="answer-tools"><button class="mini-tool copy-answer" aria-label="Хуулах" title="Хуулах">${icons.copy}</button><button class="mini-tool feedback-up" aria-label="Хэрэгтэй байсан" title="Хэрэгтэй байсан">${icons.up}</button><button class="mini-tool feedback-down" aria-label="Хэрэггүй байсан" title="Хэрэггүй байсан">${icons.down}</button></div>`;
}

function wireAnswerTools(row, data) {
  row.querySelector('.copy-answer')?.addEventListener('click', async event => {
    await navigator.clipboard.writeText(data.answer || '');
    const btn = event.currentTarget; btn.classList.add('selected'); setTimeout(()=>btn.classList.remove('selected'),900);
  });
  row.querySelector('.feedback-up')?.addEventListener('click', e => sendFeedback('up', e.currentTarget));
  row.querySelector('.feedback-down')?.addEventListener('click', e => sendFeedback('down', e.currentTarget));
  row.querySelectorAll('[data-source-index]').forEach(btn => btn.addEventListener('click', () => openSource(data.sources[Number(btn.dataset.sourceIndex)])));
}

function renderChatAnswer(data, scroll=true) {
  $('loadingAnswer')?.remove();
  const row = document.createElement('div'); row.className = 'message-row assistant-row chat-answer-row';
  const memoryBadge = data.memory_used ? '<span class="assistant-context-badge">Memory</span>' : '';
  row.innerHTML = `
    <div class="assistant-avatar"><img src="/static/mascot.svg" alt="" /></div>
    <div class="assistant-content">
      <div class="assistant-name">Дүрмээ <span>Ердийн чат</span>${memoryBadge}</div>
      <article class="chat-answer">
        <div class="chat-answer-text">${richText(data.answer || '')}</div>
        <div class="answer-footer chat-footer"><div class="answer-runtime"><span>${esc(runtimeLabel(data))}</span><span>•</span><span>${(Number(data.latency_ms||0)/1000).toFixed(1)} сек</span></div>${answerTools(data)}</div>
      </article>
    </div>`;
  wireAnswerTools(row,data); $('messages').appendChild(row); if(scroll) scrollBottom();
}

function renderPolicyAnswer(data, scroll=true) {
  $('loadingAnswer')?.remove();
  const row = document.createElement('div'); row.className = 'message-row assistant-row';
  const tone = answerTone(data);
  const steps = (data.next_steps || []).length ? `<div class="answer-section"><div class="answer-section-title">Дараагийн алхам</div><ol class="next-steps">${data.next_steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol></div>` : '';
  const approver = data.approver ? `<div class="approver-card"><span class="approver-icon">↗</span><div><small>${data.answer_type === 'ROUTING' ? 'Хандах хүн / нэгж' : 'Зөвшөөрөл авах'}</small><strong>${esc(data.approver)}</strong></div></div>` : '';
  const sources = (data.sources || []).length ? `<div class="answer-section source-section"><div class="answer-section-title"><span>Эх сурвалж</span><small>${data.sources.length} баримт</small></div><div class="source-list">${data.sources.map((source,i)=>`<button class="source-chip" data-source-index="${i}"><span class="source-kind">${sourceIcon(source.kind)}</span><span class="source-copy"><strong>${esc(source.title)}</strong><small>${esc(source.section || (source.kind==='rule'?'Дүрэм':'Эх баримт'))}</small></span><span class="source-arrow">›</span></button>`).join('')}</div></div>` : '';
  const override = data.safety_override ? '<span class="route-override-badge">Дүрэм рүү автоматаар шилжүүлэв</span>' : '';
  row.innerHTML = `
    <div class="assistant-avatar"><img src="/static/mascot.svg" alt="" /></div>
    <div class="assistant-content">
      <div class="assistant-name">Дүрмээ <span>Company policy</span>${override}</div>
      <article class="decision-card ${tone}">
        <div class="decision-head"><span class="decision-badge"><span class="decision-signal"></span>${esc(typeLabel(data))}</span><span class="confidence-badge ${esc(data.confidence||'unknown')}">${esc(confidenceLabel(data.confidence))}</span></div>
        <h2>${esc(data.headline || 'Хариулт')}</h2><div class="answer-text">${esc(data.answer || '').replace(/\n/g,'<br>')}</div>
        ${approver}${steps}${sources}
        <div class="answer-footer"><div class="answer-runtime"><span>${esc(runtimeLabel(data))}</span><span>•</span><span>${(Number(data.latency_ms||0)/1000).toFixed(1)} сек</span></div>${answerTools(data)}</div>
      </article>
    </div>`;
  wireAnswerTools(row,data); $('messages').appendChild(row); if(scroll) scrollBottom();
}

function renderAnswer(data, scroll=true) {
  if (data.answer_type === 'CHAT' || data.route === 'chat') renderChatAnswer(data,scroll);
  else renderPolicyAnswer(data,scroll);
}

function addPlainAssistant(text, scroll=true) {
  renderChatAnswer({answer_type:'CHAT',answer:text,latency_ms:0,method:'chat_llm',memory_used:false},scroll);
}

async function sendFeedback(rating, button) {
  if (!currentConversation) return;
  try {
    await api('/api/feedback',{method:'POST',body:JSON.stringify({conversation_id:currentConversation,rating,note:''})});
    button.parentElement.querySelectorAll('.feedback-up,.feedback-down').forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
  } catch (error) { console.warn(error); }
}

async function openSource(source) {
  $('sourceTitle').textContent = source.title || 'Эх сурвалж';
  const content = $('sourceContent'); content.innerHTML = '<div class="source-loading">Эх сурвалжийг нээж байна…</div>'; showSheet('sourceSheet');
  if (source.document_id) {
    try {
      const doc = await api(`/api/documents/${encodeURIComponent(source.document_id)}/preview`);
      content.innerHTML = `<div class="source-meta-grid"><div><small>Version</small><strong>${esc(doc.version||'—')}</strong></div><div><small>Category</small><strong>${esc(doc.category||'—')}</strong></div><div><small>Status</small><strong>${esc(doc.status||'—')}</strong></div></div><div class="source-highlight"><small>Хариултад ашигласан хэсэг</small><strong>${esc(source.section||'Баримт')}</strong><p>${esc(source.snippet||'')}</p></div><div class="document-excerpts">${(doc.chunks||[]).map(chunk=>`<section><h3>${esc(chunk.section||'Баримт')}</h3><p>${esc(chunk.content)}</p></section>`).join('')}</div><a class="secondary-wide" href="/api/documents/${encodeURIComponent(source.document_id)}/file">Эх файлыг татах</a>`;
    } catch(error) { content.innerHTML = `<div class="inline-error">${esc(error.message)}</div>`; }
  } else content.innerHTML = `<div class="source-highlight"><small>${esc(source.kind||'Эх сурвалж')}</small><strong>${esc(source.section||source.title||'')}</strong><p>${esc(source.snippet||'')}</p></div>`;
}

async function ask() {
  const text = $('question').value.trim(); if (!text || sending) return;
  sending = true; $('askError').hidden = true; $('askBtn').disabled = true; $('question').value = ''; autoGrow(); addUserMessage(text); addLoading();
  try {
    const data = await api('/api/ask',{method:'POST',body:JSON.stringify({question:text,mode:currentMode,conversation_id:currentConversation})});
    currentConversation = data.conversation_id; renderAnswer(data); await loadConversations();
  } catch(error) {
    $('loadingAnswer')?.remove(); $('askError').textContent = error.message; $('askError').hidden = false;
  } finally { sending = false; $('askBtn').disabled = false; $('question').focus(); }
}

function queuePrompt(mode, text, submit=false) {
  hideSheet('profileSheet'); setMode(mode); $('question').value = text; autoGrow(); $('question').focus(); if (submit) ask();
}

document.querySelectorAll('.starter-card').forEach(button => button.addEventListener('click', () => queuePrompt(button.dataset.mode || 'auto', button.dataset.q || '')));
$('modeRow').querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
$('askBtn').addEventListener('click', ask);
$('question').addEventListener('input', autoGrow);
$('question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(); } });
$('newChatBtn').addEventListener('click', () => newChat());
$('mobileMenu').addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
$('collapseSidebar').addEventListener('click', () => document.body.classList.toggle('sidebar-collapsed'));
$('userMenuBtn').addEventListener('click', () => showSheet('profileSheet'));
$('infoBtn').addEventListener('click', () => showSheet('infoSheet'));
$('showMemoryBtn').addEventListener('click', () => queuePrompt('chat','Намайг юу санаж байна?',true));
$('clearMemoryBtn').addEventListener('click', () => { if (confirm('Таны бүх personal memory-г мартуулах уу? Conversation history устахгүй.')) queuePrompt('chat','Миний personal memory-г бүгдийг март.',true); });
$('changePasswordBtn').addEventListener('click', () => { hideSheet('profileSheet'); showSheet('passwordSheet'); $('currentPassword').focus(); });
$('logoutBtn').addEventListener('click', async () => { try { await api('/api/auth/logout',{method:'POST'}); } finally { sessionStorage.removeItem('durem_csrf'); location.href='/login'; } });

$('passwordForm').addEventListener('submit', async event => {
  event.preventDefault(); const error = $('passwordError'); error.hidden = true;
  try { await api('/api/auth/change-password',{method:'POST',body:JSON.stringify({current_password:$('currentPassword').value,new_password:$('newPassword').value})}); sessionStorage.removeItem('durem_csrf'); location.href='/login'; }
  catch(e) { error.textContent = e.message; error.hidden = false; }
});

document.querySelectorAll('[data-close-sheet]').forEach(button => button.addEventListener('click', () => hideSheet(button.dataset.closeSheet)));
document.querySelectorAll('.sheet-backdrop').forEach(backdrop => backdrop.addEventListener('click', event => { if (event.target === backdrop) hideSheet(backdrop.id); }));
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); newChat(); }
  if (event.key === 'Escape') { document.querySelectorAll('.sheet-backdrop').forEach(x => { if (!x.hidden) hideSheet(x.id); }); document.body.classList.remove('sidebar-open'); }
});
document.addEventListener('click', event => { if (document.body.classList.contains('sidebar-open') && !event.target.closest('.chat-sidebar') && !event.target.closest('#mobileMenu')) document.body.classList.remove('sidebar-open'); });

bootstrap().catch(error => console.error(error));
setMode('auto'); autoGrow();
