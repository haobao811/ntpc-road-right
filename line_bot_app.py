"""
新北市路權申請 LINE Bot 整合主程式 (LIFF 網頁時間選擇版 + v3 正確引用版)
"""

import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.messaging.models import ButtonsTemplate, TemplateMessage, URIAction
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import (
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    StickerMessageContent,
    TextMessageContent,
)

# 導入你原本寫好的核心模組
from bot import AutoFillForm
from history_logger import (
    LOG_CSV_FILE,
    append_status_log,
    get_latest_statuses_for_batch,
    save_application_log,
)
from models import ApplicationResultRecord, DynamicApplyInfo
from search import parse_case_query_result, query_case_with_local_ocr

app = Flask(__name__)

# 請確保在這裡直接填入或透過環境變數帶入正確的 LINE Channel 金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "你的_Channel_Access_Token")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "你的_Channel_Secret")
NGROK_BASE_URL = os.getenv("NGROK_BASE_URL", "https://你的ngrok網址.ngrok-free.app")

# === v3 初始化 ===
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 記憶體暫存區：以 user_id 為 Key，暫存檔案路徑與解析出的地址
user_session_data = {}


# 0. 根目錄預設入口
@app.route("/")
def index():
    return """
    <h2>新北市路權申請 LINE Bot 服務運行中 🚀</h2>
    <p>這是一個 LINE 機器人後端伺服器。</p>
    <ul>
        <li>如需填寫路權時間表單，請透過 LINE 聊天室中的按鈕開啟 LIFF。</li>
        <li>直接測試表單頁面可前往：<a href="/liff-form">/liff-form</a></li>
    </ul>
    """


# 1. 提供 LIFF 網頁給 LINE 內嵌瀏覽器開啟
@app.route("/liff-form")
def liff_form():
    return render_template("liff_form.html")


# 2. 接收 LIFF 網頁傳過來的時間區間資料
@app.route("/api/liff-submit", methods=["POST"])
def api_liff_submit():
    data = request.json
    user_id = data.get("userId")
    time_range = data.get("timeRange")

    if user_id not in user_session_data or not user_session_data[user_id].get("image_path"):
        return jsonify({"success": False, "message": "找不到對應的檔案或地址，可能已被清除或過期，請重新傳送檔案！"})

    try:
        start_str, end_str = time_range.split("~")
        start_dt = datetime.strptime(start_str.strip(), "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end_str.strip(), "%Y-%m-%d %H:%M")

        if end_dt <= start_dt:
            return jsonify({"success": False, "message": "結束時間必須大於開始時間！"})

        duration = end_dt - start_dt
        duration_hours = int(duration.total_seconds() / 3600)

        session = user_session_data[user_id]
        address = session["address"]
        image_path = session["image_path"]

        # 1. 成功觸發後立刻清除暫存
        del user_session_data[user_id]

        # 2. 啟動背景執行緒跑 Selenium
        t = threading.Thread(
            target=run_selenium_background_task, args=(user_id, address, start_dt, duration, image_path)
        )
        t.start()

        # 3. 組合帶有詳細資訊的提示文字，並透過 LINE 主動推送
        summary_text = (
            f"⏳ 已經收到您的申請設定！系統正在背景自動執行新北市路權申請。\n\n"
            f"📍 施工地址：{address}\n"
            f"🕒 開始時間：{start_dt.strftime('%Y-%m-%d %H:%M')}\n"
            f"⏱️ 施工時長：{duration_hours} 小時\n"
            f"🏁 結束時間：{end_dt.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"💡 完成後會立刻發送結果通知給您，請稍候！"
        )

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=summary_text)]))

        return jsonify({"success": True, "message": "已收到請求，正在背景處理中"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/api/get-address", methods=["GET"])
def api_get_address():
    user_id = request.args.get("userId")
    if user_id in user_session_data and "address" in user_session_data[user_id]:
        return jsonify({"success": True, "address": user_session_data[user_id]["address"]})
    return jsonify({"success": False, "address": "尚未設定或已過期"})


# 3. 接收使用者傳送的「檔案」或「圖片」
@handler.add(MessageEvent, message=(ImageMessageContent, FileMessageContent))
def handle_file_message(event):
    user_id = event.source.user_id
    message_id = event.message.id

    try:
        original_filename = getattr(event.message, "file_name", "")
        stem_name = Path(original_filename).stem
        clean_addr = re.sub(r"\(\d+\)$", "", stem_name).strip()

        # 處理沒有檔案地址（或圖片未帶檔名）的情況
        if not clean_addr:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="⚠️ 無法識別檔名中的地址！若為檔案請確保檔名包含施工地址。")],
                    )
                )
            return

        if user_id in user_session_data:
            old_path = user_session_data[user_id].get("image_path")
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApiBlob(api_client)
            message_content = line_bot_api.get_message_content(message_id)

        temp_dir = tempfile.mkdtemp(prefix="road_")
        original_filename = getattr(event.message, "file_name", "1.pdf")
        temp_file_path = str(Path(temp_dir) / original_filename)
        print("暫存檔案:", temp_file_path)
        if isinstance(message_content, bytes):
            with open(temp_file_path, "wb") as f:
                f.write(message_content)
        else:
            with open(temp_file_path, "wb") as f:
                for chunk in message_content.iter_content():
                    f.write(chunk)

        stem_name = Path(original_filename).stem
        clean_addr = re.sub(r"\(\d+\)$", "", stem_name).strip()

        user_session_data[user_id] = {"image_path": temp_file_path, "address": clean_addr}

        liff_url = f"{NGROK_BASE_URL.rstrip('/')}/liff-form"

        buttons_template = ButtonsTemplate(
            title="📁 檔案接收成功！",
            text=f"📍 自動對應地址：\n{clean_addr}\n\n如需更換檔案直接重新傳送即可。\n請點擊下方按鈕設定時間：",
            actions=[URIAction(label="選擇填表時間", uri=liff_url)],
        )

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TemplateMessage(alt_text="請點擊按鈕選擇填表時間", template=buttons_template)],
                )
            )
    except Exception as e:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token, messages=[TextMessage(text=f"處理檔案時發生錯誤：{str(e)}")]
                )
            )


