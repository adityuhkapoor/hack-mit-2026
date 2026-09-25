// nimbus — UNO Q microcontroller side: read the sensors and the controls, hand them to Linux.
//
// The Linux side (python/main.py) calls these over the Arduino Bridge:
//   readings()  -> "temp_c=21.4;rh=48.0;lux=310;db=57.2"   key=value, any subset; absent sensors are omitted
//                  keys: temp_c rh lux db pm25 pressure_hpa   (wind and cloud come from web weather)
//   controls()  -> "shutter,dial"            (shutter 1 on a fresh press, dial 0..2)
//   status(n)   -> LED state: 0 idle, 1 working, 2 done, 3 error
//
// Every sensor is optional: comment out a HAVE_ line when that part did not get checked out.
// Verify at the venue: Bridge and Modulino library names against the App Lab examples.

#include <Arduino_RouterBridge.h>
#include <Modulino.h>

#define HAVE_THERMO   // Modulino Thermo: temperature + humidity (I2C/Qwiic)
#define HAVE_BUTTONS  // Modulino Buttons: A = shutter
#define HAVE_KNOB     // Modulino Knob: the mode dial
#define HAVE_PIXELS   // Modulino Pixels: status LEDs
#define HAVE_MIC      // electret mic + amp (MAX4466 / MAX9814) on A0
#define HAVE_LIGHT    // photoresistor divider on A2 (or swap in a Modulino Light)

#ifdef HAVE_THERMO
ModulinoThermo thermo;
#endif
#ifdef HAVE_BUTTONS
ModulinoButtons buttons;
#endif
#ifdef HAVE_KNOB
ModulinoKnob knob;
#endif
#ifdef HAVE_PIXELS
ModulinoPixels pixels;
#endif

const int MIC_PIN = A0, LIGHT_PIN = A2;
const float VREF = 3.3;        // UNO Q analog reference
bool shutterLatched = false;   // one press = one picture, however long it is held
int ledState = 0;

float micDb() {
#ifdef HAVE_MIC
  // Peak-to-peak over 50 ms, mapped to an approximate dBA. Calibrate MIC_OFFSET against a phone app.
  const float MIC_OFFSET = 94.0;
  unsigned long t0 = millis();
  int lo = 4095, hi = 0;
  while (millis() - t0 < 50) {
    int v = analogRead(MIC_PIN);
    lo = min(lo, v); hi = max(hi, v);
  }
  float vpp = (hi - lo) * VREF / 4095.0;
  return vpp > 0.001 ? 20.0 * log10(vpp / 1.0) + MIC_OFFSET : 30.0;
#else
  return NAN;
#endif
}

float lux() {
#ifdef HAVE_LIGHT
  // 10k photoresistor to 3.3 V, 10k to ground: a rough log mapping, good to within a stop.
  float v = max(1, analogRead(LIGHT_PIN)) * VREF / 4095.0;
  float r = 10000.0 * (VREF - v) / v;
  return 500000.0 / pow(r, 1.25);
#else
  return NAN;
#endif
}

void add(String &out, const char *key, float x) {
  if (isnan(x)) return;
  if (out.length()) out += ";";
  out += key; out += "="; out += String(x, 1);
}

String readings() {
  String out;
#ifdef HAVE_THERMO
  add(out, "temp_c", thermo.getTemperature());
  add(out, "rh", thermo.getHumidity());
#endif
  add(out, "lux", lux());
  add(out, "db", micDb());
  return out;
}

String controls() {
  int shutter = 0, dial = 0;
#ifdef HAVE_BUTTONS
  if (buttons.update()) {
    bool down = buttons.isPressed(0);
    if (down && !shutterLatched) shutter = 1;
    shutterLatched = down;
  }
#endif
#ifdef HAVE_KNOB
  // Three detented zones; the knob counts steps, so fold it into 0..2.
  long k = knob.get();
  dial = ((k / 4) % 3 + 3) % 3;
#endif
  return String(shutter) + "," + String(dial);
}

void status(int s) { ledState = s; }

void showLeds() {
#ifdef HAVE_PIXELS
  static unsigned long t = 0;
  t++;
  for (int i = 0; i < 8; i++) {
    switch (ledState) {
      case 1: pixels.set(i, (i == (t / 3) % 8) ? BLUE : RED, (i == (t / 3) % 8) ? 25 : 0); break;  // chase
      case 2: pixels.set(i, GREEN, 20); break;
      case 3: pixels.set(i, RED, 25); break;
      default: pixels.set(i, WHITE, i == 0 ? 4 : 0);
    }
  }
  pixels.show();
#endif
}

void setup() {
  Bridge.begin();
  Modulino.begin();
#ifdef HAVE_THERMO
  thermo.begin();
#endif
#ifdef HAVE_BUTTONS
  buttons.begin();
#endif
#ifdef HAVE_KNOB
  knob.begin();
#endif
#ifdef HAVE_PIXELS
  pixels.begin();
#endif
  analogReadResolution(12);
  Bridge.provide("readings", readings);
  Bridge.provide("controls", controls);
  Bridge.provide("status", status);
}

void loop() {
  showLeds();
  delay(30);
}
