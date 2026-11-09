"""
«Показать в QGIS» с сайта Лесовода (0.10.0).

Карта на сайте не нужна — для неё есть QGIS. На сайте у делянки, участка
лесных культур и выдела есть кнопка «🗺 Показать в QGIS»: сервер запоминает,
что показать, а плагин раз в пару секунд спрашивает у сервера «есть новое?»
(GET /api/map/qgis/pokazat?token=…&posle=N) и приближает карту, открывая
карточку «Что здесь». Опрос — асинхронный (QgsNetworkAccessManager), QGIS
не подвисает, даже если сервер недоступен.

Выключается галочкой в меню «Модули → Лесовод-мост».
"""

import json
from urllib import parse as urlparse

from qgis.core import QgsNetworkAccessManager, QgsSettings
from qgis.PyQt.QtCore import QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from . import settings

INTERVAL_MS = 2500
_KEY_VKL = "LesovodBridge/slushat_sayt"


def vklyucheno():
    return str(QgsSettings().value(_KEY_VKL, "true")).lower() in ("true", "1")


def zapomnit(vkl):
    QgsSettings().setValue(_KEY_VKL, "true" if vkl else "false")


class SlushatelSaita:
    def __init__(self, chto_zdes):
        self.chto_zdes = chto_zdes
        self.posle = 0  # номер последнего увиденного запроса
        self.znaem = False  # был ли уже хоть один ответ сервера
        self.reply = None
        self.timer = QTimer()
        self.timer.setInterval(INTERVAL_MS)
        self.timer.timeout.connect(self._sprosit)

    def start(self):
        if vklyucheno():
            self.timer.start()
            self._sprosit()

    def stop(self):
        self.timer.stop()
        if self.reply is not None:
            try:
                self.reply.abort()
            except RuntimeError:
                pass
            self.reply = None

    def _sprosit(self):
        if self.reply is not None:  # прошлый запрос ещё идёт
            return
        cfg = settings.load()
        base, token = (cfg.get("lesovod_base_url") or "").rstrip("/"), cfg.get("lesovod_token") or ""
        if not base or not token:
            return
        url = f"{base}/api/map/qgis/pokazat?" + urlparse.urlencode({"token": token, "posle": self.posle if self.znaem else -1})
        req = QNetworkRequest(QUrl(url))
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(8000)
        self.reply = QgsNetworkAccessManager.instance().get(req)
        self.reply.finished.connect(self._otvet)

    def _otvet(self):
        reply, self.reply = self.reply, None
        if reply is None:
            return
        try:
            if reply.error() != QNetworkReply.NoError:
                return  # сервер недоступен / старый сервер без этой функции — молча ждём
            data = json.loads(bytes(reply.readAll()).decode("utf-8") or "{}")
        except (ValueError, RuntimeError):
            return
        finally:
            reply.deleteLater()
        # первый ответ (posle=-1) — только узнать текущий номер: при запуске
        # QGIS старые запросы не показываем, только новые
        self.znaem = True
        self.posle = max(self.posle, int(data.get("nomer") or 0))
        z = data.get("pokazat")
        if z:
            self.chto_zdes.pokazat_s_saita(z)
