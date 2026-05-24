# main.py — Точка входа в приложение Uptime Monitor
# ============================================================
# Здесь происходит «сборка» приложения:
# 1. Загружается конфиг из AppData/Local
# 2. Создаётся GUI (главный поток)
# 3. Создаётся сетевой воркер (фоновый поток)
# 4. Они связываются через сигналы
# 5. Запускается цикл событий Qt
#
# Схема потоков:
#
#   ┌──────────────────────┐     Signal/Slot     ┌──────────────────────┐
#   │   ГЛАВНЫЙ ПОТОК      │ ◄─────────────────► │   ФОНОВЫЙ ПОТОК      │
#   │                      │                     │                      │
#   │  QApplication        │  host_checked ────► │  NetworkWorker       │
#   │  MainWindow          │                     │  ├─ QTimer (тик)     │
#   │  ├─ QTableWidget     │                     │  ├─ socket.connect() │
#   │  ├─ QTimer (UI)      │                     │  └─ emit(результат)  │
#   │  └─ update_host()    │                     │                      │
#   └──────────────────────┘                     └──────────────────────┘
#
# ВАЖНО: В Qt нельзя обновлять GUI из фонового потока.
# Сигналы автоматически маршрутизируют вызовы в правильный поток.
# ============================================================

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread

from src.config_manager import load_config
from src.network_worker import NetworkWorker
from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    # ── 1. Загружаем конфиг из AppData/Local ─────────────────
    config = load_config()
    hosts = config["hosts"]
    check_interval = config["check_interval"]
    timeout = config["timeout"]

    # ── 2. Создаём главное окно (UI) ─────────────────────────
    window = MainWindow(hosts)

    # ── 3. Создаём фоновый поток и воркер ────────────────────
    network_thread = QThread()
    worker = NetworkWorker(hosts, check_interval, timeout)
    worker.moveToThread(network_thread)

    # ── 4. Связываем сигналы ─────────────────────────────────
    network_thread.started.connect(worker.start)
    worker.host_checked.connect(window.update_host_status)

    # ── 5. Горячая перезагрузка конфига ───────────────────────
    # Когда пользователь сохраняет настройки в диалоге,
    # MainWindow отправляет config_changed → мы перезапускаем воркер.
    def on_config_changed(new_config: dict):
        nonlocal worker, network_thread
        # Останавливаем старый воркер и поток
        worker.stop()
        network_thread.quit()
        network_thread.wait()

        # Создаём новый поток и воркер с обновлённым конфигом
        network_thread = QThread()
        worker = NetworkWorker(
            new_config["hosts"],
            new_config["check_interval"],
            new_config["timeout"],
        )
        worker.moveToThread(network_thread)
        network_thread.started.connect(worker.start)
        worker.host_checked.connect(window.update_host_status)
        network_thread.start()

    window.config_changed.connect(on_config_changed)

    # ── 6. Запускаем ─────────────────────────────────────────
    network_thread.start()
    window.show()
    exit_code = app.exec()

    # ── 7. Корректное завершение ─────────────────────────────
    worker.stop()
    network_thread.quit()
    network_thread.wait()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
