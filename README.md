# Local Agents: CrewAI + Ollama

Two chat apps that run entirely on your machine. Ask a question, an agent
answers in the chat. No API keys, no cost per run, nothing sent to a hosted
provider.

- `single_agent/` one agent answers your question
- `multi_agent/` one agent answers, a second one tightens the answer first

Both have their own Streamlit UI and both talk to a local Ollama server running
`llama3.2:1b` (about 1.3 GB).

## Layout

```
local_agent/
  README.md            this file
  OLLAMA_SETUP.md      install Ollama, pull the model, fix common errors
  requirements.txt
  single_agent/
    app.py             the whole chat app, about 85 lines
    README.md
  multi_agent/
    app.py             the whole chat app, about 125 lines
    README.md
```

Each app is a single file that runs top to bottom, so you can read it in order
with no jumping between modules.

Each folder is self contained, so you can copy one on its own and it still runs.

## Setup

**1. Install Ollama and pull the model.** Full instructions with troubleshooting
are in [OLLAMA_SETUP.md](OLLAMA_SETUP.md). The short version:

```bash
# install Ollama from https://ollama.com/download, then:
ollama pull llama3.2:1b
ollama list          # confirm llama3.2:1b is there
```

**2. Install the Python packages.** Use a virtual environment. CrewAI pulls in a
large dependency tree and upgrades shared packages such as `pillow`, `rich` and
`opentelemetry`, which can break unrelated projects if installed globally:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS and Linux
pip install -r requirements.txt
```

## Running

Run each app from inside its own folder:

```bash
cd single_agent
streamlit run app.py
```

```bash
cd multi_agent
streamlit run app.py
```

Streamlit opens http://localhost:8501. To run both at once, give the second one
a different port:

```bash
streamlit run app.py --server.port 8502
```

## How the two differ

**Single agent.** Your question goes to one `Helpful Assistant` agent and its
answer appears in the chat. One model call, so it is the faster of the two.

**Multi agent.** The assistant answers, then an `Editor` agent rewrites that
answer to be shorter and clearer, and the chat shows the edited version. The
original is kept under an expander so you can see what changed. Two model calls,
so expect about twice the wait.

The hand-off between them is one argument:

```python
edit_task = Task(
    description="Improve the answer you were given ...",
    agent=editor,
    context=[answer_task],     # the editor receives the assistant's answer
)
```

Without `context`, the editor would answer the question itself instead of
editing.

## Follow-up questions

Streamlit re-runs the script on every interaction, so each app keeps the
conversation in `st.session_state.messages` and pastes the last few turns into
the task description. Ask "What is Python?" then "Who created it?" and the
second answer knows what "it" refers to.

## Pointing at a different model

Pull the model, then change one line at the top of the `app.py` you are using:

```bash
ollama pull llama3.2:3b
```

```python
MODEL = "llama3.2:3b"
```

`BASE_URL` sits next to it if your Ollama server is not on the default port.

## One thing to be careful about

Where each app builds its `LLM`, the model string is prefixed with `ollama/`:

```python
LLM(model="ollama/" + model, base_url=base_url)
```

That prefix is what tells CrewAI to use Ollama. **If you remove it, CrewAI treats
the model as OpenAI's**, picks up an `OPENAI_API_KEY` from your environment if
one is set, and sends your content to a hosted API. Keep the prefix.

## Speed

The model generates at a fixed rate, a few seconds per answer for
`llama3.2:1b` on this machine. No code change moves that number, so what is left is
not asking it for tokens you do not need, and not making it reload.

**Answers are capped** at `max_tokens=600`. Left uncapped a small model pads and
repeats, and you wait for text you would not read. Raise it in `app.py` if you
want longer answers.

**Keep the model loaded.** Ollama unloads an idle model after 5 minutes, and the
next question then waits a couple of seconds for it to reload before anything
happens. Pin it for half an hour:

```bash
curl http://localhost:11434/api/generate -d "{\"model\":\"llama3.2:1b\",\"prompt\":\"\",\"keep_alive\":\"30m\"}"
```

Setting `OLLAMA_KEEP_ALIVE=30m` in your environment makes that the default.

**The single agent app is the faster one.** The multi agent app makes two model
calls per question by design.

### A note on streaming

An earlier version of these apps streamed tokens into the page as they were
produced, which made them feel much quicker even though the total time was the
same. It needed a CrewAI event listener class, and these apps are deliberately
plain, so it is not here. The chat shows a spinner and then the whole answer. If
you want it back, CrewAI emits `LLMStreamChunkEvent` per token when the `LLM` is
built with `stream=True`.

### What did not work

Tuning `num_thread` looked like a 33% gain on a single run. Repeated three times
per setting the difference vanished: 35.1 tok/s default against 34.7 at four
threads and 34.9 at six, which is measurement noise. It is not worth setting,
and the first result was a reminder to repeat a benchmark before believing it.

### If you need it genuinely faster

The remaining levers are hardware and model size, not code:

- A GPU is the real answer. Ollama uses it automatically when present.
- Ask for shorter answers. Time scales almost linearly with tokens produced.
- Use the single agent app.

A *bigger* model such as `qwen2.5:3b` answers noticeably better but runs slower,
so that trade runs the other way.

## Which model, and why

`llama3.2:1b` (1.3 GB) is the default. It is small enough to run on a laptop and
answers in English without being told to.

The project started on `qwen2.5:0.5b`, which is only 397 MB. It was dropped for
one measured reason: Qwen2.5 is trained heavily on Chinese and answers a
contentless prompt like "hi" in Chinese about 10% of the time.

| Model | Size | Chinese reply to bare "hi" |
|---|---|---|
| `qwen2.5:0.5b` | 397 MB | 2 of 20 |
| `llama3.2:1b` | 1.3 GB | 0 of 20 |

There is no separate "English build" of Qwen to download. It is one multilingual
model, so re-pulling the same tag changes nothing. The fixes are an explicit
English instruction in the prompt, which both apps now carry anyway, or an
English-first model, which is the route taken here.

Either way this is a small model. It repeats itself and sometimes answers a
slightly different question than the one you asked. To improve that, pull a
bigger one and change `MODEL` at the top of the `app.py` you are using:

```bash
ollama pull llama3.2:3b
```
