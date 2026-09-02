"""
PyQt 主視窗與 UI 互動邏輯
修正 QCalendarWidget 禁用日期（disabled date）視覺無區隔，
並徹底解決背景線程傳回的 apply_info 日期被重置為當天的問題。
"""

import logging
import re
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QDate, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import HTML_CONTENT, LOC_COORDINATES, WEEKDAYS_ZH
from gis import get_coordinates_by_address
from map_bridge import MapBridge
from models import DynamicApplyInfo
from threads import AsyncProcessThread, SeleniumThread

logger = logging.getLogger(__name__)


class DragDropLabel(QLabel):
    """自訂支援拖曳檔案進來的 QLabel 元件"""

    file_dropped = pyqtSignal(str)

    def __init__(self, text=""):
        super().__init__(text)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            self.file_dropped.emit(file_path)


class GeocodeThread(QThread):
    """專門處理手動輸入地址轉經緯度的背景線程"""

    finished_signal = pyqtSignal(float, float)
    error_signal = pyqtSignal(str)

    def __init__(self, address: str):
        super().__init__()
        self.address = address

    def run(self):
        try:
            lat, lng = get_coordinates_by_address(self.address)
            self.finished_signal.emit(lat, lng)
        except Exception as e:
            self.error_signal.emit(str(e))


MSG_BOX_STYLE = """
QMessageBox {
    font-size: 18px;
    font-family: "Microsoft JhengHei", "微軟正黑體", sans-serif;
}
QMessageBox QLabel {
    font-size: 18px;
    color: #212529;
}
QMessageBox QPushButton {
    font-size: 16px;
    font-weight: bold;
    min-width: 100px;
    min-height: 40px;
    padding: 5px 15px;
    border-radius: 6px;
    background-color: #0d6efd;
    color: white;
}
QMessageBox QPushButton:hover {
    background-color: #0b5ed7;
}
"""


def show_elder_warning(parent: QWidget, title: str, text: str):
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setStyleSheet(MSG_BOX_STYLE)
    ok_btn = msg.addButton("確定", QMessageBox.ButtonRole.AcceptRole)
    msg.setDefaultButton(ok_btn)
    msg.exec()


def show_elder_critical(parent: QWidget, title: str, text: str):
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Critical)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setStyleSheet(MSG_BOX_STYLE)
    ok_btn = msg.addButton("確定", QMessageBox.ButtonRole.AcceptRole)
    msg.setDefaultButton(ok_btn)
    msg.exec()


def show_elder_question(parent: QWidget, title: str, text: str) -> bool:
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setStyleSheet(MSG_BOX_STYLE)

    no_btn = msg.addButton("取消", QMessageBox.ButtonRole.NoRole)
    no_btn.setStyleSheet(
        """
        QPushButton {
            color: #6c757d;
            background-color: transparent;
            border: 1px solid #6c757d;
        }
        QPushButton:hover {
            color: #ffffff;
            background-color: #5c636a;
            border: 1px solid #5c636a;
        }
        QPushButton:pressed {
            background-color: #495057;
            border: 1px solid #495057;
        }
    """
    )
    yes_btn = msg.addButton("確定", QMessageBox.ButtonRole.YesRole)
    msg.setDefaultButton(yes_btn)
    msg.exec()
    return msg.clickedButton() == yes_btn


def format_hour_item(hour: int) -> str:
    if 0 <= hour <= 5:
        period = "凌晨"
    elif 6 <= hour <= 11:
        period = "早上"
    elif hour == 12:
        period = "中午"
    elif 13 <= hour <= 16:
        period = "下午"
    else:
        period = "晚上"

    return f"{period} {hour:02d} 點"


