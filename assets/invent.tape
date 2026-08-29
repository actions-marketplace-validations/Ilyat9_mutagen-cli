# The --invent demo: survivors turned into verified catching tests.
# Regenerate the GIF with: scripts/record_demos.sh (fully offline — see
# assets/demo.tape for how the cache-only playground is built).
#
# Beats: run with --invent-apply (each suggested test is double-verified:
# must pass on the real code, must fail on the mutant), then run the
# generated tests to show they pass on the clean code.

Output assets/invent.gif

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 900
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set Padding 20

Hide
Type 'cd "$MUTAGEN_DEMO_DIR" && clear'
Enter
Sleep 500ms
Show

Type "mutagen run --path victim/pricing.py --all --max-mutants 3 --invent-apply"
Sleep 500ms
Enter
Sleep 8000ms

Type "pytest tests/mutagen_generated -q"
Enter
Sleep 3500ms

Sleep 3000ms