# 4-1. 接收並處理貼圖訊息
@handler.add(MessageEvent, message=StickerMessageContent)
def handle_sticker_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text="收到您的貼圖！不過目前主要任務是接收路權圖檔與設定時間哦。\n(請傳送圖檔，或隨時輸入「取消」)"
                    )
                ],
            )
        )


# 4-2. 接收一般文字訊息
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip().lower()

    # 1. 處理取消指令
    if text in ["取消", "重來", "reset", "clear"]:
        if user_id in user_session_data:
            old_path = user_session_data[user_id].get("image_path")
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
            del user_session_data[user_id]

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="🔄 已清除先前的暫存資料。請重新傳送正確的路權圖檔！")],
                )
            )
        return

    # 2. 新增：處理查詢歷史紀錄指令
    if text in ["查詢", "紀錄", "歷史", "history", "log"]:
        my_history_url = f"{NGROK_BASE_URL.rstrip('/')}/history-view?userId={user_id}"

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"📋 請點擊以下連結查看您的歷史申請與即時審核進度：\n{my_history_url}")],
                )
            )
        return

    if text in ["日曆", "行事曆", "calendar"]:
        my_calendar_url = f"{NGROK_BASE_URL.rstrip('/')}/calendar-view?userId={user_id}"

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"📅 請點擊以下連結查看您的施工排程行事曆：\n{my_calendar_url}")],
                )
            )
        return

    # 3. 原有的檔案狀態判斷
    if user_id not in user_session_data or not user_session_data[user_id].get("image_path"):
        reply_text = "⚠️ 請先傳送路權圖檔（檔名設為完整地址）！\n(輸入〈查詢〉可查看歷史紀錄，輸入〈日曆〉可查看施工行事曆，輸入〈取消〉可重來)"
    else:
        reply_text = (
            "👉 系統已有您的待辦檔案。請點擊上方按鈕選擇填表時間！\n(若傳錯檔案，可直接重新傳送新檔案或輸入〈取消〉)"
        )

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
        )


def run_selenium_background_task(user_id, address, start_dt: datetime, duration, image_path):
    """背景執行 Selenium 自動化填表核心"""
    try:
        apply_info = DynamicApplyInfo(
            address=address, start_datetime=start_dt, duration=duration, road_right_image=image_path
        )

        bot = AutoFillForm()
        success = bot.auto_fill_form(apply_info, ask_user=False)

        # 假設 bot 執行成功且已經抓取到完成結果
        if success and hasattr(bot, "completion_result"):
            res = bot.completion_result
            end_dt = start_dt + duration

            # 建立實例化 Model
            record = ApplicationResultRecord(
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id=user_id,
                address=address,
                start_time=start_dt.strftime("%Y-%m-%d %H:%M"),
                end_time=end_dt.strftime("%Y-%m-%d %H:%M"),
                case_no=res.get("case_no", "未知"),
                query_code=res.get("query_code", "未知"),
            )

            # 寫入 CSV 紀錄
            save_application_log(record)

            # 組合成功訊息推送給使用者
            duration_hours = int(duration.total_seconds() / 3600)
            success_text = (
                f"🎉 【申辦成功】您的新北市路權申請表單已自動填寫完畢！\n\n"
                f"📍 施工地址：{address}\n"
                f"🕒 開始時間：{start_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"⏱️ 施工時長：{duration_hours} 小時\n"
                f"🏁 結束時間：{end_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"📌 案件編號：{record.case_no}\n"
                f"🔑 查詢碼：{record.query_code}\n"
            )

            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=success_text)]))
        else:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id, messages=[TextMessage(text="⚠️ 自動填表流程結束，但未確認到完成畫面。")]
                    )
                )
        bot.close()

    except Exception as e:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id, messages=[TextMessage(text=f"❌ 執行 Selenium 自動填表時發生例外錯誤：\n{e})")]
                )
            )
    finally:
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except:
                pass


