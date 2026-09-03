const app = document.querySelector("#app");
const toast = document.querySelector("#toast");
const liveRegion = document.querySelector("#liveRegion");
const sessionId = new URLSearchParams(window.location.search).get("session_id");

const icons = {
  speaker: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4Z"></path><path d="M16 9.2a4 4 0 0 1 0 5.6M18.6 6.6a7.5 7.5 0 0 1 0 10.8"></path></svg>',
  play: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 5.5 18 12 8 18.5Z"></path></svg>',
  pause: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 6v12M16 6v12"></path></svg>',
};

let view = null;
let selectedOptionId = null;
let activeAudio = null;
let cancelActiveAudio = null;
let audioRunId = 0;
let busy = false;
const isolatedKanaPlaybackRate = 0.8;

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function announce(message) {
  liveRegion.textContent = "";
  requestAnimationFrame(() => { liveRegion.textContent = message; });
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 2600);
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

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function playOneAudio(url, playbackRate = 1, clipStartSeconds = 0, clipEndSeconds = null) {
  const audio = new Audio(url);
  let cancelPlayback;
  const cancelled = new Promise((resolve) => { cancelPlayback = resolve; });
  audio.playbackRate = playbackRate;
  audio.preservesPitch = true;
  activeAudio = audio;
  cancelActiveAudio = cancelPlayback;
  try {
    if (audio.readyState < 1) {
      const metadataReady = await Promise.race([
        new Promise((resolve, reject) => {
          audio.onloadedmetadata = () => resolve(true);
          audio.onerror = () => reject(new Error("音频播放失败"));
        }),
        cancelled.then(() => false),
      ]);
      if (!metadataReady) return;
    }
    const startSeconds = Math.max(0, clipStartSeconds || 0);
    const endSeconds = clipEndSeconds == null
      ? audio.duration
      : Math.min(audio.duration, clipEndSeconds);
    if (startSeconds > 0) audio.currentTime = startSeconds;
    const ended = new Promise((resolve, reject) => {
      audio.onended = () => resolve("ended");
      audio.onerror = () => reject(new Error("音频播放失败"));
    });
    await audio.play();
    if (endSeconds < audio.duration) {
      const segmentDurationMs = Math.max(0, (endSeconds - startSeconds) / playbackRate * 1000);
      const outcome = await Promise.race([
        ended,
        wait(segmentDurationMs).then(() => "segment-ended"),
        cancelled.then(() => "cancelled"),
      ]);
      if (outcome === "cancelled") return;
      if (!audio.ended) {
        audio.pause();
        audio.currentTime = endSeconds;
      }
      return;
    }
    await Promise.race([ended, cancelled]);
  } finally {
    if (activeAudio === audio) {
      activeAudio = null;
      cancelActiveAudio = null;
    }
  }
}

async function playAudio(url, button, playbackRate = 1) {
  stopAudio();
  const runId = audioRunId;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await playOneAudio(url, playbackRate);
  } catch (error) {
    showToast(error.message || "音频播放失败，请检查声音设备");
  } finally {
    if (runId === audioRunId) {
      activeAudio = null;
    }
    button.removeAttribute("aria-busy");
    button.disabled = false;
  }
}

async function playAudioSequence(clips, pauseMs, button, playbackRate = 1, onIndexChange = null) {
  stopAudio();
  const runId = audioRunId;
  const original = button.innerHTML;
  const compactButton = button.classList.contains("sequence-play-button");
  button.disabled = true;
  button.classList.add("is-playing");
  button.setAttribute("aria-label", "正在播放整组发音");
  if (compactButton) button.innerHTML = icons.pause;
  try {
    for (let index = 0; index < clips.length; index += 1) {
      if (runId !== audioRunId) return;
      if (onIndexChange) onIndexChange(index);
      const clip = typeof clips[index] === "string" ? { url: clips[index] } : clips[index];
      await playOneAudio(clip.url, playbackRate, clip.start_seconds, clip.end_seconds);
      if (clip.post_playback_silence_ms) await wait(clip.post_playback_silence_ms);
      if (index < clips.length - 1) await wait(pauseMs);
    }
    if (onIndexChange) onIndexChange(clips.length);
  } catch (error) {
    showToast(error.message || "音频播放失败，请检查声音设备");
  } finally {
    if (runId === audioRunId) {
      activeAudio = null;
    }
    button.classList.remove("is-playing");
    button.setAttribute("aria-label", "播放整组发音");
    button.disabled = false;
    if (compactButton) button.innerHTML = original;
  }
}

function stageCopy() {
  if (view.stage === "foundation_single_kana_learning") {
    return `第${view.micro_batch.batch_number}组 · 逐个学习 · ${view.single_kana.display_ja}`;
  }
  if (view.stage === "foundation_batch_intro" || view.stage === "foundation_batch_practice") {
    if (view.engine_profile === "foundation_contextual_pattern_checkpoint_v1") {
      return `第${view.micro_batch.batch_number}组 · ${view.stage === "foundation_batch_intro" ? "情境学习" : "规则辨认"}`;
    }
    const mode = view.practice_item?.response_mode === "single_choice_audio" ? "看字选音" : "听音选字";
    return `第${view.micro_batch.batch_number}组 · ${view.micro_batch.target_displays_ja[0]}行 · ${view.stage === "foundation_batch_intro" ? "整组学习" : mode}`;
  }
  if (view.stage === "foundation_checkpoint_intro" || view.stage === "foundation_checkpoint") {
    return view.engine_profile === "foundation_contextual_pattern_checkpoint_v1"
      ? "阶段小测 · 规则辨认"
      : "阶段小测 · 听音选字";
  }
  if (view.stage === "foundation_targeted_repair") return "错项短补";
  if (view.stage === "foundation_retest") return "换题复测";
  return "本单元完成";
}

