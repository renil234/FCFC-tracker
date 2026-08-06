# FCFC Squad Optimiser — AFL Tables build

This build uses **AFL Tables** as the primary source for player game-by-game statistics. The official AFL team-lineups source remains the eligibility source for the suggested team.

## Squad change

- Removed Bayley Fritsch (Melbourne)
- Added Ben Miller (Richmond)

Ben Miller appears once in the embedded roster.

## Refresh

Use **Refresh AFL Tables statistics** in the sidebar. AFL Tables publishes the basic and advanced line items on the same player page, so there is no separate advanced-statistics pass.

A failed player request preserves any previously cached records for that player. The roster remains visible even when the refresh has not run.

## Suggested team

The optimiser applies the FCFC role scoring, weights the latest three games 20% / 30% / 50%, applies a conservative next-opponent factor, and filters the final team to players confirmed in the official AFL team announcements.

## Deployment

- Main file: `app.py`
- Python: `3.12`
- Upload all files and hidden folders to the repository root.
