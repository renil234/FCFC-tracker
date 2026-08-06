name: AFL Team Line-ups

on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - "scripts/scrape_afl_lineups.py"
      - "afl_lineups.py"
      - ".github/workflows/refresh_lineups.yml"
  schedule:
    # Perth is UTC+8 year-round.
    # Thursday: 4:25, 4:35 and 4:45 pm Perth.
    - cron: "25,35,45 8 * * 4"
    # Friday: 4:25 and 4:35 pm Perth for final Sunday teams.
    - cron: "25,35 8 * * 5"

permissions:
  contents: write

concurrency:
  group: afl-team-lineups
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install collector dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests==2.32.4 beautifulsoup4==4.13.4 playwright==1.55.0
          python -m playwright install --with-deps chromium

      - name: Collect and validate AFL team line-ups
        run: python scripts/scrape_afl_lineups.py --season 2026

      - name: Upload browser diagnostics
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: afl-team-lineups-${{ github.run_id }}
          path: artifacts/lineups/
          if-no-files-found: ignore
          retention-days: 14

      - name: Commit validated line-up file
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/lineups_latest.json data/lineups/
          if git diff --cached --quiet; then
            echo "No validated line-up changes to commit."
            exit 0
          fi
          ROUND=$(python - <<'PY'
          import json
          from pathlib import Path
          data = json.loads(Path('data/lineups_latest.json').read_text())
          print(data.get('round_number') or 'latest')
          PY
          )
          git commit -m "Update AFL team line-ups for round ${ROUND}"
          git push
