# Outlooker

**Version 1.0.0**

Outlooker watches the NWS Storm Prediction Center's (SPC) convective outlooks for one or more U.S. counties and posts an alert to a Slack channel when a severe weather risk touching the county is new or goes up. It runs on your own computer or on GitHub Actions, uses only the Python standard library, and is set up with one settings file.

**Features**

- **Outlooks checked:** Days 1–3 categorical risk (Marginal to High), and Days 1–2 tornado, hail and damaging wind probabilities, each with SPC's Conditional Intensity Group (CIG) levels.
- **Your thresholds:** every alert type can be turned on or off, with its own alert and @channel ping levels.
- **No repeats:** a state file per county means each risk is announced once per outlook period, and again only if it goes up.
- **Lowered risks (optional):** a message when a risk you were alerted about is lowered or removed.
- **Several counties** in one run, each with its own alerts and a name of your choice (Home, Work …).
- **Logs channel (optional):** the full output of every check, posted to a second channel.
- **Heartbeat (optional):** a check-in with a monitoring service such as healthchecks.io after every run, so you learn when the checks stop.
- **Several instances (optional):** run it in more than one place without duplicate alerts, with each message signed by the instance that sent it.
- **Clear messages:** a large title, a one-line summary per day, the details, and links to SPC and to your own weather briefing.

## Requirements

