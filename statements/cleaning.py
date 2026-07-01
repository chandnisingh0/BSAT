"""
Data Cleaning & Validation Engine
==================================
Separate from extraction. Runs AFTER transactions are extracted.
Validates balance, dates, duplicates, narrations.
Returns issues for analyst resolution.
"""

from decimal import Decimal
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import re
from django.db.models import Sum
import uuid

class ValidationEngine:
    """Core validation logic."""
    
    BANKING_PREFIXES = [
        'NEFT/', 'RTGS/', 'IMPS/', 'UPI/', 'BY ORDER OF', 
        'TRANSFER TO', 'CREDIT BY', 'DEBIT BY', 'TOWARDS', 'BEING'
    ]
    
    GENERIC_NARRATIONS = [
        'TRANSFER', 'BY ORDER', 'CREDIT', 'DEBIT', 'PAYMENT', 
        'MISC', 'OTHERS', 'CLEARANCE', 'CASH'
    ]
    
    REVERSAL_KEYWORDS = ['REV', 'REVERSAL', 'RETURN', 'BOUNCE', 'DISHONOUR']
    
    BALANCE_TOLERANCE = Decimal('1.00')
    
    def __init__(self, statement):
        self.statement = statement
        self.issues = []
    
    # ===== CHECK 1: BALANCE RECONCILIATION =====
    
    def check_balance_reconciliation(self):
        """Verify Prior Balance + Credit - Debit = Current Balance for each row."""
        # NEW CODE: Process all transactions
        txns = list(self.statement.transactions.order_by('source_row'))
        
        # --- PREVIOUS CODE (Kept for safety) ---
        # # TEMPORARY TESTING CHANGE: Added list() and [:150] to limit lines for faster testing.
        # # Remove list() and "[:150]" below to restore normal functionality.
        # # --- ORIGINAL CODE ---
        # # txns = self.statement.transactions.order_by('source_row')
        # txns = list(self.statement.transactions.order_by('source_row')[:150])
        
        for i, txn in enumerate(txns):
            if i == 0:
                continue
            
            prior_txn = txns[i-1]
            prior_bal = prior_txn.balance or Decimal(0)
            
            expected_bal = prior_bal + (txn.credit or Decimal(0)) - (txn.debit or Decimal(0))
            actual_bal = txn.balance or Decimal(0)
            
            variance = abs(expected_bal - actual_bal)
            
            if variance > self.BALANCE_TOLERANCE:
                self.add_issue(
                    transaction=txn,
                    severity='HIGH',
                    code='BAL_MISMATCH',
                    message=f'Balance mismatch: expected {expected_bal}, got {actual_bal} (variance: {variance})',
                    resolution_required=False
                )
            
            if (txn.debit and txn.debit < 0) or (txn.credit and txn.credit < 0):
                self.add_issue(
                    transaction=txn,
                    severity='CRITICAL',
                    code='NEGATIVE_AMOUNT',
                    message='Negative amount detected',
                    resolution_required=True
                )
            
            if txn.debit and txn.credit and txn.debit > 0 and txn.credit > 0:
                self.add_issue(
                    transaction=txn,
                    severity='HIGH',
                    code='BOTH_DEBIT_CREDIT',
                    message='Both debit and credit are non-zero',
                    resolution_required=False
                )
        
        agg = self.statement.transactions.aggregate(
            total_debit=Sum('debit'),
            total_credit=Sum('credit')
        )
        
        total_debit = agg['total_debit'] or Decimal(0)
        total_credit = agg['total_credit'] or Decimal(0)
        
        # --- ORIGINAL CODE ---
        # first_txn = txns.first()
        # last_txn = txns.last()
        first_txn = txns[0] if txns else None
        last_txn = txns[-1] if txns else None
        
        if first_txn and last_txn:
            opening_bal = first_txn.balance or Decimal(0)
            closing_bal = last_txn.balance or Decimal(0)
            
            expected_closing = opening_bal + total_credit - total_debit
            variance = abs(expected_closing - closing_bal)
            
            if variance > self.BALANCE_TOLERANCE:
                self.statement.validation_notes = f'Statement-level balance variance: {variance}'
                self.statement.save()
    
    # ===== CHECK 2: DATE GAP DETECTION =====
    
    def check_date_gaps(self):
        """Find gaps > 7 working days."""
        # NEW CODE: Process all transactions
        txns = self.statement.transactions.order_by('txn_date').values_list('txn_date', flat=True).distinct()
        txn_dates = sorted(set(t for t in txns if t))
        
        # --- PREVIOUS CODE (Kept for safety) ---
        # # TEMPORARY TESTING CHANGE: Limited to top 150 rows.
        # # --- ORIGINAL CODE ---
        # # txns = self.statement.transactions.order_by('txn_date').values_list('txn_date', flat=True).distinct()
        # # if len(txns) < 2:
        # #     return
        # # txn_dates = sorted(set(txns))
        # 
        # txns_subset = self.statement.transactions.order_by('source_row')[:150]
        # txn_dates = sorted(set(t.txn_date for t in txns_subset if t.txn_date))
        
        if len(txn_dates) < 2:
            return
        
        for i in range(len(txn_dates) - 1):
            curr_date = txn_dates[i]
            next_date = txn_dates[i+1]
            
            gap_days = (next_date - curr_date).days
            working_days = self._count_working_days(curr_date, next_date)
            
            if working_days > 7:
                self.add_issue(
                    transaction=None,
                    severity='HIGH',
                    code='DATE_GAP',
                    message=f'Date gap: {gap_days} days ({working_days} working days) between {curr_date} and {next_date}',
                    resolution_required=False
                )
    
    def _count_working_days(self, start_date, end_date):
        """Count working days (exclude weekends)."""
        count = 0
        current = start_date
        while current < end_date:
            if current.weekday() < 5:
                count += 1
            current += timedelta(days=1)
        return count
    
    # ===== CHECK 3: DUPLICATE DETECTION =====
    
    def check_duplicates(self):
        """Find exact, probable, and near duplicates."""
        # NEW CODE: Process all transactions
        txns = list(self.statement.transactions.all())
        
        # --- PREVIOUS CODE (Kept for safety) ---
        # # TEMPORARY TESTING CHANGE: Added .order_by('source_row')[:150] for faster testing.
        # # Remove .order_by('source_row')[:150] to restore normal functionality.
        # # --- ORIGINAL CODE ---
        # # txns = list(self.statement.transactions.all())
        # txns = list(self.statement.transactions.order_by('source_row')[:150])
        
        checked = set()
        
        for i, txn1 in enumerate(txns):
            if txn1.id in checked:
                continue
            
            for txn2 in txns[i+1:]:
                if txn2.id in checked:
                    continue
                
                if self._is_reversal_pair(txn1, txn2):
                    self.add_issue(
                        transaction=txn1,
                        severity='INFO',
                        code='REVERSAL_PAIR',
                        message=f'Reversal pair with Row #{txn2.source_row}',
                        resolution_required=False
                    )
                    continue
                
                if (txn1.txn_date == txn2.txn_date and
                    txn1.debit == txn2.debit and
                    txn1.credit == txn2.credit and
                    txn1.reference == txn2.reference):
                    
                    self.add_issue(
                        transaction=txn1,
                        severity='HIGH',
                        code='EXACT_DUPLICATE',
                        message=f'Exact duplicate with Row #{txn2.source_row}',
                        resolution_required=False
                    )
                    checked.add(txn2.id)
                    continue
                
                if (txn1.txn_date == txn2.txn_date and
                    txn1.debit == txn2.debit and
                    txn1.credit == txn2.credit and
                    self._clean_narration(txn1.narration_raw) == self._clean_narration(txn2.narration_raw)):
                    
                    self.add_issue(
                        transaction=txn1,
                        severity='HIGH',
                        code='PROBABLE_DUPLICATE',
                        message=f'Probable duplicate with Row #{txn2.source_row}',
                        resolution_required=True
                    )
                    continue
                
                days_diff = abs((txn1.txn_date - txn2.txn_date).days) if (txn1.txn_date and txn2.txn_date) else 999
                similarity = self._narration_similarity(txn1.narration_raw, txn2.narration_raw)
                
                if days_diff <= 3 and similarity >= 0.90 and (txn1.debit == txn2.debit or txn1.credit == txn2.credit):
                    self.add_issue(
                        transaction=txn1,
                        severity='MEDIUM',
                        code='NEAR_DUPLICATE',
                        message=f'Near duplicate with Row #{txn2.source_row}',
                        resolution_required=False
                    )
    
    def _is_reversal_pair(self, txn1, txn2):
        """Check if two transactions are a reversal pair."""
        if not (txn1.txn_date and txn2.txn_date):
            return False
        
        if abs((txn1.txn_date - txn2.txn_date).days) > 7:
            return False
        
        if txn1.debit == txn2.credit and txn1.credit == txn2.debit:
            narration = (txn1.narration_raw or '' + ' ' + txn2.narration_raw or '').upper()
            return any(kw in narration for kw in self.REVERSAL_KEYWORDS)
        
        return False
    
    def _clean_narration(self, narration):
        """Strip banking prefixes and normalize."""
        text = narration or ''
        for prefix in self.BANKING_PREFIXES:
            text = re.sub(re.escape(prefix), '', text, flags=re.IGNORECASE)
        return ' '.join(text.split()).strip()
    
    def _narration_similarity(self, nar1, nar2):
        """Calculate similarity."""
        if not nar1 or not nar2:
            return 0
        return SequenceMatcher(None, nar1.lower(), nar2.lower()).ratio()
    
    # ===== CHECK 4: NARRATION QUALITY =====
    
    def check_narration_quality(self):
        """Flag blank, generic, or low-quality narrations."""
        # NEW CODE: Process all transactions
        txns = self.statement.transactions.all()
        
        # --- PREVIOUS CODE (Kept for safety) ---
        # # TEMPORARY TESTING CHANGE: Added .order_by('source_row')[:150] for faster testing.
        # # Remove .order_by('source_row')[:150] to restore normal functionality.
        # # --- ORIGINAL CODE ---
        # # txns = self.statement.transactions.all()
        # txns = self.statement.transactions.order_by('source_row')[:150]
        
        for txn in txns:
            narration = (txn.narration_raw or '').strip()
            amount = txn.debit or txn.credit or Decimal(0)
            
            if not narration:
                severity = 'CRITICAL' if amount >= Decimal('2500000') else 'HIGH'
                self.add_issue(
                    transaction=txn,
                    severity=severity,
                    code='BLANK_NARRATION',
                    message='Blank narration',
                    resolution_required=(severity == 'CRITICAL')
                )
                continue
            
            if len(narration) < 5:
                self.add_issue(
                    transaction=txn,
                    severity='MEDIUM',
                    code='SHORT_NARRATION',
                    message=f'Narration too short: "{narration}"',
                    resolution_required=False
                )
                continue
            
            clean_nar = self._clean_narration(narration)
            if clean_nar.upper() in [g.upper() for g in self.GENERIC_NARRATIONS]:
                severity = 'CRITICAL' if amount >= Decimal('2500000') else 'HIGH'
                self.add_issue(
                    transaction=txn,
                    severity=severity,
                    code='GENERIC_NARRATION',
                    message=f'Generic narration: "{clean_nar}"',
                    resolution_required=False
                )
                continue
            
            if narration.replace(' ', '').isdigit():
                self.add_issue(
                    transaction=txn,
                    severity='MEDIUM',
                    code='NUMERIC_ONLY_NARRATION',
                    message=f'Numeric-only narration: "{narration}"',
                    resolution_required=False
                )
    
    # ===== ISSUE MANAGEMENT =====    
    def add_issue(self, transaction=None, severity='MEDIUM', code='', message='', 
                resolution_required=False, suggested_action=''):
        """Log a validation issue."""
        self.issues.append({
            'id': uuid.uuid4().hex[:10],   # ⬅ NEW: unique per issue
            'transaction_id': transaction.id if transaction else None,
            'severity': severity,
            'code': code,
            'message': message,
            'resolution_required': resolution_required,
            'suggested_action': suggested_action,
            'timestamp': datetime.now().isoformat(),
            'resolved': False
        })

    def run_all_checks(self):
        """Execute all validation checks in sequence."""
        self.check_balance_reconciliation()
        self.check_date_gaps()
        self.check_duplicates()
        self.check_narration_quality()
        return self.issues
    
    def get_reliability_rating(self):
        """Assign data reliability rating."""
        critical_count = sum(1 for i in self.issues if i['severity'] == 'CRITICAL')
        high_count = sum(1 for i in self.issues if i['severity'] == 'HIGH')
        medium_count = sum(1 for i in self.issues if i['severity'] == 'MEDIUM')
        
        if critical_count == 0 and high_count == 0 and medium_count == 0:
            return 'CLEAN'
        elif critical_count == 0 and high_count == 0:
            return 'ACCEPTABLE'
        elif critical_count == 0:
            return 'QUALIFIED'
        else:
            reviewed = self.statement.validation_reviewed_by is not None
            return 'QUALIFIED' if reviewed else 'UNRELIABLE'