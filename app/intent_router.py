"""Small, deterministic routing hints for choosing the bounded MIA agent loop.

This module never authorizes an action. It only decides whether a request likely
benefits from the existing agent loop; every tool still passes server validation.
"""
from __future__ import annotations

import re

_EXPLICIT_AGENT_CUES = (
    "act as an agent", "agent mode", "use your tools", "use tools to",
    "use your tools to", "plan and execute", "research and create",
    "remember this", "save this to memory", "create a task", "make me a task plan",
    "analyze this document", "review this code", "use my local terminal",
    "autonomously", "multi-step task", "continue the task", "resume the task",
    "track my progress", "update my task", "work through this plan",
    "continue where we left off", "resume where we left off", "check my saved tasks",
    "review this repo", "inspect this repository", "work on my repo",
    "search and compare", "look up and summarize", "investigate and summarize",
    "research this and give me sources", "research and report back",
    "safenestt investigation", "run an investigation", "validation investigation", "complete validation investigation", "six-phase investigation", "six phase investigation", "investigate this email", "investigate this domain", "investigate this ip", "trace this wallet", "osint investigation", "threat intelligence lookup",
    "write code", "write a program", "help me code", "coding task", "implement this",
    "build a neural network", "neural network", "neural networks", "machine learning",
    "deep learning", "pytorch", "tensorflow", "scikit-learn", "train a model",
    "model architecture", "debug this", "fix this bug", "run the tests",
    "investment research", "investment opportunities", "compare stocks", "compare etfs",
    "stock opportunities", "etf opportunities", "opportunity scanner", "scan investment opportunities", "compare investment candidates", "market research", "analyze this stock",
    "analyze this etf", "best buying opportunity", "best entry price", "entry price", "investment candidates",
)

# Natural requests that imply a persistent or multi-step workflow, not a simple answer.
_ACTION_PATTERNS = (
    r"\b(?:remember|save)\b.{0,45}\b(?:this|that|for later|to memory|as a preference)\b",
    r"\b(?:create|make|build)\b.{0,35}\b(?:task|checklist|plan|tracker)\b",
    r"\b(?:resume|continue|pick up)\b.{0,40}\b(?:task|plan|project|work|where)\b",
    r"\b(?:review|inspect|read)\b.{0,35}\b(?:my )?(?:repo|repository|pull request|issues|document|file)\b",
    r"\b(?:search|research|investigate|compare)\b.{0,50}\b(?:and|then)\b.{0,35}\b(?:summarize|report|recommend|create|organize)\b",
    r"\b(?:track|update|mark)\b.{0,40}\b(?:my )?(?:progress|task|step|checklist)\b",
    r"\b(?:analyze|summarize|extract)\b.{0,35}\b(?:this )?(?:document|pdf|report|file)\b",
    r"\b(?:write|build|implement|debug|test|train)\b.{0,45}\b(?:code|program|neural network|model|algorithm|function|script)\b",
    r"\b(?:run|perform|execute|start)\b.{0,60}\b(?:investigation|investigate)\b",
)


def should_use_agent_tools(message: str) -> bool:
    """Return true for explicit agent requests or likely multi-step tool workflows."""
    text = re.sub(r"\s+", " ", str(message or "").casefold()).strip()
    if not text:
        return False
    if any(cue in text for cue in _EXPLICIT_AGENT_CUES):
        return True
    return any(re.search(pattern, text) for pattern in _ACTION_PATTERNS)


def should_run_investigation(message: str) -> bool:
    """Identify explicit investigation requests that must invoke the investigation tool."""
    text = re.sub(r"\s+", " ", str(message or "").casefold()).strip()
    if not text:
        return False
    cues = ("safenestt investigation", "validation investigation", "six-phase investigation", "six phase investigation", "osint investigation", "threat intelligence lookup", "investigate this domain", "investigate this ip", "investigate this email", "trace this wallet")
    if any(cue in text for cue in cues):
        return True
    return bool(re.search(r"\b(?:run|perform|execute|start)\b.{0,60}\b(?:investigation|investigate)\b", text))