- Python 3.9 or newer. No other packages are needed, except on Windows, where you also run `pip install tzdata`.
- A Slack workspace where you can add an app.
- Internet access to `www.spc.noaa.gov`, `tigerweb.geo.census.gov` (to download each county's outline once) and `hooks.slack.com`.

## Setup on your own computer

1. **Download** the code, either with Git or as a ZIP from GitHub (Code → Download ZIP):
   ```bash
   git clone https://github.com/calebking-me/Outlooker.git
   cd Outlooker
   ```
2. **Create your settings file.** Copy the example next to `Outlooker.py`:
   ```bash
   cp outlooker.example.ini outlooker.ini
   ```
   On Windows: `copy outlooker.example.ini outlooker.ini`.
3. **Fill it in.** Paste your alerts channel webhook URL under `[alerts-channel]` (see [Creating the Slack app](#creating-the-slack-app)), and pick your counties under `[county]` or `[location NAME]`. Every setting is explained in the file. `outlooker.ini` holds secrets, so keep it private; `.gitignore` already keeps it out of Git.
4. **Test it:**
   ```bash
   python3 Outlooker.py --test-webhook
   ```
   A test message should appear in your alerts channel (and in the logs channel, if you set one up).
5. **Schedule it**, for example every 15 minutes.
   - **Linux or macOS (cron):** run `crontab -e` and add:
     ```
     */15 * * * * /usr/bin/python3 /path/to/Outlooker/Outlooker.py
     ```
   - **Windows (Task Scheduler):** in a Command Prompt:
     ```
     schtasks /Create /TN Outlooker /SC MINUTE /MO 15 /TR "\"C:\Path\to\python.exe\" \"C:\Path\to\Outlooker\Outlooker.py\""
     ```

The script keeps its log (`spc_county_alert.log`), state files and county outlines next to `Outlooker.py`, so the folder must be writable.

## Setup on GitHub Actions

The repository includes `.github/workflows/outlooker.yml`, which runs the check on GitHub's servers on a schedule, so no computer has to stay on.

1. **Add the secrets** under Settings → Secrets and variables → Actions:

   | Secret | Needed for |
   |---|---|
   | `SLACK_ALERTS_WEBHOOK_URL` | the alerts channel (required) |
   | `SLACK_LOGS_WEBHOOK_URL` | the logs channel (optional; on when set) |
   | `OUTLOOKER_HEARTBEAT_URL`, `OUTLOOKER_FAILURE_URL` | the heartbeat (optional) |
   | `SLACK_BOT_TOKEN` | the duplicate check (optional) |

2. **Edit `outlooker.actions.ini`** in the repository for everything else: counties, thresholds, the briefing link and so on. This file is public, so never put a URL or token in it. Commit the change, and the next run uses it.
3. **Run it by hand** from the Actions tab: choose Outlooker → Run workflow, then pick `test-webhook` first, then `check`. Other choices are `test-alert`, `test-heartbeat` and `dry-run`.
4. **Edit the schedule** with the `cron:` line in the workflow file. Times are in UTC.

> **Scheduled runs can start late.** GitHub treats schedules as best effort: a new or changed schedule can take a while to start running, runs can be delayed or skipped when GitHub is busy (especially around the top of the hour), and GitHub turns off schedules in public repositories after 60 days with no activity. The heartbeat is the way to find out when runs stop.

## Creating the Slack app

All of this is done at [api.slack.com/apps](https://api.slack.com/apps).

1. **Create the app.** Create New App → From scratch, then name it (for example *Outlooker*) and choose your workspace.
2. **Webhooks.** Open **Incoming Webhooks** and switch it on. Click **Add New Webhook to Workspace**, choose the alerts channel, and copy the URL into `webhook` under `[alerts-channel]`. For the optional logs channel, add a second webhook for that channel and put its URL under `[logs-channel]`.
3. **Bot token (only for `check-duplicates`).** Reading the channel needs a token; webhooks can only post.
   - Under **OAuth & Permissions → Bot Token Scopes**, add `channels:history` (or `groups:history` for a private channel).
   - Click **Install to Workspace** (or **Reinstall**), and copy the **Bot User OAuth Token** (`xoxb-…`) into `bot-token`.
   - In the alerts channel, invite the app: `/invite @Outlooker`.
   - Copy the channel's ID from the channel name → **View channel details** (at the bottom, for example `C0123456789`) into `channel-id`.

The webhook URLs and the bot token are secrets: anyone who has them can post to your channels.

## Settings reference

Settings are read from `outlooker.ini` next to the script, or from the file named by `--config`. Command-line options win over environment variables, which win over the settings file. A blank setting uses its default.

| Section | Setting | Default | Meaning |
|---|---|---|---|
| `[county]` | `fips` / `county` + `state` | Montgomery County, TN (47125) | The county to check: a 5-digit FIPS code, or a name plus state |
| `[location NAME]` | `fips` / `county` + `state` | — | One more county per section; `NAME` (e.g. Home) appears in its alerts |
| `[alerts-channel]` | `webhook` | — (required) | The alerts channel's incoming webhook URL |
| | `ping` | `yes` | `no` never pings @channel, whatever the thresholds say |
| | `report-lowered` | `no` | Also post when a risk is lowered or removed |
| | `check-duplicates` | `no` | Skip an alert another instance already posted |
| | `bot-token` | — | Slack bot token, for `check-duplicates` |
| | `channel-id` | — | The alerts channel's ID, for `check-duplicates` |
| | `duplicate-window` | `24` | Hours of the channel's history to check |
| | `briefing-url` | — | A link to your weather briefing, shown under alerts |
| | `briefing-text` | `View Latest Weather Briefing` | That link's text |
| `[logs-channel]` | `enabled` | on when a webhook is set | Post every run's full output |
| | `webhook` | — | The logs channel's incoming webhook URL |
| `[categorical]` | `enabled` | `yes` | Check the categorical outlooks (Days 1–3) |
| | `alert-level` | `Slight` | Alert at this level or higher (Marginal … High, or 1–5) |
| | `ping-level` | `Enhanced` | Ping at this level or higher (`off` never pings) |
| | `ping-days` | `1, 2` | Outlook days on which it may ping |
| `[tornado]` | `enabled` | `yes` | Check the tornado outlooks (Days 1–2) |
| | `alert-probability` | `5` | Alert at this probability (%) |
| | `alert-intensity` | `off` | Alert at this CIG level on its own |
| | `ping-probability` | `10` | Ping at this probability (%) |
| | `ping-intensity` | `off` | Ping at this CIG level |
| | `in-summary` | `yes` | Name the tornado risk in the alert's summary line |
| `[hail]`, `[wind]` | `enabled` | `no` | Check the hail or damaging wind outlooks (Days 1–2) |
| | `alert-probability` | `15` | Alert at this probability (%) |
| | `alert-intensity` | `1` | Alert at this CIG level (hail 1–2, wind 1–3) |
| | `ping-probability`, `ping-intensity` | `off` | Ping levels |
| | `in-summary` | `no` | Name the risk in the alert's summary line |
| `[heartbeat]` | `url` | — | Requested after every outlook check |
| | `failure-url` | — | Requested when something went wrong |
| | `method` | `GET` | `POST` sends the run's output with the request |
| `[options]` | `instance-id` | — | Name added to every Slack message ("Sent by …") |
| | `verbose` | `no` | Debug logging |
| | `log-file` | `spc_county_alert.log` | The log file (relative paths: next to the settings file) |
| | `state-file` | one per county | What has been reported (one county only) |
| | `cache-dir` | the script's folder | Where county outlines are cached |

**Environment variables:**
- `SLACK_ALERTS_WEBHOOK_URL` (or the older `SLACK_WEBHOOK_URL`), `SLACK_LOGS_WEBHOOK_URL`, `SLACK_BOT_TOKEN` and `SLACK_ALERTS_CHANNEL_ID`
- `OUTLOOKER_HEARTBEAT_URL`, `OUTLOOKER_FAILURE_URL` and `OUTLOOKER_INSTANCE_ID`
- `SPC_COUNTY_FIPS`, or `SPC_COUNTY` with `SPC_STATE`

**Command-line options:** run `python3 Outlooker.py --help` for the full list.

## Customizing

Near the top of `Outlooker.py`, a block marked **THINGS YOU MAY WANT TO CHANGE** collects what people most often adjust:

- **Message wording:** the alert, update and test titles; the summary wording ("Risk of Severe Thunderstorms", "Tornado Risk" …); the link texts; and the disclaimer.
- **Intensity (CIG) definitions:** what each tornado, hail and wind intensity level means. SPC's wording for some hail and wind levels varies between sources, so check SPC's definitions.
- **Defaults:** the default county, time zone, and the alert and ping thresholds used when a setting is blank.

Everything that differs from one user to another belongs in the settings file instead. Every function in the script has a docstring explaining what it does.

## Testing and troubleshooting

**Test options**, none of which changes the state files:

| Command | What it does |
|---|---|
| `--test-webhook` | Sends a test message to each channel, and checks the duplicate check can read the alerts channel |
| `--test-alert` (add `--test-ping` to include the ping) | Sends a sample alert, clearly marked TEST |
| `--test-heartbeat` | Requests the heartbeat and failure URLs once each |
| `--test-county` | Shows the selected counties and their outlines |
| `--list-counties --state TN` | Lists a state's counties with their FIPS codes |
| `--dry-run` | Runs a real check but prints messages instead of sending them |
| `-v` | Adds debug detail to the log |

**Troubleshooting:**

- **Where to look first.** Every run writes to `spc_county_alert.log`. With a logs channel, each run's output is in Slack too.
- **"the alerts channel needs a Slack webhook URL".** Your settings file wasn't found or has no `webhook`. It must be named `outlooker.ini` and sit next to `Outlooker.py`, or be passed with `--config`.
- **Slack errors** such as `channel_not_found` or `invalid_token` are explained in the log. Usually the fix is a new webhook URL.
- **"outlook could not be checked".** SPC's site was unreachable or late. The run exits with code 1 and tries again next time.
- **Duplicate check errors.** `not_in_channel` means the app needs to be invited to the channel; `missing_scope` means the `channels:history` scope is missing (reinstall the app after adding it).
- **No local times on Windows:** run `pip install tzdata`.
- **GitHub Actions doesn't run on schedule:** see the note under [Setup on GitHub Actions](#setup-on-github-actions). In the Actions tab, a workflow can also be disabled and re-enabled to re-register its schedule.

**Exit codes:** 0 means success, 1 means something failed (details in the log), and 2 means a mistake in the options or the settings file.

## Disclaimer

This script was written primarily by AI. Please verify all information on the [SPC outlooks page](https://www.spc.noaa.gov/products/outlook/), and do not use it as a primary means of receiving severe weather alerts.
