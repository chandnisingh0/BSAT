from django.urls import path
from . import views
from . import admin_views

urlpatterns = [
    path("login/",            views.login_view,            name="login"),
    path("logout/",            views.logout_view,            name="logout"),
    path("change-password/",  views.change_password_view,  name="change_password"),

    path("manage/users/", admin_views.user_list_view, name="admin_user_list"),
    path("manage/users/create/", admin_views.user_create_view, name="admin_user_create"),
    path("manage/users/<int:user_id>/", admin_views.user_detail_view, name="admin_user_detail"),
    path("manage/users/<int:user_id>/reset-password/", admin_views.admin_reset_password_view, name="admin_reset_password"),
    path("manage/users/<int:user_id>/deactivate/", admin_views.user_deactivate_view,      name="admin_user_deactivate"),
    path("manage/users/<int:user_id>/reactivate/", admin_views.user_reactivate_view, name="admin_user_reactivate"),
    path("manage/audit-log/", admin_views.audit_log_view, name="admin_audit_log"),

    path("manage/engagements/", admin_views.engagement_list_view, name="admin_engagement_list"),
    path("manage/engagements/create/", admin_views.engagement_create_view, name="admin_engagement_create"),
    path("manage/engagements/<int:engagement_id>/assign/", admin_views.engagement_assign_view, name="admin_engagement_assign"),
    path("manage/engagements/<int:engagement_id>/add-account/", admin_views.engagement_add_account_view, name="admin_engagement_add_account"),
]
