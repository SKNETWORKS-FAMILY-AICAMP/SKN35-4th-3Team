const form = document.querySelector("#chat-form");
const messages = document.querySelector("#messages");
const textarea = document.querySelector("#question");
const fileInput = document.querySelector("#files");
const fileList = document.querySelector("#file-list");
const sendButton = form.querySelector("button[type='submit']");

let selectedFiles = [];

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
    sources.forEach((source) => {
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

  const seenPrecedents = new Set();
  sources.forEach((source) => {
    const precedentKey = source.document_type === "precedent"
      ? source.document_id || source.source_url
      : null;
    if (precedentKey && seenPrecedents.has(precedentKey)) return;
    if (precedentKey) seenPrecedents.add(precedentKey);
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
    sourceList.append(item);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = textarea.value.trim();
  if (!question) return;

  // 입력창을 비우기 전에 전송 데이터 먼저 만들기 (csrf 토큰, question, files 포함)
  const formData = new FormData(form);
  const attachedNames = selectedFiles.map((file) => file.name);

  // 내 말풍선
  const userBubble = addMessage("user");
  userBubble.append(question);
  if (attachedNames.length) {
    const tag = document.createElement("div");
    tag.className = "attach-tag";
    tag.textContent = `📎 ${attachedNames.join(", ")}`;
    userBubble.append(tag);
  }

  // 입력창 초기화
  textarea.value = "";
  autoGrow();
  selectedFiles = [];
  syncFileInput();
  renderFiles();
  sendButton.disabled = true;

  // 답변 말풍선 (로딩 상태)
  const botBubble = addMessage("bot");
  const loading = document.createElement("span");
  loading.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  loading.append("상담 자료를 확인하고 있습니다.");
  botBubble.append(loading);
  scrollToBottom();

  try {
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      body: formData,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
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
  } catch (error) {
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
    scrollToBottom();
    textarea.focus();
  }
});