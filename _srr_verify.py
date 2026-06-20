#!/usr/bin/env python3
"""Throwaway copy of send_review_requests.py used only to compile-verify the
member-only guard edit on a fresh inode (the bash mount cached a stale view of
the real file). Delete after verification."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone


def is_active_member(patient: dict) -> bool:
    """True only if the Hint patient is an active member.

    Review asks are member-only. A free-consult attendee who did not enroll still
    produces a Hint clinical interaction, but asking them for a Google review lacks
    the patient-relationship / ePHI-waiver consent basis the review automation
    relies on (TCPA), and risks ineligible reviews under Google policy. Added
    2026-06-10 after non-members were observed receiving review texts.
    """
    return (patient.get("membership_status") or "").lower() == "active"


def main_loop_excerpt(patient_ids, args, clicked_hashes):
    """Mirror of the real main() per-patient loop body, to validate structure."""
    sent = 0
    skipped = 0
    errors = 0
    for pid in sorted(patient_ids):
        try:
            patient = fetch_patient(pid)
            if not patient:
                errors += 1
                continue

            # Member-only guard (2026-06-10): only active Hint members get review
            # asks. A free-consult attendee who didn't enroll still produces a Hint
            # clinical interaction, but asking them for a Google review lacks the
            # patient-relationship / ePHI-waiver consent basis (TCPA) and risks
            # ineligible reviews under Google policy. --allow-patient test mode
            # bypasses this guard. See is_active_member().
            if not args.allow_patient and not is_active_member(patient):
                status = patient.get("membership_status") or "unknown"
                skipped += 1
                continue

            phi = extract_phi_minimal(patient)
            if not phi:
                skipped += 1
                continue
            first_name, email, phone = phi

            fname_hash = hash_fname(first_name)
            if fname_hash and fname_hash in clicked_hashes:
                skipped += 1
                continue

            _dispatch_to_bridge(pid, first_name, email, phone, trigger="poller")
            sent += 1
        except Exception as e:
            errors += 1
    return sent, skipped, errors
