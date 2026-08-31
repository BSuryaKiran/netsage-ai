# NetSage AI

AI-Assisted Cisco Network Troubleshooting System

## Architecture

```
User
  ↓
Flask Backend (app.py)
  ↓
Level-0 Deterministic Checker (checker.py)
  ↓
Gemini AI Diagnosis (diagnose_prompt.md + Google GenAI SDK)
  ↓
Structured Diagnosis (JSON Schema)
  ↓
Human Review Workflow (review_store.json)
```

## Main Components

- `checker.py` — Level-0 advisory deterministic rule checker.
- `app.py` — Flask REST API backend orchestrating diagnostics, AI integration, and review storage.
- `static/index.html` — Single-page web dashboard with Gemini AI status badge and human-in-the-loop controls.
- `cases.csv` — 30-case validated Cisco networking troubleshooting dataset.
- `diagnose_prompt.md` — Structured AI system prompt instructions and output schema definition.
- `review_store.json` — Persistent store for human reviewer decisions and corrections.
- `responsible_ai_log.md` — Governance event audit log and evaluation summary report.

## Safety & Governance

- **AI is advisory**: Recommendations require human review before action.
- **No automatic network configuration**: The AI never executes configuration changes on devices.
- **No live Cisco device connection**: Operations are dataset-driven without SSH/Telnet access.
- **API credential security**: `GEMINI_API_KEY` is loaded strictly from environment variables (`.env`) and never logged or exposed.
- **Ground-truth protection**: `expected_fault` is strictly excluded from AI prompts and frontend responses.

## Running the Application

Start the Flask server using the project virtual environment:

```powershell
.\venv311\Scripts\python.exe app.py
```

Then open your web browser at:

👉 **http://127.0.0.1:5000/**

## Evaluation

The system includes a 30-case evaluation dataset (`cases.csv`) covering Layer 1 to Layer 7 network anomalies.

### Latest Evaluation Summary

- **Evaluation Date**: 2026-08-31
- **Dataset Size**: 30 cases
- **Deterministic Findings Count**: 6 Level-0 findings detected
- **Note on API Quotas**: Free-tier Gemini API rate limits (20 requests/day per project model) apply during batch evaluation. When quota limits are reached, the system gracefully logs `AI_UNAVAILABLE` status without exposing credentials or crashing.
