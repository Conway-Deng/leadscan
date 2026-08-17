"""
scoring.py
----------
Pure scoring. It turns findings into a score, a tier, and the opening line for
the call. No browser, no network, so the test suite can check every rule.

THE IDEAL CUSTOMER PROFILE
A firm is a good lead when all three are true:
  1. Quiet          -- few Google reviews, so they lack organic reach.
  2. Invests to be seen -- a live advertisement tag, or content on Instagram
                        or TikTok.
  3. Broken funnel  -- no way to capture a lead, a slow site, no HTTPS, or no
                       site at all.
A firm with a large following is not a lead. They already own an audience.

HONESTY RULE
The opening line must never claim something the scan did not see. The earlier
version told every firm with no detected tag "You may be spending on ads
without tracking". That is a guess dressed as a fact, and a prospect who does
not buy advertisements hears it as a lie in the first sentence. Each tier now
states only what the scan actually found.
"""

import config

# Tier names, best first.
TIER_HOT = "hot"        # Confirmed advertisement spend and a leaking funnel.
TIER_WARM = "warm"      # Organic social effort and a leaking funnel.
TIER_COOL = "cool"      # Quiet firm with a real defect, but no proof of effort.


def _quiet(review_count):
    return review_count is None or review_count <= config.QUIET_REVIEWS


def _pains(findings):
    """
    List the defects found, worst first.

    Each entry is (description, points, consequence). The consequence is the
    second half of the opening line, so it must follow from THAT defect. An
    earlier draft reused one consequence for every defect and produced the
    sentence "there is no HTTPS padlock, so nobody has a way to get in touch",
    which does not make sense to the person hearing it.
    """
    pains = []
    if not findings["can_capture_lead"]:
        pains.append((
            "no form, no booking link, and no click-to-call on the page",
            40,
            None,          # None means "use the channel consequence"
        ))
    if not findings["has_mobile_viewport"]:
        pains.append((
            "the site is not built for a phone screen",
            15,
            "most of your visitors arrive on a phone and give up",
        ))
    if findings["is_slow"]:
        pains.append((
            f"the page needed {findings['load_seconds']}s to load",
            15,
            "most people leave before they see anything",
        ))
    if not findings["is_https"]:
        pains.append((
            "there is no HTTPS padlock",
            10,
            "the browser warns your visitors before they read one word",
        ))
    return pains


def _channel_story(findings, follower_count):
    """
    Give back (points, tier, opening clause, leak clause).

    Only three honest stories are possible:
      * an advertisement tag is present -> they buy traffic
      * Instagram or TikTok is present  -> they build an audience
      * neither                         -> say nothing about marketing
    """
    if findings.get("spends_on_ads"):
        tags = findings.get("ad_tags") or ["an ad tag"]
        return (
            15,
            TIER_HOT,
            f"You are running paid ads. I can see the {tags[0]} on your site",
            "that paid traffic has nowhere to convert",
        )

    if findings.get("markets_on_social"):
        channels = [
            name
            for name, link in (
                ("Instagram", findings.get("instagram")),
                ("TikTok", findings.get("tiktok")),
            )
            if link
        ]
        channel = " and ".join(channels) or "social media"
        if follower_count:
            audience = f"your ~{follower_count:,} {channels[0]} followers"
        else:
            audience = "the people you reach there"
        return (
            10,
            TIER_WARM,
            f"You are building an audience on {channel}",
            f"{audience} have no clear next step",
        )

    # No proof of any marketing effort. Lead with the defect itself. Do not
    # invent an advertisement budget.
    return (
        0,
        TIER_COOL,
        "I had a look at your website",
        "anyone who finds you has no easy way to get in touch",
    )


