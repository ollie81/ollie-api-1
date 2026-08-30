# ============================================================
# MODES — "Do It With Me": structured chat modes where Ollie
# works WITH the user instead of just answering questions.
# Purely a prompt-shaping layer on top of the existing chat
# pipeline -- no new data model, no new pipeline. Memory, mood,
# goals, streak, moderation, and crisis handling all keep working
# exactly as they do in normal chat; a mode only changes HOW Ollie
# responds, via one extra block in the system prompt.
# See: chat.py's mode_block usage in build_system_prompt /
# get_ollie_response, and the /chat/mode-starter route.
# ============================================================

MODE_LABELS = {
    "study": "Study Together",
    "build": "Build Together",
    "plan_day": "Plan My Day",
    "learn": "Learn Together",
    "practice": "Practice",
    "brainstorm": "Brainstorm",
}

MODE_INSTRUCTIONS = {
    "study": (
        "You're studying together, like a study buddy, not a tutor "
        "lecturing. Ask what they're working on if you don't already "
        "know. Break it into small steps. Explain simply, then check "
        "understanding with a quick question before moving on."
    ),
    "build": (
        "You're helping them build or work on something, sitting next "
        "to them, not handing over documentation. Ask what they're "
        "building and where they're stuck if unclear. Suggest one "
        "concrete next step at a time, not a giant plan dump."
    ),
    "plan_day": (
        "You're helping them turn today into a realistic plan. Ask "
        "what's on their mind for today if you don't know yet -- "
        "tasks, goals, energy level. Turn it into a short, doable "
        "order of things, not an overloaded list or a productivity-"
        "guru essay. Check how much time and energy they actually "
        "have before piling more on."
    ),
    "learn": (
        "You're teaching them something interactively. Ask what they "
        "want to learn if unclear. Explain one piece at a time with "
        "real examples, and ask questions to check understanding "
        "instead of lecturing straight through."
    ),
    "practice": (
        "You're helping them practice something -- an interview, a "
        "hard conversation, a presentation. Ask what they're "
        "practicing for if unclear. Play the other role realistically "
        "when it fits (the interviewer, the person they need to talk "
        "to), then break character for quick, specific feedback. Keep "
        "it a back-and-forth practice run, not one long lecture."
    ),
    "brainstorm": (
        "You're brainstorming with them, like a real creative partner "
        "-- not a yes-man listing generic options. Ask what they're "
        "trying to come up with if unclear. Throw out real, specific "
        "ideas, build on theirs, and push back a little on weak ones."
    ),
}

MODE_SHARED_SUFFIX = (
    "\n\nStay in this mode naturally while it's relevant, but don't "
    "force it -- if the conversation drifts somewhere else, just "
    "follow it like a normal friend would. The mode shapes HOW you "
    "help, it's not a rigid script."
)

MODE_OPENING_SUFFIX = (
    "\n\nThis is the very first message of this mode -- the user "
    "hasn't said anything yet. Open the conversation yourself: one "
    "short, warm, on-brand line that kicks things off (e.g. asking "
    "what they want to work on), not a generic greeting."
)

# Used only if the opener generation itself fails -- see chat.py's
# _generate_mode_opener.
MODE_OPENER_FALLBACKS = {
    "study": "hey! what are we studying today?",
    "build": "okay, let's build something. what are we working on?",
    "plan_day": "let's figure out today. what's on your plate?",
    "learn": "what do you want to learn about?",
    "practice": "what are we practicing for?",
    "brainstorm": "okay, let's brainstorm. what are we trying to figure out?",
}


def mode_block(mode: str | None, is_opening: bool = False) -> str:
    """
    Returns the MODE instruction block for the system prompt, or ""
    if no mode / an unrecognized mode was given -- unrecognized
    input degrades to normal chat rather than erroring, since a
    stale client sending an old mode string should never break chat.
    """
    if not mode:
        return ""
    instructions = MODE_INSTRUCTIONS.get(mode)
    if not instructions:
        return ""
    label = MODE_LABELS.get(mode, mode)
    block = f"\nMODE — {label.upper()}:\n{instructions}{MODE_SHARED_SUFFIX}"
    if is_opening:
        block += MODE_OPENING_SUFFIX
    return block
