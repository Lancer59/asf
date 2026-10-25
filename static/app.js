const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {view:'chat', skills:[], selected:null, busy:false, lastRequest:''};

async function api(url, options={}) {
  const response = await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  const data = await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.detail || data.message || `Request failed (${response.status})`);
  return data;
}

function selectView(view) {
  state.view=view;
  $('#chat-view').classList.toggle('hidden',view!=='chat');
  $('#skills-view').classList.toggle('hidden',view!=='skills');
  $$('.nav-item').forEach(item=>item.classList.toggle('active',item.dataset.view===view));
  $('#page-title').textContent=view==='chat'?'Chat':'Skills';
  $('#page-eyebrow').textContent=view==='chat'?'WORKSPACE / CONVERSATION':'CAPABILITY MANAGEMENT / LIBRARY';
  if(view==='skills') loadSkills();
}

function addMessage(role, text, steps=[]) {
  const box=$('#messages');
  const welcome=$('.welcome',box); if(welcome) welcome.remove();
  const node=document.createElement('div'); node.className=`message ${role}`;
  const avatar=role==='user'?'U':'✳';
  node.innerHTML=`<div class="avatar">${avatar}</div><div class="bubble">${formatMessage(text)}</div>`;
  box.append(node);
  if(steps?.length){
    const details=document.createElement('details'); details.className='message';
    details.innerHTML=`<summary class="thinking">Agent activity · ${steps.length} events</summary><div class="bubble">${steps.map(s=>esc(typeof s.content==='string'?s.content:JSON.stringify(s.content))).join('\n\n')}</div>`;
    box.append(details);
  }
  box.scrollTop=box.scrollHeight;
}

function formatMessage(text) {
  let value=esc(typeof text==='string'?text:JSON.stringify(text));
  value=value.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1 ↗</a>');
  value=value.replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target="_blank" rel="noopener noreferrer">$1 ↗</a>');
  return value;
}

async function sendMessage(text) {
  text=text.trim(); if(!text||state.busy)return;
  state.busy=true; state.lastRequest=text;
  addMessage('user',text);
  const indicator=document.createElement('div'); indicator.className='thinking'; indicator.textContent='✳  Agent is working…'; $('#messages').append(indicator);
  $('#chat-input').value='';
  try {
    const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    if(!response.ok) throw new Error((await response.json()).detail||`Request failed (${response.status})`);
    const raw=await response.text(); indicator.remove();
    const line=raw.split('\n').find(l=>l.startsWith('data: '));
    if(!line) throw new Error('The agent ended without a response.');
    const event=JSON.parse(line.slice(6));
    addMessage('assistant',event.reply||event.error||'No response.',event.steps||[]);
    if(event.error) addMessage('assistant',`Configuration or provider error: ${event.error}`);
    if(event.draft) renderDraft(event.draft);
  } catch(error) {indicator.remove();addMessage('assistant',`Request failed: ${error.message}`)}
  finally {state.busy=false;$('#chat-input').focus()}
}

$('#chat-form').addEventListener('submit',e=>{e.preventDefault();sendMessage($('#chat-input').value)});
$('#chat-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#chat-form').requestSubmit()}});
$$('.suggestions button').forEach(button=>button.addEventListener('click',()=>sendMessage(button.dataset.prompt)));
$$('.nav-item').forEach(button=>button.addEventListener('click',()=>selectView(button.dataset.view)));
$('#clear-chat').addEventListener('click',async()=>{await api('/api/chat/clear',{method:'POST'});$('#messages').innerHTML='<div class="welcome"><div class="welcome-glyph">✳</div><span class="eyebrow">YOUR CAPABILITY WORKSPACE</span><h2>What are we working on?</h2><p>Ask a question, use live web search, or put an approved skill to work. When a reusable capability is missing, we’ll shape one together.</p><div class="suggestions"><button data-prompt="Search the web for the latest Python 3.14 release notes and cite your sources."><span>↗</span> Search current information</button><button data-prompt="Help me make a reusable capability for a task I do often."><span>✳</span> Build a reusable skill</button></div></div>';$$('.suggestions button').forEach(b=>b.onclick=()=>sendMessage(b.dataset.prompt));$('#draft-host').innerHTML=''});

