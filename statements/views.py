"""
Complete views.py with extraction, deletion, and cleaning/validation views
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction as db_transaction
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, Min, Max, Q
from django.utils import timezone

from .forms import UploadForm
from .models import Statement, Transaction, Account
from .parsers import parse_file
from .tasks import extract_statement_task
from .cleaning import ValidationEngine

from accounts.audit import log_action
from accounts.models import AuditLog
from accounts.permissions import engagement_required
from celery.result import AsyncResult
from .tasks import cleanup_cancelled_extraction

import openpyxl
from datetime import datetime

# ===== EXTRACTION VIEWS =====

@login_required
def upload_view(request):
    """Upload and extract bank statements."""
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            account = form.cleaned_data["account"]
            uploaded = form.cleaned_data["file"]

            statement = Statement.objects.create(
                account=account,
                source_file=uploaded,
                original_filename=uploaded.name,
                extraction_status="pending",
            )

            task = extract_statement_task.delay(statement.id, request.user.id)
            statement.celery_task_id = task.id
            statement.save(update_fields=["celery_task_id"])

            messages.success(
                request,
                f"'{uploaded.name}' uploaded. Extraction is running in the background — "
                f"you can keep working and check progress on the transactions page."
            )
            return redirect("transactions", statement_id=statement.id)
    else:
        form = UploadForm()

    recent = Statement.objects.order_by("-uploaded_at")
    account_count = Account.objects.count()
    statement_count = Statement.objects.count()
    transaction_count = Transaction.objects.count()
    flagged_count = Transaction.objects.exclude(quality_flag="").count()
    recent_activity = AuditLog.objects.filter(user=request.user).order_by("-timestamp")[:5]

    has_active_extraction = Statement.objects.filter(
        extraction_status__in=["pending", "processing"]
    ).exists()

    return render(request, "statements/upload.html", {
        "form": form,
        "recent": recent,
        "account_count": account_count,
        "statement_count": statement_count,
        "transaction_count": transaction_count,
        "flagged_count": flagged_count,
        "recent_activity": recent_activity,
        "has_active_extraction": has_active_extraction,
    })


@login_required
@require_POST
def cancel_extraction_view(request, statement_id):
    """Cancel ongoing extraction."""
    statement = get_object_or_404(Statement, id=statement_id)

    if statement.extraction_status not in ["pending", "processing"]:
        messages.warning(request, "This extraction is not currently running.")
        return redirect("upload")

    statement.cancel_requested = True
    statement.save(update_fields=["cancel_requested"])
    if statement.celery_task_id:
        AsyncResult(statement.celery_task_id).revoke(terminate=True, signal='SIGKILL')

    cleanup_cancelled_extraction.delay(statement.id)

    log_action(request, action="upload", user=request.user,
               engagement=statement.account.engagement,
               detail=f"Cancelled extraction for '{statement.original_filename}'")

    messages.success(request, f"Extraction for '{statement.original_filename}' cancelled.")
    return redirect("upload")


@login_required
def extraction_status_api(request):
    """Lightweight polling endpoint for extraction status."""
    recent = Statement.objects.order_by("-uploaded_at")[:10]
    data = [
        {
            "id": s.id,
            "status": s.extraction_status,
            "rows_extracted": s.rows_extracted,
        }
        for s in recent
    ]
    return JsonResponse({"statements": data})


@engagement_required
def transactions_view(request, statement_id):
    """List transactions for a statement with filters."""
    statement = get_object_or_404(Statement, id=statement_id)
    txns = statement.transactions.order_by("source_row")

    log_action(request, action="transaction_view", user=request.user,
               engagement=statement.account.engagement,
               detail=f"Viewed transactions for '{statement.original_filename}'")

    q = request.GET.get("q", "").strip()
    mode = request.GET.get("mode", "").strip()
    direction = request.GET.get("direction", "").strip()
    from_date = request.GET.get("from_date", "").strip()
    to_date = request.GET.get("to_date", "").strip()

    if q:
        txns = txns.filter(Q(narration_raw__icontains=q) | Q(counterparty_name__icontains=q))
    if mode:
        txns = txns.filter(txn_mode=mode)
    if direction == "debit":
        txns = txns.filter(debit__gt=0)
    elif direction == "credit":
        txns = txns.filter(credit__gt=0)
    if from_date:
        txns = txns.filter(txn_date__gte=from_date)
    if to_date:
        txns = txns.filter(txn_date__lte=to_date)

    agg = statement.transactions.aggregate(
        total_debit=Sum("debit"), total_credit=Sum("credit"),
        date_from=Min("txn_date"), date_to=Max("txn_date"),
    )

    paginator = Paginator(txns, 100)
    txns_page = paginator.get_page(request.GET.get("page", 1))

    return render(request, "statements/transactions.html", {
        "statement": statement,
        "txns": txns_page,
        "total_debit": agg["total_debit"] or 0,
        "total_credit": agg["total_credit"] or 0,
        "date_from": agg["date_from"],
        "date_to": agg["date_to"],
    })


# ===== DELETE VIEWS =====

@login_required
@require_POST
def delete_transaction_view(request, statement_id, transaction_id):
    """Delete a single transaction."""
    statement = get_object_or_404(Statement, id=statement_id)
    transaction = get_object_or_404(Transaction, id=transaction_id, statement=statement)
    txn_desc = f"{transaction.txn_date} | {transaction.narration_raw[:50]}"
    transaction.delete()
    messages.success(request, f"Transaction deleted: {txn_desc}")
    return redirect('transactions', statement_id=statement_id)


@login_required
def confirm_delete_statement_view(request, statement_id):
    """Show confirmation page before deleting statement."""
    statement = get_object_or_404(Statement, id=statement_id)
    return render(request, 'statements/confirm_delete.html', {
        'statement': statement,
        'transaction_count': statement.transactions.count(),
    })


@login_required
@require_POST
def delete_statement_view(request, statement_id):
    """Delete entire statement and all its transactions."""
    statement = get_object_or_404(Statement, id=statement_id)
    filename = statement.original_filename
    row_count = statement.transactions.count()
    statement.delete()
    messages.success(request, f"Deleted '{filename}' and {row_count} transactions.")
    return redirect('upload')


# ===== CLEANING & VALIDATION VIEWS =====

@login_required
def cleaning_dashboard_view(request, statement_id):
    """Main cleaning & validation dashboard."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    if not statement.validation_status or statement.validation_status == 'NOT_RUN':
        engine = ValidationEngine(statement)
        issues = engine.run_all_checks()
        rating = engine.get_reliability_rating()
        
        statement.validation_status = 'PENDING_REVIEW'
        statement.validation_rating = rating
        statement.validation_issues_count = len(issues)
        statement.validation_critical_count = len([i for i in issues if i['severity'] == 'CRITICAL'])
        statement.validation_high_count = len([i for i in issues if i['severity'] == 'HIGH'])
        statement.validation_issues = issues
        statement.save()
    
    # ONLY COUNT UNRESOLVED ISSUES 
    all_issues = statement.validation_issues or []
    unresolved_issues = [i for i in all_issues if not i.get('resolved', False)]
    
    critical_issues = [i for i in unresolved_issues if i['severity'] == 'CRITICAL']
    high_issues = [i for i in unresolved_issues if i['severity'] == 'HIGH']
    medium_issues = [i for i in unresolved_issues if i['severity'] == 'MEDIUM']
    
    # Dynamically update rating based on remaining unresolved issues
    engine = ValidationEngine(statement)
    engine.issues = unresolved_issues
    rating = engine.get_reliability_rating()
    if statement.validation_rating != rating:
        statement.validation_rating = rating
        statement.save(update_fields=['validation_rating'])
    
    return render(request, 'statements/cleaning_dashboard.html', {
        'statement': statement,
        'critical_count': len(critical_issues),
        'high_count': len(high_issues),
        'medium_count': len(medium_issues),
        'rating': statement.validation_rating,
        'total_transactions': statement.transactions.count(),
    })


