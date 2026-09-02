import calendar
import re
import io
import threading
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import time
import xml.etree.ElementTree as ET
from tkinter import filedialog, messagebox
import tkinter as tk
import logging
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, LEFT, RIGHT, TOP, W, X
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import pytesseract

# ==========================================
# 1. 資料模型 (Data Classes)
# ==========================================
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


@dataclass
class StaticUserInfo:
    """使用者靜態的基本資訊"""

    name: str
    id: str
    mobile: str
    email: str
    town: str
    address: str  # 通訊地址
    reason: str  # 申請理由 (ex: 商家招牌老舊需要更新)
    id_image: str = str(Path("./id.jpg").absolute())  # 身分證正面照片路徑


@dataclass
class DynamicApplyInfo:
    """動態填寫資料：施工地址、日期、開始時間、施工多久、路權圖檔"""

    address: str  # 施工地址 (ex: 新北市中和區中和路466號)
    start_datetime: datetime  # 施工開始日期時間 (ex: 2026-01-01 10:00:00)
    duration: timedelta  # 施工多久(timedelta)
    road_right_image: str  # 路權圖檔路徑

    def __post_init__(self):
        """初始化後自動查詢該地址對應的鄉鎮市區、村里與里長"""
        self.address = self.normalize(self.address)
        village_info = get_village_by_address(self.address)

        logger.info("village_info: %s", village_info)
        self.town = village_info.get("town", "")  # 施工地點鄉鎮區 (ex: 中和區)
        self.village = village_info.get("village", "")  # 施工地點村里 (ex: 中和里)

        # 取消xx市xx區xx里，只保留路名
        self.short_address = (
            re.sub(
                r"^\d{0,5}新北市(?:.+?區)?(?:.+?里)?(?:.+?鄰)?|\d+(?:[之-]\d+)?號.*$",
                "",
                self.address,
            ).strip()
            or self.address
        )

        no_pattern = re.compile(r"\d+(?:[之-]\d+)?號")
        match = no_pattern.search(self.address)
        house_no = match.group(0) if match else ""
        self.road_number = house_no.replace("號", "").strip() or ""

        chief_info = get_village_chief_info(self.village, self.town)
        self.chief_name = chief_info.get("chief_name") or ""  # 里長姓名

    def normalize(self, raw_address: str) -> str:
        """
        正規化地址字串：
        - Unicode 正規化 (NFKC) 轉全形為半形（數字、英文字母、空白）
        - 統一「臺」->「臺」
        - 統一各種破折號與波浪號
        - 移除多餘的標點前後空白與重複空白
        """
        # Unicode 正規化 (NFKC) 可將大部分全形英數、空白轉為半形
        s = unicodedata.normalize("NFKC", raw_address)

        # 針對部分中文符號手動做轉半形替換
        s = self._to_half_width(raw_address)

        # 統一臺->臺
        s = s.replace("臺", "臺")

        # 統一各種長短破折號與波浪號
        s = s.replace("－", "-").replace("—", "-").replace(r"–", "-").replace("〜", "~")

        # 移除標點符號與空白處理
        s = re.sub(r"\s*[,，:]+\s*", " ", s)
        s = re.sub(r"\s+", "", s).strip()

        return s

    def _to_half_width(self, input_str: str) -> str:
        """輔助方法：將常見全形標點轉半形"""
        if not input_str:
            return input_str

        char_map = {
            "，": ",",
            "。": ".",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
            "「": '"',
            "」": '"',
            "『": '"',
            "』": '"',
            " ": " ",
            # 全形數字
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
        }
        return "".join(char_map.get(ch, ch) for ch in input_str)


# ==========================================
# 2. 地理資訊與開放資料 API
# ==========================================


