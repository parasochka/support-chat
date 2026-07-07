"""Starter (baseline) knowledge base for freshly created products.

When a partner adds a new casino product, its chat must work out of the box —
BEFORE the owner translates and uniquifies the content for that brand. This
module holds a generic, brand-neutral topic catalogue + KB texts that answer
the routine questions any online casino player asks, without asserting a
single brand-specific fact: no brand names, no URLs, no concrete amounts,
fees, limits, or timeframes. Wherever a real casino would have a number, the
text points the player to the place in the product UI where that number is
authoritative (the cashier, the promotion terms, the game info panel), so the
assistant is helpful yet never invents or inherits another brand's values.

Deliberately NOT a copy of any live product's KB: the content here is written
from scratch as the lowest common denominator of casino support. The live
(default) product's bespoke KB stays untouched — this seed runs only in
`db.create_product` for NEW products, never at boot, and only inserts topics
the product does not already have.

KB texts are English (the model-facing prompt language; the Layer-3 language
directive makes the model answer in the player's language regardless). Topic
titles ship in the five languages the translations registry covers.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Starter topics: (slug, {lang: title}, kb_text). Order = display order.
# Seven topics, mirroring the live picker layout: six specialized ones plus
# `other` — the always-available catch-all, a NORMAL visible topic like the
# rest (no topic is ever hidden) that sorts last and carries its own KB.
# ---------------------------------------------------------------------------

_DEPOSITS = """\
This topic covers adding funds to the casino account.

Q: How do I make a deposit?
A: Log in, open the cashier (the "Deposit" button), choose a payment method,
enter the amount and confirm. The methods available to you, with their limits,
are listed on the deposit page — they can differ by country and currency.

Q: What is the minimum / maximum deposit?
A: Limits depend on the payment method. The exact minimum and maximum are
shown next to each method on the deposit page before you confirm the payment.

Q: How fast is a deposit credited?
A: Card and e-wallet deposits are usually credited within minutes.
Cryptocurrency deposits are credited after the required network
confirmations, which normally takes a few minutes but depends on the network.

Q: Is there a deposit fee?
A: Any fee is always shown in the cashier before you confirm the payment —
check the total on the confirmation screen.

Q: I paid but the deposit has not arrived. What do I do?
A: First check the payment status on the side of your bank, wallet or
exchange. For crypto, verify you sent to the exact address and network shown
on the deposit page and that the transaction has enough confirmations. If the
payment is confirmed on the sender's side but still not credited, this needs
a human operator: the player should contact support with the transaction id
or a payment receipt.

Q: Which crypto network should I use?
A: Only the network shown next to the deposit address on the deposit page.
Sending on a different network can lead to a permanent loss of funds. Always
take a fresh address from the deposit page before each transfer.

Q: Can I deposit from someone else's card or wallet?
A: No. Only your own payment instruments can be used — third-party payments
are not accepted, for security and anti-money-laundering reasons.

Q: Where do I enter a promo code?
A: If a promotion requires a code, there is a promo-code field in the cashier
on the deposit step — enter the code before confirming the payment.

Q: What currency will my deposit be in?
A: The account currencies, and any conversion rate applied, are shown in the
cashier before you confirm — review the final amount on the confirmation
screen.

Q: Can I deposit without taking a bonus?
A: Yes — if a deposit bonus is offered, you can decline it in the cashier or
on the bonus selection step before paying.
"""

_WITHDRAWALS = """\
This topic covers withdrawing winnings and managing payout requests.

Q: How do I withdraw money?
A: Open the cashier and choose "Withdraw", pick a payout method, enter the
amount and your payment details, and confirm. Available methods and their
limits are listed on the withdrawal page.

Q: Why do I have to withdraw to the same method I deposited with?
A: Most casinos apply a closed-loop (return-to-source) rule: winnings go back
to the payment method used for depositing, as an anti-fraud and
anti-money-laundering measure. If your deposit method cannot receive payouts,
the withdrawal page will offer the allowed alternatives.

Q: How long does a withdrawal take?
A: A withdrawal first passes an internal review, then the payment provider
processes it. The current status is visible in your transaction history.
Processing times differ by method — crypto payouts are usually the fastest.

