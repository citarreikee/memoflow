# Memoflow Memory-Aware Agent Backend

FastAPI backend extracted from Flick's AI ReAct conversation stack and extended into the first working Memoflow memory runtime.

Current runtime provides:

- Streaming `/api/chat`
- In-process session CRUD for normal multi-turn chat
- DeepSeek, Kimi, and Ollama provider adapters
- ReAct tool loop with a minimal safe `current_time` tool
- durable episode persistence
- inspectable short-term state
- model-driven memory atom ingestion through local sidecar `qwen3:30b-a3b`
- retrieval event persistence
- memory retrieval and prompt-side context injection
- Health, model, info, and tool listing endpoints

It still excludes graph memory, contradiction reconciliation, external queue workers, device control, Telegram, MiHome, Home Assistant, email, and web scraping logic.

Memory v0.1 implemented scope:

- durable episode storage
- inspectable short-term state
- task-specialized harness config
- model-driven ADD-only memory atom ingestion
- retrieval event inspection
- pre-chat memory retrieval and context assembly
- memory inspection APIs under `/api/memory/*`

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python main.py
```

## Chat In Terminal

Keep the backend running in one terminal, then open another terminal:

```powershell
.\.venv\Scripts\Activate.ps1
python chat_cli.py --model deepseek-chat
```

Inside the chat client:

- `/exit` exits
- `/model kimi-k2.5` switches model
- `/tools off` disables ReAct tools
- `/tools on` enables ReAct tools

```powershell
curl.exe -N -X POST http://localhost:3001/api/chat `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"deepseek-chat\",\"message\":\"hello\",\"enableTools\":true}"
```

## Memory Inspection

```powershell
curl.exe http://localhost:3001/api/memory/status
curl.exe http://localhost:3001/api/memory/harnesses
curl.exe http://localhost:3001/api/memory/episodes
curl.exe http://localhost:3001/api/memory/short-term
curl.exe http://localhost:3001/api/memory/atoms
curl.exe http://localhost:3001/api/memory/retrieval-events
```
