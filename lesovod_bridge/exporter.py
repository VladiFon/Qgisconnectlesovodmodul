"""
Выгрузка выбранных лесосек из базы ГИСлесхоз в shp (с .prj) — для
текущих изменений (приказ Минлесхоза №130 от 10.06.2026: полигоны сдаются
в РУП «Белгослес» shp-файлами с файлом *.prj).

В самом ГИСлесхоз выгрузка есть, но лесосеки там идут одним списком без
поиска и фильтра. Здесь — те же лесосеки из таблицы area (только чтение),
но с фильтром по лесничеству, кварталу, году, виду рубки и поиском.

Координаты не пересчитываются: shp пишется в той системе координат, в
которой лесосеки лежат в базе ГИСлесхоз (SRID из самой геометрии, обычно
32635 — UTM 35N). Модуль без зависимостей от QGIS, кроме osgeo (GDAL идёт
в составе QGIS).
"""

import json
import os

# Поля shp: имя в dbf (не длиннее 10 символов) -> ключ в properties лесосеки.
# Имена столбцов area оставлены как в ГИСлесхоз, чтобы файл читался так же,
# как его собственная выгрузка; два длинных имени укорочены.
SHP_FIELDS = [
    ("uid", "uid"),
    ("num_lch", "num_lch"),
    ("num_kv", "num_kv"),
    ("num_vds", "num_vds"),
    ("area", "area"),
    ("leshos", "leshos"),
    ("num", "num"),
    ("usetype", "usetype"),
    ("cuttingtyp", "cuttingtyp"),
    ("fio", "fio"),
    ("date", "date"),
    ("info", "info"),
    ("leshos_txt", "leshos_text"),
    ("lesnch_txt", "lesnich_text"),
]

ENCODINGS = ["UTF-8", "CP1251"]


class ExportError(Exception):
    pass


def as_text(value):
    return "" if value is None else str(value).strip()


def year_of(props):
    """Год лесосеки из поля date ("2026-03-14", "2026-03-14T00:00:00" и т.п.)."""
    date = as_text(props.get("date"))
    return date[:4] if len(date) >= 4 and date[:4].isdigit() else ""


def lesnichestvo_of(props):
    """Подпись лесничества для фильтра: название, если есть, иначе номер."""
    name = as_text(props.get("lesnich_text"))
    num = as_text(props.get("num_lch"))
    if name and num:
        return f"{name} ({num})"
    return name or num


def _split_list(text):
    """"12, 14 20" -> {"12", "14", "20"}; пустая строка -> пустое множество."""
    parts = text.replace(";", ",").replace(" ", ",").split(",")
    return {p.strip().lstrip("0") or "0" for p in parts if p.strip()}


def _norm_kv(value):
    text = as_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text.lstrip("0") or ("0" if text else "")


def choices(features):
    """Значения для выпадающих списков диалога: лесничества, годы, виды
    рубки, виды пользования — по тем лесосекам, что реально есть в базе."""
    result = {"lesnichestvo": set(), "year": set(), "cuttingtyp": set(), "usetype": set()}
    for f in features:
        p = f["properties"]
        result["lesnichestvo"].add(lesnichestvo_of(p))
        result["year"].add(year_of(p))
        result["cuttingtyp"].add(as_text(p.get("cuttingtyp")))
        result["usetype"].add(as_text(p.get("usetype")))
    return {
        key: sorted((v for v in values if v), reverse=(key == "year"))
        for key, values in result.items()
    }


def filter_features(features, lesnichestvo="", kvartaly="", year="", cuttingtyp="", usetype="", search=""):
    """Отбор лесосек. Пустое значение фильтра = без ограничения.
    kvartaly — список кварталов через запятую/пробел; search — подстрока
    (без учёта регистра) в номере лесосеки, выделах, ФИО, примечании."""
    kv_set = _split_list(kvartaly) if kvartaly.strip() else None
    needle = search.strip().lower()
    out = []
    for f in features:
        p = f["properties"]
        if lesnichestvo and lesnichestvo_of(p) != lesnichestvo:
            continue
        if kv_set is not None and _norm_kv(p.get("num_kv")) not in kv_set:
            continue
        if year and year_of(p) != year:
            continue
        if cuttingtyp and as_text(p.get("cuttingtyp")) != cuttingtyp:
            continue
        if usetype and as_text(p.get("usetype")) != usetype:
            continue
        if needle:
            hay = " ".join(as_text(p.get(k)) for k in ("num", "num_kv", "num_vds", "fio", "info", "uid")).lower()
            if needle not in hay:
                continue
        out.append(f)
    return out


def _srs_for(features, fallback_epsg=None):
    """Система координат выгрузки: SRID геометрии из базы, иначе EPSG из
    настроек плагина. Разные SRID в одной выгрузке — ошибка (shp один)."""
    from osgeo import osr

    srids = {f.get("srid") for f in features if f.get("srid")}
    if len(srids) > 1:
        raise ExportError(
            "Выбранные лесосеки лежат в разных системах координат (SRID "
            + ", ".join(str(s) for s in sorted(srids))
            + "). Выгрузите их по отдельности."
        )
    epsg = next(iter(srids), None) or (int(fallback_epsg) if fallback_epsg else None)
    if not epsg:
        raise ExportError(
            "Не известна система координат лесосек: в геометрии нет SRID. "
            "Укажите EPSG в «Настройках Лесовод-моста»."
        )
    srs = osr.SpatialReference()
    if srs.ImportFromEPSG(int(epsg)) != 0:
        raise ExportError(f"Неизвестный EPSG: {epsg}")
    return srs, int(epsg)


def write_shapefile(path, features, fallback_epsg=None, encoding="UTF-8"):
    """Пишет выбранные лесосеки в shp (+ shx, dbf, prj, cpg).
    Возвращает (число записанных, EPSG). Существующий файл с тем же
    именем перезаписывается целиком."""
    from osgeo import ogr

    if not features:
        raise ExportError("Не выбрано ни одной лесосеки.")
    if not path.lower().endswith(".shp"):
        path += ".shp"
    srs, epsg = _srs_for(features, fallback_epsg)

    driver = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(path):
        driver.DeleteDataSource(path)
    ds = driver.CreateDataSource(path)
    if ds is None:
        raise ExportError(f"Не удалось создать файл {path}")
    name = os.path.splitext(os.path.basename(path))[0]
    layer = ds.CreateLayer(name, srs, ogr.wkbPolygon, options=[f"ENCODING={encoding}"])

    for shp_name, key in SHP_FIELDS:
        if key == "area":
            fd = ogr.FieldDefn(shp_name, ogr.OFTReal)
            fd.SetWidth(12)
            fd.SetPrecision(4)
        else:
            fd = ogr.FieldDefn(shp_name, ogr.OFTString)
            fd.SetWidth(254)
        layer.CreateField(fd)

    written = 0
    for f in features:
        geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
        if geom is None:
            continue
        geom.FlattenTo2D()
        feat = ogr.Feature(layer.GetLayerDefn())
        for shp_name, key in SHP_FIELDS:
            value = f["properties"].get(key)
            if value is None or value == "":
                continue
            if key == "area":
                try:
                    feat.SetField(shp_name, float(str(value).replace(",", ".")))
                except ValueError:
                    pass
            else:
                feat.SetField(shp_name, str(value))
        feat.SetGeometry(geom)
        layer.CreateFeature(feat)
        written += 1
    ds = None  # закрыть и сбросить файлы на диск
    return written, epsg
