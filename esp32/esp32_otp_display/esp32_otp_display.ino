#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <WiFi.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ===================== AUTO-CYCLE TIMER SETTING =====================
// Each mode runs for 45 seconds (45,000 milliseconds)
const unsigned long MODE_DURATION_MS = 45000UL;

// ===================== PIN DEFINITIONS (ESP32-S3) =====================
#define OLED_SDA      8
#define OLED_SCL      9
#define OLED_ADDR     0x3C
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64

#define NRF_CE_PIN    4
#define NRF_CSN_PIN   5
#define NRF_SCK_PIN   12
#define NRF_MOSI_PIN  11
#define NRF_MISO_PIN  13

// ===================== NRF24L01 REGISTERS & COMMANDS =====================
#define NRF_CMD_R_REGISTER    0x00
#define NRF_CMD_W_REGISTER    0x20
#define NRF_CMD_R_RX_PAYLOAD  0x61
#define NRF_CMD_FLUSH_RX      0xE2
#define NRF_CMD_NOP           0xFF

#define NRF_REG_CONFIG        0x00
#define NRF_REG_EN_AA         0x01
#define NRF_REG_EN_RXADDR     0x02
#define NRF_REG_SETUP_AW      0x03
#define NRF_REG_RF_CH         0x05
#define NRF_REG_RF_SETUP      0x06
#define NRF_REG_STATUS        0x07
#define NRF_REG_RPD           0x09
#define NRF_REG_RX_ADDR_P0    0x0A
#define NRF_REG_RX_PW_P0      0x11

// ===================== GLOBAL OBJECTS & BUFFERS =====================
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
SPIClass nrfSPI(FSPI);

const uint8_t NUM_CHANNELS = 126; // 2400 MHz to 2525 MHz
uint8_t signalLevel[NUM_CHANNELS];
uint8_t peakHold[NUM_CHANNELS];

// Waterfall history buffer: 44 rows x 16 bytes (128 bits per row)
#define WF_ROWS 44
uint8_t waterfallBuf[WF_ROWS][16];

// Modes: 0 = Spectrum, 1 = Waterfall, 2 = Wi-Fi + Rogue Audit, 3 = Air Sniffer
uint8_t currentMode = 0;
unsigned long modeStartMs = 0;

// Wi-Fi Audit Variables
int wifiApCount = 0;
int wifiChLoad[15] = {0};
int cleanestWifiCh = 1;
unsigned long lastWifiScanMs = 0;

// Sniffer Variables
uint8_t snifferCh = 40;
uint32_t packetsPerSec = 0;
uint32_t packetCounter = 0;
unsigned long lastSecMs = 0;
uint8_t lastPacketBytes[8] = {0};
bool hasPacketSample = false;

// ===================== LOW-LEVEL NRF24L01 SPI HELPERS =====================
uint8_t nrfReadReg(uint8_t reg) {
  nrfSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(NRF_CSN_PIN, LOW);
  nrfSPI.transfer(NRF_CMD_R_REGISTER | (reg & 0x1F));
  uint8_t val = nrfSPI.transfer(NRF_CMD_NOP);
  digitalWrite(NRF_CSN_PIN, HIGH);
  nrfSPI.endTransaction();
  return val;
}

void nrfWriteReg(uint8_t reg, uint8_t val) {
  nrfSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(NRF_CSN_PIN, LOW);
  nrfSPI.transfer(NRF_CMD_W_REGISTER | (reg & 0x1F));
  nrfSPI.transfer(val);
  digitalWrite(NRF_CSN_PIN, HIGH);
  nrfSPI.endTransaction();
}

void nrfWriteRegMulti(uint8_t reg, const uint8_t *buf, uint8_t len) {
  nrfSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(NRF_CSN_PIN, LOW);
  nrfSPI.transfer(NRF_CMD_W_REGISTER | (reg & 0x1F));
  for (uint8_t i = 0; i < len; i++) {
    nrfSPI.transfer(buf[i]);
  }
  digitalWrite(NRF_CSN_PIN, HIGH);
  nrfSPI.endTransaction();
}