function renderShell(content, rangeText = "阶段小测", pageClass = "") {
  const progress = view.progress || { current: 1, total: 1 };
  const percent = view.stage === "completed" ? 100 : Math.max(4, Math.round((progress.current / progress.total) * 100));
  app.innerHTML = `
    <section class="page-shell ${escapeHtml(pageClass)}">
      <section class="unit-heading" aria-labelledby="unit-title">
        <p class="unit-code">${escapeHtml(view.work_unit.unit_code)}</p>
        <h1 id="unit-title">${escapeHtml(view.work_unit.title_zh)}</h1>
      </section>
      <section class="progress-section" aria-label="学习进度">
        <div class="progress-copy"><strong>${escapeHtml(stageCopy())}</strong><span>${progress.current} / ${progress.total}</span></div>
        <progress class="progress-track" aria-label="当前学习进度" value="${percent}" max="100">${percent}%</progress>
      </section>
      <div class="range-strip"><strong>${view.micro_batch ? "本组范围" : "当前阶段"}</strong><span>${escapeHtml(rangeText)}</span></div>
      ${content}
      <p class="session-note">学习进度已自动保存</p>
    </section>`;
  bindAudioButtons();
  app.focus({ preventScroll: true });
}

function renderAudioButton(url, playbackRate = 1) {
  return `<button class="audio-button" type="button" data-audio-url="${escapeHtml(url)}" data-playback-rate="${playbackRate}">${icons.speaker}<span>播放</span></button>`;
}

function renderConsonantSingleKanaShell(content, item) {
  const progress = view.progress || { current: 1, total: 1 };
  const percent = Math.max(4, Math.round((progress.current / progress.total) * 100));
  app.innerHTML = `
    <section class="page-shell single-kana-page-shell consonant-single-kana-page-shell">
      <section class="consonant-learning-heading" aria-labelledby="unit-title">
        <p class="unit-code">${escapeHtml(view.work_unit.unit_code)} · ${escapeHtml(view.micro_batch.target_displays_ja[0])}行</p>
        <div class="consonant-page-title-row">
          <h1 id="unit-title">先听「${escapeHtml(item.display_ja)}」，再把声音和字形连起来</h1>
          <span>${progress.current} / ${progress.total}</span>
        </div>
        <progress class="progress-track" aria-label="当前学习进度" value="${percent}" max="100">${percent}%</progress>
      </section>
      ${content}
      <p class="session-note">学习进度已自动保存</p>
    </section>`;
  bindAudioButtons();
  app.focus({ preventScroll: true });
}

