// A push-button between BUTTON_PIN and GND: writes single, double or long on the serial port.

const int BUTTON_PIN = 2;
const unsigned long DEBOUNCE_MS = 30;
const unsigned long DOUBLE_WINDOW_MS = 350;
const unsigned long LONG_PRESS_MS = 800;

bool down = false;
bool lastRead = false;
bool longSent = false;
int clicks = 0;
unsigned long changedAt = 0;
unsigned long pressedAt = 0;
unsigned long releasedAt = 0;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  // 9600 is what a Linux serial port is at before anyone sets it.
  Serial.begin(9600);
}

void loop() {
  unsigned long now = millis();
  bool reading = digitalRead(BUTTON_PIN) == LOW;

  if (reading != lastRead) {
    lastRead = reading;
    changedAt = now;
  }

  if (now - changedAt >= DEBOUNCE_MS && reading != down) {
    down = reading;
    if (down) {
      pressedAt = now;
      longSent = false;
    } else if (!longSent) {
      clicks++;
      releasedAt = now;
    }
  }

  if (down && !longSent && now - pressedAt >= LONG_PRESS_MS) {
    longSent = true;
    clicks = 0;
    Serial.println("long");
  }

  if (clicks >= 2) {
    clicks = 0;
    Serial.println("double");
  } else if (clicks == 1 && !down && now - releasedAt >= DOUBLE_WINDOW_MS) {
    clicks = 0;
    Serial.println("single");
  }
}
