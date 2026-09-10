
import time
import ddddocr

ocr_detector = ddddocr.DdddOcr(show_ad=False)


def solve(image_bytes):
    """使用本地 AI (ddddocr) 進行驗證碼辨識"""
    start = time.time()
    # ddddocr 可以直接吃圖片的二進位資料 (bytes)
    code_text = ocr_detector.classification(image_bytes)
    elapsed = time.time() - start
    return code_text.strip(), elapsed
