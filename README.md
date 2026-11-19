Outlooker.py watches the NWS Storm Prediction Center’s convective outlooks for a county and posts a Slack alert when the risk is new or goes up. The default is Montgomery County, TN, and you can pick another county.

Outlooks checked: Days 1–3 categorical risk and Days 1–2 tornado probability, with Conservative Impact Group (CIG) intensity levels when a tornado risk is present.
Alert thresholds: It alerts at Slight or higher, or at a tornado probability of 5% or more. It pings @channel for Enhanced or higher on Day 1 or 2, or for a tornado probability of 10% or more.
Message contents: Times appear in both Z and Central. Each message links to the SPC outlooks page and ends with the updated disclaimer.
Repeat alerts: A state file for each county stops it from repeating alerts.
Dependencies: It uses only the Python standard library.
Testing: Built-in --test-county and --test-webhook options let you check your setup before relying on it.
