# FCFC Squad Optimiser — 2026 scoring views

This build uses AFL Tables for match statistics and AFL.com.au for official team announcements.

## Changes in this build

- The season is locked to 2026.
- The optimiser uses only 2026 games. No 2025 data is blended into projections.
- The statistics area has two tabs only:
  - 2026 season averages
  - Last 4 games averages
- Only scoring statistics are shown: goals, behinds, goal score (6 × goals + behinds), disposals, marks, tackles, hit outs, frees for and Stud score.
- Stud score is calculated for each game using the FCFC formula and then averaged.

## Deployment

Use Python 3.12 and `app.py` as the Streamlit entry file.
