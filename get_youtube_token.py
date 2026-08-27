import json
from google_auth_oauthlib.flow import InstalledAppFlow

print("Download OAuth Desktop client JSON from Google Cloud and save it as client_secret.json.")
flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json", ["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=0)
print("\nCopy this value into the GitHub secret YOUTUBE_REFRESH_TOKEN:\n")
print(creds.refresh_token)

