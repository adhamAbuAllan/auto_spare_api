from pathlib import Path

from django.http import HttpResponse


def chat_tester(request):
    tester_path = Path(__file__).resolve().parent / "testing" / "chat_test.html"
    return HttpResponse(tester_path.read_text(encoding="utf-8"), content_type="text/html")
