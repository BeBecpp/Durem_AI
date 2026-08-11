const $ = (id) => document.getElementById(id);
let csrf = sessionStorage.getItem('durem_csrf') || '';
let state = { departments: [], roles: [], users: [], rules: [], documents: [], responsibilities: [], settings: null, health: null, security: null, knowledgeHealth: null, gaps: [] };

function esc(value='') { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtDate(value) { if (!value) return '—'; try { return new Date(value).toLocaleString('mn-MN', {dateStyle:'medium',timeStyle:'short'}); } catch { return value; } }
function boolValue(value) { return value === true || value === 1 || value === '1'; }

async function api(url, options={}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || 'GET').toUpperCase();
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  if (['POST','PUT','PATCH','DELETE'].includes(method) && csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(url, {...options, headers});
  if (response.status === 401) { location.href='/login'; throw new Error('Нэвтрэх шаардлагатай.'); }
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || body || `HTTP ${response.status}`);
  return body;
}

function openModal({title,kicker='DUREM Admin',body,saveText='Хадгалах',onSave,wide=false}) {
  $('modalKicker').textContent = kicker; $('modalTitle').textContent = title; $('modalBody').innerHTML = body;
  $('modalBackdrop').hidden = false;
  document.querySelector('.admin-modal').style.width = wide ? 'min(920px,100%)' : '';
  $('modalFoot').innerHTML = `<button class="btn" id="modalCancel">Болих</button>${onSave ? `<button class="btn btn-primary" id="modalSave">${esc(saveText)}</button>` : ''}`;
  $('modalCancel').onclick = closeModal;
  if (onSave) $('modalSave').onclick = async () => {
    const button = $('modalSave'); button.disabled=true; const old=button.textContent; button.textContent='Хадгалж байна…';
    try { await onSave(); closeModal(); } catch(error) { showModalError(error.message); } finally { button.disabled=false; button.textContent=old; }
  };
}
function closeModal(){ $('modalBackdrop').hidden=true; $('modalBody').innerHTML=''; $('modalFoot').innerHTML=''; }
function showModalError(message){ let box=$('modalInlineError'); if(!box){ box=document.createElement('div'); box.id='modalInlineError'; box.className='inline-error'; box.style.marginTop='14px'; $('modalBody').appendChild(box);} box.textContent=message; }
$('modalClose').onclick=closeModal;
$('modalBackdrop').addEventListener('click', e => { if(e.target === $('modalBackdrop')) closeModal(); });

function switchSection(name) {
  document.querySelectorAll('.section-view').forEach(el => el.classList.toggle('active', el.id === `section-${name}`));
  document.querySelectorAll('#adminNav [data-section]').forEach(btn => btn.classList.toggle('active', btn.dataset.section === name));
  const titles = {dashboard:'Тойм',documents:'Knowledge base',rules:'Decision rules',organization:'Байгууллага',routing:'Хариуцлага',gaps:'Knowledge gaps',audit:'Audit log',security:'Security',settings:'Тохиргоо'};
  $('pageTitle').textContent = titles[name] || 'DUREM Admin';
  document.body.classList.remove('sidebar-open');
  if(name==='gaps') loadGaps();
  if(name==='audit') loadAudit();
  if(name==='security') loadSecurity();
  if(name==='settings') loadSettings();
}
document.querySelectorAll('#adminNav [data-section]').forEach(btn => btn.onclick=()=>switchSection(btn.dataset.section));

async function bootstrap(){
  const me = await api('/api/auth/me'); csrf = me.csrf_token || csrf; sessionStorage.setItem('durem_csrf',csrf);
  if(!me.user.is_admin){ location.href='/'; return; }
  $('adminUser').textContent = me.user.name; $('companyName').textContent='Loading…';
  await Promise.all([loadHealth(),loadDashboard(),loadDepartments(),loadRoles(),loadUsers(),loadRules(),loadDocuments(),loadResponsibilities(),loadSettings(),loadSecurity(),loadKnowledgeHealth(),loadGaps()]);
}

async function loadHealth(){
  try{
    const h=await api('/api/health'); state.health=h;
    $('healthDot').classList.toggle('ok',h.status==='ok'); $('healthText').textContent=h.llm_reachable?'Local AI бэлэн':'AI offline';
    $('checkLlm').textContent=h.llm_reachable?'Ready':'Offline'; $('checkDb').textContent=h.database?'Ready':'Error'; $('checkEmbed').textContent=h.embeddings_enabled?'Enabled':'Lexical';
  }catch{ $('healthText').textContent='Health алдаа'; }
}

async function loadDashboard(){
  const data=await api('/api/admin/stats');
  const s=data.stats;
  const cards=[['Идэвхтэй хэрэглэгч',s.users,'Organization'],['Knowledge docs',s.documents,'Active documents'],['Decision rules',s.rules,'Enabled rules'],['Өнөөдрийн асуулт',s.questions_today,'Employee queries'],['7 хоногийн gap',s.not_found,'NOT_FOUND']];
  $('statsGrid').innerHTML=cards.map(([label,value,foot])=>`<div class="card stat-card"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-foot">${esc(foot)}</div></div>`).join('');
  $('recentActivity').innerHTML=data.recent.length?data.recent.map(item=>`<div class="activity"><div class="activity-icon">${item.event_type==='assistant'?'✦':item.event_type==='auth'?'↗':item.event_type==='security'?'◆':'⚙'}</div><div><strong>${esc(item.user_name)} · ${esc(item.action)}</strong><p>${esc(JSON.stringify(item.metadata||{}).slice(0,150))}</p><time>${esc(fmtDate(item.created_at))}</time></div></div>`).join(''):'<div class="empty-state">Үйлдэл алга.</div>';
  if(state.security) renderSecurityMini();
}

