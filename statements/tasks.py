"""
Celery background tasks for statement extraction.

extract_statement_task runs the full parser pipeline in a separate worker
process, saving transactions incrementally (page by page / chunk by chunk)
and pushing live progress events over WebSocket so the browser can show
rows appearing in real time without the HTTP request itself blocking.
"""
import logging
from celery import shared_task
from django.db import transaction as db_transaction
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Statement, Transaction
from .parsers import parse_file
from accounts.audit import log_action
from accounts.models import AuditLog

logger = logging.getLogger("statements.tasks")


def _push_progress(statement_id, data: dict):
    """Send a progress event to all browser tabs watching this statement."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"statement_{statement_id}",
        {"type": "statement.progress", "data": data},
    )


@shared_task(bind=True)
def extract_statement_task(self, statement_id: int, user_id: int | None = None):
    statement = Statement.objects.select_related("account", "account__engagement").get(id=statement_id)

    statement.extraction_status = "processing"
    statement.extraction_started_at = timezone.now()
    statement.save(update_fields=["extraction_status", "extraction_started_at"])

    _push_progress(statement_id, {
        "event": "started",
        "message": f"Extraction started for {statement.original_filename}",
    })

    try:
        rows_iter, file_type, notes = parse_file(
            statement.source_file.path, stream=True
        )
    except TypeError:
        try:
            rows, file_type, notes = parse_file(statement.source_file.path)
            rows_iter = iter(rows)
        except Exception as exc:
            _fail(statement, statement_id, exc)
            return
    except Exception as exc:
        _fail(statement, statement_id, exc)
        return

    CHUNK_SIZE = 50  # smaller chunks than before so progress feels more "live"
    total_saved = 0
    chunk = []

    def flush_chunk(chunk_list):
        nonlocal total_saved
        if not chunk_list:
            return
        with db_transaction.atomic():
            created = Transaction.objects.bulk_create(chunk_list)
        total_saved += len(created)
        _push_progress(statement_id, {
            "event": "progress",
            "rows_so_far": total_saved,
            "latest_rows": [
                {
                    "txn_date": str(t.txn_date) if t.txn_date else None,
                    "narration_raw": t.narration_raw[:60],
                    "debit": float(t.debit) if t.debit else None,
                    "credit": float(t.credit) if t.credit else None,
                    "balance": float(t.balance) if t.balance else None,
                    "balance_type": t.balance_type,
                    "quality_flag": t.quality_flag,
                }
                for t in created[-5:]  # just the latest few, for a live "ticker" feel
            ],
        })

    # try:
    #     for r in rows_iter:
    #         narration = r.get("narration_raw", "") or ""
    #         flag = r.get("quality_flag") or ("LOW_NARRATION" if len(narration.strip()) < 3 else "")
    #         chunk.append(Transaction(
    #             statement=statement,
    #             txn_date=r.get("txn_date"),
    #             value_date=r.get("value_date"),
    #             txn_time=r.get("txn_time"),
    #             narration_raw=narration,
    #             debit=r.get("debit"),
    #             credit=r.get("credit"),
    #             balance=r.get("balance"),
    #             balance_type=r.get("balance_type", "") or "",
    #             reference=r.get("reference", "") or "",
    #             txn_mode=r.get("txn_mode", "") or "",
    #             counterparty_name=r.get("counterparty_name", "") or "",
    #             source_row=r.get("source_row"),
    #             quality_flag=flag,
    #             bank_json_data=r.get("bank_json_data"),
    #         ))
    #         if len(chunk) >= CHUNK_SIZE:
    #             flush_chunk(chunk)
    #             chunk = []

    #     flush_chunk(chunk)

    try:
        rows_since_cancel_check = 0
        cancelled = False

        for r in rows_iter:
            narration = r.get("narration_raw", "") or ""
            flag = r.get("quality_flag") or ("LOW_NARRATION" if len(narration.strip()) < 3 else "")
            chunk.append(Transaction(
                statement=statement,
                txn_date=r.get("txn_date"),
                value_date=r.get("value_date"),
                txn_time=r.get("txn_time"),
                narration_raw=narration,
                debit=r.get("debit"),
                credit=r.get("credit"),
                balance=r.get("balance"),
                balance_type=r.get("balance_type", "") or "",
                reference=r.get("reference", "") or "",
                txn_mode=r.get("txn_mode", "") or "",
                counterparty_name=r.get("counterparty_name", "") or "",
                source_row=r.get("source_row"),
                quality_flag=flag,
                bank_json_data=r.get("bank_json_data"),
            ))
            rows_since_cancel_check += 1

            if len(chunk) >= CHUNK_SIZE:
                flush_chunk(chunk)
                chunk = []

            if rows_since_cancel_check >= CHUNK_SIZE:
                rows_since_cancel_check = 0
                statement.refresh_from_db(fields=["cancel_requested"])
                if statement.cancel_requested:
                    cancelled = True
                    break

        flush_chunk(chunk)

        if cancelled:
            statement.extraction_status = "cancelled"
            statement.notes = f"Extraction cancelled by user. {total_saved} rows were saved before cancellation."
            statement.save(update_fields=["extraction_status", "notes"])
            _push_progress(statement_id, {
                "event": "cancelled",
                "rows_total": total_saved,
                "message": f"Extraction cancelled. {total_saved} rows were saved.",
            })
            return

        statement.file_type = file_type
        statement.rows_extracted = total_saved
        statement.notes = notes
        statement.extraction_status = "completed"
        statement.save(update_fields=["file_type", "rows_extracted", "notes", "extraction_status"])

        if user_id:
            from accounts.models import User
            user = User.objects.filter(id=user_id).first()
            if user:
                log_action(None, action="upload", user=user, engagement=statement.account.engagement,
                           detail=f"Uploaded '{statement.original_filename}' ({total_saved} transactions) "
                                  f"to account {statement.account}")

        _push_progress(statement_id, {
            "event": "completed",
            "rows_total": total_saved,
            "notes": notes,
            "message": f"Extracted {total_saved} transactions from {statement.original_filename}.",
        })

    except Exception as exc:
        logger.exception(f"Extraction failed for statement {statement_id}")
        _fail(statement, statement_id, exc)

@shared_task
def cleanup_cancelled_extraction(statement_id: int):
    """
    Runs separately from the main extraction task, triggered right after a
    hard-kill cancel. Since SIGKILL gives the running task zero chance to
    clean up after itself, this task does that cleanup instead: deletes any
    partially-saved transactions and finalizes the statement's status.
    """
    try:
        statement = Statement.objects.get(id=statement_id)
    except Statement.DoesNotExist:
        return

    deleted_count, _ = Transaction.objects.filter(statement=statement).delete()

    statement.extraction_status = "cancelled"
    statement.rows_extracted = 0
    statement.cancel_requested = False  # reset, in case of re-upload reuse
    statement.notes = f"Extraction cancelled by user. {deleted_count} partially-extracted rows were deleted."
    statement.save(update_fields=["extraction_status", "rows_extracted", "cancel_requested", "notes"])

    _push_progress(statement_id, {
        "event": "cancelled",
        "rows_total": 0,
        "message": f"Extraction cancelled. {deleted_count} partial rows were removed.",
    })

    logger.info(f"cleanup_cancelled_extraction: statement {statement_id}, deleted {deleted_count} rows")
    
@shared_task
def cleanup_stuck_extractions(stale_after_minutes: int = 30):
    """
    Periodic safety net — runs every few minutes via Celery Beat.
    Any statement stuck in 'pending' or 'processing' for longer than
    stale_after_minutes (worker crash, server restart mid-task, etc.)
    gets marked 'failed' so it doesn't show a spinner forever.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(minutes=stale_after_minutes)

    stuck = Statement.objects.filter(
        extraction_status__in=["pending", "processing"],
        uploaded_at__lt=cutoff,
    )

    count = 0
    for s in stuck:
        s.extraction_status = "failed"
        s.notes = (s.notes or "") + " [Auto-marked failed: extraction exceeded timeout, likely worker crash.]"
        s.save(update_fields=["extraction_status", "notes"])
        _push_progress(s.id, {
            "event": "failed",
            "message": "Extraction timed out and was marked as failed. Please re-upload.",
        })
        count += 1

    logger.info(f"cleanup_stuck_extractions: marked {count} stale statements as failed")
    return count

def _fail(statement, statement_id, exc):
    statement.extraction_status = "failed"
    statement.notes = f"Parser error: {exc}"
    statement.save(update_fields=["extraction_status", "notes"])
    _push_progress(statement_id, {
        "event": "failed",
        "message": f"Extraction failed: {exc}",
    })