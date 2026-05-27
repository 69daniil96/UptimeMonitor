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
#
# ПРОВЕРКА ДОСТУПНОСТИ — 2 уровня:
# ─────────────────────────────────────────────────────────
# Для порта 443 (HTTPS) используется двухуровневая проверка:
#
#   ┌───────────────┐     ┌───────────────┐
#   │  TCP connect  │ ──► │ TLS handshake │
#   │  (socket)     │     │ (ssl)         │
#   └───────┬───────┘     └───────┬───────┘
#           │                     │
#      Порт открыт?        Сертификат OK?
#      ● ONLINE             ● ONLINE
#      ● OFFLINE            ⚠ DPI BLOCK
#
# Почему это важно:
# DPI (Deep Packet Inspection) блокирует сервисы НЕ на уровне TCP,
# а на уровне TLS — читая поле SNI (Server Name Indication) в
# ClientHello и сбрасывая соединение. Поэтому TCP connect проходит
# успешно, но сервис фактически не работает.
#
# Для портов != 443 (напр. DNS на 53) TLS-проверка невозможна,
# используется только TCP.
# ============================================================

import socket
import ssl
import time
from PySide6.QtCore import QObject, Signal, QTimer


class NetworkWorker(QObject):
    """Фоновый воркер для проверки доступности хостов.

    Работает в отдельном QThread. Периодически проверяет
    список хостов через TCP socket (+ TLS для порта 443)
    и отправляет результаты через сигнал host_checked.

    Параметры сигнала host_checked:
        name (str)       — имя хоста из конфига (напр. "Google DNS")
        host (str)       — адрес (напр. "8.8.8.8")
        port (int)       — порт (напр. 53)
        status (str)     — "online" | "offline" | "dpi_block"
        ping_ms (float)  — время ответа в миллисекундах (0.0 если оффлайн)
    """

    # ── Сигналы ──────────────────────────────────────────────
    # Сигнал: результат проверки одного хоста
    # Аргументы: (name, host, port, status, ping_ms)
    # status: "online" | "offline" | "dpi_block"
    host_checked = Signal(str, str, int, str, float)

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
        1. Вызывает _check_single_host() для TCP (+ TLS) проверки
        2. Отправляет результат через сигнал host_checked
        После проверки всех хостов — отправляет cycle_finished.
        """
        for host_info in self._hosts:
            status, ping_ms = self._check_single_host(
                host_info["host"], host_info["port"]
            )
            # emit() отправляет данные в UI-поток через очередь событий Qt
            self.host_checked.emit(
                host_info["name"],
                host_info["host"],
                host_info["port"],
                status,
                ping_ms,
            )
        # Все хосты проверены — сообщаем UI, что цикл завершён
        self.cycle_finished.emit()

    def _check_single_host(self, host: str, port: int) -> tuple[str, float]:
        """Проверяет доступность одного хоста.

        Двухуровневая проверка:
        1. TCP connect — проверяет, что порт открыт
        2. TLS handshake (только для порта 443) — проверяет, что
           TLS-соединение устанавливается (DPI не блокирует SNI)

        Матрица результатов (порт 443):
        ┌────────────┬──────────────┬──────────────────┐
        │ TCP        │ TLS          │ Результат        │
        ├────────────┼──────────────┼──────────────────┤
        │ ❌ Fail    │ —            │ "offline"        │
        │ ✅ OK      │ ❌ Fail      │ "dpi_block"      │
        │ ✅ OK      │ ✅ OK        │ "online"         │
        └────────────┴──────────────┴──────────────────┘

        Для порта != 443: только TCP → "online" / "offline".

        Returns:
            (status: str, ping_ms: float)
            status = "online" | "offline" | "dpi_block"
            ping_ms = 0.0 если хост оффлайн
        """
        try:
            # ── Уровень 1: TCP connect ──────────────────────
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)

            start_time = time.time()
            result = sock.connect_ex((host, port))
            tcp_ms = (time.time() - start_time) * 1000

            if result != 0:
                # TCP не подключился → хост полностью недоступен
                sock.close()
                return "offline", 0.0

            # ── Уровень 2: TLS handshake (только порт 443) ──
            if port == 443:
                try:
                    ctx = ssl.create_default_context()
                    # Переустанавливаем таймаут для TLS-фазы
                    sock.settimeout(self._timeout)
                    ssock = ctx.wrap_socket(sock, server_hostname=host)
                    ssock.close()
                    # Пинг = только TCP (сетевая задержка),
                    # TLS — лишь проверка, не влияет на отображение
                    return "online", round(tcp_ms, 1)

                except (ssl.SSLError, ssl.SSLCertVerificationError):
                    # TLS ошибка: DPI сбросил соединение на этапе
                    # ClientHello, или подменил сертификат
                    try:
                        sock.close()
                    except OSError:
                        pass
                    return "dpi_block", round(tcp_ms, 1)

                except (ConnectionResetError, ConnectionAbortedError):
                    # DPI отправил RST после прочтения SNI
                    try:
                        sock.close()
                    except OSError:
                        pass
                    return "dpi_block", round(tcp_ms, 1)

                except (socket.timeout, TimeoutError):
                    # DPI дропнул пакет (нет ответа на ClientHello)
                    try:
                        sock.close()
                    except OSError:
                        pass
                    return "dpi_block", round(tcp_ms, 1)

                except OSError:
                    # Прочие сетевые ошибки на TLS-фазе
                    try:
                        sock.close()
                    except OSError:
                        pass
                    return "dpi_block", round(tcp_ms, 1)

            # ── Порт != 443: только TCP ─────────────────────
            sock.close()
            return "online", round(tcp_ms, 1)

        except (socket.timeout, socket.error, OSError):
            # socket.timeout — сервер не ответил вовремя
            # socket.error   — ошибка сети (нет маршрута, отказ и т.д.)
            # OSError        — общие ошибки ОС (DNS не найден и т.д.)
            return "offline", 0.0
