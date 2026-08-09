# Contributing

Thanks for your interest in contributing to this project! We welcome contributions of all sizes — from bug reports and documentation improvements to feature work and code review.

## Getting Started

We follow [conventional commits](https://www.conventionalcommits.org/) in this project. Commit types include:
- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation
- `test:` — test additions or updates
- `chore:` — dependencies, config, tooling
- `refactor:` — code restructuring without behavioral change

Example: `feat: add voting comparison view` or `fix: handle missing profile data`.

## Development Setup

### Backend (Python)

Install dependencies with `uv`:

```bash
uv sync
uv run pytest -q
```

Code quality is enforced with `ruff`:

```bash
uv run ruff check .
uv run ruff format .
```

### Frontend (React/TypeScript)

Install and run the dev server:

```bash
cd ui
npm install
npm run dev
npm run build
```

Frontend code is linted with ESLint and formatted with Prettier (configured via your editor or pre-commit hooks). You should see any issues when you build or start the dev server.

## Running Tests

```bash
# Python
uv run pytest -q

# Frontend (build validation)
cd ui && npm run build
```

## Important: Cloud Infrastructure Requirement

**This project requires a Google Cloud Platform project with BigQuery access to run the full pipeline.** The application will not function without:
- A GCP project ID
- A BigQuery dataset
- A service account with BigQuery permissions
- Google Gemini API credentials
- X/Twitter API v2 bearer token

There is currently **no offline demo mode**. If you'd like to contribute but don't have cloud access, here are great entry points:

- **Frontend-only issues**: UI improvements, mobile layout fixes, new components
- **Documentation**: README, API docs, architecture guides, translations
- **Data/testing**: issue reporting, feature requests, UX feedback

See `.env.example` for all required environment variables.

## Package Naming & CLI Conventions

When adding or modifying CLI modules under `src/mk_tracking/`:

1. **Package Prefixes**:
   - `collect_*`: Data Collection & Ingestion pipeline steps (e.g. `collect_knesset`, `collect_x`).
   - `process_*`: Data Processing, vectorization, scoring, and AI summary pipeline steps (e.g. `process_embeddings`, `process_issue_scoring`, `process_bill_issues`, `process_summaries`).
   - `analyze_*`: Standalone analysis and utility tools **outside** the production pipeline (e.g. `analyze_votes`).
   - `ui_app`: FastAPI web server.

2. **Date Parameters**:
   - Do **NOT** use relative year offsets or `--years` flags.
   - Use standard `--start-date YYYY-MM-DD` (and optionally `--end-date YYYY-MM-DD`).

3. **Gemini Flag Names**:
   - Do **NOT** use generic flag names like `--eval-gemini`.
   - Use explicit, descriptive flag names (e.g. `--classify-divisiveness`, `--classify-topics`).

## Submitting a Contribution

1. Fork the repository and create a feature branch: `git checkout -b feat/your-feature-name`
2. Make your changes, following the code style guidelines above
3. Write or update tests for your changes
4. Commit with a conventional commit message
5. Push and open a pull request against `main`

## Code of Conduct

Please review our [Code of Conduct](CODE_OF_CONDUCT.md). We're committed to providing a welcoming and inclusive environment for all contributors.

## Community

We're part of **The Public Knowledge Workshop** (hasadna), an Israeli NGO building civic tech and government transparency tools.

- **Weekly meetups**: Mondays at Google Campus Tel Aviv (Hebrew and English welcome)
- **Email**: [info@hasadna.org.il](mailto:info@hasadna.org.il)
- **Website**: [hasadna.org.il](https://hasadna.org.il)

Questions? Don't hesitate to reach out — we'd love to meet you and discuss ideas for the project.

## License

By contributing to this project, you agree that your contributions will be licensed under the MIT License.
