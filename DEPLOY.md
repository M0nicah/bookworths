# Deploying Bookworths

## Streamlit Community Cloud (free)

1. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub.
2. Click **New app** → **Deploy a public app from a repo**.
3. Fill in:
   - **Repository:** `M0nicah/bookworths`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Open **Advanced settings** → set **Python version** to `3.11`.
5. Under **Secrets**, paste:
   ```toml
   BOOKWORTHS_MULTIUSER = "1"
   ```
   This gives every visitor their own counterparty memory. **Without it, all
   visitors share one database** and one person's confirmed suppliers would
   change another person's categorisations.
6. Click **Deploy**. First build takes 3–5 minutes.

Your URL will be `https://<something>.streamlit.app`.

To redeploy, just `git push` — Streamlit Cloud rebuilds automatically.

---

## Before you share the link

This app reads **real M-Pesa financial statements**. A Community Cloud app is
**public**: anyone with the URL can open it and upload a statement. There is no
login, and free-tier apps sleep after inactivity and wake for anyone who visits.

That is fine for a demo of your own data. It is **not** suitable for other
people's statements until you add authentication.

What the deployed app does and does not do:

| | |
|---|---|
| Uploaded statements | Held in memory for the session, never written to disk |
| Learned counterparties | Per-session temp file, discarded when the session ends |
| Generated reports | Offered as downloads; not stored server-side |
| Access control | **None** — anyone with the URL can use the app |

To gate access, add a password check at the top of `app.py` using
`st.secrets["password"]`, or move to a host that supports real authentication.

---

## Running it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Locally the app uses a single `data/bookworths.db`, so it gets smarter every run
— confirmations persist and the exception queue shrinks over time. That shared
memory is the point on your own machine, and the thing to avoid on a shared URL.

---

## Other hosts

| Host | Notes |
|---|---|
| **Streamlit Community Cloud** | Free, simplest, public. What this guide covers. |
| **Hugging Face Spaces** | Free, supports private Spaces. Needs a `Dockerfile` or `app.py` at root. |
| **Render / Railway** | Paid tiers, persistent disk, custom domains. Run `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`. |
| **Fly.io / a VPS** | Full control, needs a `Dockerfile` and manual TLS. |

For any host with a persistent disk, set `BOOKWORTHS_MULTIUSER=1` unless the app
is genuinely single-user.
