"""
Отправка контуров лесосек на сервер «Лесовод».

Реальный контракт взят из выгруженного пользователем openapi.json
сервера «Лесовод» (эндпоинты приёма слоёв, теги "map"):

- POST /api/map/import-layer — multipart/form-data, поле файла "file"
  (GeoJSON/Shapefile.zip/экспорт «Лесной страж»), query-параметры
  layer_name (опц.) и lesnichestvo_num (опц.). Один вызов = один новый
  batch_id — сервер сам ничего не заменяет по имени слоя.
- GET /api/map/import-layers/batches — список уже загруженных пачек.
- DELETE /api/map/import-layers/{batch_id} — убрать пачку целиком.

Требование 3 промпта ("целиком заменяем содержимое одной конкретной
пачки при каждой публикации, а не накапливаем поверх старого") сервер
сам не выполняет — он лишь даёт список/удаление по batch_id. Поэтому
здесь это сделано на стороне плагина: перед загрузкой нового файла
находим все прежние пачки с layer_name == "qgis_lesoseki" и удаляем их,
и только потом грузим новую.

Схема ответа GET .../batches в openapi.json не типизирована (FastAPI
отдаёт произвольный dict/list без pydantic-модели), поэтому имя поля
идентификатора пачки распознаётся защитно (см. _extract_batch_id) —
если формат окажется другим, публикация прерывается с понятной
ошибкой вместо удаления наугад.
"""

import json
import mimetypes
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

LAYER_NAME = "qgis_lesoseki"

_IMPORT_PATH = "/api/map/import-layer"
_BATCHES_PATH = "/api/map/import-layers/batches"
_DELETE_BATCH_PATH = "/api/map/import-layers/{batch_id}"


class PublishError(Exception):
    pass


# Cloudflare перед сервером «Лесовод» банит запросы с дефолтным
# User-Agent Python (Python-urllib/х.у) как подозрительные — ошибка
# 403 "error code: 1010" (WAF-блок по сигнатуре клиента), а не
# проблема авторизации или самой ручки. Обычный User-Agent браузера
# такую блокировку снимает.
_USER_AGENT = "LesovodBridge-QGIS-Plugin/1.0"


def _auth_headers(token):
    if not token:
        raise PublishError(
            "Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»)."
        )
    return {"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT}


def _request(method, url, headers, data=None):
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        hint = ""
        if exc.code == 403 and "1010" in body:
            hint = (
                " Это похоже на блокировку Cloudflare WAF по сигнатуре клиента "
                "(error code: 1010), а не на проблему с токеном или самой ручкой — "
                "нужно на стороне сервера разрешить в Cloudflare запросы к "
                "/api/map/import-layer* с этим User-Agent или с этим Bearer-токеном."
            )
        raise PublishError(
            f"Сервер «Лесовод» ответил ошибкой {exc.code} на {method} {url}: {body}{hint}"
        ) from exc
    except urlerror.URLError as exc:
        raise PublishError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc


def _extract_batch_id(batch):
    if isinstance(batch, dict):
        for key in ("batch_id", "id"):
            if key in batch and batch[key]:
                return batch[key]
    return None


def _extract_layer_name(batch):
    if isinstance(batch, dict):
        for key in ("layer_name", "name"):
            if key in batch:
                return batch[key]
    return None


def _list_batches(base_url, token, lesnichestvo_num=None):
    query = {}
    if lesnichestvo_num:
        query["lesnichestvo_num"] = lesnichestvo_num
    url = base_url.rstrip("/") + _BATCHES_PATH
    if query:
        url += "?" + urlparse.urlencode(query)

    status, text = _request("GET", url, headers=_auth_headers(token))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(f"Не удалось разобрать ответ {_BATCHES_PATH} как JSON: {text[:200]!r}") from exc

    if isinstance(data, dict):
        for key in ("batches", "items", "results"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise PublishError(
            f"Неожиданный формат ответа {_BATCHES_PATH} (dict без списка пачек): {list(data.keys())}"
        )
    if isinstance(data, list):
        return data
    raise PublishError(f"Неожиданный формат ответа {_BATCHES_PATH}: {type(data)}")


def _delete_old_batches(base_url, token, lesnichestvo_num=None):
    batches = _list_batches(base_url, token, lesnichestvo_num)
    to_delete = [b for b in batches if _extract_layer_name(b) == LAYER_NAME]

    deleted = []
    for batch in to_delete:
        batch_id = _extract_batch_id(batch)
        if not batch_id:
            raise PublishError(
                "Нашлась старая пачка со слоем "
                f"{LAYER_NAME!r}, но не удалось определить её batch_id "
                f"(формат записи: {batch!r}). Публикация остановлена, "
                "чтобы не удалить не ту пачку по ошибке."
            )
        url = base_url.rstrip("/") + _DELETE_BATCH_PATH.format(batch_id=urlparse.quote(str(batch_id), safe=""))
        _request("DELETE", url, headers=_auth_headers(token))
        deleted.append(batch_id)
    return deleted


def _build_multipart(fields, file_field_name, filename, file_bytes, content_type):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def publish(base_url, token, features, lesnichestvo_num=None):
    """Полностью заменяет пачку layer_name="qgis_lesoseki" на сервере
    «Лесовод»: удаляет прежние загрузки с этим именем слоя, затем
    грузит новый GeoJSON-файл с контурами лесосек."""
    if not base_url:
        raise PublishError("Не задан адрес сервера «Лесовод» (настройки плагина).")

    deleted = _delete_old_batches(base_url, token, lesnichestvo_num)

    geojson_bytes = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
    ).encode("utf-8")

    query = {"layer_name": LAYER_NAME}
    if lesnichestvo_num:
        query["lesnichestvo_num"] = lesnichestvo_num
    url = base_url.rstrip("/") + _IMPORT_PATH + "?" + urlparse.urlencode(query)

    content_type = mimetypes.guess_type("qgis_lesoseki.geojson")[0] or "application/geo+json"
    body, multipart_content_type = _build_multipart(
        fields={},
        file_field_name="file",
        filename="qgis_lesoseki.geojson",
        file_bytes=geojson_bytes,
        content_type=content_type,
    )

    headers = _auth_headers(token)
    headers["Content-Type"] = multipart_content_type

    status, response_text = _request("POST", url, headers=headers, data=body)
    return status, response_text, deleted
