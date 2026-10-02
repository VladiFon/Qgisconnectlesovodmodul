"""
Карточки «Что здесь» — характеристики объекта Лесовода по его атрибутам
(участок лесных культур, делянка, обмер, метка, выдел ГИСлесхоза) в виде
HTML для панели плагина. Без импортов QGIS — проверяется обычным Python.
"""

import html

# поля GeoJSON-слоёв сервера -> подписи в карточке, в порядке показа
POLYA_KULTUR = [
    ("vid_kultur", "Вид культур"),
    ("glavnaya_poroda", "Главная порода"),
    ("sostav_formula", "Состав"),
    ("god_sozdaniya", "Год создания"),
    ("ploshad", "Площадь, га"),
    ("status", "Состояние"),
    ("metod_sozdaniya", "Способ создания"),
    ("sposob_obrabotki", "Обработка почвы"),
    ("posadochnyy_material", "Посадочный материал"),
    ("shema_posadki", "Схема посадки"),
    ("gustota_posadki", "Густота посадки, шт/га"),
    ("normativ_perevoda", "Норматив перевода, шт/га"),
    ("tlu", "ТЛУ"),
    ("kategoriya_ploshadi", "Категория площади"),
    ("vydel_staryy", "Старый выдел"),
    ("podvydel", "Подвыдел"),
    ("posl_meropriyatie", "Последнее мероприятие"),
    ("prizhivaemost_pct", "Приживаемость, %"),
    ("kolichestvo_na_ga", "Количество, шт/га"),
    ("sostav_fakt", "Состав по факту"),
    ("primechaniya", "Примечание"),
]

POLYA_DELYANKI = [
    ("status_rabot", "Статус работ"),
    ("vid_rubki", "Вид рубки"),
    ("gruppa_label", "Вид пользования"),
    ("ploshad", "Площадь, га"),
]

POLYA_OBMERA = [
    ("ploshad_ga", "Площадь, га"),
    ("perimetr_m", "Периметр, м"),
    ("note_text", "Примечание"),
]

POLYA_METKI = [
    ("note_text", "Текст"),
    ("author_fio", "Автор"),
    ("created_at", "Дата"),
]

# поля выдела в слоях ГИСлесхоза (имена встречаются в разном регистре)
KV_FIELDS = ("num_kv", "kvartal", "kv")
VD_FIELDS = ("num_vd", "num_vds", "vydel", "vd")
LCH_FIELDS = ("lesnich_text", "num_lch")
PLOSHAD_FIELDS = ("area_ga", "ploshad", "s_ga", "area", "s")


def pusto(value):
    return value is None or str(value).strip() in ("", "NULL", "None")


def tekst(value):
    """Значение для показа: 2.0 -> 2, 1.2345 -> 1.23, True -> да."""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].lstrip("-").isdigit() else text


def iz_polya(attrs, names):
    """Первое непустое значение из полей names (без учёта регистра)."""
    lower = {str(k).lower(): v for k, v in attrs.items()}
    for name in names:
        value = lower.get(name)
        if not pusto(value):
            return tekst(value)
    return ""


def _stroki(attrs, polya):
    out = []
    for key, label in polya:
        value = attrs.get(key)
        if pusto(value):
            continue
        if key == "vid_kultur" and attrs.get("vid_kultur_avto") in (True, "true", "True", 1):
            out.append((label, html.escape(tekst(value)) + " <i>(определён автоматически)</i>"))
            continue
        out.append((label, html.escape(tekst(value))))
    return out


def _mesto(attrs):
    kv, vd = iz_polya(attrs, KV_FIELDS), iz_polya(attrs, VD_FIELDS)
    parts = []
    if kv:
        parts.append(f"кв. {html.escape(kv)}")
    if vd:
        parts.append(f"выд. {html.escape(vd)}")
    lesn = attrs.get("lesnichestvo")
    if not pusto(lesn):
        parts.append(html.escape(tekst(lesn)) + " лесничество"
                     if "лесничеств" not in str(lesn).lower() else html.escape(tekst(lesn)))
    return ", ".join(parts)


