const STORAGE_KEY = "n5-entry-diagnostic-v1:session";

const app = document.querySelector("#app");
const headerAction = document.querySelector("#headerAction");
const toast = document.querySelector("#toast");
const liveRegion = document.querySelector("#liveRegion");

const icons = {
  arrow: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
  play: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m8 5 11 7-11 7z"/></svg>',
  retry: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 4v6h6M20 20v-6h-6M5.5 15a7 7 0 0 0 11.5 2M18.5 9A7 7 0 0 0 7 7"/></svg>',
  close: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
};

const defaultIntake = {
  experience: "never",
  kana: "none",
  target: "none",
  weekly_time: "3to5",
};

let bootstrap = null;
let state = loadState() || freshState();
let activeAudio = null;
let exitReturnView = "question";

function freshState() {
  return {
    schema_version: 1,
    session_id: "",
    view: "intake",
    intake: { ...defaultIntake },
    responses: [],
    ended_reason: null,
    current_item: null,
    progress: null,
    result: null,
    selected_option_id: null,
    fragment_order: [],
    audio_plays: {},
    device_panel_open: false,
    updated_at: new Date().toISOString(),
  };
}

function loadState() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (!value || value.schema_version !== 1) return null;
    return { ...freshState(), ...value, intake: { ...defaultIntake, ...value.intake } };
  } catch {
    return null;
  }
}

function saveState() {
  state.updated_at = new Date().toISOString();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function clearState() {
  stopAudio();
  localStorage.removeItem(STORAGE_KEY);
  state = freshState();
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function announce(message) {
  liveRegion.textContent = "";
  requestAnimationFrame(() => {
    liveRegion.textContent = message;
  });
}

function setHeader(mode) {
  if (mode === "question") {
    headerAction.hidden = false;
    headerAction.innerHTML = `${icons.close}<span>暂时退出</span>`;
    headerAction.onclick = () => renderExit();
  } else if (mode === "exit") {
    headerAction.hidden = false;
    headerAction.innerHTML = `${icons.close}<span>返回测试</span>`;
    headerAction.onclick = () => renderQuestion();
  } else {
    headerAction.hidden = true;
    headerAction.onclick = null;
  }
}

async function requestNext() {
  app.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/next", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.session_id,
        intake: state.intake,
        responses: state.responses,
        ended_reason: state.ended_reason,
      }),
    });
    const payload = await response.json();
    if (!response.ok || payload.status === "error") {
      throw new Error(payload.message || "测试服务暂时不可用");
    }
    state.session_id = payload.session_id;
    if (payload.status === "complete") {
      state.view = "result";
      state.result = payload.result;
      state.current_item = null;
      state.progress = null;
      state.selected_option_id = null;
      state.fragment_order = [];
      saveState();
      renderResult();
      return;
    }
    state.view = "question";
    state.current_item = payload.item;
    state.progress = payload.progress;
    state.selected_option_id = null;
    state.fragment_order = [];
    state.device_panel_open = false;
    saveState();
    renderQuestion();
  } catch (error) {
    renderError(error.message);
  } finally {
    app.removeAttribute("aria-busy");
  }
}

