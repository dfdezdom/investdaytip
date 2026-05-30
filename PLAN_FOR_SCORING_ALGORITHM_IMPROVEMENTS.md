# Plan de Mejora para el Algoritmo de Scoring - InvestDayTip

## Antecedentes
Este plan fue desarrollado basado en el análisis del algoritmo de scoring actual (`src/investdaytip/scoring.py`) y el contexto de mercado observado a través del advisor subagent (VIX neutral, riesgo de burbuja medio, señales de cautela en capex de IA).

## Estado Actual del Algoritmo

### Fortalezas:
- Transparencia en pesos y factores claramente definidos
- Robustez en manejo de datos faltantes (valor neutral 50)
- Especialización con modelos diferenciados para acciones vs ETFs
- Interpretabilidad mediante generación de notas explicativas (rationale)
- Normalización consistente usando funciones lineales por tramos

### Limitaciones Identificadas:
1. Pesos estáticos que no se ajustan dinámicamente al régimen de mercado
2. Enfoque fundamental puro con poca consideración de factores macro o de sentimiento
3. Normalización lineal que puede no capturar relaciones no lineales óptimas
4. Ausencia de factores de calidad de gestión (eficiencia de asignación de capital)
5. Sensibilidad limitada a burbujas específicas del sector (aunque el advisor las detecta)

## Objetivos de Mejora

### Primario:
Mejorar la capacidad del algoritmo para identificar oportunidades en entornos de incertidumbre (neutral con señales de cautela)

### Secundarios:
- Aumentar adaptabilidad a diferentes regímenes de mercado
- Mejorar detección de riesgos específicos (sostenibilidad del capex, calidad de gestión)
- Mantener o mejorar interpretabilidad
- Preservar compatibilidad hacia atrás

## Propuestas de Mejora (Enfoque Fásico)

### Fase 1: Mejoras Inmediatas (Bajo Esfuerzo/Alto Impacto)

**A. Ajuste Dinámico de Pesos basado en Régimen de Mercado**
- Implementar modificador de pesos basado en indicadores del advisor (VIX, riesgo de burbuja)
- Ejemplo: En alta incertidumbre (VIX > 25), aumentar peso de Health y reducir Trend
- Ventaja: Mayor adaptabilidad sin cambiar el núcleo del algoritmo

**B. Enriquecimiento del Factor Health para Acciones**
- Añadir métrica de eficiencia de capital: ROIC (Return on Invested Capital) cuando esté disponible
- Incluir tendencia de mejora/deterioro de márgenes (no solo nivel absoluto)
- Ventaja: Mejor captura de sostenibilidad de modelos de negocio

**C. Factor de Valoración Relativa por Sector para ETFs**
- Añadir comparación de valuation del ETF vs su benchmark sectorial
- Ventaja: Mejor identificación de ETFs infra/over-valorados dentro de su categoría

### Fase 2: Mejoras Medianas (Esfuerzo Medio/Impacto Medio)

**D. Regime Detection Integrado**
- Desarrollar indicador interno de régimen (VIX, spread crédito, momentum índice)
- Usar régimen para seleccionar entre conjuntos de pesos predefinidos
- Ventaja: Más sofisticado que simples modificadores

**E. Normalización Adaptativa**
- Reemplazar funciones lineales fijas con funciones que se ajusten a distribuciones históricas
- Usar percentiles rolling en lugar de mínimos/máximos fijos
- Ventaja: Mejor manejo de regimes de valoración cambiantes

### Fase 3: Exploración (Esfuerzo Alto/Impacto Incerto)

**F. Factores de Calidad de Gestión**
- Métricas de asignación de capital: crecimiento sostenible de ROC vs WACC
- Consistencia en cumplimiento de guidance
- Balance entre reinversión y retorno a accionistas

**G. Enfoque Ensemble Ligero**
- Mantener modelo actual como "base" agregando componente simple de ML para ajustes finales
- Regresión lineal ligera sobre factores para predecir retorno forward 6-12m

## Enfoque de Implementación

### Principios Rectores:
1. Compatibilidad hacia atrás: Todos los cambios deben permitir revertir al comportamiento original
2. Incrementalismo: Implementar en fases pequeñas, testeables independiente
3. Transparencia mantenida: Cualquier complejidad añadida debe venir con explicación clara
4. Configurable: Nuevos parámetros deben ser ajustables sin código nuevo

### Estrategia de Rollout:
1. Implementar como flags de configuración (desactivados por defecto)
2. Período de prueba parallela: ejecutar nuevo y viejo scoring lado a lado
3. Métricas de comparación: rank correlation, distribución de scores, predictive power
4. Activación gradual basada en evidencia de mejora

## Métricas de Evaluación
- **Estabilidad**: Menor volatilidad en scores para mismos fundamentales
- **Predictividad**: Mejor correlación con retornos forward 3-6-12 meses
- **Discriminación**: Mejor separación entre quintiles de rendimiento futuro
- **Robustez**: Menor sensibilidad a outliers en datos de entrada

## Próximos Pasos Sugeridos
1. Validar hipótesis con análisis retrospectivo (¿habría mejorado el scoring actual con pesos dinámicos durante 2022?)
2. Definir configuración por régimen (mapear regímenes a ajustes de peso)
3. Prototipar mejora más impactante primero (probablemente ajuste dinámico de pesos)
4. Establecer checklist de implementación para cuando salga del modo planificación

## Archivos Relacionados
- `src/investdaytip/scoring.py` - Algoritmo de scoring principal
- `src/investdaytip/advisor.py` - Fuente de indicadores de régimen (VIX, riesgo burbuja)
- `src/investdaytip/recommender.py` - Orquestación que utiliza el scoring
- `tests/test_scoring.py` - Tests existentes que deben seguir pasando

---
*Plan creado: [Fecha actual basada en el entorno: Sáb 30 Mayo 2026]*
*Para usar en futuras sesiones de desarrollo cuando salga del modo planificación*