@login_required
def cleaning_issues_view(request, statement_id):
    """Detailed issue review page."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    severity_filter = request.GET.get('severity', '')
    resolution_filter = request.GET.get('resolution', '')
    
    issues = statement.validation_issues or []
    
    # ONLY SHOW UNRESOLVED ISSUES
    issues = [i for i in issues if not i.get('resolved', False)]
    
    if severity_filter:
        issues = [i for i in issues if i['severity'] == severity_filter]
    
    if resolution_filter == 'required':
        issues = [i for i in issues if i.get('resolution_required')]
    elif resolution_filter == 'optional':
        issues = [i for i in issues if not i.get('resolution_required')]
    
    return render(request, 'statements/cleaning_issues.html', {
        'statement': statement,
        'issues': issues,
        'severity_filter': severity_filter,
        'resolution_filter': resolution_filter,
    })


@login_required
def interactive_validation_view(request, statement_id):
    """Interactive split-screen validation issues dashboard view."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    # Handle POST requests (actions from the interactive page)
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # 1. Action: Resolve a specific issue
        if action == 'resolve_issue':
            issue_id = request.POST.get('issue_id')
            analyst_action = request.POST.get('analyst_action', 'acknowledged')
            issues = statement.validation_issues or []
            matched = False
            for issue in issues:
                if issue.get('id') == issue_id:
                    issue['analyst_action'] = analyst_action
                    issue['analyst_user_id'] = request.user.id
                    issue['resolved_at'] = timezone.now().isoformat()
                    issue['resolved'] = True
                    matched = True
                    break
            if matched:
                statement.validation_issues = issues
                # Recalculate counts
                unresolved = [i for i in issues if not i.get('resolved', False)]
                statement.validation_issues_count = len(unresolved)
                statement.validation_critical_count = sum(1 for i in unresolved if i.get('severity') == 'CRITICAL')
                statement.validation_high_count = sum(1 for i in unresolved if i.get('severity') == 'HIGH')
                
                # Recalculate rating
                engine = ValidationEngine(statement)
                engine.issues = unresolved
                rating = engine.get_reliability_rating()
                statement.validation_rating = rating
                statement.save()
                
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        'status': 'success',
                        'message': 'Issue marked as resolved ✅',
                        'issue_id': issue_id,
                        'unresolved_count': len(unresolved)
                    })
                messages.success(request, 'Issue marked as resolved ✅')
            else:
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'error', 'message': 'Issue not found'})
                messages.error(request, 'Issue not found')
            
            # Redirect back to the same view with query params
            selected_txn = request.POST.get('selected_txn', '')
            url = f"/statement/{statement_id}/clean/interactive/"
            if selected_txn:
                url += f"?selected_txn={selected_txn}"
            return redirect(url)
            
        # 2. Action: Override fields on the selected transaction
        elif action == 'update_transaction':
            txn_id = request.POST.get('transaction_id')
            transaction = get_object_or_404(Transaction, id=txn_id, statement=statement)
            field = request.POST.get('field')
            new_value = request.POST.get('value')
            
            if field == 'narration':
                original = transaction.narration_raw
                transaction.narration_raw = new_value
                transaction.save()
                statement.log_correction('narration', original, new_value, request.user)
                
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        'status': 'success',
                        'message': 'Transaction narration overridden',
                        'field': 'narration',
                        'value': new_value
                    })
                messages.success(request, 'Transaction narration overridden')
                
            elif field == 'debit':
                original = transaction.debit
                if new_value.strip() == '':
                    transaction.debit = None
                    final_val = None
                else:
                    transaction.debit = new_value
                    final_val = float(new_value)
                transaction.save()
                statement.log_correction('debit', original, new_value, request.user)
                
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({
                        'status': 'success',
                        'message': 'Transaction debit overridden',
                        'field': 'debit',
                        'value': final_val
                    })
                messages.success(request, 'Transaction debit overridden')
            
            # Redirect back to the same view with query params
            url = f"/statement/{statement_id}/clean/interactive/?selected_txn={txn_id}"
            return redirect(url)

    # GET Request: Render the page
    # Fetch all transactions ordered by source_row
    txns = statement.transactions.order_by('source_row')
    
    # Get all validation issues
    all_issues = statement.validation_issues or []
    
    # Get unresolved issues
    unresolved_issues = [i for i in all_issues if not i.get('resolved', False)]
    
    # Map transaction ID to its unresolved issues
    txn_issues_map = {}
    flagged_txn_ids = set()
    txns_by_id = {t.id: t for t in txns}
    for issue in unresolved_issues:
        t_id = issue.get('transaction_id')
        if t_id:
            flagged_txn_ids.add(t_id)
            if t_id in txns_by_id:
                issue['txn_row'] = txns_by_id[t_id].source_row
            if t_id not in txn_issues_map:
                txn_issues_map[t_id] = []
            txn_issues_map[t_id].append(issue)
            
    # Selected transaction calculation
    selected_txn_id = request.GET.get('selected_txn')
    selected_transaction = None
    if selected_txn_id:
        try:
            selected_transaction = statement.transactions.get(id=selected_txn_id)
        except (Transaction.DoesNotExist, ValueError):
            pass
            
    # Fallback default selected transaction: the first transaction that has an unresolved issue
    if not selected_transaction:
        for issue in unresolved_issues:
            t_id = issue.get('transaction_id')
            if t_id:
                try:
                    selected_transaction = statement.transactions.get(id=t_id)
                    break
                except Transaction.DoesNotExist:
                    pass
                    
    # If still no transaction has issues, default to the first transaction of the statement
    if not selected_transaction and txns.exists():
        selected_transaction = txns.first()
        
    # Get issues for the selected transaction
    selected_issues = []
    if selected_transaction:
        selected_issues = txn_issues_map.get(selected_transaction.id, [])
        
    # Extract statement-level issues (e.g. date gaps)
    statement_issues = [i for i in unresolved_issues if not i.get('transaction_id')]
        
    return render(request, 'statements/interactive_validation.html', {
        'statement': statement,
        'txns': txns,
        'flagged_txn_ids': flagged_txn_ids,
        'selected_transaction': selected_transaction,
        'selected_issues': selected_issues,
        'statement_issues': statement_issues,
        'unresolved_issues': unresolved_issues,
        'unresolved_count': len(unresolved_issues),
    })


