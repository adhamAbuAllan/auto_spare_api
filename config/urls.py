"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from api.views import health, privacy_policy_page
from config.media import serve_media

urlpatterns = [
    path('', TemplateView.as_view(template_name='api/home.html'), name='home'),
    path('health/', health, name='root_health'),
    path('privacy-policy/', privacy_policy_page, name='privacy_policy'),
    path('api/', include('api.urls')),
    path('admin/', admin.site.urls),
]

if settings.MEDIA_URL:
    media_url = settings.MEDIA_URL
    if media_url.startswith("/"):
        media_url = media_url[1:]
    if media_url and not media_url.endswith("/"):
        media_url = f"{media_url}/"

    urlpatterns += [
        re_path(
            rf"^{media_url}(?P<path>.*)$",
            serve_media,
        )
    ]
