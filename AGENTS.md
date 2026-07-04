# PolyWeather Agent Instructions

## Language and Communication

- Default to English for all responses.
- Directly state what you are doing, what you found, and what's next; avoid empty pleasantries.
- If the user asks to "commit and push", "deploy", or "check logs", verify locally first, then commit and push, and check GitHub Actions or online status.
- Do not bundle unrelated tasks into one conclusion; when a new direction arises, suggest the user open a new Thread under the same Project.

## Project and Thread Usage

- One Project corresponds to the PolyWeather shared codebase and long-term direction.
- Each specific task uses its own independent Thread / Chat, for example:
  - Landing page and product packaging
  - Observation data collection and SSE patch
  - Telegram push
  - Payments and membership
  - Deployment, CI, server status
- Threads under the same Project share the folder and `AGENTS.md`, but their context is separate to avoid old issues influencing new task judgments.

## Code Work Principles

- Read existing code and configuration before making changes; prefer following the project's established patterns.
- Use `rg` / `rg --files` to find files and text.
- Use `apply_patch` for manual file edits.
- Do not roll back unrelated changes made by the user or other agents.
- Only modify files directly relevant to the current task; avoid tangential refactoring.
- Add focused tests for non-trivial new logic; keep validation scope proportional to risk.

## Frontend Conventions

- The frontend lives in `frontend/`, using Next.js, React, TypeScript.
- UI changes must account for mobile responsiveness: no text overlap, button or label overflow.
- Work interfaces (charts, terminals, detail panels) should maintain information density and scannability, avoiding marketing-style decoration.
- Common validation commands:
  - `cd frontend && npm run test:business`
  - `cd frontend && npm run typecheck`
  - When necessary, start a local preview and check both desktop and mobile viewports in a browser; close the local port when done.

## Backend and Data Conventions

- Python code lives primarily in `src/`, `web/`, `tests/`.
- Observation data refreshes should follow the data source's native frequency; avoid the Web, collector, and Telegram all force-refreshing the same external source simultaneously.
- Telegram defaults to reading only from the latest cache/DB; only fall back to a refresh when there is no cached data at all.
- External source calls should consider singleflight, rate limiting, caching, and graceful degradation to avoid 502/408 errors or Supabase/disk IO pressure.
- Common validation commands:
  - `python -m ruff check .`
  - `python -m pytest`

## CI, Commits, and Deployment

- Pushing to `main` triggers `.github/workflows/ci.yml`:
  - `python-quality`
  - `frontend-quality`
  - `build-and-push`
  - `deploy`
- Before committing, run at least the validators relevant to your changes; before pushing, confirm with `git status --short`.
- After pushing, check the latest GitHub Actions run; if it fails, locate the failing job and step, then fix.
- Production smoke checks should prioritize:
  - `https://api.polyweather.top/healthz`
  - `https://polyweather.top/`
  - Relevant page or API paths

## Product Direction Notes

- PolyWeather's current focus is not selling an API.
- Core differentiators are: settlement-source priority, real-time observation sources, runway/city-level granular temperature, SSE patches, Telegram cache reading, and interpretation capability oriented toward trading/prediction markets.
- Public packaging, educational content, chart variable completeness, and paid tiers can be strengthened, but do not reposition the product as a general-purpose weather API.

## Memory Notes

- `AGENTS.md` is an explicit project-level rule file that follows the repository and Project.
- Memory is a user-account-level preference that the agent cannot enable on behalf of the user.
- It is recommended to enable Memory in Codex / ChatGPT settings and save long-term preferences, for example:
  - PolyWeather project defaults to English replies.
  - Open a separate Thread for each task.
  - After making changes, prioritize verification, commit, push, and check deployment status.
  - Do not proactively package PolyWeather as an API-selling product.
