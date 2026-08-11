"""Scheduler entry point: invoke once per minute from a trusted platform cron."""
import os
import urllib.request

url = os.environ["CALLPULSE_API_URL"].rstrip("/") + "/launcher/run"
request = urllib.request.Request(url, method="POST", headers={"Authorization": f"Bearer {os.environ['CALLPULSE_ACTIONS_API_KEY']}"})
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.read().decode())
