from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (
    Bridge,
    BridgeType,
    GuitarModel,
    GuitarPickup,
    Pickup,
    PickupType,
    Tuner,
)


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


def _recompute(guitars) -> None:
    """Recompute derived facets for every guitar in ``guitars``.

    recompute_derived() persists via QuerySet.update() (no save/signal), so this
    never re-fires the receivers below — no recursion even though it writes
    GuitarModel rows.
    """
    for guitar in guitars:
        guitar.recompute_derived()


# A guitar's denormalised facets are computed from its attached components, so
# *editing a shared component in place* (the natural collab-db "correction"
# path) must re-derive every guitar that references it — otherwise the indexed
# facet columns silently go stale and contradict the real component (the
# "facets can't contradict reality" guarantee in PRODUCT.md). The GuitarModel /
# GuitarPickup receivers above only cover the guitar and its pickup *links*;
# these cover the components (and the vocab types that drive their flags).
@receiver(post_save, sender=Bridge)
def recompute_on_bridge_change(sender, instance, **kwargs):
    _recompute(GuitarModel.objects.filter(bridge=instance))


@receiver(post_save, sender=BridgeType)
def recompute_on_bridge_type_change(sender, instance, **kwargs):
    _recompute(GuitarModel.objects.filter(bridge__bridge_type=instance))


@receiver(post_save, sender=Tuner)
def recompute_on_tuner_change(sender, instance, **kwargs):
    _recompute(GuitarModel.objects.filter(tuners=instance))


@receiver(post_save, sender=Pickup)
def recompute_on_pickup_edit(sender, instance, **kwargs):
    _recompute(
        GuitarModel.objects.filter(guitar_pickups__pickup=instance).distinct()
    )


@receiver(post_save, sender=PickupType)
def recompute_on_pickup_type_change(sender, instance, **kwargs):
    _recompute(
        GuitarModel.objects.filter(
            guitar_pickups__pickup__pickup_type=instance
        ).distinct()
    )
