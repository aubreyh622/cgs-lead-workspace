# Local integration instructions

- This checkout is the integrated local workspace. Keep the sibling cgs-lead-identification folder and the teammate GitHub repository unchanged.
- Write maintained engineering documentation, code comments and UI copy in English. Communicate with the user in Chinese. Preserve original source archives under docs/upstream/.
- Follow docs/DEVELOPMENT_PLAN.md. The user authorizes one phase at a time.
- When the authorized phase passes its checks, report the result and stop. Wait for explicit user agreement before the next phase. P0 approval does not authorize P1.
- Reuse the existing research worker, validation and qualification components. Keep changes focused.
- Research and qualification use separate SQLite files because their runs tables have different schemas.
- Separate public facts, opportunity hypotheses, research feedback and confirmed qualification evidence.
- No real provider calls are needed for P0. Do not put credentials in code, exports, tests or reports.
- Run relevant offline tests. Distinguish blocked environment checks from code failures and do not claim a live integration was verified by mocks.
