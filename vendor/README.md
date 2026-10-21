GNU LibreDWG 0.14 is built from source in a dedicated Docker build stage.
Only the native DWG reader/converter and GPLv3+ license enter the final runtime image;
there is no ODA or remote/cloud conversion dependency.

Official source: https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz
SHA-256: 62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275
Detached signature: the same URL plus `.sig`
GNU signing keys: https://ftp.gnu.org/gnu/gnu-keyring.gpg

Docker checks the pinned SHA-256 and detached GNU signature before compiling.
The source archive is included in the deployment source directory and can be
redistributed under its COPYING license. These tools run as separate processes;
the Python application does not link against LibreDWG.

A verbatim copy of the source archive's `COPYING` is also provided at
`licenses/GPL-3.0.txt`. Retain these materials and the build/install scripts when
distributing the converter binary or a container image containing it, in the
manner required by the applicable GPL terms. See `THIRD_PARTY_NOTICES.md`.

LibreDWG's reader supports many DWG versions, but some advanced/custom objects
may not be converted. Check previews against the source drawing; do not treat
the normalized DXF as a lossless archival copy. The original upload is not edited.