Q: What does the "pending" status mean?
A: The request is still in the internal review stage. While it is pending it
can usually still be cancelled, and the money returns to your balance.

Q: Can I cancel a withdrawal?
A: While the request is pending, yes — either from the transaction history or
by asking support. After it is sent to the payment provider it can no longer
be cancelled.

Q: Why was my withdrawal declined?
A: The usual reasons: bonus wagering is not finished yet, the account is not
verified, the payout details don't match the account holder, or a limit was
exceeded. The transaction history shows the reason where available; if it is
unclear, this needs a human operator to check the specific request.

Q: What are the withdrawal limits?
A: Minimums, maximums and any daily/weekly caps depend on the method and are
shown on the withdrawal page next to each method.

Q: Is there a withdrawal fee?
A: Any fee is shown before you confirm the request. For crypto payouts the
network fee depends on the blockchain, not the casino.

Q: Why am I asked to verify my account before withdrawing?
A: Identity verification (KYC) before the first payout is a standard
licensing requirement. Complete the verification in your profile — after
approval the withdrawal proceeds normally.

Q: My withdrawal is taking longer than usual.
A: Check the request status in the transaction history first. If it has been
processed on the casino side, the remaining time depends on the payment
provider. If the status has not changed for an unusually long time, a human
operator should look into the specific request.
"""

_BONUSES = """\
This topic covers bonuses, free spins, promotions and wagering.

Q: Where can I see the current promotions?
A: On the promotions page of the site. Each promotion has its own terms page
with the exact amounts, requirements and dates — those terms are the single
source of truth for that offer.

Q: How do I activate a bonus?
A: Depending on the promotion: select it in the cashier before depositing,
enter its promo code, or opt in on the promotion page. The activation steps
are described in the promotion's terms.

Q: What is wagering?
A: A wagering (playthrough) requirement is the total amount you must bet
before bonus money or bonus winnings can be withdrawn. For example, a x30
wager on a bonus means betting thirty times the bonus amount. The exact
multiplier, eligible games and time limit are in the promotion's terms.

Q: Do all games count towards wagering equally?
A: Usually not — different game categories contribute different percentages
(slots typically contribute the most, table and live games less or nothing).
The exact weighting table is part of the promotion's terms.

Q: I didn't receive my bonus. Why?
A: Common reasons: the deposit was below the promotion's minimum, the promo
code was not entered, another bonus is already active, or the bonus was
declined at the cashier. Check the promotion's terms first; if everything
looks right and the bonus is still missing, a human operator should review
the account.

Q: What is the difference between real and bonus balance?
A: Real money is yours and can be withdrawn at any time. Bonus funds are
credited by a promotion and become withdrawable only after the wagering
requirement is met. Bets usually spend real money first — the exact order is
in the bonus terms.

Q: Can I cancel an active bonus?
A: Usually yes, from the bonus section of your profile or via support. Note
that cancelling an active bonus normally voids the bonus and any winnings
made with it.

Q: How do free spins work?
A: Free spins are credited to specific games listed in the promotion. Their
winnings are usually credited as bonus funds with their own wagering
requirement — see the promotion's terms for the exact rules.

Q: Can I combine several bonuses?
A: Typically only one deposit bonus can be active at a time. Whether offers
can be combined is defined in each promotion's terms.
"""

_ACCOUNT = """\
This topic covers registration, login, password, profile, account safety and
identity verification (KYC).

Q: How do I register?
A: Press the sign-up button, fill in the registration form and confirm your
contact details. You must be of legal gambling age in your jurisdiction and
provide accurate information — the details are checked during verification.

Q: Can I have more than one account?
A: No. One account per person is a standard rule; duplicate accounts can be
blocked and their bonuses voided.

Q: I can't log in. What should I check?
A: Verify the email/username and password (mind keyboard layout and Caps
Lock), then try resetting the password via "Forgot password". After several
failed attempts the login can be temporarily locked — wait a bit and try
again. If access still fails, a human operator should check the account.

