const app = document.querySelector("#app");
const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session_id");
let selectedOption = null;
let currentPayload = null;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderStimulus(stimulus) {
  if (stimulus.kind === "passage") {
    return `<p class="passage" lang="ja">${escapeHtml(stimulus.text_ja)}</p>`;
  }
  if (stimulus.kind === "memo") {
    return `<section class="document-stimulus" lang="ja">
      <p class="memo-addressee">${escapeHtml(stimulus.addressee_ja)}</p>
      <div class="memo-lines">${stimulus.lines_ja.map(line => `<span>${escapeHtml(line)}</span>`).join("")}</div>
      <p class="memo-sender">${escapeHtml(stimulus.sender_ja)}</p>
    </section>`;
  }
  if (stimulus.kind === "table") {
    return `<section class="document-stimulus" lang="ja">
      <h3 class="document-title">${escapeHtml(stimulus.title_ja)}</h3>
      <table class="document-table">
        <thead><tr>${stimulus.columns_ja.map(value => `<th scope="col">${escapeHtml(value)}</th>`).join("")}</tr></thead>
        <tbody>${stimulus.rows.map(row => `<tr>${row.map(value => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
      ${stimulus.note_ja ? `<p class="document-note">${escapeHtml(stimulus.note_ja)}</p>` : ""}
    </section>`;
  }
  throw new Error("不支持的阅读材料类型");
}

function activeStep(payload) {
  if (payload.stage === "teaching") {
    return Math.min(payload.teaching_index, payload.content.steps.length - 2);
  }
  return payload.content.steps.length - 1;
}

function shell(payload, body) {
  const content = payload.content;
  const progress = Math.max(0, Math.min(100, payload.progress_ratio));
  return `
    <header class="topbar">
      <div class="brand"><span class="brand-mark">あ</span><span>日语起点课程</span></div>
      <div class="save-state">阅读进度保存在本次隔离会话中</div>
    </header>
    <main class="workspace">
      <aside class="sidebar">
        <p class="unit-code">${escapeHtml(content.unit_code)}</p>
        <h1 class="unit-title">${escapeHtml(content.display_title_zh)}</h1>
        <p class="unit-task">${escapeHtml(content.learning_task_zh)}</p>
        <nav class="step-list" aria-label="学习步骤">
          ${content.steps.map((label, index) => `
            <div class="step ${index === activeStep(payload) ? "active" : ""}">
              <span class="step-index">${index + 1}</span>
              <span>${escapeHtml(label)}</span>
            </div>`).join("")}
        </nav>
      </aside>
      <section class="main-panel">
        <div class="progress-meta">
          <span class="stage-label">${escapeHtml(payload.stage_label)}</span>
          <span class="progress-count">${escapeHtml(payload.progress_label)}</span>
        </div>
        <div class="progress-track" aria-hidden="true"><div class="progress-fill" style="width:${progress}%"></div></div>
        ${body}
        <p class="session-note">本包未进入正式激活目录</p>
      </section>
    </main>`;
}

function renderTeaching(payload) {
  const item = payload.content.teaching;
  const finalTeaching = payload.teaching_index + 1 === payload.teaching_count;
  app.innerHTML = shell(payload, `
    <article class="learning-card">
      <span class="type-label">教学 ${payload.teaching_index + 1} / ${payload.teaching_count}</span>
      <h2 class="question">${escapeHtml(item.title_zh)}</h2>
      ${renderStimulus(item.stimulus)}
      <div class="focus-block">
        <p class="focus-label">读法重点</p>
        <p class="focus-value">${escapeHtml(item.reading_focus_zh)}</p>
      </div>
      <div class="context-block">
        <p class="context-title">怎么判断</p>
        <p class="context-copy">${escapeHtml(item.explanation_zh)}</p>
      </div>
      <div class="card-actions">
        <p class="evidence-note">教学内容只记录接触，不形成掌握结论。</p>
        <button class="primary-action" data-action="${finalTeaching ? "begin_practice" : "next_teaching"}">${finalTeaching ? "开始练习" : "下一个"} →</button>
      </div>
    </article>`);
}

function optionMarkup(item, interactive) {
  return item.options.map(option => {
    const selected = selectedOption === option.option_id;
    const tag = interactive ? "button" : "div";
    const attrs = interactive ? `data-option="${escapeHtml(option.option_id)}" role="radio" aria-checked="${selected}"` : "";
    return `<${tag} class="option ${selected ? "selected" : ""}" ${attrs}>
      <span class="option-letter">${escapeHtml(option.option_id)}</span>
      <span>${escapeHtml(option.display_text_ja)}</span>
    </${tag}>`;
  }).join("");
}

function renderPractice(payload) {
  const item = payload.content.practice;
  app.innerHTML = shell(payload, `
    <article class="learning-card">
      <span class="type-label">练习 ${payload.practice_index + 1} / ${payload.practice_count}</span>
      ${renderStimulus(item.stimulus)}
      <h2 class="question">${escapeHtml(item.prompt_zh)}</h2>
      <div class="options" role="radiogroup" aria-label="答案选项">${optionMarkup(item, true)}</div>
      <div class="card-actions">
        <p class="evidence-note">提交前可以修改；答案由本地服务端判定。</p>
        <button class="primary-action" data-action="submit_answer" ${selectedOption ? "" : "disabled"}>确认答案 →</button>
      </div>
    </article>`);
}

function renderFeedback(payload) {
  const item = payload.content.practice;
  const feedback = payload.feedback;
  const finalPractice = payload.practice_index + 1 === payload.practice_count;
  selectedOption = feedback.selected_option_id;
  app.innerHTML = shell(payload, `
    <article class="learning-card">
      <span class="type-label">作答反馈</span>
      ${renderStimulus(item.stimulus)}
      <h2 class="question">${escapeHtml(item.prompt_zh)}</h2>
      <div class="options">${optionMarkup(item, false)}</div>
      <div class="feedback ${feedback.correct ? "correct" : "incorrect"}">
        <p class="feedback-title">${feedback.correct ? "回答正确" : `正确答案是 ${escapeHtml(feedback.correct_option_id)}`}</p>
        <p class="feedback-copy">${escapeHtml(feedback.explanation_zh)}</p>
      </div>
      <div class="card-actions">
        <p class="evidence-note">本次回答只形成练习证据，不形成掌握结论。</p>
        <button class="primary-action" data-action="${finalPractice ? "finish" : "next_practice"}">${finalPractice ? "完成体验" : "下一题"} →</button>
      </div>
    </article>`);
}

function renderCompleted(payload) {
  app.innerHTML = shell(payload, `
    <article class="learning-card">
      <span class="type-label">${escapeHtml(payload.content.unit_code)}完整内容候选</span>
      <p class="passage">本次教学与${payload.practice_count}道练习已完成。</p>
      <div class="context-block">
        <p class="context-title">结果只留在隔离会话中</p>
        <p class="context-copy">这不是课程激活、阶段成绩或掌握结论。</p>
      </div>
    </article>`);
}

function render(payload) {
  currentPayload = payload;
  if (payload.stage === "teaching") renderTeaching(payload);
  else if (payload.stage === "practice") renderPractice(payload);
  else if (payload.stage === "feedback") renderFeedback(payload);
  else renderCompleted(payload);
}

function showError(message) {
  app.innerHTML = `<div class="error-state"><h1>无法打开验收会话</h1><p>${escapeHtml(message)}</p></div>`;
}

async function loadSession() {
  if (!sessionId) throw new Error("链接缺少 session_id");
  const response = await fetch(`/api/practice-sessions/${encodeURIComponent(sessionId)}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "会话读取失败");
  render(payload);
}

async function applyAction(action) {
  const body = {action, action_token: currentPayload.action_token};
  if (action === "submit_answer") body.option_id = selectedOption;
  const response = await fetch(`/api/practice-sessions/${encodeURIComponent(sessionId)}/actions`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "操作失败");
  selectedOption = null;
  render(payload);
}

app.addEventListener("click", async event => {
  const option = event.target.closest("[data-option]");
  if (option && currentPayload?.stage === "practice") {
    selectedOption = option.dataset.option;
    renderPractice(currentPayload);
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  try {
    await applyAction(action);
  } catch (error) {
    showError(error.message);
  }
});

loadSession().catch(error => showError(error.message));
