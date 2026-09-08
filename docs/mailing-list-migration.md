# Mailing lists and blog emails

The new site stores subscribers, consent, mailing-list memberships, email drafts and delivery history in its existing PostgreSQL database. Its dedicated Plunk project delivers email. The secret key stays in the API service configuration.

## Squarespace source inspected on 2026-09-08

- All Contacts: 170; Accepts Marketing: 156 true and 14 false.
- Lists: master (115 members), HOA_Meeting (44 members), with six people in both lists.
- Three sent campaigns and one draft. Sent emails shared blog content, linked to the original post, and included an unsubscribe footer. The draft contained template filler and is not migrated.
- Export: All Contacts > menu > Export All Contacts. The private source CSV is retained outside the repository. Its SHA-256 is `50009744fbe48015cd288eaee9fc676f574b5f5960062b2324d2258979d5f239`.
- Relevant CSV fields: Email, First Name, Last Name, Accepts Marketing, Mailing Lists, Subscriber Since, Subscriber Source, Created On and Tags. Billing and donation fields are not imported.

## Administration

- `/admin/mailing-list`: search names/addresses, filter subscription/list, paginate, unsubscribe, edit contact names and memberships, export CSV.
- `/admin/mailing-list/lists`: create and edit lists with total and subscribed counts.
- `/admin/mailing-list/import`: preview CSV, inspect source statuses and invalid rows, acknowledge permission, import in repeatable batches. Case-insensitive duplicates combine lists and retain the most restrictive status. Existing opt-outs are never reactivated. Import sends no emails.
- `/admin/campaigns`: create an email from a published blog post, choose all subscribers or selected lists, add an opening note, save, preview exact recipient count, send a test, and confirm the real send. Overlapping memberships produce one delivery per address.
- Sent emails retain a content snapshot and per-recipient outcomes. Queue cancellation stops pending deliveries; an in-progress provider request may finish. Accepted means provider acceptance, not proven inbox delivery. Failed/unknown outcomes require review in Plunk and are never automatically resent.

## Public subscription flow

Signup records explicit consent and sends a confirmation with a random, hashed, expiring token. A GET does not activate or unsubscribe anyone; the page requires a button press. Existing subscribers receive no redundant confirmation. An opted-out address stays opted out until a fresh confirmation succeeds. Old confirmation links cannot reactivate an opt-out. Every campaign email has a site unsubscribe link and one-click unsubscribe headers. Rate-limit records contain keyed hashes, not raw email addresses/IPs.

Public blog pages load saved published posts, so email links display the managed content. Subscriber data and unsubscribe tokens are never exposed by public list endpoints. Admin routes use the existing authenticated session.

## API service configuration

`PLUNK_SECRET_KEY`, `PLUNK_API_BASE_URL=https://next-api.useplunk.com`, `PLUNK_FROM_ADDRESS=info@savesixesrd.org`, `PLUNK_FROM_NAME=Save Sixes Rd`, `PLUNK_REPLY_TO=info@savesixesrd.org`.

`SITE_URL` must be the website origin; `API_PUBLIC_URL` the API origin. `NEWSLETTER_POSTAL_ADDRESS` supplies the footer address. `NEWSLETTER_ENABLED` and `CAMPAIGN_WORKER_ENABLED` default false; enable after the deployment and sender are verified. The API's lifespan creates the new `newsletter_*` tables without changing existing posts/admins. The worker stores claims before provider calls, rechecks consent immediately before sending, and marks abandoned claims unknown after five minutes rather than resending them.

The frontend uses `VITE_API_URL` or the existing Railway API. `CORS_ORIGINS` must include the frontend origin. At domain cutover, update SITE_URL and CORS_ORIGINS and verify links on both origins. Squarespace remains intact; DNS cutover and cancellation are separate work.

## Verification and reconciliation

Run `python -m pytest tests -q` from `backend` and `npm run build` at repository root. Tests use synthetic addresses and mock Plunk, covering imports, list membership, opt-outs, confirmation replay, durable rate limits, audience changes, duplicate queue requests, cancellation, uncertain sends, escaped email content, and authentication.

Before migration, retain the source CSV and a destination backup outside Git. After migration, compare every normalized address, consent status, original name, source metadata and membership with the source, not only totals. Retain the private reconciliation report. Verify a signup/confirmation/unsubscribe round trip and a blog-email test through the approved mailbox. Migration is not permission to broadcast to the source audience.

Provider references: [send API](https://docs.useplunk.com/api-reference/public-api/sendEmail), [API overview and idempotency](https://docs.useplunk.com/api-reference/overview).