async function loadDepartments(){ state.departments=await api('/api/admin/departments'); renderDepartments(); }
function renderDepartments(){
  $('departmentList').innerHTML=state.departments.map(d=>`<div class="health-row"><div><strong style="font-size:12px">${esc(d.name)}</strong><div class="help">${esc(d.description||'')}</div></div><div style="display:flex;align-items:center;gap:7px"><span class="status ${boolValue(d.active)?'active':'inactive'}">${boolValue(d.active)?'Active':'Off'}</span><button class="btn btn-sm" data-edit-dept="${d.id}">Засах</button></div></div>`).join('');
  document.querySelectorAll('[data-edit-dept]').forEach(b=>b.onclick=()=>editDepartment(Number(b.dataset.editDept)));
}
async function loadRoles(){ state.roles=await api('/api/admin/roles'); renderRoles(); }
function renderRoles(){
  $('roleList').innerHTML=state.roles.map(r=>`<div class="health-row"><div><strong style="font-size:12px">${esc(r.name)}</strong><div class="help">${esc(r.description||'')}</div></div><div style="display:flex;align-items:center;gap:7px"><span class="status ${boolValue(r.active)?'active':'inactive'}">${boolValue(r.is_admin)?'Admin':'Role'}</span><button class="btn btn-sm" data-edit-role="${r.id}">Засах</button></div></div>`).join('');
  document.querySelectorAll('[data-edit-role]').forEach(b=>b.onclick=()=>editRole(Number(b.dataset.editRole)));
}
async function loadUsers(){ state.users=await api('/api/admin/users'); renderUsers(); }
function renderUsers(){
  $('userCount').textContent=`${state.users.length} хэрэглэгч`;
  $('userTable').innerHTML=state.users.map(u=>`<tr><td><strong>${esc(u.name)}</strong></td><td>${esc(u.username)}</td><td>${esc(u.department||'—')}</td><td>${esc(u.role||'—')}</td><td><span class="status ${boolValue(u.active)?'active':'inactive'}">${boolValue(u.active)?'Active':'Disabled'}</span></td><td><button class="btn btn-sm" data-edit-user="${u.id}">Засах</button></td></tr>`).join('');
  document.querySelectorAll('[data-edit-user]').forEach(b=>b.onclick=()=>editUser(Number(b.dataset.editUser)));
}

function departmentOptions(selected=''){ return `<option value="">— Сонгохгүй —</option>`+state.departments.filter(d=>boolValue(d.active)).map(d=>`<option value="${d.id}" ${String(d.id)===String(selected)?'selected':''}>${esc(d.name)}</option>`).join(''); }
function roleOptions(selected=''){ return `<option value="">— Сонгохгүй —</option>`+state.roles.filter(r=>boolValue(r.active)).map(r=>`<option value="${r.id}" ${String(r.id)===String(selected)?'selected':''}>${esc(r.name)}</option>`).join(''); }
function userOptions(selected=''){ return `<option value="">— Сонгохгүй —</option>`+state.users.filter(u=>boolValue(u.active)).map(u=>`<option value="${u.id}" ${String(u.id)===String(selected)?'selected':''}>${esc(u.name)}</option>`).join(''); }

function editDepartment(id=null){
  const d=state.departments.find(x=>x.id===id)||{name:'',description:'',active:1};
  openModal({title:id?'Хэлтэс засах':'Хэлтэс нэмэх',body:`<div class="form-grid"><div class="field span-2"><label class="label">Нэр</label><input id="fDeptName" value="${esc(d.name)}"></div><div class="field span-2"><label class="label">Тайлбар</label><textarea id="fDeptDesc">${esc(d.description||'')}</textarea></div><div class="field"><label class="label">Төлөв</label><select id="fDeptActive"><option value="true" ${boolValue(d.active)?'selected':''}>Active</option><option value="false" ${!boolValue(d.active)?'selected':''}>Disabled</option></select></div></div>`,onSave:async()=>{
    const payload={name:$('fDeptName').value.trim(),description:$('fDeptDesc').value.trim(),active:$('fDeptActive').value==='true'};
    await api(id?`/api/admin/departments/${id}`:'/api/admin/departments',{method:id?'PUT':'POST',body:JSON.stringify(payload)}); await loadDepartments();
  }});
}
function editRole(id=null){
  const r=state.roles.find(x=>x.id===id)||{name:'',description:'',is_admin:0,active:1};
  openModal({title:id?'Role засах':'Role нэмэх',body:`<div class="form-grid"><div class="field span-2"><label class="label">Role нэр</label><input id="fRoleName" value="${esc(r.name)}"></div><div class="field span-2"><label class="label">Тайлбар</label><textarea id="fRoleDesc">${esc(r.description||'')}</textarea></div><div class="field"><label class="label">Admin эрх</label><select id="fRoleAdmin"><option value="false" ${!boolValue(r.is_admin)?'selected':''}>Үгүй</option><option value="true" ${boolValue(r.is_admin)?'selected':''}>Тийм</option></select></div><div class="field"><label class="label">Төлөв</label><select id="fRoleActive"><option value="true" ${boolValue(r.active)?'selected':''}>Active</option><option value="false" ${!boolValue(r.active)?'selected':''}>Disabled</option></select></div></div>`,onSave:async()=>{
    const payload={name:$('fRoleName').value.trim(),description:$('fRoleDesc').value.trim(),is_admin:$('fRoleAdmin').value==='true',active:$('fRoleActive').value==='true'};
    await api(id?`/api/admin/roles/${id}`:'/api/admin/roles',{method:id?'PUT':'POST',body:JSON.stringify(payload)}); await loadRoles();
  }});
}
function editUser(id=null){
  const u=state.users.find(x=>x.id===id)||{username:'',name:'',department_id:'',role_id:'',active:1};
  openModal({title:id?'Хэрэглэгч засах':'Хэрэглэгч нэмэх',body:`<div class="form-grid"><div class="field"><label class="label">Нэр</label><input id="fUserName" value="${esc(u.name)}"></div><div class="field"><label class="label">Username</label><input id="fUsername" value="${esc(u.username)}"></div><div class="field"><label class="label">Хэлтэс</label><select id="fUserDept">${departmentOptions(u.department_id)}</select></div><div class="field"><label class="label">Role</label><select id="fUserRole">${roleOptions(u.role_id)}</select></div><div class="field"><label class="label">${id?'Шинэ нууц үг (хоосон = хэвээр)':'Нууц үг'}</label><input id="fUserPass" type="password"><div class="help">12+ тэмдэгт, жижиг/том үсэг/тоо/тусгай тэмдэгтийн 3 төрлийг ашиглана.</div></div><div class="field"><label class="label">Төлөв</label><select id="fUserActive"><option value="true" ${boolValue(u.active)?'selected':''}>Active</option><option value="false" ${!boolValue(u.active)?'selected':''}>Disabled</option></select></div></div>`,onSave:async()=>{
    const payload={username:$('fUsername').value.trim(),name:$('fUserName').value.trim(),password:$('fUserPass').value,department_id:$('fUserDept').value?Number($('fUserDept').value):null,role_id:$('fUserRole').value?Number($('fUserRole').value):null,active:$('fUserActive').value==='true'};
    await api(id?`/api/admin/users/${id}`:'/api/admin/users',{method:id?'PUT':'POST',body:JSON.stringify(payload)}); await loadUsers();
  }});
}