function renderIntake() {
  stopAudio();
  state.view = "intake";
  setHeader("intake");
  const canResume = state.responses.length > 0 || Boolean(state.current_item) || Boolean(state.result);
  const answered = state.responses.length;
  app.innerHTML = `
    <section class="intake-layout">
      <div class="intake-copy">
        <h1>先弄清起点，<br>再安排怎么学。</h1>
        <p>这不是JLPT模考，也不会给你一个虚假的“合格概率”。它会用最少的题目判断：你应该从零开始，还是从某个薄弱点接上。</p>
        <ul class="boundary-list">
          <li><span class="check-icon">✓</span><span>自适应出题，通常6～18题，最多24题</span></li>
          <li><span class="check-icon">✓</span><span>音频故障不会算错，可以单独结束测试</span></li>
          <li><span class="check-icon">✓</span><span>测试结果只用于确定学习起点，不冒充JLPT分数</span></li>
        </ul>
      </div>
      <form class="intake-card" id="intakeForm">
        <h2>告诉我你现在的大概情况</h2>
        <p class="form-intro">不用回忆得很精确。正式起点主要由后面的作答证据决定。</p>
        ${selectField("experience", "你学过多久日语？", [
          ["never", "完全没学过"], ["days", "学过几天"], ["weeks", "学过几周"], ["months", "学过几个月或更久"],
        ])}
        ${selectField("kana", "五十音目前是什么状态？", [
          ["none", "基本不认识"], ["some", "能认出一部分"], ["hiragana", "平假名较熟"], ["both", "平假名和片假名都较熟"],
        ])}
        ${selectField("target", "离你的目标时间还有多久？", [
          ["none", "暂时没有固定日期"], ["30", "30天以内"], ["60", "31～60天"], ["90", "61～90天或更久"],
        ])}
        ${selectField("weekly_time", "每周大概能学多久？", [
          ["under3", "3小时以内"], ["3to5", "3～5小时"], ["5to8", "5～8小时"], ["over8", "8小时以上"],
        ])}
        ${canResume ? `
          <div class="resume-panel">
            <strong>${state.result ? "上次测试已经完成" : `上次已保存 ${answered} 道作答`}</strong>
            <div class="resume-actions">
              <button class="outline-button" type="button" id="resumeButton">${state.result ? "查看结果" : "继续测试"}</button>
              <button class="quiet-button" type="button" id="discardButton">重新开始</button>
            </div>
          </div>` : ""}
        <button class="primary-button wide-button" type="submit">开始测试 ${icons.arrow}</button>
        <p class="form-footnote">作答记录只保存在当前浏览器里</p>
      </form>
    </section>`;

  document.querySelector("#intakeForm").addEventListener("submit", (event) => {
    event.preventDefault();
    clearState();
    state.intake = readIntake(event.currentTarget);
    saveState();
    requestNext();
  });
  document.querySelector("#resumeButton")?.addEventListener("click", () => {
    if (state.result) renderResult();
    else requestNext();
  });
  document.querySelector("#discardButton")?.addEventListener("click", () => {
    clearState();
    renderIntake();
    showToast("旧记录已清除");
  });
}

function selectField(name, label, options) {
  return `<div class="intake-field"><label for="${name}">${label}</label><select id="${name}" name="${name}">
    ${options.map(([value, text]) => `<option value="${value}" ${state.intake[name] === value ? "selected" : ""}>${text}</option>`).join("")}
  </select></div>`;
}

function readIntake(form) {
  const data = new FormData(form);
  return Object.fromEntries(["experience", "kana", "target", "weekly_time"].map((key) => [key, data.get(key)]));
}

function renderQuestion() {
  stopAudio();
  const item = state.current_item;
  if (!item) {
    requestNext();
    return;
  }
  state.view = "question";
  saveState();
  setHeader("question");
  const progress = state.progress || {};
  const isOrdering = item.response_mode === "ordered_fragments";
  app.innerHTML = `
    <section class="diagnostic-layout">
      <div class="progress-section">
        <div class="progress-meta">
          <span class="progress-domain">${escapeHtml(item.domain_label_zh)} · ${escapeHtml(item.ability_title_zh)}</span>
          <span class="progress-count">第 ${progress.current_number || state.responses.length + 1} 题 · 自适应进度</span>
        </div>
        <div class="progress-track" role="progressbar" aria-label="测试进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.progress_percent || 0}"><span class="progress-value" style="width:${Math.max(2, progress.progress_percent || 0)}%"></span></div>
      </div>
      <div class="question-wrap">
        <section class="question-card" aria-labelledby="questionTitle">
          <div class="question-meta">
            <span class="question-type">${isOrdering ? "句子组成" : item.stimulus.audio_url || item.options.some((o) => o.audio_url) ? "听力识别" : "选择题"}</span>
            <span class="question-limit">${item.maximum_plays_per_clip ? `每段音频最多播放 ${item.maximum_plays_per_clip} 次` : "提交前可以修改"}</span>
          </div>
          <h1 id="questionTitle">${escapeHtml(item.prompt_zh)}</h1>
          ${renderStimulus(item)}
          ${isOrdering ? renderOrdering(item) : renderChoices(item)}
          ${(item.stimulus.audio_url || item.options.some((o) => o.audio_url)) ? `<button class="quiet-button device-link" id="deviceIssueButton" type="button">音频无法播放？</button>${state.device_panel_open ? renderDevicePanel() : ""}` : ""}
          <div class="question-actions">
            ${isOrdering ? `<button class="outline-button" id="clearOrderButton" type="button">${icons.retry} 清空重排</button>` : `<span></span>`}
            <button class="primary-button" id="submitAnswerButton" type="button" ${answerReady(item) ? "" : "disabled"}>确认答案 ${icons.arrow}</button>
          </div>
          <p class="save-note">已自动保存。提交后本题不显示对错，避免影响后续诊断。</p>
        </section>
      </div>
    </section>`;
  bindQuestionEvents(item);
  app.focus({ preventScroll: true });
}

