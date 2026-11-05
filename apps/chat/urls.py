from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("", views.home, name="home"),
    path("chat/", views.index, name="index"),
    path("guide/", views.guide, name="guide"),
    path("api/chat/", views.chat, name="api"),
]
