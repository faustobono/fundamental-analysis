// Glosario compartido entre el screener y el análisis profundo.
//
// Mismo criterio que format.js: un diccionario estático en el front, con clave
// en la que el server ya manda (`metric.name`, `ratio.name`, `band.metric`, o
// una clave fija para conceptos que no son una métrica puntual). El server no
// manda la explicación en cada respuesta a propósito — el texto no cambia entre
// requests, así que mandarlo en cada payload sería peso muerto en cada fetch.
//
// Cada entrada es {titulo, texto}. El texto intenta decir *qué es* y, cuando
// aporta, *cómo se calcula* — no vender la métrica, explicarla.

const GLOSSARY = {
  // --- ratios de valuación -------------------------------------------------

  pe: {
    titulo: "P/E (Price / Earnings)",
    texto:
      "Precio de la acción dividido por la ganancia por acción. Cuántos años de " +
      "ganancia actual pagás al comprar. Un P/E negativo no existe como tal — la " +
      "empresa pierde plata — así que ese caso se descarta en vez de mostrarse " +
      "como 'barato'.",
  },
  pb: {
    titulo: "P/B (Price / Book)",
    texto:
      "Precio de mercado dividido por el patrimonio contable. Cuánto paga el " +
      "mercado por cada peso de patrimonio en libros. Un P/B alto no es malo por " +
      "sí solo: negocios con poco activo físico (software, marcas) suelen tener " +
      "P/B alto sin que signifique que están caros.",
  },
  ev_ebitda: {
    titulo: "EV/EBITDA",
    texto:
      "Enterprise Value (market cap + deuda neta) sobre EBITDA. A diferencia del " +
      "P/E, incluye la deuda de la empresa — por eso se usa para comparar " +
      "empresas con estructuras de capital distintas.",
  },
  ev_revenue: {
    titulo: "EV/Revenue",
    texto:
      "Enterprise Value sobre ingresos. Se usa cuando la empresa todavía no tiene " +
      "ganancias positivas y el P/E no se puede calcular.",
  },
  fcf_yield: {
    titulo: "FCF yield (Free Cash Flow yield)",
    texto:
      "Flujo de caja libre dividido por market cap. Al revés que el P/E: acá más " +
      "alto es más barato. Mide cuánta caja genera el negocio en relación a lo " +
      "que cuesta comprarlo — para muchos inversores es más difícil de maquillar " +
      "contablemente que la ganancia neta.",
  },
  ps: {
    titulo: "P/S (Price / Sales)",
    texto: "Precio de mercado sobre ingresos. Similar al EV/Revenue pero sin restar la deuda.",
  },
  dividend_yield: {
    titulo: "Dividend yield",
    texto: "Dividendo anual por acción dividido por el precio actual. Cuánto rinde en dividendos, sin contar la variación del precio.",
  },

  // --- rentabilidad ---------------------------------------------------------

  roic: {
    titulo: "ROIC (Return on Invested Capital)",
    texto:
      "Aproximación: EBIT × (1 − tasa impositiva efectiva) / (deuda + patrimonio). " +
      "Mide cuánto retorno genera el negocio sobre el capital que usa para " +
      "operar, sin importar cómo se financió (deuda o equity). Es una " +
      "aproximación para comparar empresas entre sí, no un cálculo de precisión " +
      "para valuación — no resta caja excedente ni excluye goodwill.",
  },
  roe: {
    titulo: "ROE (Return on Equity)",
    texto:
      "Ganancia neta dividida por patrimonio. A diferencia del ROIC, sí depende " +
      "de cuánta deuda usa la empresa: más deuda puede inflar el ROE sin que el " +
      "negocio sea mejor — por eso conviene mirarlo junto al ROIC y al ratio " +
      "deuda/patrimonio, no solo.",
  },
  gross_margin: {
    titulo: "Margen bruto",
    texto:
      "Ganancia bruta (ingresos − costo de lo vendido) sobre ingresos. Si figura " +
      "sin dato en un banco u otra financiera, no es un error: esas empresas no " +
      "tienen 'costo de lo vendido' en el sentido tradicional, así que el ratio " +
      "no aplica a su negocio.",
  },
  operating_margin: {
    titulo: "Margen operativo",
    texto: "Resultado operativo sobre ingresos — la ganancia del negocio antes de intereses e impuestos, como % de lo que factura.",
  },
  net_margin: {
    titulo: "Margen neto",
    texto: "Ganancia neta sobre ingresos — lo que queda para el accionista después de todo, como % de lo que factura la empresa.",
  },
  roa: {
    titulo: "ROA (Return on Assets)",
    texto:
      "Ganancia neta dividida por el activo total. Cuánto retorno genera la " +
      "empresa sobre todo lo que posee, sin importar cómo lo financió. Un ROA " +
      "bajo con un ROE alto suele significar que la rentabilidad para el " +
      "accionista viene apalancada con deuda, no del negocio en sí.",
  },
  asset_turnover: {
    titulo: "Rotación de activos",
    texto: "Ingresos divididos por activo total. Cuánta venta genera la empresa por cada peso de activo — una medida de eficiencia operativa, no de rentabilidad.",
  },
  effective_tax_rate: {
    titulo: "Tasa impositiva efectiva",
    texto:
      "Impuesto a las ganancias pagado sobre la ganancia antes de impuestos, " +
      "tomado del propio balance. Cuando no se puede derivar (o da un número " +
      "fuera de un rango creíble — créditos fiscales, quebrantos), se usa una " +
      "tasa por defecto y queda marcado en las advertencias.",
  },

  // --- crecimiento -----------------------------------------------------------

  revenue_growth_yoy: {
    titulo: "Crecimiento de ingresos YoY",
    texto: "Variación de los ingresos contra el mismo período del año anterior (year over year).",
  },
  revenue: { titulo: "Ingresos", texto: "Ingresos totales del ejercicio, tal como los reporta la empresa." },
  eps_diluted: {
    titulo: "EPS diluido",
    texto:
      "Ganancia por acción, contando todas las acciones que existirían si se " +
      "ejercieran opciones, warrants y convertibles pendientes. Es más " +
      "conservador que el EPS básico porque asume la dilución máxima posible.",
  },
  free_cash_flow: {
    titulo: "Free Cash Flow (FCF)",
    texto: "Caja generada por las operaciones, menos las inversiones de capital (capex). Es la caja que le queda a la empresa después de mantener y hacer crecer el negocio.",
  },
  fcf_after_sbc: {
    titulo: "FCF − SBC",
    texto:
      "Free cash flow menos la compensación en acciones (stock-based " +
      "compensation) del período. El SBC no sale de la caja de la empresa, así " +
      "que el FCF reportado lo ignora — pero diluye al accionista igual. Restarlo " +
      "es la lectura más conservadora de cuánta caja 'de verdad' le queda al " +
      "accionista actual.",
  },
  sbc_to_revenue: {
    titulo: "SBC / ingresos",
    texto: "Compensación en acciones como % de los ingresos. Un número alto es una señal de dilución significativa, incluso si el FCF reportado se ve sano.",
  },
  fcf_margin: {
    titulo: "Margen de FCF",
    texto: "Free cash flow sobre ingresos. Qué porción de cada peso facturado termina como caja libre real, después de mantener y hacer crecer el negocio.",
  },
  shares_outstanding: {
    titulo: "Acciones en circulación",
    texto:
      "Cantidad de acciones emitidas, por ejercicio. Si sube con el tiempo hay " +
      "dilución (la empresa emite acciones nuevas); si baja, la empresa está " +
      "recomprando más de lo que emite — buena señal para el accionista actual " +
      "si se hace a un precio razonable.",
  },
  payout_ratio: {
    titulo: "Payout ratio",
    texto:
      "Dividendos pagados sobre ganancia neta del mismo ejercicio. Qué " +
      "porción de la ganancia se reparte en dividendos en vez de reinvertirse. " +
      "Sin dato cuando la empresa no reparte dividendos o cuando pierde plata " +
      "(un payout sobre una base negativa no tiene lectura).",
  },
  cagr_revenue: {
    titulo: "CAGR de ingresos",
    texto: "Tasa de crecimiento anual compuesta de los ingresos, entre el primer y el último ejercicio disponible.",
  },
  cagr_eps: {
    titulo: "CAGR de EPS diluido",
    texto: "Tasa de crecimiento anual compuesta de la ganancia por acción diluida.",
  },
  cagr_fcf: {
    titulo: "CAGR de FCF",
    texto: "Tasa de crecimiento anual compuesta del free cash flow.",
  },
  analyst_consensus: {
    titulo: "Consenso de analistas",
    texto:
      "Proyección promedio de crecimiento para el próximo ejercicio, según los " +
      "analistas que cubren la empresa. Es opinión de terceros, no un dato del " +
      "balance — puede estar equivocada, y cuantos menos analistas la sigan, " +
      "menos representativa es.",
  },

  // --- salud financiera --------------------------------------------------

  debt_to_equity: {
    titulo: "Deuda / Patrimonio",
    texto: "Deuda total dividida por patrimonio. Cuánta deuda usa la empresa en relación a lo que aportaron los accionistas.",
  },
  debt_to_equity_trend: {
    titulo: "Tendencia deuda/patrimonio",
    texto:
      "Variación del ratio deuda/patrimonio contra el ejercicio anterior. " +
      "Negativo significa que la empresa se está desapalancando (bajando su " +
      "deuda relativa) — el signo importa tanto como la magnitud.",
  },
  net_debt: {
    titulo: "Deuda neta",
    texto: "Deuda total menos la caja disponible. Si es negativa, la empresa tiene más caja que deuda — una posición financiera sana, no un ratio inválido.",
  },
  net_debt_to_ebitda: {
    titulo: "Deuda neta / EBITDA",
    texto: "Cuántos años de EBITDA harían falta para pagar toda la deuda neta. Es el ratio de apalancamiento más común para comparar empresas de distinto tamaño.",
  },
  interest_coverage: {
    titulo: "Cobertura de intereses",
    texto: "EBIT dividido por los intereses pagados. Cuántas veces alcanza la ganancia operativa para cubrir el costo de la deuda — más alto es más seguro.",
  },
  current_ratio: {
    titulo: "Liquidez corriente",
    texto: "Activo corriente dividido por pasivo corriente. Si la empresa puede cubrir sus obligaciones de corto plazo con lo que tiene a mano en el corto plazo.",
  },
  cash_days: {
    titulo: "Días de caja",
    texto: "Cuántos días de gasto operativo cubre la caja disponible, al ritmo actual.",
  },

  // --- fortaleza financiera / riesgo ----------------------------------------

  altman_z_score: {
    titulo: "Altman Z-Score",
    texto:
      "Combina liquidez, ganancias acumuladas, rentabilidad operativa, " +
      "apalancamiento a valor de mercado y eficiencia de activos en un solo " +
      "número que estima riesgo de quiebra. Z > 2.99 es zona segura, Z < 1.81 " +
      "es zona de riesgo, y en el medio queda una zona gris. Es una señal de " +
      "alerta temprana, no un diagnóstico de crédito definitivo — y requiere el " +
      "market cap actual, así que sólo se calcula para el último ejercicio.",
  },
  piotroski_f_score: {
    titulo: "Piotroski F-Score",
    texto:
      "9 criterios binarios (rentabilidad, calidad de la ganancia, " +
      "apalancamiento, liquidez, dilución y márgenes) que comparan el último " +
      "ejercicio contra el anterior. Cada uno suma 1 punto si se cumple. Un " +
      "puntaje alto (7-9) sugiere una empresa fundamentalmente fuerte y en " +
      "mejora; uno bajo (0-2), fundamentals deteriorándose. Si a algún " +
      "criterio le falta el dato para evaluarse, se excluye del puntaje en vez " +
      "de contar como no cumplido.",
  },

  // --- costo de capital ----------------------------------------------------

  beta: {
    titulo: "Beta",
    texto:
      "Qué tan volátil es la acción en relación al mercado en general. Beta 1 " +
      "significa que se mueve igual que el mercado; mayor a 1, más volátil; " +
      "menor a 1, menos volátil.",
  },
  cost_of_debt: {
    titulo: "Costo de deuda (histórico)",
    texto: "Intereses pagados en el ejercicio, sobre el stock de deuda total. Es el costo histórico ya pagado, no el costo marginal al que la empresa se endeudaría hoy — eso requiere datos de mercado que el bot no calcula.",
  },
  capital_weights: {
    titulo: "Peso deuda / equity",
    texto: "Qué proporción del capital total de la empresa (deuda + market cap) es deuda, y cuál es equity. Es un insumo para armar el WACC, no un ratio de riesgo por sí solo.",
  },
  wacc: {
    titulo: "¿Por qué no hay WACC?",
    texto:
      "El WACC (costo promedio ponderado del capital) requiere una tasa libre de " +
      "riesgo y un equity risk premium — son supuestos de mercado, no datos de " +
      "la empresa, y envejecen en silencio si se hardcodean. El bot entrega los " +
      "componentes verificables (beta, costo de deuda, estructura de capital) " +
      "para que el WACC se arme con supuestos explícitos, no automáticamente.",
  },

  // --- valuación histórica ---------------------------------------------------

  valuation_percentile: {
    titulo: "Percentil (valuación histórica)",
    texto:
      "En qué percentil de su propia historia de precios cotiza el múltiplo " +
      "actual. p95 significa que el múltiplo de hoy es más alto que el 95% de " +
      "las observaciones históricas — es decir, está caro respecto a como cotizó " +
      "antes esa misma empresa. No se compara contra otras empresas, sólo contra " +
      "sí misma.",
  },
  valuation_vs_median: {
    titulo: "vs. mediana",
    texto: "Cuánto por encima o por debajo está el múltiplo actual respecto de su propia mediana histórica, en %.",
  },
  valuation_verdict: {
    titulo: "Lectura (barato / justo / caro)",
    texto:
      "Resumen del percentil histórico: percentil ≤25 se lee 'barato', ≥75 " +
      "'caro', el resto 'justo' — siempre respecto a la propia historia de la " +
      "empresa, no del mercado ni del sector. Con pocas observaciones (menos de " +
      "12 meses de precios) no se emite lectura: no alcanza para que el " +
      "percentil signifique algo.",
  },
  valuation_lag: {
    titulo: "Rezago de publicación",
    texto:
      "Los múltiplos históricos se calculan cruzando cada precio sólo con el " +
      "balance que YA era público en esa fecha (con un margen de ~90 días desde " +
      "el cierre del ejercicio). Sin este recorte, se valuaría con información " +
      "que en su momento nadie tenía — un sesgo de anticipación que infla " +
      "artificialmente la precisión del análisis.",
  },

  // --- ranking sectorial -----------------------------------------------------

  percentile_method: {
    titulo: "Método: Percentil",
    texto:
      "Cada empresa se ubica en un percentil dentro de su sector para cada " +
      "métrica (0 = peor del grupo, 100 = mejor). Es robusto a outliers: una " +
      "empresa con un ROIC absurdamente alto no distorsiona el resto del " +
      "ranking, sólo ocupa el percentil 100.",
  },
  zscore_method: {
    titulo: "Método: Z-score",
    texto:
      "Cada empresa se mide en desvíos estándar respecto del promedio del " +
      "sector. A diferencia del percentil, premia la magnitud: una empresa " +
      "muy por encima del resto se distingue de una apenas por encima, cosa " +
      "que el percentil no captura tan bien. Es más sensible a outliers.",
  },
  composite_score: {
    titulo: "Puntaje compuesto",
    texto:
      "Promedio ponderado de los puntajes de cada métrica (percentil o " +
      "z-score, según el método elegido). Si a una empresa le falta el dato de " +
      "una métrica, esa métrica se excluye del promedio y los pesos se " +
      "renormalizan entre las que sí tiene — el dato faltante no la castiga ni " +
      "la premia.",
  },
  missing_metric: {
    titulo: "Sin dato",
    texto: "El proveedor no reporta esta métrica para esta empresa. Se excluye del puntaje compuesto y los pesos se renormalizan sobre las métricas disponibles — no se imputa con cero ni con un promedio inventado.",
  },
  coverage: {
    titulo: "Cobertura",
    texto: "Qué fracción de las métricas del ranking tiene dato para esta empresa. Una cobertura baja significa que el puntaje compuesto se apoya en menos información — léelo con más cautela.",
  },
  thin_sector: {
    titulo: "Pocos peers",
    texto:
      "Con muy pocas empresas en el sector, el percentil deja de ser " +
      "significativo: con sólo dos, una queda siempre en el percentil 0 y la " +
      "otra en el 100, sin importar cuán parecidas sean en realidad. Es una " +
      "advertencia para leer el ranking de ese sector con más cautela, no un " +
      "error.",
  },
  unrankable: {
    titulo: "Sin rankear",
    texto: "La empresa no alcanzó el mínimo de métricas con dato para calcular un puntaje compuesto confiable. Se muestra aparte, no se descarta.",
  },

  // --- identidad / CEDEARs ---------------------------------------------------

  cedear: {
    titulo: "CEDEAR / ADR",
    texto:
      "Este ticker cotiza en Argentina pero sus fundamentals no se traen del " +
      "listado local — se resuelve al subyacente (la acción original en EE.UU.) " +
      "y se piden ahí, porque el listado local no publica balances confiables. " +
      "Los ratios siguen siendo válidos: son adimensionales, así que no hace " +
      "falta ajustar por el tipo de cambio del CEDEAR.",
  },
  currency_mismatch: {
    titulo: "Monedas mixtas",
    texto:
      "El balance de esta empresa está en una moneda distinta a la de su " +
      "cotización (por ejemplo, un ADR argentino: balance en pesos, cotización " +
      "en dólares). Los ratios que cruzan precio de mercado con datos del " +
      "balance (P/E, EV/EBITDA, P/B, FCF yield) no se calculan en ese caso — " +
      "cruzar dos monedas sin convertir no da un número válido, da ruido.",
  },
};

/** Devuelve `{titulo, texto}` para una clave, o null si no existe. */
function glossaryEntry(key) {
  const entry = GLOSSARY[key];
  if (!entry && key) {
    console.warn(`glosario: falta la entrada "${key}"`);
  }
  return entry ?? null;
}