async function loadSkills() {
  try {state.skills=await api(`/api/skills?q=${encodeURIComponent($('#skill-search').value)}&status=${encodeURIComponent($('#status-filter').value)}`)}
  catch(error){$('#skill-list').innerHTML=`<p class="subtle">Could not load skills: ${esc(error.message)}</p>`;return}
  $('#skill-count').textContent=state.skills.filter(s=>s.status==='approved').length;
  $('#empty-skills').classList.toggle('hidden',state.skills.length>0);
  $('#skill-list').innerHTML=state.skills.map(skill=>`<div class="skill-card ${state.selected===skill.slug?'selected':''}" data-slug="${esc(skill.slug)}"><div class="skill-card-top"><strong>${esc(skill.name)}</strong><span class="status-pill ${esc(skill.status)}">${esc(skill.status)}</span><span class="version">v${skill.version}</span></div><p>${esc(skill.description)}</p></div>`).join('');
  $$('.skill-card').forEach(card=>card.onclick=()=>showSkill(card.dataset.slug));
}

async function showSkill(slug) {
  state.selected=slug; await loadSkills();
  const s=await api(`/api/skills/${encodeURIComponent(slug)}`); const d=s.content||{};
  $('#skill-detail').innerHTML=`<div class="detail-head"><div><span class="eyebrow">${esc(d.category||'GENERAL')} · ${esc(d.skill_type||'instruction')}</span><h2>${esc(s.name)}</h2><p>${esc(s.description)}</p></div><div class="detail-actions"><button class="button" id="edit-current">Edit revision</button><button class="button" id="test-current">Run test</button>${s.status==='approved'?'<button class="button danger" id="deprecate-current">Deprecate</button>':''}</div></div>
    <div class="detail-section"><div class="kv-grid"><div class="kv"><span>Status</span><strong>${esc(s.status)}</strong></div><div class="kv"><span>Current version</span><strong>v${s.version}</strong></div><div class="kv"><span>Last execution</span><strong>${esc(s.last_execution||'Not run')}</strong></div><div class="kv"><span>Updated</span><strong>${esc(new Date(s.updated_at).toLocaleString())}</strong></div></div></div>
    <div class="detail-section"><h3>Instructions</h3><pre>${esc(d.instructions||'No additional instructions.')}</pre></div>
    ${d.code?`<div class="detail-section"><h3>Reviewed Python source</h3><pre>${esc(d.code)}</pre></div>`:''}
    <div class="detail-section"><h3>Inputs · dependencies · permissions</h3><pre>${esc(JSON.stringify({inputs:d.inputs,dependencies:d.dependencies,permissions:d.permissions,tools:d.tools},null,2))}</pre></div>
    <div class="detail-section"><h3>Tests and evidence</h3><pre>${esc(JSON.stringify({tests:d.tests,last_test:s.last_test?JSON.parse(s.last_test):null},null,2))}</pre></div>
    <div class="detail-section"><h3>Version history</h3>${(s.history||[]).map(v=>`<div class="history-item"><strong>v${v.version} · ${esc(v.note)}</strong><small>${esc(v.actor)} · ${esc(new Date(v.changed_at).toLocaleString())}</small></div>`).join('')||'<p>No revisions yet.</p>'}</div>
    <div class="detail-section"><h3>Lifecycle audit</h3><pre>${esc(JSON.stringify(s.audit||[],null,2))}</pre></div><div id="detail-result"></div>`;
  $('#deprecate-current')?.addEventListener('click',async()=>{await api(`/api/skills/${slug}/deprecate`,{method:'POST'});showSkill(slug)});
  $('#test-current')?.addEventListener('click',async()=>{
    const inputs=prompt('Test inputs as JSON',JSON.stringify({user_input:''})); if(inputs===null)return;
    try{const result=await api(`/api/skills/${slug}/test`,{method:'POST',body:JSON.stringify({skill:{},inputs:JSON.parse(inputs)})});$('#detail-result').innerHTML=`<div class="test-result ${result.success?'':'fail'}">${esc(JSON.stringify(result,null,2))}</div>`}catch(e){$('#detail-result').innerHTML=`<div class="test-result fail">${esc(e.message)}</div>`}
  });
  $('#edit-current')?.addEventListener('click',()=>editSkill(s));
}

