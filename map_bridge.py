"""
PyQt WebChannel 通訊橋樑：處理 JavaScript 地圖點擊與逆向地理編碼 (非同步背景 Thread 版)
"""

import logging

import requests
from PyQt6.QtCore import QObject, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices

logger = logging.getLogger(__name__)


class ReverseGeocodeThread(QThread):
    """專門處理 Nominatim API 逆向地理編碼的背景線程"""

    finished_signal = pyqtSignal(str, float, float)

    def __init__(self, lat: float, lng: float):
        super().__init__()
        self.lat = lat
        self.lng = lng

    def run(self):
        url = f"https://nominatim.openstreetmap.org/reverse?lat={self.lat}&lon={self.lng}&format=json&accept-language=zh-TW"
        headers = {"User-Agent": "PyQt6_Map_App/1.0"}

        try:
            res = requests.get(url, headers=headers, timeout=5, verify=False)
            if res.status_code == 200:
                data = res.json()
                address = data.get("display_name", "查無門牌地址")
                village = data.get("address", {}).get("village")
                if village:
                    address = address.replace(f" {village},", "")
                address = "".join([i.strip() for i in address.split(",")[::-1]]).removeprefix("臺灣")
            else:
                address = "查詢失敗"
        except Exception as e:
            logger.error(f"逆向地理編碼網路請求錯誤: {e}")
            address = ""

        self.finished_signal.emit(address, self.lat, self.lng)


class MapBridge(QObject):
    address_found = pyqtSignal(str, float, float)
    request_started = pyqtSignal()  # 點擊瞬間發送給 UI 顯示 Loading 的訊號

    def __init__(self):
        super().__init__()
        self._geo_thread = None

    @pyqtSlot(float, float)
    def on_map_clicked(self, lat: float, lng: float):
        # 1. 瞬間觸發 UI 顯示 Loading 動畫
        self.request_started.emit()

        # 2. 開啟背景 Thread 執行 HTTP 請求，不卡住 UI
        self._geo_thread = ReverseGeocodeThread(lat, lng)
        self._geo_thread.finished_signal.connect(self._on_geocode_finished)
        self._geo_thread.start()

    def _on_geocode_finished(self, address: str, lat: float, lng: float):
        # 3. 查詢完成，將結果通知 UI
        self.address_found.emit(address, lat, lng)

    @pyqtSlot(float, float)
    def open_street_view(self, lat: float, lng: float):
        """接收前端傳來的經緯度，開啟 Google 街景"""
        url = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lng}"
        QDesktopServices.openUrl(QUrl(url))
