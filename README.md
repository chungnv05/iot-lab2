# Bài thực hành số 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

Môn: IoT và Ứng dụng (INT14149)

Bài này dùng lại nút cảm biến ESP32 mô phỏng trên Wokwi ở Bài 1 (DHT22 + HC-SR04, thêm cảm biến ánh sáng LDR) và dựng một pipeline hoàn chỉnh:

```
 ┌──────────── Wokwi ────────────┐        ┌──────────── Máy tính (gateway) ─────────────────────────────┐
 │ DHT22 ─┐                      │  MQTT  │                                                             │
 │ HC-SR04├─► ESP32 ─► JSON ─────┼──────► │ broker.hivemq.com ─► collector.py ─► InfluxDB  iot_raw      │
 │ LDR ───┘   (NTP timestamp)    │ :1883  │   (subscribe)        validate,        │                     │
 └───────────────────────────────┘        │                      dedupe, đệm      ▼                     │
                                          │                                 preprocess.py ─► iot_processed
                                          │                                       │                     │
                                          │                  Streamlit app (dashboard + cảnh báo) ◄─────┘
                                          └─────────────────────────────────────────────────────────────┘
```

| Thành phần | Công nghệ | Tệp |
|---|---|---|
| Thiết bị | ESP32 + DHT22 + HC-SR04 + LDR trên Wokwi | `firmware/wokwi/` |
| Broker | HiveMQ public broker (không cần cài) | – |
| Thu thập | Python, paho-mqtt | `gateway/collector.py`, `gateway/validator.py` |
| Lưu trữ | InfluxDB 2.x | `gateway/storage.py`, `scripts/setup_influxdb.py` |
| Tiền xử lý | pandas, numpy, scikit-learn | `preprocessing/` |
| Dashboard / App | Streamlit + Plotly | `app/dashboard.py` |
| Đo đạc | Độ trễ end-to-end, benchmark ghi/đọc | `tools/latency_report.py`, `tools/benchmark_storage.py` |
| Thiết bị giả lập | Thay Wokwi khi cần thử nhanh | `tools/simulator.py` |

---

## 0. Chạy nhanh bằng các file .bat (Windows)

| Thứ tự | File | Việc làm |
|---|---|---|
| 1 | `buoc1_cai_dat.bat` | Tạo `.venv`, cài thư viện Python, tải InfluxDB 2.9.1 vào `influxdb/` (chỉ làm 1 lần) |
| 2 | `buoc2_khoi_dong_influxdb.bat` | Chạy InfluxDB (127.0.0.1:8086), tạo org/token/bucket + retention |
| 2b | `buoc2b_xoa_du_lieu_cu.bat` | (tùy chọn) xóa dữ liệu thử nghiệm cũ |
| — | VS Code → `firmware/esp32-pio` → ✓ Build → F1 *Wokwi: Start Simulator* | Chạy ESP32 mô phỏng |
| 3 | `buoc3_chay_he_thong.bat` | Collector + tiền xử lý (lặp 60 s) + app http://localhost:8501 |
| 4 | `buoc4_do_dac.bat` | Sau 20–30 phút: số liệu độ trễ, benchmark, tiền xử lý → `output/` |
| 5 | `buoc5_app_va_so_lieu.bat` | Mở lại app + tính lại số liệu trên 3 giờ dữ liệu gần nhất |
| — | `dung_tat_ca.bat` | Tắt collector, tiền xử lý, app, InfluxDB |

> Sơ đồ Wokwi dùng board **`board-esp32-devkit-c-v4`** (tên chân `esp:15`, `esp:5V`, `esp:GND.1`…) giống Bài 1. Board `esp32-devkit-v1` cho kết quả DHT22 `TIMEOUT` trong Wokwi VS Code.

## 1. Cài đặt thủ công (Windows, làm một lần)

### 1.1. Python
Cần Python 3.10 trở lên. Mở thư mục dự án và chạy **`setup_windows.bat`** (bấm đúp). Script sẽ tạo `.venv`, cài thư viện và tạo file `.env`.

Nếu muốn làm thủ công:
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Mở `.env` và đổi **`MQTT_GROUP`** thành một tên riêng của nhóm, ví dụ `nhom15`. Vì broker là công cộng, tên này giúp dữ liệu không bị lẫn với nhóm khác.

