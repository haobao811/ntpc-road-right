import os
import pandas as pd
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
    new_data = pd.DataFrame([{
        "時間": record.timestamp,
        "使用者ID": record.user_id,
        "施工地址": record.address,
        "開始時間": record.start_time,
        "結束時間": record.end_time,
        "案件編號": record.case_no,
        "查詢碼": record.query_code
    }])

    try:
        header_flag = not os.path.exists(LOG_CSV_FILE) or os.path.getsize(LOG_CSV_FILE) == 0
        new_data.to_csv(LOG_CSV_FILE, mode="a", index=False, header=header_flag, encoding="utf-8-sig")
    except Exception as e:
        print(f"⚠️ 寫入記錄檔失敗: {e}")


def append_status_log(case_no: str, status: str):
    """安全地將每次查詢到的狀態新增到日誌中"""
    new_row = pd.DataFrame([{
        "時間": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "案件編號": str(case_no),
        "案件狀態": status
    }])

    # 如果檔案不存在則寫入表頭，存在則純粹 append 附加在後面
    header = not os.path.exists(STATUS_LOG_FILE)
    new_row.to_csv(STATUS_LOG_FILE, mode='a', header=header, index=False, encoding="utf-8-sig")


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
