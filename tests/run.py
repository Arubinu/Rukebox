"""Runs the Python suite with one process per test module, side by side: the same
tests as `python -m unittest discover -s tests`, in a fraction of the time.

    python tests/run.py                  every module
    python tests/run.py test_web test_party
    RUKEBOX_TEST_JOBS=4 python tests/run.py
"""
import concurrent.futures
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run_module(name):
    started = time.monotonic()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-m", "unittest", name], cwd=HERE, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = done.stdout.decode("utf-8", "replace")
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    skipped = re.search(r"skipped=(\d+)", output)
    return {"name": name, "ok": done.returncode == 0, "output": output,
            "tests": int(ran.group(1)) if ran else 0, "skipped": int(skipped.group(1)) if skipped else 0,
            "seconds": time.monotonic() - started}


def main(argv):
    names = [arg[:-3] if arg.endswith(".py") else arg for arg in argv] or sorted(
        f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    # The biggest files first: the slow ones should not be left for the end.
    names.sort(key=lambda name: -os.path.getsize(os.path.join(HERE, name + ".py")))
    jobs = int(os.environ.get("RUKEBOX_TEST_JOBS") or os.cpu_count() or 4)
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_module, names))
    failed = [r for r in results if not r["ok"]]
    for r in failed:
        print("=" * 70)
        print("%s FAILED" % r["name"])
        print(r["output"].rstrip())
    slowest = sorted(results, key=lambda r: -r["seconds"])[:3]
    print("-" * 70)
    print("Ran %d tests in %.1fs (%d modules, %d at once; slowest: %s)" % (
        sum(r["tests"] for r in results), time.monotonic() - started, len(results), jobs,
        ", ".join("%s %.1fs" % (r["name"], r["seconds"]) for r in slowest)))
    skipped = sum(r["skipped"] for r in results)
    if failed:
        print("FAILED (%s)" % ", ".join(r["name"] for r in failed))
        return 1
    print("OK" + (" (skipped=%d)" % skipped if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
