# Frontend dependency maintenance

The release keeps the upstream development workspace dependencies while its
production entry builds only the authenticated, read-only CAD QA application.
Firebase and its Node.js gRPC transport are absent from that production browser
bundle. Vite, source-map-js and brace-expansion are build/lint dependencies.

The lockfile includes these compatible security updates:

| Dependency | Version | Advisory |
| --- | --- | --- |
| Vite | 6.4.5 | [launch-editor argument handling](https://github.com/advisories/GHSA-587h-7wmj-h5pm) |
| brace-expansion | 1.1.21 | [recursive expansion denial of service](https://github.com/advisories/GHSA-qhr7-859c-m2p7) |
| source-map-js | 1.2.2 | [indexed source-map denial of service](https://github.com/advisories/GHSA-68fv-2mgg-jv7q) |
| @grpc/grpc-js | 1.13.6 | [certificate authorization](https://github.com/advisories/GHSA-m9gg-hp2v-232j) |

`package.json` pins an exact `@grpc/grpc-js` override because the retained
Firestore dependency requests `~1.9.0`, which otherwise keeps a vulnerable
transport version. This changes the transport within major version 1 and does
not downgrade Firebase. The override applies to this frontend dependency tree;
it does not change the Python backend or enable Firestore in production.

Review the override when updating Firebase. Remove it only when the dependency
tree resolves a patched transport without it. Legacy Firebase/Firestore flows
need their own integration checks before being enabled; production QA tests do
not exercise a real Firebase project.

From `web/frontend`, verify a clean installation and the complete advisory tree:

```bash
npm ci
npm audit
npm ls vite brace-expansion source-map-js @grpc/grpc-js
npm run test:font
npm run lint
npm run build
```

Do not use `npm audit fix --force`: it may change major versions or downgrade
Firebase. The installed tree has no `dxf-parser` or `fast-xml-parser` npm package;
the DXF viewer carries its own parser implementation.
