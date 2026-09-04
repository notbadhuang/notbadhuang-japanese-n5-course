/* Shared, opt-in browser continuation. The service owns progress and planning. */
window.N5Continuity = (() => {
  let current, adapter, token, proposal, syncing=false, synced=false, helpOpen=false, helpText='';
  const sid=new URLSearchParams(location.search).get('session_id');
  const endpoint='/api/continuity?session_id='+encodeURIComponent(sid||'');
  const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  const app=()=>document.getElementById('app');
  const label=()=>[current?.work_unit?.unit_code||current?.unit_code||current?.unit||'',current?.work_unit?.title_zh||current?.content?.display_title_zh||current?.title||'学习活动'].filter(Boolean).join(' · ');
  const button=(id,text,quiet=false)=>`<button type="button" id="${id}" class="n5c-button${quiet?' n5c-quiet':''}">${text}</button>`;
  async function api(body){
    if(!token){const r=await fetch(endpoint);if(!r.ok)throw Error('会话尚未准备好');token=(await r.json()).token;}
    const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-N5-Continuation':token},body:JSON.stringify(body)});
    if(!r.ok){token=null;throw Error('暂时无法同步或打开下一项，请重试。');}
    return r.json();
  }
  function completed(error=''){
    const a=app();a.classList.add('n5c-completed');
    const summary=current?.result?.evidence_summary||current?.summary||{};
    const count=summary.answered_count??current?.practice_count;
    const teach=current?.teaching_count;
    const next=proposal;
    const title=synced?'这一单元学完了':error?'答题已结束，记录还没同步好':'正在同步学习记录…';
    let action='';
    if(error&&!synced)action=button('n5c-retry','重试同步');
    else if(synced&&next?.status==='selection')action=next.sessions.map((s,i)=>button('n5c-choice-'+i,esc(s.label))).join('');
    else if(synced&&next?.status==='ready')action=button('n5c-next',error?'重新打开':'继续学习');
    if(synced)action+=button('n5c-stop','今天先到这里',true);
    a.innerHTML=`<div class="n5c-main"><p class="n5c-blue">${esc(label())}</p><h1>${title}</h1><p class="n5c-muted">${synced?'本次学习记录已保存到本机。你可以接着学，也可以先休息。':'暂时不进入下一单元，已提交的答案会保留。'}</p><div class="n5c-summary">${teach!=null?`<div><span>教学示范</span><strong>${teach} 项已完成</strong></div>`:''}${count!=null?`<div><span>本单元练习</span><strong>${count} 题已作答</strong></div>`:''}<div><span>保存状态</span><strong class="${synced?'n5c-green':'n5c-muted'}">${synced?'已保存':error?'等待同步':'正在同步'}</strong></div></div><div class="n5c-next${error?' n5c-error':''}"><p class="n5c-blue">${synced?'接下来建议学习':'学习记录'}</p><h2>${esc(synced?(next?.status==='selection'?'继续未完成的活动':next?.label):'先同步本次学习记录')}</h2><p class="n5c-muted">${esc(error||next?.message||'按当前路线、实际进度和到期复习安排。')}</p><div class="n5c-actions">${action}</div><p id="n5c-status" role="status"></p></div><p class="n5c-muted">想换个练习方向，或有内容没弄懂？打开「学习帮助」，随时问 WorkBuddy。</p><small class="n5c-muted">完成记录只代表本次实际学习，不等于已掌握或通过 JLPT。</small></div>`;
    document.getElementById('n5c-retry')?.addEventListener('click',sync);
    document.getElementById('n5c-next')?.addEventListener('click',()=>start());
    next?.sessions?.forEach((s,i)=>document.getElementById('n5c-choice-'+i)?.addEventListener('click',()=>start(s.session_id)));
    document.getElementById('n5c-stop')?.addEventListener('click',()=>{document.getElementById('n5c-status').textContent='进度已保存，可以关闭页面。下次在 WorkBuddy 中说“继续学习”。';});
    header();
  }
  async function sync(){
    if(syncing)return;syncing=true;completed();
    try{proposal=await api({action:'sync'});synced=true;completed();}
    catch(e){completed(e.message);}finally{syncing=false;}
  }
  async function start(selected){
    document.querySelectorAll('.n5c-actions button').forEach(n=>n.disabled=true);
    document.getElementById('n5c-status').textContent='正在打开下一项…';
    try{
      const r=await api({action:'continue',plan_key:proposal.plan_key,selected});
      if(r.status==='refresh'){await sync();return;}
      const url=new URL(r.practice_url);if(url.hostname!=='127.0.0.1'||url.protocol!=='http:')throw Error('下一项地址不合法');
      // The bridge verifies the destination before returning this local URL.
      location.assign(url.href);
    }catch(e){completed(e.message);}
  }
  function header(){
    let h=app().querySelector('header');
    if(h){document.querySelector('header.n5c-moved')?.remove();h.classList.add('n5c-moved');app().parentNode.insertBefore(h,app());}
    if(document.getElementById('n5c-help-button'))return;
    h=h||document.querySelector('header');
    if(!h){h=document.createElement('header');h.className='n5c-header';h.innerHTML='<strong><span class="n5c-mark">あ</span>正能日语</strong>';app().parentNode.insertBefore(h,app());}
    h.classList.add('n5c-tools');
    const brand=h.querySelector('.brand-name')||h.querySelector('.brand span:last-child');if(brand&&brand.textContent.includes('日语起点'))brand.textContent='正能日语';
    const b=document.createElement('button');b.id='n5c-help-button';b.className='n5c-link';b.textContent='学习帮助';b.onclick=help;
    h.append(b);
  }
  function teaching(){return new Set(['teaching','feedback','learning_teaching','recovery_teaching','learning_meaning_contexts','learning_variant','learning_embedded_support','system_map','foundation_batch_intro','foundation_single_kana_learning']).has(current?.stage);}
  function restricted(){return (current?.kind==='g3_mock'&&current.stage!=='completed')||current?.kind==='entry_diagnostic'||(!teaching()&&current?.stage!=='completed');}
  async function help(){
    const technical=restricted();
    const surface=app().querySelector('article,.card,.learning-card')||app();
    const visible=surface.innerText.slice(0,6000);
    if(teaching()){
      try{await adapter.pause();}catch{window.alert('暂时无法保存播放位置，请重试后再打开帮助。');return;}
    }
    helpText=technical?`我正在正能日语的「${label()}」中，需要操作或设备帮助。请不要解释正在测试的题目。`:`我正在正能日语学习「${label()}」。以下是当前已显示的学习内容：\n${visible}\n\n我想问：`;
    helpOpen=true;app().hidden=true;
    let panel=document.getElementById('n5c-help');if(!panel){panel=document.createElement('section');panel.id='n5c-help';document.body.append(panel);}
    panel.hidden=false;panel.className='n5c-main';
    panel.innerHTML=`<p class="n5c-blue">学习帮助</p><h1>有问题，随时问 WorkBuddy</h1><p class="n5c-muted">这里不会开启新对话。复制学习信息，切回 WorkBuddy 提问；聊完再回来继续。</p><div class="n5c-summary n5c-context"><span>你现在学到这里</span><h2>${esc(label())}</h2>${technical?'<p>测验期间只提供操作与设备帮助，不复制题面或请求解题提示。</p>':`<pre>${esc(visible)}</pre>`}<small class="n5c-muted">只复制当前已显示的必要信息，不包含整份学习档案。</small></div><p id="n5c-help-timer" class="n5c-muted"></p><h2>你可以这样问</h2><p class="n5c-muted">${technical?'“音频无法播放，请帮我检查，不要提交或修改我的答案。”':'“这部分我还没理解，请换一种方式讲解。”'}</p><p class="n5c-muted">“今天想改练阅读，请保留当前进度，帮我换一个活动。”</p><div class="n5c-actions">${button('n5c-copy','复制学习信息')}${button('n5c-back','返回当前学习',true)}</div><p id="n5c-copy-status" role="status"></p><textarea id="n5c-copy-fallback" aria-label="手动复制学习信息" readonly hidden></textarea><p class="n5c-muted">打开帮助不会提交答案或改变学习进度。测验计时不会暂停。</p>`;
    document.getElementById('n5c-copy').onclick=async()=>{try{await navigator.clipboard.writeText(helpText);document.getElementById('n5c-copy-status').textContent='已复制，请切回 WorkBuddy 粘贴并写下你的问题。';}catch{const n=document.getElementById('n5c-copy-fallback');n.value=helpText;n.hidden=false;n.focus();n.select();document.getElementById('n5c-copy-status').textContent='无法自动复制，请从下方手动复制。';}};
    document.getElementById('n5c-back').onclick=()=>{helpOpen=false;panel.hidden=true;app().hidden=false;document.getElementById('n5c-help-button')?.focus();};
    document.getElementById('n5c-back').focus();
    // Reopen the same offline guide without leaving or replacing this activity.
    // Older course versions expose no link; no guessed file URL from an HTTP page.
    try {
      const response = await fetch('/api/learning-tour');
      if (!response.ok) return;
      const result = await response.json();
      if (!helpOpen || result.status !== 'ready' || result.url !== '/reference/tour.html#1' || document.getElementById('n5c-tour')) return;
      const link = document.createElement('a');
      link.id='n5c-tour';link.className='n5c-link';link.href=result.url;
      link.target='_blank';link.rel='noopener';link.textContent='使用介绍';
      panel.append(link);
    } catch { /* Help and recovery remain usable without the optional guide. */ }
  }
  setInterval(()=>{const n=document.getElementById('n5c-help-timer');if(helpOpen&&n&&current?.deadline_epoch){const s=Math.max(0,Math.ceil(current.deadline_epoch-Date.now()/1000));n.textContent=`模考仍在计时：本部分剩余 ${Math.floor(s/60)} 分 ${s%60} 秒。`; }},1000);
  function update(value,options){
    current=value;adapter=options;header();
    if(current?.stage==='completed'&&current?.kind!=='entry_diagnostic'){
      if(synced)completed();else if(!syncing)sync();
    }
    if(helpOpen)app().hidden=true;
  }
  return {update};
})();
