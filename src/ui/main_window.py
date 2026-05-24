# src/ui/main_window.py — Главное (и единственное) окно приложения
# ============================================================
# Это GUI-слой приложения. Он работает в ГЛАВНОМ потоке Qt.
#
# У окна два состояния (режима):
#
# ┌─────────────────────────────────────────────────────────┐
# │ РЕЖИМ «НОРМАЛЬНЫЙ» (окно видно / не перекрыто)          │
# │ ─ Показывает ВСЕ хосты и их статусы                     │
# │ ─ Обычное окно, перекрывается другими окнами             │
# │ ─ Флаги: Qt.FramelessWindowHint | Qt.Tool               │
# └─────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────┐
# │ РЕЖИМ «АЛЕРТ» (окно перекрыто + есть оффлайн-хосты)    │
# │ ─ Показывает ТОЛЬКО упавшие хосты                       │
# │ ─ Всплывает поверх всех окон (WindowStaysOnTopHint)     │
# │ ─ Когда все хосты вернутся — автоматически уходит назад │
# └─────────────────────────────────────────────────────────┘
#
# Переключение между режимами происходит по СОБСТВЕННОМУ таймеру
# окна (каждые 150 мс), НЕЗАВИСИМО от сетевого воркера.
#
# Определение перекрытия:
# На Windows используется Win32 API через ctypes:
#   GetForegroundWindow() — какое окно сейчас АКТИВНО у пользователя
#   IsZoomed()            — развёрнуто ли оно на весь экран
# Если активное окно — чужое и развёрнуто → наше окно перекрыто.
# Cooldown (1 сек) предотвращает мерцание при клике по нашему окну.
# ============================================================

import ctypes
import time

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton,
    QApplication,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont

from src.ui.settings_dialog import SettingsDialog
from src.config_manager import load_config



# ── Цвета для индикации статуса ────────────────────────────
COLOR_ONLINE  = QColor(80, 230, 120)    # Зелёный — хост доступен
COLOR_OFFLINE = QColor(255, 80, 80)     # Красный — хост недоступен
COLOR_WAITING = QColor(180, 180, 180)   # Серый   — ещё не проверен
COLOR_BG      = QColor(25, 25, 35, 230) # Фон окна (тёмный, полупрозрачный)
COLOR_HEADER  = QColor(45, 45, 65)      # Фон заголовка таблицы
COLOR_ROW_ALT = QColor(35, 35, 50, 200) # Чередующиеся строки

# Стиль кнопок заголовка (общий для шестерёнки, пина и крестика)
_HEADER_BTN_STYLE = """
    QPushButton {{
        background: transparent;
        color: {color};
        border: none;
        font-size: {size};
        font-weight: bold;
        border-radius: 4px;
    }}
    QPushButton:hover {{
        background: {hover_bg};
        color: {hover_color};
    }}
"""


