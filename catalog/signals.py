from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import GuitarModel, GuitarPickup


@receiver(post_save, sender=GuitarModel)
def recompute_on_guitar_save(sender, instance, **kwargs):
    # recompute_derived() uses QuerySet.update(), which does NOT re-fire
    # post_save, so this can't recurse.
    instance.recompute_derived()


@receiver(post_save, sender=GuitarPickup)
@receiver(post_delete, sender=GuitarPickup)
def recompute_on_pickup_change(sender, instance, **kwargs):
    # Guard against the cascade case: if the parent guitar is itself being
    # deleted, there's nothing to recompute.
    guitar = GuitarModel.objects.filter(pk=instance.guitar_id).first()
    if guitar is not None:
        guitar.recompute_derived()
