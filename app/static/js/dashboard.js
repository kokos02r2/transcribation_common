// Глобальный перехватчик для обработки истечения сессии
async function fetchWithAuth(url, options = {}, config = {}) {
  const { throwOnError = true } = config;
  const response = await fetch(url, { ...options, credentials: "include" });
  
  // Если сессия истекла (401 или 403), перенаправляем на логин
  if (response.status === 401 || response.status === 403) {
    console.warn("🔒 Сессия истекла, перенаправляем на логин...");
    window.location.reload();
    return null; // Прерываем обработку
  }
  
  if (!response.ok && throwOnError) {
    throw new Error(`Ошибка запроса: ${response.status}`);
  }
  
  return response;
}

// Инициализация графика
let chart;
const ctx = document.getElementById("minutesChart").getContext("2d");
let currentWebhookToken = "";

function setTokenDisplay(element, text, mode = "muted") {
  if (!element) return;
  element.textContent = text;
  element.classList.remove("token-value--muted", "token-value--error");
  if (mode === "muted") {
    element.classList.add("token-value--muted");
  } else if (mode === "error") {
    element.classList.add("token-value--error");
  }
}

function setCopyWebhookEnabled(enabled) {
  const copyBtn = document.getElementById("copyWebhookTokenBtn");
  if (!copyBtn) return;
  copyBtn.disabled = !enabled;
}

// Функция для получения всех дат в диапазоне
function getDatesInRange(startDate, endDate) {
    const dates = [];
    const currentDate = new Date(startDate);
    const end = new Date(endDate);
    
    while (currentDate <= end) {
        dates.push(new Date(currentDate));
        currentDate.setDate(currentDate.getDate() + 1);
    }
    
    return dates;
}

// Функция для получения первого и последнего дня месяца
function getMonthRange(year, month) {
  const startDate = `${year}-${month}-01`;
  const nextMonth = parseInt(month, 10) === 12 ? 1 : parseInt(month, 10) + 1;
  const nextYear = parseInt(month, 10) === 12 ? parseInt(year, 10) + 1 : year;
  const endDate = `${nextYear}-${String(nextMonth).padStart(2, "0")}-01`;
  return { startDate, endDate };
}

// Функция форматирования даты
function formatDate(dateString) {
  const months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const date = new Date(dateString);
  const day = date.getDate();
  const month = months[date.getMonth()];
  return `${day} ${month}`;
}

// Функция загрузки данных и обновления графика
async function loadStats(startDate, endDate) {
  try {
    const response = await fetchWithAuth("/audio_usage/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        start_date: startDate,
        end_date: endDate,
      }),
    });

    if (!response) return;

    const data = await response.json();
    
    // Получаем все даты в диапазоне
    const allDates = getDatesInRange(startDate, endDate);
    const formattedDates = allDates.map(date => formatDate(date.toISOString().split('T')[0]));
    
    // Инициализируем массивы для всех дат нулями
    const speechTranscriptionMinutes = new Array(allDates.length).fill(0);
    const dailyRevenueInRubles = new Array(allDates.length).fill(0);

    // Заполняем данные из ответа сервера
    Object.entries(data.daily_minutes).forEach(([date, dayData]) => {
        const dateIndex = allDates.findIndex(d => 
            d.toISOString().split('T')[0] === date
        );
        
        if (dateIndex !== -1) {
            let daySpeech = 0;
            let dayRevenue = 0;

            for (const processingType in dayData) {
                const { speech, no_speech } = dayData[processingType];
                daySpeech += speech;

                // Расчет стоимости
                const noSpeechRate = 0.2;
                const transcriptionRate = 0.8;
                const bothRate = 1.1;

                dayRevenue += no_speech * noSpeechRate;
                if (processingType === "both") {
                    dayRevenue += speech * bothRate;
                } else {
                    dayRevenue += speech * transcriptionRate;
                }
            }

            speechTranscriptionMinutes[dateIndex] = daySpeech;
            dailyRevenueInRubles[dateIndex] = dayRevenue;
        }
    });

    // Обновляем статистику
    document.getElementById("speechTranscriptionMinutes").textContent = speechTranscriptionMinutes.reduce((a, b) => a + b, 0).toFixed(2);
    document.getElementById("cost").textContent = dailyRevenueInRubles.reduce((a, b) => a + b, 0).toFixed(2);

    if (chart) chart.destroy();

    // Создаем новый график
    const ctx = document.getElementById("minutesChart").getContext("2d");
    chart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: formattedDates,
        datasets: [
          {
            label: "Минуты с транскрипцией",
            data: speechTranscriptionMinutes,
            backgroundColor: "rgba(54, 162, 235, 0.6)",
            borderColor: "rgba(54, 162, 235, 1)",
            borderWidth: 1,
          },
          {
            label: "Сумма в рублях",
            data: dailyRevenueInRubles,
            backgroundColor: "rgba(255, 159, 64, 0.6)",
            borderColor: "rgba(255, 159, 64, 1)",
            borderWidth: 1,
            type: 'line',
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            title: { display: true, text: "Минуты" },
          },
          y1: {
            beginAtZero: true,
            position: 'right',
            title: { display: true, text: "Рубли" },
            grid: {
              drawOnChartArea: false,
            },
          },
          x: {
            title: { display: false, text: "Дата" },
            ticks: {
              maxRotation: 45,
              minRotation: 45
            }
          },
        },
      },
    });
  } catch (error) {
    console.error("Ошибка:", error);
  }
}

