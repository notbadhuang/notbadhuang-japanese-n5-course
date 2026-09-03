const app = document.querySelector("#app");
const toast = document.querySelector("#toast");
const liveRegion = document.querySelector("#liveRegion");
const headerMode = document.querySelector("#headerMode");
const sessionId = new URLSearchParams(window.location.search).get("session_id");

const icons = {
  arrow: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
  play: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m8 5 11 7-11 7z"/></svg>',
  speaker: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M11 5 6 9H3v6h3l5 4V5Zm4 4a4 4 0 0 1 0 6m2.5-8.5a8 8 0 0 1 0 11"/></svg>',
  check: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>',
  info: '<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/></svg>',
};

let view = null;
let selectedOptionId = null;
let activeAudio = null;
let cancelActiveAudio = null;
let audioRunId = 0;
let busy = false;
let skipLearningPending = false;

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

function stopAudio() {
  audioRunId += 1;
  if (cancelActiveAudio) cancelActiveAudio();
  cancelActiveAudio = null;
  if (!activeAudio) return;
  activeAudio.pause();
  activeAudio.currentTime = 0;
  activeAudio = null;
}

async function playAudio(url, button) {
  stopAudio();
  const runId = audioRunId;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const audio = new Audio(url);
    let cancelPlayback;
    const cancelled = new Promise((resolve) => { cancelPlayback = resolve; });
    activeAudio = audio;
    cancelActiveAudio = cancelPlayback;
    await audio.play();
    await Promise.race([
      new Promise((resolve, reject) => {
        audio.onended = resolve;
        audio.onerror = () => reject(new Error("音频播放失败"));
      }),
      cancelled,
    ]);
  } catch (error) {
    showToast(error.message || "音频播放失败，请检查声音设备");
  } finally {
    if (runId === audioRunId) {
      activeAudio = null;
      cancelActiveAudio = null;
    }
    button.removeAttribute("aria-busy");
    button.disabled = false;
  }
}

function stageLabel(stage) {
  return {
    review_question: "短复习",
    review_feedback: "短复习 · 补一下",
    review_retry: "短复习 · 换题再试",
    review_completion: "复习完成",
    learning_teaching: "学习内容",
    learning_meaning_contexts: "区分课程义",
    learning_variant: "识别另一说法",
    learning_variant_practice: "识别练习",
    learning_embedded_support: "看懂语法支架",
    group_practice_intro: "准备本组练习",
    group_practice: "本组练习",
    system_map: "系统整理",
    checkpoint_intro: "准备本单元小测",
    unit_checkpoint: "本单元小测",
    recovery_intro: "准备定向补练",
    recovery_teaching: "定向补练",
    recovery_guided: "定向补练",
    recovery_verification: "换题复测",
    completed: "本单元完成",
  }[stage] || "学习中";
}

function setHeaderMode(label = "") {
  headerMode.textContent = label;
  headerMode.hidden = !label;
}

function currentStageLabel() {
  if (["learning_meaning_contexts", "learning_variant", "learning_variant_practice", "learning_embedded_support"].includes(view.stage)) {
    return stageLabel(view.stage);
  }
  if (view.learning_group) {
    if (view.stage === "group_practice" || view.stage === "group_practice_intro") {
      return `第${view.learning_group.group_number}组练习 · ${view.learning_group.title_zh}`;
    }
    return `第${view.learning_group.group_number}组 · ${view.learning_group.title_zh}`;
  }
  return stageLabel(view.stage);
}

function stepState(step) {
  const groups = {
    learning_teaching: 0,
    learning_meaning_contexts: 0,
    learning_variant: 0,
    learning_variant_practice: 0,
    learning_embedded_support: 0,
    group_practice_intro: 0,
    group_practice: 0,
    system_map: 0,
    checkpoint_intro: 1,
    unit_checkpoint: 1,
    recovery_intro: 2,
    recovery_teaching: 2,
    recovery_guided: 2,
    recovery_verification: 2,
    completed: 3,
  };
  if (view.stage === "completed") return "done";
  const current = groups[view.stage] ?? 0;
  if (step < current) return "done";
  if (step === current) return "active";
  return "";
}

