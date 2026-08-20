# Bookworths

### _Clear books, real value_

Financial reconciliation and profit intelligence for Kenyan Instagram and
social-commerce sellers — thrift/apparel, bedding, footwear, home decor.

Bookworths ingests a raw M-Pesa statement, separates the business from the
household, finds the fees nobody notices, and produces a one-page Profit Pack
the seller can actually read on their phone.

📖 **[Full operating manual → DOCUMENTATION.md](DOCUMENTATION.md)** — setup, how it
decides, what the numbers mean, and how to read them critically.

---

## Quick start

**Graphical interface** — upload a statement, categorise flagged items by
clicking, download the reports:

```bash
pip install -r requirements.txt
streamlit run app.py
```

**Command line** — same pipeline, no browser:

```bash
python main.py                    # business mode (default)
python main.py --mode personal    # household income vs spending
```

**Two modes.** *Business* analyses a trading business (profit, stock, restock).
*Personal* analyses a household (income vs spending, savings, debt, runway) with
no business concepts at all. Pick one in the GUI sidebar or with `--mode`.

That runs the whole pipeline against a built-in 67-row mock M-Pesa statement —
**no API key and no configuration required**. Deliverables land in `output/`.

Against a real Safaricom export:

```bash
python main.py --statement statement.csv --business "Thrift by Njeri"
python main.py --statement statement.pdf --pdf-password 123456   # needs pdfplumber
python main.py --cash 24500 --backend anthropic --out july_books
```

Run the tests:

```bash
python test_bookworths.py          # standalone, no pytest needed
python -m pytest test_bookworths.py -v
```

---

## Architecture

```
M-Pesa CSV / XLSX / PDF
          │
          ▼
┌──────────────────────────────────────────────┐
│  INGESTION            app/ingest/            │
│  • header aliasing across statement formats  │
│  • entity extraction (phone / till / paybill)│
│  • fee-line merging onto the parent payment  │
│  → list[Transaction]                         │
└──────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────┐
│  4-LAYER ENGINE       app/engine/            │
│                                              │
│  L1  deterministic regex, Safaricom tariffs, │
│      Fuliza, known paybills (KPLC, Meta…)    │
│  L2  SQLite entity memory, then name hints   │
│  L3  LLM structured-output disambiguation    │
│  L4  confidence < 0.85 → exception queue     │
│  → list[ClassifiedTransaction]               │
└──────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────┐
│  REPORTING            app/reports/           │
│  A  Vendor Profit Pack (Markdown + HTML)     │
│  B  Restock Safety Budget                    │
│  C  WhatsApp exception template              │
│  D  Personal Finance Analysis                │
└──────────────────────────────────────────────┘
```

Each layer only sees what the layer above could not settle. On the mock month,
**57 of 59 transactions resolve in Layers 1–2 with just 2 LLM calls** — and
because confident verdicts are written back into entity memory, month two costs
less again.

---

## Chart of accounts

| Code | Account | Group |
|---|---|---|
| 4000 | Customer Orders | Revenue |
| 4010 | Delivery Fees Collected | Revenue |
| 5000 | Stock Restocks & Bale Purchases | COGS |
| 5010 | Sourcing Transport | COGS |
| 6100 | Logistics & Delivery — riders, parcel offices, CBD shelf rent | Operating |
| 6200 | Marketing & Visibility — IG boosts, shoots, influencers | Operating |
| 6300 | Packaging & Operational — mailers, boxes, stickers | Operating |
| 7000 | Transaction Tariffs & Financial Fees | Operating |
| 3000 | Owner Personal Drawings | **Equity / non-business** |
| 9999 | Unresolved — needs seller confirmation | Suspense |

**3000 is the account that matters.** These sellers run the business from the
same M-Pesa line they buy groceries with. Every other bookkeeping tool silently
folds that into "expenses" and reports a profit that is quietly wrong.

---

## Layer 3 backends

Configured by `--backend`; `auto` (default) picks the first one available:

| Backend | Requires | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` + `anthropic` | Claude structured outputs via `messages.parse` |
| `openai` | `OPENAI_API_KEY` + `openai` | `responses.parse`, `temperature=0` |
| `heuristic` | nothing | Offline keyword scorer — deterministic, always available |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python main.py --backend anthropic
```

Determinism on the Claude path comes from a constrained Pydantic output schema
plus `effort: "low"` — current Claude models reject `temperature`, so a low
sampling temperature is not the lever there. The OpenAI path does use
`temperature=0.0`.

If a classifier call fails, that transaction is routed to the exception queue
rather than aborting the run.

---

## Module B — how safe-to-spend is derived

```
  Cash in M-Pesa (closing balance, or --cash)
− CBD shelf rent due            (size of the last one seen)
− Rider balances owing          (~1 week of observed rider spend)
− Owner draw reserve            (observed monthly personal spend)
− Operating buffer              (10% of cash, tunable)
− Held back for unconfirmed items
= SAFE TO SPEND ON NEXT RESTOCK   (floored at zero)
```

Commitments left unspecified are **estimated from the seller's own history**
rather than assumed to be zero — under-estimating commitments is the expensive
direction of error.

---

## Teaching the system

Every seller reply makes the next month cheaper and quieter:

```python
from app.engine import EntityMemory
from app.schema import Account

memory = EntityMemory("data/bookworths.db")
memory.confirm("254764331199", Account.COGS_RESTOCK, "Eastleigh supplier")
```

Confirmations are stored at 0.99 confidence and outrank both the seeded book and
any LLM verdict. Repeat sightings nudge confidence upward, capped at 0.99.

---

## Outputs

| File | What it is |
|---|---|
| `bookworths_profit_pack.md` | Profit Pack + Restock Budget, Markdown |
| `bookworths_profit_pack.html` | Printable A4 one-pager, self-contained CSS |
| `bookworths_whatsapp_draft.txt` | Ready-to-paste seller message |
| `bookworths_ledger.csv` | Every transaction with account, confidence, layer, rationale |

The GUI (`streamlit run app.py`) offers the same four as downloads, plus
one-click categorisation of flagged transactions that writes back to entity
memory.

---

## Project layout

```
main.py                  CLI entrypoint
app.py                   Streamlit interface
app/
├── schema.py            Pydantic models + chart of accounts
├── mockdata.py          Built-in 67-row M-Pesa statement
├── pipeline.py          Orchestration
├── ingest/normalize.py  CSV/XLSX/PDF → Transaction
├── engine/
│   ├── tariffs.py       Layer 1
│   ├── memory.py        Layer 2 (SQLite)
│   ├── llm.py           Layer 3 (Anthropic / OpenAI / heuristic)
│   └── categorize.py    Orchestration + Layer 4
└── reports/
    ├── profit_pack.py   Module A
    ├── restock.py       Module B
    ├── whatsapp.py      Module C
    ├── personal.py      Module D (drawings breakdown)
    ├── household.py     Personal mode (standalone)
    ├── charts.py        Altair chart builders
    ├── theme.py         Design tokens (Pesabook theme)
    └── people.py        Top counterparties
```

---

## Caveats

- The seeded counterparties in `app/engine/memory.py` are illustrative Nairobi
  vendors. Replace them with a real book before production use.
- The Anthropic and OpenAI backends are written against the current SDK
  signatures but have **not been exercised against a live API** in this build —
  no credentials were available. The offline heuristic backend is fully tested.
- PDF ingestion covers the standard Safaricom table layout; Safaricom changes
  that layout periodically.
- Figures are derived from M-Pesa data only. Cash and bank sales outside M-Pesa
  are invisible to the pipeline and must be added separately.
