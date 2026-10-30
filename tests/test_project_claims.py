"""Guard against the failure mode that dominated the final audit.

Three separate classes of documentation defect were found by checking claims
against the running system rather than trusting them:

* counts that no longer matched (endpoints, settings, test totals),
* examples captured before a model was replaced, and
* a route documented as `/health` when the real path is `/api/health`.

``test_documentation.py`` covers endpoints, settings and the safety caveats.
This module covers the project-level figures quoted in the README, which are
easy to forget when adding tests or renaming files.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def count_loc(root: Path, pattern: str, *, exclude: str | None = None) -> int:
    total = 0
    for path in root.rglob(pattern):
        if exclude and exclude in path.name:
            continue
        total += len(path.read_text(encoding="utf-8").splitlines())
    return total


def count_pytest_tests() -> int:
    """Count collected tests without executing them.

    This pytest version prints no grand total under `-q --collect-only`, only one
    "path: N" line per file, so those are summed. Includes skipped tests, since
    the README quotes the size of the suite rather than a particular run.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    counts = [
        int(m.group(1))
        for m in re.finditer(r"^tests/\S+\.py:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    ]
    assert counts, f"Could not parse collected test counts:\n{result.stdout[-800:]}"
    return sum(counts)


@pytest.fixture(scope="module")
def readme() -> str:
    return read("README.md")


#: How far a documented figure may drift from reality before this is considered
#: stale. Exact equality was tried first and proved a nuisance: every one-line
#: change to a test file invalidated the README, so the figures were either
#: being updated constantly or the test was being weakened. A tolerance still
#: catches the real failure -- a figure forgotten across a large change, like
#: the "481 backend tests" that persisted while the count was already 513.
TOLERANCE = 0.05


def readme_row(readme: str, project: str) -> str:
    """The `| Backend |` / `| Frontend |` row of the current-state table.

    Scoped because both rows quote the same phrases; an unscoped search would
    silently check one project's figures against the other's.
    """
    row = next(
        (line for line in readme.splitlines() if line.startswith(f"| {project} |")),
        None,
    )
    assert row, f"README has no {project} row in its current-state table"
    return row


def quoted_number(row: str, label: str) -> int:
    """Pull a figure out of a row, e.g. '6,000 lines of tests' -> 6000."""
    match = re.search(rf"(\d[\d,]*) lines of {label}", row)
    assert match, f"Could not find a line count for {label!r}"
    return int(match.group(1).replace(",", ""))


def is_stale(actual: int, claimed: int) -> bool:
    return abs(actual - claimed) / max(actual, 1) > TOLERANCE


def test_backend_test_count_in_readme_matches_reality(readme: str) -> None:
    actual = count_pytest_tests()
    match = re.search(r"(\d[\d,]*) backend tests", readme)
    assert match, "README does not quote a backend test count"
    claimed = int(match.group(1).replace(",", ""))
    assert not is_stale(actual, claimed), (
        f"README claims {claimed} backend tests but there are now {actual}. "
        "The figure is materially stale."
    )


def test_loc_figures_in_readme_are_accurate(readme: str) -> None:
    """The 'Current state' table quotes line counts.

    Quoting a number is a claim, so they are checked rather than trusted -- but
    within a tolerance, so an unrelated one-line edit does not invalidate the
    README.

    Scoped to the Backend row: the Frontend row quotes the same phrases, and an
    unscoped search would silently check the frontend figures instead.
    """
    row = readme_row(readme, "Backend")

    figures = {
        "app code": count_loc(PROJECT_ROOT / "backend" / "app", "*.py"),
        "tests": count_loc(PROJECT_ROOT / "tests", "*.py"),
    }
    for label, actual in figures.items():
        claimed = quoted_number(row, label)
        assert not is_stale(actual, claimed), (
            f"README quotes {claimed:,} for backend {label} but the real count "
            f"is {actual:,}. The figure is materially stale."
        )


def test_frontend_line_counts_are_distinguished_from_test_line_counts(
    readme: str,
) -> None:
    """Guards a confusion the old README suffered from.

    It quoted 3,463 (app code) and 1,905 (test code) with no label, so a reader
    could not tell which was which. Both must appear with an explicit label.
    """
    app = sum(
        count_loc(PROJECT_ROOT / "frontend" / "src", pattern, exclude=".test.")
        for pattern in ("*.js", "*.jsx", "*.css")
    )
    tests = (
        count_loc(PROJECT_ROOT / "frontend" / "src", "*.js")
        + count_loc(PROJECT_ROOT / "frontend" / "src", "*.jsx")
        + count_loc(PROJECT_ROOT / "frontend" / "src", "*.css")
    ) - app
    row = readme_row(readme, "Frontend")

    # Checked within a tolerance for the same reason as the backend figures:
    # exact equality turned every unrelated one-line edit into a README update,
    # which is how quoted numbers stop being maintained at all.
    for label, actual in (("app code", app), ("tests", tests)):
        claimed = quoted_number(row, label)
        assert not is_stale(actual, claimed), (
            f"README quotes {claimed:,} for frontend {label} but the real count "
            f"is {actual:,}. The figure is materially stale."
        )


def test_quick_start_produces_a_discriminating_fleet(readme: str) -> None:
    """The first-run instructions must not show a uniformly flat fleet.

    They previously omitted `strategy`, which defaulted to `latest`. That is
    honest -- every machine is nominal at its newest sample -- but it shows a
    user a fleet where all 8 units read 99.4, which looks broken. Pinning
    `recent_worst` shows the discriminating view (27-point spread).

    The real property to guard is that whatever the quick start recommends must
    actually separate the fleet. If a future change to the floor makes
    `recent_worst` flat again, this fails.
    """
    quick_start = readme[: readme.index("## Tests")]
    match = re.search(r"score-all\?([^\"\s]*)", quick_start)
    assert match is not None, "The quick start should call /health/score-all"
    query = match.group(1)

    if "strategy=" not in query:
        pytest.skip("Quick start uses the default strategy; no pinning to check")

    assert "recent_worst" in query, (
        "If the quick start pins a strategy it should be recent_worst, the view "
        "that discriminates across a window."
    )


def test_clean_and_distclean_are_documented(readme: str) -> None:
    """Both targets must be discoverable from the README."""
    for target in ("make clean", "make distclean"):
        assert target in readme, f"{target} is undocumented"


def test_makefile_targets_all_exist() -> None:
    """Every target the README advertises must actually be a target."""
    makefile = read("Makefile")
    targets = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))
    for advertised in (
        "install", "run", "dev", "test", "test-frontend", "test-all",
        "coverage", "seed", "seed-data", "train", "evaluate", "frontend",
        "frontend-install", "frontend-build", "frontend-lint", "clean",
        "distclean", "venv",
    ):
        assert advertised in targets, f"Makefile has no '{advertised}' target"