function renderShell(content) {
  setHeaderMode("");
  const progress = view.progress || { current: 1, total: 12 };
  const percent = view.stage === "completed" ? 100 : Math.max(4, Math.round((progress.current / progress.total) * 100));
  const groupStatus = view.learning_group
    ? `<div class="group-status"><strong>${view.stage === "group_practice" || view.stage === "group_practice_intro" ? "本组练习" : "本组学习"}</strong><span>${view.learning_group.target_labels_zh.map(escapeHtml).join("・")}</span></div>`
    : "";
  app.innerHTML = `
    <section class="learning-layout">
      <aside class="unit-rail">
        <p class="unit-code">${escapeHtml(view.work_unit.unit_code)}</p>
        <h1>${escapeHtml(view.work_unit.title_zh)}</h1>
        <p class="unit-task">${escapeHtml(view.work_unit.unit_task_brief)}</p>
        <nav class="step-list" aria-label="本单元学习步骤">
          ${["分组学习与边练", "本单元小测", "定向补练（按需）"].map((label, index) => `
            <div class="step ${stepState(index)}">
              <span class="step-number">${stepState(index) === "done" ? icons.check : index + 1}</span>
              <span>${label}</span>
            </div>`).join("")}
        </nav>
      </aside>
      <div class="learning-main">
        <section class="progress-section">
          <div class="progress-meta">
            <strong>${escapeHtml(currentStageLabel())}</strong>
            <span>${progress.current} / ${progress.total}</span>
          </div>
          <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
            <span style="width:${percent}%"></span>
          </div>
        </section>
        ${groupStatus}
        ${content}
        <p class="session-note">学习进度已自动保存</p>
      </div>
    </section>`;
  bindCommonEvents();
  app.focus({ preventScroll: true });
}

function renderReviewShell(content) {
  const progress = view.review || { position: 1, total: 1 };
  const percent = view.stage === "review_completion"
    ? 100
    : Math.max(6, Math.round((progress.position / progress.total) * 100));
  setHeaderMode(stageLabel(view.stage));
  app.innerHTML = `
    <section class="review-layout">
      <div class="review-heading">
        <div>
          <p>${escapeHtml(progress.source_unit_code || view.work_unit.unit_code)} · ${escapeHtml(progress.source_unit_title_zh)}</p>
          <h1>今天先复习 ${progress.total} 项</h1>
        </div>
        <strong>${progress.position} / ${progress.total}</strong>
      </div>
      <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
        <span style="width:${percent}%"></span>
      </div>
      ${content}
      <p class="session-note">复习进度已自动保存</p>
    </section>`;
  app.focus({ preventScroll: true });
}

function renderBoundary(text = view.boundary_notice_zh) {
  return `<div class="boundary-note">${icons.info}<span>${escapeHtml(text)}</span></div>`;
}

function renderAudioButton(url) {
  if (!url) return "";
  return `<button class="audio-button" type="button" data-audio-url="${escapeHtml(url)}">${icons.speaker}<span>播放</span></button>`;
}

function renderSupportMarkers(markers = []) {
  if (!markers.length) return "";
  return `<p class="support-note">${markers.map((item) => `${escapeHtml(item.surface)}：${escapeHtml(item.reason_zh)}`).join("；")}</p>`;
}

