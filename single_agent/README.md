# Single Agent Chat

Ask a question, one CrewAI agent answers in the chat. Runs on a local Ollama
model. The whole app is `app.py`, about 85 lines.

## Run it

```bash
streamlit run app.py
```

Setup for Ollama and the model is in [../OLLAMA_SETUP.md](../OLLAMA_SETUP.md).

## How it works

```python
llm   = LLM(model="ollama/llama3.2:1b", base_url="http://localhost:11434")
agent = Agent(role="Helpful Assistant", goal=..., backstory=..., llm=llm)
task  = Task(description=question, expected_output="A clear, direct answer.", agent=agent)
crew  = Crew(agents=[agent], tasks=[task])

answer = str(crew.kickoff())
```

Streamlit supplies the chat: `st.chat_input` takes the question and
`st.chat_message` prints both sides. A fresh crew is built per question, which
is what CrewAI expects since a `Task` carries one instruction.

## Things worth knowing

**The `ollama/` prefix keeps this local.** Drop it and CrewAI treats the model as
OpenAI's and uses an `OPENAI_API_KEY` from your environment if one is set.

**History is passed in the task description.** Streamlit re-runs the script on
every interaction, so the conversation is kept in `st.session_state.messages`,
and the last few turns are pasted into the task. That is what makes follow-ups
work: ask "What is Python?" then "Who created it?" and the second answer knows
what "it" means.

**`max_tokens=600` and `max_iter=3`** keep a 0.5B model from rambling or talking
to itself instead of answering.

**Errors come back as a chat message** rather than a red traceback, since the
usual cause is simply that Ollama is not running.

## Verified

Driven through Streamlit's own test harness:

- "What is an LLM in one sentence?" answered correctly in the chat
- "What is Python?" then "Who created it?" returned "Guido van Rossum created
  Python", so the history really is reaching the agent
- No exceptions on either turn

Answers take 2 to 4 seconds on `llama3.2:1b`.