function renderContextLesson() {
  selectedOptionId = null;
  const batch = view.micro_batch;
  const lesson = batch.contextual_lesson;
  const progress = view.progress || { current: 1, total: 1 };
  const percent = Math.max(4, Math.round((progress.current / progress.total) * 100));
  const renderMorae = (word) => word.morae.map((mora, index) => `
    <span class="context-mora ${word.focus_indexes.includes(index) ? "is-focus" : ""}">
      <b lang="ja">${escapeHtml(mora)}</b><small>第${index + 1}拍</small>
    </span>`).join("");
  const rightPanel = lesson.visual_cards ? `
    <p class="context-visual-title">${escapeHtml(lesson.visual_title_zh)}</p>
    <div class="context-symbol-grid" lang="ja">${lesson.visual_cards.map((card) => `
      <div class="context-symbol-card ${card.active ? "is-active" : ""}">
        <strong>${escapeHtml(card.symbol)}</strong><span>${escapeHtml(card.label_zh)}</span>
      </div>`).join("")}</div>` : `
    <p class="context-visual-title">${escapeHtml(lesson.rule_body_zh)}</p>
    <div class="context-pattern-list" lang="ja">${lesson.pattern_rows.map((row, index) => `
      <div class="context-pattern-row ${index === 0 ? "is-active" : ""}">
        <strong>${escapeHtml(row.formula_ja)}</strong><span>${escapeHtml(row.example_ja)}</span>
      </div>`).join("")}</div>
    ${lesson.warning_zh ? `<p class="context-warning">${escapeHtml(lesson.warning_zh)}</p>` : ""}`;

  app.innerHTML = `
    <section class="page-shell context-lesson-page-shell">
      <section class="context-learning-heading" aria-labelledby="unit-title">
        <div><p class="unit-code">${escapeHtml(lesson.eyebrow_zh)}</p><h1 id="unit-title">${escapeHtml(lesson.title_zh)}</h1></div>
        <span>${progress.current} / ${progress.total}</span>
      </section>
      <progress class="progress-track context-progress" aria-label="当前学习进度" value="${percent}" max="100">${percent}%</progress>
      <section class="context-lesson-grid">
        <article class="context-primary-panel">
          <div class="context-panel-heading"><b>1 · 听完整词</b><span>${escapeHtml(lesson.listen_hint_zh)}</span></div>
          <div class="context-word-row">
            <div><strong lang="ja">${escapeHtml(lesson.primary_word.reading_ja)}</strong><span>${escapeHtml(lesson.primary_word.form_ja)} · ${escapeHtml(lesson.primary_word.meaning_zh)}</span></div>
            ${renderAudioButton(lesson.primary_word.audio_url, 1)}
          </div>
          <h2>${escapeHtml(lesson.mora_intro_zh)}</h2>
          <div class="context-mora-row">${renderMorae(lesson.primary_word)}</div>
          <div class="context-rule-note"><span aria-hidden="true">≋</span><p><strong>${escapeHtml(lesson.rule_title_zh)}</strong><small>${escapeHtml(lesson.rule_body_zh)}</small></p></div>
        </article>
        <aside class="context-secondary-panel">
          <div class="context-panel-heading outline"><b>2 · ${lesson.visual_cards ? "看字形" : "看写法"}</b><span>${lesson.visual_cards ? "平假名 / 片假名" : "用词记，不背空规则"}</span></div>
          ${rightPanel}
        </aside>
      </section>
      <section class="context-example-card">
        <div class="context-step"><b>3</b><span>${lesson.visual_cards ? "再找一次" : "再听一词"}</span></div>
        <div class="context-example-copy"><p>听「${escapeHtml(lesson.secondary_word.reading_ja)}」，找出刚学过的规则。</p><strong lang="ja">${lesson.secondary_word.morae.map(escapeHtml).join(" ｜ ")}　${escapeHtml(lesson.secondary_word.form_ja)} · ${escapeHtml(lesson.secondary_word.meaning_zh)}</strong></div>
        ${renderAudioButton(lesson.secondary_word.audio_url, 1)}
      </section>
      <button class="primary-button context-lesson-next" id="startBatch" type="button">${escapeHtml(lesson.cta_label_zh)}</button>
      <p class="session-note">学习进度已自动保存</p>
    </section>`;
  bindAudioButtons();
  document.querySelector("#startBatch").addEventListener("click", () => sendAction({ action: "start_micro_batch", asset_id: batch.micro_batch_id }));
  app.focus({ preventScroll: true });
}

function renderBatchIntro() {
  selectedOptionId = null;
  const batch = view.micro_batch;
  if (batch.presentation_kind === "contextual_pattern_lesson") {
    renderContextLesson();
    return;
  }
  if (batch.articulation_support) {
    renderArticulationBatchIntro(batch);
    return;
  }
  renderShell(`
    <article class="content-card">
      <p class="exercise-type">整组学习</p>
      <h2>${escapeHtml(view.batch_intro.title_zh)}</h2>
      <p class="exercise-help">${escapeHtml(view.batch_intro.body_zh)}</p>
      <button class="audio-button" id="playSequence" type="button">${icons.speaker}<span>播放</span></button>
      <p class="audio-hint">可以反复听</p>
      <div class="kana-sequence" lang="ja">${batch.target_displays_ja.map((kana) => `<span>${escapeHtml(kana)}</span>`).join("")}</div>
      <button class="primary-button" id="startBatch" type="button">${batch.has_single_kana_learning ? "逐个学习这5个假名" : "开始本组练习"}</button>
      <p class="feedback-rule">${batch.has_single_kana_learning ? "接下来会逐个看笔顺、发音和一个真实词语。" : "听过整组后，再用声音去辨认写法。"}</p>
    </article>`, batch.target_displays_ja.join("・"));
  document.querySelector("#startBatch").addEventListener("click", () => sendAction({ action: "start_micro_batch", asset_id: batch.micro_batch_id }));
  document.querySelector("#playSequence").addEventListener("click", (event) => playAudioSequence(
    [batch.sequence_audio_url],
    0,
    event.currentTarget,
    batch.sequence_audio_playback_rate ?? 1,
  ));
}

