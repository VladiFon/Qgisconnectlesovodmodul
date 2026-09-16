from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from . import settings


class LesovodBridgeSettingsDialog(QDialog):
    """Настройки плагина: подключение к базе ГИСлесхоз (только чтение)
    и подключение к серверу «Лесовод». Ни один из этих параметров не
    хранится в коде плагина — только в настройках QGIS текущего
    пользователя."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Лесовод-мост — настройки")

        values = settings.load()

        db_box = QGroupBox("База ГИСлесхоз (только чтение) — те же 5 значений, "
                            "что и в настройках ГИСлесхоз: шестерёнка -> «База данных»")
        db_form = QFormLayout(db_box)

        self.db_host = QLineEdit(values["db_host"])
        self.db_port = QSpinBox()
        self.db_port.setRange(1, 65535)
        self.db_port.setValue(int(values["db_port"] or 5432))
        self.db_name = QLineEdit(values["db_name"])
        self.db_user = QLineEdit(values["db_user"])
        self.db_password = QLineEdit(values["db_password"])
        self.db_password.setEchoMode(QLineEdit.Password)

        db_form.addRow("Адрес соединения (host):", self.db_host)
        db_form.addRow("Порт:", self.db_port)
        db_form.addRow("Имя базы данных:", self.db_name)
        db_form.addRow("Имя пользователя:", self.db_user)
        db_form.addRow("Пароль:", self.db_password)

        server_box = QGroupBox("Сервер «Лесовод»")
        server_form = QFormLayout(server_box)

        self.lesovod_base_url = QLineEdit(values["lesovod_base_url"])
        self.lesovod_token = QLineEdit(values["lesovod_token"])
        self.lesovod_token.setEchoMode(QLineEdit.Password)
        self.lesovod_token.setPlaceholderText("только сам токен, без слова Bearer")
        self.lesnichestvo_num = QLineEdit(values["lesnichestvo_num"])
        self.lesnichestvo_num.setPlaceholderText("необязательно — фильтр/метка лесничества")

        server_form.addRow("Адрес сервера:", self.lesovod_base_url)
        server_form.addRow("Токен (без слова «Bearer»):", self.lesovod_token)
        server_form.addRow("Номер лесничества:", self.lesnichestvo_num)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(db_box)
        layout.addWidget(server_box)
        layout.addWidget(buttons)

    def save(self):
        settings.save({
            "db_host": self.db_host.text().strip(),
            "db_port": str(self.db_port.value()),
            "db_name": self.db_name.text().strip(),
            "db_user": self.db_user.text().strip(),
            "db_password": self.db_password.text(),
            "lesovod_base_url": self.lesovod_base_url.text().strip().rstrip("/"),
            "lesovod_token": self.lesovod_token.text().strip(),
            "lesnichestvo_num": self.lesnichestvo_num.text().strip(),
        })
