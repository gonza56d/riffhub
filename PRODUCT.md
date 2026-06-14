# Here it is the initial prompt:
```
We are starting to work on the web application "riffhub".
Riffhub is a web application (front->back->postgres) that works for guitarists.
The idea is to be like an old-fashioned "forum" in some way (musicians are creatures of habit), but at the same time it has to be modern and not outdated/outmoded.
This "modern forum" will be a huge guitar-gear & guitar-specs database, that will have each piece of gear under different classifications: Bridges, nuts, frets (number & material), number of strings (6, 7, 8, 9, 12), fretboards, necks, brands, scale length, body, pickups combinations, pickup models, and so on (note that there are pieces of gear like "bridges" but also guitar specs like "scale length" or "number of frets" -- I'm thinking that maybe *GEAR* can be independent objects by themselves, while *GUITAR SPECS* can be attributes of the different guitar models, but you're free to suggest your approach that you think is the best one).
The objective: Anyone can enter to our beautiful modern-vintage forum (looking modern-vintage while behaving fast like a modern application) and start filtering like: "I wanna see all the guitar models that exist, that are 24.75 inch scale length and have 7 string" (something very peculiar! finding a 7-string, 24.75" scale length guitar can be difficult, but not impossible! we're here to inform and help! :) ).
This place will also be very collaborative. While we will try to fill our database while we develop this, anyone that has signed up to our app can submit any piece of gear or entire guitar to our database too, then it falls under the category "under revision" that will not show up on users' new queries, but there will be a special section of our modern-vintage forum where anyone can collaborate and vote (possitive or negative) for any submitted gear piece or guitar. Users' profiles will have the typical score that anyone has on the typical web forums back then, this score will mark how participative and collaborative anyone is, and it should work both for posting/commenting information helping others (let's call this "the forum section") but also should score for uploading new stuff (gear and guitars) to the collaborative database (let's call this "the collab-db section") because I wanna make recognized to those who help making this big but also prevent trolls or people that upload gear without checking carefully --> This part is VERY important: I want this site to gain REPUTATION by giving precise and 100% true information regarding gear and guitar specs, so we have to warn and later temporarily prevent those who upload incorrect stuff to keep uploading misleading or incorrect things. "The Forum Section" is very participative and has freedom, you're even free to swear (but not free to upload illegal things or things unrelated to music/guitar while you still have free speech, the "ban" will not exists for insulting but you'll be responsible of your own reputation based on how you treat others -- freedom at work) but "The Collab-db Section" will be more restrictive in terms of truthness. In a nutshell: You can go and comment on someone else's post saying that his guitar is ugly if you want and no one can ban you (you could receive negative votes, though) or say that X artist is shit, but you cannot upload the "Ibanez RG" model into the database specifying that it is a 29-inch scale length guitar (other people that participates in the collab section will be able to fix/correct what you uploaded, or even reject it if it's very wrong -- and we have to be able to detect people that tries to upload troll stuff to the collab-db so they don't keep introducing noise to our collaborators).
Who can upload stuff to the collab-db: Anyone that has signed up and confirmed email.
Who can review stuff postulated in the collab-db: Anyone that has submited three pieces of gear or guitars that don't exist in the db and have been accepted by other collab-admins. (this number has to be configurable, because in the future, when the DB has a lot of information it might become very hard to think of three different stuff that has not been uploaded yet. A default value should not exist and raise an error if this is not configured).

The Forum Section will have this layout:
Topic -> Subtopic -> Post (title and body) -> Comment (body only)
Example:
Gear -> Guitars -> "Jackson Dinky VS Ibanez RG" -> "Oh I prefer Dinkies because..."
Gear -> Amps -> "EL34 or 6L6?" -> "Oh I prefer 6L6s!! Because..."
And so on...

Rules/behavior:
Posts and comments can be upvoted and downvoted (you cannot do both, if you downvote your upvote is removed and vice-versa). Also you cannot vote your own comments and posts. Posts and comments can also be reacted with any regular emojis, users can react with as many emojis as they want to any comment and post but they cannot react their own comment/post. Only one per type: they can react a smiling face and a heart, for example, but only one time each. Clicking again in the same one means you remove the reaction.
You can post images as blobs in our db (is it better to use blobs or better to use the Pillow library? Decide.), but videos have to be linked from an external platform like youtube.
You have free speech, meaning that you can even insult and you'll not be censored. But you cannot publish unrelated stuff: If you posted stuff that is acceptable by riffhub, but in the wrong category, Community Moderators and Riffhub Creators can move them to a different topic/subtopic, BUT, it's very different if you post something non related to guitar or music at all like football stuff: these will get deleted. Pornography (of any kind), selling guns or drugs, or some stuff really illegal like these will get you permanently banned. You have speech freedom, but cannot post illegal things (on posts, comments, or DMs). Also threatening someone to harm or death can lead you to be silenced and later banned if you continue (silences should be counted: the first one has to last one week, second one one month, third permanent silence and publicly flagged like so, so everyone knows this user cannot longer post, comment, or send DMs). What I mean: Imagine someone posts a video playing a guitar solo, it's not the same to have free-speech to comment the post saying "your playing on this video sucks" (this user is being rude, but the community is free to downvote him and give him bad reputation and he will have to deal with that) than saying "Tell me where you live at so we can fight" or "I will fucking kill you stupid." (these last examples are something very different where someone else is under danger of real life harm).
Selling stuff: It's ok in the indicated topic and subtopic, but when users barely enter these specific sections have to read and accept a condition where riffhub is not responsible of their buying or selling, or coordination with others for meetings and/or payments. Selling topic is a pre-defined one called "Gear Market" and pre-defined subtopics are: Guitars, Basses, Studio, Percussion, Other. Pieces of gear go under they "parent" instrument (for example, a guitar floyd rose goes under "Guitars"). When you post to these specific and special sections, the post unlocks a special field called "Price" that is dedicated specifically to indicate the price of what you're selling (posting) -- Comments do not need this field, if they offer less money they just comment it. This section has also the voting feature like any other regular post or comment.

Pre-defined/initial forum topics and subtopics:
| Gear:
| + Guitars
| + Basses
| + Percussion
| + Studio
| + Other
| State Of Art:
| + Metal
| + Blues
| + Classic Rock
| + Other
| Events:
| + Metal
| + Blues
| + Classic Rock
| + Other
| Gear Market:
| + Guitars
| + Basses
| + Studio
| + Percussion
| + Other
Notes on topics and subtopics:
- They are sorted by the amount of activity they have (most actives first). ANY action that a user does counts as a +1 activity, no matter what it is (posting, commenting, voting, reacting).
- New topics and subtopics can be submitted for community revision and votes. ANY NON ANNONYMOUS USER is able to vote here, but ONLY DATABASE COLLABORATORS OR HIGHER LEVELS can submit/propose new ones for votes. Submitted topics and subtopics last one week and they have to have a 75% or more positive votes. This feature can be disabled at any time by any Riffhub Creator.
- Count positive and negative votes individually, so we can decide at any time how do we display them for different stuff.


User levels:
- Annonymous user -> Not logged in. Can see the content, but cannot vote posts/comments/submittedgear, cannot post, cannot comment, cannot submit gear, cannot send DMs.
- Regular user -> logged in. Can do all the things that I said annonymous user cannot. (Still cannot vote for new submitted gear into the collab-db. But can try to upload gear into the collab-db and can vote posts and comments).
- Database Collaborator -> Can do all the things that a regular user can, plus they can vote for new submitted gear and submit a correction for the new incoming db-postulated gear. This is the type of profile that a regular user earns when they have submitted three(actually the configurable number) or more pieces of gear and these were accepted.
- Riffhub Community Founder -> A database collaborator that has uploaded 10 or more pieces of gear. I think 30 can be a good number to think of a quantity that is easy to achieve only during the first period of existence of riffhub, marking those who initially help to build this. At the same time 30 can become a difficult number to achieve if our initial db load-in is very effective, so we might need to think of a lower number later when we see how well did we initially populate our db, so make this number also configurable but leave it to 30 as default. A default value should not exist and raise an error if this is not configured. Also, this profile has to be toggleable so we can make it not achievable anymore (but still exist for those who earned it) and in this way we properly recognize our "elder" (in a good way!) collaborators that really put those firsts seeds of riffhub when we look back.
- Community Moderator -> All the power that a Riffhub Community Founder has, plus they are able to delete other users' comments, posts, warn them, and utimatelly ban them. Cannot ban Riffhub Creators.
- Riffhub Creator -> All the power that a Community Moderator has, plus they can: give and remove someone the Community Moderator profile, ban Community Moderators, create/edit/delete The Forum Section's categories and subcategories. Should have all the admin power.

Technology:
I want the backend to be Python, specifically the latest stable version of Django. In this way we can start this as a full stack application, using Docker Compose for local development and latest stable version of Postgres as database.
Frontend has to be lightweight. No jquery allowed. Let's decide if it's better to go with Native JS or some framework/lib, but I want it not to depend on any dedicated frontend server other than what Django serves as responses.
We have to start by deciding what categories do we have for gear. And after that when you finish the initial backend models and tables we have to start deciding the frontend fashion (remember: vintage-modern options!).
```

