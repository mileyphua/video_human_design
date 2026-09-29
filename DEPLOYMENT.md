# Secure Streamlit Deployment

Do not commit or upload `.streamlit/secrets.toml`, `.env`, generated videos, or subtitle files.

## Streamlit Community Cloud

1. Push only the app files to GitHub.
2. In Streamlit Cloud, open the app settings.
3. Add these values in **Secrets**:

```toml
GEMINI_API_KEY = "your-gemini-key"
GEMINI_MODEL = "gemini-2.5-flash"
THD_API_KEY = "your-thd-key"
THD_BASE_URL = "https://api.totalhumandesign.com"
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
```

4. Deploy with `streamlit_app.py` as the main file.

The app reads keys from Streamlit secrets or environment variables on the server. It does not prefill API keys into browser inputs.

## Local Development

Create `.streamlit/secrets.toml` from `.streamlit/secrets.example.toml` and put real keys there. This file is ignored by git.