@login_required
def cleaning_transaction_detail_view(request, statement_id, transaction_id):
    """Detailed view of a single transaction."""
    statement = get_object_or_404(Statement, id=statement_id)
    transaction = get_object_or_404(Transaction, id=transaction_id, statement=statement)
    
    issues = [i for i in statement.validation_issues if i.get('transaction_id') == transaction.id]
    
    if request.method == 'POST':
        field = request.POST.get('field')
        new_value = request.POST.get('value')
        
        if field == 'narration':
            original = transaction.narration_raw
            transaction.narration_raw = new_value
            transaction.save()
            statement.log_correction('narration', original, new_value, request.user)
            messages.success(request, f'Narration updated')
        
        elif field == 'debit':
            original = transaction.debit
            transaction.debit = new_value
            transaction.save()
            statement.log_correction('debit', original, new_value, request.user)
            messages.success(request, f'Debit updated')
        
        return redirect('cleaning_transaction_detail', statement_id=statement_id, transaction_id=transaction_id)
    
    return render(request, 'statements/cleaning_transaction.html', {
        'statement': statement,
        'transaction': transaction,
        'issues': issues,
    })

@login_required
@require_POST
def resolve_issue_view(request, statement_id, issue_id):
    """Analyst marks an issue as resolved."""
    statement = get_object_or_404(Statement, id=statement_id)
    action = request.POST.get('action')
    
    issues = statement.validation_issues or []
    for issue in issues:
        if issue.get('id') == issue_id:
            issue['analyst_action'] = action
            issue['analyst_user_id'] = request.user.id
            issue['resolved_at'] = timezone.now().isoformat()
            issue['resolved'] = True
            break
    
    statement.validation_issues = issues
    
    # RECALCULATE COUNTS
    unresolved = [i for i in issues if not i.get('resolved', False)]
    statement.validation_issues_count = len(unresolved)
    statement.validation_critical_count = sum(1 for i in unresolved if i.get('severity') == 'CRITICAL')
    statement.validation_high_count = sum(1 for i in unresolved if i.get('severity') == 'HIGH')
    statement.save()
    
    # RECALCULATE RATING
    engine = ValidationEngine(statement)
    engine.issues = unresolved
    rating = engine.get_reliability_rating()
    statement.validation_rating = rating
    statement.save()
    
    messages.success(request, f'Issue resolved ✅')
    return redirect('cleaning_issues', statement_id=statement_id)