@retry(
    stop=stop_after_attempt(3),  # 最多重試 3 次 (含第一次呼叫共 3 次)
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        "請求失敗，即將進行第 %s 次重試... 原因: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def get_village_by_address(address: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 步驟 1: 地址 -> 經緯度 (ArcGIS)
    arcgis_url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    arcgis_params = {
        "SingleLine": address,
        "f": "json",
        "outSR": '{"wkid":4326}',
        "maxLocations": 1,
    }

    try:
        res_arcgis = requests.get(
            arcgis_url, params=arcgis_params, headers=headers, timeout=15
        )
        res_arcgis.raise_for_status()
        arcgis_data = res_arcgis.json()

        candidates = arcgis_data.get("candidates", [])
        if not candidates:
            raise ValueError("ArcGIS 查無此地址對應的座標")

        location = candidates[0]["location"]
        lng = location["x"]
        lat = location["y"]

    except Exception as e:
        raise ValueError(f"ArcGIS 轉座標失敗: {e}")

    # 步驟 2: 經緯度 -> 村里 (NLSC)
    nlsc_url = f"https://api.nlsc.gov.tw/other/TownVillagePointQuery/{lng}/{lat}/4326"

    res_nlsc = requests.get(nlsc_url, headers=headers, timeout=25)
    res_nlsc.raise_for_status()

    root = ET.fromstring(res_nlsc.content)
    cty_name = root.findtext("ctyName") or ""
    town_name = root.findtext("townName") or ""
    village_name = root.findtext("villageName") or ""

    if not village_name:
        raise ValueError("NLSC 座標查無對應村里資訊")

    return {
        "address": address,
        "longitude": lng,
        "latitude": lat,
        "county": cty_name,
        "town": town_name,
        "village": village_name,
        "location_key": f"{cty_name}{town_name}{village_name}",
    }


@retry(
    stop=stop_after_attempt(3),  # 最多重試 3 次 (含第一次呼叫共 3 次)
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        "請求失敗，即將進行第 %s 次重試... 原因: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def get_village_chief_info(village_name: str, district_name: str = None) -> dict:
    url = "https://data.ntpc.gov.tw/api/datasets/d0070ef9-ae5c-423f-9f5c-e22aef21c33b/csv?page=0&size=20000"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text), dtype=str)
        filtered_df = df[df["village"] == village_name]

        if district_name and not filtered_df.empty:
            filtered_df = filtered_df[
                filtered_df["address"].str.contains(district_name, na=False)
            ]

        if filtered_df.empty:
            dist_str = f"（{district_name}）" if district_name else ""
            raise ValueError(f"查無【{dist_str}{village_name}】的里長資料")

        row = filtered_df.iloc[0]
        return {
            "village": row["village"],
            "chief_name": row["name"],
            "address": row["address"],
            "tel": row["tel"],
            "mobile": row["mobile"],
        }

    except Exception as e:
        raise ValueError(f"讀取里長 API 失敗: {e}")


# ==========================================
# 3. Selenium 自動化表單填寫
# ==========================================


