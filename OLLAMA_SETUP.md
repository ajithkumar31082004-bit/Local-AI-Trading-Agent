# Installing Ollama and pulling Llama 3.2 1B

Ollama runs language models on your own machine. Nothing is sent to an API and
there are no keys or costs. This guide covers install, pulling the model, and
the errors you are most likely to hit.

## 1. Install Ollama

### Windows

The quickest route, and the one used to verify this guide:

```powershell
winget install --id Ollama.Ollama --accept-package-+agreements --accept-source-agreements
```

It is a large download, so expect it to sit without output for several minutes.

Or install by hand:

1. Download the installer from https://ollama.com/download/windows
2. Run `OllamaSetup.exe` and click through it. No options to change.
3. Ollama adds a tray icon and registers itself to start with Windows.

The binary lands in `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`.

### macOS

```bash
brew install ollama
```

Or download the app from https://ollama.com/download/mac and drag it to
Applications.

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

## 2. Confirm it installed

Open a **new** terminal, because the installer adds Ollama to your PATH and an
already-open terminal will not see it:

```bash
ollama --version
```

You should get a version string such as `ollama version 0.5.x`. If the command
is not found on Windows, the binary lives at
`%LOCALAPPDATA%\Programs\Ollama\ollama.exe` and you can add that folder to PATH.

## 3. Start the server

On Windows and macOS the desktop app runs the server for you, so this step is
usually unnecessary. If nothing is listening, start it by hand:

```bash
ollama serve
```

That holds the terminal open. Leave it running and use a second terminal for
everything else. The server listens on `http://localhost:11434`.

## 4. Pull the model

```bash
ollama pull llama3.2:1b
```

1.3 GB, so give it a few minutes. The tag matters: `llama3.2:1b` is the 1
billion parameter version. Plain `llama3.2` pulls the 3B model, roughly 2 GB.

This project used `qwen2.5:0.5b` first, at only 397 MB. It was dropped because
Qwen is trained heavily on Chinese and answers a contentless prompt like "hi" in
Chinese roughly 10% of the time (2 of 20 measured). Llama 3.2 did it 0 of 20
times. If download size matters more to you than that, `qwen2.5:0.5b` still
works and the apps pin English in the prompt anyway.

Confirm it is there:

```bash
ollama list
```

```
NAME             ID              SIZE      MODIFIED
llama3.2:1b      baf6a787fdff    1.3 GB    2 minutes ago
```

## 5. Test the model

```bash
ollama run llama3.2:1b "Write one sentence about local AI models."
```

If you get a sentence back, everything the apps need is working. Type `/bye` to
leave the interactive prompt.

You can also check the HTTP API directly, which is what the apps actually use:

```bash
curl http://localhost:11434/api/tags
```

## 6. Install the Python packages

From the `local_agent` folder:

```bash
pip install -r requirements.txt
```

This pulls in CrewAI, Streamlit and Requests. CrewAI needs Python 3.10 or
newer.

## Troubleshooting

**`ollama: command not found`**
The terminal was open before the install. Close it and open a new one. On
Windows, check `%LOCALAPPDATA%\Programs\Ollama\` exists.

**`connection refused` on port 11434**
The server is not running. Start it with `ollama serve`, or open the Ollama
desktop app.

**`model 'llama3.2:1b' not found`**
It was never pulled, or a different tag was pulled. Run `ollama list` to see
what you actually have, then `ollama pull llama3.2:1b`.

**Port 11434 already in use**
An Ollama server is already running, which is fine. If you need a different
port, set `OLLAMA_HOST=127.0.0.1:11500` before starting it, and put the
matching URL in the app sidebar.

**`UnicodeEncodeError: 'charmap' codec can't encode character`**
Windows terminals default to the cp1252 encoding, which cannot print the emoji
that models and CrewAI's logs emit. The scripts in this project already switch
stdout to UTF-8 when run directly. If you hit it in your own script, add this
near the top:

```python
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

Or set `PYTHONUTF8=1` in your environment to fix it for every script.

**CrewAI logs are full of `CrewAIEventsBus` handler errors**
Same cp1252 cause, coming from CrewAI's own console output. Both crews take a
`verbose` argument that defaults to `False`, which keeps those logs off. Pass
`verbose=True` only if you want the agent trace and your terminal handles UTF-8.

**The first run of the day is slow, later ones are quicker**
Ollama unloads an idle model after 5 minutes, so the next call reloads it first,
costing roughly 2 seconds before any text appears. Both apps show whether the
model is resident and have a Preload button. From the terminal you can pin it
yourself:

```bash
curl http://localhost:11434/api/generate -d "{\"model\":\"llama3.2:1b\",\"prompt\":\"\",\"keep_alive\":\"30m\"}"
ollama ps      # shows what is loaded and when it expires
```

Setting `OLLAMA_KEEP_ALIVE=30m` in your environment makes it the default for
every model.

**Replies are slow**
Expected on CPU. A 0.5B model still needs to generate every token. Generation speed is set by your hardware. On this machine Ollama reports
`100% GPU`, and a short chat answer comes back in 2 to 4 seconds. A 300 word piece takes 15 to 18 seconds
through the single agent and 16 to 22 seconds through both, with streaming
putting the first words on screen after about 2 seconds. A GPU is the only thing
that materially changes the rate; Ollama uses one automatically when present. If you have the disk
space and a GPU, `qwen2.5:3b` is noticeably better at writing.

**Output quality is weak**
1B is still a small model. It repeats itself and drifts off the question
sometimes. Pulling `llama3.2:3b` or `qwen2.5:3b` improves it noticeably; change
`MODEL` at the top of the `app.py` you are using.

**Replies come back in Chinese**
That was Qwen, not a broken install. Qwen2.5 is trained heavily on Chinese and
falls back to it when a prompt carries no language signal, such as a bare "hi".
There is no separate English build to download; the fix is either an English
instruction in the prompt, which both apps now include, or an English-first
model like `llama3.2:1b`.

## Useful commands

| Command | What it does |
|---|---|
| `ollama list` | Models you have pulled |
| `ollama pull <model>` | Download a model |
| `ollama rm <model>` | Delete a model and free the disk |
| `ollama ps` | Models currently loaded in memory |
| `ollama run <model>` | Chat with a model in the terminal |
| `ollama serve` | Start the server in the foreground |