Q: How do I reset my password?
A: Use the "Forgot password" link on the login form — a reset link is sent to
the account email. If the email doesn't arrive, check the spam folder and
that the address is the one used at registration.

Q: How do I change my email, phone or other profile data?
A: Contact details can usually be edited in the profile settings. Data
confirmed by identity verification (name, date of birth) is locked and can
only be changed through support with supporting documents.

Q: How do I enable two-factor authentication?
A: If the casino offers 2FA, it is enabled in the profile's security
settings. Using it is strongly recommended.

Q: How do I set deposit or betting limits, or take a break from playing?
A: Responsible-gaming tools — deposit, bet, loss and session limits, time-out
and self-exclusion — are available in the account settings or through
support. If a player says they have trouble controlling their play, this must
be handed to a human operator right away.

Q: How do I close my account?
A: Ask support to close it. Any active bonuses are closed and the remaining
balance is paid out according to the withdrawal rules before the account is
deactivated.

Q: Why is my account blocked?
A: Accounts can be restricted during verification checks or for terms
violations. The specific reason for a block can only be clarified by a human
operator — the player should contact support.

Q: What is verification and why is it required?
A: Identity verification (KYC) confirms your identity, age and payment
details. It is a licensing requirement for all regulated casinos and protects
the account owner from fraud.

Q: When do I have to verify?
A: Typically before the first withdrawal, and additionally whenever a check
is triggered by security rules. The site prompts you when verification is
needed.

Q: What documents are needed?
A: Usually a government-issued photo ID (passport, national ID or driving
licence) and a proof of address (a recent utility bill or bank statement). A
selfie with the document can also be requested. The exact list is shown on
the verification page.

Q: How do I upload documents?
A: In your profile's verification section: photograph or scan each document
and upload it. The document must be fully visible, all corners in frame,
text readable, no glare, and not expired.

Q: How long does the review take?
A: Documents are reviewed in the order received; the verification page shows
the current status. If the review takes unusually long, a human operator can
check the case.

Q: Why were my documents rejected?
A: Common reasons: blurry or cropped photo, glare over the data, an expired
document, or a name/address that doesn't match the account details. Re-upload
a corrected photo; if the reason is unclear, ask a human operator.

Q: What is a source-of-funds check?
A: For large amounts, regulations may require proof of where the money comes
from — e.g. a payslip, bank statement or proof of crypto origin. This is a
standard enhanced check, handled individually with support.

Q: Is my data safe?
A: Verification data is used only for the legally required checks and is
processed under the privacy policy published on the site.
"""

_GAMES = """\
This topic covers betting, casino games, live tables and game issues.

Q: A game won't load. What can I try?
A: Refresh the page, clear the browser cache, try another browser or
incognito mode, and make sure the browser is up to date. A VPN or ad blocker
can also interfere. If one specific game keeps failing while others work,
report it — the game provider may be having an outage.

Q: My game round was interrupted (connection lost, screen froze). Did I lose my bet?
A: No — round results are recorded on the game server, not in your browser.
Reopen the game: an unfinished round usually resumes or settles
automatically, and the outcome appears in your game history. If a round looks
stuck or unsettled in the history, a human operator should check it with the
game name and approximate time.

Q: Can I try games for free?
A: Many slots offer a demo (fun-play) mode without real bets — where
available, the option is shown on the game card. Live dealer games normally
have no demo mode.

Q: Are the games fair?
A: Games come from licensed providers and use certified random number
generators; live games are run with real dealers under the provider's
supervision. Each game's rules and RTP (return-to-player) are published in
the game's info panel.

Q: What are the betting limits?
A: Limits are per game or per table. They are shown inside the game — in the
bet panel or the table information.

Q: Where can I find the rules of a game?
A: Every game has an info/rules section (usually an "i" or "?" icon inside
the game) with the full rules, paytable and RTP.

Q: I won but the money is not on my balance.
A: Check the game history first: the round's result and payout are recorded
there. Winnings from bonus play go to the bonus balance until wagering is
complete. If the history shows a win that is genuinely missing from the
balance, a human operator must check it.

