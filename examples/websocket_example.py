"""Listens for live events. No asyncio needed: ws.run() blocks until Ctrl+C."""

from neuroinfoapi_client import NeuroInfoApiWebsocketClient

ws = NeuroInfoApiWebsocketClient("YOUR_API_TOKEN")


@ws.on("streamOnline")
def on_online(data, timestamp):
    print("Live:", data["title"])


@ws.on("streamOffline")
def on_offline(data, timestamp):
    print("Stream ended")


@ws.on("scheduleUpdate")
def on_schedule(data, timestamp):
    print(f"Schedule updated: {data['year']} week {data['week']}")


ws.on("_connected", lambda session_id: print("Connected, session", session_id))
ws.on("_disconnected", lambda code, reason: print("Disconnected:", code, reason))
ws.on("_reconnecting", lambda attempt, delay: print(f"Reconnecting in {delay:.1f}s (attempt {attempt})"))
ws.on("_error", lambda error: print("Error:", error))

print("Waiting for events, press Ctrl+C to stop")
ws.run()
