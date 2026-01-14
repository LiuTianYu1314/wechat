from django.contrib import admin
from django.urls import path, include
from wecom_ai_bot import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('callback/', views.wecom_callback, name='wecom_callback'),# 企业微信回调路由
]