Q: What is RTP?
A: RTP (return to player) is the long-run percentage of stakes a game pays
back, e.g. 96%. It is a statistical average over millions of rounds, not a
promise for any session — no outcome can be predicted or guaranteed.
"""

_TECHNICAL = """\
This topic covers site, app and connectivity problems.

Q: The site won't open or loads slowly.
A: Check your internet connection, refresh the page, clear the browser cache
and cookies, and try another browser or incognito mode. If you use a VPN or
proxy, try switching it off — or on, if your network restricts access.

Q: I get an error during payment. Should I retry?
A: Don't retry immediately. First check your transaction history and your
bank/wallet to see whether the first attempt actually went through — this
avoids double payments. If money was deducted but nothing appears in the
history, a human operator should check it with the payment time and amount.

Q: Is there a mobile app?
A: The site works in mobile browsers; where a mobile app or an installable
web app (PWA) is offered, the site itself shows the install prompt or link.
Install only from the official site or official stores.

Q: I keep getting logged out.
A: Sessions expire after a period of inactivity, and a password change ends
all sessions for safety. If it happens constantly, clear cookies, make sure
the browser accepts them, and disable aggressive privacy extensions for the
site.

Q: I don't receive emails (confirmation, password reset).
A: Check the spam/junk folder and confirm the address in your profile is
correct. Add the sender to your contacts. If emails still don't arrive, a
human operator can resend or check the delivery.

Q: The page displays incorrectly (broken layout, missing buttons).
A: Force-refresh the page (Ctrl+F5 / Cmd+Shift+R), update the browser, and
disable extensions that modify pages. Current browser versions are supported;
very old browsers may not render the site correctly.

Q: How do I report a bug?
A: Describe what you did, what happened and what you expected, plus your
device, browser and a screenshot if possible — then pass it to support. That
information makes the fix much faster.
"""

_OTHER = """\
This is the general topic for questions that don't fit a specialized one.

Q: What can this support chat help with?
A: Deposits and withdrawals, account and verification, bonuses and
promotions, betting and games, and technical problems. Pick a topic or just
describe the question — the chat routes it to the right place.

Q: How do I talk to a human?
A: Ask for a human operator at any time and the chat hands the conversation
over to the support team.

Q: In what languages can I get help?
A: Write in the language you prefer — the assistant answers in the language
of your message where supported.

Q: Where are the terms and conditions and the privacy policy?
A: Both are published on the site — the links are in the site footer. The
published documents are the authoritative version of all rules.

Q: Is this casino licensed?
A: Licensing and regulator information is published on the site, normally in
the footer and in the terms and conditions.

Q: What is responsible gaming?
A: Gambling should stay entertainment. The account settings offer tools to
keep it under control: deposit, bet, loss and session limits, time-outs and
self-exclusion. If a player says play is getting out of control, hand the
conversation to a human operator immediately and with care.

Q: How do I change the site language?
A: The site language switcher is in the site header or footer; your choice is
remembered for the next visit.

Q: When is support available?
A: Support availability and all contact channels are listed on the site's
contact page.
"""

# ---------------------------------------------------------------------------
# Starter RETENTION knowledge base — the single free-text document a new
# product's Telegram retention bot starts with (see db.seed_starter_retention_kb).
# Same contract as the support starter: English, brand-neutral, no brand names,
# no URLs, no concrete amounts/dates — wherever a real casino would have a
# number or a link, the text sends the player to the site UI instead. The owner
# replaces it with the brand's own scenario base from the admin Retention KB tab.
# ---------------------------------------------------------------------------
STARTER_RETENTION_KB = """\
## What this conversation is for
This is a warm, personal retention chat, not support. Keep the player engaged,
curious and feeling like a VIP guest: react to what they say, ask small easy
questions, and gently keep the excitement of playing alive. Never pressure,
never guilt-trip, never push a deposit directly.

## Reasons to come back (safe generic hooks)
When steering the player back to the site, use only generic, always-true hooks:
- New games and slots appear on the site regularly - suggest browsing the games
  lobby for something fresh that fits their taste.
- Promotions and bonuses change over time - suggest checking the promotions
  page on the site for what is currently available for their account.
