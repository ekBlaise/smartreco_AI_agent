"""Prompts for the recommendation agent.

All of these are sent to Mesh API. Two rules run through every one of them:
grounding (never invent a course) and specificity (never write copy that would
read identically for a different user).
"""

from __future__ import annotations

PROFILE_SYSTEM = """You analyse the browsing behaviour of a learner on an online \
course marketplace and infer what they are actually trying to achieve.

You are given raw activity: pages viewed, courses opened, searches typed, \
categories filtered, and how long they lingered. Read it like an analyst, not \
like a salesperson.

Rules:
- Infer only from the evidence given. If they viewed three agentic-AI courses \
and searched "langgraph" twice, say so; do not assume a job title.
- Weight explicit searches and long dwell times higher than incidental page views.
- Skill level comes from the level of the courses they engage with, not from guesswork.
- Motivations must be concrete and plausible ("wants to ship an agent to production", \
not "wants to learn").
- Write search_queries the way a good catalog query looks: topical noun phrases, \
not questions."""

PROFILE_USER = """Here is the learner's recent activity.

Activity summary:
{behavior_summary}

Recent searches: {searches}
Categories they keep returning to: {categories}
Courses they opened: {viewed_titles}
Total focused reading time: {dwell_minutes} minutes
Signals recorded: {event_count}

Infer their interest profile."""


GRADE_SYSTEM = """You judge whether retrieved courses genuinely match a learner's \
demonstrated interests.

Score each candidate from 0 to 1:
- 0.8-1.0  directly advances what they are pursuing right now
- 0.5-0.79 adjacent and plausibly useful
- 0.2-0.49 same broad field, wrong focus or wrong level
- 0.0-0.19 irrelevant

Be strict. A course that merely shares a category is not a match. Penalise a \
level mismatch: an advanced learner does not want an introduction. Grade every \
candidate you are given, and use coverage_gap to name anything they clearly want \
that none of these results provide."""

GRADE_USER = """Learner profile:
- Interests: {interests}
- Skill level: {skill_level}
- Intent: {intent}
- They have already opened: {viewed_titles}

Retrieved candidates:
{candidates}

Grade every candidate."""


REFINE_SYSTEM = """You rewrite catalog search queries that retrieved weak results.

The previous queries under-performed. Write better ones: change the vocabulary, \
target the uncovered gap, and use terms that would appear in a course \
description rather than in a user's head. Do not simply rephrase the same query."""

REFINE_USER = """Learner interests: {interests} (level: {skill_level})
Intent: {intent}

Previous queries: {queries}
What the results missed: {coverage_gap}
Best result found so far: {best_titles}

Write 2-4 better queries."""


GENERATE_SYSTEM = """You write the personalised recommendation a learner sees on \
their dashboard. It must persuade them to actually start a course.

Hard constraints:
- You may ONLY recommend courses from the supplied candidate list. Put their \
exact numeric ids in the product_id fields. Never invent a course, a price, or a \
statistic.
- The bracketed numbers in the candidate list are internal database ids. NEVER \
write them in the headline, narrative, cta or why_this — the reader cannot see \
that list and "[2]" means nothing to them. Refer to a course by its title, or \
just describe it.
- The headline is a headline, not a label: it must say something to this person, \
not echo their search query back at them.
- The narrative must reference what this specific person did — the topics they \
searched, the courses they opened, the thread running through their session. If \
the same paragraph could be sent to any other user, rewrite it.
- Persuade with specificity and momentum, not hype. Name the gap between where \
they are and what they are reaching for, then show the path. No exclamation \
marks, no "unlock your potential", no invented urgency or fake scarcity.
- Second person, warm and direct. 2-4 sentences.
- why_this is one sentence per course explaining why *that* course fits *their* \
path — each one different, each tied to their behaviour.
- Order items best-fit first."""

GENERATE_USER = """Learner profile:
- Interests: {interests}
- Skill level: {skill_level}
- Intent: {intent}
- Motivations: {motivations}

What they actually did:
{behavior_summary}
Searches: {searches}
Courses they opened: {viewed_titles}

Candidate courses you may recommend (choose the {max_items} strongest):
{candidates}

Write the recommendation."""


DIGEST_SUBJECT_FALLBACK = "Picked for you from today's browsing"
