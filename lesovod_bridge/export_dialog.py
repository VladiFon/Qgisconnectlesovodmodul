from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import exporter

ALL = "— все —"

# (заголовок столбца, ключ в properties лесосеки)
COLUMNS = [
    ("Лесничество", None),
    ("Кв.", "num_kv"),
    ("Выд.", "num_vds"),
    ("№ лесосеки", "num"),
    ("Площадь, га", "area"),
    ("Вид рубки", "cuttingtyp"),
    ("Пользование", "usetype"),
    ("Дата", "date"),
    ("Примечание", "info"),
]


class ExportDialog(QDialog):
    """Выбор лесосек из базы ГИСлесхоз для выгрузки в shp: фильтры сверху,
    список с галочками, внизу — сохранить. Галочки сохраняются при смене
    фильтра, так что можно набрать лесосеки из разных кварталов."""

    def __init__(self, features, fallback_epsg=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Лесовод-мост — выгрузка лесосек в shp")
        self.resize(1000, 640)
        self.features = features
        self.fallback_epsg = fallback_epsg
        self.checked = set()  # индексы в self.features
        self.visible = []  # индексы строк таблицы -> индексы в self.features
        self.saved_path = None

        opts = exporter.choices(features)

        self.lesnichestvo = self._combo(opts["lesnichestvo"])
        self.year = self._combo(opts["year"])
        self.cuttingtyp = self._combo(opts["cuttingtyp"])
        self.usetype = self._combo(opts["usetype"])
        self.kvartaly = QLineEdit()
        self.kvartaly.setPlaceholderText("например: 23, 29, 163")
        self.search = QLineEdit()
        self.search.setPlaceholderText("№ лесосеки, выдел, ФИО, примечание")

        filters = QFormLayout()
        filters.addRow("Лесничество:", self.lesnichestvo)
        filters.addRow("Кварталы:", self.kvartaly)
        filters.addRow("Год (по дате):", self.year)
        filters.addRow("Вид рубки:", self.cuttingtyp)
        filters.addRow("Пользование:", self.usetype)
        filters.addRow("Поиск:", self.search)

        for combo in (self.lesnichestvo, self.year, self.cuttingtyp, self.usetype):
            combo.currentIndexChanged.connect(self.refresh)
        for edit in (self.kvartaly, self.search):
            edit.textChanged.connect(self.refresh)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)

        check_shown = QPushButton("Отметить показанные")
        check_shown.clicked.connect(lambda: self._set_shown(True))
        uncheck_shown = QPushButton("Снять с показанных")
        uncheck_shown.clicked.connect(lambda: self._set_shown(False))
        uncheck_all = QPushButton("Снять все")
        uncheck_all.clicked.connect(self._uncheck_all)
        self.counter = QLabel()

        row_buttons = QHBoxLayout()
        for w in (check_shown, uncheck_shown, uncheck_all):
            row_buttons.addWidget(w)
        row_buttons.addStretch(1)
        row_buttons.addWidget(self.counter)

        self.encoding = QComboBox()
        self.encoding.addItems(exporter.ENCODINGS)
        self.add_to_project = QCheckBox("Добавить выгруженный слой в проект")
        self.add_to_project.setChecked(True)
        save = QPushButton("Сохранить shp…")
        save.setDefault(True)
        save.clicked.connect(self.save)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Кодировка:"))
        bottom.addWidget(self.encoding)
        bottom.addWidget(self.add_to_project)
        bottom.addStretch(1)
        bottom.addWidget(save)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addLayout(row_buttons)
        layout.addWidget(self.table)
        layout.addLayout(bottom)

        self.refresh()

    @staticmethod
    def _combo(values):
        combo = QComboBox()
        combo.addItem(ALL)
        combo.addItems(values)
        return combo

    @staticmethod
    def _value(combo):
        text = combo.currentText()
        return "" if text == ALL else text

    def refresh(self):
        shown = exporter.filter_features(
            self.features,
            lesnichestvo=self._value(self.lesnichestvo),
            kvartaly=self.kvartaly.text(),
            year=self._value(self.year),
            cuttingtyp=self._value(self.cuttingtyp),
            usetype=self._value(self.usetype),
            search=self.search.text(),
        )
        ids = {id(f): i for i, f in enumerate(self.features)}
        self.visible = [ids[id(f)] for f in shown]

        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.visible))
        for row, idx in enumerate(self.visible):
            props = self.features[idx]["properties"]
            for col, (_, key) in enumerate(COLUMNS):
                text = exporter.lesnichestvo_of(props) if key is None else exporter.as_text(props.get(key))
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if idx in self.checked else Qt.Unchecked)
                    item.setData(Qt.UserRole, idx)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self._update_counter()

    def _on_item_changed(self, item):
        if item.column() != 0:
            return
        idx = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self.checked.add(idx)
        else:
            self.checked.discard(idx)
        self._update_counter()

    def _set_shown(self, value):
        if value:
            self.checked.update(self.visible)
        else:
            self.checked.difference_update(self.visible)
        self.refresh()

    def _uncheck_all(self):
        self.checked.clear()
        self.refresh()

    def _update_counter(self):
        area = 0.0
        for idx in self.checked:
            try:
                area += float(str(self.features[idx]["properties"].get("area") or 0).replace(",", "."))
            except ValueError:
                pass
        self.counter.setText(
            f"Показано: {len(self.visible)} из {len(self.features)}   "
            f"Отмечено: {len(self.checked)} ({area:.2f} га)"
        )

    def save(self):
        if not self.checked:
            QMessageBox.warning(self, "Лесовод-мост", "Отметьте галочками лесосеки для выгрузки.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить лесосеки в shp", "", "Shapefile (*.shp)")
        if not path:
            return
        selected = [self.features[i] for i in sorted(self.checked)]
        try:
            count, epsg = exporter.write_shapefile(
                path, selected, fallback_epsg=self.fallback_epsg, encoding=self.encoding.currentText()
            )
        except exporter.ExportError as exc:
            QMessageBox.critical(self, "Лесовод-мост", str(exc))
            return
        if not path.lower().endswith(".shp"):
            path += ".shp"
        self.saved_path = path if self.add_to_project.isChecked() else None
        QMessageBox.information(
            self,
            "Лесовод-мост",
            f"Выгружено лесосек: {count}\nФайл: {path}\nСистема координат: EPSG:{epsg} (файл .prj рядом)",
        )
        self.accept()