@login_required
def export_cleaned_data_view(request, statement_id):
    """Export cleaned data to Excel."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    issues = statement.validation_issues or []
    required_unresolved = [i for i in issues if i.get('resolution_required') and not i.get('resolved')]
    
    if required_unresolved:
        messages.error(request, f'{len(required_unresolved)} issue(s) require resolution.')
        return redirect('cleaning_issues', statement_id=statement_id)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cleaned Transactions"
    
    headers = [
        'Idx', 'Date', 'Narration', 'Debit', 'Credit', 'Balance', 'Reference',
        'Mode', 'Counterparty', 'Status', 'Flag Code', 'Flag Description'
    ]
    ws.append(headers)
    
    txns = statement.transactions.order_by('source_row')
    for txn in txns:
        txn_issues = [i for i in issues if i.get('transaction_id') == txn.id]
        if txn_issues:
            status = 'Flagged'
            codes = ', '.join(i.get('code', '') for i in txn_issues)
            desc = '; '.join(i.get('message', '') for i in txn_issues)
        else:
            status = 'Clean'
            codes = ''
            desc = ''
        
        ws.append([
            txn.source_row,
            txn.txn_date,
            txn.narration_raw,
            float(txn.debit) if txn.debit else '',
            float(txn.credit) if txn.credit else '',
            float(txn.balance) if txn.balance else '',
            txn.reference,
            txn.txn_mode,
            txn.counterparty_name,
            status,
            codes,
            desc,
        ])
    
    filename = f'cleaned_{statement.id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    
    statement.last_cleaned_export_at = timezone.now()
    statement.last_cleaned_export_by = request.user
    statement.save()
    
    messages.success(request, f'Cleaned data exported')
    return response

# """
# Complete views.py with extraction, deletion, and cleaning/validation views
# """