class MainWindow(QMainWindow):
    """Главное окно приложения Uptime Monitor.

    Управляет двумя состояниями отображения:
    - Нормальное: все хосты, обычное окно
    - Алертное:   только оффлайн-хосты, поверх всех окон

    Сигналы:
        hosts_changed(list) — новый список хостов после сохранения настроек
        config_changed(dict) — полный новый конфиг (для обновления воркера)
    """

    hosts_changed = Signal(list)
    config_changed = Signal(dict)

    def __init__(self, hosts: list):
        """
        Args:
            hosts: список хостов из конфига
        """
        super().__init__()

        # ── Сохраняем данные ─────────────────────────────────
        self._hosts = hosts
        self._host_statuses = {}
        self._alert_mode = False
        # Режим «рабочий стол»: True → окно всегда позади всех (виджет)
        self._desktop_mode = False
        # Защита от мерцания: время последнего переключения режима
        self._last_mode_switch = 0.0

        # ── Настройки окна ───────────────────────────────────
        self.setWindowTitle("Uptime Monitor")
        self._normal_flags = Qt.FramelessWindowHint | Qt.Tool
        self.setWindowFlags(self._normal_flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.move(12, 12)

        # ── Собираем интерфейс ───────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        self._main_layout = QVBoxLayout(central)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Заголовок + кнопки управления
        self._build_header()

        # Таблица хостов
        self.table = self._create_table()
        self._main_layout.addWidget(self.table)
        self._populate_table(self._hosts)

        # Стили (тёмная тема)
        self._apply_styles()

        # ── Таймер проверки перекрытия ────────────────────────
        # Работает НЕЗАВИСИМО от сетевого воркера (каждые 150 мс).
        # Проверяет: «Есть ли развёрнутое окно? Есть ли оффлайн?»
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._check_window_state)
        self._ui_timer.start(150)

        # ── Перетаскивание мышкой ─────────────────────────────
        self._drag_position = None

        self.show()

    # ══════════════════════════════════════════════════════════
    # ПОСТРОЕНИЕ ЗАГОЛОВКА (title + gear + pin + close)
    # ══════════════════════════════════════════════════════════

    def _build_header(self):
        """Создаёт горизонтальную панель:  [⚡ TITLE] ... [⚙] [─] [✕]"""
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)

        # Заголовок
        self._title_label = QLabel("⚡ UPTIME MONITOR")
        self._title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._title_label.setStyleSheet(
            "color: #c0c0c0; padding: 8px 12px; background: transparent;"
        )
        header.addWidget(self._title_label)
        header.addStretch()

        # ── Шестерёнка (настройки) ────────────────────────────
        self._gear_btn = QPushButton("⚙")
        self._gear_btn.setFixedSize(28, 28)
        self._gear_btn.setCursor(Qt.PointingHandCursor)
        self._gear_btn.setToolTip("Настройки")
        self._gear_btn.setStyleSheet(_HEADER_BTN_STYLE.format(
            color="#707090", size="15px",
            hover_bg="rgba(100, 100, 200, 0.25)", hover_color="#a0a0ff",
        ))
        self._gear_btn.clicked.connect(self._open_settings)
        header.addWidget(self._gear_btn)

        # ── Кнопка переключения режимов (горизонтальная палочка) ─
        # Два режима:
        #   ▁ (палочка внизу) → Режим 1: обычное поведение с алертами
        #   ▔ (палочка вверху) → Режим 2: виджет на рабочем столе
        #                        (WindowStaysOnBottomHint, без алертов)
        self._pin_btn = QPushButton("▁")  # палочка внизу = режим 1
        self._pin_btn.setFixedSize(28, 28)
        self._pin_btn.setCursor(Qt.PointingHandCursor)
        self._pin_btn.setToolTip("Режим рабочего стола (всегда позади)")
        self._pin_btn.setStyleSheet(_HEADER_BTN_STYLE.format(
            color="#707090", size="14px",
            hover_bg="rgba(100, 100, 200, 0.25)", hover_color="#a0a0ff",
        ))
        self._pin_btn.clicked.connect(self._toggle_desktop_mode)
        header.addWidget(self._pin_btn)

        # ── Крестик (полное закрытие приложения) ───────────────
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip("Закрыть приложение")
        self._close_btn.setStyleSheet(_HEADER_BTN_STYLE.format(
            color="#707090", size="14px",
            hover_bg="rgba(255, 60, 60, 0.3)", hover_color="#ff5555",
        ))
        # QApplication.quit() завершает ВСЁ приложение, а не просто окно.
        # self.close() только прячет окно — процесс может остаться.
        self._close_btn.clicked.connect(QApplication.quit)
        header.addWidget(self._close_btn)

        self._main_layout.addLayout(header)

    # ══════════════════════════════════════════════════════════
    # КНОПКИ ДЕЙСТВИЙ
    # ══════════════════════════════════════════════════════════

    def _toggle_desktop_mode(self):
        """Переключает между двумя режимами окна.

        Режим 1 (▁, палочка внизу):
          Обычное поведение — все хосты на рабочем столе,
          алерт поверх всех окон при оффлайне + перекрытии.

        Режим 2 (▔, палочка вверху):
          Окно-виджет на рабочем столе — WindowStaysOnBottomHint.
          Видно ТОЛЬКО когда свернуты все окна. Без алертов.
        """
        self._desktop_mode = not self._desktop_mode

        if self._desktop_mode:
            # Режим 2: виджет рабочего стола
            self._pin_btn.setText("▔")
            self._pin_btn.setToolTip("Режим рабочего стола (кликни для обычного)")
            # Если были в алерте — выходим
            if self._alert_mode:
                self._alert_mode = False
                self._title_label.setText("⚡ UPTIME MONITOR")
                self._title_label.setStyleSheet(
                    "color: #c0c0c0; padding: 8px 12px; background: transparent;"
                )
            # Ставим флаг «позади всех окон»
            self.setWindowFlags(
                self._normal_flags | Qt.WindowStaysOnBottomHint
            )
            self.show()
            self._refresh_table()
        else:
            # Режим 1: обычное поведение
            self._pin_btn.setText("▁")
            self._pin_btn.setToolTip("Режим рабочего стола (всегда позади)")
            # Убираем StaysOnBottom, возвращаем обычные флаги
            self.setWindowFlags(self._normal_flags)
            self.show()
            self._refresh_table()

    def _open_settings(self):
        """Открывает диалог настроек."""
        config = load_config()
        dialog = SettingsDialog(config, parent=self)
        dialog.config_saved.connect(self._on_config_saved)
        dialog.exec()

    def _on_config_saved(self, new_config: dict):
        """Слот: вызывается когда пользователь сохранил настройки."""
        self._hosts = new_config["hosts"]
        self._host_statuses.clear()
        self._populate_table(self._hosts)
        self._refresh_table()
        # Сообщаем main.py что конфиг изменился (для перезапуска воркера)
        self.config_changed.emit(new_config)

    # ══════════════════════════════════════════════════════════
    # СОЗДАНИЕ ИНТЕРФЕЙСА
    # ══════════════════════════════════════════════════════════

    def _create_table(self) -> QTableWidget:
        """Создаёт и настраивает таблицу для отображения хостов."""
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Хост", "Статус", "Пинг"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        from PySide6.QtWidgets import QHeaderView
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _populate_table(self, hosts: list):
        """Заполняет таблицу списком хостов."""
        self.table.setRowCount(len(hosts))
        for row, host_info in enumerate(hosts):
            name_item = QTableWidgetItem(host_info["name"])
            name_item.setForeground(QColor(220, 220, 220))
            self.table.setItem(row, 0, name_item)

            status_item = QTableWidgetItem("⏳")
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(COLOR_WAITING)
            self.table.setItem(row, 1, status_item)

            ping_item = QTableWidgetItem("—")
            ping_item.setTextAlignment(Qt.AlignCenter)
            ping_item.setForeground(QColor(160, 160, 160))
            self.table.setItem(row, 2, ping_item)

            if row % 2 == 1:
                for col in range(3):
                    self.table.item(row, col).setBackground(COLOR_ROW_ALT)

    def _apply_styles(self):
        """Применяет тёмную тему к окну и таблице."""
        self.setStyleSheet("""
            QMainWindow {
                background: rgba(25, 25, 35, 230);
                border-radius: 10px;
            }
            QWidget {
                background: rgba(25, 25, 35, 230);
                border-radius: 10px;
            }
            QTableWidget {
                background: transparent;
                border: none;
                color: #e0e0e0;
                font-family: 'Consolas', 'Cascadia Code', monospace;
                font-size: 12px;
                selection-background-color: rgba(80, 80, 120, 100);
            }
            QHeaderView::section {
                background: rgba(45, 45, 65, 220);
                color: #a0a0c0;
                border: none;
                padding: 6px;
                font-weight: bold;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
        """)

    # ══════════════════════════════════════════════════════════
    # ОБНОВЛЕНИЕ ДАННЫХ (вызывается из сигнала воркера)
    # ══════════════════════════════════════════════════════════

    def update_host_status(self, name: str, host: str, port: int,
                           is_online: bool, ping_ms: float):
        """Слот: обновляет статус одного хоста.

        Подключается к сигналу host_checked воркера.
        Qt автоматически маршрутизирует вызов в главный поток
        (queued connection).
        """
        self._host_statuses[name] = {
            "host": host, "port": port,
            "is_online": is_online, "ping_ms": ping_ms,
        }
        self._refresh_table()

    def _refresh_table(self):
        """Перерисовывает таблицу: все хосты или только оффлайн."""
        if self._alert_mode:
            hosts_to_show = [
                h for h in self._hosts
                if h["name"] in self._host_statuses
                and not self._host_statuses[h["name"]]["is_online"]
            ]
        else:
            hosts_to_show = self._hosts

        self.table.setRowCount(len(hosts_to_show))

        for row, host_info in enumerate(hosts_to_show):
            name = host_info["name"]
            status = self._host_statuses.get(name)

            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(220, 220, 220))
            self.table.setItem(row, 0, name_item)

            if status:
                is_online = status["is_online"]
                ping_ms = status["ping_ms"]

                if is_online:
                    s_item = QTableWidgetItem("● ONLINE")
                    s_item.setForeground(COLOR_ONLINE)
                else:
                    s_item = QTableWidgetItem("● OFFLINE")
                    s_item.setForeground(COLOR_OFFLINE)
                s_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 1, s_item)

                ping_text = f"{ping_ms} ms" if is_online else "—"
                p_item = QTableWidgetItem(ping_text)
                p_item.setTextAlignment(Qt.AlignCenter)
                p_item.setForeground(
                    QColor(160, 220, 180) if is_online else QColor(120, 120, 120)
                )
                self.table.setItem(row, 2, p_item)
            else:
                s_item = QTableWidgetItem("⏳")
                s_item.setTextAlignment(Qt.AlignCenter)
                s_item.setForeground(COLOR_WAITING)
                self.table.setItem(row, 1, s_item)

                p_item = QTableWidgetItem("—")
                p_item.setTextAlignment(Qt.AlignCenter)
                p_item.setForeground(QColor(120, 120, 120))
                self.table.setItem(row, 2, p_item)

            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    if self._alert_mode:
                        item.setBackground(QColor(80, 20, 20, 180))
                    elif row % 2 == 1:
                        item.setBackground(COLOR_ROW_ALT)

        self._adjust_size()

    # ══════════════════════════════════════════════════════════
    # ЛОГИКА ПЕРЕКЛЮЧЕНИЯ РЕЖИМОВ (нормальный ↔ алерт)
    # ══════════════════════════════════════════════════════════

    def _check_window_state(self):
        """Вызывается UI-таймером каждые 150 мс.

        Матрица решений:
        ┌──────────────┬────────────────┬───────────────────────┐
        │              │ Есть макс.окно │ Нет макс.окна         │
        ├──────────────┼────────────────┼───────────────────────┤
        │ Есть оффлайн │ АЛЕРТ          │ НОРМАЛЬНЫЙ            │
        │ Всё онлайн   │ (без алерта)   │ НОРМАЛЬНЫЙ            │
        └──────────────┴────────────────┴───────────────────────┘

        В режиме рабочего стола (_desktop_mode) алерты не срабатывают.
        """
        # Защита от мерцания: не переключаемся чаще чем раз в секунду
        if time.time() - self._last_mode_switch < 1.0:
            return

        # В режиме рабочего стола — алерты отключены
        if self._desktop_mode:
            return

        has_offline = any(
            not s["is_online"] for s in self._host_statuses.values()
        )
        is_covered = self._is_covered()

        if is_covered and has_offline:
            if not self._alert_mode:
                self._enter_alert_mode()
        else:
            if self._alert_mode:
                self._exit_alert_mode()

    def _enter_alert_mode(self):
        """Включает алерт: поверх всех окон, только оффлайн-хосты."""
        self._alert_mode = True
        self._last_mode_switch = time.time()

        self.setWindowFlags(self._normal_flags | Qt.WindowStaysOnTopHint)
        self.show()

        self._title_label.setText("⚠ HOSTS DOWN")
        self._title_label.setStyleSheet(
            "color: #ff5555; padding: 8px 12px; background: transparent;"
            "font-weight: bold;"
        )
        self._refresh_table()

    def _exit_alert_mode(self):
        """Возвращает обычный режим: все хосты, обычное окно."""
        self._alert_mode = False
        self._last_mode_switch = time.time()

        self.setWindowFlags(self._normal_flags)
        self.show()

        self._title_label.setText("⚡ UPTIME MONITOR")
        self._title_label.setStyleSheet(
            "color: #c0c0c0; padding: 8px 12px; background: transparent;"
        )
        self._refresh_table()

    def _is_covered(self) -> bool:
        """Определяет, перекрыто ли окно развёрнутым приложением.

        Логика: если АКТИВНОЕ (foreground) окно — не наше и развёрнуто
        на весь экран, значит пользователь смотрит на него и наше окно
        скрыто за ним.

        Почему НЕ EnumWindows:
        EnumWindows находит ВСЕ развёрнутые окна, включая те, что
        пользователь не видит (например, IDE в фоне). Это приводило
        к тому, что алерт был включён ВСЕГДА, даже когда наше окно
        видно на рабочем столе рядом с развёрнутым окном.

        Правильная семантика:
        «Перекрыто» = пользователь АКТИВНО смотрит на чужое развёрнутое
        окно → наше окно позади него → нужен алерт.

        Побочный эффект:
        Когда пользователь кликает по нашему алертному окну, мы
        становимся foreground → _is_covered() = False → exit alert.
        Cooldown (1 сек) в _check_window_state предотвращает мерцание.
        """
        try:
            user32 = ctypes.windll.user32
            our_hwnd = int(self.winId())
            fg = user32.GetForegroundWindow()

            # Если мы сами на переднем плане — мы не перекрыты
            if fg == our_hwnd or fg == 0:
                return False

            # Если активное окно развёрнуто — мы перекрыты
            return bool(user32.IsZoomed(fg))

        except Exception:
            return False

    # ══════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ══════════════════════════════════════════════════════════

    def _adjust_size(self):
        """Подгоняет размер окна под количество строк."""
        row_count = self.table.rowCount()
        height = 40 + 30 + max(row_count, 1) * 32 + 10
        self.setFixedSize(320, min(height, 600))

    # ── Перетаскивание окна мышкой ────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint() - self.pos()
            )

    def mouseMoveEvent(self, event):
        if self._drag_position and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)

    def mouseReleaseEvent(self, event):
        self._drag_position = None