# Initial prompt finished. You can document anything else useful for you in the future below this section as we define this product.

---

# Engineering notes & decisions

*This section records **how** we build riffhub: the stack, decisions + rationale, and how to run things. The spec above remains the source of truth for **what** we're building.*

## Stack (decided & verified running)

- **Backend:** Django 6.0.6 (latest stable) on Python 3.12.
- **Database:** PostgreSQL 18 (latest stable) via psycopg 3.
- **Local dev:** Docker Compose — `web` (Django dev server) + `db` (Postgres). A local `venv/` mirrors deps for IDE/tooling and is gitignored.
- **Config:** environment-driven via django-environ — one settings module switched by **`DJANGO_ENV`** (`dev` | `prod`); see `.env.example` and `deploy/RENDER.md`. A **custom user model** (`accounts.User`) is set from the very first migration (changing it later is painful).
- **Frontend (decided & live):** Django server-rendered templates + **HTMX** for snappy partial page updates (no SPA, no separate front-end server) + small sprinkles of **Alpine.js**, hand-written CSS. No jQuery. Aesthetic: **Warm Vintage Workshop** (parchment + walnut + amber, slab-serif, faint paper grain). See the Frontend section below.

## Decision — environments, config & deployment (Render)

**One settings module** (`config/settings.py`), switched by **`DJANGO_ENV`** (`dev` default | `prod`). Rationale: keep the existing 12-factor, single-file style — no `settings/` package, no `DJANGO_SETTINGS_MODULE` juggling — while making the dev↔prod difference one explicit switch the operator sets per environment (`dev` locally, `prod` on the host).

