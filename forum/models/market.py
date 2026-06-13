from django.conf import settings
from django.db import models
from django.utils import timezone


class MarketDisclaimerAcceptance(models.Model):
    """Records that a user accepted the Gear Market disclaimer.

    PRODUCT.md: before participating in the selling section a user must read
    and accept that "riffhub is not responsible for sales/coordination/
    payments". One acceptance per user is enough; ``accepted_at`` timestamps
    when they agreed.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="market_disclaimer_acceptance",
    )
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Market disclaimer acceptance"
        verbose_name_plural = "Market disclaimer acceptances"
        ordering = ["-accepted_at"]

    def __str__(self) -> str:
        return f"{self.user} accepted the Gear Market disclaimer"
