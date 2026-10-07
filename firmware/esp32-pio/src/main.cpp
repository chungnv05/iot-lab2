#include <Arduino.h>
/*
 * BÀI THỰC HÀNH SỐ 2 - THU THẬP, LƯU TRỮ VÀ TIỀN XỬ LÝ DỮ LIỆU IoT
 * Firmware nút cảm biến ESP32 (mô phỏng Wokwi, kế thừa mạch Bài 1)
 *
 * Cảm biến : DHT22 (nhiệt độ, độ ẩm) - HC-SR04 (khoảng cách) - LDR (ánh sáng)
 * Giao thức: MQTT (PubSubClient) tới broker công cộng, payload JSON
 * Topic    : ptit/iot/<GROUP_ID>/<DEVICE_ID>/telemetry   (dữ liệu đo)
 *            ptit/iot/<GROUP_ID>/<DEVICE_ID>/status      (online/offline, retained + LWT)
 *            ptit/iot/<GROUP_ID>/<DEVICE_ID>/ping -> pong (gateway đo RTT)
 *
 * Payload mẫu:
 * {"device_id":"esp32-01","seq":12,"ts":1791350475123,"uptime_s":60,
 *  "temperature":25.10,"humidity":50.20,"distance_cm":100.88,"light_lux":503.2,"rssi":-70}
 *   - seq : số thứ tự tăng dần  -> phát hiện mất gói / trùng gói
 *   - ts  : thời điểm đo (epoch milliseconds, đồng bộ NTP) -> đo độ trễ end-to-end
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>       // "DHT sensor library for ESPx" (Wokwi khuyến nghị cho ESP32)
#include <time.h>
#include <sys/time.h>
#include <math.h>

// ======================= CẤU HÌNH =======================
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* MQTT_SERVER   = "broker.hivemq.com";   // broker công cộng, không cần tài khoản
const int   MQTT_PORT     = 1883;
const char* GROUP_ID      = "nhom15";              // ĐỔI cho khớp với MQTT_GROUP trong file .env
const char* DEVICE_ID     = "esp32-01";
const unsigned long SEND_INTERVAL_MS = 5000;       // chu kỳ gửi 5 s (DHT22 cần >= 2 s)

// Bật = 1 để cố ý sinh dữ liệu lỗi (spike, thiếu giá trị, giá trị vô lý, gói trùng)
// giúp kiểm chứng bước validate & tiền xử lý. Tắt = 0 khi muốn dữ liệu "sạch".
#define FAULT_INJECTION 1

// ======================= CHÂN =======================
const int DHT_PIN  = 15;   // DHT22 SDA -> GPIO15 (giống Bài 1)
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int LDR_PIN  = 34;   // AO của module quang trở (ADC1, chỉ đọc)
const int LED_PIN  = 2;

WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp       dht;

char topicTelemetry[96];
char topicStatus[96];
char topicPing[96];      // gateway gửi ping -> thiết bị trả pong ngay để đo RTT (độ trễ mạng 2 chiều)
char topicPong[96];
unsigned long lastSend = 0;
unsigned long seqNo = 0;
bool timeSynced = false;

// ---------------------------------------------------------
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  Serial.print("Connecting WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print(" connected, IP=");
  Serial.println(WiFi.localIP());
}

// Đồng bộ đồng hồ qua NTP (UTC) để gắn timestamp chính xác cho mỗi bản tin
void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  Serial.print("Sync NTP");
  unsigned long start = millis();
  while (time(nullptr) < 1700000000 && millis() - start < 15000) {
    delay(300);
    Serial.print(".");
  }
  timeSynced = time(nullptr) >= 1700000000;
  Serial.println(timeSynced ? " OK" : " FAILED (gateway sẽ dùng thời gian nhận)");
}

uint64_t epochMillis() {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)(tv.tv_usec / 1000);
}

// Nhận ping từ gateway -> trả nguyên nội dung về topic pong (đo round-trip time)
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, topicPing) != 0 || length > 120) return;
  char buf[128];
  memcpy(buf, payload, length);   // copy vì payload trỏ vào buffer dùng chung của PubSubClient
  buf[length] = 0;
  mqttClient.publish(topicPong, buf);
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    String clientId = String("ESP32-Bai2-") + DEVICE_ID + "-" + String((uint32_t)esp_random(), HEX);
    Serial.print("Connecting MQTT...");
    // Last Will: nếu thiết bị rớt mạng đột ngột, broker tự publish "offline"
    if (mqttClient.connect(clientId.c_str(), NULL, NULL, topicStatus, 1, true, "offline")) {
      Serial.println(" connected");
      mqttClient.publish(topicStatus, "online", true);
      mqttClient.subscribe(topicPing);
    } else {
      Serial.printf(" failed, rc=%d. Retry in 2 s\n", mqttClient.state());
      delay(2000);
    }
  }
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return NAN;
  return duration * 0.0343f / 2.0f;
}

// Quy đổi ADC -> lux theo mô hình quang trở của Wokwi (GAMMA=0.7, RL10=50 kΩ)
float readLux() {
  const float GAMMA = 0.7f, RL10 = 50.0f;
  int raw = analogRead(LDR_PIN);                 // 0..4095
  float voltage = raw / 4095.0f * 3.3f;
  if (voltage >= 3.29f) return 0.0f;             // tránh chia cho 0 (rất tối)
  float resistance = 2000.0f * voltage / (1.0f - voltage / 3.3f);
  if (resistance <= 0) return 100000.0f;         // rất sáng
  return powf(RL10 * 1e3f * powf(10.0f, GAMMA) / resistance, 1.0f / GAMMA);
}

// Ghi số thực vào JSON, NaN -> null
void fmtNum(char* out, size_t n, float v) {
  if (isfinite(v)) snprintf(out, n, "%.2f", v);
  else snprintf(out, n, "null");
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  analogReadResolution(12);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  delay(dht.getMinimumSamplingPeriod());
  TempAndHumidity t0 = dht.getTempAndHumidity();
  Serial.printf("DHT test: T=%.2f H=%.2f status=%s\n", t0.temperature, t0.humidity, dht.getStatusString());
  randomSeed(esp_random());

  snprintf(topicTelemetry, sizeof(topicTelemetry), "ptit/iot/%s/%s/telemetry", GROUP_ID, DEVICE_ID);
  snprintf(topicStatus, sizeof(topicStatus), "ptit/iot/%s/%s/status", GROUP_ID, DEVICE_ID);
  snprintf(topicPing, sizeof(topicPing), "ptit/iot/%s/%s/ping", GROUP_ID, DEVICE_ID);
  snprintf(topicPong, sizeof(topicPong), "ptit/iot/%s/%s/pong", GROUP_ID, DEVICE_ID);

  connectWiFi();
  syncTime();
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(512);
  mqttClient.setCallback(onMqttMessage);
  // Mô phỏng có thể chạy chậm hơn thời gian thực (15-40%) -> tăng keep-alive để broker
  // không ngắt kết nối giữa 2 lần publish
  mqttClient.setKeepAlive(90);
  mqttClient.setSocketTimeout(30);
  Serial.printf("Publish topic: %s\n", topicTelemetry);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastSend < SEND_INTERVAL_MS) return;
  lastSend = now;

  TempAndHumidity th = dht.getTempAndHumidity();
  if (dht.getStatus() != DHTesp::ERROR_NONE) {          // thử lại 1 lần khi đọc lỗi
    Serial.printf("DHT error: %s -> retry\n", dht.getStatusString());
    delay(dht.getMinimumSamplingPeriod());
    th = dht.getTempAndHumidity();
  }
  float temperature = th.temperature;
  float humidity    = th.humidity;
  float distance    = readDistanceCm();
  float lux         = readLux();
  bool  sendDuplicate = false;

#if FAULT_INJECTION
  long r = random(100);
  if (r < 4)       temperature += (random(2) ? 15.0f : -15.0f);  // 4%: spike (outlier)
  else if (r < 7)  humidity = NAN;                               // 3%: thiếu giá trị -> null
  else if (r < 9)  temperature = 999.0f;                         // 2%: giá trị vô lý -> gateway loại
  else if (r < 11) sendDuplicate = true;                         // 2%: gửi trùng gói
#endif

  // Nếu tất cả cảm biến đều lỗi thì bỏ qua chu kỳ này
  if (!isfinite(temperature) && !isfinite(humidity) && !isfinite(distance)) {
    Serial.println("All sensors invalid - skip publish");
    return;
  }

  seqNo++;
  char sT[16], sH[16], sD[16], sL[16];
  fmtNum(sT, sizeof(sT), temperature);
  fmtNum(sH, sizeof(sH), humidity);
  fmtNum(sD, sizeof(sD), distance);
  fmtNum(sL, sizeof(sL), lux);
  unsigned long long ts = timeSynced ? (unsigned long long)epochMillis() : 0ULL;

  char payload[320];
  snprintf(payload, sizeof(payload),
           "{\"device_id\":\"%s\",\"seq\":%lu,\"ts\":%llu,\"uptime_s\":%lu,"
           "\"temperature\":%s,\"humidity\":%s,\"distance_cm\":%s,\"light_lux\":%s,\"rssi\":%d}",
           DEVICE_ID, seqNo, ts, millis() / 1000, sT, sH, sD, sL, WiFi.RSSI());

  bool ok = mqttClient.publish(topicTelemetry, payload);
  if (sendDuplicate) mqttClient.publish(topicTelemetry, payload);
  Serial.printf("%s | publish=%s%s\n", payload, ok ? "OK" : "FAILED", sendDuplicate ? " (dup)" : "");

  digitalWrite(LED_PIN, HIGH);
  delay(50);
  digitalWrite(LED_PIN, LOW);
}