# from django.shortcuts import render, redirect, get_object_or_404
# from django.contrib import messages
# from django.db import transaction as db_transaction
# from django.http import JsonResponse, HttpResponse
# from django.views.decorators.http import require_POST
# from django.contrib.auth.decorators import login_required
# from django.core.paginator import Paginator
# from django.db.models import Sum, Min, Max, Q
# from django.utils import timezone

# from .forms import UploadForm
# from .models import Statement, Transaction, Account
# from .parsers import parse_file
# from .tasks import extract_statement_task
# from .cleaning import ValidationEngine

# from accounts.audit import log_action
# from accounts.models import AuditLog
# from accounts.permissions import engagement_required
# from celery.result import AsyncResult
# from .tasks import cleanup_cancelled_extraction

# import openpyxl
# from datetime import datetime

# # ===== EXTRACTION VIEWS =====

# @login_required
# def upload_view(request):
#     """Upload and extract bank statements."""
#     if request.method == "POST":
#         form = UploadForm(request.POST, request.FILES)
#         if form.is_valid():
#             account = form.cleaned_data["account"]
#             uploaded = form.cleaned_data["file"]

#             statement = Statement.objects.create(
#                 account=account,
#                 source_file=uploaded,
#                 original_filename=uploaded.name,
#                 extraction_status="pending",
#             )

#             task = extract_statement_task.delay(statement.id, request.user.id)
#             statement.celery_task_id = task.id
#             statement.save(update_fields=["celery_task_id"])