### 1.2. InfluxDB 2.x (không cần Docker)
1. Tải bản Windows: <https://download.influxdata.com/influxdb/releases/influxdb2-2.9.1-windows_amd64.zip>
2. Giải nén (ví dụ vào `C:\influxdb`), rồi chạy `influxd.exe`. **Giữ cửa sổ này mở** trong suốt quá trình làm bài. Nếu Windows Firewall hỏi, chọn *Allow* cho Private network.
3. Ở cửa sổ dự án (đã `activate`), chạy:
   ```bat
   python -m scripts.setup_influxdb
   ```
   Script sẽ tự làm các việc sau:
   - tạo user `admin` / `admin12345`, org `ptit` và ghi token vào `.env`;
   - tạo 2 bucket kèm retention policy: `iot_raw` giữ 7 ngày, `iot_processed` giữ 30 ngày.

   Bạn có thể xem giao diện web của InfluxDB tại <http://localhost:8086>.

> Đã có Docker? Bạn có thể dùng `docker run -p 8086:8086 influxdb:2.7` thay cho bước 2, các bước còn lại giữ nguyên.

---

## 2. Chạy hệ thống

### Bước 1 – Thiết bị trên Wokwi
1. Vào <https://wokwi.com>, chọn *New Project* → *ESP32*.
2. Dán nội dung `firmware/wokwi/sketch.ino` vào `sketch.ino`, và `firmware/wokwi/diagram.json` vào `diagram.json`.
3. Tab *Library Manager*: thêm **DHT sensor library for ESPx** và **PubSubClient**, hoặc tạo file `libraries.txt` giống trong thư mục.
4. Trong `sketch.ino`, sửa `GROUP_ID` cho **trùng với `MQTT_GROUP`** trong `.env`.
5. Bấm ▶ để chạy. Serial Monitor phải hiện lần lượt `WiFi connected`, `Sync NTP OK`, `MQTT connected`, sau đó cứ 5 giây in ra một bản tin JSON.

> `FAULT_INJECTION 1` trong sketch sẽ cố ý tạo dữ liệu lỗi: spike, thiếu giá trị, giá trị vô lý, gói trùng. Mục đích là để bước validate và tiền xử lý có việc để làm. Đặt `0` nếu muốn dữ liệu sạch.

### Bước 1 (cách khác) – Chạy ESP32 mô phỏng ngay trong VS Code
Thư mục `firmware/esp32-pio/` là một dự án PlatformIO, dùng cùng mã với `sketch.ino` và đã có sẵn `wokwi.toml` + `diagram.json`.
1. Cài 2 tiện ích VS Code: **PlatformIO IDE** và **Wokwi Simulator**.
2. *File → Open Folder…* → chọn **`firmware/esp32-pio`** (mở riêng thư mục này để PlatformIO nhận dự án).
3. Lần đầu: `F1` → **Wokwi: Request a New License** → đăng nhập Wokwi trên trình duyệt → license được nạp tự động (miễn phí).
4. Build: bấm ✓ (*PlatformIO: Build*) ở thanh dưới cùng. Lần đầu sẽ tải toolchain ESP32 (vài trăm MB, mất vài phút).
5. `F1` → **Wokwi: Start Simulator**. Mạch hiện ra trong tab mới, Serial Monitor ở panel Terminal. Bấm vào DHT22 / HC-SR04 / LDR để đổi giá trị.
6. Sửa code trong `src/main.cpp` → build lại → simulator tự nạp firmware mới.

### Bước 2 – Collector, tiền xử lý và app
Bấm đúp **`start_all.bat`**. Script mở 3 cửa sổ:

| Cửa sổ | Lệnh tương đương |
|---|---|
| COLLECTOR | `python -m gateway.collector` |
| PREPROCESS | `python -m preprocessing.preprocess --start 1h --loop 60` |
| APP | `streamlit run app/dashboard.py` → trình duyệt mở <http://localhost:8501> |

App có 4 tab:
- **Real-time**: giá trị mới nhất, trạng thái online/offline, biểu đồ từng cảm biến, cảnh báo vượt ngưỡng (chỉnh ngưỡng ở sidebar).
- **Đã tiền xử lý**: so sánh dữ liệu thô với dữ liệu đã làm sạch/resample, rolling mean, delta, chuẩn hóa; đánh dấu outlier bằng ✖ đỏ.
- **Độ trễ & chất lượng**: độ trễ E2E (trung bình/P50/P95/max), phân rã độ trễ theo từng chặng, số gói mất/trùng/bị loại.
- **Lưu trữ**: số điểm dữ liệu và dung lượng của từng bucket, ước tính dung lượng theo retention.

