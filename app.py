"""
app.py - NetSage AI Flask Backend API
======================================
Level-0 diagnostic simulation backend.

Endpoints:
  GET  /                     Serve the frontend (static/index.html)
  GET  /api/health            Health check
  GET  /api/cases             All 30 troubleshooting cases
  GET  /api/cases/<id>        Single case by ID
  POST /api/diagnose          Run Level-0 checker + build structured diagnosis

AI mode: level0_simulation
No external AI API is used in this stage.
"""

import csv
import json
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, abort

# Load environment variables from .env if present
load_dotenv()

from google import genai
from google.genai import types

from checker import run_level0_checker

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASES_FILE = os.path.join(BASE_DIR, "cases.csv")
STATIC_DIR = os.path.join(BASE_DIR, "static")
REVIEW_STORE_FILE = os.path.join(BASE_DIR, "review_store.json")

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# Review Store Helpers
# ---------------------------------------------------------------------------

def load_reviews_store() -> dict:
    """Load reviews from review_store.json. Returns dict with 'reviews' list."""
    if not os.path.exists(REVIEW_STORE_FILE):
        return {"reviews": []}
    try:
        with open(REVIEW_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "reviews" in data and isinstance(data["reviews"], list):
                return data
            else:
                app.logger.error("Corrupted structure in review_store.json")
                raise ValueError("Corrupted structure in review_store.json")
    except ValueError as ve:
        raise ve
    except Exception as exc:
        app.logger.error("Failed reading review_store.json: %s", exc)
        raise RuntimeError("Failed to read review_store.json.")


def save_review_record(record: dict) -> None:
    """Append a review record to review_store.json safely without data corruption."""
    store = load_reviews_store()
    store["reviews"].append(record)
    temp_file = REVIEW_STORE_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(temp_file, REVIEW_STORE_FILE)

# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def load_cases() -> list[dict]:
    """
    Load all cases from cases.csv using Python's csv module.

    Returns an empty list if the file is missing or malformed,
    without crashing the application.
    """
    cases = []
    try:
        with open(CASES_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cases.append(dict(row))
    except FileNotFoundError:
        app.logger.error("cases.csv not found at: %s", CASES_FILE)
    except Exception as exc:
        app.logger.error("Failed to load cases.csv: %s", exc)
    return cases


# Load once at startup — cases are static for this stage.
CASES: list[dict] = load_cases()

# Build an O(1) lookup by id string.
CASES_BY_ID: dict[str, dict] = {c["id"]: c for c in CASES}


# ---------------------------------------------------------------------------
# Level-0 diagnosis builder
# ---------------------------------------------------------------------------

# OSI layer inference from finding keywords
_LAYER_HINTS = [
    ("shutdown", "Layer 1"),
    ("duplex", "Layer 1"),
    ("gateway mismatch", "Layer 3"),
    ("duplicate ip", "Layer 3"),
    ("missing vlan", "Layer 2"),
    ("vlan", "Layer 2"),
]

# Safe next commands keyed by finding keyword
_NEXT_COMMANDS = {
    "shutdown": "show ip interface brief",
    "gateway":  "show ip interface brief",
    "duplicate": "show ip arp",
    "vlan":     "show vlan brief",
}

_DEFAULT_NEXT_CMD = "show ip interface brief"


def _infer_osi_layer(findings: list[str], supplied_layer: str) -> str:
    """Return the most appropriate OSI layer given findings and the supplied layer."""
    if findings:
        text = " ".join(findings).lower()
        for keyword, layer in _LAYER_HINTS:
            if keyword in text:
                return layer
    if supplied_layer:
        return supplied_layer
    return "Layer 3"


def _infer_next_command(findings: list[str]) -> str:
    """Return a safe next diagnostic command based on finding keywords."""
    if not findings:
        return _DEFAULT_NEXT_CMD
    text = " ".join(findings).lower()
    for keyword, cmd in _NEXT_COMMANDS.items():
        if keyword in text:
            return cmd
    return _DEFAULT_NEXT_CMD


def build_level0_diagnosis(
    symptom: str,
    show_output: str,
    topology_note: str,
    osi_layer: str,
    checker_result: dict,
) -> dict:
    """
    Build a structured diagnostic response from Level-0 checker findings.

    This is a DETERMINISTIC simulation — NOT an AI-generated result.
    It is clearly labelled 'level0_simulation' in the API response.

    Returns a dict matching the diagnose_prompt.md JSON schema.
    """
    findings = checker_result.get("deterministic_findings", [])

    if findings:
        root_cause = "; ".join(findings)
        confidence = "High"
        evidence = [f"[Level-0 Deterministic] {f}" for f in findings]
        layer = _infer_osi_layer(findings, osi_layer)
        next_cmd = _infer_next_command(findings)
        fix_steps = [
            "Review the deterministic finding(s) listed in the evidence.",
            "Collect the recommended next diagnostic command output.",
            "Identify the specific device and interface involved.",
            "Plan the configuration correction.",
            "Have a human engineer review the fix before applying it.",
        ]
        verification = next_cmd
    else:
        # No deterministic evidence — return a conservative placeholder.
        root_cause = "No deterministic Level-0 root cause identified."
        confidence = "Low"
        evidence = []
        layer = osi_layer if osi_layer else "Layer 3"
        next_cmd = _DEFAULT_NEXT_CMD
        fix_steps = [
            "Collect additional Cisco diagnostic output.",
            "Review the relevant interface and routing configuration.",
            "Perform human review before applying any change.",
        ]
        verification = "ping <gateway>"

    return {
        "root_cause": root_cause,
        "osi_layer": layer,
        "confidence": confidence,
        "evidence": evidence,
        "next_command": next_cmd,
        "fix_steps": fix_steps,
        "verification_command": verification,
    }


# ---------------------------------------------------------------------------
# Gemini AI Diagnosis Builder
# ---------------------------------------------------------------------------

DIAGNOSE_PROMPT_FILE = os.path.join(BASE_DIR, "diagnose_prompt.md")


def load_diagnose_prompt() -> str:
    """Load system instructions from diagnose_prompt.md."""
    if not os.path.exists(DIAGNOSE_PROMPT_FILE):
        app.logger.error("diagnose_prompt.md not found at %s", DIAGNOSE_PROMPT_FILE)
        raise FileNotFoundError("diagnose_prompt.md file not found.")
    with open(DIAGNOSE_PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def build_ai_diagnosis(
    symptom: str,
    show_output: str,
    topology_note: str,
    checker_result: dict,
) -> dict:
    """
    Construct prompt and call Gemini AI for structured diagnosis.
    Validates output structure and constraints before returning.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY_MISSING")

    system_prompt = load_diagnose_prompt()

    findings = checker_result.get("deterministic_findings", [])
    findings_str = "\n".join([f"- {f}" for f in findings]) if findings else "None detected."

    ai_input = (
        f"SYMPTOM:\n{symptom}\n\n"
        f"CISCO SHOW OUTPUT:\n{show_output}\n\n"
        f"TOPOLOGY / CONTEXT:\n{topology_note if topology_note else 'N/A'}\n\n"
        f"LEVEL-0 DETERMINISTIC FINDINGS:\n{findings_str}"
    )

    response_text = None
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

    try:
        client = genai.Client(api_key=api_key)
        models_to_try = [model_name]
        fallback_candidates = ["gemini-2.5-flash-lite", "gemini-1.5-flash", "gemini-flash-latest", "gemini-3.6-flash"]
        for cand in fallback_candidates:
            if cand not in models_to_try:
                models_to_try.append(cand)

        last_err = None
        for m in models_to_try:
            try:
                res = client.models.generate_content(
                    model=m,
                    contents=ai_input,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json"
                    )
                )
                if res and res.text:
                    response_text = res.text
                    break
            except Exception as exc:
                app.logger.warning("Gemini model %s failed: %s", m, exc)
                last_err = exc

        if not response_text:
            raise RuntimeError(f"Gemini API call failed: {last_err}")

    except ValueError as ve:
        raise ve
    except Exception as exc:
        app.logger.error("Gemini API error: %s", exc)
        raise RuntimeError("AI diagnosis is temporarily unavailable.")

    # Parse and validate JSON response
    try:
        raw_text = response_text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        data = json.loads(raw_text)
    except Exception as exc:
        app.logger.error("Failed to parse Gemini JSON response: %s", exc)
        raise ValueError("Invalid JSON response from AI model.")

    if not isinstance(data, dict):
        raise ValueError("AI response must be a JSON object.")

    required_keys = [
        "root_cause", "osi_layer", "confidence",
        "evidence", "next_command", "fix_steps", "verification_command"
    ]
    for k in required_keys:
        if k not in data:
            raise ValueError(f"AI response missing required key: {k}")

    root_cause = str(data.get("root_cause") or "").strip()
    osi_layer  = str(data.get("osi_layer")  or "").strip()
    confidence = str(data.get("confidence") or "").strip()
    evidence   = data.get("evidence")
    next_cmd   = str(data.get("next_command") or "").strip()
    fix_steps  = data.get("fix_steps")
    verify_cmd = str(data.get("verification_command") or "").strip()

    if not root_cause or not next_cmd or not verify_cmd:
        raise ValueError("Required text fields in AI response cannot be empty.")

    allowed_confidence = ["High", "Medium", "Low"]
    if confidence not in allowed_confidence:
        raise ValueError(f"Invalid confidence '{confidence}'. Must be High, Medium, or Low.")

    allowed_osi = ["Layer 1", "Layer 2", "Layer 3", "Layer 4", "Layer 7"]
    if osi_layer not in allowed_osi:
        raise ValueError(f"Invalid osi_layer '{osi_layer}'. Must be one of {allowed_osi}.")

    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list of strings.")

    if not isinstance(fix_steps, list):
        raise ValueError("fix_steps must be a list of strings.")

    return {
        "root_cause": root_cause,
        "osi_layer": osi_layer,
        "confidence": confidence,
        "evidence": [str(e) for e in evidence],
        "next_command": next_cmd,
        "fix_steps": [str(s) for s in fix_steps],
        "verification_command": verify_cmd,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def serve_frontend():
    """Serve the main frontend SPA from static/index.html."""
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check — confirms service is up and identifies diagnostic mode."""
    mode = "gemini" if os.getenv("GEMINI_API_KEY") else "level0_simulation"
    return jsonify({
        "status": "ok",
        "service": "NetSage AI",
        "version": "1.0",
        "diagnostic_mode": mode,
        "cases_loaded": len(CASES),
    }), 200


@app.route("/api/cases", methods=["GET"])
def get_cases():
    """Return all loaded troubleshooting cases as JSON."""
    if not CASES:
        return jsonify({
            "status": "error",
            "message": "No cases loaded. Check cases.csv.",
        }), 500
    return jsonify({"cases": CASES}), 200


@app.route("/api/cases/<case_id>", methods=["GET"])
def get_case(case_id: str):
    """Return a single case by its ID string."""
    case = CASES_BY_ID.get(case_id)
    if case is None:
        return jsonify({
            "status": "error",
            "message": "Case not found.",
        }), 404
    return jsonify({"case": case}), 200


@app.route("/api/diagnose", methods=["POST"])
def diagnose():
    """
    Run the Level-0 deterministic checker and return a structured diagnosis.

    Required JSON fields:
        symptom    (str)
        show_output (str)

    Optional JSON fields:
        topology_note  (str, default "")
        expected_fault (str, default "" — ground truth, not used for diagnosis)
        osi_layer      (str, default "")
    """
    # --- Parse JSON body ---
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        data = None

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Request body must be valid JSON.",
        }), 400

    # --- Validate required fields ---
    symptom    = (data.get("symptom")    or "").strip()
    show_output = (data.get("show_output") or "").strip()

    if not symptom or not show_output:
        return jsonify({
            "status": "error",
            "message": "Symptom and show_output are required.",
        }), 400

    # --- Optional fields ---
    topology_note  = (data.get("topology_note")  or "").strip()
    osi_layer      = (data.get("osi_layer")      or "").strip()
    # expected_fault is accepted but NOT used for diagnosis (it is ground truth only)

    # --- Run Level-0 checker ---
    try:
        checker_result = run_level0_checker(symptom, show_output, topology_note)
    except Exception as exc:
        app.logger.error("Checker error: %s", exc)
        return jsonify({
            "status": "error",
            "message": "Internal error during rule checking.",
        }), 500

    # --- Check GEMINI_API_KEY ---
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({
            "status": "error",
            "message": "Gemini API key is not configured.",
        }), 503

    # --- Build Gemini AI diagnosis ---
    try:
        ai_diagnosis = build_ai_diagnosis(
            symptom, show_output, topology_note, checker_result
        )
        ai_mode = "gemini"
    except ValueError as ve:
        if str(ve) == "GEMINI_API_KEY_MISSING":
            return jsonify({
                "status": "error",
                "message": "Gemini API key is not configured.",
            }), 503
        app.logger.error("AI diagnosis validation error: %s", ve)
        return jsonify({
            "status": "error",
            "message": "AI diagnosis is temporarily unavailable.",
        }), 503
    except Exception as exc:
        app.logger.error("AI diagnosis error: %s", exc)
        return jsonify({
            "status": "error",
            "message": "AI diagnosis is temporarily unavailable.",
        }), 503

    return jsonify({
        "status": "success",
        "rule_checker": checker_result,
        "ai_diagnosis": ai_diagnosis,
        "ai_mode": ai_mode,
    }), 200


# ---------------------------------------------------------------------------
# Human Review Workflow Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/review", methods=["POST"])
def post_review():
    """
    Record a human review action (ACCEPT, EDIT_AND_CORRECT, REJECT).
    Stores the decision and optional correction persistently in review_store.json.
    """
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        data = None

    if not isinstance(data, dict):
        return jsonify({
            "status": "error",
            "message": "Request body must be valid JSON.",
        }), 400

    action = (data.get("action") or "").strip()
    diagnosis = data.get("diagnosis")

    if not action or action not in ["ACCEPT", "EDIT_AND_CORRECT", "REJECT"]:
        return jsonify({
            "status": "error",
            "message": "Valid action (ACCEPT, EDIT_AND_CORRECT, REJECT) is required.",
        }), 400

    if not isinstance(diagnosis, dict) or not diagnosis:
        return jsonify({
            "status": "error",
            "message": "Diagnosis object is required.",
        }), 400

    corrected_diagnosis = None
    if action == "EDIT_AND_CORRECT":
        corrected_diagnosis = data.get("corrected_diagnosis")
        if not isinstance(corrected_diagnosis, dict) or not corrected_diagnosis:
            return jsonify({
                "status": "error",
                "message": "corrected_diagnosis object is required when action is EDIT_AND_CORRECT.",
            }), 400

        required_keys = [
            "root_cause", "osi_layer", "confidence",
            "evidence", "next_command", "fix_steps", "verification_command"
        ]
        for key in required_keys:
            if key not in corrected_diagnosis:
                return jsonify({
                    "status": "error",
                    "message": f"corrected_diagnosis missing required field '{key}'.",
                }), 400

        if not isinstance(corrected_diagnosis.get("evidence"), list):
            return jsonify({"status": "error", "message": "evidence must be an array."}), 400
        if not isinstance(corrected_diagnosis.get("fix_steps"), list):
            return jsonify({"status": "error", "message": "fix_steps must be an array."}), 400

        if corrected_diagnosis.get("confidence") not in ["High", "Medium", "Low"]:
            return jsonify({"status": "error", "message": "confidence must be High, Medium, or Low."}), 400

        allowed_osi = ["Layer 1", "Layer 2", "Layer 3", "Layer 4", "Layer 7"]
        if corrected_diagnosis.get("osi_layer") not in allowed_osi:
            return jsonify({"status": "error", "message": f"osi_layer must be one of {allowed_osi}."}), 400

    review_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    reviewer_note = (data.get("reviewer_note") or "").strip()

    record = {
        "review_id": review_id,
        "timestamp": timestamp,
        "action": action,
        "original_diagnosis": diagnosis,
        "corrected_diagnosis": corrected_diagnosis,
        "reviewer_note": reviewer_note,
    }

    try:
        save_review_record(record)
    except Exception as exc:
        app.logger.error("Failed saving review: %s", exc)
        return jsonify({
            "status": "error",
            "message": "Failed to store review record.",
        }), 500

    messages = {
        "ACCEPT": "Diagnosis accepted.",
        "REJECT": "Diagnosis rejected.",
        "EDIT_AND_CORRECT": "Diagnosis corrected and saved.",
    }

    return jsonify({
        "status": "success",
        "message": messages[action],
        "review_id": review_id,
    }), 200


@app.route("/api/reviews", methods=["GET"])
def get_reviews():
    """Return all review records stored in review_store.json."""
    try:
        store = load_reviews_store()
        return jsonify({
            "status": "success",
            "reviews": store.get("reviews", []),
        }), 200
    except Exception as exc:
        app.logger.error("Failed getting reviews: %s", exc)
        return jsonify({
            "status": "error",
            "message": "Failed to read reviews.",
        }), 500


@app.route("/api/review-stats", methods=["GET"])
def get_review_stats():
    """Return summary statistics calculated from review_store.json."""
    try:
        store = load_reviews_store()
        reviews = store.get("reviews", [])
        total_reviews = len(reviews)
        accepted = sum(1 for r in reviews if r.get("action") == "ACCEPT")
        edited_and_corrected = sum(1 for r in reviews if r.get("action") == "EDIT_AND_CORRECT")
        rejected = sum(1 for r in reviews if r.get("action") == "REJECT")
        acceptance_rate = round((accepted / total_reviews * 100), 2) if total_reviews > 0 else 0.0

        return jsonify({
            "status": "success",
            "total_reviews": total_reviews,
            "accepted": accepted,
            "edited_and_corrected": edited_and_corrected,
            "rejected": rejected,
            "acceptance_rate": acceptance_rate,
        }), 200
    except Exception as exc:
        app.logger.error("Failed getting review stats: %s", exc)
        return jsonify({
            "status": "error",
            "message": "Failed to compute review stats.",
        }), 500


# ---------------------------------------------------------------------------
# Generic error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "message": "Resource not found."}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"status": "error", "message": "Method not allowed."}), 405


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"status": "error", "message": "Internal server error."}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
