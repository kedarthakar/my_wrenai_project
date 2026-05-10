# Wren AI Data Intelligence

Ask questions about your telecom data in plain English. Claude generates SQL, runs it, and visualizes results.

## Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

### macOS / Linux

```bash
git clone https://github.com/kedarthakar/my_wrenai_project
cd my_wrenai_project

# create venv and install deps
python -m venv web/.venv
source web/.venv/bin/activate
pip install -r web/requirements.txt

# add your API key
cp web/.env.example web/.env
# edit web/.env → set ANTHROPIC_API_KEY=sk-ant-...

# load your CSV into DuckDB
python load_data.py path/to/ookla_data.csv

# start
cd web && ./start.sh
```

### Windows

```bat
git clone https://github.com/kedarthakar/my_wrenai_project
cd my_wrenai_project\web
setup.bat

:: add your API key
copy web\.env.example web\.env
:: edit web\.env → set ANTHROPIC_API_KEY=sk-ant-...

:: load CSV
cd ..
load_data.bat path\to\ookla_data.csv

:: start
cd web && start.bat
```

App opens at **http://localhost:8000**

## Example Questions

| Question | What it does |
|----------|-------------|
| Show me 5G speeds of all 3 operators in Kingdom | National median download/upload by operator |
| Where should STC invest in 5G? | Localities where STC trails Mobily or Zain |
| Show STC's 5G speed for the past 4 weeks | Weekly trend for STC |
| Compare 5G latency across operators in Riyadh | City-level latency comparison |
| Which region has the best Mobily 5G download speed? | Top region by download |

## UI Features

- **Ask** — type any data question, agent generates and runs SQL
- **▶ Run** — edit the SQL and re-run without re-asking
- **✓ Correct** — save the SQL as a confirmed example; agent reuses it for similar questions
- **Context** (`/instructions`) — define business rules and term mappings
- **History** — left sidebar shows past questions; click to re-ask

## Adding Business Context

Go to **http://localhost:8000/instructions** and define what your business terms mean. Examples:

```
"Investment focus" → localities where STC median_download_mbps < Mobily OR Zain
"Kingdom-wide" → place_type = 'country' AND place_name = 'Saudi Arabia'
"Latest period" → aggregation_period = 'MTD' ORDER BY start_date DESC LIMIT 1
```

The agent reads this before generating every SQL query.

## Project Structure

```
my_wrenai_project/
├── web/
│   ├── app.py              # FastAPI backend
│   ├── requirements.txt
│   ├── start.sh / start.bat
│   ├── setup.bat           # Windows one-time setup
│   └── templates/          # HTML UI
├── models/                 # Wren MDL schema definitions
├── queries.yml             # Saved NL→SQL examples
├── instructions.md         # Business rules for the agent
├── load_data.py            # Load CSV → data/data.duckdb
└── load_data.bat           # Windows wrapper for load_data.py
```

## Data Notes

- `data/data.duckdb` is not in git (too large) — generate with `load_data.py`
- Valid `aggregation_period` values: `Day`, `Week`, `MTD`, `Month` (case-sensitive)
- Operators: `STC`, `Mobily`, `Zain`
