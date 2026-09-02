"""
PyQt QThread 工作執行緒，負責耗時工作與 GUI 解耦
"""

import logging
from datetime import datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal

from bot import AutoFillForm
from gis import _load_village_chief_dataframe
from models import DynamicApplyInfo

logger = logging.getLogger(__name__)


class PreloadChiefDataThread(QThread):
    """背景預載里長 CSV 資料的執行緒"""

    finished_signal = pyqtSignal()

    def run(self):
        try:
            # 呼叫你原本帶有 @lru_cache 的函式進行快取載入
            _load_village_chief_dataframe()
            logger.info("背景預載里長 CSV 資料成功！")
        except Exception as e:
            logger.warning(f"背景預載里長 CSV 資料失敗: {e}")
        finally:
            self.finished_signal.emit()


class AsyncProcessThread(QThread):
    finished_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, address: str, start_dt: datetime, duration: timedelta, img_path: str):
        super().__init__()
        self.address = address
        self.start_dt = start_dt
        self.duration = duration
        self.img_path = img_path

    def run(self):
        try:
            info = DynamicApplyInfo(
                address=self.address,
                start_datetime=self.start_dt,
                duration=self.duration,
                road_right_image=self.img_path,
            )
            self.finished_signal.emit(info)
        except Exception as e:
            self.error_signal.emit(str(e))


class SeleniumThread(QThread):
    success_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, apply_info: DynamicApplyInfo):
        super().__init__()
        self.apply_info = apply_info

    def run(self):
        try:
            bot = AutoFillForm()
            # 當 auto_fill_form 執行完畢且完成 Log 記錄、瀏覽器關閉後會返回 True
            if bot.auto_fill_form(self.apply_info):
                self.success_signal.emit()
        except Exception as e:
            self.error_signal.emit(str(e))