function renderArticulationBatchIntro(batch) {
  const support = batch.articulation_support;
  const kanaNodes = batch.target_displays_ja.map((kana, index) => `
    <span class="listening-kana" data-sequence-kana="${index}">${escapeHtml(kana)}</span>
  `).join("");
  const progressNodes = batch.target_displays_ja.map((kana, index) => `
    <span class="sequence-progress-node" data-sequence-node="${index}" aria-label="${escapeHtml(kana)}"></span>
  `).join("");
  renderShell(`
    <article class="batch-listening-layout">
      <header class="batch-listening-heading">
        <h2>${escapeHtml(support.intro_title_zh)}</h2>
        <p>${escapeHtml(support.intro_body_zh)}</p>
      </header>
      <div class="listening-kana-row" lang="ja">${kanaNodes}</div>
      <div class="sequence-progress" aria-label="整组播放位置">
        <span class="sequence-progress-line" aria-hidden="true"></span>
        <span class="sequence-progress-fill" id="sequenceProgressFill" aria-hidden="true"></span>
        ${progressNodes}
      </div>
      <div class="sequence-player">
        <button class="sequence-play-button" id="playSequence" type="button" aria-label="播放整组发音">${icons.play}</button>
        <strong>播放整组发音</strong>
        <span>可以反复听</span>
      </div>
      <section class="batch-articulation-card" aria-labelledby="batch-articulation-title">
        <img src="${escapeHtml(support.visual_url)}" alt="${escapeHtml(support.alt_zh)}">
        <div>
          <h3 id="batch-articulation-title">${escapeHtml(support.title_zh)}</h3>
          <p>${escapeHtml(support.body_zh)}</p>
        </div>
      </section>
      <button class="primary-button batch-listening-next" id="startBatch" type="button">${escapeHtml(support.cta_label_zh)}</button>
    </article>`, batch.target_displays_ja.join("・"), "batch-listening-page-shell");

  const updateSequenceProgress = (index) => {
    const sequenceLength = batch.target_displays_ja.length;
    if (index >= sequenceLength) {
      document.querySelector("#sequenceProgressFill").style.width = "80%";
      document.querySelectorAll("[data-sequence-kana], [data-sequence-node]").forEach((node) => {
        node.classList.remove("is-active");
        node.classList.add("is-played");
      });
      announce("整组发音播放完成");
      return;
    }
    const denominator = Math.max(1, batch.target_displays_ja.length - 1);
    document.querySelector("#sequenceProgressFill").style.width = `${(index / denominator) * 80}%`;
    document.querySelectorAll("[data-sequence-kana]").forEach((node) => {
      node.classList.toggle("is-active", Number(node.dataset.sequenceKana) === index);
      node.classList.toggle("is-played", Number(node.dataset.sequenceKana) < index);
    });
    document.querySelectorAll("[data-sequence-node]").forEach((node) => {
      node.classList.toggle("is-active", Number(node.dataset.sequenceNode) === index);
      node.classList.toggle("is-played", Number(node.dataset.sequenceNode) < index);
    });
    announce(`正在播放${batch.target_displays_ja[index]}`);
  };

  document.querySelector("#startBatch").addEventListener("click", () => sendAction({ action: "start_micro_batch", asset_id: batch.micro_batch_id }));
  document.querySelector("#playSequence").addEventListener("click", (event) => playAudioSequence(
    [batch.sequence_audio_url],
    0,
    event.currentTarget,
    batch.sequence_audio_playback_rate ?? 1,
    (index) => updateSequenceProgress(index === 0 ? 0 : batch.target_displays_ja.length),
  ));
}

function renderStrokeSvg(item) {
  const paths = item.stroke_paths.map((stroke) => `<path class="stroke-path" data-stroke-number="${stroke.number}" d="${escapeHtml(stroke.d)}"></path>`).join("");
  const labels = item.stroke_labels.map((label) => `<text class="stroke-number" x="${label.x}" y="${label.y}">${escapeHtml(label.text)}</text>`).join("");
  return `<svg class="stroke-svg" viewBox="0 0 109 109" role="img" aria-label="${escapeHtml(item.display_ja)}的${item.stroke_count}画笔顺动画">${paths}${labels}</svg>`;
}

let strokeAnimations = [];

function animateStrokeOrder(rate = 1) {
  strokeAnimations.forEach((animation) => animation.cancel());
  strokeAnimations = [];
  const paths = [...document.querySelectorAll(".stroke-path")];
  let delay = 120;
  paths.forEach((path) => {
    const length = path.getTotalLength();
    path.style.strokeDasharray = `${length}`;
    path.style.strokeDashoffset = `${length}`;
    const animation = path.animate(
      [{ strokeDashoffset: length }, { strokeDashoffset: 0 }],
      { duration: 680 / rate, delay, fill: "forwards", easing: "ease-in-out" },
    );
    strokeAnimations.push(animation);
    delay += 760 / rate;
  });
}

function setupTraceCanvas() {
  const canvas = document.querySelector("#traceCanvas");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const resize = () => {
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const snapshot = canvas.width && canvas.height ? canvas.toDataURL() : null;
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = 7;
    context.strokeStyle = "#1168f5";
    if (snapshot) {
      const image = new Image();
      image.onload = () => context.drawImage(image, 0, 0, rect.width, rect.height);
      image.src = snapshot;
    }
  };
  resize();
  let drawing = false;
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  canvas.addEventListener("pointerdown", (event) => {
    drawing = true;
    canvas.setPointerCapture(event.pointerId);
    const current = point(event);
    context.beginPath();
    context.moveTo(current.x, current.y);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    const current = point(event);
    context.lineTo(current.x, current.y);
    context.stroke();
  });
  canvas.addEventListener("pointerup", () => { drawing = false; });
  canvas.addEventListener("pointercancel", () => { drawing = false; });
  document.querySelector("#clearTrace").addEventListener("click", () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
  });
}

