# src/network_worker.py — Фоновый сетевой воркер
# ============================================================
# Этот модуль работает в ОТДЕЛЬНОМ потоке (QThread).
# Он периодически проверяет доступность хостов через TCP-сокеты
# и отправляет результаты в UI-поток через систему сигналов Qt.
#
# ВАЖНО для начинающего разработчика:
# ─────────────────────────────────────────────────────────
# В Qt НЕЛЬЗЯ обновлять интерфейс из фонового потока напрямую.
# Вместо этого мы используем механизм Signal/Slot:
#
#   Воркер (фоновый поток)           UI (главный поток)
#   ─────────────────────            ─────────────────────
#   self.host_checked.emit(...)  →   window.update_host_status(...)
#   self.cycle_finished.emit()   →   (любой слот)
#
# emit() — безопасно отправляет данные между потоками.
# Qt сам позаботится о том, чтобы слот вызвался в правильном потоке.
# ============================================================

import socket
import time
from PySide6.QtCore import QObject, Signal, QTimer


class NetworkWorker(QObject):
    """Фоновый воркер для проверки доступности хостов.

    Работает в отдельном QThread. Периодически проверяет
    список хостов через TCP socket и отправляет результаты
    через сигнал host_checked.

    Параметры сигнала host_checked:
        name (str)       — имя хоста из конфига (напр. "Google DNS")
        host (str)       — адрес (напр. "8.8.8.8")
        port (int)       — порт (напр. 53)
        is_online (bool) — True если хост доступен
        ping_ms (float)  — время ответа в миллисекундах (0.0 если оффлайн)
    """

    # ── Сигналы ──────────────────────────────────────────────
    # Сигнал: результат проверки одного хоста
    # Аргументы: (name: str, host: str, port: int, is_online: bool, ping_ms: float)
    host_checked = Signal(str, str, int, bool, float)

    # Сигнал: один полный цикл проверки всех хостов завершён
    cycle_finished = Signal()

    def __init__(self, hosts: list, interval: int, timeout: int):
        """
        Args:
            hosts:    список словарей [{"name": ..., "host": ..., "port": ...}, ...]
            interval: интервал между циклами проверки (секунды)
            timeout:  таймаут подключения к одному хосту (секунды)
        """
        super().__init__()
        self._hosts = hosts
        # QTimer работает в миллисекундах, поэтому умножаем на 1000
        self._interval_ms = interval * 1000
        self._timeout = timeout
        self._timer = None  # QTimer создаётся в start(), уже внутри потока

    def start(self):
        """Запускает периодическую проверку.

        ВАЖНО: Этот метод вызывается ПОСЛЕ moveToThread(),
        когда воркер уже живёт в фоновом потоке.
        QTimer нужно создавать именно здесь, а не в __init__,
        потому что таймер должен принадлежать тому же потоку,
        в котором он будет работать.
        """
        # Создаём таймер и привязываем его к воркеру (self = parent)
        self._timer = QTimer(self)
        # Каждый раз когда таймер «тикает» — вызываем _check_all_hosts
        self._timer.timeout.connect(self._check_all_hosts)
        # Запускаем таймер с интервалом из конфига
        self._timer.start(self._interval_ms)
        # Первую проверку делаем сразу, не дожидаясь первого тика
        self._check_all_hosts()

    def stop(self):
        """Останавливает таймер проверки."""
        if self._timer:
            self._timer.stop()

    def _check_all_hosts(self):
        """Проверяет все хосты из списка по очереди.

        Для каждого хоста:
        1. Вызывает _check_single_host() для TCP-проверки
        2. Отправляет результат через сигнал host_checked
        После проверки всех хостов — отправляет cycle_finished.
        """
        for host_info in self._hosts:
            is_online, ping_ms = self._check_single_host(
                host_info["host"], host_info["port"]
            )
            # emit() отправляет данные в UI-поток через очередь событий Qt
            self.host_checked.emit(
                host_info["name"],
                host_info["host"],
                host_info["port"],
                is_online,
                ping_ms,
            )
        # Все хосты проверены — сообщаем UI, что цикл завершён
        self.cycle_finished.emit()

    def _check_single_host(self, host: str, port: int) -> tuple[bool, float]:
        """Проверяет доступность одного хоста через TCP-соединение.

        Алгоритм:
        1. Создаём TCP-сокет (AF_INET = IPv4, SOCK_STREAM = TCP)
        2. Устанавливаем таймаут (чтобы не ждать вечно)
        3. Пытаемся подключиться к host:port
        4. Если подключились — хост онлайн, замеряем время
        5. Если ошибка (таймаут, отказ, DNS не найден) — оффлайн

        Returns:
            (is_online: bool, ping_ms: float)
            ping_ms = 0.0 если хост оффлайн
        """
        try:
            # Создаём новый TCP-сокет для каждой проверки
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Таймаут: если за N секунд нет ответа — исключение
            sock.settimeout(self._timeout)

            # Засекаем время ДО подключения
            start_time = time.time()
            # connect_ex() возвращает 0 при успехе, код ошибки при неудаче.
            # В отличие от connect(), не выбрасывает исключение при ошибке.
            result = sock.connect_ex((host, port))
            # Считаем время в миллисекундах
            ping_ms = (time.time() - start_time) * 1000

            # Закрываем сокет — он нам больше не нужен
            sock.close()

            # result == 0 означает успешное подключение
            if result == 0:
                return True, round(ping_ms, 1)
            else:
                return False, 0.0

        except (socket.timeout, socket.error, OSError):
            # socket.timeout — сервер не ответил вовремя
            # socket.error   — ошибка сети (нет маршрута, отказ и т.д.)
            # OSError        — общие ошибки ОС (DNS не найден и т.д.)
            return False, 0.0
