"""Finite, whole-question inventory language; unknown clauses never become scope.

Keyword rules classify rejections only. Acceptance requires a full grammar match,
so an unregistered exclusion or instruction cannot ride on a valid prefix.
"""

import re

LOT = re.compile(
    r"(?<![A-Za-z0-9_-])LOT-[A-Za-z0-9][A-Za-z0-9_-]{0,119}(?![A-Za-z0-9_-])"
)
UTC = re.compile(r"(?<![0-9A-Za-z])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?![0-9A-Za-z])")

ALL = r"(?:全部|所有|完整|全量)"
VISIBLE = r"(?:可见|当时已知且已进站的?|已知且已开始的?|当时已知且已开始的?)?"
ITEMS = ALL + VISIBLE + r"作业(?:清单)?"
VERB = r"(?:请)?(?:列出|提供)"
CUTOFF = r"截至@utc@(?:\((?:含边界|含该时刻)\))?"
CN_PATTERNS = (
    CUTOFF + r",?" + VERB + r"@lot@的?" + ITEMS,
    VERB + r"@lot@" + CUTOFF + r"的?" + ITEMS,
    r"@lot@@utc@请列出截至上述UTC时刻的" + ITEMS,
    r"帮我看看@lot@截至@utc@已经开始且当时已知的作业有哪些,全部列出来",
    r"我需要一份作业清单:批次@lot@;截止为@utc@\(含该时刻\);请列出所有当时已知且已进站的作业",
)
EN_ITEMS = r"(?:all operations|complete operations|every operation|(?:the )?complete operation inventory)"
EN_PATTERN = (
    r"(?:please )?(?:list|show|provide) "
    + EN_ITEMS
    + r" (?:for|of) @lot@(?: known and started)? as of @utc@"
    + r"(?:, inclusive|, including every operation known and started by that cutoff)?\.?"
)
PUNCTUATION = str.maketrans(
    {"，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")"}
)
DISCLAIMER = re.compile(r"^不是追溯(?:,也不是质量判断)?;")


def semantic_question(question):
    # Only this exact leading disclaimer is supported, never arbitrary negation.
    normalized = re.sub(r"\s+", "", question.translate(PUNCTUATION))
    prefix = DISCLAIMER.match(normalized)
    if prefix:
        return normalized[prefix.end() :]
    return question


def explicit_inventory(question):
    shaped = UTC.sub("@utc@", LOT.sub("@lot@", question.translate(PUNCTUATION)))
    cn = re.sub(r"\s+", "", shaped)
    # Repeating the same literal scope or requesting empty inventories is safe;
    # slot uniqueness is checked separately before this grammar is consulted.
    suffix = r"(?:,包括为空的情况|\.确认仍是@lot@,仍截至@utc@)?\.?"
    if any(re.fullmatch(pattern + suffix, cn) for pattern in CN_PATTERNS):
        return True
    en = re.sub(r"\s+", " ", shaped).strip()
    return re.fullmatch(EN_PATTERN, en, re.IGNORECASE) is not None
