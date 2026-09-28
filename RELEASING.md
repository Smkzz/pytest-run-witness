# Release procedure

This checklist is for maintainers.

1. Start from a clean `main` checkout.
2. Confirm the version in `pyproject.toml`, `src/pytest_run_witness/__init__.py`, and `CHANGELOG.md`.
3. Run the complete test suite and hosted CI matrix.
4. Build from a clean Git export:
   ```console
   python -m build
   python -m twine check dist/*
   ```
5. Install both the wheel and sdist into fresh environments and run the tests from outside the source tree.
6. Review the exact release diff and run the security review against the frozen release commit.
7. Create the release tag only after all gates pass.
8. Publish through the repository's PyPI Trusted Publishing workflow.
9. Verify the files and metadata on PyPI before announcing the release.

The publication workflow must remain separated from build/test jobs so the job holding `id-token: write` does not execute project tests or arbitrary build-time code.
