"""
checker.py — NetSage AI Level-0 Deterministic Rule Checker

Inspects raw Cisco show-command output, symptoms, and topology notes for
obvious, deterministic configuration problems BEFORE the AI diagnosis layer.

Rules implemented:
  - check_interface_shutdown  : detects shutdown / administratively down interfaces
  - check_subnet_mismatch     : detects host/gateway in different subnets
  - check_duplicate_ip        : detects the same IP assigned to multiple labelled hosts
  - check_missing_vlan        : detects a referenced VLAN absent from the VLAN database

Public API:
  run_level0_checker(symptom, show_output, topology_note="") -> dict

No AI, no external APIs, no cases.csv, no expected_fault column.
Pure Python standard library.
"""

import re
import ipaddress
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(value) -> str:
    """Return a stripped string for any input, or empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


def _combined_text(*parts) -> str:
    """Join multiple text inputs into one searchable block."""
    return "\n".join(_safe_str(p) for p in parts)


def _parse_ip(raw: str) -> Optional[ipaddress.IPv4Address]:
    """Parse a single IPv4 address string; return None on failure."""
    try:
        return ipaddress.IPv4Address(raw.strip())
    except (ipaddress.AddressValueError, ValueError):
        return None


def _parse_network(ip: ipaddress.IPv4Address,
                   mask_str: str) -> Optional[ipaddress.IPv4Network]:
    """Build an IPv4Network from an address and dotted-decimal mask string."""
    try:
        mask = ipaddress.IPv4Address(mask_str.strip())
        network = ipaddress.IPv4Network(
            f"{ip}/{mask}", strict=False
        )
        return network
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rule 1 — Interface Shutdown Detection
# ---------------------------------------------------------------------------

# Regex to capture an interface name from a Cisco config/show line
_IF_NAME_RE = re.compile(
    r"interface\s+(\S+)",
    re.IGNORECASE
)

# Patterns that confirm a shutdown state
_SHUTDOWN_PATTERNS = [
    re.compile(r"^\s*shutdown\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"administratively\s+down", re.IGNORECASE),
    re.compile(r"is\s+administratively\s+down", re.IGNORECASE),
]


def check_interface_shutdown(show_output: str) -> Optional[str]:
    """
    Detect evidence of a shutdown interface in Cisco show/config output.

    Looks for bare 'shutdown' config lines or 'administratively down' phrases.
    Tries to identify the associated interface name from the surrounding context.

    Returns:
        A human-readable finding string, or None if no shutdown evidence found.
    """
    text = _safe_str(show_output)
    if not text:
        return None

    lines = text.splitlines()

    # --- Check for bare 'shutdown' config lines ---
    current_interface: Optional[str] = None
    for line in lines:
        # Track the current interface block we are in
        if_match = _IF_NAME_RE.match(line)
        if if_match:
            current_interface = if_match.group(1)

        # Bare 'shutdown' directive inside an interface block
        if re.match(r"^\s*shutdown\s*$", line, re.IGNORECASE):
            if current_interface:
                return (
                    f"Interface Shutdown Detected: "
                    f"Interface '{current_interface}' is set to SHUTDOWN."
                )
            return "Interface Shutdown Detected: A shutdown directive was found in the configuration."

    # --- Check for 'administratively down' in show interface output ---
    for pattern in _SHUTDOWN_PATTERNS[1:]:  # skip bare shutdown, handled above
        match = pattern.search(text)
        if match:
            # Try to find the nearest interface name in the whole text
            all_interfaces = _IF_NAME_RE.findall(text)
            if all_interfaces:
                iface = all_interfaces[-1]  # closest preceding interface
                return (
                    f"Interface Shutdown Detected: "
                    f"Interface '{iface}' is administratively down."
                )
            return (
                "Interface Shutdown Detected: "
                "An interface is reported as administratively down."
            )

    return None


# ---------------------------------------------------------------------------
# Rule 2 — Subnet / Gateway Mismatch Detection
# ---------------------------------------------------------------------------

# Flexible label patterns for IP, mask, and gateway fields
_IP_PATTERNS = [
    re.compile(r"(?:IPv4\s+Address|Host\s+IP|IP)\s*[:\s]+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE),
]
_MASK_PATTERNS = [
    re.compile(r"(?:Subnet\s+Mask|Mask)\s*[:\s]+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE),
]
_GW_PATTERNS = [
    re.compile(r"(?:Default\s+Gateway|Gateway)\s*[:\s]+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE),
]


def _find_first(patterns, text) -> Optional[str]:
    """Return the first regex group match from a list of patterns."""
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def check_subnet_mismatch(show_output: str) -> Optional[str]:
    """
    Detect when the host IP and default gateway are in different subnets.

    Uses Python's ipaddress module for accurate subnet calculation.
    Supports common Cisco/Windows ipconfig label styles.

    Returns:
        A human-readable finding string, or None if no mismatch detected.
    """
    text = _safe_str(show_output)
    if not text:
        return None

    ip_str   = _find_first(_IP_PATTERNS, text)
    mask_str = _find_first(_MASK_PATTERNS, text)
    gw_str   = _find_first(_GW_PATTERNS, text)

    if not (ip_str and mask_str and gw_str):
        return None  # insufficient data — no finding

    host_ip = _parse_ip(ip_str)
    gw_ip   = _parse_ip(gw_str)

    if not (host_ip and gw_ip):
        return None  # unparseable addresses — stay silent

    host_net = _parse_network(host_ip, mask_str)
    if host_net is None:
        return None

    if gw_ip not in host_net:
        return (
            f"Gateway Mismatch Detected: "
            f"Host IP ({host_ip}) and Gateway ({gw_ip}) are in different subnets!"
        )

    return None


# ---------------------------------------------------------------------------
# Rule 3 — Duplicate IP Detection
# ---------------------------------------------------------------------------

# Match lines like "PC1 IP: 1.2.3.4", "PC2: 1.2.3.4", "Host1 ipconfig: 1.2.3.4"
_DUP_LINE_RE = re.compile(
    r"(PC\d+|Host\d+|Router\d*|Device\d*)\s*(?:IP|ipconfig|address)?\s*[:\s]+(\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)


def check_duplicate_ip(show_output: str) -> Optional[str]:
    """
    Detect when the same IP address is associated with multiple named hosts.

    Looks for patterns like 'PC1 IP: x.x.x.x' and 'PC2 IP: x.x.x.x'
    and flags when the address is identical.

    Returns:
        A human-readable finding string, or None if no duplicate detected.
    """
    text = _safe_str(show_output)
    if not text:
        return None

    # Collect (host_label, ip) pairs
    matches = _DUP_LINE_RE.findall(text)
    if len(matches) < 2:
        return None

    # Group hosts by IP
    ip_to_hosts: dict[str, list[str]] = {}
    for host, raw_ip in matches:
        ip = _parse_ip(raw_ip)
        if ip is None:
            continue
        ip_str = str(ip)
        ip_to_hosts.setdefault(ip_str, []).append(host)

    for ip_str, hosts in ip_to_hosts.items():
        # Only flag if the SAME ip is on at least two DISTINCT host labels
        unique_hosts = list(dict.fromkeys(hosts))  # preserve order, deduplicate
        if len(unique_hosts) >= 2:
            host_list = " and ".join(unique_hosts)
            return (
                f"Duplicate IP Detected: "
                f"{ip_str} appears on multiple hosts ({host_list})."
            )

    return None


# ---------------------------------------------------------------------------
# Rule 4 — Missing VLAN Detection
# ---------------------------------------------------------------------------

# Extract VLAN IDs that appear active in a 'show vlan brief' block.
# Handles both full Cisco format: "10   default   active   Fa0/1"
# and simplified summary format:  " VLAN 10 Active"
_VLAN_ACTIVE_RE = re.compile(
    r"(?:"
    r"^\s*(\d+)\s+\S.*\bactive\b"          # full table: id  name  active
    r"|"
    r"^\s*VLAN\s+(\d+)\s+.*\bactive\b"     # summary:    VLAN 10 Active
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Find VLAN IDs referenced in topology text or symptom (e.g. "VLAN 50")
_VLAN_REF_RE = re.compile(r"\bVLAN\s+(\d+)\b", re.IGNORECASE)

# Detect whether the text contains a 'show vlan brief'-style table
_VLAN_TABLE_RE = re.compile(
    r"(show\s+vlan|VLAN\s+Name|Active|active)",
    re.IGNORECASE,
)


def check_missing_vlan(show_output: str, topology_note: str = "",
                       symptom: str = "") -> Optional[str]:
    """
    Detect when a VLAN referenced in the symptom or topology is absent
    from the active VLAN database shown in the output.

    Requires both:
      - A 'show vlan brief'-style table in show_output (so we have ground truth)
      - At least one VLAN reference in symptom/topology that is absent from the table

    Returns:
        A human-readable finding string, or None if evidence is insufficient.
    """
    show = _safe_str(show_output)
    topo = _safe_str(topology_note)
    sym  = _safe_str(symptom)

    if not show:
        return None

    # Only proceed if output looks like a VLAN table
    if not _VLAN_TABLE_RE.search(show):
        return None

    # Collect VLANs confirmed active in the table.
    # findall returns tuples (group1, group2) due to the alternation — pick the non-empty one.
    raw_matches = _VLAN_ACTIVE_RE.findall(show)
    active_vlans: set[int] = set()
    for g1, g2 in raw_matches:
        vid_str = g1 or g2
        if vid_str:
            active_vlans.add(int(vid_str))

    # Also consider VLANs mentioned in the show output (non-active lines like headers) —
    # we only want to flag truly absent ones, so we check against active_vlans only.
    if not active_vlans:
        return None  # can't determine what is present — stay silent

    # Collect VLANs referenced in symptom and topology
    referenced: dict[int, str] = {}  # vlan_id -> source label
    for m in _VLAN_REF_RE.finditer(sym):
        vid = int(m.group(1))
        if vid not in (1,):  # skip default VLAN 1 references
            referenced[vid] = "symptom"
    for m in _VLAN_REF_RE.finditer(topo):
        vid = int(m.group(1))
        if vid not in (1,):
            referenced.setdefault(vid, "topology note")

    if not referenced:
        return None

    missing = [vid for vid in referenced if vid not in active_vlans]

    if missing:
        vid = missing[0]  # report the first missing VLAN
        return (
            f"Missing VLAN Detected: "
            f"Referenced VLAN {vid} is not configured in the switch VLAN database."
        )

    return None


# ---------------------------------------------------------------------------
# Orchestrator — run_level0_checker
# ---------------------------------------------------------------------------

def run_level0_checker(symptom: str,
                       show_output: str,
                       topology_note: str = "") -> dict:
    """
    Run all Level-0 deterministic checks against the supplied evidence.

    Args:
        symptom       : The reported network problem description.
        show_output   : Raw Cisco show-command or configuration text.
        topology_note : Optional topology/context description.

    Returns:
        {
            "rule_checker_passed": bool,
            "flags_count": int,
            "deterministic_findings": [str, ...]
        }

    Never raises an exception — all errors are silently absorbed.
    """
    symptom       = _safe_str(symptom)
    show_output   = _safe_str(show_output)
    topology_note = _safe_str(topology_note)

    findings: list[str] = []

    try:
        result = check_interface_shutdown(show_output)
        if result:
            findings.append(result)
    except Exception:
        pass

    try:
        result = check_subnet_mismatch(show_output)
        if result:
            findings.append(result)
    except Exception:
        pass

    try:
        result = check_duplicate_ip(show_output)
        if result:
            findings.append(result)
    except Exception:
        pass

    try:
        result = check_missing_vlan(show_output, topology_note, symptom)
        if result:
            findings.append(result)
    except Exception:
        pass

    return {
        "rule_checker_passed": len(findings) == 0,
        "flags_count": len(findings),
        "deterministic_findings": findings,
    }


# ---------------------------------------------------------------------------
# Self-test / demonstration (run directly: python checker.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    # --- Individual function tests ---
    TESTS = [
        {
            "label": "check_interface_shutdown - bare shutdown directive",
            "fn": check_interface_shutdown,
            "input": "interface GigabitEthernet0/0\n shutdown",
            "expect_none": False,
        },
        {
            "label": "check_interface_shutdown - administratively down",
            "fn": check_interface_shutdown,
            "input": "GigabitEthernet0/2 is administratively down, line protocol is down",
            "expect_none": False,
        },
        {
            "label": "check_interface_shutdown - no shutdown present",
            "fn": check_interface_shutdown,
            "input": "interface GigabitEthernet0/0\n ip address 192.168.1.1 255.255.255.0",
            "expect_none": True,
        },
        {
            "label": "check_subnet_mismatch - gateway in wrong subnet",
            "fn": check_subnet_mismatch,
            "input": "IP: 192.168.1.50\nMask: 255.255.255.0\nGateway: 192.168.2.1",
            "expect_none": False,
        },
        {
            "label": "check_subnet_mismatch - gateway in same subnet",
            "fn": check_subnet_mismatch,
            "input": "IP: 192.168.1.50\nMask: 255.255.255.0\nGateway: 192.168.1.1",
            "expect_none": True,
        },
        {
            "label": "check_duplicate_ip - same IP on PC1 and PC2",
            "fn": check_duplicate_ip,
            "input": "PC1 IP: 192.168.1.10\nPC2 IP: 192.168.1.10",
            "expect_none": False,
        },
        {
            "label": "check_duplicate_ip - different IPs",
            "fn": check_duplicate_ip,
            "input": "PC1 IP: 192.168.1.10\nPC2 IP: 192.168.1.20",
            "expect_none": True,
        },
    ]

    print("=" * 60)
    print("NetSage AI - Level-0 Checker Self-Test")
    print("=" * 60)

    all_pass = True
    for t in TESTS:
        result = t["fn"](t["input"])
        passed = (result is None) == t["expect_none"]
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {t['label']}")
        if not t["expect_none"] and result:
            print(f"         >> {result}")

    # --- run_level0_checker case tests ---
    CASE_TESTS = [
        {
            "case": 1,
            "desc": "VLAN mismatch (wrong VLAN on port)",
            "symptom": "PC gets IP but cannot ping gateway in VLAN 10",
            "topology": "PC1 connected to Switch Port Fa0/1 assigned to VLAN 10",
            "show": "interface FastEthernet0/1\n switchport access vlan 20",
            "expect_flags": 0,   # no deterministic rule covers wrong VLAN # in port config
        },
        {
            "case": 2,
            "desc": "Interface shutdown (DHCP router port down)",
            "symptom": "PC1 cannot obtain IP address via DHCP",
            "topology": "PC1 -> Switch -> Router DHCP Server",
            "show": (
                "ip dhcp pool LAN\n network 192.168.1.0 255.255.255.0\n"
                " default-router 192.168.1.254\n"
                "interface GigabitEthernet0/0\n shutdown"
            ),
            "expect_flags": 1,
        },
        {
            "case": 8,
            "desc": "Duplicate IP on PC1 and PC2",
            "symptom": "Two PCs experience periodic connection drops",
            "topology": "PC1 (192.168.1.10) and PC2 (192.168.1.10)",
            "show": "PC1 ipconfig: 192.168.1.10\nPC2 ipconfig: 192.168.1.10",
            "expect_flags": 1,
        },
        {
            "case": 10,
            "desc": "Gateway subnet mismatch",
            "symptom": "PC cannot reach any host outside local subnet",
            "topology": "PC1 connected to Switch1",
            "show": "ipconfig\n IP: 192.168.1.50\n Mask: 255.255.255.0\n Gateway: 192.168.2.1",
            "expect_flags": 1,
        },
        {
            "case": 19,
            "desc": "Missing VLAN 50 from switch VLAN database",
            "symptom": "VLAN 50 hosts cannot talk to local gateway",
            "topology": "Access Switch Fa0/10 -> PC5",
            "show": "show vlan brief\n VLAN 10 Active\n VLAN 30 Active",
            "expect_flags": 1,
        },
        {
            "case": 27,
            "desc": "Static route inactive - exit interface shutdown",
            "symptom": "Static Route not appearing in routing table",
            "topology": "Core Router R1",
            "show": (
                "ip route 10.5.0.0 255.255.0.0 GigabitEthernet0/2 192.168.10.2\n"
                "show ip interface G0/2\n GigabitEthernet0/2 is administratively down"
            ),
            "expect_flags": 1,
        },
    ]

    print()
    print("=" * 60)
    print("Case-Specific Tests")
    print("=" * 60)

    for ct in CASE_TESTS:
        r = run_level0_checker(ct["symptom"], ct["show"], ct["topology"])
        # For cases where we expect >=1 flag, check flags_count > 0
        if ct["expect_flags"] == 0:
            passed = True   # acceptable if checker finds 0 or more (not a required detection)
            note = "(no deterministic rule required)"
        else:
            passed = r["flags_count"] >= 1
            note = ""
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] Case {ct['case']}: {ct['desc']} {note}")
        for f in r["deterministic_findings"]:
            print(f"         >> {f}")

    # --- JSON serialization test ---
    print()
    print("=" * 60)
    print("JSON Serialization Test")
    print("=" * 60)
    try:
        sample = run_level0_checker(
            "PC cannot reach gateway",
            "interface GigabitEthernet0/0\n shutdown",
        )
        json_str = json.dumps(sample, indent=2)
        print(json_str)
        print("  [PASS] JSON serialization")
    except Exception as e:
        all_pass = False
        print(f"  [FAIL] JSON serialization - {e}")

    print()
    print("=" * 60)
    overall = "ALL TESTS PASSED" if all_pass else "SOME TESTS FAILED"
    print(f"Result: {overall}")
    print("=" * 60)