class IntegratedApp(QtWidgets.QMainWindow):
    def __init__(self, version):
        super().__init__()
        self.setWindowTitle(f"新北市路權申請系統 {version}")
        self.resize(1200, 800)

        self.selected_date = None
        self.last_selected_qdate = None
        self.image_path = ""
        self.is_updating_address = False

        self.geocode_thread = None
        self.parsed_apply_info = None
        self.is_parsing_address = False

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        root_layout = QHBoxLayout(main_widget)

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)

        # 頂部進度列與重置按鈕容器
        top_header_layout = QHBoxLayout()
        self.lbl_progress = QLabel("步驟 1 / 5：輸入施工地址")
        self.lbl_progress.setFont(QFont("Microsoft JhengHei", 16, QFont.Weight.Bold))
        self.lbl_progress.setStyleSheet("color: #0d6efd; background-color: #e7f1ff; padding: 10px; border-radius: 6px;")

        self.btn_reset = QPushButton("🔄 重新填寫")
        self.btn_reset.setFont(QFont("Microsoft JhengHei", 12))
        self.btn_reset.setToolTip("放棄目前的輸入並重新開始")
        self.btn_reset.setStyleSheet(
            """
            QPushButton {
                background-color: #f8f9fa;
                color: #6c757d;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 10px 14px;
            }
            QPushButton:hover {
                background-color: #e2e6ea;
                color: #495057;
                border-color: #adb5bd;
            }
        """
        )
        self.btn_reset.clicked.connect(self.reset_form_completely)

        top_header_layout.addWidget(self.lbl_progress, stretch=1)
        top_header_layout.addWidget(self.btn_reset)
        left_layout.addLayout(top_header_layout)

        self.stacked_widget = QStackedWidget()

        # 卡片 1: 施工地址
        page1 = QWidget()
        p1_layout = QVBoxLayout(page1)
        lbl_p1 = QLabel("請輸入施工地址 (或點擊右側地圖選擇)：")
        lbl_p1.setFont(QFont("Microsoft JhengHei", 13))

        addr_container = QWidget()
        addr_layout = QHBoxLayout(addr_container)
        addr_layout.setContentsMargins(0, 0, 0, 0)
        addr_layout.setSpacing(8)

        self.addr_entry = QLineEdit("")
        self.addr_entry.setPlaceholderText("例如：新北市板橋區中山路一段1號")
        self.addr_entry.setFont(QFont("Microsoft JhengHei", 14))

        self.loading_label = QLabel("⏳ 載入中...")
        self.loading_label.setFont(QFont("Microsoft JhengHei", 12, QFont.Weight.Bold))
        self.loading_label.setStyleSheet(
            "color: #0d6efd; background-color: #e7f1ff; border: 1px solid #0d6efd; border-radius: 4px; padding: 4px 8px;"
        )
        self.loading_label.hide()

        addr_layout.addWidget(self.addr_entry, stretch=1)
        addr_layout.addWidget(self.loading_label)

        self.addr_entry.textChanged.connect(self.on_address_input_changed)
        self.addr_entry.editingFinished.connect(self.on_address_changed)

        p1_layout.addWidget(lbl_p1)
        p1_layout.addWidget(addr_container)
        p1_layout.addStretch()
        self.stacked_widget.addWidget(page1)

        # 卡片 2: 施工日期
        page2 = QWidget()
        p2_layout = QVBoxLayout(page2)
        lbl_p2 = QLabel("請在下方日曆選擇施工日期（前 8 天不可選擇）：")
        lbl_p2.setFont(QFont("Microsoft JhengHei", 13))

        self.calendar = QCalendarWidget()
        min_date = QDate.currentDate().addDays(9)
        self.calendar.setMinimumDate(min_date)
        self.calendar.setFont(QFont("Microsoft JhengHei", 11))

        self.calendar.setStyleSheet(
            """
            QCalendarWidget {
                background-color: #ffffff;
                border: 2px solid #0d6efd;
                border-radius: 8px;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar {
                background-color: #0b3d91;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                min-height: 46px;
            }
            QCalendarWidget QToolButton {
                color: #ffc107;
                font-size: 16px;
                font-weight: bold;
                background-color: transparent;
                border: none;
                margin: 4px;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QCalendarWidget QToolButton:hover {
                background-color: #1d4ed8;
                color: #ffffff;
            }
            QCalendarWidget QToolButton::menu-indicator {
                image: none;
            }
            QCalendarWidget QWidget#qt_calendar_calendarview QHeaderView::section {
                background-color: #e7f1ff;
                color: #0a58ca;
                font-weight: bold;
                font-size: 14px;
                padding: 6px;
                border: none;
                border-bottom: 2px solid #b6d4fe;
            }
            QCalendarWidget QAbstractItemView:enabled {
                background-color: #ffffff;
                color: #212529;
                font-size: 15px;
                font-weight: bold;
            }
            """
        )

        self.apply_disabled_dates_format(min_date)
        self.calendar.clicked.connect(self.on_date_selected)
        self.calendar.currentPageChanged.connect(lambda: self.apply_disabled_dates_format(min_date))

        self.lbl_selected_date_info = QLabel("尚未選擇日期")
        self.lbl_selected_date_info.setFont(QFont("Microsoft JhengHei", 13, QFont.Weight.Bold))
        self.lbl_selected_date_info.setStyleSheet("color: #6c757d;")
        p2_layout.addWidget(lbl_p2)
        p2_layout.addWidget(self.calendar)
        p2_layout.addWidget(self.lbl_selected_date_info)
        p2_layout.addStretch()
        self.stacked_widget.addWidget(page2)

        # 卡片 3: 開始時間
        page3 = QWidget()
        p3_layout = QVBoxLayout(page3)
        lbl_p3 = QLabel("請選擇施工開始時間：")
        lbl_p3.setFont(QFont("Microsoft JhengHei", 13))
        time_layout = QHBoxLayout()
        self.hour_cb = QComboBox()
        self.hour_cb.setFont(QFont("Microsoft JhengHei", 13))
        hour_options = [format_hour_item(i) for i in range(24)]
        self.hour_cb.addItems(["-- 請選擇開始時間 --"] + hour_options)

        self.minute_cb = QComboBox()
        self.minute_cb.setFont(QFont("Microsoft JhengHei", 13))
        self.minute_cb.addItems(["00 分", "30 分"])

        time_layout.addWidget(self.hour_cb)
        time_layout.addWidget(self.minute_cb)
        p3_layout.addWidget(lbl_p3)
        p3_layout.addLayout(time_layout)
        p3_layout.addStretch()
        self.stacked_widget.addWidget(page3)

        # 卡片 4: 施工時長（改為動態帶入結束時間選項）
        page4 = QWidget()
        p4_layout = QVBoxLayout(page4)
        lbl_p4 = QLabel("請選擇施工時長：")
        lbl_p4.setFont(QFont("Microsoft JhengHei", 13))
        self.duration_cb = QComboBox()
        self.duration_cb.setFont(QFont("Microsoft JhengHei", 13))
        self.duration_cb.addItem("-- 請選擇施工時長 --")
        p4_layout.addWidget(lbl_p4)
        p4_layout.addWidget(self.duration_cb)
        p4_layout.addStretch()
        self.stacked_widget.addWidget(page4)

        # 卡片 5: 路權圖檔與確認送出（已整合點擊按鈕與拖曳檔案功能）
        page5 = QWidget()
        p5_layout = QVBoxLayout(page5)
        lbl_p5 = QLabel("請上傳路權圖檔並確認申請內容：")
        lbl_p5.setFont(QFont("Microsoft JhengHei", 13))

        img_layout = QHBoxLayout()
        self.btn_img = QPushButton("📁 選擇圖檔")
        self.btn_img.setFont(QFont("Microsoft JhengHei", 12))
        self.btn_img.clicked.connect(self.choose_image)

        self.img_label = DragDropLabel("📁 點擊左側按鈕 或 將圖檔拖曳至此")
        self.img_label.setFont(QFont("Microsoft JhengHei", 12))
        self.img_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #0d6efd;
                border-radius: 6px;
                background-color: #f8f9fa;
                color: #6c757d;
                padding: 10px;
            }
            QLabel:hover {
                background-color: #e9ecef;
                color: #495057;
            }
        """
        )
        self.img_label.file_dropped.connect(self.handle_dropped_image)

        img_layout.addWidget(self.btn_img)
        img_layout.addWidget(self.img_label, stretch=1)

        self.summary_label = QLabel()
        self.summary_label.setFont(QFont("Microsoft JhengHei", 11))
        self.summary_label.setStyleSheet(
            "background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px; border-radius: 6px;"
        )

        p5_layout.addWidget(lbl_p5)
        p5_layout.addLayout(img_layout)
        p5_layout.addWidget(self.summary_label)
        p5_layout.addStretch()
        self.stacked_widget.addWidget(page5)

        left_layout.addWidget(self.stacked_widget, stretch=1)

        # 底部導覽按鈕
        nav_layout = QHBoxLayout()
        self.btn_prev = QPushButton("⬅️ 上一步")
        self.btn_prev.setFont(QFont("Microsoft JhengHei", 13))
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(self.go_prev)

        self.btn_next = QPushButton("下一步 ➡️")
        self.btn_next.setFont(QFont("Microsoft JhengHei", 13, QFont.Weight.Bold))
        self.btn_next.setStyleSheet("background-color: #0d6efd; color: white; padding: 8px;")
        self.btn_next.clicked.connect(self.go_next)

        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.btn_next)
        left_layout.addLayout(nav_layout)

        root_layout.addWidget(left_container, stretch=4)

        # ------------------- 右側：MapBridge + Leaflet 地圖 -------------------
        map_container = QWidget()
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(5, 5, 5, 5)

        combo_layout = QHBoxLayout()
        lbl_jump = QLabel("快速跳轉標點：")
        lbl_jump.setFont(QFont("Microsoft JhengHei", 11))
        combo_layout.addWidget(lbl_jump)
        self.comboBox_campus = QComboBox()
        self.comboBox_campus.setFont(QFont("Microsoft JhengHei", 11))
        self.comboBox_campus.addItems(list(LOC_COORDINATES.keys()))
        self.comboBox_campus.currentIndexChanged.connect(self.which_campus)
        combo_layout.addWidget(self.comboBox_campus)
        # map_layout.addLayout(combo_layout)

        self.web_view = QWebEngineView()
        self.bridge = MapBridge()

        self.bridge.request_started.connect(self.show_loading)
        self.bridge.address_found.connect(self.update_address_ui)

        self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        self.web_view.setHtml(HTML_CONTENT)

        map_layout.addWidget(self.web_view, stretch=1)
        root_layout.addWidget(map_container, stretch=6)

    def apply_disabled_dates_format(self, min_date: QDate):
        """強行替最小可選擇日期（前 8 天）之前的全部日期著色為灰底灰字"""
        disabled_fmt = QTextCharFormat()
        disabled_fmt.setBackground(QColor("#e9ecef"))
        disabled_fmt.setForeground(QColor("#adb5bd"))

        curr = min_date.addDays(-1)
        start_bound = min_date.addDays(-60)

        while curr >= start_bound:
            self.calendar.setDateTextFormat(curr, disabled_fmt)
            curr = curr.addDays(-1)

    def show_loading(self):
        self.loading_label.show()
        QApplication.processEvents()

    def hide_loading(self):
        self.loading_label.hide()
        QApplication.processEvents()

    def on_address_changed(self):
        if self.is_updating_address:
            return

        address = self.addr_entry.text().strip()
        if not address:
            return

        self.show_loading()

        self.geocode_thread = GeocodeThread(address)
        self.geocode_thread.finished_signal.connect(self._on_manual_geocode_finished)
        self.geocode_thread.error_signal.connect(self._on_manual_geocode_error)
        self.geocode_thread.start()

    def _on_manual_geocode_finished(self, lat: float, lng: float):
        self.web_view.page().runJavaScript(f"setMapCenter({lat}, {lng});")
        self.hide_loading()

    def _on_manual_geocode_error(self, err_msg: str):
        logger.warning(f"無法查詢地址座標: {err_msg}")
        self.hide_loading()

    def start_background_address_parse(self):
        address = self.addr_entry.text().strip()
        if not address or "新北市" not in address:
            return

        self.is_parsing_address = True
        self.parsed_apply_info = None

        dummy_dt = datetime.now()
        dummy_duration = timedelta(hours=1)

        self.async_thread = AsyncProcessThread(address, dummy_dt, dummy_duration, "")
        self.async_thread.finished_signal.connect(self.on_bg_info_processed)
        self.async_thread.error_signal.connect(self.on_bg_info_error)
        self.async_thread.start()

    def on_bg_info_processed(self, apply_info: DynamicApplyInfo):
        self.parsed_apply_info = apply_info
        self.is_parsing_address = False
        self.hide_loading()
        logger.info(f"背景解析里長成功: {apply_info.town} {apply_info.village} 里長: {apply_info.chief_name}")
        if self.stacked_widget.currentIndex() == 4:
            self.update_summary()

    def on_bg_info_error(self, msg: str):
        self.is_parsing_address = False
        self.hide_loading()
        logger.warning(f"背景解析地址失敗: {msg}")

    def update_progress_header(self):
        idx = self.stacked_widget.currentIndex()
        steps = [
            "步驟 1 / 5：輸入施工地址",
            "步驟 2 / 5：選擇施工日期",
            "步驟 3 / 5：選擇開始時間",
            "步驟 4 / 5：選擇施工時長",
            "步驟 5 / 5：上傳圖檔與確認送出",
        ]
        self.lbl_progress.setText(steps[idx])
        self.btn_prev.setEnabled(idx > 0)

        if idx > 0:
            self.comboBox_campus.setEnabled(False)
            self.web_view.page().runJavaScript("if (typeof setMapClickable === 'function') { setMapClickable(false); }")
        else:
            self.comboBox_campus.setEnabled(True)
            self.web_view.page().runJavaScript("if (typeof setMapClickable === 'function') { setMapClickable(true); }")

        if idx == 4:
            self.btn_next.setText("🚀 送出自動填表")
            self.btn_next.setStyleSheet("background-color: #198754; color: white; padding: 8px; font-weight: bold;")
            self.update_summary()
        else:
            self.btn_next.setText("下一步 ➡️")
            self.btn_next.setStyleSheet("background-color: #0d6efd; color: white; padding: 8px; font-weight: bold;")

    def update_duration_options(self):
        """根據已選擇的開始時間，動態計算並更新施工時長選項，後方顯示 (結束: mm-dd H:M)"""
        self.duration_cb.clear()
        self.duration_cb.addItem("-- 請選擇施工時長 --")

        if not self.selected_date:
            return

        hour_match = re.search(r"(\d+)", self.hour_cb.currentText())
        if not hour_match:
            return

        start_hour = int(hour_match.group(1))
        minute = int(self.minute_cb.currentText().replace(" 分", ""))

        start_dt = datetime(
            self.selected_date.year,
            self.selected_date.month,
            self.selected_date.day,
            start_hour,
            minute,
        )

        for i in range(1, 49):
            end_dt = start_dt + timedelta(hours=i)
            delta_days = (end_dt.date() - start_dt.date()).days
            if delta_days >= 2:
                break

            end_str = end_dt.strftime("%m-%d %H:%M")
            self.duration_cb.addItem(f"{i} 小時 【結束: {end_str}】")

    def validate_current_step(self) -> bool:
        idx = self.stacked_widget.currentIndex()
        if idx == 0:
            addr = self.addr_entry.text().strip()
            if not addr or "新北市" not in addr:
                show_elder_warning(self, "提醒", "請輸入包含『新北市』的完整地址！")
                return False
        elif idx == 1:
            if not self.selected_date:
                show_elder_warning(self, "提醒", "請在日曆上點選施工日期！")
                return False
        elif idx == 2:
            if self.hour_cb.currentIndex() == 0:
                show_elder_warning(self, "提醒", "請選擇開始時間！")
                return False
        elif idx == 3:
            if self.duration_cb.currentIndex() == 0:
                show_elder_warning(self, "提醒", "請選擇施工時長！")
                return False

            hour_match = re.search(r"(\d+)", self.hour_cb.currentText())
            duration_match = re.match(r"^(\d+)", self.duration_cb.currentText())

            if hour_match and duration_match:
                start_hour = int(hour_match.group(1))
                duration_hours = int(duration_match.group(1))
                minute = int(self.minute_cb.currentText().replace(" 分", ""))

                start_dt = datetime(
                    self.selected_date.year,
                    self.selected_date.month,
                    self.selected_date.day,
                    start_hour,
                    minute,
                )
                end_dt = start_dt + timedelta(hours=duration_hours)
                delta_days = (end_dt.date() - start_dt.date()).days
                if delta_days >= 2:
                    show_elder_warning(
                        self,
                        "提醒",
                        "施工時長不可跨到第三日，請縮短施工時長或提早開始時間！",
                    )
                    return False

        elif idx == 4:
            if not self.image_path:
                show_elder_warning(self, "提醒", "請上傳路權圖檔！")
                return False
        return True

    def go_next(self):
        if not self.validate_current_step():
            return

        idx = self.stacked_widget.currentIndex()

        if idx == 0:
            if self.parsed_apply_info is None and not self.is_parsing_address:
                self.start_background_address_parse()

        # 當準備從開始時間頁面 (idx == 2) 前往施工時長頁面 (idx == 3) 時，動態更新時長選單
        if idx == 2:
            self.update_duration_options()

        if idx < 4:
            self.stacked_widget.setCurrentIndex(idx + 1)
            self.update_progress_header()
        else:
            self.submit()

    def go_prev(self):
        idx = self.stacked_widget.currentIndex()
        if idx > 0:
            self.stacked_widget.setCurrentIndex(idx - 1)
            self.update_progress_header()

    def reset_form_completely(self, ask=True):
        """一鍵清空所有填寫內容並返回步驟 1"""
        if ask and not show_elder_question(self, "重新填寫確認", "確定要放棄目前的輸入並重新開始嗎？"):
            return

        self.selected_date = None
        self.last_selected_qdate = None
        self.image_path = ""
        self.parsed_apply_info = None
        self.is_parsing_address = False

        self.addr_entry.clear()
        self.calendar.setSelectedDate(QDate.currentDate())

        min_date = self.calendar.minimumDate()
        self.apply_disabled_dates_format(min_date)

        self.lbl_selected_date_info.setText("尚未選擇日期")
        self.lbl_selected_date_info.setStyleSheet("color: #6c757d;")

        self.hour_cb.setCurrentIndex(0)
        self.minute_cb.setCurrentIndex(0)
        self.duration_cb.clear()
        self.duration_cb.addItem("-- 請選擇施工時長 --")

        self.img_label.setText("📁 點擊左側按鈕 或 將圖檔拖曳至此")
        self.img_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #0d6efd;
                border-radius: 6px;
                background-color: #f8f9fa;
                color: #6c757d;
                padding: 10px;
            }
        """
        )
        self.summary_label.clear()

        self.stacked_widget.setCurrentIndex(0)
        self.update_progress_header()

        self.comboBox_campus.setEnabled(True)
        self.web_view.page().runJavaScript("if (typeof setMapClickable === 'function') { setMapClickable(true); }")

        logger.info("使用者已重置表單，回到步驟 1。")

    def get_selected_datetime_range(self):
        if not self.selected_date:
            return None, None

        hour_match = re.search(r"(\d+)", self.hour_cb.currentText())
        duration_match = re.match(r"^(\d+)", self.duration_cb.currentText())

        if not hour_match or not duration_match:
            return None, None

        hour = int(hour_match.group(1))
        minute = int(self.minute_cb.currentText().replace(" 分", ""))
        duration_hours = int(duration_match.group(1))

        start_dt = datetime(
            self.selected_date.year,
            self.selected_date.month,
            self.selected_date.day,
            hour,
            minute,
        )
        end_dt = start_dt + timedelta(hours=duration_hours)
        return start_dt, end_dt

    def update_summary(self):
        w_name = WEEKDAYS_ZH[self.selected_date.weekday()] if self.selected_date else ""
        date_str = f"{self.selected_date.strftime('%Y-%m-%d')} ({w_name})" if self.selected_date else ""
        file_name = Path(self.image_path).name if self.image_path else "未選擇"

        chief_info_str = (
            "查詢中..."
            if self.is_parsing_address
            else (
                f"{self.parsed_apply_info.town} ({self.parsed_apply_info.village}) - 里長: {self.parsed_apply_info.chief_name}"
                if self.parsed_apply_info
                else "尚未取得資訊"
            )
        )

        summary_text = (
            f"<b>📍 施工地址：</b> {self.addr_entry.text().strip()}<br>"
            f"<b>🏛️ 行政區/里長：</b> {chief_info_str}<br>"
            f"<b>📅 施工日期：</b> {date_str}<br>"
            f"<b>⏰ 開始時間：</b> {self.hour_cb.currentText()} {self.minute_cb.currentText()}<br>"
            f"<b>⏳ 施工時長：</b> {self.duration_cb.currentText()}<br>"
            f"<b>📁 圖檔名稱：</b> {file_name}"
        )
        self.summary_label.setText(summary_text)

    def on_address_input_changed(self):
        self.parsed_apply_info = None

    def update_address_ui(self, address: str, lat: float, lng: float):
        if self.stacked_widget.currentIndex() != 0:
            self.hide_loading()
            return

        self.is_updating_address = True
        self.addr_entry.setText(address)
        self.parsed_apply_info = None
        self.is_updating_address = False

        self.hide_loading()

    def which_campus(self):
        if self.stacked_widget.currentIndex() != 0:
            return

        loc = self.comboBox_campus.currentText()
        if loc in LOC_COORDINATES:
            lat, lng = LOC_COORDINATES[loc]
            self.web_view.page().runJavaScript(f"setMapCenter({lat}, {lng});")
            self.bridge.on_map_clicked(lat, lng)

    def on_date_selected(self, qdate: QDate):
        min_date = self.calendar.minimumDate()
        if qdate < min_date:
            return

        if self.last_selected_qdate and self.last_selected_qdate >= min_date:
            self.calendar.setDateTextFormat(self.last_selected_qdate, QTextCharFormat())

        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#0d6efd"))
        fmt.setForeground(QColor("#ffffff"))
        fmt.setFontWeight(QFont.Weight.Bold)

        self.calendar.setDateTextFormat(qdate, fmt)
        self.last_selected_qdate = qdate

        self.selected_date = date(qdate.year(), qdate.month(), qdate.day())
        w_name = WEEKDAYS_ZH[self.selected_date.weekday()]

        self.lbl_selected_date_info.setText(f"✅ 已選擇施工日期：{self.selected_date.strftime('%Y-%m-%d')} ({w_name})")
        self.lbl_selected_date_info.setStyleSheet(
            "color: #198754; background-color: #d1e7dd; border: 1px solid #198754; "
            "padding: 8px 12px; border-radius: 6px; font-size: 14px; font-weight: bold;"
        )

    def handle_dropped_image(self, path: str):
        """處理透過拖曳進來的檔案"""
        if path.lower().endswith((".jpg", ".png", ".pdf", ".jpeg")):
            self.image_path = path
            self.img_label.setText(f"已上傳: {Path(path).name}")
            self.img_label.setStyleSheet(
                """
                QLabel {
                    border: 2px solid #198754;
                    border-radius: 6px;
                    background-color: #d1e7dd;
                    color: #155724;
                    font-weight: bold;
                    padding: 10px;
                }
            """
            )
            self.update_summary()
        else:
            show_elder_warning(self, "格式錯誤", "請上傳格式為 .jpg, .png 或 .pdf 的圖檔！")

    def choose_image(self):
        desktop_path = str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(self, "選擇圖檔", desktop_path, "圖片 (*.jpg *.png *.pdf)")
        if path:
            self.image_path = path
            self.img_label.setText(f"已上傳: {Path(path).name}")
            self.img_label.setStyleSheet(
                """
                QLabel {
                    border: 2px solid #198754;
                    border-radius: 6px;
                    background-color: #d1e7dd;
                    color: #155724;
                    font-weight: bold;
                    padding: 10px;
                }
            """
            )
            self.update_summary()

    def submit(self):
        start_dt, end_dt = self.get_selected_datetime_range()
        if not start_dt or not end_dt:
            show_elder_warning(self, "錯誤", "時間或時長格式不正確！")
            return

        duration = end_dt - start_dt

        if self.parsed_apply_info:
            self.on_info_processed(self.parsed_apply_info)
        else:
            self.btn_next.setEnabled(False)
            self.btn_next.setText("查詢資料中...")
            address = self.addr_entry.text().strip()

            self.async_thread = AsyncProcessThread(address, start_dt, duration, self.image_path)
            self.async_thread.finished_signal.connect(self.on_info_processed)
            self.async_thread.error_signal.connect(self.on_error)
            self.async_thread.start()

    def on_info_processed(self, apply_info: DynamicApplyInfo):
        start_dt, end_dt = self.get_selected_datetime_range()
        if start_dt and end_dt:
            apply_info.start_datetime = start_dt
            apply_info.start_time = start_dt
            apply_info.end_time = end_dt
            apply_info.duration = end_dt - start_dt

        if self.image_path:
            apply_info.image_path = self.image_path

        confirm = show_elder_question(
            self,
            "資料確認送出",
            textwrap.dedent(
                f"""
                解析成功！

                行政區：{apply_info.town} ({apply_info.village})
                里長：{apply_info.chief_name}
                開始時間：{apply_info.start_datetime.strftime('%Y-%m-%d %H:%M')}
                截止時間：{apply_info.end_time.strftime('%Y-%m-%d %H:%M')}

                請問是否要開啟自動填表？
                """
            ).strip(),
        )
        if confirm:
            self.selenium_thread = SeleniumThread(apply_info)
            self.selenium_thread.error_signal.connect(self.on_error)
            self.selenium_thread.success_signal.connect(self.on_submission_success)
            self.selenium_thread.finished.connect(self.reset_btn)
            self.selenium_thread.start()
        else:
            self.reset_btn()

    def on_error(self, msg: str):
        show_elder_critical(self, "發生錯誤", msg)
        self.reset_btn()

    def reset_btn(self):
        self.btn_next.setEnabled(True)
        self.update_progress_header()

    def on_submission_success(self):
        show_elder_warning(self, "申辦完成", "表單已成功送達並完成日誌記錄！瀏覽器已自動關閉。")
        self.reset_form_completely(ask=False)
