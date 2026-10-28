// Ejemplo para TR RGB Analyzer: lectura de un sensor capacitivo de humedad del suelo
// e impresión en formato CSV por el Monitor Serie:
//
//     fecha,hora,parcela,lectura_adc,porcentaje_humedad
//
// Uso: escriba la FECHA, cambie PARCELA antes de medir cada parcela, abra el Monitor Serie
// (9600 baudios), copie las líneas y péguelas en un archivo .csv (la cabecera sólo una vez).
//
// - lectura_adc: valor RAW de analogRead (0-1023 en Arduino UNO). Es el dato que usa el análisis.
// - porcentaje_humedad: estimación del propio Arduino con map() entre RAW_SECO y RAW_HUMEDO.
//   Depende de esa calibración y NO es una humedad volumétrica universal; el programa lo
//   conserva aparte. En muchos sensores capacitivos, RAW más alto = suelo MÁS SECO.
// - La hora es aproximada (tiempo desde el arranque sumado a HORA_INICIO). Con un módulo
//   RTC (p. ej. DS3231) se puede obtener la hora real.

const int PIN_SENSOR = A0;
const char* FECHA = "2026-10-12";   // <-- fecha del día de medición (AAAA-MM-DD)
const char* PARCELA = "1";          // <-- debe coincidir con el ID de la parcela en el programa
const int HORA_INICIO = 15;         // <-- hora y minuto aproximados al encender la placa
const int MINUTO_INICIO = 30;
const int N_MEDICIONES = 5;
const int ESPERA_MS = 2000;

const int RAW_SECO = 800;           // <-- calibración: lectura con el sensor en suelo seco / al aire
const int RAW_HUMEDO = 400;         // <-- calibración: lectura con el sensor en agua / suelo saturado

void imprimirHora() {
  unsigned long s = millis() / 1000;
  unsigned long total = (unsigned long)HORA_INICIO * 3600UL + (unsigned long)MINUTO_INICIO * 60UL + s;
  int h = (total / 3600UL) % 24, m = (total / 60UL) % 60, sec = total % 60;
  if (h < 10) Serial.print("0");
  Serial.print(h); Serial.print(":");
  if (m < 10) Serial.print("0");
  Serial.print(m); Serial.print(":");
  if (sec < 10) Serial.print("0");
  Serial.print(sec);
}

void setup() {
  Serial.begin(9600);
  delay(1000);
  Serial.println("fecha,hora,parcela,lectura_adc,porcentaje_humedad");
  for (int i = 1; i <= N_MEDICIONES; i++) {
    int raw = analogRead(PIN_SENSOR);
    int pct = constrain(map(raw, RAW_SECO, RAW_HUMEDO, 0, 100), 0, 100);
    Serial.print(FECHA); Serial.print(",");
    imprimirHora(); Serial.print(",");
    Serial.print(PARCELA); Serial.print(",");
    Serial.print(raw); Serial.print(",");
    Serial.println(pct);
    delay(ESPERA_MS);
  }
}

void loop() {
  // Nada: pulse RESET para repetir la serie con otra parcela.
}