function renderTeaching() {
  selectedOptionId = null;
  const card = view.teaching_card;
  const recovery = view.stage === "recovery_teaching";
  const actions = recovery
    ? `<div class="action-buttons"><button class="primary-button" id="recoveryTeaching" type="button">开始补练 ${icons.arrow}</button></div>`
    : skipLearningPending
      ? `<div class="route-confirmation" role="group" aria-labelledby="skipLearningTitle">
          <strong id="skipLearningTitle">${escapeHtml(view.route_copy.skip_confirm_title_zh)}</strong>
          <div class="route-action-stack">
            <button class="primary-button" id="continueTeaching" type="button">${escapeHtml(view.learning_group.primary_action_zh)} ${icons.arrow}</button>
            <button class="text-action" id="confirmSkipLearning" type="button">${escapeHtml(view.route_copy.skip_confirm_action_zh)}</button>
          </div>
        </div>`
      : `<div class="route-action-stack">
          <button class="primary-button" id="continueTeaching" type="button">${escapeHtml(view.learning_group.primary_action_zh)} ${icons.arrow}</button>
          <button class="text-action" id="skipLearning" type="button">${escapeHtml(view.route_copy.skip_action_zh)}</button>
        </div>`;
  const routeNote = recovery ? `<p>${escapeHtml(view.recovery_reason_zh)}</p>` : "";
  if (card.target_kind === "vocabulary") {
    const context = card.teaching_context;
    renderShell(`
      <article class="content-card teaching-card">
        <span class="type-label">词汇</span>
        <div class="word-row">
          <div><p class="word-form" lang="ja">${escapeHtml(card.form)}</p><p class="word-reading" lang="ja">${escapeHtml(card.reading)}</p></div>
          ${renderAudioButton(card.word_audio_url)}
        </div>
        <div class="meaning-block"><span>本单元课程义</span><strong>${card.course_meaning_labels_zh.map(escapeHtml).join("／")}</strong></div>
        <div class="teaching-context-row">
          <div class="context-block"><strong lang="ja">${escapeHtml(context.ja)}</strong><span>${escapeHtml(context.zh)}</span>${renderSupportMarkers(context.support_markers)}</div>
          ${renderAudioButton(card.audio_url)}
        </div>
        <div class="card-actions ${recovery ? "" : "route-choice"}">
          ${routeNote}
          ${actions}
        </div>
      </article>`);
  } else {
    const slots = card.six_slots;
    const copy = card.learner_copy;
    renderShell(`
      <article class="content-card grammar-card">
        <span class="type-label">语法</span>
        <h2>${escapeHtml(card.title_zh)}</h2>
        <p class="grammar-explanation">${escapeHtml(copy.explanation_zh)}</p>
        <div class="grammar-pattern">
          <span>先这样记</span>
          <strong lang="ja">${escapeHtml(copy.learner_pattern_zh)}</strong>
        </div>
        <dl class="grammar-grid">
          <div><dt>怎么使用</dt><dd>${escapeHtml(copy.connection_zh)}</dd></div>
          <div><dt>术语不用硬背</dt><dd>${escapeHtml(copy.term_note_zh)}</dd></div>
        </dl>
        <div class="grammar-example">
          <div class="context-block"><strong lang="ja">${escapeHtml(copy.example_ja)}</strong><span>${escapeHtml(copy.example_zh)}</span><p>${escapeHtml(copy.example_explanation_zh)}</p></div>
          ${renderAudioButton(card.audio_url)}
        </div>
        <div class="contrast-note"><strong>注意</strong><span>${escapeHtml(copy.error_or_contrast_zh)}</span></div>
        <div class="card-actions ${recovery ? "" : "route-choice"}">
          ${routeNote}
          ${actions}
        </div>
      </article>`);
  }
  if (recovery) {
    document.querySelector("#recoveryTeaching").addEventListener("click", () => sendAction({
      action: "start_recovery_guided",
      asset_id: card.teaching_card_id,
    }));
  } else {
    document.querySelector("#continueTeaching").addEventListener("click", () => sendAction({
      action: "continue_learning",
      asset_id: card.teaching_card_id,
    }));
    const skipLearning = document.querySelector("#skipLearning");
    if (skipLearning) skipLearning.addEventListener("click", () => {
      skipLearningPending = true;
      renderTeaching();
      document.querySelector("#continueTeaching")?.focus();
      announce("请确认是否跳过这一项的学习");
    });
    const confirmSkipLearning = document.querySelector("#confirmSkipLearning");
    if (confirmSkipLearning) confirmSkipLearning.addEventListener("click", () => sendAction({
      action: "skip_learning",
      asset_id: card.teaching_card_id,
    }));
  }
}

function renderMeaningContexts() {
  selectedOptionId = null;
  const group = view.meaning_group;
  const rows = group.contexts.map((item, index) => `
    <div class="meaning-context-row">
      <div class="meaning-context-number">${index + 1}</div>
      <div class="meaning-context-copy">
        <strong lang="ja">${escapeHtml(item.ja)}</strong>
        <span>${escapeHtml(item.zh)}</span>
        <small>${escapeHtml(item.course_meaning_label_zh)}</small>
      </div>
      ${renderAudioButton(item.audio_url)}
    </div>`).join("");
  renderShell(`
    <article class="content-card meaning-context-card">
      <span class="type-label">多义词</span>
      <div class="extension-word-row">
        <div><p class="word-form" lang="ja">${escapeHtml(group.form)}</p><p class="word-reading" lang="ja">${escapeHtml(group.reading)}</p></div>
        <div class="extension-summary"><span>本课程采用的意思</span><strong>${group.contexts.map((item) => escapeHtml(item.course_meaning_label_zh)).join("；")}</strong></div>
      </div>
      <div class="meaning-context-list">${rows}</div>
      <div class="card-actions extension-actions">
        <p>同一个词按语境理解；两个课程义仍属于同一个学习目标。</p>
        <button class="primary-button" id="continueMeaningContexts" type="button">继续 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#continueMeaningContexts").addEventListener("click", () => sendAction({
    action: "continue_meaning_contexts",
    asset_id: group.primary_target_id,
  }));
}

function renderVariant() {
  selectedOptionId = null;
  const card = view.variant_card;
  renderShell(`
    <article class="content-card variant-card">
      <span class="type-label">识别即可 · 不新增目标</span>
      <div class="variant-heading-row">
        <div><p class="word-form" lang="ja">${escapeHtml(card.form)}</p><p class="word-reading" lang="ja">${escapeHtml(card.reading)}</p></div>
        ${renderAudioButton(card.audio_url)}
      </div>
      <div class="variant-relation"><span>与本课主目标的关系</span><strong>「${escapeHtml(card.primary_form)}」的常见另一种说法</strong></div>
      <div class="variant-example">
        <div class="variant-example-copy"><strong lang="ja">${escapeHtml(card.example_ja)}</strong><span>${escapeHtml(card.example_zh)}</span><small>见到时能认出即可；后面只安排一次识别练习。</small></div>
        ${renderAudioButton(card.example_audio_url)}
      </div>
      <div class="card-actions extension-actions">
        <p>不进入独立小测，也不重复计算学习目标。</p>
        <button class="primary-button" id="continueVariant" type="button">认一下，继续 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#continueVariant").addEventListener("click", () => sendAction({
    action: "continue_variant",
    asset_id: card.variant_card_id,
  }));
}

