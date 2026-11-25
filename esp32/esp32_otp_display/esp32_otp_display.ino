/*
  =============================================================================
  IoT Smart Attendance System - ESP32 OTP LED Matrix Display
  =============================================================================
  Hardware:
    - ESP32 Development Board (ESP32-WROOM-32)
    - MAX7219 4-in-1 Dot Matrix Display Module (32x8 LEDs)

  Wiring:
    MAX7219 VCC  -->  ESP32 5V (VIN)
    MAX7219 GND  -->  ESP32 GND
    MAX7219 DIN  -->  ESP32 GPIO 23 (MOSI)
    MAX7219 CS   -->  ESP32 GPIO 5  (SS)
    MAX7219 CLK  -->  ESP32 GPIO 18 (SCK)

  Required Libraries (Install via Arduino Library Manager or PlatformIO):
    1. "MD_Parola" by majicDesigns
    2. "MD_MAX72XX" by majicDesigns
    3. "ArduinoJson" by Benoit Blanchon (v6 or v7)
    4. "WiFi" & "HTTPClient" (Built-in with ESP32 Arduino Core)
  =============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <MD_Parola.h>
#include <MD_MAX72XX.h>
#include <SPI.h>

// ----------------- Configuration Settings -----------------
// 1. WiFi Credentials
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// 2. Backend Server Endpoint
// Works with BOTH hosted HTTPS sites (Render / Railway / Cloudflare / ngrok)
// AND local LAN HTTP servers (e.g. http://192.168.1.50:8000/api/device/otp)
const char* SERVER_URL    = "https://your-app-name.onrender.com/api/device/otp";

// 3. Secret Device Authentication Key (Must match backend DEVICE_SECRET_KEY)
const char* DEVICE_KEY    = "esp32_classroom_key_98765";

// ----------------- Hardware Setup -----------------
#define HARDWARE_TYPE MD_MAX72XX::FC16_HW
#define MAX_DEVICES 4   // 4 linked 8x8 matrices = 32x8 total

#define CLK_PIN   18    // SCK
#define DATA_PIN  23    // MOSI
#define CS_PIN    5     // CS

// Hardware SPI instance (faster and smoother)
MD_Parola P = MD_Parola(HARDWARE_TYPE, CS_PIN, MAX_DEVICES);

// ----------------- State Variables -----------------
char currentOtp[10] = "------";
char displayBuffer[16] = "------";
unsigned long lastFetchTime = 0;
unsigned long otpExpiresInMs = 30000;
unsigned long otpReceivedAt = 0;
bool isFetching = false;

// Forward Declarations
void connectWiFi();
bool fetchOtpFromServer();
void updateDisplay();

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[SYSTEM] Initializing Smart Attendance ESP32 Display...");

  // Initialize LED Matrix Display
  P.begin();
  P.setIntensity(4); // Brightness 0 to 15
  P.displayClear();
  P.displayText("INIT", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
  P.displayAnimate();

  // Connect to WiFi
  connectWiFi();

  // Initial OTP fetch
  fetchOtpFromServer();
}

void loop() {
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Connection lost. Reconnecting...");
    P.displayText("RECONN", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
    P.displayAnimate();
    connectWiFi();
    return;
  }

  unsigned long currentMillis = millis();

  // Check if it is time to refresh OTP (fetch 2 seconds before theoretical expiry)
  unsigned long elapsedSinceFetch = currentMillis - otpReceivedAt;
  if (!isFetching && (elapsedSinceFetch >= (otpExpiresInMs - 2000) || strlen(currentOtp) < 4)) {
    Serial.println("[OTP] Token nearing expiration or missing. Refreshing...");
    fetchOtpFromServer();
  }

  // Update Matrix Animation & Timer indicator
  updateDisplay();
  delay(50);
}

void connectWiFi() {
  Serial.printf("[WIFI] Connecting to '%s'...\n", WIFI_SSID);
  P.displayText("WIFI...", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
  P.displayAnimate();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WIFI] Connected successfully!");
    Serial.printf("[WIFI] ESP32 IP Address: %s\n", WiFi.localIP().toString().c_str());
    
    // Show IP on serial and briefly flash OK
    P.displayText("READY", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
    P.displayAnimate();
    delay(1000);
  } else {
    Serial.println("\n[WIFI] Connection failed! Will retry in loop.");
    P.displayText("NO WIFI", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
    P.displayAnimate();
  }
}

bool fetchOtpFromServer() {
  isFetching = true;
  HTTPClient http;
  WiFiClientSecure secureClient;
  String urlStr = String(SERVER_URL);

  if (urlStr.startsWith("https://")) {
    // Allow HTTPS connections to hosted servers (Render / Railway / Cloudflare / ngrok)
    secureClient.setInsecure();
    http.begin(secureClient, SERVER_URL);
  } else {
    http.begin(SERVER_URL);
  }

  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(10000);

  // Prepare JSON Payload
  JsonDocument docRequest;
  docRequest["device_key"] = DEVICE_KEY;
  String requestBody;
  serializeJson(docRequest, requestBody);

  Serial.printf("[HTTP] POST %s\n", SERVER_URL);
  int httpResponseCode = http.POST(requestBody);

  if (httpResponseCode == 200) {
    String responseString = http.getString();
    Serial.printf("[HTTP] Response (200): %s\n", responseString.c_str());

    JsonDocument docResponse;
    DeserializationError error = deserializeJson(docResponse, responseString);

    if (!error) {
      const char* otp = docResponse["otp"];
      int expiresIn = docResponse["expires_in"] | 30;

      if (otp != nullptr && strlen(otp) >= 4) {
        strncpy(currentOtp, otp, sizeof(currentOtp) - 1);
        currentOtp[sizeof(currentOtp) - 1] = '\0';

        otpExpiresInMs = (unsigned long)expiresIn * 1000;
        otpReceivedAt = millis();

        Serial.printf("[OTP] New OTP Acquired: %s (Valid for %d s)\n", currentOtp, expiresIn);
        http.end();
        isFetching = false;
        return true;
      }
    } else {
      Serial.printf("[JSON] Deserialization error: %s\n", error.c_str());
    }
  } else {
    Serial.printf("[HTTP] Error on sending POST: %d\n", httpResponseCode);
    if (httpResponseCode == 401) {
      P.displayText("BAD KEY", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
    } else {
      P.displayText("SRV ERR", PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
    }
    P.displayAnimate();
    delay(1500);
  }

  http.end();
  isFetching = false;
  return false;
}

void updateDisplay() {
  unsigned long now = millis();
  unsigned long elapsed = now - otpReceivedAt;
  long remainingSec = (long)((otpExpiresInMs > elapsed) ? ((otpExpiresInMs - elapsed) / 1000) : 0);

  // Display format: 6-digit OTP centered
  // Example: "481920"
  snprintf(displayBuffer, sizeof(displayBuffer), "%s", currentOtp);

  // Update Parola text
  P.displayText(displayBuffer, PA_CENTER, 0, 0, PA_PRINT, PA_NO_EFFECT);
  P.displayAnimate();

  // Subtle visual heartbeat: blink the lower rightmost LED dot on the last matrix
  // to indicate active clock sync without cluttering the 6 digits
  bool dotState = ((now / 500) % 2) == 0;
  MD_MAX72XX* mx = P.getGraphicObject();
  if (mx != nullptr) {
    // Coordinate for bottom right LED (module 0, row 7, col 0 in Parola coordinates)
    mx->setPoint(7, 0, dotState);
  }
}
