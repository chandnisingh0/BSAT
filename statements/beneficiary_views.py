"""
Beneficiary Identification Views
=================================
Views for running identification, analyst review, and counterparty ledger display
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Sum, Q
from django.utils import timezone

from .models import Statement, Transaction, Counterparty, BeneficiaryIdentification
from .beneficiary_engine import BeneficiaryEngine


@login_required
def beneficiary_dashboard_view(request, statement_id):
    """Dashboard showing beneficiary identification status and progress."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    # Check if identification has started
    counterparties_count = statement.counterparties.count()
    identified_transactions = statement.transactions.filter(beneficiary__isnull=False).count()
    total_eligible = statement.transactions.filter(
        Q(debit__gte=100000) | Q(credit__gte=100000)
    ).count()
    
    stats = {
        'counterparties_identified': counterparties_count,
        'transactions_identified': identified_transactions,
        'total_eligible': total_eligible,
        'identification_rate': f"{(identified_transactions / total_eligible * 100) if total_eligible > 0 else 0:.1f}%" if total_eligible > 0 else "0%",
        'pending_review': Counterparty.objects.filter(statement=statement, reviewed_by__isnull=True).count(),
    }
    
    # Get top counterparties by net position
    top_counterparties = statement.counterparties.order_by('-net_position')[:5]
    
    return render(request, 'statements/beneficiary_dashboard.html', {
        'statement': statement,
        'stats': stats,
        'top_counterparties': top_counterparties,
    })


@login_required
@require_POST
def start_beneficiary_identification_view(request, statement_id):
    """Start the three-layer beneficiary identification process."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    # Check if already done
    if statement.counterparties.exists():
        messages.warning(request, 'Beneficiary identification already completed for this statement.')
        return redirect('beneficiary_dashboard', statement_id=statement_id)
    
    try:
        # Run the engine
        engine = BeneficiaryEngine(statement, transaction_threshold=100000, confidence_threshold='HIGH')
        result = engine.run_identification()
        
        # Process Layer 1 & Layer 2 results
        created_counterparties = {}
        
        for item in result['results']:
            txn_id = item['transaction_id']
            result_data = item['result']
            status = item['status']
            
            transaction = Transaction.objects.get(id=txn_id)
            
            # Get or create counterparty
            counterparty_key = result_data['beneficiary_name']
            if counterparty_key not in created_counterparties:
                counterparty, created = Counterparty.objects.get_or_create(
                    statement=statement,
                    name=counterparty_key,
                    defaults={
                        'beneficiary_type': result_data.get('beneficiary_type', 'UNKNOWN'),
                        'identification_method': 'RULE_BASED' if status == 'IDENTIFIED_LAYER1' else 'OLLAMA',
                        'highest_confidence': result_data['confidence'],
                    }
                )
                created_counterparties[counterparty_key] = counterparty
            else:
                counterparty = created_counterparties[counterparty_key]
            
            # Update transaction
            transaction.beneficiary = counterparty
            transaction.beneficiary_identified_by = 'RULE_BASED' if status == 'IDENTIFIED_LAYER1' else 'OLLAMA'
            transaction.beneficiary_confidence = result_data['confidence']
            transaction.save()
            
            # Log identification
            BeneficiaryIdentification.objects.create(
                transaction=transaction,
                counterparty=counterparty,
                layer_identified='LAYER_1' if status == 'IDENTIFIED_LAYER1' else 'LAYER_2',
                confidence=result_data['confidence'],
                extraction_basis=result_data.get('extraction_basis', ''),
                layer1_result=result_data if status == 'IDENTIFIED_LAYER1' else None,
                layer2_result=result_data if status == 'IDENTIFIED_LAYER2' else None,
            )
        
        # Update counterparty financials
        for counterparty in created_counterparties.values():
            counterparty.update_financials()
        
        # Create review queue items for unresolved
        review_count = len(result['review_queue'])
        
        messages.success(
            request,
            f"Beneficiary identification complete! "
            f"{len(result['results'])} identified, {review_count} pending analyst review."
        )
        
        if review_count > 0:
            return redirect('analyst_review_queue', statement_id=statement_id)
        else:
            return redirect('counterparty_ledger', statement_id=statement_id)
    
    except Exception as e:
        messages.error(request, f'Identification failed: {str(e)}')
        return redirect('beneficiary_dashboard', statement_id=statement_id)


@login_required
def analyst_review_queue_view(request, statement_id):
    """Display unidentified transactions for analyst review."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    # Get unidentified eligible transactions
    unidentified = statement.transactions.filter(
        beneficiary__isnull=True,
        debit__gte=100000
    ) | statement.transactions.filter(
        beneficiary__isnull=True,
        credit__gte=100000
    )
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(unidentified.order_by('-debit', '-credit'), 20)
    page = request.GET.get('page', 1)
    txns_page = paginator.get_page(page)
    
    return render(request, 'statements/analyst_review_queue.html', {
        'statement': statement,
        'txns': txns_page,
        'total_unidentified': unidentified.count(),
    })


