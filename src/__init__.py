"""TR RGB Analyzer — núcleo científico (sin interfaz gráfica).

Módulos:
    image_loader   Carga de fotografías (sólo lectura)
    preprocessing  Preprocesamiento opcional y registrado
    indices        Índices RGB (GLI, VARI, ExG + experimentales) y máscara de vegetación
    roi            Parcelas / ROIs rectangulares
    statistics     Estadística descriptiva y agregación por parcela
    field_data     Humedad (Arduino), calibración y observaciones visuales
    correlation    Pearson y Spearman con interpretación prudente
    graphs         Mapas y gráficas (Matplotlib)
    validation     Control de calidad
    project        Proyectos (JSON)
    pipeline       Flujo de análisis completo
    export         CSV y Excel
    report         Informe HTML / Markdown
    utils          Utilidades generales
"""

from .utils import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
