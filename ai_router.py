
import json
import os

from chatbot import (
    run_cell_line_list,
    run_drug_targets,
    run_explain_chart,
    run_kinase_info,
    run_kinase_targets,
    run_ksea_cell_line_comparison,
    run_ksea_condition_comparison,
    run_ksea_heatmap,
    run_fold_change_visualisation,
    run_kinase_substrate_check,
    run_ksea_single,
    run_ksea_top_visualisation,
    run_list_all_kinases,
    run_network_attributes_lookup,
    run_network_visualisation,
    run_perturbation_list,
    run_phosphosite_viewer,
    run_protein_lookup,
    run_top_connected_kinases,
)
from utils import estimate_tokens


# Maximum estimated tokens allowed in a single OpenAI request
MAX_OPENAI_INPUT_TOKENS = 10000

# Per-session conversation history, kept in memory for the life of the process
_histories: dict[str, list[dict]] = {}

# Stores the last generated chart path per session for on-demand explanation
_last_images: dict[str, str] = {}

# Instructions sent to GPT at the start of every conversation
SYSTEM_MESSAGE = (
    "You are a biological research assistant using KINEPIK. "
    "Choose one tool when the user asks for protein information, kinase targets, "
    "perturbations, KSEA results, or KSEA visualisations. Do not invent database results. "
    "Use ksea_single for one named kinase under one perturbation. "
    "Use ksea_top_visualisation when the user asks which kinases are most activated, inhibited, "
    "affected, or changed under a perturbation. "
    "IMPORTANT: When the user asks which kinases a DRUG targets, binds to, or is known to inhibit "
    "(e.g. 'which kinases does Imatinib target?', 'what does Dasatinib target?'), "
    "you MUST use drug_targets — never use ksea_top_visualisation for this. "
    "Only use ksea_top_visualisation when the user asks which kinases are most activated or inhibited BY a drug. "
    "Drugs and perturbations include: Gefitinib, AZD3759, Dasatinib, Erlotinib, Tofacitinib, Imatinib, "
    "and any other compound name that is not a gene symbol or UniProt ID. "
    "Use ksea_cell_line_comparison when the user wants to compare ONE kinase under ONE drug "
    "across DIFFERENT CELL LINES (MCF7, HL60, NTERA2) — e.g. 'does AKT1 respond differently to "
    "AZD3759 in different cell lines?' or 'compare EGFR under Dasatinib across cell lines'. "
    "This is distinct from ksea_condition_comparison, which compares multiple DRUGS in one cell line. "
"Use ksea_heatmap when the user mentions multiple kinases AND multiple perturbations, "
    "or asks for a heatmap, grid, matrix, or comparison table. "
    "Do not answer any questions that are not related to kinase signalling. "
    "IMPORTANT: When the user asks to explain, interpret, or describe a chart, image, or visualisation "
    "(e.g. 'what does this show', 'explain this', 'what does the image show', 'interpret the results', "
    "'what can I see', 'what does this mean'), you MUST call the explain_chart tool. "
    "Never reply with plain text claiming you cannot see an image — always use the explain_chart tool instead. "
    "Do NOT use explain_chart for a follow-up question that asks something NEW about an entity from the "
    "previous result (e.g. 'does the top one target BRCA1?', 'what about its network?') — these are new "
    "questions requiring their own tool, not a request to explain the previous chart. "
    "CONVERSATION CONTEXT: Always address the user's most recent message directly — do not go back to "
    "answer an earlier message instead of the current one, even if an earlier question seems unresolved. "
    "When the user refers to an entity from earlier in the conversation using a pronoun or vague reference "
    "('the top one', 'it', 'its', 'that kinase'), resolve it to the specific, most recently discussed "
    "matching entity (e.g. the top-ranked kinase from the last chart), then use that resolved value as the "
    "argument for whichever tool matches the CURRENT message's request."
)

