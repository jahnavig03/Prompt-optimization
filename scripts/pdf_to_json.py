#!/usr/bin/env python3
"""
pdf_to_json.py — one-off converter for legacy "Live Bot Test Report" PDFs.

Usage:
    python scripts/pdf_to_json.py /path/to/report.pdf [-o out.json]

Once you've converted, upload the .json via the lab UI (Knowledge tab).
New runs can be exported directly as JSON from the lab, so this script is
only needed for PDFs you generated before that feature shipped.
"""

import argparse
import json
import sys
from pathlib import Path

# Make the project root importable when run from anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import knowledge_store


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", help="Path to the lab-generated PDF report")
    ap.add_argument("-o", "--out", default=None,
                    help="Output .json path (default: same name, .json extension)")
    args = ap.parse_args()

    in_path = Path(args.pdf).expanduser().resolve()
    if not in_path.exists():
        sys.exit(f"error: file not found: {in_path}")

    out_path = Path(args.out).expanduser().resolve() if args.out \
        else in_path.with_suffix(".json")

    payload = knowledge_store.parse_lab_pdf(str(in_path))
    n = len(payload.get("accepted_tests", []))
    out_path.write_text(json.dumps(payload, indent=2))

    print(f"✓ Wrote {n} accepted test case(s) → {out_path}")
    if n == 0:
        print("  (no PASS tests found — is this a Prompt Optimization Lab report?)")


if __name__ == "__main__":
    main()
