import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from . import db_reader, publisher, settings
from .settings_dialog import LesovodBridgeSettingsDialog


class LesovodBridgePlugin:
    """Отдельный плагин «Лесовод-мост». Не редактирует и не встраивается
    в плагин ГИСлесхоз — работает рядом, своей отдельной кнопкой в
    панели инструментов QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.publish_action = None
        self.settings_action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.publish_action = QAction(icon, "Опубликовать в Лесовод", self.iface.mainWindow())
        self.publish_action.triggered.connect(self.run_publish)
        self.iface.addToolBarIcon(self.publish_action)
        self.iface.addPluginToMenu("Лесовод-мост", self.publish_action)

        self.settings_action = QAction("Настройки Лесовод-моста…", self.iface.mainWindow())
        self.settings_action.triggered.connect(self.run_settings)
        self.iface.addPluginToMenu("Лесовод-мост", self.settings_action)

    def unload(self):
        if self.publish_action:
            self.iface.removeToolBarIcon(self.publish_action)
            self.iface.removePluginMenu("Лесовод-мост", self.publish_action)
        if self.settings_action:
            self.iface.removePluginMenu("Лесовод-мост", self.settings_action)

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
            status, response_text, deleted_batches = publisher.publish(
                base_url=values["lesovod_base_url"],
                token=values["lesovod_token"],
                features=features,
                lesnichestvo_num=values["lesnichestvo_num"] or None,
            )
        except publisher.PublishError as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Лесовод-мост", str(exc))
            return

        message = f"Опубликовано лесосек: {len(features)} (HTTP {status})."
        if deleted_batches:
            message += f"\nСтарых пачек этого слоя удалено: {len(deleted_batches)}."
        if read_errors:
            message += "\n\nПропущено строк с нераспознанной геометрией:\n" + "\n".join(read_errors)
        QMessageBox.information(self.iface.mainWindow(), "Лесовод-мост", message)