function renderStimulus(item) {
  const stimulus = item.stimulus || {};
  const text = stimulus.displayed_text_ja || stimulus.sentence_ja || stimulus.passage_ja || stimulus.instruction_ja;
  let html = "";
  if (stimulus.preannounced_goal_zh) html += `<div class="stimulus-block"><p class="pool-label">先看任务</p><p class="stimulus-main">${escapeHtml(stimulus.preannounced_goal_zh)}</p></div>`;
  if (text) html += `<div class="stimulus-block"><p class="stimulus-main" lang="ja">${markTarget(text, stimulus.target_ja)}</p></div>`;
  if (stimulus.title_ja || Array.isArray(stimulus.rows_ja)) {
    html += `<div class="notice-material">${stimulus.title_ja ? `<h2 lang="ja">${escapeHtml(stimulus.title_ja)}</h2>` : ""}${(stimulus.rows_ja || []).map((row) => renderMaterialRow(row)).join("")}</div>`;
  }
  if (stimulus.visual_url) html += `<img class="scene-visual" src="${escapeHtml(stimulus.visual_url)}" alt="本题场景示意图">`;
  if (stimulus.audio_url) html += renderAudioPlayer(stimulus.audio_url, "题目音频", item.maximum_plays_per_clip || 2);
  if (item.options.some((option) => option.audio_url)) {
    html += `<div class="option-audio-grid">${item.options.map((option) => option.audio_url ? `<button class="option-audio" type="button" data-audio-url="${escapeHtml(option.audio_url)}" data-audio-key="${escapeHtml(option.option_id)}">${icons.play}<span>${escapeHtml(option.label)}</span></button>` : "").join("")}</div>`;
  }
  return html;
}

function markTarget(text, target) {
  if (!target || !text.includes(target)) return escapeHtml(text);
  const index = text.indexOf(target);
  return `${escapeHtml(text.slice(0, index))}<span class="target-mark">${escapeHtml(target)}</span>${escapeHtml(text.slice(index + target.length))}`;
}

function renderMaterialRow(row) {
  if (Array.isArray(row)) return `<div class="notice-row"><span>${escapeHtml(row[0] || "")}</span><span>${escapeHtml(row.slice(1).join(" "))}</span></div>`;
  if (row && typeof row === "object") {
    const values = Object.values(row);
    return `<div class="notice-row"><span>${escapeHtml(values[0] || "")}</span><span>${escapeHtml(values.slice(1).join(" "))}</span></div>`;
  }
  return `<div class="notice-row"><span></span><span>${escapeHtml(row)}</span></div>`;
}

function renderAudioPlayer(url, label, limit) {
  const count = state.audio_plays[url] || 0;
  return `<div class="audio-player" data-player="${escapeHtml(url)}">
    <button class="audio-button" type="button" data-audio-url="${escapeHtml(url)}" data-audio-key="main" ${count >= limit ? "disabled" : ""} aria-label="播放${escapeHtml(label)}">${icons.play}</button>
    <div class="audio-copy"><strong>${escapeHtml(label)}</strong><span>点击播放 · 已播放 ${count}/${limit} 次</span></div>
    <span class="audio-state">${count >= limit ? "次数已用完" : "准备就绪"}</span>
  </div>`;
}

function renderChoices(item) {
  return `<div class="choice-list" role="radiogroup" aria-label="答案选项">${item.options.map((option, index) => `
    <button class="choice ${state.selected_option_id === option.option_id ? "is-selected" : ""}" type="button" role="radio" aria-checked="${state.selected_option_id === option.option_id}" data-option-id="${escapeHtml(option.option_id)}">
      <span class="choice-key">${String.fromCharCode(65 + index)}</span><span class="choice-label">${escapeHtml(option.label)}</span>
    </button>`).join("")}</div>`;
}

