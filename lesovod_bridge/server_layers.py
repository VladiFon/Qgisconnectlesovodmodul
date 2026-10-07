"""
Слои с сервера «Лесовод» в QGIS (обратное направление к публикации лесосек).

Каждый слой — GeoJSON с сервера (?token=... — тот же токен, что в настройках
плагина). Раньше QGIS подключал сам URL, и это вешало QGIS: OGR не держит
скачанное, а качает весь GeoJSON заново на каждое новое подключение — при
первой перерисовке, после минуты простоя на каждом сдвиге карты, на каждый
запрос объектов. Без таймаута: медленный или недоступный сервер = QGIS
висит, а отмена перерисовки при сдвиге карты ждёт, пока докачается.

Теперь плагин сам скачивает GeoJSON в фоне (QgsTask, с таймаутом), кладёт
его в локальный GeoPackage (с пространственным индексом) в папке профиля
QGIS и подключает слой к этому файлу. Перерисовка, «Что здесь», выбор —
всё по локальному файлу, без сети. «Обновить слои Лесовода» и открытие
проекта перекачивают данные в фоне и подменяют файл у слоя (стиль и
подписи остаются). Слои старых проектов, подключённые прямо к URL,
при открытии проекта сами переводятся на локальную копию.

- Метки рабочих — точки с цветом по типу, подписью (текст метки), а во
  всплывающей подсказке (Map Tips) — тип, автор, дата и фото.
- Делянки — выделы с делянками (или свой контур лесосеки) с легендой: по
  статусу работ, по виду рубки (ССР, УЗ, ПРЖ…) или по виду пользования —
  те же цвета, что на телефоне и на сайте (см. okraska.py).
- Лесные культуры — участки с легендой по виду культур (обычные, под
  пологом, плантационные, ландшафтные…).
- Обмеры с телефона — контуры, обойдённые рабочими по GPS, с площадью.
"""

import glob
import os
import time
from functools import partial
from urllib import parse as urlparse
from urllib import request as urlrequest

