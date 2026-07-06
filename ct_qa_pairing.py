#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_qa_pairing.py — single source of truth for matching a mentor's answer
to a student's question across the Discord learning pipeline.

Why this file exists
---------------------
discord_monitor.py and generate_discord_report.py each independently
implemented "find the question a reply answers." Both walked the reply
chain (is_reply_to) the same way, but discord_monitor.py also bolted on a
second heuristic — pairing any two messages that landed close together in
time and shared enough trading keywords, used whenever no explicit reply
was found. That heuristic mispaired an unrelated YouTube-link message with
an unconnected Gilad follow-up during the 2026-07-06 scheduled run, because
proximity in time is a much weaker signal than an actual reply.

This module keeps only the reliable signal — reply chains — as the single
`pair_messages()` interface. Both scripts call this instead of maintaining
their own copy.
"""
import hashlib
from ct_taxonomy import keyword_hits


def message_hash(msg: dict) -> str:
    """Short, stable fingerprint for a message (used for dedup)."""
    key = f"{msg.get('author', '')}-{msg.get('content', '')[:80]}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def pair_messages(messages: list, lookback: int = 30, min_keyword_hits: int = 2) -> list:
    """
    Group messages into question → answer(s) pairs using reply chains
    (`is_reply_to`) only. A question can have more than one mentor reply;
    all of them are kept and the "best" one (most trading-keyword hits) is
    surfaced for display.

    Returns a list of dicts:
      {
        'question':           question message dict,
        'answers':            [answer message dicts, ...] chronological,
        'best_answer':         the answer with the most keyword hits,
        'merged_answer_text':  all answers' content joined with a space,
      }

    Pairs whose combined question+answers text doesn't clear
    `min_keyword_hits` trading keywords are dropped (filters out chit-chat
    that happens to be a reply, e.g. "thanks!").
    """
    # Step 1 — map every reply → the question it replies to, by walking
    # backwards up to `lookback` messages looking for an author match.
    reply_to_q = {}
    for i, msg in enumerate(messages):
        reply_to = msg.get('is_reply_to')
        if not reply_to:
            continue
        for j in range(i - 1, max(-1, i - lookback), -1):
            if messages[j].get('author') == reply_to:
                reply_to_q[i] = j
                break

    # Step 2 — group all replies that point at the same question.
    q_to_replies = {}
    for reply_idx, q_idx in reply_to_q.items():
        q_to_replies.setdefault(q_idx, []).append(reply_idx)

    # Step 3 — build pairs, filtering out low-signal chit-chat.
    pairs = []
    for q_idx, reply_idxs in sorted(q_to_replies.items()):
        question = messages[q_idx]
        answers = [messages[i] for i in sorted(reply_idxs)]
        merged_answer_text = ' '.join(a.get('content', '') for a in answers)
        combined = question.get('content', '') + ' ' + merged_answer_text
        if keyword_hits(combined) < min_keyword_hits:
            continue
        best_answer = max(answers, key=lambda a: keyword_hits(a.get('content', '')))
        pairs.append({
            'question':          question,
            'answers':           answers,
            'best_answer':       best_answer,
            'merged_answer_text': merged_answer_text,
        })
    return pairs
