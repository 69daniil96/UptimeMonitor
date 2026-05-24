# src/config_manager.py — Менеджер конфигурации
# ============================================================
# Хранит настройки в %LOCALAPPDATA%/UptimeMonitor/config.json.
# При первом запуске создаёт файл с дефолтными значениями.
# ============================================================

import json
import os
import copy

# Дефолтная конфигурация (используется при первом запуске)
DEFAULT_CONFIG = {
    "hosts": [
        {"name": "Google DNS",   "host": "8.8.8.8",    "port": 53},
        {"name": "Cloudflare",   "host": "1.1.1.1",    "port": 53},
        {"name": "GitHub",       "host": "github.com",  "port": 443},
        {"name": "Yandex",       "host": "ya.ru",       "port": 443},
        {"name": "Localhost",    "host": "127.0.0.1",   "port": 80},
    ],
    "check_interval": 3,
    "timeout": 2,
}


def _config_dir() -> str:
    """Путь к папке конфига: %LOCALAPPDATA%/UptimeMonitor/"""
    local = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(local, "UptimeMonitor")


def _config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


def load_config() -> dict:
    """Загружает конфиг из AppData. Создаёт дефолтный, если файла нет."""
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass  # Файл повреждён — пересоздадим
    config = copy.deepcopy(DEFAULT_CONFIG)
    save_config(config)
    return config


def save_config(config: dict):
    """Сохраняет конфиг в AppData/Local."""
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def get_config_path() -> str:
    """Возвращает путь к файлу конфига (для отображения в UI)."""
    return _config_path()