function renderOrdering(item) {
  const fragments = item.stimulus.fragments || [];
  const byId = Object.fromEntries(fragments.map((fragment) => [fragment.fragment_id, fragment]));
  const selected = state.fragment_order;
  const remaining = fragments.filter((fragment) => !selected.includes(fragment.fragment_id));
  return `
    <div class="ordering-status"><strong>已放置 ${selected.length} / ${fragments.length}</strong><span>${selected.length === fragments.length ? "全部词块已放置，仍可点击调整" : "点击下方词块依次放入"}</span></div>
    <ol class="answer-slots" aria-label="当前排列">${fragments.map((_, index) => {
      const id = selected[index];
      return `<li class="answer-slot ${id ? "is-filled" : ""}">${id ? `<button class="fragment" type="button" data-placed-id="${escapeHtml(id)}" aria-label="移回词块 ${escapeHtml(byId[id].text_ja)}">${escapeHtml(byId[id].text_ja)}</button>` : `<span>${index + 1}</span>`}</li>`;
    }).join("")}</ol>
    <div><p class="pool-label">待放置词块</p><div class="fragment-pool">${remaining.map((fragment) => `<button class="fragment" type="button" data-fragment-id="${escapeHtml(fragment.fragment_id)}">${escapeHtml(fragment.text_ja)}</button>`).join("")}</div></div>`;
}

function renderDevicePanel() {
  return `<section class="device-panel" aria-labelledby="devicePanelTitle">
    <h2 id="devicePanelTitle">先确认是不是设备问题</h2>
    <p>播放测试提示音。如果仍然没有声音，可以结束本次测试；系统会记录“证据不足”，不会把听力题算错。</p>
    <div class="device-actions">
      <button class="outline-button" id="testAudioButton" type="button">${icons.play} 播放</button>
      <button class="quiet-button" id="closeDeviceButton" type="button">可以播放，继续作答</button>
      <button class="quiet-button" id="endForDeviceButton" type="button">仍无法播放，结束测试</button>
    </div>
  </section>`;
}

function answerReady(item) {
  if (item.response_mode === "single_choice") return Boolean(state.selected_option_id);
  return state.fragment_order.length === (item.stimulus.fragments || []).length;
}

function bindQuestionEvents(item) {
  document.querySelectorAll("[data-option-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selected_option_id = button.dataset.optionId;
      saveState();
      renderQuestion();
    });
  });
  document.querySelectorAll("[data-fragment-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.fragment_order.push(button.dataset.fragmentId);
      saveState();
      renderQuestion();
    });
  });
  document.querySelectorAll("[data-placed-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.fragment_order = state.fragment_order.filter((id) => id !== button.dataset.placedId);
      saveState();
      renderQuestion();
    });
    button.addEventListener("keydown", (event) => {
      const index = state.fragment_order.indexOf(button.dataset.placedId);
      const delta = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
      if (!delta || index + delta < 0 || index + delta >= state.fragment_order.length) return;
      event.preventDefault();
      [state.fragment_order[index], state.fragment_order[index + delta]] = [state.fragment_order[index + delta], state.fragment_order[index]];
      saveState();
      renderQuestion();
    });
  });
  document.querySelectorAll("[data-audio-url]").forEach((button) => {
    button.addEventListener("click", () => playAudio(button.dataset.audioUrl, button));
  });
  document.querySelector("#deviceIssueButton")?.addEventListener("click", () => {
    state.device_panel_open = !state.device_panel_open;
    saveState();
    renderQuestion();
  });
  document.querySelector("#closeDeviceButton")?.addEventListener("click", () => {
    state.device_panel_open = false;
    saveState();
    renderQuestion();
  });
  document.querySelector("#testAudioButton")?.addEventListener("click", () => playAudio(bootstrap.device_preflight_audio_url, document.querySelector("#testAudioButton"), false));
  document.querySelector("#endForDeviceButton")?.addEventListener("click", () => {
    state.ended_reason = "persistent_audio_playback_failure";
    saveState();
    requestNext();
  });
  document.querySelector("#clearOrderButton")?.addEventListener("click", () => {
    state.fragment_order = [];
    saveState();
    renderQuestion();
    announce("排列已清空");
  });
  document.querySelector("#submitAnswerButton")?.addEventListener("click", submitAnswer);
}