function renderSingleKanaLearning() {
  selectedOptionId = null;
  const item = view.single_kana;
  if (item.presentation_kind === "consonant_without_mouth") {
    renderConsonantSingleKanaLearning(item);
    return;
  }
  const exampleAudioNote = item.example_audio_kind === "sentence"
    ? `<span class="example-audio-script">音频：${escapeHtml(item.example_audio_script_ja)}</span>`
    : "";
  renderShell(`
    <article class="single-kana-card">
      <div class="single-kana-visual-row">
        <section class="pronunciation-panel">
          <div class="panel-heading"><span>发音与口型</span><strong>0.8倍速播放</strong></div>
          <div class="pronunciation-main">
            <div class="single-kana-display" lang="ja">${escapeHtml(item.display_ja)}</div>
            ${renderAudioButton(item.isolated_audio_url, item.isolated_audio_playback_rate)}
          </div>
          <div class="mouth-row">
            <img src="${escapeHtml(item.mouth_visual_url)}" alt="${escapeHtml(item.display_ja)}的口腔发音示意图">
            <p>${escapeHtml(item.pronunciation_hint_zh)}</p>
          </div>
        </section>
        <section class="stroke-learning-panel">
          <div class="panel-heading"><span>写法与笔顺</span><strong>${item.stroke_count}画</strong></div>
          <div class="stroke-stage">${renderStrokeSvg(item)}</div>
          <div class="stroke-controls">
            <button class="secondary-button" id="replayStroke" type="button">重播笔顺</button>
          </div>
          <button class="trace-toggle" id="traceToggle" type="button">可选书写</button>
          <div class="trace-area" id="traceArea" hidden>
            <div class="trace-guide" lang="ja">${escapeHtml(item.display_ja)}</div>
            <canvas id="traceCanvas" aria-label="书写${escapeHtml(item.display_ja)}的练习区"></canvas>
            <button class="text-button" id="clearTrace" type="button">清空重写</button>
          </div>
        </section>
      </div>
      <section class="example-card">
        <p>例词</p>
        <div class="example-word"><strong lang="ja">${escapeHtml(item.example_reading_ja)}</strong><span lang="ja">${escapeHtml(item.example_written_ja)}</span></div>
        <div class="example-meaning">${escapeHtml(item.example_meaning_zh)}</div>
        ${renderAudioButton(item.example_audio_url, 1)}
        ${exampleAudioNote}
      </section>
      <section class="origin-card">
        <p>字形来源</p>
        <div class="origin-flow" lang="ja"><span>${escapeHtml(item.source_kanji)}</span><b>→</b><strong>${escapeHtml(item.display_ja)}</strong></div>
        <small>平假名「${escapeHtml(item.display_ja)}」由汉字「${escapeHtml(item.source_kanji)}」的草书写法逐渐简化而来。观察线条怎样从「${escapeHtml(item.source_kanji)}」变成「${escapeHtml(item.display_ja)}」即可，不要求记住这个汉字。</small>
      </section>
      <button class="primary-button single-kana-next" id="continueSingleKana" type="button">${escapeHtml(item.cta_label_zh)}</button>
      <p class="stroke-credit">笔顺数据：${escapeHtml(item.stroke_attribution.name)}（${escapeHtml(item.stroke_attribution.license)}）</p>
    </article>`, view.micro_batch.target_displays_ja.join("・"), "single-kana-page-shell");
  animateStrokeOrder(1);
  document.querySelector("#replayStroke").addEventListener("click", () => animateStrokeOrder(1));
  document.querySelector("#traceToggle").addEventListener("click", (event) => {
    const area = document.querySelector("#traceArea");
    area.hidden = !area.hidden;
    event.currentTarget.textContent = area.hidden ? "可选书写" : "收起书写区";
    if (!area.hidden) setupTraceCanvas();
  });
  document.querySelector("#continueSingleKana").addEventListener("click", () => sendAction({ action: "continue_single_kana", asset_id: item.target_id }));
}