class AutoFillForm:
    def __init__(self):
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service)
        self.wait = WebDriverWait(self.driver, 15)

    def auto_fill_form(self, apply_info: DynamicApplyInfo):
        try:
            self.driver.get("https://service.ntpc.gov.tw/eservice/Ac125024.action")
            self.driver.maximize_window()

            # 勾選同意條款
            self.wait.until(EC.element_to_be_clickable((By.ID, "chkAgree"))).click()
            self.driver.find_element(By.ID, "btnAgree").click()

            # 靜態申辦人預設資料
            input_value = StaticUserInfo(
                name="蕭子芸",
                id="F230585667",
                mobile="0970217793",
                email="j39273692@gmail.com",
                town="中和區",
                address="中和路466號8樓",
                reason="商家招牌老舊需要更新",
            )

            # 填寫基礎表單欄位
            logger.info("填寫申請人資料")
            self.wait.until(
                EC.visibility_of_element_located((By.NAME, "applNameP"))
            ).send_keys(input_value.name)
            self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//input[@type='radio' and @id='sex' and @value='F']")
                )
            ).click()
            logger.info("填寫身分證字號")
            self.wait.until(EC.element_to_be_clickable((By.NAME, "idNum"))).send_keys(
                input_value.id
            )
            logger.info("填寫手機號碼")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applMobile"))
            ).send_keys(input_value.mobile)
            logger.info("填寫電子郵件")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applEmail"))
            ).send_keys(input_value.email)
            logger.info("填寫區域")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applTown"))
            ).send_keys(input_value.town)
            logger.info("填寫地址")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applAddr"))
            ).send_keys(input_value.address)
            logger.info("填寫理由")
            # 選擇申請類型與理由
            logger.info("選擇申請類型")
            self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//label[@for='applyType']"))
            ).click()
            logger.info("填寫理由")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applyReason"))
            ).send_keys(input_value.reason)

            # 填入動態施工資料
            logger.info("填寫區域")
            town_select_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "applyRoadTown"))
            )
            town_select = Select(town_select_element)
            logger.info("選擇區域")
            town_select.select_by_visible_text(apply_info.town)
            logger.info("填寫地址")
            logger.info("填寫路名")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applyRoadAddr"))
            ).send_keys(apply_info.short_address)
            logger.info("填寫路名")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applyRoadNumStr"))
            ).send_keys(apply_info.road_number)
            logger.info("填寫路名")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applyRoadNumEnd"))
            ).send_keys(apply_info.road_number)
            logger.info("填寫開始日期")
            self.wait.until(
                EC.element_to_be_clickable((By.NAME, "applyStrDt"))
            ).send_keys(apply_info.start_datetime.strftime("%Y-%m-%d"))

            # 【修正】: 轉為兩位數字串（例如 '10', '00'）
            hour_str = f"{apply_info.start_datetime.hour:02d}"
            minute_str = f"{apply_info.start_datetime.minute:02d}"

            logger.info("選擇開始時間")
            hour_select_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "asdHour"))
            )
            hour_select = Select(hour_select_element)
            hour_select.select_by_visible_text(hour_str)
            logger.info("選擇開始時間")
            minute_select_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "asdMinute"))
            )
            minute_select = Select(minute_select_element)
            minute_select.select_by_visible_text(minute_str)

            logger.info("填寫結束日期")
            end_datetime = apply_info.start_datetime + apply_info.duration
            logger.info("填寫結束日期")
            end_date_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "applyEndDt"))
            )
            end_date_input_element.send_keys(end_datetime.strftime("%Y-%m-%d"))

            logger.info("填寫結束時間")
            end_hour_str = f"{end_datetime.hour:02d}"
            end_minute_str = f"{end_datetime.minute:02d}"

            logger.info("選擇結束時間")
            end_hour_select_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "aedHour"))
            )
            end_hour_select = Select(end_hour_select_element)
            end_hour_select.select_by_visible_text(end_hour_str)

            logger.info("選擇結束時間")
            end_minute_select_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "aedMinute"))
            )
            end_minute_select = Select(end_minute_select_element)
            end_minute_select.select_by_visible_text(end_minute_str)

            logger.info("填寫村里")
            village_name_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "villageName"))
            )
            village_name_input_element.send_keys(apply_info.chief_name)

            today = date.today()
            logger.info("填寫村里通知日期")
            village_notify_date_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "villageNotifyDt"))
            )
            village_notify_date_input_element.send_keys(
                (today - timedelta(days=1)).strftime("%Y-%m-%d")
            )

            logger.info("填寫里長姓名")
            residendt_name_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "residendtName"))
            )
            residendt_name_input_element.send_keys("蕭秋霖")

            logger.info("填寫里長通知日期")
            residendt_notify_date_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "residendtNotifyDt"))
            )
            residendt_notify_date_input_element.send_keys(
                (today - timedelta(days=1)).strftime("%Y-%m-%d")
            )

            # 驗證碼辨識
            logger.info("辨識驗證碼")
            img_element = self.wait.until(
                EC.visibility_of_element_located((By.ID, "authImage"))
            )
            img_bytes = img_element.screenshot_as_png
            image = Image.open(io.BytesIO(img_bytes))

            image = image.convert("L")
            threshold = 150
            image = image.point(lambda p: 255 if p > threshold else 0)

            custom_config = r"--psm 6 -c tessedit_char_whitelist=0123456789"
            code_text = pytesseract.image_to_string(image, config=custom_config).strip()

            logger.info("辨識到的驗證碼為: %s", code_text)

            logger.info("填寫驗證碼")
            auth_code_input_element = self.wait.until(
                EC.presence_of_element_located((By.ID, "atuh_gCode"))
            )
            auth_code_input_element.click()
            auth_code_input_element.send_keys(code_text)

            # --- 1. 上傳路權圖（第 1 個項目）---
            logger.info("上傳路權圖")
            self.upload_angular_file(self.driver, 0, apply_info.road_right_image)

            # 上傳第 2 份：身分證 (Index 1)
            logger.info("上傳身分證")
            self.upload_angular_file(self.driver, 1, input_value.id_image)

            #  button[data-ng-click="doSave(ac125024);"]
            self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[@data-ng-click='doSave(ac125024);']")
                )
            ).click()

            # 滾動到頁面最上方
            self.driver.execute_script("window.scrollTo(0, 0);")

            logger.info("等待5秒")
            time.sleep(5)
            logger.info("等待5秒完成")
        except Exception as e:
            logger.error("自動填表發生錯誤: %s", e)

    @staticmethod
    def upload_angular_file(driver, item_index, file_path):
        """
        透過 JS 設定 AngularJS 索引並繞過 OS 檔案視窗，直接上傳檔案
        :param driver: Selenium WebDriver 實例
        :param item_index: 0 代表第 1 個上傳項目, 1 代表第 2 個項目
        :param file_path: 檔案的絕對路徑
        """
        # 1. 定位第 N 個「上傳檔案」按鈕 (XPath 索引從 1 開始)
        btn_xpath = f"(//input[@value='上傳檔案'])[{item_index + 1}]"
        btn_element = driver.find_element(By.XPATH, btn_xpath)

        # 2. 透過 JavaScript 執行 Angular 的 FF($index)，並暫時『封鎖』跳出視窗的 .click() 事件
        js_code = """
        var btn = arguments[0];
        var index = arguments[1];

        // 暫時覆蓋所有 input[type="file"] 的 click 函式，防止原生視窗跳出
        var fileInputs = document.querySelectorAll('input[type="file"]');
        var originalClicks = [];
        fileInputs.forEach(function(input, i) {
            originalClicks[i] = input.click;
            input.click = function() {};
        });

        // 取得 Angular Scope 並觸發 FF(index) 設定當前 index
        var scope = angular.element(btn).scope();
        scope.$apply(function() {
            scope.FF(index);
        });

        // 恢復原本的 click 函式
        fileInputs.forEach(function(input, i) {
            input.click = originalClicks[i];
        });
        """
        driver.execute_script(js_code, btn_element, item_index)

        # 3. 定位對應位置的 <input type="file" id="testF1"> 元素
        file_input_xpath = f"(//input[@type='file' and @id='testF1'])[{item_index + 1}]"
        file_input = driver.find_element(By.XPATH, file_input_xpath)

        # 4. 若 input 被 hidden (display: none)，先透過 JS 顯示出來
        driver.execute_script("arguments[0].style.display = 'block';", file_input)

        # 5. 直接將檔案路徑傳入 (完全不經過 OS 檔案選擇視窗)
        file_input.send_keys(file_path)


