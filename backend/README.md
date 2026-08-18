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

