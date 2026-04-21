# Memoflow Conversation Backend

Minimal FastAPI backend extracted from Flick's AI ReAct conversation stack.

This foundation intentionally keeps only:

- Streaming `/api/chat`
- In-process session CRUD for normal multi-turn chat
- DeepSeek, Kimi, and Ollama provider adapters
- ReAct tool loop with a minimal safe `current_time` tool
- Health, model, info, and tool listing endpoints

It intentionally excludes long-term memory, memory extraction, memory persistence, summarization, vector retrieval, device control, Telegram, MiHome, Home Assistant, email, and web scraping logic.

Memory v0.1 is now being added behind explicit runtime modules. Current implemented scope:

- durable episode storage
- inspectable short-term state
- task-specialized harness config
- conservative ADD-only memory atom ingestion
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
```