# ==========================================
# 4. 長輩友善 GUI 介面與警告彈窗
# ==========================================


def show_senior_warning(parent, title, message):
    dialog = ttk.Toplevel(parent)
    dialog.title(title)
    dialog.grab_set()
    dialog.resizable(False, False)

    main_frame = ttk.Frame(dialog, padding=25)
    main_frame.pack(fill=BOTH, expand=True)

    msg_label = ttk.Label(
        main_frame,
        text=f"⚠️  {message}",
        font=("Microsoft JhengHei", 20, "bold"),
        bootstyle="warning",
        wraplength=450,
        justify=LEFT,
    )
    msg_label.pack(pady=(10, 25))

    btn_style = ttk.Style()
    btn_style.configure(
        "SeniorWarning.TButton", font=("Microsoft JhengHei", 15, "bold")
    )

    ok_btn = ttk.Button(
        main_frame,
        text="我知道了",
        style="SeniorWarning.TButton",
        bootstyle="warning",
        command=dialog.destroy,
    )
    ok_btn.pack(fill=X, ipady=8)

    dialog.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = parent.winfo_y() + (parent.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    ok_btn.focus_set()
    dialog.wait_window()


class CustomConfirmDialog(tk.Toplevel):
    def __init__(self, parent, title, message, font_size=16):
        super().__init__(parent)
        self.title(title)
        self.result = False

        # 視窗設定 Modal 效果
        self.transient(parent)
        self.grab_set()

        # 設定 Padding 與佈局
        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill="both", expand=True)

        # 訊息內文 (字體放大)
        msg_label = ttk.Label(
            main_frame,
            text=message,
            font=("Microsoft JhengHei", font_size),
            justify="left",
        )
        msg_label.pack(padx=10, pady=(0, 20), fill="both", expand=True)

        # 按鈕區域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(10, 0))

        # 自訂放大字體樣式 (搭配 ttkbootstrap 的顏色)
        style = ttk.Style()
        style.configure(
            "Yes.TButton",
            font=("Microsoft JhengHei", font_size, "bold"),
            background="#198754",
            foreground="white",
        )
        style.configure("No.TButton", font=("Microsoft JhengHei", font_size))

        # 「是」按鈕：綠色 (bootstyle="success")
        btn_yes = ttk.Button(
            btn_frame,
            text="開始申請",
            bootstyle="success",  # 綠色
            style="Yes.TButton",
            command=self.on_yes,
        )
        btn_yes.pack(side="right", padx=10)

        # 「否/取消」按鈕：灰色 (bootstyle="secondary")
        btn_no = ttk.Button(
            btn_frame,
            text="取消",
            bootstyle="secondary",  # 灰色
            style="No.TButton",
            command=self.on_no,
        )
        btn_no.pack(side="right", padx=10)

        # 視窗置中處理
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

        parent.wait_window(self)

    def on_yes(self):
        self.result = True
        self.destroy()

    def on_no(self):
        self.result = False
        self.destroy()