function renderVariantPractice() {
  const item = view.variant_practice;
  const feedback = view.feedback;
  if (feedback) selectedOptionId = feedback.selected_option_id;
  const primaryReading = item.primary_reading && item.primary_reading !== item.primary_form
    ? `<span lang="ja">${escapeHtml(item.primary_reading)}</span>`
    : "";
  const answerReading = item.answer_reading && item.answer_reading !== item.answer_form
    ? `<span lang="ja">${escapeHtml(item.answer_reading)}</span>`
    : "";
  const answerReveal = feedback ? `
    <div class="variant-answer-reveal">
      <div class="variant-answer-audio">
        <div><small>答案发音</small><strong lang="ja">${escapeHtml(item.answer_form)}</strong>${answerReading}</div>
        ${renderAudioButton(item.audio_url)}
      </div>
      <div class="variant-answer-example">
        <div class="variant-example-copy">
          <small>放回例句</small>
          <strong lang="ja">${escapeHtml(item.example_ja)}</strong>
          ${item.example_zh ? `<span>${escapeHtml(item.example_zh)}</span>` : ""}
        </div>
        ${renderAudioButton(item.example_audio_url)}
      </div>
    </div>` : "";
  renderShell(`
    <article class="content-card practice-card variant-practice-card">
      <div class="practice-meta"><strong>识别练习</strong><span>不计入独立小测</span></div>
      <h2>${escapeHtml(item.prompt_zh)}</h2>
      <div class="variant-primary-cue"><div><small>本课主目标</small><strong lang="ja">${escapeHtml(item.primary_form)}</strong>${primaryReading}</div><span>先判断它与选项的关系</span></div>
      ${renderOptions(item.options, feedback)}
      ${feedback ? `<div class="feedback ${feedback.correct ? "success" : "retry"}">${feedback.correct ? icons.check : icons.info}<span>${feedback.correct ? "回答正确。" : "这次没有选对。"}${escapeHtml(feedback.message_zh)}</span></div>` : ""}
      ${answerReveal}
      <div class="card-actions">
        <p>这道题只检查另一说法、写法或读法的对应关系，不形成新的掌握结论。</p>
        <button class="primary-button" id="variantPracticeAction" type="button" ${!feedback && !selectedOptionId ? "disabled" : ""}>${feedback ? "继续" : "确认答案"} ${icons.arrow}</button>
      </div>
    </article>`);
  if (!feedback) {
    document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
      selectedOptionId = button.dataset.optionId;
      renderVariantPractice();
    }));
  }
  document.querySelector("#variantPracticeAction").addEventListener("click", () => sendAction({
    action: feedback ? "continue_variant_practice" : "submit_variant_practice",
    asset_id: item.variant_practice_item_id,
    ...(feedback ? {} : { option_id: selectedOptionId }),
  }));
}

