import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QInputDialog, QMessageBox

from . import db_reader, okraska, publisher, server_layers, settings
from .export_dialog import ExportDialog
from .settings_dialog import LesovodBridgeSettingsDialog


class LesovodBridgePlugin:
    """Отдельный плагин «Лесовод-мост». Не редактирует и не встраивается
    в плагин ГИСлесхоз — работает рядом, своей отдельной кнопкой в
    панели инструментов QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.publish_action = None
        self.export_action = None
        self.settings_action = None
        self.layer_actions = []

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.publish_action = QAction(icon, "Опубликовать в Лесовод", self.iface.mainWindow())
        self.publish_action.triggered.connect(self.run_publish)
        self.iface.addToolBarIcon(self.publish_action)
        self.iface.addPluginToMenu("Лесовод-мост", self.publish_action)

        self.export_action = QAction("Выгрузить лесосеки в shp…", self.iface.mainWindow())
        self.export_action.triggered.connect(self.run_export)
        self.iface.addPluginToMenu("Лесовод-мост", self.export_action)

        # слои с сервера: метки рабочих с фото, делянки по статусу, культуры, обмеры
        for title, handler in (
            ("Метки рабочих (с фото)", self.run_add_geo_notes),
            ("Делянки по статусу работ", self.run_add_delyanki),
            ("Делянки по виду рубки (ССР, УЗ, ПРЖ…)", self.run_add_delyanki_vid),
            ("Делянки по виду пользования", self.run_add_delyanki_gruppa),
            ("Лесные культуры по виду", self.run_add_lesokultury),
            ("Раскрасить «Лесосеки» ГИСлесхоза по виду рубки", self.run_okrasit_gisleshoz_vid),
            ("Раскрасить «Лесосеки» ГИСлесхоза по виду пользования", self.run_okrasit_gisleshoz_gruppa),
            ("Обмеры с телефона", self.run_add_tracks),
            ("Обновить слои Лесовода", self.run_reload),
        ):
            action = QAction(title, self.iface.mainWindow())
            action.triggered.connect(handler)
            self.iface.addPluginToMenu("Лесовод-мост", action)
            self.layer_actions.append(action)

        self.settings_action = QAction("Настройки Лесовод-моста…", self.iface.mainWindow())
        self.settings_action.triggered.connect(self.run_settings)
        self.iface.addPluginToMenu("Лесовод-мост", self.settings_action)

    def unload(self):
        if self.publish_action:
            self.iface.removeToolBarIcon(self.publish_action)
            self.iface.removePluginMenu("Лесовод-мост", self.publish_action)
        if self.export_action:
            self.iface.removePluginMenu("Лесовод-мост", self.export_action)
        if self.settings_action:
            self.iface.removePluginMenu("Лесовод-мост", self.settings_action)
        for action in self.layer_actions:
            self.iface.removePluginMenu("Лесовод-мост", action)
        self.layer_actions = []

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

    def _add_server_layer(self, add, **kwargs):
        values = settings.load()
        try:
            add(values["lesovod_base_url"], values["lesovod_token"], **kwargs)
        except server_layers.ServerLayerError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return
        # подсказки карты показывают фото метки при наведении
        try:
            self.iface.actionMapTips().setChecked(True)
        except AttributeError:
            pass

    def run_add_geo_notes(self):
        self._add_server_layer(server_layers.add_geo_notes)

    def run_add_delyanki(self):
        self._add_server_layer(server_layers.add_delyanki, lesnichestvo_num=settings.load()["lesnichestvo_num"] or None)

    def run_add_delyanki_vid(self):
        self._add_server_layer(server_layers.add_delyanki, lesnichestvo_num=settings.load()["lesnichestvo_num"] or None,
                               rezhim="vid")

    def run_add_delyanki_gruppa(self):
        self._add_server_layer(server_layers.add_delyanki, lesnichestvo_num=settings.load()["lesnichestvo_num"] or None,
                               rezhim="gruppa")

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

    def run_add_lesokultury(self):
        self._add_server_layer(server_layers.add_lesokultury, lesnichestvo_num=settings.load()["lesnichestvo_num"] or None)

    def run_add_tracks(self):
        self._add_server_layer(server_layers.add_tracks)

    def run_reload(self):
        count = server_layers.reload_all()
        self.iface.messageBar().pushInfo("Лесовод-мост", f"Обновлено слоёв: {count}" if count else "Слоёв Лесовода в проекте нет")
