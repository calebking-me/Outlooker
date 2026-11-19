#!/usr/bin/env python3
"""
spc_county_alert.py
===================

Checks the NWS Storm Prediction Center (SPC) convective outlooks for a U.S. county
(Montgomery County, Tennessee by default) and posts an alert to a Slack channel
through an Incoming Webhook.

Choosing the county
-------------------
* Default: Montgomery County, TN (FIPS code 47125).
* By name:  --county "Davidson" --state TN   ("Davidson County" and "davidson" also
  work; the state may be an abbreviation, a full name or a 2-digit FIPS code).
* By code:  --fips 47037   (5-digit county FIPS code: 2 digits state + 3 digits county).
* Not sure of the spelling?  --list-counties --state TN
* The same choices can come from environment variables SPC_COUNTY + SPC_STATE, or
  SPC_COUNTY_FIPS. Command-line options win over environment variables.
* SPC outlooks cover the contiguous United States only.

Names are looked up with the U.S. Census Bureau. If a name matches more than one
county (for example "Richmond" in Virginia is both a county and an independent city),
the script stops and lists the matches with their FIPS codes.

What it checks
--------------
* Categorical outlooks for Days 1, 2 and 3.
* Tornado probability outlooks for Days 1 and 2. (SPC does not issue a separate
  tornado outlook for Day 3.)
* Tornado Conditional Intensity Groups (CIG1, CIG2, CIG3) for Days 1 and 2. A CIG
  describes the strongest tornado reasonably expected IF tornadoes occur; it is
  about intensity, not likelihood. The CIG level is reported alongside the tornado
  probability, and the message then includes the definitions of the CIG levels.

A hazard counts for the county when ANY part of the county touches a shaded area.
The script tests the county's full boundary polygon against every outlook polygon,
not a single point, and reports the highest level that touches the county.

When it sends to Slack
----------------------
* Every shaded area that touches the county is written to the log file.
* A Slack message is sent only for a categorical risk of Slight or higher, or a
  tornado probability of 5% or higher, and only when that risk is new or has
  increased since the last alert for the same outlook period (see "State file").
* A tornado CIG is included whenever that day's tornado probability is being
  reported (5% or higher). A new or higher CIG counts as an increase in risk and
  sends a message; on its own it does not ping the channel.
* The message pings the whole channel (<!channel>, Slack's syntax for @channel) when:
    - the categorical risk is Enhanced or higher on the Day 1 or Day 2 outlook, or
    - the tornado probability is 10% or higher on any outlook.
  A ping happens once per outlook period and hazard, and again only if that risk
  increases. If a risk that was already reported reaches the ping window without
  increasing (for example a Day 3 Enhanced Risk that becomes the Day 2 outlook),
  the script sends one message to deliver the ping.

State file
----------
SPC reissues outlooks several times a day, and today's Day 2 outlook becomes
tomorrow's Day 1 outlook. The state file remembers, for each outlook period
(identified by the time the period ends, which SPC calls EXPIRE), the highest level
already reported and already pinged. A reissued outlook with the same or a lower
level is logged but not sent. Entries older than a few days are removed automatically.
Each county has its own state file (spc_county_alert_state_<FIPS>.json), so several
counties can be monitored by separate scheduled runs.

Testing the setup
-----------------
    --test-webhook   send a short test message to Slack to confirm the webhook works
    --test-alert     send a sample alert (clearly marked TEST, made-up data) so you can
                     see the formatting; add --test-ping to include the @channel ping
    --test-county    show which county was selected and its boundary details
    --dry-run        run the real check but print the message instead of sending it
None of the test options read or change the state file. The offline unit tests are
in test_spc_county_alert.py (python -m unittest -v test_spc_county_alert).

Slack webhook handling
----------------------
The webhook URL is checked before use (must be https, no stray spaces or quotes) and
is never written to the log; logs show only https://hooks.slack.com/****. Slack's
documented error codes (for example channel_not_found or channel_is_archived) are
translated into plain-language advice. Rate limiting (HTTP 429), Slack server errors
(5xx) and network failures are retried up to 3 times; other errors are not.

Data sources
------------
* SPC outlook GeoJSON files: https://www.spc.noaa.gov/products/outlook/
    dayNotlk_cat.nolyr.geojson      categorical (LABEL = TSTM, MRGL, SLGT, ENH, MDT, HIGH)
    dayNotlk_torn.nolyr.geojson     tornado probability (DN = percent: 2, 5, 10, ...)
    dayNotlk_cigtorn.nolyr.geojson  tornado intensity group (LABEL = CIG1, CIG2, CIG3)
* County boundaries: U.S. Census Bureau TIGERweb, Counties layer. Each county's
  outline is downloaded once and cached as county_<FIPS>.geojson (see --cache-dir).

Requirements and setup
----------------------
* Python 3.9+, standard library only. On Windows, also run: pip install tzdata
* Create a Slack app with Incoming Webhooks enabled and add a webhook for your
  channel. Put the URL in the SLACK_WEBHOOK_URL environment variable. The URL is a
  secret: keep it out of shared files and version control.
* Check the webhook:  python spc_county_alert.py --test-webhook
* Run it on a schedule, for example every 30 minutes with cron:
    */30 * * * * SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \\
                 /usr/bin/python3 /path/to/spc_county_alert.py

Other options
-------------
    --no-ping      never ping the channel (handy while testing in a real channel)
    --spc-base     URL or local folder to read the SPC files from (for testing)
    -v             verbose (debug) logging

Exit codes: 0 = success; 1 = something failed (details in the log). A run in which
one outlook file failed still reports the outlooks that did load.

Disclaimer: this script was written with AI assistance. Verify all information on
the SPC website. It must not be used as a primary means of receiving severe weather
alerts.
"""
from __future__ import annotations