async function loadWebhookToken(showValue = false) {
  const displayEl = document.getElementById("webhookTokenDisplay");
  const response = await fetchWithAuth("/get_webhook_token/", { method: "GET" }, { throwOnError: false });
  if (!response) return;

  if (response.status === 404) {
    currentWebhookToken = "";
    setCopyWebhookEnabled(false);
    setTokenDisplay(displayEl, "Webhook токен не сгенерирован.", "muted");
    return;
  }

  if (!response.ok) {
    currentWebhookToken = "";
    setCopyWebhookEnabled(false);
    setTokenDisplay(displayEl, `Ошибка загрузки webhook токена: ${response.status}`, "error");
    return;
  }

  const data = await response.json();
  currentWebhookToken = data.webhook_token || "";
  setCopyWebhookEnabled(Boolean(currentWebhookToken));

  if (showValue && currentWebhookToken) {
    setTokenDisplay(displayEl, currentWebhookToken, "normal");
    return;
  }

  if (currentWebhookToken) {
    setTokenDisplay(displayEl, "Webhook токен создан. Нажмите «Показать текущий».", "muted");
  } else {
    setTokenDisplay(displayEl, "Webhook токен не сгенерирован.", "muted");
  }
}

async function initTokenControls() {
  const apiDisplayEl = document.getElementById("apiTokenDisplay");
  const webhookDisplayEl = document.getElementById("webhookTokenDisplay");
  const generateApiBtn = document.getElementById("generateApiTokenBtn");
  const deleteApiBtn = document.getElementById("deleteApiTokenBtn");
  const showWebhookBtn = document.getElementById("showWebhookTokenBtn");
  const generateWebhookBtn = document.getElementById("generateWebhookTokenBtn");
  const deleteWebhookBtn = document.getElementById("deleteWebhookTokenBtn");
  const copyWebhookBtn = document.getElementById("copyWebhookTokenBtn");

  if (!apiDisplayEl || !webhookDisplayEl) {
    return;
  }

  setCopyWebhookEnabled(false);
  await loadWebhookToken(false);

  generateApiBtn?.addEventListener("click", async () => {
    const response = await fetchWithAuth("/generate_api_token/", { method: "POST" }, { throwOnError: false });
    if (!response) return;
    if (!response.ok) {
      setTokenDisplay(apiDisplayEl, `Ошибка генерации API токена: ${response.status}`, "error");
      return;
    }
    const data = await response.json();
    const apiToken = data.api_token || "";
    if (!apiToken) {
      setTokenDisplay(apiDisplayEl, "Сервер не вернул API токен.", "error");
      return;
    }
    setTokenDisplay(apiDisplayEl, apiToken, "normal");
  });

  deleteApiBtn?.addEventListener("click", async () => {
    const response = await fetchWithAuth("/delete_api_token/", { method: "DELETE" }, { throwOnError: false });
    if (!response) return;
    if (response.status === 404) {
      setTokenDisplay(apiDisplayEl, "API токен не найден.", "muted");
      return;
    }
    if (!response.ok) {
      setTokenDisplay(apiDisplayEl, `Ошибка удаления API токена: ${response.status}`, "error");
      return;
    }
    setTokenDisplay(apiDisplayEl, "API токен удален. При необходимости сгенерируйте новый.", "muted");
  });

  showWebhookBtn?.addEventListener("click", async () => {
    await loadWebhookToken(true);
  });

  generateWebhookBtn?.addEventListener("click", async () => {
    const response = await fetchWithAuth("/generate_webhook_token/", { method: "POST" }, { throwOnError: false });
    if (!response) return;
    if (!response.ok) {
      setTokenDisplay(webhookDisplayEl, `Ошибка генерации webhook токена: ${response.status}`, "error");
      return;
    }
    const data = await response.json();
    currentWebhookToken = data.webhook_token || "";
    setCopyWebhookEnabled(Boolean(currentWebhookToken));
    if (!currentWebhookToken) {
      setTokenDisplay(webhookDisplayEl, "Сервер не вернул webhook токен.", "error");
      return;
    }
    setTokenDisplay(webhookDisplayEl, currentWebhookToken, "normal");
  });

  deleteWebhookBtn?.addEventListener("click", async () => {
    const response = await fetchWithAuth("/delete_webhook_token/", { method: "DELETE" }, { throwOnError: false });
    if (!response) return;
    if (response.status === 404) {
      currentWebhookToken = "";
      setCopyWebhookEnabled(false);
      setTokenDisplay(webhookDisplayEl, "Webhook токен не найден.", "muted");
      return;
    }
    if (!response.ok) {
      setTokenDisplay(webhookDisplayEl, `Ошибка удаления webhook токена: ${response.status}`, "error");
      return;
    }
    currentWebhookToken = "";
    setCopyWebhookEnabled(false);
    setTokenDisplay(webhookDisplayEl, "Webhook токен удален. При необходимости сгенерируйте новый.", "muted");
  });

  copyWebhookBtn?.addEventListener("click", async () => {
    if (!currentWebhookToken) return;
    try {
      await navigator.clipboard.writeText(currentWebhookToken);
      setTokenDisplay(webhookDisplayEl, "Webhook токен скопирован в буфер обмена.", "muted");
    } catch (error) {
      setTokenDisplay(webhookDisplayEl, "Не удалось скопировать токен. Скопируйте вручную.", "error");
    }
  });
}

