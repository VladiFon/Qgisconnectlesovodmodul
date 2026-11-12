"""
Пометки лесосек на картах (0.13.0): «Скрыть с карт» и «Лесные культуры».

Беда, на которую жаловался Влад: в ГИСлесхозе лесосеки копятся за все
годы, уборка захламлённости и прочие рубки нарезаются на весь выдел, и
полигоны ложатся друг на друга в несколько слоёв — и в QGIS, и в телефоне.

Пометки хранит сервер «Лесовод» (app/karta_pometki.py), их видят и сайт, и
телефон. Здесь:

- «Скрыть / вернуть / лесные культуры» по выделенным полигонам — для слоя
  делянок Лесовода, слоя «Лесные культуры» и слоя «Лесосеки» ГИСлесхоза
  (ключ — uid лесосеки).
- Слой «Лесосеки» ГИСлесхоза рисует сам ГИСлесхоз из своей базы, сервер его
  не фильтрует. Поэтому плагин вешает на него фильтр (subsetString):
  «uid NOT IN (скрытые)». Скрытые — те, что скрыли вручную, плюс правила:
  виды рубок, которые не показывать, и лесосеки старше N лет. Свой фильтр
  ГИСлесхоза (если был) сохраняется, плагин дописывает своё через AND и
  снимает при «Показать всё». База ГИСлесхоза не меняется.
- Лесосеки ГИСлесхоза на лесных культурах — отдельный слой-накладка
  «🌲 Лесосеки на культурах» со штриховкой и подписью «л/к» (стиль самого
  ГИСлесхоза не трогаем).
"""

import json
import re
import time
from urllib import parse as urlparse
from urllib import request as urlrequest

