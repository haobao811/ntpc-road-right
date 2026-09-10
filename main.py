"""
應用程式入口點
"""

import logging
import os
import subprocess
import sys
import tempfile

import requests
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui import IntegratedApp

# 假設這是本地當前版本
CURRENT_VERSION = "v1.1.3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def check_and_update():
    repo = "haobao811/ntpc-road-right"
    url = f"https://api.github.com/repos/{repo}/releases/latest"

    try:
        response = requests.get(url, verify=False, timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_version = data["tag_name"]  # 例如 "v1.1.0"

            if latest_version != CURRENT_VERSION:
                # 必須先建立 QApplication 才能使用 QMessageBox 彈出視窗
                app = QApplication.instance()
                if not app:
                    app = QApplication(sys.argv)

                reply = QMessageBox.question(
                    None,
                    "發現新版本",
                    f"偵測到新版本 {latest_version}（目前版本：{CURRENT_VERSION}）\n是否要立即下載並更新？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )

                if reply != QMessageBox.StandardButton.Yes:
                    logging.info("使用者選擇暫不更新。")
                    return

                assets = data.get("assets", [])
                if not assets:
                    logging.warning("找到新版本，但沒有可下載的附件。")
                    QMessageBox.warning(None, "更新失敗", "找到新版本，但沒有可下載的附件檔案。")
                    return

                download_url = assets[0]["browser_download_url"]
                file_name = assets[0]["name"]

                logging.info(f"發現新版本: {latest_version} (目前版本: {CURRENT_VERSION})，準備自動更新...")

                # 下載到暫存資料夾
                temp_dir = tempfile.gettempdir()
                installer_path = os.path.join(temp_dir, file_name)

                logging.info(f"正在下載更新至: {installer_path}")
                dl_response = requests.get(download_url, verify=False, stream=True)
                if dl_response.status_code == 200:
                    with open(installer_path, "wb") as f:
                        for chunk in dl_response.iter_content(chunk_size=8192):
                            f.write(chunk)

                    logging.info("下載完成，正在啟動安裝程式並關閉當前應用程式...")

                    # 執行下載的新版本/安裝包
                    subprocess.Popen([installer_path], shell=True)
                    sys.exit(0)
            else:
                logging.info("目前已是最新版本。")
        else:
            logging.warning("讀取 GitHub Release 失敗")
    except Exception as e:
        logging.error(f"檢查更新時發生錯誤: {e}")


def auto_install_tesseract():
    """當偵測到系統未安裝 Tesseract 時，嘗試自動呼叫系統指令進行靜默安裝"""
    logging.info("嘗試自動安裝 Tesseract-OCR")
    try:
        # 透過 winget 自動安裝
        cmd = "winget install UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements"
        subprocess.check_call(cmd, shell=True)
    except Exception as e:
        logging.error(f"自動安裝 Tesseract 失敗: {e}")
        return False


if __name__ == "__main__":
    # 注意：Qt 應用程式通常需要先初始化 QApplication 才能安全建立 QMessageBox
    app = QApplication(sys.argv)

    # 1. 檢查 GitHub 版本更新
    check_and_update()

    # 3. 啟動主畫面
    app_font = QFont("Microsoft JhengHei", 16)
    app.setFont(app_font)

    window = IntegratedApp(version=CURRENT_VERSION)
    window.show()
    sys.exit(app.exec())