// Функция для установки дат от начала до конца текущего месяца
function setDefaultDates() {
    const today = new Date();
    const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
    const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    
    // Форматируем даты в формат YYYY-MM-DD
    const formatDate = (date) => {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    };
    
    document.getElementById('startDate').value = formatDate(firstDay);
    document.getElementById('endDate').value = formatDate(lastDay);
}

// Вызываем функцию при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    setDefaultDates();
    // Получаем даты после их установки
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    loadStats(startDate, endDate);
    initTokenControls();
});

// Обработчик изменения дат
document.getElementById("dateForm").addEventListener("change", (e) => {
  const startDate = document.getElementById("startDate").value;
  const endDate = document.getElementById("endDate").value;
  
  // Проверяем, что конечная дата не раньше начальной
  if (new Date(endDate) < new Date(startDate)) {
    alert("Конечная дата не может быть раньше начальной!");
    return;
  }
  
  loadStats(startDate, endDate);
});

// Обработчик выхода
document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    const response = await fetchWithAuth("/auth/jwt/logout", {
      method: "POST",
    });

    if (!response) return; // Если перенаправлены на логин, выходим

    // Успешный выход
    window.location.replace("/login");
  } catch (error) {
    console.error("Ошибка:", error);
    window.location.replace("/login");
  }
});
