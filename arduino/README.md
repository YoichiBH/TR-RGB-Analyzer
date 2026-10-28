# Registrador d'humitat del sòl (Arduino)

Programa de l'Arduino UNO que es va fer servir per mesurar la humitat del sòl a les zones A, B i C del Treball de Recerca.

- `registrador_humedad_optimizado/`: **versió final**, la que es va utilitzar al treball de camp.
- `TR-sensor-humedad/`: primera versió.

## Material

Arduino UNO, sensor capacitiu Capacitive Soil Moisture Sensor v1.2, rellotge RTC DS3231, mòdul micro SD, pantalla LCD 20x4 I2C (0x27) i dos pulsadors.

## Connexions

| Component | Pin |
|---|---|
| Sensor d'humitat | A0 |
| LCD i RTC (I2C) | SDA (A4), SCL (A5) |
| Mòdul micro SD (SPI) | CS a D4, D11, D12, D13 |
| Pulsador «canviar zona» | D5 |
| Pulsador «desar mesura» | D6 |

## Biblioteques

Wire, SPI, SD, RTClib i LiquidCrystal_I2C.

## Calibració

Calibració de dos punts: 520 = 0 % (sensor a l'aire) i 220 = 100 % (sensor dins l'aigua).

```
H (%) = (520 − V) / (520 − 220) · 100
```

El resultat es limita entre 0 % i 100 %.

## Sortida

Cada mesura s'afegeix com una fila nova al fitxer `HUMEDAD.CSV` de la targeta SD:

```
fecha,hora,parcela,lectura_adc,porcentaje_humedad
```

Aquest fitxer es pot importar directament a TR RGB Analyzer.
