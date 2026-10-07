"""
«Что здесь» — характеристики объектов Лесовода в точке карты.

Обычный инструмент QGIS «Определить» и выбор работают только по активному
слою: если активен «Выдела» ГИСлесхоза, щелчок по участку лесных культур
выбирает выдел, а характеристик культур не видно. Здесь щелчок смотрит сразу
во все видимые слои Лесовода (лесные культуры — первыми, затем делянки,
обмеры, метки) независимо от того, какой слой активен, и показывает карточки
в панели «Лесовод: что здесь» справа. Если объектов Лесовода в точке нет —
показывается выдел из слоя ГИСлесхоза (кв./выд.).

Тот же показ срабатывает, когда объект слоя Лесовода выделен обычным
инструментом выбора, и после поиска «кв 35 выд 12» на панели инструментов.
"""

from itertools import islice
from urllib import parse as urlparse

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsHighlight, QgsMapToolEmitPoint
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QDockWidget, QTextBrowser

from . import kartochki, settings

KIND_PROPERTY = "lesovod_bridge/kind"
# порядок карточек: культуры важнее всего (из-за них и сделано)
PORYADOK = ["lesokultury", "delyanki_status", "delyanki_vid", "delyanki_gruppa", "tracks", "geo_notes"]
MAX_KARTOCHEK = 30

PODSKAZKA = (
    "<p><b>Что здесь</b> — включите кнопку <i>«Что здесь»</i> на панели «Лесовод-мост» "
    "и щёлкните по карте: покажутся характеристики участка лесных культур, делянки, "
    "обмера или метки в этой точке — какой бы слой ни был активным.</p>"
    "<p>Можно и найти выдел: впишите на панели <i>«35 12»</i> (квартал и выдел) и нажмите Enter.</p>"
)


def lesovod_kind(layer):
    if not isinstance(layer, QgsVectorLayer):
        return None
    return layer.customProperty(KIND_PROPERTY) or None


def _vidimyy(layer):
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    return node is not None and node.isVisible()


def _attrs(feature):
    return {f.name(): feature[f.name()] for f in feature.fields()}


def _klyuch(kind, layer, feature):
    """Один и тот же объект Лесовода в разных объектах/слоях: участок культур
    в нескольких выделах, делянка в слоях по статусу и по виду рубки."""
    names = feature.fields().names()
    for field in ("item_id", "id"):
        if field in names and not kartochki.pusto(feature[field]):
            return kind.split("_")[0], str(feature[field])
    return layer.id(), feature.id()


def _est_polya_vydela(layer):
    names = {f.name().lower() for f in layer.fields()}
    return any(n in names for n in kartochki.KV_FIELDS) and any(n in names for n in kartochki.VD_FIELDS)


def _photo_src(attrs):
    url = attrs.get("photo_url")
    if kartochki.pusto(url):
        return None
    values = settings.load()
    base = (values.get("lesovod_base_url") or "").rstrip("/")
    token = values.get("lesovod_token") or ""
    return f"{base}{url}?token={urlparse.quote(token, safe='')}" if base else None


