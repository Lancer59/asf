const $ = (selector, root=document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {busy:false, activeModelId:null, models:[], reply:''};

async function api(url, options={}) {
  const response=await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.detail||data.message||`Request failed (${response.status})`);
  return data;
}
function format(text){
  return esc(text).replace(/```[\w+-]*\n([\s\S]*?)```/g,'<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .split(/\n{2,}/).map(block=>block.startsWith('<pre>')?block:`<p>${block.replace(/\n/g,'<br>')}</p>`).join('');
}
function addMessage(role,text=''){
  const box=$('#messages');$('.welcome',box)?.remove();
  const node=document.createElement('div');node.className=`message ${role}`;
  node.innerHTML=`<div class="avatar">${role==='user'?'U':'✳'}</div><div class="bubble">${role==='assistant'?`<div class="response-content markdown-body runner-content">${format(text)}</div>`:`<div class="response-content">${esc(text)}</div>`}</div>`;
  box.append(node);box.scrollTop=box.scrollHeight;return node;
}
function addWorkStatus(node){
  const status=document.createElement('div');status.className='work-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  status.innerHTML='<div class="work-bot" aria-hidden="true"><span class="bot-antenna"></span><div class="bot-head"><i></i><i></i><b></b></div><div class="bot-body"><span class="bot-arm bot-arm-left"></span><span class="bot-core">✳</span><span class="bot-arm bot-arm-right"></span></div><span class="bot-spark">✦</span></div><div class="work-copy"><strong>Getting started</strong><span>Finding the right way to help…</span><div class="work-track"><i></i></div></div>';
  node.querySelector('.bubble').append(status);return status;
}
function updateWorkStatus(status,event){
  if(!status)return;
  const title=$('strong',status),detail=$('.work-copy span',status);
  const labels={progress:event.message||'Working through your request',tool_start:event.label||'Running a step',tool_end:event.label||'Step complete'};
  title.textContent=labels[event.type]||'Working through your request';
  detail.textContent=event.type==='progress'?'Choosing the useful steps for this task.':event.type==='tool_start'?'Running the tool and collecting its result.':'Adding the result to your answer.';
  if(event.type==='error'){title.textContent='This needs another look';detail.textContent=event.message||'The request could not be completed.';status.classList.add('work-failed')}
}
function addStep(node,event){
  let panel=$('.runner-steps',node);
  if(!panel){panel=document.createElement('details');panel.className='runner-steps';panel.innerHTML='<summary>Steps</summary><ol></ol>';node.querySelector('.bubble').append(panel)}
  const list=$('ol',panel),item=document.createElement('li');
  const label=event.label||event.message||'Working on your request';
  item.innerHTML=`<strong>${esc(label)}</strong>${event.input?`<pre>${esc(JSON.stringify(event.input,null,2))}</pre>`:''}${event.output?`<pre>${esc(event.output)}</pre>`:''}`;
  list.append(item);$('summary',panel).textContent=`Steps · ${list.children.length}`;
}
function updatePicker(){const picker=$('#chat-model-picker');picker.replaceChildren();for(const model of state.models){const option=document.createElement('option');option.value=model.id;option.textContent=model.configured?model.label:`${model.label} · setup needed`;option.disabled=!model.configured;picker.append(option)}picker.value=state.activeModelId||'';picker.disabled=state.busy||state.models.filter(model=>model.configured).length<2}
async function loadConfig(){const config=await api('/api/config');state.models=config.models||[];state.activeModelId=config.active_model_id;const sandboxLabel=config.sandbox_backend==='local'?'Local Python · not isolated':config.sandbox_backend==='docker'?'Docker sandbox':`Sandbox config error · ${config.sandbox_backend}`;$('#system-state').textContent=`${config.llm_configured?`Model ready · ${config.model}`:'Model setup needed'} · ${sandboxLabel}`;$('#system-state').title=config.sandbox_warning||'';$('#config-banner').classList.toggle('hidden',config.llm_configured);updatePicker()}
$('#chat-model-picker').addEventListener('change',async event=>{const old=state.activeModelId;event.target.disabled=true;$('#model-switch-status').textContent='Switching model…';try{const result=await api('/api/config/model',{method:'PUT',body:JSON.stringify({model_id:event.target.value})});await loadConfig();$('#model-switch-status').textContent=`Using ${result.label}`}catch(error){event.target.value=old||'';$('#model-switch-status').textContent=error.message;updatePicker()}});
async function sendMessage(text){
  text=text.trim();if(!text||state.busy)return;state.busy=true;updatePicker();
  addMessage('user',text);const assistant=addMessage('assistant');const workStatus=addWorkStatus(assistant);const bubble=$('.response-content',assistant);bubble.innerHTML='';
  $('#chat-input').value='';state.reply='';let pending='';
  try{
    const response=await fetch('/api/runner/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    if(!response.ok)throw new Error((await response.json()).detail||`Request failed (${response.status})`);
    const reader=response.body.getReader(),decoder=new TextDecoder();
    const handle=event=>{if(event.type==='token'){state.reply+=event.text;bubble.innerHTML=format(state.reply)}else if(event.type==='done'){if(event.error)bubble.innerHTML=format(event.error);else if(!state.reply)bubble.innerHTML=format(event.reply||'No response.');workStatus.remove()}else if(event.type==='error'){bubble.innerHTML=format(event.message||'Request failed');updateWorkStatus(workStatus,event)}else{updateWorkStatus(workStatus,event);addStep(assistant,event)}};
    while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const frames=pending.split('\n\n');pending=frames.pop();for(const frame of frames){const line=frame.split('\n').find(item=>item.startsWith('data: '));if(line)handle(JSON.parse(line.slice(6)))}}
    pending+=decoder.decode();if(pending.trim()){const line=pending.split('\n').find(item=>item.startsWith('data: '));if(line)handle(JSON.parse(line.slice(6)))}
  }catch(error){bubble.innerHTML=format(`Request failed: ${error.message}`);updateWorkStatus(workStatus,{type:'error',message:error.message});addStep(assistant,{label:'Request failed',output:error.message})}
  finally{state.busy=false;updatePicker();$('#chat-input').focus()}
}
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();sendMessage($('#chat-input').value)});
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('#chat-form').requestSubmit()}});
document.querySelectorAll('.suggestions button').forEach(button=>button.addEventListener('click',()=>sendMessage(button.dataset.prompt)));
$('#clear-chat').addEventListener('click',async()=>{await api('/api/runner/chat/clear',{method:'POST'});$('#messages').innerHTML='<div class="welcome"><div class="welcome-glyph"><img src="/static/asf-mark.svg" alt="Agent Skill Foundry"></div><span class="eyebrow">TASK EXECUTION WORKSPACE</span><h2>What should I help you do?</h2><p>Use available tools and approved skills. The Runner checks the skill library only when a skill looks relevant.</p><div class="suggestions"><button data-prompt="Calculate 432134234234 + 2352435235"><span>∑</span> Calculate exactly</button><button data-prompt="Is this JSON valid? { &quot;name&quot;: &quot;Ada&quot;, &quot;active&quot;: true }"><span>✓</span> Validate JSON</button><button data-prompt="Search the web for the latest Python 3.14 release notes and cite sources."><span>↗</span> Search the web</button></div></div>';document.querySelectorAll('.suggestions button').forEach(button=>button.addEventListener('click',()=>sendMessage(button.dataset.prompt)))});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadConfig().catch(()=>{})});
loadConfig().catch(error=>{$('#system-state').textContent=`Configuration unavailable: ${error.message}`});
