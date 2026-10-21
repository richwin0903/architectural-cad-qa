# Conversion regression fixture

`libredwg-line-2018.dxf.gz` contains the normalized ASCII DXF produced by
GNU LibreDWG 0.14 from its `test/test-data/2018/Line.dwg` fixture.
It is a small upstream converter test drawing, not an uploaded customer drawing.

- Official source: <https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz>.
- Source archive SHA-256:
  `62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275`.
- Source-package license: GNU GPL version 3 or later; retained in
  `vendor/libredwg-0.14.tar.xz` as `COPYING`, and reproduced in
  [licenses/GPL-3.0.txt](../../../licenses/GPL-3.0.txt).
- Reproduction command:
  `dwg2dxf -v0 --as r2018 -y -o OUTPUT INPUT`.
- Uncompressed DXF SHA-256:
  `d8a923c19cc45c2f68d190f2f56fadb02584fc594926b77bf28d9c23c3f2990b`.

The corresponding upstream `Line.txt` records a baseline line length of
17.4075 drawing units. The fixture declares inches; this number must not be
presented as metres. It retains the real converter metadata that exposed
unnecessary EditHistory reserialization during read-only upload.

The reproduction command preserves this fixture's R2018 format. Production
conversion preserves each input's version, since forcing an older DWG to R2018
can lose its entity chain in this converter version.

Compressed fixture SHA-256: `1ae1bf79e4ee594ba568ed20f99a6fb5bfd792504059b1f3efc0e56fa8772ada`.
This converter-derived regression input is retained under the upstream
GPL-3.0-or-later terms; it is not relicensed as Apache-2.0 merely because the
surrounding application uses it.
