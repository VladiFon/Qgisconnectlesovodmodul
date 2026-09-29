"""
«Отметить выбранное как лесные культуры» — в ГИСлесхозе признака л/к нет,
поэтому участок культур отмечают в QGIS: выделяют полигон(ы) на любом слое
(«Выдела» или «Лесосеки» ГИСлесхоза, свой нарисованный слой, выгрузка GPS),
выбирают вид культур, год и породу — и участок со своим контуром создаётся
в «Лесоводе» (POST /api/map/qgis/lesokultury). Дальше он виден на сайте в
«Лесных культурах», на телефоне и в слое «Лесные культуры по виду».

База ГИСлесхоза не меняется.
"""

import datetime
import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

KV_FIELDS = ("num_kv", "kvartal", "kv")
VD_FIELDS = ("num_vd", "num_vds", "vydel", "vd")
LCH_FIELDS = ("num_lch",)


class OtmetkaError(Exception):
    pass


def _attr(feature, names):
    fields = {f.name().lower(): f.name() for f in feature.fields()}
    for name in names:
        real = fields.get(name)
        if real is not None:
            value = feature[real]
            if value is not None and str(value).strip() not in ("", "NULL"):
                text = str(value).strip()
                return text[:-2] if text.endswith(".0") else text
    return ""


def vybrannye(layer):
    """Выделенные полигоны активного слоя — или OtmetkaError с понятным текстом."""
    if not isinstance(layer, QgsVectorLayer) or layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise OtmetkaError("Сделайте активным полигональный слой (например, «Выдела» или «Лесосеки» ГИСлесхоза).")
    features = list(layer.selectedFeatures())
    if not features:
        raise OtmetkaError(f"На слое «{layer.name()}» ничего не выделено — выделите участок(и) инструментом выбора.")
    return features


def to_wgs84_geojson(geometry, layer):
    geom = QgsGeometry(geometry)
    transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    geom.transform(transform)
    return json.loads(geom.asJson(7))


class OtmetkaDialog(QDialog):
    """Вид культур, год, порода; квартал и выдел — из атрибутов, для одного
    участка их можно поправить."""

    def __init__(self, features, vidy_kultur, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Отметить как лесные культуры")
        self.features = features
        form = QFormLayout(self)
        n = len(features)
        form.addRow(QLabel(f"Выделено участков: {n}. Каждый станет участком лесных культур со своим контуром."))

        self.vid = QComboBox()
        for v in vidy_kultur:
            self.vid.addItem(v["label"], v["kod"])
        form.addRow("Вид культур", self.vid)

        self.god = QSpinBox()
        self.god.setRange(1950, 2100)
        self.god.setValue(datetime.date.today().year)
        form.addRow("Год создания", self.god)

        self.poroda = QLineEdit()
        self.poroda.setPlaceholderText("например, С, Е, Д")
        form.addRow("Главная порода", self.poroda)

        self.kvartal = QLineEdit(_attr(features[0], KV_FIELDS))
        self.vydel = QLineEdit(_attr(features[0], VD_FIELDS))
        if n == 1:
            form.addRow("Квартал", self.kvartal)
            form.addRow("Выдел", self.vydel)
        else:
            form.addRow(QLabel("Квартал и выдел берутся из атрибутов каждого участка."))

        self.primechaniya = QLineEdit()
        form.addRow("Примечание", self.primechaniya)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Отметить")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def zapisi(self, layer, lesnichestvo_num):
        """Тела запросов к серверу — по одному на участок."""
        out = []
        for f in self.features:
            if len(self.features) == 1:
                kv, vd = self.kvartal.text().strip(), self.vydel.text().strip()
            else:
                kv, vd = _attr(f, KV_FIELDS), _attr(f, VD_FIELDS)
            if not kv:
                raise OtmetkaError("Не удалось взять квартал из атрибутов — отметьте участки по одному и впишите квартал.")
            out.append({
                "geometry": to_wgs84_geojson(f.geometry(), layer),
                "lesnichestvo_num": _attr(f, LCH_FIELDS) or lesnichestvo_num or None,
                "kvartal": kv,
                "vydel": vd,
                "vid_kultur": self.vid.currentData(),
                "god_sozdaniya": str(self.god.value()),
                "glavnaya_poroda": self.poroda.text().strip() or None,
                "primechaniya": self.primechaniya.text().strip() or None,
            })
        return out


def otpravit(base_url, token, body):
    from .publisher import USER_AGENT

    if not base_url or not token:
        raise OtmetkaError("Не заданы адрес сервера «Лесовод» и токен (настройки плагина).")
    url = base_url.rstrip("/") + "/api/map/qgis/lesokultury?" + urlparse.urlencode({"token": token})
    req = urlrequest.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            text = json.loads(text).get("detail", text)
        except (ValueError, AttributeError):
            pass
        if exc.code == 404:
            text = "сервер ещё не обновлён (нет /api/map/qgis/lesokultury)"
        raise OtmetkaError(f"Сервер «Лесовод» ответил {exc.code}: {text}") from exc
    except urlerror.URLError as exc:
        raise OtmetkaError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc
