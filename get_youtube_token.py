import os
import subprocess
from google_auth_oauthlib.flow import InstalledAppFlow

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",
        ["https://www.googleapis.com/auth/youtube.upload"]
    )
    print("\nStarting local OAuth server on port 8080...")
    creds = flow.run_local_server(port=8080, open_browser=False, prompt="consent", access_type="offline")
    refresh_token = creds.refresh_token
    print(f"\nAuthorization successful!")
    
    try:
        subprocess.run(
            ["gh", "secret", "set", "YOUTUBE_REFRESH_TOKEN", "-R", "prashant-rajpu/idlevelocity-shorts-automation", "-a", "actions", "-b", refresh_token],
            check=True
        )
        print("✓ Successfully saved YOUTUBE_REFRESH_TOKEN to GitHub repository secrets!")
    except Exception as e:
        print(f"Could not automatically set GitHub secret: {e}")
        print(f"Please manually add YOUTUBE_REFRESH_TOKEN:\n{refresh_token}")

if __name__ == "__main__":
    main()
