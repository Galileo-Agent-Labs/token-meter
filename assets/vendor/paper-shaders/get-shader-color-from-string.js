/* * * * * * * * * * * * * * * * * * * * * * * * * * * *
 *                    Paper Shaders                    *
 *       https://github.com/paper-design/shaders       *
 * * * * * * * * * * * * * * * * * * * * * * * * * * * */

function getShaderColorFromString(colorString) {
  if (Array.isArray(colorString)) {
    if (colorString.length === 4) return colorString;
    if (colorString.length === 3) return [...colorString, 1];
    return fallbackColor;
  }
  if (typeof colorString !== "string") {
    return fallbackColor;
  }
  let r, g, b, a = 1;
  if (colorString.startsWith("#")) {
    [r, g, b, a] = hexToRgba(colorString);
  } else if (colorString.startsWith("rgb")) {
    const rgba = parseRgba(colorString);
    if (rgba === null) return fallbackColor;
    [r, g, b, a] = rgba;
  } else if (colorString.startsWith("hsl")) {
    const hsla = parseHsla(colorString);
    if (hsla === null) return fallbackColor;
    [r, g, b, a] = hslaToRgba(hsla);
  } else {
    console.error("Unsupported color format", colorString);
    return fallbackColor;
  }
  return [clamp(r, 0, 1), clamp(g, 0, 1), clamp(b, 0, 1), clamp(a, 0, 1)];
}
function hexToRgba(hex) {
  hex = hex.replace(/^#/, "");
  if (hex.length === 3 || hex.length === 4) {
    hex = hex.split("").map((char) => char + char).join("");
  }
  if (hex.length === 6) {
    hex = hex + "ff";
  }
  if (!/^[0-9a-f]{8}$/i.test(hex)) {
    console.warn("Invalid hex color");
    return fallbackColor;
  }
  const r = parseInt(hex.slice(0, 2), 16) / 255;
  const g = parseInt(hex.slice(2, 4), 16) / 255;
  const b = parseInt(hex.slice(4, 6), 16) / 255;
  const a = parseInt(hex.slice(6, 8), 16) / 255;
  return [r, g, b, a];
}
function parseRgba(rgba) {
  const match = rgba.match(/^rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+))?\s*\)$/i);
  if (!match) return null;
  return [
    parseInt(match[1] ?? "0") / 255,
    parseInt(match[2] ?? "0") / 255,
    parseInt(match[3] ?? "0") / 255,
    match[4] === void 0 ? 1 : parseFloat(match[4])
  ];
}
function parseHsla(hsla) {
  const match = hsla.match(/^hsla?\s*\(\s*(\d+)\s*,\s*(\d+)%\s*,\s*(\d+)%\s*(?:,\s*([0-9.]+))?\s*\)$/i);
  if (!match) return null;
  return [
    parseInt(match[1] ?? "0"),
    parseInt(match[2] ?? "0"),
    parseInt(match[3] ?? "0"),
    match[4] === void 0 ? 1 : parseFloat(match[4])
  ];
}
function hslaToRgba(hsla) {
  const [h, s, l, a] = hsla;
  const hDecimal = h / 360;
  const sDecimal = s / 100;
  const lDecimal = l / 100;
  let r, g, b;
  if (s === 0) {
    r = g = b = lDecimal;
  } else {
    const hue2rgb = (p2, q2, t) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p2 + (q2 - p2) * 6 * t;
      if (t < 1 / 2) return q2;
      if (t < 2 / 3) return p2 + (q2 - p2) * (2 / 3 - t) * 6;
      return p2;
    };
    const q = lDecimal < 0.5 ? lDecimal * (1 + sDecimal) : lDecimal + sDecimal - lDecimal * sDecimal;
    const p = 2 * lDecimal - q;
    r = hue2rgb(p, q, hDecimal + 1 / 3);
    g = hue2rgb(p, q, hDecimal);
    b = hue2rgb(p, q, hDecimal - 1 / 3);
  }
  return [r, g, b, a];
}
const clamp = (n, min, max) => Math.min(Math.max(n, min), max);
const fallbackColor = [0.5, 0.5, 0.5, 1];
export {
  clamp,
  getShaderColorFromString
};
//# sourceMappingURL=get-shader-color-from-string.js.map
