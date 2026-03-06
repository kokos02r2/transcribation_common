const state = {
  apiToken: "",
  webhookToken: "",
  apiRevealed: false,
  webhookRevealed: false,
  modalToken: "",
};

async function fetchWithAuth(url, options = {}, config = {}) {
  const { throwOnError = true } = config;
  const response = await fetch(url, { ...options, credentials: "include" });

  if (response.status === 401 || response.status === 403) {
    window.location.reload();
    return null;
  }

  if (!response.ok && throwOnError) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response;
}

function maskToken(token) {
  if (!token) return "";
  if (token.length <= 12) return token;
  return `${token.slice(0, 6)}...${token.slice(-4)}`;
}

function setPreview(kind, text, mode = "normal") {
  const preview = document.getElementById(kind === "api" ? "apiPreviewBtn" : "webhookPreviewBtn");
  if (!preview) return;
  preview.textContent = text;
  preview.classList.remove("token-preview--muted", "token-preview--error");
  if (mode === "muted") preview.classList.add("token-preview--muted");
  if (mode === "error") preview.classList.add("token-preview--error");
}

function setMeta(kind, text) {
  const meta = document.getElementById(kind === "api" ? "apiMeta" : "webhookMeta");
  if (!meta) return;
  meta.textContent = text;
}

function setCopyEnabled(kind, enabled) {
  const btn = document.getElementById(kind === "api" ? "copyApiBtn" : "copyWebhookBtn");
  if (!btn) return;
  btn.disabled = !enabled;
}

function syncApiPreview() {
  if (!state.apiToken) {
    setPreview("api", "Токен не сгенерирован", "muted");
    setCopyEnabled("api", false);
    return;
  }
  setCopyEnabled("api", true);
  if (state.apiRevealed) {
    setPreview("api", state.apiToken);
  } else {
    setPreview("api", maskToken(state.apiToken), "muted");
  }
}

function syncWebhookPreview() {
  if (!state.webhookToken) {
    setPreview("webhook", "Webhook токен не сгенерирован", "muted");
    setCopyEnabled("webhook", false);
    return;
  }
  setCopyEnabled("webhook", true);
  if (state.webhookRevealed) {
    setPreview("webhook", state.webhookToken);
  } else {
    setPreview("webhook", maskToken(state.webhookToken), "muted");
  }
}

function showTokenModal({ title, text, token }) {
  state.modalToken = token || "";
  const titleEl = document.getElementById("tokenNoticeTitle");
  const textEl = document.getElementById("tokenNoticeText");
  const valueEl = document.getElementById("tokenNoticeValue");

  if (titleEl) titleEl.textContent = title;
  if (textEl) textEl.textContent = text;
  if (valueEl) valueEl.textContent = token || "Токен не получен";

  $("#tokenNoticeModal").modal("show");
}

async function copyText(value) {
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (error) {
    return false;
  }
}

async function loadWebhookToken() {
  const response = await fetchWithAuth("/get_webhook_token/", { method: "GET" }, { throwOnError: false });
  if (!response) return;

  if (response.status === 404) {
    state.webhookToken = "";
    state.webhookRevealed = false;
    syncWebhookPreview();
    setMeta("webhook", "Текущий токен не создан. Можно сгенерировать новый.");
    return;
  }

  if (!response.ok) {
    setPreview("webhook", `Ошибка загрузки: ${response.status}`, "error");
    setMeta("webhook", "Не удалось получить текущий webhook токен.");
    return;
  }

  const data = await response.json();
  state.webhookToken = data.webhook_token || "";
  state.webhookRevealed = false;
  syncWebhookPreview();
  setMeta("webhook", "Токен загружен. Нажмите на поле токена для полного просмотра.");
}

async function handleGenerateApi() {
  const response = await fetchWithAuth("/generate_api_token/", { method: "POST" }, { throwOnError: false });
  if (!response) return;

  if (!response.ok) {
    setPreview("api", `Ошибка генерации: ${response.status}`, "error");
    setMeta("api", "Не удалось сгенерировать API токен.");
    return;
  }

  const data = await response.json();
  state.apiToken = data.api_token || "";
  state.apiRevealed = false;
  syncApiPreview();
  setMeta("api", "API токен обновлен. Старый токен больше недействителен.");

  if (state.apiToken) {
    showTokenModal({
      title: "API токен сгенерирован",
      text: "Скопируйте токен сейчас. После закрытия окна полный токен больше не отобразится, будет доступна только перегенерация.",
      token: state.apiToken,
    });
  }
}

