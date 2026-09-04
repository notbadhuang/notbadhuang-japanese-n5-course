const app = document.querySelector('#app'), audio = document.querySelector('#audio');
const sid = new URLSearchParams(location.search).get('session_id');
const query = `?session_id=${encodeURIComponent(sid || '')}`;
let state, queue = Promise.resolve(), playing = false, restored = true, lastPosition = 0, detailsOpen = false, activeTicket = null;
const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
const btn = (id,text,disabled=false,cls='primary') => `<button id="${id}" class="${cls}" ${disabled?'disabled':''}>${text}</button>`;
const soundIcon = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5 6 9H3v6h3l5 4V5Z M15 8a6 6 0 0 1 0 8 M18 5a10 10 0 0 1 0 14"/></svg>';
const message = text => { const node=document.querySelector('#message');node.textContent=text;node.hidden=!text; };
async function request(path,body){const response=await fetch(path+query,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(data.message||'本地服务未响应');return data;}
async function reload(){state=await request('/api/state');render();}
function act(action,extra={},draw=true){
  const task=queue.then(async()=>{state=await request('/api/action',{action,action_token:state.action_token,...extra});if(draw)render();return state;});
  queue=task.catch(async error=>{message(error.message);try{await reload();}catch{message('连接中断；进度未确认保存。请恢复本地服务后重新连接。');}});
  return task;
}
function bind(id,handler){const n=document.getElementById(id);if(n)n.onclick=()=>Promise.resolve(handler()).catch(e=>message(e.message));}
function shell(title,label,steps,active,content,count=''){
  app.innerHTML=`<aside><div class="blue">${esc(label)}</div><h2>${esc(title)}</h2><p>${state.kind==='entry_diagnostic'?'诊断只帮助安排从哪里开始，不代表已完成任何课程。':'可以直接开始，无需先完成前置课程。原有学习进度会保留。'}</p><ol class="steps">${steps.map((s,i)=>`<li class="${i===active?'active':''}"><span>${i+1}</span>${esc(s)}</li>`).join('')}</ol></aside><section><div class="meta"><span class="blue">${esc(steps[active]||'结果与后续')}</span><span>${esc(count)}</span></div>${state.count?`<div class="track"><i style="width:${(state.index+1)/state.count*100}%"></i></div>`:''}<div class="card">${content}</div><p class="notes">进度保存在本机；下次在 WorkBuddy 中说“继续学习”。</p></section>`;
}
function audioBox(text='先听一遍，再选择答案。',asset=null){
  const a=state.audio, recover=a?.ticket && (a.status==='interrupted'||(a.status==='playing'&&restored));
  const used=state.kind==='g3_mock'&&state.audio_used&&!recover;
  return `<div class="box"><div class="row"><span id="audioStatus" class="${playing?'blue':''}">${esc(recover?'音频被中断，点击播放从已保存位置继续。':playing?'音频播放中 · 可以选择并确认答案':text)}</span><button class="primary ${playing?'playing':''}" data-play="${esc(asset||'')}" ${playing||used?'disabled':''}>${soundIcon} 播放</button></div></div>`;
}
function options(item,locked=false){return `<div class="options ${locked?'locked':''}" role="radiogroup" aria-label="答案">${item.options.map((o,i)=>`<button class="option ${state.selected===o.option_id?'selected':''}" role="radio" aria-checked="${state.selected===o.option_id}" data-option="${esc(o.option_id)}" ${locked?'disabled':''}><span>${String.fromCharCode(65+i)}</span><span>${esc(o.display_text_ja||o.label)}</span></button>`).join('')}</div>`;}
function bindOptions(){document.querySelectorAll('[data-option]').forEach(node=>node.onclick=()=>act('select',{option_id:node.dataset.option}).catch(()=>{}));}
function bindAudio(){document.querySelectorAll('[data-play]').forEach(node=>node.onclick=async()=>{
  try{
    const a=state.audio, recovering=a?.ticket&&(a.status==='interrupted'||(a.status==='playing'&&restored));
    await act(recovering?'resume_audio':'play',recovering?{ticket:a.ticket}:{asset:node.dataset.play});
    const clip=state.audio; restored=false;activeTicket=clip.ticket;
    audio.onloadedmetadata=()=>{if(clip.position)audio.currentTime=Math.min(clip.position,audio.duration);};
    audio.src='/media'+query+'&ticket='+encodeURIComponent(clip.ticket);
    playing=true;lastPosition=clip.position||0;render();await audio.play();
  }catch(error){if(error.name==='AbortError'&&!playing)return;playing=false;message(error.message);if(state.audio?.ticket)await act('audio_failed',{ticket:state.audio.ticket}).catch(()=>{});}
});}
audio.addEventListener('ended',()=>{playing=false;if(state.audio?.ticket===activeTicket)act('audio_ended',{ticket:activeTicket}).catch(()=>{});});
audio.addEventListener('error',()=>{if(playing){playing=false;act('audio_failed',{ticket:state.audio.ticket}).catch(()=>{});message('音频中断，请重试；这不会计作答错。');}});
audio.addEventListener('timeupdate',()=>{if(playing&&audio.currentTime-lastPosition>=1){lastPosition=audio.currentTime;act('audio_position',{ticket:state.audio.ticket,position:lastPosition},false).catch(()=>{});}});
document.querySelector('#exit').onclick=async()=>{try{if(playing){audio.pause();playing=false;await act('audio_position',{ticket:state.audio.ticket,position:audio.currentTime},false);await act('audio_failed',{ticket:state.audio.ticket},false);}await act('save_exit');restored=true;message('进度已保存，可以关闭此页面。下次回到 WorkBuddy 说“继续学习”。');}catch{message('保存未确认成功，请保持页面并重新连接后重试。');}};
function listening(){
  const stage=state.stage, item=state.item;
  if(stage==='completed'){completed('听力 · '+state.unit);return;}
  let content=`<h1>${esc(item.prompt_zh)}</h1>`;
  if(stage==='teaching'){
    const arrival=item.item_id==='n5-g3-listening-l01-teaching-arrival-v1';
    content+=audioBox(arrival?'先听一遍，留意到达时间。':'先听一遍，留意问题中的信息。')+`<details ${detailsOpen?'open':''}><summary>原文与讲解</summary><p class="japanese" lang="ja">${esc(item.transcript_ja)}</p><p class="muted">${esc(item.explanation_zh)}</p>${arrival?'<div class="soft row"><div><p class="muted">出发</p><strong>九時</strong></div><div><p class="muted">到达</p><strong>十時半</strong></div></div>':''}</details><div class="row">${btn('skip','跳过讲解，开始练习',false,'quiet')}${btn('next','下一个',playing)}</div>`;
  }else{
    const locked=['feedback','pending_feedback','pending_check'].includes(stage);
    content+=audioBox()+options(item,locked);
    if(stage==='feedback')content+=`<div class="success">${state.feedback.selected===state.feedback.correct_option_id?'回答正确。':'核对一下：'} ${esc(state.feedback.explanation_zh)}</div>${btn('next','继续',playing)}`;
    else if(locked)content+='<p class="muted">答案已确认，等待音频结束。</p>';
    else content+=`<div class="row"><p class="muted">确认后锁定答案。<br>${stage==='stage_check'?'阶段小测不逐题提示答案。':'音频播完后再显示反馈。'}</p>${btn('confirm','确认答案',!state.selected||!state.audio?.ticket)}</div>`;
  }
  shell(state.title,'听力 · '+state.unit,['教学示范','引导练习','阶段小测','结果与后续'],stage==='teaching'?0:['practice','feedback','pending_feedback'].includes(stage)?1:2,content,`${state.index+1} / ${state.count}`);
  const details=app.querySelector('details');if(details)details.ontoggle=()=>{detailsOpen=details.open;};
  bind('skip',()=>{audio.pause();playing=false;return act('skip_teaching');});bind('next',()=>{detailsOpen=false;return act('next');});bind('confirm',()=>act('confirm'));bindOptions();bindAudio();
}
function completed(label){const r=state.result; shell('本次活动已完成',label,['结果与后续'],0,`<h1>本次练习已完成</h1><p>已作答 ${r.evidence_summary.answered_count} 题，其中 ${r.evidence_summary.correct_count} 题答对。</p><p class="muted">结果只说明本次表现，不代表前置课程已完成，也不换算官方分数或合格结论。</p><div class="soft">回到 WorkBuddy，说“继续学习”，回收结果并安排后续。</div>`);}
function mock(){
  if(state.stage==='completed'){completed('综合模拟');return;}
  let content,active=0,count='共 28 题';
  if(state.stage==='device')content=`<h1>先确认声音，再开始模考</h1><p class="muted">这是一套项目自编练习，不是 JLPT 官方试卷。<br>结果用于了解本次表现，不换算官方分数或合格结论。</p>${audioBox('播放测试音频，调整到舒适音量。')}<label class="check"><input id="deviceCheck" type="checkbox" ${state.device_confirmed?'':'disabled'}>我已听清测试音频</label><h2>开始前请了解</h2><p>按文字·词汇、语法·阅读、听力依次进行。<br>各部分单独计时，开始后退出页面也不会暂停计时。<br>模考听力每题只播放一次；结束后查看结果。</p><div class="row"><p class="muted">${state.device_confirmed?'请确认能听清声音。':'请先播放测试音频。'}</p>${btn('startSection','开始文字·词汇',true)}</div>`;
  else if(state.stage==='between_sections'){active=state.section_index+2;content=`<h1>本部分已完成</h1><p>下一部分：${esc(state.next_title)}</p>${btn('startSection','开始下一部分')}`;}
  else{active=state.section_index+1;count=`${state.index+1} / ${state.count}`;const i=state.item;content=`<p class="muted" id="timer"></p><h1>${esc(i.prompt_zh)}</h1>${stimulus(i.stimulus||i)}${state.has_audio?audioBox('本题音频只播放一次。'):''}${options(i)}<div class="row"><p class="muted">选择后自动保存；本部分结束前不揭晓答案。</p>${btn('next',state.index+1===state.count?'提交本部分':'下一题',!state.selected||playing||state.audio?.status==='interrupted')}</div>`;}
  shell('自编 N5 综合模拟','综合模拟',['设备检查','文字 · 词汇','语法 · 阅读','听力'],active,content,count);
  const check=document.querySelector('#deviceCheck');if(check)check.onchange=()=>{document.querySelector('#startSection').disabled=!check.checked;};
  bind('startSection',()=>act('start_section'));bind('next',()=>act('next'));bindOptions();bindAudio();
}
function markTarget(text,target){
  if(!target||!text.includes(target))return esc(text);
  const at=text.indexOf(target);
  return esc(text.slice(0,at))+`<u class="target-word">${esc(target)}</u>`+esc(text.slice(at+target.length));
}
function stimulus(s){let text=s.displayed_text_ja||s.sentence_ja||s.passage_ja||s.instruction_ja||s.text_ja||'';let html=text?`<div class="soft japanese" lang="ja">${markTarget(text,s.target_ja)}</div>`:'';if(s.preannounced_goal_zh)html=`<p>${esc(s.preannounced_goal_zh)}</p>`+html;if(s.title_ja)html+=`<h2>${esc(s.title_ja)}</h2>`;if(s.rows_ja)html+=`<div class="soft">${s.rows_ja.map(r=>`<p>${esc(Array.isArray(r)?r.join(' · '):typeof r==='object'?Object.values(r).join(' · '):r)}</p>`).join('')}</div>`;if(s.visual_url)html+=`<img class="visual" src="/visual${query}" alt="本题场景示意图">`;return html;}
function diagnosticReport(p){
  const titles={initial_pass:'本次初步通过',priority_practice:'建议优先练习',needs_confirmation:'需要进一步确认',not_tested:'本次未测试'};
  const groups=Object.entries(p.groups).filter(([,rows])=>rows.length);
  return `<div class="diagnostic-evidence">${groups.map(([key,rows])=>{
    const explanation=key==='initial_pass'?(rows.every(r=>r.attempted_count===1)?`这 ${rows.length} 项各测了 1 题，均答对。`:'这些项目本次有效作答均答对，详细题量见下方。')+'初步通过不代表已经掌握。':key==='priority_practice'?'这些项目追加确认后仍有错误，建议从对应练习开始。':key==='not_tested'?'本次没有取得这些项目的有效作答，不据此判断会或不会。':'作答证据尚不充分，或受到音频等测试条件影响；暂不下结论。';
    const list=rs=>`<ul>${rs.map(r=>`<li>${esc(r.title_zh)}${key==='not_tested'?'':`<span class="evidence-count">有效作答 ${r.attempted_count} 题 · 答对 ${r.correct_count} 题${r.invalid_count?' · 存在无效作答':''}${r.blocked?' · 前置证据不足':''}</span>`}</li>`).join('')}</ul>`;
    return `<section><h3 class="${key==='initial_pass'?'blue':''}">${titles[key]} · ${rows.length} 项</h3><p class="muted">${explanation}</p>${list(rows.slice(0,4))}${rows.length>4?`<details><summary class="link">展开全部 ${rows.length} 项</summary>${list(rows)}</details>`:''}</section>`;
  }).join('')}</div><div class="soft"><strong>这是一份学习起点建议，不是完整能力鉴定。</strong><p class="muted">本次已测试 ${p.tested_count} / ${p.planned_count} 个能力项目，各项题量可能不同。<br>后续通过课程练习继续验证；不换算 JLPT 成绩或合格概率。</p></div>`;
}
function diagnosticPage(){
  const stage=state.stage, d=state.diagnostic;let content,active=0,count='';
  if(stage==='intake'){
    const fields={experience:['你学过多久日语？',[['never','完全没学过'],['days','学过几天'],['weeks','学过几周'],['months','学过几个月或更久']]],kana:['五十音目前是什么状态？',[['none','基本不认识'],['some','认识一部分'],['hiragana','平假名较熟'],['both','平假名、片假名都较熟']]],target:['离目标时间还有多久？',[['none','没有固定日期'],['30','30天以内'],['60','31～60天'],['90','61～90天或更久']]],weekly_time:['每周大概能学多久？',[['under3','3小时以内'],['3to5','3～5小时'],['5to8','5～8小时'],['over8','8小时以上']]]};
    content=`<h1>先弄清起点，再安排怎么学</h1><p class="muted">使用短分流诊断，不必答完全部题库；音频故障不计为语言错误。</p><div class="fields">${Object.entries(fields).map(([k,[title,options]])=>`<label>${title}<select name="${k}">${options.map(([v,t])=>`<option value="${v}" ${state.intake[k]===v?'selected':''}>${t}</option>`).join('')}</select></label>`).join('')}</div>${btn('begin','开始测试')}`;
  }else if(stage==='question'){
    active=1;const i=d.item,p=state.diagnostic_presentation.progress;
    count=p.total_item_count?`第 ${p.current_number} 题 / 共 ${p.total_item_count} 题`:`第 ${p.current_number} 题 · 最多 ${p.maximum_item_count} 题`;
    content=`<p class="muted diagnostic-progress">${p.total_item_count?`还剩 ${p.remaining_item_count} 题（含当前题），完成后查看起点建议。`:'根据回答可能提前结束；需要进一步确认时会显示剩余题数。'}</p><h1>${esc(i.prompt_zh)}</h1>${stimulus(i.stimulus)}`;
    if(i.stimulus.audio_url)content+=audioBox('先播放音频，再作答。',i.stimulus.audio_url);
    content+=i.options.filter(o=>o.audio_url).map(o=>`<div class="row"><span>${esc(o.label)}</span><button class="primary" data-play="${esc(o.audio_url)}" ${playing?'disabled':''}>播放</button></div>`).join('');
    if(i.response_mode==='ordered_fragments'){const map=Object.fromEntries(i.stimulus.fragments.map(f=>[f.fragment_id,f.text_ja]));content+=`<p class="muted">点击词块排列；已选词块可以移回。</p><div class="fragments soft">${state.fragment_order.map(id=>`<button class="fragment" data-remove="${esc(id)}">${esc(map[id])}</button>`).join('')||'尚未放置词块'}</div><div class="fragments">${i.stimulus.fragments.filter(f=>!state.fragment_order.includes(f.fragment_id)).map(f=>`<button class="fragment" data-add="${esc(f.fragment_id)}">${esc(f.text_ja)}</button>`).join('')}</div>`;}else content+=options(i);
    content+=btn('confirm','确认答案',playing||(i.response_mode==='ordered_fragments'?state.fragment_order.length!==i.stimulus.fragments.length:!state.selected));
    if(i.stimulus.audio_url||i.options.some(o=>o.audio_url))content+=btn('deviceFailure','音频始终无法播放，结束本次诊断',false,'quiet');
  }else{
    active=2;const r=d.result,lane=r.recommended_start_lane;let target=lane==='start_from_zero'||lane==='foundation_repair'?'F01 · 平假名：あ行到さ行':lane==='core_language_build'?'U01 · 人称、身份与国家':r.recommended_start.title;
    const p=state.diagnostic_presentation;count=`本次完成 ${p.answered_count} 题`;
    const title=lane==='receptive_integration'?'建议从阅读、听力练习开始':lane==='insufficient_evidence'?'先解决测试条件，再确定起点':lane==='mock_readiness_candidate'?'可以继续做综合模拟练习':`建议从${esc(r.recommended_start.short_title)}开始`;
    const intro=lane==='receptive_integration'?'基础文字、词汇和语法的首轮题目表现较好。<br>这次遇到的困难主要集中在阅读信息理解和听力关键点。':lane==='mock_readiness_candidate'?'首轮题目均答对，可以通过综合模拟继续了解本次表现；不等于已经达到 N5 合格水平。':esc(r.recommended_start.description);
    content=`<h1>${title}</h1><p class="muted">${intro}</p>${['start_from_zero','foundation_repair','core_language_build'].includes(lane)?`<div class="box"><h2>${esc(target)}</h2></div>`:''}${diagnosticReport(p)}<p class="muted">应用建议不会把未学过的课程记为完成，原有学习进度也会保留。</p>${state.handoff?`<div class="success">${esc(state.handoff.message_zh||'起点已应用。回到 WorkBuddy 继续。')}</div>${state.handoff.practice_url?`<a class="primary" href="${esc(state.handoff.practice_url)}">开始学习</a>`:''}`:`<div class="row">${btn('later','暂不应用，稍后继续',false,'quiet')}${lane==='insufficient_evidence'?'':btn('apply','使用这个起点')}</div>`}`;
  }
  shell('找到适合的学习起点','入口诊断',['背景问询','诊断作答','起点建议','开始学习'],active,content,count);
  bind('begin',()=>act('begin_diagnostic',{intake:Object.fromEntries([...document.querySelectorAll('select')].map(n=>[n.name,n.value]))}));
  bind('confirm',()=>act('confirm'));bind('apply',()=>{document.querySelector('#apply').disabled=true;return act('apply_diagnostic');});bind('later',()=>act('save_exit').then(()=>message('结果已保存。回到 WorkBuddy 可继续其他活动。')));bind('deviceFailure',()=>act('device_failure'));
  document.querySelectorAll('[data-add]').forEach(n=>n.onclick=()=>act('select',{fragment_order:[...state.fragment_order,n.dataset.add]}).catch(()=>{}));document.querySelectorAll('[data-remove]').forEach(n=>n.onclick=()=>act('select',{fragment_order:state.fragment_order.filter(id=>id!==n.dataset.remove)}).catch(()=>{}));bindOptions();bindAudio();
}
function render(){if(state.kind==='g3_listening')listening();else if(state.kind==='g3_mock')mock();else diagnosticPage();}
setInterval(()=>{if(state?.deadline_epoch){const n=document.querySelector('#timer'),s=Math.max(0,Math.ceil(state.deadline_epoch-Date.now()/1000));if(n)n.textContent=`本部分剩余 ${Math.floor(s/60)} 分 ${s%60} 秒`;if(!s)reload().catch(()=>message('计时已到，等待本地服务确认。'));}},1000);
reload().catch(error=>{message(error.message);app.innerHTML='<section><h1>未能连接学习会话</h1><p>请回到 WorkBuddy 恢复服务后刷新此页。</p></section>';});