void nrfReadPayload(uint8_t *buf, uint8_t len) {
  nrfSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(NRF_CSN_PIN, LOW);
  nrfSPI.transfer(NRF_CMD_R_RX_PAYLOAD);
  for (uint8_t i = 0; i < len; i++) {
    buf[i] = nrfSPI.transfer(NRF_CMD_NOP);
  }
  digitalWrite(NRF_CSN_PIN, HIGH);
  nrfSPI.endTransaction();
}

void nrfFlushRx() {
  nrfSPI.beginTransaction(SPISettings(8000000, MSBFIRST, SPI_MODE0));
  digitalWrite(NRF_CSN_PIN, LOW);
  nrfSPI.transfer(NRF_CMD_FLUSH_RX);
  digitalWrite(NRF_CSN_PIN, HIGH);
  nrfSPI.endTransaction();
}

bool initNRF24() {
  pinMode(NRF_CE_PIN, OUTPUT);
  pinMode(NRF_CSN_PIN, OUTPUT);
  digitalWrite(NRF_CE_PIN, LOW);
  digitalWrite(NRF_CSN_PIN, HIGH);

  nrfSPI.begin(NRF_SCK_PIN, NRF_MISO_PIN, NRF_MOSI_PIN, NRF_CSN_PIN);
  delay(50);

  nrfWriteReg(NRF_REG_RF_CH, 0x55);
  if (nrfReadReg(NRF_REG_RF_CH) != 0x55) {
    return false;
  }

  nrfWriteReg(NRF_REG_EN_AA, 0x00);
  nrfWriteReg(NRF_REG_EN_RXADDR, 0x01);
  nrfWriteReg(NRF_REG_RF_SETUP, 0x0F);
  nrfWriteReg(NRF_REG_CONFIG, 0x03);
  delay(5);
  nrfFlushRx();
  return true;
}

void configureSnifferMode(uint8_t ch) {
  digitalWrite(NRF_CE_PIN, LOW);
  nrfWriteReg(NRF_REG_EN_AA, 0x00);
  nrfWriteReg(NRF_REG_SETUP_AW, 0x00);
  uint8_t promiscAddr[2] = {0xAA, 0x00};
  nrfWriteRegMulti(NRF_REG_RX_ADDR_P0, promiscAddr, 2);
  nrfWriteReg(NRF_REG_RX_PW_P0, 8);
  nrfWriteReg(NRF_REG_RF_SETUP, 0x07);
  nrfWriteReg(NRF_REG_RF_CH, ch);
  nrfWriteReg(NRF_REG_CONFIG, 0x03);
  nrfWriteReg(NRF_REG_STATUS, 0x70);
  nrfFlushRx();
  digitalWrite(NRF_CE_PIN, HIGH);
}

// ===================== UNIVERSITY WELCOME STARTUP SCREEN =====================
void showUniversityWelcomeScreen() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);

  // Centered lines on 128x64 OLED
  display.setCursor(34, 6);
  display.print(F("Welcome to"));

  display.drawFastHLine(16, 17, 96, SSD1306_WHITE);

  display.setCursor(4, 22);
  display.print(F("J.C. Bose University"));

  display.setCursor(22, 33);
  display.print(F("of Science and"));

  display.setCursor(34, 44);
  display.print(F("Technology"));

  // Smooth startup loading bar at bottom
  for (int pct = 0; pct <= 100; pct += 10) {
    display.drawRect(4, 56, 120, 6, SSD1306_WHITE);
    int fillW = map(pct, 0, 100, 0, 116);
    if (fillW > 0) {
      display.fillRect(6, 58, fillW, 2, SSD1306_WHITE);
    }
    display.display();
    delay(150);
  }
  delay(1200);
}