async function handleDeleteApi() {
  const response = await fetchWithAuth("/delete_api_token/", { method: "DELETE" }, { throwOnError: false });
  if (!response) return;

  if (response.status !== 404 && !response.ok) {
    setPreview("api", `Ошибка удаления: ${response.status}`, "error");
    setMeta("api", "Не удалось удалить API токен.");
    return;
  }

  state.apiToken = "";
  state.apiRevealed = false;
  syncApiPreview();
  setMeta("api", "API токен удален. Можно сгенерировать новый.");
}

async function handleGenerateWebhook() {
  const response = await fetchWithAuth("/generate_webhook_token/", { method: "POST" }, { throwOnError: false });
  if (!response) return;

  if (!response.ok) {
    setPreview("webhook", `Ошибка генерации: ${response.status}`, "error");
    setMeta("webhook", "Не удалось сгенерировать webhook токен.");
    return;
  }

  const data = await response.json();
  state.webhookToken = data.webhook_token || "";
  state.webhookRevealed = false;
  syncWebhookPreview();
  setMeta("webhook", "Webhook токен обновлен. Старый токен больше недействителен.");

  if (state.webhookToken) {
    showTokenModal({
      title: "Webhook токен сгенерирован",
      text: "Рекомендуется сразу скопировать токен и сохранить в безопасном месте.",
      token: state.webhookToken,
    });
  }
}

async function handleDeleteWebhook() {
  const response = await fetchWithAuth("/delete_webhook_token/", { method: "DELETE" }, { throwOnError: false });
  if (!response) return;

  if (response.status !== 404 && !response.ok) {
    setPreview("webhook", `Ошибка удаления: ${response.status}`, "error");
    setMeta("webhook", "Не удалось удалить webhook токен.");
    return;
  }

  state.webhookToken = "";
  state.webhookRevealed = false;
  syncWebhookPreview();
  setMeta("webhook", "Webhook токен удален. Можно сгенерировать новый.");
}

function bindEvents() {
  document.getElementById("generateApiBtn")?.addEventListener("click", handleGenerateApi);
  document.getElementById("deleteApiBtn")?.addEventListener("click", handleDeleteApi);
  document.getElementById("generateWebhookBtn")?.addEventListener("click", handleGenerateWebhook);
  document.getElementById("deleteWebhookBtn")?.addEventListener("click", handleDeleteWebhook);

  document.getElementById("apiPreviewBtn")?.addEventListener("click", () => {
    if (!state.apiToken) return;
    state.apiRevealed = !state.apiRevealed;
    syncApiPreview();
  });

  document.getElementById("webhookPreviewBtn")?.addEventListener("click", () => {
    if (!state.webhookToken) return;
    state.webhookRevealed = !state.webhookRevealed;
    syncWebhookPreview();
  });

  document.getElementById("copyApiBtn")?.addEventListener("click", async () => {
    const ok = await copyText(state.apiToken);
    setMeta("api", ok ? "API токен скопирован." : "Не удалось скопировать API токен.");
  });

  document.getElementById("copyWebhookBtn")?.addEventListener("click", async () => {
    const ok = await copyText(state.webhookToken);
    setMeta("webhook", ok ? "Webhook токен скопирован." : "Не удалось скопировать webhook токен.");
  });

  document.getElementById("copyModalTokenBtn")?.addEventListener("click", async () => {
    await copyText(state.modalToken);
  });

  document.getElementById("logoutBtn")?.addEventListener("click", async () => {
    const response = await fetchWithAuth("/auth/jwt/logout", { method: "POST" }, { throwOnError: false });
    if (!response) return;
    window.location.replace("/login");
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  syncApiPreview();
  syncWebhookPreview();
  bindEvents();
  await loadWebhookToken();
});
