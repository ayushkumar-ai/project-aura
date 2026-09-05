from core.history import ConversationHistory, ConversationTurn


def test_conversation_turn():
    turn = ConversationTurn(
        user_input="Hello AURA",
        assistant_output="Hello! How can I help?",
    )

    assert turn.user_input == "Hello AURA"
    assert turn.assistant_output == "Hello! How can I help?"

def test_conversation_history():
    history = ConversationHistory(
        turns=[
            ConversationTurn(
                user_input="Hello AURA",
                assistant_output="Hello!",
            ),
            ConversationTurn(
                user_input="What can you do?",
                assistant_output="I can help you.",
            ),
        ]
    )

    assert len(history.turns) == 2
    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[1].assistant_output == "I can help you."


def test_conversation_histories_have_independent_turns():
    history_one = ConversationHistory()
    history_two = ConversationHistory()

    history_one.turns.append(
        ConversationTurn(
            user_input="Hello",
            assistant_output="Hi!",
        )
    )

    assert len(history_one.turns) == 1
    assert len(history_two.turns) == 0


def test_conversation_history_add_turn():
    history = ConversationHistory()

    history.add_turn(
        user_input="Hello AURA",
        assistant_output="Hello!",
    )

    assert len(history.turns) == 1
    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[0].assistant_output == "Hello!"
    assert history.turns[0].tool_name is None
    assert history.turns[0].tool_result is None


def test_conversation_turn_with_tool_observation():
    turn = ConversationTurn(
        user_input="calculate 5 + 5",
        assistant_output="The result is 10.",
        tool_name="calculator",
        tool_result="10",
    )

    assert turn.user_input == "calculate 5 + 5"
    assert turn.assistant_output == "The result is 10."
    assert turn.tool_name == "calculator"
    assert turn.tool_result == "10"


def test_conversation_history_add_turn_with_tool_observation():
    history = ConversationHistory()

    history.add_turn(
        user_input="calculate 5 + 5",
        assistant_output="The result is 10.",
        tool_name="calculator",
        tool_result="10",
    )

    assert len(history.turns) == 1
    assert history.turns[0].tool_name == "calculator"
    assert history.turns[0].tool_result == "10"
