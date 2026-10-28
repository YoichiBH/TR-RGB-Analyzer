# TR RGB Analyzer

Aplicación científica y reproducible para analizar **fotografías RGB de un dron de bajo coste** en un
Treball de Recerca (TR) de agricultura de precisión.

```text
FOTOS RGB → PREPROCESAMIENTO → PARCELAS/ROIs → ÍNDICES RGB → ESTADÍSTICAS
→ DATOS DEL SUELO (ARDUINO) → CORRELACIONES → GRÁFICAS → CSV/EXCEL → INFORME
```

Funciona **sin conexión a Internet**, en Windows y en ordenadores modestos (procesa las imágenes de una en una).

> **Advertencia científica.** Se trabaja con imágenes **RGB**: **no hay banda NIR** y **no se calcula NDVI**.
> GLI, VARI y ExG son índices RGB que se interpretan como **indicadores relativos**; no son mediciones
> absolutas de clorofila, agua ni salud vegetal. Las correlaciones describen **asociaciones**, no causalidad.

---

## Índice

1. [Instalación](#1-instalación)
2. [Estructura del proyecto](#2-estructura-del-proyecto)
3. [Cómo importar fotos](#3-cómo-importar-fotos)
4. [Cómo crear parcelas](#4-cómo-crear-parcelas)
5. [Cómo importar los datos del Arduino](#5-cómo-importar-los-datos-del-arduino)
6. [Cómo analizar](#6-cómo-analizar)
7. [Cómo interpretar GLI](#7-cómo-interpretar-gli)
8. [Cómo interpretar VARI](#8-cómo-interpretar-vari)
9. [Cómo interpretar ExG](#9-cómo-interpretar-exg)
10. [Correlaciones](#10-correlaciones)
11. [Exportación](#11-exportación)
12. [Limitaciones](#12-limitaciones)
13. [Solución de errores](#13-solución-de-errores)
14. [Modo consola, demostración y tests](#14-modo-consola-demostración-y-tests)

---

## 1. Instalación

1. Instale **Python 3.10 o superior** desde <https://www.python.org/downloads/>.
   Durante la instalación marque **«Add python.exe to PATH»** y deje marcada la opción **«tcl/tk and IDLE»**
   (necesaria para la interfaz gráfica).
2. Copie la carpeta `TR_RGB_Analyzer` donde quiera (por ejemplo, en *Documentos*).
3. Haga **doble clic en `run.bat`**. La primera vez instala automáticamente las librerías
   (necesita Internet sólo esa vez) y abre el programa.

Instalación manual (opcional), desde una consola abierta en la carpeta:

```bash
python -m pip install -r requirements.txt
python app.py
```

| Librería | Uso | ¿Obligatoria? |
|---|---|---|
| numpy, Pillow, pandas, matplotlib | cálculo, imágenes, tablas, gráficas | Sí |
| scipy | valores *p* de Pearson/Spearman | No (hay implementación interna) |
| openpyxl | exportar a Excel `.xlsx` | No (los CSV siempre funcionan) |
| pytest | ejecutar los tests | No |

---

## 2. Estructura del proyecto

```text
TR_RGB_Analyzer/
├── app.py              Interfaz gráfica (GUI)
├── main.py             Modo consola (sin interfaz)
├── demo.py             Modo demostración con datos sintéticos
├── run.bat             Lanzador para Windows (doble clic)
├── config.json         Configuración (umbrales, escalas, formato CSV…)
├── requirements.txt    Dependencias
├── src/                Núcleo científico (sin interfaz)
│   ├── image_loader.py   carga de fotos (sólo lectura), EXIF, avisos de RAM
│   ├── preprocessing.py  preprocesamiento opcional y registrado
│   ├── indices.py        GLI, VARI, ExG (+ ExR, ExGR, NGRDI experimentales), máscara de vegetación
│   ├── roi.py            parcelas/ROIs: crear, editar, duplicar, guardar JSON/CSV
│   ├── statistics.py     media, mediana, sd, mín, máx, percentiles, n; agregación por parcela
│   ├── field_data.py     CSV del Arduino, calibración opcional, observaciones visuales
│   ├── correlation.py    Pearson y Spearman con interpretación prudente
│   ├── graphs.py         mapas de índices y gráficas
│   ├── validation.py     control de calidad
│   ├── project.py        proyectos (JSON)
│   ├── pipeline.py       flujo completo de análisis + metadatos
│   ├── export.py         CSV y Excel
│   ├── report.py         informe HTML y Markdown
│   └── utils.py          utilidades
├── gui/                Interfaz Tkinter (ventana, visor, editor de parcelas, resultados…)
├── input/
│   ├── fotos/          (opcional) sus fotos para el modo consola
│   ├── parcelas/       (opcional) parcelas.csv para el modo consola
│   └── datos_campo/    (opcional) CSV del Arduino y observaciones
├── output/             resultados (una subcarpeta por proyecto)
├── projects/           proyectos guardados (.json) y autoguardado de parcelas
├── plantillas/         plantillas CSV vacías y ejemplo de programa para Arduino
├── demo/               (se crea con demo.py) datos SINTÉTICOS, separados de los reales
├── logs/               registro de errores
└── tests/              tests automáticos (pytest)
```

Todas las carpetas necesarias se crean automáticamente al arrancar.

---

## 3. Cómo importar fotos

1. Abra el programa (`run.bat`) y pulse **Proyecto → Nuevo** (póngale un nombre).
2. En **1. FOTOGRAFÍAS** pulse **Importar fotos…** y seleccione una o varias imágenes **JPG, JPEG o PNG**
   (Ctrl o Mayús para seleccionar varias).
3. En la pestaña **Fotografías** verá: nombre, resolución, megapíxeles, tamaño, formato, fecha
   (EXIF si existe) y número de parcelas. Arriba a la derecha aparece el número de imágenes cargadas.

Notas:
- **Las fotos originales nunca se modifican**: el programa sólo las lee y trabaja con copias en memoria.
- Si una foto no tiene fecha EXIF puede indicarla con **Editar fecha…** (necesario para la evolución temporal).
- Si una imagen es muy grande (> 40 MP, configurable) aparece un aviso de RAM; puede activar
  **Reducir resolución** en *Opciones de análisis* (queda registrado en los metadatos).
- *Quitar del proyecto* no borra el archivo.

---

## 4. Cómo crear parcelas

En la pestaña **Visor y parcelas**:

1. Elija la foto en el desplegable **Foto**.
2. Pulse **Crear parcela** (o **✏ Dibujar parcela**) y **arrastre con el ratón** un rectángulo sobre la zona.
3. Se abre un formulario con el **ID de parcela** (A, B, C… — debe coincidir con la columna `parcela` del CSV del
   Arduino), nombre, foto, coordenadas y descripción. Pulse *Aceptar*.

Controles del visor:

| Acción | Cómo |
|---|---|
| Zoom | rueda del ratón (centrado en el cursor), botones ＋ / － / Ajustar / 1:1 |
| Desplazar | arrastrar con botón derecho o central (o izquierdo sobre una zona vacía) |
| Seleccionar parcela | clic sobre ella (o en la lista inferior) |
| Mover parcela | arrastrarla |
| Cambiar tamaño | arrastrar una esquina (tiradores amarillos) de la parcela seleccionada |
| Editar | doble clic o botón **Editar** |
| Eliminar | tecla **Supr** o botón **Eliminar** |
| Coordenadas X/Y | se muestran abajo a la izquierda (píxeles de la foto original) |
| Ver índices | desplegable **Vista**: Original, GLI, VARI, ExG, máscara de vegetación |

Otras opciones: **Coordenadas…** (introducir coordenadas a mano), **Renombrar**, **Duplicar** (parcela nueva del
mismo tamaño), **Copiar a otra foto** (misma parcela en otras fechas), **Importar/Exportar** (JSON o CSV).

Las parcelas se **guardan automáticamente** en JSON y CSV junto al proyecto (`<proyecto>_parcelas.json/.csv`).

Convención de coordenadas: píxeles de la imagen original, origen arriba-izquierda, `x2` e `y2` excluidos
(ancho = x2 − x1). Si una parcela se sale de la imagen se recorta y se avisa en el control de calidad.

---

## 5. Cómo importar los datos del Arduino

Formato recomendado del CSV del Arduino (separador `,` o `;`):

```text
fecha,hora,parcela,lectura_adc,porcentaje_humedad
2026-10-12,15:35:34,1,480,8
2026-10-12,15:35:43,2,514,0
2026-10-12,15:35:48,3,490,5
```

| Columna | Qué hace el programa |
|---|---|
| `fecha`, `hora` | Se combinan en una marca temporal (`2026-10-12 15:35:34`) y se muestran la primera y la última medición de cada parcela. |
| `parcela` | Debe coincidir **exactamente** con el ID de la parcela dibujada (si el CSV usa `1, 2, 3`, llame a las parcelas `1, 2, 3`). |
| `lectura_adc` | Lectura **RAW** del sensor (`analogRead`). Es el **dato principal** del análisis y de las correlaciones. |
| `porcentaje_humedad` | Opcional. Porcentaje calculado **por el propio Arduino** (`map()`/`constrain()`). Se conserva **aparte** (`humedad_pct_arduino`), con su propia gráfica, pero **no sustituye** a la lectura RAW ni entra en las correlaciones: depende de la calibración programada en el Arduino. Se avisa si hay valores recortados a 0 % o 100 %. |

No hace falta una columna `medicion`: las lecturas de cada parcela se numeran por orden de aparición.
También se acepta el formato antiguo `parcela,medicion,humedad_raw`.

1. En **4. DATOS DE CAMPO** pulse **Importar CSV Arduino…**.
2. El programa calcula por parcela: **media, mediana, desviación estándar, mínimo, máximo y número de mediciones**.
3. Las filas no válidas **no se ocultan**: se informa de cada una.

**Los valores RAW no se convierten a porcentaje.** Si lo desea, en la pestaña *Datos de campo* →
**Calibración del sensor** introduzca *RAW suelo seco* y *RAW suelo húmedo* y active la casilla:

```text
humedad_% = (RAW − RAW_seco) / (RAW_húmedo − RAW_seco) × 100     (limitada a 0–100 %)
```

> Esta estimación depende de la calibración realizada y no representa una humedad volumétrica universal.
> El valor RAW original se conserva siempre.

**Estado general de la vegetación:** botón **Introducir observaciones…** → escala 1–5
(1 muy pobre · 2 pobre · 3 intermedio · 4 bueno · 5 muy bueno) y descripción libre (coloración, densidad,
estrés visible, uniformidad, observaciones). También puede importarse un CSV (`plantillas/observaciones_plantilla.csv`).

En `plantillas/arduino_humedad_csv.ino` hay un ejemplo de programa que imprime las lecturas en este formato.

---

## 6. Cómo analizar

1. En **3. ÍNDICES RGB** marque GLI, VARI y/o ExG (los índices experimentales están aparte).
2. (Opcional) **Opciones de análisis…**:
   - Preprocesamiento (normalización 0–1, excluir píxeles saturados/oscuros/transparentes, reducir resolución).
     Nada se aplica sin que usted lo active; todo queda registrado.
   - Escala de ExG: valores normalizados 0–1 o coordenadas cromáticas.
   - **Máscara RGB aproximada de vegetación** (ExG > umbral, o G > R y G > B) y elegir
     *Analizar todos los píxeles* o *Analizar solo píxeles clasificados como vegetación*.
   - Mapas: **escala común** (mismo rango para todas las fotos → comparables) o **escala automática**.
   - Valor de cada ROI en la tabla por parcela: media o mediana.
3. Pulse **▶ Analizar**. Verá una barra de progreso (`8 / 10 fotografías`); la interfaz no se congela y puede
   **Cancelar**. Si ya hay resultados, se pregunta si desea sobrescribirlos o guardarlos en una carpeta nueva.
4. Resultados (sección **5. RESULTADOS** y pestaña **Resultados**):
   - **Estadísticas**: tabla experimental por parcela y estadísticas por ROI.
   - **Correlaciones**: Pearson r, Spearman ρ, valores *p*, n e interpretación prudente.
   - **Gráficas** y **Mapas**.
   - **Control de calidad**: imágenes válidas, parcelas, NaN, divisiones por cero, datos que faltan…

Si después importa o cambia datos de campo **no hace falta volver a analizar las fotos**: las tablas,
correlaciones, gráficas, exportaciones e informe se recalculan con los datos actuales.

### Resultados generados (carpeta `output/<proyecto>/`)

| Carpeta / archivo | Contenido |
|---|---|
| `indices/<foto>_GLI.png`, `_VARI.png`, `_ExG.png` | mapas de índices con título, fórmula, barra de color, modo y rango de escala, parcelas |
| `indices/<foto>_mascara_vegetacion.png` | máscara RGB aproximada de vegetación y cobertura estimada |
| `rois/<foto>_parcelas.png`, `parcelas.json/.csv` | fotos con las parcelas dibujadas y definición de parcelas |
| `statistics/resultados_por_roi.csv` | mean, median, std, min, max, p5, p25, p75, p95, n por índice, parcela y foto |
| `statistics/resumen_imagenes.csv` | píxeles válidos/excluidos, cobertura, divisiones por cero por foto |
| `statistics/comparacion_entre_fotos.csv` | variabilidad de cada parcela entre fotos y cambio temporal |
| `statistics/evolucion_temporal.csv` | valores por parcela y fecha |
| `statistics/datos_campo_resumen.csv`, `observaciones.csv`, `control_calidad.csv` | datos de campo y QC |
| `exports/resultados_por_parcela.csv` | **tabla experimental central** (índices, cobertura, humedad, estado) |
| `exports/resultados_completos.csv`, `correlaciones.csv`, `datos_campo.csv` | exportaciones finales |
| `exports/TR_RGB_resultados.xlsx` | Excel: Resumen, Fotos, Parcelas, ROIs, Campo, Observaciones, Correlaciones… |
| `graphs/*.png` | barras por parcela, humedad, estado, scatter de correlaciones, evolución temporal |
| `reports/informe.html` / `informe.md` | informe automático |
| `analysis_metadata.json` | **reproducibilidad**: fecha, hora, versión, fórmulas, fotos, parcelas, configuración |

---

## 7. Cómo interpretar GLI

```text
GLI = (2G − R − B) / (2G + R + B)
```

- Rango teórico −1 a +1. Suelo desnudo ≈ −0.1 a 0.05; vegetación verde ≈ 0.1 a 0.5.
- **Más alto = más predominio relativo del verde** en la parcela.
- Sensible a la iluminación, las sombras, el balance de blancos y el color del suelo.
- Compare parcelas **de la misma foto** o fotos tomadas en condiciones parecidas.

## 8. Cómo interpretar VARI

```text
VARI = (G − R) / (G + R − B)
```

- Habitualmente −1 a +1, pero puede dar **valores extremos** cuando `G + R − B ≈ 0` (píxeles azulados, sombras).
- Más alto = más verde frente a rojo (relativo).
- Por defecto, los valores fuera de **[−1, 1] se recortan** a ese intervalo (opción `vari_clip` del proyecto) y el
  número de píxeles recortados aparece en el control de calidad y en la columna `VARI_recortados`.
  Sin el recorte, unos pocos píxeles con valores de ±80 desplazan la media de toda la parcela.

## 9. Cómo interpretar ExG

```text
ExG = 2G − R − B
```

- **Por defecto se calcula con coordenadas cromáticas** r = R/(R+G+B), g, b (Woebbecke et al., 1995):
  ExG = 2g − r − b, rango −1 a 2. Así el índice depende menos de la iluminación. Con RGB normalizado 0–1
  (opción del proyecto) el rango práctico es ≈ −0.2 a 0.8.
- La **máscara de vegetación** usa siempre ExG sobre RGB 0–1 con su umbral (opción `vegetation_exg_mode`), para
  que la cobertura vegetal no cambie al cambiar el modo del índice ExG.
- Más alto = más exceso de verde. Es el índice más usado para **separar vegetación del suelo**
  (por eso la máscara de vegetación usa ExG > umbral).
- Depende de la exposición de la foto.

**Índices experimentales** (separados de los principales, columnas `EXP_`): ExR = 1.4R − G,
ExGR = ExG − ExR, NGRDI = (G − R)/(G + R) (NGRDI **no es NDVI**). Fórmulas, rangos y limitaciones en el botón ⓘ.

Ninguno de estos índices mide clorofila, agua o salud de forma absoluta; **no permiten afirmar que una planta
esté enferma**.

---

## 10. Correlaciones

Se calculan sobre la **tabla por parcela** (n = número de parcelas con ambos datos):

- humedad RAW ↔ GLI, VARI, ExG
- estado general ↔ GLI, VARI, ExG
- humedad RAW ↔ cobertura vegetal estimada mediante RGB

Para cada una: **Pearson r** (asociación lineal), **Spearman ρ** (asociación monótona por rangos; más adecuada con
pocas parcelas y para el estado general, que es ordinal), valores *p* y **n**.

Con **n ≤ 8**, el valor *p* de Spearman se calcula de forma **exacta por permutaciones**. La aproximación habitual
da p = 0 cuando ρ = ±1, lo cual es falso: con 3 parcelas, un orden perfecto aparece por azar en 2 de las 6
permutaciones posibles (p = 0,333).

Cómo leerlas:
- |r| < 0.1 prácticamente nula · < 0.3 débil · < 0.7 moderada · ≥ 0.7 fuerte (criterio orientativo).
- Con **pocas parcelas** (n < 10) el resultado es **exploratorio**; con n < 5 un solo punto puede cambiarlo todo.
- **Una correlación no implica causalidad.** El programa escribe siempre «se observa una asociación…».
- **Sentido del sensor:** en muchos sensores capacitivos un RAW **más alto** indica suelo **más seco**. Una
  correlación negativa RAW–GLI significaría entonces más verdor en suelo más húmedo. Compruebe su sensor
  (mida en suelo seco y en agua).
- En los scatter sólo se dibuja la línea de tendencia con n ≥ 5 (configurable).

---

## 11. Exportación

En **6. EXPORTACIÓN**:

- **Exportar CSV**: resultados completos, por parcela, correlaciones y datos de campo (`exports/`).
  Por defecto los CSV usan `;` y decimal `,` para abrirse bien en Excel en español/catalán
  (cambie `"csv_format": "standard"` en `config.json` para `,` y `.`).
- **Exportar Excel**: `TR_RGB_resultados.xlsx` con hojas Resumen, Fotos, Parcelas, ROIs, Campo, Campo_resumen,
  Observaciones, Correlaciones y Control_calidad (requiere `openpyxl`).
- **Generar informe**: `reports/informe.html` (autocontenido, con gráficas) e `informe.md`. Separa
  **datos medidos**, **cálculos** e **interpretación** prudente, e incluye control de calidad y limitaciones.
  **No escribe conclusiones científicas**: esas las redacta usted.

Antes de reemplazar resultados existentes el programa pregunta **«¿Desea sobrescribir los resultados?»**.

---

## 12. Limitaciones

- Los índices utilizados se obtienen a partir de imágenes RGB y deben interpretarse como indicadores relativos.
  **No equivalen a NDVI** y no constituyen mediciones directas de clorofila, contenido hídrico o salud vegetal.
- Las correlaciones obtenidas describen asociaciones entre las variables medidas y **no permiten establecer
  causalidad** por sí solas.
- La calidad de los resultados depende de las condiciones de iluminación, altura de vuelo, orientación de la
  cámara, resolución, calibración del sensor y selección de las parcelas.
- La máscara de vegetación es aproximada (sombras, vegetación seca o suelos verdosos pueden clasificarse mal).
- Las cámaras de consumo aplican balance de blancos y procesado automáticos que pueden variar entre fotos.
- La humedad RAW depende del sensor, del suelo y de la inserción; sin calibración no es un porcentaje.
- La humedad se resume por parcela; si mide en días distintos a los vuelos, téngalo en cuenta al interpretar.

**Lo que el programa NO hace (a propósito):** calcular NDVI, inventar datos, modificar las imágenes, convertir RAW a
humedad sin calibración, eliminar outliers, ocultar resultados negativos, deformar escalas de gráficas, mezclar
datos sintéticos con reales, ni afirmar causalidad o enfermedad.

---

## 13. Solución de errores

| Problema | Solución |
|---|---|
| `run.bat` dice que no encuentra Python | Instale Python 3.10+ marcando «Add python.exe to PATH». |
| `No module named tkinter` | Reinstale Python marcando «tcl/tk and IDLE». |
| Falla la instalación de librerías | Ejecute `python -m pip install -r requirements.txt` y lea el error (¿Internet? ¿antivirus?). |
| El programa va lento o se queda sin memoria | *Opciones de análisis → Reducir resolución* (p. ej. 3000 px). Cierre otros programas. |
| «Imagen corrupta o ilegible» | Vuelva a copiar la foto desde la tarjeta del dron. |
| El CSV del Arduino da error | Compruebe la cabecera `parcela,medicion,humedad_raw`; el mensaje indica la fila con problemas. |
| «Parcela sin humedad» en el control de calidad | El ID de parcela del CSV debe ser idéntico al de la ROI (A ≠ a ≠ «A »). |
| Las correlaciones salen «—» | Se necesitan al menos 3 parcelas con ambos datos y que ninguna variable sea constante. |
| «No se pudo escribir… ¿está abierto en Excel?» | Cierre el CSV/XLSX en Excel y repita la exportación. |
| La correlación tiene el signo «al revés» | Revise el sentido del sensor capacitivo (RAW alto = seco en muchos modelos). |
| «Fotos no encontradas» al abrir un proyecto | Las fotos se han movido; el proyecto guarda rutas absolutas y relativas a su carpeta. |
| Otros errores | Consulte `logs/tr_rgb_analyzer.log`. |

---

## 14. Modo consola, demostración y tests

### Modo consola (sin interfaz)

```bash
python main.py
```

Sin argumentos analiza `input/fotos`, las parcelas de `input/parcelas/*.csv|*.json` y los CSV de
`input/datos_campo` (los archivos `observ*.csv` se leen como observaciones) y escribe en `output/`.

```bash
python main.py --project projects/mi_tr.json
python main.py --input input/fotos --output output
python main.py --input input/fotos --rois input/parcelas/parcelas.csv --field input/datos_campo/humedad.csv --only-vegetation --scale common
python main.py --help
```

Si ya existen resultados pregunta antes de sobrescribir (o use `--overwrite`); si no se confirma, crea una carpeta
nueva con fecha y hora.

### Modo demostración

```bash
python demo.py
```

Genera fotos, parcelas, humedad y observaciones **sintéticas** en `demo/` y ejecuta el análisis completo.
Todo queda marcado como **«DEMO — NO SON DATOS EXPERIMENTALES»** (marca de agua en gráficas, banner en el informe,
columna `origen_datos` en los CSV) y se guarda siempre en `demo/`, nunca en `input/` ni `output/`.
`python demo.py --gui` abre además el proyecto demo en la interfaz.

### Tests

```bash
python -m pytest
```

Cubren las fórmulas GLI/VARI/ExG, divisiones por cero, ROIs (incluidas las que se salen de la imagen),
estadísticas, lectura de CSV (incluido un CSV vacío), calibración, correlaciones (comparadas con SciPy),
control de calidad y el flujo completo (comprobando que las fotos originales no cambian).

---

## Reproducibilidad

Cada análisis guarda `analysis_metadata.json` con fecha, hora, versión del software y de las librerías,
índices y fórmulas, opciones de preprocesamiento, máscara, escala de los mapas, fotos (ruta, tamaño, fecha),
parcelas, datos de campo, calibración, configuración completa y control de calidad. Junto con el proyecto `.json`
permite repetir exactamente el experimento.

Licencia: MIT (ver `LICENSE`).
