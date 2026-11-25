"""Stands in for `python -m legoin_api.picture.worker` in the process runner's tests."""

import json
import sys
import time

spec = json.loads(sys.stdin.read())


def emit(event):
    print(json.dumps(event), flush=True)


behaviour = spec["behaviour"]
emit({"type": "stage", "stage": "designing"})
if behaviour == "done":
    print("not an event", flush=True)
    emit({"type": "failed", "code": "picture_unusable", "message": "Nothing to build."})
elif behaviour == "hang":
    emit({"type": "calling", "worst": 0.2})
    time.sleep(60)
elif behaviour == "memory":
    hog = bytearray(200 * 1024 * 1024)
    hog[::4096] = b"x" * len(hog[::4096])
    time.sleep(60)
elif behaviour == "crash":
    sys.exit(3)
