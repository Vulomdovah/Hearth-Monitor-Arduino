// heart_monitor.ino
// Arduino UNO + AD8232 (Heart Monitor breakout)
// Detecta cada latido en tiempo real y calcula BPM. Envia todo por Serial.
//
// CONEXIONES (protoboard, cables M-M):
//   AD8232 OUTPUT -> A0
//   AD8232 LO+    -> D10
//   AD8232 LO-    -> D11
//   AD8232 3.3V   -> 3.3V del Arduino
//   AD8232 GND    -> GND
//   AD8232 SDN    -> dejar sin conectar (o a 3.3V para forzar encendido)
//   Electrodos: RA, LA, RL segun el cable de 3 puntas del sensor
//
// IMPORTANTE: este es un proyecto de hobby, no un dispositivo medico.
// No lo uses para tomar decisiones de salud reales.

const int PIN_LO_PLUS = 10;
const int PIN_LO_MINUS = 11;
const int PIN_SIGNAL = A0;

const unsigned long SAMPLE_INTERVAL_US = 4000; // ~250 Hz de muestreo
const unsigned long REFRACTORY_MS = 250;       // min entre picos -> tope 240 bpm

int baseline = 512;                 // se auto-ajusta solo
unsigned long lastBeatTime = 0;
unsigned long lastSampleTime = 0;
bool aboveThreshold = false;

const int NUM_INTERVALS = 4;        // media movil corta -> respuesta rapida
unsigned long intervals[NUM_INTERVALS];
int intervalIndex = 0;
int intervalCount = 0;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
}

void loop() {
  unsigned long now = micros();
  if (now - lastSampleTime < SAMPLE_INTERVAL_US) return;
  lastSampleTime = now;

  // Electrodos despegados
  if (digitalRead(PIN_LO_PLUS) == HIGH || digitalRead(PIN_LO_MINUS) == HIGH) {
    Serial.println("L");
    return;
  }

  int signal = analogRead(PIN_SIGNAL);

  // Señal cruda para dibujar la linea verde (formato compacto: R + valor)
  Serial.print('R');
  Serial.println(signal);

  // baseline adaptativo lento, para no perder el pico R
  baseline = baseline + ((signal - baseline) >> 6);

  unsigned long nowMs = millis();

  // Deteccion de pico por cruce de umbral sobre el baseline
  if (!aboveThreshold && signal > baseline + 100 && (nowMs - lastBeatTime) > REFRACTORY_MS) {
    aboveThreshold = true;

    if (lastBeatTime != 0) {
      unsigned long interval = nowMs - lastBeatTime;
      intervals[intervalIndex] = interval;
      intervalIndex = (intervalIndex + 1) % NUM_INTERVALS;
      if (intervalCount < NUM_INTERVALS) intervalCount++;

      unsigned long sum = 0;
      for (int i = 0; i < intervalCount; i++) sum += intervals[i];
      float avgInterval = sum / (float)intervalCount;
      int bpm = (int)(60000.0 / avgInterval);

      Serial.print('B');
      Serial.println(bpm);
    }

    lastBeatTime = nowMs;
    Serial.println('P'); // evento inmediato de latido
  } else if (signal < baseline + 50) {
    aboveThreshold = false;
  }
}
