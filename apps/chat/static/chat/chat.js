const form = document.querySelector("#chat-form");
const messages = document.querySelector("#messages");
const textarea = document.querySelector("#question");
const fileInput = document.querySelector("#files");
const fileList = document.querySelector("#file-list");
const sendButton = form.querySelector("button[type='submit']");
const newChatButton = document.querySelector("#new-chat");
document.querySelectorAll(".example-question").forEach((button) => {
  button.addEventListener("click", () => {
    if (textarea.disabled || sendButton.disabled) return;
    const example = button.dataset.question;
    // Keep any draft the user has already written.
    textarea.value = textarea.value.trim()
      ? `${textarea.value.trimEnd()}\n\n${example}`
      : example;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  });
});
const welcomeMessage = messages.firstElementChild.cloneNode(true);
const helpButton = document.querySelector("#chat-help");
const helpTooltip = document.querySelector("#chat-help-tooltip");
const helpWrap = helpButton.parentElement;
let helpPinned = false;
function showHelp(visible) {
  helpTooltip.hidden = !visible;
  helpButton.setAttribute("aria-expanded", String(visible));
}
helpWrap.addEventListener("mouseenter", () => showHelp(true));
helpWrap.addEventListener("mouseleave", () => {
  if (!helpPinned && !helpWrap.contains(document.activeElement)) showHelp(false);
});
helpButton.addEventListener("focus", () => showHelp(true));
helpWrap.addEventListener("focusout", (event) => {
  if (!helpWrap.contains(event.relatedTarget)) {
    helpPinned = false;
    showHelp(false);
  }
});
helpButton.addEventListener("click", () => {
  helpPinned = !helpPinned;
  showHelp(helpPinned);
});
document.addEventListener("click", (event) => {
  if (!helpWrap.contains(event.target)) {
    helpPinned = false;
    showHelp(false);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    helpPinned = false;
    showHelp(false);
  }
});
const conversationIndex = document.querySelector("#conversation-index-list");
const conversationPreview = document.querySelector("#conversation-preview");
let conversationEntries = [];

function activateConversation(button) {
  conversationEntries.forEach((entry) => {
    if (entry.button === button) entry.button.setAttribute("aria-current", "true");
    else entry.button.removeAttribute("aria-current");
  });
}

function hideConversationPreview() {
  conversationPreview.hidden = true;
}

function addConversationIndex(question, target) {
  const number = conversationEntries.length + 1;
  const summary = Array.from(question.replace(/\s+/g, " ").trim());
  const preview = summary.slice(0, 70).join("") + (summary.length > 70 ? "…" : "");
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "index-button";
  button.textContent = number;
  target.id = `conversation-${number}`;
  button.setAttribute("aria-controls", target.id);
  button.setAttribute("aria-label", `${number}번 대화: ${preview}`);
  button.setAttribute("aria-describedby", "conversation-preview");
  const showPreview = () => {
    conversationPreview.textContent = `${number}. ${preview}`;
    conversationPreview.hidden = false;
    const rect = button.getBoundingClientRect();
    const popup = conversationPreview.getBoundingClientRect();
    conversationPreview.style.left = `${Math.max(12, Math.min(rect.right + 10, window.innerWidth - popup.width - 12))}px`;
    conversationPreview.style.top = `${Math.max(12, Math.min(rect.top, window.innerHeight - popup.height - 12))}px`;
  };
  button.addEventListener("mouseenter", showPreview);
  button.addEventListener("focus", showPreview);
  button.addEventListener("mouseleave", hideConversationPreview);
  button.addEventListener("blur", hideConversationPreview);
  button.addEventListener("click", () => {
    hideConversationPreview();
    activateConversation(button);
    messages.scrollTo({
      top: messages.scrollTop + target.getBoundingClientRect().top - messages.getBoundingClientRect().top - 20,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
    messages.querySelectorAll(".index-target").forEach((node) => node.classList.remove("index-target"));
    target.classList.add("index-target");
  });
  target.addEventListener("animationend", () => target.classList.remove("index-target"));
  item.append(button);
  conversationIndex.append(item);
  conversationEntries.push({ target, button });
}

messages.addEventListener("scroll", () => {
  const top = messages.getBoundingClientRect().top + 32;
  let current = null;
  conversationEntries.forEach((entry) => {
    if (entry.target.getBoundingClientRect().top <= top) current = entry;
  });
  if (!current) current = conversationEntries[0];
  // 마지막 질문이 상단에 닿지 못해도, 스크롤 끝에서는 마지막 대화를 선택합니다.
  if (messages.scrollHeight - messages.clientHeight - messages.scrollTop <= 2) {
    current = conversationEntries[conversationEntries.length - 1];
  }
  activateConversation(current?.button);
}, { passive: true });
window.addEventListener("resize", hideConversationPreview);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideConversationPreview();
});
const progressPanel = document.querySelector(".progress-panel");
const progressDescription = document.querySelector("#progress-description");
const progressSteps = Array.from(document.querySelectorAll(".progress-steps li"));
const answerDialog = document.querySelector("#answer-dialog");
const answerDialogContent = document.querySelector("#answer-dialog-content");
document.querySelector("#close-answer-dialog").addEventListener("click", () => answerDialog.close());
answerDialog.addEventListener("click", (event) => {
  const rect = answerDialog.getBoundingClientRect();
  if (event.target === answerDialog && (
    event.clientX < rect.left || event.clientX > rect.right ||
    event.clientY < rect.top || event.clientY > rect.bottom
  )) answerDialog.close();
});
answerDialog.addEventListener("close", () => answerDialogContent.replaceChildren());

