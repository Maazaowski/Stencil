"""Prompt evaluation harness (dev-only).

Runs each AI call type (prompt) against labeled cases, scores the processed output
against committed expected deliverables, flags hallucinations/inconsistencies, and
writes per-run artifacts so prompt versions can be compared run-vs-run. Gated on the
live ``debug`` runtime setting; off in production.
"""