function editSkill(s){
  const d=s.content||{}; $('#skill-detail').insertAdjacentHTML('beforeend',`<div class="detail-section" id="revision-editor"><h3>Propose new version · v${s.version+1}</h3><div class="draft-form"><div class="field"><label for="rev-name">Name</label><input id="rev-name" value="${esc(d.name)}"></div><div class="field"><label for="rev-type">Skill type</label><select id="rev-type"><option ${d.skill_type==='instruction'?'selected':''}>instruction</option><option ${d.skill_type==='python'?'selected':''}>python</option><option ${d.skill_type==='hybrid'?'selected':''}>hybrid</option><option ${d.skill_type==='tool'?'selected':''}>tool</option></select></div><div class="field full"><label for="rev-description">Purpose</label><textarea id="rev-description" class="mid">${esc(d.description)}</textarea></div><div class="field full"><label for="rev-instructions">Instructions</label><textarea id="rev-instructions" class="mid">${esc(d.instructions)}</textarea></div>${d.code?`<div class="field full"><label for="rev-code">Python source</label><textarea id="rev-code" class="code">${esc(d.code)}</textarea></div>`:''}<div class="field full"><label for="rev-feedback">Change note</label><textarea id="rev-feedback" class="mid" placeholder="What changed in this revision?"></textarea></div></div><div class="draft-actions"><button class="button" id="cancel-revision">Cancel</button><button class="button button-primary" id="save-revision">Validate & save version</button></div><div id="rev-result"></div></div>`);
  $('#cancel-revision').onclick=()=>$('#revision-editor').remove();
  $('#save-revision').onclick=async()=>{const content={name:$('#rev-name').value,description:$('#rev-description').value,skill_type:$('#rev-type').value,instructions:$('#rev-instructions').value};if($('#rev-code'))content.code=$('#rev-code').value;try{const result=await api(`/api/skills/${s.slug}`,{method:'PATCH',body:JSON.stringify({content})});if(result.status==='test_failed')$('#rev-result').innerHTML=`<div class="test-result fail">${esc(JSON.stringify(result.test,null,2))}</div>`;else showSkill(s.slug)}catch(e){$('#rev-result').innerHTML=`<div class="test-result fail">${esc(e.message)}</div>`}};
}

$('#skill-search').addEventListener('input',()=>loadSkills());$('#status-filter').addEventListener('change',()=>loadSkills());
$('#new-skill').addEventListener('click',()=>{selectView('chat');$('#chat-input').value='Help me create a reusable skill for: ';$('#chat-input').focus()});

