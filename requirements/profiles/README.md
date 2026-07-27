# Dependency profiles

These profiles separate prediction runtime, research, testing, and dashboard
deployment. Dependency membership is operational metadata; it does not change
or authorize prediction mathematics.

| Profile | Input | Lock | Permitted consumer |
|---|---|---|---|
| Existing comparator runtime | `requirements.txt` | `requirements-aws-comparator.lock` | Existing comparator automation only |
| Scaffold runtime | `requirements/profiles/scaffold-runtime.in` | `requirements/profiles/scaffold-runtime.lock` | Installed read-only trust-boundary wheel and container |
| Scaffold tests | `requirements/profiles/scaffold-test.in` | `requirements/profiles/scaffold-test.lock` | Candidate CI and local QA |
| Scaffold build | `requirements/profiles/scaffold-build.in` | `requirements/profiles/scaffold-build.lock` | Isolated wheel construction only |
| Scaffold lint | `requirements/profiles/scaffold-lint.in` | `requirements/profiles/scaffold-lint.lock` | Exact CloudFormation lint only |
| Research | `requirements/profiles/research.in` | Not generated in this candidate | Explicit research tasks only; never dashboard or collector runtime |

The scaffold runtime, test, and build locks were generated with a pinned `pip-tools`
environment under CPython 3.12. They contain hashes for every selected package,
and a fresh CPython 3.12 environment installed the test lock with
`pip install --require-hashes --no-deps` and passed `pip check`. The authoring
host was Windows, so Linux compatibility is not inferred from that result.
Candidate CI independently installs the same locks on Ubuntu 24.04 and fails
closed if hashes, imports, tests, package inspection, or the container build fail.

Neither lock is an authorization to deploy. A Linux CI pass, human review, and
all staging prerequisites remain mandatory before an AWS staging deployment.

`research.in` closes the known declaration gap for optional code importing
scikit-learn, CatBoost, LightGBM, DuckDB, Joblib, and Matplotlib. It has no lock
yet because no fitting or research execution is authorized in this staging
candidate.