function renderConsonantSingleKanaLearning(item) {
  const readingRest = item.example_reading_ja.startsWith(item.display_ja)
    ? item.example_reading_ja.slice(item.display_ja.length)
    : item.example_reading_ja;
  renderConsonantSingleKanaShell(`
    <article class="single-kana-card consonant-single-kana-card">
      <div class="single-kana-visual-row consonant-kana-visual-row">
        <section class="pronunciation-panel consonant-pronunciation-panel">
          <div class="consonant-panel-heading">
            <span><b>1</b> 发音</span>
            <small>先听整体，再看声音怎么接起来</small>
          </div>
          <div class="consonant-pronunciation-main">
            <div class="consonant-kana-focus">
              <strong lang="ja">${escapeHtml(item.display_ja)}</strong>
              <span>${escapeHtml(item.romanization)}</span>
            </div>
            <div class="consonant-sound-details">
              <h3>听清 ${escapeHtml(item.sound_onset_ipa)}，马上接「${escapeHtml(item.vowel_kana)}」</h3>
              <p>${escapeHtml(item.pronunciation_hint_zh)}</p>
              <div class="sound-relation" aria-label="${escapeHtml(item.sound_onset_ipa)}加${escapeHtml(item.vowel_kana)}组成${escapeHtml(item.display_ja)}">
                <strong>${escapeHtml(item.sound_onset_ipa)}</strong><span>+</span><b lang="ja">${escapeHtml(item.vowel_kana)}</b><i aria-hidden="true">→</i><em lang="ja">${escapeHtml(item.display_ja)}</em>
              </div>
              <div class="consonant-audio-actions">
                ${renderAudioButton(item.isolated_audio_url, 1)}
                ${renderAudioButton(item.isolated_audio_url, item.isolated_audio_playback_rate)}
              </div>
            </div>
          </div>
        </section>
        <section class="stroke-learning-panel consonant-stroke-panel">
          <div class="consonant-panel-heading stroke-heading">
            <span><b>2</b> 写法与笔顺</span>
            <small>共 ${item.stroke_count} 画</small>
          </div>
          <div class="stroke-stage">${renderStrokeSvg(item)}</div>
          <div class="consonant-stroke-actions">
            <div class="stroke-controls">
              <button class="secondary-button" id="replayStroke" type="button">重播笔顺</button>
            </div>
            <button class="trace-toggle" id="traceToggle" type="button">可选书写</button>
          </div>
          <div class="trace-area" id="traceArea" hidden>
            <div class="trace-guide" lang="ja">${escapeHtml(item.display_ja)}</div>
            <canvas id="traceCanvas" aria-label="书写${escapeHtml(item.display_ja)}的练习区"></canvas>
            <button class="text-button" id="clearTrace" type="button">清空重写</button>
          </div>
        </section>
      </div>
      <section class="consonant-example-card">
        <div class="section-step"><b>3</b><span>放进词里</span></div>
        <div class="consonant-example-content">
          <p>再听一次，确认「${escapeHtml(item.display_ja)}」在词的开头仍然是同一个声音。</p>
          <div class="consonant-example-word"><strong lang="ja">${escapeHtml(item.display_ja)}</strong><b lang="ja">${escapeHtml(readingRest)}</b><span lang="ja">${escapeHtml(item.example_written_ja)}</span><small>${escapeHtml(item.example_meaning_zh)}</small></div>
        </div>
        ${renderAudioButton(item.example_audio_url, 1)}
      </section>
      <section class="consonant-origin-card">
        <div class="origin-label"><small>补充</small><strong>字形来源</strong></div>
        <div class="origin-flow" lang="ja"><span>${escapeHtml(item.source_kanji)}</span><b>→</b><strong>${escapeHtml(item.display_ja)}</strong></div>
        <p>平假名「${escapeHtml(item.display_ja)}」由汉字「${escapeHtml(item.source_kanji)}」的草书写法逐渐简化而来。这里只看线条怎样收束成「${escapeHtml(item.display_ja)}」，不要求记住来源汉字。</p>
      </section>
      <button class="primary-button single-kana-next" id="continueSingleKana" type="button">${escapeHtml(item.cta_label_zh)}</button>
      <p class="stroke-credit">笔顺数据：${escapeHtml(item.stroke_attribution.name)}（${escapeHtml(item.stroke_attribution.license)}）</p>
    </article>`, item);
  animateStrokeOrder(1);
  document.querySelector("#replayStroke").addEventListener("click", () => animateStrokeOrder(1));
  document.querySelector("#traceToggle").addEventListener("click", (event) => {
    const area = document.querySelector("#traceArea");
    area.hidden = !area.hidden;
    event.currentTarget.textContent = area.hidden ? "可选书写" : "收起书写区";
    if (!area.hidden) setupTraceCanvas();
  });
  document.querySelector("#continueSingleKana").addEventListener("click", () => sendAction({ action: "continue_single_kana", asset_id: item.target_id }));
}

function renderChoices(options, feedback) {
  return `<div class="choice-grid" role="radiogroup" aria-label="假名选项">${options.map((option) => {
    const selected = selectedOptionId === option.option_id;
    const wrong = feedback && !feedback.correct && feedback.selected_option_id === option.option_id;
    const correct = feedback && feedback.correct && feedback.selected_option_id === option.option_id;
    const classes = ["choice-button", wrong ? "is-wrong" : "", correct ? "is-correct" : ""].filter(Boolean).join(" ");
    return `<button class="${classes}" type="button" role="radio" aria-checked="${selected}" aria-pressed="${selected}" data-option-id="${escapeHtml(option.option_id)}" ${feedback?.correct ? "disabled" : ""}>${escapeHtml(option.text)}</button>`;
  }).join("")}</div>`;
}

function renderAudioChoices(options, feedback = null) {
  return `<div class="audio-choice-grid" role="radiogroup" aria-label="发音选项">${options.map((option) => {
    const selected = selectedOptionId === option.option_id;
    const wrong = feedback && !feedback.correct && feedback.selected_option_id === option.option_id;
    const correct = feedback && feedback.correct && feedback.selected_option_id === option.option_id;
    const classes = ["audio-choice", wrong ? "is-wrong" : "", correct ? "is-correct" : ""].filter(Boolean).join(" ");
    return `<button class="${classes}" type="button" role="radio" aria-checked="${selected}" aria-pressed="${selected}" data-audio-option-id="${escapeHtml(option.option_id)}" data-audio-url="${escapeHtml(option.audio_url)}" data-playback-rate="${option.playback_rate ?? isolatedKanaPlaybackRate}" ${feedback?.correct ? "disabled" : ""}><span class="option-letter">${escapeHtml(option.option_id)}</span>${icons.speaker}<span>播放</span></button>`;
  }).join("")}</div>`;
}

