"""Stands in for `python -m legoin_api.picture.worker` in the process runner's tests."""

import json
import sys
import time

spec = json.loads(sys.stdin.read())


def emit(event):
    print(json.dumps(event), flush=True)


behaviour = spec["behaviour"]
emit({"type": "calling", "build": 0, "worst": 0.1})
if behaviour == "done":
    print("not an event", flush=True)
    emit({"type": "failed", "build": 0, "code": "refused", "message": "Declined."})
elif behaviour == "hang":
    emit({"type": "calling", "worst": 0.2})
    time.sleep(60)
elif behaviour == "memory":
    hog = bytearray(200 * 1024 * 1024)
    hog[::4096] = b"x" * len(hog[::4096])
    time.sleep(60)
elif behaviour == "crash":
    sys.exit(3)