### Chạy thử không cần Wokwi và InfluxDB
Bấm đúp **`start_demo.bat`**. Thiết bị giả lập sẽ gửi qua broker thật, collector ghi ra file `logs/dryrun_points.jsonl`, và app đọc dữ liệu từ file đó.

---

## 3. Đo đạc và lấy số liệu cho báo cáo

```bat
python -m tools.latency_report --minutes 60      :: bảng độ trễ + output/latency_hist.png
python -m tools.benchmark_storage --points 5000  :: thông lượng ghi theo batch, thời gian truy vấn
python -m preprocessing.preprocess --start 1h    :: output/report_<device>.json (thống kê từng bước)
python -m tools.simulator --devices 5 --interval 1   :: thử tải nhiều thiết bị
python -m pytest -q                               :: kiểm thử đơn vị (10 test)
```

**Lưu ý về đo độ trễ:** độ trễ mạng được tính bằng `thời điểm gateway nhận − ts` (ts do ESP32 lấy qua NTP). Vì vậy đồng hồ của máy tính cần được đồng bộ trước khi đo: *Settings → Time & language → Sync now*. Nếu đồng hồ lệch, app sẽ cảnh báo khi thấy độ trễ âm.

---

## 4. Thiết kế dữ liệu

### 4.1. Topic & payload MQTT
```
ptit/iot/<group>/<device_id>/telemetry   QoS0 (PubSubClient), QoS1 phía gateway
ptit/iot/<group>/<device_id>/status      "online"/"offline", retained, dùng làm Last Will
```
```json
{"device_id":"esp32-01","seq":12,"ts":1791350475123,"uptime_s":60,
 "temperature":25.10,"humidity":50.20,"distance_cm":100.88,"light_lux":503.2,"rssi":-70}
```
- `seq` dùng để phát hiện mất gói, trùng gói và thiết bị khởi động lại.
- `ts` là thời điểm đo (epoch ms, lấy qua NTP).
- Giá trị lỗi được gửi là `null`.

### 4.2. Schema InfluxDB

| Bucket (retention) | Measurement | Tags | Fields |
|---|---|---|---|
| `iot_raw` (7 ngày) | `sensor_raw` | device_id, group, ts_source | temperature, humidity, distance_cm, light_lux, rssi (float); seq, uptime_s (int); net_latency_ms, proc_latency_ms |
| | `quality_event` | device_id, kind (gap / duplicate / restart / field_dropped / status_*) | count, lost, seq, detail |
| | `rejected` | device_id, kind (lý do) | count |
| | `pipeline_metrics` | device_id, kind | net_ms, proc_ms, write_ms, e2e_ms, seq |
| `iot_processed` (30 ngày) | `sensor_processed` | device_id, rule, outlier_method | giá trị sạch, `*_rollmean`, `*_rollstd`, `*_delta`, `*_minmax`, `*_z`, `*_imputed`, dew_point, n_samples, n_outliers |

Lý do thiết kế:
- **Tag** chỉ dùng cho giá trị ít thay đổi, cần để lọc/nhóm (device_id), nhờ vậy số series thấp.
- **Field** dùng cho số đo.
- `time` là thời điểm đo trên thiết bị, không phải thời điểm nhận.

### 4.3. Xử lý trùng lặp và mất mát
| Vấn đề | Cách xử lý |
|---|---|
| Gói trùng (QoS1 gửi lại, firmware gửi 2 lần) | `SequenceTracker` bỏ các seq đã thấy; ngoài ra InfluxDB tự ghi đè điểm có cùng series + timestamp (idempotent) |
| Mất gói | Phát hiện qua khoảng nhảy của seq, ghi `quality_event kind=gap, lost=n` |
| Thiết bị khởi động lại | seq và uptime cùng giảm, ghi `kind=restart` |
| InfluxDB tạm ngừng | Collector đệm dữ liệu trong RAM và `logs/buffer.jsonl`, tự ghi bù khi DB chạy lại |
| Thiết bị rớt mạng | MQTT Last Will publish `offline`; app hiện 🔴 khi quá 30 giây không có bản tin mới |
| Dữ liệu sai | Validator loại bản tin hỏng (`rejected`), bỏ riêng trường ngoài miền đo (`field_dropped`) |

