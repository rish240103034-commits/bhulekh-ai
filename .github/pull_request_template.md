## Summary

<!-- What does this PR change, and why? Link any related issue: Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Documentation
- [ ] Security

## How was this tested?

<!-- Commands you ran and what you observed. -->

- [ ] `ruff check .` is clean
- [ ] `pytest` passes (backend)
- [ ] `npm run build` succeeds (frontend, if touched)

## Checklist

- [ ] Added/updated tests for the change (security changes → `tests/test_security.py`)
- [ ] Schema change includes an Alembic migration
- [ ] State-changing actions write to `audit_logs`
- [ ] New settings use the `BHULEKH_` prefix and are documented in `.env.example`
- [ ] No secrets, credentials, or personal data committed
- [ ] `CHANGELOG.md` updated under *Unreleased* (for user-facing changes)

## Notes for reviewers

<!-- Anything reviewers should focus on, migration/config steps, screenshots, etc. -->
