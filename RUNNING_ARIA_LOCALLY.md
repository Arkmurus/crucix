# Running ARIA on Any Computer

ARIA is a Python service — not a single `.exe` file. But you can run her on any
computer (Windows, Mac, Linux) in about 5 minutes.

## Quick Start

```bash
# 1. Get the code
git clone https://github.com/Arkmurus/crucix.git
cd crucix

# 2. Run the launcher
python scripts/aria_local_launcher.py
```

The launcher will:
- Check your Python version (3.13+ required)
- Install all dependencies automatically
- Create a `.env` file for your API keys
- Start ARIA on `http://localhost:8000`

## Manual Setup

```bash
# 1. Install Python 3.13+
#    Download from: https://www.python.org/downloads/

# 2. Install dependencies
pip install -r aria_service/requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your API keys (DeepSeek, etc.)

# 4. Run
python -m uvicorn aria_service.main:app --host 0.0.0.0 --port 8000
```

## What You Get

Once running, ARIA is fully operational:

| Endpoint | Purpose |
|---|---|
| `http://localhost:8000/health/live` | Health check |
| `http://localhost:8000/docs` | API documentation (Swagger UI) |
| `http://localhost:8000/api/aria/chat` | Chat with ARIA |
| `http://localhost:8000/api/aria/coder/status` | Coder status |

## What Works Without Any API Keys

Even without DeepSeek or any LLM provider, ARIA's autonomous coding system
works at full capacity:

- **Code understanding** — AST analysis, type inference, complexity metrics
- **Gap detection** — finds bugs and missing features
- **Code editing** — 9 fix strategies (error handling, null checks, etc.)
- **Multi-file orchestration** — coordinated edits across files
- **Refactoring** — extract method, rename, split modules
- **Self-healing** — analyses error patterns, generates fixes
- **Test generation** — writes pytest files with unit + capability tests
- **Safety guardrails** — rate limits, cost caps, circuit breakers

The only thing that needs an API key is **novel code synthesis** (writing
brand-new business logic from scratch). Everything else is 100% self-contained.

## System Requirements

- **Python**: 3.13 or higher
- **RAM**: 4GB minimum (8GB recommended for torch)
- **Disk**: 5GB for code + dependencies
- **OS**: Windows 10+, macOS 12+, or Linux (any modern distro)
- **No Docker required** — runs directly on bare metal

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  ARIA Service                    │
│                                                   │
│  ┌─────────────┐  ┌──────────────────────────┐   │
│  │ FastAPI      │  │  Autonomous Coder        │   │
│  │ (main.py)    │  │  ┌────────────────────┐  │   │
│  │             │  │  │ GapDetector        │  │   │
│  │ /chat       │  │  │ SelfCoder          │  │   │
│  │ /coder      │  │  │ Safety             │  │   │
│  │ /health     │  │  │ TestRunner         │  │   │
│  │ /dd         │  │  │ CodeUnderstanding  │  │   │
│  │ /intel      │  │  │ CodeSynthesis      │  │   │
│  └─────────────┘  │  └────────────────────┘  │   │
│                   └──────────────────────────┘   │
│                                                   │
│  ┌─────────────┐  ┌──────────────────────────┐   │
│  │ LLM Chain   │  │  State Backend           │   │
│  │ (optional)  │  │  (SQLite / Redis)        │   │
│  └─────────────┘  └──────────────────────────┘   │
└─────────────────────────────────────────────────┘
```
