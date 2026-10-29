"""
«Отправить кварталы и выделы в Лесовод» — границы для живой карты сайта и
телефона. Сервер держит их в двух файлах (map_kvartala.geojson,
map_vydela.geojson); раньше после обновления карт в ГИСлесхозе их
выгружали из QGIS руками и подкладывали на сервер.

Теперь: выбрать слои «Кварталы» и «Выдела» (ищутся по имени сами) —
плагин в фоне переводит их в EPSG:32635, оставляет только номера
лесничества / квартала / выдела, жмёт gzip, показывает, сколько чего
по лесничествам, и после «Да» отправляет (POST /api/map/qgis/granicy).
Сервер заменяет только пришедшие лесничества, старый файл хранит копией.

База ГИСлесхоза не меняется.
"""

import gzip
import io
import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel

from .otmetit_lk import KV_FIELDS, VD_FIELDS
from .publisher import _norm_name

CELEVAYA_SK = "EPSG:32635"  # в ней сервер читает файлы границ (forest_map.FOREST_MAP_SOURCE_EPSG)
TOCHNOST = 2  # знаков после запятой в метрах — сантиметры
TIMEOUT = 600  # выделы всего лесхоза — десятки мегабайт даже в gzip

SLOI = {
    # sloy: (подпись, слова в имени слоя, нужен ли выдел)
    "kvartaly": ("Кварталы", ("квартал",), False),
    "vydela": ("Выдела", ("выдел",), True),
}
LCH_NUM_FIELDS = ("num_lch",)
LCH_NAME_FIELDS = ("lesnich_text",)


class GranicyError(Exception):
    pass


def _poligonalnye():
    return [lr for lr in QgsProject.instance().mapLayers().values()
            if isinstance(lr, QgsVectorLayer) and lr.geometryType() == QgsWkbTypes.PolygonGeometry
            and not lr.customProperty("lesovod_bridge/kind")]


def nayti_sloy(slova):
    """Слой ГИСлесхоза по имени: сначала точное «Кварталы»/«Выдела», потом по подстроке."""
    sloi = _poligonalnye()
    for lr in sloi:
        if lr.name().strip().lower() in ("кварталы", "выдела", "выделы") and any(s in lr.name().lower() for s in slova):
            return lr
    return next((lr for lr in sloi if any(s in lr.name().lower() for s in slova)), None)


def _pole(layer, names):
    by_lower = {f.name().lower(): i for i, f in enumerate(layer.fields())}
    return next((by_lower[n] for n in names if n in by_lower), None)


class GranicyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Кварталы и выделы → Лесовод")
        form = QFormLayout(self)
        form.addRow(QLabel(
            "Границы кварталов и выделов для живой карты на сайте и в телефонах.\n"
            "Сервер заменит только те лесничества, что есть в слое, остальные не тронет;\n"
            "старые файлы останутся на сервере копией. Перед отправкой покажу, что уходит."))
        filtr = getattr(QgsMapLayerProxyModel, "PolygonLayer")
        self.vybor = {}
        for sloy, (podpis, slova, _vd) in SLOI.items():
            galka = QCheckBox(podpis)
            combo = QgsMapLayerComboBox(self)
            combo.setFilters(filtr)
            combo.setAllowEmptyLayer(True)
            nayden = nayti_sloy(slova)
            combo.setLayer(nayden)
            galka.setChecked(nayden is not None)
            combo.layerChanged.connect(lambda lr, g=galka: g.setChecked(lr is not None))
            form.addRow(galka, combo)
            self.vybor[sloy] = (galka, combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Подготовить")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def vybrannye(self):
        """{sloy: layer} — отмеченные галкой и выбранные."""
        out = {}
        for sloy, (galka, combo) in self.vybor.items():
            if galka.isChecked():
                if combo.currentLayer() is None:
                    raise GranicyError(f"Не выбран слой для «{SLOI[sloy][0]}».")
                out[sloy] = combo.currentLayer()
        if not out:
            raise GranicyError("Отметьте хотя бы один слой.")
        return out


def podgotovit(sloy, layer):
    """В главном потоке: всё, что нужно фоновой задаче, без самого слоя
    (слой из другого потока трогать нельзя — только его FeatureSource)."""
    podpis, _slova, nuzhen_vd = SLOI[sloy]
    i_lch = _pole(layer, LCH_NUM_FIELDS)
    i_name = _pole(layer, LCH_NAME_FIELDS)
    i_kv = _pole(layer, KV_FIELDS)
    i_vd = _pole(layer, VD_FIELDS) if nuzhen_vd else None
    if i_lch is None and i_name is None:
        raise GranicyError(f"В слое «{layer.name()}» нет поля лесничества (num_lch или lesnich_text).")
    if i_kv is None:
        raise GranicyError(f"В слое «{layer.name()}» нет поля квартала ({', '.join(KV_FIELDS)}).")
    if nuzhen_vd and i_vd is None:
        raise GranicyError(f"В слое «{layer.name()}» нет поля выдела ({', '.join(VD_FIELDS)}).")
    request = QgsFeatureRequest()
    request.setSubsetOfAttributes([i for i in (i_lch, i_name, i_kv, i_vd) if i is not None])
    return {
        "sloy": sloy,
        "podpis": podpis,
        "imya_sloya": layer.name(),
        "source": QgsVectorLayerFeatureSource(layer),
        "request": request,
        "transform": QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(CELEVAYA_SK),
                                            QgsProject.instance().transformContext()),
        "vsego": max(1, layer.featureCount()),
        "polya": (i_lch, i_name, i_kv, i_vd),
    }


# ----------------------------------------------------- в фоновом потоке --

def _znachenie(value):
    """QVariant/число/текст -> 35, «35а» или None."""
    if value is None:
        return None
    if hasattr(value, "isNull") and value.isNull():
        return None
    text = str(value).strip()
    if text in ("", "NULL"):
        return None
    try:
        f = float(text.replace(",", "."))
    except ValueError:
        return text
    return int(f) if f.is_integer() else text


def _num_lesnichestva(feature, i_lch, i_name, by_name, known_nums):
    """Как при публикации лесосек: сначала название (lesnich_text), потом num_lch,
    если такой номер сервер знает. Иначе None — объект не отправляем."""
    if i_name is not None:
        name = _norm_name(feature.attribute(i_name))
        if name:
            num = by_name.get(name) or next((n for key, n in by_name.items() if key and name.startswith(key)), None)
            if num is not None:
                return int(num)
    if i_lch is not None:
        num = _znachenie(feature.attribute(i_lch))
        if num is not None and str(num) in known_nums:
            return int(num)
    return None


def sobrat(task, zadanie, lesnichestva):
    """FeatureSource -> gzip GeoJSON в EPSG:32635 + сводка по лесничествам."""
    by_name = {_norm_name(name): str(num) for name, num in lesnichestva.items()}
    known_nums = {str(num) for num in lesnichestva.values()}
    nazvaniya = {str(num): name for name, num in lesnichestva.items()}
    i_lch, i_name, i_kv, i_vd = zadanie["polya"]
    transform = zadanie["transform"]

    buf = io.BytesIO()
    po_lesn, bez_lesn, bez_geom, n = {}, 0, 0, 0
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(b'{"type":"FeatureCollection","features":[')
        pervyy = True
        for i, feature in enumerate(zadanie["source"].getFeatures(zadanie["request"])):
            if task.isCanceled():
                return None
            if i % 500 == 0:
                task.setProgress(100.0 * i / zadanie["vsego"])
            geom = feature.geometry()
            if geom is None or geom.isEmpty() or geom.type() != QgsWkbTypes.PolygonGeometry:
                bez_geom += 1
                continue
            lch = _num_lesnichestva(feature, i_lch, i_name, by_name, known_nums)
            kv = _znachenie(feature.attribute(i_kv))
            if lch is None or kv is None:
                bez_lesn += 1
                continue
            props = {"num_lch": lch, "num_kv": kv}
            if i_vd is not None:
                props["num_vd"] = _znachenie(feature.attribute(i_vd))
            geom.transform(transform)
            obj = '{"type":"Feature","properties":%s,"geometry":%s}' % (
                json.dumps(props, ensure_ascii=False), geom.asJson(TOCHNOST))
            gz.write(("" if pervyy else ",").encode() + obj.encode("utf-8"))
            pervyy = False
            po_lesn[lch] = po_lesn.get(lch, 0) + 1
            n += 1
        gz.write(b"]}")
    return {
        "sloy": zadanie["sloy"],
        "podpis": zadanie["podpis"],
        "imya_sloya": zadanie["imya_sloya"],
        "data": buf.getvalue(),
        "obektov": n,
        "po_lesnichestvam": {nazvaniya.get(str(k), f"№{k}"): v for k, v in sorted(po_lesn.items())},
        "propushcheno_bez_lesnichestva": bez_lesn,
        "propushcheno_bez_geometrii": bez_geom,
    }


