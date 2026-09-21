# Multi Agent Chat

Ask a question, one agent answers it and a second agent tightens that answer
before it reaches the chat. Runs on a local Ollama model. The whole app is
`app.py`, about 125 lines.

## Run it

```bash
streamlit run app.py
```

Setup for Ollama and the model is in [../OLLAMA_SETUP.md](../OLLAMA_SETUP.md).

## The pipeline

```
question -> Helpful Assistant -> answer -> Editor -> improved answer
```

```python
answer_task = Task(description=question, agent=assistant, ...)
edit_task   = Task(
    description="Improve the answer you were given ...",
    agent=editor,
    context=[answer_task],     # the hand-off: the editor receives the answer
)
crew = Crew(agents=[assistant, editor], tasks=[answer_task, edit_task])
```

`context=[answer_task]` is the whole hand-off. Without it the editor would get
the question but not the answer, and would just write its own.

## Seeing both versions

`crew.kickoff()` returns the last task's output, which is the edited answer.
The first agent's raw answer comes from `result.tasks_output[0]` and is shown
under a "First answer, before editing" expander, so you can see what the editor
changed. The expander only appears when the two actually differ.

## Things worth knowing

**The `ollama/` prefix keeps this local.** Drop it and CrewAI treats the model as
OpenAI's and uses an `OPENAI_API_KEY` from your environment if one is set.

**History is passed in the task description.** Streamlit re-runs the script on
every interaction, so the conversation is kept in `st.session_state.messages`
and the last few turns are pasted into the task. That is what makes follow-up
questions work.

**Both agents share one `LLM` object.** They differ by `role`, `goal` and
`backstory`, which is enough to make them behave differently.

**Two agents means two model calls,** so answers take about twice as long as the
single agent app. That is the trade for the editing pass.

## Verified

Driven through Streamlit's own test harness: "Why run AI models locally?"
returned an edited answer in the chat with the pre-edit version available in the
expander, and no exceptions.