# Tool definitions sent to GPT so it knows which functions are available and what they do
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ksea_single",
            "description": "Get the KSEA z-score for one named kinase or protein under a perturbation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Gene symbol or UniProt ID, e.g. ERK, AKT, MTOR, P31749.",
                    },
                    "perturbation": {
                        "type": "string",
                        "description": "Perturbation or drug name. Default AZD3759.",
                    },
                    "cell_line": {
                        "type": "string",
                        "description": "Cell line. Default MCF7.",
                    },
                },
                "required": ["protein"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ksea_top_visualisation",
            "description": "Plot the top kinases under a perturbation. Use direction to filter: 'positive' for most activated, 'negative' for most inhibited, omit for both.",
            "parameters": {
                "type": "object",
                "properties": {
                    "perturbation": {
                        "type": "string",
                        "description": "Perturbation or drug name. Default AZD3759.",
                    },
                    "cell_line": {
                        "type": "string",
                        "description": "Cell line. Default MCF7.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["positive", "negative"],
                        "description": "Filter to only activated (positive) or only inhibited (negative) kinases.",
                    },
                },
                "required": ["perturbation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ksea_condition_comparison",
            "description": "Compare one kinase's KSEA z-scores across multiple perturbations or drugs and produce a bar chart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Gene symbol or UniProt ID, e.g. TTK, ERK, AKT, P31749.",
                    },
                    "perturbations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Perturbations or drugs to compare, e.g. AZD3759, Dasatinib.",
                    },
                    "cell_line": {
                        "type": "string",
                        "description": "Cell line. Default MCF7.",
                    },
                },
                "required": ["protein", "perturbations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ksea_cell_line_comparison",
            "description": (
                "Compare one kinase's KSEA z-score under a single drug/perturbation across "
                "multiple cell lines (MCF7, HL60, NTERA2) and produce a bar chart. "
                "Use when the user asks whether a drug's effect differs by cell line, cell type, "
                "or asks to compare a kinase across cell lines under the same condition."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Gene symbol or UniProt ID, e.g. AKT1, EGFR, P31749.",
                    },
                    "perturbation": {
                        "type": "string",
                        "description": "Single perturbation or drug name, e.g. AZD3759, Dasatinib.",
                    },
                    "cell_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Cell lines to compare. Default is all three: MCF7, HL60, NTERA2.",
                    },
                },
                "required": ["protein", "perturbation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "protein_lookup",
            "description": "Look up readable protein information from KINEPIK.",
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Gene symbol or UniProt ID, e.g. EGFR, ERK, P31749.",
                    }
                },
                "required": ["protein"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kinase_info",
            "description": (
                "Look up a kinase's classification info: its family, group, and how many "
                "phosphosites are on record for it. Use when the user asks what family or "
                "group a kinase belongs to, or for general kinase classification info — "
                "e.g. 'what family is CDK1 in?', 'tell me about the AKT1 kinase'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. CDK1, EGFR.",
                    }
                },
                "required": ["protein"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_all_kinases",
            "description": (
                "List kinases available in the KINEPIK database (a preview of the first 10 "
                "of roughly 504). Use when the user asks to see, list, or browse all kinases "
                "in KINEPIK, or asks what kinases are in the database."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "network_attributes_lookup",
            "description": (
                "Look up network node metadata/attributes (such as IDs and names) for one or "
                "more kinases in the signalling network. Use when the user asks for node "
                "attributes, metadata, or details about how a kinase appears in the network — "
                "distinct from network_visualisation, which draws the actual graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proteins": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more kinase gene symbols or UniProt IDs.",
                    }
                },
                "required": ["proteins"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kinase_substrate_check",
            "description": (
                "Answer a direct yes/no question about whether a specific kinase targets a "
                "specific substrate protein, e.g. 'does CDK1 target BRCA1?', 'is EGFR a target "
                "of AKT1?'. ALWAYS use this — not phosphosite_viewer or kinase_targets — when "
                "the user names BOTH a kinase AND a specific substrate/target protein."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kinase": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. CDK1, EGFR, P06493.",
                    },
                    "substrate": {
                        "type": "string",
                        "description": "Substrate gene symbol to check, e.g. BRCA1, NPM1.",
                    },
                },
                "required": ["kinase", "substrate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fold_change_visualisation",
            "description": (
                "Show the raw experimental fold-change values (not the summary KSEA z-score) "
                "under one drug. If the user names a specific substrate, shows the kinase's "
                "effect on that substrate, e.g. 'raw fold-change for CDK1's effect on BRCA1 "
                "under Dasatinib'. If the user only names a kinase and a drug with no substrate, "
                "e.g. 'fold-change data for CDK1 under Dasatinib', omit substrate and this will "
                "show the kinase's own autophosphorylation sites instead. Always requires a drug."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kinase": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. CDK1, EGFR.",
                    },
                    "substrate": {
                        "type": "string",
                        "description": "Substrate gene symbol, e.g. BRCA1, NPM1. Omit if the user didn't name one.",
                    },
                    "perturbation": {
                        "type": "string",
                        "description": "Drug/perturbation name, e.g. Dasatinib, AZD3759.",
                    },
                    "cell_line": {
                        "type": "string",
                        "description": "Cell line. Default MCF7.",
                    },
                },
                "required": ["kinase", "perturbation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "phosphosite_viewer",
            "description": (
                "Show a full, paginated, searchable table of a kinase's phosphosites — either "
                "the sites it targets on OTHER proteins, or its OWN autophosphorylation sites. "
                "These are two different, unrelated lists — watch the exact preposition used. "
                "'phosphosites ON <kinase>' (e.g. 'phosphosites on CDK1', 'show me the phosphosites "
                "on EGFR') means mode=own — sites located on the kinase itself — even without the "
                "word 'itself' or 'own'. Also mode=own for 'what phosphosites does CDK1 have on "
                "itself', 'its own autophosphorylation sites', or 'not its targets'. "
                "mode=targets is for phrasing like 'phosphosites CDK1 targets', 'list all targets "
                "of EGFR', 'phosphosite targets for CDK1', or 'phosphosites CDK1 phosphorylates/acts "
                "on' — sites on OTHER proteins. Do NOT use this if the user names a specific "
                "substrate to check — use kinase_substrate_check instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. CDK1, EGFR, P06493.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["targets", "own"],
                        "description": (
                            "'targets' for phosphosites this kinase acts on, on other proteins. "
                            "'own' for phosphosites on the kinase itself (autophosphorylation "
                            "sites) — this includes phrasing like 'phosphosites on <kinase>', not "
                            "just 'its own' or 'itself'. Required — infer from phrasing, default "
                            "to 'targets' only if genuinely ambiguous."
                        ),
                    },
                },
                "required": ["protein", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kinase_targets",
            "description": (
                "Get a short preview (first 10) of phosphosite targets phosphorylated by a "
                "kinase. Prefer phosphosite_viewer for a full browsable list, or "
                "kinase_substrate_check if the user names a specific substrate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. AKT, ERK, EGFR.",
                    }
                },
                "required": ["protein"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "perturbation_list",
            "description": "List available perturbations in KINEPIK.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cell_line_list",
            "description": (
                "Show which cell lines KINEPIK has experimental data for. "
                "Use when the user asks what cell lines are available, supported, or in the database."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ksea_heatmap",
            "description": (
                "Plot a heatmap of KSEA z-scores for multiple kinases across multiple perturbations. "
                "Use when the user wants to compare several kinases and several drugs/perturbations at once, "
                "or asks for a heatmap, grid, or matrix view."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kinases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of gene symbols or UniProt IDs, e.g. AKT, MTOR, EGFR.",
                    },
                    "perturbations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of perturbation or drug names, e.g. AZD3759, Dasatinib.",
                    },
                    "cell_line": {
                        "type": "string",
                        "description": "Cell line. Default MCF7.",
                    },
                },
                "required": ["kinases", "perturbations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drug_targets",
            "description": (
                "Look up the known kinase targets of a drug — the kinases it is known to bind to or inhibit. "
                "Use when the user asks which kinases a drug targets, inhibits, or is known to affect. "
                "This is different from KSEA which shows activity changes — this shows direct binding targets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drug": {
                        "type": "string",
                        "description": "Drug or perturbation name, e.g. Imatinib, Gefitinib, Dasatinib.",
                    }
                },
                "required": ["drug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "network_visualisation",
            "description": (
                "Show a network diagram of how a kinase connects to other proteins. "
                "Use when the user asks how a kinase fits in, its network, relationships, "
                "connections, or signalling map. Before calling this, if the user hasn't "
                "already said whether they want kinase-only connections or all interactions "
                "including non-kinase substrates, ask them directly which they want — do not "
                "call this tool until that's known, never assume."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protein": {
                        "type": "string",
                        "description": "Kinase gene symbol or UniProt ID, e.g. MTOR, AKT, ERK.",
                    },
                    "kinase_only": {
                        "type": "boolean",
                        "description": (
                            "True to show only kinase-to-kinase connections, false to include "
                            "all interactions such as non-kinase substrates. Must be confirmed "
                            "with the user, never assumed."
                        ),
                    },
                },
                "required": ["protein", "kinase_only"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_connected_kinases",
            "description": (
                "Rank kinases by how many connections they have in the KINEPIK signalling network. "
                "Use when the user asks which kinases are most connected, most central, most important, "
                "or have the most interactions in the network."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "Number of top kinases to return. Default 10.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_chart",
            "description": (
                "Explain or interpret the last chart or visualisation that was generated. "
                "Use when the user asks what the chart shows, what it means, to explain it, "
                "or to interpret the results."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def estimate_openai_request_tokens(user_message, model=None):
    """Estimate the total token count of a request to OpenAI, including the
    system message, user message, and tool definitions."""
    tool_text = json.dumps(TOOLS, separators=(",", ":"))
    return (
        estimate_tokens(SYSTEM_MESSAGE, model=model)
        + estimate_tokens(user_message, model=model)
        + estimate_tokens(tool_text, model=model)
    )


def _validate_user_message(user_message, model=None):
    """Check the user message is not empty and won't exceed the token limit.
    Returns an error string if there's a problem, or None if it's fine."""
    if not user_message or not user_message.strip():
        return "Please enter a message."
    token_count = estimate_openai_request_tokens(user_message, model=model)
    if token_count > MAX_OPENAI_INPUT_TOKENS:
        return (
            f"Your request is too long to send safely to OpenAI "
            f"({token_count} estimated input tokens). Please shorten it."
        )
    return None


def _dispatch_tool(tool_name, args, session_id=None):
    """Route a tool call from GPT to the correct handler in chatbot.py.
    Each tool_name maps to one of the run_* functions imported at the top."""
    if tool_name == "explain_chart":
        image_path = _last_images.get(session_id) if session_id else None
        return run_explain_chart(image_path)
    if tool_name == "ksea_single":
        return run_ksea_single(
            protein=args["protein"],
            perturbation=args.get("perturbation", "AZD3759"),
            cell_line=args.get("cell_line", "MCF7"),
        )
    if tool_name == "ksea_top_visualisation":
        return run_ksea_top_visualisation(
            perturbation=args.get("perturbation", "AZD3759"),
            cell_line=args.get("cell_line", "MCF7"),
            direction=args.get("direction"),
        )
    if tool_name == "ksea_condition_comparison":
        return run_ksea_condition_comparison(
            protein=args["protein"],
            perturbations=args["perturbations"],
            cell_line=args.get("cell_line", "MCF7"),
        )
    if tool_name == "ksea_cell_line_comparison":
        return run_ksea_cell_line_comparison(
            protein=args["protein"],
            perturbation=args["perturbation"],
            cell_lines=args.get("cell_lines"),
        )
    if tool_name == "ksea_heatmap":
        return run_ksea_heatmap(
            kinases=args["kinases"],
            perturbations=args["perturbations"],
            cell_line=args.get("cell_line", "MCF7"),
        )
    if tool_name == "protein_lookup":
        return run_protein_lookup(args["protein"])
    if tool_name == "drug_targets":
        return run_drug_targets(args["drug"])
    if tool_name == "kinase_targets":
        return run_kinase_targets(args["protein"])
    if tool_name == "phosphosite_viewer":
        return run_phosphosite_viewer(args["protein"], mode=args.get("mode", "targets"))
    if tool_name == "kinase_substrate_check":
        return run_kinase_substrate_check(args["kinase"], args["substrate"])
    if tool_name == "fold_change_visualisation":
        return run_fold_change_visualisation(
            kinase=args["kinase"],
            substrate=args.get("substrate"),
            perturbation=args["perturbation"],
            cell_line=args.get("cell_line", "MCF7"),
        )
    if tool_name == "kinase_info":
        return run_kinase_info(args["protein"])
    if tool_name == "list_all_kinases":
        return run_list_all_kinases()
    if tool_name == "network_attributes_lookup":
        return run_network_attributes_lookup(args["proteins"])
    if tool_name == "perturbation_list":
        return run_perturbation_list()
    if tool_name == "network_visualisation":
        return run_network_visualisation(args["protein"], kinase_only=args.get("kinase_only", True))
    if tool_name == "cell_line_list":
        return run_cell_line_list()
    if tool_name == "top_connected_kinases":
        return run_top_connected_kinases(top_n=args.get("top_n", 10))

    return {"reply": "I could not choose a valid KINEPIK tool.", "image": None}


def clear_history(session_id: str) -> None:
    """Delete the conversation history for a given session.
    Called when the user clicks 'New conversation'."""
    _histories.pop(session_id, None)


def _get_history(session_id: str | None) -> list[dict]:
    """Retrieve the stored conversation history for a session.
    Returns an empty list if no history exists yet."""
    if not session_id:
        return []
    return _histories.get(session_id, [])


def _save_history(session_id: str | None, history: list[dict]) -> None:
    """Save the full conversation history for a session with no limit.
    History is kept for the duration of the session and cleared when
    the user clicks 'New conversation'."""
    if not session_id:
        return
    _histories[session_id] = history


def ai_chatbot_reply(user_message, session_id=None):
    """Send a natural language message to GPT and let it decide which KINEPIK tool to call.

    Steps:
    1. Check the OpenAI API key is configured
    2. Validate the message length against the token limit
    3. Load conversation history for this session
    4. Send system prompt + history + user message + tool definitions to GPT
    5. GPT picks a tool → _dispatch_tool calls the matching handler in chatbot.py
    6. Save the exchange to history and return the result

    Returns a dict with 'reply' (text) and optionally 'image' (path to a chart).
    """
    if not os.getenv("OPENAI_API_KEY"):
        return {
            "reply": "OpenAI is not configured yet. Set OPENAI_API_KEY, then restart the app.",
            "image": None,
        }

    try:
        from openai import OpenAI
    except ImportError:
        return {
            "reply": "The OpenAI Python package is not installed. Run: pip install -r requirements.txt",
            "image": None,
        }

    model = os.getenv("OPENAI_MODEL", "gpt-5.1")
    validation_error = _validate_user_message(user_message, model=model)
    if validation_error:
        return {"reply": validation_error, "image": None}

    client = OpenAI()
    history = _get_history(session_id)

    # Build the full message list: system prompt + past conversation + new message
    messages = (
        [{"role": "system", "content": SYSTEM_MESSAGE}]
        + history
        + [{"role": "user", "content": user_message}]
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
    except Exception as exc:
        return {"reply": f"OpenAI error: {exc}", "image": None}

    message = response.choices[0].message

    # If GPT responded with plain text (no tool call), return it directly
    if not message.tool_calls:
        reply_text = message.content or "I could not map that request to a KINEPIK tool."
        _save_history(session_id, history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply_text},
        ])
        return {"reply": reply_text, "image": None}

    # GPT picked a tool — parse its arguments and call the matching handler
    tool_call = message.tool_calls[0]
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        return {"reply": "I could not parse the OpenAI tool arguments.", "image": None}

    result = _dispatch_tool(tool_call.function.name, args, session_id=session_id)

    # Remember the last chart generated so the user can ask to explain it
    if isinstance(result, dict) and result.get("image"):
        if session_id:
            _last_images[session_id] = result["image"]

    # Second pass: let GPT read the tool's actual result and write a direct
    # answer to the user's question, instead of returning the fixed template
    # reply as-is. This is what lets comparative/analytical questions (e.g.
    # "which drug affects it more strongly?") get a real answer rather than
    # a generic description — GPT never saw the tool's output before this.
    original_reply = result.get("reply", "") if isinstance(result, dict) else ""
    if original_reply:
        try:
            synthesis_messages = messages + [
                message.model_dump(exclude_none=True),
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": original_reply,
                },
                {
                    "role": "system",
                    "content": (
                        "Using ONLY the data in the tool result above, directly answer the "
                        "user's most recent question in 1-3 sentences. If the question asked "
                        "for a comparison or judgement (e.g. 'which is stronger'), state the "
                        "answer explicitly using the actual values given. Do not repeat the "
                        "raw 'Values:' line verbatim — refer to the specific numbers naturally "
                        "in your answer.\n\n"
                        "STRICT ANTI-HALLUCINATION RULE: every specific name, ID, number, or "
                        "score you write MUST be copied from the tool result above — never "
                        "estimated, recalled from general knowledge, or guessed, even if it "
                        "seems plausible. Before writing any entity name or figure, check it "
                        "appears verbatim in the tool result; if it does not, leave it out. "
                        "If the tool result's text does not actually contain the specific "
                        "data needed to answer precisely (e.g. it describes a chart in general "
                        "terms — 'top kinases were plotted' — without listing which ones or "
                        "their scores), you MUST say so explicitly (e.g. 'the specific values "
                        "aren't listed in the text, but the chart shows...') instead of "
                        "inventing plausible-sounding names or numbers to fill the gap. A "
                        "vague-but-honest answer is correct; a specific-but-fabricated answer "
                        "is a serious failure, even under pressure to sound precise."
                    ),
                },
            ]
            synthesis_response = client.chat.completions.create(
                model=model,
                messages=synthesis_messages,
            )
            synthesized = synthesis_response.choices[0].message.content
            if synthesized:
                result = {**result, "reply": f"{synthesized}\n\n{original_reply}"}
        except Exception:
            pass  # fall back to the original templated reply on any failure

    # Save the exchange to history (image paths are not stored, just the text)
    _save_history(session_id, history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": result.get("reply", "")},
    ])

    return result
