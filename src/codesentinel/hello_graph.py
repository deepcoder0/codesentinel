"""
Hello World LangGraph — verifies that LangGraph + Ollama are working.

This is the CS-001 acceptance test: a minimal 2-node graph that calls Ollama
and returns a response. If this runs, your dev environment is correctly set up.

Usage:
    python -m codesentinel.hello_graph
"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from codesentinel.config import OLLAMA_HOST, OLLAMA_MODEL

log = structlog.get_logger()


class HelloState(TypedDict):
    """Minimal state for the hello world graph."""

    question: str
    answer: str


def ask_ollama(state: HelloState) -> dict:
    """Node that sends a question to Ollama and returns the answer."""
    log.info("ask_ollama_start", model=OLLAMA_MODEL)

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=0.1,
    )

    messages = [
        SystemMessage(content="You are a helpful assistant. Reply in one sentence."),
        HumanMessage(content=state["question"]),
    ]

    response = llm.invoke(messages)
    answer = response.content

    log.info("ask_ollama_done", answer_length=len(answer))
    return {"answer": answer}


def build_hello_graph() -> StateGraph:
    """Build a minimal 2-node LangGraph graph."""
    graph = StateGraph(HelloState)

    graph.add_node("ask", ask_ollama)

    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)

    return graph.compile()


def main() -> None:
    """Run the hello world graph."""
    print("=" * 60)
    print("🧪 CodeSentinel — LangGraph + Ollama Verification")
    print("=" * 60)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Ollama: {OLLAMA_HOST}")
    print()

    graph = build_hello_graph()

    result = graph.invoke({
        "question": "What is a code review in one sentence?",
        "answer": "",
    })

    print(f"Question: What is a code review in one sentence?")
    print(f"Answer:   {result['answer']}")
    print()
    print("✅ LangGraph + Ollama working! Dev environment is ready.")
    print("=" * 60)


if __name__ == "__main__":
    main()
