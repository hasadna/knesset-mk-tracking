# Bill Issues Evaluator

This package populates the `mk_tracking.bill_issue` BigQuery table by evaluating unprocessed legislative bills against a predefined list of political issues. It uses Gemini's structured output generation to analyze the bill's title and summary and score it against each topic.

## Usage

You must pass the `GEMINI_API_KEY` environment variable. The script relies on Application Default Credentials (ADC) for BigQuery access. 

```bash
cd bill_issues

# Install dependencies using uv
uv sync

# Run the CLI tool
export GEMINI_API_KEY="your_api_key"
uv run bill-issues

# To test on a limited number of bills:
uv run bill-issues --limit 5
```

## Structure
- `cli.py`: Core logic for loading bills, sending structured prompt to Gemini, and saving scores back to `mk_tracking.bill_issue` via a DML MERGE query.
- `pyproject.toml`: Dependency configuration (Pydantic, `google-genai`, `google-cloud-bigquery`).
