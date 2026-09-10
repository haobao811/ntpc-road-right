"""
新北市路權申請 LINE Bot 整合主程式 (LIFF 網頁時間選擇版 + v3 正確引用版)
"""

import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

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
from history_logger import get_user_application_history, save_application_log
from models import ApplicationResultRecord, DynamicApplyInfo

app = Flask(__name__)

# 請確保在這裡直接填入或透過環境變數帶入正確的 LINE Channel 金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "你的_Channel_Access_Token")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "你的_Channel_Secret")

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

        suffix = ".pdf" if isinstance(event.message, FileMessageContent) else ".jpg"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
            if isinstance(message_content, bytes):
                tf.write(message_content)
            else:
                for chunk in message_content.iter_content():
                    tf.write(chunk)
            temp_file_path = tf.name

        original_filename = getattr(event.message, "file_name", "新北市板橋區中山路一段1號.jpg")
        stem_name = Path(original_filename).stem
        clean_addr = re.sub(r"\(\d+\)$", "", stem_name).strip()

        user_session_data[user_id] = {"image_path": temp_file_path, "address": clean_addr}

        liff_url = os.getenv("LIFF_URL", "https://你的ngrok網址.ngrok-free.app/liff-form")

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
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"處理檔案時發生錯誤：{str(e)}")])
            )


# 4-1. 接收並處理貼圖訊息
@handler.add(MessageEvent, message=StickerMessageContent)
def handle_sticker_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="收到您的貼圖！不過目前主要任務是接收路權圖檔與設定時間哦。\n(請傳送圖檔，或隨時輸入「取消」)")],
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
        history_text = get_user_application_history(user_id=None)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=history_text)],
                )
            )
        return

    # 3. 原有的檔案狀態判斷
    if user_id not in user_session_data or not user_session_data[user_id].get("image_path"):
        reply_text = "⚠️ 請先傳送路權圖檔（檔名設為完整地址）！\n(輸入「查詢」可查看歷史紀錄，輸入「取消」可重來)"
    else:
        reply_text = "👉 系統已有您的待辦檔案。請點擊上方按鈕選擇填表時間！\n(若傳錯檔案，可直接重新傳送新檔案或輸入「取消」)"

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
                    PushMessageRequest(to=user_id, messages=[TextMessage(text="⚠️ 自動填表流程結束，但未確認到完成畫面。")])
                )
        bot.close()

    except Exception as e:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=f"❌ 執行 Selenium 自動填表時發生例外錯誤：\n{e})")])
            )
    finally:
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except:
                pass


if __name__ == "__main__":
    app.run(port=5000, debug=True)
