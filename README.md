# Youtube Music Scrobbler

This is a Python script that allows you to fetch your YouTube Music listening history and scrobble it to Last.fm. automatically using GitHub Actions. This means your listening history will be fetched and scrobbled to Last.fm on a schedule, without needing to run the script manually on your local machine.

## Setup

I recommend using [uv](https://docs.astral.sh/uv/) for this project.

1. Clone or download the repository to your local machine.

2. Create a set of credentials through Google Cloud Console as described
[here](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html).

3. Create an API key and secret for Last.fm
[here](https://www.last.fm/api/account/create).

4. Create a `.env` file in the project's root directory with the following
information:

```sh
LAST_FM_API=...
LAST_FM_API_SECRET=...
LAST_FM_USERNAME=...
```

5. Use `ytmusicapi` to authenticate with Youtube Music by running

```sh
uv run ytmusicapi browser
```

Follow the prompts to create `browser.json`

6. Now you can start the script with

```sh
uv run start.py
```

## Automation

1. Fork this repository (or clone if you want it private)

2. Run the script once locally to retrieve your Last.fm session key:

```sh
uv run start.py
```

This will generate oauth.json and output your Last.fm session key, which you’ll need for automation.

3. Add the following secrets under "Settings > Secrets and Variables > Actions > Repository Secrets" in your GitHub repository:

```sh
LAST_FM_API=...
LAST_FM_API_SECRET=...
LAST_FM_USERNAME=...
LASTFM_SESSION=...
BROWSER_JSON=...  (Paste the entire contents of browser.json as a single secret)
```

4. Enable GitHub Actions in your repository settings. Once set up, GitHub Actions will run the script daily at 1:00 AM UTC or manually via the “Run workflow” button.

# How It Works

## Fetching YouTube Music History:
The script retrieves your YouTube Music listening history for today and yesterday using the ytmusicapi library. It processes each track in the history to prepare it for scrobbling to Last.fm.

## Duplicate Prevention:
The script uses a local SQLite database (`data.db`) to track recently scrobbled songs. Before scrobbling any track, it checks:
- **Database check**: Has this track/artist combination been scrobbled in the last 2 hours?
- **Video ID check**: Has this specific YouTube video been scrobbled recently?
- **Session check**: Has this track already been scrobbled in the current run?

This multi-layer approach prevents duplicate scrobbles even if you replay the same song multiple times.

## Metadata Normalization:
Track and artist names are cleaned to ensure consistent matching:
- Removes " - Topic" suffix from YouTube auto-generated artist channels
- Strips common video suffixes like "(Official Video)", "(Lyric Video)", etc.
- Normalizes whitespace

## Scrobbling to Last.fm:
Tracks that pass duplicate checks are scrobbled to Last.fm with:
- **Natural spacing**: 90-second delays between scrobbles to appear realistic
- **Backdated timestamps**: Scrobbles are timestamped 30 seconds in the past
- **Rate limiting**: 0.5-second delays between API calls to avoid rate limits

## Database Maintenance:
The script automatically cleans up old database records (older than 6 hours) to keep the database size manageable while maintaining recent history for duplicate detection.

## Logging:
Detailed timestamped logs show the progress of each operation, including successful scrobbles, skipped duplicates, and any errors encountered.

**Note:** Running this workflow on GitHub's servers counts toward your GitHub Actions usage limits. If you fork this repository or enable the workflow on your own repo, be aware that excessive runs may consume your free GitHub Actions minutes or lead to rate limits.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE)
file for more information.