/* Read-only handbook: intentionally no fetch, storage, profile, or event APIs. */
(() => {
  "use strict";
  const data = window.N5_REFERENCE;
  const main = document.querySelector("#main");
  if (!data) { main.textContent = "学习资料未能加载。请保留文件夹内的全部文件，再重新打开 index.html。"; return; }
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const normalize = s => s.normalize("NFKC").toLowerCase().replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));
  const searchText = row => normalize(row.pairs
    ? [...row.pairs.flatMap(p=>[p.form,p.reading]), ...row.meanings].join(" ")
    : [row.form,row.title,row.function,row.connection,row.explanation,row.note].filter(Boolean).join(" "));
  const speaker = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m11 5-6 4H2v6h3l6 4V5Z"/><path d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></svg>';
  const chevron = open => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="${open ? "m6 15 6-6 6 6" : "m6 9 6 6 6-6"}"/></svg>`;
  let page = "vocabulary", query = "", unit = "", sort = "course", tab = "numbers";
  function readSection() {
    const section = window.location.hash.slice(1);
    page = ["grammar", "appendices"].includes(section) ? section : "vocabulary";
    if (["numbers", "dates", "forms"].includes(section)) { page = "appendices"; tab = section; }
  }
  readSection();
  const expanded = new Set([data.vocabulary[0].id, data.grammar[0].id]);
  const vocabMap = new Map(data.vocabulary.map(row => [row.id, row]));
  // Plain-language display forms approved in the desktop design; L2 stays unchanged.
  const grammarLabels = {
    "n5-grammar-particle-wa-topic-v1":"名词＋は",
    "n5-grammar-polite-predication-noun-naadjective-desu-v1":"名词／な形容词＋です",
    "n5-grammar-particle-to-exhaustive-list-v1":"名词１＋と＋名词２",
    "n5-grammar-question-particle-ka-v1":"礼貌句＋か",
    "n5-grammar-particle-mo-additive-v1":"名词＋も",
    "n5-grammar-particle-no-noun-link-v1":"名词１＋の＋名词２"
  };
  let currentAudio = null, currentButton = null, playToken = 0;
  const status = document.querySelector("#audio-status");
  const playButton = (path, label, primary=false) => `<button type="button" class="audio${primary ? " primary" : ""}" data-audio="${esc(path)}" aria-label="播放：${esc(label)}" aria-pressed="false">${speaker}<span>播放</span></button>`;
  const toggle = (row, label="展开") => `<button type="button" class="expand" data-expand="${esc(row.id)}" aria-expanded="${expanded.has(row.id)}" aria-controls="detail-${esc(row.id)}" aria-label="${expanded.has(row.id) ? "收起" : label}：${esc(row.pairs?.[0].form || row.form)}">${expanded.has(row.id) ? "收起" : label}${chevron(expanded.has(row.id))}</button>`;
  const examples = rows => rows.map(e => `<div class="example"><div>${e.label ? `<p class="example-label">${esc(e.label)}</p>` : ""}<p class="ja" lang="ja">${esc(e.ja)}</p><p class="translation">${esc(e.zh)}</p></div>${playButton(e.audio, e.ja, true)}</div>`).join("");
  const cols = widths => `<colgroup>${widths.map(w => `<col class="w${w}">`).join("")}</colgroup>`;
  const head = labels => `<thead><tr>${labels.map(x => `<th scope="col">${esc(x)}</th>`).join("")}</tr></thead>`;
  const licenses = '<p class="license">课程范围不等于 JLPT 官方完整清单 · <a href="THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener">许可与署名</a></p>';
  const match = row => normalize(query).split(/\s+/).filter(Boolean).every(q => searchText(row).includes(q));

  function stopAudio() {
    playToken++;
    if (currentAudio) { currentAudio.pause(); currentAudio.removeAttribute("src"); currentAudio.load(); }
    if (currentButton) currentButton.setAttribute("aria-pressed", "false");
    currentAudio = null; currentButton = null; status.textContent = "";
  }

  async function play(button) {
    stopAudio();
    const token = playToken;
    const audio = new Audio(button.dataset.audio);
    currentAudio = audio; currentButton = button;
    button.setAttribute("aria-pressed", "true");
    const finish = error => {
      if (token !== playToken) return;
      button.setAttribute("aria-pressed", "false");
      currentAudio = null; currentButton = null;
      status.textContent = error ? "这段音频未能播放。请确认 audio 文件夹完整后重试。" : "";
    };
    audio.addEventListener("ended", () => finish(false), {once:true});
    audio.addEventListener("error", () => finish(true), {once:true});
    try { await audio.play(); } catch (_) { finish(true); }
  }

  function tableVocabulary() {
    const rows = data.vocabulary.filter(r => (!unit || r.unit === unit) && match(r));
    if (sort === "kana") rows.sort((a,b) => a.pairs[0].reading.localeCompare(b.pairs[0].reading, "ja"));
    return `<div class="table-wrap"><table aria-label="N5 词汇总表">${cols([60,148,160,285,123,124,208])}${head(["序号","词汇","读音","本课程释义","主讲课程","发音","例句"])}<tbody>${rows.map(row => `<tr data-word="${esc(row.id)}"><td>${String(row.number).padStart(2,"0")}</td><td lang="ja"><div class="stack">${row.pairs.map(p=>`<span>${esc(p.form)}</span>`).join("")}</div></td><td lang="ja"><div class="stack">${row.pairs.map(p=>`<span>${esc(p.reading)}</span>`).join("")}</div></td><td>${row.meanings.map(esc).join("；")}</td><td>${esc(row.unit)}</td><td><div class="stack">${row.pairs.map(p=>playButton(p.audio, `${p.form}（${p.reading}）`)).join("")}</div></td><td>${toggle(row)}</td></tr>${expanded.has(row.id) ? `<tr class="expanded" id="detail-${esc(row.id)}"><td colspan="7">${examples(row.examples)}</td></tr>` : ""}`).join("")}</tbody></table>${rows.length ? "" : '<p class="empty">没有找到匹配的词条。试试其他词形、读音或中文关键词。</p>'}</div><p class="footer" role="status">显示 ${rows.length} / ${data.vocabulary.length} 个词条 · 向下浏览全部内容 · 表头与搜索操作保持可见</p>`;
  }

  function grammarTable(rows) {
    return `<table aria-label="语法目录">${cols([64,268,460,136,180])}${head(["编号","形式","用途","主讲课程","讲解"])}<tbody>${rows.map(row=>`<tr><td>${String(row.number).padStart(2,"0")}</td><td>${esc(grammarLabels[row.id] || row.form)}</td><td>${esc(row.function)}</td><td>${esc(row.unit)}</td><td>${toggle(row)}</td></tr>${expanded.has(row.id) ? `<tr class="expanded" id="detail-${esc(row.id)}"><td colspan="5"><div class="grammar-detail"><div class="grammar-notes"><div><p class="label">怎么接</p><p>${esc(row.connection)}</p></div><div><p class="label">注意</p><p>${esc(row.note)}</p></div></div>${examples(row.examples)}<p class="explanation">${esc(row.explanation)}</p></div></td></tr>` : ""}`).join("")}</tbody></table>`;
  }

  function supportPanel() {
    return data.support.filter(match).map(row=>`<section class="supplement"><h2>词形辅助说明 · ${esc(row.title)}</h2><p>${esc(row.form)} · ${esc(row.unit)}</p><p>${esc(row.connection)}</p><p class="explanation">${esc(row.explanation)}</p></section>`).join("");
  }

  function tableGrammar() {
    const rows = data.grammar.filter(match);
    return grammarTable(rows) + (rows.length ? "" : '<p class="empty">没有找到匹配的主要语法。试试其他关键词。</p>') + `<p class="footer" role="status">显示 ${rows.length} / 66 项主要语法 · 编号用于查找，不是学习进度。可直接打开任意条目。</p>` + supportPanel();
  }

  function appendixTable(rows) {
    return `<table class="appendix-table" aria-label="常用表达读音对照">${cols([230,180,210,240,124,124])}${head(["想表达什么","日语","读音","使用区别","主讲课程","发音"])}<tbody>${rows.flatMap(item=>{
      const row = vocabMap.get(item.id);
      return row.pairs.map(pair=>`<tr><td>${esc(item.meaning || row.meanings.join("；"))}</td><td lang="ja">${esc(pair.form)}</td><td lang="ja">${esc(pair.reading)}</td><td>${esc(item.label)}</td><td>${esc(row.unit)}</td><td>${playButton(pair.audio, `${pair.form}（${pair.reading}）`)}</td></tr>`);
    }).join("")}</tbody></table>`;
  }

  function appendices() {
    let html = '<h1>常用附表</h1><p class="subtitle">把容易混淆的形式放在一起，按含义和用法查读音。</p><div class="tabs" role="tablist" aria-label="附表类别">';
    html += [...data.appendices,{key:"forms",label:"基础变形"}].map(t=>`<button role="tab" id="tab-${t.key}" aria-controls="appendix-content" tabindex="${tab===t.key ? 0 : -1}" aria-selected="${tab===t.key}" data-tab="${t.key}">${t.label}</button>`).join("") + `</div><section id="appendix-content" role="tabpanel" aria-labelledby="tab-${tab}">`;
    if (tab === "forms") {
      const rows = data.grammar.filter(row => row.id.startsWith("n5-grammar-inflection-"));
      html += '<h2 class="section-title">基础变形，按用途查找</h2><p class="section-intro">先看对应形式，再展开接续与例句。这里整理课程已收录的变形讲解，不把所有动词机械套用同一个规则。N＝名词，V＝动词，イA＝い形容词，ナA＝な形容词。</p>' + grammarTable(rows) + supportPanel();
    } else {
      const section = data.appendices.find(s=>s.key===tab);
      html += `<h2 class="section-title">${esc(section.title)}</h2><p class="section-intro">${esc(section.intro)}</p>${appendixTable(section.rows)}`;
      if (tab === "numbers") html += '<div class="callout"><strong>月份 ≠ 持续时间</strong><p>いちがつ 是“一月份”；いっかげつ 和 ひとつき 表示“一个月”。</p></div><p class="section-intro">附表按课程范围整理；需要更多说明时，可查词汇总表中的同一条目。</p><h2 class="section-title">更多已收录的数字与量词</h2><p class="section-intro">量词词头的独立读音不等于与数字组合后的读音。尚未收录的组合不在这里推算。</p>' + appendixTable(section.more);
    }
    return html + '</section><p class="footer">自由查阅，不写学习记录；完整量词组合与更多活用对照留待后续核对补充。</p>';
  }

  function renderResults() {
    stopAudio();
    document.querySelector("#results").innerHTML = page === "vocabulary" ? tableVocabulary() : tableGrammar();
  }

  function render() {
    stopAudio();
    document.querySelectorAll("nav button").forEach(b=>{
      if(b.dataset.page===page)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
    });
    if (page === "appendices") main.innerHTML = appendices() + licenses;
    else {
      const vocabulary = page === "vocabulary";
      main.innerHTML = `<h1>${vocabulary ? "词汇总表" : "语法目录"}</h1><p class="subtitle">${vocabulary ? "本课程收录 701 个词条。看读音、听发音，也可以展开例句。" : "按课程顺序查看 66 项主要语法，另附 1 项词形辅助说明。"}</p><div class="toolbar"><input type="search" aria-label="${vocabulary ? "搜索词汇" : "搜索语法"}" placeholder="${vocabulary ? "搜索词形、假名或中文释义" : "搜索句型、含义或接续方式"}" value="${esc(query)}">${vocabulary ? `<select aria-label="主讲课程" id="unit"><option value="">全部课程</option>${data.units.map(u=>`<option value="${u}"${unit===u ? " selected" : ""}>${u}</option>`).join("")}</select><select aria-label="排序" id="sort"><option value="course"${sort==="course" ? " selected" : ""}>课程顺序</option><option value="kana"${sort==="kana" ? " selected" : ""}>假名顺序</option></select>` : ""}</div><div id="results">${vocabulary ? tableVocabulary() : tableGrammar()}</div>${licenses}`;
    }
    document.title = `${page==="vocabulary" ? "词汇总表" : page==="grammar" ? "语法目录" : "常用附表"} · 正能日语`;
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.audio) { play(button); return; }
    if (button.dataset.expand) {
      const id = button.dataset.expand;
      if(expanded.has(id))expanded.delete(id);else expanded.add(id);
      const y = window.scrollY;
      if(page==="appendices") render();else renderResults();
      document.querySelector(`[data-expand="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
      window.scrollTo(0,y);return;
    }
    if (button.dataset.page) { page=button.dataset.page; query="";unit=""; render();window.scrollTo(0,0); }
    if (button.dataset.tab) { tab=button.dataset.tab;render();document.querySelector(`[data-tab="${tab}"]`).focus({preventScroll:true}); }
  });
  document.addEventListener("input", event => {if(event.target.matches('input[type="search"]')){query=event.target.value;renderResults();}});
  document.addEventListener("change", event => {
    if(event.target.id==="unit")unit=event.target.value;
    else if(event.target.id==="sort")sort=event.target.value;
    else return;
    renderResults();
  });
  document.addEventListener("keydown", event => {
    if (!event.target.matches('[role="tab"]'))return;
    const keys=["numbers","dates","forms"], i=keys.indexOf(tab);
    if(event.key==="ArrowRight")tab=keys[(i+1)%3];else if(event.key==="ArrowLeft")tab=keys[(i+2)%3];else return;
    event.preventDefault();render();document.querySelector(`[data-tab="${tab}"]`).focus();
  });
  window.addEventListener("pagehide", stopAudio);
  window.addEventListener("hashchange", () => { readSection(); query=""; unit=""; render(); });
  render();
})();
