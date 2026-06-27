"""
Database models.

Three tables:
  Account     - one bank account of the Corporate Debtor
  Statement   - one uploaded file (belongs to an account)
  Transaction - one row of a bank statement (belongs to a statement)

These map to the DF-01..DF-07 fields in your rules document.
"""
from django.db import models


from django.conf import settings
from accounts.models import Engagement
from decimal import Decimal


# from django.contrib.auth.models import User
from django.conf import settings

class Account(models.Model):
    engagement = models.ForeignKey(
        Engagement, on_delete=models.CASCADE, related_name="accounts",
        null=True, blank=True,
    )
    cd_name = models.CharField("Corporate Debtor name", max_length=255)
    bank_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=50)
    is_cd_account = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("bank_name", "account_number")

    def __str__(self):
        return f"{self.cd_name} - {self.bank_name} ({self.account_number})"
    
# ============================
class Statement(models.Model):
    FILE_TYPES = [
        ("csv", "CSV / Excel"),
        ("pdf_text", "PDF (text)"),
        ("pdf_scan", "PDF (scanned/OCR)"),
        ("image", "Image (OCR)"),
        ("rpt", "RPT (text report)"),
        ("unknown", "Unknown"),
    ]
 
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]
 
    VALIDATION_STATUS_CHOICES = [
        ("NOT_RUN", "Not Run"),
        ("PENDING_REVIEW", "Pending Review"),
        ("CLEAN", "Clean"),
        ("ACCEPTABLE", "Acceptable"),
        ("QUALIFIED", "Qualified"),
        ("UNRELIABLE", "Unreliable"),
    ]
 
    RATING_CHOICES = [
        ("CLEAN", "Clean"),
        ("ACCEPTABLE", "Acceptable"),
        ("QUALIFIED", "Qualified"),
        ("UNRELIABLE", "Unreliable"),
    ]
 
    # ===== CORE FIELDS (EXTRACTION) =====
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="statements")
    source_file = models.FileField(upload_to="uploads/")
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20, choices=FILE_TYPES, default="unknown")
    rows_extracted = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
 
    extraction_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    extraction_started_at = models.DateTimeField(null=True, blank=True)
    extraction_completed_at = models.DateTimeField(null=True, blank=True)
 
    celery_task_id = models.CharField(max_length=255, blank=True)
    cancel_requested = models.BooleanField(default=False)
 
    # ===== CLEANING & VALIDATION FIELDS =====
    
    # Status tracking
    validation_status = models.CharField(
        max_length=20,
        choices=VALIDATION_STATUS_CHOICES,
        default="NOT_RUN",
        help_text="Current state of validation workflow"
    )
    
    validation_rating = models.CharField(
        max_length=20,
        choices=RATING_CHOICES,
        blank=True,
        help_text="Data quality rating (CLEAN, ACCEPTABLE, QUALIFIED, UNRELIABLE)"
    )
    
    # Issue tracking
    validation_issues_count = models.IntegerField(
        default=0,
        help_text="Total number of validation issues found"
    )
    
    validation_critical_count = models.IntegerField(
        default=0,
        help_text="Count of CRITICAL severity issues"
    )
    
    validation_high_count = models.IntegerField(
        default=0,
        help_text="Count of HIGH severity issues"
    )
    
    # Issues storage (JSON format)
    validation_issues = models.JSONField(
        default=list,
        blank=True,
        help_text="List of validation issues detected (JSON)"
    )
    
    # Timing
    validation_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When validation checks started"
    )
    
    validation_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When validation checks completed"
    )
    
    # Analyst review - FIX: Use settings.AUTH_USER_MODEL
    validation_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_statements",
        help_text="User who reviewed/approved validation issues"
    )
    
    validation_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When analyst completed validation review"
    )
    
    # Notes & corrections
    validation_notes = models.TextField(
        blank=True,
        help_text="Analyst notes on validation issues and corrections"
    )
    
    correction_log = models.JSONField(
        default=list,
        blank=True,
        help_text="Log of manual corrections made by analyst"
    )
    
    # Export tracking - FIX: Use settings.AUTH_USER_MODEL
    last_cleaned_export_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When cleaned data was last exported"
    )
    
    last_cleaned_export_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exported_statements",
        help_text="User who exported cleaned data"
    )
 
    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['account', '-uploaded_at']),
            models.Index(fields=['extraction_status']),
            models.Index(fields=['validation_status']),
            models.Index(fields=['validation_rating']),
        ]
 
    def __str__(self):
        return f"{self.original_filename} ({self.file_type})"
 
    # ===== HELPER METHODS =====
 
    def mark_extraction_complete(self):
        """Mark extraction as completed."""
        from django.utils import timezone
        self.extraction_status = "completed"
        self.extraction_completed_at = timezone.now()
        self.save()
 
    def start_validation(self):
        """Initialize validation process."""
        from django.utils import timezone
        self.validation_status = "PENDING_REVIEW"
        self.validation_started_at = timezone.now()
        self.save()
 
    def complete_validation(self, rating, critical_count, high_count, total_count, issues):
        """Save validation results."""
        from django.utils import timezone
        self.validation_rating = rating
        self.validation_critical_count = critical_count
        self.validation_high_count = high_count
        self.validation_issues_count = total_count
        self.validation_issues = issues
        self.validation_completed_at = timezone.now()
        self.save()
 
    def approve_validation(self, user):
        """Analyst approves validation and marks reviewed."""
        from django.utils import timezone
        self.validation_reviewed_by = user
        self.validation_reviewed_at = timezone.now()
        if self.validation_rating == "UNRELIABLE":
            self.validation_rating = "QUALIFIED"
        self.save()
 
    def has_unresolved_critical_issues(self):
        """Check if there are unresolved critical issues."""
        critical = [i for i in self.validation_issues if i.get('severity') == 'CRITICAL' and not i.get('resolved')]
        return len(critical) > 0
 
    def can_export_cleaned_data(self):
        """Check if statement can be exported."""
        unresolved_required = [
            i for i in self.validation_issues
            if i.get('resolution_required') and not i.get('resolved')
        ]
        return len(unresolved_required) == 0
 
    def get_issue_summary(self):
        """Get summary of issues by severity and type."""
        summary = {
            'critical': 0,
            'high': 0,
            'medium': 0,
            'info': 0,
            'by_code': {}
        }
        
        for issue in self.validation_issues:
            severity = issue.get('severity', 'INFO')
            code = issue.get('code', 'UNKNOWN')
            
            if severity == 'CRITICAL':
                summary['critical'] += 1
            elif severity == 'HIGH':
                summary['high'] += 1
            elif severity == 'MEDIUM':
                summary['medium'] += 1
            else:
                summary['info'] += 1
            
            if code not in summary['by_code']:
                summary['by_code'][code] = 0
            summary['by_code'][code] += 1
        
        return summary
 
    def log_correction(self, field, original, corrected, user):
        """Log a manual correction made by analyst."""
        from django.utils import timezone
        
        correction = {
            'field': field,
            'original_value': str(original),
            'corrected_value': str(corrected),
            'corrected_by_user_id': user.id,
            'corrected_by_username': user.username,
            'timestamp': timezone.now().isoformat()
        }
        
        if not self.correction_log:
            self.correction_log = []
        
        self.correction_log.append(correction)
        self.save()
 
    def get_corrections_count(self):
        """Get total number of corrections made."""
        return len(self.correction_log) if self.correction_log else 0
 
    @property
    def is_fully_validated(self):
        """Check if validation is complete and reviewed."""
        return self.validation_status == "CLEAN" or self.validation_rating in ["QUALIFIED", "ACCEPTABLE"]
 
    @property
    def quality_badge_class(self):
        """Return Bootstrap badge class for rating."""
        rating_map = {
            'CLEAN': 'success',
            'ACCEPTABLE': 'info',
            'QUALIFIED': 'warning',
            'UNRELIABLE': 'danger',
        }
        return rating_map.get(self.validation_rating, 'secondary')
 
    def __repr__(self):
        return f"<Statement id={self.id} file={self.original_filename} status={self.extraction_status} validation={self.validation_rating}>"