def _kartochka(zagolovok, color, mesto, stroki, podval=""):
    # строки, а не таблица: панель справа узкая, таблица в QTextBrowser там ломается
    rows = "".join(f"<span style='color:#555'>{label}:</span> <b>{value}</b><br>" for label, value in stroki)
    return (
        f"<table width='100%' cellspacing='0' cellpadding='4' style='margin-bottom:10px'><tr>"
        f"<td width='6' bgcolor='{html.escape(str(color))}'></td><td>"
        f"<span style='font-size:12pt'><b>{html.escape(zagolovok)}</b></span><br>"
        f"{mesto + '<br>' if mesto else ''}{rows}{podval}</td></tr></table>"
    )


def kultury(attrs):
    color = attrs.get("vid_kultur_color") or "#00bcd4"
    stroki = _stroki(attrs, POLYA_KULTUR)
    if attrs.get("has_kontur") in (False, "false", "False", 0):
        stroki.append(("Контур", "по выделу <i>(свой контур участка не загружен)</i>"))
    nomer = attrs.get("id")
    zagolovok = "Участок лесных культур" + (f" № {tekst(nomer)}" if not pusto(nomer) else "")
    return _kartochka(zagolovok, color, _mesto(attrs), stroki)


def delyanka(attrs):
    color = attrs.get("color") or attrs.get("vid_rubki_color") or "#ff9800"
    nazvanie = attrs.get("nazvanie")
    zagolovok = "Делянка" + (f" «{tekst(nazvanie)}»" if not pusto(nazvanie) else "")
    return _kartochka(zagolovok, color, _mesto(attrs), _stroki(attrs, POLYA_DELYANKI))


def obmer(attrs):
    nazvanie = attrs.get("nazvanie")
    zagolovok = "Обмер с телефона" + (f" «{tekst(nazvanie)}»" if not pusto(nazvanie) else "")
    return _kartochka(zagolovok, "#00c853", _mesto(attrs), _stroki(attrs, POLYA_OBMERA))


def metka(attrs, photo_src=None):
    zagolovok = tekst(attrs.get("kategoriya_label")) if not pusto(attrs.get("kategoriya_label")) else "Метка"
    podval = f"<a href='{html.escape(photo_src)}'>Открыть фото</a>" if photo_src else ""
    return _kartochka(zagolovok, attrs.get("color") or "#2196f3", "", _stroki(attrs, POLYA_METKI), podval)


def vydel(attrs, sloy):
    """Выдел (или лесосека) из слоя ГИСлесхоза — когда объектов Лесовода в точке нет."""
    stroki = []
    ploshad = iz_polya(attrs, PLOSHAD_FIELDS)
    if ploshad:
        stroki.append(("Площадь", html.escape(ploshad)))
    lesn = iz_polya(attrs, LCH_FIELDS)
    if lesn:
        stroki.append(("Лесничество", ("№ " if lesn.isdigit() else "") + html.escape(lesn)))
    return _kartochka(f"Выдел — слой «{sloy}»", "#9e9e9e", _mesto(attrs), stroki)


PO_VIDU = {
    "lesokultury": kultury,
    "delyanki_status": delyanka,
    "delyanki_vid": delyanka,
    "delyanki_gruppa": delyanka,
    "tracks": obmer,
}


def kartochka(kind, attrs, photo_src=None):
    if kind == "geo_notes":
        return metka(attrs, photo_src)
    return PO_VIDU.get(kind, delyanka)(attrs)


def razobrat_poisk(text):
    """«35 12», «35/12», «кв 35 выд 12», «35» -> ("35", "12") / ("35", "")."""
    cleaned = (text or "").lower()
    for word in ("квартал", "кв.", "кв", "выдел", "выд.", "выд", "в."):
        cleaned = cleaned.replace(word, " ")
    for sep in ("/", "-", ",", ";", ":"):
        cleaned = cleaned.replace(sep, " ")
    parts = cleaned.split()
    if not parts:
        return "", ""
    return parts[0], (parts[1] if len(parts) > 1 else "")


def norm_id(value):
    text = tekst(value).strip().lower() if not pusto(value) else ""
    return text.lstrip("0") or text
