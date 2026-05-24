<div align="center">

# ⚡ Uptime Monitor

**Десктопная утилита мониторинга доступности хостов в реальном времени**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-Qt6-41CD52?style=flat-square&logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Socket](https://img.shields.io/badge/Socket-TCP-FF6B35?style=flat-square)](https://docs.python.org/3/library/socket.html)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?style=flat-square&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

Минималистичное полупрозрачное окно, которое сидит на рабочем столе и показывает статусы хостов. Если хост падает, а окно перекрыто полноэкранным приложением — автоматически всплывает поверх всех окон и показывает только упавшие хосты.

| Режим | Условие | Поведение |
|---|---|---|
| **Нормальный** | Рабочий стол виден | Все хосты, обычное окно |
| **Алерт** | Окно перекрыто + есть оффлайн | Поверх всех окон, только 🔴 упавшие |
| **Рабочий стол** | Переключен вручную (▔) | Всегда позади всех окон, без алертов |

```
┌─────────────────────┐   Signal/Slot    ┌─────────────────────┐
│  ГЛАВНЫЙ ПОТОК      │ ◄─────────────► │  ФОНОВЫЙ ПОТОК      │
│  MainWindow (GUI)   │  host_checked   │  NetworkWorker       │
│  ├─ QTableWidget    │ ◄────────────── │  ├─ QTimer           │
│  ├─ QTimer (UI)     │                 │  └─ socket.connect() │
│  └─ alert / normal  │                 │                      │
└─────────────────────┘                 └─────────────────────┘
```

---

### 1. Установка окружения
```bash
# Клонировать репозиторий
git clone https://github.com/<your-username>/UptimeMonitor.git
cd UptimeMonitor

# Создать виртуальное окружение
python -m venv .venv

# Активировать (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Установить зависимости
pip install -r requirements.txt
```

### 2. Запустить
```bash
python main.py
```

---

## Структура проекта
```
UptimeMonitor/
├── main.py                    ← точка входа, сборка потоков
├── config.py                  ← дефолтные значения (справочно)
├── requirements.txt           ← зависимости (PySide6)
└── src/
    ├── config_manager.py      ← менеджер конфига (AppData/Local)
    ├── network_worker.py      ← фоновый воркер (проверка через socket)
    └── ui/
        ├── main_window.py     ← GUI: таблица, режимы, перетаскивание
        └── settings_dialog.py ← диалог настроек (⚙)
```

---

## Настройка хостов

Конфигурация хранится в `%LOCALAPPDATA%\UptimeMonitor\config.json` и управляется через встроенный диалог настроек (⚙).

При первом запуске создаётся файл с дефолтными значениями:

```json
{
    "hosts": [
        {"name": "Google DNS",  "host": "8.8.8.8",    "port": 53},
        {"name": "Cloudflare",  "host": "1.1.1.1",    "port": 53},
        {"name": "GitHub",      "host": "github.com",  "port": 443}
    ],
    "check_interval": 3,
    "timeout": 2
}
```

> Изменения через диалог настроек применяются **мгновенно** — воркер перезапускается автоматически (горячая перезагрузка).

---

## Панель управления

Кнопки в правом верхнем углу окна:

| Кнопка | Действие |
|---|---|
| ⚙ | Открыть настройки — добавить/удалить хосты, изменить интервал и таймаут |
| ▁ | Режим 1 (обычный) — алерты работают, окно всплывает при оффлайне |
| ▔ | Режим 2 (рабочий стол) — окно **всегда позади** всех окон, алерты отключены |
| ✕ | Закрыть приложение (полное завершение процесса) |

### Два режима работы

**Режим 1 — Обычный** (▁ палочка внизу):
- На рабочем столе: показывает **все** хосты
- При развёрнутом активном окне + оффлайн-хост: всплывает поверх всех окон, показывает **только** упавшие

**Режим 2 — Рабочий стол** (▔ палочка вверху):
- Окно с флагом `WindowStaysOnBottomHint` — видно **только** на рабочем столе
- Любое окно перекрывает его
- Алерты полностью отключены

---

## Как это работает

| Компонент | Поток | Задача |
|---|---|---|
| `NetworkWorker` | Фоновый (`QThread`) | TCP-проверка хостов через `socket` |
| `MainWindow` | Главный | Отображение таблицы, переключение режимов |
| `SettingsDialog` | Главный | Редактирование конфига через GUI |
| `config_manager` | — | Чтение/запись `config.json` в AppData/Local |
| `Signal/Slot` | — | Потокобезопасная передача данных между потоками |

### Определение перекрытия окна (Windows)
```python
# Win32 API через ctypes:
GetForegroundWindow()  # какое окно сейчас АКТИВНО
IsZoomed()             # развёрнуто ли оно на весь экран
```

Если активное (foreground) окно — не наше и развёрнуто → наше окно перекрыто → включаем алерт. Cooldown (1 сек) предотвращает мерцание при клике по окну монитора.

---

## Решение типичных проблем

<details>
<summary><b>ImportError: DLL load failed (PySide6)</b></summary>

Скорее всего запущен системный Python, а не из `.venv`:
```bash
# Правильно:
.venv\Scripts\python.exe main.py

# Или активируйте venv:
.venv\Scripts\Activate.ps1
python main.py
```
</details>

<details>
<summary><b>Окно не появляется</b></summary>

Окно привязано к верхнему левому углу экрана (12, 12). Если у вас несколько мониторов — окно может оказаться на другом экране.
</details>

<details>
<summary><b>Localhost всегда оффлайн</b></summary>

Порт 80 не слушается на вашей машине. Откройте настройки (⚙) и удалите Localhost или поменяйте порт на тот, что действительно открыт.
</details>

<details>
<summary><b>Где хранится конфиг?</b></summary>

```
%LOCALAPPDATA%\UptimeMonitor\config.json
```

Обычно это `C:\Users\<имя>\AppData\Local\UptimeMonitor\config.json`. Путь отображается в диалоге настроек (⚙).
</details>

---

<div align="center">
Made by a student, don't judge too harshly.
</div>
