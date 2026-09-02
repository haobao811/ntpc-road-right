"""
應用程式入口點
"""

import logging
import os
import sys

import pytesseract
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui import IntegratedApp

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 檢查是否安裝 Tesseract OCR
    try:
        default_install_dir = r"C:\Program Files\Tesseract-OCR"
        tesseract_exe = os.path.join(default_install_dir, "tesseract.exe")
        if os.path.isfile(tesseract_exe):
            pytesseract.pytesseract.tesseract_cmd = tesseract_exe

        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("未安裝 Tesseract OCR")
        msg.setText(
            "系統偵測尚未安裝 Tesseract OCR 或未將其加入環境變數。\n請先安裝後再重新啟動程式！"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
        sys.exit(1)

    app_font = QFont("Microsoft JhengHei", 16)
    app.setFont(app_font)

    window = IntegratedApp()
    window.show()
    sys.exit(app.exec())
