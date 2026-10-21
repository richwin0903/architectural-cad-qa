// dxf-viewer 1.0.46 indexes only the primary Unicode value of each glyph.
// Noto CJK shares outlines with Unicode radicals, so Chinese aliases disappear.
// Keep this compatibility fix in both the main and worker Vite build pipelines.
const ORIGINAL = `            if (glyph.unicode === undefined) {
                continue
            }
            this.charMap.set(String.fromCodePoint(glyph.unicode), glyph)`;
const REPLACEMENT = `            const codePoints = glyph.unicodes?.length ? glyph.unicodes : [glyph.unicode]
            for (const codePoint of codePoints) {
                if (Number.isInteger(codePoint)) {
                    this.charMap.set(String.fromCodePoint(codePoint), glyph)
                }
            }`;

export function transformDxfFontAliases(code, id) {
  if (!id.split('?')[0].replaceAll('\\', '/').endsWith('/dxf-viewer/src/TextRenderer.js')) return null;
  if (!code.includes(ORIGINAL)) throw new Error('dxf-viewer Font implementation changed; review the Noto CJK Unicode alias compatibility fix.');
  return { code: code.replace(ORIGINAL, REPLACEMENT), map: null };
}

export default function dxfFontAliases() {
  return { name: 'dxf-noto-unicode-aliases', enforce: 'pre', transform: transformDxfFontAliases };
}
