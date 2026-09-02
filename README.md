# job-monitor

Job monitor script for tracking new design roles across targeted companies and sending ntfy push notifications.

## Setup

1. Install dependencies:
   ```bash
   pip install requests
   ```
2. Set your private ntfy topic:
   ```bash
   export NTFY_TOPIC="kvnk-job-monitor"
   ```
3. Run the script:
   ```bash
   python3 job_monitor.py
   ```

The first run records the current state and avoids spamming notifications. Later runs only alert on new design-role matches.

## Files

- `companies.json` contains the companies to track.
- `state.json` stores the seen job IDs.
- `job_monitor.py` fetches jobs and sends notifications.
- `test_job_monitor.py` contains regression checks for the monitor logic.
