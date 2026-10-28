#include <Wire.h>
#include <RTClib.h>
#include <SPI.h>
#include <SD.h>
#include <LiquidCrystal_I2C.h>

// Configuración LCD I2C para pantalla 20x4 (Dirección 0x27, 20 columnas, 4 filas)
LiquidCrystal_I2C lcd(0x27, 20, 4);

// Pines de componentes
const int PIN_SENSOR = A0;
const int PIN_SD_CS = 4;
const int PIN_BTN_GUARDAR = 6; // Botón 1: Iniciar / Guardar medición
const int PIN_BTN_PARCELA = 5; // Botón 2: Cambiar de parcela (A -> B -> C -> A...)

// Constantes de calibración del sensor de humedad
const int VALOR_SECO = 520;   // 0% de humedad (al aire)
const int VALOR_HUMEDO = 220; // 100% de humedad (en agua)

RTC_DS3231 rtc;
File archivo;

// Arreglo con las parcelas disponibles
const char PARCELAS[] = {'A', 'B', 'C'};
int indiceParcela = 0; // 0 = 'A', 1 = 'B', 2 = 'C'

unsigned long ultimoDebounceGuardar = 0;
unsigned long ultimoDebounceParcela = 0;
const unsigned long DELAY_DEBOUNCE = 250; // Evita rebotes mecánicos de los botones

bool estadoPrevioGuardar = HIGH;
bool estadoPrevioParcela = HIGH;

void setup() {
  Serial.begin(9600);
  Wire.begin();

  // Configurar botones con resistencias PULL-UP internas
  pinMode(PIN_BTN_GUARDAR, INPUT_PULLUP);
  pinMode(PIN_BTN_PARCELA, INPUT_PULLUP);

  // Inicializar Pantalla LCD 20x4
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("   REGISTRADOR DE   ");
  lcd.setCursor(0, 1);
  lcd.print("  HUMEDAD DE SUELO  ");
  lcd.setCursor(0, 3);
  lcd.print("Iniciando...");

  // Inicializar Reloj RTC
  if (!rtc.begin()) {
    Serial.println("ERROR: No se detecta el RTC.");
    lcd.setCursor(0, 3);
    lcd.print("ERROR: Sin RTC!     ");
    while (true) {}
  }

  if (rtc.lostPower()) {
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
  }

  // Inicializar Tarjeta MicroSD
  if (!SD.begin(PIN_SD_CS)) {
    Serial.println("ERROR: No se detecta la MicroSD.");
    lcd.setCursor(0, 3);
    lcd.print("ERROR: Sin MicroSD! ");
    while (true) {}
  }

  // Crear encabezado CSV si no existe
  if (!SD.exists("HUMEDAD.CSV")) {
    archivo = SD.open("HUMEDAD.CSV", FILE_WRITE);
    if (archivo) {
      archivo.println("fecha,hora,parcela,lectura_adc,porcentaje_humedad");
      archivo.close();
    } else {
      Serial.println("ERROR creando el CSV.");
      lcd.setCursor(0, 3);
      lcd.print("ERROR: Crear CSV!   ");
      while (true) {}
    }
  }

  delay(1500);
  lcd.clear();
  Serial.println("Sistema Listo.");
}

