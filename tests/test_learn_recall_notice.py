"""The one-line notice the learn extension attaches to a recall result."""

from __future__ import annotations


class TestRecallNotice:
    def test_none_when_matches_found_and_query_unchanged(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("hybrid:embedding:m+bm25", 20, 20, 4, 4, "z>=1 or bm25>=0.15", 0.6, 2.1, False, "q")
        assert recall_notice(s, original_query="q") is None

    def test_explains_empty_recall(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("hybrid:embedding:m+bm25", 20, 20, 0, 3, "z>=1 or bm25>=0.15", 0.29, 0.8, False, "q")
        assert recall_notice(s, original_query="q") == (
            "no learning cleared the floor (z>=1 or bm25>=0.15): 20 scored, best similarity 0.29 "
            "under hybrid:embedding:m+bm25; 3 nearest shown as weak matches"
        )

    def test_mentions_small_store_and_cleaned_query(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        floor = "small store of 3: all returned; matched on a lexical hit (bm25>=0.15) or a shared tag"
        s = RecallSummary("hybrid:embedding:m+bm25", 3, 3, 1, 3, floor, 0.2, 1.0, True, "task A norrell photonics")
        assert recall_notice(s, original_query="RUN_TOKEN=3b09311f2873 task A norrell_photonics") == (
            "small store of 3: all 3 returned, 1 matched; query cleaned to 'task A norrell photonics'"
        )

    def test_mentions_only_the_cleaned_query_when_matches_exist(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 20, 20, 2, 2, "score>=0.15", 0.4, 1.9, False, "task A")
        assert recall_notice(s, original_query="RUN_TOKEN=3b09311f2873 task A") == "query cleaned to 'task A'"


class TestRecallNoticeEdges:
    def test_empty_store_notice_with_and_without_cleaning(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 0, 0, 0, 0, "n/a (empty store)", None, None, False, "export")
        assert recall_notice(s, original_query="export") == "no learnings stored yet for this project"
        assert recall_notice(s, original_query="RUN_TOKEN=3b09311f2873 export") == (
            "no learnings stored yet for this project; query cleaned to 'export'"
        )

    def test_nothing_returned_and_unknown_best_similarity(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 20, 20, 0, 0, "score>=0.15", None, None, False, "q")
        assert recall_notice(s, original_query="q") == (
            "no learning cleared the floor (score>=0.15): 20 scored, best similarity n/a under bm25; nothing returned"
        )

    def test_small_store_reports_returned_not_store_size(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary(
            "bm25", 3, 3, 1, 1, "small store of 3: all returned; matched at score>=0.15", 0.4, 1.4, True, "q"
        )
        assert recall_notice(s, original_query="q") == "small store of 3: all 1 returned, 1 matched"

    def test_cleaning_notice_when_only_whitespace_changed_and_when_matches_exist(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 20, 20, 2, 2, "score>=0.15", 0.4, 1.9, False, "task A")
        assert recall_notice(s, original_query=" task A ") == "query cleaned to 'task A'"
        assert recall_notice(s, original_query="task A") is None


class TestRecallNoticeCrossedFixtures:
    def test_nonempty_store_with_nothing_scored_is_not_called_empty(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 20, 0, 0, 0, "score>=0.15", 0.31, 1.2, False, "q")
        text = recall_notice(s, original_query="q")
        assert text is not None and text.startswith("no learning cleared the floor") and "stored yet" not in text
        assert "best similarity 0.31" in text and "nothing returned" in text

    def test_small_store_distinguishes_returned_from_matched(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary(
            "bm25", 5, 5, 1, 3, "small store of 5: all returned; matched at score>=0.15", 0.4, 1.4, True, "q"
        )
        assert recall_notice(s, original_query="q") == "small store of 5: all 3 returned, 1 matched"

    def test_equal_length_cleaning_is_still_reported(self):
        from trace_mcp.extensions.learn import recall_notice
        from trace_mcp.extensions.learn.matching import RecallSummary

        s = RecallSummary("bm25", 20, 20, 2, 2, "score>=0.15", 0.4, 1.9, False, "task a")
        assert recall_notice(s, original_query="task_a") == "query cleaned to 'task a'"