async function loadRules(){ state.rules=await api('/api/admin/rules'); renderRules(); }
function renderRules(){
  const q=($('ruleSearch')?.value||'').toLowerCase(); const rows=state.rules.filter(r=>`${r.id} ${r.title} ${r.text} ${r.keywords}`.toLowerCase().includes(q));
  $('ruleCount').textContent=`${state.rules.length} дүрэм`;
  $('ruleGrid').innerHTML=rows.length?rows.map(r=>`<article class="card rule-card"><div class="rule-top"><div><div class="rule-id">${esc(r.id)} · ${esc(r.category)}</div><h3>${esc(r.title)}</h3></div><span class="status ${boolValue(r.active)?'active':'inactive'}">${boolValue(r.active)?'Active':'Off'}</span></div><p>${esc(r.text)}</p><div class="rule-tags"><span>${esc(r.decision_hint)}</span>${r.approver?`<span>→ ${esc(r.approver)}</span>`:''}<span>priority ${esc(r.priority)}</span></div><div class="rule-actions"><button class="btn btn-sm" data-edit-rule="${esc(r.id)}">Засах</button><button class="btn btn-sm btn-danger" data-delete-rule="${esc(r.id)}">Устгах</button></div></article>`).join(''):'<div class="card empty-state"><div class="empty-icon">◆</div>Дүрэм олдсонгүй.</div>';
  document.querySelectorAll('[data-edit-rule]').forEach(b=>b.onclick=()=>editRule(b.dataset.editRule));
  document.querySelectorAll('[data-delete-rule]').forEach(b=>b.onclick=()=>deleteRule(b.dataset.deleteRule));
}
function editRule(id=null, seedQuestion=""){
  const r=state.rules.find(x=>x.id===id)||{id:'',title:seedQuestion?`Gap: ${seedQuestion.slice(0,70)}`:'',text:'',category:'general',keywords:seedQuestion,decision_hint:'AUTO',approver:'',role_scope:'',department_scope:'',priority:100,metric:'',min_value:null,max_value:null,min_inclusive:1,max_inclusive:1,source_document_id:'',source_section:'',active:1};
  const docs=`<option value="">— Холбохгүй —</option>`+state.documents.map(d=>`<option value="${esc(d.id)}" ${d.id===r.source_document_id?'selected':''}>${esc(d.title)}</option>`).join('');
  openModal({title:id?'Дүрэм засах':'Дүрэм нэмэх',wide:true,body:`<div class="form-grid"><div class="field"><label class="label">Rule ID</label><input id="fRuleId" value="${esc(r.id)}" ${id?'readonly':''}></div><div class="field"><label class="label">Category</label><input id="fRuleCategory" value="${esc(r.category)}"></div><div class="field span-2"><label class="label">Гарчиг</label><input id="fRuleTitle" value="${esc(r.title)}"></div><div class="field span-2"><label class="label">Батлагдсан дүрмийн текст</label><textarea id="fRuleText" style="min-height:140px">${esc(r.text)}</textarea></div><div class="field span-2"><label class="label">Keywords</label><input id="fRuleKeywords" value="${esc(r.keywords||'')}" placeholder="хөнгөлөлт, гэрээ, автомашин"><div class="help">Retrieval-д ашиглах үгсийг таслалаар салгана.</div></div><div class="field"><label class="label">Decision hint</label><select id="fRuleDecision">${['AUTO','ALLOWED','DENIED','APPROVAL_REQUIRED','NOT_FOUND'].map(v=>`<option ${v===r.decision_hint?'selected':''}>${v}</option>`).join('')}</select></div><div class="field"><label class="label">Approver</label><input id="fRuleApprover" value="${esc(r.approver||'')}"></div><div class="field"><label class="label">Role scope</label><input id="fRuleRole" value="${esc(r.role_scope||'')}" placeholder="Ажилтан,Менежер"></div><div class="field"><label class="label">Department scope</label><input id="fRuleDepartment" value="${esc(r.department_scope||'')}" placeholder="Борлуулалт"></div><div class="field"><label class="label">Priority</label><input id="fRulePriority" type="number" value="${esc(r.priority)}"></div><div class="field"><label class="label">Төлөв</label><select id="fRuleActive"><option value="true" ${boolValue(r.active)?'selected':''}>Active</option><option value="false" ${!boolValue(r.active)?'selected':''}>Disabled</option></select></div><div class="field"><label class="label">Deterministic metric</label><select id="fRuleMetric"><option value="" ${!r.metric?'selected':''}>None / AI decides</option><option value="percent" ${r.metric==='percent'?'selected':''}>Percent (%)</option><option value="mnt" ${r.metric==='mnt'?'selected':''}>MNT</option><option value="number" ${r.metric==='number'?'selected':''}>Number</option></select></div><div class="field"><label class="label">Min value</label><input id="fRuleMin" type="number" step="any" value="${r.min_value ?? ''}"></div><div class="field"><label class="label">Max value</label><input id="fRuleMax" type="number" step="any" value="${r.max_value ?? ''}"></div><div class="field"><label class="label">Boundary</label><div style="display:flex;gap:10px"><label class="help"><input id="fRuleMinInc" type="checkbox" style="width:auto" ${boolValue(r.min_inclusive)?'checked':''}> min inclusive</label><label class="help"><input id="fRuleMaxInc" type="checkbox" style="width:auto" ${boolValue(r.max_inclusive)?'checked':''}> max inclusive</label></div></div><div class="field"><label class="label">Source document</label><select id="fRuleDocument">${docs}</select></div><div class="field"><label class="label">Source section</label><input id="fRuleSection" value="${esc(r.source_section||'')}"></div></div>`,onSave:async()=>{
    const payload={id:$('fRuleId').value.trim(),title:$('fRuleTitle').value.trim(),text:$('fRuleText').value.trim(),category:$('fRuleCategory').value.trim()||'general',keywords:$('fRuleKeywords').value.trim(),decision_hint:$('fRuleDecision').value,approver:$('fRuleApprover').value.trim(),role_scope:$('fRuleRole').value.trim(),department_scope:$('fRuleDepartment').value.trim(),priority:Number($('fRulePriority').value||100),metric:$('fRuleMetric').value,min_value:$('fRuleMin').value===''?null:Number($('fRuleMin').value),max_value:$('fRuleMax').value===''?null:Number($('fRuleMax').value),min_inclusive:$('fRuleMinInc').checked,max_inclusive:$('fRuleMaxInc').checked,source_document_id:$('fRuleDocument').value,source_section:$('fRuleSection').value.trim(),active:$('fRuleActive').value==='true'};
    await api('/api/admin/rules',{method:'POST',body:JSON.stringify(payload)}); await loadRules();
  }});
}
async function deleteRule(id){ if(!confirm(`${id} дүрмийг устгах уу?`)) return; await api(`/api/admin/rules/${encodeURIComponent(id)}`,{method:'DELETE'}); await loadRules(); }