#             messages.success(
#                 request,
#                 f"'{uploaded.name}' uploaded. Extraction is running in the background — "
#                 f"you can keep working and check progress on the transactions page."
#             )
#             return redirect("transactions", statement_id=statement.id)
#     else:
#         form = UploadForm()

#     recent = Statement.objects.order_by("-uploaded_at")
#     account_count = Account.objects.count()
#     statement_count = Statement.objects.count()
#     transaction_count = Transaction.objects.count()
#     flagged_count = Transaction.objects.exclude(quality_flag="").count()
#     recent_activity = AuditLog.objects.filter(user=request.user).order_by("-timestamp")[:5]

#     has_active_extraction = Statement.objects.filter(
#         extraction_status__in=["pending", "processing"]
#     ).exists()

#     return render(request, "statements/upload.html", {
#         "form": form,
#         "recent": recent,
#         "account_count": account_count,
#         "statement_count": statement_count,
#         "transaction_count": transaction_count,
#         "flagged_count": flagged_count,
#         "recent_activity": recent_activity,
#         "has_active_extraction": has_active_extraction,
#     })


# @login_required
# @require_POST
# def cancel_extraction_view(request, statement_id):
#     """Cancel ongoing extraction."""
#     statement = get_object_or_404(Statement, id=statement_id)

#     if statement.extraction_status not in ["pending", "processing"]:
#         messages.warning(request, "This extraction is not currently running.")
#         return redirect("upload")

#     statement.cancel_requested = True
#     statement.save(update_fields=["cancel_requested"])
#     if statement.celery_task_id:
#         AsyncResult(statement.celery_task_id).revoke(terminate=True, signal='SIGKILL')

#     cleanup_cancelled_extraction.delay(statement.id)

#     log_action(request, action="upload", user=request.user,
#                engagement=statement.account.engagement,
#                detail=f"Cancelled extraction for '{statement.original_filename}'")

#     messages.success(request, f"Extraction for '{statement.original_filename}' cancelled.")
#     return redirect("upload")


# @login_required
# def extraction_status_api(request):
#     """Lightweight polling endpoint for extraction status."""
#     recent = Statement.objects.order_by("-uploaded_at")[:10]
#     data = [
#         {
#             "id": s.id,
#             "status": s.extraction_status,
#             "rows_extracted": s.rows_extracted,
#         }
#         for s in recent
#     ]
#     return JsonResponse({"statements": data})


# @engagement_required
# def transactions_view(request, statement_id):
#     """List transactions for a statement with filters."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     txns = statement.transactions.order_by("source_row")

#     log_action(request, action="transaction_view", user=request.user,
#                engagement=statement.account.engagement,
#                detail=f"Viewed transactions for '{statement.original_filename}'")

#     q = request.GET.get("q", "").strip()
#     mode = request.GET.get("mode", "").strip()
#     direction = request.GET.get("direction", "").strip()
#     from_date = request.GET.get("from_date", "").strip()
#     to_date = request.GET.get("to_date", "").strip()

#     if q:
#         txns = txns.filter(Q(narration_raw__icontains=q) | Q(counterparty_name__icontains=q))
#     if mode:
#         txns = txns.filter(txn_mode=mode)
#     if direction == "debit":
#         txns = txns.filter(debit__gt=0)
#     elif direction == "credit":
#         txns = txns.filter(credit__gt=0)
#     if from_date:
#         txns = txns.filter(txn_date__gte=from_date)
#     if to_date:
#         txns = txns.filter(txn_date__lte=to_date)

#     agg = statement.transactions.aggregate(
#         total_debit=Sum("debit"), total_credit=Sum("credit"),
#         date_from=Min("txn_date"), date_to=Max("txn_date"),
#     )

#     paginator = Paginator(txns, 100)
#     txns_page = paginator.get_page(request.GET.get("page", 1))