class Transaction(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name="transactions")
    txn_date = models.DateField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    txn_time = models.TimeField(null=True, blank=True)
    narration_raw = models.TextField(blank=True)
    debit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    credit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    balance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    balance_type = models.CharField(max_length=2, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    txn_mode = models.CharField(max_length=20, blank=True)
    counterparty_name = models.CharField(max_length=255, blank=True)
    source_row = models.IntegerField(null=True, blank=True)
    quality_flag = models.CharField(max_length=100, blank=True)
    bank_json_data = models.JSONField(null=True, blank=True)
    
    # ===== STEP 4A: BENEFICIARY IDENTIFICATION =====
    beneficiary = models.ForeignKey(
        'Counterparty',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions"
    )
    beneficiary_identified_by = models.CharField(
        max_length=20,
        choices=[
            ('RULE_BASED', 'Rule-Based'),
            ('OLLAMA', 'Ollama LLM'),
            ('ANALYST', 'Analyst'),
            ('UNIDENTIFIED', 'Unidentified'),
        ],
        blank=True
    )
    beneficiary_confidence = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.txn_date} | {self.narration_raw[:30]}"


class Counterparty(models.Model):
    """Identified beneficiary/counterparty ledger."""
    
    BENEFICIARY_TYPES = [
        ('COMPANY', 'Company'),
        ('INDIVIDUAL', 'Individual'),
        ('BANK', 'Bank'),
        ('GOVERNMENT', 'Government'),
        ('UNKNOWN', 'Unknown'),
    ]
    
    IDENTIFICATION_METHODS = [
        ('RULE_BASED', 'Rule-Based'),
        ('OLLAMA', 'Ollama LLM'),
        ('ANALYST', 'Analyst'),
    ]
    
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name="counterparties")
    name = models.CharField(max_length=255)
    beneficiary_type = models.CharField(max_length=20, choices=BENEFICIARY_TYPES)
    identification_method = models.CharField(max_length=20, choices=IDENTIFICATION_METHODS)
    
    highest_confidence = models.DecimalField(max_digits=3, decimal_places=2)
    identification_count = models.IntegerField(default=1)
    
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_position = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    transaction_count = models.IntegerField(default=0)
    
    first_transaction_date = models.DateField(null=True, blank=True)
    last_transaction_date = models.DateField(null=True, blank=True)
    
    account_numbers = models.JSONField(default=list, blank=True)
    ifsc_codes = models.JSONField(default=list, blank=True)
    
    above_aggregate_threshold = models.BooleanField(default=True)
    analyst_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('statement', 'name')
        ordering = ['-total_debit', '-total_credit']
    
    def __str__(self):
        return f"{self.name} ({self.beneficiary_type})"
    
    def update_financials(self):
        """Recalculate financial totals from transactions."""
        from django.db.models import Sum
        agg = self.transactions.aggregate(total_debit=Sum('debit'), total_credit=Sum('credit'))
        self.total_debit = agg['total_debit'] or Decimal(0)
        self.total_credit = agg['total_credit'] or Decimal(0)
        self.net_position = self.total_credit - self.total_debit
        self.transaction_count = self.transactions.count()
        txns = self.transactions.order_by('txn_date')
        if txns.exists():
            self.first_transaction_date = txns.first().txn_date
            self.last_transaction_date = txns.last().txn_date
        self.save()


