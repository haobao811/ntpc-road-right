import time
import ocr
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlencode
from dataclasses import dataclass, field

BASE_URL = "https://service.ntpc.gov.tw"


@dataclass
class Attachment:
    title: str
    file_name: str
    download_url: str
    description: str = ""


@dataclass
class CaseInfo:
    case_no: str = ""
    status: str = ""
    organ: str = ""
    officer: str = ""
    contact: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


def parse_case_query_result(html_content: str) -> CaseInfo:
    """解析案件進度查詢回傳的 HTML 內容，並封裝成 CaseInfo 資料模型"""
    soup = BeautifulSoup(html_content, "html.parser")
    result_dict = {}
    attachments = []

    items = soup.find_all("li")
    for item in items:
        title_div = item.find("div", class_="title")
        right_box = item.find("div", class_="right-box")

        if title_div and right_box:
            title = (
                title_div.get_text(strip=True)
                .replace("：", "")
                .replace("\xa0", "")
                .strip()
            )
            if not title or title == "&nbsp;":
                continue

            # 檢查步驟條
            step_box = right_box.find("ul", class_="step-box")
            if step_box:
                steps = [li.get_text(strip=True) for li in step_box.find_all("li")]
                value = " -> ".join(steps)
            else:
                # 檢查下載按鈕
                button = right_box.find("button")
                if button and button.has_attr("ng-click"):
                    ng_click_val = button["ng-click"]
                    if "downloadFile" in ng_click_val:
                        try:
                            params = [
                                p.strip().strip("'\"")
                                for p in ng_click_val.split("(")[1]
                                .rstrip(")")
                                .split(",")
                            ]
                            if len(params) >= 4:
                                query_params = {
                                    "caseNo": params[0],
                                    "caseQueryCd": params[1],
                                    "fileId": params[2],
                                    "fileName": params[3],
                                }
                                download_url = f"{BASE_URL}/service/downloadfromQuery.action?{urlencode(query_params)}"

                                btn_text = button.get_text(strip=True)
                                full_text = right_box.get_text(strip=True).replace(
                                    btn_text, ""
                                )
                                file_desc = " ".join(full_text.split())

                                attachments.append(
                                    Attachment(
                                        title=title,
                                        file_name=params[3],
                                        download_url=download_url,
                                        description=file_desc,
                                    )
                                )
                                value = download_url
                        except Exception:
                            pass
                if not button or "downloadFile" not in ng_click_val:
                    value = " ".join(right_box.get_text(strip=True).split())

            if title in result_dict:
                if not isinstance(result_dict[title], list):
                    result_dict[title] = [result_dict[title]]
                result_dict[title].append(value)
            else:
                result_dict[title] = value

    # 處理辦理狀態（取得最新一筆）
    status_raw = result_dict.get("辦理狀態")
    if isinstance(status_raw, list):
        status_val = status_raw[-1]
    else:
        status_val = status_raw or ""

    return CaseInfo(
        case_no=result_dict.get("案件編號", ""),
        status=status_val,
        organ=result_dict.get("主辦機關", ""),
        officer=result_dict.get("承辦人員", ""),
        contact=result_dict.get("聯絡資訊", ""),
        attachments=attachments,
        raw_data=result_dict,
    )


def query_case_with_local_ocr(
    case_no: str, query_code: str, max_retries: int = 10
):
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
            " like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": f"{BASE_URL}/service/",
        "Origin": BASE_URL,
    })

    print("🌐 正在初始化連線...")
    for attempt in range(1, max_retries + 1):
        print(f"🔄 嘗試第 {attempt} 次 API 查詢與本地 AI 驗證碼辨識...")

        # 1. 取得驗證碼圖片
        current_mills = int(time.time() * 1000)
        captcha_url = f"{BASE_URL}/service/GetImageQuery?len=5&reload={current_mills}"
        res_captcha = session.get(captcha_url)

        if res_captcha.status_code != 200:
            print("❌ 無法取得驗證碼圖片，稍後重試...")
            time.sleep(1)
            continue

        # 2. 透過本地 AI (ddddocr) 辨識
        code_text, elapsed = ocr.solve(res_captcha.content)

        print(f"🤖 本地 AI 辨識結果: [{code_text}] (耗時 {elapsed:.3f} 秒)")

        if not code_text:
            print("⚠️ 辨識結果為空，重新取得...")
            continue

        # 3. 組合 POST 參數送出查詢
        post_url = f"{BASE_URL}/service/CaseQuery.action"
        payload = {
            "caseNo": case_no,
            "caseQueryCd": query_code,
            "caseQueryVerifyCd": code_text,
        }

        response = session.post(post_url, data=payload)

        if response.status_code == 200:
            res_text = response.text

            if (
                "驗證碼已逾時,請更新後重新輸入!" in res_text
                or "驗證碼錯誤" in res_text
            ):
                print("❌ 驗證碼錯誤或逾時，重新嘗試...")
                continue

            soup = BeautifulSoup(res_text, "html.parser")
            alert_div = soup.find("div", class_="alert")
            if alert_div:
                print(f"⚠️ 系統回應：{alert_div.text.strip()}")
                return res_text

            if soup.find_all("li"):
                print("✅ 查詢成功！")
                return res_text

            print("⚠️ 未知的回應內容，重試中...")
        else:
            print(f"❌ 請求失敗，HTTP 狀態碼: {response.status_code}")

        time.sleep(1)

    print("❌ 已達到最大重試次數。")
    return None


if __name__ == "__main__":
    res = query_case_with_local_ocr("J260831-4664", "BNUv")
    if res:
        parsed_data = parse_case_query_result(res)

        print("\n--- 解析結果 ---")

        print(f"案件編號: {parsed_data.case_no}")
        print(f"辦理狀態: {parsed_data.status}")
        print(f"主辦機關: {parsed_data.organ}")
        print(f"承辦人員: {parsed_data.officer}")
        print(f"聯絡資訊: {parsed_data.contact}")
        for att in parsed_data.attachments:
            if att.title.startswith('附件檔案'):
                print(att.download_url, att.title)
