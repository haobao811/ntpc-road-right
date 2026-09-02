"""
gis.py - 地理資訊查詢模組
"""

import io
import logging
import xml.etree.ElementTree as ET
from functools import lru_cache

import pandas as pd
import requests
import urllib3
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)
from urllib3.exceptions import InsecureRequestWarning

# 隱藏 InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)


logger = logging.getLogger(__name__)

# 使用全域 Session 提升 HTTP 請求效能
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

# 新北市村里長 Open Data 下載網址
NTPC_CHIEF_CSV_URL = (
    "https://data.ntpc.gov.tw/api/datasets/d0070ef9-ae5c-423f-9f5c-e22aef21c33b/csv"
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, TimeoutError)),
    reraise=True,
)
def get_coordinates_by_address(address: str) -> tuple[float, float]:
    """地址正查經緯度 (ArcGIS Geocoding API)

    :param address: 查詢地址
    :return: (latitude, longitude)
    """
    arcgis_url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    address = f"臺灣{address}"
    params = {
        "SingleLine": address,
        "f": "json",
        "outSR": '{"wkid":4326}',
        "maxLocations": 1,
    }

    try:
        response = HTTP_SESSION.get(arcgis_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("ArcGIS 地址定位請求失敗 [%s]: %s", address, e)
        raise

    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"查無此地址對應的經緯度座標: {address}")

    location = candidates[0].get("location", {})
    if "y" not in location or "x" not in location:
        raise ValueError(f"ArcGIS 回傳座標格式錯誤: {location}")

    return location["y"], location["x"]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, TimeoutError)),
    reraise=True,
)
def get_village_by_address(address: str) -> dict[str, str | float]:
    """給定地址，先透過 ArcGIS 轉座標，再呼叫 NLSC 反查縣市、鄉鎮區、村里名稱

    :param address: 查詢地址
    :return: 包含縣市/鄉鎮/村里與座標的字典
    """
    lat, lng = get_coordinates_by_address(address)

    nlsc_url = f"https://api.nlsc.gov.tw/other/TownVillagePointQuery/{lng}/{lat}/4326"
    try:
        response = HTTP_SESSION.get(nlsc_url, timeout=15)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        cty_name = root.findtext("ctyName") or ""
        town_name = root.findtext("townName") or ""
        village_name = root.findtext("villageName") or ""
    except Exception as e:
        logger.warning("NLSC 門牌點位查詢失敗 (%s, %s): %s", lat, lng, e)
        cty_name, town_name, village_name = "", "", ""

    return {
        "address": address,
        "longitude": lng,
        "latitude": lat,
        "county": cty_name,
        "town": town_name,
        "village": village_name,
    }


@lru_cache(maxsize=1)
def _load_village_chief_dataframe() -> pd.DataFrame:
    """快取載入新北市村里長清單，避免每次查詢都重複下載 20,000 筆 CSV"""
    logger.info("初次下載新北市村里長 Open Data CSV 資料...")
    params = {"page": 0, "size": 20000}
    response = HTTP_SESSION.get(NTPC_CHIEF_CSV_URL, params=params, timeout=20)
    response.raise_for_status()

    # 讀取 CSV 並統一為 string 型別
    df = pd.read_csv(io.StringIO(response.text), dtype=str)
    # 填充 NaN 避免模糊比對或字串操作出錯
    df.fillna("", inplace=True)
    return df


def get_village_chief_info(
    village_name: str, district_name: str | None = None
) -> dict[str, str]:
    """查詢村里長聯絡資訊 (附帶快取機制)

    :param village_name: 里名稱 (例如: "中和里")
    :param district_name: 行政區名稱 (例如: "中和區")
    :return: 里長聯絡資訊字典
    """
    if not village_name:
        return {}

    try:
        df = _load_village_chief_dataframe()
    except Exception as e:
        logger.error("無法取得村里長 Open Data: %s", e)
        return {}

    # 進行村里篩選
    filtered_df = df[df["village"] == village_name]

    if district_name and not filtered_df.empty:
        filtered_df = filtered_df[
            filtered_df["address"].str.contains(district_name, na=False)
        ]

    if filtered_df.empty:
        logger.warning(
            "查無 %s (%s) 之里長資訊", village_name, district_name or "未指定區"
        )
        return {}

    row = filtered_df.iloc[0]
    return {
        "village": row.get("village", ""),
        "chief_name": row.get("name", ""),
        "address": row.get("address", ""),
        "tel": row.get("tel", ""),
        "mobile": row.get("mobile", ""),
    }
