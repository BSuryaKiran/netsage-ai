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
from flask import Flask, jsonify, request, send_from_directory, abort

from checker import run_level0_checker

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASES_FILE = os.path.join(BASE_DIR, "cases.csv")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder="static")

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
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def serve_frontend():
    """Serve the main frontend SPA from static/index.html."""
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check — confirms service is up and identifies diagnostic mode."""
    return jsonify({
        "status": "ok",
        "service": "NetSage AI",
        "version": "1.0",
        "diagnostic_mode": "level0_simulation",
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

    # --- Build structured diagnosis ---
    try:
        ai_diagnosis = build_level0_diagnosis(
            symptom, show_output, topology_note, osi_layer, checker_result
        )
    except Exception as exc:
        app.logger.error("Diagnosis builder error: %s", exc)
        return jsonify({
            "status": "error",
            "message": "Internal error building diagnosis.",
        }), 500

    return jsonify({
        "status": "success",
        "rule_checker": checker_result,
        "ai_diagnosis": ai_diagnosis,
        "ai_mode": "level0_simulation",
    }), 200


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
