"""
資料結構模型與地址標準化邏輯
"""

import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

from gis import get_village_by_address, get_village_chief_info


@dataclass
class StaticUserInfo:
    name: str
    id: str
    mobile: str
    email: str
    town: str
    address: str
    reason: str
    resident: str
    id_image: str = str(Path("./id.jpg").absolute())


class DynamicApplyInfo:
    def __init__(
        self,
        address: str,
        start_datetime: datetime,
        duration: timedelta,
        road_right_image: str,
    ):
        self.address = address
        # 同時支援並對齊兩種命名習慣，避免屬性不一致導致爬蟲讀取錯誤
        self.start_datetime = start_datetime
        self.start_time = start_datetime
        self.end_time = start_datetime + duration
        self.duration = duration
        self.image_path = road_right_image

        # 爬蟲自動解析欄位
        self.town = ""
        self.village = ""
        self.chief_name = ""
        self.address = self.normalize(self.address)
        village_info = get_village_by_address(self.address)

        self.town = village_info.get("town", "")
        self.village = village_info.get("village", "")

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
        self.chief_name = chief_info.get("chief_name") or ""

    def to_dict(self):
        return {'address': self.address, 'start_datetime': str(self.start_datetime), 'end_time': str(self.end_time)}

    def normalize(self, raw_address: str) -> str:
        s = unicodedata.normalize("NFKC", raw_address)
        s = self._to_half_width(s)
        s = s.replace("臺", "臺")
        s = s.replace("－", "-").replace("—", "-").replace(r"–", "-").replace("〜", "~")
        s = re.sub(r"\s*[,，:]+\s*", " ", s)
        return re.sub(r"\s+", "", s).strip()

    def _to_half_width(self, input_str: str) -> str:
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


@dataclass
class ApplicationResultRecord:
    timestamp: str
    user_id: str
    address: str
    start_time: str
    end_time: str
    case_no: str
    query_code: str

    def to_dict(self):
        return asdict(self)
