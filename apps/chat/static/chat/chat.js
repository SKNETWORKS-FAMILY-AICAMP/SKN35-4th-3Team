const form = document.querySelector("#chat-form");
const result = document.querySelector("#result");
const statusBox = document.querySelector("#status");
const answerBox = document.querySelector("#answer");
const sourceList = document.querySelector("#sources");

function renderSources(sources = []) {
  sourceList.replaceChildren();
  if (!sources.length) {
    const item = document.createElement("li");
    item.textContent = "표시할 출처가 없습니다.";
    sourceList.append(item);
    return;
  }
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
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  result.hidden = false;
  statusBox.textContent = "상담 자료를 확인하고 있습니다.";
  answerBox.textContent = "";
  sourceList.replaceChildren();

  try {
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "요청 처리에 실패했습니다.");
    statusBox.textContent = payload.status;
    answerBox.textContent = payload.answer || "답변이 없습니다.";
    renderSources(payload.sources || payload.citations);
  } catch (error) {
    statusBox.textContent = "오류";
    answerBox.textContent = error.message;
    renderSources();
  } finally {
    button.disabled = false;
  }
});
