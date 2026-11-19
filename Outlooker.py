#!/usr/bin/env python3
"""
Outlooker.py
============

Checks the NWS Storm Prediction Center (SPC) convective outlooks for one or more U.S.
counties (Montgomery County, Tennessee by default) and posts an alert to a Slack
channel, the alerts channel, through an Incoming Webhook. It can also post the full
output of every run to a second Slack channel, the logs channel, and check in with a
monitoring service after every run.

Settings file
-------------
The script reads its settings from outlooker.ini in the same folder as the script,
so a scheduled run needs no command-line options or environment variables. Copy
outlooker.example.ini to outlooker.ini and fill it in; the example explains each
setting. --config FILE reads a different file instead (for example, one per county).
* Command-line options win over environment variables, which win over the settings
  file. A blank setting uses the default.
* Relative paths in the settings file are relative to the folder the file is in.
* An unknown section or setting is reported as an error, so a typo is not ignored.
* The file holds the webhook URLs, which are secrets. Keep it private and out of
  version control (the repository's .gitignore excludes outlooker.ini).

Choosing the counties
---------------------
* Default: Montgomery County, TN (FIPS code 47125).
* By name:  --county "Davidson" --state TN   ("Davidson County" and "davidson" also
  work; the state may be an abbreviation, a full name or a 2-digit FIPS code).
* By code:  --fips 47037   (5-digit county FIPS code: 2 digits state + 3 digits county).
* Not sure of the spelling?  --list-counties --state TN
* The same choices can come from the [county] section of the settings file, or from
  environment variables SPC_COUNTY + SPC_STATE, or SPC_COUNTY_FIPS.
* Several counties: add a [location NAME] section to the settings file for each one,
  with the same settings as [county] (fips, or county and state). NAME is your own
  label, such as Home or Work, and appears in the messages. All of them are checked in
  the same run, each with its own alerts and state file.
* The counties are taken as a whole from the first of these that names one: the
  command line, then the environment variables, then the settings file.
* SPC outlooks cover the contiguous United States only.

Names are looked up with the U.S. Census Bureau. If a name matches more than one
county (for example "Richmond" in Virginia is both a county and an independent city),
the script stops and lists the matches with their FIPS codes.

What it checks
--------------
* Categorical outlooks for Days 1, 2 and 3.
* Tornado probability outlooks for Days 1 and 2. (SPC does not issue separate
  tornado, hail or wind outlooks for Day 3.)
* Optionally, hail and damaging wind probability outlooks for Days 1 and 2 ([hail]
  and [wind] in the settings file).
* Conditional Intensity Groups (CIGs) for Days 1 and 2 for each of those hazards:
  CIG1 to CIG3 for tornado and wind, CIG1 and CIG2 for hail. A CIG describes the
  strongest event reasonably expected IF that hazard occurs; it is about intensity,
  not likelihood. A message that reports a CIG includes the definitions of the levels.

A hazard counts for the county when ANY part of the county touches a shaded area.
The script tests the county's full boundary polygon against every outlook polygon,
not a single point, and reports the highest level that touches the county.

When it sends to the alerts channel
-----------------------------------
* Every shaded area that touches the county is written to the log file.
* An alert is sent to the alerts channel when a risk reaches its alert threshold and
  is new or has increased since the last alert for the same outlook period (see
  "State file"). The thresholds are:
    - categorical: Slight or higher
    - tornado: a probability of 5% or higher (a tornado CIG never alerts on its own)
    - hail and wind, when turned on: a probability or a CIG at or above the levels in
      [hail] and [wind] (by default 15%, or CIG1).
* When a tornado, hail or wind risk is reported, that day's probability and CIG for
  the same hazard are both included, even if only one of them reached its threshold.
  A new or higher CIG counts as an increase in risk and sends a message.
* The message pings the whole channel (<!channel>, Slack's syntax for @channel) when:
    - the categorical risk is Enhanced or higher on the Day 1 or Day 2 outlook,
    - the tornado probability is 10% or higher on any outlook, or
    - a hail or wind probability or CIG reaches the ping level set in [hail] or [wind]
      (by default, hail and wind never ping).
  A ping happens once per outlook period and hazard, and again only if that risk
  increases. If a risk that was already reported reaches the ping window without
  increasing (for example a Day 3 Enhanced Risk that becomes the Day 2 outlook),
  the script sends one message to deliver the ping.

Lowered or removed risks
------------------------
With report-lowered = yes in [alerts-channel], the alerts channel also gets a message
when a risk it was told about goes down: to a lower level, below the alert threshold,
or out of the outlook altogether. After that, a rise is measured from the lowered
level, so the channel hears about it again. Only outlooks that loaded on this run
count: an outlook file that fails to download never looks like a removed risk.
Without the setting, a decrease is mentioned only when another change sends a message.

Logs channel
------------
When the logs channel is turned on ([logs-channel] in the settings file), every
outlook check posts its full output - the same lines written to the console and the
log file - to the logs channel, whether or not there was anything to report. Alerts
still go to the alerts channel as described above.
* The output is posted as a code block under a one-line status: finished OK, with
  warnings, or with errors. It never pings anyone: Slack mentions such as <!channel>
  that appear in the output are shown as plain text.
* An unusually long output is shortened in the middle to fit in one Slack message;
  the full output is always in the log file.
* The run log is posted after the alert. If it cannot be posted, the error is logged
  and the exit code is 1; the alert has already been sent.
* --test-webhook sends a test message to both channels. The other test options do not
  post to the logs channel, and --dry-run prints the run log instead of posting it.

Heartbeat and failure signal
----------------------------
For a monitoring service such as healthchecks.io, Uptime Kuma or Cronitor ([heartbeat]
in the settings file):
* url is requested at the end of every outlook check, whatever the outcome. If the
  requests stop arriving, the service can warn you that the script is not running.
* failure-url is requested after the heartbeat when anything went wrong (for
  healthchecks.io, the same address with /fail added to the end).
* method is GET (the default) or POST, which sends the run's output as the body.
* Both addresses are treated as secrets and are masked in the log and the logs
  channel. A mistake in the settings file stops the script before it can send
  either; the missing heartbeat is how the monitoring service notices.

State file
----------
SPC reissues outlooks several times a day, and today's Day 2 outlook becomes
tomorrow's Day 1 outlook. The state file remembers, for each outlook period
(identified by the time the period ends, which SPC calls EXPIRE), the highest level
already reported and already pinged, and the level the alerts channel was last told
about. A reissued outlook with the same or a lower level is logged but not sent.
Entries older than a few days are removed automatically. Each county has its own state
file (spc_county_alert_state_<FIPS>.json).

Testing the setup
-----------------
    --test-webhook   send a short test message to the alerts channel, and to the logs
                     channel when it is turned on, to confirm the webhooks work
    --test-alert     send a sample alert (clearly marked TEST, made-up data) to the
                     alerts channel so you can see the formatting; add --test-ping to
                     include the @channel ping
    --test-heartbeat request the heartbeat URL and the failure URL once each (the
                     monitoring service may then show a failure until the next run)
    --test-county    show which counties were selected and their boundary details
    --dry-run        run the real check but print the messages instead of sending them
None of the test options read or change the state file. The offline unit tests are
in test_spc_county_alert.py (python -m unittest -v test_spc_county_alert).

Slack webhook handling
----------------------
Each webhook URL is checked before use (must be https, with no stray spaces, quotes or
invisible characters) and is never written to the log or posted to the logs channel;
logs show only https://hooks.slack.com/****. Slack's documented error codes (for
example channel_not_found or channel_is_archived) are translated into plain-language
advice. Rate limiting (HTTP 429), Slack server errors (5xx) and network failures are
retried up to 3 times; other errors are not.

Data sources
------------
* SPC outlook GeoJSON files: https://www.spc.noaa.gov/products/outlook/
    dayNotlk_cat.nolyr.geojson      categorical (LABEL = TSTM, MRGL, SLGT, ENH, MDT, HIGH)
    dayNotlk_torn.nolyr.geojson     tornado probability (DN = percent: 2, 5, 10, ...)
    dayNotlk_cigtorn.nolyr.geojson  tornado intensity group (LABEL = CIG1, CIG2, CIG3)
    dayNotlk_hail.nolyr.geojson     hail probability (DN = percent: 5, 15, 30, 45, 60)
    dayNotlk_cighail.nolyr.geojson  hail intensity group (LABEL = CIG1, CIG2)
    dayNotlk_wind.nolyr.geojson     damaging wind probability (DN = percent: 5, 15, ...)
    dayNotlk_cigwind.nolyr.geojson  wind intensity group (LABEL = CIG1, CIG2, CIG3)
  Each file is downloaded once per run and checked against every county.
* County boundaries: U.S. Census Bureau TIGERweb, Counties layer. Each county's
  outline is downloaded once and cached as county_<FIPS>.geojson (see --cache-dir).

Requirements and setup
----------------------
* Python 3.9+, standard library only. On Windows, also run: pip install tzdata
* Create a Slack app with Incoming Webhooks enabled and add a webhook for your alerts
  channel. To use the logs channel, add a second webhook for that channel.
* Copy outlooker.example.ini to outlooker.ini next to this script and paste in the
  webhook URL(s). The URLs are secrets: keep them out of shared files and version
  control.
* Check the webhooks:  python3 Outlooker.py --test-webhook
* Run it on a schedule, for example every 30 minutes with cron:
    */30 * * * * /usr/bin/python3 /path/to/Outlooker.py
* The webhook URLs can come from environment variables instead
  (SLACK_ALERTS_WEBHOOK_URL, SLACK_LOGS_WEBHOOK_URL) or from the command line
  (--alerts-channel-webhook, --logs-channel-webhook). The earlier names
  SLACK_WEBHOOK_URL and --webhook still work for the alerts channel.

Other options
-------------
    --config FILE       read the settings from FILE instead of outlooker.ini
    --heartbeat-url URL, --failure-url URL   the [heartbeat] addresses (environment
                        variables OUTLOOKER_HEARTBEAT_URL, OUTLOOKER_FAILURE_URL)
    --no-ping           never ping the channel (handy while testing in a real channel)
    --no-logs-channel   do not post to the logs channel on this run (--logs-channel
                        posts to it on this run even if the settings file turns it off)
    --spc-base          URL or local folder to read the SPC files from (for testing)
    -v                  verbose (debug) logging

Exit codes: 0 = success; 1 = something failed (details in the log); 2 = a mistake in
the command-line options or the settings file. A run in which one outlook file failed
still reports the outlooks that did load.

Disclaimer: this script was written with AI assistance. Verify all information on
the SPC website. It must not be used as a primary means of receiving severe weather
alerts.
"""
from __future__ import annotations

import argparse
import configparser
import difflib
import http.client
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_COUNTY_FIPS = "47125"  # Montgomery County, Tennessee
LEGACY_STATE_FILE = "spc_county_alert_state.json"  # name used before county selection
CONFIG_FILE = "outlooker.ini"  # settings file, read from the script's folder by default

# The sections and settings the settings file may contain (see outlooker.example.ini).
# Each [location NAME] section names one more county, with the settings of [county].
LOCATION_SECTION = "location"
HAZARD_SETTINGS = ("enabled", "alert-probability", "alert-intensity", "ping-probability",
                   "ping-intensity")
CONFIG_SETTINGS = {
    "county": ("fips", "county", "state"),
    "alerts-channel": ("webhook", "ping", "report-lowered"),
    "logs-channel": ("enabled", "webhook"),
    "hail": HAZARD_SETTINGS,
    "wind": HAZARD_SETTINGS,
    "heartbeat": ("url", "failure-url", "method"),
    "options": ("verbose", "log-file", "state-file", "cache-dir"),
}

# Census TIGERweb Counties layer. outSR=4326 returns longitude/latitude, which is
# what the SPC files use.
TIGERWEB_COUNTIES = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                     "State_County/MapServer/1/query")
COUNTY_FIELDS = "GEOID,NAME,BASENAME,STATE,COUNTY,INTPTLAT,INTPTLON"

SPC_BASE = "https://www.spc.noaa.gov/products/outlook/"
SPC_OUTLOOKS_PAGE = "https://www.spc.noaa.gov/products/outlook/"
USER_AGENT = "spc-county-alert/1.1 (county severe weather outlook monitor)"
LOCAL_TZ = "America/Chicago"  # %Z gives CST or CDT automatically

