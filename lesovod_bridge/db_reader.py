"""
Чтение таблицы area из базы ГИСлесхоз (RUP «Белгослес», PostgreSQL/PostGIS).

Жёсткое правило: подключение только на чтение. Ни один запрос здесь не
пишет и не меняет данные в этой базе — это чужая официальная программа,
её нельзя трогать.

Имена столбцов взяты дословно из models/public.py плагина ГИСлесхоз
(таблица area) — не переименовывать и не угадывать другие варианты.
"""

import json
import struct

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


_HEX = set("0123456789abcdefABCDEF")

_EWKB_Z = 0x80000000
_EWKB_M = 0x40000000
_EWKB_SRID = 0x20000000


class _WkbReader:
    """Разбор WKB/EWKB (PostGIS) в GeoJSON без сторонних библиотек."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.srid = None

    def _unpack(self, fmt, size):
        value = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def geometry(self):
        (order,) = self._unpack("B", 1)
        e = "<" if order == 1 else ">"
        (gtype,) = self._unpack(e + "I", 4)
        has_z = bool(gtype & _EWKB_Z)
        has_m = bool(gtype & _EWKB_M)
        if gtype & _EWKB_SRID:
            (srid,) = self._unpack(e + "I", 4)
            if self.srid is None:
                self.srid = srid
        gtype &= 0x0FFFFFFF
        # ISO WKB: 1001 = Z, 2001 = M, 3001 = ZM
        if gtype >= 1000:
            iso = gtype // 1000
            has_z = has_z or iso in (1, 3)
            has_m = has_m or iso in (2, 3)
            gtype %= 1000
        dims = 2 + has_z + has_m

        def point():
            values = self._unpack(e + "d" * dims, 8 * dims)
            return list(values[:3] if has_z else values[:2])

        def points():
            (n,) = self._unpack(e + "I", 4)
            return [point() for _ in range(n)]

        def rings():
            (n,) = self._unpack(e + "I", 4)
            return [points() for _ in range(n)]

        if gtype == 1:
            return {"type": "Point", "coordinates": point()}
        if gtype == 2:
            return {"type": "LineString", "coordinates": points()}
        if gtype == 3:
            return {"type": "Polygon", "coordinates": rings()}
        if gtype in (4, 5, 6, 7):
            (n,) = self._unpack(e + "I", 4)
            parts = [self.geometry() for _ in range(n)]
            if gtype == 7:
                return {"type": "GeometryCollection", "geometries": parts}
            name = {4: "MultiPoint", 5: "MultiLineString", 6: "MultiPolygon"}[gtype]
            return {"type": name, "coordinates": [p["coordinates"] for p in parts]}
        raise GisleshozReadError(f"неподдерживаемый тип геометрии WKB: {gtype}")


def _geom_text_to_geojson(geom_text):
    """Поле geom в area. Возвращает (geometry, srid или None).

    На практике (28.09.2026) это столбец PostGIS geometry: psycopg2 отдаёт его
    строкой hex EWKB ("0106000020 7B7F0000 ..." — MultiPolygon, SRID 32635).
    Также понимаем двоичный WKB, GeoJSON и WKT."""
    if isinstance(geom_text, (bytes, bytearray, memoryview)):
        reader = _WkbReader(bytes(geom_text))
        return reader.geometry(), reader.srid

    text = (geom_text or "").strip()
    if not text:
        raise GisleshozReadError("пустое поле geom")

    if text.startswith("{"):
        return json.loads(text), None

    if len(text) % 2 == 0 and text[:2] in ("00", "01") and set(text) <= _HEX:
        try:
            reader = _WkbReader(bytes.fromhex(text))
            return reader.geometry(), reader.srid
        except struct.error as exc:
            raise GisleshozReadError(f"обрезанная геометрия WKB: {text[:40]!r}…") from exc

    if text.upper().startswith("SRID="):
        srid_part, _, text = text.partition(";")
        srid = int(srid_part[5:])
    else:
        srid = None

    from osgeo import ogr

    geom = ogr.CreateGeometryFromWkt(text)
    if geom is None:
        raise GisleshozReadError(f"не удалось разобрать geom (ни WKB, ни WKT): {text[:80]!r}")
    return json.loads(geom.ExportToJson()), srid


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
            geometry, srid = _geom_text_to_geojson(geom_text)
        except (GisleshozReadError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"лесосека uid={uid}: {exc}")
            continue

        properties = {}
        for key, value in record.items():
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            properties[key] = value

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        }
        if srid:
            feature["srid"] = srid  # для пересчёта в градусы; на сервер не уходит
        features.append(feature)

    return features, errors
