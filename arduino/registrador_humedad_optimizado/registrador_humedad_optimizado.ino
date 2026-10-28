#include <Wire.h>
#include <RTClib.h>
#include <SPI.h>
#include <SD.h>
#include <LiquidCrystal_I2C.h>

// =====================================================
// CONFIGURACION DEL HARDWARE
// Arduino Uno + RTC DS3231 + LCD I2C 20x4 + MicroSD
// =====================================================

const byte PIN_SENSOR = A0;
const byte PIN_SD_CS = 4;
const byte PIN_BTN_GUARDAR = 6;
const byte PIN_BTN_PARCELA = 5;

// En Arduino Uno, D10 debe ser OUTPUT para mantener SPI
// en modo maestro, aunque el CS de la SD sea D4.
const byte PIN_SPI_MASTER = 10;

// Calibracion: ajustar con mediciones reales.
// Con estos valores: 520 = 0%, 220 = 100%.
const int VALOR_SECO = 520;
const int VALOR_HUMEDO = 220;

const unsigned long DEBOUNCE_MS = 250;
const unsigned long REFRESCO_LCD_MS = 250;
const unsigned long MENSAJE_MS = 1400;

const char NOMBRE_ARCHIVO[] = "HUMEDAD.CSV";
const char CABECERA_CSV[] =
  "fecha,hora,parcela,lectura_adc,porcentaje_humedad";

LiquidCrystal_I2C lcd(0x27, 20, 4);
RTC_DS3231 rtc;

const char PARCELAS[] = {'A', 'B', 'C'};
byte indiceParcela = 0;

bool estadoPrevioGuardar = HIGH;
bool estadoPrevioParcela = HIGH;
unsigned long ultimoCambioGuardar = 0;
unsigned long ultimoCambioParcela = 0;
unsigned long ultimaActualizacionLCD = 0;

bool sdLista = false;
bool rtcLista = false;

// =====================================================
// UTILIDADES
// =====================================================

void mostrarEstado(const char *mensaje) {
  lcd.setCursor(0, 3);
  lcd.print("                    ");
  lcd.setCursor(0, 3);
  lcd.print(mensaje);
}

void imprimirFechaHora(const DateTime &dt, char *fecha, size_t tamFecha,
                       char *hora, size_t tamHora) {
  snprintf(fecha, tamFecha, "%04d-%02d-%02d",
           dt.year(), dt.month(), dt.day());
  snprintf(hora, tamHora, "%02d:%02d:%02d",
           dt.hour(), dt.minute(), dt.second());
}

int calcularHumedad(int lecturaADC) {
  if (VALOR_SECO == VALOR_HUMEDO) {
    return -1; // Calibracion no valida
  }

  long porcentaje = (long)(lecturaADC - VALOR_SECO) * 100L /
                    (VALOR_HUMEDO - VALOR_SECO);

  if (porcentaje < 0) porcentaje = 0;
  if (porcentaje > 100) porcentaje = 100;

  return (int)porcentaje;
}

// =====================================================
// INICIALIZACION MICROSD
// =====================================================

bool iniciarSD() {
  // Evitar que otros dispositivos SPI seleccionen el bus.
  pinMode(PIN_SPI_MASTER, OUTPUT);
  digitalWrite(PIN_SPI_MASTER, HIGH);

  pinMode(PIN_SD_CS, OUTPUT);
  digitalWrite(PIN_SD_CS, HIGH);

  Serial.println(F("[SD] Iniciando tarjeta..."));

  if (!SD.begin(PIN_SD_CS)) {
    Serial.println(F("[SD] ERROR: SD.begin() ha fallado."));
    return false;
  }

  Serial.println(F("[SD] Inicializacion correcta."));

  // Crear cabecera si el archivo aun no existe.
  if (!SD.exists(NOMBRE_ARCHIVO)) {
    Serial.println(F("[SD] CSV no existe; creando cabecera."));

    File nuevo = SD.open(NOMBRE_ARCHIVO, FILE_WRITE);
    if (!nuevo) {
      Serial.println(F("[SD] ERROR: no se pudo crear el CSV."));
      return false;
    }

    nuevo.println(CABECERA_CSV);
    nuevo.flush();
    nuevo.close();

    Serial.println(F("[SD] Cabecera creada."));
  }

  // Verificar que el archivo se puede abrir para anexar.
  File prueba = SD.open(NOMBRE_ARCHIVO, FILE_WRITE);
  if (!prueba) {
    Serial.println(F("[SD] ERROR: no se pudo abrir CSV al verificar."));
    return false;
  }
  prueba.close();

  Serial.println(F("[SD] CSV accesible para escritura."));
  return true;
}

