# How-to: use function calling

Drive tools from an agent client through the proxy. The backend implements OpenAI function calling natively, so there is nothing to configure on the proxy side.

## 1. Send tools with the request

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hy3",
    "messages": [{"role": "user", "content": "What is 12 * 30?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "calculator",
        "description": "Multiply two numbers",
        "parameters": {
          "type": "object",
          "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
          "required": ["a", "b"]
        }
      }
    }]
  }'
```

## 2. Execute what comes back

When the model wants a tool, the response carries `finish_reason: "tool_calls"` and `choices[0].message.tool_calls` with the function name and JSON arguments. Run the tool in your client.

## 3. Return the result

Append the assistant message with its `tool_calls`, then a message with `role: "tool"`, the matching `tool_call_id`, and the result as content. Send the conversation again. The model answers in plain text.

## Loop rules

- the sequence repeats for multi-step jobs: the model can request tools again in its reply
- streaming works the same way; tool-call argument fragments arrive as deltas on `choices[].delta.tool_calls`
- unknown model IDs and malformed `tools` are rejected by the backend with error 11102; model ID casing does not matter (the proxy folds it to lowercase), but the spelling must match a catalog ID

If a tool loop worked yesterday and fails today, the backend protocol may have moved; see [the protocol explanation](../explanation/protocol.md).