async function loadKnowledgeHealth(){
  try{
    state.knowledgeHealth=await api('/api/admin/knowledge-health'); const k=state.knowledgeHealth;
    const issues=(k.issues||[]).map(x=>`<div class="kh-issue ${esc(x.level)}"><span>${x.level==='danger'?'!':x.level==='warning'?'△':'i'}</span><div><strong>${esc(x.title)}</strong><p>${esc(x.detail)}</p></div></div>`).join('');
    $('knowledgeHealth').innerHTML=`<div class="kh-score"><small>KNOWLEDGE HEALTH</small><strong>${esc(k.score)}%</strong><span>${k.score>=90?'Healthy':k.score>=70?'Needs review':'Action needed'}</span></div><div class="kh-metrics"><div><strong>${esc(k.active_documents)}</strong><span>active docs</span></div><div><strong>${esc(k.hybrid_documents)}</strong><span>hybrid index</span></div><div><strong>${esc(k.active_rules)}</strong><span>active rules</span></div><div><strong>${esc(k.future_documents)}</strong><span>scheduled docs</span></div></div><div class="kh-issues">${issues||'<div class="kh-clean">✓ Knowledge source-ийн зөрчил илрээгүй.</div>'}</div>`;
  }catch(e){ $('knowledgeHealth').innerHTML=`<div class="kh-clean">Knowledge health ачаалж чадсангүй: ${esc(e.message)}</div>`; }
}