// =====================================================
// ACTUALIZACION DE PANTALLA
// =====================================================

void actualizarLCD(const DateTime &ahora, int lecturaADC,
                   int porcentaje, char parcela) {
  char fecha[11];
  char hora[9];

  imprimirFechaHora(ahora, fecha, sizeof(fecha), hora, sizeof(hora));

  lcd.setCursor(0, 0);
  lcd.print("Fecha: ");
  lcd.print(fecha);
  lcd.print(" ");

  lcd.setCursor(0, 1);
  lcd.print("Hora: ");
  lcd.print(hora);
  lcd.print(" P:");
  lcd.print(parcela);
  lcd.print(" ");

  lcd.setCursor(0, 2);
  lcd.print("Humedad: ");
  if (porcentaje >= 0 && porcentaje < 100) lcd.print(' ');
  if (porcentaje >= 0 && porcentaje < 10) lcd.print(' ');
  if (porcentaje < 0) {
    lcd.print("--");
  } else {
    lcd.print(porcentaje);
  }
  lcd.print("% (");
  lcd.print(lecturaADC);
  lcd.print(")   ");
}

// =====================================================
// GUARDAR MEDICION
// =====================================================

bool guardarMedicionSD(const DateTime &ahora, char parcela,
                       int lecturaADC, int porcentaje) {
  if (!sdLista) {
    Serial.println(F("[SD] Tarjeta no marcada como lista; reintentando."));
    sdLista = iniciarSD();

    if (!sdLista) {
      mostrarEstado("ERROR: SD no lista");
      return false;
    }
  }

  char fecha[11];
  char hora[9];
  imprimirFechaHora(ahora, fecha, sizeof(fecha), hora, sizeof(hora));

  Serial.println();
  Serial.println(F("===== DIAGNOSTICO DE ESCRITURA ====="));
  Serial.print(F("Archivo existe: "));
  Serial.println(SD.exists(NOMBRE_ARCHIVO) ? F("SI") : F("NO"));
  Serial.println(F("Abriendo HUMEDAD.CSV..."));

  // No reinicializar SD mientras un archivo esta abierto.
  File archivo = SD.open(NOMBRE_ARCHIVO, FILE_WRITE);

  if (!archivo) {
    Serial.println(F("[SD] ERROR: SD.open() fallo."));
    Serial.println(F("[SD] Intentando reinicializar la tarjeta una vez."));

    sdLista = iniciarSD();
    if (sdLista) {
      archivo = SD.open(NOMBRE_ARCHIVO, FILE_WRITE);
    }

    if (!archivo) {
      Serial.println(F("[SD] ERROR: sigue sin poder abrirse el CSV."));
      mostrarEstado("ERROR: Abrir CSV");
      return false;
    }
  }

  Serial.println(F("[SD] Archivo abierto."));

  archivo.print(fecha);
  archivo.print(',');
  archivo.print(hora);
  archivo.print(',');
  archivo.print(parcela);
  archivo.print(',');
  archivo.print(lecturaADC);
  archivo.print(',');
  archivo.println(porcentaje);

  // flush/close no devuelven confirmacion de escritura fisica.
  archivo.flush();
  archivo.close();

  Serial.print(F("[SD] Fila enviada: "));
  Serial.print(fecha);
  Serial.print(',');
  Serial.print(hora);
  Serial.print(',');
  Serial.print(parcela);
  Serial.print(',');
  Serial.print(lecturaADC);
  Serial.print(',');
  Serial.println(porcentaje);

  Serial.println(F("[SD] Archivo cerrado."));
  Serial.println(F("===== FIN DIAGNOSTICO ====="));

  mostrarEstado("GUARDADO EN SD");
  return true;
}

