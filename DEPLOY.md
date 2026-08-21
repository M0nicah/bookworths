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
| Uploaded statements | Written to a private `0600` temp file only while parsing, then overwritten with zeros and deleted |
| Statement contents | Held in server memory for the session; cleared when the session ends |
| Learned counterparties | Phone/till numbers and names of counterparties **you confirm**, in a per-session temp database |
| Generated reports | Offered as downloads; not stored server-side |
| Outbound network calls | **None**, unless an AI classifier key is configured (see below) |
| Access control | **None** — anyone with the URL can use the app |
| Server logs | Streamlit logs requests, not statement contents |

### The one case where data leaves the server

If `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set, transactions that Layers 1–2
cannot resolve are sent to that provider for classification — including the
**counterparty name, phone/till number, amount and date**. Typically a handful
of rows per statement, but it is real financial data going to a third party.

**No key is configured by default, so this is off.** The offline classifier
handles everything on the server. Only add a key if you accept that trade-off,
and tell your testers if you do. The in-app "Your data & privacy" panel reports
which mode is active.

### What this does not protect against

- Anyone with the URL can open the app and upload a statement.
- The host (Streamlit) can see traffic and could see memory contents.
- A statement in server memory is readable by anyone with server access.
- Nothing is encrypted at rest beyond the filesystem's own protections.

For anything beyond demoing your own data, add authentication first.

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
