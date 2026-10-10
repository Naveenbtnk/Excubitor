from app.models import Alert, Incident
from app.schemas.incidents import TriageResult
from app.schemas.knowledge import KnowledgeHit
from app.schemas.postmortems import PostMortemDraft
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


async def write_post_mortem(incident: Incident) -> PostMortemDraft:
    """Draft the whole document in one structured call.

    The narrative is part of the same reply rather than a second request: the sections and the
    write-up come from one reading of the incident, so asking twice would spend another call to
    get a story that might disagree with the fields beside it.
    """
    system = (
        "You are Excubitor, writing an incident post-mortem for an SRE team. Return concise "
        "structured JSON: a short markdown narrative, a timeline summary of what happened and "
        "in what order, the root cause, the contributing factors as a list of one-sentence "
        "items, the impact on users and systems, and concrete follow-up action items. Stay "
        "strictly with what the incident record supports; do not invent numbers, owners or "
        "dates. If something is not known from the record, say so rather than guessing."
    )
    user = (
        f"Incident: {incident.title}\nSummary: {incident.summary}\n"
        f"Root cause: {incident.root_cause}\nRecommendation: {incident.recommendation}\n"
        f"Status: {incident.status}\nAssignee: {incident.assignee or 'unassigned'}"
    )
    reply = await get_llm().chat(
        [Message(role="user", content=user)], system=system, response_model=PostMortemDraft
    )
    assert isinstance(reply, PostMortemDraft)
    return reply
