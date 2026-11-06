## What and why

<!-- What changes for clinicians or for whoever deploys the app, and why. -->

## Checklist

- [ ] **How to use updated in this PR** for every user visible change (`app/client/src/components/guide/content.tsx`): plain language for nurses and physicians, labels quoted as the app shows them, a `?` on every new control. Or: this PR changes nothing a user can see.
- [ ] Static demo kept in step with the server, if the server changed: `app/fixtures/parity.py`, then `EDIP_PARITY_REQUIRED=1 npx vitest run src/demo` in `app/client`.
- [ ] Tests: `pytest -q` at the repo root; `npm run typecheck && npm run lint && npx vitest run` in `app/client`.
- [ ] Docs in step (`app/README.md`, `docs/setup.md`, `docs/data-model.md`, `docs/feature-status.md`) where settings, tables or behaviour changed.
- [ ] No secrets, staff emails, customer names, or em or en dashes (`scripts/scan_sensitive.py` runs before every commit).
