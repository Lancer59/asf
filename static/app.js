const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {view:'chat', skills:[], selected:null, busy:false, lastRequest:'', providerProfiles:{}, models:[], activeModelId:null, creatingModelProfile:false};

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
  $('#page-title').textContent=view==='chat'?'Skill Foundry':'Skills';
  $('#page-eyebrow').textContent=view==='chat'?'SKILL MANAGEMENT / FOUNDRY':'CAPABILITY MANAGEMENT / LIBRARY';
  $('#model-picker-wrap').classList.toggle('hidden',view!=='chat');
  $('#model-switch-status').classList.toggle('hidden',view!=='chat');
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
    let input=event.input;
    if(event.name==='propose_reusable_skill'&&input)input={task:input.task,proposal:'Complete skill draft prepared'};
    item.innerHTML=`<strong>${esc(label)}</strong>${input&&event.type==='tool_start'?`<pre>${esc(JSON.stringify(input,null,2))}</pre>`:''}${event.output?`<pre>${esc(event.output)}</pre>`:''}`;
    list.append(item);
  }
  $('.activity-title',panel).textContent=`Steps · ${list.children.length}`;
  $('.activity-state',panel).textContent=open?'In progress…':'';
}

function addWorkStatus(messageNode){
  const node=document.createElement('div');node.className='work-status';node.setAttribute('role','status');node.setAttribute('aria-live','polite');
  node.innerHTML='<div class="work-bot" aria-hidden="true"><span class="bot-antenna"></span><div class="bot-head"><i></i><i></i><b></b></div><div class="bot-body"><span class="bot-arm bot-arm-left"></span><span class="bot-core">✳</span><span class="bot-arm bot-arm-right"></span></div><span class="bot-spark">✦</span></div><div class="work-copy"><strong>Getting started</strong><span>Preparing your workspace…</span><div class="work-track"><i></i></div></div>';
  messageNode.querySelector('.bubble').append(node);return node;
}

function updateWorkStatus(status,event){
  if(!status)return;
  const title=$('strong',status),detail=$('.work-copy span',status);
  let heading='Working through your request',copy='I’ll keep the activity and result together here.';
  if(event.type==='progress'){
    heading=event.message||heading;
    if(/thinking/i.test(heading))copy='Understanding what this capability needs to do.';
  }else if(event.type==='tool_start'){
    if(event.name==='propose_reusable_skill'){heading='Putting your skill draft together';copy='Organizing its instructions, inputs, tests, and code.'}
    else if(event.name==='list_approved_skills'){heading='Checking the skills library';copy='Looking for an existing capability that fits.'}
    else if(event.name?.includes('search')){heading='Researching the requirements';copy='Gathering current references for the draft.'}
    else {heading=event.label||heading;copy='Running this step and collecting its result.'}
  }else if(event.type==='critique_start'){
    heading='Checking the draft';copy='Running its first test and reviewing what the result shows.'
  }else if(event.type==='tool_end'&&event.name==='propose_reusable_skill'){
    if((event.output||'').startsWith('Draft created:')||(event.output||'').startsWith('Draft already created:')){heading='Your draft is ready';copy='Review the proposal below and adjust anything you need.';status.classList.add('work-ready')}
    else{heading='The draft needs another pass';copy='The proposal did not pass validation, so no draft was created.';status.classList.add('work-failed')}
  }else if(event.type==='error'){
    heading='This needs another look';copy=event.message||'The request could not be completed.';status.classList.add('work-failed');
  }
  title.textContent=heading;detail.textContent=copy;
}

function showDraftProgressFields(fields){
  const host=$('#draft-host'),list=$('[data-progress-fields]',host);if(!list)return;
  const labels={name:'Name',description:'Purpose',skill_type:'Skill type',instructions:'Instructions',inputs:'Input contract',expected_output:'Expected output',tests:'Test cases',code:'Python source',tools:'Tools',dependencies:'Dependencies',permissions:'Permissions',tags:'Tags',category:'Category'};
  for(const [key,value] of Object.entries(fields||{})){
    if($(`[data-progress-field="${CSS.escape(key)}"]`,list))continue;
    const item=document.createElement('details');item.className='progress-field';item.dataset.progressField=key;
    const summary=document.createElement('summary');summary.textContent=labels[key]||key;
    const preview=document.createElement('pre');const rendered=typeof value==='string'?value:JSON.stringify(value,null,2);preview.textContent=rendered.length>1200?rendered.slice(0,1200)+'…':rendered;
    item.append(summary,preview);list.append(item);
  }
}