class ChtoZdes:
    """Панель с карточками + инструмент карты + подсветка найденного."""

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.highlights = []
        self.dock = QDockWidget("Лесовод: что здесь", iface.mainWindow())
        self.dock.setObjectName("LesovodBridgeChtoZdes")
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(PODSKAZKA)
        self.dock.setWidget(self.browser)
        self.dock.setMinimumWidth(300)
        self.dock.visibilityChanged.connect(self._dock_visibility)
        iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.canvasClicked.connect(self.v_tochke)

        self._podklyucheny = {}
        QgsProject.instance().layersAdded.connect(self._sloi_dobavleny)
        self._sloi_dobavleny(QgsProject.instance().mapLayers().values())

    # ------------------------------------------------------------ служебное --

    def unload(self):
        self.ubrat_podsvetku()
        try:
            QgsProject.instance().layersAdded.disconnect(self._sloi_dobavleny)
        except (TypeError, RuntimeError):
            pass
        for layer_id, slot in list(self._podklyucheny.items()):
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                try:
                    layer.selectionChanged.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._podklyucheny = {}
        if self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self.iface.removeDockWidget(self.dock)
        self.dock.deleteLater()

    def _dock_visibility(self, visible):
        if not visible:
            self.ubrat_podsvetku()

    def _sloi_dobavleny(self, layers):
        # слои Лесовода получают свой признак до добавления в проект (server_layers._replace_layer)
        for layer in layers:
            if lesovod_kind(layer) and layer.id() not in self._podklyucheny:
                slot = self._vydelenie_slot(layer)
                layer.selectionChanged.connect(slot)
                self._podklyucheny[layer.id()] = slot

    def _vydelenie_slot(self, layer):
        layer_id = layer.id()

        def slot(*_args):
            lr = QgsProject.instance().mapLayer(layer_id)
            if lr is None or lr.selectedFeatureCount() == 0:
                return
            kind = lesovod_kind(lr)
            naydeno = [(kind, lr, f) for f in islice(lr.getSelectedFeatures(), MAX_KARTOCHEK)]
            self.pokazat(naydeno, zagolovok=f"Выделено на слое «{lr.name()}»: {lr.selectedFeatureCount()}",
                         podsvetit=False)

        return slot

    def vklyuchit(self, action):
        """Включить инструмент «Что здесь» (кнопка панели)."""
        self.tool.setAction(action)
        self.canvas.setMapTool(self.tool)
        self.dock.show()
        self.dock.raise_()

    def ubrat_podsvetku(self):
        for h in self.highlights:
            try:
                self.canvas.scene().removeItem(h)
            except RuntimeError:
                pass
        self.highlights = []

    def _podsvetit(self, layer, feature):
        h = QgsHighlight(self.canvas, feature.geometry(), layer)
        h.setColor(QColor(255, 0, 0, 220))
        h.setFillColor(QColor(255, 0, 0, 40))
        h.setWidth(3)
        h.show()
        self.highlights.append(h)

    # -------------------------------------------------------------- поиск ---

    def _sloi(self):
        """(вид, слой) видимых слоёв Лесовода в порядке PORYADOK."""
        sloi = [(lesovod_kind(lr), lr) for lr in QgsProject.instance().mapLayers().values()]
        sloi = [(k, lr) for k, lr in sloi if k and _vidimyy(lr)]
        return sorted(sloi, key=lambda kl: PORYADOK.index(kl[0]) if kl[0] in PORYADOK else len(PORYADOK))

    def _sloi_gisleshoza(self):
        return [
            lr for lr in QgsProject.instance().mapLayers().values()
            if isinstance(lr, QgsVectorLayer) and not lesovod_kind(lr) and _vidimyy(lr)
            and lr.geometryType() == QgsWkbTypes.PolygonGeometry and _est_polya_vydela(lr)
        ]

    def _v_tochke_sloya(self, layer, point, radius):
        """Объекты слоя в точке (point и radius — в системе координат карты)."""
        transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), layer.crs(), QgsProject.instance())
        try:
            rect = transform.transformBoundingBox(
                QgsRectangle(point.x() - radius, point.y() - radius, point.x() + radius, point.y() + radius))
            pt = transform.transform(point)
        except Exception:  # noqa: BLE001 - точка вне области определения СК слоя
            return []
        request = QgsFeatureRequest().setFilterRect(rect)
        if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            return list(layer.getFeatures(request))
        tochka = QgsGeometry.fromPointXY(pt)
        return [f for f in layer.getFeatures(request) if f.hasGeometry() and f.geometry().intersects(tochka)]

    def v_tochke(self, point, _button=None):
        radius = self.canvas.mapUnitsPerPixel() * 5
        naydeno, vidennye = [], set()
        for kind, layer in self._sloi():
            for f in self._v_tochke_sloya(layer, point, radius):
                # участок в нескольких выделах приходит несколькими объектами с одним id
                klyuch = _klyuch(kind, layer, f)
                if klyuch in vidennye:
                    continue
                vidennye.add(klyuch)
                naydeno.append((kind, layer, f))
        if not naydeno:
            for layer in self._sloi_gisleshoza():
                naydeno.extend((None, layer, f) for f in self._v_tochke_sloya(layer, point, 0)[:3])
                if naydeno:
                    break
        self.pokazat(naydeno)

    def nayti(self, text):
        """Поиск по «кв выд» в слоях Лесовода и ГИСлесхоза; приближает карту."""
        kv, vd = kartochki.razobrat_poisk(text)
        if not kv:
            return
        kv, vd = kartochki.norm_id(kv), kartochki.norm_id(vd)

        def podkhodit(attrs):
            if kartochki.norm_id(kartochki.iz_polya(attrs, kartochki.KV_FIELDS)) != kv:
                return False
            if not vd:
                return True
            # участок культур в нескольких выделах: «12,13» / «12.1»
            vydely = kartochki.iz_polya(attrs, kartochki.VD_FIELDS).replace(";", ",").split(",")
            return any(kartochki.norm_id(v.split(".")[0]) == vd or kartochki.norm_id(v) == vd for v in vydely)

        def v_sloe(layer):
            # перебор — без геометрии (большой слой ГИСлесхоза из базы), геометрия — только найденным
            bez_geom = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
            ids = [f.id() for f in layer.getFeatures(bez_geom) if podkhodit(_attrs(f))]
            return list(layer.getFeatures(QgsFeatureRequest().setFilterFids(ids))) if ids else []

        naydeno = []
        for kind, layer in self._sloi():
            naydeno.extend((kind, layer, f) for f in v_sloe(layer))
        if not naydeno:
            for layer in self._sloi_gisleshoza():
                naydeno.extend((None, layer, f) for f in v_sloe(layer))
                if naydeno:
                    break
        mesto = f"кв. {kv}" + (f" выд. {vd}" if vd else "")
        if not naydeno:
            self.iface.messageBar().pushWarning("Лесовод-мост", f"{mesto}: не найдено в видимых слоях")
            return
        self.pokazat(naydeno[:MAX_KARTOCHEK], zagolovok=f"Найдено: {mesto}")
        self._priblizit(naydeno)

    def _priblizit(self, naydeno):
        dest = self.canvas.mapSettings().destinationCrs()
        extent = QgsRectangle()
        extent.setMinimal()
        for _kind, layer, f in naydeno:
            if not f.hasGeometry():
                continue
            geom = QgsGeometry(f.geometry())
            geom.transform(QgsCoordinateTransform(layer.crs(), dest, QgsProject.instance()))
            extent.combineExtentWith(geom.boundingBox())
        if extent.isNull():
            return
        if extent.width() == 0 or extent.height() == 0:  # точка (метка)
            extent.grow(self.canvas.mapUnitsPerPixel() * 100)
        extent.scale(1.3)
        self.canvas.setExtent(extent)
        self.canvas.refresh()

    # -------------------------------------------------------------- показ ---

    def pokazat(self, naydeno, zagolovok=None, podsvetit=True):
        self.ubrat_podsvetku()
        if not naydeno:
            est_kultury = any(k == "lesokultury" for k, _lr in self._sloi())
            body = "<p>В этой точке объектов Лесовода нет.</p>"
            if not est_kultury:
                body += ("<p>Слой лесных культур не добавлен или выключен — нажмите кнопку "
                         "<i>«Лесные культуры»</i> на панели «Лесовод-мост».</p>")
            self.browser.setHtml(body)
            self.dock.show()
            return
        parts = [f"<p style='color:#555'>{zagolovok}</p>"] if zagolovok else []
        for kind, layer, f in naydeno:
            attrs = _attrs(f)
            if kind is None:
                parts.append(kartochki.vydel(attrs, layer.name()))
            else:
                parts.append(kartochki.kartochka(kind, attrs, _photo_src(attrs) if kind == "geo_notes" else None))
            if podsvetit and f.hasGeometry():
                self._podsvetit(layer, f)
        self.browser.setHtml("".join(parts))
        self.dock.show()
        self.dock.raise_()