function renderEmbeddedSupport() {
  selectedOptionId = null;
  const card = view.embedded_support;
  renderShell(`
    <article class="content-card embedded-support-card">
      <span class="type-label">语法支架 · 不单独计目标</span>
      <p class="support-form" lang="ja">${escapeHtml(card.canonical_form)}</p>
      <h2>${escapeHtml(card.title_zh)}</h2>
      <div class="support-purpose"><span>这一步只帮你找到连接位置</span><strong>后续形式接在去掉「ます」之后</strong></div>
      <div class="support-example"><strong lang="ja">出します → 出し</strong><span>${escapeHtml(card.teaching_explanation_zh)}</span><small>这里只先看懂位置，不要求把它当成新的主目标。</small></div>
      <div class="card-actions extension-actions">
        <p>本卡不单独形成掌握证据。</p>
        <button class="primary-button" id="continueEmbeddedSupport" type="button">看懂了，继续 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#continueEmbeddedSupport").addEventListener("click", () => sendAction({
    action: "continue_embedded_support",
    asset_id: card.embedded_support_card_id,
  }));
}

function renderOptions(options, feedback = null) {
  return `<div class="choice-list" role="radiogroup" aria-label="答案选项">${options.map((option) => {
    const selected = selectedOptionId === option.option_id;
    const correct = feedback?.revealed_option_id === option.option_id;
    const wrong = feedback && selected && !correct;
    const classes = ["choice", selected ? "selected" : "", correct ? "correct" : "", wrong ? "wrong" : ""].filter(Boolean).join(" ");
    return `<button class="${classes}" type="button" role="radio" aria-checked="${selected}" data-option-id="${escapeHtml(option.option_id)}" ${feedback ? "disabled" : ""}>
      <span class="choice-key">${escapeHtml(option.option_id)}</span>
      <span>${escapeHtml(option.text)}</span>
      ${correct ? icons.check : ""}
    </button>`;
  }).join("")}</div>`;
}

function renderReviewOptions(options, selectedWrongId = null) {
  return `<div class="choice-list" role="radiogroup" aria-label="答案选项">${options.map((option) => {
    const selected = selectedOptionId === option.option_id;
    const wrong = selectedWrongId === option.option_id;
    const classes = ["choice", selected ? "selected" : "", wrong ? "wrong" : ""].filter(Boolean).join(" ");
    return `<button class="${classes}" type="button" role="radio" aria-checked="${selected}" data-option-id="${escapeHtml(option.option_id)}" ${selectedWrongId ? "disabled" : ""}>
      <span class="choice-key">${escapeHtml(option.option_id)}</span>
      <span>${escapeHtml(option.text)}</span>
    </button>`;
  }).join("")}</div>`;
}

function renderReviewQuestion() {
  const item = view.review_item;
  renderReviewShell(`
    <article class="content-card practice-card review-card">
      <div class="review-instruction"><strong>先自己试一试</strong><span>不用先看讲解，直接选答案。</span></div>
      <h2>${escapeHtml(item.prompt_zh || item.prompt_ja)}</h2>
      ${item.prompt_ja && item.prompt_zh ? `<div class="review-prompt-ja" lang="ja">${escapeHtml(item.prompt_ja)}</div>` : ""}
      ${renderReviewOptions(item.options)}
      <div class="card-actions review-actions">
        <span></span>
        <button class="primary-button" id="reviewAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认答案 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderReviewQuestion();
  }));
  document.querySelector("#reviewAction").addEventListener("click", () => sendAction({
    action: "submit_review",
    asset_id: item.checkpoint_item_id,
    option_id: selectedOptionId,
  }));
}

function renderReviewFeedback() {
  const item = view.review_item;
  const feedback = view.review_feedback;
  selectedOptionId = feedback.selected_option_id;
  renderReviewShell(`
    <article class="content-card practice-card review-card">
      <div class="review-instruction error"><strong>这题没答对</strong><span>先看清这个知识点，再换一道题试。</span></div>
      <h2>${escapeHtml(item.prompt_zh || item.prompt_ja)}</h2>
      ${item.prompt_ja && item.prompt_zh ? `<div class="review-prompt-ja" lang="ja">${escapeHtml(item.prompt_ja)}</div>` : ""}
      ${renderReviewOptions(item.options, feedback.selected_option_id)}
      <div class="review-teaching"><strong lang="ja">${escapeHtml(feedback.teaching.title_ja)}</strong><span>${escapeHtml(feedback.teaching.explanation_zh)}</span></div>
      <div class="card-actions review-actions">
        <span></span>
        <button class="primary-button" id="reviewRetry" type="button">换一道题再试 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#reviewRetry").addEventListener("click", () => sendAction({ action: "start_review_retry" }));
}

function renderReviewRetry() {
  const item = view.review_retry_item;
  renderReviewShell(`
    <article class="content-card practice-card review-card">
      <div class="review-instruction"><strong>换一道题再试</strong><span>这次使用不同的题面。</span></div>
      <h2>${escapeHtml(item.prompt_zh || item.prompt_ja)}</h2>
      ${item.prompt_ja && item.prompt_zh ? `<div class="review-prompt-ja" lang="ja">${escapeHtml(item.prompt_ja)}</div>` : ""}
      ${renderReviewOptions(item.options)}
      <div class="card-actions review-actions">
        <span></span>
        <button class="primary-button" id="reviewRetryAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认答案 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderReviewRetry();
  }));
  document.querySelector("#reviewRetryAction").addEventListener("click", () => sendAction({
    action: "submit_review_retry",
    asset_id: item.verification_item_id,
    option_id: selectedOptionId,
  }));
}

