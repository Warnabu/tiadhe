# Machine Learning para Detección de Subida - Guaguas FTV

## Estado Actual (Sistema Probabilístico)

El sistema actual usa un **motor de 12 señales probabilísticas** (validado 100% accuracy en 15 escenarios):

| # | Señal | Tipo | P(Subir) | Peso |
|---|-------|------|----------|------|
| 1 | Botón "voy a subir" | Explícita | 0.95 | 1.0 |
| 2 | GPS en parada + consultó app | Implícita | 0.80 | 1.0 |
| 3 | GPS en parada sin app | Implícita | 0.50 | 1.0 |
| 4 | Camina hacia parada + app | Implícita | 0.70 | 1.0 |
| 5 | Acelerómetro: patrón bus | Implícita | 0.85 | 0.8 |
| 6 | Inferencia colectiva 3+ users | Social | 0.95 | 1.5 |
| 7 | GPS en trazado a velocidad bus | Implícita | 0.90 | 1.2 |
| 8 | Espera >5 min en parada | Implícita | 0.60 | 0.7 |
| 9 | Histórico usuario (reputación) | Contextual | Variable | 0.5-1.0 |
| 10 | Hora programada ± ventana | Temporal | 0.65 | 0.8 |
| 11 | Variante de línea detectada | Contextual | 0.75 | 0.9 |
| 12 | Edge cases (callejón, GPS ruido) | Corrección | Variable | 0.3-0.5 |

**Score final**: `Σ(signal_i * weight_i * reputation) / Σ(weight_i * reputation)`

**Umbrales**:
- `≥0.9` → `confirmar_auto`
- `0.7-0.9` → `notificar_confirmar`
- `0.4-0.7` → `sospechoso`
- `<0.4` → `descartar`

---

## ¿Por Qué Considerar ML?

### Ventajas del Sistema Actual
- ✅ **Explicable**: Cada decisión se puede auditar señal por señal
- ✅ **Controlable**: Pesos ajustables sin reentrenar
- ✅ **Robusto a pocos datos**: Funciona desde el día 1
- ✅ **Privacidad**: No requiere enviar datos crudos al servidor
- ✅ **100% accuracy** en prototipo validado

### Limitaciones Actuales
- ❌ Pesos fijos (heurísticos, no aprendidos)
- ❌ Interacciones no lineales entre señales no capturadas
- ❌ No adapta automáticamente a patrones locales (ej. parada específica con GPS ruidoso)
- ❌ Difícil añadir nuevas señales sin retunear todo

---

## Enfoques ML Recomendados

### 1. Gradient Boosting (XGBoost / LightGBM) - **Recomendado para empezar**
```
Features (por evento de parada):
- 12 señales actuales (normalizadas 0-1)
- Reputación usuario (0.3-1.0)
- Hora del día (sin/cos)
- Día semana (one-hot)
- Línea + sentido + variante (embeddings)
- Cluster parada (embedding)
- Histórico: últimas N subidas del usuario (éxito/fallo)
- Contexto: clima, festivo, hora punta

Target: subida_real (0/1) - etiquetado por:
  - Botón explícito confirmado
  - Inferencia colectiva quorum ≥3
  - Validación manual (admin)
```

**Ventajas**: Rápido, interpretable (SHAP values), maneja features categóricas, robusto a outliers.

### 2. Red Neuronal Simple (MLP)
```
Input: 30-50 features → Hidden: 64-128 → Output: 1 (sigmoid)
Loss: Binary Cross Entropy + class_weight (imbalance)
```
**Mejor para**: Capturar interacciones no lineales complejas.
**Riesgo**: Overfitting con pocos datos, menos interpretable.

### 3. Learning to Rank (LambdaMART)
Si el objetivo es **ordenar candidatos** (qué usuario subió realmente) en lugar de clasificar binario.

---

## Pipeline de Datos Propuesto

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Eventos    │────▶│  Feature    │────▶│  Etiquetado │────▶│  Entrenam.  │
│  (parquet)  │     │  Engineering│     │  (semi-auto)│     │  (offline)  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
       ▲                                                    │
       │                                                    ▼
       │                      ┌─────────────┐     ┌─────────────┐
       └──────────────────────│  Inferencia │◀────│  Modelo     │
                              │  (online)   │     │  (artifact) │
                              └─────────────┘     └─────────────┘
```

### Fuentes de Etiquetas (Ground Truth)
1. **Botón explícito** → `label=1` (alta confianza)
2. **Inferencia colectiva quorum≥3** → `label=1` (alta confianza)
3. **Usuario rechaza notificación** → `label=0` (media confianza)
4. **Timeout 2 min sin confirmar** → `label=0` (baja confianza)
5. **Validación manual admin** → `label=1/0` (máxima confianza)

---

## Implementación Práctica (Primer Paso)

### Opción A: Híbrido (Recomendado)
Mantener motor probabilístico + añadir **ML como señal #13**

```python
# Nueva señal ML
def senal_ml(features: dict) -> tuple[float, float]:
    """Devuelve (p_subir, peso)"""
    proba = model.predict_proba([features])[0][1]
    peso = min(1.0, max(0.3, proba))  # calibrar
    return proba, peso
```

### Opción B: Reemplazo gradual
1. Entrenar modelo offline con datos históricos
2. A/B test: 10% tráfico → ML, 90% → probabilístico
3. Comparar métricas: precision@recall, F1, latencia
4. Migración progresiva

---

## Datos Necesarios para Empezar

| Dato | Disponibilidad | Comentario |
|------|----------------|------------|
| 12 señales por evento | ✅ Ya en `motor_senales.py` | Extraer a parquet |
| Reputación usuario | ✅ En `comunidad_pg.py` | Normalizar 0-1 |
| Etiquetas ground truth | ⚠️ Parcial | Requiere logging de feedback usuario |
| Contexto (clima, festivos) | ❌ No | Añadir API externa |

---

## Próximos Pasos Sugeridos

1. **Semana 1**: Instrumentar logging de features + labels en producción (BigQuery/Parquet)
2. **Semana 2**: Etiquetado semi-automático (reglas + revisión manual 100 muestras)
3. **Semana 3**: Entrenar XGBoost baseline, evaluar con cross-validation temporal
4. **Semana 4**: A/B test shadow mode (ML score logged, no usado para decisiones)
5. **Semana 5**: Si mejora ≥5% F1 → habilitar como señal #13 con peso bajo
6. **Semana 6+**: Iterar features, calibrar pesos, considerar reemplazo completo

---

## Métricas de Éxito

| Métrica | Actual (Probabilístico) | Objetivo ML |
|---------|------------------------|-------------|
| Accuracy | 100% (prototipo 15 casos) | ≥99% |
| Precision | 100% | ≥98% |
| Recall | 100% | ≥98% |
| F1 Score | 100% | ≥98% |
| Latencia p99 | <5ms | <20ms |
| Explicabilidad | Completa | SHAP values |

---

## Archivos Relacionados

- `motor_senales.py` - Señales 1-12 actuales
- `prototipo_sube.py` - Simulador + evaluación (15 escenarios)
- `test_motor_senales.py` - Tests integración
- `comunidad_pg.py` - Reputación usuario (feature clave)
- `api_sube.py` - Endpoints para logging eventos