from django.http import JsonResponse


def index(request):
    return JsonResponse({"app": "members", "status": "ready"})
