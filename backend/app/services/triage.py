from app.models import Alert, Incident
from app.schemas.incidents import TriageResult
from app.schemas.knowledge import KnowledgeHit
from app.services.llm import get_llm
from app.services.llm.base import Message


def _format_hits(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return "No matching runbooks were found."
    return "\n\n".join(f"[{hit.title} - {hit.source}]\n{hit.content}" for hit in hits)


async def triage_alert(alert: Alert, hits: list[KnowledgeHit]) -> Incident:
    system = (
        "You are Excubitor, a senior SRE copilot. Return concise structured JSON with "
        "a practical summary, likely root cause, ordered remediation steps, and confidence."
    )
    user = (
        f"Alert title: {alert.title}\n"
        f"Source: {alert.source}\nSeverity: {alert.severity}\nDetail: {alert.detail}\n\n"
        f"Relevant runbooks:\n{_format_hits(hits)}"
    )
    result = await get_llm().chat(
        [Message(role="user", content=user)], system=system, response_model=TriageResult
    )
    assert isinstance(result, TriageResult)
    return Incident(
        alert_id=alert.id,
        title=alert.title,
        summary=result.summary,
        root_cause=result.root_cause,
        recommendation="\n".join(
            f"{index + 1}. {step}" for index, step in enumerate(result.remediation_steps)
        ),
        confidence=result.confidence,
        status="triage",
    )


async def write_post_mortem(incident: Incident) -> str:
    system = (
        "You are Excubitor. Write a concise post-mortem in markdown with sections: "
        "Summary, Timeline, Root cause, Impact, Remediation, Follow-ups."
    )
    user = (
        f"Incident: {incident.title}\nSummary: {incident.summary}\n"
        f"Root cause: {incident.root_cause}\nRecommendation: {incident.recommendation}"
    )
    reply = await get_llm().chat([Message(role="user", content=user)], system=system)
    assert isinstance(reply, str)
    return reply
