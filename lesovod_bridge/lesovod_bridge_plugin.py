import os

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QInputDialog, QLineEdit, QMenu, QMessageBox, QToolButton

from . import db_reader, okraska, otmetit_lk, privyazat_delyanku, publisher, server_layers, settings
from .chto_zdes import ChtoZdes
from .export_dialog import ExportDialog
from .settings_dialog import LesovodBridgeSettingsDialog

MENU = "Лесовод-мост"


def _gdal_timeouts():
    """Слои старых проектов, подключённые прямо к URL сервера, QGIS открывает
    сам ещё до плагина. У GDAL по умолчанию ожидание без конца — недоступный
    сервер вешал QGIS намертво. Ставим предел, если его не задали до нас."""
    try:
        from osgeo import gdal
    except ImportError:
        return
    for key, value in (("GDAL_HTTP_CONNECTTIMEOUT", "10"), ("GDAL_HTTP_TIMEOUT", "60")):
        if not gdal.GetConfigOption(key):
            gdal.SetConfigOption(key, value)


def _icon(name):
    path = os.path.join(os.path.dirname(__file__), "icons", name)
    return QIcon(path) if os.path.exists(path) else QIcon()


class LesovodBridgePlugin:
    """Отдельный плагин «Лесовод-мост». Не редактирует и не встраивается
    в плагин ГИСлесхоз — работает рядом, своей панелью инструментов
    «Лесовод-мост» (всё основное — в один щелчок) и меню Модули -> Лесовод-мост."""

    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.chto_zdes = None
        self.menu_actions = []
        _gdal_timeouts()

    def _action(self, title, handler, icon=None, tip=None, menu=True):
        action = QAction(icon or QIcon(), title, self.iface.mainWindow())
        action.triggered.connect(handler)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        if menu:
            self.iface.addPluginToMenu(MENU, action)
            self.menu_actions.append(action)
        return action

    def _knopka_s_menyu(self, default, actions, tip, popup=QToolButton.MenuButtonPopup):
        """Кнопка панели: щелчок — основное действие, стрелка — остальные."""
        button = QToolButton(self.toolbar)
        menu = QMenu(button)
        for action in actions:
            menu.addAction(action)
        button.setMenu(menu)
        button.setDefaultAction(default)
        button.setPopupMode(popup)
        button.setToolTip(tip)
        self.toolbar.addWidget(button)
        return button

    def initGui(self):
        self.chto_zdes = ChtoZdes(self.iface)

        # --- действия (они же — в меню Модули -> Лесовод-мост) -------------
        self.chto_zdes_action = self._action(
            "Что здесь — характеристики участка", self.run_chto_zdes, _icon("chto_zdes.svg"),
            "Что здесь: щёлкните по карте — покажутся характеристики участка лесных культур, делянки, "
            "обмера или метки в этой точке, какой бы слой ни был активным")
        self.chto_zdes_action.setCheckable(True)

        a_kultury = self._action("Лесные культуры по виду", self.run_add_lesokultury, _icon("kultury.svg"),
                                 "Добавить / обновить слой «Лесные культуры» с сервера Лесовода")
        a_del_status = self._action("Делянки по статусу работ", self.run_add_delyanki, _icon("delyanki.svg"),
                                    "Делянки по статусу работ (ожидает / в работе / выполнено)")
        a_del_vid = self._action("Делянки по виду рубки (ССР, УЗ, ПРЖ…)", self.run_add_delyanki_vid,
                                 _icon("delyanki.svg"))
        a_del_gruppa = self._action("Делянки по виду пользования", self.run_add_delyanki_gruppa, _icon("delyanki.svg"))
        a_metki = self._action("Метки рабочих (с фото)", self.run_add_geo_notes,
                               QgsApplication.getThemeIcon("/mActionAddMarker.svg"))
        a_tracks = self._action("Обмеры с телефона", self.run_add_tracks,
                                QgsApplication.getThemeIcon("/mActionMeasureArea.svg"))
        a_otmetit = self._action("Отметить выбранное как лесные культуры…", self.run_otmetit_lk, _icon("otmetit.svg"),
                                 "Выделенные полигоны активного слоя (выдел, лесосека) отметить как участок "
                                 "лесных культур в Лесоводе")
        a_delyanka = self._action("Привязать выбранное к делянке…", self.run_privyazat_delyanku,
                                  _icon("delyanka_kontur.svg"),
                                  "Выделенные полигоны активного слоя (лесосека, выдел) сделать контуром лесосеки "
                                  "делянки в Лесоводе — уже заведённой на этом выделе или новой")
        a_okr_vid = self._action("Раскрасить «Лесосеки» ГИСлесхоза по виду рубки", self.run_okrasit_gisleshoz_vid,
                                 _icon("okraska.svg"))
        a_okr_gruppa = self._action("Раскрасить «Лесосеки» ГИСлесхоза по виду пользования",
                                    self.run_okrasit_gisleshoz_gruppa, _icon("okraska.svg"))
        a_reload = self._action("Обновить слои Лесовода", self.run_reload,
                                QgsApplication.getThemeIcon("/mActionRefresh.svg"),
                                "Перечитать с сервера все слои Лесовода в проекте")
        a_publish = self._action("Опубликовать в Лесовод", self.run_publish, _icon("publish.svg"),
                                 "Отправить лесосеки из базы ГИСлесхоза на сервер Лесовода")
        a_export = self._action("Выгрузить лесосеки в shp…", self.run_export, _icon("shp.svg"),
                                "Выгрузить выбранные лесосеки ГИСлесхоза в shp (текущие изменения)")
        a_settings = self._action("Настройки Лесовод-моста…", self.run_settings,
                                  QgsApplication.getThemeIcon("/mActionOptions.svg"))

        # --- панель инструментов -------------------------------------------
        self.toolbar = self.iface.addToolBar(MENU)
        self.toolbar.setObjectName("LesovodBridgeToolbar")
        self.toolbar.setToolTip("Лесовод-мост")

        self.toolbar.addAction(self.chto_zdes_action)
        self.poisk = QLineEdit(self.toolbar)
        self.poisk.setPlaceholderText("кв выд: 35 12")
        self.poisk.setToolTip("Найти квартал / выдел в слоях Лесовода и ГИСлесхоза: «35 12» или «35» и Enter")
        self.poisk.setClearButtonEnabled(True)
        self.poisk.setMaximumWidth(150)
        self.poisk.returnPressed.connect(self.run_nayti)
        self.toolbar.addWidget(self.poisk)
        self.toolbar.addSeparator()

        self.toolbar.addAction(a_kultury)
        self._knopka_s_menyu(a_del_status, [a_del_status, a_del_vid, a_del_gruppa],
                             "Делянки: щелчок — по статусу работ, стрелка — по виду рубки / виду пользования")
        sloi = QAction(_icon("sloi.svg"), "Слои Лесовода", self.iface.mainWindow())
        self._knopka_s_menyu(sloi, [a_kultury, a_del_status, a_del_vid, a_del_gruppa, a_metki, a_tracks],
                             "Слои Лесовода: культуры, делянки, метки рабочих, обмеры с телефона",
                             QToolButton.InstantPopup)
        self.toolbar.addAction(a_reload)
        self.toolbar.addSeparator()

        self.toolbar.addAction(a_delyanka)
        self.toolbar.addAction(a_otmetit)
        self._knopka_s_menyu(a_okr_vid, [a_okr_vid, a_okr_gruppa],
                             "Раскрасить «Лесосеки» ГИСлесхоза: по виду рубки / по виду пользования")
        self.toolbar.addSeparator()

        self.toolbar.addAction(a_publish)
        self.toolbar.addAction(a_export)
        self.toolbar.addAction(a_settings)

        QgsProject.instance().readProject.connect(self._proekt_otkryt)
        self._proekt_otkryt()

    def unload(self):
        try:
            QgsProject.instance().readProject.disconnect(self._proekt_otkryt)
        except (TypeError, RuntimeError):
            pass
        for action in self.menu_actions:
            self.iface.removePluginMenu(MENU, action)
        self.menu_actions = []
        if self.chto_zdes:
            self.chto_zdes.unload()
            self.chto_zdes = None
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

    def run_chto_zdes(self, checked=True):
        if checked:
            self.chto_zdes.vklyuchit(self.chto_zdes_action)
        elif self.iface.mapCanvas().mapTool() is self.chto_zdes.tool:
            self.iface.mapCanvas().unsetMapTool(self.chto_zdes.tool)

    def run_nayti(self):
        self.chto_zdes.nayti(self.poisk.text())

    def run_settings(self):
        dialog = LesovodBridgeSettingsDialog(self.iface.mainWindow())
        if dialog.exec_():
            dialog.save()

    def _read_lesoseki(self):
        """Читает лесосеки из базы ГИСлесхоз (только чтение). Возвращает
        (features, read_errors) или None, если чтение не удалось — тогда
        пользователю уже показано сообщение."""
        values = settings.load()

        missing = [
            label
            for key, label in (
                ("db_host", "адрес соединения"),
                ("db_name", "имя базы данных"),
                ("db_user", "имя пользователя"),
                ("db_password", "пароль"),
            )
            if not values[key]
        ]
        if missing:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Лесовод-мост",
                "Не заполнены настройки подключения к базе ГИСлесхоз: "
                + ", ".join(missing)
                + ".\nОткройте «Лесовод-мост -> Настройки Лесовод-моста…».",
            )
            return None

        try:
            features, read_errors = db_reader.fetch_lesoseki(
                db_host=values["db_host"],
                db_port=values["db_port"],
                db_name=values["db_name"],
                db_user=values["db_user"],
                db_password=values["db_password"],
            )
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку чтения
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Лесовод-мост",
                f"Не удалось прочитать базу ГИСлесхоз (только чтение):\n{exc}",
            )
            return None

        if not features:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Лесовод-мост",
                "В таблице area не нашлось ни одной лесосеки с распознанной геометрией."
                + (("\n\nПредупреждения:\n" + "\n".join(read_errors)) if read_errors else ""),
            )
            return None
        return features, read_errors

    def run_publish(self):
        values = settings.load()
        read = self._read_lesoseki()
        if read is None:
            return
        features, read_errors = read

        try:
            result = publisher.publish(
                base_url=values["lesovod_base_url"],
                token=values["lesovod_token"],
                features=features,
                lesnichestvo_num=values["lesnichestvo_num"] or None,
                source_epsg=values.get("source_epsg") or None,
            )
        except publisher.PublishError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return

        names = {num: name for name, num in result["lesnichestva"].items()}
        lines = [f"  {names.get(num, 'лесничество ' + num)}: {count}" for num, count in result["published"].items()]
        message = "Опубликовано лесосек по лесничествам:\n" + "\n".join(lines)
        if result["deleted"]:
            message += f"\n\nСтарых пачек слоя удалено: {len(result['deleted'])}."
        if result["skipped"]:
            message += (
                f"\n\nНе удалось определить лесничество у {len(result['skipped'])} лесосек "
                f"(uid: {', '.join(result['skipped'][:20])}) — они не отправлены. "
                "Если все лесосеки из одного лесничества, укажите его номер в настройках."
            )
        if read_errors:
            message += "\n\nПропущено строк с нераспознанной геометрией:\n" + "\n".join(read_errors)
        QMessageBox.information(self.iface.mainWindow(), "Лесовод-мост", message)

    def run_export(self):
        read = self._read_lesoseki()
        if read is None:
            return
        features, read_errors = read
        if read_errors:
            self.iface.messageBar().pushWarning(
                "Лесовод-мост", f"Пропущено лесосек с нераспознанной геометрией: {len(read_errors)}"
            )
        dialog = ExportDialog(
            features, fallback_epsg=settings.load().get("source_epsg") or None, parent=self.iface.mainWindow()
        )
        if dialog.exec_() and dialog.saved_path:
            name = os.path.splitext(os.path.basename(dialog.saved_path))[0]
            self.iface.addVectorLayer(dialog.saved_path, name, "ogr")

    # ------------------------------------------------------ слои с сервера ---

    def _add_server_layer(self, kind, **params):
        """Слой качается в фоне — QGIS не ждёт сервер; появится, когда скачается."""
        values = settings.load()
        title = server_layers.VIDY[kind][1]

        def gotovo(layer, oshibka):
            if oshibka:
                QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост", oshibka)
                return
            if kind == "geo_notes":
                # подсказки карты показывают фото метки при наведении
                try:
                    self.iface.actionMapTips().setChecked(True)
                except AttributeError:
                    pass
            self.iface.messageBar().pushSuccess("Лесовод-мост", f"Слой «{title}» загружен: {layer.featureCount()} об.")

        try:
            server_layers.dobavit(kind, values["lesovod_base_url"], values["lesovod_token"], gotovo, **params)
        except server_layers.ServerLayerError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return
        self.iface.messageBar().pushInfo("Лесовод-мост", f"Загружаю «{title}» с сервера — работать можно, слой появится сам")

    def _lesnichestvo(self):
        return settings.load()["lesnichestvo_num"] or None

    def run_add_geo_notes(self):
        self._add_server_layer("geo_notes")

    def run_add_delyanki(self):
        self._add_server_layer("delyanki_status", lesnichestvo_num=self._lesnichestvo())

    def run_add_delyanki_vid(self):
        self._add_server_layer("delyanki_vid", lesnichestvo_num=self._lesnichestvo())

    def run_add_delyanki_gruppa(self):
        self._add_server_layer("delyanki_gruppa", lesnichestvo_num=self._lesnichestvo())

    def _okrasit_gisleshoz(self, rezhim):
        """Стиль слоя «Лесосеки» ГИСлесхоза (или его выгрузки) по cuttingtyp /
        usetype — только в проекте QGIS, база ГИСлесхоза не меняется."""
        layers = okraska.gisleshoz_lesoseki_layers()
        if not layers:
            QMessageBox.information(
                self.iface.mainWindow(), "Лесовод-мост",
                "В проекте нет слоя лесосек ГИСлесхоза (с полями cuttingtyp / usetype). "
                "Инициализируйте проект в ГИСлесхозе или добавьте слой «Лесосеки».")
            return
        layer = layers[0]
        active = self.iface.activeLayer()
        if len(layers) > 1:
            names = [lr.name() for lr in layers]
            current = names.index(active.name()) if active in layers else 0
            name, ok = QInputDialog.getItem(self.iface.mainWindow(), "Лесовод-мост", "Какой слой раскрасить:",
                                            names, current, False)
            if not ok:
                return
            layer = layers[names.index(name)]
        values = settings.load()
        legendy = okraska.load_legendy(values["lesovod_base_url"], values["lesovod_token"])
        try:
            okraska.okrasit_gisleshoz(layer, rezhim, legendy)
        except ValueError as exc:
            QMessageBox.warning(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())
        self.iface.messageBar().pushInfo("Лесовод-мост", f"Слой «{layer.name()}» раскрашен, легенда — в панели слоёв")

    def run_okrasit_gisleshoz_vid(self):
        self._okrasit_gisleshoz("vid")

    def run_okrasit_gisleshoz_gruppa(self):
        self._okrasit_gisleshoz("gruppa")

    def run_otmetit_lk(self):
        """Выделенные полигоны активного слоя -> участки лесных культур в «Лесоводе»."""
        layer = self.iface.activeLayer()
        try:
            features = otmetit_lk.vybrannye(layer)
        except otmetit_lk.OtmetkaError as exc:
            QMessageBox.information(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return
        values = settings.load()
        legendy = okraska.load_legendy(values["lesovod_base_url"], values["lesovod_token"])
        dialog = otmetit_lk.OtmetkaDialog(features, legendy.get("vidy_kultur") or [], self.iface.mainWindow())
        if dialog.exec_() != dialog.Accepted:
            return
        sozdano, ploshad = 0, 0.0
        try:
            for body in dialog.zapisi(layer, values["lesnichestvo_num"]):
                result = otmetit_lk.otpravit(values["lesovod_base_url"], values["lesovod_token"], body)
                sozdano += 1
                ploshad += float(result.get("ploshad_kontura") or 0)
        except otmetit_lk.OtmetkaError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост",
                                 f"Отмечено участков: {sozdano}.\n{exc}")
            return
        self._obnovit_sloi(tiho=True)
        self.iface.messageBar().pushSuccess(
            "Лесовод-мост", f"Отмечено как лесные культуры: {sozdano} уч., {ploshad:.2f} га — видно на сайте и в слое «Лесные культуры по виду»")

    def run_privyazat_delyanku(self):
        """Выделенные полигоны -> контуры лесосек делянок в «Лесоводе»."""
        layer = self.iface.activeLayer()
        try:
            features = otmetit_lk.vybrannye(layer)
        except otmetit_lk.OtmetkaError as exc:
            QMessageBox.information(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return
        values = settings.load()
        if not values["lesovod_base_url"] or not values["lesovod_token"]:
            QMessageBox.warning(self.iface.mainWindow(), "Лесовод-мост",
                                "Не заданы адрес сервера «Лесовод» и токен — «Настройки Лесовод-моста…».")
            return
        legendy = okraska.load_legendy(values["lesovod_base_url"], values["lesovod_token"])
        stroki = [privyazat_delyanku.stroka_iz_obekta(f, legendy, values["lesnichestvo_num"]) for f in features]
        dialog = privyazat_delyanku.PrivyazkaDialog(stroki, legendy, values["lesovod_base_url"],
                                                    values["lesovod_token"], self.iface.mainWindow())
        if dialog.exec_() != dialog.Accepted:
            return
        privyazano, novyh, preduprezhdeniya = 0, 0, []
        obshaya_delyanka = None
        try:
            for body, novaya in dialog.zapisi(layer):
                if novaya and dialog.odnoy.isChecked() and obshaya_delyanka is not None:
                    body["delyanka_id"] = obshaya_delyanka
                result = privyazat_delyanku.otpravit(values["lesovod_base_url"], values["lesovod_token"], body)
                if novaya:
                    novyh += 1
                    obshaya_delyanka = obshaya_delyanka or result.get("delyanka_id")
                else:
                    privyazano += 1
                if result.get("warning"):
                    preduprezhdeniya.append(f"кв. {body['kvartal']} выд. {body['vydel']}: {result['warning']}")
        except otmetit_lk.OtmetkaError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост",
                                 f"Привязано: {privyazano}, новых: {novyh}.\n{exc}")
            return
        self._obnovit_sloi(tiho=True)
        text = f"Контуров привязано к делянкам: {privyazano}, новых делянок/выделов: {novyh} — видно на сайте, " \
               "в приложении и в слое «Делянки»"
        if preduprezhdeniya:
            QMessageBox.warning(self.iface.mainWindow(), "Лесовод-мост",
                                text + ".\n\nПроверьте площадь:\n" + "\n".join(preduprezhdeniya))
        else:
            self.iface.messageBar().pushSuccess("Лесовод-мост", text)

    def run_add_lesokultury(self):
        self._add_server_layer("lesokultury", lesnichestvo_num=self._lesnichestvo())

    def run_add_tracks(self):
        self._add_server_layer("tracks")

    def _obnovit_sloi(self, tiho=False):
        """Перекачать слои Лесовода в фоне. tiho — без сообщения «начал»."""
        values = settings.load()

        def gotovo(obnovleno, oshibki):
            if oshibki:
                spisok = "; ".join(f"«{server_layers.VIDY[k][1]}»: {t}" for k, t in oshibki.items())
                self.iface.messageBar().pushWarning(
                    "Лесовод-мост", f"Не обновлены слои {spisok}. На карте — последняя скачанная копия.")
            if obnovleno:
                self.iface.messageBar().pushSuccess("Лесовод-мост", f"Слои Лесовода обновлены: {obnovleno}")

        count = server_layers.reload_all(values["lesovod_base_url"], values["lesovod_token"], gotovo)
        if not tiho:
            self.iface.messageBar().pushInfo(
                "Лесовод-мост", f"Обновляю слоёв: {count} — в фоне, работать можно" if count else "Слоёв Лесовода в проекте нет")
        return count

    def run_reload(self):
        self._obnovit_sloi()

    def _proekt_otkryt(self, *_args):
        """Проект со слоями старой версии (подключены прямо к URL — из-за этого
        QGIS и вис) или с потерянной локальной копией — перевести на копию."""
        if server_layers.nuzhno_perevesti():
            self._obnovit_sloi(tiho=True)
