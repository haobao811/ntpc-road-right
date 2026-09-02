"""
Selenium 自動化表單填寫與 Tesseract OCR 驗證碼處理模組
"""

import io
import logging
import time
from datetime import date, datetime, timedelta

import pytesseract
from bs4 import BeautifulSoup
from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from config import STATIC_USER_INFO
from haishan import get_police_precinct
from models import DynamicApplyInfo

logger = logging.getLogger(__name__)


class AutoFillForm:
    def __init__(self):
        self.driver = webdriver.Chrome()
        self.wait = WebDriverWait(self.driver, 15)

    def auto_fill_form(self, apply_info: DynamicApplyInfo):
        try:
            self.driver.get("https://service.ntpc.gov.tw/eservice/Ac125024.action")
            self.driver.maximize_window()

            self.wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
            self.wait.until(EC.element_to_be_clickable((By.ID, "chkAgree"))).click()
            self.driver.find_element(By.ID, "btnAgree").click()

            input_value = STATIC_USER_INFO

            self.wait.until(EC.visibility_of_element_located((By.NAME, "applNameP"))).send_keys(input_value.name)
            self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//input[@type='radio' and @id='sex' and @value='F']"))
            ).click()
            self.wait.until(EC.element_to_be_clickable((By.NAME, "idNum"))).send_keys(input_value.id)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applMobile"))).send_keys(input_value.mobile)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applEmail"))).send_keys(input_value.email)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applTown"))).send_keys(input_value.town)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applAddr"))).send_keys(input_value.address)

            self.wait.until(EC.element_to_be_clickable((By.XPATH, "//label[@for='applyType']"))).click()
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applyReason"))).send_keys(input_value.reason)

            town_select = Select(self.wait.until(EC.presence_of_element_located((By.ID, "applyRoadTown"))))
            town_select.select_by_visible_text(apply_info.town)

            self.wait.until(EC.element_to_be_clickable((By.NAME, "applyRoadAddr"))).send_keys(apply_info.short_address)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applyRoadNumStr"))).send_keys(apply_info.road_number)
            self.wait.until(EC.element_to_be_clickable((By.NAME, "applyRoadNumEnd"))).send_keys(apply_info.road_number)

            if apply_info.town == "板橋區":
                station_select = Select(self.wait.until(EC.element_to_be_clickable((By.ID, "policeStationsData"))))
                station_select.select_by_visible_text(get_police_precinct(apply_info.village))

            self.wait.until(EC.element_to_be_clickable((By.NAME, "applyStrDt"))).send_keys(
                apply_info.start_datetime.strftime("%Y-%m-%d")
            )
            Select(self.wait.until(EC.presence_of_element_located((By.ID, "asdHour")))).select_by_visible_text(
                f"{apply_info.start_datetime.hour:02d}"
            )
            Select(self.wait.until(EC.presence_of_element_located((By.ID, "asdMinute")))).select_by_visible_text(
                f"{apply_info.start_datetime.minute:02d}"
            )

            end_datetime = apply_info.start_datetime + apply_info.duration
            self.wait.until(EC.presence_of_element_located((By.ID, "applyEndDt"))).send_keys(
                end_datetime.strftime("%Y-%m-%d")
            )
            Select(self.wait.until(EC.presence_of_element_located((By.ID, "aedHour")))).select_by_visible_text(
                f"{end_datetime.hour:02d}"
            )
            Select(self.wait.until(EC.presence_of_element_located((By.ID, "aedMinute")))).select_by_visible_text(
                f"{end_datetime.minute:02d}"
            )

            self.wait.until(EC.presence_of_element_located((By.ID, "villageName"))).send_keys(apply_info.chief_name)
            today = date.today()
            self.wait.until(EC.presence_of_element_located((By.ID, "villageNotifyDt"))).send_keys(
                (today - timedelta(days=1)).strftime("%Y-%m-%d")
            )
            self.wait.until(EC.presence_of_element_located((By.ID, "residendtName"))).send_keys(input_value.resident)
            self.wait.until(EC.presence_of_element_located((By.ID, "residendtNotifyDt"))).send_keys(
                (today - timedelta(days=1)).strftime("%Y-%m-%d")
            )

            self.upload_file(0, apply_info.image_path)
            self.upload_file(1, input_value.id_image)

            self.solve_and_fill_captcha()
            self.click_save()
            self.retry_on_captcha_error()

            self.flashing_submit_button()
            self.scroll_to_table()

            self.driver.execute_script("window.alert('自動填入完成，請確認資料正確後點選下一步！');")
            self._wait_for_user_to_dismiss_alert()
            if self.parse_completion_and_log():
                self.close()
                return True
            return False

        except Exception as e:
            logger.error(f"填表失敗: {e}")
            raise e

    def scroll_to_table(self):
        logger.info("滾動到確認資訊")
        target_element = self.wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="acSnapShot"]/div[3]')))

        # 2. 透過 JavaScript 將該元素滾動到視窗中央（block: 'center' 可以確保上下都有足夠空間看清楚）
        self.driver.execute_script(
            """
            arguments[0].scrollIntoView({
                behavior: 'smooth',
                block: 'center',
                inline: 'nearest'
            });
        """,
            target_element,
        )

    def flashing_submit_button(self):
        logger.info("閃爍按鈕")
        next_btn = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[@data-ng-click='doSend(ac125024);']"))
        )

        # 2. 定義具備最高權重與強制覆蓋的 CSS
        flash_css = """
        @keyframes forcePulse {
            0% { transform: scale(1); filter: brightness(1); }
            50% { transform: scale(1.15); filter: brightness(1.3); outline: 4px solid #f44336 !important; }
            100% { transform: scale(1); filter: brightness(1); }
        }
        .selenium-flashing-force {
            animation: forcePulse 0.5s ease-in-out infinite !important;
            transform-origin: center center !important;
            z-index: 999999 !important;
        }
        """

        # 3. 確保樣式有被確實注入
        self.driver.execute_script(
            f"""
            if (!document.getElementById('selenium-flash-style')) {{
                let style = document.createElement('style');
                style.id = 'selenium-flash-style';
                style.innerHTML = `{flash_css}`;
                document.head.appendChild(style);
            }}
        """
        )

        # 4. 加上 class
        self.driver.execute_script("arguments[0].classList.add('selenium-flashing-force');", next_btn)

    def click_save(self):
        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-ng-click='doSave(ac125024);']"))).click()

    def solve_and_fill_captcha(self, refresh: bool = False):
        img_element = self.wait.until(EC.visibility_of_element_located((By.ID, "authImage")))
        if refresh:
            refresh_element = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, 'reloadCaseQuery')]"))
            )
            refresh_element.click()
            time.sleep(1)
            img_element = self.wait.until(EC.visibility_of_element_located((By.ID, "authImage")))

        img = Image.open(io.BytesIO(img_element.screenshot_as_png)).convert("L")
        img = img.point(lambda p: 255 if p > 150 else 0)
        code_text = pytesseract.image_to_string(img, config=r"--psm 8 -c tessedit_char_whitelist=0123456789").strip()
        logger.info("辨識到的驗證碼為: %s", code_text)

        auth_input = self.wait.until(EC.presence_of_element_located((By.ID, "atuh_gCode")))
        auth_input.clear()
        time.sleep(0.2)
        logger.info("輸入驗證碼")
        auth_input.send_keys(code_text or "1111")

    def retry_on_captcha_error(self, max_retries: int = 20):
        for attempt in range(max_retries):
            if not self._captcha_error_popup_present():
                return True
            logger.warning("偵測到驗證碼錯誤，重新驗證 (%s/%s)", attempt + 1, max_retries)
            self.solve_and_fill_captcha(refresh=True)
            self.click_save()
        logger.error("驗證碼連續錯誤，已達重試上限")

    def _captcha_error_popup_present(self, timeout: float = 1.0) -> bool:
        """縮短檢查時間，若驗證碼正確能更快進入下一步"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                alert = self.driver.switch_to.alert
                text = alert.text or ""
                logger.info("偵測到彈出視窗: %s", text)
                is_error = "驗證碼錯誤" in text
                alert.accept()
                time.sleep(0.2)
                return is_error
            except NoAlertPresentException:
                pass

            for el in self.driver.find_elements(By.XPATH, "//*[contains(normalize-space(.), '驗證碼錯誤')]"):
                try:
                    if el.is_displayed():
                        logger.info("偵測到驗證碼錯誤彈出視窗")
                        self._dismiss_captcha_error_modal()
                        return True
                except Exception:
                    continue
            time.sleep(0.1)  # 加快輪詢頻率
        return False

    def _dismiss_captcha_error_modal(self):
        for xpath in (
            "//button[normalize-space()='確定']",
            "//button[normalize-space()='關閉']",
            "//button[normalize-space()='OK']",
            "//*[contains(@class,'modal')]//button",
        ):
            for btn in self.driver.find_elements(By.XPATH, xpath):
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    time.sleep(0.3)
                    return

    def upload_file(self, index: int, path: str):
        btn = self.driver.find_element(By.XPATH, f"(//input[@value='上傳檔案'])[{index + 1}]")
        js = """
        var btn = arguments[0]; var idx = arguments[1];
        var inputs = document.querySelectorAll('input[type="file"]');
        inputs.forEach(i => i.click = function(){});
        angular.element(btn).scope().$apply(function(s){ s.FF(idx); });
        """
        self.driver.execute_script(js, btn, index)
        file_inp = self.driver.find_element(By.XPATH, f"(//input[@type='file' and @id='testF1'])[{index + 1}]")
        self.driver.execute_script("arguments[0].style.display = 'block';", file_inp)
        file_inp.send_keys(path)

    def close(self):
        """關閉瀏覽器驅動"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("瀏覽器已成功關閉。")
            except Exception as e:
                logger.error(f"關閉瀏覽器時發生錯誤: {e}")

    def _wait_for_user_to_dismiss_alert(self, timeout: int = 60 * 60):
        """等待使用者手動關閉瀏覽器的 alert 視窗"""
        logger.info("等待使用者手動關閉彈出視窗...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 試著抓取 alert，如果存在代表使用者還沒關閉
                _ = self.driver.switch_to.alert
                time.sleep(0.5)
            except NoAlertPresentException:
                # 抓不到 alert，代表使用者已經手動點掉或關閉了
                logger.info("使用者已手動關閉彈出視窗。")
                return True
        logger.warning("等待使用者關閉彈出視窗超時。")
        return False

    def parse_completion_and_log(self, log_file="application_success.log", timeout: int = 60 * 60 * 24):
        """等待完成畫面出現，透過 BeautifulSoup 解析申請日期、案件編號、案件查詢碼並寫入 Log"""
        try:
            logger.info("等待使用者完成最後步驟與跳出完成畫面...")
            long_wait = WebDriverWait(self.driver, timeout)
            _ = long_wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "completed-massage")))

            html_content = self.driver.page_source
            soup = BeautifulSoup(html_content, "html.parser")
            completed_div = soup.find("div", class_="completed-massage")

            result_data = {}
            if completed_div:
                items = completed_div.find_all("li", class_="list-group-item")
                for item in items:
                    title_div = item.find("div", class_="title")
                    value_div = item.find("div", class_="right-box")
                    if title_div and value_div:
                        title = title_div.text.strip().replace("：", "")
                        value = value_div.text.strip()
                        result_data[title] = value

            app_date = result_data.get("申請日期", "未知")
            case_no = result_data.get("案件編號", "未知")
            query_code = result_data.get("案件查詢碼", "未知")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_message = f"[{timestamp}] 申辦成功！ | " f"申請日期: {app_date} | " f"案件編號: {case_no} | " f"案件查詢碼: {query_code}"

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_message + "\n")

            logger.info(f"已成功紀錄至本地日誌: {log_message}")
            return True
        except Exception as e:
            logger.error(f"解析完成畫面或寫入 Log 失敗: {e}")
            return False
