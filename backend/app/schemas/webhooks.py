"""Prometheus Alertmanager's webhook payload, and what we answer with.

The models are lenient on purpose. Alertmanager owns this shape, adds fields between
versions, and we are not in a position to make it validate anything: an unrecognised key is
dropped rather than refused, so a future Alertmanager release does not silently stop our
ingestion. The fields we do read are the ones the mapping in
:mod:`app.services.alertmanager` depends on.
"""

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerAlert(BaseModel):
    """One entry of ``alerts[]``: a single firing or resolved alert instance."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: str | None = Field(default=None, alias="startsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    # Carried for debugging only. It is Alertmanager's dedupe key, not ours: see the
    # "Known limitations" note in the README for why ingestion does not use it yet.
    fingerprint: str | None = None


class AlertmanagerWebhook(BaseModel):
    """The group Alertmanager posts: shared labels, plus the alerts that share them."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: str = "4"
    status: str = "firing"
    receiver: str = ""
    group_key: str = Field(default="", alias="groupKey")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str | None = Field(default=None, alias="externalURL")
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class WebhookIngestResult(BaseModel):
    """What we created, so Alertmanager's logs and ours can be lined up."""

    accepted: int
    opened: int
    resolved: int
    alert_ids: list[int]
