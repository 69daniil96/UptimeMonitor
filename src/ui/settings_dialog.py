# src/ui/settings_dialog.py — Диалог настроек
# ============================================================
# Открывается по клику на шестерёнку. Позволяет:
# - Добавлять / удалять хосты
# - Менять интервал проверки и таймаут
# Сохраняет изменения в AppData/Local через config_manager.
# ============================================================

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QPushButton, QLabel, QSpinBox,
    QHeaderView, QAbstractItemView, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from src.config_manager import save_config, get_config_path


class SettingsDialog(QDialog):
    """Диалог настроек с тёмной темой.

    Сигнал config_saved(dict) — отправляется при сохранении
    с новой конфигурацией, чтобы главное окно обновилось.
    """

    config_saved = Signal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("⚙ Настройки")
        self.setMinimumSize(500, 400)
        self.setModal(True)

        self._apply_dark_theme()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Путь к конфигу ───────────────────────────────────
        path_label = QLabel(f"📁 {get_config_path()}")
        path_label.setStyleSheet("color: #808090; font-size: 10px; padding: 2px;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        # ── Таблица хостов ───────────────────────────────────
        hosts_label = QLabel("Хосты для мониторинга:")
        hosts_label.setStyleSheet("color: #d0d0d0; font-weight: bold;")
        layout.addWidget(hosts_label)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Имя", "Хост / IP", "Порт"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 70)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._table)

        # Заполняем таблицу
        for host in self._config.get("hosts", []):
            self._add_host_row(host["name"], host["host"], host["port"])

        # Кнопки добавить/удалить
        btn_row = QHBoxLayout()
        add_btn = QPushButton("＋ Добавить")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("✕ Удалить")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Параметры ────────────────────────────────────────
        params_layout = QHBoxLayout()

        params_layout.addWidget(QLabel("Интервал (сек):"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 300)
        self._interval_spin.setValue(self._config.get("check_interval", 3))
        params_layout.addWidget(self._interval_spin)

        params_layout.addSpacing(20)

        params_layout.addWidget(QLabel("Таймаут (сек):"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 30)
        self._timeout_spin.setValue(self._config.get("timeout", 2))
        params_layout.addWidget(self._timeout_spin)

        params_layout.addStretch()
        layout.addLayout(params_layout)

        # ── Сохранить / Отмена ───────────────────────────────
        bottom = QHBoxLayout()
        bottom.addStretch()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        save_btn = QPushButton("💾 Сохранить")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: rgba(80, 200, 120, 0.2);
                color: #50c878;
                border: 1px solid rgba(80, 200, 120, 0.4);
                padding: 6px 18px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(80, 200, 120, 0.35);
            }
        """)
        save_btn.clicked.connect(self._on_save)
        bottom.addWidget(save_btn)

        layout.addLayout(bottom)

    def _add_host_row(self, name="", host="", port=443):
        """Добавляет строку в таблицу хостов."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name))
        self._table.setItem(row, 1, QTableWidgetItem(host))
        port_item = QTableWidgetItem(str(port))
        port_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, 2, port_item)

    def _on_add(self):
        self._add_host_row("Новый хост", "0.0.0.0", 80)
        # Фокус на ячейку имени новой строки для быстрого редактирования
        last = self._table.rowCount() - 1
        self._table.setCurrentCell(last, 0)
        self._table.editItem(self._table.item(last, 0))

    def _on_delete(self):
        rows = set(idx.row() for idx in self._table.selectedIndexes())
        for row in sorted(rows, reverse=True):
            self._table.removeRow(row)

    def _on_save(self):
        """Собирает данные из UI, сохраняет в файл, emit сигнал."""
        hosts = []
        for row in range(self._table.rowCount()):
            name = (self._table.item(row, 0).text() or "").strip()
            host = (self._table.item(row, 1).text() or "").strip()
            try:
                port = int(self._table.item(row, 2).text())
            except (ValueError, AttributeError):
                port = 80

            if host:  # пропускаем пустые строки
                hosts.append({"name": name or host, "host": host, "port": port})

        if not hosts:
            QMessageBox.warning(self, "Ошибка", "Добавьте хотя бы один хост.")
            return

        new_config = {
            "hosts": hosts,
            "check_interval": self._interval_spin.value(),
            "timeout": self._timeout_spin.value(),
        }
        save_config(new_config)
        self.config_saved.emit(new_config)
        self.accept()

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QDialog {
                background: #1e1e2e;
                color: #d0d0e0;
            }
            QLabel {
                color: #d0d0e0;
            }
            QTableWidget {
                background: #252535;
                color: #e0e0e0;
                border: 1px solid #3a3a50;
                gridline-color: #3a3a50;
                font-size: 12px;
            }
            QHeaderView::section {
                background: #2d2d45;
                color: #a0a0c0;
                border: none;
                padding: 5px;
                font-weight: bold;
            }
            QTableWidget::item {
                padding: 3px 6px;
            }
            QPushButton {
                background: rgba(60, 60, 90, 0.6);
                color: #c0c0d0;
                border: 1px solid #4a4a60;
                padding: 5px 14px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: rgba(80, 80, 120, 0.7);
                color: #ffffff;
            }
            QSpinBox {
                background: #252535;
                color: #e0e0e0;
                border: 1px solid #3a3a50;
                padding: 3px 8px;
                border-radius: 3px;
            }
        """)
