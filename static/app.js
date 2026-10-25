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
  node.innerHTML=`<div class="avatar">${avatar}</div><div class="bubble">${role==='assistant'?`<div class="response-content markdown-body">${formatMessage(text)}</div>`:`<div class="response-content">${esc(text)}</div>`}</div>`;
  box.append(node);
  box.scrollTop=box.scrollHeight;
  return node;
}

function formatMessage(text) {
  const source=String(typeof text==='string'?text:JSON.stringify(text)??'');
  const blocks=source.replace(/\r\n?/g,'\n').split(/\n{2,}/);
  return blocks.map(block=>{
    const trimmed=block.trim();if(!trimmed)return '';
    const code=trimmed.match(/^```([\w+-]*)\n([\s\S]*?)\n?```$/);
    if(code)return `<pre><code>${esc(code[2])}</code></pre>`;
    if(/^\s*\|?.+\|\s*\n\s*\|?\s*:?-{3,}/.test(trimmed)){
      const rows=trimmed.split('\n').filter(line=>!/^\s*\|?\s*:?-{3,}/.test(line)).map(line=>line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim()));
      if(rows.length)return `<div class="markdown-table-wrap"><table>${rows.map((row,i)=>`<tr>${row.map(cell=>`<${i===0?'th':'td'}>${inlineMarkdown(cell)}</${i===0?'th':'td'}>`).join('')}</tr>`).join('')}</table></div>`;
    }
    if(/^\s*[-*+]\s/m.test(trimmed))return `<ul>${trimmed.split('\n').map(line=>line.match(/^\s*[-*+]\s+(.*)$/)).filter(Boolean).map(m=>`<li>${inlineMarkdown(m[1])}</li>`).join('')}</ul>`;
    if(/^\s*\d+\.\s/m.test(trimmed))return `<ol>${trimmed.split('\n').map(line=>line.match(/^\s*\d+\.\s+(.*)$/)).filter(Boolean).map(m=>`<li>${inlineMarkdown(m[1])}</li>`).join('')}</ol>`;
    const heading=trimmed.match(/^(#{1,6})\s+(.*)$/);
    if(heading){const n=Math.min(heading[1].length,4);return `<h${n}>${inlineMarkdown(heading[2])}</h${n}>`}
    return `<p>${inlineMarkdown(trimmed).replace(/\n/g,'<br>')}</p>`;
  }).join('');
}

function inlineMarkdown(value){
  let html=esc(value);
  html=html.replace(/`([^`]+)`/g,'<code>$1</code>');
  html=html.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/__(.+?)__/g,'<strong>$1</strong>');
  html=html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g,'<em>$1</em>').replace(/_([^_]+)_/g,'<em>$1</em>');
  html=html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1 ↗</a>');
  return html.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,'$1<a href="$2" target="_blank" rel="noopener noreferrer">$2 ↗</a>');
}

function addActivity(messageNode, events, open=false){
  let panel=$('.activity-panel',messageNode);
  if(!panel){panel=document.createElement('details');panel.className='activity-panel';panel.innerHTML='<summary class="thinking"><span class="activity-title">Steps</span><span class="activity-state"></span></summary><ol class="activity-events"></ol>';messageNode.querySelector('.bubble').append(panel)}
  if(open)panel.open=true;
  const list=$('.activity-events',panel);
  for(const event of events){
    const item=document.createElement('li');
    const label=event.label|| (event.type==='tool_start'?'Working…':event.type==='tool_end'?'Done':event.message||'Working on your request');
    item.innerHTML=`<strong>${esc(label)}</strong>${event.input&&event.type==='tool_start'?`<pre>${esc(JSON.stringify(event.input,null,2))}</pre>`:''}${event.output?`<pre>${esc(event.output)}</pre>`:''}`;
    list.append(item);
  }
  $('.activity-title',panel).textContent=`Steps · ${list.children.length}`;
  $('.activity-state',panel).textContent=open?'In progress…':'';
}

async function sendMessage(text) {
  text=text.trim(); if(!text||state.busy)return;
  state.busy=true; state.lastRequest=text;
  addMessage('user',text);
  const assistant=addMessage('assistant','');
  const bubble=$('.response-content',assistant);bubble.innerHTML='<span class="stream-placeholder">✳ Working…</span>';
  const events=[];let pending='';
  $('#chat-input').value='';
  try {
    const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    if(!response.ok) throw new Error((await response.json()).detail||`Request failed (${response.status})`);
    const reader=response.body.getReader(),decoder=new TextDecoder();
    const handleEvent=event=>{if(event.type==='token'){const placeholder=$('.stream-placeholder',bubble);if(placeholder)placeholder.remove();state.liveReply=(state.liveReply||'')+event.text;bubble.innerHTML=formatMessage(state.liveReply)}else if(event.type==='done'){if(!state.liveReply)bubble.innerHTML=formatMessage(event.reply||'No response.');if(event.error)bubble.innerHTML=formatMessage(event.error);state.liveReply='';const status=$('.activity-state',assistant);if(status)status.textContent=''}else if(event.type==='draft'){renderDraft(event.draft)}else if(event.type==='skill_offer'){showSkillOffer(assistant,event.request)}else{events.push(event);addActivity(assistant,[event],true)}};
    while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const frames=pending.split('\n\n');pending=frames.pop();for(const frame of frames){const line=frame.split('\n').find(l=>l.startsWith('data: '));if(line)handleEvent(JSON.parse(line.slice(6)))}}
    pending+=decoder.decode();if(pending.trim()){const line=pending.split('\n').find(l=>l.startsWith('data: '));if(line)handleEvent(JSON.parse(line.slice(6)))}
    if(!events.length)addActivity(assistant,[{type:'status',message:'Request completed'}]);
  } catch(error) {bubble.innerHTML=formatMessage(`Request failed: ${error.message}`);addActivity(assistant,[{type:'error',message:error.message}],true)}
  finally {state.busy=false;$('#chat-input').focus()}
}

function showSkillOffer(messageNode,request){
  const offer=document.createElement('div');offer.className='skill-offer';
  offer.innerHTML=`<span>Want me to turn this into a reusable skill?</span><button class="button button-primary">Create skill proposal</button>`;
  $('.response-content',messageNode).append(offer);
  $('button',offer).onclick=async()=>{const button=$('button',offer);button.disabled=true;button.textContent='Preparing proposal…';try{const draft=await api('/api/skills/drafts',{method:'POST',body:JSON.stringify({message:request})});renderDraft(draft);offer.remove()}catch(error){button.disabled=false;button.textContent='Try again';offer.insertAdjacentHTML('beforeend',`<small>${esc(error.message)}</small>`)}};
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
  const host=$('#draft-host'); const skill=draft.skill||{};
  const defaults={name:'New reusable capability',description:`A reusable capability for: ${draft.request||'your task'}`,skill_type:'instruction',instructions:`Define a repeatable process for: ${draft.request||'the supplied task'}\nKeep request-specific values as inputs.`,inputs:{user_input:{type:'string',default:''}},expected_output:'A useful result for the supplied input.',dependencies:[],permissions:[],tests:[],tags:[],category:'general',tools:[]};
  for(const [key,value] of Object.entries(defaults))if(skill[key]===undefined||skill[key]===null)skill[key]=value;
  const testInputs=()=>{
    if(Array.isArray(skill.tests)&&skill.tests.length&&skill.tests[0].inputs)return skill.tests[0].inputs;
    const sample={};
    for(const [name,definition] of Object.entries(skill.inputs||{})){
      const spec=definition&&typeof definition==='object'?definition:{};
      if(Object.hasOwn(spec,'example'))sample[name]=spec.example;
      else if(Object.hasOwn(spec,'default'))sample[name]=spec.default;
      else if(/schema/i.test(name))sample[name]={type:'object',properties:{name:{type:'string'}}};
      else if(/json|document|string|text|input/i.test(name))sample[name]=`{\n  "name": "Ada",\n  "active": true\n}`;
      else if(/bool/i.test(spec.type||''))sample[name]=false;
      else if(/number|integer/i.test(spec.type||''))sample[name]=1;
      else if(/array|list/i.test(spec.type||''))sample[name]=[];
      else if(/object/i.test(spec.type||''))sample[name]={};
      else sample[name]='Example input';
    }
    return Object.keys(sample).length?sample:{user_input:'Example input'};
  };
  host.innerHTML=`<section class="draft-card" data-draft="${esc(draft.id)}"><div class="draft-head"><strong>✳ SKILL PROPOSAL · REVIEW REQUIRED</strong><small>UNAPPROVED · ${esc(skill.skill_type||'instruction')}</small></div><div class="draft-body"><div class="test-context">Original task · ${esc(draft.request)}</div><div class="draft-form" style="margin-top:11px"><div class="field"><label>Reusable capability name</label><input data-field="name" value="${esc(skill.name)}"></div><div class="field"><label>Skill type</label><select data-field="skill_type"><option ${skill.skill_type==='instruction'?'selected':''}>instruction</option><option ${skill.skill_type==='tool'?'selected':''}>tool</option><option ${skill.skill_type==='python'?'selected':''}>python</option><option ${skill.skill_type==='hybrid'?'selected':''}>hybrid</option></select></div><div class="field full"><label>Reusable purpose</label><textarea data-field="description" class="mid">${esc(skill.description)}</textarea></div><div class="field full"><label>Instructions</label><textarea data-field="instructions" class="mid">${esc(skill.instructions)}</textarea></div><div class="field"><label>Inputs / defaults · JSON</label><textarea data-field="inputs" class="mid">${esc(JSON.stringify(skill.inputs||{},null,2))}</textarea></div><div class="field"><label>Expected output</label><textarea data-field="expected_output" class="mid">${esc(skill.expected_output||'')}</textarea></div><div class="field"><label>Dependencies · JSON list</label><textarea data-field="dependencies" class="mid">${esc(JSON.stringify(skill.dependencies||[],null,2))}</textarea></div><div class="field"><label>Permissions · JSON list</label><textarea data-field="permissions" class="mid">${esc(JSON.stringify(skill.permissions||[],null,2))}</textarea></div><div class="field full"><label>Tests · JSON</label><textarea data-field="tests" class="mid">${esc(JSON.stringify(skill.tests||[],null,2))}</textarea></div><div class="field"><label>Tags · comma separated</label><input data-field="tags" value="${esc((skill.tags||[]).join(', '))}"></div><div class="field"><label>Category</label><input data-field="category" value="${esc(skill.category||'general')}"></div>${skill.code!==undefined?`<div class="field full"><label>Full Python source · reviewed source only</label><textarea data-field="code" class="code">${esc(skill.code||'')}</textarea></div>`:''}</div><div class="detail-section"><h3>Test inputs · JSON</h3><textarea data-test-input class="draft-feedback">${esc(JSON.stringify(testInputs(),null,2))}</textarea></div><div class="draft-actions"><button class="button" data-action="test">Run test</button><button class="button button-warn" data-action="revise">Apply feedback & revise</button><button class="button button-danger" data-action="reject">Reject</button><button class="button button-primary" data-action="accept">Accept & run</button></div><div class="detail-section"><label class="field"><span class="eyebrow">FEEDBACK · ALWAYS AVAILABLE</span><textarea data-feedback class="draft-feedback" placeholder="What should change? Feedback triggers a visible revision and fresh review."></textarea></label></div><div data-attempts></div><div data-result></div></div></section>`;
  const card=$('[data-draft]',host); const collect=()=>{const s={...skill};$$('[data-field]',card).forEach(el=>{const k=el.dataset.field;if(['inputs','dependencies','permissions','tests'].includes(k)){try{s[k]=JSON.parse(el.value)}catch{throw Error(`${k} must be valid JSON`)}}else if(k==='tags'){s[k]=el.value.split(',').map(x=>x.trim()).filter(Boolean)}else{s[k]=el.value}});return s};
  const record=(result,source)=>{const n=document.createElement('div');n.className='detail-section';const blocked=result.status==='sandbox_unavailable';const block=(title,value)=>`<section class="test-output"><h4>${title}</h4><pre>${esc(typeof value==='string'?value:JSON.stringify(value,null,2))}</pre></section>`;n.innerHTML=`<h3>${blocked?'Test not run · sandbox unavailable':`Test attempt · ${esc(new Date().toLocaleString())}`}</h3>${result.error?`<div class="test-result fail">${esc(result.error)}</div>`:''}${result.inputs!==undefined?block('Input passed to run(inputs)',result.inputs):''}${result.return_value!==undefined?block('Function return value',result.return_value):''}${result.stdout?block('Skill print output · stdout',result.stdout):''}${result.user_stderr?block('Skill diagnostic output · stderr',result.user_stderr):''}<details class="test-details"><summary>Run details · ${result.success?'passed':blocked?'not run':'failed'}</summary><div class="test-context">Source SHA-256 · ${esc(result.source_hash||'not applicable')}\nExit status · ${esc(result.exit_code??'disabled')} · Duration · ${esc(result.duration_ms??0)} ms\nTested source snapshot follows\n${esc(source)}</div>${result.stderr?block('Execution error',result.stderr):''}</details>`;$('[data-attempts]',card).prepend(n)};
  card.addEventListener('click',async e=>{const action=e.target.closest('[data-action]')?.dataset.action;if(!action)return;const result=$('[data-result]',card);try{const current=collect();if(action==='test'){const inputs=JSON.parse($('[data-test-input]',card).value);const tested=await api(`/api/skills/drafts/${draft.id}/test`,{method:'POST',body:JSON.stringify({skill:current,inputs})});record(tested,current.code||current.instructions)}else if(action==='revise'){const feedback=$('[data-feedback]',card).value.trim();const revised=await api(`/api/skills/drafts/${draft.id}/revise`,{method:'POST',body:JSON.stringify({action:'revise',skill:current,feedback})});if(revised.draft){const old=JSON.stringify(current,null,2),next=JSON.stringify(revised.draft.skill,null,2);renderDraft(revised.draft);const diff=document.createElement('div');diff.className='detail-section';diff.innerHTML=`<h3>Visible revision · inspect the change</h3><div class="draft-diff"><pre>BEFORE\n${esc(old)}</pre><pre>AFTER\n${esc(next)}</pre></div>`;$('#draft-host').prepend(diff)}}else if(action==='reject'){const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'reject',skill:current,feedback:''})});card.remove();addMessage('assistant',reviewed.message)}else{const inputs=JSON.parse($('[data-test-input]',card).value);const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'accept',skill:current,inputs})});if(reviewed.status==='approved'){card.remove();await loadSkills();selectView('skills');await showSkill(reviewed.skill.slug);addMessage('assistant',`Approved ${reviewed.skill.name} v${reviewed.skill.version}. The skill will be available on the next chat request.`)}else if(reviewed.test){record(reviewed.test,current.code||current.instructions);result.textContent='Acceptance validation failed. The draft remains open.'}else result.textContent=reviewed.message||reviewed.status}}
    catch(error){result.innerHTML=`<div class="test-result fail">${esc(error.message)}</div>`}});
}

async function config() {
  const c=await api('/api/config');
  $('#system-state').textContent=c.llm_configured?`Model ready · ${c.model}`:'Model setup needed';
  $('#config-banner').classList.toggle('hidden',c.llm_configured);
  $('#config-copy').textContent='Add an OpenAI-compatible endpoint, key, and model to enable chat.';
  $('#provider-url').value=c.base_url;$('#provider-model').value=c.model;
}
// A model may be configured in the browser before the updated bundle or config
// response arrives. Recheck when the tab returns to the foreground.
document.addEventListener('visibilitychange',()=>{if(!document.hidden)config().catch(()=>{})});
$('#open-settings').onclick=()=>$('#settings-dialog').showModal();
$('#close-settings').onclick=()=>$('#settings-dialog').close();
$('#settings-form').addEventListener('submit',async e=>{e.preventDefault();const result=$('#settings-result');result.textContent='Saving…';try{const c=await api('/api/config/provider',{method:'PUT',body:JSON.stringify({base_url:$('#provider-url').value,api_key:$('#provider-key').value,model:$('#provider-model').value})});result.textContent=`Connected settings saved for ${c.model}.`;$('#provider-key').value='';await config();setTimeout(()=>$('#settings-dialog').close(),700)}catch(error){result.textContent=error.message}});
$('#system-state').addEventListener('click',()=>$('#settings-dialog').showModal());
config().catch(()=>{});loadSkills().catch(()=>{});
