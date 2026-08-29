#!/usr/bin/env python
"""Build the offline demo playground that the VHS tapes in assets/ record.

The GIFs must never depend on a live model or an API key: they are recorded
against a copy of the polygon fixture (tests/fixtures/victim_project) whose
on-disk LLM cache is pre-seeded from tests/fixtures/canned_mutants.json — the
same fixed mutant set the offline benchmark uses — plus hand-written
`--invent` replies. Every `mutagen run` in the tapes therefore replays from
cache: no network, identical output on every recording.

Usage:
    python scripts/demo.py [--dest DIR] [--no-check]

Prints the demo repo path (progress goes to stderr). scripts/record_demos.sh
feeds that path to the tapes through MUTAGEN_DEMO_DIR and then runs `vhs` on
every assets/*.tape.

The default --check first runs the exact command the tapes record and
verifies it passes fully from cache: every LLM call cached, $0.00, the
expected survivors, every suggested test `verified`. If that check breaks —
usually because a prompt or the cache-key construction changed — fix the
mirrors below before re-recording, or the "offline" recording will try to
call the API and fail loudly (or worse, silently cost money with a key set).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from benchmark import CANNED, DEFAULT_MAX_TOKENS, _map_coverage, make_repo  # noqa: E402

from mutagen_cli.cache import Cache  # noqa: E402
from mutagen_cli.generator import generate, mutants_per_target, read_test_context  # noqa: E402
from mutagen_cli.prompts import (  # noqa: E402
    INVENT_SCHEMA,
    INVENT_SYSTEM,
    MUTANT_SCHEMA,
    MUTANT_SYSTEM,
    invent_user,
    mutant_user,
)
from mutagen_cli.provider import ReplayProvider, default_model, reasoning_tag  # noqa: E402
from mutagen_cli.scope import collect_targets, map_tests  # noqa: E402

PROVIDER = "openrouter"
# Must match the recording command in the tapes: --path wins over --all in
# collect_targets, so the demo scope is exactly pricing.py.
DEMO_PATHS = ["victim/pricing.py"]
DEMO_MAX_MUTANTS = 3
DEMO_MAX_FILES = 20
# Kept out of the run env so a cache miss can never quietly turn the
# "offline" verification into a live, billed API call.
SECRET_ENV_VARS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")

# Hand-written `--invent` replies, one per surviving canned mutant of
# apply_discount, keyed by the mutant's search_block. Each one is
# double-verified by the pipeline itself (must pass on the real code, must
# fail on the mutant) — and again by --check, which asserts the `verified`
# status end-to-end. Blind spots B16-B18 of WEAK_TESTS.md.
INVENT_REPLIES = {
    "        discount = min(discount, max_discount)": {
        "test_name": "test_cap_is_a_ceiling",
        "explanation": (
            "Pins that the cap lowers a too-large discount, not raises a smaller one."
        ),
        "test_code": (
            "from victim.pricing import apply_discount\n\n\n"
            "def test_cap_is_a_ceiling():\n"
            "    assert apply_discount(100, 50, max_discount=10) == 90.0\n"
        ),
    },
    "    if percent < 0 or percent > 100:": {
        "test_name": "test_negative_percent_is_rejected",
        "explanation": "Pins that a negative percentage is rejected, not paid out.",
        "test_code": (
            "import pytest\n\n"
            "from victim.pricing import apply_discount\n\n\n"
            "def test_negative_percent_is_rejected():\n"
            "    with pytest.raises(ValueError):\n"
            "        apply_discount(100, -10)\n"
        ),
    },
    "    return round(price - discount, 2)": {
        "test_name": "test_total_is_rounded_to_cents",
        "explanation": "Pins cent-level rounding of the discounted total.",
        "test_code": (
            "from victim.pricing import apply_discount\n\n\n"
            "def test_total_is_rounded_to_cents():\n"
            "    assert apply_discount(1.0, 33) == 0.67\n"
        ),
    },
}


def build_demo_repo(dest: Path) -> Path:
    """Fresh polygon repo with the same uncommitted-edit beat as the old tape."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    repo = make_repo(dest)
    # A harmless comment inside the function under test gives the tapes their
    # "I changed something" `git diff --stat` beat. It must stay
    # behaviour-preserving: the canned search blocks have to keep matching.
    pricing = repo / "victim" / "pricing.py"
    text = pricing.read_text(encoding="utf-8")
    marker = "    discount = price * percent / 100.0"
    if marker not in text:
        raise SystemExit("victim/pricing.py drifted; update the demo comment edit")
    pricing.write_text(
        text.replace(marker, "    # percentages come from the promo engine\n" + marker),
        encoding="utf-8",
    )
    return repo


def openrouter_key(*prompt_parts: str) -> str:
    # Must mirror OpenRouterProvider.complete_json exactly — provider, model,
    # reasoning tag, max_tokens, system, user, sort_keys schema serialization
    # (see provider.py; same contract scripts/benchmark.py documents).
    return Cache.key(
        PROVIDER, default_model(PROVIDER), reasoning_tag(False),
        str(DEFAULT_MAX_TOKENS), *prompt_parts,
    )