function setDraftProgressStep(index){
  $$('.progress-steps span',$('#draft-host')).forEach((step,i)=>{step.classList.toggle('current',i===index);step.classList.toggle('complete',i<index)});
}

async function sendMessage(text) {
  text=text.trim(); if(!text||state.busy)return;
  state.busy=true; state.lastRequest=text;
  const startingSkill=/\b(skill|capability)\b/i.test(text)&&/\b(create|make|build|want|need|generate)\b/i.test(text);
  if(startingSkill&&!$('#draft-host').querySelector('[data-draft]')){
    $('#draft-host').classList.add('is-working');
    $('#draft-host').innerHTML='<div class="draft-progress"><span class="welcome-glyph">✳</span><span class="eyebrow">NEW SKILL SESSION</span><h2>Preparing your skill</h2><p>Reviewing your request and shaping a draft. If one detail is essential, the agent will ask once before preparing it.</p><div class="progress-steps"><span class="current">Understanding the request</span><span>Preparing the proposal</span><span>Running the first review</span></div><div class="progress-fields" data-progress-fields></div></div>';
  }
  updateModelPickerState();
  addMessage('user',text);
  const assistant=addMessage('assistant','');
  const workStatus=addWorkStatus(assistant);
  const bubble=$('.response-content',assistant);bubble.innerHTML='';
  const events=[];let pending='';
  $('#chat-input').value='';
  try {
    const response=await fetch('/api/foundry/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    if(!response.ok) throw new Error((await response.json()).detail||`Request failed (${response.status})`);
    const reader=response.body.getReader(),decoder=new TextDecoder();
    const handleEvent=event=>{
      if(event.type==='token'){
        state.liveReply=(state.liveReply||'')+event.text;bubble.innerHTML=formatMessage(state.liveReply);
      }else if(event.type==='draft_progress'){
        setDraftProgressStep(1);showDraftProgressFields(event.fields);
      }else if(event.type==='done'){
        if(!state.liveReply)bubble.innerHTML=formatMessage(event.reply||'No response.');if(event.error)bubble.innerHTML=formatMessage(event.error);state.liveReply='';workStatus.remove();const status=$('.activity-state',assistant);if(status)status.textContent='';
        if(event.session_blocked){$('#draft-host').classList.remove('is-working');if(!$('#draft-host').querySelector('[data-draft]')){$('#draft-host').classList.add('is-locked');$('#draft-host').innerHTML=`<div class="draft-empty"><span class="placeholder-icon">✓</span><span class="eyebrow">SKILL SESSION IN PROGRESS</span><h2>Clear to start another skill</h2><p>${esc(event.reply)}</p></div>`}}
      }else if(event.type==='draft'){
        renderDraft(event.draft);updateWorkStatus(workStatus,{type:'tool_end',name:'propose_reusable_skill'});
      }else if(event.type==='critique_start'){
        setDraftProgressStep(2);updateWorkStatus(workStatus,event);showDraftCritique({draft_id:event.draft_id,status:'running'});
      }else if(event.type==='critique'){
        showDraftCritique(event);events.push({type:'status',label:'First test and critique complete'});addActivity(assistant,[{type:'status',label:'First test and critique complete'}]);
      }else if(event.type==='skill_offer'){
        showSkillOffer(assistant,event.request);
      }else{
        if(event.type==='tool_start'&&event.name==='propose_reusable_skill')setDraftProgressStep(1);
        events.push(event);updateWorkStatus(workStatus,event);addActivity(assistant,[event]);
      }
    };
    while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const frames=pending.split('\n\n');pending=frames.pop();for(const frame of frames){const line=frame.split('\n').find(l=>l.startsWith('data: '));if(line)handleEvent(JSON.parse(line.slice(6)))}}
    pending+=decoder.decode();if(pending.trim()){const line=pending.split('\n').find(l=>l.startsWith('data: '));if(line)handleEvent(JSON.parse(line.slice(6)))}
    if(!events.length)addActivity(assistant,[{type:'status',message:'Request completed'}]);
  } catch(error) {bubble.innerHTML=formatMessage(`Request failed: ${error.message}`);updateWorkStatus(workStatus,{type:'error',message:error.message});addActivity(assistant,[{type:'error',message:error.message}])}
  finally {state.busy=false;updateModelPickerState();$('#chat-input').focus()}
}

