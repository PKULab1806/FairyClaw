from fairyclaw.infrastructure.llm.client import OpenAICompatibleLLMClient


def _client() -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient.__new__(OpenAICompatibleLLMClient)


def test_parse_chat_completion_result_with_tool_calls() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "content": "ok",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "function": {"name": "run_command", "arguments": '{"command":"pwd"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    result = _client()._parse_chat_completion_result(data)
    assert result.text == "ok"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "tc_1"
    assert result.tool_calls[0].name == "run_command"
    assert result.tool_calls[0].arguments_json == '{"command":"pwd"}'
    assert result.finish_reason == "tool_calls"
    assert result.prompt_tokens == 11


def test_parse_responses_result_with_function_call() -> None:
    data = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
            {"type": "function_call", "call_id": "resp_tc_1", "name": "search_web", "arguments": '{"q":"x"}'},
        ],
        "usage": {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
    }
    result = _client()._parse_responses_result(data)
    assert result.text == "done"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "resp_tc_1"
    assert result.tool_calls[0].name == "search_web"
    assert result.tool_calls[0].arguments_json == '{"q":"x"}'
    assert result.finish_reason == "tool_calls"
    assert result.completion_tokens == 3


def test_parse_responses_result_maps_incomplete_length() -> None:
    data = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [
            {"type": "function_call", "call_id": "resp_tc_2", "name": "run_command", "arguments": '{"command":"pwd"'}
        ],
    }
    result = _client()._parse_responses_result(data)
    assert result.finish_reason == "length"