@login_required
@require_POST
def assign_beneficiary_view(request, statement_id, transaction_id):
    """Analyst assigns a beneficiary to a transaction."""
    statement = get_object_or_404(Statement, id=statement_id)
    transaction = get_object_or_404(Transaction, id=transaction_id, statement=statement)
    
    beneficiary_name = request.POST.get('beneficiary_name', '').strip()
    beneficiary_type = request.POST.get('beneficiary_type', 'UNKNOWN')
    
    if not beneficiary_name:
        messages.error(request, 'Beneficiary name required.')
        return redirect('analyst_review_queue', statement_id=statement_id)
    
    # Get or create counterparty
    counterparty, created = Counterparty.objects.get_or_create(
        statement=statement,
        name=beneficiary_name.upper(),
        defaults={
            'beneficiary_type': beneficiary_type,
            'identification_method': 'ANALYST',
            'highest_confidence': 1.0,
        }
    )
    
    # Update transaction
    transaction.beneficiary = counterparty
    transaction.beneficiary_identified_by = 'ANALYST'
    transaction.beneficiary_confidence = 1.0
    transaction.save()
    
    # Log identification
    BeneficiaryIdentification.objects.create(
        transaction=transaction,
        counterparty=counterparty,
        layer_identified='LAYER_3',
        confidence=1.0,
        extraction_basis='Manually assigned by analyst',
        analyst_confirmed=True,
        analyst_confirmed_by=request.user,
        confirmed_at=timezone.now(),
    )
    
    # Update counterparty financials
    counterparty.update_financials()
    
    messages.success(request, f'Beneficiary assigned: {beneficiary_name}')
    return redirect('analyst_review_queue', statement_id=statement_id)


@login_required
def counterparty_ledger_view(request, statement_id):
    """Display counterparty ledger with all identified beneficiaries."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    # Apply filters
    search = request.GET.get('search', '').strip()
    counterparty_type = request.GET.get('type', '')
    method = request.GET.get('method', '')
    
    counterparties = statement.counterparties.all()
    
    if search:
        counterparties = counterparties.filter(name__icontains=search)
    if counterparty_type:
        counterparties = counterparties.filter(beneficiary_type=counterparty_type)
    if method:
        counterparties = counterparties.filter(identification_method=method)
    
    # Sorting
    sort_by = request.GET.get('sort', '-total_debit')
    counterparties = counterparties.order_by(sort_by)
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(counterparties, 25)
    page = request.GET.get('page', 1)
    counterparties_page = paginator.get_page(page)
    
    # Summary stats
    summary = {
        'total_counterparties': statement.counterparties.count(),
        'total_transactions': statement.transactions.filter(beneficiary__isnull=False).count(),
        'total_value': statement.transactions.filter(beneficiary__isnull=False).aggregate(
            Sum('debit')
        )['debit__sum'] or 0,
    }
    
    return render(request, 'statements/counterparty_ledger.html', {
        'statement': statement,
        'counterparties': counterparties_page,
        'summary': summary,
        'search': search,
        'counterparty_type': counterparty_type,
        'method': method,
    })


@login_required
def counterparty_detail_view(request, statement_id, counterparty_id):
    """Detailed view of a single counterparty and all their transactions."""
    statement = get_object_or_404(Statement, id=statement_id)
    counterparty = get_object_or_404(Counterparty, id=counterparty_id, statement=statement)
    
    transactions = counterparty.transactions.order_by('-txn_date')
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(transactions, 50)
    page = request.GET.get('page', 1)
    txns_page = paginator.get_page(page)
    
    # Identification audit trail
    audit_trail = BeneficiaryIdentification.objects.filter(counterparty=counterparty).order_by('-created_at')
    
    return render(request, 'statements/counterparty_detail.html', {
        'statement': statement,
        'counterparty': counterparty,
        'txns': txns_page,
        'audit_trail': audit_trail,
    })


@login_required
@require_POST
def approve_counterparty_view(request, statement_id, counterparty_id):
    """Analyst approves a counterparty identification."""
    statement = get_object_or_404(Statement, id=statement_id)
    counterparty = get_object_or_404(Counterparty, id=counterparty_id, statement=statement)
    
    analyst_notes = request.POST.get('analyst_notes', '').strip()
    
    counterparty.reviewed_by = request.user
    counterparty.reviewed_at = timezone.now()
    counterparty.analyst_notes = analyst_notes
    counterparty.save()
    
    # Mark all related identifications as confirmed
    BeneficiaryIdentification.objects.filter(counterparty=counterparty).update(
        analyst_confirmed=True,
        analyst_confirmed_by=request.user,
        confirmed_at=timezone.now(),
    )
    
    messages.success(request, f'Counterparty approved: {counterparty.name}')
    return redirect('counterparty_detail', statement_id=statement_id, counterparty_id=counterparty_id)


@login_required
def export_counterparty_ledger_view(request, statement_id):
    """Export counterparty ledger to Excel."""
    statement = get_object_or_404(Statement, id=statement_id)
    
    import openpyxl
    from datetime import datetime
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Counterparty Ledger"
    
    # Headers
    headers = [
        'Beneficiary Name', 'Type', 'Identification Method', 'Confidence',
        'Total Debit', 'Total Credit', 'Net Position', 'Transaction Count',
        'First Date', 'Last Date', 'Above Threshold', 'Reviewed By'
    ]
    ws.append(headers)
    
    # Data
    for cp in statement.counterparties.all().order_by('-total_debit'):
        ws.append([
            cp.name,
            cp.get_beneficiary_type_display(),
            cp.get_identification_method_display(),
            float(cp.highest_confidence),
            float(cp.total_debit),
            float(cp.total_credit),
            float(cp.net_position),
            cp.transaction_count,
            cp.first_transaction_date,
            cp.last_transaction_date,
            'Yes' if cp.above_aggregate_threshold else 'No',
            cp.reviewed_by.username if cp.reviewed_by else '',
        ])
    
    # Save and return
    from django.http import HttpResponse
    filename = f'counterparty_ledger_{statement.id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    
    return response