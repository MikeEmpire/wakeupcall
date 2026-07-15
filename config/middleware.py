from django.http import JsonResponse


class LoadBalancerHealthCheckMiddleware:
    """Answer only the ALB process check before host and HTTPS validation."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path == "/health/"
            and request.META.get("HTTP_USER_AGENT") == "ELB-HealthChecker/2.0"
        ):
            return JsonResponse({"status": "ok"})
        return self.get_response(request)