function addDetailsButton(bubble) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "answer-details-button";
  button.textContent = "자세히 보기 >";
  button.addEventListener("click", () => {
    answerDialogContent.replaceChildren(...Array.from(bubble.children)
      .filter((child) => child !== button)
      .map((child) => child.cloneNode(true)));
    answerDialog.showModal();
  });
  bubble.append(button);
}

function setProgress(step, description, failed = false) {
  progressDescription.textContent = description;
  progressPanel.classList.toggle("has-error", failed);
  progressSteps.forEach((item, index) => {
    item.classList.toggle("done", index < step);
    item.classList.toggle("current", index === step);
    if (index === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    item.querySelector(".step-dot").textContent = index < step ? "✓" : String(index + 1);
  });
}

let selectedFiles = [];

newChatButton.addEventListener("click", () => {
  conversationEntries = [];
  conversationIndex.replaceChildren();
  hideConversationPreview();
  setProgress(-1, "질문을 기다리고 있어요.");
  document.querySelector("#attachment-stage-note").textContent = "이미지·PDF를 첨부한 경우";
  messages.replaceChildren(welcomeMessage.cloneNode(true));
  textarea.value = "";
  autoGrow();
  selectedFiles = [];
  syncFileInput();
  renderFiles();
  messages.scrollTop = 0;
  textarea.focus();
});

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}

function addMessage(role) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  wrap.append(bubble);
  messages.append(wrap);
  scrollToBottom();
  return bubble;
}

/* textarea 자동 높이 */
function autoGrow() {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
}
textarea.addEventListener("input", autoGrow);

/* Enter 전송 / Shift+Enter 줄바꿈 (한글 조합 중에는 무시) */
textarea.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

/* 선택한 파일을 input.files에 동기화 → new FormData(form)이 그대로 동작 */
function syncFileInput() {
  const transfer = new DataTransfer();
  selectedFiles.forEach((file) => transfer.items.add(file));
  fileInput.files = transfer.files;
}

function renderFiles() {
  fileList.replaceChildren();
  fileList.hidden = selectedFiles.length === 0;
  selectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    item.append(file.name);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `${file.name} 삭제`);
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      syncFileInput();
      renderFiles();
    });
    item.append(remove);
    fileList.append(item);
  });
}

fileInput.addEventListener("change", () => {
  selectedFiles = selectedFiles.concat(Array.from(fileInput.files));
  syncFileInput();
  renderFiles();
});

