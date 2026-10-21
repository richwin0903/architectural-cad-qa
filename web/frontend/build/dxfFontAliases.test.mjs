import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import opentype from 'opentype.js';
import { transformDxfFontAliases } from './dxfFontAliases.js';

test('the bundled Noto font retains all demo Chinese glyph aliases in dxf-viewer', async () => {
  const source = await readFile(new URL('../node_modules/dxf-viewer/src/TextRenderer.js', import.meta.url), 'utf8');
  const transformed = transformDxfFontAliases(source, '/node_modules/dxf-viewer/src/TextRenderer.js');
  const start = transformed.code.indexOf('class Font {');
  const end = transformed.code.indexOf('\n    /**\n     * @param char', start);
  assert.ok(start >= 0 && end > start, 'Extract the actual dependency Font constructor.');
  const Font = vm.runInNewContext(`${transformed.code.slice(start, end)}\n}\nFont;`);
  const bytes = await readFile(new URL('../../../fonts/NotoSansSC-Regular.ttf', import.meta.url));
  const font = opentype.parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  const indexed = new Font(font);
  for (const char of '二层平面图单位合成测试图纸非真实建筑项目会议室办公室走廊门窗闭合轮廓为预设边界标准图块') {
    assert.ok(indexed.charMap.has(char), `CJK character ${char} must resolve through all glyph Unicode aliases.`);
  }
  assert.ok(font.charToGlyph('走').unicodes.length > 1, 'The regression fixture includes a shared radical outline.');
  assert.equal(transformDxfFontAliases('unchanged', '/other/module.js'), null);
  assert.throws(() => transformDxfFontAliases('changed', '/node_modules/dxf-viewer/src/TextRenderer.js'), /implementation changed/);
});