#     return render(request, "statements/transactions.html", {
#         "statement": statement,
#         "txns": txns_page,
#         "total_debit": agg["total_debit"] or 0,
#         "total_credit": agg["total_credit"] or 0,
#         "date_from": agg["date_from"],
#         "date_to": agg["date_to"],
#     })


# # ===== DELETE VIEWS =====

# @login_required
# @require_POST
# def delete_transaction_view(request, statement_id, transaction_id):
#     """Delete a single transaction."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     transaction = get_object_or_404(Transaction, id=transaction_id, statement=statement)
#     txn_desc = f"{transaction.txn_date} | {transaction.narration_raw[:50]}"
#     transaction.delete()
#     messages.success(request, f"Transaction deleted: {txn_desc}")
#     return redirect('transactions', statement_id=statement_id)


# @login_required
# def confirm_delete_statement_view(request, statement_id):
#     """Show confirmation page before deleting statement."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     return render(request, 'statements/confirm_delete.html', {
#         'statement': statement,
#         'transaction_count': statement.transactions.count(),
#     })


# @login_required
# @require_POST
# def delete_statement_view(request, statement_id):
#     """Delete entire statement and all its transactions."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     filename = statement.original_filename
#     row_count = statement.transactions.count()
#     statement.delete()
#     messages.success(request, f"Deleted '{filename}' and {row_count} transactions.")
#     return redirect('upload')


# # ===== CLEANING & VALIDATION VIEWS =====

# @login_required
# def cleaning_dashboard_view(request, statement_id):
#     """Main cleaning & validation dashboard."""
#     statement = get_object_or_404(Statement, id=statement_id)
    
#     if not statement.validation_status or statement.validation_status == 'NOT_RUN':
#         engine = ValidationEngine(statement)
#         issues = engine.run_all_checks()
#         rating = engine.get_reliability_rating()
        
#         statement.validation_status = 'PENDING_REVIEW'
#         statement.validation_rating = rating
#         statement.validation_issues_count = len(issues)
#         statement.validation_critical_count = len([i for i in issues if i['severity'] == 'CRITICAL'])
#         statement.validation_high_count = len([i for i in issues if i['severity'] == 'HIGH'])
#         statement.validation_issues = issues
#         statement.save()
    
#     critical_issues = [i for i in statement.validation_issues if i['severity'] == 'CRITICAL']
#     high_issues = [i for i in statement.validation_issues if i['severity'] == 'HIGH']
#     medium_issues = [i for i in statement.validation_issues if i['severity'] == 'MEDIUM']
    
#     return render(request, 'statements/cleaning_dashboard.html', {
#         'statement': statement,
#         'critical_count': len(critical_issues),
#         'high_count': len(high_issues),
#         'medium_count': len(medium_issues),
#         'rating': statement.validation_rating,
#         'total_transactions': statement.transactions.count(),
#     })


# @login_required
# def cleaning_issues_view(request, statement_id):
#     """Detailed issue review page."""
#     statement = get_object_or_404(Statement, id=statement_id)
    
#     severity_filter = request.GET.get('severity', '')
#     resolution_filter = request.GET.get('resolution', '')
    
#     issues = statement.validation_issues or []
    
#     if severity_filter:
#         issues = [i for i in issues if i['severity'] == severity_filter]
    
#     if resolution_filter == 'required':
#         issues = [i for i in issues if i.get('resolution_required')]
#     elif resolution_filter == 'optional':
#         issues = [i for i in issues if not i.get('resolution_required')]
    
#     return render(request, 'statements/cleaning_issues.html', {
#         'statement': statement,
#         'issues': issues,
#         'severity_filter': severity_filter,
#         'resolution_filter': resolution_filter,
#     })


# @login_required
# def cleaning_transaction_detail_view(request, statement_id, transaction_id):
#     """Detailed view of a single transaction."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     transaction = get_object_or_404(Transaction, id=transaction_id, statement=statement)
    
#     issues = [i for i in statement.validation_issues if i.get('transaction_id') == transaction.id]
    
#     if request.method == 'POST':
#         field = request.POST.get('field')
#         new_value = request.POST.get('value')
        
