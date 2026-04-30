import pytest

from agent_memory.recall_policy import should_recall


@pytest.mark.parametrize(
    "message, expected_trigger",
    [
        ("What did we decide about storing embeddings?", "previous_decision"),
        ("Use the same approach as in the previous implementation.", "earlier_work"),
        ("Continue from where we stopped on the parser refactor.", "earlier_work"),
        ("What are my naming preferences for this repo?", "preferences"),
        ("In this codebase, how do we handle lifecycle status?", "project_specific"),
        ("Where did we leave off on Stage 9?", "project_specific"),
        ("Summarize the project history for agent-memory.", "history_or_status"),
        ("What's the status of this migration task?", "history_or_status"),
    ],
)
def test_recall_policy_avoids_false_negatives(message, expected_trigger):
    decision = should_recall(message)

    assert decision.should_recall is True
    assert decision.confidence >= 0.6
    assert expected_trigger in {trigger.name for trigger in decision.triggers}


@pytest.mark.parametrize(
    "message",
    [
        "Write a Python function that reverses a list.",
        "Run git status.",
        "Explain what a binary search tree is.",
        "Create a new React project called dashboard.",
        "Run tests in this repo.",
        "Format this JSON document.",
        "What's next in the Fibonacci sequence?",
        "What is the HTTP status code for not found?",
        "What is the capital of France?",
    ],
)
def test_recall_policy_avoids_false_positives(message):
    decision = should_recall(message)

    assert decision.should_recall is False
    assert decision.confidence < 0.6
    assert decision.triggers == ()


@pytest.mark.parametrize(
    "message, query",
    [
        ("Toby, сохрани это решение", "сохрани это решение"),
        ("Тоби проанализируй статью и сохрани", "проанализируй статью и сохрани"),
        ("tb what did we decide about status?", "status"),
    ],
)
def test_recall_policy_treats_toby_alias_as_memory_trigger(message, query):
    decision = should_recall(message)

    assert decision.should_recall is True
    assert decision.confidence == 0.99
    assert "agent_memory_alias" in {trigger.name for trigger in decision.triggers}
    assert decision.query == query
