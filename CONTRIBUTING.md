# Contributing to Herakles Daimon

Thank you for your interest in contributing.

## Development Setup

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/herakles-dev/herakles-daimon.git
   cd herakles-daimon
   ```

2. Run first-time setup:
   ```bash
   ./setup.sh
   ```

3. For frontend development with hot reload, run Next.js locally:
   ```bash
   npm install
   npm run dev    # http://localhost:3000
   ```
   Keep the Docker backend running (`docker compose up -d backend db`) so the WebSocket proxy and database are available.

4. For backend development:
   ```bash
   docker compose restart backend   # After Python changes
   docker compose logs -f backend   # Follow logs
   ```

5. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Code Style

### TypeScript (frontend)

- Strict mode is enabled (`tsconfig.json`)
- All components and hooks must be fully typed — no `any`
- Use `const` and arrow functions for components
- Colocate types in `src/lib/types.ts` unless they are local to a single file
- Run `npm run lint` before committing

### Python (backend)

- Type hints on all function signatures (`def foo(x: str) -> dict:`)
- Use `asyncpg` parameterized queries — never string interpolation for SQL
- All DB queries use `fetch_one()` / `fetch_all()` / `execute()` from `backend/db.py`
- Sanitize any external-origin text (YouTube titles, user input) via `_sanitize_for_gemini()` before injecting into Gemini sessions
- Run `pytest` before committing

### General

- Dark theme only; no light-mode styles
- Mobile landscape is the primary layout target
- Keep CLAUDE.md and the key files table up to date when adding major new files

## Running Tests

```bash
cd backend
pytest                               # All tests
pytest tests/test_music_engine.py    # Single file
pytest -v --tb=short                 # Verbose output
```

There is no frontend test suite at this time. Lint with `npm run lint`.

## Adding a Gemini Tool

Tools are the primary extension point. See the "Adding a New Gemini Tool" section in [CLAUDE.md](CLAUDE.md) for the step-by-step procedure. Key points:

1. The schema lives in `src/lib/constants.ts` (`TOOL_DECLARATIONS`)
2. The handler lives in `backend/main.py` (`_execute_server_tool`)
3. Async tools must use `_inject_text()` + clear the `_turn_complete` gate after injecting
4. Never expose credentials or database access to the browser through a new tool

## Pull Request Process

1. Make sure tests pass and lint is clean
2. Keep the PR focused — one feature or fix per PR
3. Update `CLAUDE.md` key files table if you add significant new files
4. Describe the change and the reasoning in the PR description
5. Reference any related GitHub issues

## Reporting Issues

Use [GitHub Issues](https://github.com/herakles-dev/herakles-daimon/issues).

Include:
- Steps to reproduce
- Expected behavior
- Actual behavior
- Your environment (OS, Docker version, browser)
- Relevant log output (`docker compose logs backend`)

## Using Claude Code

The `CLAUDE.md` file gives Claude Code full context about the codebase. You can use Claude Code to explore unfamiliar parts of the code, add features following existing patterns, and debug issues:

```bash
claude    # Start Claude Code — it reads CLAUDE.md automatically
```
