export const Q = 1.602176634e-19;
function positive(value, name) {
  if (value === '' || value === null || value === undefined || !Number.isFinite(Number(value)) || Number(value) <= 0) throw new RangeError(`${name}必須是大於零的有限數值`);
  return Number(value);
}
export function sheetResistance(ns, mobility) {
  const n = positive(ns, '面密度'), mu = positive(mobility, '遷移率');
  const result = 1 / (Q * n * mu);
  if (!Number.isFinite(result) || result <= 0) throw new RangeError('數值超出可計算範圍');
  return result;
}
export function cdf(t, eta, beta) {
  const e = positive(eta, 'η'), b = positive(beta, 'β');
  if (t === '' || !Number.isFinite(Number(t)) || Number(t) < 0) throw new RangeError('時間必須是非負有限數值');
  return -Math.expm1(-Math.pow(Number(t) / e, b));
}
export function quantile(p, eta, beta) {
  const e = positive(eta, 'η'), b = positive(beta, 'β');
  if (p === '' || !Number.isFinite(Number(p)) || Number(p) <= 0 || Number(p) >= 1) throw new RangeError('分位 p 必須滿足 0 < p < 1');
  const value = e * Math.pow(-Math.log1p(-Number(p)), 1 / b);
  if (!Number.isFinite(value) || value <= 0) throw new RangeError('分位時間超出可計算範圍');
  return value;
}