function playAudio(url, button, countPlay = true) {
  const limit = state.current_item?.maximum_plays_per_clip || 2;
  const count = state.audio_plays[url] || 0;
  if (countPlay && count >= limit) {
    showToast(`这段音频最多播放 ${limit} 次`);
    return;
  }
  stopAudio();
  activeAudio = new Audio(url);
  if (countPlay) {
    state.audio_plays[url] = count + 1;
    saveState();
  }
  button?.setAttribute("aria-busy", "true");
  activeAudio.addEventListener("ended", () => {
    button?.removeAttribute("aria-busy");
    activeAudio = null;
    if (countPlay) renderQuestion();
  }, { once: true });
  activeAudio.addEventListener("error", () => {
    button?.removeAttribute("aria-busy");
    activeAudio = null;
    state.device_panel_open = true;
    saveState();
    renderQuestion();
    showToast("音频没有成功播放，请检查设备");
  }, { once: true });
  activeAudio.play().catch(() => {
    button?.removeAttribute("aria-busy");
    state.device_panel_open = true;
    saveState();
    renderQuestion();
  });
}

function stopAudio() {
  if (!activeAudio) return;
  activeAudio.pause();
  activeAudio.src = "";
  activeAudio = null;
}

function submitAnswer() {
  const item = state.current_item;
  if (!item || !answerReady(item)) return;
  const response = {
    diagnostic_item_id: item.diagnostic_item_id,
    ...(item.response_mode === "single_choice" ? { option_id: state.selected_option_id } : { fragment_order: [...state.fragment_order] }),
  };
  state.responses.push(response);
  state.current_item = null;
  state.selected_option_id = null;
  state.fragment_order = [];
  saveState();
  requestNext();
}

function renderExit() {
  stopAudio();
  exitReturnView = state.view;
  state.view = "exit";
  saveState();
  setHeader("exit");
  app.innerHTML = `<section class="exit-panel">
    <span class="brand-mark" aria-hidden="true">あ</span>
    <h1>进度已经保存</h1>
    <p>目前已记录 ${state.responses.length} 道作答。你可以关闭这个页面，下次在同一浏览器继续。</p>
    <button class="primary-button" id="continueButton" type="button">继续测试 ${icons.arrow}</button>
    <button class="quiet-button" id="backHomeButton" type="button">回到测试说明</button>
  </section>`;
  document.querySelector("#continueButton").onclick = () => exitReturnView === "result" ? renderResult() : renderQuestion();
  document.querySelector("#backHomeButton").onclick = renderIntake;
}

function renderResult() {
  stopAudio();
  if (!state.result) {
    requestNext();
    return;
  }
  state.view = "result";
  saveState();
  setHeader("result");
  const result = state.result;
  const screening = result.screening_coverage?.screening_coverage_percent || 0;
  const diagnostic = result.diagnostic_coverage?.diagnostic_coverage_percent || 0;
  app.innerHTML = `<section class="result-layout">
    <p class="result-kicker">你的测试结果 · 基于 ${state.responses.length} 道有效作答</p>
    <div class="result-hero">
      <div><h1>建议从“${escapeHtml(result.recommended_start.title)}”开始</h1><p>${escapeHtml(result.recommended_start.description)}</p></div>
      <div class="route-card"><span>推荐起点</span><strong>${escapeHtml(result.recommended_start.short_title)}</strong></div>
    </div>
    <div class="coverage-grid">
      ${coverageCard("筛查覆盖", screening, "已经触达多少个能力点，用来判断测试走到哪里。", "")}
      ${coverageCard("结论覆盖", diagnostic, "有多少能力点已经取得足够证据，可以形成较稳定结论。", "is-conclusion")}
    </div>
    <div class="evidence-boundary"><strong>这个结果能说明什么？</strong><span>它用于确定学习起点，不等于JLPT分数、合格证，也不估算合格概率。</span></div>
    <div class="result-columns">
      ${evidenceColumn("已确认优势", result.confirmed_strengths, "is-strength")}
      ${evidenceColumn("优先补齐", result.priority_gaps, "is-gap")}
      ${evidenceColumn("尚未测定", result.deferred_or_unmeasured, "is-deferred")}
    </div>
    <div class="result-actions">
      <button class="primary-button" id="planButton" type="button">生成我的学习安排 ${icons.arrow}</button>
      <button class="outline-button" id="profileButton" type="button">继续完整画像</button>
      <button class="quiet-button" id="downloadButton" type="button">保存结果</button>
      <button class="quiet-button" id="restartButton" type="button">重新测试</button>
    </div>
    <section class="plan-panel" id="planPanel" hidden></section>
  </section>`;
  document.querySelector("#planButton").onclick = renderPlanPanel;
  document.querySelector("#profileButton").onclick = () => showToast("扩展画像题组尚未开放；当前结果已完整保存");
  document.querySelector("#downloadButton").onclick = downloadResult;
  document.querySelector("#restartButton").onclick = () => {
    clearState();
    renderIntake();
    showToast("已开始一轮新测试");
  };
  document.querySelectorAll("[data-disclosure]").forEach((button) => {
    button.onclick = () => expandEvidenceColumn(button);
  });
  app.focus({ preventScroll: true });
}

