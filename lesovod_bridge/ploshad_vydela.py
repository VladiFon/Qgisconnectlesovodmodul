"""
«Площадь делянки по выделам» — сколько гектаров делянка (лесосека) занимает
в каждом выделе, через который проходит. Лесосека часто режет 2–3 выдела,
а в документы (лесорубочный билет, технологическая карта, текущие
изменения) площадь нужна по каждому выделу отдельно.

Считается геометрически: контур делянки пересекается с полигонами слоя
«Выдела» ГИСлесхоза (ищется по имени, как для «Кварталы и выделы → Лесовод»).
Площадь — в метрической системе координат слоя выделов (как в ГИСлесхозе),
а если слой в градусах — в EPSG:32635.

Для делянок Лесовода рядом показывается, что записано по выделам в самом
Лесоводе, и подсвечивается, где записанное расходится с картой.

Ничего не меняет ни в базе ГИСлесхоза, ни в Лесоводе — только считает.
"""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from . import kartochki
from .granicy import nayti_sloy

ZAPASNAYA_SK = "EPSG:32635"
LCH_FIELDS = ("num_lch", "lesnich_text")
DELYANKI_KINDS = ("delyanki_status", "delyanki_vid", "delyanki_gruppa")
# None — свой слой / слой ГИСлесхоза; культуры — только со своим контуром (иначе это целый выдел)
DRUGIE_KINDS = (None, "lesokultury", "tracks", "proby")
NAZVANIYA = {"lesokultury": "Участок лесных культур", "tracks": "Обмер", "proby": "Проба"}
SLOVA_LESOSEK = ("лесосек",)


class PloshadError(Exception):
    pass


def _pole(layer, names):
    by_lower = {f.name().lower(): f.name() for f in layer.fields()}
    return next((by_lower[n] for n in names if n in by_lower), None)


def sloy_vydelov():
    """Слой «Выдела» ГИСлесхоза из проекта (с полями квартала и выдела) или PloshadError."""
    layer = nayti_sloy(("выдел",))
    if layer is None or _pole(layer, kartochki.KV_FIELDS) is None or _pole(layer, kartochki.VD_FIELDS) is None:
        raise PloshadError("В проекте нет слоя «Выдела» ГИСлесхоза с полями квартала и выдела (num_kv, num_vd) — "
                           "добавьте его, по нему считается площадь по выделам.")
    return layer


def eto_lesoseki(layer):
    """Слой лесосек ГИСлесхоза (по имени) — для него тоже показываем площадь по выделам."""
    return isinstance(layer, QgsVectorLayer) and not layer.customProperty("lesovod_bridge/kind") \
        and layer.geometryType() == QgsWkbTypes.PolygonGeometry \
        and any(s in layer.name().lower() for s in SLOVA_LESOSEK)


def _sk_rascheta(vydela):
    crs = vydela.crs()
    if crs.isValid() and not crs.isGeographic():
        return crs
    return QgsCoordinateReferenceSystem(ZAPASNAYA_SK)


def _ispravit(geom):
    if geom.isGeosValid():
        return geom
    try:
        fixed = geom.makeValid()
        if not fixed.isEmpty():
            return fixed
    except AttributeError:  # очень старый QGIS
        pass
    return geom.buffer(0, 5)


def _v_sk(geometry, crs_from, crs_to):
    geom = QgsGeometry(geometry)
    if crs_from != crs_to:
        geom.transform(QgsCoordinateTransform(crs_from, crs_to, QgsProject.instance()))
    return _ispravit(geom)


def poschitat(geometrii, crs, vydela, zapisano=None):
    """geometrii — контуры одной делянки (в crs), объединяются.
    Возвращает сводку kartochki.sobrat_po_vydelam."""
    sk = _sk_rascheta(vydela)
    kuski = [_v_sk(g, crs, sk) for g in geometrii if g is not None and not g.isEmpty()]
    if not kuski:
        raise PloshadError("У делянки нет контура.")
    delyanka = _ispravit(QgsGeometry.unaryUnion(kuski)) if len(kuski) > 1 else kuski[0]
    vsego = delyanka.area() / 10000.0

    k_vydelam = QgsCoordinateTransform(vydela.crs(), sk, QgsProject.instance())
    rect = QgsCoordinateTransform(sk, vydela.crs(), QgsProject.instance()).transformBoundingBox(delyanka.boundingBox())
    pole_kv, pole_vd = _pole(vydela, kartochki.KV_FIELDS), _pole(vydela, kartochki.VD_FIELDS)
    pole_lch = _pole(vydela, LCH_FIELDS)
    polya = [p for p in (pole_kv, pole_vd, pole_lch) if p]
    request = QgsFeatureRequest().setFilterRect(rect).setSubsetOfAttributes(polya, vydela.fields())

    engine = QgsGeometry.createGeometryEngine(delyanka.constGet())
    engine.prepareGeometry()
    rezultat = []
    for f in vydela.getFeatures(request):
        if not f.hasGeometry():
            continue
        vydel = _ispravit(QgsGeometry(f.geometry()))
        if vydela.crs() != sk:
            vydel.transform(k_vydelam)
        if not engine.intersects(vydel.constGet()):
            continue
        kusok = delyanka.intersection(vydel)
        if kusok.isEmpty():
            continue
        lch = kartochki.tekst(f[pole_lch]) if pole_lch and not kartochki.pusto(f[pole_lch]) else ""
        rezultat.append((lch, f[pole_kv], f[pole_vd], kusok.area() / 10000.0, vydel.area() / 10000.0))
    return kartochki.sobrat_po_vydelam(rezultat, vsego, zapisano)


