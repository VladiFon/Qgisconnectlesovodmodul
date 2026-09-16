"""
Чтение таблицы area из базы ГИСлесхоз (RUP «Белгослес», PostgreSQL/PostGIS).

Жёсткое правило: подключение только на чтение. Ни один запрос здесь не
пишет и не меняет данные в этой базе — это чужая официальная программа,
её нельзя трогать.

Имена столбцов взяты дословно из models/public.py плагина ГИСлесхоз
(таблица area) — не переименовывать и не угадывать другие варианты.
"""

import json

import psycopg2

# Точные имена столбцов таблицы area (models/public.py плагина ГИСлесхоз).
_COLUMNS = [
    "geom",
    "uid",
    "num_lch",
    "num_kv",
    "num_vds",
    "area",
    "leshos",
    "num",
    "usetype",
    "cuttingtyp",
    "fio",
    "date",
    "info",
    "leshos_text",
    "lesnich_text",
]


class GisleshozReadError(Exception):
    pass


def _connect_readonly(db_host, db_port, db_name, db_user, db_password):
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        # На уровне сессии БД тоже запрещаем запись — защита от опечатки
        # в будущем SQL-запросе, а не только "мы просто не пишем INSERT".
        options="-c default_transaction_read_only=on",
        connect_timeout=10,
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _geom_text_to_geojson(geom_text):
    """Поле geom в area — текстовое. Формат заранее не документирован
    (WKT или уже GeoJSON) — пробуем оба варианта, ничего не придумываем
    сверх этого."""
    text = (geom_text or "").strip()
    if not text:
        raise GisleshozReadError("пустое поле geom")

    if text.startswith("{"):
        return json.loads(text)

    from osgeo import ogr

    geom = ogr.CreateGeometryFromWkt(text)
    if geom is None:
        raise GisleshozReadError(f"не удалось разобрать geom как WKT: {text[:80]!r}")
    return json.loads(geom.ExportToJson())


def fetch_lesoseki(db_host, db_port, db_name, db_user, db_password, schema="public"):
    """Возвращает (features, errors):
    - features: список dict в форме GeoJSON Feature (geometry + properties
      со всеми столбцами area, кроме geom).
    - errors: список текстовых предупреждений по строкам, где geometry не
      удалось разобрать (эти строки просто пропускаются, а не роняют всю
      публикацию).
    """
    conn = _connect_readonly(db_host, db_port, db_name, db_user, db_password)
    try:
        columns_sql = ", ".join(f'"{c}"' for c in _COLUMNS)
        query = f'SELECT {columns_sql} FROM "{schema}"."area"'
        with conn.cursor() as cur:
            cur.execute(query)
            colnames = [d[0] for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    features = []
    errors = []
    for row in rows:
        record = dict(zip(colnames, row))
        geom_text = record.pop("geom")
        uid = record.get("uid")
        try:
            geometry = _geom_text_to_geojson(geom_text)
        except (GisleshozReadError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"лесосека uid={uid}: {exc}")
            continue

        properties = {}
        for key, value in record.items():
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            properties[key] = value

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        })

    return features, errors