function showSkillOffer(messageNode,request){
  const offer=document.createElement('div');offer.className='skill-offer';
  offer.innerHTML=`<span>Want me to turn this into a reusable skill?</span><button class="button button-primary">Create skill proposal</button>`;
  $('.response-content',messageNode).append(offer);
  $('button',offer).onclick=async()=>{const button=$('button',offer);button.disabled=true;button.textContent='Preparing proposal…';try{const draft=await api('/api/skills/drafts',{method:'POST',body:JSON.stringify({message:request})});renderDraft(draft);offer.remove();showDraftCritique({draft_id:draft.id,status:'running'});try{showDraftCritique({...await api(`/api/skills/drafts/${draft.id}/critique`,{method:'POST'}),draft_id:draft.id})}catch(error){showDraftCritique({draft_id:draft.id,test:{status:'not_run',error:error.message},summary:'The first-pass critique could not be completed.'})}}catch(error){button.disabled=false;button.textContent='Try again';offer.insertAdjacentHTML('beforeend',`<small>${esc(error.message)}</small>`)}};
}

$('#chat-form').addEventListener('submit',e=>{e.preventDefault();sendMessage($('#chat-input').value)});
$('#chat-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#chat-form').requestSubmit()}});
$$('.suggestions button').forEach(button=>button.addEventListener('click',()=>sendMessage(button.dataset.prompt)));
$$('.nav-item[data-view]').forEach(button=>button.addEventListener('click',()=>selectView(button.dataset.view)));
$('#clear-chat').addEventListener('click',async()=>{await api('/api/foundry/chat/clear',{method:'POST'});$('#messages').innerHTML='<div class="welcome"><div class="welcome-glyph"><img src="/static/asf-mark.svg" alt="Agent Skill Foundry"></div><span class="eyebrow">SKILL MANAGEMENT WORKSPACE</span><h2>What capability should we build?</h2><p>Create a reusable skill, test and revise a draft, or manage approved capabilities. For one-off tasks, open Skill Runner.</p><div class="suggestions"><button data-prompt="Create a Python calculator skill with add, subtract, multiply, and divide."><span>✳</span> Build a calculator skill</button><button data-prompt="Show me the approved skills in the library."><span>⧉</span> Browse approved skills</button><a class="button" href="/runner">Open Skill Runner</a></div></div>';$$('.suggestions button').forEach(b=>b.onclick=()=>sendMessage(b.dataset.prompt));$('#draft-host').classList.remove('is-working','is-locked');$('#draft-host').innerHTML='<div class="draft-empty"><span class="placeholder-icon">✳</span><span class="eyebrow">SKILL WORKSPACE</span><h2>Your skill draft will appear here</h2><p>Describe a reusable capability. The draft, first-pass review, tests, and revisions stay together in this workspace.</p></div>'});

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
  if(s.status==='approved'){
    const button=document.createElement('button');button.type='button';button.className='button button-primary';button.id='edit-with-agent';button.textContent='Edit with agent';
    $('#edit-current')?.after(button);button.addEventListener('click',()=>agentEditSkill(s));
  }
}

