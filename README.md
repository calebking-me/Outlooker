Outlooker.py watches the NWS Storm Prediction Center’s convective outlooks for a county and posts an alert to your Slack alerts channel when the risk is new or goes up. It can also post the full output of every outlook check to a separate Slack logs channel. The default is Montgomery County, TN, and you can pick another county.

Setup: Copy outlooker.example.ini to outlooker.ini next to Outlooker.py and paste in your Slack webhook URL(s). After that, python3 Outlooker.py runs with no command-line options or environment variables. Command-line options and environment variables still work and override the file. Keep outlooker.ini private: the webhook URLs are secrets, and .gitignore keeps the file out of git.
Outlooks checked: Days 1–3 categorical risk and Days 1–2 tornado probability, with Conservative Impact Group (CIG) intensity levels when a tornado risk is present.
Alert thresholds: It alerts at Slight or higher, or at a tornado probability of 5% or more. It pings @channel for Enhanced or higher on Day 1 or 2, or for a tornado probability of 10% or more.
Message contents: Times appear in both Z and Central. Each message links to the SPC outlooks page and ends with the updated disclaimer.
Logs channel: Optional. When it is turned on, every outlook check (every normal run, not the test options) posts its full output to the logs channel, whether or not there is anything to report. Alerts still go to the alerts channel. The logs channel never pings anyone.
Repeat alerts: A state file for each county stops it from repeating alerts.
Dependencies: It uses only the Python standard library.
Testing: Built-in --test-county and --test-webhook options let you check your setup before relying on it. --test-webhook sends a test message to the alerts channel and, when it is turned on, to the logs channel.
