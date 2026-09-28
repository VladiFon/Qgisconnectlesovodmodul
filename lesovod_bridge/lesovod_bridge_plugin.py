import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from . import db_reader, publisher, server_layers, settings
from .settings_dialog import LesovodBridgeSettingsDialog


class LesovodBridgePlugin:
    """Отдельный плагин «Лесовод-мост». Не редактирует и не встраивается
    в плагин ГИСлесхоз — работает рядом, своей отдельной кнопкой в
    панели инструментов QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.publish_action = None
        self.settings_action = None
        self.layer_actions = []

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.publish_action = QAction(icon, "Опубликовать в Лесовод", self.iface.mainWindow())
        self.publish_action.triggered.connect(self.run_publish)
        self.iface.addToolBarIcon(self.publish_action)
        self.iface.addPluginToMenu("Лесовод-мост", self.publish_action)

        # слои с сервера: метки рабочих с фото, делянки по статусу, культуры, обмеры
        for title, handler in (
            ("Метки рабочих (с фото)", self.run_add_geo_notes),
            ("Делянки по статусу работ", self.run_add_delyanki),
            ("Лесные культуры", self.run_add_lesokultury),
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
        if self.settings_action:
            self.iface.removePluginMenu("Лесовод-мост", self.settings_action)
        for action in self.layer_actions:
            self.iface.removePluginMenu("Лесовод-мост", action)
        self.layer_actions = []

    def run_settings(self):
        dialog = LesovodBridgeSettingsDialog(self.iface.mainWindow())
        if dialog.exec_():
            dialog.save()

    def run_publish(self):
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
            return

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
            return

        if not features:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Лесовод-мост",
                "В таблице area не нашлось ни одной лесосеки с распознанной геометрией."
                + (("\n\nПредупреждения:\n" + "\n".join(read_errors)) if read_errors else ""),
            )
            return

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

    def run_add_lesokultury(self):
        self._add_server_layer(server_layers.add_lesokultury, lesnichestvo_num=settings.load()["lesnichestvo_num"] or None)

    def run_add_tracks(self):
        self._add_server_layer(server_layers.add_tracks)

    def run_reload(self):
        count = server_layers.reload_all()
        self.iface.messageBar().pushInfo("Лесовод-мост", f"Обновлено слоёв: {count}" if count else "Слоёв Лесовода в проекте нет")