class BeneficiaryIdentification(models.Model):
    """Audit trail for beneficiary identification."""
    
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="beneficiary_identifications")
    counterparty = models.ForeignKey(Counterparty, on_delete=models.CASCADE)
    
    layer_identified = models.CharField(
        max_length=20,
        choices=[
            ('LAYER_1', 'Rule-Based'),
            ('LAYER_2', 'Ollama LLM'),
            ('LAYER_3', 'Analyst'),
        ]
    )
    confidence = models.DecimalField(max_digits=3, decimal_places=2)
    extraction_basis = models.TextField()
    
    layer1_result = models.JSONField(null=True, blank=True)
    layer2_result = models.JSONField(null=True, blank=True)
    
    analyst_confirmed = models.BooleanField(default=False)
    analyst_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="confirmed_identifications")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.transaction.id} → {self.counterparty.name}"
    
# class Statement(models.Model):
#     FILE_TYPES = [
#         ("csv", "CSV / Excel"),
#         ("pdf_text", "PDF (text)"),
#         ("pdf_scan", "PDF (scanned/OCR)"),
#         ("image", "Image (OCR)"),
#         ("rpt", "RPT (text report)"),
#         ("unknown", "Unknown"),
#     ]
 
#     STATUS_CHOICES = [
#         ("pending", "Pending"),
#         ("processing", "Processing"),
#         ("completed", "Completed"),
#         ("failed", "Failed"),
#         ("cancelled", "Cancelled"),
#     ]
 
#     VALIDATION_STATUS_CHOICES = [
#         ("NOT_RUN", "Not Run"),
#         ("PENDING_REVIEW", "Pending Review"),
#         ("CLEAN", "Clean"),
#         ("ACCEPTABLE", "Acceptable"),
#         ("QUALIFIED", "Qualified"),
#         ("UNRELIABLE", "Unreliable"),
#     ]
 