function renderReviewCompletion() {
  selectedOptionId = null;
  const summary = view.review_completion;
  const next = summary.next_work_unit;
  renderReviewShell(`
    <article class="content-card review-completion-card">
      <div class="completion-icon">${icons.check}</div>
      <h2>这一小组复习完成</h2>
      <p>现在可以继续学习新的内容。</p>
      <div class="review-summary-grid">
        <div class="direct"><strong>${summary.direct_correct_count} 项</strong><span>直接答对</span></div>
        <div class="recovered"><strong>${summary.recovered_count} 项</strong><span>补一下后答对</span></div>
        <div><strong>${summary.unresolved_count} 项</strong><span>下次再练</span></div>
      </div>
      ${next ? `<div class="next-unit-card"><span>接下来</span><strong>${escapeHtml(next.unit_code)} · ${escapeHtml(next.title_zh)}</strong></div>` : ""}
      <div class="review-completion-actions">
        ${summary.can_continue_new_learning ? `<button class="outline-button" id="finishReview" type="button">今天先到这里</button><button class="primary-button" id="continueNewLearning" type="button">继续学习 ${escapeHtml(next.unit_code)} ${icons.arrow}</button>` : `<button class="primary-button" id="finishReview" type="button">完成本次复习 ${icons.arrow}</button>`}
      </div>
    </article>`);
  document.querySelector("#finishReview")?.addEventListener("click", () => sendAction({ action: "finish_review" }));
  document.querySelector("#continueNewLearning")?.addEventListener("click", () => sendAction({ action: "continue_new_learning" }));
}

function renderGroupPracticeIntro() {
  const intro = view.group_practice_intro;
  renderShell(`
    <article class="content-card transition-card group-practice-intro">
      <span class="type-label group-complete-label">第${view.learning_group.group_number}组完成</span>
      <h2>${escapeHtml(intro.title_zh)}</h2>
      <p>${escapeHtml(intro.body_zh)}</p>
      <div class="group-practice-summary">
        <span>本组内容</span>
        <strong lang="ja">${intro.target_labels_zh.map(escapeHtml).join("　")}</strong>
      </div>
      <div class="card-actions">
        <p>练习只使用本组内容；可以放心试错。</p>
        <button class="primary-button" id="startGroupPractice" type="button">开始本组练习 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#startGroupPractice").addEventListener("click", () => sendAction({ action: "start_group_practice" }));
}

function renderGuided() {
  const item = view.guided_item;
  const feedback = view.feedback;
  const recovery = view.practice_phase === "targeted_recovery";
  if (feedback) selectedOptionId = feedback.selected_option_id;
  renderShell(`
    <article class="content-card practice-card">
      <div class="practice-meta"><strong>${recovery ? "定向补练" : `第${view.learning_group.group_number}组练习`}</strong><span>${feedback ? "已作答" : "提交前可以修改"}</span></div>
      <h2>${escapeHtml(item.prompt_zh || "选择合适的形式，完成句子。")}</h2>
      <div class="practice-context"><div><strong lang="ja">${escapeHtml(item.context_ja || item.prompt_ja || "")}</strong>${renderSupportMarkers(item.support_markers)}</div>${renderAudioButton(item.audio_url)}</div>
      ${renderOptions(item.options, feedback)}
      ${feedback ? `<div class="feedback ${feedback.correct ? "success" : "retry"}">${feedback.correct ? icons.check : icons.info}<span>${feedback.correct ? "回答正确。" : "这次没有选对。"}${escapeHtml(feedback.message_zh)}</span></div>` : ""}
      <div class="card-actions">
        <p>${recovery ? "练完后会换一道不同的题重新验证。" : "这是本组学习过程，可以放心试错；答完会立即讲解。"}</p>
        <button class="primary-button" id="guidedAction" type="button" ${!feedback && !selectedOptionId ? "disabled" : ""}>${feedback ? (recovery ? "换题复测" : (view.learning_group.is_last_practice_item ? "完成本组练习" : "继续下一题")) : "确认答案"} ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderGuided();
  }));
  document.querySelector("#guidedAction").addEventListener("click", () => {
    if (feedback) {
      selectedOptionId = null;
      sendAction({ action: "continue_guided" });
    } else {
      sendAction({ action: "submit_guided", asset_id: item.practice_item_id, option_id: selectedOptionId });
    }
  });
}

