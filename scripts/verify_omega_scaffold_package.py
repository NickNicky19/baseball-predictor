"""Executable wheel allowlist and import-to-lock closure gates."""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.metadata
import importlib.resources
import re
import stat
import sys
import sysconfig
import zipfile
from pathlib import Path

LOCK_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)")
REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^;\s]+)")
LOCAL_ROOTS = {"dashboard", "src"}
REQUIRED_IMPORTS = {
    "dashboard",
    "dashboard.app",
    "dashboard.contracts",
    "dashboard.snapshot_store",
    "src.omega_contracts",
    "src.omega_contracts.chronology",
    "fastapi",
    "jinja2",
    "pydantic",
    "uvicorn",
}


class PackagingGateError(RuntimeError):
    pass


def normalize_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def parse_hash_lock(path: Path) -> dict[str, str]:
    locked: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        match = LOCK_LINE.match(line)
        if match:
            name = normalize_distribution(match.group(1))
            if name in locked:
                raise PackagingGateError(f"duplicate locked distribution: {name}")
            locked[name] = match.group(2)
    if not locked or "--hash=sha256:" not in text:
        raise PackagingGateError("runtime dependency lock is empty or unhashed")
    return locked


def expected_wheel_entries(path: Path) -> list[str]:
    entries = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if entries != sorted(entries) or len(entries) != len(set(entries)):
        raise PackagingGateError("wheel allowlist must be sorted and unique")
    return entries


def inspect_wheel(wheel_path: Path, allowlist_path: Path) -> list[tuple[str, int]]:
    expected = expected_wheel_entries(allowlist_path)
    with zipfile.ZipFile(wheel_path) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if len(names) != len(set(names)):
            raise PackagingGateError("wheel contains duplicate ZIP entries")
        if sorted(names) != expected:
            missing = sorted(set(expected) - set(names))
            unexpected = sorted(set(names) - set(expected))
            raise PackagingGateError(
                f"wheel entry allowlist mismatch; missing={missing}; unexpected={unexpected}"
            )
        for item in members:
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PackagingGateError(f"wheel contains a symlink: {item.filename}")
            if (
                item.filename.startswith(("/", "\\"))
                or ".." in Path(item.filename).parts
            ):
                raise PackagingGateError(
                    f"wheel contains an unsafe path: {item.filename}"
                )
    return [(item.filename, item.file_size) for item in members]


def verify_single_dashboard_authority(repository_root: Path) -> None:
    matches = sorted(
        path.relative_to(repository_root).as_posix()
        for path in repository_root.rglob("app.py")
        if path.parent.name == "dashboard" and ".git" not in path.parts
    )
    if matches != ["dashboard/app.py"]:
        raise PackagingGateError(
            f"dashboard authority is duplicated or absent: {matches}"
        )


def _module_source_imports(module_name: str) -> set[str]:
    module = importlib.import_module(module_name)
    path = Path(module.__file__ or "")
    if path.suffix != ".py":
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_installed_environment(
    lock_path: Path, checkout: Path | None
) -> dict[str, str]:
    locked = parse_hash_lock(lock_path)
    imported = {
        name: importlib.import_module(name) for name in sorted(REQUIRED_IMPORTS)
    }
    site_roots = {
        Path(value).resolve()
        for key in ("purelib", "platlib")
        if (value := sysconfig.get_path(key))
    }
    for name, module in imported.items():
        file_name = getattr(module, "__file__", None)
        if file_name is None:
            continue
        resolved = Path(file_name).resolve()
        if checkout is not None and (
            resolved == checkout or checkout in resolved.parents
        ):
            raise PackagingGateError(
                f"source-checkout import leak: {name} -> {resolved}"
            )
        if not any(resolved == root or root in resolved.parents for root in site_roots):
            raise PackagingGateError(
                f"installed import is outside site-packages: {name} -> {resolved}"
            )
    dashboard_root = importlib.resources.files("dashboard")
    for relative in ("templates/index.html", "static/style.css"):
        if not dashboard_root.joinpath(relative).is_file():
            raise PackagingGateError(f"required package data is absent: {relative}")

    mapping = importlib.metadata.packages_distributions()
    third_party_roots: set[str] = set()
    for module_name in (
        "dashboard.app",
        "dashboard.contracts",
        "dashboard.snapshot_store",
    ):
        for root in _module_source_imports(module_name):
            if root not in LOCAL_ROOTS and root not in sys.stdlib_module_names:
                third_party_roots.add(root)
    for root in sorted(third_party_roots):
        distributions = {normalize_distribution(item) for item in mapping.get(root, [])}
        if not distributions or not distributions.intersection(locked):
            raise PackagingGateError(
                f"third-party import is not closed by runtime lock: {root}"
            )

    distribution = importlib.metadata.distribution("omega-trust-scaffold")
    exact_requirements: dict[str, str] = {}
    for requirement in distribution.requires or []:
        match = REQUIREMENT.match(requirement)
        if match is None:
            raise PackagingGateError(f"wheel dependency is not exact: {requirement}")
        exact_requirements[normalize_distribution(match.group(1))] = match.group(2)
    for name, version in exact_requirements.items():
        if locked.get(name) != version:
            raise PackagingGateError(
                f"wheel dependency is missing or mismatched in runtime lock: {name}=={version}"
            )
        if importlib.metadata.version(name) != version:
            raise PackagingGateError(f"installed dependency version mismatch: {name}")
    return exact_requirements


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    wheel = subparsers.add_parser("wheel")
    wheel.add_argument("--wheel", type=Path, required=True)
    wheel.add_argument("--allowlist", type=Path, required=True)
    authority = subparsers.add_parser("authority")
    authority.add_argument("--repository-root", type=Path, required=True)
    installed = subparsers.add_parser("installed")
    installed.add_argument("--runtime-lock", type=Path, required=True)
    installed.add_argument("--checkout", type=Path)
    args = parser.parse_args()
    if args.command == "wheel":
        entries = inspect_wheel(args.wheel, args.allowlist)
        print(f"wheel allowlist verified: {len(entries)} entries")
    elif args.command == "authority":
        verify_single_dashboard_authority(args.repository_root.resolve(strict=True))
        print("single dashboard implementation verified")
    else:
        closure = verify_installed_environment(
            args.runtime_lock.resolve(strict=True),
            args.checkout.resolve(strict=True) if args.checkout else None,
        )
        print(
            f"installed import-to-lock closure verified: {len(closure)} direct requirements"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