#     RATING_CHOICES = [
#         ("CLEAN", "Clean"),
#         ("ACCEPTABLE", "Acceptable"),
#         ("QUALIFIED", "Qualified"),
#         ("UNRELIABLE", "Unreliable"),
#     ]
 
#     # ===== CORE FIELDS (EXTRACTION) =====
#     account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="statements")
#     source_file = models.FileField(upload_to="uploads/")
#     original_filename = models.CharField(max_length=255)
#     file_type = models.CharField(max_length=20, choices=FILE_TYPES, default="unknown")
#     rows_extracted = models.IntegerField(default=0)
#     notes = models.TextField(blank=True)
#     uploaded_at = models.DateTimeField(auto_now_add=True)
 
#     extraction_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
#     extraction_started_at = models.DateTimeField(null=True, blank=True)
#     extraction_completed_at = models.DateTimeField(null=True, blank=True)
 
#     celery_task_id = models.CharField(max_length=255, blank=True)
#     cancel_requested = models.BooleanField(default=False)
 
#     # ===== CLEANING & VALIDATION FIELDS =====
    
#     # Status tracking
#     validation_status = models.CharField(
#         max_length=20,
#         choices=VALIDATION_STATUS_CHOICES,
#         default="NOT_RUN",
#         help_text="Current state of validation workflow"
#     )
    
#     validation_rating = models.CharField(
#         max_length=20,
#         choices=RATING_CHOICES,
#         blank=True,
#         help_text="Data quality rating (CLEAN, ACCEPTABLE, QUALIFIED, UNRELIABLE)"
#     )
    
#     # Issue tracking
#     validation_issues_count = models.IntegerField(
#         default=0,
#         help_text="Total number of validation issues found"
#     )
    
#     validation_critical_count = models.IntegerField(
#         default=0,
#         help_text="Count of CRITICAL severity issues"
#     )
    
#     validation_high_count = models.IntegerField(
#         default=0,
#         help_text="Count of HIGH severity issues"
#     )
    
#     # Issues storage (JSON format)
#     validation_issues = models.JSONField(
#         default=list,
#         blank=True,
#         help_text="List of validation issues detected (JSON)"
#     )
    
#     # Timing
#     validation_started_at = models.DateTimeField(
#         null=True,
#         blank=True,
#         help_text="When validation checks started"
#     )
    
#     validation_completed_at = models.DateTimeField(
#         null=True,
#         blank=True,
#         help_text="When validation checks completed"
#     )
    
#     # Analyst review
#     validation_reviewed_by = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         null=True,
#         blank=True,
#         on_delete=models.SET_NULL,
#         related_name="reviewed_statements",
#         help_text="User who reviewed/approved validation issues"
#     )
    
#     validation_reviewed_at = models.DateTimeField(
#         null=True,
#         blank=True,
#         help_text="When analyst completed validation review"
#     )
    
#     # Notes & corrections
#     validation_notes = models.TextField(
#         blank=True,
#         help_text="Analyst notes on validation issues and corrections"
#     )
    
#     correction_log = models.JSONField(
#         default=list,
#         blank=True,
#         help_text="Log of manual corrections made by analyst"
#     )
    
#     # Export tracking
#     last_cleaned_export_at = models.DateTimeField(
#         null=True,
#         blank=True,
#         help_text="When cleaned data was last exported"
#     )
    
#     last_cleaned_export_by = models.ForeignKey(
#         settings.AUTH_USER_MODEL,
#         null=True,
#         blank=True,
#         on_delete=models.SET_NULL,
#         related_name="exported_statements",
#         help_text="User who exported cleaned data"
#     )
 
#     class Meta:
#         ordering = ['-uploaded_at']
#         indexes = [
#             models.Index(fields=['account', '-uploaded_at']),
#             models.Index(fields=['extraction_status']),
#             models.Index(fields=['validation_status']),
#             models.Index(fields=['validation_rating']),
#         ]
 
#     def __str__(self):
#         return f"{self.original_filename} ({self.file_type})"
 
#     # ===== HELPER METHODS =====
 
#     def mark_extraction_complete(self):
#         """Mark extraction as completed."""
#         from django.utils import timezone
#         self.extraction_status = "completed"
#         self.extraction_completed_at = timezone.now()
#         self.save()
 
#     def start_validation(self):
#         """Initialize validation process."""
#         from django.utils import timezone
#         self.validation_status = "PENDING_REVIEW"
#         self.validation_started_at = timezone.now()
#         self.save()
 