function editSkill(s){
  const d=s.content||{}; $('#skill-detail').insertAdjacentHTML('beforeend',`<div class="detail-section" id="revision-editor"><h3>Propose new version · v${s.version+1}</h3><div class="draft-form"><div class="field"><label for="rev-name">Name</label><input id="rev-name" value="${esc(d.name)}"></div><div class="field"><label for="rev-type">Skill type</label><select id="rev-type"><option ${d.skill_type==='instruction'?'selected':''}>instruction</option><option ${d.skill_type==='python'?'selected':''}>python</option><option ${d.skill_type==='hybrid'?'selected':''}>hybrid</option><option ${d.skill_type==='tool'?'selected':''}>tool</option></select></div><div class="field full"><label for="rev-description">Purpose</label><textarea id="rev-description" class="mid">${esc(d.description)}</textarea></div><div class="field full"><label for="rev-instructions">Instructions</label><textarea id="rev-instructions" class="mid">${esc(d.instructions)}</textarea></div>${d.code?`<div class="field full"><label for="rev-code">Python source</label><textarea id="rev-code" class="code">${esc(d.code)}</textarea></div>`:''}<div class="field full"><label for="rev-feedback">Change note</label><textarea id="rev-feedback" class="mid" placeholder="What changed in this revision?"></textarea></div></div><div class="draft-actions"><button class="button" id="cancel-revision">Cancel</button><button class="button button-primary" id="save-revision">Validate & save version</button></div><div id="rev-result"></div></div>`);
  $('#cancel-revision').onclick=()=>$('#revision-editor').remove();
  $('#save-revision').onclick=async()=>{const content={name:$('#rev-name').value,description:$('#rev-description').value,skill_type:$('#rev-type').value,instructions:$('#rev-instructions').value};if($('#rev-code'))content.code=$('#rev-code').value;try{const result=await api(`/api/skills/${s.slug}`,{method:'PATCH',body:JSON.stringify({content})});if(result.status==='test_failed')$('#rev-result').innerHTML=`<div class="test-result fail">${esc(JSON.stringify(result.test,null,2))}</div>`;else showSkill(s.slug)}catch(e){$('#rev-result').innerHTML=`<div class="test-result fail">${esc(e.message)}</div>`}};
}

