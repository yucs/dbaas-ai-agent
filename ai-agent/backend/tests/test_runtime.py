from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.runtime import (  # noqa: E402
    AgentApprovalRequest,
    AgentRunOutput,
    DeepAgentRuntime,
)
from dbass_ai_agent.identity.models import Identity  # noqa: E402
from dbass_ai_agent.operations.models import OperationRecord, OperationResult, OperationTarget  # noqa: E402
from dbass_ai_agent.sessions.models import ApprovalRecord, ChatMessage, SessionMeta  # noqa: E402


class DeepAgentRuntimeDbaasTests(unittest.TestCase):
    def test_dbaas_message_invokes_real_runtime_path(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        calls: list[tuple[str, str]] = []

        def invoke_agent(thread_id: str, prompt: str) -> str:
            calls.append((thread_id, prompt))
            return "已查询 dbaas-server 并返回结果"

        runtime._invoke_agent = invoke_agent
        session = _session_meta()
        user_message = _user_message("请查询 mysql-xf2 当前 CPU 和内存状态")

        reply = runtime.generate_reply(
            identity=_identity(),
            session=session,
            user_message=user_message,
        )

        self.assertEqual(reply.content, "已查询 dbaas-server 并返回结果")
        self.assertIsNone(reply.warning)
        self.assertEqual(calls, [(session.thread_id, user_message.content)])

    def test_dbaas_stream_message_uses_stream_path_without_mock_warning(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        calls: list[tuple[str, str]] = []

        def stream_agent_text(thread_id: str, prompt: str):
            calls.append((thread_id, prompt))
            yield "已通过 DBAAS 工具"
            yield "完成查询"

        runtime._stream_agent_text = stream_agent_text
        session = _session_meta()
        user_message = _user_message("扩容 mysql-xf2 到 16C64G 前先检查集群状态")

        events = list(
            runtime.stream_reply(
                identity=_identity(),
                session=session,
                user_message=user_message,
            )
        )

        self.assertEqual([event.event for event in events], ["started", "token", "token", "completed"])
        self.assertTrue(all(event.warning is None for event in events))
        self.assertEqual(events[-1].content, "已通过 DBAAS 工具完成查询")
        self.assertEqual(calls, [(session.thread_id, user_message.content)])

    def test_resume_nested_approval_returns_next_approval_request(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        runtime.artifacts = SimpleNamespace(agent=FakeNestedApprovalAgent())

        def normalize_run_output(_result):
            return AgentRunOutput(
                approval_request=AgentApprovalRequest(
                    action_requests=[{"name": "update_service_storage_tool", "args": {}}],
                    review_configs=[{"allowed_decisions": ["approve", "reject"]}],
                    tool_call_ids=["call_nested"],
                )
            )

        runtime._normalize_run_output = normalize_run_output
        operation = _operation_record()

        reply = runtime.resume_approval(
            identity=_identity(),
            session=_session_meta(),
            approval=_approval_record(),
            decision="approved",
            operation_service=FakeOperationService(operation),
            task_service=object(),
        )

        self.assertTrue(reply.paused)
        self.assertIsNotNone(reply.approval_request)
        self.assertEqual(reply.approval_request.tool_call_ids, ["call_nested"])
        self.assertEqual(reply.warning, None)
        self.assertEqual(reply.content, "")

    def test_extract_approval_request_preserves_batch_action_requests(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        result = {
            "__interrupt__": [
                SimpleNamespace(
                    value={
                        "action_requests": [
                            {
                                "name": "create_service_image_upgrade_task_tool",
                                "args": {
                                    "service_name": "analytics",
                                    "child_service_type": "clickhouse",
                                    "image": "clickhouse:24.4.2",
                                },
                            },
                            {
                                "name": "create_service_image_upgrade_task_tool",
                                "args": {
                                    "service_name": "analytics",
                                    "child_service_type": "keeper",
                                    "image": "keeper:24.5.1",
                                },
                            },
                        ],
                        "review_configs": [
                            {"allowed_decisions": ["approve", "reject"]},
                            {"allowed_decisions": ["approve", "reject"]},
                        ],
                    }
                )
            ],
            "messages": [
                SimpleNamespace(
                    tool_calls=[
                        {
                            "id": "call_clickhouse",
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "analytics",
                                "child_service_type": "clickhouse",
                                "image": "clickhouse:24.4.2",
                            },
                        },
                        {
                            "id": "call_keeper",
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "analytics",
                                "child_service_type": "keeper",
                                "image": "keeper:24.5.1",
                            },
                        },
                    ]
                )
            ],
        }

        output = runtime._normalize_run_output(result)

        self.assertIsNotNone(output.approval_request)
        self.assertEqual(len(output.approval_request.action_requests), 2)
        self.assertEqual(output.approval_request.tool_call_ids, ["call_clickhouse", "call_keeper"])


class FakeNestedApprovalAgent:
    def invoke(self, payload, *, config):
        return {"payload": payload, "config": config}


class FakeOperationService:
    def __init__(self, operation: OperationRecord) -> None:
        self.operation = operation

    def find_by_approval(self, session: SessionMeta, approval_id: str) -> OperationRecord:
        return self.operation


def _session_meta() -> SessionMeta:
    now = datetime.now(UTC)
    return SessionMeta(
        session_id="sess_test",
        user_id="admin",
        role="admin",
        thread_id="thread_test",
        title="runtime test",
        created_at=now,
        updated_at=now,
    )


def _user_message(content: str) -> ChatMessage:
    return ChatMessage(
        message_id="msg_test",
        role="user",
        content=content,
        created_at=datetime.now(UTC),
    )


def _identity() -> Identity:
    return Identity(user_id="admin", role="admin", user=None)


def _approval_record() -> ApprovalRecord:
    now = datetime.now(UTC)
    return ApprovalRecord(
        approval_id="appr_test",
        status="approved",
        action="service.resource.update",
        session_id="sess_test",
        thread_id="thread_test",
        run_id="run_approval",
        request_message_id="msg_request",
        created_at=now,
        decided_at=now,
        decided_by="admin",
    )


def _operation_record() -> OperationRecord:
    now = datetime.now(UTC)
    target = OperationTarget(
        kind="service",
        id="mysql-xf2",
        name="mysql-xf2",
        qualifiers={"child_service_type": "mysql"},
    )
    return OperationRecord(
        operation_id="op_test",
        approval_id="appr_test",
        session_id="sess_test",
        thread_id="thread_test",
        run_id="run_operation",
        tool_call_id="call_test",
        action="service.resource.update",
        execution_mode="sync",
        status="succeeded",
        result=OperationResult(
            operation_id="op_test",
            approval_id="appr_test",
            action="service.resource.update",
            targets=[target],
            execution_mode="sync",
            status="succeeded",
            summary="已更新 mysql-xf2/mysql 的资源规格。",
        ),
        created_at=now,
        started_at=now,
        completed_at=now,
    )


if __name__ == "__main__":
    unittest.main()
