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

## Match Centre

The Match Centre saves your submitted team and your opponent's submitted team for a selected round. Opponent players are entered using their official AFL name and club so they can be included in the next AFL.com.au statistics refresh. During or after matches, use **Refresh AFL.com.au statistics** to update the scorecard.

Interchange players are applied in INT1-INT4 order when a named starter has no match record for the selected round. A bench player's preferred role is used first; if no matching-role bench player is available, the next available interchange player is used.
