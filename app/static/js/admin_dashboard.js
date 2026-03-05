async function fetchWithAuth(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: "include" });
  if (response.status === 401 || response.status === 403) {
    console.warn("🔒 Сессия истекла, перенаправляем на логин...");
    window.location.reload();
    return null;
  }
  if (!response.ok) {
    throw new Error(`Ошибка запроса: ${response.status}`);
  }
  return response;
}

let chart;

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

function formatDate(dateString) {
  const months = [
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
  ];
  const date = new Date(dateString);
  const day = date.getDate();
  const month = months[date.getMonth()];
  return `${day} ${month}`;
}

function getSelectedUserEmails() {
  const checked = Array.from(
    document.querySelectorAll("#usersList input[type=checkbox]:checked")
  );
  return checked.map((el) => el.value);
}

function setDefaultDates() {
  const today = new Date();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
  const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);

  const fmt = (date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };

  document.getElementById("startDate").value = fmt(firstDay);
  document.getElementById("endDate").value = fmt(lastDay);
}

function renderUsers(users) {
  const container = document.getElementById("usersList");
  container.innerHTML = "";

  if (!users.length) {
    container.innerHTML = '<div class="text-muted">Нет пользователей</div>';
    return;
  }

  for (const u of users) {
    const div = document.createElement("div");
    div.className = "custom-control custom-checkbox";
    div.innerHTML = `
      <input class="custom-control-input" type="checkbox" id="user_${u.id}" value="${u.email}">
      <label class="custom-control-label" for="user_${u.id}">${u.email}</label>
    `;
    container.appendChild(div);
  }
}

function filterUsersList(query) {
  const q = (query || "").toLowerCase();
  const items = Array.from(document.querySelectorAll("#usersList label"));
  for (const label of items) {
    const match = label.textContent.toLowerCase().includes(q);
    const row = label.closest(".custom-control") || label.closest("div");
    if (row) row.style.display = match ? "" : "none";
  }
}

async function loadUsers() {
  const response = await fetchWithAuth("/admin/users/", { method: "GET" });
  if (!response) return;
  const data = await response.json();
  renderUsers(data.users || []);
}

async function loadStats(startDate, endDate) {
  const selectedUsers = getSelectedUserEmails();

  const response = await fetchWithAuth("/admin/audio_usage/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start_date: startDate,
      end_date: endDate,
      user_emails: selectedUsers.length ? selectedUsers : null,
    }),
  });

  if (!response) return;
  const data = await response.json();

  const allDates = getDatesInRange(startDate, endDate);
  const formattedDates = allDates.map((date) =>
    formatDate(date.toISOString().split("T")[0])
  );

  const speechTranscriptionMinutes = new Array(allDates.length).fill(0);
  const dailyRevenueInRubles = new Array(allDates.length).fill(0);
  const ratesByIndex = new Array(allDates.length).fill(null);
  const tariffChangeDate = "2026-02-19";
  const oldRates = { noSpeechRate: 0.2, transcriptionRate: 0.8, bothRate: 1.1 };
  const newRates = { noSpeechRate: 0.0, transcriptionRate: 0.7, bothRate: 1.1 };
  const getRatesForDay = (dayKey) =>
    dayKey >= tariffChangeDate ? newRates : oldRates;
  const formatRate = (value) =>
    `${Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    })} руб`;

  Object.entries(data.daily_minutes || {}).forEach(([date, dayData]) => {
    const dateIndex = allDates.findIndex(
      (d) => d.toISOString().split("T")[0] === date
    );

    if (dateIndex === -1) return;

    let daySpeech = 0;
    let dayRevenue = 0;
    const dayKey = String(date).split("T")[0];
    const { noSpeechRate, transcriptionRate, bothRate } = getRatesForDay(dayKey);
    ratesByIndex[dateIndex] = { noSpeechRate, transcriptionRate, bothRate };

    for (const processingType in dayData) {
      const { speech, no_speech } = dayData[processingType];
      daySpeech += speech;

      dayRevenue += no_speech * noSpeechRate;
      if (processingType === "both") {
        dayRevenue += speech * bothRate;
      } else {
        dayRevenue += speech * transcriptionRate;
      }
    }

    speechTranscriptionMinutes[dateIndex] = daySpeech;
    dailyRevenueInRubles[dateIndex] = dayRevenue;
  });

  document.getElementById("speechTranscriptionMinutes").textContent =
    speechTranscriptionMinutes.reduce((a, b) => a + b, 0).toFixed(2);
  document.getElementById("cost").textContent = dailyRevenueInRubles
    .reduce((a, b) => a + b, 0)
    .toFixed(2);

  if (chart) chart.destroy();
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
          type: "line",
          yAxisID: "y1",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel(context) {
              if (context.dataset.label !== "Сумма в рублях") return "";
              const rates = ratesByIndex[context.dataIndex];
              if (!rates) return "";
              return [`Минута с речью: ${formatRate(rates.transcriptionRate)}`];
            },
          },
        },
      },
      scales: {
        y: { beginAtZero: true, title: { display: true, text: "Минуты" } },
        y1: {
          beginAtZero: true,
          position: "right",
          title: { display: true, text: "Рубли" },
          grid: { drawOnChartArea: false },
        },
        x: {
          title: { display: false, text: "Дата" },
          ticks: { maxRotation: 45, minRotation: 45 },
        },
      },
    },
  });
}

function bindUiHandlers() {
  document.getElementById("userSearch").addEventListener("input", (e) => {
    filterUsersList(e.target.value);
  });

  document.getElementById("selectAllBtn").addEventListener("click", () => {
    const boxes = Array.from(
      document.querySelectorAll("#usersList input[type=checkbox]")
    );
    for (const b of boxes) b.checked = true;
    const startDate = document.getElementById("startDate").value;
    const endDate = document.getElementById("endDate").value;
    loadStats(startDate, endDate);
  });

  document.getElementById("clearAllBtn").addEventListener("click", () => {
    const boxes = Array.from(
      document.querySelectorAll("#usersList input[type=checkbox]")
    );
    for (const b of boxes) b.checked = false;
    const startDate = document.getElementById("startDate").value;
    const endDate = document.getElementById("endDate").value;
    loadStats(startDate, endDate);
  });

  document
    .getElementById("usersList")
    .addEventListener("change", async () => {
      const startDate = document.getElementById("startDate").value;
      const endDate = document.getElementById("endDate").value;
      await loadStats(startDate, endDate);
    });

  document.getElementById("dateForm").addEventListener("change", async () => {
    const startDate = document.getElementById("startDate").value;
    const endDate = document.getElementById("endDate").value;
    if (new Date(endDate) < new Date(startDate)) {
      alert("Конечная дата не может быть раньше начальной!");
      return;
    }
    await loadStats(startDate, endDate);
  });

  document.getElementById("logoutBtn").addEventListener("click", async () => {
    try {
      const response = await fetchWithAuth("/auth/jwt/logout", { method: "POST" });
      if (!response) return;
      window.location.replace("/login");
    } catch (error) {
      console.error("Ошибка:", error);
      window.location.replace("/login");
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  setDefaultDates();
  bindUiHandlers();
  await loadUsers();
  const startDate = document.getElementById("startDate").value;
  const endDate = document.getElementById("endDate").value;
  await loadStats(startDate, endDate);
});