function renderSystemMap() {
  const map = view.system_map;
  const previewTone = map.preview_tone === "amber" ? " tone-amber" : "";
  const cardVariant = map.layout_variant === "dense_reading" ? " dense-reading" : "";
  const columns = map.columns.map((column) => `
    <section class="system-map-column ${escapeHtml(column.accent)}">
      <span class="system-map-column-label">${escapeHtml(column.label_zh)}</span>
      <div class="system-map-items">
        ${column.items.map((item) => `<div class="system-map-item"><strong lang="ja">${escapeHtml(item.form)}</strong><span>${escapeHtml(item.note_zh)}</span></div>`).join("")}
      </div>
    </section>`).join("");
  renderShell(`
    <article class="content-card system-map-card${cardVariant}">
      <div class="system-map-heading">
        <div>
          <span class="type-label">${escapeHtml(map.eyebrow_zh)}</span>
          <h2>${escapeHtml(map.title_zh)}</h2>
          <p>${escapeHtml(map.explanation_zh)}</p>
        </div>
        <strong>${map.position} / ${map.total}</strong>
      </div>
      <div class="system-map-grid columns-${map.columns.length}">${columns}</div>
      <section class="system-map-preview${previewTone}">
        <div><span>${escapeHtml(map.preview_title_zh)}</span><strong lang="ja">${map.preview_items.map(escapeHtml).join("　")}</strong></div>
        <p>${escapeHtml(map.preview_note_zh)}</p>
      </section>
      <div class="card-actions">
        <p>${escapeHtml(map.footer_note_zh || "灰色内容只是预告，不计入本单元已学内容。")}</p>
        <button class="primary-button" id="continueSystemMap" type="button">${map.position === map.total ? "整理完了，去做小测" : "下一张关系图"} ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelector("#continueSystemMap").addEventListener("click", () => sendAction({
    action: "continue_system_map",
    asset_id: map.system_map_snapshot_id,
  }));
}

function renderCheckpointIntro() {
  const intro = view.checkpoint_intro;
  renderShell(`
    <article class="content-card transition-card">
      <span class="type-label">本单元小测</span>
      <h2>${escapeHtml(intro.title_zh)}</h2>
      <p>${escapeHtml(intro.body_zh)}</p>
      <div class="transition-list">
        <div><strong>${intro.item_count} 题</strong><span>覆盖本单元全部知识点</span></div>
        <div><strong>${view.route_summary.skipped_learning_count} 项</strong><span>跳过了学习</span></div>
      </div>
      ${renderBoundary("这里只记录本次小测表现；即使全部答对，后面仍需要复习。")}
      <div class="card-actions"><p>小测过程中不逐题显示答案。</p><button class="primary-button" id="startCheckpoint" type="button">开始本单元小测 ${icons.arrow}</button></div>
    </article>`);
  document.querySelector("#startCheckpoint").addEventListener("click", () => sendAction({ action: "start_checkpoint" }));
}

function renderCheckpoint() {
  const item = view.checkpoint;
  renderShell(`
    <article class="content-card practice-card">
      <div class="practice-meta"><strong>本单元小测</strong><span>提交后进入下一题</span></div>
      <h2>${escapeHtml(item.prompt_zh || item.prompt_ja)}</h2>
      ${item.prompt_ja && item.prompt_zh ? `<div class="practice-context"><strong lang="ja">${escapeHtml(item.prompt_ja)}</strong></div>` : ""}
      ${renderOptions(item.options)}
      <div class="card-actions">
        <p>本阶段使用不同于边练的新情境，并且不逐题显示对错。</p>
        <button class="primary-button" id="checkpointAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认答案 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderCheckpoint();
  }));
  document.querySelector("#checkpointAction").addEventListener("click", () => sendAction({
    action: "submit_checkpoint",
    asset_id: item.checkpoint_item_id,
    option_id: selectedOptionId,
  }));
}

function renderRecoveryIntro() {
  const intro = view.recovery_intro;
  renderShell(`
    <article class="content-card transition-card">
      <span class="type-label">定向补练</span>
      <h2>${escapeHtml(intro.title_zh)}</h2>
      <p>${escapeHtml(intro.body_zh)}</p>
      <div class="transition-list single"><div><strong>${intro.target_count} 项</strong><span>需要快速补练并换题复测</span></div></div>
      ${renderBoundary("答对的内容不会重学；补练后的结果仍只是本次证据。")}
      <div class="card-actions"><p>补练只处理本轮没有答对的知识点。</p><button class="primary-button" id="startRecovery" type="button">开始定向补练 ${icons.arrow}</button></div>
    </article>`);
  document.querySelector("#startRecovery").addEventListener("click", () => sendAction({ action: "start_recovery" }));
}

