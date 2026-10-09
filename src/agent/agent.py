"""Claude on Amazon Bedrock (Converse API) with tool use. Returns a validated, cited assessment."""
import time

from botocore.exceptions import ClientError

from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.agent.tools import TOOL_SPECS, ToolRunner, validate_assessment
from src.common.config import get_logger

log = get_logger(__name__)
THROTTLE_CODES = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


class AssessmentError(RuntimeError):
    pass


class ChangeRiskAgent:
    def __init__(self, bedrock, model_id: str, runner: ToolRunner, max_turns: int = 10,
                 max_submit_attempts: int = 3, backoff: float = 1.5):
        self.bedrock, self.model_id, self.runner = bedrock, model_id, runner
        self.max_turns, self.max_submit_attempts, self.backoff = max_turns, max_submit_attempts, backoff

    def _converse(self, messages: list) -> dict:
        for attempt in range(6):
            try:
                return self.bedrock.converse(
                    modelId=self.model_id,
                    system=[{"text": SYSTEM_PROMPT}],
                    messages=messages,
                    toolConfig={"tools": TOOL_SPECS, "toolChoice": {"any": {}}},
                    inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in THROTTLE_CODES or attempt == 5:
                    raise
                time.sleep(self.backoff ** attempt)
        raise RuntimeError("unreachable")

    def assess(self, change_number: str) -> dict:
        messages = [{"role": "user", "content": [{"text": (
            f"Assess upcoming change {change_number}. Investigate with the tools, then call submit_assessment.")}]}]
        submit_failures, tool_calls = 0, []

        for _ in range(self.max_turns):
            resp = self._converse(messages)
            message = resp["output"]["message"]
            messages.append(message)
            results = []
            for block in message["content"]:
                use = block.get("toolUse")
                if not use:
                    continue
                name, args, tool_id = use["name"], use["input"], use["toolUseId"]
                tool_calls.append(name)

                if name == "submit_assessment":
                    problems = validate_assessment(args, self.runner.seen_ids, change_number)
                    if not problems:
                        return {"change": change_number, **args, "prompt_version": PROMPT_VERSION,
                                "model_id": self.model_id, "tool_calls": tool_calls}
                    submit_failures += 1
                    if submit_failures >= self.max_submit_attempts:
                        raise AssessmentError(f"assessment rejected {submit_failures} times: {problems}")
                    results.append({"toolResult": {"toolUseId": tool_id, "status": "error", "content": [
                        {"text": "Assessment rejected: " + "; ".join(problems) + ". Fix and resubmit."}]}})
                    continue

                try:
                    output = self.runner.run(name, args)
                    status = "error" if "error" in output else "success"
                except Exception as exc:  # tool failure is reported to the model, not fatal
                    log.warning("tool failed", extra={"ctx": {"tool": name, "error": str(exc)}})
                    output, status = {"error": str(exc)}, "error"
                results.append({"toolResult": {"toolUseId": tool_id, "status": status,
                                               "content": [{"json": output}]}})
            if not results:
                messages.append({"role": "user", "content": [{"text": "Call a tool. Finish with submit_assessment."}]})
            else:
                messages.append({"role": "user", "content": results})
        raise AssessmentError(f"no valid assessment after {self.max_turns} turns")