import argparse
import difflib
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_COUNTY_FIPS = "47125"  # Montgomery County, Tennessee
LEGACY_STATE_FILE = "spc_county_alert_state.json"  # name used before county selection

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
# Tornado probability contours SPC uses on the Day 1 and Day 2 outlooks (percent).
TORNADO_LEVELS = (2, 5, 10, 15, 30, 45, 60)

# Alert and ping thresholds.
ALERT_MIN_CATEGORY = "SLGT"
ALERT_MIN_TORNADO = 5
PING_MIN_CATEGORY = "ENH"
PING_CATEGORY_DAYS = (1, 2)
PING_MIN_TORNADO = 10  # on any outlook that has a tornado product (Days 1 and 2)

# Tornado Conditional Intensity Groups. Each names the strongest tornado SPC considers
# a reasonable expectation if tornadoes occur. Edit the wording here if SPC changes it.
CIG_LEVELS = (1, 2, 3)
CIG_DEFINITIONS = {
    1: "EF2 or stronger tornadoes possible",
    2: "EF3 or stronger tornadoes possible",
    3: "EF4 or stronger tornadoes possible (rare, high-end events)",
}

CATEGORICAL = "categorical"
TORNADO = "tornado"
CIG = "tornado_cig"
KIND_LABELS = {
    CATEGORICAL: "categorical",
    TORNADO: "tornado",
    CIG: "tornado intensity (CIG)",
}
KIND_ORDER = {CATEGORICAL: 0, TORNADO: 1, CIG: 2}

STATE_RETENTION = timedelta(days=4)
CHANNEL_PING = "<!channel>"

# Slack webhook settings.
SLACK_HOST = "hooks.slack.com"
SLACK_ATTEMPTS = 3        # tries for rate limits, Slack server errors and network errors
SLACK_TIMEOUT = 15        # seconds per try
SLACK_MAX_RETRY_WAIT = 60  # cap on a Retry-After wait, seconds

_NEW_WEBHOOK = "Create a new webhook in your Slack app and update SLACK_WEBHOOK_URL."
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
    kind: str  # CATEGORICAL, TORNADO or CIG
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
)