def _delyanka_lesovoda(layer, feature):
    """Все объекты слоя делянок Лесовода той же делянки: (контуры, записано по выделам)."""
    names = feature.fields().names()
    d_id = feature["delyanka_id"] if "delyanka_id" in names else None
    if kartochki.pusto(d_id):
        features = [feature]
    else:
        request = QgsFeatureRequest().setFilterExpression(f'"delyanka_id" = {int(d_id)}')
        features = list(layer.getFeatures(request)) or [feature]
    geometrii, zapisano, vidennye = [], {}, set()
    for f in features:
        item = f["item_id"] if "item_id" in names else f.id()
        if item in vidennye:  # пункт делянки, нарезанный по нескольким выделам
            continue
        vidennye.add(item)
        ploshad = kartochki._chislo(f["ploshad"]) if "ploshad" in names else None
        if ploshad is not None:
            key = (kartochki.tekst(f["kvartal"]), kartochki.tekst(f["vydel"]))
            zapisano[key] = zapisano.get(key, 0.0) + ploshad
        # без своего контура сервер отдаёт полигон всего выдела — он делянкой не является
        if "has_kontur" in names and f["has_kontur"] in (False, 0, "false", "False"):
            continue
        if f.hasGeometry():
            geometrii.append(f.geometry())
    # тот же контур, привязанный к двум пунктам, — один раз
    unikalnye = []
    for g in geometrii:
        if not any(g.equals(u) for u in unikalnye):
            unikalnye.append(g)
    return unikalnye, zapisano, (None if kartochki.pusto(d_id) else str(d_id))


def klyuch(kind, layer, feature):
    """Одна делянка Лесовода приходит несколькими объектами (по пунктам) — считаем её один раз."""
    if kind in DELYANKI_KINDS and "delyanka_id" in feature.fields().names() \
            and not kartochki.pusto(feature["delyanka_id"]):
        return ("delyanka", str(feature["delyanka_id"]))
    return (layer.id(), feature.id())


def dlya_obekta(kind, layer, feature, vydela):
    """(klyuch, nazvanie, svodka) для делянки Лесовода или лесосеки ГИСлесхоза."""
    if kind in DELYANKI_KINDS:
        geometrii, zapisano, d_id = _delyanka_lesovoda(layer, feature)
        nazvanie = feature["nazvanie"] if "nazvanie" in feature.fields().names() else None
        nazvanie = kartochki.tekst(nazvanie) if not kartochki.pusto(nazvanie) else f"делянка {d_id or feature.id()}"
        if not geometrii:
            raise PloshadError(f"У делянки «{nazvanie}» нет своего контура лесосеки — на карте она показана "
                               "целыми выделами. Привяжите контур кнопкой «Привязать выбранное к делянке…».")
        return klyuch(kind, layer, feature), nazvanie, poschitat(geometrii, layer.crs(), vydela, zapisano)
    attrs = {f.name(): feature[f.name()] for f in feature.fields()}
    if kind == "lesokultury" and attrs.get("has_kontur") in (False, 0, "false", "False"):
        raise PloshadError("У участка культур нет своего контура — на карте он показан целым выделом.")
    nomer = kartochki.iz_polya(attrs, ("num", "nomer", "id", "nazvanie"))
    kv, vd = kartochki.iz_polya(attrs, kartochki.KV_FIELDS), kartochki.iz_polya(attrs, kartochki.VD_FIELDS)
    if kind in NAZVANIYA:
        nazvanie = NAZVANIYA[kind]
    else:
        nazvanie = "Лесосека" if eto_lesoseki(layer) else f"Полигон слоя «{layer.name()}»"
    nazvanie += (f" № {nomer}" if nomer else "") + (f", кв. {kv}" if kv else "") + (f" выд. {vd}" if vd else "")
    return klyuch(kind, layer, feature), nazvanie, poschitat([feature.geometry()], layer.crs(), vydela)
