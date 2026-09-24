# Dependency profiles

These profiles separate prediction runtime, research, testing, and dashboard
deployment. Dependency membership is operational metadata; it does not change
or authorize prediction mathematics.

| Profile | Input | Lock | Permitted consumer |
|---|---|---|---|
| Existing comparator runtime | `requirements.txt` | `requirements-aws-comparator.lock` | Existing comparator automation only |
| Dashboard | `requirements/profiles/dashboard.in` | `requirements/profiles/dashboard.lock` | Read-only dashboard container |
| Tests | `requirements/profiles/test.in` | `requirements/profiles/test.lock` | Candidate CI and local QA |
| Research | `requirements/profiles/research.in` | Not generated in this candidate | Explicit research tasks only; never dashboard or collector runtime |

The dashboard and test locks were generated with a pinned `pip-tools`
environment under CPython 3.12. They contain hashes for every selected package,
and a fresh CPython 3.12 environment installed the test lock with
`pip install --require-hashes --no-deps` and passed `pip check`. The authoring
host was Windows, so Linux compatibility is not inferred from that result.
Candidate CI independently installs the same lock on Ubuntu 24.04 and fails
closed if resolution, hashes, imports, tests, or the container build fail.

Neither lock is an authorization to deploy. A Linux CI pass, human review, and
all staging prerequisites remain mandatory before an AWS staging deployment.

`research.in` closes the known declaration gap for optional code importing
scikit-learn, CatBoost, LightGBM, DuckDB, Joblib, and Matplotlib. It has no lock
yet because no fitting or research execution is authorized in this staging
candidate.