// =====================================================
// SETUP
// =====================================================

void setup() {
  Serial.begin(9600);
  Wire.begin();

  pinMode(PIN_BTN_GUARDAR, INPUT_PULLUP);
  pinMode(PIN_BTN_PARCELA, INPUT_PULLUP);

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("REGISTRADOR HUMEDAD");
  lcd.setCursor(0, 1);
  lcd.print("Iniciando sistema...");

  // RTC
  if (!rtc.begin()) {
    rtcLista = false;
    Serial.println(F("[RTC] ERROR: no se detecta DS3231."));
    mostrarEstado("ERROR: Sin RTC");
    while (true) {
      delay(100);
    }
  }

  rtcLista = true;

  if (rtc.lostPower()) {
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
    Serial.println(F("[RTC] Hora ajustada a la compilacion."));
    Serial.println(F("[RTC] Ajusta la hora si no coincide con la real."));
  }

  // MicroSD
  sdLista = iniciarSD();

  if (!sdLista) {
    Serial.println(F("[SD] La inicializacion fallo. Se puede reintentar al guardar."));
    mostrarEstado("ERROR INICIAL SD");
  } else {
    mostrarEstado("SD preparada");
  }

  Serial.println(F("Sistema iniciado."));
  Serial.println(F("Pulsa D6 para guardar."));
  Serial.println(F("Pulsa D5 para cambiar parcela."));

  delay(1200);
  lcd.clear();
}

// =====================================================
// LOOP
// =====================================================

void loop() {
  if (!rtcLista) return;

  DateTime ahora = rtc.now();
  int lecturaADC = analogRead(PIN_SENSOR);
  int porcentaje = calcularHumedad(lecturaADC);
  char parcelaActual = PARCELAS[indiceParcela];

  if (millis() - ultimaActualizacionLCD >= REFRESCO_LCD_MS) {
    ultimaActualizacionLCD = millis();
    actualizarLCD(ahora, lecturaADC, porcentaje, parcelaActual);
  }

  // Boton guardar: flanco HIGH -> LOW con debounce
  bool lecturaGuardar = digitalRead(PIN_BTN_GUARDAR);
  if (lecturaGuardar == LOW &&
      estadoPrevioGuardar == HIGH &&
      millis() - ultimoCambioGuardar >= DEBOUNCE_MS) {
    ultimoCambioGuardar = millis();

    if (porcentaje < 0) {
      Serial.println(F("[SENSOR] ERROR: valores de calibracion iguales."));
      mostrarEstado("ERROR calibracion");
    } else {
      bool guardado = guardarMedicionSD(
        ahora, parcelaActual, lecturaADC, porcentaje
      );

      if (!guardado) {
        Serial.println(F("[SD] No se ha confirmado la apertura del archivo."));
      }
    }
  }
  estadoPrevioGuardar = lecturaGuardar;

  // Boton parcela: A -> B -> C -> A
  bool lecturaParcela = digitalRead(PIN_BTN_PARCELA);
  if (lecturaParcela == LOW &&
      estadoPrevioParcela == HIGH &&
      millis() - ultimoCambioParcela >= DEBOUNCE_MS) {
    ultimoCambioParcela = millis();
    indiceParcela = (indiceParcela + 1) % 3;

    char nuevaParcela = PARCELAS[indiceParcela];

    Serial.print(F("Parcela cambiada a: "));
    Serial.println(nuevaParcela);

    lcd.setCursor(0, 3);
    lcd.print("Parcela seleccion: ");
    lcd.print(nuevaParcela);
    lcd.print(" ");
  }
  estadoPrevioParcela = lecturaParcela;

  delay(10);
}
