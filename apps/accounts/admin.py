from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PhoneNumber, User

admin.site.register(User, UserAdmin)


@admin.register(PhoneNumber)
class PhoneNumberAdmin(admin.ModelAdmin):
    list_display = ("number", "user", "verified_at", "created_at")
    list_filter = ("verified_at",)
    search_fields = ("number", "user__username", "user__email")
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")