def otpravit(base_url, token, sloy, data):
    from .publisher import USER_AGENT

    if not base_url or not token:
        raise GranicyError("Не заданы адрес сервера «Лесовод» и токен (настройки плагина).")
    url = base_url.rstrip("/") + "/api/map/qgis/granicy?" + urlparse.urlencode({"token": token, "sloy": sloy})
    req = urlrequest.Request(url, data=data, method="POST", headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/gzip", "Accept": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            text = json.loads(text).get("detail", text)
        except (ValueError, AttributeError):
            pass
        if exc.code in (404, 405):
            text = "сервер ещё не обновлён (нет /api/map/qgis/granicy)"
        raise GranicyError(f"Сервер «Лесовод» ответил {exc.code}: {text}") from exc
    except urlerror.URLError as exc:
        raise GranicyError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc


def _sobrat_vse(task, zadaniya, base_url):
    from .publisher import fetch_lesnichestva

    lesnichestva = fetch_lesnichestva(base_url)
    out = []
    for zadanie in zadaniya:
        result = sobrat(task, zadanie, lesnichestva)
        if result is None:
            return None
        out.append(result)
    return out


def _otpravit_vse(task, paketi, base_url, token):
    out = []
    for i, paket in enumerate(paketi):
        if task.isCanceled():
            return None
        out.append((paket, otpravit(base_url, token, paket["sloy"], paket["data"])))
        task.setProgress(100.0 * (i + 1) / len(paketi))
    return out


# ------------------------------------------------------- в главном потоке --

_ZADACHI = []  # держим ссылки, иначе Python соберёт задачу до конца


def _zapustit(opisanie, funkciya, args, gotovo):
    def finished(exception, result=None):
        if task in _ZADACHI:
            _ZADACHI.remove(task)
        gotovo(exception, result)

    task = QgsTask.fromFunction(opisanie, funkciya, *args, on_finished=finished)
    _ZADACHI.append(task)
    QgsApplication.taskManager().addTask(task)
    return task


def sobrat_v_fone(zadaniya, base_url, gotovo):
    return _zapustit("Лесовод-мост: подготовка кварталов и выделов", _sobrat_vse, (zadaniya, base_url), gotovo)


def otpravit_v_fone(paketi, base_url, token, gotovo):
    return _zapustit("Лесовод-мост: отправка кварталов и выделов", _otpravit_vse, (paketi, base_url, token), gotovo)


def svodka(paketi):
    stroki = []
    for p in paketi:
        mb = len(p["data"]) / 1024 / 1024
        stroki.append(f"{p['podpis']} (слой «{p['imya_sloya']}»): {p['obektov']} шт., {mb:.1f} МБ")
        for name, count in p["po_lesnichestvam"].items():
            stroki.append(f"    {name}: {count}")
        if p["propushcheno_bez_lesnichestva"]:
            stroki.append(f"    пропущено без лесничества/квартала: {p['propushcheno_bez_lesnichestva']}")
        if p["propushcheno_bez_geometrii"]:
            stroki.append(f"    пропущено без полигона: {p['propushcheno_bez_geometrii']}")
    return "\n".join(stroki)
