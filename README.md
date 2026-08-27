# Idlevelocity — Fully Automated Hinglish Shorts

This project creates and publishes two vertical self-improvement Shorts every day. It uses Gemini for original scripts, Pexels for licensed stock video, Microsoft Edge neural voice for Hinglish narration, FFmpeg for 1080×1920 rendering and the official YouTube Data API for uploading.

## What happens automatically

1. Selects an unused topic from `data/topics.txt`.
2. Writes an original 95–120 word Hinglish script.
3. Downloads matching portrait stock footage.
4. Creates a Hindi neural voiceover and timed captions.
5. Renders a 1080×1920 MP4.
6. Uploads it publicly to YouTube with title, description and hashtags.
7. Records the YouTube ID so topics are not repeated.

Default times are **09:17 and 18:17 UAE time** (GitHub schedule uses UTC). Scheduled runs can occasionally be delayed by GitHub.

## One-time setup from Android

### 1. Create a private GitHub repository

Create a private repository named `idlevelocity-shorts-automation`. Upload every file and folder from this package, keeping the folder structure unchanged.

### 2. Get free keys

- **Gemini:** Create an API key in Google AI Studio. Restrict it to Gemini API only.
- **Pexels:** Create a free Pexels account and request an API key at `pexels.com/api`.
- **YouTube:** In Google Cloud Console, create a project, enable **YouTube Data API v3**, configure the OAuth consent screen and create an OAuth client of type **Desktop app**. Download its JSON as `client_secret.json`.

Never upload `client_secret.json` or paste keys into source files.

### 3. Generate the YouTube refresh token

Use your Termux Ubuntu environment because OAuth needs a callback on the same device:

1. Put `client_secret.json` and this project in the Ubuntu-accessible folder.
2. Open Ubuntu, enter the project folder and run `python3 -m venv .venv`.
3. Run `source .venv/bin/activate`.
4. Run `pip install google-auth-oauthlib`.
5. Run `python get_youtube_token.py`.
6. Android should open the Google authorization page. Select the Google account that owns **@idlevelocity0**, approve access and return to the terminal.
7. Copy the displayed refresh token, then delete `client_secret.json` from the phone.

If the browser does not open automatically, copy the authorization URL shown in the terminal and open it in the same phone's browser while the helper remains running.

### 4. Add repository secrets

GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | Gemini key |
| `PEXELS_API_KEY` | Pexels key |
| `YOUTUBE_CLIENT_ID` | `client_id` from the downloaded JSON |
| `YOUTUBE_CLIENT_SECRET` | `client_secret` from the downloaded JSON |
| `YOUTUBE_REFRESH_TOKEN` | Value produced by the helper |

### 5. Test before automatic publishing

Open **Actions → Generate and publish Idlevelocity Short → Run workflow**. The current configuration publishes immediately, so set `privacy_status` in `config.json` to `unlisted` for the first test if desired. Check the generated Short, captions, sound, title and description.

After a successful test, leave the workflow enabled. It will run twice daily without your phone being online.

## Customization

- Add one topic per line in `data/topics.txt`.
- Change the voice, niche, hashtags, duration target or upload visibility in `config.json`.
- Change publish times in `.github/workflows/publish-short.yml`. Cron is currently UTC.
- Set the repository variable `GEMINI_MODEL` if the default model name changes.

## Important safeguards

- Stock clips come from Pexels, but keep a record of the source API response if you need a detailed licensing audit.
- Do not reuse other creators' scripts, voices, clips or music.
- Fully automated output can make factual or tonal mistakes. Check the channel regularly and immediately disable the workflow if quality drops.
- Never commit credentials. Rotate any key that is accidentally exposed.
- Avoid repetitive, mass-produced variations. Add fresh topics and review audience retention to keep content genuinely useful.

## Troubleshooting

- **401 from YouTube:** refresh token revoked or OAuth credentials do not match.
- **403 from YouTube:** API disabled, quota/permission issue, or the OAuth app/channel is not authorized.
- **Gemini 404:** set a currently available free model in the `GEMINI_MODEL` repository variable.
- **No stock result:** simplify the topic's generated `stock_query` or rerun.
- **Workflow did not run exactly on time:** scheduled GitHub jobs may be delayed; manual runs remain available.