def score_website_lead(findings, review_count, follower_count=None, error=None):
    """
    Score one business that has a website.

    Give back a dict:
        score, tier, warm, disqualified, hook, reasons
    """
    # --- Skip an influencer-run firm. They already own an audience. ---
    if follower_count is not None and follower_count >= config.INFLUENCER_FOLLOWERS:
        return _result(
            0, "", False, True,
            f"SKIP - about {follower_count:,} followers. They already own an "
            f"audience, so they do not need lead-generation help.",
            ["large social following"],
        )

    # --- No website at all. This is not the same as a broken website. ---
    if error == "no website":
        quiet = _quiet(review_count)
        return _result(
            40 if quiet else 15,
            TIER_COOL if quiet else "",
            quiet, False,
            "You have no website at all, so you depend on word of mouth and on "
            "Google. There is nothing that turns an interested person into a "
            "booking.",
            ["no website", "run --social-only to find their Instagram or TikTok"],
        )

    # --- The site is dead or broken. This is a strong lead only for a small
    #     firm. A large established firm that returns one 503 is almost always
    #     a temporary fault, not a broken funnel. ---
    if error:
        if review_count is not None and review_count > config.ESTABLISHED_REVIEWS:
            return _result(
                10, "", False, False,
                f"The site failed to load ({error}), but the firm has "
                f"{review_count} reviews. This is probably a temporary fault on "
                f"an established firm, not a real lead.",
                [f"site error: {error}", "established firm"],
            )
        return _result(
            60, TIER_COOL, True, False,
            f"Your website did not load for me ({error}). If you pay for ads or "
            f"post on social, every click lands on a dead page.",
            [f"site error: {error}"],
        )

    pains = _pains(findings)
    channel_points, tier, opening, leak = _channel_story(findings, follower_count)
    score = sum(points for _, points, _ in pains) + channel_points

    reasons = [text for text, _, _ in pains]
    if findings.get("ad_tags"):
        reasons.append("ad tags: " + ", ".join(findings["ad_tags"]))
    if findings.get("measures_only"):
        # Analytics without an ad tag. Say so plainly. It is a useful fact for
        # the call, but it is not proof of spend and must not raise the tier.
        reasons.append(
            "analytics only (" + ", ".join(findings["analytics_tags"]) + ") - no ad tag found"
        )

    # --- The quiet signal. Few reviews means a small firm that likely needs
    #     help. Many reviews means the firm already has traction. ---
    quiet_note = ""
    if review_count is not None:
        if review_count <= config.QUIET_REVIEWS:
            score += 20
            quiet_note = (
                f" You have only {review_count} Google reviews, so you are still "
                f"under the radar."
            )
            reasons.append(f"quiet: {review_count} reviews")
        elif review_count <= config.MID_REVIEWS:
            score += 5
        else:
            score -= 10
            reasons.append(f"established: {review_count} reviews")

    score = max(0, min(score, 100))

    # Warm means the call is worth making: there is a real defect AND some sign
    # that the firm invests in being seen or is small enough to need help.
    warm = bool(pains) and (
        findings.get("spends_on_ads")
        or findings.get("markets_on_social")
        or (review_count is not None and review_count <= config.QUIET_REVIEWS)
    )

    if not pains:
        tier = ""
        hook = f"{opening}, and the funnel looks solid. Lower priority."
    else:
        top_pain, _points, consequence = pains[0]
        hook = f"{opening}, but {top_pain}, so {consequence or leak}.{quiet_note}"

    if not warm:
        tier = ""

    return _result(score, tier, warm, False, hook, reasons)


def score_social_only_lead(socials, follower_count, review_count):
    """
    Score a business that has no website and runs off Instagram or TikTok.

    These firms do not have a broken funnel. They have no funnel. The one
    disqualifier is size: above the limit they can sell for themselves.
    """
    if follower_count is not None and follower_count > config.SOCIAL_ONLY_MAX_FOLLOWERS:
        return _result(
            0, "", False, True,
            f"SKIP - about {follower_count:,} followers and no website. Big "
            f"enough to run their own lead generation, so not a fit.",
            ["large social following, no website"],
        )

    channels = [
        name
        for name, link in (
            ("Instagram", socials.get("instagram")),
            ("TikTok", socials.get("tiktok")),
        )
        if link
    ]

    if not channels:
        score = 55
        reasons = ["no website", "no social profile found"]
        if _quiet(review_count) and review_count is not None:
            score += 15
            reasons.append(f"quiet: {review_count} reviews")
        return _result(
            min(score, 100), TIER_COOL, True, False,
            "You have no website, and I could not find an active Instagram or "
            "TikTok either. You are close to invisible online, so word of mouth "
            "just evaporates.",
            reasons,
        )

    channel = " and ".join(channels)
    score = 70
    reasons = ["no website", f"active on {channel}"]
    if follower_count is not None:
        audience = f"Your ~{follower_count:,} {channels[0]} followers have nowhere to go:"
    else:
        audience = "Anyone who finds you there has no next step:"
    if review_count is not None and review_count <= config.QUIET_REVIEWS:
        score += 10
        reasons.append(f"quiet: {review_count} reviews")

    hook = (
        f"Everything you do runs off your {channel}, and there is no website at "
        f"all. {audience} no site, no form, no way to book you."
    )
    return _result(min(score, 100), TIER_WARM, True, False, hook, reasons)


def _result(score, tier, warm, disqualified, hook, reasons):
    return {
        "score": score,
        "tier": tier,
        "warm": bool(warm),
        "disqualified": bool(disqualified),
        "hook": hook,
        "reasons": reasons,
    }


# Sort order used everywhere. Hot first, then warm, then cool. Inside a tier,
# the higher score comes first.
TIER_RANK = {TIER_HOT: 0, TIER_WARM: 1, TIER_COOL: 2, "": 3}


def sort_key(row):
    return (TIER_RANK.get(row.get("tier", ""), 3), -row.get("score", 0), row.get("name", ""))