// ===================== BOTTOM 45-SECOND PROGRESS BAR =====================
void drawModeTimerBar(unsigned long elapsedMs) {
  unsigned long clamped = constrain(elapsedMs, 0UL, MODE_DURATION_MS);
  uint8_t secDone = clamped / 1000UL;
  uint8_t totalSec = MODE_DURATION_MS / 1000UL;

  // Progress bar frame on bottom-left (x: 0..85, y: 57..63)
  display.drawRect(0, 57, 86, 7, SSD1306_WHITE);
  int fillW = (int)map((long)clamped, 0L, (long)MODE_DURATION_MS, 0L, 82L);
  if (fillW > 0) {
    display.fillRect(2, 59, fillW, 3, SSD1306_WHITE);
  }

  // Completed seconds counter on bottom-right (e.g., "14/45s")
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(90, 57);
  if (secDone < 10) display.print('0');
  display.print(secDone);
  display.print('/');
  display.print(totalSec);
  display.print('s');
}

// ===================== SPECTRUM SWEEP ENGINE =====================
void sweepAllChannels(uint8_t passes) {
  memset(signalLevel, 0, sizeof(signalLevel));
  digitalWrite(NRF_CE_PIN, LOW);
  nrfWriteReg(NRF_REG_EN_AA, 0x00);
  nrfWriteReg(NRF_REG_RF_SETUP, 0x0F);
  nrfWriteReg(NRF_REG_CONFIG, 0x03);

  for (uint8_t p = 0; p < passes; p++) {
    for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
      nrfWriteReg(NRF_REG_RF_CH, ch);
      digitalWrite(NRF_CE_PIN, HIGH);
      delayMicroseconds(155);
      uint8_t rpd = nrfReadReg(NRF_REG_RPD) & 0x01;
      digitalWrite(NRF_CE_PIN, LOW);
      if (rpd) {
        signalLevel[ch]++;
      }
    }
  }

  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    if (signalLevel[ch] >= peakHold[ch]) {
      peakHold[ch] = signalLevel[ch];
    } else if (peakHold[ch] > 0) {
      peakHold[ch]--;
    }
  }
}

// ===================== WI-FI + ROGUE AUDIT SCAN =====================
void runWifiAudit() {
  memset(wifiChLoad, 0, sizeof(wifiChLoad));
  wifiApCount = WiFi.scanNetworks(false, true);
  if (wifiApCount < 0) wifiApCount = 0;

  for (int i = 0; i < wifiApCount; i++) {
    int ch = WiFi.channel(i);
    int rssi = WiFi.RSSI(i);
    int weight = constrain(map(rssi, -95, -35, 1, 10), 1, 10);
    if (ch >= 1 && ch <= 14) {
      wifiChLoad[ch] += weight;
      if (ch > 1) wifiChLoad[ch - 1] += weight / 2;
      if (ch < 14) wifiChLoad[ch + 1] += weight / 2;
    }
  }

  int candidates[3] = {1, 6, 11};
  cleanestWifiCh = 1;
  int minLoad = wifiChLoad[1];
  for (int k = 1; k < 3; k++) {
    if (wifiChLoad[candidates[k]] < minLoad) {
      minLoad = wifiChLoad[candidates[k]];
      cleanestWifiCh = candidates[k];
    }
  }
  WiFi.scanDelete();
  lastWifiScanMs = millis();
}

