import os
import csv
import pandas as pd
from datetime import datetime
from models import ApplicationResultRecord

STATUS_LOG_FILE = "status_history.csv"
LOG_CSV_FILE = "applications_log.csv"


def init_csv_log():
    """初始化 CSV 紀錄檔並寫入表頭"""
    if not os.path.exists(LOG_CSV_FILE):
        df = pd.DataFrame(columns=["時間", "使用者ID", "施工地址", "開始時間", "結束時間", "案件編號", "查詢碼"])
        df.to_csv(LOG_CSV_FILE, index=False, encoding="utf-8-sig")


# 模組載入時自動初始化
init_csv_log()


def save_application_log(record: ApplicationResultRecord):
    """將 ApplicationResultRecord 物件寫入 CSV"""
    new_data = pd.DataFrame(
        [
            {
                "時間": record.timestamp,
                "使用者ID": record.user_id,
                "施工地址": record.address,
                "開始時間": record.start_time,
                "結束時間": record.end_time,
                "案件編號": record.case_no,
                "查詢碼": record.query_code,
            }
        ]
    )

    try:
        header_flag = not os.path.exists(LOG_CSV_FILE) or os.path.getsize(LOG_CSV_FILE) == 0
        new_data.to_csv(LOG_CSV_FILE, mode="a", index=False, header=header_flag, encoding="utf-8-sig")
    except Exception as e:
        print(f"⚠️ 寫入記錄檔失敗: {e}")


def append_status_log(case_no: str, status: str):
    """使用內建 csv 模組安全地將狀態附加到日誌中（不依賴 pandas）"""
    file_exists = os.path.exists(STATUS_LOG_FILE)

    with open(STATUS_LOG_FILE, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        # 如果檔案不存在，先寫入表頭
        if not file_exists:
            writer.writerow(["時間", "案件編號", "案件狀態"])

        # 寫入資料列，內建 csv 會自動處理特殊字元與引號包覆
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(case_no), status])


def get_latest_status_by_case(case_no: str) -> str:
    """根據指定的案件編號，從狀態日誌中找出最新的一筆狀態"""
    if not os.path.exists(STATUS_LOG_FILE):
        return ""

    try:
        df = pd.read_csv(STATUS_LOG_FILE, encoding="utf-8-sig")
        if df.empty or "案件編號" not in df.columns:
            return ""

        # 過濾出該案件編號的所有紀錄
        df["案件編號"] = df["案件編號"].astype(str)
        matched = df[df["案件編號"] == str(case_no)]

        if matched.empty:
            return ""

        # 依時間排序並回傳最後一筆（最新）的狀態
        matched = matched.sort_values("時間")
        return str(matched.iloc[-1]["案件狀態"])
    except Exception as e:
        print(f"❌ 讀取狀態日誌失敗: {e}")
        return ""


def get_latest_statuses_for_batch(case_nos: list) -> dict:
    """傳入一組案件編號清單，從 status_history.csv 中撈取每個案件時間最晚的一筆狀態"""
    if not os.path.exists(STATUS_LOG_FILE):
        return {}

    try:
        df = pd.read_csv(STATUS_LOG_FILE, encoding="utf-8-sig")
        if df.empty or "案件編號" not in df.columns:
            return {}

        df["案件編號"] = df["案件編號"].astype(str)
        # 只篩選出目前清單內的案件編號
        matched = df[df["案件編號"].isin([str(c) for c in case_nos])]

        if matched.empty:
            return {}

        # 依時間由舊到新排序，並保留每個案件編號的最後一筆（最新狀態）
        matched = matched.sort_values("時間")
        latest_df = matched.drop_duplicates(subset=["案件編號"], keep="last")

        # 回傳 {"案件編號": "最新狀態"} 的對應字典
        return dict(zip(latest_df["案件編號"], latest_df["案件狀態"]))
    except Exception as e:
        print(f"❌ 批次讀取狀態日誌失敗: {e}")
        return {}
