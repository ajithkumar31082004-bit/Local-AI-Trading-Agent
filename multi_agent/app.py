"""Chat with two CrewAI agents running on a local Ollama model.

You ask a question, the first agent answers it, the second agent rewrites that
answer to be clearer, and the chat shows the improved version. Start it with:

    streamlit run app.py

Needs Ollama running with llama3.2:1b pulled. See ../OLLAMA_SETUP.md.
"""

import streamlit as st
from crewai import LLM, Agent, Crew, Task

MODEL = "llama3.2:1b"
BASE_URL = "http://localhost:11434"

st.set_page_config(page_title="Ask the Agents", page_icon="books")
st.title("Ask the Agents")
st.caption("One agent answers, a second one tightens the answer. Both run locally.")

if st.sidebar.button("Clear chat"):
    st.session_state.messages = []

st.sidebar.markdown("**Pipeline**\n\n1. Assistant answers\n2. Editor rewrites the answer")

# Chat history lives in session state so it survives Streamlit's re-runs.
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        # The first agent's raw answer is kept so you can see what the editor changed.
        if message.get("draft"):
            with st.expander("First answer, before editing"):
                st.markdown(message["draft"])

question = st.chat_input("Ask anything")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    st.chat_message("user").markdown(question)

    # The "ollama/" prefix is what keeps this local. Without it CrewAI treats
    # the model as OpenAI's and uses OPENAI_API_KEY if it is set.
    llm = LLM(
        model="ollama/" + MODEL,
        base_url=BASE_URL,
        temperature=0.7,
        max_tokens=600,      # a small model rambles if you let it
    )

    assistant = Agent(
        role="Helpful Assistant",
        goal="Answer the question clearly and get to the point.",
        backstory=(
            "You explain things in plain words, use concrete examples, and "
            "stop once the question is answered. You always reply in English, "
            "whatever language the question hints at."
        ),
        llm=llm,
        allow_delegation=False,
        max_iter=3,
        verbose=False,
    )

    editor = Agent(
        role="Editor",
        goal="Make the answer shorter and clearer without changing its meaning.",
        backstory=(
            "You have a low tolerance for padding. You cut empty phrases and "
            "repetition, and you edit the answer in front of you rather than "
            "writing a new one. You always write in English."
        ),
        llm=llm,
        allow_delegation=False,
        max_iter=3,
        verbose=False,
    )

    # Give the agents the last few turns so follow-up questions make sense.
    history = "\n".join(
        "%s: %s" % (m["role"], m["content"]) for m in st.session_state.messages[-7:-1]
    )
    answer_task = Task(
        description=(
            ("Earlier conversation:\n%s\n\n" % history if history else "")
            + "Answer this question: %s\n\n"
            "Answer in English. Answer directly, with no preamble." % question
        ),
        expected_output="A clear, direct answer.",
        agent=assistant,
    )

    edit_task = Task(
        description=(
            "Improve the answer you were given to this question: %s\n\n"
            "Cut repetition and filler, fix clumsy sentences, keep the meaning. "
            "Return the improved answer only, with no notes about what you "
            "changed." % question
        ),
        expected_output="The same answer, tighter and clearer.",
        agent=editor,
        context=[answer_task],   # the hand-off: the editor receives the answer
    )

    crew = Crew(agents=[assistant, editor], tasks=[answer_task, edit_task], verbose=False)

    with st.chat_message("assistant"):
        with st.spinner("Answering, then editing"):
            try:
                result = crew.kickoff()
                answer = str(result)
                # tasks_output holds one entry per task, so [0] is the first answer.
                draft = str(result.tasks_output[0]) if result.tasks_output else ""
            except Exception as exc:
                answer = (
                    "That failed: %s\n\nIs `ollama serve` running and is "
                    "`%s` pulled?" % (exc, MODEL)
                )
                draft = ""
        st.markdown(answer)
        if draft and draft.strip() != answer.strip():
            with st.expander("First answer, before editing"):
                st.markdown(draft)

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "draft": draft}
    )