class DataError(Exception):
    """Raised when downloaded or cached data is missing or not what we expect."""


class SlackError(RuntimeError):
    """Raised when a message could not be delivered to Slack. Never contains the URL."""


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


def tornado_pct(props: dict) -> int | None:
    """Tornado probability (percent) of a feature, or None if it is not a probability area.

    DN normally holds the percent. LABEL is used as a fallback ("0.05", "5" or "5%").
    Hatched intensity areas (labels such as "CIG1" or "SIGN") return None.
    """
    dn = props.get("DN")
    if isinstance(dn, (int, float)) and not isinstance(dn, bool) and dn in TORNADO_LEVELS:
        return int(dn)
    text = str(props.get("LABEL", "")).strip().rstrip("%")
    try:
        value = float(text)
    except ValueError:
        return None
    pct = round(value * 100) if value < 1 else round(value)
    return pct if pct in TORNADO_LEVELS else None


_CIG_RE = re.compile(r"CIG\s*([1-3])\b", re.IGNORECASE)


def cig_level(props: dict) -> int | None:
    """Tornado CIG level (1-3) named in a feature's LABEL or LABEL2, else None.

    Only the label text is trusted. DN is deliberately not used, because reporting
    the wrong intensity group is worse than reporting none.
    """
    for key in ("LABEL", "LABEL2"):
        match = _CIG_RE.search(str(props.get(key, "")))
        if match:
            return int(match.group(1))
    return None


def level_of(kind: str, props: dict):
    """The risk level a feature represents (category label, tornado %, CIG), or None."""
    if kind == CATEGORICAL:
        label = props.get("LABEL")
        return label if label in RANK else None
    if kind == CIG:
        return cig_level(props)
    return tornado_pct(props)


def rank_of(kind: str, level) -> int | None:
    """A comparable number for a level, or None for a missing or invalid value."""
    if kind == CATEGORICAL:
        return RANK.get(level) if isinstance(level, str) else None
    valid = CIG_LEVELS if kind == CIG else TORNADO_LEVELS
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


def evaluate_product(geojson: dict, kind: str, county_geometry: dict) -> Evaluation:
    """Find the highest level in an outlook file whose shaded area touches the county."""
    if not isinstance(geojson, dict) or not isinstance(geojson.get("features", []), list):
        raise DataError("not a GeoJSON FeatureCollection")
    best = None
    issue = valid = expire = None
    for feature in geojson.get("features", []):
        props = (feature or {}).get("properties") or {}
        level = level_of(kind, props)
        if level is None:
            log.debug("Ignoring feature with LABEL=%r DN=%r", props.get("LABEL"), props.get("DN"))
            continue
        # All features in one file share the same times.
        issue = issue or parse_spc_time(props.get("ISSUE"))
        valid = valid or parse_spc_time(props.get("VALID"))
        expire = expire or parse_spc_time(props.get("EXPIRE"))
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
        return self.expire.strftime("%Y-%m-%dT%H:%MZ")

    @property
    def rank(self) -> int:
        return rank_of(self.kind, self.level)

    @property
    def alertable(self) -> bool:
        """Meets the Slack threshold on its own. A CIG never does: it is reported
        only together with that day's tornado probability (decided in run())."""
        if self.kind == CATEGORICAL:
            return self.rank >= RANK[ALERT_MIN_CATEGORY]
        if self.kind == TORNADO:
            return self.level >= ALERT_MIN_TORNADO
        return False

    @property
    def ping_eligible(self) -> bool:
        if self.kind == CATEGORICAL:
            return self.day in PING_CATEGORY_DAYS and self.rank >= RANK[PING_MIN_CATEGORY]
        if self.kind == TORNADO:
            return self.level >= PING_MIN_TORNADO
        return False  # no ping rule for CIG

    def describe(self) -> str:
        if self.kind == CATEGORICAL:
            return f"Severe thunderstorms: *{CATEGORY_NAMES[self.level]}*"
        if self.kind == CIG:
            return f"Tornado intensity: *CIG{self.level}* ({CIG_DEFINITIONS[self.level]})"
        return f"Tornado: *{self.level}%* probability within 25 miles of a point"

    def short(self) -> str:
        return f"Day {self.day} {KIND_LABELS[self.kind]} {short_level(self.kind, self.level)}"


