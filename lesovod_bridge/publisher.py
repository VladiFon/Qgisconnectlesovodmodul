"""
Отправка контуров лесосек на сервер «Лесовод».

Пачка помечается layer_name = "qgis_lesoseki" и заменяется целиком при
каждой публикации — она отдельная от пачки, куда пишет мобильное
приложение (метки рабочего в поле), см. требование 3 промпта.
"""

import json
from urllib import error as urlerror
from urllib import request as urlrequest

LAYER_NAME = "qgis_lesoseki"


class PublishError(Exception):
    pass


def publish(base_url, import_path, token, features):
    if not import_path:
        raise PublishError(
            "Не задан путь ручки приёма слоёв на сервере «Лесовод» "
            "(настройки плагина -> «Путь ручки приёма слоёв»). "
            "Точный путь и формат нужно посмотреть на "
            f"{base_url}/docs — плагин не должен его угадывать."
        )
    if not token:
        raise PublishError(
            "Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»)."
        )

    url = base_url.rstrip("/") + "/" + import_path.lstrip("/")
    payload = {
        "layer_name": LAYER_NAME,
        "type": "FeatureCollection",
        "features": features,
    }
    body = json.dumps(payload).encode("utf-8")

    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        raise PublishError(
            f"Сервер «Лесовод» ответил ошибкой {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    except urlerror.URLError as exc:
        raise PublishError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc
