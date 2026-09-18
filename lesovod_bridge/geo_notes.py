"""
Скачивание меток рабочих с сервера «Лесовод» обратно в QGIS.

Обратное направление относительно publisher.py: здесь плагин ничего не
отправляет, а тянет уже готовый GeoJSON с метками, которые рабочие
оставляют с телефона в поле (кнопка «Обновить метки»).

Контракт эндпоинта (по описанию пользователя, не из openapi.json —
на момент публикации publisher.py этой ручки в схеме ещё не было):
GET /api/map/geo-notes.geojson?token=<токен> — авторизация тем же
токеном, что и публикация лесосек, но в query-параметре, а не в
заголовке Authorization.
"""

import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

_GEO_NOTES_PATH = "/api/map/geo-notes.geojson"

# Тот же WAF Cloudflare, что и перед остальными ручками «Лесовод»,
# банит запросы с дефолтным User-Agent Python (см. publisher.py).
_USER_AGENT = "LesovodBridge-QGIS-Plugin/1.0"


class GeoNotesFetchError(Exception):
    pass


def _clean_token(token):
    # Та же защита от случайно вписанного префикса "Bearer ", что и в
    # publisher._auth_headers — здесь токен идёт в query-параметр без
    # схемы, поэтому "Bearer " там тем более лишний.
    if token.lower().startswith("bearer "):
        return token[len("bearer "):].strip()
    return token


def fetch_geo_notes_geojson(base_url, token):
    if not base_url:
        raise GeoNotesFetchError("Не задан адрес сервера «Лесовод» (настройки плагина).")
    if not token:
        raise GeoNotesFetchError(
            "Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»)."
        )

    url = base_url.rstrip("/") + _GEO_NOTES_PATH + "?" + urlparse.urlencode(
        {"token": _clean_token(token)}
    )
    req = urlrequest.Request(url, method="GET", headers={"User-Agent": _USER_AGENT})
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urlerror.HTTPError as exc:
        raise GeoNotesFetchError(
            f"Сервер «Лесовод» ответил ошибкой {exc.code} на GET {_GEO_NOTES_PATH}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    except urlerror.URLError as exc:
        raise GeoNotesFetchError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GeoNotesFetchError(f"Сервер вернул не GeoJSON: {body[:200]!r}") from exc