function agentEditSkill(s){
  $('#agent-edit-panel')?.remove();
  const panel=document.createElement('section');panel.id='agent-edit-panel';panel.className='detail-section agent-edit-panel';
  panel.innerHTML=`<div class="agent-edit-heading"><div><span class="eyebrow">AGENT REVISION · NOT SAVED</span><h3>Edit ${esc(s.name)} with the agent</h3></div><button type="button" class="button" data-close-agent-edit>Close</button></div><div class="agent-edit-thread" data-agent-thread aria-live="polite"><div class="agent-chat-message assistant">Describe the change you want. I’ll propose an updated version for review; nothing is saved until you apply it.</div></div><form class="agent-edit-compose" data-agent-form><label class="sr-only" for="agent-edit-input">Describe the skill change</label><textarea id="agent-edit-input" rows="3" placeholder="For example: handle empty responses and return a useful error…" required></textarea><button class="button button-primary" type="submit">Ask agent</button></form><div class="agent-edit-preview" data-agent-preview></div><div class="agent-edit-result" data-agent-result></div>`;
  $('#skill-detail').append(panel);
  let current=JSON.parse(JSON.stringify(s.content||{}));
  let hasProposal=false;
  const thread=$('[data-agent-thread]',panel),preview=$('[data-agent-preview]',panel),result=$('[data-agent-result]',panel);
  const addChat=(role,text)=>{const node=document.createElement('div');node.className=`agent-chat-message ${role}`;node.textContent=text;thread.append(node);thread.scrollTop=thread.scrollHeight;return node};
  $('[data-close-agent-edit]',panel).onclick=()=>panel.remove();
  $('[data-agent-form]',panel).addEventListener('submit',async event=>{
    event.preventDefault();const input=$('#agent-edit-input',panel),feedback=input.value.trim();if(!feedback)return;
    addChat('user',feedback);input.value='';const pending=addChat('assistant','Preparing a proposed revision…');
    const send=$('button[type="submit"]',event.currentTarget);send.disabled=true;result.textContent='';
    try{
      const response=await api(`/api/skills/${encodeURIComponent(s.slug)}/agent-revision`,{method:'POST',body:JSON.stringify({feedback,current_skill:hasProposal?current:null})});
      current=response.skill;hasProposal=true;pending.textContent='Proposed changes are ready below. Review them before saving.';
      const changes=Object.entries(response.diff||{});
      preview.innerHTML=`<div class="agent-edit-preview-head"><div><span class="eyebrow">PROPOSED VERSION · v${s.version+1}</span><strong>${changes.length} changed ${changes.length===1?'field':'fields'}</strong></div><span class="status-pill">Not saved</span></div>${changes.length?`<div class="agent-edit-diff">${changes.map(([key,value])=>`<details><summary>${esc(key.replaceAll('_',' '))}</summary><div class="agent-diff-columns"><section><small>Current</small><pre>${esc(typeof value.before==='string'?value.before:JSON.stringify(value.before,null,2)??'—')}</pre></section><section><small>Proposed</small><pre>${esc(typeof value.after==='string'?value.after:JSON.stringify(value.after,null,2)??'—')}</pre></section></div></details>`).join('')}</div>`:'<p class="subtle">No content fields changed. Add more detail to your request.</p>'}<label class="field agent-test-input"><span>Inputs used to validate this version · JSON</span><textarea data-agent-inputs class="draft-feedback">${esc(JSON.stringify(current.tests?.[0]?.inputs||{},null,2))}</textarea></label><button type="button" class="button button-primary" data-apply-revision ${changes.length?'':'disabled'}>${['python','hybrid'].includes(current.skill_type)?'Validate & save new version':'Save new version'}</button>`;
      $('[data-apply-revision]',preview)?.addEventListener('click',async buttonEvent=>{
        const apply=buttonEvent.currentTarget;apply.disabled=true;apply.textContent='Validating and saving…';result.textContent='';
        try{
          const inputs=JSON.parse($('[data-agent-inputs]',preview).value);
          const saved=await api(`/api/skills/${encodeURIComponent(s.slug)}`,{method:'PATCH',body:JSON.stringify({content:current,inputs})});
          if(saved.status==='test_failed'){
            result.innerHTML=`<div class="test-result fail">The revised code did not pass, so the saved skill was left unchanged.<pre>${esc(JSON.stringify(saved.test,null,2))}</pre></div>`;
            apply.disabled=false;apply.textContent='Retry validation & save';return;
          }
          addChat('assistant',`Saved as version ${saved.skill.version}.`);await loadSkills();await showSkill(s.slug);
        }catch(error){result.innerHTML=`<div class="test-result fail">${esc(error.message)}</div>`;apply.disabled=false;apply.textContent='Retry';}
      });
    }catch(error){pending.textContent=`Could not prepare a revision: ${error.message}`}
    finally{send.disabled=false;input.focus()}
  });
  panel.scrollIntoView({behavior:'smooth',block:'start'});
}

$('#skill-search').addEventListener('input',()=>loadSkills());$('#status-filter').addEventListener('change',()=>loadSkills());
$('#new-skill').addEventListener('click',()=>{selectView('chat');$('#chat-input').value='Help me create a reusable skill for: ';$('#chat-input').focus()});