function renderPractice() {
  const item = view.practice_item;
  const feedback = view.feedback;
  if (feedback?.correct) selectedOptionId = feedback.selected_option_id;
  const isShapeToSound = item.response_mode === "single_choice_audio";
  const isContextualVisual = item.response_mode === "single_choice_visual";
  renderShell(`
    <article class="content-card">
      <p class="exercise-type">${isContextualVisual ? "规则辨认" : isShapeToSound ? "看字选音" : "听音选字"}</p>
      <h2>${isContextualVisual ? escapeHtml(item.prompt_zh) : isShapeToSound ? "看到这个假名，选择对应的发音" : "听一遍，选择对应的假名"}</h2>
      <p class="exercise-help">${isContextualVisual ? "根据刚才的完整词和节拍判断，不需要给特殊音强行配一个孤立发音。" : isShapeToSound ? "三个选项都可以反复试听，再选择最符合的一个。" : "可以重复播放，不需要记住罗马字。"}</p>
      ${isContextualVisual ? "" : isShapeToSound ? `<div class="display-kana" lang="ja">${escapeHtml(item.display_ja)}</div>` : `${renderAudioButton(item.prompt_audio_url, item.prompt_audio_playback_rate ?? isolatedKanaPlaybackRate)}<p class="audio-hint">可以反复听</p>`}
      ${isShapeToSound ? renderAudioChoices(item.options, feedback) : renderChoices(item.options, feedback)}
      ${feedback ? `<div class="feedback ${feedback.correct ? "success" : "retry"}">${escapeHtml(feedback.message_zh)}</div>` : ""}
      <button class="primary-button" id="practiceAction" type="button" ${!feedback?.correct && !selectedOptionId ? "disabled" : ""}>${feedback?.correct ? "下一题" : feedback ? "重新确认" : "确认答案"}</button>
      <p class="feedback-rule">学习练习会立即反馈；阶段检查不会逐题公布答案。</p>
    </article>`, view.micro_batch.target_displays_ja.join("・"));
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderPractice();
  }));
  document.querySelectorAll("[data-audio-option-id]").forEach((button) => button.addEventListener("click", (event) => {
    selectedOptionId = button.dataset.audioOptionId;
    playAudio(button.dataset.audioUrl, button, Number(button.dataset.playbackRate || isolatedKanaPlaybackRate));
    document.querySelectorAll("[data-audio-option-id]").forEach((candidate) => {
      const selected = candidate.dataset.audioOptionId === selectedOptionId;
      candidate.setAttribute("aria-checked", selected);
      candidate.setAttribute("aria-pressed", selected);
    });
    document.querySelector("#practiceAction").disabled = false;
  }));
  document.querySelector("#practiceAction").addEventListener("click", () => {
    if (feedback?.correct) sendAction({ action: "continue_practice" });
    else sendAction({ action: "submit_practice", asset_id: item.item_id, option_id: selectedOptionId });
  });
}

function renderRepair() {
  selectedOptionId = null;
  const repair = view.repair;
  renderShell(`
    <article class="content-card repair-card">
      <p class="exercise-type">错项短补</p>
      <h2>${escapeHtml(repair.title_zh)}</h2>
      <p class="exercise-help">${escapeHtml(repair.body_zh)}</p>
      <div class="repair-grid ${repair.mouth_visual_url ? "has-visual" : ""}">
        ${repair.mouth_visual_url ? `<img class="mouth-visual" src="${escapeHtml(repair.mouth_visual_url)}" alt="${escapeHtml(repair.display_ja)}的发音口腔示意图">` : ""}
        <div class="repair-copy">
          <div class="display-kana compact" lang="ja">${escapeHtml(repair.display_ja)}</div>
          ${repair.presentation_kind === "contextual_visual" ? "" : renderAudioButton(repair.correct_audio_url, repair.audio_playback_rate ?? isolatedKanaPlaybackRate)}
          ${repair.source_kanji ? `<p class="kana-source" lang="ja">${escapeHtml(repair.source_kanji)} → ${escapeHtml(repair.display_ja)}</p>` : ""}
          <p class="repair-hint">${escapeHtml(repair.hint_zh)}</p>
        </div>
      </div>
      <button class="primary-button" id="startRetest" type="button">开始复测</button>
    </article>`, "只补答错项");
  document.querySelector("#startRetest").addEventListener("click", () => sendAction({ action: "start_retest", asset_id: repair.target_id }));
}

function renderRetest() {
  const item = view.retest;
  const isContextualVisual = item.response_mode === "single_choice_visual";
  renderShell(`
    <article class="content-card">
      <p class="exercise-type">换题复测</p>
      <h2>${escapeHtml(item.title_zh)}</h2>
      <p class="exercise-help">${escapeHtml(item.body_zh)}</p>
      ${isContextualVisual ? `<h3 class="context-retest-prompt">${escapeHtml(item.prompt_zh)}</h3>${renderChoices(item.options)}` : `<div class="display-kana" lang="ja">${escapeHtml(item.display_ja)}</div>${renderAudioChoices(item.options)}`}
      <button class="primary-button" id="retestAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认答案</button>
      <p class="feedback-rule">这次结果只作为本轮证据，之后仍会安排间隔复习。</p>
    </article>`, "只测答错项");
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderRetest();
  }));
  document.querySelectorAll("[data-audio-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.audioOptionId;
    playAudio(button.dataset.audioUrl, button, Number(button.dataset.playbackRate || isolatedKanaPlaybackRate));
    document.querySelectorAll("[data-audio-option-id]").forEach((candidate) => {
      const selected = candidate.dataset.audioOptionId === selectedOptionId;
      candidate.setAttribute("aria-checked", selected);
      candidate.setAttribute("aria-pressed", selected);
    });
    document.querySelector("#retestAction").disabled = false;
  }));
  document.querySelector("#retestAction").addEventListener("click", () => sendAction({ action: "submit_retest", asset_id: item.item_id, option_id: selectedOptionId }));
}

