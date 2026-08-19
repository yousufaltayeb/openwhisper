#!/usr/bin/env python3
import json
import sys


def write(frame):
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


sequence = 0
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "fixture.hang":
        continue
    if method == "app.bootstrap":
        result = {"protocolVersion": 1, "engineSessionId": "fixture"}
    elif method == "app.shutdown":
        write({"v": 1, "kind": "response", "id": request["id"], "ok": True, "result": {"accepted": True}})
        break
    else:
        result = {"accepted": True}
    write({"v": 1, "kind": "response", "id": request["id"], "ok": True, "result": result})
    if method == "dictation.start":
        sequence += 1
        write({"v": 1, "kind": "event", "seq": sequence, "event": "dictation.state", "payload": {"state": "recording", "sessionId": "fixture-session"}})