function renderDraft(draft) {
  const host=$('#draft-host'); host.classList.remove('is-working','is-locked'); const skill=draft.skill||{};
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
  if(draft.scope_note){const note=document.createElement('div');note.className='scope-note';note.innerHTML='<strong>Scoped MVP</strong>';note.append(document.createTextNode(` ${draft.scope_note}`));$('.draft-body',host).insertBefore(note,$('.draft-form',host))}
  const card=$('[data-draft]',host); const collect=()=>{const s={...skill};$$('[data-field]',card).forEach(el=>{const k=el.dataset.field;if(['inputs','dependencies','permissions','tests'].includes(k)){try{s[k]=JSON.parse(el.value)}catch{throw Error(`${k} must be valid JSON`)}}else if(k==='tags'){s[k]=el.value.split(',').map(x=>x.trim()).filter(Boolean)}else{s[k]=el.value}});return s};
  const record=(result,source)=>{const n=document.createElement('div');n.className='detail-section';const blocked=result.status==='sandbox_unavailable';const block=(title,value)=>`<section class="test-output"><h4>${title}</h4><pre>${esc(typeof value==='string'?value:JSON.stringify(value,null,2))}</pre></section>`;n.innerHTML=`<h3>${blocked?'Test not run · sandbox unavailable':`Test attempt · ${esc(new Date().toLocaleString())}`}</h3>${result.error?`<div class="test-result fail">${esc(result.error)}</div>`:''}${result.inputs!==undefined?block('Input passed to run(inputs)',result.inputs):''}${result.return_value!==undefined?block('Function return value',result.return_value):''}${result.stdout?block('Skill print output · stdout',result.stdout):''}${result.user_stderr?block('Skill diagnostic output · stderr',result.user_stderr):''}<details class="test-details"><summary>Run details · ${result.success?'passed':blocked?'not run':'failed'}</summary><div class="test-context">Source SHA-256 · ${esc(result.source_hash||'not applicable')}\nExit status · ${esc(result.exit_code??'disabled')} · Duration · ${esc(result.duration_ms??0)} ms\nTested source snapshot follows\n${esc(source)}</div>${result.stderr?block('Execution error',result.stderr):''}</details>`;$('[data-attempts]',card).prepend(n)};
  card.addEventListener('click',async e=>{const action=e.target.closest('[data-action]')?.dataset.action;if(!action)return;const result=$('[data-result]',card);try{const current=collect();if(action==='test'){const inputs=JSON.parse($('[data-test-input]',card).value);const tested=await api(`/api/skills/drafts/${draft.id}/test`,{method:'POST',body:JSON.stringify({skill:current,inputs})});record(tested,current.code||current.instructions)}else if(action==='revise'){const feedback=$('[data-feedback]',card).value.trim();const revised=await api(`/api/skills/drafts/${draft.id}/revise`,{method:'POST',body:JSON.stringify({action:'revise',skill:current,feedback})});if(revised.draft){const old=JSON.stringify(current,null,2),next=JSON.stringify(revised.draft.skill,null,2);renderDraft(revised.draft);const diff=document.createElement('div');diff.className='detail-section';diff.innerHTML=`<h3>Visible revision · inspect the change</h3><div class="draft-diff"><pre>BEFORE\n${esc(old)}</pre><pre>AFTER\n${esc(next)}</pre></div>`;$('#draft-host').prepend(diff)}}else if(action==='reject'){const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'reject',skill:current,feedback:''})});card.remove();addMessage('assistant',reviewed.message)}else{const inputs=JSON.parse($('[data-test-input]',card).value);const reviewed=await api(`/api/skills/drafts/${draft.id}/review`,{method:'POST',body:JSON.stringify({action:'accept',skill:current,inputs})});if(reviewed.status==='approved'){card.remove();await loadSkills();selectView('skills');await showSkill(reviewed.skill.slug);addMessage('assistant',`Approved ${reviewed.skill.name} v${reviewed.skill.version}. The skill will be available on the next chat request.`)}else if(reviewed.test){record(reviewed.test,current.code||current.instructions);result.textContent='Acceptance validation failed. The draft remains open.'}else result.textContent=reviewed.message||reviewed.status}}
    catch(error){result.innerHTML=`<div class="test-result fail">${esc(error.message)}</div>`}});
}

function showDraftCritique(payload){
  const card=$$('[data-draft]').find(item=>item.dataset.draft===payload.draft_id);if(!card)return;
  let panel=$('.draft-critique',card);if(!panel){panel=document.createElement('section');panel.className='draft-critique';panel.setAttribute('aria-live','polite');card.querySelector('.draft-body').prepend(panel)}
  if(payload.status==='running'){panel.innerHTML='<strong>First-pass check</strong><p>The agent is running the first test and reviewing its result…</p>';return}
  const test=payload.test||{},status=test.status||'unknown';
  const label=status==='passed'?'Test passed':status==='sandbox_unavailable'?'Test not run · sandbox unavailable':status==='not_applicable'?'Code test not applicable':status==='not_run'?'Test not run':'Test failed';
  const detail=test.return_value!==undefined?JSON.stringify(test.return_value,null,2):test.error||test.stderr||test.stdout||'No test output was returned.';
  panel.innerHTML=`<strong>Agent’s first-pass critique · ${esc(label)}</strong><div class="markdown-body">${formatMessage(payload.summary||'The agent could not summarize the first test.')}</div><details><summary>First test result</summary><pre>${esc(detail)}</pre></details>`;
}

