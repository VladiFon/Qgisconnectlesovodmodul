"""
Хранение настроек плагина «Лесовод-мост» через QgsSettings.

Ничего из перечисленного ниже не зашивается в код:
- 5 параметров подключения к базе ГИСлесхоз (host/port/dbname/user/password) —
  пользователь берёт их из настроек самого ГИСлесхоз на своём компьютере
  (QGIS -> панель ГИСлесхоз -> шестерёнка -> вкладка «База данных»)
  и вписывает в диалог настроек этого плагина.
- Токен сервера «Лесовод» (LESOVOD_MAP_IMPORT_SERVICE_TOKEN) — вписывается
  так же, руками, в диалоге настроек.
"""

from qgis.core import QgsSettings

_GROUP = "LesovodBridge"

DEFAULTS = {
    "db_host": "",
    "db_port": "5432",
    "db_name": "",
    "db_user": "",
    "db_password": "",
    "lesovod_base_url": "https://lesovodapipom.store",
    "lesovod_token": "",
    "lesnichestvo_num": "",
}


def _key(name):
    return f"{_GROUP}/{name}"


def load():
    s = QgsSettings()
    return {name: s.value(_key(name), default) for name, default in DEFAULTS.items()}


def save(values):
    s = QgsSettings()
    for name in DEFAULTS:
        if name in values:
            s.setValue(_key(name), values[name])
