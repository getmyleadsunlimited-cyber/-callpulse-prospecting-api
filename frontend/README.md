# CallPulse frontend foundation

Next.js App Router foundation for the customer SaaS. This branch intentionally contains no dashboard, lead inbox, onboarding, billing, agency, integration, or tracking screens.

## Security model

- The browser authenticates through same-origin BFF route handlers. Backend bearer tokens are stored only in an `HttpOnly`, `__Host-` session cookie and are never returned to browser JavaScript.
- Mutations require an exact configured `Origin`, same-site fetch metadata, and a double-submit CSRF token.
- `CALLPULSE_API_URL` is server-only. Never put backend credentials or internal URLs in variables prefixed with `NEXT_PUBLIC_`.
- Workspace IDs are included in every query key so cached tenant data cannot collide across workspace switches.
- Analytics accepts a narrow, non-PII event union and rejects PII-shaped keys.

## Local commands

```bash
cp .env.example .env.local
npm install
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```