async function config() {
  const c=await api('/api/config');
  const sandboxLabel=c.sandbox_backend==='local'?'Local Python · not isolated':c.sandbox_backend==='docker'?'Docker sandbox':`Sandbox config error · ${c.sandbox_backend}`;
  $('#system-state').textContent=`${c.llm_configured?`Model ready · ${c.provider_type==='azure_openai'?'Azure demo · ':''}${c.model}`:'Model setup needed'} · ${sandboxLabel}`;
  $('#system-state').title=c.sandbox_warning||'';
  $('#config-banner').classList.toggle('hidden',c.llm_configured);
  $('#config-copy').textContent='Choose an OpenAI-compatible endpoint or Azure OpenAI, then provide its key and model deployment.';
  state.providerProfiles={openai_compatible:c.openai_compatible,azure_openai:c.azure_openai};
  state.models=c.models||[];
  state.activeModelId=c.active_model_id;
  renderModelPicker();
  populateSettingsProfiles(c.active_model_id);
}
function renderModelPicker() {
  const picker=$('#chat-model-picker');
  picker.replaceChildren();
  for(const model of state.models){
    const option=document.createElement('option');
    option.value=model.id;
    option.textContent=model.configured?model.label:`${model.label} · configure key`;
    option.disabled=!model.configured;
    picker.append(option);
  }
  if(!state.models.length){picker.add(new Option('No model profiles',''));}
  picker.value=state.activeModelId||'';
  updateModelPickerState();
}
function updateModelPickerState() {
  const picker=$('#chat-model-picker');
  if(picker)picker.disabled=state.busy||state.models.filter(model=>model.configured).length<2;
}
$('#chat-model-picker').addEventListener('change',async event=>{
  const modelId=event.target.value;
  const previous=state.activeModelId;
  $('#model-switch-status').textContent='Switching model…';
  event.target.disabled=true;
  try{
    const selected=await api('/api/config/model',{method:'PUT',body:JSON.stringify({model_id:modelId})});
    await config();
    $('#model-switch-status').textContent=`Using ${selected.label}`;
  }catch(error){
    event.target.value=previous||'';
    $('#model-switch-status').textContent=`Model switch failed: ${error.message}`;
  }finally{updateModelPickerState()}
});
function populateSettingsProfiles(selectedId) {
  const target=$('#profile-target');
  target.replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a profile';target.append(placeholder);
  for(const profile of state.models){
    const option=document.createElement('option');
    option.value=profile.id;
    option.textContent=`${profile.label} · ${profile.provider_type==='azure_openai'?'Azure':'OpenAI-compatible'}${profile.configured?'':' · setup needed'}`;
    target.append(option);
  }
  if(selectedId&&state.models.some(profile=>profile.id===selectedId))target.value=selectedId;
  showProviderProfile(target.value);
}
function showProviderProfile(profileId) {
  const profile=state.models.find(item=>item.id===profileId)||{};
  const providerType=profile.provider_type||'openai_compatible';
  const azure=providerType==='azure_openai';
  $('#provider-url-label').textContent=azure?'Azure resource endpoint':'Base URL';
  $('#provider-url').placeholder=azure?'https://your-resource.openai.azure.com/':'https://api.openai.com/v1';
  $('#provider-key-label').textContent=azure?'Azure OpenAI API key':'API key';
  $('#provider-model-label').textContent=azure?'Azure deployment name':'Model name';
  $('#provider-model').placeholder=azure?'gpt-5.3-chat':'gpt-4o-mini';
  $('#provider-type').value=providerType;
  $('#provider-id').value=profile.id||'';
  $('#provider-id').disabled=!state.creatingModelProfile;
  $('#provider-label').value=profile.label||'';
  $('#provider-url').value=profile.base_url||(state.providerProfiles[providerType]||{}).base_url||'';
  $('#provider-model').value=profile.model||(state.providerProfiles[providerType]||{}).model||'';
  $('#provider-key-env').value=profile.api_key_env||(azure?'AZURE_OPENAI_API_KEY':'OPENAI_API_KEY');
  $('#provider-api-version').value=profile.api_version||'2024-08-01-preview';
  $('#azure-version-field').classList.toggle('hidden',!azure);
  $('#azure-note').classList.toggle('hidden',!azure);
}
function addModelProfile() {
  state.creatingModelProfile=true;
  const target=$('#profile-target');
  target.value='';
  $('#provider-id').disabled=false;
  $('#provider-id').value='';
  $('#provider-label').value='';
  $('#provider-type').value='openai_compatible';
  $('#provider-url').value='';
  $('#provider-url').placeholder='https://provider.example/v1';
  $('#provider-key').value='';
  $('#provider-key-env').value='NEW_MODEL_API_KEY';
  $('#provider-model').value='';
  $('#provider-api-version').value='v1';
  $('#azure-version-field').classList.add('hidden');
  $('#azure-note').classList.add('hidden');
  $('#settings-result').textContent='Enter a unique profile ID, endpoint, model/deployment, and API key variable.';
}
// A model may be configured in the browser before the updated bundle or config
// response arrives. Recheck when the tab returns to the foreground.
document.addEventListener('visibilitychange',()=>{if(!document.hidden)config().catch(()=>{})});
$('#open-settings').onclick=async()=>{state.creatingModelProfile=false;await config();$('#settings-dialog').showModal()};
$('#close-settings').onclick=()=>$('#settings-dialog').close();
$('#profile-target').addEventListener('change',event=>{state.creatingModelProfile=false;showProviderProfile(event.target.value)});
$('#add-model-profile').addEventListener('click',addModelProfile);
$('#provider-type').addEventListener('change',event=>{
  const azure=event.target.value==='azure_openai';
  $('#provider-url-label').textContent=azure?'Azure resource endpoint':'Base URL';
  $('#provider-key-label').textContent=azure?'Azure OpenAI API key':'API key';
  $('#provider-model-label').textContent=azure?'Azure deployment name':'Model name';
  $('#azure-version-field').classList.toggle('hidden',!azure);
  $('#azure-note').classList.toggle('hidden',!azure);
  if(state.creatingModelProfile||!$('#provider-key-env').value||['OPENAI_API_KEY','AZURE_OPENAI_API_KEY'].includes($('#provider-key-env').value))$('#provider-key-env').value=azure?'AZURE_OPENAI_API_KEY':'OPENAI_API_KEY';
});
$('#settings-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const result=$('#settings-result');result.textContent='Saving profile…';
  const modelId=$('#profile-target').value;
  const selected=state.models.find(profile=>profile.id===modelId);
  try{
    let c;
    if(state.creatingModelProfile||selected&&!selected.legacy){
      const creating=state.creatingModelProfile;
      c=await api('/api/config/model-profile',{method:creating?'POST':'PUT',body:JSON.stringify({
        model_id:creating?$('#provider-id').value:modelId,create:creating,label:$('#provider-label').value,provider_type:$('#provider-type').value,
        base_url:$('#provider-url').value,api_key:$('#provider-key').value,api_key_env:$('#provider-key-env').value,
        model:$('#provider-model').value,api_version:$('#provider-api-version').value
      })});
    }else{
      c=await api('/api/config/provider',{method:'PUT',body:JSON.stringify({
        provider_type:$('#provider-type').value,base_url:$('#provider-url').value,api_key:$('#provider-key').value,
        model:$('#provider-model').value,api_version:$('#provider-api-version').value
      })});
    }
    result.textContent=`Saved ${c.label||c.model} profile. It will be used for the next chat request.`;
    state.creatingModelProfile=false;$('#provider-key').value='';await config();setTimeout(()=>$('#settings-dialog').close(),900);
  }catch(error){result.textContent=error.message}
});
$('#system-state').addEventListener('click',()=>$('#settings-dialog').showModal());
config().catch(()=>{});loadSkills().catch(()=>{});
