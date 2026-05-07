#!/usr/bin/env python3
"""
Hint Health Webhook Receiver
============================
Listens for Hint Health webhook events and sends a review-request email to
each patient who completes a service or enrolls as a member.


WHY THIS FILE EXISTS
--------------------
Replaces the old cron-polling approach with a real-time event-driven
architecture. When Hint fires an event, this receiver sends a branded email
from care@mtbakermedical.com with a link to the self-hosted star-rating
review funnel (/review?fname=FirstName).


IMPORTANT: Hint does NOT have appointment-specific webhook events (no
appointment.completed or visit.completed — Hint is a membership platform).
The closest available proxies for "patient had a service" are:


  membership.created     — patient enrolled; first invite trigger
  customer_invoice.paid  — an invoice was paid, implying a service was rendered
  patient.created        — new patient record created (enrollment intent)


USAGE
-----
Development (ngrok tunnel):
  # Terminal 1:
  ngrok http 5000
  # Terminal 2:
  SMTP_PASS=<app-password> python3 hint_webhook_receiver.py


Production (Render):
  HINT_ENV=production
  HINT_API_KEY=<practices_key>
  HINT_PARTNER_API_KEY=<partner_key>
  SMTP_USER=care@mtbakermedical.com
  SMTP_PASS=<google-app-password>
  REVIEW_BASE_URL=https://your-service.onrender.com
  DRY_RUN=false


ENVIRONMENT VARIABLES
---------------------
  HINT_ENV             sandbox | production  (default: sandbox)
  HINT_API_KEY         Hint practices API key (for patient lookups)
  HINT_PARTNER_API_KEY Hint partner API key (for signature verification)
                       Get from https://app.hint.com/partner/api_keys
  SMTP_USER            care@mtbakermedical.com  (default)
  SMTP_PASS            Google App Password for SMTP_USER — must be set for live sends
  REVIEW_BASE_URL      Public URL of this receiver (used to build /review?fname= links)
  DRY_RUN              true | false  (default: true — logs email, does not send)
  PORT                 HTTP port (default: 5000)


WEBHOOK REGISTRATION
--------------------
Once running with a public URL, register it in the Hint Partner Portal:
  Sandbox:    https://app.staging.hint.com/partner/account/webhooks
  Production: https://app.hint.com/partner/account/webhooks


Select events: membership.created, customer_invoice.paid, patient.created


SIGNATURE VERIFICATION
----------------------
Hint signs each request with:
  X-Hint-Signature: sha256=<HMAC-SHA256 hex>
Keyed on the partner API key (NOT the practices API key).
"""


import os
import json
import hashlib
import hmac
import logging
import sys
import smtplib
import urllib.parse
from datetime import datetime, timezone