async function loadDocuments(){ state.documents=await api('/api/admin/documents'); renderDocuments(); }
function renderDocuments(){
  const q=($('documentSearch')?.value||'').toLowerCase(); const docs=state.documents.filter(d=>`${d.title} ${d.filename} ${d.category}`.toLowerCase().includes(q));
  $('documentCount').textContent=`${state.documents.length} баримт`;
  $('documentGrid').innerHTML=docs.length?docs.map(d=>`<article class="card doc-card ${d.status==='archived'?'is-archived':''}"><div class="doc-card-head"><div class="doc-icon">${d.filename.toLowerCase().endsWith('.pdf')?'P':'D'}</div><span class="status ${d.status==='active'?'active':'inactive'}">${d.status==='active'?'Active':'Archived'}</span></div><h3>${esc(d.title)}</h3><p>${esc(d.filename)}</p><div class="doc-meta"><span>${esc(d.category)}</span><span>v${esc(d.version)}</span><span>${esc(d.chunk_count)} chunks</span><span>${esc(d.index_mode)}</span><span>${esc(d.visibility)}</span>${d.effective_from?`<span>from ${esc(d.effective_from)}</span>`:''}${d.effective_to?`<span>to ${esc(d.effective_to)}</span>`:''}</div><div class="doc-actions"><button class="btn btn-sm" data-preview-doc="${esc(d.id)}">Preview</button><button class="btn btn-sm" data-status-doc="${esc(d.id)}" data-next-status="${d.status==='active'?'archived':'active'}">${d.status==='active'?'Archive':'Activate'}</button><button class="btn btn-sm" data-reindex-doc="${esc(d.id)}" ${d.status==='archived'?'disabled':''}>↻ Reindex</button><button class="btn btn-sm btn-danger" data-delete-doc="${esc(d.id)}">Устгах</button></div></article>`).join(''):'<div class="card empty-state"><div class="empty-icon">▤</div>Knowledge base хоосон байна.</div>';
  document.querySelectorAll('[data-preview-doc]').forEach(b=>b.onclick=()=>previewDocument(b.dataset.previewDoc));
  document.querySelectorAll('[data-status-doc]').forEach(b=>b.onclick=()=>setDocStatus(b.dataset.statusDoc,b.dataset.nextStatus));
  document.querySelectorAll('[data-reindex-doc]').forEach(b=>b.onclick=()=>reindexDoc(b.dataset.reindexDoc));
  document.querySelectorAll('[data-delete-doc]').forEach(b=>b.onclick=()=>deleteDoc(b.dataset.deleteDoc));
}
function addDocument(){
  openModal({title:'Knowledge base-д баримт оруулах',wide:true,saveText:'Upload & index',body:`<div class="form-grid"><div class="field span-2"><label class="label">Файл</label><input id="fDocFile" type="file" accept=".pdf,.docx,.xlsx,.txt,.md,.csv"><div class="help">PDF, DOCX, XLSX, TXT, MD, CSV · max 40MB</div></div><div class="field span-2"><label class="label">Гарчиг</label><input id="fDocTitle" placeholder="Хөдөлмөрийн дотоод журам"></div><div class="field"><label class="label">Category</label><input id="fDocCategory" value="general"></div><div class="field"><label class="label">Version</label><input id="fDocVersion" value="1.0"></div><div class="field"><label class="label">Visibility</label><select id="fDocVisibility"><option value="all">All employees</option><option value="department">Specific department</option><option value="admin">Admin only</option></select></div><div class="field"><label class="label">Department</label><select id="fDocDepartment">${departmentOptions()}</select></div><div class="field"><label class="label">Effective from</label><input id="fDocFrom" type="date"></div><div class="field"><label class="label">Effective to</label><input id="fDocTo" type="date"></div><div class="field span-2"><label class="help doc-archive-toggle"><input id="fDocArchivePrevious" type="checkbox" checked> Ижил гарчигтай өмнөх active version-уудыг автоматаар archive хийх</label></div></div>`,onSave:async()=>{
    const file=$('fDocFile').files[0]; if(!file) throw new Error('Файлаа сонгоно уу.');
    const fd=new FormData(); fd.append('file',file); fd.append('title',$('fDocTitle').value.trim()||file.name); fd.append('category',$('fDocCategory').value.trim()||'general'); fd.append('version',$('fDocVersion').value.trim()||'1.0'); fd.append('visibility',$('fDocVisibility').value); fd.append('department_id',$('fDocDepartment').value); fd.append('effective_from',$('fDocFrom').value); fd.append('effective_to',$('fDocTo').value); fd.append('archive_previous',String($('fDocArchivePrevious').checked));
    await api('/api/admin/documents/upload',{method:'POST',body:fd}); await loadDocuments(); await Promise.all([loadDashboard(),loadKnowledgeHealth()]);
  }});
}
async function previewDocument(id){
  try{
    const d=await api(`/api/documents/${encodeURIComponent(id)}/preview`);
    const chunks=(d.chunks||[]).map(c=>`<section class="preview-chunk"><strong>${esc(c.section||'Баримт')}</strong><p>${esc(c.content||'')}</p></section>`).join('');
    openModal({title:d.title,kicker:'KNOWLEDGE PREVIEW',wide:true,onSave:null,body:`<div class="doc-preview-meta"><span>${esc(d.filename)}</span><span>v${esc(d.version)}</span><span>${esc(d.category)}</span><span>${esc(d.status)}</span></div><div class="doc-preview-actions"><a class="secondary-action" href="/api/documents/${encodeURIComponent(d.id)}/file">↓ Original файл</a></div><div class="preview-chunks">${chunks||'<div class="empty-state">Preview text алга.</div>'}</div>`});
  }catch(e){ alert(e.message); }
}

async function setDocStatus(id,status){ const label=status==='archived'?'архивлах':'идэвхжүүлэх'; if(!confirm(`Энэ баримтыг ${label} уу?`)) return; try{ await api(`/api/admin/documents/${encodeURIComponent(id)}/status`,{method:'PATCH',body:JSON.stringify({status})}); await loadDocuments(); await Promise.all([loadDashboard(),loadKnowledgeHealth()]); }catch(e){ alert(e.message); } }
async function reindexDoc(id){ try{ const result=await api(`/api/admin/documents/${encodeURIComponent(id)}/reindex`,{method:'POST'}); alert(`Reindex дууслаа: ${result.chunks} chunks · ${result.index_mode}`); await Promise.all([loadDocuments(),loadKnowledgeHealth()]); }catch(e){ alert(e.message); } }
async function deleteDoc(id){ if(!confirm('Баримт болон индексийг бүрэн устгах уу?')) return; await api(`/api/admin/documents/${encodeURIComponent(id)}`,{method:'DELETE'}); await loadDocuments(); await Promise.all([loadDashboard(),loadKnowledgeHealth()]); }

