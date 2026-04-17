import json
import os
import random
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QPixmap, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QDoubleSpinBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "画像ルーレット配信アプリ"
SETTINGS_FILE = "roulette_state.json"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FILENAME_PATTERN = re.compile(r"^(?P<order>\d{3})_(?P<name>.+)$")


@dataclass
class ImageEntry:
    path: str
    name: str
    order: Optional[int] = None


@dataclass
class HistoryEntry:
    draw_order: int
    path: str
    name: str


class ImageViewer(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(520, 520)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QLabel { background-color: #161616; color: #d0d0d0; border: 1px solid #404040; }"
        )
        self.setText("画像を読み込んでください")

    def set_image(self, path: Optional[str]) -> None:
        if not path or not os.path.exists(path):
            self._pixmap = None
            self.setText("画像がありません")
            self.setPixmap(QPixmap())
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._pixmap = None
            self.setText("画像を表示できません")
            self.setPixmap(QPixmap())
            return

        self._pixmap = pixmap
        self._refresh()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if not self._pixmap:
            return
        scaled = self._pixmap.scaled(
            self.size() - QSize(16, 16),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.setPixmap(scaled)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 920)

        self.images: List[ImageEntry] = []
        self.used_paths: List[str] = []
        self.history: List[HistoryEntry] = []

        self.current_result_path: Optional[str] = None
        self.final_selected_path: Optional[str] = None
        self.roulette_running = False
        self.elapsed_ms = 0
        self.tick_ms = 80

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_roulette_tick)

        self.icon_cache: dict[tuple[str, int, int], QIcon] = {}

        self._build_ui()
        self._load_state()
        self._refresh_all_lists()
        self._update_status_label()

    def _build_ui(self) -> None:
        toolbar = QToolBar("toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_images_action = QAction("画像追加", self)
        add_images_action.triggered.connect(self.add_images)
        toolbar.addAction(add_images_action)

        add_folder_action = QAction("フォルダ読込", self)
        add_folder_action.triggered.connect(self.add_folder)
        toolbar.addAction(add_folder_action)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        form = QFormLayout()
        self.stop_seconds_spin = QDoubleSpinBox()
        self.stop_seconds_spin.setRange(0.0, 60.0)
        self.stop_seconds_spin.setDecimals(1)
        self.stop_seconds_spin.setSingleStep(0.5)
        self.stop_seconds_spin.setValue(3.0)
        form.addRow("停止秒数", self.stop_seconds_spin)

        self.no_repeat_checkbox = QCheckBox("重複しないモード")
        self.no_repeat_checkbox.setChecked(True)
        form.addRow("抽選設定", self.no_repeat_checkbox)
        left_layout.addLayout(form)

        self.image_list = QListWidget()
        self.image_list.setViewMode(QListWidget.IconMode)
        self.image_list.setIconSize(QSize(120, 120))
        self.image_list.setResizeMode(QListWidget.Adjust)
        self.image_list.setMovement(QListWidget.Static)
        self.image_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.image_list.itemSelectionChanged.connect(self._on_image_selected)
        left_layout.addWidget(QLabel("登録画像一覧"))
        left_layout.addWidget(self.image_list, stretch=1)

        button_row = QHBoxLayout()
        self.add_images_button = QPushButton("画像追加")
        self.add_images_button.clicked.connect(self.add_images)
        button_row.addWidget(self.add_images_button)

        self.add_folder_button = QPushButton("フォルダ読込")
        self.add_folder_button.clicked.connect(self.add_folder)
        button_row.addWidget(self.add_folder_button)
        left_layout.addLayout(button_row)

        button_row2 = QHBoxLayout()
        self.remove_button = QPushButton("選択削除")
        self.remove_button.clicked.connect(self.remove_selected_image)
        button_row2.addWidget(self.remove_button)

        self.reset_used_button = QPushButton("使用済み解除")
        self.reset_used_button.clicked.connect(self.reset_used_images)
        button_row2.addWidget(self.reset_used_button)
        left_layout.addLayout(button_row2)

        self.clear_all_images_button = QPushButton("登録画像を全削除")
        self.clear_all_images_button.clicked.connect(self.clear_all_images)
        left_layout.addWidget(self.clear_all_images_button)

        self.clear_history_button = QPushButton("履歴クリア")
        self.clear_history_button.clicked.connect(self.clear_history)
        left_layout.addWidget(self.clear_history_button)

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        self.viewer = ImageViewer()
        center_layout.addWidget(self.viewer, stretch=1)

        self.name_label = QLabel("表示名: -")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet("font-size: 20px; padding: 8px;")
        center_layout.addWidget(self.name_label)

        self.status_label = QLabel("待機中")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 16px; color: #bbbbbb; padding-bottom: 8px;")
        center_layout.addWidget(self.status_label)

        start_row = QHBoxLayout()
        self.start_button = QPushButton("スタート")
        self.start_button.setMinimumHeight(54)
        self.start_button.clicked.connect(self.start_roulette)
        start_row.addWidget(self.start_button)

        self.reset_all_button = QPushButton("全体リセット")
        self.reset_all_button.setMinimumHeight(54)
        self.reset_all_button.clicked.connect(self.reset_all)
        start_row.addWidget(self.reset_all_button)
        center_layout.addLayout(start_row)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("履歴（引いた順）"))

        self.history_list = QListWidget()
        self.history_list.setIconSize(QSize(96, 96))
        self.history_list.setSelectionMode(QAbstractItemView.NoSelection)
        right_layout.addWidget(self.history_list, stretch=1)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 2)

    def add_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を選択",
            "",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if not files:
            return
        self._add_image_paths(files)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "画像フォルダを選択")
        if not folder:
            return

        paths = []
        for child in sorted(Path(folder).iterdir()):
            if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                paths.append(str(child))

        if not paths:
            QMessageBox.information(self, "画像なし", "対応画像が見つかりませんでした。")
            return

        self._add_image_paths(paths)

    def _add_image_paths(self, paths: List[str]) -> None:
        existing = {entry.path for entry in self.images}
        warnings = []

        for path in paths:
            norm = os.path.normpath(path)
            if norm in existing:
                continue

            parsed_name, parsed_order, warning = self._parse_filename(norm)
            if warning:
                warnings.append(f"{os.path.basename(norm)}: {warning}")

            self.images.append(ImageEntry(path=norm, name=parsed_name, order=parsed_order))
            existing.add(norm)

        self.images.sort(key=self._image_sort_key)
        self._refresh_all_lists()
        self._save_state()

        if warnings:
            QMessageBox.information(
                self,
                "命名ルールについて",
                "以下のファイルは推奨命名ルール（001_表示名.png）ではありません。\n\n"
                + "\n".join(warnings[:15]),
            )

    def _parse_filename(self, path: str) -> tuple[str, Optional[int], Optional[str]]:
        stem = Path(path).stem
        match = FILENAME_PATTERN.match(stem)
        if match:
            order = int(match.group("order"))
            name = match.group("name")
            return name, order, None
        return stem, None, "推奨ルールは 001_表示名 です"

    def _image_sort_key(self, entry: ImageEntry):
        return (entry.order is None, entry.order if entry.order is not None else 999999, entry.name.lower())

    def remove_selected_image(self) -> None:
        item = self.image_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)

        self.images = [img for img in self.images if img.path != path]
        self.used_paths = [p for p in self.used_paths if p != path]
        self.history = [h for h in self.history if h.path != path]

        if self.current_result_path == path:
            self.current_result_path = None
            self.viewer.set_image(None)
            self.name_label.setText("表示名: -")

        self._refresh_all_lists()
        self._update_status_label()
        self._save_state()

    def clear_all_images(self) -> None:
        if not self.images:
            QMessageBox.information(self, "削除対象なし", "登録画像はありません。")
            return

        reply = QMessageBox.question(
            self,
            "登録画像を全削除",
            "登録画像、使用済み状態、履歴をすべて削除します。よろしいですか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.timer.stop()
        self.roulette_running = False
        self.final_selected_path = None
        self.current_result_path = None
        self.images = []
        self.used_paths = []
        self.history = []
        self.viewer.set_image(None)
        self.name_label.setText("表示名: -")
        self.start_button.setEnabled(True)
        self._refresh_all_lists()
        self._update_status_label()
        self._save_state()

    def _on_image_selected(self) -> None:
        item = self.image_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        entry = self._find_image(path)
        if not entry:
            return
        self.viewer.set_image(entry.path)
        self.name_label.setText(f"表示名: {entry.name}")

    def _find_image(self, path: str) -> Optional[ImageEntry]:
        for entry in self.images:
            if entry.path == path:
                return entry
        return None

    def start_roulette(self) -> None:
        if self.roulette_running:
            return
        if not self.images:
            QMessageBox.warning(self, "抽選不可", "画像が登録されていません。")
            return

        available = self._get_available_images()
        if not available:
            QMessageBox.information(self, "抽選終了", "未使用の画像がありません。使用済み解除または全体リセットをしてください。")
            return

        self.final_selected_path = random.choice(available).path
        stop_seconds = float(self.stop_seconds_spin.value())

        if stop_seconds <= 0.0:
            self._finish_roulette_immediately()
            return

        self.roulette_running = True
        self.elapsed_ms = 0
        self.tick_ms = 80
        self.start_button.setEnabled(False)
        self.timer.start(self.tick_ms)
        self._update_status_label()

    def _get_available_images(self) -> List[ImageEntry]:
        if not self.no_repeat_checkbox.isChecked():
            return list(self.images)
        used = set(self.used_paths)
        return [img for img in self.images if img.path not in used]

    def _on_roulette_tick(self) -> None:
        if not self.roulette_running:
            return

        stop_ms = int(self.stop_seconds_spin.value() * 1000)
        available = self._get_available_images()
        if not available:
            self.timer.stop()
            self.roulette_running = False
            self.start_button.setEnabled(True)
            self._update_status_label()
            return

        current = random.choice(available)
        self.viewer.set_image(current.path)
        self.name_label.setText(f"表示名: {current.name}")

        self.elapsed_ms += self.tick_ms
        remaining_ratio = self.elapsed_ms / max(stop_ms, 1)
        self.tick_ms = self._calc_next_tick_ms(remaining_ratio)
        self.timer.start(self.tick_ms)

        if self.elapsed_ms >= stop_ms:
            self._finish_roulette_immediately()

    def _calc_next_tick_ms(self, progress: float) -> int:
        if progress < 0.50:
            return 80
        if progress < 0.75:
            return 130
        if progress < 0.90:
            return 220
        return 360

    def _finish_roulette_immediately(self) -> None:
        self.timer.stop()
        self.roulette_running = False
        self.start_button.setEnabled(True)

        if not self.final_selected_path:
            self._update_status_label()
            return

        entry = self._find_image(self.final_selected_path)
        if not entry:
            QMessageBox.warning(self, "エラー", "選ばれた画像が見つかりません。")
            self._update_status_label()
            return

        self.current_result_path = entry.path
        self.viewer.set_image(entry.path)
        self.name_label.setText(f"表示名: {entry.name}")

        if self.no_repeat_checkbox.isChecked() and entry.path not in self.used_paths:
            self.used_paths.append(entry.path)

        history_entry = HistoryEntry(
            draw_order=len(self.history) + 1,
            path=entry.path,
            name=entry.name,
        )
        self.history.append(history_entry)

        self.final_selected_path = None
        self._refresh_used_marks_only()

        item = QListWidgetItem()
        item.setText(f"{history_entry.draw_order}回目\n{history_entry.name}")
        icon = self._make_icon(history_entry.path, QSize(96, 96))
        if icon:
            item.setIcon(icon)
        item.setToolTip(history_entry.path)
        self.history_list.addItem(item)

        self._update_status_label()
        self._save_state()

    def reset_used_images(self) -> None:
        self.used_paths = []
        self._refresh_used_marks_only()
        self._update_status_label()
        self._save_state()

    def clear_history(self) -> None:
        self.history = []
        self._refresh_history_list()
        self._update_status_label()
        self._save_state()

    def reset_all(self) -> None:
        self.timer.stop()
        self.roulette_running = False
        self.final_selected_path = None
        self.current_result_path = None
        self.used_paths = []
        self.history = []
        self.viewer.set_image(None)
        self.name_label.setText("表示名: -")
        self.start_button.setEnabled(True)
        self._refresh_all_lists()
        self._update_status_label()
        self._save_state()

    def _refresh_all_lists(self) -> None:
        self._refresh_image_list()
        self._refresh_history_list()

    def _refresh_image_list(self) -> None:
        selected_path = None
        current_item = self.image_list.currentItem()
        if current_item:
            selected_path = current_item.data(Qt.UserRole)

        self.image_list.setUpdatesEnabled(False)
        self.image_list.clear()
        used = set(self.used_paths)

        for entry in self.images:
            item = QListWidgetItem()
            label = entry.name
            if entry.order is not None:
                label = f"{entry.order:03d}_{entry.name}"
            if entry.path in used and self.no_repeat_checkbox.isChecked():
                label += "\n[使用済み]"
            item.setText(label)
            item.setData(Qt.UserRole, entry.path)
            item.setToolTip(entry.path)

            icon = self._make_icon(entry.path, QSize(120, 120))
            if icon:
                item.setIcon(icon)
            if entry.path in used and self.no_repeat_checkbox.isChecked():
                item.setBackground(QColor("#2a2a2a"))
            self.image_list.addItem(item)

        self.image_list.setUpdatesEnabled(True)

        if selected_path:
            for i in range(self.image_list.count()):
                item = self.image_list.item(i)
                if item.data(Qt.UserRole) == selected_path:
                    self.image_list.setCurrentItem(item)
                    break

    def _refresh_history_list(self) -> None:
        self.history_list.setUpdatesEnabled(False)
        self.history_list.clear()
        for hist in self.history:
            item = QListWidgetItem()
            item.setText(f"{hist.draw_order}回目\n{hist.name}")
            icon = self._make_icon(hist.path, QSize(96, 96))
            if icon:
                item.setIcon(icon)
            item.setToolTip(hist.path)
            self.history_list.addItem(item)
        self.history_list.setUpdatesEnabled(True)

    def _make_icon(self, path: str, size: QSize) -> Optional[QIcon]:
        if not os.path.exists(path):
            return None

        key = (path, size.width(), size.height())
        cached = self.icon_cache.get(key)
        if cached is not None:
            return cached

        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon = QIcon(scaled)
        self.icon_cache[key] = icon
        return icon

    def _update_status_label(self) -> None:
        available_count = len(self._get_available_images())
        total_count = len(self.images)
        if self.roulette_running:
            self.status_label.setText("抽選中...")
            return
        self.status_label.setText(
            f"登録: {total_count}枚 / 抽選可能: {available_count}枚 / 履歴: {len(self.history)}件"
        )

    def _refresh_used_marks_only(self) -> None:
        used = set(self.used_paths)
        self.image_list.setUpdatesEnabled(False)
        for i in range(self.image_list.count()):
            item = self.image_list.item(i)
            path = item.data(Qt.UserRole)
            entry = self._find_image(path)
            if not entry:
                continue

            label = entry.name
            if entry.order is not None:
                label = f"{entry.order:03d}_{entry.name}"
            if path in used and self.no_repeat_checkbox.isChecked():
                label += "\n[使用済み]"
                item.setBackground(QColor("#2a2a2a"))
            else:
                item.setBackground(QColor("transparent"))
            item.setText(label)
        self.image_list.setUpdatesEnabled(True)

    def _save_state(self) -> None:
        data = {
            "stop_seconds": float(self.stop_seconds_spin.value()),
            "no_repeat": self.no_repeat_checkbox.isChecked(),
            "images": [asdict(entry) for entry in self.images],
            "used_paths": self.used_paths,
            "history": [asdict(entry) for entry in self.history],
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "保存失敗", f"状態保存に失敗しました。\n{exc}")

    def _load_state(self) -> None:
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        loaded_images = []
        for raw in data.get("images", []):
            path = raw.get("path")
            name = raw.get("name")
            order = raw.get("order")
            if not path or not name:
                continue
            if os.path.exists(path):
                loaded_images.append(ImageEntry(path=path, name=name, order=order))

        self.images = loaded_images
        self.images.sort(key=self._image_sort_key)

        existing_paths = {entry.path for entry in self.images}
        self.used_paths = [p for p in data.get("used_paths", []) if p in existing_paths]

        self.history = []
        for raw in data.get("history", []):
            path = raw.get("path")
            name = raw.get("name")
            draw_order = raw.get("draw_order")
            if path in existing_paths and isinstance(draw_order, int) and name:
                self.history.append(HistoryEntry(draw_order=draw_order, path=path, name=name))
        self.history.sort(key=lambda x: x.draw_order)

        stop_seconds = data.get("stop_seconds", 3.0)
        no_repeat = data.get("no_repeat", True)
        try:
            self.stop_seconds_spin.setValue(float(stop_seconds))
        except (TypeError, ValueError):
            self.stop_seconds_spin.setValue(3.0)
        self.no_repeat_checkbox.setChecked(bool(no_repeat))

        if self.images:
            self.viewer.set_image(self.images[0].path)
            self.name_label.setText(f"表示名: {self.images[0].name}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_state()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