function coverageCard(title, value, explanation, className) {
  return `<section class="coverage-card ${className}"><h2>${title}</h2><p class="coverage-subtitle">${explanation}</p><div class="coverage-value">${value}%</div><div class="coverage-bar" aria-hidden="true"><span style="width:${value}%"></span></div></section>`;
}

function evidenceColumn(title, items, className) {
  const canCollapse = className === "is-strength" || className === "is-deferred";
  return `<section class="evidence-column ${className}"><h2>${title}</h2><span>${items.length} 个能力点</span>${items.length ? `<ul class="evidence-list">${items.map((item) => `<li>${escapeHtml(item.title_zh)}</li>`).join("")}</ul>` : `<p class="empty-evidence">当前没有足够证据放入这一栏。</p>`}${canCollapse ? `<button class="disclosure-button" type="button" data-disclosure="${className}" aria-expanded="false" aria-label="展开${title}"></button>` : ""}</section>`;
}

function expandEvidenceColumn(button) {
  const column = button.closest(".evidence-column");
  const expanded = column.classList.toggle("is-expanded");
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute("aria-label", `${expanded ? "收起" : "展开"}${column.querySelector("h2").textContent}`);
}

function renderPlanPanel() {
  const result = state.result;
  const gapNames = result.priority_gaps.slice(0, 4).map((item) => item.title_zh);
  const timeText = {
    under3: "每周3小时以内",
    "3to5": "每周3～5小时",
    "5to8": "每周5～8小时",
    over8: "每周8小时以上",
  }[state.intake.weekly_time] || "按你的可用时间";
  const panel = document.querySelector("#planPanel");
  panel.hidden = false;
  panel.innerHTML = `<h2>学习安排交接单</h2><p>这一步只生成可供Agent继续规划的可靠输入，不虚构精确天数。</p><div class="plan-brief">
    <strong>起点：</strong><span>${escapeHtml(result.recommended_start.title)}</span>
    <strong>时间：</strong><span>${escapeHtml(timeText)}</span>
    <strong>优先项：</strong><span>${escapeHtml(gapNames.length ? gapNames.join("、") : "继续扩展画像或进入限时模考")}</span>
    <strong>边界：</strong><span>未测定项目需要在后续学习中继续采样，不能当成已掌握。</span>
  </div><button class="outline-button" id="copyPlanButton" type="button">复制交接单</button>`;
  document.querySelector("#copyPlanButton").onclick = async () => {
    const text = `N5学习起点：${result.recommended_start.title}\n可用时间：${timeText}\n优先项：${gapNames.join("、") || "扩展画像或限时模考"}\n注意：未测定项目仍需继续采样。`;
    try {
      await navigator.clipboard.writeText(text);
      showToast("学习安排交接单已复制");
    } catch {
      showToast("浏览器未允许复制，请手动选择文字");
    }
  };
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function downloadResult() {
  const safeResult = {
    schema_version: 1,
    saved_at: new Date().toISOString(),
    answered_item_count: state.responses.length,
    intake: state.intake,
    result: state.result,
  };
  const blob = new Blob([JSON.stringify(safeResult, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "n5-entry-diagnostic-result.json";
  link.click();
  URL.revokeObjectURL(url);
  showToast("结果文件已保存");
}

function renderError(message) {
  stopAudio();
  setHeader("error");
  app.innerHTML = `<section class="error-state"><h1>测试暂时没有接上</h1><p>${escapeHtml(message)}</p><button class="primary-button" id="retryButton" type="button">重新连接</button><button class="quiet-button" id="homeButton" type="button">回到首页</button></section>`;
  document.querySelector("#retryButton").onclick = requestNext;
  document.querySelector("#homeButton").onclick = renderIntake;
}

async function start() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("无法读取测试配置");
    bootstrap = await response.json();
    if (state.result) renderResult();
    else if (state.current_item || state.responses.length) requestNext();
    else renderIntake();
  } catch (error) {
    renderError(error.message);
  }
}

start();
