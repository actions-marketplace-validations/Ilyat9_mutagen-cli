# Regenerate the GIF with: scripts/record_demos.sh
# (requires vhs + ffmpeg: brew install vhs ffmpeg)
#
# The recording is fully offline: scripts/record_demos.sh rebuilds the demo
# playground from tests/fixtures/victim_project and pre-seeds its LLM cache
# from tests/fixtures/canned_mutants.json (the same polygon the offline
# benchmark uses), so `mutagen run` replays from cache — no API key, no
# network, identical output on every recording. If the check inside
# scripts/demo.py fails, fix the cache-key mirrors there before re-recording.

Output assets/demo.gif

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

Type "git diff --stat"
Enter
Sleep 2000ms

Type "mutagen run --path victim/pricing.py --all --max-mutants 3"
Sleep 500ms
Enter
Sleep 6000ms

Sleep 4000ms
