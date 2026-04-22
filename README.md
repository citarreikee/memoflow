# Memoflow Conversation Agent Backend

FastAPI backend extracted from Flick's AI ReAct conversation stack.

Current runtime provides:

- Streaming `/api/chat`
- In-process session CRUD for normal multi-turn chat
- DeepSeek, Kimi, and Ollama provider adapters
- ReAct tool loop with a minimal safe `current_time` tool
- Health, model, info, and tool listing endpoints

It does not include persistent memory, memory atom extraction, retrieval injection, graph memory, contradiction reconciliation, external queue workers, device control, Telegram, MiHome, Home Assistant, email, or web scraping logic.

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
