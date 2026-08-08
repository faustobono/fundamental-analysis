// Formato compartido entre el screener y el análisis profundo.
//
// El server manda cada número junto con su formato ('pct', 'x', 'delta_x',
// 'money', 'num', 'days'); acá sólo se traduce a texto. Ningún archivo de
// front decide cómo se ve un número — eso ya vino resuelto del bot.

// `Intl.NumberFormat({notation: "compact"})` se probó y descartó: en es-AR
// produce basura entre 10^11 y 10^12 ("-902,43 mil M" en vez de "-902.43 B")
// y no tiene sufijo por arriba del billón — se queda sin unidad y devuelve
// "5000 B". Pasa seguido con montos en ARS: la inflación argentina pone
// balances de bancos en el orden de los billones de pesos. Se usa la misma
// escala T/B/M/K que el lado Python (`bot/brief/render.py`), a mano, para que
// CLI y web muestren el mismo número de la misma forma.
function formatMoney(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const scales = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [limit, suffix] of scales) {
    if (abs >= limit) return `${sign}${(abs / limit).toFixed(2)} ${suffix}`;
  }
  return `${sign}${abs.toFixed(0)}`;
}

function formatValue(value, format) {
  if (value === null || value === undefined) return "—";
  switch (format) {
    case "pct":
      return `${(value * 100).toFixed(1)}%`;
    case "x":
      return `${value.toFixed(2)}×`;
    case "delta_x":
      // Lleva signo a propósito: el signo ES la información (desapalancamiento).
      return `${value >= 0 ? "+" : ""}${value.toFixed(2)}×`;
    case "money":
      return formatMoney(value);
    case "days":
      return `${Math.round(value)} días`;
    case "num":
      return value.toFixed(2);
    default:
      return value.toFixed(2);
  }
}
