"""Reply control placement on catalog detail pages.

The per-comment "Reply" box must render directly under its parent comment and
*before* that comment's replies, so a user with many replies doesn't have to
scroll past all of them to reply. This guards the template ordering in
``templates/catalog/_catalog_comments.html``.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog import services
from catalog.constants import PublicationStatus
from catalog.models import Brand, GuitarModel
from core.models import SiteConfiguration

User = get_user_model()


def make_user(username, **flags):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com",
        password="pw-12345", email_confirmed=True, **flags,
    )


def make_guitar(brand, name):
    return GuitarModel.objects.create(
        brand=brand, name=name, num_strings=6,
        scale_length_min_inches=Decimal("25.5"),
        scale_length_max_inches=Decimal("25.5"),
        status=PublicationStatus.PUBLISHED,
    )


class CatalogReplyPlacementTests(TestCase):
    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()
        self.brand = Brand.objects.create(name="Acme", status=PublicationStatus.PUBLISHED)
        self.guitar = make_guitar(self.brand, "GTR-1")
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def test_reply_box_renders_before_the_replies(self):
        comment = services.add_catalog_comment(
            target=self.guitar, author=self.alice, body="Top-level comment"
        )
        services.add_catalog_comment(
            target=self.guitar, author=self.bob, body="A reply", parent=comment
        )

        self.client.force_login(self.alice)
        resp = self.client.get(reverse("catalog:detail", args=[self.guitar.pk]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode()
        # The reply box must appear before the rendered reply body.
        self.assertIn('class="catalog-reply-box"', html)
        self.assertIn('class="catalog-reply"', html)
        self.assertLess(
            html.index('class="catalog-reply-box"'),
            html.index('class="catalog-reply"'),
        )