function renderDraft(draft) {
  const host=$('#draft-host'); const skill=draft.skill; const prior=JSON.stringify(skill.code||'');
  host.innerHTML=`<section class="draft-card" data-draft="${esc(draft.id)}"><div class="draft-head"><strong>✳ SKILL PROPOSAL · REVIEW REQUIRED</strong><small>UNAPPROVED · ${esc(skill.skill_type||'instruction')}</small></div><div class="draft-body"><div class="test-context">Original task · ${esc(draft.request)}</div><div class="draft-form" style="margin-top:11px"><div class="field"><label>Reusable capability name</label><input data-field="name" value="${esc(skill.name)}"></div><div class="field"><label>Skill type</label><select data-field="skill_type"><option ${skill.skill_type==='instruction'?'selected':''}>instruction</option><option ${skill.skill_type==='tool'?'selected':''}>tool</option><option ${skill.skill_type==='python'?'selected':''}>python</option><option ${skill.skill_type==='hybrid'?'selected':''}>hybrid</option></select></div><div class="field full"><label>Reusable purpose</label><textarea data-field="description" class="mid">${esc(skill.description)}</textarea></div><div class="field full"><label>Instructions</label><textarea data-field="instructions" class="mid">${esc(skill.instructions)}</textarea></div><div class="field"><label>Inputs / defaults · JSON</label><textarea data-field="inputs" class="mid">${esc(JSON.stringify(skill.inputs||{},null,2))}</textarea></div><div class="field"><label>Expected output</label><textarea data-field="expected_output" class="mid">${esc(skill.expected_output||'')}</textarea></div><div class="field"><label>Dependencies · JSON list</label><textarea data-field="dependencies" class="mid">${esc(JSON.stringify(skill.dependencies||[],null,2))}</textarea></div><div class="field"><label>Permissions · JSON list</label><textarea data-field="permissions" class="mid">${esc(JSON.stringify(skill.permissions||[],null,2))}</textarea></div><div class="field full"><label>Tests · JSON</label><textarea data-field="tests" class="mid">${esc(JSON.stringify(skill.tests||[],null,2))}</textarea></div><div class="field"><label>Tags · comma separated</label><input data-field="tags" value="${esc((skill.tags||[]).join(', '))}"></div><div class="field"><label>Category</label><input data-field="category" value="${esc(skill.category||'general')}"></div>${skill.code!==undefined?`<div class="field full"><label>Full Python source · reviewed source only</label><textarea data-field="code" class="code">${esc(skill.code||'')}</textarea></div>`:''}</div><div class="detail-section"><h3>Test inputs · JSON</h3><textarea data-test-input class="draft-feedback">${esc(JSON.stringify({user_input:draft.request},null,2))}</textarea></div><div class="draft-actions"><button class="button" data-action="test">Run test</button><button class="button button-warn" data-action="revise">Apply feedback & revise</button><button class="button button-danger" data-action="reject">Reject</button><button class="button button-primary" data-action="accept">Accept & run</button></div><div class="detail-section"><label class="field"><span class="eyebrow">FEEDBACK · ALWAYS AVAILABLE</span><textarea data-feedback class="draft-feedback" placeholder="What should change? Feedback triggers a visible revision and fresh review."></textarea></label></div><div data-attempts></div><div data-result></div></div></section>`;
  const card=$('[data-draft]',host); const collect=()=>{const s={...skill};$$('[data-field]',card).forEach(el=>{const k=el.dataset.field;if(['inputs','dependencies','permissions','tests'].includes(k)){try{s[k]=JSON.parse(el.value)}catch{throw Error(`${k} must be valid JSON`)}}else if(k==='tags'){s[k]=el.value.split(',').map(x=>x.trim()).filter(Boolean)}else{s[k]=el.value}});return s};
  const record=(result,source)=>{const n=document.createElement('div');n.className='detail-section';n.innerHTML=`<h3>Test attempt · ${esc(new Date().toLocaleString())}</h3><div class="test-context">Source SHA-256 · ${esc(result.source_hash||'not applicable')}\nExit status · ${esc(result.exit_code??'disabled')} · Duration · ${esc(result.duration_ms??0)} ms\nTested source snapshot follows\n${esc(source)}</div><div class="test-result ${result.success?'':'fail'}">${esc(JSON.stringify(result,null,2))}</div>`;$('[data-attempts]',card).prepend(n)};
  card.addEventListener('click',async e=>{const action=e.target.closest('[data-action]')?.dataset.action;if(!action)return;const result=$('[data-result]',card);try{const current=collect();if(action==='test'){const inputs=JSON.parse($('[data-test-input]',card).value);const tested=await api(`/api/skills/drafts/${draft.id}/test`,{method:'POST',body:JSON.stringify({skill:current,inputs})});record(tested,current.code||current.instructions)}else if(action==='revise'){const feedback=$('[data-feedback]',card).value.trim();const revised=await api(`/api/skills/drafts/${draft.id}/revise`,{method:'POST',body:JSON.stringify({action:'revise',skill:current,feedback})});if(revised.draft){const old=JSON.stringify(current,null,2),next=JSON.stringify(revised.draft.skill,null,2);renderDraft(revised.draft);const diff=document.createElement('div');diff.className='detail-section';diff.innerHTML=`<h3>Visible revision · inspect the change</h3><div class="draft-diff"><pre>BEFORE\n${esc(old)}</pre><pre>AFTER\n${esc(next)}</pre></div>`;$('#draft-host').prepend(diff)}}else if(action==='reject'){const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'reject',skill:current,feedback:''})});card.remove();addMessage('assistant',reviewed.message)}else{const inputs=JSON.parse($('[data-test-input]',card).value);const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'accept',skill:current,inputs})});if(reviewed.status==='approved'){card.remove();await loadSkills();selectView('skills');await showSkill(reviewed.skill.slug);addMessage('assistant',`Approved ${reviewed.skill.name} v${reviewed.skill.version}. The skill will be available on the next chat request.`)}else if(reviewed.test){record(reviewed.test,current.code||current.instructions);result.textContent='Acceptance validation failed. The draft remains open.'}else result.textContent=reviewed.message||reviewed.status}}
    catch(error){result.innerHTML=`<div class="test-result fail">${esc(error.message)}</div>`}});
}

async function config() {
  const c=await api('/api/config');
  const missing=[];if(!c.llm_configured)missing.push('GROQ_API_KEY');if(!c.search_configured)missing.push('TAVILY_API_KEY');
  $('#system-state').textContent=missing.length?`Setup needed · ${missing.length} key${missing.length>1?'s':''}`:'Agent and live search ready';
  if(missing.length){$('#config-banner').classList.remove('hidden');$('#config-copy').textContent=`Add ${missing.join(' and ')} to new/.env. The model is ${c.model}.`}
}
config().catch(()=>{});loadSkills().catch(()=>{});
