"""
Слои с сервера «Лесовод» в QGIS (обратное направление к публикации лесосек).

Каждый слой — GeoJSON по адресу сервера с ?token=... (тот же токен, что в
настройках плагина): QGIS подключает URL как обычный векторный слой, свой
заголовок Authorization в такой URL не вставить. Кнопка «Обновить слои
Лесовода» перечитывает все такие слои без повторного добавления.

- Метки рабочих — точки с цветом по типу, подписью (текст метки), а во
  всплывающей подсказке (Map Tips) — тип, автор, дата и фото.
- Делянки — выделы с делянками, цвет по статусу работ (ожидает / в работе /
  выполнено — те же цвета, что на телефоне и на сайте).
- Лесные культуры — выделы с участками лесных культур.
- Обмеры с телефона — контуры, обойдённые рабочими по GPS, с площадью.
"""

from urllib import parse as urlparse

from qgis.core import (
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

_KIND_PROPERTY = "lesovod_bridge/kind"


class ServerLayerError(Exception):
    pass


def _url(base_url, path, token, **params):
    if not base_url:
        raise ServerLayerError("Не задан адрес сервера «Лесовод» (настройки плагина).")
    if not token:
        raise ServerLayerError("Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»).")
    query = {"token": token}
    query.update({k: v for k, v in params.items() if v})
    return base_url.rstrip("/") + path + "?" + urlparse.urlencode(query)


def _labels(layer, expression, size=9, color="#000000"):
    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True
    text_format = QgsTextFormat()
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(1)
    buffer.setColor(QColor("#ffffff"))
    text_format.setBuffer(buffer)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _replace_layer(kind, layer):
    """Одного вида — один слой: повторное нажатие кнопки заменяет прежний."""
    project = QgsProject.instance()
    for existing in list(project.mapLayers().values()):
        if existing.customProperty(_KIND_PROPERTY) == kind:
            project.removeMapLayer(existing.id())
    layer.setCustomProperty(_KIND_PROPERTY, kind)
    project.addMapLayer(layer)
    return layer


def _load(url, title):
    layer = QgsVectorLayer(url, title, "ogr")
    if not layer.isValid():
        raise ServerLayerError(
            f"QGIS не смог открыть слой «{title}». Проверьте адрес сервера и токен в настройках "
            "плагина и что сервер обновлён (нужны адреса /api/map/qgis/... и /api/map/geo-notes.geojson)."
        )
    return layer


def add_geo_notes(base_url, token):
    url = _url(base_url, "/api/map/geo-notes.geojson", token)
    layer = _load(url, "Метки рабочих (Лесовод)")

    symbol = QgsMarkerSymbol.createSimple({"name": "circle", "size": "4", "outline_color": "#000000", "outline_width": "0.3"})
    symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, QgsProperty.fromField("color"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _labels(layer, 'coalesce("note_text", "kategoriya_label")', size=8)

    # Всплывающая подсказка (Вид -> Подсказки карты): фото грузится с сервера с тем же токеном
    photo_base = base_url.rstrip("/")
    token_q = urlparse.quote(token, safe="")
    layer.setDisplayExpression('coalesce("kategoriya_label", \'Метка\') || \' — \' || coalesce("author_fio", \'\')')
    layer.setMapTipTemplate(
        "<b>[% \"kategoriya_label\" %]</b><br>"
        "[% coalesce(\"note_text\", '') %]<br>"
        "<small>[% coalesce(\"author_fio\", '') %] · [% coalesce(\"created_at\", '') %]</small><br>"
        "[% CASE WHEN \"photo_url\" IS NOT NULL THEN "
        f"'<img src=\"{photo_base}' || \"photo_url\" || '?token={token_q}\" width=\"320\">' "
        "ELSE '' END %]"
    )
    return _replace_layer("geo_notes", layer)


def add_delyanki(base_url, token, lesnichestvo_num=None):
    url = _url(base_url, "/api/map/qgis/delyanki.geojson", token, lesnichestvo_num=lesnichestvo_num)
    layer = _load(url, "Делянки по статусу (Лесовод)")
    symbol = QgsFillSymbol.createSimple({"color": "#ff9800", "outline_color": "#333333", "outline_width": "0.4"})
    symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, QgsProperty.fromExpression("set_color_part(\"color\", 'alpha', 120)"))
    symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromField("color"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _labels(layer, 'coalesce("nazvanie", \'\') || \'\\n\' || coalesce("status_rabot", \'\')')
    layer.setMapTipTemplate(
        "<b>[% coalesce(\"nazvanie\", 'Делянка') %]</b><br>"
        "кв. [% \"kvartal\" %], выд. [% \"vydel\" %]<br>Статус: [% \"status_rabot\" %]"
    )
    return _replace_layer("delyanki", layer)


def add_lesokultury(base_url, token, lesnichestvo_num=None):
    url = _url(base_url, "/api/map/qgis/lesokultury.geojson", token, lesnichestvo_num=lesnichestvo_num)
    layer = _load(url, "Лесные культуры (Лесовод)")
    symbol = QgsFillSymbol.createSimple({
        "color": "0,229,255,40", "outline_color": "#00b8d4", "outline_width": "0.6", "outline_style": "dash",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _labels(layer, 'coalesce("glavnaya_poroda", \'\') || \' \' || coalesce("god_sozdaniya", \'\')', color="#006064")
    return _replace_layer("lesokultury", layer)


def add_tracks(base_url, token):
    url = _url(base_url, "/api/map/qgis/tracks.geojson", token)
    layer = _load(url, "Обмеры с телефона (Лесовод)")
    # обмер всегда замкнут — полигон
    fill = QgsFillSymbol.createSimple({"color": "0,230,118,30", "outline_color": "#00c853", "outline_width": "0.8"})
    layer.setRenderer(QgsSingleSymbolRenderer(fill))
    _labels(layer, 'coalesce("nazvanie", \'Обмер\') || \'\\n\' || coalesce(to_string("ploshad_ga"), \'\') || \' га\'', color="#1b5e20")
    return _replace_layer("tracks", layer)


def reload_all():
    """Перечитать все слои Лесовода (метки, делянки, …) с сервера."""
    count = 0
    for layer in QgsProject.instance().mapLayers().values():
        if layer.customProperty(_KIND_PROPERTY):
            layer.dataProvider().reloadData()
            layer.triggerRepaint()
            count += 1
    return count
