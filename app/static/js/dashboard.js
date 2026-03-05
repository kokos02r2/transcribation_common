// Глобальный перехватчик для обработки истечения сессии
async function fetchWithAuth(url, options = {}) {
  const response = await fetch(url, { ...options, credentials: "include" });
  
  // Если сессия истекла (401 или 403), перенаправляем на логин
  if (response.status === 401 || response.status === 403) {
    console.warn("🔒 Сессия истекла, перенаправляем на логин...");
    window.location.reload();
    return null; // Прерываем обработку
  }
  
  if (!response.ok) {
    throw new Error(`Ошибка запроса: ${response.status}`);
  }
  
  return response;
}

// Инициализация графика
let chart;
const ctx = document.getElementById("minutesChart").getContext("2d");

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