// ===================== OLED RENDERERS =====================
void renderSpectrumMode(uint8_t maxPasses, unsigned long elapsedMs) {
  uint8_t peakCh = 0, peakVal = 0;
  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    if (signalLevel[ch] > peakVal) {
      peakVal = signalLevel[ch];
      peakCh = ch;
    }
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.print(F("1/4 SPEC "));
  if (peakVal > 0) {
    display.print(2400 + peakCh);
    display.print(F("M "));
    display.print((peakVal * 100) / maxPasses);
    display.print(F("%"));
  } else {
    display.print(F("CLEAR"));
  }

  // Top divider + Wi-Fi Ch 1, Ch 6, Ch 11 reference ticks
  display.drawFastHLine(0, 9, 128, SSD1306_WHITE);
  display.drawFastVLine(1 + 12, 10, 2, SSD1306_WHITE);
  display.drawFastVLine(1 + 37, 10, 2, SSD1306_WHITE);
  display.drawFastVLine(1 + 62, 10, 2, SSD1306_WHITE);

  // Spectrum baseline at y=54
  display.drawFastHLine(1, 54, 126, SSD1306_WHITE);

  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    uint8_t x = ch + 1;
    uint8_t barH = map(signalLevel[ch], 0, maxPasses, 0, 41);
    uint8_t pkH  = map(peakHold[ch], 0, maxPasses, 0, 41);

    if (barH > 0) {
      display.drawFastVLine(x, 53 - barH, barH, SSD1306_WHITE);
    }
    if (pkH > 0) {
      display.drawPixel(x, 53 - pkH, SSD1306_WHITE);
    }
  }

  drawModeTimerBar(elapsedMs);
  display.display();
}

void renderWaterfallMode(unsigned long elapsedMs) {
  for (int r = WF_ROWS - 1; r > 0; r--) {
    memcpy(waterfallBuf[r], waterfallBuf[r - 1], 16);
  }
  memset(waterfallBuf[0], 0, 16);

  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    if (signalLevel[ch] > 0) {
      uint8_t x = ch + 1;
      waterfallBuf[0][x >> 3] |= (0x80 >> (x & 0x07));
    }
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print(F("2/4 WATERFALL 2.4-2.5G"));
  display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

  for (uint8_t r = 0; r < WF_ROWS; r++) {
    for (uint8_t b = 0; b < 16; b++) {
      uint8_t byteVal = waterfallBuf[r][b];
      if (!byteVal) continue;
      for (uint8_t bit = 0; bit < 8; bit++) {
        if (byteVal & (0x80 >> bit)) {
          display.drawPixel((b << 3) + bit, 11 + r, SSD1306_WHITE);
        }
      }
    }
  }

  drawModeTimerBar(elapsedMs);
  display.display();
}

void renderDualAuditMode(unsigned long elapsedMs) {
  if (millis() - lastWifiScanMs > 12000 || lastWifiScanMs == 0) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(10, 24);
    display.print(F("Scanning Wi-Fi + RF..."));
    drawModeTimerBar(millis() - modeStartMs);
    display.display();
    runWifiAudit();
    elapsedMs = millis() - modeStartMs;
  }

  uint8_t nonWifiHits = 0;
  uint8_t roguePeakCh = 0;
  uint8_t rogueMax = 0;
  for (uint8_t ch = 84; ch < NUM_CHANNELS; ch++) {
    if (signalLevel[ch] > 0) {
      nonWifiHits += signalLevel[ch];
      if (signalLevel[ch] > rogueMax) {
        rogueMax = signalLevel[ch];
        roguePeakCh = ch;
      }
    }
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.print(F("3/4 WIFI+ROGUE AUDIT"));
  display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

  display.setCursor(0, 12);
  display.print(F("WiFi APs Found : "));
  display.print(wifiApCount);

  display.setCursor(0, 22);
  display.print(F("Best WiFi Ch   : Ch "));
  display.print(cleanestWifiCh);

  display.setCursor(0, 32);
  display.print(F("Upper ISM Hits : "));
  display.print(nonWifiHits);

  display.drawRect(0, 42, 128, 12, SSD1306_WHITE);
  display.setCursor(4, 44);
  if (nonWifiHits >= 3) {
    display.print(F("ALERT: RF @ "));
    display.print(2400 + roguePeakCh);
    display.print(F("MHz"));
  } else {
    display.print(F("STATUS: NO ROGUE TX"));
  }

  drawModeTimerBar(elapsedMs);
  display.display();
}

