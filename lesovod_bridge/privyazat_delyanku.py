"""
«Привязать выбранное к делянке» — как «Отметить как лесные культуры», только
для делянок: выделенный полигон (лесосека или выдел ГИСлесхоза, свой
нарисованный слой, выгрузка GPS) становится контуром лесосеки делянки в
«Лесоводе».

Для каждого выделенного полигона плагин спрашивает сервер, какие делянки уже
заведены на этом квартале/выделе (GET /api/map/qgis/delyanki-na-vydele), и
предлагает: привязать контур к одной из них или завести новую делянку (таксация
выдела подтянется сама, площадь — по контуру). Несколько новых полигонов можно
завести одной делянкой (каждый — её выдел). Отправка — POST /api/map/qgis/delyanka.

Вид рубки угадывается по полю cuttingtyp лесосеки ГИСлесхоза (те же слова,
что при раскраске), номер лесосеки — из поля num. База ГИСлесхоза не меняется.
"""

import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .otmetit_lk import KV_FIELDS, LCH_FIELDS, VD_FIELDS, OtmetkaError, _attr, to_wgs84_geojson

CUTTING_FIELDS = ("cuttingtyp",)
NOMER_FIELDS = ("num", "lesoseka_nomer", "nomer")
NOVAYA = "new"
IZ_ATRIBUTOV = "__attr__"


def ugadat_vid_rubki(text, legendy):
    """Код вида рубки по тексту (cuttingtyp) — как expr_vid_rubki в okraska.py
    и app/vidy.py на сервере; "" — не распознан."""
    norm = (text or "").lower().replace("ё", "е").replace("несплош", "несплш")
    if not norm.strip():
        return ""
    for vid in legendy.get("vidy_rubok") or []:
        for slova in vid.get("slova") or []:
            if slova and all(s in norm for s in slova):
                if vid["kod"] == "СПЛ" and "несплш" in norm:
                    continue
                return vid["kod"]
    return ""


def podpis_kandidata(k):
    """Строка выпадающего списка «Куда» для делянки-кандидата."""
    parts = [k.get("nazvanie") or f"делянка №{k.get('delyanka_id')}", f"её выдел {k.get('vydel') or '—'}"]
    if k.get("lesoseka_nomer"):
        parts.append(f"лесосека {k['lesoseka_nomer']}")
    if k.get("ploshad"):
        parts.append(f"{k['ploshad']} га")
    parts.append(k.get("status_rabot") or "")
    if k.get("arhiv"):
        parts.append("архив")
    if k.get("has_kontur"):
        parts.append("контур уже есть — заменится")
    return "Привязать: " + ", ".join(p for p in parts if p)


def nazvanie_obshey(stroki):
    """Название новой делянки из нескольких полигонов: «кв. 35 выд. 12, 13 (QGIS)»."""
    po_kv = {}
    for kv, vd in stroki:
        po_kv.setdefault(kv, [])
        if vd and vd not in po_kv[kv]:
            po_kv[kv].append(vd)
    return "; ".join(f"кв. {kv} выд. {', '.join(vds) or '—'}" for kv, vds in po_kv.items()) + " (QGIS)"


def vybor_po_umolchaniyu(kandidaty):
    """Если на выделе ровно одна рабочая (не архивная) делянка — к ней,
    иначе — новая делянка (человек выберет сам)."""
    rabochie = [k for k in kandidaty if not k.get("arhiv")]
    return rabochie[0]["item_id"] if len(rabochie) == 1 else NOVAYA