- If the player mentions a bonus they saw, do not quote or confirm any amounts,
  percentages or conditions - the promotion's own terms on the site are the
  single source of truth; invite them to open it there.

## Talking about games
Ask what they like to play (slots, live tables, sports) and mirror their
excitement. You may talk about game types in general terms - themes, live
dealers, big-win thrill - but never state RTP numbers, odds, limits or
strategies, and never promise or predict a win. If they ask "which game pays
best", say every game is a matter of taste and luck and suggest exploring the
lobby or trying a demo mode where available.

## Compliments and VIP feeling
Make the player feel special: notice their return, remember what they told you
earlier in the chat, celebrate their wins with them and sympathize warmly with
near-misses. Light playful flirtation is welcome while the mood is right; drop
it instantly in any money, complaint or sensitive moment.

## If the player has been away
Welcome them back warmly, with zero guilt: you missed them, it is lovely to see
them, ask what they have been up to. Then a soft hook - something new in the
games lobby or on the promotions page might be fun to check out.

## What you must NOT handle here (route out)
Deposits and withdrawals, account access or blocks, verification and documents,
bonus disputes, technical failures, complaints, legal threats, anything about
responsible gaming (limits, breaks, self-exclusion) and any explicit request
for a human/operator or manager. Do not answer these even partially - hand the
conversation off warmly and immediately.

## Responsible gaming
Never bring up gambling addiction yourself and never moralize. If the player
says play is getting out of control or asks to limit or pause it, drop all
playfulness, respond with genuine care, and hand the conversation off right
away.

## Photos
Photos of you may be offered by the system as candidates. Only send what the
candidate list allows, keep captions in character, and if there are no
candidates do not promise a photo - keep the chat going with words.
"""


# (slug, {lang: title}, kb_text) — order in this tuple = display order.
STARTER_TOPICS: tuple[tuple[str, dict[str, str], str], ...] = (
    ("deposits", {
        "en": "Deposits", "ru": "Пополнение", "es": "Depósitos",
        "tr": "Para yatırma", "pt": "Depósitos",
    }, _DEPOSITS),
    ("withdrawals", {
        "en": "Withdrawals", "ru": "Вывод средств", "es": "Retiros",
        "tr": "Para çekme", "pt": "Saques",
    }, _WITHDRAWALS),
    ("account_kyc", {
        "en": "Account & verification", "ru": "Аккаунт и верификация",
        "es": "Cuenta y verificación", "tr": "Hesap ve doğrulama",
        "pt": "Conta e verificação",
    }, _ACCOUNT),
    ("bonuses", {
        "en": "Bonuses & promotions", "ru": "Бонусы и акции",
        "es": "Bonos y promociones", "tr": "Bonuslar ve promosyonlar",
        "pt": "Bônus e promoções",
    }, _BONUSES),
    ("betting_games", {
        "en": "Betting & games", "ru": "Ставки и игры",
        "es": "Apuestas y juegos", "tr": "Bahisler ve oyunlar",
        "pt": "Apostas e jogos",
    }, _GAMES),
    ("technical", {
        "en": "Technical issues", "ru": "Технические вопросы",
        "es": "Problemas técnicos", "tr": "Teknik sorunlar",
        "pt": "Problemas técnicos",
    }, _TECHNICAL),
    ("other", {
        "en": "Other", "ru": "Другое", "es": "Otro", "tr": "Diğer",
        "pt": "Outro",
    }, _OTHER),
)


def starter_prompt_variables(brand_name: str) -> dict[str, str]:
    """Baseline prompt variables for a new product.

    Seeds the FULL registry into the product layer so a new casino never
    inherits another brand's global prompt-variable overrides: every key gets
    the template default from prompts.PROMPT_VARIABLES, and `brand_name` is
    set to the product's own name. The owner uniquifies persona/tone later
    from the admin Prompt → Prompt variables sub-tab.
    """
    import prompts  # local import: db → starter_kb → prompts would otherwise risk a cycle

    values = {key: default for key, _desc, default in prompts.PROMPT_VARIABLES}
    values["brand_name"] = (brand_name or "").strip() or values["brand_name"]
    return values