**Host choice — Render, not PythonAnywhere.** We first targeted PythonAnywhere, but its affordable paid tier only offers **MySQL**, and riffhub leans on Postgres semantics MySQL can't reproduce — notably the **partial unique constraints** on proposal votes (`UniqueConstraint(condition=...)` in `forum`), which MySQL silently drops, plus utf8mb4/emoji storage and case-sensitivity differences. Rather than take on a risky DB migration for a barely-started app, we chose **Render**: fixed-price (no usage metering), managed **Postgres**, about as simple as PA.

- `DJANGO_ENV` drives `DEBUG` (still overridable via `DEBUG=`), and in `prod` turns on the security stack — `SECURE_PROXY_SSL_HEADER` (Render terminates TLS at its edge and forwards `X-Forwarded-Proto`), secure session/CSRF cookies, opt-in HSTS — plus stderr logging (Render captures it to the service log). `SECURE_SSL_REDIRECT` is left **off on Render** (the edge already forces HTTPS; a Django-level redirect would 301 the internal health check).
- **Fail loudly:** in `prod`, `SECRET_KEY`, `ALLOWED_HOSTS`, and `DATABASE_URL` have no defaults and raise `ImproperlyConfigured` if unset — the same "never silently run misconfigured" philosophy as the SiteConfiguration thresholds. `ALLOWED_HOSTS` also auto-includes Render's injected `RENDER_EXTERNAL_HOSTNAME`.
- The database is always `DATABASE_URL`-driven, so "dockerized local db vs Render's managed Postgres" is just a different URL; persistent connections (`CONN_MAX_AGE` + `CONN_HEALTH_CHECKS`) are on in prod.
- **No Docker in prod:** Render runs a plain **gunicorn/WSGI** app. **WhiteNoise** serves compressed, hashed static (`CompressedManifestStaticFilesStorage`) straight from the app. User-uploaded **media** is served by Django from a **Render persistent disk** at `MEDIA_ROOT` (Render's FS is otherwise ephemeral); S3-compatible object storage (R2/B2/S3 via django-storages) is the documented scale-up. The opt-in Compose `scheduler` becomes a Render **cron job** running `manage.py evaluate_pending`.
- Everything is described as code in **`render.yaml`** (web + Postgres + media disk + cron); deploy playbook in `deploy/RENDER.md`; env knobs in `.env.example`.

## Decision — image storage: filesystem + Pillow (not DB blobs)

The spec asks "blobs vs Pillow"; these aren't actually alternatives (Pillow *processes* images; storage is a separate axis). Decision: store image **bytes on disk** via Django's `ImageField`, and use **Pillow** to validate uploads (really-an-image, format allow-list, max dimensions) and to generate thumbnails. Rationale: keeps multi-MB binaries out of Postgres → smaller DB, cheap/fast backups, and an easy later move to S3-compatible object storage with no schema change. Videos remain external links (YouTube etc.) per the spec.

## Decision — catalog data model (confirmed with the product owner)

**Scope:** guitars only for v1 (the central `GuitarModel`), modelled so other instruments can be added later without a rewrite.

**Two deliberate halves:**

1. **Hand-entered specs = typed columns + FK controlled-vocabulary tables** on `GuitarModel` (the hot filter path): # strings, scale length (min/max — composable for multiscale), # frets, fret material, fretboard material, fretboard **radius** (composable: min/max + compound/flat flags), neck construction/material/profile, neck depth, nut width, body material/shape, headstock type, selector switch, country of origin. Vocabularies (`FretMaterial`, `BodyShape`, `FretboardRadius`, …) are small, indexed, community-extensible lookup tables, so filter dropdowns and referential integrity come for free.

2. **Gear = four typed catalog objects:** `Bridge`, `Pickup`, `Tuner`, `Nut` — exactly the components that *drive* guitar specs. (Strings became a plain "# of strings" spec; easy-swap hardware like knobs is out.) These are **typed models, not JSON**: with only four stable categories and the derived facets depending on their attributes, typed columns give the integrity riffhub's "100% true" goal needs. *(This supersedes an earlier JSON-schema idea.)*

**Derived facets — the truth guarantee.** Several filterable facets are *calculated* from the attached gear, never hand-typed, so they can't contradict the real components:

| Facet (stored + indexed on `GuitarModel`) | Computed from |
|---|---|
| `pickup_combination` (e.g. HSH), `electronics_type` (active/passive/mixed), `has_hum_cancellation` | the guitar's pickups (symbol, active flag, humbucking flag) |
| `has_tremolo`, `has_piezo` | the bridge |
| `has_locking_tuners` | the tuners |
| `neck_thickness_class` (thin/mid/thick) | neck depth measurement |
| `is_multiscale` | scale min ≠ max |

Facets are **denormalised into indexed columns** (filterable in SQL) and recomputed via signals whenever the guitar or its components change (`catalog/signals.py`, `GuitarModel.recompute_derived()`).

**Collab-db workflow (scaffolded; voting/corrections next).** Every catalog entity inherits `CatalogEntry` → `status` (under_revision / published / rejected) + `submitted_by` + review timestamps. Normal queries use `.published()`; the collab section will surface `.under_revision()`. Submission voting, corrections and reputation-driven promotion build on this base.

**Seed data.** `manage.py seed_catalog` loads reference vocabularies + 6 illustrative guitars (Strat, Les Paul, RG550, RG7321 7-string, LTD EC-1000 active, Strandberg multiscale) that exercise every facet. Idempotent; specs are starter/illustrative and community-correctable.

## User levels & reputation (`accounts`)

`Level` (IntegerChoices, directly comparable): Anonymous(0) < Regular(10) < Collaborator(20) < Founder(30) < Moderator(40) < Creator(50). `User.level` returns the highest applicable: granted flags (`is_riffhub_creator`, `is_community_moderator`) win, then the **sticky** `is_founder` badge, then config-driven Collaborator promotion (accepted-submission count ≥ threshold), else Regular. Crucially, when the thresholds are unset the derivation returns Regular — it never crashes and never silently promotes. `is_at_least(level)` powers permission checks; `add_reputation(n)` adjusts the score; `EmailConfirmation` (uuid token) gates collab submissions. `accounts.services.recompute_standing(user)` re-derives accepted counts from the catalog and awards the sticky Founder badge.

Reputation weights (starting values, could move to SiteConfiguration): post +2, comment +1, received up/down ±1 (forum), accepted catalog submission +10.

## Collab-db review workflow (`catalog`)

`ReviewVote` (generic +1/−1 on any catalog entry) and `Correction` (proposed fix) ride on the `CatalogEntry` status. `catalog.services`: `cast_review_vote` (Collaborators+ only, no self-vote), `evaluate_submission` (publishes when net votes ≥ `gear_acceptance_min_net_votes` and distinct voters ≥ `gear_acceptance_min_voters`; credits the submitter +1 accepted and +10 rep, recomputes standing), `reject_submission` (marks rejected, ticks the reject counter), `can_submit_to_collab` (needs confirmed email unless `REQUIRE_EMAIL_CONFIRMATION` is disabled; blocks after `max_rejected_before_cooldown` rejects — the troll guard). Acceptance/cooldown knobs live in `SiteConfiguration` (defaults 3 / 3 / 3).

## Forum domain (`forum`)

Hierarchy `Topic → Subtopic → Post(title+body) → Comment(body)`. **Comments thread one level deep**: a comment carries a self-FK `parent` and may reply to a *top-level* comment but never to a reply (`Comment.clean` enforces the single level + same-post check). Instead of nesting deeper, a reply (or any comment) may **tag** other users via `@username`; `forum.services._apply_mentions` resolves the handles at creation, keeps only **non-banned** accounts (a banned user can never be tagged), and stores them in `Comment.mentions`. The `comment_body` template filter (in `forum/templatetags/forum_extras.py`) renders the body HTML-escaped, links the stored mentions to profiles, and leaves unknown/banned handles as plain text. Generic `Vote` (up/down mutually exclusive, no self-vote, re-cast toggles off; positives and negatives counted **separately**), generic `Reaction` (one of each emoji per user per target, no self-react, toggle), generic `Attachment` (ImageField + Pillow validation: ≤ 5 MiB, ≤ 4096², JPEG/PNG/GIF/WEBP). Videos are external `video_url`s only. **Gear Market**: an `is_market` topic requiring a `MarketDisclaimerAcceptance`; market posts carry `price` + `currency` (enforced in `Post.clean`). Community `TopicProposal`/`SubtopicProposal` + `ProposalVote` (Collaborators+ may propose, any member votes; `evaluate_proposal` accepts on ≥ pass-ratio after the window, then materialises the real topic/subtopic). Every action bumps `activity_count` on the subtopic + its topic (default ordering is by activity). All rules live in `forum.services`; `manage.py seed_forum` loads the predefined topics/subtopics.

**Author self-deletion** (distinct from moderator *remove*): a `core.Deletable` mixin (`is_deleted`/`deleted_at`/`deleted_by` + `mark_deleted`) lets a user delete *their own* `Post`/`Comment` (`forum.services.delete_post`/`delete_comment`, author-only → `PermissionDenied` otherwise; soft, idempotent).
- A deleted **post** disappears for everyone but moderators — its body *and* its comments are inaccessible (`post_detail` 404s non-mods; `subtopic_detail` and comment counts exclude it). Moderators audit deletions at **`/deleted`** (`deleted:index` lists the whole live topic/subtopic tree with per-subtopic deleted-post counts; `deleted:subtopic` lists a subtopic's deleted posts and links to the normal detail page, which moderators can still open).
- A deleted **comment/reply** renders a "This message was deleted." placeholder for everyone, and its **reactions are preserved and shown** (display-only — voting/reacting on it 404s). The original body is **never sent to non-moderators** (it isn't in the page HTML); moderators and Creators reveal it on demand via the gated `forum:comment_original` endpoint ("Show Original Message"). Deleting a top-level comment keeps its replies intact under the placeholder.

## Moderation (`moderation`)

Free speech is the default (rudeness/insults aren't moderated); these tools target *unrelated/illegal* content and threats.
- **Content:** moderators *move* a mis-filed (but acceptable) post to another subtopic, or *soft-remove* off-topic content — hidden from public views, kept for audit, restorable. A `core.Moderatable` mixin adds the soft-delete fields to `Post`/`Comment`; public forum views hide removed content (moderators still see it, marked `[removed]`). This is separate from an author *deleting their own* content (`core.Deletable`; see the Forum domain) — `remove` is the moderator action, `delete` is the user's, and the two states are independent.
- **Users:** `warn`; `silence` — counted & escalating (**1 week → 1 month → permanent + publicly flagged**), and a silenced user can't post/comment (enforced in `forum.services.create_post`/`create_comment` via `can_participate`); `ban` — deactivates the account (`is_active=False`), with the rules that moderators **cannot** ban Creators and **only** Creators can ban Moderators.
- Everything is audited (`Warning` / `Silence` / `Ban` / `ContentAction`). Inline moderator controls live on post/comment pages (gated via the `is_moderator` context processor) and a `/moderation/` dashboard shows active silences/bans + the recent log. All rules live in `moderation.services`.

## Still to build / nice-to-have

All six core domains (catalog, collab workflow, accounts/levels, forum, moderation), their UIs, **user profile pages**, the **Creator topic/subtopic management UI** (`/forum/manage/`), and **direct messages** (`messaging` app, `/messages/` — canonical 1:1 conversations, unread badges via a context processor, silenced/banned users blocked from sending, "Send a message" from profiles) are built and verified. **Scheduled evaluation** is now in too: `forum.services.sweep_due_proposals()` + `catalog.services.sweep_pending_submissions()`, driven by a `evaluate_pending` management command and an opt-in `scheduler` Compose service (`docker compose --profile scheduler up`, interval `EVAL_INTERVAL_SECONDS`, default hourly) that resolves proposals whose voting window has closed and publishes submissions that have cleared the bar. Every spec feature and nice-to-have is built — including **DM reporting & moderation**: a participant reports another's message (`messaging.services.report_message`, participant-only, dedup'd), moderators review a queue at `/messages/reports/` (the privacy exception — they may read reported DM content) and either dismiss it or remove the message (soft-delete via `core.Moderatable`; removed DMs render `[removed by a moderator]` in the thread). Covered by `tests/test_dm_moderation.py` (22 tests).

## Direct messages (`messaging`)

`Conversation` (canonical 1:1 via `for_pair`, ordered by `user_low`/`user_high` pk) + `DirectMessage` (sender, body, `is_read`). `messaging.services`: `send_message` (blocks self-DM, empty body, and silenced/banned senders via `can_participate`), `mark_read`, `unread_count`, `inbox_rows`, `get_conversation`. Views (`/messages/`): inbox, a username-addressed thread (`/messages/u/<name>/`, get-or-creates + marks read), and an HTMX `send` endpoint. A `messaging_flags` context processor surfaces `unread_dm_count` for the nav badge. Covered by `tests/test_messaging.py` (39 tests).

## Frontend (decided & live)

Stack: **HTMX + Alpine.js** over Django templates, both vendored locally in `static/js/vendor/` (no CDN, no FE server, no jQuery). Aesthetic: **Warm Vintage Workshop** — parchment/walnut/amber palette, slab-serif headings, faint paper-grain overlay — in `static/css/riffhub.css` (CSS variables make re-skinning easy). `templates/base.html` is the shell.

**Dark theme (toggleable).** A dark counterpart to the light palette ships as `html[data-theme="dark"]` overrides of those same CSS variables (the light `:root` theme is left untouched; a few hard-coded light spots — header gradient, hover shadows, the confirm-banner/profile-flag boxes, error-red text — are patched). A nav toggle flips `<html data-theme>` instantly and persists the choice: a `theme` field on `User` for signed-in users (cross-device) **and** a long-lived cookie for everyone. `accounts.context_processors.theme` resolves the active theme server-side (`active_theme`, user → cookie → light) and renders it on the shell so there's no flash; the toggle POSTs to `accounts.set_theme`.

First page: the **catalog browse + filter** at `/` (`catalog/guitar_browse.html` + the `_guitar_results.html` partial, view `catalog.views.guitar_browse`). The filter form fires `hx-get` on change; the view returns just the results fragment for HTMX requests and the full page otherwise; `hx-push-url` keeps filtered URLs shareable. Facets: strings, scale, pickup layout, electronics, frets, neck, body shape, country, and the boolean feature flags (tremolo, locking tuners, hum-cancelling, piezo, multiscale, fretless). Verified live: `?strings=7` → RG7321, `?has_tremolo=1` → Strat + RG550, `?is_multiscale=1` → Strandberg, `?strings=7&scale=24.75` → empty (the rare-combo case riffhub exists to surface).

Next FE candidates: guitar detail page, forum views, a landing page, and auth (sign-up / login / email-confirm) screens.

## Configurable thresholds — no silent defaults

The spec requires several values to be **explicitly configured**, raising an error if unset (never a hidden fallback):
- collaborator-promotion threshold (accepted submissions),
- community-founder threshold (spec suggests 30) + founder-achievable on/off toggle,
- topic/subtopic-proposal feature toggle, its voting window (1 week) and pass ratio (75%).

These live in a `core.SiteConfiguration` singleton whose typed accessors raise `ImproperlyConfigured` when a required value is missing — directly satisfying *"a default value should not exist and raise an error if this is not configured."*

## Project layout

```
config/      Django project: settings, urls, wsgi/asgi
core/        Cross-cutting: SiteConfiguration, shared base models, voting/reaction mixins
accounts/    Custom User, reputation, derived levels + granted roles, email confirmation
catalog/     Collab-db: brands, gear, guitar models + specs, submit/review/vote/correct workflow
forum/       Topics, subtopics, posts, comments, votes, emoji reactions, Gear Market
moderation/  Warnings, silences (1w / 1m / permanent), bans, content moves
```

## How to run

```
cp .env.example .env
docker compose up --build               # web -> http://localhost:8000 , db -> :5432
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
# Local tooling via venv (point DATABASE_URL at localhost first — see .env.example):
./venv/bin/python manage.py <command>
```

**Production (no Docker):** set `DJANGO_ENV=prod` and deploy to **Render** (managed Postgres, fixed-price) — `render.yaml` is the Blueprint. Full walkthrough: `deploy/RENDER.md`.
