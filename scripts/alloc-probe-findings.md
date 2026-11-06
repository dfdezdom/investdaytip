# alloc-probe — resultados y cierre de la línea (2026-11-06)

Experimento: ¿se puede replicar el esqueleto de un *systematic global macro* (estilo
Quantedge) con ETFs accesibles y sin apalancamiento? Código: `scripts/alloc_probe.py`
(reproducible con los CSV de precios fijados en disco). Rúbrica de corte declarada
antes de correr: batir al 60/40 en **Sharpe y drawdown fuera de muestra**.

## 1. Motor de asignación (trend 12-1 + inverse-vol + vol target 10%)

| Cartera (2004-2026) | CAGR | Vol | Sharpe | MaxDD | Calmar |
|---|---|---|---|---|---|
| trend + vol target | 3.29% | 5.82% | 0.59 | −16.6% | 0.20 |
| ídem apalancado ×1.72 a vol 10% | 5.50% | 10.00% | 0.59 | −27.5% | 0.20 |
| 60/40 (SPY/IEF) | 8.16% | 10.74% | 0.79 | −31.4% | 0.26 |

- Exposición media 94.4%: el bajo retorno **no** es caja ociosa.
- El apalancamiento sube el retorno y **no mueve el Sharpe**: no hay alpha oculto
  que copiar, solo riesgo, mercados y ejecución.
- **Rechazado.**

## 2. Risk budget por sleeve (renta variable / bonos / commodities / RE / FX)

Sharpe 0.58 vs 0.59, CAGR −0.11 pp, DD +1 pp. La concentración en bonos no era la
causa raíz. **Rechazado.**

## 3. Overlay de tendencia sobre 60/40

| Muestra | 60/40 | + overlay RV→caja | + overlay ambas patas |
|---|---|---|---|
| 2004-2026 | 0.79 / DD −31.4% | **0.86** / −22.4% | — |
| 1993-2026 (VFINX/VFITX) | 0.81 / −32.4% | **0.93** / −20.1% | 0.91 / −19.3% |
| 1993-2003 | 0.93 / −24.3% | 1.14 / −11.5% | 1.11 / −11.5% |
| 2004-2015 | 0.64 / −32.4% | 0.93 / −9.2% | 0.87 / −9.2% |
| 2016-2026 | **0.91** / −19.8% | 0.76 / −20.1% | 0.78 / −19.3% |

- Mejora en 2 de 3 décadas y en la muestra completa (Sharpe +0.12, DD −12 pp,
  Calmar 0.40 vs 0.26), pero **pierde en las tres métricas en 2016-2026**.
- La hipótesis pre-registrada «filtrar también la pata de bonos arregla 2022»
  **se rechaza**: 0.91 vs 0.93 en la muestra larga, 0.78 vs 0.76 en la reciente.
  Filtrar solo la renta variable y rotar a **caja** es lo mejor de las variantes.
- La ventaja es **seguro de crash** (2008, 2000-02), no una fuente de alpha; su
  coste es 0.5-0.8 pp de CAGR.

## Veredicto

Ninguna variante pasa el gate tal como se definió (Sharpe **y** DD fuera de
muestra). El overlay es la única línea con evidencia real y aun así es
dependiente de régimen: 33 años son ~3 regímenes, muestra insuficiente para
declarar una prima estable.

## Sesgos declarados

Survivorship del universo ETF; caja al 0%; sin financiación ni slippage extra;
costes 10 bp por turnover (24.4%/mes de turnover en el motor).

## Qué haría falta para reabrir

1. Walk-forward con **selección de regla dentro de cada fold** (no una regla fija
   elegida mirando el resultado).
2. 60/40 no-US (Europa, Japón) como validación cruzada.
3. Futuros (ES/ZN/GC/CL/6E) para pasar de 22 mercados correlacionados a 40+.

Hasta entonces la línea queda **archivada como experimento documentado**: no se
publica como módulo ni como cartera modelo.