from qgis.core import (
    QgsApplication,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsLinePatternFillSymbolLayer,
    QgsProject,
    QgsSettings,
    QgsSingleSymbolRenderer,
    QgsTask,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import okraska

TIMEOUT = 15
_SUBSET_ORIG = "lesovod_bridge/subset_orig"
_SUBSET_NASH = "lesovod_bridge/subset_nash"
_NAKLADKA = "lesovod_bridge/lk_nakladka"
_PRYATAT_KEY = "LesovodBridge/pryatat_skrytye"
LK_COLOR = "#00897b"


class PometkiError(Exception):
    pass


# ------------------------------------------------------------------ сеть --

def _zapros(base_url, token, path, body=None, method=None):
    from .publisher import USER_AGENT

    if not base_url or not token:
        raise PometkiError("Не заданы адрес сервера «Лесовод» и токен — «Настройки Лесовод-моста…».")
    url = base_url.rstrip("/") + path + "?" + urlparse.urlencode({"token": token})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urlrequest.Request(url, data=data, method=method or ("POST" if data else "GET"), headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        from urllib import error as urlerror

        if isinstance(exc, urlerror.HTTPError) and exc.code == 404:
            raise PometkiError("Сервер «Лесовод» ещё не обновлён: нет пометок лесосек (нужен пакет vse83 и новее).")
        if isinstance(exc, urlerror.HTTPError):
            raise PometkiError(f"Сервер ответил {exc.code}")
        raise PometkiError(f"Нет связи с сервером «Лесовод»: {exc}")


_KESH = {"vremya": 0, "dannye": None}


def zagruzit(base_url, token, svezhie=False):
    """Пометки с сервера; держим минуту, чтобы «Что здесь» не дёргал сеть."""
    if not svezhie and _KESH["dannye"] is not None and time.time() - _KESH["vremya"] < 60:
        return _KESH["dannye"]
    dannye = _zapros(base_url, token, "/api/map/qgis/pometki.json")
    _KESH.update(vremya=time.time(), dannye=dannye)
    return dannye


_ZADACHI = []


def zagruzit_v_fone(base_url, token, gotovo):
    """Пометки и справочник видов рубок — в фоне (QGIS не ждёт сервер);
    gotovo(dannye, legendy, oshibka) — потом, в главном потоке."""

    def rabota(_task):
        dannye = zagruzit(base_url, token, svezhie=True)
        return {"dannye": dannye, "legendy": okraska.load_legendy(base_url, token)}

    def finished(exception, result=None):
        if task in _ZADACHI:
            _ZADACHI.remove(task)
        if exception is not None or result is None:
            gotovo(None, None, str(exception) if exception is not None else "отменено")
        else:
            gotovo(result["dannye"], result["legendy"], None)

    task = QgsTask.fromFunction("Лесовод-мост: пометки лесосек", rabota, on_finished=finished)
    _ZADACHI.append(task)
    QgsApplication.taskManager().addTask(task)


def poslednie():
    return _KESH["dannye"]


def otpravit(base_url, token, body):
    result = _zapros(base_url, token, "/api/map/qgis/pometki", body)
    _KESH["dannye"] = None
    return result


def sohranit_nastroyki(base_url, token, nastroyki):
    result = _zapros(base_url, token, "/api/map/qgis/karta-nastroyki", nastroyki, method="PUT")
    _KESH["dannye"] = None
    return result


# -------------------------------------------------- что выделено в слое --

def _pole(layer, *names):
    have = {f.name().lower(): f.name() for f in layer.fields()}
    for n in names:
        if n in have:
            return have[n]
    return None


def eto_gisleshoz(layer):
    return isinstance(layer, QgsVectorLayer) and _pole(layer, "uid") is not None \
        and (_pole(layer, "cuttingtyp") or _pole(layer, "usetype")) is not None


def norm_id(value):
    if value is None:
        return ""
    s = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    return s


def _vydely(text):
    return [v for v in (norm_id(p) for p in re.split(r"[,;/ ]+", str(text or ""))) if v]


def _znach(f, name):
    if not name:
        return None
    v = f[name]
    return None if v is None or str(v) == "NULL" else v


def vybrannye(layer):
    """-> (istochnik, [klyuch], {klyuch: описание}) для выделенных объектов."""
    if not isinstance(layer, QgsVectorLayer):
        raise PometkiError("Выберите слой с лесосеками (делянки Лесовода или «Лесосеки» ГИСлесхоза).")
    feats = list(layer.selectedFeatures())
    if not feats:
        raise PometkiError(f"В слое «{layer.name()}» ничего не выделено. Выделите лесосеки и нажмите ещё раз.")
    kind = layer.customProperty("lesovod_bridge/kind") or ""
    if str(kind).startswith("delyanki"):
        istochnik, pole = "delyanka", "delyanka_id"
    elif kind == "lesokultury":
        istochnik, pole = "lk", "id"
    elif eto_gisleshoz(layer):
        istochnik, pole = "gisleshoz", _pole(layer, "uid")
    else:
        raise PometkiError(f"Слой «{layer.name()}» — не лесосеки: нужны делянки Лесовода, «Лесные культуры» "
                           "или «Лесосеки» ГИСлесхоза (с полями uid и cuttingtyp).")
    kv, vd = _pole(layer, "num_kv", "kvartal"), _pole(layer, "num_vds", "num_vd", "vydel")
    num, vid, data = _pole(layer, "num"), _pole(layer, "cuttingtyp", "vid_rubki"), _pole(layer, "date")
    klyuchi, opisaniya = [], {}
    for f in feats:
        k = norm_id(_znach(f, pole))
        if not k or k in opisaniya:
            continue
        chasti = [f"кв. {norm_id(_znach(f, kv))}" if kv else "", f"выд. {_znach(f, vd) or ''}" if vd else "",
                  f"лесосека № {norm_id(_znach(f, num))}" if num and _znach(f, num) is not None else "",
                  str(_znach(f, vid) or "") if vid else "", str(_znach(f, data) or "")[:10] if data else ""]
        klyuchi.append(k)
        opisaniya[k] = ", ".join(c for c in chasti if c.strip())
    if not klyuchi:
        raise PometkiError("У выделенных объектов нет номера (uid / delyanka_id) — пометить нечего.")
    return istochnik, klyuchi, opisaniya


DEYSTVIYA = [
    ("Скрыть с карт (QGIS и телефон)", {"skryta": True}),
    ("Вернуть на карты", {"skryta": False}),
    ("🌲 Это лесные культуры", {"lesokultury": "da"}),
    ("Это НЕ лесные культуры", {"lesokultury": "net"}),
    ("Лесные культуры — определять само (по выделу)", {"lesokultury": "avto"}),
]


# ------------------------------------------------- слой ГИСлесхоза --

def pryatat():
    return QgsSettings().value(_PRYATAT_KEY, True, type=bool)


def zapomnit_pryatat(value):
    QgsSettings().setValue(_PRYATAT_KEY, bool(value))


def _god(value):
    m = re.search(r"(19|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


def razobrat_gisleshoz(layer, dannye, legendy):
    """Пройти по всем лесосекам слоя (без нашего фильтра) -> (скрыть uid,
    {uid: описание л/к})."""
    nast = dannye.get("nastroyki") or {}
    skryty = {norm_id(u) for u in dannye.get("gisleshoz_skryty") or []}
    lk_da = {norm_id(u) for u in dannye.get("gisleshoz_lk") or []}
    lk_net = {norm_id(u) for u in dannye.get("gisleshoz_ne_lk") or []}
    lk_vydely = {(norm_id(x.get("kvartal")), norm_id(x.get("vydel"))): x.get("info") or "л/к"
                 for x in dannye.get("lk_vydely") or []}
    vidy = set(nast.get("skryt_vidy") or [])
    let = int(nast.get("gisleshoz_let") or 0)
    min_god = time.localtime().tm_year - let + 1 if let else None

    uid_f = _pole(layer, "uid")
    kv_f, vd_f = _pole(layer, "num_kv"), _pole(layer, "num_vds", "num_vd")
    date_f, info_f = _pole(layer, "date"), _pole(layer, "info")
    cut_f = _pole(layer, "cuttingtyp")
    vid_expr = None
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    if vidy and cut_f:
        vid_expr = QgsExpression(okraska.expr_vid_rubki(cut_f, legendy))
        vid_expr.prepare(context)

    skryt, lk = set(), {}
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
    for f in layer.getFeatures(request):
        uid = norm_id(_znach(f, uid_f))
        if not uid:
            continue
        if uid in skryty:
            skryt.add(uid)
        elif vid_expr is not None:
            context.setFeature(f)
            if vid_expr.evaluate(context) in vidy:
                skryt.add(uid)
        if min_god and uid not in skryt:
            god = _god(_znach(f, date_f)) or _god(_znach(f, info_f))
            if god and god < min_god:
                skryt.add(uid)
        if uid in lk_net:
            continue
        if uid in lk_da:
            lk[uid] = "отмечено вручную"
            continue
        kv = norm_id(_znach(f, kv_f))
        naideno = [lk_vydely[(kv, v)] for v in _vydely(_znach(f, vd_f)) if (kv, v) in lk_vydely]
        if naideno:
            lk[uid] = ", ".join(dict.fromkeys(naideno))
    return skryt, lk


def _spisok_sql(layer, uids):
    pole = _pole(layer, "uid")
    field = layer.fields().field(pole)
    if field.isNumeric():
        vals = [u for u in uids if re.fullmatch(r"-?\d+(\.\d+)?", u)]
    else:
        vals = ["'" + u.replace("'", "''") + "'" for u in uids]
    return f'"{pole}" NOT IN ({", ".join(vals)})' if vals else ""


def primenit_k_gisleshoz(layer, skryt):
    """Повесить / снять фильтр «без скрытых» у слоя ГИСлесхоза."""
    orig = layer.customProperty(_SUBSET_ORIG)
    if orig is None:
        orig = layer.subsetString()
    elif layer.subsetString() != (layer.customProperty(_SUBSET_NASH) or ""):
        # фильтр поменяли руками / ГИСлесхозом после нас — он и есть исходный
        orig = layer.subsetString()
    nash = _spisok_sql(layer, sorted(skryt)) if skryt else ""
    novyy = f"({orig}) AND {nash}" if orig and nash else (nash or orig)
    layer.setCustomProperty(_SUBSET_ORIG, orig)
    layer.setCustomProperty(_SUBSET_NASH, novyy)
    if layer.subsetString() != novyy:
        layer.setSubsetString(novyy)
    layer.triggerRepaint()


def snyat_s_gisleshoz(layer):
    orig = layer.customProperty(_SUBSET_ORIG)
    if orig is None:
        return
    if layer.subsetString() == (layer.customProperty(_SUBSET_NASH) or ""):
        layer.setSubsetString(orig)
    layer.removeCustomProperty(_SUBSET_ORIG)
    layer.removeCustomProperty(_SUBSET_NASH)
    layer.triggerRepaint()


def _bez_nashego_filtra(layer):
    """Временно вернуть исходный фильтр, чтобы увидеть все лесосеки."""
    orig = layer.customProperty(_SUBSET_ORIG)
    if orig is not None and layer.subsetString() == (layer.customProperty(_SUBSET_NASH) or ""):
        layer.setSubsetString(orig)


def nakladka_lk(layer, lk):
    """Слой-накладка «🌲 Лесосеки на культурах»: копии контуров лесосек
    ГИСлесхоза, лежащих на лесных культурах, со штриховкой и подписью."""
    project = QgsProject.instance()
    for lr in list(project.mapLayers().values()):
        if lr.customProperty(_NAKLADKA) == layer.id():
            project.removeMapLayer(lr.id())
    if not lk:
        return None
    crs = layer.crs().authid() or layer.crs().toWkt()
    mem = QgsVectorLayer(f"MultiPolygon?crs={crs}&field=uid:string&field=lk_info:string", "🌲 Лесосеки на культурах", "memory")
    uid_f = _pole(layer, "uid")
    feats = []
    expr = QgsFeatureRequest().setSubsetOfAttributes([uid_f], layer.fields())
    for f in layer.getFeatures(expr):
        uid = norm_id(_znach(f, uid_f))
        if uid in lk and f.hasGeometry():
            nf = QgsFeature(mem.fields())
            nf.setGeometry(f.geometry())
            nf.setAttributes([uid, lk[uid]])
            feats.append(nf)
    mem.dataProvider().addFeatures(feats)
    mem.updateExtents()
    mem.setCustomProperty(_NAKLADKA, layer.id())
    oformit_lk_nakladku(mem)
    project.addMapLayer(mem, False)
    root = project.layerTreeRoot()
    node = root.findLayer(layer.id())
    parent = node.parent() if node is not None else root
    idx = parent.children().index(node) if node is not None else 0
    parent.insertLayer(idx, mem)
    return mem


def shtrikhovka():
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(45)
    hatch.setDistance(2.2)
    hatch.setColor(QColor(LK_COLOR))
    try:
        hatch.setLineWidth(0.35)
    except AttributeError:
        pass
    return hatch


def oformit_lk_nakladku(layer):
    symbol = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": LK_COLOR, "outline_width": "0.6",
                                         "outline_style": "dash"})
    symbol.appendSymbolLayer(shtrikhovka())
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    from .server_layers import _labels

    _labels(layer, "'🌲 л/к'", size=8, color=LK_COLOR)
    layer.setMapTipTemplate("<b>🌲 Лесосека на лесных культурах</b><br>[% \"lk_info\" %]")


def obnovit_gisleshoz(dannye, legendy, pryatat_skrytye=True):
    """Применить пометки ко всем слоям лесосек ГИСлесхоза в проекте.
    -> [(имя слоя, скрыто, на культурах)]"""
    itog = []
    for layer in okraska.gisleshoz_lesoseki_layers():
        if not eto_gisleshoz(layer):
            continue
        _bez_nashego_filtra(layer)
        skryt, lk = razobrat_gisleshoz(layer, dannye, legendy)
        if pryatat_skrytye:
            primenit_k_gisleshoz(layer, skryt)
        else:
            snyat_s_gisleshoz(layer)
        nakladka_lk(layer, {u: i for u, i in lk.items() if not (pryatat_skrytye and u in skryt)})
        itog.append((layer.name(), len(skryt), len(lk)))
    return itog


def lk_dlya_attrs(attrs):
    """Для карточки «Что здесь» лесосеки ГИСлесхоза: описание культур или ''."""
    dannye = poslednie()
    if not dannye:
        return ""
    low = {str(k).lower(): v for k, v in attrs.items()}
    uid = norm_id(low.get("uid"))
    if uid in {norm_id(u) for u in dannye.get("gisleshoz_ne_lk") or []}:
        return ""
    if uid in {norm_id(u) for u in dannye.get("gisleshoz_lk") or []}:
        return "отмечено вручную"
    kv = norm_id(low.get("num_kv"))
    lk_vydely = {(norm_id(x.get("kvartal")), norm_id(x.get("vydel"))): x.get("info") or "л/к"
                 for x in dannye.get("lk_vydely") or []}
    naideno = [lk_vydely[(kv, v)] for v in _vydely(low.get("num_vds") or low.get("num_vd")) if (kv, v) in lk_vydely]
    return ", ".join(dict.fromkeys(naideno))


# --------------------------------------------------------------- диалоги --

class SkrytyeDialog(QDialog):
    """«Скрытые с карт»: правила (закрытые, виды рубок, старые лесосеки) и
    список скрытого вручную с кнопкой «Вернуть»."""

    def __init__(self, dannye, base_url, token, parent=None):
        super().__init__(parent)
        self.base_url, self.token = base_url, token
        self.izmeneno = False
        self.setWindowTitle("Лесовод-мост: что не показывать на картах")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Для QGIS (слои Лесовода и «Лесосеки» ГИСлесхоза) и карты в телефоне. "
                             "Ничего не удаляется — скрытое возвращается здесь."))
        nast = dannye.get("nastroyki") or {}
        self.zakrytye = QCheckBox("Не показывать закрытые делянки (архив после акта)")
        self.zakrytye.setChecked(bool(nast.get("skryt_zakrytye", True)))
        lay.addWidget(self.zakrytye)
        row = QHBoxLayout()
        row.addWidget(QLabel("Лесосеки ГИСлесхоза старше"))
        self.let = QComboBox()
        for v in (0, 1, 2, 3, 5, 10):
            self.let.addItem("— показывать все —" if v == 0 else f"{v} {'года' if v == 1 else 'лет'}", v)
        i = self.let.findData(int(nast.get("gisleshoz_let") or 0))
        self.let.setCurrentIndex(max(0, i))
        row.addWidget(self.let)
        row.addWidget(QLabel("не показывать (по году в поле date)"))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Виды рубок, которые не показывать совсем:"))
        self.vidy = QListWidget()
        self.vidy.setMaximumHeight(150)
        skryt_vidy = set(nast.get("skryt_vidy") or [])
        for v in dannye.get("vidy_rubok") or []:
            item = QListWidgetItem(f"{v['kod']} — {v['label']}")
            item.setData(Qt.UserRole, v["kod"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if v["kod"] in skryt_vidy else Qt.Unchecked)
            self.vidy.addItem(item)
        lay.addWidget(self.vidy)
        lay.addWidget(QLabel("Скрыто и помечено вручную:"))
        self.tablica = QTableWidget(0, 3)
        self.tablica.setHorizontalHeaderLabels(["Лесосека", "Что", "Кто / когда"])
        self.tablica.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tablica.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tablica.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.skrytye = list(dannye.get("skrytye") or [])
        self._zapolnit()
        lay.addWidget(self.tablica)
        vernut = QPushButton("Вернуть выделенные на карты")
        vernut.clicked.connect(self._vernut)
        lay.addWidget(vernut)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        buttons.accepted.connect(self._sohranit)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _zapolnit(self):
        self.tablica.setRowCount(len(self.skrytye))
        for r, p in enumerate(self.skrytye):
            chto = ", ".join(x for x in (
                "скрыта" if p.get("skryta") else "",
                "🌲 культуры" if p.get("lesokultury") is True else "не культуры" if p.get("lesokultury") is False else "",
            ) if x)
            for c, text in enumerate((f"{p.get('nazvanie')} · {p.get('istochnik_label')}", chto,
                                      f"{p.get('kto') or ''} {str(p.get('updated_at') or '')[:16]}")):
                self.tablica.setItem(r, c, QTableWidgetItem(text))

    def _vernut(self):
        rows = sorted({i.row() for i in self.tablica.selectedIndexes()})
        if not rows:
            return
        po_ist = {}
        for r in rows:
            p = self.skrytye[r]
            po_ist.setdefault(p["istochnik"], []).append(p["klyuch"])
        try:
            for ist, kl in po_ist.items():
                otpravit(self.base_url, self.token, {"istochnik": ist, "klyuchi": kl, "skryta": False,
                                                     "lesokultury": "avto"})
        except PometkiError as exc:
            from qgis.PyQt.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Лесовод-мост", str(exc))
            return
        self.skrytye = [p for i, p in enumerate(self.skrytye) if i not in rows]
        self._zapolnit()
        self.izmeneno = True

    def nastroyki(self):
        vidy = [self.vidy.item(i).data(Qt.UserRole) for i in range(self.vidy.count())
                if self.vidy.item(i).checkState() == Qt.Checked]
        return {"skryt_zakrytye": self.zakrytye.isChecked(), "gisleshoz_let": int(self.let.currentData()),
                "skryt_vidy": vidy}

    def _sohranit(self):
        try:
            sohranit_nastroyki(self.base_url, self.token, self.nastroyki())
        except PometkiError as exc:
            from qgis.PyQt.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Лесовод-мост", str(exc))
            return
        self.izmeneno = True
        self.accept()
