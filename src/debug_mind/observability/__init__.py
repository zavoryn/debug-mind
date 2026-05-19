"""Observability module — structured logging and optional OTel integration.

Why this exists: Production debugging requires machine-parseable logs.
Phase 1 used print() and rich console, which can't be ingested by log aggregators.
This module provides JSON-formatted structured logging that includes trace_id,
case_id, and token usage metadata.
"""
