"""
系統配置檔與前端 HTML 範本
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from models import StaticUserInfo

load_dotenv()

HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Leaflet Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        html, body, #map { height: 100%; margin: 0; padding: 0; }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var pyBridge = null;
        var marker = null;
        var map = null;
        var isMapClickable = true; // 控制地圖點擊標點的開關

        // 供 PyQt 控制點擊開關的函式
        function setMapClickable(clickable) {
            isMapClickable = clickable;
        }

        document.addEventListener("DOMContentLoaded", function() {
            if (typeof qt !== 'undefined') {
                new QWebChannel(qt.webChannelTransport, function(channel) {
                    pyBridge = channel.objects.pyBridge;
                });
            }

            map = L.map('map').setView([24.9989, 121.5135], 16);

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '© OpenStreetMap'
            }).addTo(map);

            marker = L.marker([24.9989, 121.5135]).addTo(map);

            map.on('click', function(e) {
                // 當處於鎖定狀態（非步驟 1）時，忽略點擊事件
                if (!isMapClickable) {
                    return;
                }

                var lat = e.latlng.lat;
                var lng = e.latlng.lng;

                if (marker) {
                    marker.setLatLng(e.latlng);
                } else {
                    marker = L.marker(e.latlng).addTo(map);
                }

                if (pyBridge) {
                    pyBridge.on_map_clicked(lat, lng);
                }
            });

            map.on('contextmenu', function(e) {
                var lat = e.latlng.lat;
                var lng = e.latlng.lng;

                // 透過 QWebChannel 傳遞給 Python 端處理
                if (typeof pyBridge !== 'undefined' && pyBridge.open_street_view) {
                    pyBridge.open_street_view(lat, lng);
                } else {
                    // 備用方案：若 QWebChannel 尚未準備好，則直接在網頁端開啟
                    window.open('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=' + lat + ',' + lng, '_blank');
                }
            });
        });

        function setMapCenter(lat, lng) {
            if (map) {
                var latlng = L.latLng(lat, lng);
                map.panTo(latlng);
                if (marker) {
                    marker.setLatLng(latlng);
                } else {
                    marker = L.marker(latlng).addTo(map);
                }
            }
        }
    </script>
</body>
</html>
"""

LOC_COORDINATES = {
    "永和四號公園": (24.9989, 121.5135),
}

WEEKDAYS_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

name = os.getenv("USER_NAME")
id_num = os.getenv("USER_ID")
mobile = os.getenv("USER_MOBILE")
email = os.getenv("USER_EMAIL")
town = os.getenv("USER_TOWN")
address = os.getenv("USER_ADDRESS")
reason = os.getenv("USER_REASON")
resident = os.getenv("USER_RESIDENT")

if not all([name, id_num, mobile, email, town, address, reason, resident]):
    raise ValueError("偵測到環境變數中缺少必要的個人資料設定，請檢查 .env 檔案！")

STATIC_USER_INFO = StaticUserInfo(
    name=name,
    id=id_num,
    mobile=mobile,
    email=email,
    town=town,
    address=address,
    reason=reason,
    resident=resident,
)
ID_IMAGE = str(Path("./id.jpg").absolute())
if not os.path.isfile(ID_IMAGE):
    raise FileNotFoundError(f"找不到身分證圖片檔案，請確認該檔案是否存在於: {ID_IMAGE}")
