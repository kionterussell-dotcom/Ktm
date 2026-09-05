# X ingestion — what is actually viable

You asked for a straight answer instead of a scraper that pretends to work. Here it is.

## The three options

### 1. X API v2, paid tier
Pulling `GET /2/users/:id/tweets` for @BetMGMnews, @johnewing, @PatrickE_Vegas, @DaveMasonBOL.

- **Free tier does not work for this.** It is write-oriented; it does not give you a
  usable read of someone else's timeline. This is the single most common reason a
  "working" X scraper returns empty forever.
- **Basic** is the entry paid tier — on the order of **$200/month**, with a read cap
  around 10k posts/month. Four accounts posting a few times a day is far inside that cap,
  so the cap is not the problem. The price is.
- **Pro** is roughly **$5,000/month**. Irrelevant here.
- Cost per useful post is terrible: you are paying $200/month to automate maybe 10–15
  posts a week that actually move a read.
- Verify current pricing and endpoint access at developer.x.com before buying — X has
  repriced and re-gated these tiers repeatedly, and my figures may be stale.

### 2. Playwright driving a logged-in persistent profile
Technically works. I am not recommending it, for three reasons that are not hypothetical:

- It violates X's terms of service. The account at risk is **your** account.
- Automated-access detection on a logged-in session is aggressive. The realistic failure
  mode is not a clean error — it is a challenge screen or a shadow-limited timeline, which
  produces a **plausible-looking empty result**. That is exactly the failure your rules
  forbid: the desk would read "no liability posts this week" when the truth is "we got
  logged out on Tuesday."
- It breaks on every UI change, and X ships UI changes constantly.

### 3. Manual paste into `manual/` via `/paste`
Free, zero ban risk, and it cannot silently return empty — if you did not paste, the desk
says the source is missing, which is the correct and visible state.

## Recommendation: manual paste. Option 3.

Not as a consolation prize — it is genuinely the right call at your volume:

- **The volume is tiny.** These four accounts produce maybe 10–15 posts a week that carry
  a stated liability. That is a handful of pastes per slate, not a data pipeline.
- **You are already reading these timelines.** The marginal cost of pasting a post you
  just read is seconds. The marginal cost of the API is $2,400/year.
- **The valuable posts need a human to spot them anyway.** "We need the Chiefs to lose" is
  a BOOK NEED. "Great slate today folks" is not. A timeline puller ingests both and you
  end up reading the timeline regardless.
- **It fails visibly.** This is the deciding argument. Both automated paths fail quietly
  when they fail, and your desk rules treat a quiet failure as the worst outcome there is.

Revisit the API only if you find yourself pasting daily and resenting it. At that point
the $200/month buys real time back. Until then it buys nothing.

## What paste-in already gives you

`/paste` writes the content verbatim to `manual/YYYY-MM-DD-<source>.md`, extracts figures
into the `manual` table tagged with the account handle and post timestamp, and flags any
conflict with a fetched number for the same book — with the insider post winning, per
CLAUDE.md.

The `manual` table is deliberately **not** joined into `splits`. A stated liability from a
book employee and a scraped ticket% are different kinds of fact, and averaging them would
destroy the thing that makes the insider post valuable.

## Pikkit — same class, same answer

You said "Pikkit does the same as Action, I believe." Close, but the distinction matters
and CLAUDE.md already draws it:

- Action Network reports **book** data — tickets and handle at sportsbooks.
- Pikkit reports **tracked-bettor** data — the aggregated action of Pikkit's own users,
  who skew sharper and more engaged than the betting public.

They will often point the same way, and when they diverge that divergence is a signal in
itself. Blending them into one "public %" throws that away. Pikkit is app/Pro only with no
public feed, so it is paste-in, and it is stored as its own line, never merged into a book
split.
