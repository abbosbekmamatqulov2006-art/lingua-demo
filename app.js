/* ==========================================================================
   Lingua — client

   Progress lives here, in localStorage, and is posted to the server only when
   the next set should be tailored to it. There are no accounts and nothing
   about the learner is stored server-side.
   ========================================================================== */

const KEY = "lingua.v1";

const state = {
  level: "A2",
  native: "uz",
  types: [],
  lesson: null,
  questions: [],
  answers: {},
  graded: null,
  report: null,
  rounds: null,
  roundIndex: 0,
  roundScore: 0,
  roundPicked: null,
  chat: [],
  config: null,
  file: null,
};

const store = {
  read() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch { return {}; }
  },
  write(patch) {
    const merged = { ...store.read(), ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(merged)); } catch { /* private mode */ }
    return merged;
  },
};

const $ = (id) => document.getElementById(id);
const escape = (text) => String(text ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* --------------------------------------------------------------- helpers */

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 3200);
}

function show(view) {
  document.querySelectorAll(".view").forEach((node) =>
    node.classList.toggle("active", node.id === `view-${view}`));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function loading(message) {
  $("loading-text").textContent = message;
  show("loading");
}

async function api(path, options = {}) {
  const response = await fetch(`/api/${path}`, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return response.json();
}

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/* Browser speech synthesis: free, offline, and exactly right for a language app. */
function speak(text) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-GB";
  utterance.rate = 0.9;
  speechSynthesis.speak(utterance);
}

const SPEAKER = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/></svg>`;

const speakerFor = (text) =>
  `<button class="speak" data-say="${escape(text)}" aria-label="Listen">${SPEAKER}</button>`;

/* --------------------------------------------------------------- boot */

async function boot() {
  const saved = store.read();
  state.level = saved.level || "A2";
  state.native = saved.native || guessLanguage();

  try {
    state.config = await api("config");
  } catch {
    $("hero-sub").textContent = "Could not reach the server. Refresh to try again.";
    return;
  }
  state.types = state.config.default_types;

  renderLevels();
  renderLanguages();
  renderChips();
  renderStreak();
  renderResume();
  wire();
}

function guessLanguage() {
  const tag = (navigator.language || "en").slice(0, 2).toLowerCase();
  return ["uz", "ru", "en", "de", "fr", "es", "tr", "ar"].includes(tag) ? tag : "uz";
}

function renderLevels() {
  $("level-picker").innerHTML = state.config.levels.map((level) => `
    <button data-level="${level.code}" class="${level.code === state.level ? "on" : ""}">
      ${level.code}<span class="sub">${escape(level.label)}</span>
    </button>`).join("");

  $("level-picker").onclick = (event) => {
    const button = event.target.closest("[data-level]");
    if (!button) return;
    state.level = button.dataset.level;
    store.write({ level: state.level });
    renderLevels();
  };
}

function renderLanguages() {
  $("native-picker").innerHTML = state.config.languages.map((language) =>
    `<option value="${language.code}" ${language.code === state.native ? "selected" : ""}>
      ${escape(language.label)}</option>`).join("");

  $("native-picker").onchange = (event) => {
    state.native = event.target.value;
    store.write({ native: state.native });
  };
}

const SUGGESTIONS = [
  "when do we use should?",
  "present perfect vs past simple",
  "a, an, the",
  "in, on, at — what's the difference?",
  "words for talking about work",
  "gerund or infinitive?",
];

function renderChips() {
  $("topic-chips").innerHTML = SUGGESTIONS.map((topic) =>
    `<button class="chip" data-topic="${escape(topic)}">${escape(topic)}</button>`).join("");

  $("topic-chips").onclick = (event) => {
    const chip = event.target.closest("[data-topic]");
    if (chip) $("topic-input").value = chip.dataset.topic;
  };
}

function renderStreak() {
  const { streak = 0 } = store.read();
  const node = $("streak");
  node.classList.toggle("hidden", streak < 1);
  node.textContent = streak === 1 ? "1 day" : `${streak} day streak`;
}

function renderResume() {
  const { queue = [] } = store.read();
  const today = new Date().toISOString().slice(0, 10);
  const due = queue.filter((item) => (item.due || "9999") <= today);
  if (!due.length) return;

  $("resume-slot").innerHTML = `
    <div class="card" style="margin-top:16px">
      <span class="eyebrow">Waiting for you</span>
      <p style="margin-bottom:14px">${due.length} question${due.length > 1 ? "s" : ""}
      you got wrong before ${due.length > 1 ? "are" : "is"} due for review.</p>
      <button class="btn-ghost" id="review-btn">Review them now</button>
    </div>`;

  $("review-btn").onclick = () => {
    state.lesson = { title: "Review", kind: "review", cefr: state.level,
                     rules: [], common_mistakes: [], vocabulary: [], anchors: [] };
    state.questions = due.map((item) => item.question);
    state.answers = {};
    state.graded = null;
    state.report = null;
    openLesson("practice");
  };
}

/* --------------------------------------------------------------- input */

function wire() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) =>
        t.classList.toggle("active", t === tab));
      document.querySelectorAll(".panel").forEach((panel) =>
        panel.classList.toggle("active", panel.id === `panel-${tab.dataset.panel}`));
      $("start-btn").textContent = { ask: "Teach me this", upload: "Read this page",
                                     paste: "Use this text" }[tab.dataset.panel];
    };
  });

  const dropzone = $("dropzone");
  const fileInput = $("file-input");

  dropzone.onclick = () => fileInput.click();
  dropzone.ondragover = (event) => { event.preventDefault(); dropzone.classList.add("over"); };
  dropzone.ondragleave = () => dropzone.classList.remove("over");
  dropzone.ondrop = (event) => {
    event.preventDefault();
    dropzone.classList.remove("over");
    if (event.dataTransfer.files[0]) acceptFile(event.dataTransfer.files[0]);
  };
  fileInput.onchange = () => fileInput.files[0] && acceptFile(fileInput.files[0]);

  $("start-btn").onclick = start;
  $("topic-input").onkeydown = (event) => { if (event.key === "Enter") start(); };
  $("home-link").onclick = () => show("start");
  $("settings-btn").onclick = () => { show("start"); $("native-picker").focus(); };

  document.addEventListener("click", (event) => {
    const speaker = event.target.closest("[data-say]");
    if (speaker) speak(speaker.dataset.say);
  });
}

function acceptFile(file) {
  if (file.size > 8 * 1024 * 1024) return toast("That file is over 8 MB");
  state.file = file;

  const preview = $("preview");
  if (file.type.startsWith("image/")) {
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
  } else {
    preview.hidden = true;
  }
  $("dropzone").querySelector("strong").textContent = file.name;
}

async function start() {
  const active = document.querySelector(".tab.active").dataset.panel;

  try {
    if (active === "ask") {
      const topic = $("topic-input").value.trim();
      if (topic.length < 2) return toast("Type what you'd like to learn");
      loading("Writing your lesson…");
      const data = await post("lesson/topic",
        { topic, level: state.level, native: state.native });
      state.lesson = data.lesson;

    } else if (active === "upload") {
      if (!state.file) return toast("Choose a file first");
      loading("Reading your page…");
      const form = new FormData();
      form.append("file", state.file);
      form.append("level", state.level);
      form.append("native", state.native);
      const data = await api("lesson/file", { method: "POST", body: form });
      state.lesson = data.lesson;

    } else {
      const text = $("paste-input").value.trim();
      if (text.length < 10) return toast("Paste a little more text");
      loading("Reading your text…");
      const data = await post("lesson/text",
        { text, level: state.level, native: state.native });
      state.lesson = data.lesson;
    }

    state.questions = [];
    state.answers = {};
    state.graded = null;
    state.report = null;
    state.rounds = null;
    state.chat = [];
    openLesson("learn");

  } catch (error) {
    show("start");
    toast(error.message);
  }
}

/* --------------------------------------------------------------- lesson */

function openLesson(section) {
  renderLesson();
  show("lesson");
  switchSection(section || "learn");
}

function switchSection(name) {
  document.querySelectorAll("#lesson-nav button").forEach((button) =>
    button.classList.toggle("on", button.dataset.sec === name));
  document.querySelectorAll(".lesson-sec").forEach((node) =>
    node.hidden = node.id !== `sec-${name}`);

  if (name === "practice") renderPractice();
  if (name === "words") renderWords();
  if (name === "ask") renderChat();
  if (name === "progress") renderProgress();
}

$("lesson-nav").onclick = (event) => {
  const button = event.target.closest("[data-sec]");
  if (button) switchSection(button.dataset.sec);
};

$("to-practice").onclick = () => switchSection("practice");
$("new-lesson").onclick = () => { renderResume(); show("start"); };

function renderLesson() {
  const lesson = state.lesson;
  $("lesson-title").textContent = lesson.title || "Lesson";
  $("lesson-meta").textContent =
    [lesson.kind, lesson.cefr && `level ${lesson.cefr}`].filter(Boolean).join(" · ");

  const parts = [];

  if (lesson.explanation) {
    parts.push(`<div class="card"><div class="prose">${
      escape(lesson.explanation).replace(/\n+/g, "</div><div class='prose'>")
    }</div></div>`);
  }

  if (lesson.rules?.length) {
    parts.push(`<div style="margin-top:26px"><span class="eyebrow">The rules</span>`);
    for (const rule of lesson.rules) {
      const examples = (rule.examples || []).map((example) =>
        `<div class="ex"><span>${escape(example)}</span>${speakerFor(example)}</div>`).join("");
      parts.push(`
        <div class="rule-card">
          <h4>${escape(rule.point || "")}</h4>
          ${rule.form ? `<div class="form">${escape(rule.form)}</div>` : ""}
          ${examples}
          ${rule.note ? `<div class="note">${escape(rule.note)}</div>` : ""}
        </div>`);
    }
    parts.push(`</div>`);
  }

  if (lesson.common_mistakes?.length) {
    parts.push(`<div style="margin-top:22px"><span class="eyebrow">What goes wrong for speakers of your language</span>`);
    for (const item of lesson.common_mistakes) {
      parts.push(`
        <div class="mistake">
          <div class="pair">
            <span class="x">${escape(item.wrong || "")}</span>
            <span class="v">${escape(item.right || "")}</span>
          </div>
          ${item.why ? `<div class="why">${escape(item.why)}</div>` : ""}
        </div>`);
    }
    parts.push(`</div>`);
  }

  $("lesson-body").innerHTML = parts.join("");
}

/* --------------------------------------------------------------- practice */

function renderPractice() {
  const node = $("sec-practice");

  if (!state.questions.length) {
    node.innerHTML = `
      <div class="card">
        <span class="eyebrow">Ready when you are</span>
        <p style="margin-bottom:6px">Questions built only on what this lesson
        covers, at level ${state.level} — and checked before you see them.</p>
        <div class="settings">
          <div class="field">
            <label>How many</label>
            <div class="segmented" id="count-picker">
              ${[5, 8, 12, 16].map((n) =>
                `<button data-count="${n}" class="${n === 8 ? "on" : ""}">${n}</button>`).join("")}
            </div>
          </div>
          <div class="field">
            <label>Exercise types</label>
            <div class="segmented" id="type-picker">
              ${state.config.exercise_types.map((type) =>
                `<button data-type="${type.code}" class="${state.types.includes(type.code) ? "on" : ""}"
                  style="flex:0 1 auto;padding:8px 12px">${escape(type.label)}</button>`).join("")}
            </div>
          </div>
        </div>
        <div class="sticky-actions">
          <button class="btn-primary" id="gen-btn" style="width:100%">Generate exercises</button>
        </div>
      </div>`;

    let count = 8;
    $("count-picker").onclick = (event) => {
      const button = event.target.closest("[data-count]");
      if (!button) return;
      count = Number(button.dataset.count);
      [...$("count-picker").children].forEach((child) =>
        child.classList.toggle("on", child === button));
    };

    $("type-picker").onclick = (event) => {
      const button = event.target.closest("[data-type]");
      if (!button) return;
      const code = button.dataset.type;
      state.types = state.types.includes(code)
        ? state.types.filter((t) => t !== code)
        : [...state.types, code];
      if (!state.types.length) state.types = [code];
      [...$("type-picker").children].forEach((child) =>
        child.classList.toggle("on", state.types.includes(child.dataset.type)));
    };

    $("gen-btn").onclick = () => generate(count);
    return;
  }

  if (!state.graded) return renderQuestions();
  renderResults();
}

async function generate(count) {
  const saved = store.read();
  loading("Writing and checking your questions…");
  try {
    const data = await post("exercises", {
      lesson: state.lesson, level: state.level, native: state.native,
      types: state.types, count, weak: saved.weak || [],
    });
    state.questions = data.questions;
    state.report = data.report;
    state.answers = {};
    state.graded = null;
    show("lesson");
    switchSection("practice");
  } catch (error) {
    show("lesson");
    switchSection("practice");
    $("sec-practice").insertAdjacentHTML("afterbegin",
      `<div class="error-box">${escape(error.message)}</div>`);
  }
}

const LETTERS = "ABCD";

function questionMarkup(question, index, graded) {
  const label = state.config.exercise_types
    .find((type) => type.code === question.type)?.label || question.type;

  const stem = escape(question.prompt).replace(/_{2,}/g, '<span class="blank"></span>');
  const verdict = graded?.results[index];

  let body = "";
  if (question.options) {
    body = `<div class="options">${question.options.map((option, position) => {
      let classes = "option";
      if (graded) {
        const chosen = state.answers[index] === option;
        const isRight = option === question.answer;
        if (isRight) classes += " correct";
        else if (chosen) classes += " incorrect";
      } else if (state.answers[index] === option) {
        classes += " picked";
      }
      return `<button class="${classes}" data-q="${index}" data-option="${escape(option)}"
        ${graded ? "disabled" : ""}>
        <span class="key">${LETTERS[position]}</span><span>${escape(option)}</span></button>`;
    }).join("")}</div>`;
  } else {
    body = `<input type="text" data-q="${index}" placeholder="Your answer"
      value="${escape(state.answers[index] || "")}" ${graded ? "disabled" : ""}
      autocomplete="off" autocapitalize="off" spellcheck="false">`;
  }

  let feedback = "";
  if (graded) {
    const trap = question.trap
      ? `<div class="trap">${escape(question.trap)}</div>` : "";
    if (verdict.correct) {
      feedback = `<div class="verdict right">
        <strong>Correct</strong>${verdict.typo ? ` — small spelling slip: ${escape(verdict.note)}` : ""}
        ${trap}</div>`;
    } else {
      feedback = `<div class="verdict wrong">
        <div>You wrote: ${escape(state.answers[index] || "—")}</div>
        <div class="answer">${escape(question.answer)} ${speakerFor(question.answer)}</div>
        <div class="why">${escape(question.explanation || "")}</div>
        ${trap}</div>`;
    }
  }

  return `<div class="question">
    <div class="q-head">${index + 1} · ${escape(label)}</div>
    <div class="q-stem">${stem}</div>
    ${body}${feedback}</div>`;
}

function renderQuestions() {
  const node = $("sec-practice");
  node.innerHTML = `
    ${state.questions.map((question, index) => questionMarkup(question, index, null)).join("")}
    <div class="sticky-actions">
      <button class="btn-primary" id="check-btn" style="width:100%">Check my answers</button>
    </div>`;

  node.onclick = (event) => {
    const option = event.target.closest("[data-option]");
    if (option) {
      state.answers[Number(option.dataset.q)] = option.dataset.option;
      const siblings = option.parentElement.children;
      [...siblings].forEach((child) => child.classList.toggle("picked", child === option));
    }
  };

  node.oninput = (event) => {
    const field = event.target.closest("input[data-q]");
    if (field) state.answers[Number(field.dataset.q)] = field.value;
  };

  $("check-btn").onclick = check;
}

async function check() {
  const answered = Object.values(state.answers).filter((value) =>
    String(value || "").trim()).length;
  if (!answered) return toast("Answer at least one question first");

  const saved = store.read();
  try {
    const data = await post("grade", {
      questions: state.questions,
      answers: state.answers,
      queue: saved.queue || [],
      skills: saved.skills || {},
    });
    state.graded = data.graded;

    const [streak, day] = nextStreak(saved.lastDay, saved.streak || 0);
    store.write({ queue: data.queue, skills: data.skills, weak: data.weak,
                  streak, lastDay: day });
    renderStreak();
    renderResults();
  } catch (error) {
    toast(error.message);
  }
}

function nextStreak(lastDay, current) {
  const today = new Date();
  const stamp = today.toISOString().slice(0, 10);
  const yesterday = new Date(today.getTime() - 864e5).toISOString().slice(0, 10);
  if (lastDay === stamp) return [current, stamp];
  if (lastDay === yesterday) return [current + 1, stamp];
  return [1, stamp];
}

function renderResults() {
  const graded = state.graded;
  const report = state.report;

  const reportBlock = report ? `
    <details class="report">
      <summary>Quality check — ${report.generated} written, ${report.final} passed</summary>
      <p style="font-size:.84rem;color:var(--muted);margin-top:10px">
        Every question has to be grounded in something this lesson actually
        teaches and fit the level. These were turned away:</p>
      <table>${(report.rejected || []).map((row) =>
        `<tr><td>${escape(row.prompt || "—")}</td><td>${escape(row.reason)}</td></tr>`
      ).join("") || "<tr><td>Nothing rejected this time.</td><td></td></tr>"}</table>
      ${report.revised ? `<p style="font-size:.82rem;color:var(--muted);margin-top:8px">
        ${report.revised} rewritten during review.</p>` : ""}
    </details>` : "";

  $("sec-practice").innerHTML = `
    <div class="card score-card">
      <div class="score-num">${graded.correct}/${graded.total}</div>
      <div class="score-label">${graded.percent}% correct</div>
      <div class="bar"><i style="width:${graded.percent}%"></i></div>
    </div>
    <div style="margin-top:30px">
      ${state.questions.map((question, index) =>
        questionMarkup(question, index, graded)).join("")}
    </div>
    ${reportBlock}
    <div class="btn-row" style="margin-top:24px">
      <button class="btn-primary" id="again-btn">Another set</button>
      <button class="btn-ghost" id="sheet-btn">Download worksheet</button>
      <button class="btn-ghost" id="back-lesson">Back to the lesson</button>
    </div>`;

  $("again-btn").onclick = () => {
    state.questions = [];
    state.answers = {};
    state.graded = null;
    renderPractice();
  };
  $("back-lesson").onclick = () => switchSection("learn");
  $("sheet-btn").onclick = downloadWorksheet;
}

async function downloadWorksheet() {
  try {
    const response = await fetch("/api/worksheet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions: state.questions, lesson: state.lesson }),
    });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = Object.assign(document.createElement("a"),
      { href: url, download: "lingua-worksheet.md" });
    link.click();
    URL.revokeObjectURL(url);
  } catch {
    toast("Could not build the worksheet");
  }
}

/* --------------------------------------------------------------- words */

async function renderWords() {
  const node = $("sec-words");
  const words = state.lesson.vocabulary || [];

  if (words.length < 4) {
    node.innerHTML = `<div class="empty">
      <p>This lesson is grammar, so there's no word list to play with. Ask for
      something like “words for describing people” and the game fills up.</p></div>`;
    return;
  }

  if (!state.rounds) {
    node.innerHTML = `<div class="card game">
      <span class="eyebrow">${words.length} words from this lesson</span>
      <p style="margin:10px 0 20px">Four meanings, one right. Listen, choose, move on.</p>
      <button class="btn-primary" id="play-btn">Start</button></div>`;
    $("play-btn").onclick = async () => {
      try {
        const data = await post("vocabulary/rounds", { vocabulary: words });
        state.rounds = data.rounds;
        state.roundIndex = 0;
        state.roundScore = 0;
        state.roundPicked = null;
        renderWords();
      } catch (error) { toast(error.message); }
    };
    return;
  }

  if (state.roundIndex >= state.rounds.length) {
    node.innerHTML = `<div class="card score-card">
      <div class="score-num">${state.roundScore}/${state.rounds.length}</div>
      <div class="score-label">words known</div>
      <div class="btn-row" style="justify-content:center;margin-top:22px">
        <button class="btn-primary" id="replay">Play again</button>
      </div></div>`;
    $("replay").onclick = () => { state.rounds = null; renderWords(); };
    return;
  }

  const round = state.rounds[state.roundIndex];
  const picked = state.roundPicked;

  node.innerHTML = `
    <div class="bar" style="margin-bottom:22px">
      <i style="width:${(state.roundIndex / state.rounds.length) * 100}%"></i></div>
    <div class="card game">
      <div class="word">${escape(round.word)} ${speakerFor(round.word)}</div>
      <div class="pos">${escape(round.pos || "")}</div>
      <div class="options">
        ${round.options.map((option, position) => {
          let classes = "option";
          if (picked) {
            if (option === round.answer) classes += " correct";
            else if (option === picked) classes += " incorrect";
          }
          return `<button class="${classes}" data-pick="${escape(option)}"
            ${picked ? "disabled" : ""}>
            <span class="key">${LETTERS[position]}</span><span>${escape(option)}</span></button>`;
        }).join("")}
      </div>
      ${picked ? `
        <div style="margin-top:18px;text-align:left">
          ${round.meaning_native ? `<div style="color:var(--muted);font-size:.88rem">
            ${escape(round.meaning_native)}</div>` : ""}
          ${round.example ? `<div class="prose" style="margin-top:8px">
            ${escape(round.example)} ${speakerFor(round.example)}</div>` : ""}
          <button class="btn-primary" id="next-word" style="margin-top:18px;width:100%">
            Next</button>
        </div>` : ""}
    </div>`;

  node.onclick = (event) => {
    const button = event.target.closest("[data-pick]");
    if (button && !state.roundPicked) {
      state.roundPicked = button.dataset.pick;
      if (state.roundPicked === round.answer) state.roundScore += 1;
      renderWords();
      return;
    }
    if (event.target.closest("#next-word")) {
      state.roundIndex += 1;
      state.roundPicked = null;
      renderWords();
    }
  };
}

/* --------------------------------------------------------------- chat */

function renderChat() {
  const node = $("sec-ask");
  node.innerHTML = `
    <div class="card">
      <span class="eyebrow">Ask about this lesson</span>
      <div class="chat-log" id="chat-log">
        ${state.chat.length ? state.chat.map((message) =>
          `<div class="bubble ${message.role}">${escape(message.text)}</div>`).join("")
          : `<p style="color:var(--muted);font-size:.92rem">
             Anything you didn't follow — ask it here, in your own language.</p>`}
      </div>
      <div class="chat-form">
        <input type="text" id="chat-input" placeholder="Why is it 'should go' and not 'should to go'?">
        <button class="btn-primary" id="chat-send">Ask</button>
      </div>
    </div>`;

  const send = async () => {
    const question = $("chat-input").value.trim();
    if (!question) return;
    state.chat.push({ role: "me", text: question });
    $("chat-input").value = "";
    renderChat();
    $("chat-log").insertAdjacentHTML("beforeend",
      `<div class="bubble bot" id="thinking">…</div>`);

    try {
      const data = await post("chat", {
        question, lesson: state.lesson, level: state.level, native: state.native,
      });
      state.chat.push({ role: "bot", text: data.reply });
    } catch (error) {
      state.chat.push({ role: "bot", text: error.message });
    }
    renderChat();
  };

  $("chat-send").onclick = send;
  $("chat-input").onkeydown = (event) => { if (event.key === "Enter") send(); };
}

/* --------------------------------------------------------------- progress */

function renderProgress() {
  const saved = store.read();
  const skills = saved.skills || {};
  const node = $("sec-progress");
  const names = Object.keys(skills);

  if (!names.length) {
    node.innerHTML = `<div class="empty">
      <p>Answer a set of questions and your weak spots start showing up here.</p></div>`;
    return;
  }

  const rows = names.map((name) => {
    const counts = skills[name];
    const accuracy = Math.round(100 * (counts.seen - counts.wrong) / counts.seen);
    return { name, accuracy, seen: counts.seen };
  }).sort((a, b) => a.accuracy - b.accuracy);

  const answered = rows.reduce((total, row) => total + row.seen, 0);
  const today = new Date().toISOString().slice(0, 10);
  const due = (saved.queue || []).filter((item) => (item.due || "9999") <= today).length;

  node.innerHTML = `
    <div class="stat-grid">
      <div class="card stat"><b>${answered}</b><span>answered</span></div>
      <div class="card stat"><b>${saved.streak || 0}</b><span>day streak</span></div>
      <div class="card stat"><b>${due}</b><span>due today</span></div>
    </div>
    <div class="card">
      <span class="eyebrow">Accuracy by area</span>
      ${rows.map((row) => `
        <div class="skill-row">
          <span class="name">${escape(row.name.replace(/_/g, " "))}</span>
          <span class="track"><i class="fill" style="width:${row.accuracy}%;
            background:${row.accuracy < 60 ? "var(--wrong)" :
                        row.accuracy < 85 ? "var(--accent)" : "var(--right)"}"></i></span>
          <span class="pct">${row.accuracy}%</span>
        </div>`).join("")}
      <p style="font-size:.84rem;color:var(--muted);margin-top:14px">
        The lowest bars are what your next set of questions will target.</p>
    </div>
    <div class="btn-row" style="margin-top:18px">
      <button class="btn-quiet" id="clear-btn">Clear my progress</button>
    </div>`;

  $("clear-btn").onclick = () => {
    if (!confirm("Delete everything Lingua has recorded on this device?")) return;
    localStorage.removeItem(KEY);
    renderStreak();
    renderProgress();
    toast("Cleared");
  };
}

boot();