void renderSnifferMode(unsigned long elapsedMs) {
  uint8_t status = nrfReadReg(NRF_REG_STATUS);
  if (status & 0x40) {
    nrfReadPayload(lastPacketBytes, 8);
    nrfWriteReg(NRF_REG_STATUS, 0x40);
    nrfFlushRx();
    packetCounter++;
    hasPacketSample = true;
  }

  if (millis() - lastSecMs >= 1000) {
    packetsPerSec = packetCounter;
    packetCounter = 0;
    lastSecMs = millis();
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.print(F("4/4 AIR SNIFFER"));
  display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

  display.setCursor(0, 12);
  display.print(F("Freq : "));
  display.print(2400 + snifferCh);
  display.print(F(" MHz (Ch"));
  display.print(snifferCh);
  display.print(F(")"));

  display.setCursor(0, 22);
  display.print(F("Rate : "));
  display.print(packetsPerSec);
  display.print(F(" frames/s"));

  display.setCursor(0, 33);
  display.print(F("Last Raw Frame (Hex):"));

  display.setCursor(0, 44);
  if (hasPacketSample) {
    for (uint8_t i = 0; i < 6; i++) {
      if (lastPacketBytes[i] < 0x10) display.print('0');
      display.print(lastPacketBytes[i], HEX);
      display.print(' ');
    }
  } else {
    display.print(F("Listening on air..."));
  }

  drawModeTimerBar(elapsedMs);
  display.display();
}

// ===================== SERIAL CSV TELEMETRY =====================
void streamSerialCSV() {
  for (uint8_t ch = 0; ch < NUM_CHANNELS; ch++) {
    Serial.print(signalLevel[ch]);
    if (ch < NUM_CHANNELS - 1) Serial.print(',');
  }
  Serial.println();
}

// ===================== SETUP & MAIN LOOP =====================
void setup() {
  Serial.begin(115200);

  Wire.begin(OLED_SDA, OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    while (true) { delay(100); }
  }

  // 1. Show Welcome Message on Startup
  showUniversityWelcomeScreen();

  // 2. Initialize Wi-Fi & NRF24L01
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (!initNRF24()) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 10);
    display.println(F("NRF24L01 NOT FOUND!"));
    display.println(F("Check SPI Wiring:"));
    display.println(F("CE:4 CSN:5 SCK:12"));
    display.println(F("MOSI:11 MISO:13"));
    display.println(F("+ 10uF Cap on 3V3"));
    display.display();
    while (true) { delay(500); }
  }

  // Start the 45-second timer for Mode 1
  modeStartMs = millis();
}

void loop() {
  unsigned long elapsedMs = millis() - modeStartMs;

  // Automatically switch to the next mode every 45 seconds
  if (elapsedMs >= MODE_DURATION_MS) {
    currentMode = (currentMode + 1) % 4;
    modeStartMs = millis();
    elapsedMs = 0;

    if (currentMode == 0) {
      memset(peakHold, 0, sizeof(peakHold));
    } else if (currentMode == 2) {
      lastWifiScanMs = 0;
    } else if (currentMode == 3) {
      // Auto-tune Sniffer onto the strongest channel detected earlier
      uint8_t bestCh = 40, bestVal = 0;
      for (uint8_t c = 0; c < NUM_CHANNELS; c++) {
        if (signalLevel[c] > bestVal) {
          bestVal = signalLevel[c];
          bestCh = c;
        }
      }
      snifferCh = bestCh;
      packetsPerSec = 0;
      packetCounter = 0;
      hasPacketSample = false;
      lastSecMs = millis();
      configureSnifferMode(snifferCh);
    }
  }

  const uint8_t SWEEP_PASSES = 10;
  if (currentMode == 0) {
    sweepAllChannels(SWEEP_PASSES);
    renderSpectrumMode(SWEEP_PASSES, millis() - modeStartMs);
    streamSerialCSV();
  } else if (currentMode == 1) {
    sweepAllChannels(6);
    renderWaterfallMode(millis() - modeStartMs);
    streamSerialCSV();
  } else if (currentMode == 2) {
    sweepAllChannels(8);
    renderDualAuditMode(millis() - modeStartMs);
  } else if (currentMode == 3) {
    renderSnifferMode(millis() - modeStartMs);
  }
}