async function loadResponsibilities(){ state.responsibilities=await api('/api/admin/responsibilities'); renderResponsibilities(); }
function renderResponsibilities(){
  const rows=state.responsibilities;
  $('responsibilityGrid').innerHTML=rows.length?rows.map(r=>`<article class="card rule-card"><div class="rule-top"><div><div class="rule-id">${esc(r.id)}</div><h3>${esc(r.topic)}</h3></div><span class="status ${boolValue(r.active)?'active':'inactive'}">${boolValue(r.active)?'Active':'Off'}</span></div><p>${esc(r.instructions||'')}</p><div class="rule-tags"><span>${esc(r.keywords)}</span>${r.user_name?`<span>${esc(r.user_name)}</span>`:''}${r.role?`<span>${esc(r.role)}</span>`:''}${r.department?`<span>${esc(r.department)}</span>`:''}</div><div class="rule-actions"><button class="btn btn-sm" data-edit-resp="${esc(r.id)}">Засах</button><button class="btn btn-sm btn-danger" data-delete-resp="${esc(r.id)}">Устгах</button></div></article>`).join(''):'<div class="card empty-state"><div class="empty-icon">↗</div>Routing тохиргоо алга.</div>';
  document.querySelectorAll('[data-edit-resp]').forEach(b=>b.onclick=()=>editResponsibility(b.dataset.editResp)); document.querySelectorAll('[data-delete-resp]').forEach(b=>b.onclick=()=>deleteResponsibility(b.dataset.deleteResp));
}
function editResponsibility(id=null){
  const r=state.responsibilities.find(x=>x.id===id)||{id:'',topic:'',keywords:'',department_id:'',user_id:'',role_id:'',instructions:'',active:1};
  openModal({title:id?'Routing засах':'Routing нэмэх',body:`<div class="form-grid"><div class="field"><label class="label">ID</label><input id="fRespId" value="${esc(r.id)}" ${id?'readonly':''}></div><div class="field"><label class="label">Topic</label><input id="fRespTopic" value="${esc(r.topic)}"></div><div class="field span-2"><label class="label">Keywords</label><input id="fRespKeywords" value="${esc(r.keywords)}" placeholder="гэрээ,NDA,хууль"></div><div class="field"><label class="label">Хариуцах хэлтэс</label><select id="fRespDept">${departmentOptions(r.department_id)}</select></div><div class="field"><label class="label">Хариуцах role</label><select id="fRespRole">${roleOptions(r.role_id)}</select></div><div class="field span-2"><label class="label">Хариуцах хүн</label><select id="fRespUser">${userOptions(r.user_id)}</select></div><div class="field span-2"><label class="label">Заавар</label><textarea id="fRespInstructions">${esc(r.instructions||'')}</textarea></div><div class="field"><label class="label">Төлөв</label><select id="fRespActive"><option value="true" ${boolValue(r.active)?'selected':''}>Active</option><option value="false" ${!boolValue(r.active)?'selected':''}>Disabled</option></select></div></div>`,onSave:async()=>{
    const payload={id:$('fRespId').value.trim(),topic:$('fRespTopic').value.trim(),keywords:$('fRespKeywords').value.trim(),department_id:$('fRespDept').value?Number($('fRespDept').value):null,user_id:$('fRespUser').value?Number($('fRespUser').value):null,role_id:$('fRespRole').value?Number($('fRespRole').value):null,instructions:$('fRespInstructions').value.trim(),active:$('fRespActive').value==='true'};
    await api('/api/admin/responsibilities',{method:'POST',body:JSON.stringify(payload)}); await loadResponsibilities();
  }});
}
async function deleteResponsibility(id){ if(!confirm('Routing устгах уу?'))return; await api(`/api/admin/responsibilities/${encodeURIComponent(id)}`,{method:'DELETE'}); await loadResponsibilities(); }

async function loadGaps(){
  try{
    state.gaps=await api('/api/admin/unanswered?limit=100');
    $('gapNavCount').textContent=state.gaps.length; $('gapCount').textContent=`${state.gaps.length} gap`;
    $('gapList').innerHTML=state.gaps.length?state.gaps.map(g=>`<article class="gap-item"><div class="gap-mark">?</div><div><strong>${esc(g.question||'Асуулт')}</strong><p>${esc(g.user_name)} · ${esc(g.mode||'auto')} · ${esc(fmtDate(g.created_at))}</p></div><button class="small-action" data-gap-question="${esc(g.question||'')}">Дүрэм нэмэх</button></article>`).join(''):'<div class="empty-state polished"><div class="empty-icon">✓</div><strong>Knowledge gap алга</strong><p>DUREM сүүлийн асуултуудад эх сурвалжтай хариулж чадсан байна.</p></div>';
    document.querySelectorAll('[data-gap-question]').forEach(b=>b.onclick=()=>{switchSection('rules');editRule(null,b.dataset.gapQuestion);});
  }catch(e){ console.warn(e); }
}

function renderSecurityMini(){
  if(!state.security)return; const score=Number(state.security.score||0);
  $('securityMiniScore').textContent=`${score}%`; $('securityMiniBar').style.width=`${score}%`;
  $('securityMiniText').textContent=score>=90?'Production security сайн байна.':score>=70?'Хэдэн hardening тохиргоо үлдсэн байна.':'LAN production өмнө security hardening шаардлагатай.';
}