from qgis.core import (
    QgsApplication,
    QgsDataProvider,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsTask,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from . import okraska

_KIND_PROPERTY = "lesovod_bridge/kind"
# адрес слоя на сервере без токена: «/api/map/qgis/delyanki.geojson?lesnichestvo_num=3»
_ISTOCHNIK_PROPERTY = "lesovod_bridge/istochnik"

TIMEOUT = 120  # с — GeoJSON всех делянок лесхоза бывает в несколько МБ
KEEP_FILES = 3  # сколько последних копий слоя одного вида держать в папке


class ServerLayerError(Exception):
    pass


def _proverit(base_url, token):
    if not base_url:
        raise ServerLayerError("Не задан адрес сервера «Лесовод» (настройки плагина).")
    if not token:
        raise ServerLayerError("Не задан токен сервера «Лесовод» (настройки плагина -> «Токен»).")


def _istochnik(path, **params):
    query = {k: v for k, v in params.items() if v}
    return path + ("?" + urlparse.urlencode(query) if query else "")


def _url(base_url, istochnik, token):
    path, _, query = istochnik.partition("?")
    params = dict(urlparse.parse_qsl(query))
    params["token"] = token
    return base_url.rstrip("/") + path + "?" + urlparse.urlencode(params)


def istochnik_iz_url(source):
    """Слой старой версии плагина подключён прямо к URL — его адрес без
    сервера и токена (или None, если источник не URL)."""
    if not source.lower().startswith(("http://", "https://")):
        return None
    parts = urlparse.urlsplit(source)
    params = [(k, v) for k, v in urlparse.parse_qsl(parts.query) if k != "token"]
    return parts.path + ("?" + urlparse.urlencode(params) if params else "")


def cache_dir():
    path = os.path.join(QgsApplication.qgisSettingsDirPath(), "lesovod_bridge", "sloi")
    os.makedirs(path, exist_ok=True)
    return path


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


def _oformit_metki(layer, base_url, token, _legendy):

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


DELYANKI_TITLES = {
    "status": "Делянки по статусу (Лесовод)",
    "vid": "Делянки по виду рубки (Лесовод)",
    "gruppa": "Делянки по виду пользования (Лесовод)",
}


def _oformit_delyanki(rezhim, layer, _base_url, _token, legendy):
    """rezhim: status — по статусу работ, vid — по виду рубки (ССР, УЗ…),
    gruppa — по виду пользования (главное / промежуточное / прочие)."""
    layer.setRenderer(okraska.renderer_delyanok(rezhim, legendy))
    podpis = {
        "status": 'coalesce("status_rabot", \'\')',
        "vid": 'coalesce("vid_rubki_kod", \'\')',
        "gruppa": 'coalesce("vid_rubki_kod", \'\')',
    }[rezhim]
    _labels(layer, 'coalesce("nazvanie", \'\') || \'\\n\' || ' + podpis)
    layer.setMapTipTemplate(
        "<b>[% coalesce(\"nazvanie\", 'Делянка') %]</b><br>"
        "кв. [% \"kvartal\" %], выд. [% \"vydel\" %]<br>Статус: [% \"status_rabot\" %]<br>"
        "Вид рубки: [% coalesce(\"vid_rubki\", '—') %]<br>[% coalesce(\"gruppa_label\", '') %]"
    )


def _oformit_kultury(layer, _base_url, _token, legendy):
    layer.setRenderer(okraska.renderer_kultur(legendy))
    _labels(layer, 'coalesce("glavnaya_poroda", \'\') || \' \' || coalesce("god_sozdaniya", \'\')', color="#006064")
    layer.setMapTipTemplate(
        "<b>[% coalesce(\"vid_kultur\", 'Лесные культуры') %]</b><br>"
        "кв. [% \"kvartal\" %], выд. [% \"vydel\" %]<br>"
        "[% coalesce(\"glavnaya_poroda\", '') %] [% coalesce(\"god_sozdaniya\", '') %], "
        "[% coalesce(to_string(\"ploshad\"), '') %] га"
    )


def _oformit_obmery(layer, _base_url, _token, _legendy):
    # обмер всегда замкнут — полигон
    fill = QgsFillSymbol.createSimple({"color": "0,230,118,30", "outline_color": "#00c853", "outline_width": "0.8"})
    layer.setRenderer(QgsSingleSymbolRenderer(fill))
    _labels(layer, 'coalesce("nazvanie", \'Обмер\') || \'\\n\' || coalesce(to_string("ploshad_ga"), \'\') || \' га\'', color="#1b5e20")




# вид слоя -> (адрес на сервере, название, оформление, нужна ли легенда)
VIDY = {
    "geo_notes": ("/api/map/geo-notes.geojson", "Метки рабочих (Лесовод)", _oformit_metki, False),
    "delyanki_status": ("/api/map/qgis/delyanki.geojson", DELYANKI_TITLES["status"],
                        partial(_oformit_delyanki, "status"), True),
    "delyanki_vid": ("/api/map/qgis/delyanki.geojson", DELYANKI_TITLES["vid"], partial(_oformit_delyanki, "vid"), True),
    "delyanki_gruppa": ("/api/map/qgis/delyanki.geojson", DELYANKI_TITLES["gruppa"],
                        partial(_oformit_delyanki, "gruppa"), True),
    "lesokultury": ("/api/map/qgis/lesokultury.geojson", "Лесные культуры по виду (Лесовод)", _oformit_kultury, True),
    "tracks": ("/api/map/qgis/tracks.geojson", "Обмеры с телефона (Лесовод)", _oformit_obmery, False),
}
# точки — POINT, остальное — полигоны (MULTIPOLYGON, чтобы пустой слой тоже был полигональным)
TOCHECHNYE = {"geo_notes"}


# ------------------------------------------------------------- скачивание --
# Всё ниже до «в главном потоке» выполняется в фоновом потоке QgsTask:
# только urllib и GDAL, никаких слоёв и виджетов QGIS.

def _skachat_geojson(url):
    from .publisher import USER_AGENT

    req = urlrequest.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"})
    with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _v_geopackage(kind, data):
    """GeoJSON -> новый файл GeoPackage с пространственным индексом.
    Имя каждый раз новое: старый файл может быть занят слоем (Windows)."""
    from osgeo import gdal

    papka = cache_dir()
    stem = os.path.join(papka, f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{int(time.time() * 1000) % 1000:03d}")
    src, dst = stem + ".geojson", stem + ".gpkg"
    with open(src, "wb") as f:
        f.write(data)
    # GDAL после первой ошибки сыплет следующими (откат, триггеры) — человеку
    # нужна первая, настоящая причина
    oshibki = []

    def lovit(klass, _nomer, tekst):
        if klass >= gdal.CE_Failure:
            oshibki.append(tekst)

    gdal.PushErrorHandler(lovit)
    try:
        # -unsetFid: поле «id» из GeoJSON — не ключ слоя. У культур один
        # участок в нескольких выделах = несколько объектов с одним id; без
        # этого GDAL (3.6 и ниже) делает id первичным ключом GeoPackage и
        # падает на втором объекте (UNIQUE constraint failed: lesokultury.id,
        # а в сообщение попадает «no such table» про триггер). Ключ — свой
        # счётчик fid, а id остаётся обычным полем (нужен «Что здесь»).
        result = gdal.VectorTranslate(
            dst, src, options=["-unsetFid"], format="GPKG", layerName=kind,
            geometryType="POINT" if kind in TOCHECHNYE else "MULTIPOLYGON",
        )
        if result is None:
            prichina = oshibki[0] if oshibki else gdal.GetLastErrorMsg()
            raise ServerLayerError(f"не удалось разобрать GeoJSON с сервера: {prichina}")
        result = None  # закрыть файл
    finally:
        gdal.PopErrorHandler()
        try:
            os.remove(src)
        except OSError:
            pass
    return dst


def _skachat_vse(task, zadaniya, base_url, token, nuzhna_legenda):
    """zadaniya: [(kind, istochnik)] -> {"faily": {kind: path}, "oshibki": {kind: текст}, "legendy": …}"""
    faily, oshibki, skachano = {}, {}, {}
    for i, (kind, istochnik) in enumerate(zadaniya):
        if task.isCanceled():
            break
        try:
            # делянки по статусу / виду / пользованию — один и тот же адрес: качаем раз
            data = skachano.get(istochnik)
            if data is None:
                data = skachano[istochnik] = _skachat_geojson(_url(base_url, istochnik, token))
            faily[kind] = _v_geopackage(kind, data)
        except Exception as exc:  # noqa: BLE001 — покажем человеку
            oshibki[kind] = _tekst_oshibki(exc)
        task.setProgress(100.0 * (i + 1) / max(1, len(zadaniya)))
    legendy = okraska.load_legendy(base_url, token) if nuzhna_legenda and faily else None
    return {"faily": faily, "oshibki": oshibki, "legendy": legendy}


def _tekst_oshibki(exc):
    import socket
    from urllib import error as urlerror

    if isinstance(exc, urlerror.HTTPError):
        return f"сервер ответил {exc.code}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"сервер не ответил за {TIMEOUT} с"
    if isinstance(exc, urlerror.URLError):
        return f"нет связи с сервером ({exc.reason})"
    return str(exc)


# ------------------------------------------------------- в главном потоке --

_ZADACHI = []  # держим ссылки, иначе Python соберёт задачу до конца


def _zapustit(opisanie, zadaniya, base_url, token, nuzhna_legenda, gotovo):
    def finished(exception, result=None):
        if task in _ZADACHI:
            _ZADACHI.remove(task)
        if exception is not None or result is None:
            text = _tekst_oshibki(exception) if exception is not None else "загрузка отменена"
            result = {"faily": {}, "oshibki": {k: text for k, _ in zadaniya}, "legendy": None}
        gotovo(result)

    task = QgsTask.fromFunction(opisanie, _skachat_vse, zadaniya, base_url, token, nuzhna_legenda,
                                on_finished=finished)
    _ZADACHI.append(task)
    QgsApplication.taskManager().addTask(task)
    return task


def _uri(path, kind):
    return f"{path}|layername={kind}"


def lesovod_sloi():
    return [lr for lr in QgsProject.instance().mapLayers().values()
            if isinstance(lr, QgsVectorLayer) and lr.customProperty(_KIND_PROPERTY) in VIDY]


def _replace_layer(kind, layer):
    """Одного вида — один слой: повторное нажатие кнопки заменяет прежний."""
    project = QgsProject.instance()
    for existing in lesovod_sloi():
        if existing.customProperty(_KIND_PROPERTY) == kind:
            project.removeMapLayer(existing.id())
    project.addMapLayer(layer)
    return layer


def dobavit(kind, base_url, token, gotovo, **params):
    """Добавить (заменить) слой Лесовода. Скачивание — в фоне; gotovo(layer,
    oshibka) вызывается потом в главном потоке. QGIS не ждёт сервер."""
    _proverit(base_url, token)
    path, title, oformit, nuzhna_legenda = VIDY[kind]
    istochnik = _istochnik(path, **params)

    def posle(result):
        if kind not in result["faily"]:
            gotovo(None, f"Не удалось загрузить слой «{title}»: {result['oshibki'].get(kind, 'неизвестная ошибка')}. "
                         "Проверьте адрес сервера и токен в настройках плагина и что сервер обновлён.")
            return
        layer = QgsVectorLayer(_uri(result["faily"][kind], kind), title, "ogr")
        if not layer.isValid():
            gotovo(None, f"QGIS не смог открыть скачанный слой «{title}».")
            return
        # признаки — до добавления в проект (chto_zdes подключается по layersAdded)
        layer.setCustomProperty(_KIND_PROPERTY, kind)
        layer.setCustomProperty(_ISTOCHNIK_PROPERTY, istochnik)
        oformit(layer, base_url, token, result["legendy"] or okraska.load_legendy())
        _replace_layer(kind, layer)
        uborka(kind)
        gotovo(layer, None)

    _zapustit(f"Лесовод-мост: {title}", [(kind, istochnik)], base_url, token, nuzhna_legenda, posle)


def reload_all(base_url, token, gotovo=None):
    """Перекачать в фоне все слои Лесовода в проекте и подменить у них файл
    (стиль, подписи, место в легенде — прежние). Слои, подключённые прямо к
    URL (старая версия плагина), переводятся на локальную копию.
    Возвращает число слоёв; gotovo(obnovleno, oshibki) — по окончании."""
    sloi, zadaniya = {}, []
    for layer in lesovod_sloi():
        kind = layer.customProperty(_KIND_PROPERTY)
        istochnik = layer.customProperty(_ISTOCHNIK_PROPERTY) or istochnik_iz_url(layer.source()) or VIDY[kind][0]
        sloi[layer.id()] = (kind, istochnik)
        if (kind, istochnik) not in zadaniya:
            zadaniya.append((kind, istochnik))
    if not sloi:
        return 0
    try:
        _proverit(base_url, token)
    except ServerLayerError as exc:
        if gotovo:
            gotovo(0, {k: str(exc) for k, _ in zadaniya})
        return len(sloi)

    def posle(result):
        obnovleno = 0
        for layer_id, (kind, istochnik) in sloi.items():
            layer = QgsProject.instance().mapLayer(layer_id)
            path = result["faily"].get(kind)
            if layer is None or path is None:
                continue
            layer.setDataSource(_uri(path, kind), layer.name(), "ogr", QgsDataProvider.ProviderOptions())
            layer.setCustomProperty(_ISTOCHNIK_PROPERTY, istochnik)
            layer.triggerRepaint()
            obnovleno += 1
        for kind in result["faily"]:
            uborka(kind)
        if gotovo:
            gotovo(obnovleno, result["oshibki"])

    _zapustit("Лесовод-мост: обновление слоёв", zadaniya, base_url, token,
              any(VIDY[k][3] for k, _ in zadaniya), posle)
    return len(sloi)


def nuzhno_perevesti():
    """Есть ли в проекте слои Лесовода, подключённые прямо к URL (старая
    версия) или потерявшие свой файл."""
    for layer in lesovod_sloi():
        if istochnik_iz_url(layer.source()) or not layer.isValid():
            return True
    return False


def uborka(kind):
    """Удалить старые копии слоя этого вида, кроме тех, к которым подключены
    слои проекта, и последних KEEP_FILES. Занятые файлы (Windows) — потом."""
    zanyaty = {os.path.normcase(os.path.abspath(lr.source().split("|")[0])) for lr in lesovod_sloi()}
    faily = sorted(glob.glob(os.path.join(cache_dir(), f"{kind}_*.gpkg")), reverse=True)
    for path in faily[KEEP_FILES:]:
        if os.path.normcase(os.path.abspath(path)) in zanyaty:
            continue
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.remove(p)
            except OSError:
                pass