#     def complete_validation(self, rating, critical_count, high_count, total_count, issues):
#         """Save validation results."""
#         from django.utils import timezone
#         self.validation_rating = rating
#         self.validation_critical_count = critical_count
#         self.validation_high_count = high_count
#         self.validation_issues_count = total_count
#         self.validation_issues = issues
#         self.validation_completed_at = timezone.now()
#         self.save()
 
#     def approve_validation(self, user):
#         """Analyst approves validation and marks reviewed."""
#         from django.utils import timezone
#         self.validation_reviewed_by = user
#         self.validation_reviewed_at = timezone.now()
#         # If UNRELIABLE and now reviewed -> QUALIFIED
#         if self.validation_rating == "UNRELIABLE":
#             self.validation_rating = "QUALIFIED"
#         self.save()
 
#     def has_unresolved_critical_issues(self):
#         """Check if there are unresolved critical issues."""
#         critical = [i for i in self.validation_issues if i.get('severity') == 'CRITICAL' and not i.get('resolved')]
#         return len(critical) > 0
 
#     def can_export_cleaned_data(self):
#         """Check if statement can be exported."""
#         # Must have zero unresolved CRITICAL issues
#         # All RESOLUTION_REQUIRED issues must be resolved
#         unresolved_required = [
#             i for i in self.validation_issues
#             if i.get('resolution_required') and not i.get('resolved')
#         ]
#         return len(unresolved_required) == 0
 
#     def get_issue_summary(self):
#         """Get summary of issues by severity and type."""
#         summary = {
#             'critical': 0,
#             'high': 0,
#             'medium': 0,
#             'info': 0,
#             'by_code': {}
#         }
        
#         for issue in self.validation_issues:
#             severity = issue.get('severity', 'INFO')
#             code = issue.get('code', 'UNKNOWN')
            
#             if severity == 'CRITICAL':
#                 summary['critical'] += 1
#             elif severity == 'HIGH':
#                 summary['high'] += 1
#             elif severity == 'MEDIUM':
#                 summary['medium'] += 1
#             else:
#                 summary['info'] += 1
            
#             if code not in summary['by_code']:
#                 summary['by_code'][code] = 0
#             summary['by_code'][code] += 1
        
#         return summary
 
#     def log_correction(self, field, original, corrected, user):
#         """Log a manual correction made by analyst."""
#         from django.utils import timezone
        
#         correction = {
#             'field': field,
#             'original_value': str(original),
#             'corrected_value': str(corrected),
#             'corrected_by_user_id': user.id,
#             'corrected_by_username': getattr(user, 'username', None) or getattr(user, 'email', str(user)),
#             'timestamp': timezone.now().isoformat()
#         }
        
#         if not self.correction_log:
#             self.correction_log = []
        
#         self.correction_log.append(correction)
#         self.save()
 
#     def get_corrections_count(self):
#         """Get total number of corrections made."""
#         return len(self.correction_log) if self.correction_log else 0
 
#     @property
#     def is_fully_validated(self):
#         """Check if validation is complete and reviewed."""
#         return self.validation_status == "CLEAN" or self.validation_rating in ["QUALIFIED", "ACCEPTABLE"]
 
#     @property
#     def quality_badge_class(self):
#         """Return Bootstrap badge class for rating."""
#         rating_map = {
#             'CLEAN': 'success',
#             'ACCEPTABLE': 'info',
#             'QUALIFIED': 'warning',
#             'UNRELIABLE': 'danger',
#         }
#         return rating_map.get(self.validation_rating, 'secondary')
 
#     def __repr__(self):
#         return f"<Statement id={self.id} file={self.original_filename} status={self.extraction_status} validation={self.validation_rating}>"
 
 

# class Transaction(models.Model):
#     statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name="transactions")
#     txn_date = models.DateField(null=True, blank=True)
#     value_date = models.DateField(null=True, blank=True)
#     txn_time = models.TimeField(null=True, blank=True)
#     narration_raw = models.TextField(blank=True)
#     debit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
#     credit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
#     balance = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
#     balance_type = models.CharField(max_length=2, blank=True)
#     reference = models.CharField(max_length=200, blank=True)
#     txn_mode = models.CharField(max_length=20, blank=True)
#     counterparty_name = models.CharField(max_length=255, blank=True)
#     source_row = models.IntegerField(null=True, blank=True)
#     quality_flag = models.CharField(max_length=100, blank=True)
#     bank_json_data = models.JSONField(null=True, blank=True)

    
#     def __str__(self):
#         return f"{self.txn_date} | {self.narration_raw[:30]}"
