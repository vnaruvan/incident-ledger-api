# routes.py

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from app.api.deps import get_actor

from app.core.database import get_db
from app.schemas.incident import IncidentLogCreate, IncidentLogRead, UpdateIncident, IncidentRawRead
from app.schemas.auth import ApiKeyCreate, ApiKeyCreated, AuditLogRead
from app.crud.crud import (
    create_incident,
    get_incident_by_id,
    search_incidents,
    update_incident,
    delete_incident_soft,
)
from app.crud.crud_auth import authenticate_api_key, require_role, create_api_key, append_audit_log, ActorContext
from app.models.auth import AuditLog
from app.security.redaction import redact_text

router = APIRouter(dependencies=[Depends(get_actor)])



def get_actor(
    db: Session = Depends(get_db),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> ActorContext:
    actor = authenticate_api_key(db, x_api_key or "")
    if not actor:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return actor

@router.get("/me")
def me(actor: ActorContext = Depends(get_actor)):
    return {"tenant_id": actor.tenant_id, "actor_id": actor.actor_id, "role": actor.role}



@router.post("/incidents", response_model=IncidentLogRead)
def create_incident_route(
    payload: IncidentLogCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"responder", "admin"})
    obj = create_incident(db, tenant_id=actor.tenant_id, incident=payload)
    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_CREATE",
        resource_type="incident",
        resource_id=str(obj.id),
        request_meta={"service": obj.service, "severity": obj.severity},
        result_ids=None,
    )
    return obj


@router.get("/incidents/{incident_id}", response_model=IncidentLogRead)
def get_incident_route(
    incident_id: int,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    include_deleted: bool = Query(default=False),
):
    # viewer/responder/admin can read non-deleted; auditor can include_deleted
    if include_deleted:
        require_role(actor, {"auditor", "admin"})
    else:
        require_role(actor, {"viewer", "responder", "auditor", "admin"})

    obj = get_incident_by_id(db, tenant_id=actor.tenant_id, incident_id=incident_id, include_deleted=include_deleted)
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_READ",
        resource_type="incident",
        resource_id=str(incident_id),
        request_meta={"include_deleted": include_deleted},
        result_ids=None,
    )
    return obj


@router.post("/incidents/{incident_id}/archive")
def archive_incident_route(
    incident_id: int,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    from app.core.storage import archive_incident, ArchiveUnavailable

    require_role(actor, {"responder", "admin"})
    obj = get_incident_by_id(db, tenant_id=actor.tenant_id, incident_id=incident_id, include_deleted=False)
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")
    # Explicit public schema excludes message_raw and internal vectors.
    snapshot = IncidentLogRead.model_validate(obj).model_dump(mode="json")
    try:
        archive = archive_incident(actor.tenant_id, incident_id, snapshot)
    except ArchiveUnavailable:
        raise HTTPException(status_code=503, detail="Incident archive unavailable") from None
    append_audit_log(
        db, actor=actor, action="INCIDENT_ARCHIVE", resource_type="incident",
        resource_id=str(incident_id), request_meta=archive, result_ids=None,
    )
    return archive


@router.get("/incidents/{incident_id}/raw", response_model=IncidentRawRead)
def get_incident_raw_route(
    incident_id: int,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    # Only responder/admin can see raw
    require_role(actor, {"responder", "admin"})

    obj = get_incident_by_id(db, tenant_id=actor.tenant_id, incident_id=incident_id, include_deleted=False)
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_READ_RAW",
        resource_type="incident",
        resource_id=str(incident_id),
        request_meta=None,
        result_ids=None,
    )
    return obj


@router.patch("/incidents/{incident_id}", response_model=IncidentLogRead)
def update_incident_route(
    incident_id: int,
    payload: UpdateIncident,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"responder", "admin"})

    obj = update_incident(db, tenant_id=actor.tenant_id, incident_id=incident_id, update=payload)
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_UPDATE",
        resource_type="incident",
        resource_id=str(incident_id),
        request_meta={"message_changed": payload.message is not None},
        result_ids=None,
    )
    return obj


@router.delete("/incidents/{incident_id}", response_model=IncidentLogRead)
def delete_incident_route(
    incident_id: int,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"admin"})

    obj = delete_incident_soft(db, tenant_id=actor.tenant_id, incident_id=incident_id, deleted_by=actor.actor_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_DELETE",
        resource_type="incident",
        resource_id=str(incident_id),
        request_meta=None,
        result_ids=None,
    )
    return obj


@router.get("/search", response_model=List[IncidentLogRead])
def search_route(
    q: str = Query(..., min_length=1),
    top_k: int = Query(default=5, ge=1, le=50),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"viewer", "responder", "auditor", "admin"})

    results = search_incidents(db, tenant_id=actor.tenant_id, query=q, top_k=top_k)

    append_audit_log(
        db,
        actor=actor,
        action="INCIDENT_SEARCH",
        resource_type="incident",
        resource_id=None,
        request_meta={"query": redact_text(q), "top_k": top_k},
        result_ids=[r.id for r in results],
    )
    return results


@router.post("/admin/api-keys", response_model=ApiKeyCreated)
def create_api_key_route(
    payload: ApiKeyCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"admin"})

    # Admin can create keys only in their own tenant
    if payload.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot create keys for another tenant")

    row, plain = create_api_key(db, tenant_id=payload.tenant_id, actor_id=payload.actor_id, role=payload.role, name=payload.name)

    append_audit_log(
        db,
        actor=actor,
        action="API_KEY_CREATE",
        resource_type="api_key",
        resource_id=str(row.id),
        request_meta={"role": row.role, "name": row.name},
        result_ids=None,
    )

    return ApiKeyCreated(
        id=row.id,
        tenant_id=row.tenant_id,
        actor_id=row.actor_id,
        role=row.role,
        name=row.name,
        api_key=plain,
    )


@router.get("/audit-logs", response_model=List[AuditLogRead])
def list_audit_logs_route(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    require_role(actor, {"auditor", "admin"})

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == actor.tenant_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return rows
