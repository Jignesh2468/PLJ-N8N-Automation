"""Run every test (no API keys needed):  python run_all_tests.py
Use this before replacing the code with any update — all lines must say PASSED."""
import os
import subprocess
import sys

TESTS = ["tests/test_igr_match.py", "tests/test_city_layout.py", "tests/test_patterns.py", "tests/test_routing.py",
         "tests/test_extract_split.py", "tests/test_subject_merge.py", "tests/test_generic_inputs.py",
         "tests/test_all_cases.py"]
failed = 0
for t in TESTS:
    # UTF-8 both ways: the tests print Marathi, which the Windows console code page cannot decode
    r = subprocess.run([sys.executable, t], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    last = (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0
    failed += not ok
    print(f"{'PASSED' if ok else 'FAILED'}  {t}  {'' if ok else (r.stdout[-800:] + r.stderr[-800:])}")
print("\nALL TESTS PASSED — safe to use" if not failed else f"\n{failed} TEST FILE(S) FAILED — do not use this version")
sys.exit(1 if failed else 0)