def short_level(kind: str, level) -> str:
    if kind == CATEGORICAL:
        return CATEGORY_SHORT.get(level, str(level))
    if kind == CIG:
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

    @property
    def label(self) -> str:
        return f"FIPS {self.geoid}" if self.geoid else f"{self.name}, {self.state}"


@dataclass(frozen=True)
class County:
    """A resolved county with its boundary."""
    geoid: str
    name: str  # e.g. "Montgomery County"
    state: str  # e.g. "TN"
    geometry: dict

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


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
        return "No Slack webhook URL. Set SLACK_WEBHOOK_URL or pass --webhook."
    if any(c in url for c in " \t\r\n\"'<>"):
        return "The Slack webhook URL contains spaces or quote marks; copy it again from Slack."
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


def deliver(args: argparse.Namespace, text: str, what: str) -> int:
    """Print (dry run) or send a message. Returns an exit code."""
    if args.dry_run:
        print(text)
        log.info("Dry run: %s printed, not sent", what)
        return 0
    problem = check_webhook(args.webhook)
    if problem:
        log.error("Cannot send the %s: %s", what, problem)
        return 1
    log.info("Sending the %s to %s", what, mask_webhook(args.webhook))
    try:
        post_to_slack(args.webhook, text)
    except SlackError as exc:
        log.error("Sending the %s failed: %s", what, exc)
        return 1
    log.info("The %s was sent to Slack", what)
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
    if status == "lower":
        return f"down from {previous} previously reported"
    return "previously reported"


def cig_definition_lines(entries) -> list[str]:
    """The CIG definitions block, shown when any CIG is in the message.

    Each level that applies to the county is bolded and names the outlook day(s).
    """
    days_by_level: dict[int, list[int]] = {}
    for hazard, _status, _prev in entries:
        if hazard.kind == CIG:
            days_by_level.setdefault(hazard.level, []).append(hazard.day)
    if not days_by_level:
        return []
    lines = ["", "*Tornado intensity groups (CIG)* - the strongest tornado reasonably "
                 "expected if tornadoes occur. They describe intensity, not likelihood."]
    for level in CIG_LEVELS:
        text = f"CIG{level}: {CIG_DEFINITIONS[level]}"
        if level in days_by_level:
            days = " and ".join(f"Day {d}" for d in sorted(days_by_level[level]))
            lines.append(f"- *{text}* (applies to {days})")
        else:
            lines.append(f"- {text}")
    return lines


def build_message(county_label: str, entries: list[tuple[Hazard, str, str | None]],
                  ping: bool, tz: ZoneInfo | None, failed: list[str]) -> str:
    """Build the Slack text.

    entries: (hazard, status, previous level text) for every alert-level hazard,
    where status is "new", "upgraded", "unchanged" or "lower".
    """
    headline = f"*SPC severe weather outlook alert for {county_label}*"
    lines = [f"{CHANNEL_PING} {headline}" if ping else headline,
             "Highest risk touching any part of the county:"]

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
            lines.append(f"- {hazard.describe()} [{status_text(status, previous)}]")
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


