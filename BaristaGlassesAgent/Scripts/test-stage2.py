"""Build and test registration/session readiness with the SDK's MockDeviceKit.

Run: python3 Scripts/test-stage2.py
Requires an already booted iOS Simulator and the existing Xcode/Meta SDK. No
camera capability, video recording, backend, or additional dependency is used.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "DEVELOPER_DIR": "/Applications/Xcode.app/Contents/Developer"}
BUNDLE = "com.meta.hackathon.BaristaGlassesAgent"


def command(*arguments):
    return subprocess.check_output(arguments, env=ENV, cwd=ROOT, text=True).strip()


def main():
    devices = json.loads(command("xcrun", "simctl", "list", "devices", "booted", "--json"))
    device = next((d for group in devices["devices"].values() for d in group if d["state"] == "Booted"), None)
    if not device:
        raise SystemExit("Boot an iOS Simulator in Xcode first.")
    simulator = device["udid"]
    log = Path("/tmp/cafepilot-stage2-simulator-build.log")
    with log.open("w") as output:
        subprocess.run(["xcodebuild", "-project", "BaristaGlassesAgent.xcodeproj", "-scheme", "BaristaGlassesAgent",
                        "-configuration", "Debug", "-destination", f"platform=iOS Simulator,id={simulator}",
                        "-disableAutomaticPackageResolution", "META_APP_ID=0", "CLIENT_TOKEN=", "build"],
                       env=ENV, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
    settings = json.loads(command("xcodebuild", "-project", "BaristaGlassesAgent.xcodeproj", "-scheme", "BaristaGlassesAgent",
                                  "-configuration", "Debug", "-destination", f"platform=iOS Simulator,id={simulator}",
                                  "-showBuildSettings", "-json"))[0]["buildSettings"]
    app = Path(settings["TARGET_BUILD_DIR"]) / settings["FULL_PRODUCT_NAME"]
    command("xcrun", "simctl", "install", simulator, str(app))
    run_id = str(uuid4())
    command("xcrun", "simctl", "launch", "--terminate-running-process", simulator, BUNDLE,
            "--stage2-self-test", "--test-run-id", run_id)
    container = Path(command("xcrun", "simctl", "get_app_container", simulator, BUNDLE, "data"))
    report = container / "Documents/Diagnostics/stage2-smoke-test.json"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if report.exists():
            result = json.loads(report.read_text())
            if result.get("run_id") == run_id:
                print(json.dumps(result, indent=2))
                print("Report:", report)
                if not result["passed"]:
                    raise SystemExit(1)
                assert (container / "Documents/Recordings").is_dir()
                return
        time.sleep(0.2)
    raise SystemExit(f"Mock test timed out. Inspect {container / 'Documents/startup.log'} and {log}.")


if __name__ == "__main__":
    main()