def seed(repo: Path) -> int:
    """Pre-seed the on-disk cache for the demo scope. Returns the target count."""
    # macOS /var is a symlink to /private/var: collect_targets resolves the
    # --path argument before its is_relative_to(root) check, so the repo root
    # must be resolved too (repo_root() does the same for the real CLI).
    repo = Path(repo).resolve()
    canned = json.loads(CANNED.read_text(encoding="utf-8"))
    # collect_targets resolves relative --path values against the *current*
    # directory (scope.py), and the seeder does not run from inside the repo —
    # pass absolute paths, which it accepts verbatim.
    abs_paths = [str(repo / rel) for rel in DEMO_PATHS]
    targets = collect_targets(
        repo, base=None, paths=abs_paths, all_files=True, max_files=DEMO_MAX_FILES
    )
    map_tests(repo, targets)
    # Coverage mapping refines the test->function mapping the prompt carries;
    # skipping it would leave every key pointing at a heuristic-mapping prompt
    # the CLI never sends (the benchmark makes the same call).
    _map_coverage(repo, targets)
    per_target = mutants_per_target(len(targets), DEMO_MAX_MUTANTS)
    cache = Cache(repo, enabled=True)

    for target in targets:
        raw = [m for m in canned.get(target.label, []) if isinstance(m, dict)]
        reply = {"mutants": [{k: v for k, v in m.items() if k != "expected"} for m in raw]}
        user = mutant_user(target, read_test_context(repo, target), per_target)
        key = openrouter_key(MUTANT_SYSTEM, user, json.dumps(MUTANT_SCHEMA, sort_keys=True))
        cache.put(key, {"data": reply})

        # Rebuild the concrete Mutant objects from the same reply so each
        # --invent prompt — and therefore its cache key — matches byte-for-byte
        # what the recorded run will send.
        mutants, _, _ = generate(
            repo, [target], ReplayProvider([reply]), default_model(PROVIDER),
            max_mutants=DEMO_MAX_MUTANTS,
        )
        expected = {m["search_block"]: m.get("expected", "") for m in raw}
        for mutant in mutants:
            if expected.get(mutant.search_block) != "survived":
                continue  # --invent is only called for survivors
            reply_body = INVENT_REPLIES.get(mutant.search_block)
            if reply_body is None:
                raise SystemExit(
                    f"no hand-written --invent reply for surviving mutant: "
                    f"{mutant.search_block!r} — add one to INVENT_REPLIES"
                )
            iuser = invent_user(target, mutant, read_test_context(repo, target))
            ikey = openrouter_key(
                INVENT_SYSTEM, iuser, json.dumps(INVENT_SCHEMA, sort_keys=True)
            )
            cache.put(ikey, {"data": reply_body})
    return len(targets)


def verify(repo: Path) -> dict:
    """Run the exact command the tapes record; assert it replays from cache.

    Runs against a throwaway copy: --invent-apply writes tests into
    tests/mutagen_generated/, map_tests then folds them into the function's
    test context, and every cache key in the *original* playground would go
    stale — the tapes would record a live-key failure instead of a demo.
    """
    fd, out_name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    out = Path(out_name)
    check_repo = Path(tempfile.mkdtemp(prefix="mutagen-demo-check-")) / "repo"
    shutil.copytree(repo, check_repo)
    cmd = [
        sys.executable, "-m", "mutagen_cli.cli", "run",
        "--path", *DEMO_PATHS, "--all",
        "--max-mutants", str(DEMO_MAX_MUTANTS),
        "--invent-apply", "--report-json", str(out),
    ]
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV_VARS}
    env["PYTHONPATH"] = str(REPO / "src")
    try:
        proc = subprocess.run(cmd, cwd=str(check_repo), env=env, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit("the demo run failed — the tapes would record this failure")
        report = json.loads(out.read_text(encoding="utf-8"))
    finally:
        out.unlink(missing_ok=True)
        shutil.rmtree(check_repo.parent, ignore_errors=True)

    usage = report["usage"]
    problems = []
    if usage["calls"] != usage["cached_calls"] or usage["failed_calls"]:
        problems.append(f"not fully served from cache: {usage}")
    if usage["cost_usd"] != 0:
        problems.append(f"unexpected cost: ${usage['cost_usd']}")
    if len(report["mutants"]) != DEMO_MAX_MUTANTS:
        problems.append(f"expected {DEMO_MAX_MUTANTS} mutants, got {len(report['mutants'])}")
    survivors = [m for m in report["mutants"] if m["verdict"] == "survived"]
    if len(survivors) != DEMO_MAX_MUTANTS:
        problems.append(
            f"expected {DEMO_MAX_MUTANTS} survivors, got {len(survivors)} — the canned "
            "verdicts no longer hold, update INVENT_REPLIES/canned_mutants.json"
        )
    for mutant in survivors:
        if mutant.get("suggested_test_status") != "verified":
            problems.append(
                f"suggested test not verified for: {mutant['description']!r} "
                f"({mutant.get('suggested_test_status')})"
            )
    if problems:
        raise SystemExit("demo verification failed:\n  " + "\n  ".join(problems))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path,
        default=Path(tempfile.gettempdir()) / "mutagen-demo",
        help="where to build the demo playground (rebuilt from scratch)",
    )
    parser.add_argument("--no-check", action="store_true", help="skip the offline run check")
    args = parser.parse_args()

    print("building demo playground...", file=sys.stderr)
    repo = build_demo_repo(args.dest)
    n_targets = seed(repo)
    print(f"seeded cache for {n_targets} target(s)", file=sys.stderr)
    if not args.no_check:
        print("verifying the recorded command replays from cache...", file=sys.stderr)
        report = verify(repo)
        score = report["score"]
        print(
            f"check ok: {report['usage']['calls']} LLM calls, all cached, $0.00, "
            f"score {score * 100:.0f}%, {report['duration_seconds']:.1f}s",
            file=sys.stderr,
        )
    print(repo)


if __name__ == "__main__":
    main()

