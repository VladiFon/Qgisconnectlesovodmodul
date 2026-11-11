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

POLYA_PROBY = [
    ("uchastok", "Участок культур"),
    ("forma", "Форма"),
    ("ploshad_m2", "Площадь, м²"),
    ("etapy", "Учёты"),
    ("orientir", "Ориентир"),
    ("tochnost_m", "Точность GPS, м"),
    ("avtor_fio", "Поставил"),
    ("created_at", "Дата"),
]

POLYA_SKLADA = [
    ("ostatok", "На складе"),
    ("porody", "По породам, м³"),
    ("delyanka", "Делянка"),
    ("pochemu", "Внимание"),
    ("srok_vyvozki", "Срок вывозки"),
    ("dney_bez_dvizheniya", "Дней без движения"),
    ("vid", "Вид склада"),
    ("lesoseka", "Лесосека"),
    ("data_otkrytiya", "Открыт"),
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


def proba(attrs):
    """Пробная площадь лесных культур — на ней тех. приёмка и инвентаризации 1-го и 3-го года."""
    nomer = attrs.get("nomer")
    zagolovok = "Проба" + (f" П{tekst(nomer)}" if not pusto(nomer) else "")
    return _kartochka(zagolovok, "#2e5e3e", _mesto(attrs), _stroki(attrs, POLYA_PROBY))


def sklad(attrs):
    """Склад ЕГАИС: что на нём лежит по последней выгрузке «Наличие продукции»."""
    return _kartochka("Склад: " + tekst(attrs.get("nazvanie")), attrs.get("color") or "#2e7d32",
                      _mesto(attrs), _stroki(attrs, POLYA_SKLADA))


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
    chto = "Лесосека" if "лесосек" in str(sloy).lower() else "Выдел"
    return _kartochka(f"{chto} — слой «{sloy}»", "#9e9e9e", _mesto(attrs), stroki)


PO_VIDU = {
    "lesokultury": kultury,
    "delyanki_status": delyanka,
    "delyanki_vid": delyanka,
    "delyanki_gruppa": delyanka,
    "tracks": obmer,
    "proby": proba,
    "sklady_egais": sklad,
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


# ------------------------------------------------ площадь делянки по выделам --

KASANIE_GA = 0.01  # кусок меньше 100 м² и меньше 1 % делянки — касание по границе, а не часть выдела
KASANIE_DOLYA = 0.01
RASHOZHDENIE_GA = 0.05  # «в Лесоводе записано» расходится с картой заметно, если больше и 0.05 га, и 5 %
RASHOZHDENIE_DOLYA = 0.05


def _chislo(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def ga(value):
    return f"{value:.2f}"


def sobrat_po_vydelam(kuski, vsego_ga, zapisano=None):
    """Сводка «делянка по выделам» из кусков пересечения.

    kuski — [(лесничество, кв, выд, га куска, га всего выдела)], выдел может
    прийти несколькими кусками (несколько объектов слоя на один выдел);
    zapisano — {(кв, выд): га} — что записано по выделам делянки в Лесоводе.
    Возвращает словарь для po_vydelam_html / po_vydelam_tsv."""
    gruppy = {}
    for lch, kv, vd, kusok, vydel_ga in kuski:
        key = (str(lch or ""), norm_id(kv), norm_id(vd))
        g = gruppy.setdefault(key, {"lch": key[0], "kv": tekst(kv) if not pusto(kv) else "",
                                    "vd": tekst(vd) if not pusto(vd) else "", "ga": 0.0, "vydel_ga": 0.0})
        g["ga"] += kusok
        g["vydel_ga"] += vydel_ga or 0.0
    chasti, kasaniya = [], {"n": 0, "ga": 0.0}
    for g in gruppy.values():
        if g["ga"] < KASANIE_GA and (not vsego_ga or g["ga"] < vsego_ga * KASANIE_DOLYA):
            kasaniya["n"] += 1
            kasaniya["ga"] += g["ga"]
        else:
            chasti.append(g)
    zapisano = {(norm_id(kv), norm_id(vd)): v for (kv, vd), v in (zapisano or {}).items()}
    for g in chasti:
        g["lesovod_ga"] = zapisano.pop((norm_id(g["kv"]), norm_id(g["vd"])), None)
    # записано в Лесоводе, а на карте делянка этот выдел не задевает
    for (kv, vd), v in zapisano.items():
        chasti.append({"lch": "", "kv": kv, "vd": vd, "ga": 0.0, "vydel_ga": 0.0, "lesovod_ga": v})
    chasti.sort(key=lambda g: -g["ga"])
    pokryto = sum(g["ga"] for g in gruppy.values())
    return {
        "vsego_ga": vsego_ga,
        "chasti": chasti,
        "kasaniya": kasaniya,
        "vne_ga": max(0.0, vsego_ga - pokryto),
        "pokazat_lch": len({g["lch"] for g in chasti if g["lch"]}) > 1,
        "est_lesovod": any(g["lesovod_ga"] is not None for g in chasti),
    }


def _rashozhdenie(karta, lesovod):
    raznica = karta - lesovod
    return abs(raznica) > RASHOZHDENIE_GA and abs(raznica) > max(karta, lesovod) * RASHOZHDENIE_DOLYA


def _imya_vydela(g, pokazat_lch):
    imya = f"кв. {g['kv']} выд. {g['vd']}" if g["vd"] else f"кв. {g['kv']}"
    if pokazat_lch and g["lch"]:
        imya += f" ({'лесн. № ' if g['lch'].isdigit() else ''}{g['lch']})"
    return imya


def po_vydelam_html(svodka, sloy_vydelov, kopirovat_href=None):
    """Блок «Площадь по выделам» для панели — строками, панель узкая."""
    vsego = svodka["vsego_ga"]
    out = [f"<span style='color:#555'>По выделам (слой «{html.escape(sloy_vydelov)}»), "
           f"всего <b>{ga(vsego)} га</b>:</span><br>"]
    if not svodka["chasti"]:
        out.append("<i>Делянка не попадает ни в один выдел слоя.</i><br>")
    for g in svodka["chasti"]:
        imya = html.escape(_imya_vydela(g, svodka["pokazat_lch"]))
        if not g["ga"]:
            out.append(f"&bull; <b>{imya}</b> — <span style='color:#c62828'>в Лесоводе записано "
                       f"{ga(g['lesovod_ga'])} га, а контур делянки этот выдел не задевает</span><br>")
            continue
        stroka = f"&bull; <b>{imya}</b> — <b>{ga(g['ga'])} га</b>"
        if vsego and g["ga"]:
            stroka += f" · {g['ga'] / vsego * 100:.0f}% делянки"
        if g["vydel_ga"]:
            stroka += f" · {min(100.0, g['ga'] / g['vydel_ga'] * 100):.0f}% выдела"
        lesovod = g.get("lesovod_ga")
        if lesovod is not None:
            if _rashozhdenie(g["ga"], lesovod):
                stroka += (f"<br>&nbsp;&nbsp;<span style='color:#c62828'>в Лесоводе записано {ga(lesovod)} га "
                           f"(по карте {'+' if g['ga'] > lesovod else '−'}{ga(abs(g['ga'] - lesovod))})</span>")
            else:
                stroka += f"<br>&nbsp;&nbsp;<span style='color:#2e7d32'>в Лесоводе {ga(lesovod)} га — сходится</span>"
        elif svodka["est_lesovod"]:
            stroka += "<br>&nbsp;&nbsp;<span style='color:#c62828'>в Лесоводе этого выдела у делянки нет</span>"
        out.append(stroka + "<br>")
    if svodka["vne_ga"] >= KASANIE_GA:
        out.append(f"<span style='color:#555'>вне выделов слоя (дорога, просека, нет выдела): "
                   f"{ga(svodka['vne_ga'])} га</span><br>")
    k = svodka["kasaniya"]
    if k["n"]:
        out.append(f"<span style='color:#888'>ещё {k['n']} касани{'е' if k['n'] == 1 else 'й'} соседних выделов "
                   f"по границе (вместе {k['ga'] * 10000:.0f} м²) — неточность границ, в список не включено</span><br>")
    if kopirovat_href:
        out.append(f"<a href='{html.escape(kopirovat_href)}'>Скопировать для Excel</a><br>")
    return "".join(out)


def po_vydelam_tsv(nazvanie, svodka):
    """Строки для вставки в Excel (табуляция): делянка, кв, выд, га, % делянки, % выдела, в Лесоводе."""
    stroki = []
    for g in svodka["chasti"]:
        vsego = svodka["vsego_ga"]
        stroki.append("\t".join([
            nazvanie, g["lch"], g["kv"], g["vd"], ga(g["ga"]).replace(".", ","),
            f"{g['ga'] / vsego * 100:.0f}" if vsego else "",
            f"{min(100.0, g['ga'] / g['vydel_ga'] * 100):.0f}" if g["vydel_ga"] else "",
            ga(g["lesovod_ga"]).replace(".", ",") if g.get("lesovod_ga") is not None else "",
        ]))
    return stroki


TSV_ZAGOLOVOK = "\t".join(["Делянка", "Лесничество", "Квартал", "Выдел", "Площадь в выделе, га",
                           "% делянки", "% выдела", "В Лесоводе, га"])
