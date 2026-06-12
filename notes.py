"""Meeting note composition helpers."""


def build_notes(
    raw_text: str,
    context=None,
    notes_mode: str = "overwrite",
    existing_notes: str = "",
    notes_generator=None,
) -> str:
    """Generate final note text for overwrite, append, and improve modes."""
    if notes_generator is None:
        raise ValueError("notes_generator is required")

    if notes_mode == "append":
        notes = notes_generator(raw_text, context=context)
        if context and context.title:
            notes = f"# {context.title}\n\n{notes}"
        if existing_notes:
            notes = existing_notes + "\n\n---\n\n" + notes
        return notes

    if notes_mode == "improve":
        notes = notes_generator(raw_text, context=context, existing_notes=existing_notes)
        if context and context.title and not notes.startswith("#"):
            notes = f"# {context.title}\n\n{notes}"
        return notes

    notes = notes_generator(raw_text, context=context)
    if context and context.title:
        notes = f"# {context.title}\n\n{notes}"
    return notes
