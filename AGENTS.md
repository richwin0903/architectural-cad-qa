# Project scope

This repository publishes the read-only architectural CAD QA MVP derived from
jeremylongshore/cad-ai-agent. Preserve upstream copyright and license notices.

Serve `web.backend.production:app` in production. The retained upstream
`web.backend.main:app` includes development and editing APIs and must not be
used as the public entry point. Do not enable `CAD_WEB_DEV_MODE` publicly.

Keep secrets, private drawings, cookies, runtime databases and logs out of Git,
frontend assets, image layers and tool output. Use the protected runtime file.

Run the documented backend tests and frontend font test, lint and build for
changes affecting them. Run `python scripts/check-release.py --tree` before
committing, and the index check after staging. Tests must not call paid models.

Provisioned trial accounts have per-account quotas and session-isolated drawings.
There is no public registration, payment, invitation-code redemption or currency
spend cap. Provision/manage trials only through the server CLI. Keep generated
accounts.txt and accounts-import.json inside ignored, protected .local directories.
Publish or push only when the user requests it; local preparation does not
authorize creating a remote repository.
