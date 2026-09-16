---
name: Bug report
about: Report something that isn't working as expected
title: "[Bug] "
labels: bug
assignees: ''
---

<!--
Security issue? Do NOT file it here. Follow SECURITY.md and use a private advisory instead.
-->

## Describe the bug

A clear and concise description of what the bug is.

## To reproduce

Steps to reproduce the behaviour:
1. Go to '...'
2. Upload / click '...'
3. See error

## Expected behaviour

What you expected to happen.

## Actual behaviour

What actually happened. Include the exact error message and, if you have it, the
`X-Request-ID` from the response (it ties to a single server log line).

## OCR diagnostics (for extraction issues)

If a document extracted nothing or wrong text, paste the relevant part of
`http://localhost:8000/health` — it reports which language packs are usable.

## Environment

- Component: backend / frontend / docker
- Version or commit:
- OS:
- Python / Node version:
- Tesseract & Poppler installed? Which language packs?

## Screenshots / sample

If applicable, add screenshots or a **non-sensitive** sample document. Do not attach real
personal land records.

## Additional context

Anything else that might help.