#         if field == 'narration':
#             original = transaction.narration_raw
#             transaction.narration_raw = new_value
#             transaction.save()
#             statement.log_correction('narration', original, new_value, request.user)
#             messages.success(request, f'Narration updated')
        
#         elif field == 'debit':
#             original = transaction.debit
#             transaction.debit = new_value
#             transaction.save()
#             statement.log_correction('debit', original, new_value, request.user)
#             messages.success(request, f'Debit updated')
        
#         return redirect('cleaning_transaction_detail', statement_id=statement_id, transaction_id=transaction_id)
    
#     return render(request, 'statements/cleaning_transaction.html', {
#         'statement': statement,
#         'transaction': transaction,
#         'issues': issues,
#     })


# @login_required
# @require_POST
# def resolve_issue_view(request, statement_id, issue_id):
#     """Analyst marks an issue as resolved."""
#     statement = get_object_or_404(Statement, id=statement_id)
#     action = request.POST.get('action')

#     issues = statement.validation_issues or []
#     matched_code = None
#     for issue in issues:
#         if issue.get('id') == issue_id:
#             issue['analyst_action'] = action
#             issue['analyst_user_id'] = request.user.id
#             issue['resolved_at'] = timezone.now().isoformat()
#             issue['resolved'] = True
#             matched_code = issue.get('code')
#             break

#     statement.validation_issues = issues
#     statement.save()

#     # Recompute rating from the ACTUAL stored issues, not a fresh empty list
#     engine = ValidationEngine(statement)
#     engine.issues = issues
#     rating = engine.get_reliability_rating()
#     statement.validation_rating = rating
#     statement.save()

#     if matched_code:
#         messages.success(request, f'Issue {matched_code} resolved')
#     else:
#         messages.warning(request, 'Issue not found — it may have already been processed.')
#     return redirect('cleaning_issues', statement_id=statement_id)

# @login_required
# def export_cleaned_data_view(request, statement_id):
#     """Export cleaned data to Excel."""
#     statement = get_object_or_404(Statement, id=statement_id)
    
#     issues = statement.validation_issues or []
#     required_unresolved = [i for i in issues if i.get('resolution_required') and not i.get('resolved')]
    
#     if required_unresolved:
#         messages.error(request, f'{len(required_unresolved)} issue(s) require resolution.')
#         return redirect('cleaning_issues', statement_id=statement_id)
    
#     wb = openpyxl.Workbook()
#     ws = wb.active
#     ws.title = "Cleaned Transactions"
    
#     headers = [
#         'Idx', 'Date', 'Narration', 'Debit', 'Credit', 'Balance', 'Reference',
#         'Mode', 'Counterparty', 'Status', 'Flag Code', 'Flag Description'
#     ]
#     ws.append(headers)
    
#     txns = statement.transactions.order_by('source_row')
#     for txn in txns:
#         txn_issues = [i for i in issues if i.get('transaction_id') == txn.id]
#         if txn_issues:
#             status = 'Flagged'
#             codes = ', '.join(i.get('code', '') for i in txn_issues)
#             desc = '; '.join(i.get('message', '') for i in txn_issues)
#         else:
#             status = 'Clean'
#             codes = ''
#             desc = ''
        
#         ws.append([
#             txn.source_row,
#             txn.txn_date,
#             txn.narration_raw,
#             float(txn.debit) if txn.debit else '',
#             float(txn.credit) if txn.credit else '',
#             float(txn.balance) if txn.balance else '',
#             txn.reference,
#             txn.txn_mode,
#             txn.counterparty_name,
#             status,
#             codes,
#             desc,
#         ])
    
#     filename = f'cleaned_{statement.id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
#     response = HttpResponse(
#         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
#     )
#     response['Content-Disposition'] = f'attachment; filename="{filename}"'
#     wb.save(response)
    
#     statement.last_cleaned_export_at = timezone.now()
#     statement.last_cleaned_export_by = request.user
#     statement.save()
    
#     messages.success(request, f'Cleaned data exported')
#     return response