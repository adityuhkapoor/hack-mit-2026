// Nimbus — the UNO Q microcontroller side.
//
// The board is an I2C peripheral at 0x08 for the Raspberry Pi and an I2C controller for the MLX90640
// thermal array. It answers three commands, each a write of [cmd, arg] followed by a read:
//
//   0x01        -> 12 bytes: min, max, mean of the last frame (float32 LE)
//   0x02 <idx>  -> 256 bytes: chunk idx (0..11) of the last frame, 768 float32 LE pixels in °C
//   0x07        -> 4 bytes: buttons. [0..1] pins held now, [2..3] pins pressed since the last read
//                  (little-endian, bit n = digital pin Dn, active-low buttons to ground on INPUT_PULLUP)
//
// Buses: the Pi may be on the header SDA/SCL (Wire) or on A4/A5 (Wire2), and the thermal array on any of
// the three. Setup scans for the MLX90640 (0x33) and becomes a peripheral on every other bus, so the wiring
// does not have to be known. Serial (the App Lab monitor / ttyACM on the Pi) logs frames and button changes.
//
// Flash from the UNO Q's own Linux: see device/README.md ("Reflashing the UNO Q").

#include <Wire.h>
#include <Adafruit_MLX90640.h>
#include <zephyr/drivers/i2c.h>

// The Zephyr core's Wire cannot do a repeated start, which the MLX90640 needs between the address write
// and the read, so the (patched, see patches/) driver reads through Zephyr's i2c API on the bus we found.
void mlx_set_zephyr_i2c(const struct device *dev, uint8_t addr);
const struct device *zephyrI2C[] = {DEVICE_DT_GET(DT_NODELABEL(i2c2)), DEVICE_DT_GET(DT_NODELABEL(i2c4)),
                                    DEVICE_DT_GET(DT_NODELABEL(i2c3))};   // Wire, Wire1, Wire2 (variant order)

const uint8_t ADDR = 0x08;
const uint8_t MLX_ADDR = 0x33;
const int PIXELS = 768, CHUNK = 256, CHUNKS = 12;

Adafruit_MLX90640 mlx;
TwoWire *buses[] = {&Wire, &Wire1, &Wire2};
const char *busNames[] = {"Wire", "Wire1", "Wire2"};
TwoWire *mlxBus = nullptr;
const char *mlxBusName = "none";

float frame[PIXELS];               // being filled by getFrame()
float tx[PIXELS];                  // last complete frame, served over I2C
float stats[3] = {NAN, NAN, NAN};  // min, max, mean
volatile uint8_t cmd = 0, arg = 0;
unsigned long frames = 0;

// Buttons: every free digital pin. Bit n = Dn. Pins held now, and pins pressed since the Pi last asked.
const int BUTTON_PINS[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};
const int N_BUTTONS = sizeof(BUTTON_PINS) / sizeof(BUTTON_PINS[0]);
volatile uint16_t held = 0, latched = 0;
uint16_t lastReported = 0;
unsigned long lastChange[14] = {0};

void pollButtons() {
  unsigned long now = millis();
  for (int i = 0; i < N_BUTTONS; i++) {
    int p = BUTTON_PINS[i];
    bool down = digitalRead(p) == LOW;
    bool was = held & (1 << p);
    if (down != was && now - lastChange[p] > 30) {   // 30 ms debounce
      lastChange[p] = now;
      if (down) { held |= (1 << p); latched |= (1 << p); }
      else held &= ~(1 << p);
    }
  }
}

// One receive/request pair serves whichever bus the Pi is on; both peripherals share them.
void receiveFrom(TwoWire &w, int n) {
  if (n < 1) return;
  cmd = w.read();
  arg = n >= 2 ? w.read() : 0;
  while (w.available()) w.read();
}

void requestFrom(TwoWire &w) {
  switch (cmd) {
    case 0x01:
      w.write((uint8_t *)stats, sizeof(stats));
      break;
    case 0x02:
      if (arg < CHUNKS) w.write((uint8_t *)tx + arg * CHUNK, CHUNK);
      break;
    case 0x07: {
      uint16_t h = held, l = latched;
      latched = 0;
      uint8_t out[4] = {(uint8_t)(h & 0xff), (uint8_t)(h >> 8), (uint8_t)(l & 0xff), (uint8_t)(l >> 8)};
      w.write(out, 4);
      break;
    }
    default:
      w.write((uint8_t)0);
  }
}

