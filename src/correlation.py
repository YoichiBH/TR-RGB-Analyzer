"""Correlaciones de Pearson y Spearman con lenguaje prudente.

* Pearson r: asociación LINEAL.
* Spearman ρ: asociación MONÓTONA basada en rangos; más robusta con pocos
  datos, valores extremos o variables ordinales (estado general 1–5).
* Los coeficientes se calculan siempre con NumPy. Los valores p se obtienen
  con SciPy si está instalado y, si no, con una implementación interna de la
  distribución t de Student (misma aproximación que usa SciPy para Spearman).
* Con muestras pequeñas (n ≤ 8) el valor p de Spearman se calcula de forma
  EXACTA probando todas las permutaciones posibles. La aproximación t da
  p = 0 cuando ρ = ±1, lo que es falso: con n = 3, un orden perfecto aparece
  por azar en 2 de las 6 permutaciones (p = 0,333).
* Con pocas parcelas los resultados tienen valor EXPLORATORIO.
* Una correlación describe una asociación; NUNCA demuestra causalidad.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

try:  # SciPy es opcional
    from scipy import stats as _scipy_stats  # type: ignore

    HAS_SCIPY = True
except Exception:  # noqa: BLE001
    _scipy_stats = None
    HAS_SCIPY = False

CAUSALITY_NOTICE = (
    "Las correlaciones obtenidas describen asociaciones entre las variables medidas "
    "y no permiten establecer causalidad por sí solas."
)

VARIABLE_LABELS = {
    "humedad_raw_media": "Humedad RAW (media)",
    "humedad_estimada_pct_media": "Humedad estimada (%)",
    "humedad_pct_arduino_media": "porcentaje_humedad del Arduino (%)",
    "estado_general": "Estado general (1–5)",
    "cobertura_rgb_pct": "Cobertura vegetal estimada RGB (%)",
    "GLI": "GLI", "VARI": "VARI", "ExG": "ExG",
}


def label(var: str) -> str:
    if var.startswith("EXP_"):
        return f"{var[4:]} (experimental)"
    return VARIABLE_LABELS.get(var, var)


@dataclass
class CorrelationResult:
    """Resultado de correlacionar dos variables."""

    var_x: str
    var_y: str
    n: int
    pearson_r: float = math.nan
    pearson_p: float = math.nan
    spearman_rho: float = math.nan
    spearman_p: float = math.nan
    p_method: str = ""
    warning: str = ""
    interpretation: str = ""
    parcels: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        d = asdict(self)
        d["variable_x"] = label(self.var_x)
        d["variable_y"] = label(self.var_y)
        d["parcels"] = ", ".join(self.parcels)
        return d


# ---------------------------------------------------------------------------
# Cálculos numéricos
# ---------------------------------------------------------------------------
def rankdata(values) -> np.ndarray:
    """Rangos 1..n con empates promediados (equivalente a scipy.stats.rankdata)."""
    a = np.asarray(values, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    sorted_a = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def pearson_r(x, y) -> float:
    """Coeficiente de Pearson; NaN si alguna variable es constante o n < 2."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2 or x.size != y.size:
        return math.nan
    dx, dy = x - x.mean(), y - y.mean()
    den = math.sqrt(float((dx * dx).sum()) * float((dy * dy).sum()))
    if den == 0:
        return math.nan
    r = float((dx * dy).sum() / den)
    return max(-1.0, min(1.0, r))


def spearman_rho(x, y) -> float:
    """Coeficiente de Spearman = Pearson sobre los rangos."""
    return pearson_r(rankdata(x), rankdata(y))


