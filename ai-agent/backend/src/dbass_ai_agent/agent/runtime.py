from __future__ import annotations

from dataclasses import dataclass

from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.ids import new_run_id
from dbass_ai_agent.sessions.models import ChatMessage, SessionMeta

from .dbaas_guard import build_not_supported_message, looks_like_dbaas_question


@dataclass(frozen=True, slots=True)
class AgentReply:
    run_id: str
    content: str
    mode: str
    warning: str | None = None


class DemoAgentRuntime:
    def generate_reply(
        self,
        *,
        identity: Identity,
        session: SessionMeta,
        user_message: ChatMessage,
        history: list[ChatMessage],
    ) -> AgentReply:
        question = user_message.content.strip()

        if looks_like_dbaas_question(question):
            return AgentReply(
                run_id=new_run_id(),
                content=build_not_supported_message(),
                mode="demo",
                warning="mock-server-disabled",
            )

        reply = self._reply_for_general_question(identity, session, question, history)
        return AgentReply(run_id=new_run_id(), content=reply, mode="demo")

    def _reply_for_general_question(
        self,
        identity: Identity,
        session: SessionMeta,
        question: str,
        history: list[ChatMessage],
    ) -> str:
        normalized = question.lower()

        if any(keyword in normalized for keyword in {"你是谁", "who are you", "介绍一下你"}):
            return (
                "我是 dbaas 智能助手的第一阶段演示版，当前重点是多用户、多 session 和继续问答能力。"
                "现在普通问题可以直接回答，DBAAS 后台调用后续再接入。"
            )

        if "mysql" in normalized:
            return (
                "MySQL 是一种常见的开源关系型数据库管理系统，支持结构化数据存储、事务处理、索引查询和主从复制等能力。"
                "它常用于业务系统、网站和中后台服务。"
            )

        if any(keyword in normalized for keyword in {"session", "会话"}):
            return (
                f"当前你在会话 `{session.session_id}` 中继续提问，后端会复用同一个 `thread_id`，"
                "并把消息记录到当前用户目录下的本地 session 文件里。"
            )

        turns = len([message for message in history if message.role == "user"])
        return (
            "当前后端已经接好多用户、多 session 和本地会话存储，"
            f"现在处于 demo 问答模式。你是 `{identity.user_id}`，当前会话已经记录了 {turns} 轮用户发言。"
            f"关于“{question}”，下一阶段接入真实 DeepAgent 模型后可以给出更完整的回答。"
        )