# 5. 新增：歷史申請與即時審核結果查詢頁面
@app.route("/history-view")
def history_view():
    return render_template("history.html")


def get_or_fetch_case_status(row):
    """
    檢查 status_history.csv 是否已有狀態；
    若無或需即時更新，則透過本地 OCR 與政府 API 查詢，並寫入 status_history.csv。
    """
    case_no = str(row["案件編號"])
    query_code = str(row.get("查詢碼", ""))

    # 先試著從 status_history.csv 抓取最新狀態
    latest_map = get_latest_statuses_for_batch([case_no])
    if case_no in latest_map and latest_map[case_no]:
        return latest_map[case_no]

    # 如果 status log 裡面沒有，且有合法的案件編號與查詢碼，則即時爬蟲查詢
    status_text = "查詢中..."
    if case_no and case_no != "未知" and query_code and query_code != "未知":
        html_res = query_case_with_local_ocr(case_no, query_code, max_retries=3)
        if html_res:
            case_info = parse_case_query_result(html_res)
            status_text = case_info.status or "未知狀態"
            # 寫入狀態歷史日誌
            append_status_log(case_no, status_text)
        else:
            status_text = "查詢失敗"
    else:
        status_text = "無效編號"

    return status_text


@app.route("/api/history-more", methods=["GET"])
def api_history_more():
    user_id = request.args.get("userId")
    search_query = request.args.get("search", "").strip()
    offset = int(request.args.get("offset", 0))
    limit = 5

    if not os.path.exists(LOG_CSV_FILE):
        return jsonify({"success": True, "records": [], "has_more": False})

    try:
        df = pd.read_csv(LOG_CSV_FILE, encoding="utf-8-sig")

        # 支援依施工地址進行關鍵字模糊過濾
        if search_query:
            clean_query = search_query.replace(" ", "")
            df = df[
                df["施工地址"].str.contains(search_query, na=False) | 
                df["案件編號"].astype(str).str.replace(r"\s+", "", regex=True).str.endswith(clean_query)
            ]

        reversed_df = df.iloc[::-1].reset_index(drop=True)

        batch_df = reversed_df.iloc[offset : offset + limit]
        has_more = (offset + limit) < len(reversed_df)

        if batch_df.empty:
            return jsonify({"success": True, "records": [], "has_more": False})

        unique_user_ids = batch_df["使用者ID"].dropna().astype(str).unique() if "使用者ID" in batch_df.columns else []
        user_name_map = {}
        if unique_user_ids:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                for uid in unique_user_ids:
                    if uid and uid != "未知" and uid != "nan":
                        try:
                            profile = line_bot_api.get_profile(uid)
                            user_name_map[uid] = profile.display_name
                        except:
                            user_name_map[uid] = "未知用戶"
                    else:
                        user_name_map[uid] = "未知用戶"

        records = []
        for _, row in batch_df.iterrows():
            row_uid = str(row.get("使用者ID", ""))
            records.append(
                {
                    "timestamp": row["時間"],
                    "address": row["施工地址"],
                    "start_time": row["開始時間"],
                    "end_time": row["結束時間"],
                    "case_no": str(row["案件編號"]),
                    "query_code": str(row.get("查詢碼", "")),
                    "user_name": user_name_map.get(row_uid, "未知用戶"),
                }
            )

        return jsonify({"success": True, "records": records, "has_more": has_more})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/fetch-case-detail", methods=["POST"])
