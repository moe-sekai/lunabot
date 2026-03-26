# Minecraft Log Listening Service for LunaBot

### Usage

1. Install dependencies (Python 3.8+ required):

```bash
pip install -r requirements.txt
```

2. Run the service with the log of the Minecraft server redirected to standard input:

```bash
./server.sh | python log_service.py --host 0.0.0.0 --port 12345
```

3. Switch the listening mode to `log` and set the url in thegroup.
