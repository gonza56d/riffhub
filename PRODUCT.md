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
Initial prompt finished. You can document anything else useful for you in the future below this section.
