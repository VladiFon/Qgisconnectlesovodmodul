"""
Окраска слоёв с легендой: по статусу работ, по виду рубки (ССР, УЗ, ПРЖ…),
по виду пользования (главное / промежуточное / прочие — как в ГИСлесхозе) и
по виду лесных культур.

Справочник с цветами берётся с сервера «Лесовод» (/api/map/qgis/legendy.json),
при недоступности сервера — из legendy_default.json рядом с плагином. Цвета
те же, что в приложении и на сайте.

Слой «Лесосеки» самого ГИСлесхоза плагин тоже умеет раскрасить: вид рубки
определяется по тексту поля cuttingtyp, вид пользования — по usetype. Меняется
только стиль слоя в проекте QGIS, база ГИСлесхоза не трогается.
"""

import json
import time
import os
from urllib import parse as urlparse
from urllib import request as urlrequest

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsProject,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

STATUSY = [
    {"kod": "ожидает", "label": "Ожидает", "color": "#ff9800"},
    {"kod": "в работе", "label": "В работе", "color": "#ffd600"},
    {"kod": "выполнено", "label": "Выполнено", "color": "#43a047"},
]
SERYY = "#9e9e9e"

# режим -> (поле слоя делянок, ключ справочника или None для статусов)
REZHIMY_DELYANOK = {
    "status": ("status_rabot", None),
    "vid": ("vid_rubki_kod", "vidy_rubok"),
    "gruppa": ("gruppa", "gruppy_polzovaniya"),
}


def _default_legendy():
    path = os.path.join(os.path.dirname(__file__), "legendy_default.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# справочник с сервера на время работы QGIS: раньше его качали заново при
# каждом добавлении слоя и каждом диалоге (до 15 с ожидания на каждый)
_KESH = {}
KESH_SEKUND = 600
NET_SVYAZI_SEKUND = 60  # после неудачи не стучимся снова минуту


def load_legendy(base_url=None, token=None):
    """Справочник с сервера; старый сервер или нет связи — встроенный."""
    if base_url and token:
        from .publisher import USER_AGENT

        klyuch = (base_url.rstrip("/"), token)
        zapis = _KESH.get(klyuch)
        if zapis and time.time() - zapis[0] < (KESH_SEKUND if zapis[1] else NET_SVYAZI_SEKUND):
            return zapis[1] or _default_legendy()
        url = base_url.rstrip("/") + "/api/map/qgis/legendy.json?" + urlparse.urlencode({"token": token})
        req = urlrequest.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        data = None
        try:
            with urlrequest.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not (isinstance(data, dict) and data.get("vidy_rubok")):
                data = None
        except Exception:  # noqa: BLE001 — нет связи / сервер без этого адреса
            data = None
        _KESH[klyuch] = (time.time(), data)
        if data:
            return data
    return _default_legendy()


def _fill(color, alpha=120):
    c = QColor(color)
    fill = QColor(c)
    fill.setAlpha(alpha)
    symbol = QgsFillSymbol.createSimple({"outline_width": "0.5"})
    symbol.symbolLayer(0).setFillColor(fill)
    symbol.symbolLayer(0).setStrokeColor(c)
    return symbol


def categorized(field_or_expression, items, other_label="Не указан", s_kodom=False):
    """items: [{"kod", "label", "color"}] — категория на каждый код + «прочее».
    s_kodom — в легенде «УЗ — Уборка захламленности»."""
    categories = []
    for it in items:
        if not it.get("kod"):
            continue
        label = f'{it["kod"]} — {it["label"]}' if s_kodom else it["label"]
        categories.append(QgsRendererCategory(it["kod"], _fill(it["color"]), label))
    categories.append(QgsRendererCategory("", _fill(SERYY), other_label))
    return QgsCategorizedSymbolRenderer(field_or_expression, categories)


def renderer_delyanok(rezhim, legendy):
    field, key = REZHIMY_DELYANOK[rezhim]
    if key is None:
        return categorized(field, STATUSY, "Статус не указан")
    items = legendy.get(key) or []
    if key == "gruppy_polzovaniya":
        return categorized(field, items, "Вид пользования не указан")
    return categorized(field, items, "Вид рубки не указан", s_kodom=True)


def renderer_kultur(legendy):
    return categorized("vid_kultur_kod", legendy.get("vidy_kultur") or [], "Вид не указан")


# ------------------------------------------------ слой «Лесосеки» ГИСлесхоза ---

def _sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


def _norm_expr(field):
    # как app/vidy.py на сервере: нижний регистр, ё -> е, «несплош» не должно
    # совпадать со «сплош»
    return (f"replace(replace(lower(coalesce(\"{field}\", '')), 'ё', 'е'), 'несплош', 'несплш')")


def expr_vid_rubki(field, legendy):
    """CASE-выражение QGIS: код вида рубки по тексту поля (cuttingtyp)."""
    text = _norm_expr(field)
    whens = []
    for vid in legendy.get("vidy_rubok") or []:
        for slova in vid.get("slova") or []:
            cond = " AND ".join(f"strpos({text}, {_sql_text(s)}) > 0" for s in slova)
            if vid["kod"] == "СПЛ":
                cond += f" AND strpos({text}, 'несплш') = 0"
            whens.append(f"WHEN {cond} THEN {_sql_text(vid['kod'])}")
    return "CASE " + " ".join(whens) + " ELSE '' END"


def expr_gruppa(usetype_field, cuttingtyp_field, legendy):
    """Вид пользования: по usetype, иначе — группа распознанного вида рубки."""
    text = _norm_expr(usetype_field)
    po_vidu = " ".join(
        f"WHEN {expr_vid_rubki(cuttingtyp_field, legendy)} = {_sql_text(v['kod'])} THEN {_sql_text(v['gruppa'])}"
        for v in legendy.get("vidy_rubok") or [] if v.get("kod") and v.get("gruppa")
    ) if cuttingtyp_field else ""
    return (
        "CASE "
        f"WHEN strpos({text}, 'главн') > 0 THEN 'glavnoe' "
        f"WHEN strpos({text}, 'промежут') > 0 THEN 'promezhutochnoe' "
        f"WHEN strpos({text}, 'проч') > 0 THEN 'prochie' "
        f"{po_vidu} ELSE '' END"
    )


def _field(layer, name):
    for f in layer.fields():
        if f.name().lower() == name:
            return f.name()
    return None


def gisleshoz_lesoseki_layers():
    """Слои проекта с полями cuttingtyp / usetype (слой «Лесосеки» ГИСлесхоза,
    его выгрузка в shp и т.п.)."""
    out = []
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and (_field(layer, "cuttingtyp") or _field(layer, "usetype")):
            out.append(layer)
    return out


def okrasit_gisleshoz(layer, rezhim, legendy):
    """rezhim: "vid" — по виду рубки, "gruppa" — по виду пользования."""
    cutting = _field(layer, "cuttingtyp")
    usetype = _field(layer, "usetype")
    if rezhim == "vid":
        if not cutting:
            raise ValueError(f"В слое «{layer.name()}» нет поля cuttingtyp (вид рубки).")
        renderer = categorized(expr_vid_rubki(cutting, legendy), legendy.get("vidy_rubok") or [],
                               "Вид рубки не распознан", s_kodom=True)
    else:
        if not usetype and not cutting:
            raise ValueError(f"В слое «{layer.name()}» нет полей usetype / cuttingtyp.")
        renderer = categorized(expr_gruppa(usetype or cutting, cutting, legendy),
                               legendy.get("gruppy_polzovaniya") or [], "Вид пользования не указан")
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return layer
