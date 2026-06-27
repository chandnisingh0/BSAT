from django.urls import path
from . import views
from . import beneficiary_views

urlpatterns = [
    # ===== EXTRACTION & TRANSACTIONS =====
    path("", views.upload_view, name="upload"),
    path("statement/<int:statement_id>/", views.transactions_view, name="transactions"),
    path("api/extraction-status/", views.extraction_status_api, name="extraction_status_api"),
    path("statement/<int:statement_id>/cancel/", views.cancel_extraction_view, name="cancel_extraction"),

    # ===== DELETE TRANSACTION & STATEMENT =====
    path('statement/<int:statement_id>/transaction/<int:transaction_id>/delete/', views.delete_transaction_view, name='delete_transaction'),
    path('statement/<int:statement_id>/delete/confirm/', views.confirm_delete_statement_view, name='confirm_delete_statement'),
    path('statement/<int:statement_id>/delete/', views.delete_statement_view, name='delete_statement'),

    # ===== CLEANING & VALIDATION =====
    path('statement/<int:statement_id>/clean/', views.cleaning_dashboard_view, name='cleaning_dashboard'),
    path('statement/<int:statement_id>/clean/issues/', views.cleaning_issues_view, name='cleaning_issues'),
    path('statement/<int:statement_id>/clean/transaction/<int:transaction_id>/', views.cleaning_transaction_detail_view, name='cleaning_transaction_detail'),
    path('statement/<int:statement_id>/clean/resolve/<str:issue_id>/', views.resolve_issue_view, name='resolve_issue'),
    path('statement/<int:statement_id>/clean/export/', views.export_cleaned_data_view, name='export_cleaned'),

    # ===== BENEFICIARY IDENTIFICATION (STEP 4A) =====
    path('statement/<int:statement_id>/beneficiary/', beneficiary_views.beneficiary_dashboard_view, name='beneficiary_dashboard'),
    path('statement/<int:statement_id>/beneficiary/start/', beneficiary_views.start_beneficiary_identification_view, name='start_beneficiary_identification'),
    path('statement/<int:statement_id>/beneficiary/review-queue/', beneficiary_views.analyst_review_queue_view, name='analyst_review_queue'),
    path('statement/<int:statement_id>/beneficiary/assign/<int:transaction_id>/', beneficiary_views.assign_beneficiary_view, name='assign_beneficiary'),
    path('statement/<int:statement_id>/beneficiary/ledger/', beneficiary_views.counterparty_ledger_view, name='counterparty_ledger'),
    path('statement/<int:statement_id>/beneficiary/counterparty/<int:counterparty_id>/', beneficiary_views.counterparty_detail_view, name='counterparty_detail'),
    path('statement/<int:statement_id>/beneficiary/counterparty/<int:counterparty_id>/approve/', beneficiary_views.approve_counterparty_view, name='approve_counterparty'),
    path('statement/<int:statement_id>/beneficiary/ledger/export/', beneficiary_views.export_counterparty_ledger_view, name='export_counterparty_ledger'),
]

# from django.urls import path
# from . import views

# urlpatterns = [
#     path("", views.upload_view, name="upload"),
#     path("statement/<int:statement_id>/", views.transactions_view, name="transactions"),
#     path("api/extraction-status/", views.extraction_status_api, name="extraction_status_api"),
#     path("statement/<int:statement_id>/cancel/", views.cancel_extraction_view, name="cancel_extraction"),

#     # for delete transaction
#     path('statement/<int:statement_id>/transaction/<int:transaction_id>/delete/', views.delete_transaction_view, name='delete_transaction'),
#     path('statement/<int:statement_id>/delete/confirm/', views.confirm_delete_statement_view, name='confirm_delete_statement'),
#     path('statement/<int:statement_id>/delete/', views.delete_statement_view, name='delete_statement'),

#     # validation and cleaning
#     path('statement/<int:statement_id>/clean/', views.cleaning_dashboard_view, name='cleaning_dashboard'),
#     path('statement/<int:statement_id>/clean/issues/', views.cleaning_issues_view, name='cleaning_issues'),
#     path('statement/<int:statement_id>/clean/transaction/<int:transaction_id>/', views.cleaning_transaction_detail_view, name='cleaning_transaction_detail'),
#     # path('statement/<int:statement_id>/clean/resolve/<str:issue_code>/', views.resolve_issue_view, name='resolve_issue'),
#     path('statement/<int:statement_id>/clean/resolve/<str:issue_id>/', views.resolve_issue_view, name='resolve_issue'),
#     path('statement/<int:statement_id>/clean/export/', views.export_cleaned_data_view, name='export_cleaned'),
# ]
