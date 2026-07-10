#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""Hardware-free test of the shared target resolver (gestalt/targets/resolve.py)
— the primitive both the human pointer's focus acquisition and, eventually, an
agent-facing click() resolve an approximate point through."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gestalt.targets.resolve import resolve_target  # noqa: E402

BTN = {"cx": 100.0, "cy": 100.0, "w": 40, "h": 20, "role": "push button",
       "source": "atspi", "name": "Export"}
CANCEL = {"cx": 130.0, "cy": 100.0, "w": 40, "h": 20, "role": "push button",
          "source": "atspi", "name": "Cancel"}
CV_TARGET = {"cx": 500.0, "cy": 500.0, "w": 200, "h": 200, "role": "pane",
             "source": "cv", "name": ""}


def test_nearest_within_radius():
    assert resolve_target(102, 101, [BTN, CANCEL], radius=50) is BTN
    assert resolve_target(128, 101, [BTN, CANCEL], radius=50) is CANCEL


def test_nothing_within_radius_returns_none():
    assert resolve_target(9000, 9000, [BTN, CANCEL], radius=50) is None


def test_empty_target_list():
    assert resolve_target(100, 100, [], radius=50) is None


def test_name_hint_filters_and_is_case_insensitive():
    assert resolve_target(115, 100, [BTN, CANCEL], radius=50, name_hint="export") is BTN
    assert resolve_target(115, 100, [BTN, CANCEL], radius=50, name_hint="EXPORT") is BTN
    assert resolve_target(115, 100, [BTN, CANCEL], radius=50, name_hint="cancel") is CANCEL


def test_name_hint_substring_match():
    assert resolve_target(100, 100, [BTN], radius=50, name_hint="xpo") is BTN


def test_name_hint_no_match_returns_none_even_if_geometrically_closer():
    # BTN is the nearer target, but the hint only matches CANCEL — geometry
    # alone must not win once a hint is given.
    assert resolve_target(100, 100, [BTN, CANCEL], radius=50, name_hint="cancel") is CANCEL
    assert resolve_target(100, 100, [BTN], radius=50, name_hint="nonexistent") is None


def test_cv_target_never_matches_a_real_hint():
    assert resolve_target(500, 500, [CV_TARGET], radius=50, name_hint="pane") is None


def test_empty_or_whitespace_hint_behaves_as_no_filter():
    assert resolve_target(102, 101, [BTN, CANCEL], radius=50, name_hint="") is BTN
    assert resolve_target(102, 101, [BTN, CANCEL], radius=50, name_hint="   ") is BTN
    assert resolve_target(102, 101, [BTN, CANCEL], radius=50, name_hint=None) is BTN


def test_missing_name_key_handled_gracefully():
    no_name = {"cx": 100.0, "cy": 100.0, "role": "widget", "source": "cv"}
    assert resolve_target(101, 100, [no_name], radius=50) is no_name
    assert resolve_target(101, 100, [no_name], radius=50, name_hint="anything") is None


def main():
    test_nearest_within_radius()
    test_nothing_within_radius_returns_none()
    test_empty_target_list()
    test_name_hint_filters_and_is_case_insensitive()
    test_name_hint_substring_match()
    test_name_hint_no_match_returns_none_even_if_geometrically_closer()
    test_cv_target_never_matches_a_real_hint()
    test_empty_or_whitespace_hint_behaves_as_no_filter()
    test_missing_name_key_handled_gracefully()
    print("test_resolve: resolver + name-hint filtering hold")


if __name__ == "__main__":
    main()
