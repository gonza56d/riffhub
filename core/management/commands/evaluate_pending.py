"""Run the proposal and submission evaluation sweeps.

This command is the scheduled entry point for the periodic evaluation work:

* forum proposals whose voting window has closed are tallied and
  accepted/rejected (``forum.services.sweep_due_proposals``), and
* collab-db submissions that have accrued enough review votes are published
  (``catalog.services.sweep_pending_submissions``).

It is invoked on a loop by the opt-in ``scheduler`` Docker Compose service
(see ``docker-compose.yml``), but is safe to run by hand:
``manage.py evaluate_pending``.

Each sweep runs independently: if one raises, its failure is reported on
stderr and the other sweep still runs.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Run the periodic evaluation sweeps: resolve due forum proposals and "
        "publish collab-db submissions that have cleared the acceptance bar."
    )

    def handle(self, *args, **options):
        try:
            from forum.services import sweep_due_proposals

            props = sweep_due_proposals()
            self.stdout.write(
                self.style.SUCCESS(
                    "Proposals: evaluated={evaluated} accepted={accepted} "
                    "rejected={rejected}".format(**props)
                )
            )
        except Exception as exc:  # noqa: BLE001 — one sweep must not block the other
            self.stderr.write(f"Proposal sweep failed: {exc!r}")

        try:
            from catalog.services import sweep_pending_submissions

            subs = sweep_pending_submissions()
            self.stdout.write(
                self.style.SUCCESS(
                    "Submissions: evaluated={evaluated} "
                    "published={published}".format(**subs)
                )
            )
        except Exception as exc:  # noqa: BLE001 — one sweep must not block the other
            self.stderr.write(f"Submission sweep failed: {exc!r}")