# State and DC FIPS codes. Alaska and Hawaii are accepted but SPC does not cover them.
STATES = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"),
    "DC": ("11", "District of Columbia"), "FL": ("12", "Florida"),
    "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"), "ID": ("16", "Idaho"),
    "IL": ("17", "Illinois"), "IN": ("18", "Indiana"), "IA": ("19", "Iowa"),
    "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"), "LA": ("22", "Louisiana"),
    "ME": ("23", "Maine"), "MD": ("24", "Maryland"), "MA": ("25", "Massachusetts"),
    "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"), "MS": ("28", "Mississippi"),
    "MO": ("29", "Missouri"), "MT": ("30", "Montana"), "NE": ("31", "Nebraska"),
    "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"), "NJ": ("34", "New Jersey"),
    "NM": ("35", "New Mexico"), "NY": ("36", "New York"), "NC": ("37", "North Carolina"),
    "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"), "OK": ("40", "Oklahoma"),
    "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"), "RI": ("44", "Rhode Island"),
    "SC": ("45", "South Carolina"), "SD": ("46", "South Dakota"),
    "TN": ("47", "Tennessee"), "TX": ("48", "Texas"), "UT": ("49", "Utah"),
    "VT": ("50", "Vermont"), "VA": ("51", "Virginia"), "WA": ("53", "Washington"),
    "WV": ("54", "West Virginia"), "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"),
}
STATE_BY_FIPS = {fips: abbr for abbr, (fips, _name) in STATES.items()}
OUTSIDE_SPC_COVERAGE = {"02", "15"}  # Alaska, Hawaii

# Categorical risk levels, lowest to highest. TSTM (general thunderstorms) is shaded
# on the map but is not a severe risk level.
CATEGORIES = ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
RANK = {label: i for i, label in enumerate(CATEGORIES)}
CATEGORY_NAMES = {
    "TSTM": "General Thunderstorms",
    "MRGL": "Marginal Risk (level 1 of 5)",
    "SLGT": "Slight Risk (level 2 of 5)",
    "ENH": "Enhanced Risk (level 3 of 5)",
    "MDT": "Moderate Risk (level 4 of 5)",
    "HIGH": "High Risk (level 5 of 5)",
}
CATEGORY_SHORT = {
    "TSTM": "General Thunderstorms", "MRGL": "Marginal", "SLGT": "Slight",
    "ENH": "Enhanced", "MDT": "Moderate", "HIGH": "High",
}
# Probability contours SPC uses on the Day 1 and Day 2 outlooks (percent).
TORNADO_LEVELS = (2, 5, 10, 15, 30, 45, 60)
HAIL_WIND_LEVELS = (5, 15, 30, 45, 60)

# Alert and ping thresholds for the categorical and tornado outlooks.
ALERT_MIN_CATEGORY = "SLGT"
ALERT_MIN_TORNADO = 5
PING_MIN_CATEGORY = "ENH"
PING_CATEGORY_DAYS = (1, 2)
PING_MIN_TORNADO = 10  # on any outlook that has a tornado product (Days 1 and 2)

# Conditional Intensity Groups (CIGs). Each names the strongest event SPC considers a
# reasonable expectation if that hazard occurs. Edit the wording here if SPC changes it.
CIG_LEVELS = (1, 2, 3)  # tornado
CIG_DEFINITIONS = {
    1: "EF2 or stronger tornadoes possible",
    2: "EF3 or stronger tornadoes possible",
    3: "EF4 or stronger tornadoes possible (rare, high-end events)",
}
# Hail and wind CIG1 took over SPC's earlier "significant severe" thresholds (2-inch
# hail, 65-knot gusts). The higher levels are worded from published summaries of the
# 2026 change, which differ on the exact sizes and speeds: check SPC's definitions.
HAIL_CIG_DEFINITIONS = {
    1: "hail 2 inches or larger possible",
    2: "hail about 3.5 inches or larger possible (rare, high-end events)",
}
WIND_CIG_DEFINITIONS = {
    1: "gusts of 75 mph or stronger possible",
    2: "intense, organized damaging winds possible, such as a derecho",
    3: "extreme, widespread damaging winds possible (rare, high-end derechos)",
}

CATEGORICAL = "categorical"
TORNADO = "tornado"
CIG = "tornado_cig"
HAIL = "hail"
HAIL_CIG = "hail_cig"
WIND = "wind"
WIND_CIG = "wind_cig"
KIND_LABELS = {
    CATEGORICAL: "categorical",
    TORNADO: "tornado",
    CIG: "tornado intensity (CIG)",
    HAIL: "hail",
    HAIL_CIG: "hail intensity (CIG)",
    WIND: "wind",
    WIND_CIG: "wind intensity (CIG)",
}
KIND_ORDER = {kind: i for i, kind in enumerate(KIND_LABELS)}  # the order above
# Tornado, hail and wind each have a probability outlook and an intensity outlook.
# HAZARD_OF maps every kind to its hazard; categorical is a hazard of its own.
INTENSITY_OF = {TORNADO: CIG, HAIL: HAIL_CIG, WIND: WIND_CIG}
HAZARD_OF = {CATEGORICAL: CATEGORICAL, **{p: p for p in INTENSITY_OF},
             **{i: p for p, i in INTENSITY_OF.items()}}
PROBABILITY_LEVELS = {TORNADO: TORNADO_LEVELS, HAIL: HAIL_WIND_LEVELS, WIND: HAIL_WIND_LEVELS}
INTENSITY_DEFINITIONS = {CIG: CIG_DEFINITIONS, HAIL_CIG: HAIL_CIG_DEFINITIONS,
                         WIND_CIG: WIND_CIG_DEFINITIONS}
# How each kind is introduced in a message.
HAZARD_NAMES = {
    CATEGORICAL: "Severe thunderstorms",
    TORNADO: "Tornado",
    CIG: "Tornado intensity",
    HAIL: "Hail (1 inch or larger)",
    HAIL_CIG: "Hail intensity",
    WIND: "Damaging wind (58 mph or stronger)",
    WIND_CIG: "Wind intensity",
}
# The heading of the definitions block, per intensity kind.
INTENSITY_HEADINGS = {
    CIG: "*Tornado intensity groups (CIG)* - the strongest tornado reasonably expected "
         "if tornadoes occur.",
    HAIL_CIG: "*Hail intensity groups (CIG)* - the largest hail reasonably expected if "
              "severe hail occurs.",
    WIND_CIG: "*Wind intensity groups (CIG)* - the strongest winds reasonably expected "
              "if severe winds occur.",
}


@dataclass(frozen=True)
class HazardRules:
    """When a tornado, hail or wind risk is reported and when it pings the channel.

    Each value is a probability (percent) or a CIG level; None means never.
    """
    alert_probability: int | None
    alert_intensity: int | None
    ping_probability: int | None
    ping_intensity: int | None


TORNADO_RULES = HazardRules(ALERT_MIN_TORNADO, None, PING_MIN_TORNADO, None)
HAIL_WIND_DEFAULTS = HazardRules(15, 1, None, None)  # when [hail] or [wind] is turned on
DEFAULT_RULES = {TORNADO: TORNADO_RULES}  # hail and wind are checked only when turned on

STATE_RETENTION = timedelta(days=4)
CHANNEL_PING = "<!channel>"

# Slack webhook settings.
SLACK_HOST = "hooks.slack.com"
SLACK_ATTEMPTS = 3        # tries for rate limits, Slack server errors and network errors
SLACK_TIMEOUT = 15        # seconds per try
SLACK_MAX_RETRY_WAIT = 60  # cap on a Retry-After wait, seconds

# The two Slack channels. Alerts go to the alerts channel; the full output of every
# outlook check goes to the logs channel when it is turned on.
ALERTS_CHANNEL = "alerts channel"
LOGS_CHANNEL = "logs channel"
# Longer run logs are shortened in the middle. Slack truncates messages at 40,000
# characters and recommends far fewer, so this stays well below that.
LOGS_MAX_CHARS = 12_000

# Heartbeat and failure signal requests to a monitoring service.
HEARTBEAT_ATTEMPTS = 3
HEARTBEAT_TIMEOUT = 10  # seconds per try

_NEW_WEBHOOK = ("Create a new webhook in your Slack app and update the webhook URL in "
                "your settings.")
# Plain-language explanations of Slack's documented Incoming Webhook error codes.
SLACK_ERROR_HELP = {
    "invalid_payload": "Slack could not read the message body.",
    "no_text": "the message was empty.",
    "too_many_attachments": "the message had too many attachments.",
    "user_not_found": "the user the webhook posts to no longer exists. " + _NEW_WEBHOOK,
    "channel_not_found": "the webhook's channel no longer exists. " + _NEW_WEBHOOK,
    "channel_is_archived": "the webhook's channel is archived. Unarchive it or "
                           "create a webhook for another channel.",
    "action_prohibited": "a workspace setting blocks this webhook from posting there. "
                         "Ask a Slack admin.",
    "posting_to_general_channel_denied": "only admins may post in that channel. "
                                         "Use a webhook for a different channel.",
    "invalid_token": "the webhook URL is not valid or has been revoked. " + _NEW_WEBHOOK,
    "no_service": "the webhook was removed or disabled. " + _NEW_WEBHOOK,
    "no_service_id": "the webhook URL is incomplete. Copy the full URL from Slack.",
    "no_active_hooks": "the webhook is disabled. " + _NEW_WEBHOOK,
    "no_team": "the Slack workspace for this webhook was not found. " + _NEW_WEBHOOK,
    "team_disabled": "the Slack workspace for this webhook is disabled.",
    "rollup_error": "Slack had an internal error. It should work on a later try.",
}

DISCLAIMER = (
    "_Disclaimer: This alert comes from a script written by Claude Opus 5.5. "
    "Verify all information on the SPC outlooks page. Do not use this as a primary "
    "means of receiving severe weather alerts._"
)

log = logging.getLogger("spc_county_alert")


@dataclass(frozen=True)
class Product:
    """One SPC outlook file to check."""
    day: int
    kind: str  # one of KIND_LABELS
    filename: str

    @property
    def label(self) -> str:
        return f"Day {self.day} {KIND_LABELS[self.kind]}"


PRODUCTS = (
    Product(1, CATEGORICAL, "day1otlk_cat.nolyr.geojson"),
    Product(2, CATEGORICAL, "day2otlk_cat.nolyr.geojson"),
    Product(3, CATEGORICAL, "day3otlk_cat.nolyr.geojson"),
    Product(1, TORNADO, "day1otlk_torn.nolyr.geojson"),
    Product(2, TORNADO, "day2otlk_torn.nolyr.geojson"),
    Product(1, CIG, "day1otlk_cigtorn.nolyr.geojson"),
    Product(2, CIG, "day2otlk_cigtorn.nolyr.geojson"),
    # Downloaded only when [hail] or [wind] is turned on.
    Product(1, HAIL, "day1otlk_hail.nolyr.geojson"),
    Product(2, HAIL, "day2otlk_hail.nolyr.geojson"),
    Product(1, HAIL_CIG, "day1otlk_cighail.nolyr.geojson"),
    Product(2, HAIL_CIG, "day2otlk_cighail.nolyr.geojson"),
    Product(1, WIND, "day1otlk_wind.nolyr.geojson"),
    Product(2, WIND, "day2otlk_wind.nolyr.geojson"),
    Product(1, WIND_CIG, "day1otlk_cigwind.nolyr.geojson"),
    Product(2, WIND_CIG, "day2otlk_cigwind.nolyr.geojson"),
)


class DataError(Exception):
    """Raised when downloaded or cached data is missing or not what we expect."""


class SlackError(RuntimeError):
    """Raised when a message could not be delivered to Slack. Never contains the URL."""


class RequestError(RuntimeError):
    """Raised when a heartbeat or failure signal could not be sent. Never contains the URL."""


class ConfigError(Exception):
    """Raised when the settings file cannot be read or contains a mistake."""


# --------------------------------------------------------------------------- #
# Geometry (pure Python). GeoJSON coordinates are [longitude, latitude].
# A polygon is a list of rings: the first ring is the outline, any others are holes.
# --------------------------------------------------------------------------- #
def iter_polygons(geometry: dict | None) -> list[list]:
    """Return every polygon (as a list of rings) in a GeoJSON geometry."""
    if not geometry:
        return []
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return list(geometry["coordinates"])
    if kind == "GeometryCollection":
        return [p for g in geometry.get("geometries", []) for p in iter_polygons(g)]
    return []


