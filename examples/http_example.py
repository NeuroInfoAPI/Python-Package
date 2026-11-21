from datetime import datetime

from neuroinfoapi_client import NeuroApiError, NeuroInfoApiClient, utils

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

client = NeuroInfoApiClient()  # no token needed for public endpoints
# client = NeuroInfoApiClient("YOUR_API_TOKEN")  # token required for some endpoints (see README)

stream = client.get_current_stream()
if utils.is_live(stream):
    print(f"LIVE: {stream['title']} ({stream['viewerCount']} viewers)")
else:
    print("Offline. Last title:", stream["title"])

schedule = client.get_latest_schedule()
final = "confirmed" if utils.is_schedule_final(schedule["status"]) else "not final yet"
print(f"\nSchedule {schedule['year']} week {schedule['week']} ({final}):")
for entry in schedule["schedule"]:
    when = datetime.fromtimestamp(entry["time"] / 1000).strftime("%d.%m. %H:%M")
    print(f"  {DAYS[entry['day']]} {when}  {entry['message']}")

# Errors are raised as NeuroApiError
try:
    subathons = client.get_current_subathons()
    print("\nActive subathons:", [s["name"] for s in subathons])
except NeuroApiError as error:
    if error.code == "SB1":
        print("\nNo subathon right now")
    else:
        print("Error:", error.code, error.message)
