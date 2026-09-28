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

Изменения 28.09.2026:
- Имя слоя теперь «лесосеки_qgis» (кириллица): приложение на телефоне
  ищет слой лесосек по слову «лесосек», латинское "qgis_lesoseki" версия
  0.3.0 не находила. Старые пачки "qgis_lesoseki" удаляются при публикации.
- Лесосеки публикуются отдельной пачкой на каждое лесничество (по полю
  lesnich_text или num_lch таблицы area) — телефон запрашивает слой своего
  лесничества, и пачка без лесничества ему не видна. Номер лесничества в
  настройках плагина теперь необязателен: если он задан, всё уходит в него.
- Координаты не в градусах (например, UTM 35N) пересчитываются в WGS84
  по EPSG из настроек.
"""

import json
import mimetypes
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

LAYER_NAME = "лесосеки_qgis"
LEGACY_LAYER_NAMES = ("qgis_lesoseki",)
_OUR_LAYER_NAMES = (LAYER_NAME,) + LEGACY_LAYER_NAMES

_IMPORT_PATH = "/api/map/import-layer"
_BATCHES_PATH = "/api/map/import-layers/batches"
_DELETE_BATCH_PATH = "/api/map/import-layers/{batch_id}"
_LESNICHESTVA_PATH = "/api/map/lesnichestva"


class PublishError(Exception):
    pass


def _auth_headers(token):
    if not token:
        raise PublishError(
            "Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»)."
        )
    return {"Authorization": f"Bearer {token}"}


def _request(method, url, headers, data=None):
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        raise PublishError(
            f"Сервер «Лесовод» ответил ошибкой {exc.code} на {method} {url}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
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


def _delete_batches(base_url, token, batches):
    deleted = []
    for batch in batches:
        batch_id = _extract_batch_id(batch)
        if not batch_id:
            raise PublishError(
                "Нашлась старая пачка слоя лесосек, но не удалось определить её batch_id "
                f"(формат записи: {batch!r}). Публикация остановлена, "
                "чтобы не удалить не ту пачку по ошибке."
            )
        url = base_url.rstrip("/") + _DELETE_BATCH_PATH.format(batch_id=urlparse.quote(str(batch_id), safe=""))
        _request("DELETE", url, headers=_auth_headers(token))
        deleted.append(batch_id)
    return deleted


def fetch_lesnichestva(base_url):
    """{"Оршанское": 3, ...} — справочник сервера (без токена)."""
    status, text = _request("GET", base_url.rstrip("/") + _LESNICHESTVA_PATH, headers={})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(f"Не удалось разобрать список лесничеств: {text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"Неожиданный формат списка лесничеств: {type(data)}")
    return {str(name): str(num) for name, num in data.items()}


def _norm_name(text):
    text = str(text or "").lower().replace("ё", "е")
    for word in ("лесничество", "лесн.", "лесн"):
        text = text.replace(word, " ")
    return " ".join(text.split())


def group_by_lesnichestvo(features, lesnichestva, override_num=None):
    """Раскладывает лесосеки по номерам лесничеств сервера.
    Возвращает ({num: [features]}, [features без лесничества])."""
    if override_num:
        return {str(override_num): list(features)}, []

    by_name = {_norm_name(name): num for name, num in lesnichestva.items()}
    known_nums = set(lesnichestva.values())
    groups, unknown = {}, []
    for feature in features:
        props = feature.get("properties") or {}
        num = None
        name = _norm_name(props.get("lesnich_text"))
        if name:
            num = by_name.get(name)
            if num is None:
                # "Оршанское лесничество ГЛХУ ..." — ищем название как начало строки
                num = next((n for key, n in by_name.items() if key and name.startswith(key)), None)
        if num is None and props.get("num_lch") not in (None, ""):
            candidate = str(props.get("num_lch")).strip()
            if candidate in known_nums:
                num = candidate
        if num is None:
            unknown.append(feature)
        else:
            groups.setdefault(num, []).append(feature)
    return groups, unknown


# ------------------------------------------------------------- координаты ---

def _coords_look_like_degrees(geometry):
    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            yield c
        elif isinstance(c, (list, tuple)):
            for item in c:
                yield from walk(item)
    for x, y, *_ in walk((geometry or {}).get("coordinates") or []):
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            return False
    return True


def to_wgs84(features, source_epsg=None):
    """Сервер ждёт GeoJSON в градусах (EPSG:4326). База ГИСлесхоз может хранить
    метры (UTM 35N и т.п.) — тогда пересчитываем по EPSG из настроек."""
    if all(_coords_look_like_degrees(f.get("geometry")) for f in features):
        return features
    if not source_epsg:
        raise PublishError(
            "Координаты лесосек не в градусах (похоже на метры). Укажите в настройках плагина "
            "EPSG системы координат базы ГИСлесхоз (например, 32635 — UTM 35N) и опубликуйте снова."
        )
    from osgeo import ogr, osr

    src = osr.SpatialReference()
    if src.ImportFromEPSG(int(source_epsg)) != 0:
        raise PublishError(f"Неизвестный EPSG: {source_epsg}")
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    for srs in (src, dst):
        if hasattr(srs, "SetAxisMappingStrategy"):  # GDAL 3: порядок x=долгота, y=широта
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)

    out = []
    for feature in features:
        geom = ogr.CreateGeometryFromJson(json.dumps(feature["geometry"]))
        if geom is None:
            continue
        geom.Transform(transform)
        out.append({**feature, "geometry": json.loads(geom.ExportToJson())})
    return out


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


def _upload(base_url, token, features, lesnichestvo_num):
    geojson_bytes = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
    ).encode("utf-8")

    query = {"layer_name": LAYER_NAME, "lesnichestvo_num": lesnichestvo_num}
    url = base_url.rstrip("/") + _IMPORT_PATH + "?" + urlparse.urlencode(query)

    content_type = mimetypes.guess_type("lesoseki.geojson")[0] or "application/geo+json"
    body, multipart_content_type = _build_multipart(
        fields={},
        file_field_name="file",
        filename="lesoseki_qgis.geojson",
        file_bytes=geojson_bytes,
        content_type=content_type,
    )
    headers = _auth_headers(token)
    headers["Content-Type"] = multipart_content_type
    status, _text = _request("POST", url, headers=headers, data=body)
    return status


def publish(base_url, token, features, lesnichestvo_num=None, source_epsg=None):
    """Полностью заменяет лесосеки из QGIS на сервере «Лесовод»: по одной
    пачке на лесничество. Старые пачки (и с прежним латинским именем, и
    без лесничества) удаляются только для тех лесничеств, что публикуются
    сейчас, — чужие лесничества не трогаются.

    Возвращает dict: published {num: count}, deleted [batch_id], skipped [uid]."""
    if not base_url:
        raise PublishError("Не задан адрес сервера «Лесовод» (настройки плагина).")
    _auth_headers(token)  # сразу понятная ошибка, если токена нет

    features = to_wgs84(features, source_epsg)
    lesnichestva = fetch_lesnichestva(base_url)
    groups, unknown = group_by_lesnichestvo(features, lesnichestva, lesnichestvo_num)
    if not groups:
        raise PublishError(
            "Ни у одной лесосеки не удалось определить лесничество (поля lesnich_text / num_lch). "
            "Укажите номер лесничества в настройках плагина — тогда все лесосеки уйдут в него."
        )

    num_to_names = {}
    for name, num in lesnichestva.items():
        num_to_names.setdefault(num, set()).add(name)
    publishing_names = set()
    for num in groups:
        publishing_names |= num_to_names.get(num, set())

    old = [
        b for b in _list_batches(base_url, token)
        if _extract_layer_name(b) in _OUR_LAYER_NAMES
        and (not (b.get("lesnichestvo") if isinstance(b, dict) else None) or b.get("lesnichestvo") in publishing_names)
    ]
    deleted = _delete_batches(base_url, token, old)

    published = {}
    for num, items in sorted(groups.items()):
        _upload(base_url, token, items, num)
        published[num] = len(items)

    skipped = [str((f.get("properties") or {}).get("uid")) for f in unknown]
    return {"published": published, "deleted": deleted, "skipped": skipped, "lesnichestva": lesnichestva}
