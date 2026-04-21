import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path("src").resolve()))
from triage.loader import load_files

OTLP_PAYLOAD = {
    "resourceSpans": [
        {
            "resource": {},
            "scopeSpans": [
                {
                    "scope": {},
                    "spans": [
                        {
                            "traceId": "t123",
                            "spanId": "s456",
                            "name": "demo-span",
                            "attributes": [
                                {"key": "turn", "value": {"intValue": "5"}},
                                {"key": "agent_id", "value": {"stringValue": "AgentA"}},
                                {
                                    "key": "action_taken",
                                    "value": {
                                        "stringValue": '{"tool_name": "move", "tool_input": {"dir": "north"}}'
                                    },
                                },
                                {"key": "action_succeeded", "value": {"boolValue": False}},
                                {"key": "failure_classification", "value": {"stringValue": "agent_error"}},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}

p = Path("spans.jsonl")
p.write_text(json.dumps(OTLP_PAYLOAD) + "\n")

result = load_files([p])
print("Errors:", result.parse_errors)
print("Events:", len(result.events))
if result.events:
    print(result.events[0].model_dump())