def api_fetch_case_detail():
    data = request.get_json() or {}
    case_no = data.get("caseNo")
    query_code = data.get("queryCode")

    if not case_no or not query_code or case_no == "未知" or query_code == "未知":
        return jsonify({"success": False, "message": "無效的案件編號或查詢碼"})

    try:
        html_res = query_case_with_local_ocr(case_no, query_code, max_retries=3)
        if not html_res:
            return jsonify({"success": False, "message": "無法連線至政府查詢系統"})

        case_info = parse_case_query_result(html_res)
        status_text = case_info.status or "未知狀態"

        # 同步寫入狀態日誌
        append_status_log(case_no, status_text)

        attachments = [
            {"file_name": att.file_name, "download_url": att.download_url, "description": att.description}
            for att in case_info.attachments
            if att.title.startswith("附件檔案")
        ]

        return jsonify(
            {
                "success": True,
                "status": status_text,
                "organ": case_info.organ or "-",
                "officer": case_info.officer or "-",
                "contact": case_info.contact or "",
                "attachments": attachments,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/calendar-view")
def calendar_view():
    user_id = request.args.get("userId", "")
    return render_template("calendar.html", user_id=user_id)


@app.route("/api/calendar-events", methods=["GET"])
def api_calendar_events():
    if not os.path.exists(LOG_CSV_FILE):
        return jsonify([])

    try:
        df = pd.read_csv(LOG_CSV_FILE, encoding="utf-8-sig")
        if df.empty:
            return jsonify([])

        # 1. 取得 FullCalendar 自動傳入的顯示區間
        start_range = request.args.get("start")
        end_range = request.args.get("end")

        if start_range and end_range:
            # 確保 DataFrame 的欄位是標準 datetime64[ns]，避免微秒/奈秒精度衝突
            df["開始時間"] = pd.to_datetime(df["開始時間"], errors="coerce").astype("datetime64[ns]")

            # 將傳入的範圍轉為相同型態
            start_dt = pd.to_datetime(start_range).strftime("%Y-%m-%d")
            end_dt = pd.to_datetime(end_range).strftime("%Y-%m-%d")

            # 進行比較
            df = df[(df["開始時間"] >= start_dt) & (df["開始時間"] <= end_dt)]

        if df.empty:
            return jsonify([])

        # 2. 批次取得現有狀態快取
        case_nos = df["案件編號"].dropna().astype(str).tolist()
        latest_status_map = get_latest_statuses_for_batch(case_nos)

        events = []
        for _, row in df.iterrows():
            case_no = str(row["案件編號"])
            status = latest_status_map.get(case_no)

            start_str = pd.to_datetime(row["開始時間"]).strftime("%Y-%m-%d %H:%M")
            end_str = str(row["結束時間"])

            # 3. 判斷邏輯：如果沒有狀態，或是狀態中「沒有已結案」，就標記需要非同步重新即時查看 (needsFetch: True)
            if not status:
                status = "點擊更新"
                color = "#6c757d"  # 灰色
                needs_fetch = True
            elif "已結案" not in status:
                # 雖然快取有狀態，但因為尚未結案，需標記讓前端在背景重新即時查看一遍
                color = "#ffc107"  # 黃色
                needs_fetch = True
            else:
                # 已經結案的案件不需要重新爬蟲
                color = "#198754"  # 綠色
                needs_fetch = False

            events.append(
                {
                    "title": f"{row['施工地址']} ({status})",
                    "start": start_str,
                    "end": end_str,
                    "backgroundColor": color,
                    "borderColor": color,
                    "extendedProps": {"caseNo": case_no, "organ": row.get("organ", "-"), "needsFetch": needs_fetch},
                }
            )

        return jsonify(events)
    except Exception as e:
        print(f"❌ 讀取行事曆事件失敗: {e}")
        return jsonify([])


@app.route("/api/fetch-case-status", methods=["POST"])
def api_fetch_case_status():
    """專門提供前端非同步動態載入與更新單一案件狀態用"""
    data = request.json or {}
    case_no = data.get("caseNo")
    if not case_no:
        return jsonify({"success": False, "message": "缺少案件編號"})

    try:
        if not os.path.exists(LOG_CSV_FILE):
            return jsonify({"success": False, "message": "找不到記錄檔"})

        df = pd.read_csv(LOG_CSV_FILE, encoding="utf-8-sig")
        matched = df[df["案件編號"].astype(str) == str(case_no)]
        if matched.empty:
            return jsonify({"success": False, "message": "找不到對應案件"})

        row = matched.iloc[0]
        query_code = str(row.get("查詢碼", ""))
        address = row.get("施工地址", "")

        status_text = "查詢失敗"
        if query_code and query_code != "未知":
            html_res = query_case_with_local_ocr(case_no, query_code, max_retries=3)
            if html_res:
                case_info = parse_case_query_result(html_res)
                status_text = case_info.status or "未知狀態"
                append_status_log(case_no, status_text)

        color = "#198754" if "已結案" in status_text else "#ffc107"
        return jsonify({"success": True, "status": status_text, "color": color, "title": f"{address} ({status_text})"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


if __name__ == "__main__":
    app.run(port=5000, debug=True)