def _betacf(a: float, b: float, x: float, max_iter: int = 300, eps: float = 1e-14) -> float:
    """Fracción continua de la beta incompleta (método de Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_regularized(a: float, b: float, x: float) -> float:
    """Función beta incompleta regularizada I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    bt = math.exp(ln_bt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


EXACT_SPEARMAN_MAX_N = 8


def spearman_exact_pvalue(x, y) -> float:
    """Valor p bilateral EXACTO de Spearman por permutaciones (sólo n pequeño).

    p = proporción de permutaciones de y cuyo |ρ| es mayor o igual que el observado.
    """
    from itertools import permutations

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    if n < 3 or n != y.size or n > EXACT_SPEARMAN_MAX_N:
        return math.nan
    rho_obs = spearman_rho(x, y)
    if not math.isfinite(rho_obs):
        return math.nan
    rx, ry = rankdata(x), rankdata(y)
    count = total = 0
    for perm in permutations(range(n)):
        rho = pearson_r(rx, ry[list(perm)])
        total += 1
        if math.isfinite(rho) and abs(rho) >= abs(rho_obs) - 1e-12:
            count += 1
    return count / total


def t_test_pvalue(r: float, n: int) -> float:
    """Valor p bilateral de H0: ρ = 0 mediante t = r·√((n−2)/(1−r²))."""
    if n < 3 or not math.isfinite(r):
        return math.nan
    if abs(r) >= 1.0:
        return 0.0
    df = n - 2
    t = abs(r) * math.sqrt(df / (1.0 - r * r))
    return float(betainc_regularized(df / 2.0, 0.5, df / (df + t * t)))


# ---------------------------------------------------------------------------
# Interpretación prudente
# ---------------------------------------------------------------------------
def strength_label(r: float) -> str:
    """Intensidad orientativa de |r| (criterio habitual, no una regla estricta)."""
    a = abs(r)
    if a < 0.1:
        return "prácticamente nula"
    if a < 0.3:
        return "débil"
    if a < 0.7:
        return "moderada"
    return "fuerte"


def interpret(res: CorrelationResult, exploratory_n: int = 10) -> str:
    """Frase descriptiva prudente, sin afirmar causalidad."""
    lx, ly = label(res.var_x), label(res.var_y)
    if not math.isfinite(res.pearson_r) and not math.isfinite(res.spearman_rho):
        return f"No se puede calcular la correlación entre {lx} y {ly}. {res.warning}".strip()
    main = res.spearman_rho if res.var_x == "estado_general" or res.var_y == "estado_general" else res.pearson_r
    if not math.isfinite(main):
        main = res.pearson_r if math.isfinite(res.pearson_r) else res.spearman_rho
    strength = strength_label(main)
    if strength == "prácticamente nula":
        text = f"No se observa una asociación apreciable entre {lx} y {ly}"
    else:
        sign = "positiva" if main > 0 else "negativa"
        text = f"Se observa una asociación {sign} {strength} entre {lx} y {ly}"
    text += (f" (Pearson r = {res.pearson_r:.2f}; Spearman ρ = {res.spearman_rho:.2f}; n = {res.n})."
             if math.isfinite(res.pearson_r) and math.isfinite(res.spearman_rho)
             else f" (n = {res.n}).")
    if res.n < exploratory_n:
        text += f" Con n = {res.n} el resultado tiene valor exploratorio."
    text += " Una correlación no permite establecer causalidad."
    return text


def correlate(x, y, var_x: str, var_y: str, labels: list[str] | None = None,
              min_n: int = 3, exploratory_n: int = 10) -> CorrelationResult:
    """Correlaciona dos variables ignorando los pares con valores ausentes.

    Args:
        x, y: valores (mismo orden).
        var_x, var_y: nombres de las variables.
        labels: identificadores de parcela (para documentar qué pares se usaron).
        min_n: mínimo de pares para calcular.
        exploratory_n: por debajo de este n se marca como exploratorio.
    """
    x = pd.to_numeric(pd.Series(list(x)), errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(pd.Series(list(y)), errors="coerce").to_numpy(dtype=float)
    labels = list(labels) if labels is not None else [str(i) for i in range(len(x))]
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    used = [lab for lab, k in zip(labels, ok) if k]
    res = CorrelationResult(var_x=var_x, var_y=var_y, n=int(x.size), parcels=[str(u) for u in used])
    warnings = []
    if x.size < min_n:
        res.warning = f"Datos insuficientes: n = {x.size} (mínimo {min_n})."
        res.interpretation = interpret(res, exploratory_n)
        return res
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        res.warning = "Una de las variables es constante; la correlación no está definida."
        res.interpretation = interpret(res, exploratory_n)
        return res
    res.pearson_r = pearson_r(x, y)
    res.spearman_rho = spearman_rho(x, y)
    if HAS_SCIPY:
        try:
            res.pearson_p = float(_scipy_stats.pearsonr(x, y)[1])
            res.spearman_p = float(_scipy_stats.spearmanr(x, y)[1])
            res.p_method = "SciPy"
        except Exception:  # noqa: BLE001
            res.p_method = ""
    if not res.p_method:
        res.pearson_p = t_test_pvalue(res.pearson_r, res.n)
        res.spearman_p = t_test_pvalue(res.spearman_rho, res.n)
        res.p_method = "t de Student (implementación interna)"
    if res.n <= EXACT_SPEARMAN_MAX_N:
        res.spearman_p = spearman_exact_pvalue(x, y)
        res.p_method += f"; Spearman exacto por permutaciones (n ≤ {EXACT_SPEARMAN_MAX_N})"
    if res.n < exploratory_n:
        warnings.append(f"Pocas parcelas (n = {res.n}): resultado exploratorio; los valores p son orientativos.")
    if res.n < 5:
        warnings.append("Con n < 5 un único punto puede cambiar totalmente el coeficiente.")
    res.warning = " ".join(warnings)
    res.interpretation = interpret(res, exploratory_n)
    return res


def main_pairs(index_keys: list[str], has_calibration: bool = False) -> list[tuple[str, str]]:
    """Pares de variables a correlacionar en el experimento principal."""
    pairs = [("humedad_raw_media", k) for k in index_keys]
    pairs += [("estado_general", k) for k in index_keys]
    pairs.append(("humedad_raw_media", "cobertura_rgb_pct"))
    return pairs


def correlation_table(df: pd.DataFrame, pairs: list[tuple[str, str]], id_col: str = "parcela",
                      min_n: int = 3, exploratory_n: int = 10) -> tuple[pd.DataFrame, list[CorrelationResult]]:
    """Calcula todas las correlaciones de ``pairs`` sobre la tabla por parcela."""
    results = []
    for vx, vy in pairs:
        if df is None or df.empty or vx not in df or vy not in df:
            res = CorrelationResult(var_x=vx, var_y=vy, n=0, warning="Variable no disponible en la tabla.")
            res.interpretation = interpret(res, exploratory_n)
        else:
            res = correlate(df[vx], df[vy], vx, vy, labels=df[id_col].astype(str).tolist() if id_col in df else None,
                            min_n=min_n, exploratory_n=exploratory_n)
        results.append(res)
    cols = ["variable_x", "variable_y", "n", "pearson_r", "pearson_p", "spearman_rho", "spearman_p",
            "p_method", "warning", "interpretation", "parcels", "var_x", "var_y"]
    table = pd.DataFrame([r.to_row() for r in results], columns=cols) if results else pd.DataFrame(columns=cols)
    return table, results
