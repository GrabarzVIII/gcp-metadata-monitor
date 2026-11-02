# Contributing

Use English for code comments, documentation, commits, and pull requests.

## Branch workflow

The initial implementation uses real branches and explicit merge commits:

1. `feature/rule-engine`: models, execution, report routing, demo, and tests.
2. `feature/terraform-deployment`: schemas, infrastructure, IAM, and image builds.
3. `docs/deployment-and-ci`: deployment manual, operational guidance, examples, and CI.

Branches were developed sequentially from integrated `main`. This demonstrates a branch workflow without claiming independent authors or fabricated reviews. Branches are retained on GitHub for inspection.

```bash
git log --graph --oneline --all
git switch -c feature/your-change main
```

Keep changes focused, run checks, push the branch, and open a pull request describing the problem, behavior, and validation. Never commit environments, credentials, Terraform state/plans, or deployment-specific variables.

## Validation

Run `python -m pytest -q`, `ruff check src tests scripts`, and `ruff format --check src tests scripts`. Test alarm boundaries, no-data behavior, partial failures, profile isolation, query restrictions, and duplicate execution handling.

After model changes, run `python scripts/generate_schemas.py` and commit generated SQL/JSON. Review migration compatibility. Cloud integration tests require an explicitly configured test project; unit tests do not use production credentials.
