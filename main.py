"""网易云音乐 NCM 格式还原工具 — 图形界面。"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

# 便携 Python 通过 ._pth 限制 sys.path，需手动加入脚本目录
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    Theme,
    setTheme,
    setThemeColor,
)

from ncm_core import NcmError, decrypt_ncm

APP_TITLE = "NCM 格式还原"
APP_VERSION = "1.0.0"
APP_ID = "ncm.format.restore.desktop"
APP_DIR = Path(__file__).resolve().parent


def _app_dir() -> Path:
    """开发目录或 PyInstaller 解压目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return APP_DIR


def _icon_path() -> Path | None:
    base = _app_dir()
    for name in ("ncm_convert.ico", "app.ico"):
        p = base / name
        if p.is_file():
            return p
        # 打包时可能放在根目录旁
        p2 = Path(sys.executable).parent / name
        if p2.is_file():
            return p2
    return None


ICON_PATH = _icon_path()


def _ensure_taskbar_icon() -> None:
    """Windows 任务栏用 AppUserModelID 关联 exe 图标。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass


def _make_icon() -> QIcon | None:
    if ICON_PATH and ICON_PATH.is_file():
        icon = QIcon(str(ICON_PATH))
        if not icon.isNull():
            return icon
    # 退回：从当前 exe 读图标
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            icon = QIcon(sys.executable)
            if not icon.isNull():
                return icon
        except Exception:  # noqa: BLE001
            pass
    return None


@dataclass
class JobItem:
    path: Path
    status: str = "待处理"
    message: str = ""
    out_path: Path | None = None
    format: str = ""
    title: str = ""
    artist: str = ""


class ConvertWorker(QThread):
    progress = Signal(int, int, str)
    item_done = Signal(int, object)
    finished_all = Signal(int, int)

    def __init__(
        self,
        jobs: list[JobItem],
        out_dir: Path | None,
        auto_rename: bool = True,
        delete_source: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.jobs = jobs
        self.out_dir = out_dir
        self.auto_rename = auto_rename
        self.delete_source = delete_source
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        total = len(self.jobs)
        ok = 0
        fail = 0
        for i, job in enumerate(self.jobs):
            if self._cancelled:
                job.status = "已取消"
                self.item_done.emit(i, job)
                break
            self.progress.emit(i + 1, total, job.path.name)
            try:
                result = decrypt_ncm(job.path)
                job.format = result.format
                job.title = result.title or job.path.stem
                job.artist = result.artist
                out_dir = self.out_dir or job.path.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / result.suggest_name(job.path.stem)
                if self.auto_rename and out_path.exists() and out_path.resolve() != job.path.resolve():
                    stem = out_path.stem
                    for n in range(1, 1000):
                        candidate = out_dir / f"{stem}_{n}.{result.format}"
                        if not candidate.exists():
                            out_path = candidate
                            break
                out_path.write_bytes(result.audio)
                job.out_path = out_path
                job.status = "成功"
                job.message = out_path.name
                if self.delete_source:
                    try:
                        job.path.unlink(missing_ok=True)
                        job.message = f"{out_path.name}（已删除 .ncm）"
                    except OSError as exc:
                        job.message = f"{out_path.name}（删除源文件失败: {exc}）"
                ok += 1
            except NcmError as exc:
                job.status = "失败"
                job.message = str(exc)
                fail += 1
            except Exception as exc:  # noqa: BLE001
                job.status = "失败"
                job.message = str(exc)
                fail += 1
                traceback.print_exc()
            self.item_done.emit(i, job)
        self.finished_all.emit(ok, fail)


class ConvertPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: list[JobItem] = []
        self.worker: ConvertWorker | None = None
        self.out_dir: Path | None = None
        self._index_map: list[int] = []
        self._setup_ui()
        self._bind()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = SubtitleLabel("NCM 格式还原")
        title.setFont(QFont(title.font().family(), 18, QFont.DemiBold))
        subtitle = CaptionLabel("把网易云 .ncm 还原为原本的 MP3 / FLAC 等格式")
        root.addWidget(title)
        root.addWidget(subtitle)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_add = PushButton("添加文件", icon=FluentIcon.ADD)
        self.btn_folder = PushButton("添加文件夹", icon=FluentIcon.FOLDER)
        self.btn_clear = PushButton("清空", icon=FluentIcon.DELETE)
        self.btn_convert = PrimaryPushButton("开始转换", icon=FluentIcon.PLAY)
        for w in (self.btn_add, self.btn_folder, self.btn_clear):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.btn_convert)
        root.addLayout(bar)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(BodyLabel("输出目录"))
        self.out_edit = LineEdit()
        self.out_edit.setPlaceholderText("留空则输出到各文件所在目录")
        self.out_edit.setClearButtonEnabled(True)
        self.btn_browse_out = PushButton("浏览…")
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.btn_browse_out)
        root.addLayout(out_row)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_row.addWidget(BodyLabel("同名自动重命名"))
        self.rename_switch = SwitchButton()
        self.rename_switch.setChecked(True)
        opt_row.addWidget(self.rename_switch)
        opt_row.addSpacing(20)
        opt_row.addWidget(BodyLabel("转换后删除 .ncm"))
        self.delete_switch = SwitchButton()
        self.delete_switch.setChecked(False)
        opt_row.addWidget(self.delete_switch)
        opt_row.addStretch(1)
        self.count_label = CaptionLabel("共 0 个文件")
        opt_row.addWidget(self.count_label)
        root.addLayout(opt_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件", "格式", "状态", "输出 / 说明"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, 1)

        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        self.status_label = CaptionLabel("就绪。可拖入 .ncm 文件，或点击上方按钮添加。")
        root.addWidget(self.status_label)

        self.setAcceptDrops(True)

    def _bind(self) -> None:
        self.btn_add.clicked.connect(self.pick_files)
        self.btn_folder.clicked.connect(self.pick_folder)
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_convert.clicked.connect(self.start_convert)
        self.btn_browse_out.clicked.connect(self.pick_out_dir)
        self.out_edit.textChanged.connect(self._on_out_changed)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths: list[Path] = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                paths.extend(sorted(p.rglob("*.ncm")))
            elif p.suffix.lower() == ".ncm":
                paths.append(p)
        self.add_paths(paths)

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择 NCM 文件", "", "网易云音乐 (*.ncm)")
        if files:
            self.add_paths([Path(f) for f in files])

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        paths = sorted(Path(folder).rglob("*.ncm"))
        if not paths:
            self._info("提示", "该文件夹下没有找到 .ncm 文件", warn=True)
            return
        self.add_paths(paths)

    def pick_out_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.out_edit.setText(folder)

    def clear_all(self) -> None:
        if self.worker and self.worker.isRunning():
            self._info("提示", "正在转换中，请稍后再清空", warn=True)
            return
        self.jobs.clear()
        self._refresh_table()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("已清空列表。")

    def add_paths(self, paths: list[Path]) -> None:
        existing = {j.path for j in self.jobs}
        added = 0
        for p in paths:
            try:
                rp = p.resolve()
            except OSError:
                rp = p
            if rp in existing:
                continue
            if rp.suffix.lower() != ".ncm":
                continue
            self.jobs.append(JobItem(path=rp))
            existing.add(rp)
            added += 1
        self._refresh_table()
        if added:
            self.status_label.setText(f"已添加 {added} 个文件，共 {len(self.jobs)} 个。")
        else:
            self.status_label.setText("没有新增文件（可能重复或不是 .ncm）。")

    def _on_out_changed(self, text: str) -> None:
        text = text.strip()
        self.out_dir = Path(text) if text else None

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self.jobs))
        for i, job in enumerate(self.jobs):
            self.table.setItem(i, 0, QTableWidgetItem(job.path.name))
            self.table.setItem(i, 1, QTableWidgetItem(job.format or "—"))
            self.table.setItem(i, 2, QTableWidgetItem(job.status))
            self.table.setItem(i, 3, QTableWidgetItem(job.message or str(job.path.parent)))
        self.count_label.setText(f"共 {len(self.jobs)} 个文件")

    def _set_row(self, idx: int, job: JobItem) -> None:
        if 0 <= idx < self.table.rowCount():
            self.table.setItem(idx, 1, QTableWidgetItem(job.format or "—"))
            self.table.setItem(idx, 2, QTableWidgetItem(job.status))
            self.table.setItem(idx, 3, QTableWidgetItem(job.message or ""))
            item = self.table.item(idx, 0)
            if item:
                self.table.scrollToItem(item)

    def start_convert(self) -> None:
        if self.worker and self.worker.isRunning():
            self._info("提示", "已有转换任务在进行", warn=True)
            return

        self._index_map = [
            i
            for i, j in enumerate(self.jobs)
            if j.status in {"待处理", "失败", "已取消"}
        ]
        if not self._index_map:
            self._info("提示", "没有待转换的文件", warn=True)
            return

        pending = [self.jobs[i] for i in self._index_map]
        for job in pending:
            job.status = "待处理"
            job.message = ""
        self._refresh_table()

        out_text = self.out_edit.text().strip()
        out_dir = Path(out_text) if out_text else None
        if out_dir is not None:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._info("错误", f"无法创建输出目录: {exc}", warn=True)
                return

        self.worker = ConvertWorker(
            pending,
            out_dir,
            auto_rename=self.rename_switch.isChecked(),
            delete_source=self.delete_switch.isChecked(),
            parent=self,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.finished_all.connect(self._on_finished)
        self.btn_convert.setEnabled(False)
        self.progress.setRange(0, len(pending))
        self.progress.setValue(0)
        self.status_label.setText("开始转换…")
        self.worker.start()

    def _on_progress(self, current: int, total: int, name: str) -> None:
        self.progress.setMaximum(total)
        self.status_label.setText(f"正在处理 {current}/{total}：{name}")

    def _on_item_done(self, idx: int, job: JobItem) -> None:
        real = self._index_map[idx] if 0 <= idx < len(self._index_map) else idx
        self._set_row(real, job)
        self.progress.setValue(min(idx + 1, self.progress.maximum()))

    def _on_finished(self, ok: int, fail: int) -> None:
        self.btn_convert.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        msg = f"完成：成功 {ok}，失败 {fail}"
        self.status_label.setText(msg)
        if fail == 0 and ok > 0:
            self._info("转换完成", msg)
        elif fail:
            self._info("部分失败", msg, warn=True)

    def _info(self, title: str, content: str, warn: bool = False) -> None:
        parent = self.window()
        if warn:
            InfoBar.warning(
                title,
                content,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=parent,
            )
        else:
            InfoBar.success(
                title,
                content,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=parent,
            )


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(960, 660)
        icon = _make_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.page = ConvertPage(self)
        layout.addWidget(self.page)


def main() -> int:
    _ensure_taskbar_icon()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("NCMTools")
    icon = _make_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    setTheme(Theme.AUTO)
    try:
        setThemeColor("#0078D4")  # Fluent 蓝
    except Exception:  # noqa: BLE001
        pass
    window = MainWindow()
    if icon is not None:
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
