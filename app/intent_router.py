"""Small, deterministic routing hints for choosing the bounded MIA agent loop.

This module never authorizes an action. It only decides whether a request likely
benefits from the existing agent loop; every tool still passes server validation.
"""
from __future__ import annotations

import re

_EXPLICIT_AGENT_CUES = (
    "create a pdf", "create pdf", "generate a pdf", "generate pdf", "make a pdf", "make pdf", "download as pdf", "export as pdf", "turn this into a pdf", "save this as a pdf", "create a downloadable report",
    "mcp", "supabase", "connected tools", "outside tools",
    "connected plugins", "connected integrations", "list my plugins",
    "which plugins", "what plugins", "which integrations are connected", "plugin inventory", "integration inventory",
    "available integrations", "show connected apps", "what integrations are connected",
    "what apps are connected",
    # Connected-app read requests must enter the tool loop; availability is still
    # enforced by owner-scoped connector schemas and execution-time token checks.
    "gmail", "google mail", "my inbox", "search my email", "search emails",
    "read my email", "read my emails", "read the email", "read this email",
    "read the full email", "read the entire email", "show me the full email",
    "open this email", "email body", "full email body", "find emails", "find an email",
    "look for emails", "check my inbox", "check my email", "emails from",
    "email from",
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
    "dns lookup", "reverse dns", "check domain reputation", "check ip reputation", "check email reputation", "shodan lookup", "virustotal lookup", "have i been pwned", "dehashed search", "public paste search", "paste site search", "crypto wallet lookup", "ethereum wallet trace", "investigation provider status", "which osint providers are connected", "what investigation tools are available",
    "run the tests", "run tests", "run pytest", "run unit tests", "test this code", "validate the build", "check the build", "run syntax checks", "check syntax", "validate git diff", "check git diff", "run local checks", "test the repository",
    "write code", "write a program", "help me code", "coding task", "implement this",
    "build a neural network", "neural network", "neural networks", "machine learning",
    "deep learning", "pytorch", "tensorflow", "scikit-learn", "train a model",
    "model architecture", "debug this", "fix this bug", "run the tests",
    "write the code", "create a function", "create a script", "refactor this",
    "write javascript", "write a python", "add a feature", "develop a feature",
    "implement a", "build me", "train a model", "training loop", "coding agent",
    "research this company", "research this company website", "company website", "analyze this company website",
    "analyze this website", "review this company's website", "look into this company",
    "use browserbase", "use firecrawl", "browse this website", "inspect this website", "extract this website",
    "website research", "marketing research", "research this public website", "browse the company website",
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
    r"\b(?:implement|build|write|create|debug|fix|refactor|code|program)\b.{0,55}\b(?:function|script|app|feature|api|model|neural network|machine learning|python|javascript|typescript|class|module|algorithm|test)\b",
    r"\b(?:neural network|machine learning|deep learning|pytorch|tensorflow|scikit.learn)\b",
    r"\b(?:browse|inspect|extract|research)\b.{0,45}\b(?:website|webpage|public site|company site)\b",
)


def should_use_agent_tools(message: str) -> bool:
    """Route coding, ML, research, investigations, and multi-step work through the bounded agent loop."""
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
