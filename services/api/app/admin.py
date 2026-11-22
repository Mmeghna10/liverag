"""Live sync status (everyone) and the admin console: users, group access, pipeline health.

Group changes are written to the SOURCE database, like the company's own HR or IT tool would.
The search index is never touched here: the change reaches it through CDC, like any other.
"""

from uuid import UUID

from confluent_kafka import KafkaException, TopicPartition
from fastapi import APIRouter, HTTPException, Request, status
from psycopg.errors import ForeignKeyViolation

from app.auth import ADMIN_GROUP_ID, CurrentUserDep, log, require_admin
from app.schemas import AdminUser, GroupInfo, PipelineStatus, SyncStatus

router = APIRouter(prefix="/api/v1", tags=["admin"])
TOPIC = "liverag.changes"
CONSUMER = "liverag-indexer"


def pipeline(request: Request) -> PipelineStatus:
    with request.app.state.source_pool.connection() as conn:
        slot = conn.execute("""
            SELECT active, confirmed_flush_lsn::text,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint
            FROM pg_replication_slots WHERE slot_name = 'liverag_slot'""").fetchone()
    with request.app.state.index_pool.connection() as conn:
        chunks = conn.execute("SELECT count(*) FROM chunks_v1").fetchone()[0]
        offset = conn.execute("SELECT next_offset, updated_at FROM sink_offsets "
                              "WHERE consumer = %s", (CONSUMER,)).fetchone()
        last_change = conn.execute("SELECT max(updated_at) FROM source_state").fetchone()[0]
    try:
        topic_end = request.app.state.kafka.get_watermark_offsets(
            TopicPartition(TOPIC, 0), timeout=2)[1]
    except (KafkaException, RuntimeError):
        topic_end = None  # Redpanda down or topic missing
    waiting = (topic_end - offset[0]) if (topic_end is not None and offset) else None

    if slot is None:
        state = "off"            # no slot: CDC has never been started (run indexer.bootstrap)
    elif not slot[0]:
        state = "paused"         # slot kept, but the CDC reader is not connected
    elif waiting:
        state = "catching_up"    # reader running, indexer has unapplied messages
    else:
        state = "live"
    return PipelineStatus(
        state=state, reader_connected=bool(slot and slot[0]),
        slot_wal_bytes=slot[2] if slot else None, confirmed_lsn=slot[1] if slot else None,
        indexer_offset=offset[0] if offset else None, topic_end=topic_end,
        messages_waiting=waiting, chunks=chunks,
        last_applied_at=offset[1] if offset else None, last_change_at=last_change,
    )


@router.get("/status")
def sync_status(request: Request, user: CurrentUserDep) -> SyncStatus:
    """Small version for the status pill every signed-in user sees."""
    p = pipeline(request)
    return SyncStatus(state=p.state, messages_waiting=p.messages_waiting,
                      last_applied_at=p.last_applied_at)


@router.get("/admin/pipeline")
def admin_pipeline(request: Request, user: CurrentUserDep) -> PipelineStatus:
    require_admin(user)
    return pipeline(request)


@router.get("/admin/groups")
def groups(request: Request, user: CurrentUserDep) -> list[GroupInfo]:
    require_admin(user)
    with request.app.state.source_pool.connection() as conn:
        rows = conn.execute("SELECT id, name FROM groups ORDER BY id").fetchall()
    return [GroupInfo(id=r[0], name=r[1]) for r in rows]


@router.get("/admin/users")
def users(request: Request, user: CurrentUserDep) -> list[AdminUser]:
    """Every user with the groups the SOURCE says they have, plus what the index currently uses
    (the two differ for a fraction of a second after a change: that is CDC at work)."""
    require_admin(user)
    with request.app.state.source_pool.connection() as conn:
        people = conn.execute("""
            SELECT u.id, u.email, u.display_name, u.is_active, u.created_at,
                   coalesce(array_agg(ug.group_id ORDER BY ug.group_id)
                            FILTER (WHERE ug.group_id IS NOT NULL), '{}')
            FROM users u LEFT JOIN user_groups ug ON ug.user_id = u.id
            GROUP BY u.id ORDER BY u.email""").fetchall()
    with request.app.state.index_pool.connection() as conn:
        rows = conn.execute("SELECT user_id, group_id FROM user_groups_replica").fetchall()
    effective: dict[UUID, list[int]] = {}
    for user_id, group_id in rows:
        effective.setdefault(user_id, []).append(group_id)
    return [AdminUser(id=p[0], email=p[1], display_name=p[2], is_active=p[3], created_at=p[4],
                      groups=p[5], effective_groups=sorted(effective.get(p[0], [])))
            for p in people]


def set_membership(request: Request, admin: CurrentUserDep, user_id: UUID, group_id: int,
                   member: bool) -> None:
    require_admin(admin)
    if not member and user_id == admin.id and group_id == ADMIN_GROUP_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove your own admin access")
    with request.app.state.source_pool.connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
        if member:
            try:
                conn.execute("INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s) "
                             "ON CONFLICT DO NOTHING", (user_id, group_id))
            except ForeignKeyViolation:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group") from None
        else:
            conn.execute("DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                         (user_id, group_id))
    log.info("access: %s %s user %s %s group %s", admin.email,
             "granted" if member else "revoked", user_id, "to" if member else "from", group_id)


@router.put("/admin/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def grant(user_id: UUID, group_id: int, request: Request, user: CurrentUserDep) -> None:
    set_membership(request, user, user_id, group_id, True)


@router.delete("/admin/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(user_id: UUID, group_id: int, request: Request, user: CurrentUserDep) -> None:
    set_membership(request, user, user_id, group_id, False)