def _zapros(base_url, token, path, params=None, body=None):
    from .publisher import USER_AGENT

    if not base_url or not token:
        raise OtmetkaError("Не заданы адрес сервера «Лесовод» и токен (настройки плагина).")
    query = dict(params or {}, token=token)
    url = base_url.rstrip("/") + path + "?" + urlparse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urlrequest.Request(
        url, data=data, method="POST" if body is not None else "GET",
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
        if exc.code == 404 and "delyank" in path and "не найден" not in str(text):
            text = "сервер ещё не обновлён (нет привязки делянок из QGIS)"
        raise OtmetkaError(f"Сервер «Лесовод» ответил {exc.code}: {text}") from exc
    except urlerror.URLError as exc:
        raise OtmetkaError(f"Не удалось соединиться с сервером «Лесовод»: {exc.reason}") from exc


def kandidaty(base_url, token, kvartal, vydel, lesnichestvo_num):
    return _zapros(base_url, token, "/api/map/qgis/delyanki-na-vydele",
                   {"kvartal": kvartal, "vydel": vydel, "lesnichestvo_num": lesnichestvo_num})


def otpravit(base_url, token, body):
    return _zapros(base_url, token, "/api/map/qgis/delyanka", body=body)


def stroka_iz_obekta(feature, legendy, lesnichestvo_num):
    """Что плагин знает о полигоне из его атрибутов."""
    return {
        "feature": feature,
        "kvartal": _attr(feature, KV_FIELDS),
        "vydel": _attr(feature, VD_FIELDS),
        "lesnichestvo_num": _attr(feature, LCH_FIELDS) or lesnichestvo_num or "",
        "nomer": _attr(feature, NOMER_FIELDS),
        "vid_avto": ugadat_vid_rubki(_attr(feature, CUTTING_FIELDS), legendy),
    }


from qgis.PyQt.QtWidgets import (  # noqa: E402
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class PrivyazkaDialog(QDialog):
    """Таблица: кв., выд., № лесосеки, «Куда» (делянки на этом выделе или
    новая); ниже — вид рубки и название для новых."""

    COL_KV, COL_VD, COL_NOMER, COL_KUDA = range(4)

    def __init__(self, stroki, legendy, base_url, token, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Привязать к делянке")
        self.resize(760, 360)
        self.stroki = stroki
        self.base_url, self.token = base_url, token
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Выделено полигонов: {len(stroki)}. Каждый станет контуром лесосеки — у уже заведённой "
            "делянки на этом выделе или у новой. Квартал и выдел можно поправить, затем «Найти делянки»."))

        self.table = QTableWidget(len(stroki), 4)
        self.table.setHorizontalHeaderLabels(["Квартал", "Выдел", "№ лесосеки", "Куда"])
        self.table.horizontalHeader().setSectionResizeMode(self.COL_KUDA, QHeaderView.Stretch)
        for row, s in enumerate(stroki):
            self.table.setItem(row, self.COL_KV, QTableWidgetItem(s["kvartal"]))
            self.table.setItem(row, self.COL_VD, QTableWidgetItem(s["vydel"]))
            self.table.setItem(row, self.COL_NOMER, QTableWidgetItem(s["nomer"]))
            self.table.setCellWidget(row, self.COL_KUDA, QComboBox())
        layout.addWidget(self.table)

        self.nayti = QPushButton("Найти делянки на этих выделах")
        self.nayti.clicked.connect(self.zagruzit_kandidatov)
        layout.addWidget(self.nayti)
        self.oshibka = QLabel("")
        self.oshibka.setWordWrap(True)
        layout.addWidget(self.oshibka)

        form = QFormLayout()
        self.vid = QComboBox()
        self.vid.addItem("по полю cuttingtyp лесосеки (если есть)", IZ_ATRIBUTOV)
        for v in legendy.get("vidy_rubok") or []:
            if v.get("kod"):
                self.vid.addItem(f"{v['kod']} — {v['label']}", v["kod"])
        form.addRow("Вид рубки (новым и тем, где не указан)", self.vid)
        self.nazvanie = QLineEdit()
        self.nazvanie.setPlaceholderText("по умолчанию «кв. 35 выд. 12 (QGIS)»")
        form.addRow("Название новой делянки", self.nazvanie)
        self.odnoy = QCheckBox("Новые — одной делянкой (каждый полигон — её выдел)")
        self.odnoy.setVisible(len(stroki) > 1)
        form.addRow(self.odnoy)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Привязать")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.zagruzit_kandidatov()

    def _text(self, row, col):
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def zagruzit_kandidatov(self):
        oshibki = []
        for row, s in enumerate(self.stroki):
            combo = self.table.cellWidget(row, self.COL_KUDA)
            combo.clear()
            kv, vd = self._text(row, self.COL_KV), self._text(row, self.COL_VD)
            spisok = []
            if kv:
                try:
                    spisok = kandidaty(self.base_url, self.token, kv, vd, s["lesnichestvo_num"])
                except OtmetkaError as exc:
                    oshibki.append(str(exc))
            combo.addItem("Новая делянка", NOVAYA)
            for k in spisok:
                combo.addItem(podpis_kandidata(k), k["item_id"])
            default = vybor_po_umolchaniyu(spisok)
            combo.setCurrentIndex(max(0, combo.findData(default)))
        self.oshibka.setText("\n".join(dict.fromkeys(oshibki)))

    def zapisi(self, layer):
        """[(тело запроса, новая ли делянка)] — по строке таблицы."""
        vid_obshiy = self.vid.currentData()
        out = []
        for row, s in enumerate(self.stroki):
            kv, vd = self._text(row, self.COL_KV), self._text(row, self.COL_VD)
            if not kv:
                raise OtmetkaError(f"Строка {row + 1}: не указан квартал.")
            kuda = self.table.cellWidget(row, self.COL_KUDA).currentData()
            vid = s["vid_avto"] if vid_obshiy == IZ_ATRIBUTOV else vid_obshiy
            body = {
                "geometry": to_wgs84_geojson(s["feature"].geometry(), layer),
                "lesnichestvo_num": s["lesnichestvo_num"] or None,
                "kvartal": kv,
                "vydel": vd,
                "vid_rubki_kod": vid or None,
                "lesoseka_nomer": self._text(row, self.COL_NOMER) or None,
            }
            if kuda == NOVAYA:
                body["nazvanie"] = self.nazvanie.text().strip() or None
            else:
                body["item_id"] = kuda
            out.append((body, kuda == NOVAYA))
        novye = [(b["kvartal"], b["vydel"]) for b, novaya in out if novaya]
        if self.odnoy.isChecked() and len(novye) > 1 and not self.nazvanie.text().strip():
            for b, novaya in out:
                if novaya:
                    b["nazvanie"] = nazvanie_obshey(novye)
        return out
