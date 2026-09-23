# Gumroad page rewrite — High Protein House Cookbook

Written 2026-09-23 after auditing the live page at
`proteinpalace.gumroad.com/l/highprotein`. Everything here is ready to paste
into the Gumroad product editor. Nothing in this file is applied automatically;
there is no Gumroad API connector on this account.

## What the page looks like today

| | Current | Problem |
|---|---|---|
| Description | 17 words | The entire sales page is one sentence |
| Interior preview | none | People buy cookbooks with their eyes |
| Reviews | 0 | Handled separately, not in this doc |
| Refund policy | not set | Nothing removes the "is this junk?" risk |
| Button | "Download" | Reads as free, then asks for money |
| Price | $10, no anchor | A bare number with no frame |

The deeper issue is not on the page: the cookbook holds 18 recipes while the
TikTok rotation posts 110 for free, with full ingredients and full method on
slides 2 to 4. See "The offer question" at the end.

---

## 1. Product description

Paste into the description field. Gumroad's editor accepts rich text, so the
headings and bullets below survive a paste from rendered markdown.

> ### 18 high-protein dinners you'll actually cook twice
>
> Every recipe is 30g+ of protein, real supermarket ingredients, and done in
> under 20 minutes. No powders pretending to be food, no 14-item shopping
> lists, no "one cup of cottage cheese" as a personality.
>
> **What's inside**
>
> - 18 complete recipes — breakfast, lunch, dinner and snacks
> - Macros on every page: calories, protein, carbs, fat
> - Exact quantities, so you can shop without doing maths
> - A printable one-page shopping list per recipe
> - Written for a normal kitchen: one pan, one oven, no sous vide
>
> **Who it's for**
>
> You lift, or you're trying to eat more protein, and you're bored of eating
> the same chicken and rice five days a week. You want food you'd serve someone
> else, that happens to hit your macros.
>
> **"Why pay when you post recipes free?"**
>
> Fair question. The free posts are one recipe at a time, in the order the
> algorithm feels like showing you. This is all 18 in one PDF, on your phone,
> in the shop, offline, sorted by meal, with the macros already worked out.
> You're paying for it to be in one place when you need it.
>
> **$10. Instant download. Keep it forever.**
>
> If you cook one recipe from it, it's paid for itself against one takeaway.
>
> 30-day money back, no questions. Email and it's refunded.

**Notes on the above**

- The "why pay when you post free" section is deliberate. That objection is
  the single biggest reason a TikTok follower doesn't buy, and answering it
  out loud converts better than pretending it isn't there.
- Do not claim a recipe count you don't have. If the expanded PDF ships, the
  headline number changes and this copy needs one edit.
- No health claims. "High protein" and "macros" are descriptive. Avoid
  anything that promises weight loss or a body outcome.

## 2. Button text

Change from `Download` to:

```
Get the cookbook — $10
```

Gumroad calls this the "call to action" setting on the product. "Download"
reads as a free file and creates a small jolt when payment appears.

## 3. Refund policy

Gumroad has a built-in refund policy field. Set it to 30 days and use:

```
30-day money back guarantee. If the recipes aren't for you, email us within
30 days and we'll refund you in full. You keep the PDF.
```

On a $10 digital product, refund requests are rare and the guarantee lifts
conversion more than it costs. "You keep the PDF" removes the last hesitation
and costs nothing to honour.

## 4. Price anchor

Two options, in order of preference.

1. **Set the price to $19 and run a permanent "$10 today" discount.** Gumroad
   shows the strike-through. This is the standard approach and it works, but
   only do it if $19 is a price you would genuinely charge.
2. **Anchor in copy instead.** Keep $10 and lean on the takeaway comparison
   already in the description. Honest, no fake discount, slightly weaker.

Pick 1 only if the answer to "would we ever sell this at $19?" is yes. A
permanent fake discount is the kind of thing that erodes trust with an audience
you are trying to build.

## 5. Interior preview images

The single highest-impact change on the page, and the one that needs someone
with the PDF open.

**Pick 3 pages:**

1. A **full recipe page** showing the layout — photo, ingredients, method,
   macros. Choose the best-looking dish.
2. The **contents page**, so a buyer can see all 18 at a glance. If there
   isn't one, this is worth making.
3. A **second recipe page** from a different meal type, to show range.

**How:** open the PDF, screenshot each page at full width, save as PNG, upload
to the Gumroad product as additional images (they appear in the gallery under
the cover). Do not use phone photos of a screen.

**Avoid:** the title page, the copyright page, and any page that is mostly
white space. They tell a buyer nothing.

Source PDFs live in Drive under `CookBook / 01-Cookbook-PDFs`.

---

## The offer question (bigger than the page)

There are two PDFs in Drive:

| File | Size | Created |
|---|---|---|
| The High Protein House V1.pdf | 164 MB | 2026-05-27 |
| The High Protein House — Expanded Version.pdf | 467 MB | 2026-06-15 |

V1 matches the 18-recipe structure doc and is presumably what's being sold.
Nobody has said what's in the expanded version. **If it holds substantially
more than 18 recipes, shipping it is the cheapest fix available to this
funnel** — it turns "18 recipes for $10 while we post 110 free" into a real
offer, and the description above needs one number changed.

If the expanded version is not substantially bigger, then the offer needs to
change shape rather than grow. The strongest candidate is to stop selling
recipes and start selling the thing the free feed can never assemble: a
28-day plan, weekly shopping lists, macro targets, and a prep schedule.
Scrolling TikTok never turns into a plan by itself.

That decision is worth making before spending more on traffic.