function renderRecoveryVerification() {
  const item = view.recovery_verification;
  renderShell(`
    <article class="content-card practice-card">
      <div class="practice-meta"><strong>换题复测</strong><span>使用新的题面</span></div>
      <h2>${escapeHtml(item.prompt_zh || item.prompt_ja)}</h2>
      ${item.prompt_ja && item.prompt_zh ? `<div class="practice-context"><strong lang="ja">${escapeHtml(item.prompt_ja)}</strong></div>` : ""}
      ${renderOptions(item.options)}
      <div class="card-actions">
        <p>复测仍不在当前题面公布答案，避免形成答案记忆。</p>
        <button class="primary-button" id="recoveryVerificationAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认复测答案 ${icons.arrow}</button>
      </div>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderRecoveryVerification();
  }));
  document.querySelector("#recoveryVerificationAction").addEventListener("click", () => sendAction({
    action: "submit_recovery_verification",
    asset_id: item.verification_item_id,
    option_id: selectedOptionId,
  }));
}

function renderCompleted() {
  selectedOptionId = null;
  const result = view.completion;
  const summary = result.evidence_summary;
  if (view.session_mode === "delayed_review" || (view.session_mode === "mixed" && !summary.new_learning_completed)) {
    setHeaderMode("复习完成");
    app.innerHTML = `<section class="review-layout final-review-result"><article class="content-card completion-card">
      <div class="completion-icon">${icons.check}</div>
      <h2>本次复习已完成</h2>
      <p class="completion-intro">结果已经保存到学习档案。</p>
      <div class="review-summary-grid">
        <div class="direct"><strong>${summary.direct_correct_count} 项</strong><span>直接答对</span></div>
        <div class="recovered"><strong>${summary.recovered_count} 项</strong><span>补一下后答对</span></div>
        <div><strong>${summary.unresolved_count} 项</strong><span>下次再练</span></div>
      </div>
      ${renderBoundary("一次复习结果不代表已经掌握，后续安排以学习档案为准。")}
      <p class="return-copy">请告诉 WorkBuddy“学完了”，继续安排下一步。</p>
    </article></section>`;
    return;
  }
  renderShell(`
    <article class="content-card completion-card">
      <div class="completion-icon">${icons.check}</div>
      <h2>本次学习已完成</h2>
      <p class="completion-intro">结果已保存。</p>
      <div class="summary-grid">
        <div><strong>${summary.skipped_learning_count} 项</strong><span>跳过了学习</span></div>
        <div><strong>${summary.answered_count} 题</strong><span>本单元小测已提交</span></div>
        <div><strong>${summary.recovery_target_count} 项</strong><span>进行了定向补练</span></div>
        <div><strong>${summary.unresolved_count} 项</strong><span>仍未即时通过</span></div>
      </div>
      ${renderBoundary(`本次有 ${summary.provisional_pass_count} 项在小测或复测中答对，后面仍需复习；这不代表已经掌握。`)}
      <p class="return-copy">请告诉 WorkBuddy“学完了”，继续安排下一步。</p>
    </article>`);
}

function bindCommonEvents() {
  document.querySelectorAll("[data-audio-url]").forEach((button) => button.addEventListener("click", () => playAudio(button.dataset.audioUrl, button)));
}

function render() {
  stopAudio();
  if (!view) return;
  if (view.stage === "review_question") renderReviewQuestion();
  else if (view.stage === "review_feedback") renderReviewFeedback();
  else if (view.stage === "review_retry") renderReviewRetry();
  else if (view.stage === "review_completion") renderReviewCompletion();
  else if (view.stage === "learning_teaching" || view.stage === "recovery_teaching") renderTeaching();
  else if (view.stage === "learning_meaning_contexts") renderMeaningContexts();
  else if (view.stage === "learning_variant") renderVariant();
  else if (view.stage === "learning_variant_practice") renderVariantPractice();
  else if (view.stage === "learning_embedded_support") renderEmbeddedSupport();
  else if (view.stage === "group_practice_intro") renderGroupPracticeIntro();
  else if (view.stage === "group_practice" || view.stage === "recovery_guided") renderGuided();
  else if (view.stage === "system_map") renderSystemMap();
  else if (view.stage === "checkpoint_intro") renderCheckpointIntro();
  else if (view.stage === "unit_checkpoint") renderCheckpoint();
  else if (view.stage === "recovery_intro") renderRecoveryIntro();
  else if (view.stage === "recovery_verification") renderRecoveryVerification();
  else renderCompleted();
}

async function loadSession() {
  if (!sessionId) {
    renderError("请从 WorkBuddy 创建学习会话后，再打开课程链接。当前页面不会擅自创建学习记录。");
    return;
  }
  try {
    const response = await fetch(`/api/practice-sessions/${encodeURIComponent(sessionId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "学习会话不存在");
    view = payload;
    render();
  } catch (error) {
    renderError(error.message);
  }
}

async function sendAction(action) {
  if (busy) return;
  busy = true;
  app.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/practice-sessions/${encodeURIComponent(sessionId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...action, step_token: view.step_token }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "无法保存本次操作");
    view = payload;
    selectedOptionId = null;
    skipLearningPending = false;
    render();
    announce(`已进入${stageLabel(view.stage)}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
    app.removeAttribute("aria-busy");
  }
}

function renderError(message) {
  app.innerHTML = `<section class="error-state"><h1>暂时无法打开本次学习</h1><p>${escapeHtml(message)}</p><button class="outline-button" type="button" id="retryButton">重新读取</button></section>`;
  document.querySelector("#retryButton").addEventListener("click", loadSession);
}

loadSession();
