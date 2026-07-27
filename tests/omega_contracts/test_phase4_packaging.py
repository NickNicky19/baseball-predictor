from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from scripts.prepare_omega_scaffold_build_context import prepare_context
from scripts.verify_omega_scaffold_package import (
    PackagingGateError,
    inspect_wheel,
    parse_hash_lock,
    verify_single_dashboard_authority,
)

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "docs/omega_stage2/phase4/WHEEL_CONTENT_ALLOWLIST.txt"


def _synthetic_wheel(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"synthetic")


def test_build_context_contains_one_authoritative_scaffold_only(tmp_path: Path) -> None:
    target = tmp_path / "build-context"
    copied = prepare_context(ROOT, target)
    assert "dashboard/app.py" in copied
    assert "src/omega_contracts/chronology.py" in copied
    assert not (target / "docs").exists()
    assert not (target / "tests").exists()
    assert not (target / "data").exists()
    assert not any("phase3" in item.casefold() for item in copied)
    with pytest.raises(ValueError, match="must not already exist"):
        prepare_context(ROOT, target)


def test_second_dashboard_implementation_is_rejected(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    shutil.copytree(ROOT / "dashboard", candidate / "dashboard")
    (candidate / "other/dashboard").mkdir(parents=True)
    (candidate / "other/dashboard/app.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PackagingGateError, match="duplicated"):
        verify_single_dashboard_authority(candidate)


def test_wheel_allowlist_rejects_unexpected_and_missing_package_data(
    tmp_path: Path,
) -> None:
    expected = ALLOWLIST.read_text(encoding="utf-8").splitlines()
    wheel = tmp_path / "synthetic.whl"
    _synthetic_wheel(wheel, expected)
    assert len(inspect_wheel(wheel, ALLOWLIST)) == len(expected)

    _synthetic_wheel(wheel, sorted(expected + ["unexpected.py"]))
    with pytest.raises(PackagingGateError, match="unexpected"):
        inspect_wheel(wheel, ALLOWLIST)

    without_template = [
        name for name in expected if name != "dashboard/templates/index.html"
    ]
    _synthetic_wheel(wheel, without_template)
    with pytest.raises(PackagingGateError, match="missing"):
        inspect_wheel(wheel, ALLOWLIST)


def test_hash_lock_parser_rejects_unhashed_and_duplicate_profiles(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.lock"
    valid.write_text(
        "example==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8"
    )
    assert parse_hash_lock(valid) == {"example": "1.0"}

    missing = tmp_path / "missing.lock"
    missing.write_text("example==1.0\n", encoding="utf-8")
    with pytest.raises(PackagingGateError, match="unhashed"):
        parse_hash_lock(missing)

    duplicate = tmp_path / "duplicate.lock"
    duplicate.write_text(
        "example==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
        "example==1.0 \\\n    --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PackagingGateError, match="duplicate"):
        parse_hash_lock(duplicate)


def test_build_backend_and_runtime_metadata_are_exact() -> None:
    pyproject = (ROOT / "packaging/omega_scaffold/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'requires = ["setuptools==80.9.0", "wheel==0.45.1"]' in pyproject
    assert "setuptools>=" not in pyproject
    assert "omega-trust-scaffold" in pyproject
    for lock in (
        ROOT / "requirements/profiles/scaffold-build.lock",
        ROOT / "requirements/profiles/scaffold-runtime.lock",
        ROOT / "requirements/profiles/scaffold-test.lock",
    ):
        assert parse_hash_lock(lock)
