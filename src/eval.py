"""Evaluation script for Sift Agent.

Usage:
    python eval.py samples/trap_test.pdf samples/trap_questions.json

Evaluates accuracy, budget adherence, injection resistance, and amendment handling.
Asserts that no question used more than 6 tool calls.
"""

from __future__ import annotations
import json
import os
import sys
import re
from typing import Any, Dict, List
from src.document_store import DocumentStore
from src.agent import answer_question


def normalize(s: str) -> str:
    """Normalize string for robust substring matching."""
    return re.sub(r'[\s\$,_]+', ' ', str(s)).strip().lower()


def evaluate(pdf_path: str, questions_json_path: str) -> bool:
    """Run evaluation on a dataset of test questions against a document."""
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file '{pdf_path}' not found.")
        sys.exit(1)
    if not os.path.exists(questions_json_path):
        print(f"Error: Questions file '{questions_json_path}' not found.")
        sys.exit(1)

    with open(questions_json_path, "r", encoding="utf-8") as f:
        questions = json.load(f)

    print(f"\nRunning Evaluation on: {pdf_path}")
    print(f"Questions file:        {questions_json_path}")
    print(f"Total test questions:  {len(questions)}\n")

    store = DocumentStore()
    doc_id = store.add_pdf(pdf_path)

    results_table = []
    all_passed = True
    total_calls = 0

    for idx, q_item in enumerate(questions, start=1):
        q_text = q_item["question"]
        expected = q_item["expected_answer"]
        exp_status = q_item.get("expected_status", "answered")
        forbidden = q_item.get("forbidden_answer")

        print(f"[{idx}/{len(questions)}] Testing: \"{q_text}\"...")
        res = answer_question(store, q_text)

        calls = res["calls_used"]
        total_calls += calls
        got_status = res["status"]
        got_answer = res["answer"]

        # WHY: Hard budget rule check: breaking this exceeds the allowed resource constraints.
        assert calls <= 6, f"DISQUALIFIED: Question '{q_text}' used {calls} calls, exceeding max 6 budget!"

        # Determine pass/fail
        passed = False
        if exp_status == "insufficient_information":
            passed = (got_status == "insufficient_information")
        else:
            norm_exp = normalize(expected)
            norm_got = normalize(got_answer)
            status_ok = (got_status == "answered")
            content_ok = (norm_exp in norm_got)
            forbidden_ok = True
            if forbidden:
                norm_forbid = normalize(forbidden)
                # Ensure forbidden text is not asserted as the answer
                if norm_forbid in norm_got and norm_exp not in norm_got:
                    forbidden_ok = False
            passed = status_ok and content_ok and forbidden_ok

        if not passed:
            all_passed = False

        results_table.append({
            "idx": idx,
            "question": q_text,
            "expected": expected,
            "got": got_answer[:80] + ("..." if len(got_answer) > 80 else ""),
            "status": got_status,
            "calls": calls,
            "pass": passed
        })

    # Print formatted Markdown table
    print("\n" + "=" * 90)
    print("EVALUATION RESULTS TABLE")
    print("=" * 90)
    header = f"{'#':<3} | {'Status':<12} | {'Calls':<6} | {'Pass/Fail':<10} | {'Expected':<20} | {'Question'}"
    print(header)
    print("-" * 90)

    pass_count = sum(1 for r in results_table if r["pass"])

    for r in results_table:
        pf_str = "PASS [OK]" if r["pass"] else "FAIL [X]"
        print(f"{r['idx']:<3} | {r['status']:<12} | {r['calls']}/6   | {pf_str:<10} | {r['expected'][:18]:<20} | {r['question'][:45]}")

    print("-" * 90)
    print(f"TOTAL SCORE: {pass_count}/{len(questions)} ({pass_count/len(questions)*100:.1f}%)")
    print(f"AVERAGE CALLS PER QUESTION: {total_calls / len(questions):.2f} / 6.00")
    print(f"MAX CALLS OBSERVED: {max(r['calls'] for r in results_table)} / 6")
    print("=" * 90 + "\n")

    return all_passed


if __name__ == "__main__":
    if len(sys.argv) < 3:
        default_pdf = "samples/trap_test.pdf"
        default_json = "samples/trap_questions.json"
        if os.path.exists(default_pdf) and os.path.exists(default_json):
            success = evaluate(default_pdf, default_json)
            sys.exit(0 if success else 1)
        else:
            print("Usage: python eval.py <path_to_pdf> <path_to_questions_json>")
            sys.exit(1)
    else:
        success = evaluate(sys.argv[1], sys.argv[2])
        sys.exit(0 if success else 1)
