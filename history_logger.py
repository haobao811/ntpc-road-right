import os
import pandas as pd
from models import ApplicationResultRecord

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


def get_user_application_history(user_id=None, limit=5):
    """讀取 CSV 並回傳特定使用者的最近幾筆申請紀錄"""
    if not os.path.exists(LOG_CSV_FILE) or os.path.getsize(LOG_CSV_FILE) == 0:
        return "📁 目前尚無任何申請歷史紀錄。"

    try:
        df = pd.read_csv(LOG_CSV_FILE, encoding="utf-8-sig")

        # 確保欄位存在且型別正確
        if "使用者ID" not in df.columns:
            return "📁 紀錄檔格式錯誤。"

        # 過濾出該使用者的資料
        user_df = df if user_id is None else df[df["使用者ID"] == user_id]

        if user_df.empty:
            return "🔍 找不到過往的申請紀錄。"

        # 取最後 N 筆（最新的在最上面，利用 tail 後反轉）
        recent_df = user_df.tail(limit).iloc[::-1]

        total_count = len(user_df)
        msg_lines = [f"📋 您的最近申請紀錄（共 {total_count} 筆）：\n"]

        for i, (_, row) in enumerate(recent_df.iterrows(), 1):
            msg_lines.append(
                f"--- 紀錄 {i} ---\n"
                f"📍 地址：{row['施工地址']}\n"
                f"🕒 時間：{row['開始時間']} ~ {row['結束時間']}\n"
                f"📌 案件編號：{row['案件編號']}\n"
                f"🔑 查詢碼：{row['查詢碼']}\n"
                f"⏱️ 申辦時間：{row['時間']}\n"
            )

        return "\n".join(msg_lines)

    except Exception as e:
        return f"⚠️ 讀取歷史紀錄發生錯誤：{e}"
