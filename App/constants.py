"""Shared constants for the Pokemon Dashboard application."""

TYPE_COLORS = {
    "grass": "#78C850",
    "fire": "#F08030",
    "water": "#6890F0",
    "bug": "#A8B820",
    "normal": "#A8A878",
    "poison": "#A040A0",
    "electric": "#F8D030",
    "ground": "#E0C068",
    "fairy": "#EE99AC",
    "fighting": "#C03028",
    "psychic": "#F85888",
    "rock": "#B8A038",
    "ghost": "#705898",
    "ice": "#98D8D8",
    "dragon": "#7038F8",
    "flying": "#A890F0",
    "steel": "#B8B8D0",
    "dark": "#705848",
}

ALL_TYPES = sorted(TYPE_COLORS.keys())


# Chat rooms (T25).
#
# The plan sketched `general` plus all 18 types. The maintainer chose to start
# with 5–6 instead: nineteen rooms split a small user base until every one of
# them looks abandoned, and an empty room reads as a broken feature rather than
# a quiet one. Growing this list later is a one-line change; shrinking it after
# people have posted is not.
#
# `general` plus the three starters, the franchise mascot's type, and the one
# people argue about most.
#
# This is a whitelist, not a suggestion: a room outside it is rejected rather
# than created, so a typo cannot silently fork a conversation or let a client
# create rooms without limit.
CHAT_ROOMS = (
    "general",
    "fire",
    "water",
    "grass",
    "electric",
    "dragon",
)