void receive0(int n) { receiveFrom(Wire, n); }
void receive1(int n) { receiveFrom(Wire1, n); }
void receive2(int n) { receiveFrom(Wire2, n); }
void request0() { requestFrom(Wire); }
void request1() { requestFrom(Wire1); }
void request2() { requestFrom(Wire2); }
void (*receivers[])(int) = {receive0, receive1, receive2};
void (*requesters[])() = {request0, request1, request2};

bool probe(TwoWire &w, uint8_t addr) {
  w.begin();
  w.setClock(400000);
  w.beginTransmission(addr);
  bool found = w.endTransmission() == 0;
  if (!found) w.end();
  return found;
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < PIXELS; i++) tx[i] = NAN;
  for (int i = 0; i < N_BUTTONS; i++) pinMode(BUTTON_PINS[i], INPUT_PULLUP);
  delay(300);

  // The array is not always awake by the time the sketch starts; keep looking for a few seconds.
  for (int attempt = 0; attempt < 20 && !mlxBus; attempt++) {
    for (int b = 0; b < 3 && !mlxBus; b++) {
      if (probe(*buses[b], MLX_ADDR)) {
        mlxBus = buses[b]; mlxBusName = busNames[b];
        mlx_set_zephyr_i2c(zephyrI2C[b], MLX_ADDR);
        Serial.print("MLX90640 on "); Serial.println(busNames[b]);
      }
    }
    if (!mlxBus) delay(250);
  }
  if (mlxBus && mlx.begin(MLX_ADDR, mlxBus)) {
    mlx.setMode(MLX90640_CHESS);
    mlx.setResolution(MLX90640_ADC_18BIT);
    mlx.setRefreshRate(MLX90640_8_HZ);
    // 400 kHz is plenty for 8 Hz; the STM32U5 I2C at 1 MHz misread the EEPROM and every pixel came back NaN
    Serial.print("MLX90640 serial "); Serial.print(mlx.serialNumber[0], HEX); Serial.print(mlx.serialNumber[1], HEX);
    Serial.println(mlx.serialNumber[2], HEX);
  } else {
    Serial.println("MLX90640 not found; serving buttons only");
    mlxBus = nullptr;
  }
  for (int b = 0; b < 3; b++) {
    if (buses[b] == mlxBus) continue;
    buses[b]->begin(ADDR);
    buses[b]->onReceive(receivers[b]);
    buses[b]->onRequest(requesters[b]);
    Serial.print("peripheral 0x08 on "); Serial.println(busNames[b]);
  }
  Serial.print("buttons on D"); Serial.print(BUTTON_PINS[0]); Serial.print("..D"); Serial.println(BUTTON_PINS[N_BUTTONS - 1]);
}

void loop() {
  pollButtons();
  if (held != lastReported) {
    lastReported = held;
    Serial.print("buttons=0x"); Serial.println(held, HEX);
  }
  if (mlxBus && mlx.getFrame(frame) == 0) {
    // In chess mode a read often carries only one subpage (the other half is 0 or NaN), so each pixel keeps
    // its last good value; anything still unread is NaN and the Pi skips it.
    float lo = 1e9, hi = -1e9, sum = 0; int n = 0;
    for (int i = 0; i < PIXELS; i++) {
      float v = frame[i];
      if (isfinite(v) && v != 0.0f && v > -40 && v < 300) tx[i] = v;
      if (!isfinite(tx[i])) continue;
      lo = min(lo, tx[i]); hi = max(hi, tx[i]); sum += tx[i]; n++;
    }
    stats[0] = n ? lo : NAN; stats[1] = n ? hi : NAN; stats[2] = n ? sum / n : NAN;
    frames++;
    if (frames % 8 == 0) {
      // The monitor drops what setup() printed before the router was ready, so repeat the essentials.
      Serial.print("mlx="); Serial.print(mlxBusName); Serial.print(" sn="); Serial.print(mlx.serialNumber[0], HEX);
      Serial.print(mlx.serialNumber[1], HEX); Serial.print(mlx.serialNumber[2], HEX);
      Serial.print(" p0="); Serial.print(frame[0], 2); Serial.print(" buttons=0x"); Serial.print(held, HEX);
      Serial.print(" Frame "); Serial.print(frames); Serial.print(" min="); Serial.print(lo, 2);
      Serial.print(" max="); Serial.print(hi, 2); Serial.print(" avg="); Serial.println(stats[2], 2);
    }
  } else {
    delay(2);
  }
}