### 4.4. Các bước tiền xử lý (`preprocessing/pipeline.py`)
1. **Làm sạch**: sắp xếp theo thời gian, bỏ timestamp trùng, ép kiểu số, loại giá trị ngoài miền đo vật lý.
2. **Outlier**: dùng IQR (k = 1,5) hoặc Z-score (|z| > 3) trên **cửa sổ trượt 15 mẫu**, có độ phân tán tối thiểu cho từng đại lượng. Nhờ vậy, việc kéo thanh trượt Wokwi (thay đổi theo bậc) không bị coi là outlier. Outlier được thay bằng NaN.
3. **Resampling**: tính trung bình theo cửa sổ (mặc định 30 s, tham số `--rule`), kèm `n_samples` và `n_outliers`.
4. **Giá trị thiếu**: nội suy tuyến tính theo thời gian, tối đa 3 cửa sổ liên tiếp. Khoảng trống dài hơn (thiết bị offline) sẽ bị bỏ, không tự điền dữ liệu giả.
5. **Đặc trưng**: rolling mean, rolling std, delta, điểm sương (dew point).
6. **Chuẩn hóa**: Min-Max (0–1) và Z-score bằng scikit-learn; tham số scaler được lưu trong `report_*.json`.
7. Ghi kết quả vào `iot_processed/sensor_processed` và `output/processed_<device>.csv`.

---

## 5. Cấu trúc thư mục
```
firmware/wokwi/      sketch.ino, diagram.json, libraries.txt   (dán lên wokwi.com)
firmware/esp32-pio/  platformio.ini, src/main.cpp, wokwi.toml, diagram.json   (chạy trong VS Code)
gateway/             collector.py, validator.py, storage.py
preprocessing/       pipeline.py (thuật toán), preprocess.py (CLI đọc/ghi InfluxDB)
app/                 dashboard.py (Streamlit), data.py (truy vấn)
tools/               simulator.py, latency_report.py, benchmark_storage.py
scripts/             setup_influxdb.py
tests/               test_validator.py, test_pipeline.py
config.py, .env.example, requirements.txt, *.bat
```

## 6. Lỗi thường gặp
| Hiện tượng | Cách xử lý |
|---|---|
| VS Code: "firmware not found" khi Start Simulator | Chưa build - bấm ✓ PlatformIO Build trước |
| VS Code: Wokwi đòi license | `F1` → Wokwi: Request a New License |
| Collector không nhận được gì | Kiểm tra `GROUP_ID` (sketch) có trùng `MQTT_GROUP` (.env) không; Serial Monitor Wokwi có báo `publish=OK` không |
| `MQTT failed rc=-2` trên Wokwi | Broker công cộng tạm lỗi, đợi rồi thử lại, hoặc đổi sang `broker.emqx.io` (đổi ở cả sketch và .env) |
| `Thiếu INFLUX_TOKEN` | Chạy lại `python -m scripts.setup_influxdb` |
| `401 unauthorized` | Token sai: tạo All Access token trong UI InfluxDB rồi dán vào `.env` |
| Tab "Đã tiền xử lý" trống | Cửa sổ PREPROCESS chưa chạy, hoặc chưa đủ dữ liệu (cần ít nhất vài phút) |
| Độ trễ âm hoặc rất lớn | Đồng bộ lại đồng hồ Windows |

## 7. Bảo mật và hướng mở rộng
- Broker công cộng, cổng 1883, **không mã hóa**: ai biết topic cũng đọc hoặc giả mạo được dữ liệu. Cách làm này chỉ phù hợp cho bài thực hành. Khi triển khai thật nên dùng HiveMQ Cloud/EMQX với user/password và TLS (cổng 8883). Collector đã hỗ trợ sẵn: chỉ cần đặt `MQTT_PORT=8883` cùng user/pass; còn firmware thì phải dùng `WiFiClientSecure`.
- Không commit `.env` (chứa token) lên GitHub; file này đã được đưa vào `.gitignore`.
- Mở rộng thêm: edge processing (lọc trung bình trên ESP32 trước khi gửi), so sánh InfluxDB với MongoDB time-series.
