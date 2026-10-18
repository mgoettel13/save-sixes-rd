# Save Sixes Rd API

FastAPI backend for the campaign blog. It provides a public published-post feed and password-protected admin post creation.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

The API is available at `http://localhost:8000`, with interactive docs at `/docs`.

Secrets and Plunk credentials belong in local `.env` files or Railway variables. Never commit them.

To enable the admin news import page, set `NEWS_AGGREGATOR_BASE_URL` to the
HTTPS origin of the aggregator and `NEWS_AGGREGATOR_TENANT_SLUG` to the public
tenant slug. `NEWS_AGGREGATOR_API_KEY` is optional and is sent only by the API
server as a bearer token. The server rejects non-public destinations and does
not follow redirects. The admin page reads published English articles and
imports each one as an editable draft; repeat imports open the existing post
without changing its content or publication status. Review and publish the
post, then use **Share by email** to preview and explicitly send through the
existing mailing-list workflow. Article reference links appear in both the
blog post and its email. Subscriber details stay in Save Sixes.

Install the updated Python dependencies and deploy the API and frontend
together. The import-tracking table is created by the existing API startup
schema initialization. No import publishes a post or sends email automatically.