async function loadSecurity(){
  try{
    state.security=await api('/api/admin/security'); const s=state.security; const score=Number(s.score||0);
    $('securityScore').textContent=`${score}%`; $('securityScoreRing').style.setProperty('--score',score);
    $('securityHeadline').textContent=score>=90?'Security posture маш сайн':score>=70?'Security posture боломжийн':'Production hardening шаардлагатай';
    $('failedLoginCount').textContent=s.failed_logins_24h; $('negativeFeedbackCount').textContent=s.negative_feedback_7d;
    $('securityChecks').innerHTML=s.checks.map(c=>`<div class="security-check ${c.ok?'ok':'warn'}"><span class="security-check-icon">${c.ok?'✓':'!'}</span><div><strong>${esc(c.label)}</strong><p>${esc(c.detail)}</p></div><span class="security-check-state">${c.ok?'Ready':'Action'}</span></div>`).join('');
    $('sessionCount').textContent=`${s.sessions.length} session`;
    $('sessionList').innerHTML=s.sessions.length?s.sessions.map(item=>`<div class="session-item"><div class="session-device"><span>${item.current?'●':'○'}</span><div><strong>${esc(item.user_name)} ${item.current?'<em>Энэ session</em>':''}</strong><p>${esc(item.ip_address||'unknown')} · ${esc((item.user_agent||'Unknown device').slice(0,90))}</p><small>Last seen ${esc(fmtDate(item.last_seen_at))}</small></div></div>${item.current?'':'<button class="small-action danger" data-revoke-session="'+esc(item.id)+'">Revoke</button>'}</div>`).join(''):'<div class="empty-state">Session алга.</div>';
    document.querySelectorAll('[data-revoke-session]').forEach(b=>b.onclick=()=>revokeSession(b.dataset.revokeSession));
    const apiTokens=s.api_tokens||[]; $('apiTokenCount').textContent=`${apiTokens.length} token`;
    $('apiTokenList').innerHTML=apiTokens.length?apiTokens.map(item=>`<div class="session-item"><div class="session-device"><span>◇</span><div><strong>${esc(item.user_name)}</strong><p>${esc(item.device_name||'DUREM App')}</p><small>Last used ${esc(fmtDate(item.last_used_at))} · expires ${esc(fmtDate(item.expires_at))}</small></div></div><button class="small-action danger" data-revoke-api="${esc(item.id)}">Revoke</button></div>`).join(''):'<div class="empty-state">Active API token алга.</div>';
    document.querySelectorAll('[data-revoke-api]').forEach(b=>b.onclick=()=>revokeApiToken(b.dataset.revokeApi));
    renderSecurityMini();
  }catch(e){console.warn(e);}
}

async function revokeSession(id){
  if(!confirm('Энэ session-ийг шууд хаах уу?'))return;
  await api(`/api/admin/sessions/${encodeURIComponent(id)}`,{method:'DELETE'});
  await loadSecurity();
}
async function revokeApiToken(id){
  if(!confirm('Энэ app API token-ийг revoke хийх үү?'))return;
  await api(`/api/admin/api-tokens/${encodeURIComponent(id)}`,{method:'DELETE'});
  await loadSecurity();
}

async function loadAudit(){
  const rows=await api('/api/admin/audit?limit=200');
  $('auditTable').innerHTML=rows.length?rows.map(a=>`<tr><td>${esc(fmtDate(a.created_at))}</td><td>${esc(a.user_name)}</td><td>${esc(a.event_type)}</td><td><strong>${esc(a.action)}</strong></td><td><code style="font-size:10px;white-space:pre-wrap">${esc(JSON.stringify(a.metadata||{}).slice(0,500))}</code></td></tr>`).join(''):'<tr><td colspan="5">Audit алга.</td></tr>';
}

async function loadSettings(){
  state.settings=await api('/api/admin/settings'); const s=state.settings; $('companyName').textContent=s.company_name;
  $('settingCompany').value=s.company_name; $('settingModel').value=s.llm_model; $('settingEmbedding').value=s.embedding_model; $('settingEmbeddingEnabled').value=String(!!s.embeddings_enabled);
  $('settingGeneralChat').value=String(!!s.general_chat_enabled); $('settingAutoRouting').value=String(!!s.auto_routing_enabled); $('settingHybridRouter').value=String(!!s.hybrid_router_enabled); $('settingPersonalMemory').value=String(!!s.personal_memory_enabled);
  $('settingChatHistory').value=String(s.chat_history_messages||16); $('settingRawChatAudit').value=String(!!s.store_raw_chat_questions); $('settingApiAccess').value=String(!!s.api_access_enabled); $('settingApiTokenTtl').value=String(s.api_token_ttl_days||30);
  $('systemFacts').innerHTML=[['Lemonade',s.lemonade_base_url],['Data directory',s.data_dir],['LLM',s.llm_model],['Embedding',s.embeddings_enabled?s.embedding_model:'Disabled'],['General chat',s.general_chat_enabled?'Enabled':'Disabled'],['Hybrid router',s.hybrid_router_enabled?'Enabled':'Deterministic only'],['Personal memory',s.personal_memory_enabled?'Enabled':'Disabled'],['App API',s.api_access_enabled?'v1 Bearer enabled':'Disabled']].map(([a,b])=>`<div class="system-fact"><span>${esc(a)}</span><strong style="text-align:right;max-width:210px;word-break:break-all">${esc(b)}</strong></div>`).join('');
}
$('settingsForm').addEventListener('submit',async e=>{e.preventDefault();$('settingsError').hidden=true;$('settingsSuccess').hidden=true;try{const payload={company_name:$('settingCompany').value.trim(),llm_model:$('settingModel').value.trim(),embedding_model:$('settingEmbedding').value.trim(),embeddings_enabled:$('settingEmbeddingEnabled').value==='true',general_chat_enabled:$('settingGeneralChat').value==='true',auto_routing_enabled:$('settingAutoRouting').value==='true',hybrid_router_enabled:$('settingHybridRouter').value==='true',personal_memory_enabled:$('settingPersonalMemory').value==='true',chat_history_messages:Number($('settingChatHistory').value||16),store_raw_chat_questions:$('settingRawChatAudit').value==='true',api_access_enabled:$('settingApiAccess').value==='true',api_token_ttl_days:Number($('settingApiTokenTtl').value||30)};await api('/api/admin/settings',{method:'PUT',body:JSON.stringify(payload)});$('settingsSuccess').textContent='Тохиргоо хадгалагдлаа.';$('settingsSuccess').hidden=false;await Promise.all([loadSettings(),loadHealth()]);}catch(error){$('settingsError').textContent=error.message;$('settingsError').hidden=false;}});