void loop() {
  DateTime ahora = rtc.now();
  int lectura = analogRead(PIN_SENSOR);
  int porcentajeHumedad = map(lectura, VALOR_SECO, VALOR_HUMEDO, 0, 100);
  porcentajeHumedad = constrain(porcentajeHumedad, 0, 100);

  // Mapear el carácter actual de la parcela ('A', 'B' o 'C')
  char parcelaActual = PARCELAS[indiceParcela];

  // Formatting de buffers para la pantalla
  char bufferFecha[11];
  char bufferHora[9];
  snprintf(bufferFecha, sizeof(bufferFecha), "%04d-%02d-%02d", ahora.year(), ahora.month(), ahora.day());
  snprintf(bufferHora, sizeof(bufferHora), "%02d:%02d:%02d", ahora.hour(), ahora.minute(), ahora.second());

  // --- FILA 0: Fecha ---
  lcd.setCursor(0, 0);
  lcd.print("Fecha: ");
  lcd.print(bufferFecha);

  // --- FILA 1: Hora y Parcela ---
  lcd.setCursor(0, 1);
  lcd.print("Hora: ");
  lcd.print(bufferHora);
  lcd.print("  P:");
  lcd.print(parcelaActual);

  // --- FILA 2: Humedad y Lectura ADC ---
  lcd.setCursor(0, 2);
  lcd.print("Humedad: ");
  if (porcentajeHumedad < 100) lcd.print(" "); // Espaciado estético para alineación
  if (porcentajeHumedad < 10)  lcd.print(" ");
  lcd.print(porcentajeHumedad);
  lcd.print("% ");
  lcd.print("(");
  lcd.print(lectura);
  lcd.print(")");

  // --- FILA 3: Estado ---
  lcd.setCursor(0, 3);
  lcd.print("Estado: Listo       ");

  // --- LECTURA DEL BOTÓN 1: INICIAR / GUARDAR MEDICIÓN (Pin 6) ---
  bool lecturaGuardar = digitalRead(PIN_BTN_GUARDAR);
  if (lecturaGuardar == LOW && estadoPrevioGuardar == HIGH && (millis() - ultimoDebounceGuardar > DELAY_DEBOUNCE)) {
    ultimoDebounceGuardar = millis();
    guardarMedicionSD(ahora, parcelaActual, lectura, porcentajeHumedad);
  }
  estadoPrevioGuardar = lecturaGuardar;

  // --- LECTURA DEL BOTÓN 2: CAMBIAR PARCELA (Pin 5) ---
  bool lecturaParcela = digitalRead(PIN_BTN_PARCELA);
  if (lecturaParcela == LOW && estadoPrevioParcela == HIGH && (millis() - ultimoDebounceParcela > DELAY_DEBOUNCE)) {
    ultimoDebounceParcela = millis();

    // Rotación circular: A -> B -> C -> A
    indiceParcela = (indiceParcela + 1) % 3;
    char nuevaParcela = PARCELAS[indiceParcela];

    // Feedback visual en LCD
    lcd.setCursor(0, 3);
    lcd.print("Parcela -> ");
    lcd.print(nuevaParcela);
    lcd.print("        ");
    
    Serial.print("Cambiado a Parcela ");
    Serial.println(nuevaParcela);

    delay(600); // Breve pausa de confirmación
  }
  estadoPrevioParcela = lecturaParcela;

  delay(50); // Pequeña pausa de estabilización
}

void guardarMedicionSD(DateTime ahora, char parcela, int lectura, int porcentaje) {
  char fecha[11];
  char hora[9];

  snprintf(fecha, sizeof(fecha), "%04d-%02d-%02d", ahora.year(), ahora.month(), ahora.day());
  snprintf(hora, sizeof(hora), "%02d:%02d:%02d", ahora.hour(), ahora.minute(), ahora.second());

  archivo = SD.open("HUMEDAD.CSV", FILE_WRITE);

  if (archivo) {
    archivo.print(fecha);
    archivo.print(",");
    archivo.print(hora);
    archivo.print(",");
    archivo.print(parcela);
    archivo.print(",");
    archivo.print(lectura);
    archivo.print(",");
    archivo.println(porcentaje);

    archivo.flush();
    archivo.close();

    // Confirmación en la Fila 3 de la LCD
    lcd.setCursor(0, 3);
    lcd.print(">> GUARDADO EN SD! <<");
    
    Serial.print("GUARDADO: ");
    Serial.print(fecha);
    Serial.print(" ");
    Serial.print(hora);
    Serial.print(" | Parcela ");
    Serial.print(parcela);
    Serial.print(" | ADC=");
    Serial.print(lectura);
    Serial.print(" | Hum=");
    Serial.print(porcentaje);
    Serial.println("%");

    delay(1200); // Muestra confirmación en pantalla
  } else {
    Serial.println("ERROR: No se pudo escribir en la SD.");
    lcd.setCursor(0, 3);
    lcd.print("Err: Guardar SD!    ");
    delay(1500);
  }
}