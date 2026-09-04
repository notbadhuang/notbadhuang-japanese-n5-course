/* Static introduction: no service, archive, storage, audio or learning events. */
(() => {
  'use strict';
  const main = document.getElementById('main');
  const phrase = '我想开始学习正能日语，请帮我安排合适的起点。';
  const headings = ['从适合你的地方开始','想查什么，不必等到那一课','不用每个问题，都打开一页','不用记网址，记住两句话'];
  const labels = ['在浏览器里 · 按课程学习','在浏览器里 · 自由查资料','在 WorkBuddy 里 · 随时提问','随时回来 · 继续与恢复'];
  const descriptions = [
    '讲解、发音、练习和小测，都在浏览器里完成。<br>完成当前活动后，可以直接继续下一项。',
    '把课程当作随手可查的参考资料。<br>查读音、听发音、看例句，不必先做诊断。',
    '查几个词、比较表达、追问一句语法，直接在对话里问。<br>浏览器负责课程，WorkBuddy 负责和你交流。',
    '下次学习，从 WorkBuddy 回来。<br>页面打不开时，也不用在坏掉的页面里找按钮。'
  ];
  const sections = [
    `<div class="panel"><h2>你的 0 → N5 学习路线</h2><div class="route"><span>假名基础</span><span>词汇与语法</span><span>阅读与听力</span><span>自编模考</span></div><p class="note">这是默认推荐顺序；想单独练阅读、听力或做模考，也可以告诉 WorkBuddy。</p></div><div class="starts"><section><h2>日语零基础</h2><p>从假名开始，不必先做测试。</p></section><section><h2>学过一些日语</h2><p>先做起点诊断，帮助找到从哪里开始。</p></section></div><p class="note">诊断用于安排起点，不代表取得 JLPT 成绩或已经完成课程。</p>`,
    `<div class="chips"><span>词汇总表</span><span>语法目录</span><span>数字／日期／量词附表</span></div><div class="panel table-panel"><p class="note" id="demo-label">词汇表片段 · 导览示例，不在这里操作</p><table class="demo" aria-describedby="demo-label"><thead><tr><th>序号</th><th>词汇</th><th>读音</th><th>本课程释义</th><th>主讲课程</th><th>发音</th><th>例句</th></tr></thead><tbody><tr><td>01</td><td>私</td><td>わたし</td><td>我</td><td>U01</td><td><span class="demo-play">播放</span></td><td><span class="demo-link">收起</span></td></tr><tr class="example"><td colspan="7"><div class="example-line"><div><strong>私はリンです。</strong><p class="note">我是小林。</p></div><span class="demo-play">播放</span></div></td></tr><tr><td>02</td><td>さい</td><td>さい</td><td>……岁（年龄）</td><td>U01</td><td><span class="demo-play">播放</span></td><td><span class="demo-link">展开</span></td></tr></tbody></table></div><p class="note">自由查阅不会计入学习进度。附表展示当前已收录内容，不是全量量词组合库。</p>`,
    `<div class="panel"><h2>可以这样问 WorkBuddy</h2><p class="prompt">「駅」是什么意思？怎么读？</p><p class="prompt">「これ」和「それ」有什么区别？</p><p class="prompt">这句没理解，能再给我一个简单的例子吗？</p></div><p class="emphasis">想换个内容也可以说：“我今天想练听力。”</p><p class="note">对话答疑不自动计入课程进度。小测和模考作答期间，只提供操作与设备帮助。</p>`,
    `<div class="panel recovery"><section><h3>下次回来学习</h3><p class="phrase">继续学习</p><p class="note">优先接着未完成的内容，有多个活动时会请你选择。</p></section><section><h3>页面不见了，或打不开</h3><p class="phrase">帮我恢复日语学习，保留学习记录</p><p class="note">先检查、再尝试恢复；不会通过清空记录来重新开始。</p></section></div><p class="note">离开前留意页面的保存提示。模考开始后，关闭页面不会暂停计时。</p><p class="note">如果恢复失败，WorkBuddy 会说明下一步；不要自行删除课程或学习记录。</p>`
  ];
  const link = (href, text, primary=false) => `<a class="${primary?'primary':'secondary'}" href="${href}">${text}</a>`;
  let previousStep = 4;
  function render(focus=false) {
    const raw = location.hash.slice(1);
    const handoff = raw === 'start';
    const step = /^[1-4]$/.test(raw) ? Number(raw) : handoff ? previousStep : 1;
    if (!handoff) previousStep = step;
    document.querySelectorAll('nav a').forEach((node, index) => {
      if (index === step - 1) node.setAttribute('aria-current','step');
      else node.removeAttribute('aria-current');
    });
    document.querySelector('aside h2').textContent = handoff ? '准备开始了吗？' : '先认识你的学习工具';
    document.getElementById('aside-description').textContent = handoff ? '先回到 WorkBuddy 对话。' : '四个用法，随时可以跳过。';
    const skip = document.getElementById('skip');
    skip.textContent = handoff ? '返回介绍' : '跳过介绍 →';
    skip.href = handoff ? '#'+previousStep : '#4';
    document.getElementById('progress').textContent = handoff ? '网页不会自动向 WorkBuddy 发送消息。' : `${step} / 4 · 使用介绍，不计入学习进度`;
    // Both destinations are shipped and verified as part of this reference bundle.
    document.getElementById('actions').innerHTML = handoff ? link('index.html#vocabulary','先查资料') :
      (step>1 ? link('#'+(step-1),'上一步') : '') + (step<4 ? link('#'+(step+1),'下一步 →',true) : link('index.html#vocabulary','先查资料')+link('#start','开始学习',true));
    main.innerHTML = handoff ? `<p class="eyebrow">下一步 · 回到 WorkBuddy</p><h1 tabindex="-1">告诉助手，你准备开始了</h1><p class="description">复制下面这句话，切换到 WorkBuddy 对话并发送。<br>它会确认你的学习经历，再打开适合的学习入口。</p><div class="panel handoff"><p class="phrase">${phrase}</p><button class="primary" id="copy" type="button">复制这句话</button><p id="copy-status" role="status" aria-live="polite"></p><textarea id="copy-fallback" aria-label="手动复制开始学习指令" readonly hidden></textarea></div><p class="note">已经学过这套课程？发送“继续学习”，保留原来的进度。</p><p class="note">查看介绍不会创建学习档案，也不会自动开始诊断。<br>真正开始学习时，WorkBuddy 会先说明本地学习记录的保存方式。</p>` : `<p class="eyebrow">${labels[step-1]}</p><h1 tabindex="-1">${headings[step-1]}</h1><p class="description">${descriptions[step-1]}</p>${sections[step-1]}`;
    document.title = `${handoff?'准备开始学习':headings[step-1]} · 正能日语使用介绍`;
    if (handoff) document.getElementById('copy').addEventListener('click', async () => {
      const button = document.getElementById('copy');
      const status = document.getElementById('copy-status');
      const fallback = document.getElementById('copy-fallback');
      button.disabled = true;
      try {
        await navigator.clipboard.writeText(phrase);
        status.textContent = '已复制，请切回 WorkBuddy 粘贴并发送。';
        fallback.hidden = true;
      } catch {
        fallback.value = phrase;
        fallback.hidden = false;
        if (fallback.isConnected) { fallback.focus(); fallback.select(); }
        status.textContent = '无法自动复制，请从下方手动复制，再切回 WorkBuddy 发送。';
      } finally { button.disabled = false; }
    });
    if (focus) main.querySelector('h1').focus();
  }
  window.addEventListener('hashchange', () => render(true));
  render();
})();