def build_test_webhook_message(county_label: str, now: datetime, tz: ZoneInfo | None) -> str:
    """A short message that confirms the webhook works. Never pings."""
    sent = fmt_utc(now)
    if tz:
        sent = f"{fmt_local(now, tz)} ({sent})"
    return "\n".join([
        f"*Test message from the SPC outlook alert script for {county_label}*",
        "If you can read this, the Slack webhook is working. This is not a weather "
        "alert; no outlook data was checked.",
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


def collect_hazards(args: argparse.Namespace, county: County, now: datetime
                    ) -> tuple[list[Hazard], list[str]]:
    """Check every outlook file. Returns (hazards found, labels of files that failed)."""
    hazards: list[Hazard] = []
    failed: list[str] = []
    issue_times: dict[int, dict[str, datetime]] = {}

    for product in PRODUCTS:
        source = product_source(args.spc_base, product.filename)
        try:
            data = fetch_json(source)
            result = evaluate_product(data, product.kind, county.geometry)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, DataError,
                KeyError, TypeError, IndexError) as exc:
            log.error("%s outlook could not be checked (%s): %s", product.label, source, exc)
            failed.append(product.label)
            continue

        if result.expire and result.expire <= now:
            log.warning("%s outlook expired at %s; SPC may be late updating it. Skipping.",
                        product.label, fmt_utc(result.expire))
            continue
        if result.issue:
            issue_times.setdefault(product.day, {})[product.kind] = result.issue

        if result.level is None:
            log.info("%s outlook: no shaded area touches %s", product.label, county.label)
            continue
        if result.expire is None:
            log.error("%s outlook: shaded area found but the file has no EXPIRE time",
                      product.label)
            failed.append(product.label)
            continue

        hazard = Hazard(product.day, product.kind, result.level,
                        result.issue, result.valid, result.expire)
        log.info("%s outlook: %s touches %s (period ending %s)", product.label,
                 short_level(product.kind, result.level), county.label, hazard.period_key)
        hazards.append(hazard)

    for day, times in issue_times.items():
        if len(set(times.values())) > 1:
            log.warning("Day %d outlook files have different issue times %s; "
                        "SPC may be part way through an update.", day,
                        {k: fmt_utc(v) for k, v in times.items()})
    return hazards, failed


def run(args: argparse.Namespace, now: datetime | None = None) -> int:
    """One complete outlook check. Returns the process exit code."""
    now = now or datetime.now(timezone.utc)
    tz = get_local_tz()

    try:
        county = resolve_county(args.county_spec, args.cache_dir, args.refresh_county)
    except DataError as exc:
        log.error("Cannot continue without the county outline: %s", exc)
        return 1
    log.info("Checking SPC outlooks for %s (FIPS %s)", county.label, county.geoid)
    if county.geoid[:2] in OUTSIDE_SPC_COVERAGE:
        log.warning("SPC outlooks cover the contiguous U.S. only; %s will never be shaded",
                    county.label)

    hazards, failed = collect_hazards(args, county, now)

    state_file, state = load_county_state(args, county)
    prune_state(state, now)
    periods = state["periods"]

    # A CIG is reported only for a day whose tornado probability is being reported.
    reported_tornado = {(h.day, h.period_key) for h in hazards
                        if h.kind == TORNADO and h.alertable}

    entries: list[tuple[Hazard, str, str | None]] = []
    changed = False
    ping_due = False
    for hazard in hazards:
        if hazard.kind == CIG:
            if (hazard.day, hazard.period_key) not in reported_tornado:
                log.info("Logged only, tornado probability below the Slack threshold: %s",
                         hazard.short())
                continue
        elif not hazard.alertable:
            log.info("Below the Slack threshold, logged only: %s", hazard.short())
            continue

        record = periods.get(hazard.period_key, {})
        prev_level = record.get(hazard.kind)
        prev_rank = rank_of(hazard.kind, prev_level)
        if prev_rank is None:
            status = "new"
        elif hazard.rank > prev_rank:
            status = "upgraded"
        elif hazard.rank < prev_rank:
            status = "lower"
        else:
            status = "unchanged"
        previous_text = short_level(hazard.kind, prev_level) if prev_rank is not None else None

        pinged_rank = rank_of(hazard.kind, record.get(f"{hazard.kind}_pinged"))
        needs_ping = hazard.ping_eligible and (pinged_rank is None or hazard.rank > pinged_rank)

        changed |= status in ("new", "upgraded")
        ping_due |= needs_ping
        entries.append((hazard, status, previous_text))
        log.info("Alert-level hazard: %s [%s]%s", hazard.short(), status,
                 "; channel ping due" if needs_ping else "")

    exit_code = 1 if failed else 0

    if not entries:
        log.info("No Slight-or-higher or 5%%-or-higher tornado risk touches %s; nothing to send",
                 county.label)
        return exit_code
    ping = ping_due and not args.no_ping
    if not changed and not ping:
        log.info("Risk has not increased since the last alert; nothing sent")
        return exit_code

    message = build_message(county.label, entries, ping, tz, failed)
    log.info("Alert created (ping=%s):\n%s", ping, message)

    if args.dry_run:
        print(message)
        log.info("Dry run: message not sent and state not saved")
        return exit_code

    if deliver(args, message, "alert") != 0:
        return 1  # the state is not saved, so the next run will try again

    # Remember the highest level reported (and pinged) for each outlook period.
    def higher(kind, old, new_rank) -> bool:
        old_rank = rank_of(kind, old)
        return old_rank is None or new_rank > old_rank

    for hazard, _status, _prev in entries:
        record = periods.setdefault(hazard.period_key, {})
        if higher(hazard.kind, record.get(hazard.kind), hazard.rank):
            record[hazard.kind] = hazard.level
        if ping and hazard.ping_eligible:
            key = f"{hazard.kind}_pinged"
            if higher(hazard.kind, record.get(key), hazard.rank):
                record[key] = hazard.level
        record["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
    state["county"] = {"fips": county.geoid, "name": county.label}
    save_state(state_file, state)
    log.debug("State saved to %s", state_file)
    return exit_code


def _county_label_best_effort(args: argparse.Namespace) -> str:
    """County name for test messages; falls back to what the user typed."""
    try:
        return resolve_county(args.county_spec, args.cache_dir, args.refresh_county).label
    except DataError as exc:
        log.warning("Could not look up the county (%s); using %s", exc, args.county_spec.label)
        return args.county_spec.label


def run_test_webhook(args: argparse.Namespace, now: datetime | None = None) -> int:
    """--test-webhook: send a short message that confirms Slack delivery."""
    now = now or datetime.now(timezone.utc)
    text = build_test_webhook_message(_county_label_best_effort(args), now, get_local_tz())
    return deliver(args, text, "test message")


def run_test_alert(args: argparse.Namespace, now: datetime | None = None) -> int:
    """--test-alert: send a sample alert in the real format, marked as a test."""
    now = now or datetime.now(timezone.utc)
    ping = args.test_ping and not args.no_ping
    text = build_test_alert(_county_label_best_effort(args), now, get_local_tz(), ping)
    return deliver(args, text, "sample alert" + (" (with channel ping)" if ping else ""))


def run_test_county(args: argparse.Namespace) -> int:
    """--test-county: show the selected county and its boundary details."""
    try:
        county = resolve_county(args.county_spec, args.cache_dir, args.refresh_county)
    except DataError as exc:
        log.error("County lookup failed: %s", exc)
        return 1
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
    print("\n".join(lines))
    log.info("County check: %s (FIPS %s) OK", county.label, county.geoid)
    return 0


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
# Command line
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(
        description="Alert a Slack channel to SPC convective outlook risks for a county "
                    "(default: Montgomery County, TN).")
    where = p.add_argument_group("county (default: Montgomery County, TN)")
    where.add_argument("--county", help="County name, e.g. 'Davidson' (needs --state; "
                                        "env SPC_COUNTY)")
    where.add_argument("--state", help="State: TN, Tennessee or 47 (env SPC_STATE)")
    where.add_argument("--fips", help="5-digit county FIPS code, e.g. 47037 "
                                      "(env SPC_COUNTY_FIPS)")

    modes = p.add_argument_group("tests and tools (only one at a time)").add_mutually_exclusive_group()
    modes.add_argument("--test-webhook", action="store_true",
                       help="Send a short test message to Slack and exit")
    modes.add_argument("--test-alert", action="store_true",
                       help="Send a sample alert marked TEST (made-up data) and exit")
    modes.add_argument("--test-county", action="store_true",
                       help="Show the selected county and its boundary, then exit")
    modes.add_argument("--list-counties", action="store_true",
                       help="List the counties in --state with their FIPS codes, then exit")
    p.add_argument("--test-ping", action="store_true",
                   help="With --test-alert, include the @channel ping")

    p.add_argument("--webhook", default=os.environ.get("SLACK_WEBHOOK_URL"),
                   help="Slack Incoming Webhook URL (default: env SLACK_WEBHOOK_URL)")
    p.add_argument("--state-file", type=Path, default=None,
                   help="Remembers what has been reported (default: "
                        "spc_county_alert_state_<FIPS>.json next to this script)")
    p.add_argument("--log-file", type=Path, default=here / "spc_county_alert.log",
                   help="Log file, rotated at 1 MB with 5 backups (default: next to this script)")
    p.add_argument("--cache-dir", type=Path, default=here,
                   help="Folder for cached county outlines (default: next to this script)")
    p.add_argument("--refresh-county", action="store_true",
                   help="Download the county outline again even if it is cached")
    p.add_argument("--spc-base", default=SPC_BASE,
                   help="URL or local folder holding the SPC outlook files (default: SPC website)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print messages instead of sending them; do not save state")
    p.add_argument("--no-ping", action="store_true", help="Never ping the channel")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args(argv)
    args.script_dir = here

    # Environment variables apply only when no county option was given on the command line.
    if args.county is None and args.state is None and args.fips is None:
        args.county = os.environ.get("SPC_COUNTY") or None
        args.state = os.environ.get("SPC_STATE") or None
        args.fips = os.environ.get("SPC_COUNTY_FIPS") or None

    if args.state is not None:
        try:
            args.state = parse_state(args.state)
        except ValueError as exc:
            p.error(str(exc))

    if args.list_counties:
        if not args.state:
            p.error("--list-counties needs --state")
    elif args.fips and (args.county or args.state):
        p.error("use either --fips or --county with --state, not both")
    elif args.county and not args.state:
        p.error("--county needs --state (for example --county Davidson --state TN)")
    elif args.state and not args.county:
        p.error("--state needs --county (or use --list-counties to see the names)")

    if args.fips:
        args.fips = args.fips.strip()
        if not re.fullmatch(r"\d{5}", args.fips) or args.fips[:2] not in STATE_BY_FIPS:
            p.error(f"--fips must be a 5-digit county FIPS code such as 47125, not {args.fips!r}")
        args.county_spec = CountySpec(geoid=args.fips)
    elif args.county:
        args.county_spec = CountySpec(name=args.county.strip(), state=args.state)
    else:
        args.county_spec = CountySpec(geoid=DEFAULT_COUNTY_FIPS)

    if args.test_ping and not args.test_alert:
        p.error("--test-ping only works with --test-alert")
    needs_webhook = not (args.test_county or args.list_counties or args.dry_run)
    if needs_webhook and not args.webhook:
        p.error("a webhook is required: set SLACK_WEBHOOK_URL or pass --webhook "
                "(or use --dry-run)")
    if args.webhook:
        args.webhook = args.webhook.strip()
    return args


def setup_logging(log_file: Path, verbose: bool) -> None:
    """Log to a rotating file and to the console."""
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
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_file, args.verbose)
    try:
        if args.test_webhook:
            return run_test_webhook(args)
        if args.test_alert:
            return run_test_alert(args)
        if args.test_county:
            return run_test_county(args)
        if args.list_counties:
            return run_list_counties(args)
        return run(args)
    except Exception:  # last resort: make sure unexpected errors reach the log
        log.exception("Unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