def _bbox(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_overlap(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _edges(ring):
    """Consecutive vertex pairs, closing the ring if the file did not."""
    pts = [(p[0], p[1]) for p in ring]
    if len(pts) > 1 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return list(zip(pts, pts[1:]))


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c) -> bool:
    """True if c (already known to be collinear with a-b) lies on segment a-b."""
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def segments_intersect(p1, p2, q1, q2) -> bool:
    """True if segment p1-p2 and segment q1-q2 cross or touch."""
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return True
    return (
        (d1 == 0 and _on_segment(q1, q2, p1)) or (d2 == 0 and _on_segment(q1, q2, p2))
        or (d3 == 0 and _on_segment(p1, p2, q1)) or (d4 == 0 and _on_segment(p1, p2, q2))
    )


def _point_in_ring(x: float, y: float, ring) -> bool:
    """Ray-casting point-in-ring test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def point_in_polygon(x: float, y: float, rings) -> bool:
    """Inside the outline and not inside any hole."""
    if not rings or not _point_in_ring(x, y, rings[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in rings[1:])


def _rings_touch(ring_a, ring_b) -> bool:
    """True if any edge of ring_a crosses or touches any edge of ring_b.

    Edges far from the other ring are discarded with bounding boxes first, so a
    small county against a continent-sized outlook polygon stays fast.
    """
    if len(ring_a) < 2 or len(ring_b) < 2:
        return False
    box_b = _bbox(ring_b)
    near_a = [e for e in _edges(ring_a) if _bbox_overlap(_bbox(e), box_b)]
    if not near_a:
        return False
    box_near_a = _bbox([p for e in near_a for p in e])
    near_b = [e for e in _edges(ring_b) if _bbox_overlap(_bbox(e), box_near_a)]
    for p1, p2 in near_a:
        box_e = _bbox((p1, p2))
        for q1, q2 in near_b:
            if _bbox_overlap(box_e, _bbox((q1, q2))) and segments_intersect(p1, p2, q1, q2):
                return True
    return False


def polygons_intersect(a, b) -> bool:
    """True if two polygons (lists of rings, holes allowed) share any area or edge.

    Two polygons meet if (1) any of their edges cross or touch, or otherwise
    (2) one lies entirely inside the other, which a single vertex test detects.
    A county that sits wholly inside a hole of an outlook polygon correctly
    returns False.
    """
    if not a or not b or not a[0] or not b[0]:
        return False
    if not _bbox_overlap(_bbox(a[0]), _bbox(b[0])):
        return False
    if any(_rings_touch(ra, rb) for ra in a for rb in b):
        return True
    ax, ay = a[0][0][0], a[0][0][1]
    bx, by = b[0][0][0], b[0][0][1]
    return point_in_polygon(ax, ay, b) or point_in_polygon(bx, by, a)


def geometries_intersect(g1: dict | None, g2: dict | None) -> bool:
    """True if any polygon of g1 meets any polygon of g2."""
    return any(polygons_intersect(a, b) for a in iter_polygons(g1) for b in iter_polygons(g2))


# --------------------------------------------------------------------------- #
# Reading the SPC outlooks
# --------------------------------------------------------------------------- #
def parse_spc_time(value) -> datetime | None:
    """SPC times look like '202611161630' (UTC)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("Unrecognized SPC timestamp: %r", value)
        return None


def probability_pct(props: dict, levels=TORNADO_LEVELS) -> int | None:
    """Probability (percent) of a feature, or None if it is not one of `levels`.

    DN normally holds the percent. LABEL is used as a fallback ("0.05", "5" or "5%").
    Hatched intensity areas (labels such as "CIG1" or "SIGN") return None.
    """
    dn = props.get("DN")
    if isinstance(dn, (int, float)) and not isinstance(dn, bool) and dn in levels:
        return int(dn)
    text = str(props.get("LABEL", "")).strip().rstrip("%")
    try:
        value = float(text)
    except ValueError:
        return None
    pct = round(value * 100) if value < 1 else round(value)
    return pct if pct in levels else None


def tornado_pct(props: dict) -> int | None:
    """Tornado probability (percent) of a feature, or None if it is not a probability area."""
    return probability_pct(props, TORNADO_LEVELS)


_CIG_RE = re.compile(r"CIG\s*([1-3])\b", re.IGNORECASE)


def cig_level(props: dict) -> int | None:
    """CIG level (1-3) named in a feature's LABEL or LABEL2, else None.

    Only the label text is trusted. DN is deliberately not used, because reporting
    the wrong intensity group is worse than reporting none.
    """
    for key in ("LABEL", "LABEL2"):
        match = _CIG_RE.search(str(props.get(key, "")))
        if match:
            return int(match.group(1))
    return None


def level_of(kind: str, props: dict):
    """The risk level a feature represents (category label, percent or CIG), or None."""
    if kind == CATEGORICAL:
        label = props.get("LABEL")
        return label if label in RANK else None
    if kind in INTENSITY_DEFINITIONS:
        level = cig_level(props)
        return level if level in INTENSITY_DEFINITIONS[kind] else None
    return probability_pct(props, PROBABILITY_LEVELS[kind])


def rank_of(kind: str, level) -> int | None:
    """A comparable number for a level, or None for a missing or invalid value."""
    if kind == CATEGORICAL:
        return RANK.get(level) if isinstance(level, str) else None
    valid = INTENSITY_DEFINITIONS.get(kind) or PROBABILITY_LEVELS[kind]
    if isinstance(level, int) and not isinstance(level, bool) and level in valid:
        return level
    return None


@dataclass(frozen=True)
class Evaluation:
    """Result of checking one outlook file against the county."""
    level: object  # highest level touching the county, or None
    issue: datetime | None
    valid: datetime | None
    expire: datetime | None


def outlook_times(geojson: dict) -> tuple[datetime | None, datetime | None, datetime | None]:
    """(issue, valid, expire) of an outlook file, from any of its features (they all
    carry the same times). Raises DataError if it is not a GeoJSON FeatureCollection."""
    if not isinstance(geojson, dict) or not isinstance(geojson.get("features", []), list):
        raise DataError("not a GeoJSON FeatureCollection")
    issue = valid = expire = None
    for feature in geojson.get("features", []):
        props = (feature or {}).get("properties") or {}
        issue = issue or parse_spc_time(props.get("ISSUE"))
        valid = valid or parse_spc_time(props.get("VALID"))
        expire = expire or parse_spc_time(props.get("EXPIRE"))
        if issue and valid and expire:
            break
    return issue, valid, expire


def evaluate_product(geojson: dict, kind: str, county_geometry: dict) -> Evaluation:
    """Find the highest level in an outlook file whose shaded area touches the county."""
    issue, valid, expire = outlook_times(geojson)
    best = None
    for feature in geojson.get("features", []):
        props = (feature or {}).get("properties") or {}
        level = level_of(kind, props)
        if level is None:
            log.debug("Ignoring feature with LABEL=%r DN=%r", props.get("LABEL"), props.get("DN"))
            continue
        if geometries_intersect(county_geometry, feature.get("geometry")):
            if best is None or rank_of(kind, level) > rank_of(kind, best):
                best = level
    return Evaluation(best, issue, valid, expire)


@dataclass(frozen=True)
class Hazard:
    """A shaded area touching the county on one outlook."""
    day: int
    kind: str
    level: object
    issue: datetime | None
    valid: datetime | None
    expire: datetime

    @property
    def period_key(self) -> str:
        """Identifies the outlook period. Day 2 and the next day's Day 1 share it."""
        return period_key_of(self.expire)

    @property
    def rank(self) -> int:
        return rank_of(self.kind, self.level)

    def _limit(self, rules: dict, alert: bool) -> int | None:
        """This kind's alert or ping threshold under `rules` (see HazardRules)."""
        r = rules.get(HAZARD_OF[self.kind])
        if r is None:
            return None
        if self.kind in INTENSITY_DEFINITIONS:
            return r.alert_intensity if alert else r.ping_intensity
        return r.alert_probability if alert else r.ping_probability

    def is_alertable(self, rules: dict) -> bool:
        """Meets its alert threshold on its own. `rules` maps tornado, hail and wind to
        their HazardRules; a hazard missing from it is not being checked."""
        if self.kind == CATEGORICAL:
            return self.rank >= RANK[ALERT_MIN_CATEGORY]
        limit = self._limit(rules, alert=True)
        return limit is not None and self.level >= limit

    def is_ping_eligible(self, rules: dict) -> bool:
        if self.kind == CATEGORICAL:
            return self.day in PING_CATEGORY_DAYS and self.rank >= RANK[PING_MIN_CATEGORY]
        limit = self._limit(rules, alert=False)
        return limit is not None and self.level >= limit

    @property
    def alertable(self) -> bool:
        """Under the default rules. A tornado CIG never alerts on its own."""
        return self.is_alertable(DEFAULT_RULES)

    @property
    def ping_eligible(self) -> bool:
        return self.is_ping_eligible(DEFAULT_RULES)

    def describe(self) -> str:
        name = HAZARD_NAMES[self.kind]
        if self.kind == CATEGORICAL:
            return f"{name}: *{CATEGORY_NAMES[self.level]}*"
        if self.kind in INTENSITY_DEFINITIONS:
            return f"{name}: *CIG{self.level}* ({INTENSITY_DEFINITIONS[self.kind][self.level]})"
        return f"{name}: *{self.level}%* probability within 25 miles of a point"

    def describe_removed(self) -> str:
        return f"{HAZARD_NAMES[self.kind]}: no longer in the outlook for the county"

    def short(self) -> str:
        return f"Day {self.day} {KIND_LABELS[self.kind]} {short_level(self.kind, self.level)}"


def short_level(kind: str, level) -> str:
    if kind == CATEGORICAL:
        return CATEGORY_SHORT.get(level, str(level))
    if kind in INTENSITY_DEFINITIONS:
        return f"CIG{level}"
    return f"{level}%"


# --------------------------------------------------------------------------- #
# Input / output helpers
# --------------------------------------------------------------------------- #
def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def product_source(base: str, filename: str) -> str:
    """Build the URL or local path of an outlook file."""
    if is_url(base):
        return base.rstrip("/") + "/" + filename
    return str(Path(base) / filename)


def fetch_json(source: str, attempts: int = 3) -> dict:
    """Load JSON from a URL (retrying with backoff) or from a local file."""
    if not is_url(source):
        return json.loads(Path(source).read_text(encoding="utf-8"))
    request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == attempts:
                raise
            delay = 2 ** attempt
            log.warning("Download of %s failed (%s); retrying in %ss", source, exc, delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Choosing and loading the county
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CountySpec:
    """What the user asked for: a FIPS code, or a county name plus a state."""
    geoid: str | None = None
    name: str | None = None
    state: str | None = None  # 2-letter abbreviation
    nickname: str | None = None  # from a [location NAME] section, e.g. "Home"

    @property
    def label(self) -> str:
        base = f"FIPS {self.geoid}" if self.geoid else f"{self.name}, {self.state}"
        return f"{base} ({self.nickname})" if self.nickname else base


@dataclass(frozen=True)
class County:
    """A resolved county with its boundary."""
    geoid: str
    name: str  # e.g. "Montgomery County"
    state: str  # e.g. "TN"
    geometry: dict
    nickname: str | None = None  # e.g. "Home"

    @property
    def label(self) -> str:
        base = f"{self.name}, {self.state}"
        return f"{base} ({self.nickname})" if self.nickname else base


def parse_state(value: str) -> str:
    """Accept 'TN', 'tn', 'Tennessee' or '47'; return the 2-letter abbreviation."""
    text = str(value).strip()
    if text.upper() in STATES:
        return text.upper()
    if text.zfill(2) in STATE_BY_FIPS and text.isdigit():
        return STATE_BY_FIPS[text.zfill(2)]
    for abbr, (_fips, name) in STATES.items():
        if name.lower() == text.lower():
            return abbr
    raise ValueError(f"unknown state {value!r}; use a 2-letter abbreviation such as TN")


def _norm_name(text) -> str:
    """Upper-case, drop periods, squeeze spaces: 'St. Louis  county' -> 'ST LOUIS COUNTY'."""
    return " ".join(str(text or "").replace(".", "").upper().split())


def _strip_suffix(name: str) -> str:
    for suffix in (" COUNTY", " PARISH"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def name_matches(props: dict, query: str) -> bool:
    """True if a Census county record matches what the user typed.

    'Davidson', 'Davidson County' and 'davidson county' all match Davidson County.
    'Richmond city' matches only the independent city; 'Richmond' matches both the
    county and the city (the caller then reports the ambiguity).
    """
    q = _norm_name(query)
    full = _norm_name(props.get("NAME"))
    base = _norm_name(props.get("BASENAME")) or _strip_suffix(full)
    return q == full or _strip_suffix(q) == base


def match_counties(rows: list[dict], query: str) -> list[dict]:
    """Records matching a typed name. An exact full-name match wins, so 'Richmond
    County' picks the county even though the city of Richmond shares its base name."""
    exact = [p for p in rows if _norm_name(p.get("NAME")) == _norm_name(query)]
    return exact or [p for p in rows if name_matches(p, query)]


def tiger_url(where: str, geometry: bool) -> str:
    """TIGERweb Counties query URL returning GeoJSON in longitude/latitude."""
    return TIGERWEB_COUNTIES + "?" + urllib.parse.urlencode({
        "where": where,
        "outFields": COUNTY_FIELDS,
        "returnGeometry": "true" if geometry else "false",
        "outSR": "4326",
        "f": "geojson",
    })


def _census_query(where: str, geometry: bool) -> list[dict]:
    """Run a TIGERweb query and return its features."""
    try:
        data = fetch_json(tiger_url(where, geometry))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise DataError(f"could not reach the Census Bureau county service: {exc}") from exc
    if isinstance(data, dict) and "error" in data:
        message = (data.get("error") or {}).get("message", "unknown error")
        raise DataError(f"the Census Bureau county service returned an error: {message}")
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise DataError("unexpected reply from the Census Bureau county service")
    return features


def list_state_counties(state: str) -> list[dict]:
    """Census records (properties only) for every county in a state, sorted by name."""
    fips = STATES[state][0]
    rows = [f.get("properties") or {} for f in _census_query(f"STATE='{fips}'", geometry=False)]
    return sorted(rows, key=lambda p: str(p.get("NAME", "")))


def lookup_geoid(name: str, state: str) -> str:
    """Find the FIPS code for a county name in a state, or raise DataError."""
    rows = list_state_counties(state)
    matches = match_counties(rows, name)
    if len(matches) == 1:
        return str(matches[0]["GEOID"])
    if len(matches) > 1:
        options = "; ".join(f"{p.get('NAME')} (--fips {p.get('GEOID')})" for p in matches)
        raise DataError(f"'{name}' matches more than one place in {state}: {options}. "
                        "Use the full name or --fips.")
    names = [str(p.get("NAME", "")) for p in rows]
    close = difflib.get_close_matches(str(name), names, n=3, cutoff=0.6)
    if not close:  # also try without "County"
        close = difflib.get_close_matches(f"{name} County", names, n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise DataError(f"no county named '{name}' in {state}.{hint} "
                    f"See the list with: --list-counties --state {state}")


def validate_county(data: dict, geoid: str) -> County:
    """Check a county GeoJSON FeatureCollection and return the County, or raise DataError."""
    features = data.get("features") if isinstance(data, dict) else None
    if not features:
        raise DataError("county data has no features")

    with_geoid = [f for f in features if "GEOID" in ((f or {}).get("properties") or {})]
    if with_geoid:
        matches = [f for f in with_geoid if str(f["properties"]["GEOID"]) == geoid]
        if not matches:
            raise DataError(f"county data has no feature with FIPS {geoid}")
        feature = matches[0]
    elif len(features) == 1:
        log.warning("County data has no GEOID property; using its single feature")
        feature = features[0]
    else:
        raise DataError("county data has several features and no GEOID to choose one")

    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    polygons = iter_polygons(geometry)
    if not polygons:
        raise DataError(f"county {geoid} has no polygon geometry")

    points = [p for poly in polygons for ring in poly for p in ring]
    min_lon, min_lat, max_lon, max_lat = _bbox(points)
    if not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise DataError("county coordinates are not longitude/latitude")
    if max_lon - min_lon > 30 or max_lat - min_lat > 30:
        raise DataError("county outline is implausibly large")
    # The Census "internal point" must fall inside the outline's bounding box.
    try:
        ilat, ilon = float(props["INTPTLAT"]), float(props["INTPTLON"])
    except (KeyError, TypeError, ValueError):
        log.debug("County data has no internal point; skipping that check")
    else:
        if not (min_lon <= ilon <= max_lon and min_lat <= ilat <= max_lat):
            raise DataError(f"county {geoid} outline does not contain its own internal point")

    state = STATE_BY_FIPS.get(geoid[:2], geoid[:2])
    name = str(props.get("NAME") or f"County {geoid}")
    log.debug("County %s (%s): %d polygon(s), %d vertices, lat %.3f..%.3f, lon %.3f..%.3f",
              name, geoid, len(polygons), len(points), min_lat, max_lat, min_lon, max_lon)
    return County(geoid, name, state, geometry)


def county_cache_path(cache_dir: Path, geoid: str) -> Path:
    return Path(cache_dir) / f"county_{geoid}.geojson"


def _load_cached(path: Path, geoid: str) -> County | None:
    """A cached county, or None if the file is missing or unusable."""
    if not path.exists():
        return None
    try:
        return validate_county(json.loads(path.read_text(encoding="utf-8")), geoid)
    except (ValueError, DataError) as exc:
        log.warning("Cached county outline %s is unusable (%s); downloading again", path, exc)
        return None


def _find_cached_by_name(cache_dir: Path, name: str, state: str) -> County | None:
    """A previously downloaded county matching name + state, so no lookup is needed."""
    fips = STATES[state][0]
    rows = []
    for path in sorted(Path(cache_dir).glob(f"county_{fips}???.geojson")):
        try:
            features = json.loads(path.read_text(encoding="utf-8")).get("features") or []
        except (ValueError, AttributeError, OSError):
            continue
        for feature in features:
            props = dict((feature or {}).get("properties") or {})
            props["_path"] = path
            rows.append(props)
    found = match_counties(rows, name)
    if len(found) != 1:
        return None  # none, or ambiguous: let the online lookup decide
    path = found[0]["_path"]
    return _load_cached(path, path.stem.split("_", 1)[1])


def resolve_county(spec: CountySpec, cache_dir: Path, refresh: bool = False) -> County:
    """Turn a CountySpec into a County with its boundary, using the cache when possible."""
    cache_dir = Path(cache_dir)
    if spec.geoid:
        geoid = spec.geoid
        cached = None if refresh else _load_cached(county_cache_path(cache_dir, geoid), geoid)
    else:
        cached = None if refresh else _find_cached_by_name(cache_dir, spec.name, spec.state)
        if cached:
            log.debug("Using cached outline for %s", cached.label)
            return cached
        log.info("Looking up %s with the Census Bureau", spec.label)
        geoid = lookup_geoid(spec.name, spec.state)
    if cached:
        return cached

    log.info("Downloading the outline for county FIPS %s from the Census Bureau", geoid)
    features = _census_query(f"GEOID='{geoid}'", geometry=True)
    if not features:
        raise DataError(f"the Census Bureau has no county with FIPS code {geoid}")
    data = {"type": "FeatureCollection", "features": features}
    county = validate_county(data, geoid)
    path = county_cache_path(cache_dir, geoid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    log.info("Saved the %s outline to %s", county.label, path)
    return county


def resolve_location(spec: CountySpec, cache_dir: Path, refresh: bool = False) -> County:
    """resolve_county, keeping the location's name (such as Home) for the messages."""
    county = resolve_county(spec, cache_dir, refresh)
    return replace(county, nickname=spec.nickname) if spec.nickname else county


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #
def mask_webhook(url: str | None) -> str:
    """Show only the scheme and host of a webhook URL; the rest is secret."""
    parsed = urllib.parse.urlparse(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return "(invalid URL)"
    return f"{parsed.scheme}://{parsed.netloc}/****"


def check_webhook(url: str | None) -> str | None:
    """Return a problem description if the webhook URL is unusable, else None.

    Plain http is accepted only for localhost (used by the unit tests). A host other
    than hooks.slack.com is allowed but logged as a warning.
    """
    if not url:
        return (f"No webhook URL is set. Add it to {CONFIG_FILE} (see outlooker.example.ini) "
                "or pass it on the command line.")
    # Invisible characters usually come from copying and pasting. Control characters
    # would also make Python's error message quote the secret URL.
    if any(c in " \"'<>" or not (c.isascii() and c.isprintable()) for c in url):
        return ("The Slack webhook URL contains spaces, quote marks or invisible characters; "
                "copy it again from Slack.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "The Slack webhook URL is not a valid web address."
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        return "The Slack webhook URL must start with https://"
    if parsed.scheme == "https" and parsed.hostname != SLACK_HOST:
        log.warning("The webhook host %s is not %s; sending anyway", parsed.hostname, SLACK_HOST)
    if parsed.hostname == SLACK_HOST and not parsed.path.strip("/"):
        return "The Slack webhook URL is incomplete; copy the full URL from Slack."
    return None


def explain_slack_error(status: int | None, body: str) -> str:
    """Turn a Slack reply into a readable sentence."""
    code = body.strip()
    help_text = SLACK_ERROR_HELP.get(code)
    prefix = f"Slack replied HTTP {status}" if status else "Slack replied"
    if help_text:
        return f"{prefix} '{code}': {help_text}"
    shown = code[:200] if code else "(empty reply)"
    return f"{prefix}: {shown}"


def _retry_wait(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), SLACK_MAX_RETRY_WAIT))
        except ValueError:
            pass
    return float(2 ** attempt)


def post_to_slack(webhook_url: str, text: str, *, timeout: float = SLACK_TIMEOUT,
                  attempts: int = SLACK_ATTEMPTS) -> None:
    """Send a message through a Slack Incoming Webhook.

    Success is HTTP 200 with the body 'ok'. Rate limiting (429), Slack server errors
    (5xx) and network errors are retried; anything else fails at once. Raises
    SlackError with a readable explanation. The URL never appears in the error.
    """
    body = json.dumps({"text": text}).encode("utf-8")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            webhook_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        retry_after = None
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = response.read().decode("utf-8", "replace")
                if response.status == 200 and reply.strip() == "ok":
                    return
                raise SlackError(explain_slack_error(response.status, reply))
        except urllib.error.HTTPError as exc:
            reply = exc.read().decode("utf-8", "replace") if exc.fp else ""
            problem = explain_slack_error(exc.code, reply)
            retryable = exc.code == 429 or exc.code >= 500
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            problem = f"could not reach Slack: {reason}"
            retryable = True

        if not retryable or attempt == attempts:
            raise SlackError(problem)
        wait = _retry_wait(attempt, retry_after)
        log.warning("Slack attempt %d of %d failed (%s); retrying in %.0fs",
                    attempt, attempts, problem, wait)
        time.sleep(wait)
    raise SlackError("unreachable")


def deliver(args: argparse.Namespace, text: str, what: str,
            channel: str = ALERTS_CHANNEL) -> int:
    """Print (dry run) or send a message to the alerts or logs channel.

    Returns an exit code.
    """
    if args.dry_run:
        print(text)
        log.info("Dry run: the %s for the %s was printed, not sent", what, channel)
        return 0
    webhook = args.logs_webhook if channel == LOGS_CHANNEL else args.alerts_webhook
    problem = check_webhook(webhook)
    if problem:
        log.error("Cannot send the %s to the %s: %s", what, channel, problem)
        return 1
    log.info("Sending the %s to the %s (%s)", what, channel, mask_webhook(webhook))
    try:
        post_to_slack(webhook, text)
    except SlackError as exc:
        log.error("Sending the %s to the %s failed: %s", what, channel, exc)
        return 1
    log.info("The %s was sent to the %s", what, channel)
    return 0


# --------------------------------------------------------------------------- #
# Heartbeat and failure signal, for a monitoring service
# --------------------------------------------------------------------------- #
def check_url(url: str | None) -> str | None:
    """Return a problem description if a heartbeat or failure URL is unusable, else None."""
    if not url:
        return "No URL is set."
    if any(c in " \"'<>" or not (c.isascii() and c.isprintable()) for c in url):
        return "The URL contains spaces, quote marks or invisible characters; copy it again."
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "The URL is not a valid web address."
    return None


def request_url(url: str, *, method: str = "GET", body: str | None = None,
                timeout: float = HEARTBEAT_TIMEOUT, attempts: int = HEARTBEAT_ATTEMPTS) -> None:
    """Request a URL; any 2xx reply is success. POST sends `body` as plain text.

    Rate limiting (429), server errors (5xx) and network errors are retried. Raises
    RequestError with a readable explanation that never contains the URL.
    """
    data = body.encode("utf-8") if method == "POST" and body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "text/plain; charset=utf-8"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        retry_after = None
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
            return  # urlopen raises HTTPError for any reply that is not 2xx
        except urllib.error.HTTPError as exc:
            problem = f"the server replied HTTP {exc.code}"
            retryable = exc.code == 429 or exc.code >= 500
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            problem = f"could not reach the server: {getattr(exc, 'reason', exc)}"
            retryable = True
        if not retryable or attempt == attempts:
            raise RequestError(problem)
        wait = _retry_wait(attempt, retry_after)
        log.warning("Attempt %d of %d failed (%s); retrying in %.0fs",
                    attempt, attempts, problem, wait)
        time.sleep(wait)
    raise RequestError("unreachable")


def notify_url(args: argparse.Namespace, url: str | None, what: str, text: str) -> int:
    """Send a heartbeat or failure signal (nothing on a dry run). Returns an exit code."""
    if args.dry_run:
        log.info("Dry run: the %s was not sent", what)
        return 0
    problem = check_url(url)
    if problem:
        log.error("Cannot send the %s: %s", what, problem)
        return 1
    log.info("Sending the %s to %s", what, mask_webhook(url))
    try:
        request_url(url, method=args.heartbeat_method, body=text)
    except RequestError as exc:
        log.error("Sending the %s failed: %s", what, exc)
        return 1
    log.info("The %s was sent", what)
    return 0


# --------------------------------------------------------------------------- #
# State file
# --------------------------------------------------------------------------- #
def load_state(path: Path) -> dict:
    """Read the state file. A missing or damaged file starts fresh."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "periods": {}}
    except ValueError:
        log.warning("State file %s is damaged; starting fresh", path)
        return {"version": 1, "periods": {}}
    if not isinstance(state, dict) or not isinstance(state.get("periods"), dict):
        log.warning("State file %s has an unexpected layout; starting fresh", path)
        return {"version": 1, "periods": {}}
    return state


def save_state(path: Path, state: dict) -> None:
    """Write the state file atomically so a crash cannot leave half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def prune_state(state: dict, now: datetime) -> None:
    """Drop outlook periods that ended more than STATE_RETENTION ago."""
    for key in list(state["periods"]):
        try:
            ended = datetime.strptime(key, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        except ValueError:
            ended = None
        if ended is None or ended < now - STATE_RETENTION:
            del state["periods"][key]
            log.debug("Removed old state entry %s", key)


def state_path_for(args: argparse.Namespace, county: County) -> Path:
    """The state file for this county (one per county unless --state-file is given)."""
    if args.state_file:
        return Path(args.state_file)
    return Path(args.script_dir) / f"spc_county_alert_state_{county.geoid}.json"


def load_county_state(args: argparse.Namespace, county: County) -> tuple[Path, dict]:
    """Load the county's state, carrying over the pre-county-selection file if needed."""
    path = state_path_for(args, county)
    legacy = Path(args.script_dir) / LEGACY_STATE_FILE
    if (not path.exists() and not args.state_file and county.geoid == DEFAULT_COUNTY_FIPS
            and legacy.exists()):
        log.info("Carrying over the earlier state file %s to %s", legacy.name, path.name)
        return path, load_state(legacy)
    return path, load_state(path)


# --------------------------------------------------------------------------- #
# Message formatting
# --------------------------------------------------------------------------- #
def fmt_utc(dt: datetime) -> str:
    """'1630Z Nov 16'."""
    return f"{dt:%H%MZ} {dt:%b} {dt.day}"


def fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    """'10:30 AM CST Mon Nov 16' (CDT during daylight saving time)."""
    local = dt.astimezone(tz)
    return f"{local.hour % 12 or 12}:{local:%M} {local:%p} {local:%Z} {local:%a} {local:%b} {local.day}"


def status_text(status: str, previous) -> str:
    if status == "new":
        return "NEW"
    if status == "upgraded":
        return f"UPGRADED from {previous}"
    if status == "lowered":
        return f"LOWERED from {previous}"
    if status == "below":
        if previous:
            return f"LOWERED from {previous}; below the alert threshold"
        return "now below the alert threshold"
    if status == "removed":
        return f"REMOVED; was {previous}"
    if status == "lower":
        return f"down from {previous} previously reported"
    return "previously reported"


def cig_definition_lines(entries) -> list[str]:
    """The CIG definitions, one block per hazard (tornado, hail, wind) with a CIG in
    the message. Each level that applies to the county is bolded and names the day(s).
    """
    lines: list[str] = []
    for kind, definitions in INTENSITY_DEFINITIONS.items():
        days_by_level: dict[int, list[int]] = {}
        for hazard, status, _prev in entries:
            if hazard.kind == kind and status != "removed":
                days_by_level.setdefault(hazard.level, []).append(hazard.day)
        if not days_by_level:
            continue
        lines += ["", f"{INTENSITY_HEADINGS[kind]} They describe intensity, not likelihood."]
        for level, definition in definitions.items():
            text = f"CIG{level}: {definition}"
            if level in days_by_level:
                days = " and ".join(f"Day {d}" for d in sorted(days_by_level[level]))
                lines.append(f"- *{text}* (applies to {days})")
            else:
                lines.append(f"- {text}")
    return lines


def build_message(county_label: str, entries: list[tuple[Hazard, str, str | None]],
                  ping: bool, tz: ZoneInfo | None, failed: list[str]) -> str:
    """Build the Slack text.

    entries: (hazard, status, previous level text) for every hazard in the message.
    status is "new", "upgraded", "unchanged" or "lower", and with report-lowered also
    "lowered", "below" (now below the alert threshold) or "removed" (gone from the
    outlook; the hazard then carries the level it had). A message that reports no new
    or higher risk, and does not ping, is headed as an update rather than an alert.
    """
    rising = ping or any(status in ("new", "upgraded") for _hazard, status, _prev in entries)
    headline = (f"*SPC severe weather outlook {'alert' if rising else 'update'} "
                f"for {county_label}*")
    lines = [f"{CHANNEL_PING} {headline}" if ping else headline,
             f"{'Highest' if rising else 'Latest'} risk touching any part of the county:"]

    by_day: dict[int, list] = {}
    for entry in entries:
        by_day.setdefault(entry[0].day, []).append(entry)

    for day in sorted(by_day):
        day_entries = sorted(by_day[day], key=lambda e: KIND_ORDER[e[0].kind])
        ref = day_entries[0][0]
        lines.append("")
        header = f"*Day {day} outlook*"
        if ref.valid:
            header += f" - valid {fmt_utc(ref.valid)} to {fmt_utc(ref.expire)}"
            if tz:
                header += f" ({fmt_local(ref.valid, tz)} to {fmt_local(ref.expire, tz)})"
        lines.append(header)
        for hazard, status, previous in day_entries:
            text = hazard.describe_removed() if status == "removed" else hazard.describe()
            lines.append(f"- {text} [{status_text(status, previous)}]")
        if ref.issue:
            issued = f"Issued {fmt_utc(ref.issue)}"
            if tz:
                issued += f" ({fmt_local(ref.issue, tz)})"
            lines.append(issued)

    lines.extend(cig_definition_lines(entries))

    if failed:
        lines.append("")
        lines.append(f"_Could not load: {', '.join(failed)}. Check SPC directly for these outlooks._")
    lines.append("")
    lines.append(f"SPC outlooks: <{SPC_OUTLOOKS_PAGE}|{SPC_OUTLOOKS_PAGE}>")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_test_webhook_message(county_label: str, now: datetime, tz: ZoneInfo | None,
                               channel: str = ALERTS_CHANNEL) -> str:
    """A short message that confirms a channel's webhook works. Never pings."""
    sent = fmt_utc(now)
    if tz:
        sent = f"{fmt_local(now, tz)} ({sent})"
    if channel == LOGS_CHANNEL:
        purpose = "The full output of every outlook check will be posted here."
    else:
        purpose = "Alerts will be posted here when a risk reaches the alert threshold."
    return "\n".join([
        f"*Test message from the SPC outlook alert script for {county_label}*",
        f"If you can read this, the {channel} webhook is working. {purpose} "
        "This is not a weather alert; no outlook data was checked.",
        f"Sent {sent}",
        "",
        f"SPC outlooks: <{SPC_OUTLOOKS_PAGE}|{SPC_OUTLOOKS_PAGE}>",
        DISCLAIMER,
    ])


def sample_entries(now: datetime) -> list[tuple[Hazard, str, str | None]]:
    """Made-up hazards for --test-alert, with times based on the current time."""
    issue = now.replace(minute=0, second=0, microsecond=0)
    end1 = issue.replace(hour=12)  # Day 1 runs to the next 1200Z
    if end1 <= issue:
        end1 += timedelta(days=1)
    end2 = end1 + timedelta(days=1)
    day1 = dict(issue=issue, valid=issue, expire=end1)
    day2 = dict(issue=issue, valid=end1, expire=end2)
    return [
        (Hazard(1, CATEGORICAL, "ENH", **day1), "upgraded", "Slight"),
        (Hazard(1, TORNADO, 10, **day1), "new", None),
        (Hazard(1, CIG, 2, **day1), "new", None),
        (Hazard(2, CATEGORICAL, "SLGT", **day2), "unchanged", "Slight"),
    ]


def build_test_alert(county_label: str, now: datetime, tz: ZoneInfo | None, ping: bool) -> str:
    """A sample alert in the real format, clearly marked as a test with made-up data."""
    banner = ("*TEST MESSAGE - sample data, not a real forecast.* "
              "This shows what an alert looks like. Check SPC for the actual outlook.")
    return banner + "\n\n" + build_message(county_label, sample_entries(now), ping, tz, [])


def slack_escape(text: str) -> str:
    """Make text show literally in Slack.

    '<', '>' and '&' start Slack's control sequences, such as <!channel> (which pings
    everyone) and links, so they are replaced with entities that Slack shows as-is.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def shorten_middle(text: str, limit: int) -> str:
    """Fit text into about `limit` characters by cutting whole lines from the middle.

    The start and the end (where a run's outcome is) are kept.
    """
    if len(text) <= limit:
        return text
    budget = max(limit - 200, 0)  # room for the note
    head = text[: budget // 3]
    if "\n" in head:
        head = head[: head.rindex("\n") + 1]
    tail = text[len(text) - (budget - len(head)):]
    if "\n" in tail:
        tail = tail[tail.index("\n") + 1:]
    omitted = text[len(head): len(text) - len(tail)]
    lines = omitted.count("\n")
    amount = f"{lines} line{'s' if lines != 1 else ''}" if lines else f"{len(omitted)} characters"
    note = (f"[... {amount} left out here to fit in one Slack message; "
            "the full output is in the log file ...]")
    if head and not head.endswith("\n"):
        head += "\n"
    return f"{head}{note}\n{tail}"


def build_run_log_message(lines: list[str], exit_code: int, highest_level: int,
                          webhooks: tuple = ()) -> str:
    """The logs channel message: a status line, then the run's output as a code block.

    Any of the given webhook URLs found in the output are masked, and the output is
    escaped so that nothing in it can ping the channel or turn into a link.
    """
    if exit_code or highest_level >= logging.ERROR:
        status = "finished with errors"
    elif highest_level >= logging.WARNING:
        status = "finished with warnings"
    else:
        status = "finished OK"
    output = _hide_urls("\n".join(lines) or "(no output)", webhooks)
    output = shorten_middle(slack_escape(output), LOGS_MAX_CHARS)
    return f"*SPC outlook check* - {status}\n```\n{output}\n```"


def run_log_text(lines: list[str], urls: tuple = ()) -> str:
    """The run's output as plain text for a monitoring service (sent with method = POST),
    with the given secret URLs masked and a long output shortened."""
    return shorten_middle(_hide_urls("\n".join(lines) or "(no output)", urls), LOGS_MAX_CHARS)


def _hide_urls(text: str, urls) -> str:
    """Replace each of the given URLs in text with its masked form."""
    for url in urls:
        if url:
            text = text.replace(url, mask_webhook(url))
    return text


# --------------------------------------------------------------------------- #
# Main logic
# --------------------------------------------------------------------------- #
def get_local_tz() -> ZoneInfo | None:
    try:
        return ZoneInfo(LOCAL_TZ)
    except ZoneInfoNotFoundError:
        log.warning("Time zone data for %s not found (on Windows: pip install tzdata); "
                    "showing UTC times only", LOCAL_TZ)
        return None


@dataclass(frozen=True)
class Outlook:
    """One outlook file, downloaded once per run and checked against every county."""
    product: Product
    data: dict
    issue: datetime | None
    valid: datetime | None
    expire: datetime | None  # from the file, or from the same day's other files

    def is_newer_than(self, other: "Outlook") -> bool:
        """Issued later, or (same issue time) for a nearer day."""
        never = datetime.min.replace(tzinfo=timezone.utc)
        return ((self.issue or never, -self.product.day)
                > (other.issue or never, -other.product.day))


def period_key_of(expire: datetime) -> str:
    """Identifies an outlook period by its end. Day 2 and the next day's Day 1 share it."""
    return expire.strftime("%Y-%m-%dT%H:%MZ")


def products_to_check(rules: dict) -> list[Product]:
    """Categorical and tornado always; hail and wind when they are turned on."""
    return [p for p in PRODUCTS if HAZARD_OF[p.kind] == CATEGORICAL or HAZARD_OF[p.kind] in rules]


def load_outlooks(args: argparse.Namespace, rules: dict, now: datetime
                  ) -> tuple[list[Outlook], list[str]]:
    """Download every outlook file once. Returns (outlooks, labels of files that failed)."""
    outlooks: list[Outlook] = []
    failed: list[str] = []
    for product in products_to_check(rules):
        source = product_source(args.spc_base, product.filename)
        try:
            data = fetch_json(source)
            issue, valid, expire = outlook_times(data)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, DataError,
                KeyError, TypeError, IndexError, AttributeError) as exc:
            log.error("%s outlook could not be checked (%s): %s", product.label, source, exc)
            failed.append(product.label)
            continue
        if expire and expire <= now:
            log.warning("%s outlook expired at %s; SPC may be late updating it. Skipping.",
                        product.label, fmt_utc(expire))
            continue
        outlooks.append(Outlook(product, data, issue, valid, expire))

    by_day: dict[int, list[Outlook]] = {}
    for outlook in outlooks:
        by_day.setdefault(outlook.product.day, []).append(outlook)
    for day, day_outlooks in sorted(by_day.items()):
        issues = {o.product.kind: o.issue for o in day_outlooks if o.issue}
        if len(set(issues.values())) > 1:
            log.warning("Day %d outlook files have different issue times %s; "
                        "SPC may be part way through an update.", day,
                        {k: fmt_utc(v) for k, v in issues.items()})

    # A file with no shaded areas at all carries no times. When the same day's other
    # files agree on theirs, it belongs to the same outlook; otherwise its period stays
    # unknown, and it can never make a risk look removed.
    complete = []
    for outlook in outlooks:
        if outlook.expire is None:
            known = {(o.issue, o.valid, o.expire) for o in by_day[outlook.product.day] if o.expire}
            if len(known) == 1:
                issue, valid, expire = known.pop()
                outlook = replace(outlook, issue=issue, valid=valid, expire=expire)
        complete.append(outlook)
    return complete, failed


def collect_hazards(outlooks: list[Outlook], county: County
                    ) -> tuple[list[Hazard], list[str], dict]:
    """Check the downloaded outlook files against one county.

    Returns (hazards found, labels of files that could not be checked, checked), where
    checked maps (period key, kind) to the outlook file that counts for it, whether or
    not anything in it touched the county.
    """
    # Around SPC's updates, two days' files can briefly cover the same outlook period
    # (a new Day 2 and a Day 3 not yet updated). Only the newest one counts.
    checked: dict[tuple[str, str], Outlook] = {}
    for outlook in outlooks:
        if outlook.expire:
            key = (period_key_of(outlook.expire), outlook.product.kind)
            if key not in checked or outlook.is_newer_than(checked[key]):
                checked[key] = outlook

    hazards: list[Hazard] = []
    failed: list[str] = []
    for outlook in outlooks:
        product = outlook.product
        key = (period_key_of(outlook.expire), product.kind) if outlook.expire else None
        if key and checked.get(key) is not outlook:
            log.info("%s outlook: replaced by a newer outlook for the same period; skipped",
                     product.label)
            continue
        try:
            result = evaluate_product(outlook.data, product.kind, county.geometry)
        except (ValueError, DataError, KeyError, TypeError, IndexError, AttributeError) as exc:
            log.error("%s outlook could not be checked for %s: %s", product.label,
                      county.label, exc)
            failed.append(product.label)
            if key:  # not checked after all, so it cannot show a removal
                checked.pop(key, None)
            continue
        if result.level is None:
            log.info("%s outlook: no shaded area touches %s", product.label, county.label)
            continue
        if outlook.expire is None:
            log.error("%s outlook: shaded area found but the file has no EXPIRE time",
                      product.label)
            failed.append(product.label)
            continue
        hazard = Hazard(product.day, product.kind, result.level,
                        outlook.issue, outlook.valid, outlook.expire)
        log.info("%s outlook: %s touches %s (period ending %s)", product.label,
                 short_level(product.kind, result.level), county.label, hazard.period_key)
        hazards.append(hazard)
    return hazards, failed, checked


def told_level(record: dict, kind: str, report_lowered: bool):
    """The level of this kind the alerts channel was last told about for a period.

    Without report-lowered this is the highest level ever reported, so a dip and a
    return to the same level are not announced. With it, it is the level of the last
    message (None after the risk was reported as lowered below the threshold or removed).
    """
    if report_lowered and f"{kind}_now" in record:
        return record[f"{kind}_now"]
    return record.get(kind)


def compare_with_state(hazards: list[Hazard], checked: dict, periods: dict, rules: dict,
                       report_lowered: bool):
    """Decide what the alerts channel should hear about one county.

    Returns (entries, changed, ping_due, cleared): entries for build_message; whether
    anything changed enough to send; whether a ping is due; and the (period key, kind)
    pairs the channel should stop tracking without a line in the message.
    """
    # A hazard (tornado, hail or wind) is reported for an outlook period when any of
    # its parts reaches its alert threshold; its other parts are then included too.
    reported = {(h.period_key, HAZARD_OF[h.kind]) for h in hazards if h.is_alertable(rules)}
    entries: list[tuple[Hazard, str, str | None]] = []
    changed = ping_due = False
    for hazard in hazards:
        if (hazard.period_key, HAZARD_OF[hazard.kind]) not in reported:
            log.info("Below the alert threshold, logged only: %s", hazard.short())
            continue
        record = periods.get(hazard.period_key, {})
        told = told_level(record, hazard.kind, report_lowered)
        told_rank = rank_of(hazard.kind, told)
        if told_rank is None:
            status = "new"
        elif hazard.rank > told_rank:
            status = "upgraded"
        elif hazard.rank < told_rank:
            status = "lowered" if report_lowered else "lower"
        else:
            status = "unchanged"
        previous_text = short_level(hazard.kind, told) if told_rank is not None else None

        pinged_rank = rank_of(hazard.kind, record.get(f"{hazard.kind}_pinged"))
        needs_ping = (hazard.is_ping_eligible(rules)
                      and (pinged_rank is None or hazard.rank > pinged_rank))

        changed |= status in ("new", "upgraded", "lowered")
        ping_due |= needs_ping
        entries.append((hazard, status, previous_text))
        log.info("Alert-level hazard: %s [%s]%s", hazard.short(), status,
                 "; channel ping due" if needs_ping else "")

    cleared: list[tuple[str, str]] = []
    if not report_lowered:
        return entries, changed, ping_due, cleared

    # Risks the channel was told about that are no longer at alert level. Only outlook
    # files checked on this run count, so a failed download never looks like a removal.
    current = {(h.period_key, h.kind): h for h in hazards}
    shown = {(h.period_key, h.kind) for h, _status, _prev in entries}
    dropped: dict[tuple[str, str], list] = {}  # (period key, hazard) -> candidate lines
    for key in sorted(periods):
        record = periods[key]
        if not isinstance(record, dict):
            continue
        for kind in KIND_LABELS:
            told = told_level(record, kind, True)
            told_rank = rank_of(kind, told)
            outlook = checked.get((key, kind))
            if told_rank is None or (key, kind) in shown or outlook is None:
                continue
            hazard = current.get((key, kind))
            if hazard is None:
                stub = Hazard(outlook.product.day, kind, told, outlook.issue, outlook.valid,
                              outlook.expire)
                line = (stub, "removed", short_level(kind, told), False)
            elif hazard.rank < told_rank:
                line = (hazard, "below", short_level(kind, told), False)
            else:  # not lower itself: the rest of its hazard fell below the threshold
                line = (hazard, "below", None, True)
            dropped.setdefault((key, HAZARD_OF[kind]), []).append(line)
    for lines in dropped.values():
        explained = any(not unchanged for *_line, unchanged in lines)
        for hazard, status, previous, unchanged in lines:
            if unchanged and explained:
                cleared.append((hazard.period_key, hazard.kind))
                continue
            entries.append((hazard, status, previous))
            changed = True
            log.info("Lowered or removed: %s [%s]", hazard.short(), status)
    return entries, changed, ping_due, cleared


def check_county(args: argparse.Namespace, county: County, outlooks: list[Outlook],
                 load_failed: list[str], now: datetime, tz: ZoneInfo | None) -> int:
    """Check one county and send its alert if one is due. Returns an exit code."""
    log.info("Checking SPC outlooks for %s (FIPS %s)", county.label, county.geoid)
    if county.geoid[:2] in OUTSIDE_SPC_COVERAGE:
        log.warning("SPC outlooks cover the contiguous U.S. only; %s will never be shaded",
                    county.label)

    hazards, check_failed, checked = collect_hazards(outlooks, county)
    failed = load_failed + check_failed
    exit_code = 1 if failed else 0

    state_file, state = load_county_state(args, county)
    prune_state(state, now)
    periods = state["periods"]
    entries, changed, ping_due, cleared = compare_with_state(
        hazards, checked, periods, args.rules, args.report_lowered)

    if not entries:
        log.info("No risk at or above the alert thresholds touches %s; "
                 "nothing to send to the alerts channel", county.label)
        return exit_code
    ping = ping_due and not args.no_ping
    if not changed and not ping:
        log.info("Risk has not %s since the last alert; nothing sent to the alerts channel",
                 "changed" if args.report_lowered else "increased")
        return exit_code

    message = build_message(county.label, entries, ping, tz, failed)
    log.info("Alert created (ping=%s):\n%s", ping, message)

    if args.dry_run:
        print(message)
        log.info("Dry run: message not sent and state not saved")
        return exit_code

    if deliver(args, message, "alert", ALERTS_CHANNEL) != 0:
        return 1  # the state is not saved, so the next run will try again

    # Remember, for each outlook period, the highest level reported (and pinged) and
    # the level this message reported.
    def higher(kind, old, new_rank) -> bool:
        old_rank = rank_of(kind, old)
        return old_rank is None or new_rank > old_rank

    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    for hazard, status, _prev in entries:
        record = periods.setdefault(hazard.period_key, {})
        if status in ("below", "removed"):
            record[f"{hazard.kind}_now"] = None
        else:
            if higher(hazard.kind, record.get(hazard.kind), hazard.rank):
                record[hazard.kind] = hazard.level
            record[f"{hazard.kind}_now"] = hazard.level
            if ping and hazard.is_ping_eligible(args.rules):
                key = f"{hazard.kind}_pinged"
                if higher(hazard.kind, record.get(key), hazard.rank):
                    record[key] = hazard.level
        record["updated"] = stamp
    for key, kind in cleared:
        periods.setdefault(key, {})[f"{kind}_now"] = None
    state["county"] = {"fips": county.geoid, "name": county.label}
    save_state(state_file, state)
    log.debug("State saved to %s", state_file)
    return exit_code


def run(args: argparse.Namespace, now: datetime | None = None) -> int:
    """One complete outlook check of every county. Returns the process exit code."""
    now = now or datetime.now(timezone.utc)
    tz = get_local_tz()
    exit_code = 0
    counties: list[County] = []
    for spec in args.locations:
        try:
            county = resolve_location(spec, args.cache_dir, args.refresh_county)
        except DataError as exc:
            log.error("Cannot check %s without its county outline: %s", spec.label, exc)
            exit_code = 1
            continue
        if any(c.geoid == county.geoid for c in counties):
            log.warning("%s is listed more than once; checking it once", county.label)
            continue
        counties.append(county)
    if not counties:
        return 1

    outlooks, failed = load_outlooks(args, args.rules, now)
    for county in counties:
        if check_county(args, county, outlooks, failed, now, tz) != 0:
            exit_code = 1
    return exit_code


def _join(labels: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _county_label_best_effort(args: argparse.Namespace) -> str:
    """The counties' names for test messages; falls back to what the user typed."""
    labels = []
    for spec in args.locations:
        try:
            labels.append(resolve_location(spec, args.cache_dir, args.refresh_county).label)
        except DataError as exc:
            log.warning("Could not look up %s (%s); using that name", spec.label, exc)
            labels.append(spec.label)
    return _join(labels)


def run_test_webhook(args: argparse.Namespace, now: datetime | None = None) -> int:
    """--test-webhook: send a short message to each channel to confirm Slack delivery."""
    now = now or datetime.now(timezone.utc)
    county_label, tz = _county_label_best_effort(args), get_local_tz()
    exit_code = 0
    for channel in (ALERTS_CHANNEL, LOGS_CHANNEL):
        if channel == LOGS_CHANNEL and not args.logs_channel:
            log.info("The logs channel is turned off, so it was not tested")
            continue
        text = build_test_webhook_message(county_label, now, tz, channel)
        if deliver(args, text, "test message", channel) != 0:
            exit_code = 1
    return exit_code


def run_test_alert(args: argparse.Namespace, now: datetime | None = None) -> int:
    """--test-alert: send a sample alert in the real format, marked as a test."""
    now = now or datetime.now(timezone.utc)
    ping = args.test_ping and not args.no_ping
    text = build_test_alert(_county_label_best_effort(args), now, get_local_tz(), ping)
    return deliver(args, text, "sample alert" + (" (with channel ping)" if ping else ""),
                   ALERTS_CHANNEL)


def run_test_county(args: argparse.Namespace) -> int:
    """--test-county: show the selected counties and their boundary details."""
    exit_code = 0
    blocks = []
    for spec in args.locations:
        try:
            county = resolve_location(spec, args.cache_dir, args.refresh_county)
        except DataError as exc:
            log.error("County lookup failed for %s: %s", spec.label, exc)
            exit_code = 1
            continue
        polygons = iter_polygons(county.geometry)
        points = [p for poly in polygons for ring in poly for p in ring]
        min_lon, min_lat, max_lon, max_lat = _bbox(points)
        lines = [
            f"County:     {county.label}",
            f"FIPS code:  {county.geoid}",
            f"Outline:    {len(polygons)} polygon(s), {len(points)} points",
            f"Extent:     lat {min_lat:.3f} to {max_lat:.3f}, lon {min_lon:.3f} to {max_lon:.3f}",
            f"Cache file: {county_cache_path(args.cache_dir, county.geoid)}",
            f"State file: {state_path_for(args, county)}",
        ]
        if county.geoid[:2] in OUTSIDE_SPC_COVERAGE:
            lines.append("Note:       SPC outlooks do not cover this state.")
        blocks.append("\n".join(lines))
        log.info("County check: %s (FIPS %s) OK", county.label, county.geoid)
    blocks.append(f"Settings:   {args.config or '(no settings file; using options and defaults)'}")
    print("\n\n".join(blocks))
    return exit_code


def run_test_heartbeat(args: argparse.Namespace) -> int:
    """--test-heartbeat: request the heartbeat and failure URLs once each."""
    text = ("Test from the SPC outlook alert script. This is not a real run; "
            "no outlook data was checked.")
    exit_code = 0
    for url, what in ((args.heartbeat_url, "heartbeat"), (args.failure_url, "failure signal")):
        if not url:
            log.info("No %s URL is set, so it was not tested", what)
            continue
        if notify_url(args, url, f"test {what}", text) != 0:
            exit_code = 1
    if args.failure_url and not args.dry_run:
        log.info("The monitoring service may show a failure until the next normal run")
    return exit_code


def run_list_counties(args: argparse.Namespace) -> int:
    """--list-counties: print every county in --state with its FIPS code."""
    try:
        rows = list_state_counties(args.state)
    except DataError as exc:
        log.error("Could not list counties: %s", exc)
        return 1
    print(f"Counties in {STATES[args.state][1]} ({len(rows)}):")
    for props in rows:
        print(f"  {props.get('GEOID')}  {props.get('NAME')}")
    return 0


# --------------------------------------------------------------------------- #
# Settings file
# --------------------------------------------------------------------------- #
def _setting_name(name: str) -> str:
    """Section and setting names ignore case, and '_' works in place of '-'."""
    return name.strip().lower().replace("_", "-")


def _unquote(value: str) -> str:
    """Remove one pair of matching quotes, which people often put around URLs."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _mask_urls(text: str) -> str:
    """Mask web addresses in an error message that may quote a line of the file."""
    return re.sub(r"https?://[^\s'\"]+", lambda m: mask_webhook(m.group()), text)


_LOCATION_RE = re.compile(LOCATION_SECTION + r"\b[\s:]*(.*)", re.IGNORECASE)


def read_config(path: Path) -> tuple[dict[str, dict[str, str]], list]:
    """Read the settings file, leaving out blank values.

    Returns ({section: {setting: value}}, locations), where locations lists the
    [location NAME] sections in file order as (section name, NAME, {setting: value}).
    Raises ConfigError if the file cannot be read or has an unknown section or setting,
    so that a typo is reported instead of quietly ignored.
    """
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    parser.optionxform = _setting_name
    try:
        with open(path, encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read the settings file {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"the settings file {path} is not UTF-8 text: {exc}") from exc
    except configparser.Error as exc:
        raise ConfigError(f"the settings file {path} has a mistake in it: "
                          f"{_mask_urls(str(exc))}") from exc

    settings: dict[str, dict[str, str]] = {}
    locations = []
    for section in parser.sections():
        name = _setting_name(section)
        location = _LOCATION_RE.fullmatch(section.strip())
        if location:
            allowed = CONFIG_SETTINGS["county"]
        elif name in CONFIG_SETTINGS:
            allowed = CONFIG_SETTINGS[name]
            if name in settings:
                raise ConfigError(f"section [{section}] appears twice in {path}")
        else:
            known = ", ".join(f"[{s}]" for s in CONFIG_SETTINGS)
            raise ConfigError(f"unknown section [{section}] in {path}; the sections are "
                              f"{known} and [{LOCATION_SECTION} NAME]")
        values = {}
        for key, value in parser.items(section):
            if key not in allowed:
                raise ConfigError(f"unknown setting '{key}' in [{section}] of {path}; the "
                                  f"settings there are {', '.join(allowed)}")
            value = _unquote(value.strip())
            if value:
                values[key] = value
        if location:
            locations.append((section.strip(), location.group(1).strip() or None, values))
        else:
            settings[name] = values
    return settings, locations


@dataclass(frozen=True)
class Config:
    """What the settings file says. Empty when there is no settings file."""
    path: Path | None = None
    values: dict = field(default_factory=dict)  # {section: {setting: value}}
    locations: list = field(default_factory=list)  # see read_config

    def text(self, section: str, key: str) -> str | None:
        return self.values.get(section, {}).get(key)

    def flag(self, section: str, key: str) -> bool | None:
        """A yes/no setting, or None if it is not set."""
        value = self.text(section, key)
        if value is None:
            return None
        flag = configparser.ConfigParser.BOOLEAN_STATES.get(value.lower())
        if flag is None:
            raise ConfigError(f"'{key}' in [{section}] of {self.path} must be yes or no, "
                              f"not {value!r}")
        return flag

    def path_setting(self, section: str, key: str) -> Path | None:
        """A file or folder setting. A relative path is relative to the settings file."""
        value = self.text(section, key)
        if value is None:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.path.parent / path

    def threshold(self, section: str, key: str, default: int | None, highest: int,
                  percent: bool) -> int | None:
        """A probability (percent) or CIG threshold. Blank uses the default; "off" means
        never. Accepts "15", "15%" or "0.15" for a percent and "2" or "CIG2" for a CIG."""
        value = self.text(section, key)
        if value is None:
            return default
        word = value.strip().lower()
        if word in ("off", "no", "none", "never"):
            return None
        number = None
        try:
            if percent:
                amount = float(word.rstrip("%").strip())
                number = round(amount * 100) if 0 < amount < 1 else amount
            else:
                number = int(word[3:] if word.startswith("cig") else word)
        except ValueError:
            pass
        if number is None or number != int(number) or not 1 <= number <= highest:
            what = "a percent from 1 to 100" if percent else f"a CIG level from 1 to {highest}"
            raise ConfigError(f"'{key}' in [{section}] of {self.path} must be {what}, or off; "
                              f"not {value!r}")
        return int(number)


def load_config(path: Path | None, script_dir: Path) -> Config:
    """Read the given settings file, or else outlooker.ini next to the script if it exists."""
    if path is None:
        path = script_dir / CONFIG_FILE
        if not path.exists():
            return Config()
    elif not path.exists():
        raise ConfigError(f"settings file {path} not found")
    values, locations = read_config(path)
    return Config(path, values, locations)


def location_specs(config: Config) -> list[CountySpec]:
    """The counties named by the [location NAME] sections of the settings file."""
    specs = []
    for section, nickname, values in config.locations:
        where = f"[{section}] in {config.path}"
        fips, name, state = values.get("fips"), values.get("county"), values.get("state")
        if fips and (name or state):
            raise ConfigError(f"{where}: use either fips, or county with state, not both")
        if fips:
            if not re.fullmatch(r"\d{5}", fips) or fips[:2] not in STATE_BY_FIPS:
                raise ConfigError(f"{where}: fips must be a 5-digit county FIPS code such as "
                                  f"47125, not {fips!r}")
            specs.append(CountySpec(geoid=fips, nickname=nickname))
        elif name and state:
            try:
                specs.append(CountySpec(name=name, state=parse_state(state), nickname=nickname))
            except ValueError as exc:
                raise ConfigError(f"{where}: {exc}") from exc
        else:
            raise ConfigError(f"{where} needs fips, or county together with state")
    return specs


def hazard_rules(config: Config, section: str) -> HazardRules | None:
    """The [hail] or [wind] thresholds, or None when that hazard is turned off (the
    default). Every value is checked even when the hazard is off."""
    defaults = HAIL_WIND_DEFAULTS
    highest_cig = max(INTENSITY_DEFINITIONS[INTENSITY_OF[section]])
    rules = HazardRules(
        config.threshold(section, "alert-probability", defaults.alert_probability, 100, True),
        config.threshold(section, "alert-intensity", defaults.alert_intensity, highest_cig, False),
        config.threshold(section, "ping-probability", defaults.ping_probability, 100, True),
        config.threshold(section, "ping-intensity", defaults.ping_intensity, highest_cig, False),
    )
    if not config.flag(section, "enabled"):
        return None
    if rules.alert_probability is None and rules.alert_intensity is None:
        raise ConfigError(f"[{section}] in {config.path} is turned on, but alert-probability "
                          "and alert-intensity are both off, so it could never alert")
    return rules


def _first(*values: str | None) -> str | None:
    """The first value that is not blank, with spaces trimmed; None if all are blank."""
    for value in values:
        if value and value.strip():
            return value.strip()
    return None


def apply_settings(args: argparse.Namespace, config: Config) -> None:
    """Fill in what the command line left out: from environment variables, then the
    settings file, then the defaults. (The counties are chosen in parse_args.)

    Raises ConfigError for a setting with a bad value.
    """
    env = os.environ
    # Read every yes/no setting first, so a bad value is reported even when a
    # command-line option makes it irrelevant for this run.
    ping = config.flag("alerts-channel", "ping")
    report_lowered = config.flag("alerts-channel", "report-lowered")
    logs_enabled = config.flag("logs-channel", "enabled")
    verbose = config.flag("options", "verbose")

    args.alerts_webhook = _first(args.alerts_webhook, env.get("SLACK_ALERTS_WEBHOOK_URL"),
                                 env.get("SLACK_WEBHOOK_URL"),  # the earlier name
                                 config.text("alerts-channel", "webhook"))
    args.logs_webhook = _first(args.logs_webhook, env.get("SLACK_LOGS_WEBHOOK_URL"),
                               config.text("logs-channel", "webhook"))
    if args.logs_channel is None:  # neither --logs-channel nor --no-logs-channel
        args.logs_channel = bool(args.logs_webhook) if logs_enabled is None else logs_enabled
    args.no_ping = args.no_ping or ping is False
    args.report_lowered = bool(report_lowered)
    args.verbose = args.verbose or bool(verbose)
    args.log_file = (args.log_file or config.path_setting("options", "log-file")
                     or args.script_dir / "spc_county_alert.log")
    args.state_file = args.state_file or config.path_setting("options", "state-file")
    args.cache_dir = (args.cache_dir or config.path_setting("options", "cache-dir")
                      or args.script_dir)

    args.rules = dict(DEFAULT_RULES)
    for section in (HAIL, WIND):
        rules = hazard_rules(config, section)
        if rules:
            args.rules[section] = rules

    args.heartbeat_url = _first(args.heartbeat_url, env.get("OUTLOOKER_HEARTBEAT_URL"),
                                config.text("heartbeat", "url"))
    args.failure_url = _first(args.failure_url, env.get("OUTLOOKER_FAILURE_URL"),
                              config.text("heartbeat", "failure-url"))
    args.heartbeat_method = (config.text("heartbeat", "method") or "GET").upper()
    if args.heartbeat_method not in ("GET", "POST"):
        raise ConfigError(f"'method' in [heartbeat] of {config.path} must be GET or POST, "
                          f"not {config.text('heartbeat', 'method')!r}")


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(
        description="Alert a Slack channel to SPC convective outlook risks for one or "
                    "more counties (default: Montgomery County, TN).",
        epilog=f"Settings can also come from {CONFIG_FILE} next to this script (copy "
               "outlooker.example.ini to start) and from environment variables. "
               "Command-line options win over environment variables, which win over the "
               "settings file.")
    p.add_argument("--config", type=Path, metavar="FILE",
                   help=f"Settings file to read (default: {CONFIG_FILE} next to this "
                        "script, if there is one)")
    where = p.add_argument_group(
        "county (default: Montgomery County, TN; for several counties, use [location NAME] "
        "sections in the settings file)")
    where.add_argument("--county", help="County name, e.g. 'Davidson' (needs --state; "
                                        "env SPC_COUNTY)")
    where.add_argument("--state", help="State: TN, Tennessee or 47 (env SPC_STATE)")
    where.add_argument("--fips", help="5-digit county FIPS code, e.g. 47037 "
                                      "(env SPC_COUNTY_FIPS)")

    slack = p.add_argument_group("Slack channels")
    slack.add_argument("--alerts-channel-webhook", dest="alerts_webhook", metavar="URL",
                       help="Incoming Webhook URL for the alerts channel, which gets the "
                            "alerts (env SLACK_ALERTS_WEBHOOK_URL)")
    slack.add_argument("--webhook", dest="alerts_webhook", help=argparse.SUPPRESS)  # old name
    slack.add_argument("--logs-channel-webhook", dest="logs_webhook", metavar="URL",
                       help="Incoming Webhook URL for the logs channel, which gets the full "
                            "output of every run (env SLACK_LOGS_WEBHOOK_URL)")
    slack.add_argument("--logs-channel", action=argparse.BooleanOptionalAction,
                       help="Post this run's output to the logs channel, or not (default: "
                            "as the settings file says, else yes when a logs channel "
                            "webhook is set)")
    slack.add_argument("--no-ping", action="store_true",
                       help="Never ping the alerts channel")

    watch = p.add_argument_group("monitoring service (settings file: [heartbeat])")
    watch.add_argument("--heartbeat-url", metavar="URL",
                       help="Requested after every outlook check, so the service can tell "
                            "you if the checks stop (env OUTLOOKER_HEARTBEAT_URL)")
    watch.add_argument("--failure-url", metavar="URL",
                       help="Requested when something goes wrong during a check "
                            "(env OUTLOOKER_FAILURE_URL)")

    modes = p.add_argument_group("tests and tools (only one at a time)").add_mutually_exclusive_group()
    modes.add_argument("--test-webhook", action="store_true",
                       help="Send a short test message to the alerts channel, and to the "
                            "logs channel when it is on, then exit")
    modes.add_argument("--test-alert", action="store_true",
                       help="Send a sample alert marked TEST (made-up data) to the alerts "
                            "channel and exit")
    modes.add_argument("--test-heartbeat", action="store_true",
                       help="Request the heartbeat URL and the failure URL once each, then "
                            "exit")
    modes.add_argument("--test-county", action="store_true",
                       help="Show the selected counties and their boundaries, then exit")
    modes.add_argument("--list-counties", action="store_true",
                       help="List the counties in --state with their FIPS codes, then exit")
    p.add_argument("--test-ping", action="store_true",
                   help="With --test-alert, include the @channel ping")

    p.add_argument("--state-file", type=Path,
                   help="Remembers what has been reported (default: "
                        "spc_county_alert_state_<FIPS>.json next to this script)")
    p.add_argument("--log-file", type=Path,
                   help="Log file, rotated at 1 MB with 5 backups (default: "
                        "spc_county_alert.log next to this script)")
    p.add_argument("--cache-dir", type=Path,
                   help="Folder for cached county outlines (default: next to this script)")
    p.add_argument("--refresh-county", action="store_true",
                   help="Download the county outline again even if it is cached")
    p.add_argument("--spc-base", default=SPC_BASE,
                   help="URL or local folder holding the SPC outlook files (default: SPC website)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print messages instead of sending them; do not save state")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args(argv)
    args.script_dir = here

    try:
        config = load_config(args.config, here)
        apply_settings(args, config)
    except ConfigError as exc:
        p.error(str(exc))
    args.config = config.path  # None when there is no settings file

    # The counties are taken as a whole from the first place that names one: the
    # command line, then the environment variables, then the settings file ([county]
    # and every [location NAME] section).
    county_from = ""
    from_file_only = False
    if args.county is None and args.state is None and args.fips is None:
        names = ("SPC_COUNTY", "SPC_STATE", "SPC_COUNTY_FIPS")
        from_env = [os.environ.get(name) or None for name in names]
        from_file = [config.text("county", key) for key in ("county", "state", "fips")]
        from_file_only = not any(from_env)
        if any(from_env):
            args.county, args.state, args.fips = from_env
            county_from = "the environment variables " + ", ".join(names)
        elif any(from_file):
            args.county, args.state, args.fips = from_file
            county_from = f"[county] in {config.path}"

    def county_error(message: str) -> None:
        if county_from:
            message += f". The county settings came from {county_from}"
        p.error(message)

    if args.state is not None:
        try:
            args.state = parse_state(args.state)
        except ValueError as exc:
            county_error(str(exc))

    if args.list_counties:
        if not args.state:
            county_error("--list-counties needs --state")
    elif args.fips and (args.county or args.state):
        county_error("use either --fips or --county with --state, not both")
    elif args.county and not args.state:
        county_error("--county needs --state (for example --county Davidson --state TN)")
    elif args.state and not args.county:
        county_error("--state needs --county (or use --list-counties to see the names)")

    if args.fips:
        args.fips = args.fips.strip()
        if not re.fullmatch(r"\d{5}", args.fips) or args.fips[:2] not in STATE_BY_FIPS:
            county_error(f"--fips must be a 5-digit county FIPS code such as 47125, "
                         f"not {args.fips!r}")

    locations = []
    if args.fips:
        locations.append(CountySpec(geoid=args.fips))
    elif args.county:
        locations.append(CountySpec(name=args.county.strip(), state=args.state))
    if from_file_only:
        try:
            locations += location_specs(config)
        except ConfigError as exc:
            p.error(str(exc))
    unique = []
    for spec in locations or [CountySpec(geoid=DEFAULT_COUNTY_FIPS)]:
        if replace(spec, nickname=None) not in [replace(u, nickname=None) for u in unique]:
            unique.append(spec)
    args.locations = unique
    if args.state_file and len(args.locations) > 1:
        p.error("state-file (--state-file) works with one county only; leave it blank to "
                "keep one state file per county")

    if args.test_ping and not args.test_alert:
        p.error("--test-ping only works with --test-alert")
    if args.test_heartbeat and not (args.heartbeat_url or args.failure_url):
        p.error("--test-heartbeat needs a heartbeat URL: set url or failure-url under "
                "[heartbeat] in the settings file, or pass --heartbeat-url")
    needs_webhook = not (args.test_county or args.list_counties or args.dry_run
                         or args.test_heartbeat)
    if needs_webhook and not args.alerts_webhook:
        if args.config:
            where = f"add webhook = <URL> under [alerts-channel] in {args.config}"
        else:
            where = (f"copy outlooker.example.ini to {here / CONFIG_FILE} and paste the "
                     "URL in as the [alerts-channel] webhook")
        p.error(f"the alerts channel needs a Slack webhook URL: {where} (or set "
                "SLACK_ALERTS_WEBHOOK_URL, pass --alerts-channel-webhook, or use --dry-run)")
    return args


class RunLog(logging.Handler):
    """Keeps a copy of this run's log lines, to post to the logs channel."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.highest = logging.NOTSET  # the most severe level logged so far

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # a logging problem must never stop the run
            self.handleError(record)
            return
        self.highest = max(self.highest, record.levelno)


def setup_logging(log_file: Path, verbose: bool) -> RunLog:
    """Log to a rotating file and to the console, and keep a copy for the logs channel."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(level)
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    console = logging.StreamHandler()
    run_log = RunLog()
    for handler in (file_handler, console, run_log):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return run_log


def secret_urls(args: argparse.Namespace) -> tuple:
    """Every URL that must never appear in a post or a request body."""
    return (args.alerts_webhook, args.logs_webhook, args.heartbeat_url, args.failure_url)


def send_run_log(args: argparse.Namespace, run_log: RunLog, exit_code: int) -> int:
    """Post the full output of this run to the logs channel. Returns an exit code."""
    text = build_run_log_message(run_log.lines, exit_code, run_log.highest, secret_urls(args))
    return deliver(args, text, "run log", LOGS_CHANNEL)


def send_heartbeat(args: argparse.Namespace, run_log: RunLog, exit_code: int) -> int:
    """Tell the monitoring service this run happened. Returns an exit code."""
    return notify_url(args, args.heartbeat_url, "heartbeat",
                      run_log_text(run_log.lines, secret_urls(args)))


def send_failure_signal(args: argparse.Namespace, run_log: RunLog, exit_code: int) -> int:
    """Tell the monitoring service this run had a problem. Returns an exit code."""
    return notify_url(args, args.failure_url, "failure signal",
                      run_log_text(run_log.lines, secret_urls(args)))


def _attempt(what: str, step, *step_args) -> int:
    """Run one of the end-of-run steps; an unexpected error is logged, not raised."""
    try:
        return step(*step_args)
    except Exception:
        log.exception("Unexpected error while sending the %s", what)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_log = setup_logging(args.log_file, args.verbose)
    if args.config:
        log.debug("Settings read from %s", args.config)
    if args.test_webhook:
        task = run_test_webhook
    elif args.test_alert:
        task = run_test_alert
    elif args.test_heartbeat:
        task = run_test_heartbeat
    elif args.test_county:
        task = run_test_county
    elif args.list_counties:
        task = run_list_counties
    else:
        task = run
    try:
        exit_code = task(args)
    except Exception:  # last resort: make sure unexpected errors reach the log
        log.exception("Unexpected error")
        exit_code = 1
    if task is not run:
        return exit_code  # the tests and tools are one-off checks

    # After every outlook check, whether or not it had anything to report: the full
    # output to the logs channel, then the heartbeat, then, if anything went wrong
    # (including those two), the failure signal.
    if args.logs_channel and _attempt("run log", send_run_log, args, run_log, exit_code):
        exit_code = 1
    if args.heartbeat_url and _attempt("heartbeat", send_heartbeat, args, run_log, exit_code):
        exit_code = 1
    if exit_code and args.failure_url:
        _attempt("failure signal", send_failure_signal, args, run_log, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