function renderCheckpointIntro() {
  selectedOptionId = null;
  const intro = view.checkpoint_intro;
  renderShell(`
    <article class="content-card transition-card">
      <p class="exercise-type">阶段小测</p>
      <h2>${escapeHtml(intro.title_zh)}</h2>
      <p class="exercise-help">${escapeHtml(intro.body_zh)}</p>
      <div class="count-box"><strong>${intro.item_count}题</strong><span>覆盖本单元${intro.item_count}个学习目标</span></div>
      <div class="boundary-note">${escapeHtml(view.boundary_notice_zh)}</div>
      <button class="primary-button" id="startCheckpoint" type="button">开始阶段小测</button>
      <p class="feedback-rule">小测过程中不逐题显示答案。</p>
    </article>`);
  document.querySelector("#startCheckpoint").addEventListener("click", () => sendAction({ action: "start_checkpoint" }));
}

function renderCheckpoint() {
  const item = view.checkpoint;
  const isContextualVisual = item.response_mode === "single_choice_visual";
  renderShell(`
    <article class="content-card">
      <p class="exercise-type">阶段小测</p>
      <h2>${isContextualVisual ? escapeHtml(item.prompt_zh) : "听一遍，选择对应的假名"}</h2>
      <p class="exercise-help">提交后直接进入下一题，本题不会公布对错。</p>
      ${isContextualVisual ? "" : `${renderAudioButton(item.prompt_audio_url, item.prompt_audio_playback_rate ?? isolatedKanaPlaybackRate)}<p class="audio-hint">可以反复听</p>`}
      ${renderChoices(item.options, null)}
      <button class="primary-button" id="checkpointAction" type="button" ${selectedOptionId ? "" : "disabled"}>确认答案</button>
      <p class="feedback-rule">阶段检查不会逐题公布答案。</p>
    </article>`);
  document.querySelectorAll("[data-option-id]").forEach((button) => button.addEventListener("click", () => {
    selectedOptionId = button.dataset.optionId;
    renderCheckpoint();
  }));
  document.querySelector("#checkpointAction").addEventListener("click", () => sendAction({ action: "submit_checkpoint", asset_id: item.item_id, option_id: selectedOptionId }));
}

function renderCompleted() {
  selectedOptionId = null;
  const summary = view.completion.evidence_summary;
  renderShell(`
    <article class="content-card completion-card">
      <div class="completion-mark" aria-hidden="true">✓</div>
      <h2>本次学习已完成</h2>
      <p class="exercise-help">结果已经保存在当前会话中，等待 WorkBuddy 回收。</p>
      <div class="summary-grid">
        <div><strong>${summary.micro_batch_count}组</strong><span>微批次学习</span></div>
        <div><strong>${summary.answered_count}题</strong><span>阶段小测已提交</span></div>
        <div><strong>${summary.provisional_pass_count}项</strong><span>本次小测答对</span></div>
        <div><strong>${summary.unresolved_count}项</strong><span>后续需要再练</span></div>
      </div>
      <div class="boundary-note">一次学习和小测不能证明已经掌握，后面仍会安排复习。</div>
      <p class="return-copy">请告诉 WorkBuddy“学完了”，继续安排下一步。</p>
    </article>`);
}

function bindAudioButtons() {
  document.querySelectorAll("[data-audio-url]:not([data-audio-option-id])").forEach((button) => button.addEventListener("click", () => playAudio(button.dataset.audioUrl, button, Number(button.dataset.playbackRate || 1))));
}

function render() {
  stopAudio();
  if (!view) return;
  if (view.stage === "foundation_batch_intro") renderBatchIntro();
  else if (view.stage === "foundation_single_kana_learning") renderSingleKanaLearning();
  else if (view.stage === "foundation_batch_practice") renderPractice();
  else if (view.stage === "foundation_checkpoint_intro") renderCheckpointIntro();
  else if (view.stage === "foundation_checkpoint") renderCheckpoint();
  else if (view.stage === "foundation_targeted_repair") renderRepair();
  else if (view.stage === "foundation_retest") renderRetest();
  else renderCompleted();
}

async function loadSession() {
  if (!sessionId) {
    renderError("请从 WorkBuddy 创建学习会话后，再打开课程链接。");
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
    render();
    announce("已进入下一步");
  } catch (error) {
    showToast(error.message);
  } finally {
    busy = false;
    app.removeAttribute("aria-busy");
  }
}

function renderError(message) {
  app.innerHTML = `<section class="error-state"><h1>暂时无法打开本次学习</h1><p>${escapeHtml(message)}</p><button class="primary-button" type="button" id="retryButton">重新读取</button></section>`;
  document.querySelector("#retryButton").addEventListener("click", loadSession);
}

loadSession();
