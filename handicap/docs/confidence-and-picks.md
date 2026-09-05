# The five-pick component — what it can honestly promise

You asked for "five guaranteed or at least 80% chance picks" at -160 or better. I am
building the component. I am building it to a different promise, and you should know why
before you rely on it.

## Why 80% at -160 does not exist

- **-110 breaks even at 52.38%.** -160 breaks even at 61.5%.
- **An 80% win rate at -160 is a ~30% ROI.** In a market with millions of dollars of
  liquidity and professional syndicates pricing it, a repeatable 30% ROI is not a
  handicapping edge — it would be the best documented return in the history of sports
  betting, available every Saturday, five times a slate.
- **The real ceiling:** long-run professional ATS handicapping runs about **54–57%**. A
  56% season at -110 is an excellent year. Public "lock" services claiming 70%+ are
  selling the claim, not the record.
- **The odds filter you set is correct and I am keeping it.** Refusing -300 to -1000
  "guaranteed" prices is right — those are the market charging you a premium for the
  feeling of safety, and one loss erases eight wins. Your instinct there is sound. It just
  also means the 80% bets, which do exist at -400 and up, are exactly the ones your own
  filter excludes. Both halves of the request are individually reasonable and cannot both
  be satisfied.
- **Your own rules file already settled this:** *"Never say lock, guaranteed, or
  can't lose."* I am siding with CLAUDE.md.

## What the component will do instead

Same shape, same five cards, honest labels:

- **Rank the whole slate by estimated edge** — model win probability against the price
  implied by the best available number, and return the top 5.
- **Each card carries a real number**: estimated win probability, the implied breakeven at
  the offered price, the edge in points, and the price threshold — *"take at +6.5 or
  better; no value at +5.5."*
- **A confidence tier, not a guarantee**: 3 units / 2 units / 1 unit, plus the explicit
  "what kills this play" line CLAUDE.md already requires.
- **An honest header**: *"5 shown, 2 clear the bet-worthy threshold."* You still get the
  shortlist you asked for. You also get told when the slate is thin, which is most slates.
- **-160 or better enforced** as a hard filter, as you specified.

A realistic good card reads **estimated 57%, breakeven 52.4%, edge +4.6 pts**. That is a
strong bet. It is not an 80% bet, and a component that told you it was would be lying to
you five times a slate.

## Props

Same engine, separate panel, NFL-weighted as you said. Props deserve their own note: the
edges are genuinely larger than in sides and totals — the books price hundreds of props
per game with less attention per line — but the limits are lower and the variance is
higher. Realistic prop edges live in the 55–60% band, which is better than sides, and
still not 80%.

## Line anomaly flagging — your last point, and it is a good one

You described evenly-matched teams with an unexplained +8 spread as something to flag.
That is a real and underrated signal, and it is going in as its own label:

**LINE ANOMALY** — the market number sits well outside what the efficiency model, the
situational layer, and the injury report justify, with no visible cause.

Two things to hold onto about it:

1. It is a **question, not an answer**. An unexplained line usually means the market knows
   something the box score does not — a quarterback who will be inactive, a suspension
   that has not been reported, a team travelling with half a roster. In CFB especially,
   where availability reporting is unreliable, a weird line is more often information than
   error.
2. It only becomes a play when you can **name the missing reason and rule it out**. The
   flag opens an investigation. It does not close one.

This requires the efficiency layer (`efficiency` in the source registry) to be built
first — you cannot flag a deviation from a model you do not have yet.
