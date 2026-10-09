// Hold Button - ESP32 side (for the flamethrower / hold-to-burn projects)
// Wiring: one leg of the button -> GPIO 4, other leg -> GND (internal pull-up, no resistor)
// While the button is held it sends "HOLD" every 80 ms.
// A quick press also still counts as a short burst in the Python script.

const int BUTTON_PIN = 14;
const unsigned long HOLD_INTERVAL_MS = 80;

unsigned long lastSend = 0;

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
}

void loop() {
  if (digitalRead(BUTTON_PIN) == LOW) {        // pressed
    if (millis() - lastSend >= HOLD_INTERVAL_MS) {
      Serial.println("HOLD");
      lastSend = millis();
    }
  }
}