class SeniorFriendlyUI:
    def __init__(self, root):
        self.root = root
        self.root.title("新北市路權申請系統")
        self.root.geometry("700x680")
        self.root.minsize(650, 680)

        self.font_title = ("Microsoft JhengHei", 22, "bold")
        self.font_label = ("Microsoft JhengHei", 16, "bold")
        self.font_entry = ("Microsoft JhengHei", 16)
        self.font_cal_head = ("Microsoft JhengHei", 14, "bold")
        self.font_selected_date = ("Microsoft JhengHei", 20, "bold")

        self.root.option_add("*TCombobox*Listbox.font", ("Microsoft JhengHei", 16))
        self.root.option_add("*TCombobox*Listbox.itemHeight", 35)
        self.style = ttk.Style()

        self.today = date.today()
        self.current_year = self.today.year
        self.current_month = self.today.month

        self.selected_date = None
        self.image_path = ""
        self.weekdays_zh = [
            "星期一",
            "星期二",
            "星期三",
            "星期四",
            "星期五",
            "星期六",
            "星期日",
        ]
        self.is_cal_expanded = True

        self.main_frame = ttk.Frame(root, padding=20)
        self.main_frame.pack(fill=BOTH, expand=True)

        title_label = ttk.Label(
            self.main_frame,
            text="📝 新北市路權申請系統",
            font=self.font_title,
            bootstyle="primary",
        )
        title_label.pack(pady=(0, 10))

        ttk.Label(self.main_frame, text="1. 請輸入地址：", font=self.font_label).pack(
            anchor=W, pady=(5, 2)
        )
        self.addr_entry = tk.Entry(
            self.main_frame,
            font=self.font_entry,
            fg="blue",  # 文字顏色：藍色
            bg="white",  # 背景顏色：白色
            relief="solid",  # 邊框樣式：實線
            bd=1,  # 邊框寬度：1px
            highlightbackground="gray",  # 未聚焦時的外框顏色：黑色
            highlightcolor="black",  # 聚焦 (Focus) 時的外框顏色：黑色
        )
        self.addr_entry.insert(0, "新北市")
        self.addr_entry.pack(fill=X, ipady=4, pady=(0, 10))

        ttk.Label(self.main_frame, text="2. 請選擇日期：", font=self.font_label).pack(
            anchor=W, pady=(5, 2)
        )

        self.cal_container = ttk.Labelframe(
            self.main_frame, bootstyle="info", padding=8
        )
        self.cal_container.pack(fill=X, pady=(0, 10))

        self.style.configure("LargeToggle.TButton", font=self.font_selected_date)
        self.style.configure("LargeImg.Outline.TButton", font=self.font_cal_head)

        self.toggle_btn = ttk.Button(
            self.cal_container,
            text="",
            style="LargeToggle.TButton",
            bootstyle="primary",
            command=self.toggle_calendar,
        )

        self.cal_body_frame = ttk.Frame(self.cal_container)
        self.cal_body_frame.pack(fill=X)

        cal_header = ttk.Frame(self.cal_body_frame)
        cal_header.pack(fill=X, pady=(0, 5))

        btn_prev = ttk.Button(
            cal_header,
            text="◀ 上個月",
            bootstyle="info-outline",
            command=self.prev_month,
        )
        btn_prev.pack(side=LEFT, ipadx=5)

        self.month_label = ttk.Label(
            cal_header,
            text="",
            font=("Microsoft JhengHei", 18, "bold"),
            bootstyle="primary",
        )
        self.month_label.pack(side=LEFT, expand=True)

        btn_next = ttk.Button(
            cal_header,
            text="下個月 ▶",
            bootstyle="info-outline",
            command=self.next_month,
        )
        btn_next.pack(side=RIGHT, ipadx=5)

        week_frame = ttk.Frame(self.cal_body_frame)
        week_frame.pack(fill=X, pady=(0, 2))

        weeks = ["一", "二", "三", "四", "五", "六", "日"]
        for i, w in enumerate(weeks):
            color = "danger" if i >= 5 else "secondary"
            lbl = ttk.Label(
                week_frame,
                text=w,
                font=self.font_cal_head,
                anchor=CENTER,
                bootstyle=color,
            )
            lbl.pack(side=LEFT, expand=True, fill=X)

        self.grid_frame = ttk.Frame(self.cal_body_frame)
        self.grid_frame.pack(fill=X)

        self.render_calendar()

        ttk.Label(
            self.main_frame, text="3. 請選擇開始時間：", font=self.font_label
        ).pack(anchor=W, pady=(5, 2))

        time_frame = ttk.Frame(self.main_frame)
        time_frame.pack(fill=X, pady=(0, 10))

        self.style.configure("Custom.TCombobox", foreground="blue")
        self.style.map(
            "Custom.TCombobox",
            foreground=[("readonly", "blue")],
            fieldbackground=[("readonly", "white")],
        )
        self.hour_cb = ttk.Combobox(
            time_frame,
            values=[f"{i:02d} 點" for i in range(24)],
            font=self.font_entry,
            width=8,
            state="readonly",
            style="Custom.TCombobox",
        )
        self.hour_cb.set("10 點")
        self.hour_cb.pack(side=LEFT, padx=(0, 10), ipady=3)

        self.minute_cb = ttk.Combobox(
            time_frame,
            values=["00 分", "30 分"],
            font=self.font_entry,
            width=8,
            state="readonly",
            style="Custom.TCombobox",
        )
        self.minute_cb.set("00 分")
        self.minute_cb.pack(side=LEFT, ipady=3)

        ttk.Label(
            self.main_frame, text="4. 請選擇使用時間長短：", font=self.font_label
        ).pack(anchor=W, pady=(5, 2))

        self.duration_cb = ttk.Combobox(
            self.main_frame,
            values=[f"{i} 小時" for i in range(1, 49)],
            font=self.font_entry,
            state="readonly",
            style="Custom.TCombobox",
        )
        self.duration_cb.set("6 小時")
        self.duration_cb.pack(fill=X, ipady=4, pady=(0, 10))

        ttk.Label(self.main_frame, text="5. 上傳路權圖檔：", font=self.font_label).pack(
            anchor=W, pady=(5, 2)
        )

        img_frame = ttk.Frame(self.main_frame)
        img_frame.pack(fill=X, pady=(0, 10))

        self.btn_select_img = ttk.Button(
            img_frame,
            text="📁 選擇照片",
            style="LargeImg.Outline.TButton",
            command=self.choose_image,
        )
        self.btn_select_img.pack(side=LEFT, ipady=6, ipadx=12)

        self.img_label = ttk.Label(
            img_frame,
            text="尚未選擇檔案",
            font=("Microsoft JhengHei", 16),
            foreground="red",
        )
        self.img_label.pack(side=LEFT, padx=10)

        # 2. 宣告按鈕（保持原本的 bootstyle="success" 不變）
        self.style.configure("success.TButton", font=("Microsoft JhengHei", 16, "bold"))
        self.submit_btn = ttk.Button(
            self.main_frame,
            text="確認送出並填寫表單 ➔",
            bootstyle="success",
            command=self.submit,
        )
        self.submit_btn.pack(fill=X, ipady=8, pady=(10, 0))

        self.addr_entry.focus_set()
        self.addr_entry.icursor(tk.END)

    def toggle_calendar(self):
        if self.is_cal_expanded:
            self.cal_body_frame.pack_forget()
            self.is_cal_expanded = False
        else:
            self.cal_body_frame.pack(fill=X)
            self.is_cal_expanded = True
        self.update_toggle_btn_text()

    def update_toggle_btn_text(self):
        if self.selected_date is None:
            return

        self.toggle_btn.pack(side=TOP, fill=X, ipady=6, pady=(0, 1))

        weekday_name = self.weekdays_zh[self.selected_date.weekday()]
        today_note = "【今天】" if self.selected_date == self.today else ""
        date_str = self.selected_date.strftime("%Y-%m-%d")

        self.style.configure("LargeToggle.TButton", font=("Microsoft JhengHei", 14))
        if self.is_cal_expanded:
            self.toggle_btn.config(
                text=f"👉 已選：{date_str} {today_note} ({weekday_name})  [點此收合 ▲]",
                bootstyle="warning-outline",
                style="LargeToggle.TButton",
            )
        else:
            self.toggle_btn.config(
                text=f"👉 已選：{date_str} {today_note} ({weekday_name})  [按此修改 🔻]",
                bootstyle="primary",
                style="LargeToggle.TButton",
            )

    def render_calendar(self):
        for widget in self.grid_frame.winfo_children():
            widget.destroy()

        self.month_label.config(
            text=f"{self.current_year} 年 {self.current_month:02d} 月"
        )
        cal = calendar.monthcalendar(self.current_year, self.current_month)

        for r, week in enumerate(cal):
            for c, day in enumerate(week):
                if day == 0:
                    lbl = ttk.Label(self.grid_frame, text="")
                    lbl.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                else:
                    curr_d = date(self.current_year, self.current_month, day)
                    btn_text = str(day)
                    is_today = curr_d == self.today
                    if is_today:
                        btn_text = f"{day}(今)"

                    min_allowed_date = self.today + timedelta(days=9)
                    if curr_d < min_allowed_date:
                        btn = ttk.Button(
                            self.grid_frame,
                            text=btn_text,
                            bootstyle="secondary",
                            state="disabled",
                        )
                    elif self.selected_date and curr_d == self.selected_date:
                        btn = ttk.Button(
                            self.grid_frame,
                            text=btn_text,
                            bootstyle="primary",
                            command=lambda d=day: self.select_day(d),
                        )
                    elif is_today:
                        btn = ttk.Button(
                            self.grid_frame,
                            text=btn_text,
                            bootstyle="warning-outline",
                            command=lambda d=day: self.select_day(d),
                        )
                    else:
                        btn = ttk.Button(
                            self.grid_frame,
                            text=btn_text,
                            bootstyle="secondary-outline",
                            command=lambda d=day: self.select_day(d),
                        )

                    btn.grid(
                        row=r,
                        column=c,
                        sticky="nsew",
                        padx=2,
                        pady=2,
                        ipady=4,
                    )

            for c in range(7):
                self.grid_frame.columnconfigure(c, weight=1)

        self.update_toggle_btn_text()

    def select_day(self, day):
        self.selected_date = date(self.current_year, self.current_month, day)
        self.cal_body_frame.pack_forget()
        self.is_cal_expanded = False
        self.render_calendar()

    def prev_month(self):
        if self.current_month == 1:
            self.current_month = 12
            self.current_year -= 1
        else:
            self.current_month -= 1
        self.render_calendar()

    def next_month(self):
        if self.current_month == 12:
            self.current_month = 1
            self.current_year += 1
        else:
            self.current_month += 1
        self.render_calendar()

    def choose_image(self):
        file_path = filedialog.askopenfilename(
            title="選擇路權圖檔",
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png *.pdf")],
        )
        if file_path:
            self.image_path = file_path
            file_name = Path(file_path).name
            self.img_label.config(text=f"已選擇: {file_name}", foreground="green")

    def submit(self):
        address = self.addr_entry.get().strip()
        if not address:
            show_senior_warning(self.root, "提醒", "請填寫地址！")
            return

        if "新北市" not in address:
            show_senior_warning(self.root, "提醒", "請填寫新北市地址！")
            return

        if not self.selected_date:
            show_senior_warning(self.root, "提醒", "請先點擊日曆選擇日期！")
            return

        img_path = self.image_path
        if not img_path:
            show_senior_warning(self.root, "提醒", "請先上傳路權圖檔！")
            return

        hour = int(self.hour_cb.get().replace(" 點", ""))
        minute = int(self.minute_cb.get().replace(" 分", ""))
        start_dt = datetime(
            self.selected_date.year,
            self.selected_date.month,
            self.selected_date.day,
            hour,
            minute,
            0,
        )

        duration_hours = int(self.duration_cb.get().replace(" 小時", ""))
        duration = timedelta(hours=duration_hours)

        # 禁用按鈕避免重複點擊
        self.submit_btn.config(state="disabled", text="處理中，請稍候...")

        # 開啟 Thread 執行耗時工作（查詢 API + Selenium 自動填表）
        threading.Thread(
            target=self._async_process,
            args=(address, start_dt, duration, img_path),
            daemon=True,
        ).start()

    def _async_process(self, address, start_dt, duration, img_path):
        """背景執行的邏輯"""
        try:
            # 建立申請資料物件 (內部包含 API 查詢)
            result = DynamicApplyInfo(
                address=address,
                start_datetime=start_dt,
                duration=duration,
                road_right_image=img_path,
            )

            weekday_str = self.weekdays_zh[start_dt.weekday()]
            hours_num = int(result.duration.total_seconds() // 3600)

            # 在主執行緒顯示提示框
            msg_text = (
                f"已成功建立資料！請問是否立即開啓自動填表功能？\n\n"
                f"📍 地址：{result.address}\n"
                f"👥 行政區：{result.town} / {result.village} (里長: {result.chief_name or '未知'})\n"
                f"⏰ 開始時間：{result.start_datetime.strftime('%Y-%m-%d')} ({weekday_str}) {result.start_datetime.strftime('%H:%M')}\n"
                f"⏱️ 時長：{hours_num} 小時\n"
                f"📄 圖檔：{Path(result.road_right_image).name}"
            )

            # 彈出自訂大字體確認視窗 (font_size 可自訂大小，預設為 14pt)
            dialog = CustomConfirmDialog(self.root, "送出確認", msg_text, font_size=16)
            is_confirmed = dialog.result

            if is_confirmed:
                # 使用者點擊「是」，啟動自動填表流程
                auto_bot = AutoFillForm()
                auto_bot.auto_fill_form(result)

        except Exception as error:
            self.root.after(0, messagebox.showerror("錯誤", f"處理失敗: {error}"))
        finally:
            # 恢復按鈕狀態
            self.root.after(
                0,
                lambda: self.submit_btn.config(
                    state="normal", text="確認送出並填寫表單 ➔"
                ),
            )


if __name__ == "__main__":
    app = ttk.Window(themename="minty-light")
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:
        show_senior_warning(app, "提醒", f"tesseract 未安裝: {e}")
    else:
        gui = SeniorFriendlyUI(app)
        app.mainloop()