function createBackup(){
  openModal({
    title:'Encrypted backup үүсгэх', kicker:'BACKUP & RECOVERY', saveText:'Backup татах',
    body:`<div class="restore-warning"><strong>🔐 Backup AES-256-GCM-ээр шифрлэгдэнэ</strong><p>Энэ passphrase DUREM дээр хадгалагдахгүй. Найдвартай газар хадгал.</p></div><div class="form-grid"><div class="field span-2"><label class="label">Backup passphrase</label><input id="fBackupPass" type="password" autocomplete="new-password" minlength="12" placeholder="12+ тэмдэгт"><div class="help">Урт, давтагддаггүй passphrase ашиглана.</div></div><div class="field span-2"><label class="label">Passphrase давтах</label><input id="fBackupPass2" type="password" autocomplete="new-password" minlength="12"></div></div>`,
    onSave:async()=>{
      const pass=$('fBackupPass').value; const pass2=$('fBackupPass2').value;
      if(pass.length<12) throw new Error('Passphrase хамгийн багадаа 12 тэмдэгт байна.');
      if(pass!==pass2) throw new Error('Passphrase таарахгүй байна.');
      const response=await fetch('/api/admin/backup',{method:'POST',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/json'},body:JSON.stringify({passphrase:pass})});
      if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||'Backup алдаа');}
      const blob=await response.blob(); const disposition=response.headers.get('content-disposition')||'';
      const match=disposition.match(/filename="?([^";]+)"?/); const filename=match?.[1]||'durem-backup.durem';
      const url=URL.createObjectURL(blob); const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
      setTimeout(()=>loadSecurity(),250);
    }
  });
}

function restoreBackup(){
  openModal({
    title:'Backup restore хийх', kicker:'DANGER ZONE', saveText:'Restore эхлүүлэх', wide:true,
    body:`<div class="restore-warning danger"><strong>⚠ Одоогийн data солигдоно</strong><p>Restore нь database болон documents-ийг backup-ийн төлөв рүү буцаана. Бүх идэвхтэй session хаагдана. DUREM restore хийхээс өмнө одоогийн data-г rollback snapshot болгож түр хадгалдаг.</p></div><div class="form-grid"><div class="field span-2"><label class="label">Backup файл</label><input id="fRestoreFile" type="file" accept=".durem,.zip"><div class="help">Шинэ encrypted .durem эсвэл legacy .zip backup.</div></div><div class="field span-2"><label class="label">Passphrase</label><input id="fRestorePass" type="password" autocomplete="off" placeholder="Encrypted backup бол заавал"></div><div class="field span-2"><label class="label">Баталгаажуулахын тулд RESTORE гэж бич</label><input id="fRestoreConfirm" autocomplete="off" placeholder="RESTORE"></div></div>`,
    onSave:async()=>{
      const file=$('fRestoreFile').files[0]; if(!file) throw new Error('Backup файл сонгоно уу.');
      if($('fRestoreConfirm').value.trim()!=='RESTORE') throw new Error('RESTORE гэж яг бичиж баталгаажуулна уу.');
      const fd=new FormData(); fd.append('file',file); fd.append('passphrase',$('fRestorePass').value);
      const response=await fetch('/api/admin/restore',{method:'POST',headers:{'X-CSRF-Token':csrf},body:fd});
      const data=await response.json().catch(()=>({})); if(!response.ok) throw new Error(data.detail||'Restore алдаа');
      sessionStorage.removeItem('durem_csrf');
      alert(`Restore амжилттай. Backup: ${data.created_at||'unknown'}\nБүх session хаагдсан тул дахин нэвтэрнэ үү.`);
      location.href='/login';
    }
  });
}

$('refreshDashboard').onclick=()=>Promise.all([loadDashboard(),loadHealth(),loadSecurity(),loadGaps()]); $('refreshAudit').onclick=loadAudit; $('refreshGaps').onclick=loadGaps; $('refreshSecurity').onclick=loadSecurity;
$('addDepartmentBtn').onclick=()=>editDepartment(); $('addRoleBtn').onclick=()=>editRole(); $('addUserBtn').onclick=()=>editUser();
$('addRuleBtn').onclick=()=>editRule(); $('addDocumentBtn').onclick=addDocument; $('addResponsibilityBtn').onclick=()=>editResponsibility(); $('backupBtn').onclick=createBackup; $('restoreBtn').onclick=restoreBackup;
$('ruleSearch').addEventListener('input',renderRules); $('documentSearch').addEventListener('input',renderDocuments); document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>switchSection(b.dataset.jump));
$('logoutBtn').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});}finally{sessionStorage.removeItem('durem_csrf');location.href='/login';}};
$('mobileMenu').onclick=()=>document.body.classList.toggle('sidebar-open');
document.addEventListener('click',event=>{if(document.body.classList.contains('sidebar-open')&&!event.target.closest('.admin-sidebar')&&!event.target.closest('#mobileMenu'))document.body.classList.remove('sidebar-open');});

bootstrap().catch(error=>{console.error(error);alert(error.message);});