function renderSources(sources = []) {
  const block = document.createElement("div");
  block.className = "sources-block";

  const title = document.createElement("p");
  title.className = "sources-title";
  title.textContent = "확인된 출처";
  block.append(title);

  const list = document.createElement("ul");
  list.className = "sources";

  if (!sources.length) {
    const item = document.createElement("li");
    item.textContent = "표시할 출처가 없습니다.";
    list.append(item);
  } else {
    const seen = new Set();
    sources.forEach((source) => {
      // 같은 문서가 여러 조각(chunk)으로 나와도 한 번만 표시
      const key = source.document_id || source.source_url || source.source_name || source.title;
      if (key && seen.has(key)) return;
      if (key) seen.add(key);

      const item = document.createElement("li");
      const label = source.source_name || source.title || source.document_id || "출처";
      if (source.source_url) {
        const link = document.createElement("a");
        link.href = source.source_url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = label;
        item.append(link);
      } else {
        item.textContent = label;
      }
      list.append(item);
    });
  }

  block.append(list);
  return block;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = textarea.value.trim();
  if (!question || sendButton.disabled) return;
  setProgress(0, "질문과 첨부파일을 전송하고 있어요.");

  // 입력창을 비우기 전에 전송 데이터 먼저 만들기 (csrf 토큰, question, files 포함)
  const formData = new FormData(form);
  const attachedFiles = [...selectedFiles];
  document.querySelector("#attachment-stage-note").textContent = attachedFiles.length
    ? `이미지·PDF ${attachedFiles.length}개 확인`
    : "첨부파일 없음 · 분석 생략";

  // 내 말풍선
  const userBubble = addMessage("user");
  addConversationIndex(question, userBubble.parentElement);
  attachedFiles.forEach((file) => {
    if (file.type.startsWith("image/") || /\.(png|jpe?g)$/i.test(file.name)) {
      const image = document.createElement("img");
      const previewUrl = URL.createObjectURL(file);
      image.className = "attachment-image";
      image.alt = file.name;
      image.addEventListener("load", () => {
        URL.revokeObjectURL(previewUrl);
        scrollToBottom();
      }, { once: true });
      image.addEventListener("error", () => {
        URL.revokeObjectURL(previewUrl);
        const fallback = document.createElement("div");
        fallback.className = "attach-tag";
        fallback.textContent = `📎 ${file.name}`;
        image.replaceWith(fallback);
      }, { once: true });
      image.src = previewUrl;
      userBubble.append(image);
      return;
    }
    const tag = document.createElement("div");
    tag.className = "attach-tag";
    tag.textContent = `📎 ${file.name}`;
    userBubble.append(tag);
  });

  const questionText = document.createElement("div");
  questionText.className = "user-question";
  questionText.textContent = question;
  userBubble.append(questionText);

  // 입력창 초기화
  textarea.value = "";
  autoGrow();
  selectedFiles = [];
  syncFileInput();
  renderFiles();
  sendButton.disabled = true;
  newChatButton.disabled = true;

  // 답변 말풍선 (로딩 상태)
  const botBubble = addMessage("bot");
  const loading = document.createElement("span");
  loading.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  loading.append("상담 자료를 확인하고 있습니다.");
  botBubble.append(loading);
  scrollToBottom();

  try {
    setProgress(1, "질문 유형에 맞춰 상담을 처리하고 있어요. 세부 단계는 응답 대기 중입니다.");
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      body: formData,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    setProgress(1, "서버 응답을 받았어요. 답변을 확인하고 있어요.");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "요청 처리에 실패했습니다.");

    botBubble.replaceChildren();

    if (payload.status) {
      const status = document.createElement("p");
      status.className = "status";
      status.textContent = payload.status;
      botBubble.append(status);
    }

    const answer = document.createElement("div");
    answer.className = "answer";
    answer.textContent = payload.answer || "답변이 없습니다.";
    botBubble.append(answer);

    botBubble.append(renderSources(payload.sources || payload.citations));
    if ((payload.sources || payload.citations || []).length > 0) {
      addDetailsButton(botBubble);
    }
    setProgress(3, "답변과 출처를 확인해 보세요.");
  } catch (error) {
    setProgress(-1, "요청을 완료하지 못했어요. 다시 시도해 주세요.", true);
    botBubble.replaceChildren();
    const status = document.createElement("p");
    status.className = "status";
    status.textContent = "오류";
    const message = document.createElement("div");
    message.className = "answer";
    message.textContent = error.message;
    botBubble.append(status, message);
  } finally {
    sendButton.disabled = false;
    newChatButton.disabled = false;
    scrollToBottom();
    textarea.focus();
  }
});
